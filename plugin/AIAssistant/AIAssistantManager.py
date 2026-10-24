# -*- coding: utf-8 -*-
#
# AIAssistantManager.py — native Qt/QML bridge for the AI Assistant plugin.
#
# Exposes the same agent machinery (LLMAgent + MCPServer + EventBus + ChatState)
# that powers the web SPA to a native QML chat window inside STE Slicer,
# so the user can choose: chat in the browser OR chat inside the application.
#
# Python 3.5 compatible.

import os

from PyQt5.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Application import Application
from UM.Logger import Logger

from .AIAssistant import PREF_API_KEY, PREF_API_URL, PREF_MODEL

IMAGE_EXT = (".png", ".jpg", ".jpeg")
MODEL_EXT = (".stl", ".obj", ".3mf")


class AIAssistantManager(QObject):
    """QML-facing manager: chat messages, status, attachments, settings."""

    messagesChanged = pyqtSignal()
    statusChanged = pyqtSignal()
    thinkingChanged = pyqtSignal()
    happyChanged = pyqtSignal()
    pascalSpeechChanged = pyqtSignal()
    slicingChanged = pyqtSignal()
    chatWindowVisibleChanged = pyqtSignal()
    attachedFilesChanged = pyqtSignal()
    settingsChanged = pyqtSignal()

    def __init__(self, extension, parent=None):
        super(AIAssistantManager, self).__init__(parent)
        self._extension = extension
        self._dialog = None
        self._messages = []
        self._is_thinking = False
        self._is_happy = False
        self._status_text = u"Готов к работе"
        self._pascal_speech = (u"Привет! Я Паскаль 🦎 Опишите задачу и приложите "
                               u"модель — я всё настрою сам.")
        self._status_type = "ready"  # ready / thinking / processing / error
        self._chat_window_visible = False
        self._attached_paths = []    # absolute paths
        self._is_slicing = False
        self._slicing_progress = 0.0

        # live updates from the shared event bus (agent thread -> main thread)
        extension.bus.subscribe(self._on_event_thread)

        # preload existing conversation (shared with the web SPA)
        for message in extension.chat.history():
            if message.get("role") == "user":
                self._messages.append({"role": "user",
                                       "content": message.get("text", "")})
            else:
                for card in message.get("tool_cards") or []:
                    self._messages.append({
                        "role": "tool",
                        "content": u"%s(%s)" % (
                            card.get("name", "?"),
                            _short_args(card.get("arguments"))),
                        "toolName": card.get("name", "?"),
                        "toolResult": u"✓" if card.get("ok") else u"✗",
                    })
                self._messages.append({"role": "assistant",
                                       "content": message.get("text", "")})

        # real slicing progress poller (reads the thread-safe state dict)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_slicing)
        self._timer.start()

    def setDialog(self, dialog):
        self._dialog = dialog

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @pyqtProperty("QVariantList", notify=messagesChanged)
    def messages(self):
        return self._messages

    @pyqtProperty(bool, notify=thinkingChanged)
    def isThinking(self):
        return self._is_thinking

    @pyqtProperty(bool, notify=happyChanged)
    def isHappy(self):
        return self._is_happy

    @pyqtProperty(str, notify=pascalSpeechChanged)
    def pascalSpeech(self):
        return self._pascal_speech

    @pyqtProperty(str, notify=statusChanged)
    def statusText(self):
        return self._status_text

    @pyqtProperty(str, notify=statusChanged)
    def statusType(self):
        return self._status_type

    @pyqtProperty(bool, notify=chatWindowVisibleChanged)
    def chatWindowVisible(self):
        return self._chat_window_visible

    @chatWindowVisible.setter
    def chatWindowVisible(self, value):
        if self._chat_window_visible != bool(value):
            self._chat_window_visible = bool(value)
            self.chatWindowVisibleChanged.emit()

    @pyqtProperty("QVariantList", notify=attachedFilesChanged)
    def attachedFiles(self):
        return [os.path.basename(p) for p in self._attached_paths]

    @pyqtProperty(bool, notify=slicingChanged)
    def isSlicing(self):
        return self._is_slicing

    @pyqtProperty(float, notify=slicingChanged)
    def slicingProgress(self):
        return self._slicing_progress

    @pyqtProperty(str, notify=settingsChanged)
    def apiKey(self):
        # QML evaluates properties at arbitrary times: never let a Python
        # exception escape into the QML engine (it triggers the crash dialog)
        try:
            return self._extension.get_prefs().getValue(PREF_API_KEY) or ""
        except Exception:
            return ""

    @pyqtProperty(str, notify=settingsChanged)
    def apiBaseUrl(self):
        try:
            return self._extension.get_prefs().getValue(PREF_API_URL) or ""
        except Exception:
            return ""

    @pyqtProperty(str, notify=settingsChanged)
    def modelName(self):
        try:
            return self._extension.get_prefs().getValue(PREF_MODEL) or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # window control
    # ------------------------------------------------------------------

    @pyqtSlot()
    def showChatWindow(self):
        if self._dialog is not None:
            self._dialog.show()
            for name in ("raise_", "requestActivate"):
                if hasattr(self._dialog, name):
                    try:
                        getattr(self._dialog, name)()
                    except Exception:
                        pass
        self.chatWindowVisible = True

    @pyqtSlot()
    def hideChatWindow(self):
        if self._dialog is not None:
            self._dialog.hide()
        self.chatWindowVisible = False

    @pyqtSlot()
    def toggleChatWindow(self):
        if self._chat_window_visible:
            self.hideChatWindow()
        else:
            self.showChatWindow()

    # ------------------------------------------------------------------
    # chat actions
    # ------------------------------------------------------------------

    @pyqtSlot()
    def openWebChat(self):
        try:
            self._extension.showChat()
        except Exception as e:
            Logger.log("e", "[AI Assistant] openWebChat failed: %s" % e)

    @pyqtSlot()
    def clearChat(self):
        self._extension.chat.clear()
        self._messages = []
        self.messagesChanged.emit()

    @pyqtSlot(str)
    def attachFile(self, file_url):
        path = str(file_url)
        for prefix in ("file:///", "file://", "file:"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        # Windows: strip the leading slash of /C:/...
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        if os.path.isfile(path) and path not in self._attached_paths:
            if len(self._attached_paths) >= 6:
                return
            self._attached_paths.append(path)
            self.attachedFilesChanged.emit()

    @pyqtSlot(int)
    def removeAttachment(self, index):
        if 0 <= index < len(self._attached_paths):
            self._attached_paths.pop(index)
            self.attachedFilesChanged.emit()

    @pyqtSlot(str, str, str)
    def saveSettings(self, api_key, api_url, model_name):
        try:
            self._extension.save_settings(api_url, api_key, model_name)
            self.settingsChanged.emit()
            self._set_status(u"Настройки сохранены", "ready")
        except Exception as e:
            Logger.log("e", "[AI Assistant] saveSettings failed: %s" % e)
            self._set_status(u"Ошибка сохранения настроек", "error")

    @pyqtSlot(str)
    def quickAction(self, action_id):
        actions = {
            "check_settings": u"Проверь текущий принтер, режим печати, материал "
                              u"и ключевые настройки; подскажи, что оптимизировать.",
            "analyze_scene": u"Проанализируй модели на столе и текущие настройки, "
                             u"предложи и примени оптимизации.",
            "arrange": u"Автоматически расставь все модели на столе.",
            "slice": u"Запусти слайсинг текущей сцены и отчитайся о результате "
                     u"(время печати, расход материала).",
            "export": u"Экспортируй проект и G-code для сдачи (export_project_and_gcode).",
        }
        prompt = actions.get(str(action_id))
        if prompt:
            self.sendMessage(prompt)

    @pyqtSlot(str)
    def sendMessage(self, text):
        user_text = str(text).strip()
        paths = list(self._attached_paths)
        if not user_text and not paths:
            return
        if self._extension.chat.is_busy():
            self._append_local(u"⚠️ Агент ещё занят предыдущей задачей", "error")
            return
        self._attached_paths = []
        self.attachedFilesChanged.emit()

        attachments = []
        for path in paths:
            lower = path.lower()
            kind = ("image" if lower.endswith(IMAGE_EXT)
                    else "model" if lower.endswith(MODEL_EXT) else "file")
            attachments.append({"name": os.path.basename(path),
                                "path": path, "kind": kind})
        # native window uses real local paths, nothing to upload
        try:
            ok, error = self._extension.submit_chat(user_text, attachments)
        except Exception as e:
            Logger.log("e", "[AI Assistant] submit_chat failed: %s" % e)
            ok, error = False, str(e)
        if not ok and error:
            hint = (u"Сначала задайте API-ключ (⚙ Настройки)"
                    if error == "api_key_missing" else error)
            self._append_local(u"⚠️ " + hint, "error")

    # ------------------------------------------------------------------
    # event bus bridge (agent threads -> Qt main thread)
    # ------------------------------------------------------------------

    def _on_event_thread(self, event):
        # called from HTTP/agent threads: marshal into the Qt main thread
        try:
            Application.getInstance().callLater(self._on_event_main, event)
        except Exception:
            pass

    def _on_event_main(self, event):
        try:
            self._handle_event(event)
        except Exception as e:
            Logger.log("w", "[AI Assistant] qml event failed: %s" % e)

    def _handle_event(self, event):
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "user":
            self._messages.append({"role": "user",
                                   "content": data.get("text", "")})
            self.messagesChanged.emit()
        elif etype == "assistant":
            text = data.get("text", "")
            self._messages.append({"role": "assistant", "content": text})
            self.messagesChanged.emit()
            self._set_thinking(False)
            self._celebrate()
            snippet = " ".join(text.replace("\n", " ").split())
            for mark in ("**", "`", "#", ">", "|"):
                snippet = snippet.replace(mark, " ")
            snippet = " ".join(snippet.split())
            if len(snippet) > 150:
                snippet = snippet[:150] + u"…"
            if snippet:
                self._say(snippet)
        elif etype == "tool_call":
            self._messages.append({
                "role": "tool",
                "content": u"%s(%s)" % (data.get("name", "?"),
                                        _short_args(data.get("arguments"))),
                "toolName": data.get("name", "?"),
                "toolResult": u"…",
            })
            self.messagesChanged.emit()
            self._set_thinking(True)
            self._set_status(u"Выполняет: %s…" % data.get("name", ""), "processing")
            self._say(u"Работаю со слайсером: %s…" % data.get("name", "?"))
        elif etype == "tool_result":
            if self._messages and self._messages[-1].get("role") == "tool":
                mark = u"✓" if data.get("ok") else u"✗"
                took = data.get("duration_ms")
                suffix = u" · %s мс" % took if took else ""
                summary = (data.get("summary") or "")[:160]
                self._messages[-1]["toolResult"] = u"%s %s%s\n%s" % (
                    mark, data.get("name", ""), suffix, summary)
                self.messagesChanged.emit()
        elif etype == "status":
            if data.get("state") == "thinking":
                self._set_thinking(True)
                self._set_status(u"Анализирует запрос…", "thinking")
                self._say(u"Хм-хм… анализирую задачу и подбираю режим…")
            elif data.get("state") == "idle":
                self._set_thinking(False)
                self._set_status(u"Готов к работе", "ready")
                self._say(u"Готов к работе! Задайте следующую задачу 🦎")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _refresh_slicing(self):
        from .agent import slicer_tools
        try:
            slicing = slicer_tools.get_slicing_state_snapshot()
        except Exception:
            return
        changed = False
        is_slicing = slicing.get("state") == "slicing"
        progress = float(slicing.get("progress", 0.0)) * 100.0
        if is_slicing != self._is_slicing or abs(progress - self._slicing_progress) > 0.5:
            self._is_slicing = is_slicing
            self._slicing_progress = progress
            changed = True
        if changed:
            self.slicingChanged.emit()

    def _append_local(self, text, role):
        self._messages.append({"role": role, "content": text})
        self.messagesChanged.emit()

    def _set_thinking(self, value):
        if self._is_thinking != value:
            self._is_thinking = value
            self.thinkingChanged.emit()

    def _celebrate(self):
        """Mascot jumps for a few seconds after each assistant reply."""
        if not self._is_happy:
            self._is_happy = True
            self.happyChanged.emit()
        QTimer.singleShot(4200, self._stop_celebrating)

    def _stop_celebrating(self):
        if self._is_happy:
            self._is_happy = False
            self.happyChanged.emit()

    def _say(self, text):
        if text and text != self._pascal_speech:
            self._pascal_speech = text
            self.pascalSpeechChanged.emit()

    def _set_status(self, text, status_type):
        self._status_text = text
        self._status_type = status_type
        self.statusChanged.emit()


def _short_args(args):
    try:
        import json
        text = json.dumps(args, ensure_ascii=False)
    except Exception:
        text = str(args)
    return text if len(text) <= 80 else text[:80] + u"…"
