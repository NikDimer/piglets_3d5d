# -*- coding: utf-8 -*-
#
# test_export_threading.py — regression test for the export self-deadlock.
#
# Live hang (faulthandler dump): main thread stuck in Threading.wait under
# GCodeWriter._getPreviewImage <- GCodeWriter.write <- our export, because the
# whole export ran on the Qt main thread and _getPreviewImage is
# @call_on_qt_thread (blocks waiting for the Qt thread = itself).
#
# The fix: export refuses main-thread callers with a clear error and keeps the
# G-code phase on the calling worker thread.

import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(HERE, "plugin", "AIAssistant")
sys.path.insert(0, PLUGIN_DIR)


def install_stubs():
    um = types.ModuleType("UM")
    um.__path__ = []
    logger_mod = types.ModuleType("UM.Logger")

    class _Logger(object):
        @staticmethod
        def log(*a, **k):
            pass

        @staticmethod
        def logException(*a, **k):
            pass

    logger_mod.Logger = _Logger
    registry_mod = types.ModuleType("UM.PluginRegistry")

    class _PluginRegistry(object):
        @staticmethod
        def getInstance():
            return _PluginRegistry()

        def getPluginObject(self, name):
            raise RuntimeError("no plugin objects in test")

    registry_mod.PluginRegistry = _PluginRegistry
    um.Logger = logger_mod
    um.PluginRegistry = registry_mod
    sys.modules["UM"] = um
    sys.modules["UM.Logger"] = logger_mod
    sys.modules["UM.PluginRegistry"] = registry_mod


install_stubs()

from agent import slicer_tools as T  # noqa: E402


class FakeSceneHolder(object):
    def getScene(self):
        return object()

    def getController(self):
        return self


class FakeApp(object):
    def __init__(self, main_thread):
        self._main = main_thread

    def getMainThread(self):
        return self._main

    def getController(self):
        return FakeSceneHolder()


def test_export_refuses_main_thread():
    """Main-thread caller must get a clear RuntimeError, not a deadlock."""
    T.get_app = lambda: FakeApp(threading.current_thread())
    try:
        T.export_project_and_gcode("gear")
        raise AssertionError("export должен отказаться работать в главном потоке")
    except RuntimeError as e:
        assert "главном потоке" in str(e), str(e)


def test_export_worker_thread_passes_guard():
    """Worker-thread caller passes the guard and proceeds into the 3MF phase."""
    T.get_app = lambda: FakeApp("fake-main-thread-object")
    T.execute_on_main_thread = lambda func, *a, **k: func(*a, **k)
    T._iter_sliceable = lambda: []   # controllable failure inside _prepare
    try:
        T.export_project_and_gcode("gear")
        raise AssertionError("_prepare должен упасть: пустая сцена")
    except RuntimeError as e:
        # guard error must NOT fire; the scene error proves we got past it
        assert "главном потоке" not in str(e), str(e)
        assert "нет моделей" in str(e), str(e)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except AssertionError as e:
                failures += 1
                print("FAIL %s: %s" % (name, e))
    sys.exit(1 if failures else 0)
