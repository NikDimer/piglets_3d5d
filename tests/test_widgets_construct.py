# -*- coding: utf-8 -*-
"""Widget-construction smoke test (offscreen).

Builds every AIAssistantWidgets class and pokes the methods the runtime
calls, catching 'valid syntax but broken structure' regressions (e.g. a
builder call stranded inside paintEvent left ChatPanel without sections
and killed the slicer via an uncaught AttributeError in a timer).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_VENV_PLUGINS = os.path.join(os.path.dirname(sys.executable), os.pardir,
                             "Lib", "site-packages", "PyQt5", "Qt5", "plugins")
if os.path.isdir(_VENV_PLUGINS):
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                          os.path.join(_VENV_PLUGINS, "platforms"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "plugin", "AIAssistant"))

from PyQt5.QtWidgets import QApplication

from AIAssistantWidgets import ChatPanel, MascotWidget, SpeechBubble

failures = []


def check(name, fn):
    try:
        fn()
        print("[OK ] %s" % name)
    except Exception as e:
        failures.append((name, e))
        print("[FAIL] %s: %r" % (name, e))


app = QApplication([])

panel = ChatPanel()
mascot = MascotWidget()
bubble = SpeechBubble()

check("panel has progress frame", lambda: panel.set_slicing(False, 0.0))
check("panel sections built",
      lambda: [panel._transcript, panel._input, panel._send_button])
check("panel append user", lambda: panel.append_user(u"привет", [u"a.stl"]))
check("panel append assistant", lambda: panel.append_assistant(u"**Готово**"))
check("panel append tool result",
      lambda: panel.append_tool_result(u"get_scene_objects", True, 10,
                                       u'{"count": 0}'))
check("panel append error", lambda: panel.append_error(u"упс"))
check("panel status/busy/typing",
      lambda: (panel.set_status(u"Готов", "ready"), panel.set_busy(True),
               panel.set_typing(True), panel.set_attachments([u"a.stl"])))
check("mascot states",
      lambda: [mascot.set_state(s) for s in ("idle", "think", "happy")])
check("bubble say sticky", lambda: bubble.say(u"Привет, я Кали!", sticky=True))
check("bubble say two lines",
      lambda: bubble.say(u"Привет, я Кали!\nТебе с чем-то помочь?", sticky=True))
check("bubble say plain", lambda: bubble.say(u"Работаю…"))
check("bubble empty hides", lambda: bubble.say(u""))
check("panel intro present",
      lambda: panel._transcript.toPlainText() or (_ for _ in ()).throw(
          AssertionError("intro missing")))

print("=" * 78)
if failures:
    print("FAILED: %d" % len(failures))
    sys.exit(1)
print("ALL WIDGET TESTS PASSED")
