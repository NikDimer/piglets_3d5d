# -*- coding: utf-8 -*-
#
# test_run_slicing_stall.py — stall detector in run_slicing.
#
# Real-world case (live session 24.10 15:11-15:23): 5D pipeline's openscad
# intersection failed ('Failed to intersect model!'), the backend wedged in
# state=slicing at progress 0.0 forever; run_slicing used to burn the whole
# 600 s timeout. Now it must return final_state="stalled" after
# stall_timeout seconds without progress movement.

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
    um.Logger = logger_mod
    sys.modules["UM"] = um
    sys.modules["UM.Logger"] = logger_mod


install_stubs()

from agent import slicer_tools as T  # noqa: E402


class FakeClock(object):
    """time.time() that only advances via time.sleep()."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _prepare(state, progress):
    clock = FakeClock()
    T.time = clock
    T.execute_on_main_thread = lambda func, *a, **k: True  # skip _start()
    T._state = {"initialized": True,
                "slicing": {"state": state, "progress": progress, "message": ""}}
    return clock


def test_stalled_when_progress_frozen():
    clock = _prepare("slicing", 0.0)
    result = T.run_slicing(wait=True, timeout=600, stall_timeout=5)
    assert result["final_state"] == "stalled", result
    assert result["stalled_seconds"] >= 5, result
    assert "openscad" in result["hint"] or "5D" in result["hint"]
    # must return long before the overall timeout
    assert clock.now - 1000.0 < 600


def test_progress_movement_resets_stall_timer():
    clock = _prepare("slicing", 0.0)
    real_sleep = clock.sleep
    ticks = [0]

    def moving_sleep(seconds):
        real_sleep(seconds)
        ticks[0] += 1
        if ticks[0] % 5 == 0:  # progress creeps -> never stalls
            T._state["slicing"]["progress"] = ticks[0] * 0.05  # unbounded

    clock.sleep = moving_sleep
    result = T.run_slicing(wait=True, timeout=30, stall_timeout=10)
    # progress kept moving: we hit the overall timeout, not the stall
    assert result["final_state"] == "timeout", result


def test_done_returns_result():
    clock = _prepare("done", 1.0)
    T.get_slicing_result = lambda: {"print_time": "1:00:00"}
    result = T.run_slicing(wait=True, timeout=10, stall_timeout=5)
    assert result["final_state"] == "done", result
    assert result["result"]["print_time"] == "1:00:00"


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
