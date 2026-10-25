# -*- coding: utf-8 -*-
#
# preview_widgets.py — standalone preview of the QWidgets chat UI.
#
# Runs AIAssistantWidgets without STE Slicer: a mock extension feeds the
# same EventBus protocol the real agent emits, so the panel, mascot, tool
# cards and markdown report render exactly like inside the slicer.
# Screenshots are saved to tools/preview_out/.
#
# Usage: python tools/preview_widgets.py [--stay]
#   --stay  keep the window open (default closes after the demo)

import os
import sys
import threading
import time

PLUGIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "plugin", "AIAssistant")
sys.path.insert(0, os.path.abspath(PLUGIN_DIR))

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

import AIAssistantWidgets as W

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_out")

DEMO_REPORT = u"""Готово! Настройки применены и нарезка завершена ✅

**Режим печати:** 3D (classic) — деталь плоская корпусная, осевых нагрузок нет.

**Материал и профиль:** ASA (УФ-стойкость для улицы), профиль *normal*.

**Ключевые параметры:**

| Параметр | Значение |
|---|---|
| layer_height | 0.12 мм |
| wall_line_count | 5 |
| infill_sparse_density | 50 % (gyroid) |
| material_print_temperature | 255 °C |
| material_bed_temperature | 100 °C |
| support_enable | false |

**Ориентация:** плоско на стол, эвольвентой вверх — `rotate_xyz=[0,0,0]`.

**Результат слайсинга:** время `1:47:32`, материал 48.2 г.

**Рекомендации:**
- ASA склонен к короблению — печать в закрытой камере
- храните катушку сухой (гигроскопичен)

Подробности: см. `AIAssistant_results/1. Шестерня/`."""


class MockBus(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = []

    def subscribe(self, cb):
        with self._lock:
            self._listeners.append(cb)

    def push(self, event_type, data=None):
        event = {"type": event_type, "data": data}
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)


class MockChat(object):
    def history(self):
        return []

    def clear(self):
        pass

    def is_busy(self):
        return False


class MockAgentThread(threading.Thread):
    """Replays the event sequence a real agent turn produces."""

    STEPS = [
        (0.0,  lambda bus: bus.push("tool_call", {
            "name": "load_and_analyze_model",
            "arguments": {"file_path": "C:/models/1_Шестерня.stl"}})),
        (0.9,  lambda bus: bus.push("tool_result", {
            "name": "load_and_analyze_model", "ok": True, "duration_ms": 3810,
            "summary": '{"bbox": [33.1, 10.2, 33.0], "volume_cm3": 12.4, '
                       '"overhangs": "нет", "symmetry": "planar"}'})),
        (1.1,  lambda bus: bus.push("tool_call", {
            "name": "set_printing_mode", "arguments": {"mode": "classic"}})),
        (1.6,  lambda bus: bus.push("tool_result", {
            "name": "set_printing_mode", "ok": True, "duration_ms": 220,
            "summary": '{"value": "classic", "label": "3D"}'})),
        (1.8,  lambda bus: bus.push("tool_call", {
            "name": "set_slicer_settings",
            "arguments": {"layer_height": 0.12, "material_print_temperature": 255}})),
        (2.3,  lambda bus: bus.push("tool_result", {
            "name": "set_slicer_settings", "ok": True, "duration_ms": 410,
            "summary": '{"applied": 2, "rejected": []}'})),
        (2.5,  lambda bus: bus.push("tool_call", {
            "name": "run_slicing", "arguments": {}})),
        (5.0,  lambda bus: bus.push("tool_result", {
            "name": "run_slicing", "ok": True, "duration_ms": 2480,
            "summary": '{"final_state": "done", "print_time": "1:47:32"}'})),
        (5.2,  lambda bus: bus.push("assistant", {"text": DEMO_REPORT})),
    ]

    def __init__(self, bus, state):
        super(MockAgentThread, self).__init__()
        self.daemon = True
        self.bus = bus
        self.state = state
        self.t0 = None

    def run(self):
        self.t0 = time.time()
        self.bus.push("status", {"state": "thinking"})
        for delay, fire in self.STEPS:
            wait = self.t0 + delay - time.time()
            if wait > 0:
                time.sleep(wait)
            # fake slicing progress between 2.5s and 5s
            if 2.6 < delay < 5.0:
                self.state["progress"] = (delay - 2.5) / 2.5
            fire(self.bus)
        self.state["slicing"] = False
        self.bus.push("status", {"state": "idle"})


class MockExtension(object):
    def __init__(self):
        self.bus = MockBus()
        self.chat = MockChat()
        self.state = {"slicing": False, "progress": 0.0}
        self.mcp = self

    # --- interface used by WidgetsUI ---

    def get_main_window_geometry(self):
        return None  # fall back to the primary screen corner

    def is_main_window_active(self):
        return True

    def get_main_window_state(self):
        return "normal"

    def get_settings_values(self):
        return {"api_url": "https://api.timeweb.ai/v1",
                "api_key": "", "model_name": "dashscope/qwen3.8-flash"}

    def save_settings(self, api_url, api_key, model_name):
        print("[preview] save_settings(%s, ***, %s)" % (api_url, model_name))

    def showChat(self):
        print("[preview] open web chat")

    def get_slicing_snapshot(self):
        if self.state["slicing"]:
            return {"state": "slicing", "progress": self.state["progress"]}
        return None

    def tools_list(self):
        return [{"name": "run_slicing", "description": u"запуск нарезки с ожиданием"},
                {"name": "export_project_and_gcode", "description": u"экспорт 3mf+gcode"}]

    def submit_chat(self, text, attachments):
        print("[preview] submit_chat: %.60r attachments=%d" % (text, len(attachments)))
        self.state["slicing"] = True
        self.state["progress"] = 0.0
        self.bus.push("user", {"text": text,
                               "attachments": [a["name"] for a in attachments]})
        MockAgentThread(self.bus, self.state).start()
        return True, None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName(u"AI Assistant — QWidgets preview")

    extension = MockExtension()
    ui = W.WidgetsUI(extension)
    ui.startup()
    ui.show_panel()

    # demo attachment chip (real temp file so the path check passes)
    demo_stl = os.path.join(OUT_DIR, "1_Шестерня.stl")
    with open(demo_stl, "wb") as f:
        f.write(b"solid preview\nendsolid preview\n")
    ui._attach_files([demo_stl])

    stay = "--stay" in sys.argv

    def run_demo():
        extension.submit_chat(
            u"Шестерня привода натяжения троса, оранжерея, УФ, 75 Н·м. "
            u"Приложила STL — настрой печать и запусти слайсинг.", [])

    def screenshot():
        panel_png = os.path.join(OUT_DIR, "panel.png")
        mascot_png = os.path.join(OUT_DIR, "mascot.png")
        ui._panel.grab().save(panel_png)
        ui._mascot.grab().save(mascot_png)
        print("[preview] saved %s and %s" % (panel_png, mascot_png))
        if not stay:
            app.quit()

    QTimer.singleShot(600, run_demo)
    QTimer.singleShot(7500, screenshot)   # after the report lands (~5.2s)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
