# -*- coding: utf-8 -*-
#
# test_settings_keys.py — settings-key contract tests.
#
# 1) Every key the plugin knows about (SUMMARY_KEYS, KEY_ALIASES targets,
#    the twin suffixes) must exist in THIS build's definition files
#    (STE-Slicer resources/definitions/*.def.json).
# 2) write_setting()/write_settings_batch() behaviour against a fake stack:
#    alias resolution, mode-twin writes, type coercion, batch atomicity.
#
# Runnable outside the slicer: UM is stubbed like in test_plugin_smoke.py.

import json
import os
import sys
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
PLUGIN_DIR = os.path.join(HERE, "plugin", "AIAssistant")
DEFS_DIR = os.path.join(HERE, "STE-Slicer", "STE-Slicer-master",
                        "resources", "definitions")
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


# ----------------------------------------------------------------------------
# 1) real keys from this build's definition files
# ----------------------------------------------------------------------------

def load_build_keys():
    keys = set()
    for name in ("fdmprinter.def.json", "fdmextruder.def.json",
                 "hfe530v5.2.4.def.json"):
        path = os.path.join(DEFS_DIR, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        def absorb(node):
            for key, spec in (node.get("children") or {}).items():
                keys.add(key)
                absorb(spec)

        for _group, spec in (data.get("settings") or {}).items():
            absorb(spec)
    return keys


BUILD_KEYS = load_build_keys()


def test_summary_keys_exist():
    missing = [k for k in T.SUMMARY_KEYS if k not in BUILD_KEYS]
    assert not missing, "SUMMARY_KEYS не найдены в сборке: %s" % missing


def test_alias_targets_exist():
    missing = [v for v in T.KEY_ALIASES.values() if v not in BUILD_KEYS]
    assert not missing, "цели алиасов не найдены в сборке: %s" % missing


def test_alias_sources_really_missing():
    # every alias source must be a key that does NOT exist (true remap)
    existing = [k for k in T.KEY_ALIASES if k in BUILD_KEYS]
    assert not existing, "алиас-источники существуют в сборке (лишние записи): %s" % existing


def test_mode_twins_exist_for_speed_and_fan():
    # this build ships _classic/_cylindrical twins for per-mode settings
    for base in ("speed_infill", "speed_wall_0", "speed_wall_x",
                 "cool_fan_enabled", "cool_fan_speed_min"):
        assert base in BUILD_KEYS, base
        assert base + "_classic" in BUILD_KEYS, base + "_classic"
        assert base + "_cylindrical" in BUILD_KEYS, base + "_cylindrical"
    # base print speed is mode-agnostic in this build (no twins) — fine too
    assert "speed_print" in BUILD_KEYS


# ----------------------------------------------------------------------------
# 2) write_setting / write_settings_batch on a fake stack
# ----------------------------------------------------------------------------

class FakeDefinition(object):
    def __init__(self, key, dtype, options=None):
        self.key = key
        self.type = dtype
        self.options = options or {}
        self.label = key
        self.description = ""


class FakeChanges(object):
    def __init__(self):
        self.values = {}

    def setProperty(self, key, _prop, value):
        self.values[key] = value


class FakeStack(object):
    def __init__(self, defs_by_key):
        self.defs = defs_by_key
        self.userChanges = FakeChanges()

    def getSettingDefinition(self, key):
        return self.defs.get(key)

    def getAllKeys(self):
        return list(self.defs.keys())


DEFS = {}
for key, dtype in [
        ("top_layers", "int"), ("layer_height", "float"),
        ("infill_sparse_density", "float"), ("brim_line_count", "int"),
        ("speed_print", "float"), ("speed_print_classic", "float"),
        ("speed_print_cylindrical", "float"),
        ("cool_fan_enabled", "bool"), ("cool_fan_enabled_classic", "bool"),
        ("cool_fan_enabled_cylindrical", "bool"),
        ("retraction_amount", "float"),
        ("infill_pattern", "enum"),
        ("support_infill_rate", "float")]:
    DEFS[key] = FakeDefinition(key, dtype)
DEFS["infill_pattern"].options = {"gyroid": "Gyroid", "grid": "Grid"}

STACK = FakeStack(DEFS)
T.get_global_stack = lambda: STACK
T.get_extruder_stack = lambda: None


def test_write_setting_basic_and_coercion():
    result = T.write_setting("top_layers", "6")
    assert result["value"] == 6 and isinstance(result["value"], int)
    assert STACK.userChanges.values["top_layers"] == 6
    assert "global" in result["written_to"]


def test_write_setting_alias_resolution():
    result = T.write_setting("print_speed", 40)
    assert result["key"] == "speed_print"
    assert STACK.userChanges.values["speed_print"] == 40.0
    # mode twins must be written with the same value
    assert STACK.userChanges.values["speed_print_classic"] == 40.0
    assert STACK.userChanges.values["speed_print_cylindrical"] == 40.0
    assert set(result["variants"]) == {"speed_print_classic", "speed_print_cylindrical"}


def test_write_setting_bool_and_enum():
    T.write_setting("cool_fan_enabled", "false")
    assert STACK.userChanges.values["cool_fan_enabled"] is False
    assert STACK.userChanges.values["cool_fan_enabled_cylindrical"] is False
    T.write_setting("infill_pattern", "Gyroid")   # by human label
    assert STACK.userChanges.values["infill_pattern"] == "gyroid"


def test_write_settings_batch_atomicity():
    batch = T.write_settings_batch({
        "layer_height": 0.12,
        "wall_line_count": 6,        # no definition in the fake stack
        "retraction_amount": 1.5,
    })
    applied = {r["key"] for r in batch["applied"]}
    assert applied == {"layer_height", "retraction_amount"}
    assert [r["key"] for r in batch["rejected"]] == ["wall_line_count"]
    assert STACK.userChanges.values["layer_height"] == 0.12


def test_alias_and_unknown_key_behaviour():
    # known Cura-style alias -> silently remapped (must NOT raise)
    result = T.write_setting("retract_amount", 2)
    assert result["key"] == "retraction_amount"
    # genuinely unknown key -> clear error
    try:
        T.write_setting("warp_speed", 2)
        raise AssertionError("должен упасть: ключа нет")
    except ValueError as e:
        assert "warp_speed" in str(e)


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
    print("build keys loaded: %d" % len(BUILD_KEYS))
    sys.exit(1 if failures else 0)
