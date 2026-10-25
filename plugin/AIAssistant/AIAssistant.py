# -*- coding: utf-8 -*-
#
# AI Assistant extension for STE Slicer (hackathon Stereotech case).
#
# Starts a local HTTP server, serves the chat SPA in the system browser,
# bridges JS <-> Python and runs the LLM agent loop with MCP tools.
#
# Python 3.5 compatible, standard library only.

import base64
import binascii
import json
import os
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from UM.Extension import Extension
from UM.Logger import Logger

from .agent import slicer_tools
from .agent.mcp_server import MCPServer
from .agent.llm_agent import LLMAgent, DEFAULT_API_URL, DEFAULT_MODEL_NAME

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PLUGIN_DIR, "web")
# plugin lives in <config>/plugins/AIAssistant -> work dir next to plugins/
CONFIG_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))
WORK_DIR = os.path.join(CONFIG_DIR, "AIAssistant_files")
RESULTS_DIR = os.path.join(CONFIG_DIR, "AIAssistant_results")

PREF_API_URL = "ai_assistant/api_url"
PREF_API_KEY = "ai_assistant/api_key"
PREF_MODEL = "ai_assistant/model_name"
PREF_RESULTS_DIR = "ai_assistant/results_dir"
PREF_TUTORIAL = "ai_assistant/tutorial_choice"

# API keys are supplied by the user and must not be distributed in source code.
DEFAULT_API_KEY = ""

MAX_BODY = 64 * 1024 * 1024  # 64 MB (stl + images)


def _new_session_token():
    return binascii.hexlify(os.urandom(32)).decode("ascii")


class EventBus(object):
    """Thread-safe event log for the SPA (long-polling transport) with
    in-process subscribers (native QML window)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = []
        self._next_id = 1
        self._listeners = []

    def subscribe(self, callback):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def push(self, event_type, data=None):
        with self._lock:
            event = {"id": self._next_id, "type": event_type,
                     "ts": int(time.time() * 1000)}
            if data is not None:
                event["data"] = data
            self._events.append(event)
            self._next_id += 1
            if len(self._events) > 2000:
                self._events = self._events[-1000:]
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                pass
        return event["id"]

    def get_after(self, after_id):
        with self._lock:
            return [e for e in self._events if e["id"] > after_id]


class ChatState(object):
    """Shared chat state: server-side history + busy flag."""

    def __init__(self, bus):
        self.bus = bus
        self._lock = threading.Lock()
        self._history = []      # UI-oriented messages
        self._busy = False

    def is_busy(self):
        with self._lock:
            return self._busy

    def set_busy(self, value):
        with self._lock:
            self._busy = value

    def history(self):
        with self._lock:
            return json.loads(json.dumps(self._history))

    def append(self, message):
        with self._lock:
            self._history.append(message)
            if len(self._history) > 400:
                self._history = self._history[-200:]

    def clear(self):
        with self._lock:
            self._history = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "STE-AIAssistant/1.0"

    # populated by AIAssistant extension
    extension = None

    def log_message(self, fmt, *args):
        Logger.log("d", "[AI Assistant] http: " + (fmt % args))

    # ---------------- helpers ----------------

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        token = self.headers.get("X-STE-AI-Token")
        if not token:
            cookie = self.headers.get("Cookie") or ""
            for item in cookie.split(";"):
                name, separator, value = item.strip().partition("=")
                if separator and name == "ste_ai_session":
                    token = value
                    break
        if token != getattr(Handler.extension, "session_token", None):
            self._send_json({"ok": False, "error": "unauthorized"}, 401)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in ("http://127.0.0.1:8765",
                                     "http://localhost:8765"):
            self._send_json({"ok": False, "error": "forbidden origin"}, 403)
            return False
        return True

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _send_file(self, path, mime, set_session_cookie=False):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except IOError:
            self.send_error(404, "File not found")
            return
        content_type = mime if mime.startswith(("video/", "image/")) else mime + "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # never let the browser serve a stale UI from cache
        self.send_header("Cache-Control", "no-store, must-revalidate")
        if set_session_cookie:
            self.send_header("Set-Cookie",
                             "ste_ai_session=%s; HttpOnly; SameSite=Strict; Path=/" %
                             Handler.extension.session_token)
        self.end_headers()
        self.wfile.write(body)

    # ---------------- GET ----------------

    def do_GET(self):
        ext = Handler.extension
        path = self.path.split("?")[0]
        query = {}
        if "?" in self.path:
            for pair in self.path.split("?")[1].split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    query[k] = v

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(WEB_DIR, "index.html"), "text/html",
                            set_session_cookie=True)
        elif path == "/style.css":
            self._send_file(os.path.join(WEB_DIR, "style.css"), "text/css")
        elif path == "/app.js":
            self._send_file(os.path.join(WEB_DIR, "app.js"), "application/javascript")
        elif path.startswith("/assets/"):
            name = os.path.basename(path[len("/assets/"):])
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            mime = {"mp4": "video/mp4", "webm": "video/webm", "png": "image/png",
                    "svg": "image/svg+xml", "webp": "image/webp"}.get(ext)
            if mime is None or not name or ".." in path:
                self.send_error(404, "Not found")
                return
            self._send_file(os.path.join(WEB_DIR, "assets", name), mime)
        elif path.startswith("/api/"):
            if not self._authorized():
                return
            self._handle_api_get(path, query, ext)
        else:
            self.send_error(404, "Not found")

    def _handle_api_get(self, path, query, ext):
        if path == "/api/status":
            try:
                status = slicer_tools.get_status_snapshot()
            except Exception as e:
                status = {"error": str(e)}
            status["busy"] = ext.chat.is_busy()
            status["settings_configured"] = ext.is_api_configured()
            self._send_json(status)
        elif path == "/api/events":
            try:
                after = int(query.get("after", "0"))
            except ValueError:
                after = 0
            self._send_json({"events": ext.bus.get_after(after)})
        elif path == "/api/history":
            self._send_json({"messages": ext.chat.history()})
        elif path == "/api/tools":
            self._send_json({"tools": ext.mcp.tools_list()})
        elif path == "/api/settings":
            prefs = ext.get_prefs()
            self._send_json({
                "api_url": prefs.getValue(PREF_API_URL) or DEFAULT_API_URL,
                "api_key": "",
                "api_key_configured": ext.is_api_configured(),
                "model_name": prefs.getValue(PREF_MODEL) or DEFAULT_MODEL_NAME,
                "results_dir": slicer_tools.get_results_dir(),
            })
        else:
            self.send_error(404, "Not found")

    # ---------------- POST ----------------

    def do_POST(self):
        ext = Handler.extension
        path = self.path.split("?")[0]

        if path.startswith("/api/") or path == "/mcp":
            if not self._authorized():
                return
        if path == "/api/settings":
            data = self._read_json()
            if data is None:
                self._send_json({"ok": False, "error": "bad json"}, 400)
                return
            ext.save_settings(data.get("api_url"), data.get("api_key"),
                              data.get("model_name"))
            self._send_json({"ok": True})
        elif path == "/api/message":
            data = self._read_json()
            if data is None:
                self._send_json({"ok": False, "error": "bad json"}, 400)
                return
            ok, error = ext.handle_chat_message(data)
            if ok:
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": error}, 409 if error == "busy" else 400)
        elif path == "/api/export":
            data = self._read_json() or {}
            model_name = (data.get("model_name") or "").strip() or "model"
            output_dir = data.get("output_dir")
            try:
                result = ext.mcp.call_tool("export_project_and_gcode", {
                    "model_name": model_name,
                    "output_dir": output_dir,
                })
                self._send_json({"ok": True, "result": result})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/scene/clear":
            try:
                result = ext.mcp.call_tool("arrange_and_clear_scene", {"action": "clear"})
                self._send_json({"ok": True, "result": result})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/clear_history":
            ext.chat.clear()
            self._send_json({"ok": True})
        elif path == "/mcp":
            self._handle_mcp(ext)
        else:
            self.send_error(404, "Not found")

    def _handle_mcp(self, ext):
        data = self._read_json()
        if data is None:
            self._send_json({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}}, 400)
            return
        try:
            result = ext.mcp.handle_request(data)
            self._send_json({"jsonrpc": "2.0", "id": data.get("id"), "result": result})
        except Exception as e:
            code = getattr(e, "rpc_code", -32603)
            self._send_json({"jsonrpc": "2.0", "id": data.get("id"),
                             "error": {"code": code, "message": str(e)}}, 200)


class ChatServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AIAssistant(Extension):
    def __init__(self):
        super().__init__()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Чат Кали (в окне слайсера)", self.showNativeChat)
        self.addMenuItem("Открыть AI-чат в браузере", self.showChat)
        self.addMenuItem("Открыть папку результатов", self.openResultsDir)

        self._server = None
        self.bus = EventBus()
        self.chat = ChatState(self.bus)
        self.mcp = MCPServer()
        self.agent = None
        self._agent_lock = threading.Lock()
        self._widgets_ui = None
        self.session_token = _new_session_token()

        self._init_preferences()
        # autostart HTTP server and native chat window once the app is up
        try:
            from steslicer.SteSlicerApplication import SteSlicerApplication
            app = SteSlicerApplication.getInstance()
            app.initializationFinished.connect(self._start_server)
            app.initializationFinished.connect(self._create_native_ui)
        except Exception as e:
            Logger.logException("e", "[AI Assistant] autostart failed: %s" % e)

    # ------------- preferences -------------

    def get_prefs(self):
        from steslicer.SteSlicerApplication import SteSlicerApplication
        return SteSlicerApplication.getInstance().getPreferences()

    def _init_preferences(self):
        try:
            prefs = self.get_prefs()
            prefs.addPreference(PREF_API_URL, DEFAULT_API_URL)
            prefs.addPreference(PREF_API_KEY, DEFAULT_API_KEY)
            prefs.addPreference(PREF_MODEL, DEFAULT_MODEL_NAME)
            prefs.addPreference(PREF_RESULTS_DIR, RESULTS_DIR)
            prefs.addPreference(PREF_TUTORIAL, "")
        except Exception as e:
            Logger.logException("e", "[AI Assistant] preferences init failed: %s" % e)

    def get_api_key(self):
        """Return the user-supplied API key, if configured."""
        try:
            return self.get_prefs().getValue(PREF_API_KEY) or DEFAULT_API_KEY
        except Exception:
            return DEFAULT_API_KEY

    def is_api_configured(self):
        return bool(self.get_api_key())

    def save_settings(self, api_url, api_key, model_name):
        prefs = self.get_prefs()
        if api_url:
            prefs.setValue(PREF_API_URL, str(api_url).strip().rstrip("/"))
        if api_key is not None and str(api_key).strip():
            prefs.setValue(PREF_API_KEY, str(api_key).strip())
        if model_name:
            prefs.setValue(PREF_MODEL, str(model_name).strip())
        slicer_tools.set_results_dir_override(prefs.getValue(PREF_RESULTS_DIR))
        Logger.log("i", "[AI Assistant] settings saved (url=%s, model=%s)" %
                   (prefs.getValue(PREF_API_URL), prefs.getValue(PREF_MODEL)))

    # ------------- server -------------

    def _start_server(self):
        if self._server is not None:
            return
        try:
            Handler.extension = self
            slicer_tools.initialize()
            self._server = ChatServer(("127.0.0.1", 8765), Handler)
            threading.Thread(target=self._server.serve_forever,
                             kwargs={"poll_interval": 0.2}, daemon=True).start()
            Logger.log("i", "[AI Assistant] HTTP server on http://127.0.0.1:8765/")
        except Exception as e:
            Logger.logException("e", "[AI Assistant] server start failed: %s" % e)

    def showChat(self):
        self._start_server()
        webbrowser.open("http://127.0.0.1:8765/")

    # ------------- native QML window -------------

    def _create_native_ui(self):
        if self._widgets_ui is not None:
            return
        try:
            from .AIAssistantWidgets import WidgetsUI
            self._widgets_ui = WidgetsUI(self)
            self._widgets_ui.startup()
            Logger.log("i", "[AI Assistant] native QWidget chat created")
            return

        except Exception as e:
            Logger.logException("e", "[AI Assistant] native UI failed: %s" % e)

    def showNativeChat(self):
        self._start_server()
        if self._widgets_ui is None:
            self._create_native_ui()
        if self._widgets_ui is not None:
            self._widgets_ui.show_panel()
        else:
            # Native UI failed to load — fall back to the browser SPA.
            self.showChat()

    # ------------- helpers for the widgets UI -------------

    def get_main_window_geometry(self):
        """(x, y, w, h) of the slicer main window or None."""
        try:
            from steslicer.SteSlicerApplication import SteSlicerApplication
            window = SteSlicerApplication.getInstance().getMainWindow()
            if window is not None:
                # x()/y() can be relative to a Qt parent. Native tool
                # windows need the main window's global screen coordinates.
                geometry = window.frameGeometry()
                return (geometry.x(), geometry.y(),
                        geometry.width(), geometry.height())
        except Exception:
            pass
        return None

    def is_main_window_active(self):
        try:
            from steslicer.SteSlicerApplication import SteSlicerApplication
            window = SteSlicerApplication.getInstance().getMainWindow()
            return bool(window is not None and window.isActive())
        except Exception:
            return True

    def get_main_window_state(self):
        """"normal" / "minimized" / "unknown" — owned windows hide on minimize."""
        try:
            from PyQt5.QtGui import QWindow
            from steslicer.SteSlicerApplication import SteSlicerApplication
            window = SteSlicerApplication.getInstance().getMainWindow()
            if window is not None and window.visibility() == QWindow.Minimized:
                return "minimized"
        except Exception:
            pass
        return "normal"

    def get_settings_values(self):
        prefs = self.get_prefs()
        return {
            "api_url": prefs.getValue(PREF_API_URL) or DEFAULT_API_URL,
            "api_key": self.get_api_key(),
            "model_name": prefs.getValue(PREF_MODEL) or DEFAULT_MODEL_NAME,
        }

    def get_slicing_snapshot(self):
        try:
            return slicer_tools.get_slicing_state_snapshot()
        except Exception:
            return None

    def openResultsDir(self):
        path = slicer_tools.get_results_dir()
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        try:
            os.startfile(path)
        except Exception as e:
            Logger.log("w", "[AI Assistant] cannot open results dir: %s" % e)

    # ------------- chat -------------

    def handle_chat_message(self, data):
        text = (data.get("text") or "").strip()
        attachments = data.get("attachments") or []
        if not text and not attachments:
            return False, "empty message"

        # save uploaded attachments to the work dir
        try:
            os.makedirs(WORK_DIR, exist_ok=True)
        except OSError:
            pass
        saved = []
        for att in attachments:
            try:
                name = self._safe_filename(att.get("name") or "file")
                raw = base64.b64decode(att.get("data") or "")
                if not raw:
                    continue
                dest = os.path.join(WORK_DIR, "%d_%s" % (int(time.time() * 1000), name))
                with open(dest, "wb") as f:
                    f.write(raw)
                saved.append({"name": name, "path": dest,
                              "kind": att.get("kind") or "file"})
            except Exception as e:
                Logger.log("w", "[AI Assistant] attachment save failed: %s" % e)

        return self.submit_chat(text, saved)

    def submit_chat(self, text, attachments):
        """Shared chat entry point for the web SPA and the native window."""
        text = (text or "").strip()
        if not text and not attachments:
            return False, "empty message"
        if self.chat.is_busy():
            return False, "busy"
        if not self.is_api_configured():
            return False, "api_key_missing"

        user_msg = {"role": "user", "text": text,
                    "attachments": [a["name"] for a in attachments]}
        self.chat.append(user_msg)
        self.bus.push("user", user_msg)

        threading.Thread(target=self._run_agent, args=(text, attachments),
                         daemon=True).start()
        return True, None

    def _run_agent(self, text, saved):
        self.chat.set_busy(True)
        self.bus.push("status", {"state": "thinking"})
        try:
            prefs = self.get_prefs()
            with self._agent_lock:
                if self.agent is None:
                    self.agent = LLMAgent(
                        api_url=prefs.getValue(PREF_API_URL) or DEFAULT_API_URL,
                        api_key=self.get_api_key(),
                        model_name=prefs.getValue(PREF_MODEL) or DEFAULT_MODEL_NAME,
                        mcp=self.mcp,
                        on_event=self._agent_event)
                else:
                    # settings may have changed since the previous turn
                    self.agent.api_url = (prefs.getValue(PREF_API_URL) or "").rstrip("/")
                    self.agent.api_key = self.get_api_key()
                    self.agent.model_name = prefs.getValue(PREF_MODEL) or self.agent.model_name
            reply, tool_cards = self.agent.run(text, saved)
            msg = {"role": "assistant", "text": reply, "tool_cards": tool_cards}
            self.chat.append(msg)
            self.bus.push("assistant", msg)
        except Exception as e:
            Logger.logException("e", "[AI Assistant] agent failed: %s" % e)
            msg = {"role": "assistant", "text":
                   "⚠️ Ошибка агента: %s" % str(e), "tool_cards": []}
            self.chat.append(msg)
            self.bus.push("assistant", msg)
        finally:
            self.chat.set_busy(False)
            self.bus.push("status", {"state": "idle"})

    def _agent_event(self, event_type, data):
        # events from the agent thread (tool calls, partial status)
        self.bus.push(event_type, data)

    @staticmethod
    def _safe_filename(name):
        name = os.path.basename(str(name))
        name = re.sub(r'[^A-Za-z0-9А-Яа-я_.()-]+', "_", name)
        return name[:120] or "file"
