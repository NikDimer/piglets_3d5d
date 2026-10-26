# -*- coding: utf-8 -*-
#
# slicer_tools.py — MCP tools implementation for STE Slicer + pure-python
# STL geometry analyzer.
#
# All UM/steslicer/PyQt5 imports are done lazily inside functions so this
# module can also be unit-tested outside of the slicer.
# Python 3.5 compatible, standard library only.

import json
import math
import os
import struct
import threading
import time

from UM.Logger import Logger

# ----------------------------------------------------------------------------
# module state
# ----------------------------------------------------------------------------

_state_lock = threading.Lock()
_state = {
    "initialized": False,
    "slicing": {"state": "idle", "progress": 0.0, "message": "",
                "error": None, "started_at": None},
}
_results_dir_override = None

# ----------------------------------------------------------------------------
# slicer log capture — keeps recent warnings/errors so a failed slice can be
# reported to the agent with the actual system error text, not a generic one
# ----------------------------------------------------------------------------

_LOG_RING_SIZE = 400       # ring buffer capacity for w/e/c log lines
_LOG_TAIL_LIMIT = 12       # lines handed to the agent per failure
_log_lock = threading.Lock()
_log_ring = []             # list of (timestamp, log_type, message)
_log_sink_installed = False


class _SlicerLogSink(object):
    """UM.Logger output that mirrors warnings/errors into an in-memory ring.

    Catches everything the slicer logs from any thread: engine stderr,
    5D-pipeline tool failures (openscad), exceptions — including cases where
    the backend wedges in 'slicing' forever and no backendError is emitted."""

    def log(self, log_type, message):
        try:
            if str(log_type) not in ("w", "e", "c"):
                return
            with _log_lock:
                _log_ring.append((time.time(), str(log_type), str(message)))
                if len(_log_ring) > _LOG_RING_SIZE:
                    del _log_ring[:len(_log_ring) - _LOG_RING_SIZE]
        except Exception:
            pass


def _install_log_sink():
    global _log_sink_installed
    if _log_sink_installed:
        return
    try:
        Logger.addLogger(_SlicerLogSink())
        _log_sink_installed = True
    except Exception as e:
        try:
            Logger.log("w", "[AI Assistant] log sink install failed: %s" % e)
        except Exception:
            pass


def _log_tail(since=None, limit=_LOG_TAIL_LIMIT):
    """Recent warning/error log lines; errors/criticals first, warnings only
    to fill up the limit. `since` bounds the slice time window."""
    with _log_lock:
        entries = list(_log_ring)
    if since is not None:
        entries = [e for e in entries if e[0] >= since]
    serious = [e for e in entries if e[1] in ("e", "c")]
    warnings = [e for e in entries if e[1] == "w"]
    picked = (serious + warnings)[-limit:]
    return ["[%s] %s" % (log_type, message[:300]) for _, log_type, message in picked]


_LOG_FILE_LEVEL_TOKENS = (" - ERROR - ", " - CRITICAL - ", " - WARNING - ")


def _slicer_log_files():
    """On-disk slicer logs (*.log in the slicer config storage, e.g.
    steslicer.log) newest first. Best-effort — may be empty outside the
    slicer."""
    try:
        from UM.Resources import Resources
        directory = Resources.getStoragePath(Resources.Resources)
        files = []
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if name.lower().endswith(".log") and os.path.isfile(path):
                files.append((os.path.getmtime(path), path))
        return [path for _, path in sorted(files, reverse=True)]
    except Exception:
        return []


def _tail_log_file(path, limit, errors_only):
    """Last error/warning lines of an on-disk slicer log (Python logging
    format: '2026-10-25 12:00:00,123 - ERROR - message')."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > 256 * 1024:
                f.seek(-256 * 1024, os.SEEK_END)
            data = f.read()
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return []
    wanted = (" - ERROR - ", " - CRITICAL - ") if errors_only else _LOG_FILE_LEVEL_TOKENS
    picked = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line and any(token in line for token in wanted):
            picked.append(line[:300])
    return picked[-max(1, int(limit)):]


def read_slicer_log(limit=40, errors_only=False, minutes=None):
    """MCP tool: the slicer log for the agent. Returns the last warnings/
    errors captured from UM.Logger (same stream that goes into steslicer.log)
    and, when the in-memory ring is short, the tail of the newest on-disk
    *.log from the slicer config folder."""
    limit = max(1, min(int(limit), 200))
    since = None
    if minutes is not None:
        try:
            since = time.time() - float(minutes) * 60.0
        except (TypeError, ValueError):
            since = None
    types = ("e", "c") if errors_only else ("w", "e", "c")
    with _log_lock:
        entries = [e for e in _log_ring if e[1] in types]
    if since is not None:
        entries = [e for e in entries if e[0] >= since]
    lines = ["[%s] %s" % (log_type, message[:300])
             for _, log_type, message in entries[-limit:]]
    source = "ring"
    if len(lines) < limit:
        for path in _slicer_log_files()[:1]:
            file_lines = _tail_log_file(path, limit - len(lines), errors_only)
            if file_lines:
                lines.extend(file_lines)
                source = "ring+file:%s" % os.path.basename(path)
                break
    return {"source": source, "count": len(lines), "lines": lines[-limit:]}

# printing mode values (STE Slicer fdmprinter.def.json -> "printing_mode")
MODE_3D = "classic"
MODE_5D_SPIRAL = "cylindrical"
MODE_5D = "cylindrical_full"
VALID_MODES = [MODE_3D, MODE_5D_SPIRAL, MODE_5D]
MODE_LABELS = {
    MODE_3D: "3D",
    MODE_5D_SPIRAL: "5D Spiral",
    MODE_5D: "5D",
}
MODE_ALIASES = {
    "3d": MODE_3D, "classic": MODE_3D, "классический": MODE_3D, "классика": MODE_3D,
    "5d spiral": MODE_5D_SPIRAL, "spiral": MODE_5D_SPIRAL, "cylindrical": MODE_5D_SPIRAL,
    "спираль": MODE_5D_SPIRAL, "5d спираль": MODE_5D_SPIRAL, "спиральная": MODE_5D_SPIRAL,
    "5d": MODE_5D, "cylindrical_full": MODE_5D, "полное 5d": MODE_5D,
}

# key settings exposed in the summary tool (engineering-relevant params).
# Names verified against THIS build's resources/definitions/fdmprinter.def.json:
# speeds live under speed_*, fan under cool_fan_*, retraction under retraction_*,
# support density is support_infill_rate.
SUMMARY_KEYS = [
    "layer_height", "layer_height_0", "line_width", "wall_line_count",
    "top_layers", "bottom_layers", "infill_sparse_density", "infill_pattern",
    "material_print_temperature", "material_bed_temperature",
    "speed_print", "speed_infill", "speed_wall", "support_enable",
    "support_type", "support_angle", "support_infill_rate", "support_interface_enable",
    "retraction_enable", "retraction_amount", "retraction_speed",
    "adhesion_type", "brim_line_count", "skirt_line_count", "draft_shield_enabled",
    "cool_fan_enabled", "machine_nozzle_size", "material_diameter",
]

# Common Cura-style names the LLM tends to use -> real keys in this build.
KEY_ALIASES = {
    "print_speed": "speed_print",
    "infill_speed": "speed_infill",
    "wall_speed": "speed_wall",
    "outer_wall_speed": "speed_wall_0",
    "inner_wall_speed": "speed_wall_x",
    "top_bottom_speed": "speed_topbottom",
    "travel_speed": "speed_travel",
    "retract_amount": "retraction_amount",
    "retract_enabled": "retraction_enable",
    "retract_speed": "retraction_speed",
    "cooling_fan_enabled": "cool_fan_enabled",
    "cooling_fan_speed": "cool_fan_speed",
    "fan_enabled": "cool_fan_enabled",
    "fan_speed": "cool_fan_speed",
    "support_density": "support_infill_rate",
}

# angular resolution for the outer-radius profile of a cross-section
PROFILE_BINS = 72


def get_app():
    from steslicer.SteSlicerApplication import SteSlicerApplication
    return SteSlicerApplication.getInstance()


# ----------------------------------------------------------------------------
# main-thread bridge
# ----------------------------------------------------------------------------

def execute_on_main_thread(func, *args, **kwargs):
    """Run func in the Qt main thread and synchronously return its result.

    Uses app.callLater (posts a CallFunctionEvent to the Qt event loop) plus a
    threading.Event so the calling (HTTP/agent) thread blocks until the main
    thread executed the function. Exceptions are re-raised in the caller.
    """
    app = get_app()
    if threading.current_thread() is app.getMainThread():
        return func(*args, **kwargs)
    result = {"value": None, "error": None}
    done = threading.Event()

    def _run():
        try:
            result["value"] = func(*args, **kwargs)
        except Exception as e:  # re-raised in the caller thread below
            result["error"] = "%s: %s" % (type(e).__name__, e)
            Logger.logException("e", "[AI Assistant] main-thread call failed")
        finally:
            done.set()

    app.callLater(_run)
    # guard: if the Qt loop is dead, fail after timeout instead of hanging
    if not done.wait(timeout=300):
        raise RuntimeError("main thread did not respond within 300 s")
    if result["error"] is not None:
        raise RuntimeError(result["error"])
    return result["value"]


# ----------------------------------------------------------------------------
# initialization + slicing signal subscription
# ----------------------------------------------------------------------------

def initialize():
    _install_log_sink()
    with _state_lock:
        if _state["initialized"]:
            return
        _state["initialized"] = True
    try:
        execute_on_main_thread(_connect_backend_signals)
    except Exception as e:
        Logger.log("w", "[AI Assistant] initialize failed: %s" % e)


def _connect_backend_signals():
    backend = get_app().getBackend()
    if backend is None:
        return
    try:
        backend.backendStateChange.disconnect(_on_backend_state)
    except TypeError:
        pass
    try:
        backend.processingProgress.disconnect(_on_backend_progress)
    except TypeError:
        pass
    try:
        backend.backendError.disconnect(_on_backend_error)
    except TypeError:
        pass
    backend.backendStateChange.connect(_on_backend_state)
    backend.processingProgress.connect(_on_backend_progress)
    backend.backendError.connect(_on_backend_error)


def _on_backend_state(state):
    # enum: 1 NotStarted, 2 Processing, 3 Done, 4 Error, 5 Disabled
    mapping = {1: "idle", 2: "slicing", 3: "done", 4: "error", 5: "disabled"}
    name = mapping.get(int(state), "unknown")
    with _state_lock:
        _state["slicing"]["state"] = name
        if name == "done":
            _state["slicing"]["progress"] = 1.0
        elif name in ("idle", "error"):
            _state["slicing"]["progress"] = 0.0 if name == "idle" else _state["slicing"]["progress"]


def _on_backend_progress(progress):
    try:
        value = float(progress)
    except (TypeError, ValueError):
        value = 0.0
    with _state_lock:
        _state["slicing"]["progress"] = max(0.0, min(1.0, value))


# ----------------------------------------------------------------------------
# backend error capture — the actual slicer-side failure reason
# ----------------------------------------------------------------------------

# StartJobResult IntEnum values shared by CuraEngineBackend (3D) and
# CylindricalBackend (5D) StartSliceJob.
START_JOB_RESULT_NAMES = {
    1: "Finished",
    2: "Error",
    3: "SettingError",
    4: "NothingToSlice",
    5: "MaterialIncompatible",
    6: "BuildPlateError",
    7: "ObjectSettingError",
    8: "ObjectsWithDisabledExtruder",
}


def _job_result_name(result):
    try:
        name = getattr(result, "name", None)
        if name:
            return str(name)
    except Exception:
        pass
    try:
        return START_JOB_RESULT_NAMES.get(int(result), str(result))
    except (TypeError, ValueError):
        return str(result)


def _error_message_holders():
    """UM.Message pop-ups currently held by the active backend (and any inner
    backends it wraps in 5D mode) — their text is what the user sees on error."""
    holders = []
    try:
        backend = get_app().getBackend()
    except Exception:
        return holders
    candidates = [backend]
    try:
        inner = getattr(backend, "_backends", None)
        if inner:
            candidates.extend(list(inner.values()))
    except Exception:
        pass
    for candidate in candidates:
        message = getattr(candidate, "_error_message", None)
        if message is not None:
            holders.append(message)
    return holders


def _on_backend_error(job):
    """backendError(job) handler: capture WHY the slice failed — the job's
    result code/exception plus the pop-up text shown in the slicer UI — so
    run_slicing can report the actual reason to the agent. May be called from
    a Job worker thread; must never raise."""
    info = {}
    try:
        error = job.getError()
        if error:
            info["job_error"] = "%s: %s" % (type(error).__name__, error)
    except Exception:
        pass
    try:
        result = job.getResult()
        if result is not None:
            info["job_result"] = _job_result_name(result)
    except Exception:
        pass
    try:
        message = job.getMessage()
        if message:
            info["job_message"] = str(message)
    except Exception:
        pass
    for holder in _error_message_holders():
        try:
            text = holder.getText()
        except Exception:
            continue
        if text:
            info["ui_message"] = str(text)
            try:
                title = holder.getTitle()
                if title:
                    info["ui_title"] = str(title)
            except Exception:
                pass
            break
    with _state_lock:
        _state["slicing"]["error"] = info
        summary = (info.get("ui_message") or info.get("job_error")
                   or info.get("job_message") or u"ошибка нарезки")
        _state["slicing"]["message"] = summary[:200]


def _wait_for_error_details(timeout=3.0):
    """backendStateChange(Error) is emitted just before backendError(job);
    poll briefly so the captured reason lands before the error is reported."""
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        with _state_lock:
            if _state["slicing"].get("error"):
                return True
        time.sleep(0.1)
    return False


def _collect_slicing_failure(final_state, since=None):
    """Everything known about why a slice failed: the backendError payload
    (UI message + job result/exception) and the slicer log tail."""
    with _state_lock:
        backend_error = dict(_state["slicing"].get("error") or {})
    return {
        "final_state": final_state,
        "backend_error": backend_error,
        "log_tail": _log_tail(since=since),
    }


def _format_slicing_failure(details):
    """Human-readable lines for the error text handed to the agent."""
    lines = []
    err = details.get("backend_error") or {}
    if err.get("ui_message"):
        title = err.get("ui_title")
        lines.append(u"сообщение слайсера: %s%s" %
                     (u"[%s] " % title if title else u"", err["ui_message"]))
    if err.get("job_result"):
        lines.append(u"код результата: %s" % err["job_result"])
    if err.get("job_error"):
        lines.append(u"исключение: %s" % err["job_error"])
    if err.get("job_message"):
        lines.append(u"детали: %s" % err["job_message"])
    for entry in details.get("log_tail") or []:
        lines.append(u"журнал: %s" % entry)
    return lines


# ----------------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------------

def get_global_stack():
    return get_app().getGlobalContainerStack()


def get_extruder_stack():
    try:
        from steslicer.Settings.ExtruderManager import ExtruderManager
        return ExtruderManager.getInstance().getActiveExtruderStack()
    except Exception:
        return None


def read_setting(key):
    stack = get_global_stack()
    if stack is None:
        return None
    value = None
    ext = get_extruder_stack()
    if ext is not None:
        try:
            value = ext.getProperty(key, "value")
        except Exception:
            value = None
    if value is None:
        try:
            value = stack.getProperty(key, "value")
        except Exception:
            value = None
    return _jsonify(value)


def _resolve_key(key):
    """Map common Cura-style names to this build's real keys."""
    key = str(key).strip()
    return KEY_ALIASES.get(key, key)


def _stack_definition(stack, key):
    """ContainerStack's per-key lookup is getSettingDefinition(); getDefinition
    exists only on containers and returns the DefinitionContainer itself."""
    try:
        return stack.getSettingDefinition(str(key))
    except Exception:
        return None


def write_setting(key, value):
    """Write a setting into userChanges of the extruder stack and the global
    stack (where defined) so it applies and shows up in the slicer UI.

    This fork adds mode-specific twins (<key>_classic / <key>_cylindrical) for
    speeds, fan and supports — the 5D engine reads the _cylindrical variant,
    the 3D engine the _classic one. When a twin exists it is written too, so
    the value holds in every printing mode."""
    stack = get_global_stack()
    if stack is None:
        raise RuntimeError("нет активного принтера (глобальный стек настроек пуст)")
    key = _resolve_key(key)
    definition = _stack_definition(stack, key)
    if definition is None:
        raise ValueError("неизвестный ключ настройки: %s" % key)

    value = _coerce_setting_value(definition, value)
    written = []
    ext = get_extruder_stack()
    prev_ext = _saved_user_change(ext, key) if ext is not None else None
    prev_global = _saved_user_change(stack, key)
    if ext is not None:
        try:
            ext.userChanges.setProperty(key, "value", value)
            written.append("extruder")
        except Exception as e:
            Logger.log("w", "[AI Assistant] extruder write failed for %s: %s" % (key, e))
    try:
        stack.userChanges.setProperty(key, "value", value)
        written.append("global")
    except Exception as e:
        Logger.log("w", "[AI Assistant] global write failed for %s: %s" % (key, e))
    if not written:
        raise RuntimeError("не удалось записать настройку %s" % key)

    # Reject out-of-range values right here: in 5D this build's
    # CylindricalBackend crashes the whole app on an invalid setting.
    state, limits = _validation_info(key)
    if _is_validation_error(state):
        _revert_user_change(ext, key, prev_ext)
        _revert_user_change(stack, key, prev_global)
        raise ValueError(
            u"значение %s=%s отклонено: %s (min=%s, max=%s) — вне "
            u"допустимого диапазона, с ним слайсер падает; запись отменена"
            % (key, _jsonify(value), _state_name(state),
               limits.get("minimum_value"), limits.get("maximum_value")))

    variants = []
    skipped_twins = []
    for suffix in ("_classic", "_cylindrical"):
        twin = key + suffix
        twin_def = _stack_definition(stack, twin)
        if twin_def is None:
            continue
        try:
            twin_value = _coerce_setting_value(twin_def, value)
            prev_twin = _saved_user_change(stack, twin)
            stack.userChanges.setProperty(twin, "value", twin_value)
            twin_state, twin_limits = _validation_info(twin)
            if _is_validation_error(twin_state):
                _revert_user_change(stack, twin, prev_twin)
                skipped_twins.append(
                    u"%s=%s (%s; min=%s, max=%s)"
                    % (twin, _jsonify(twin_value), _state_name(twin_state),
                       twin_limits.get("minimum_value"),
                       twin_limits.get("maximum_value")))
                continue
            variants.append(twin)
        except ValueError:
            raise
        except Exception as e:
            Logger.log("w", "[AI Assistant] twin write failed for %s: %s" % (twin, e))
    result = {"key": key, "value": _jsonify(value),
              "written_to": written, "variants": variants}
    if skipped_twins:
        result["skipped_twins"] = skipped_twins
    return result


def _validation_info(key):
    """Validation state of a setting the same way the slicer's StartSliceJob
    checks it ('validationState'), plus resolved min/max for the message."""
    stack = get_global_stack()
    if stack is None:
        return None, {}
    try:
        state = stack.getProperty(key, "validationState")
    except Exception:
        return None, {}
    limits = {}
    for prop in ("minimum_value", "maximum_value"):
        try:
            limits[prop] = _jsonify(stack.getProperty(key, prop))
        except Exception:
            pass
    return state, limits


def _is_validation_error(state):
    if state is None:
        return False
    name = str(getattr(state, "name", state))
    return name.endswith("Error") or name == "Exception"


def _state_name(state):
    return str(getattr(state, "name", state))


def _revert_user_change(container, key, previous):
    """Undo a userChanges write: restore the old value or drop the override."""
    if container is None:
        return
    try:
        if previous is None:
            container.userChanges.removeProperty(key, "value")
        else:
            container.userChanges.setProperty(key, "value", previous)
    except Exception as e:
        Logger.log("w", "[AI Assistant] revert %s failed: %s" % (key, e))


def _saved_user_change(container, key):
    try:
        return container.userChanges.getProperty(key, "value")
    except Exception:
        return None


def _clamp_to_range(value, limits):
    """Clamp a numeric value into [min, max]; None when not applicable."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    lo = limits.get("minimum_value")
    hi = limits.get("maximum_value")
    try:
        lo = float(lo) if lo is not None else None
    except (TypeError, ValueError):
        lo = None
    try:
        hi = float(hi) if hi is not None else None
    except (TypeError, ValueError):
        hi = None
    if lo is None and hi is None:
        return None
    clamped = number
    if lo is not None and clamped < lo:
        clamped = lo
    if hi is not None and clamped > hi:
        clamped = hi
    if abs(clamped - number) < 1e-12:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return int(clamped)
    return clamped


def _scan_invalid_settings():
    """Settings currently in a hard-error validation state:
    [(stack, key, value, state), ...]."""
    stack = get_global_stack()
    stacks = [stack]
    try:
        stacks.extend(list((stack.extruders or {}).values()))
    except Exception:
        pass
    bad = []
    for st in stacks:
        if st is None:
            continue
        try:
            keys = list(st.getAllKeys())
        except Exception:
            continue
        for key in keys:
            try:
                state = st.getProperty(key, "validationState")
            except Exception:
                continue
            if _is_validation_error(state):
                try:
                    value = st.getProperty(key, "value")
                except Exception:
                    value = None
                bad.append((st, key, value, state))
    return bad


def _setting_limits(st, key):
    limits = {}
    for prop in ("minimum_value", "maximum_value"):
        try:
            limits[prop] = _jsonify(st.getProperty(key, prop))
        except Exception:
            pass
    return limits


def _repair_invalid_settings():
    """Auto-fix settings in a hard-error validation state so the engine
    agrees to slice: clamp into [min, max], else reset to the definition
    default. Returns human-readable list of fixes / unresolved keys."""
    fixes = []
    for st, key, value, state in _scan_invalid_settings():
        limits = _setting_limits(st, key)
        clamped = _clamp_to_range(value, limits)
        if clamped is not None:
            try:
                st.userChanges.setProperty(key, "value", clamped)
                if not _is_validation_error(st.getProperty(key, "validationState")):
                    fixes.append(u"%s: %s -> %s (подрезано по диапазону)"
                                 % (key, _jsonify(value), _jsonify(clamped)))
                    continue
            except Exception:
                pass
        try:
            definition = _stack_definition(st, key)
            default = getattr(definition, "default", None)
            if default is None:
                default = getattr(definition, "defaultValue", None)
            if default is not None:
                st.userChanges.setProperty(key, "value", default)
                if not _is_validation_error(st.getProperty(key, "validationState")):
                    fixes.append(u"%s: %s -> %s (сброшено на default)"
                                 % (key, _jsonify(value), _jsonify(default)))
                    continue
        except Exception:
            pass
        fixes.append(u"%s=%s (%s) — НЕ ИСПРАВЛЕНО, исправьте вручную "
                     u"set_slicer_settings" % (key, _jsonify(value),
                                               _state_name(state)))
    return fixes


def _preflight_settings_check():
    """Before forceSlice: auto-repair invalid settings (the engine refuses to
    slice with them, and this build's CylindricalBackend crashes the app on
    ObjectSettingError in 5D). Slice only what could not be repaired is a
    blocker."""
    try:
        if not get_app().getMachineManager().stacksHaveErrors:
            return
    except Exception:
        return  # cannot tell — let the backend report it
    fixes = _repair_invalid_settings()
    if fixes:
        Logger.log("w", "[AI Assistant] auto-fixed settings: %s"
                   % "; ".join(fixes))
        with _state_lock:
            _state["slicing"]["auto_fixed"] = fixes[:20]
    remaining = _scan_invalid_settings()
    if remaining:
        raise RuntimeError(
            u"нарезка не запущена: не удалось исправить настройки — %s. "
            u"Исправьте ключи через set_slicer_settings: значение должно "
            u"быть в диапазоне min..max (диапазон есть в ответе инструмента)"
            % u"; ".join(u"%s=%s (%s)" % (key, _jsonify(value),
                                          _state_name(state))
                         for _, key, value, state in remaining[:20]))


def write_settings_batch(settings):
    """Apply a dict of settings: valid keys are applied, invalid ones are
    reported in 'rejected' — one bad key must not kill the whole batch."""
    applied = []
    rejected = []
    for raw_key, value in (settings or {}).items():
        try:
            applied.append(write_setting(raw_key, value))
        except Exception as e:
            rejected.append({"key": str(raw_key), "error": str(e)})
    return {"applied": applied, "rejected": rejected}


def _coerce_setting_value(definition, value):
    setting_type = ""
    try:
        setting_type = str(definition.type).lower()
    except Exception:
        pass
    if setting_type in ("int", "optional_int"):
        return int(float(value))
    if setting_type == "float":
        return float(value)
    if setting_type in ("bool", "optional_bool"):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "да", "on")
        return bool(value)
    if setting_type == "enum":
        text = str(value).strip()
        # accept both raw option key and human label
        try:
            options = dict(definition.options)
        except Exception:
            options = {}
        if text in options:
            return text
        for opt_key, opt_label in options.items():
            if str(opt_label).strip().lower() == text.lower():
                return opt_key
        return text
    return value


def _jsonify(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    # Qt variants and other objects -> string
    return str(value)


# ----------------------------------------------------------------------------
# printing mode
# ----------------------------------------------------------------------------

def _find_printing_mode_key():
    """Locate the printing-mode key. Normally 'printing_mode'; fall back to
    scanning stack keys for an enum whose options contain classic/cylindrical."""
    stack = get_global_stack()
    if stack is None:
        raise RuntimeError("нет активного принтера")
    try:
        if _stack_definition(stack, "printing_mode") is not None:
            return "printing_mode"
    except Exception:
        pass
    try:
        for key in stack.getAllKeys():
            definition = _stack_definition(stack, key)
            if definition is None:
                continue
            try:
                if str(definition.type).lower() != "enum":
                    continue
                options = dict(definition.options)
            except Exception:
                continue
            values = set(str(k) for k in options.keys())
            if {"classic", "cylindrical", "cylindrical_full"} <= values:
                return key
    except Exception:
        pass
    return "printing_mode"


def normalize_mode(mode):
    if mode is None:
        raise ValueError("режим печати не указан")
    text = str(mode).strip().lower()
    if text in MODE_ALIASES:
        return MODE_ALIASES[text]
    # direct match on english labels like "5D Spiral"
    for value, label in MODE_LABELS.items():
        if text == label.lower():
            return value
    raise ValueError("неизвестный режим: %s. Доступны: 3D (classic), 5D Spiral "
                     "(cylindrical), 5D (cylindrical_full)" % mode)


def set_printing_mode(mode):
    value = normalize_mode(mode)
    key = execute_on_main_thread(_find_printing_mode_key)

    def _apply():
        stack = get_global_stack()
        if stack is None:
            raise RuntimeError("нет активного принтера")
        ext = get_extruder_stack()
        if ext is not None:
            try:
                ext.userChanges.setProperty(key, "value", value)
            except Exception as e:
                Logger.log("w", "[AI Assistant] mode ext write: %s" % e)
        stack.userChanges.setProperty(key, "value", value)
        return _read_mode(key)
    return execute_on_main_thread(_apply)


def _read_mode(key=None):
    stack = get_global_stack()
    if stack is None:
        return None
    if key is None:
        key = "printing_mode"
    try:
        value = stack.getProperty(key, "value")
    except Exception:
        value = None
    value = str(value) if value is not None else None
    label = MODE_LABELS.get(value, value)
    return {"value": value, "label": label}


# ----------------------------------------------------------------------------
# machine / profiles / materials
# ----------------------------------------------------------------------------

def get_machine_summary():
    app = get_app()
    stack = get_global_stack()
    if stack is None:
        return {"active_printer": None}
    name = None
    try:
        name = stack.getName()
    except Exception:
        pass
    if not name:
        try:
            name = stack.definition.getName()
        except Exception:
            name = stack.definition.id
    mm = app.getMachineManager()
    material_id = None
    quality_type = None
    try:
        material_id = mm.activeMaterialId
    except Exception:
        pass
    try:
        quality_type = mm.activeQualityType
    except Exception:
        pass
    return {
        "active_printer": name,
        "machine_definition": stack.definition.id,
        "active_material_id": material_id,
        "active_quality_type": quality_type,
    }


def get_settings_summary():
    stack = get_global_stack()
    if stack is None:
        raise RuntimeError("нет активного принтера")
    values = {}
    for key in SUMMARY_KEYS:
        try:
            values[key] = read_setting(key)
        except Exception:
            values[key] = None
    return values


def search_settings_definitions(query):
    stack = get_global_stack()
    if stack is None:
        raise RuntimeError("нет активного принтера")
    q = str(query or "").strip().lower()
    results = []
    try:
        keys = list(stack.getAllKeys())
    except Exception:
        keys = list(SUMMARY_KEYS)
    for key in keys:
        try:
            definition = _stack_definition(stack, key)
        except Exception:
            continue
        if definition is None:
            continue
        try:
            label = str(definition.label)
        except Exception:
            label = key
        try:
            description = str(definition.description or "")
        except Exception:
            description = ""
        if q and q not in key.lower() and q not in label.lower() and q not in description.lower():
            continue
        options = []
        try:
            options = [{"value": str(k), "label": str(v)}
                       for k, v in dict(definition.options).items()]
        except Exception:
            pass
        try:
            setting_type = str(definition.type)
        except Exception:
            setting_type = "str"
        try:
            unit = str(definition.unit or "")
        except Exception:
            unit = ""
        results.append({
            "key": key, "label": label, "description": description[:200],
            "type": setting_type, "unit": unit, "options": options,
            "current_value": read_setting(key),
        })
        if len(results) >= 30:
            break
    return {"query": query, "count": len(results), "results": results}


def list_profiles_and_materials():
    app = get_app()
    stack = get_global_stack()
    if stack is None:
        raise RuntimeError("нет активного принтера")

    qualities = []
    try:
        qm = app.getQualityManager()
        groups = qm.getQualityGroups(stack)
        for quality_type, group in groups.items():
            qualities.append({
                "quality_type": str(quality_type),
                "name": str(getattr(group, "name", quality_type)),
            })
    except Exception as e:
        Logger.log("w", "[AI Assistant] quality listing failed: %s" % e)

    materials = []
    try:
        materials = _list_materials_fdmprinter(app, stack)
    except Exception as e:
        Logger.log("w", "[AI Assistant] material listing failed: %s" % e)

    active = get_machine_summary()
    return {
        "qualities": sorted(qualities, key=lambda x: x["quality_type"]),
        "materials": sorted(materials, key=lambda x: x["name"]),
        "active": active,
    }


def _list_materials_fdmprinter(app, stack):
    """List material containers available to this machine.
    Primary path: ContainerRegistry.findContainersMetadata (fast, global);
    fallback: instance containers with metadata type=material."""
    from UM.Settings.ContainerRegistry import ContainerRegistry
    registry = ContainerRegistry.getInstance()
    materials = []
    seen = set()

    metadata_list = []
    try:
        metadata_list = registry.findContainersMetadata(type="material")
    except Exception as e:
        Logger.log("w", "[AI Assistant] findContainersMetadata failed: %s" % e)
    for meta in metadata_list:
        try:
            base_file = meta.get("base_file") or meta.get("id") or ""
            if not base_file or base_file in seen:
                continue
            seen.add(base_file)
            name = meta.get("material") or meta.get("name") or base_file
            brand = meta.get("brand") or ""
            materials.append({
                "id": str(base_file),
                "name": ("%s %s" % (brand, name)).strip() if brand else str(name),
                "brand": str(brand),
                "color": str(meta.get("color_code") or meta.get("color_name") or ""),
            })
        except Exception:
            continue
    if materials:
        return materials

    containers = []
    try:
        containers = registry.findInstanceContainers(metadata="type", value="material")
    except Exception:
        containers = []
    for container in containers:
        try:
            meta = container.getMetaData()
            base_file = meta.get("base_file") or container.getId()
            if base_file in seen:
                continue
            seen.add(base_file)
            name = meta.get("material") or meta.get("name") or container.getName()
            brand = meta.get("brand") or ""
            materials.append({
                "id": str(base_file),
                "name": ("%s %s" % (brand, name)).strip() if brand else str(name),
                "brand": str(brand),
                "color": str(meta.get("color_code") or meta.get("color_name") or ""),
            })
        except Exception:
            continue
    return materials


def set_material_and_profile(material_id=None, quality_type=None,
                             save_profile_name=None):
    app = get_app()
    stack = get_global_stack()
    if stack is None:
        raise RuntimeError("нет активного принтера")
    mm = app.getMachineManager()

    def _apply():
        changed = []
        if material_id:
            position = "0"
            try:
                # first extruder position for this machine
                positions = sorted(stack.extruders.keys())
                if positions:
                    position = str(positions[0])
            except Exception:
                pass
            mm.setMaterialById(position, str(material_id))
            changed.append("material=%s" % material_id)
        if quality_type:
            # validate against available quality groups
            try:
                groups = app.getQualityManager().getQualityGroups(stack)
                if str(quality_type) not in groups:
                    available = sorted(str(k) for k in groups.keys())
                    raise ValueError("профиль качества '%s' не найден. Доступны: %s"
                                     % (quality_type, ", ".join(available)))
            except ValueError:
                raise
            except Exception:
                pass
            mm.setQualityGroupByQualityType(str(quality_type))
            changed.append("quality=%s" % quality_type)
        if save_profile_name:
            _save_current_as_profile(app, str(save_profile_name))
            changed.append("saved_profile=%s" % save_profile_name)
        return changed

    changed = execute_on_main_thread(_apply)
    # switching material/quality can wipe userChanges -> re-check the mode
    try:
        mode = _read_mode()
    except Exception:
        mode = None
    return {"changed": changed, "printing_mode_after": mode}


def _save_current_as_profile(app, name):
    """Persist current user changes as a custom quality profile."""
    try:
        from steslicer.Settings import SteSlicerStackBuilder
        stack = get_global_stack()
        builder = SteSlicerStackBuilder
        new_quality = builder.createQualityFromStack(stack, name)
        mm = app.getMachineManager()
        try:
            mm.setQualityGroupByQualityType(new_quality.getQualityType())
        except Exception as e:
            Logger.log("w", "[AI Assistant] set saved profile failed: %s" % e)
    except Exception as e:
        Logger.log("w", "[AI Assistant] save profile failed: %s" % e)


# ----------------------------------------------------------------------------
# scene / models
# ----------------------------------------------------------------------------

def _iter_sliceable():
    from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
    root = get_app().getController().getScene().getRoot()
    return [n for n in DepthFirstIterator(root) if n.callDecoration("isSliceable")]


def _node_snapshot():
    nodes = execute_on_main_thread(_iter_sliceable)
    return set(id(n) for n in nodes)


def _node_info(node):
    info = {}
    try:
        info["name"] = str(node.getName())
    except Exception:
        info["name"] = "node"
    try:
        bbox = node.getBoundingBox()
        if bbox is not None:
            info["bbox"] = {
                "width": round(float(bbox.width), 2),
                "height": round(float(bbox.height), 2),
                "depth": round(float(bbox.depth), 2),
                "center": [round(float(bbox.center.x), 2),
                           round(float(bbox.center.y), 2),
                           round(float(bbox.center.z), 2)],
                "bottom": round(float(bbox.bottom), 2),
            }
    except Exception:
        pass
    try:
        pos = node.getPosition()
        if pos is not None:
            info["position"] = [round(float(pos.x), 2), round(float(pos.y), 2),
                                round(float(pos.z), 2)]
    except Exception:
        pass
    try:
        rot = node.getRotation()
        if rot is not None:
            info["rotation_euler_deg"] = [round(math.degrees(float(rot.x)), 1),
                                          round(math.degrees(float(rot.y)), 1),
                                          round(math.degrees(float(rot.z)), 1)]
    except Exception:
        pass
    try:
        scale = node.getScale()
        if scale is not None:
            info["scale"] = [round(float(scale.x), 3), round(float(scale.y), 3),
                             round(float(scale.z), 3)]
    except Exception:
        pass
    try:
        info["is_selected"] = bool(node.isSelected())
    except Exception:
        pass
    return info


def get_scene_objects():
    def _collect():
        return [_node_info(n) for n in _iter_sliceable()]
    return execute_on_main_thread(_collect)


_MODEL_EXTENSIONS = (".stl", ".obj", ".3mf", ".ply", ".amf")


def _strip_upload_prefix(name):
    """Web uploads are saved as '<millis>_<original name>' — drop the prefix."""
    parts = str(name).split("_", 1)
    if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) >= 8:
        return parts[1]
    return str(name)


def _model_search_dirs():
    """Folders where a bare model filename may actually live (the slicer's
    CWD is its install dir, so abspath alone points the wrong way)."""
    dirs = []

    def _add(path):
        try:
            path = str(path)
            if path and os.path.isdir(path) and path not in dirs:
                dirs.append(path)
        except Exception:
            pass

    # plugin work dir (web uploads) + results dir, <config>/ level
    try:
        plugins_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        config_dir = os.path.dirname(plugins_dir)
        _add(os.path.join(config_dir, "AIAssistant_files"))
        _add(os.path.join(config_dir, "AIAssistant_results"))
    except Exception:
        pass
    # the folder the user last loaded a model from (slicer file dialog)
    try:
        _add(get_app().getPreferences().getValue("local_file/dialog_load_path"))
    except Exception:
        pass
    # common user folders
    home = os.path.expanduser("~")
    for name in ("Desktop", "Documents", "Downloads"):
        _add(os.path.join(home, name))
    return dirs


def _resolve_model_path(file_path):
    """Locate the model file even when the agent passes a bare filename or
    a path glued to the slicer's install dir. Exact file -> exact basename in
    known dirs -> upload-prefixed/stem match -> shallow walk (depth 2)."""
    raw = str(file_path).strip().strip('"').strip("'")
    candidate = os.path.abspath(raw)
    if os.path.isfile(candidate):
        return candidate
    base = os.path.basename(raw.replace("\\", "/"))
    stem = base.rsplit(".", 1)[0].lower()
    ext = ("." + base.rsplit(".", 1)[1].lower()) if "." in base else ""
    dirs = _model_search_dirs()

    def _match(name):
        low = str(name).lower()
        if low == base.lower():
            return True
        cleaned = _strip_upload_prefix(low)
        if cleaned == base.lower():
            return True
        return bool(ext) and cleaned.endswith(ext) and \
            cleaned.rsplit(".", 1)[0] == stem

    for directory in dirs:                      # exact name in known dirs
        path = os.path.join(directory, base)
        if os.path.isfile(path):
            return path
    for directory in dirs:                      # prefixed/case match, depth 0
        try:
            names = os.listdir(directory)[:2000]
        except Exception:
            continue
        for name in names:
            path = os.path.join(directory, name)
            if _match(name) and os.path.isfile(path):
                return path
    walk = list(dirs)                           # shallow walk, depth 2
    for _ in range(2):
        next_level = []
        for d in walk:
            try:
                entries = os.listdir(d)[:2000]
            except Exception:
                continue
            for name in entries:
                path = os.path.join(d, name)
                if os.path.isfile(path):
                    if _match(name):
                        return path
                else:
                    next_level.append(path)
        walk = next_level
    hints = []
    for directory in dirs:
        try:
            found = [n for n in os.listdir(directory)
                     if n.lower().endswith(_MODEL_EXTENSIONS)][:10]
            if found:
                hints.append(u"%s: %s" % (directory, u"; ".join(found)))
        except Exception:
            pass
    raise ValueError(
        u"файл не найден: %s (путь %s — это папка установки слайсера, модель "
        u"лежит в другом месте). Искали в: %s. %s. Передайте полный путь или "
        u"точное имя из списка выше."
        % (raw, candidate, u"; ".join(dirs),
           (u"Доступные модели: " + u" | ".join(hints)) if hints
           else u"В рабочих папках моделей не найдено"))


def load_model(file_path):
    """Load a model file onto the build plate; returns info about the new node."""
    from PyQt5.QtCore import QUrl
    file_path = _resolve_model_path(file_path)

    before = _node_snapshot()

    def _load():
        app = get_app()
        app.readLocalFile(QUrl.fromLocalFile(file_path))
    execute_on_main_thread(_load)

    # readLocalFile starts an async ReadFileJob — wait for a new node
    new_nodes = []
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(0.25)
        def _diff():
            nodes = _iter_sliceable()
            return [n for n in nodes if id(n) not in before]
        new_nodes = execute_on_main_thread(_diff)
        if new_nodes:
            break
    if not new_nodes:
        raise RuntimeError("модель не загрузилась в течение 20 с: %s" % file_path)

    # drop onto the platform (bottom of bbox at z=0) and center
    def _place():
        app = get_app()
        node = new_nodes[0]
        try:
            bbox = node.getBoundingBox()
            if bbox is not None:
                from UM.Math.Vector import Vector
                from UM.Operations.TranslateOperation import TranslateOperation
                delta = Vector(-float(bbox.center.x), -float(bbox.bottom), -float(bbox.center.y))
                TranslateOperation(node, delta, set_position=True).redo()
        except Exception as e:
            Logger.log("w", "[AI Assistant] placement failed: %s" % e)
        try:
            app.arrangeAll()
        except Exception:
            pass
        return _node_info(node)
    info = execute_on_main_thread(_place)
    info["source_file"] = os.path.basename(file_path)
    info["resolved_path"] = file_path
    return info


def transform_model(target="all", translate=None, rotate_xyz=None, scale=None,
                    uniform_scale=None, drop_to_bed=True):
    """transform a model (or all models): translate [x,y,z] mm (absolute set_position),
    rotate_xyz [rx,ry,rz] degrees, scale [sx,sy,sz] / uniform_scale factor."""
    from UM.Math.Quaternion import Quaternion
    from UM.Math.Vector import Vector
    from UM.Operations.TranslateOperation import TranslateOperation
    from UM.Operations.RotateOperation import RotateOperation
    from UM.Operations.ScaleOperation import ScaleOperation

    def _apply():
        nodes = _select_nodes(target)
        results = []
        for node in nodes:
            if rotate_xyz:
                rx, ry, rz = [math.radians(float(a)) for a in rotate_xyz]
                # intrinsic XYZ: apply Z, then Y, then X
                q = (Quaternion.fromAngleAxis(rz, Vector.Unit_Z) *
                     Quaternion.fromAngleAxis(ry, Vector.Unit_Y) *
                     Quaternion.fromAngleAxis(rx, Vector.Unit_X))
                center = node.getBoundingBox().center if node.getBoundingBox() else Vector(0, 0, 0)
                RotateOperation(node, q, rotate_around_point=center).redo()
            if uniform_scale:
                s = float(uniform_scale)
                ScaleOperation(node, Vector(s, s, s)).redo()
            elif scale:
                ScaleOperation(node, Vector(float(scale[0]), float(scale[1]),
                                            float(scale[2]))).redo()
            if drop_to_bed:
                _drop_node_to_bed(node)
            if translate:
                TranslateOperation(
                    node,
                    Vector(float(translate[0]), float(translate[1]), float(translate[2])),
                    set_position=True).redo()
            results.append(_node_info(node))
        return results
    return execute_on_main_thread(_apply)


def _select_nodes(target):
    nodes = _iter_sliceable()
    if not nodes:
        raise RuntimeError("на столе нет моделей")
    if target in (None, "", "all"):
        return nodes
    text = str(target).strip().lower()
    by_name = [n for n in nodes if text in str(n.getName()).lower()]
    if by_name:
        return by_name
    try:
        index = int(target)
        if 0 <= index < len(nodes):
            return [nodes[index]]
    except (TypeError, ValueError):
        pass
    selected = [n for n in nodes if n.isSelected()]
    if selected:
        return selected
    raise ValueError("модель '%s' не найдена на столе" % target)


def _drop_node_to_bed(node):
    """Move node down so its bounding-box bottom sits at z=0."""
    from UM.Math.Vector import Vector
    from UM.Operations.TranslateOperation import TranslateOperation
    bbox = node.getBoundingBox()
    if bbox is None:
        return
    bottom = float(bbox.bottom)
    if abs(bottom) > 1e-6:
        TranslateOperation(node, Vector(0, -bottom, 0)).redo()


def arrange_and_clear_scene(action="arrange"):
    def _run():
        app = get_app()
        if str(action).lower() in ("clear", "clear_all", "очистить"):
            from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
            removed = 0
            for node in _iter_sliceable():
                try:
                    node.setParent(None)
                except Exception:
                    pass
                RemoveSceneNodeOperation(node).redo()
                removed += 1
            return {"action": "clear", "removed": removed}
        elif str(action).lower() in ("arrange", "arrange_all", "расставить"):
            app.arrangeAll()
            return {"action": "arrange", "objects": len(_iter_sliceable())}
        else:
            raise ValueError("action должен быть 'arrange' или 'clear'")
    return execute_on_main_thread(_run)


# ----------------------------------------------------------------------------
# slicing
# ----------------------------------------------------------------------------

def run_slicing(wait=True, timeout=600, stall_timeout=180):
    """Start a slice and wait. Besides the overall timeout, a stall detector
    fires when the engine reports state=slicing but progress has not moved
    for stall_timeout seconds — this happens when the 5D pipeline's external
    tools fail (e.g. openscad intersection dies: 'Failed to intersect model!'
    in steslicer.log) and the backend wedges in 'slicing' at 0% forever.
    Waiting the full timeout in that case just wastes 10 minutes.

    On any failure the agent gets the actual slicer-side reason: the
    backendError payload (UI pop-up text, job result code, exception) plus
    the tail of the slicer log — see _collect_slicing_failure."""
    started_at = time.time()

    def _start():
        initialize()
        _connect_backend_signals()
        backend = get_app().getBackend()
        if backend is None:
            raise RuntimeError("бэкенд слайсинга недоступен")
        _preflight_settings_check()  # invalid settings crash 5D slicing
        backend.forceSlice()
        with _state_lock:
            _state["slicing"] = {"state": "slicing", "progress": 0.0,
                                 "message": u"запущена нарезка",
                                 "error": None, "started_at": started_at}
        return True
    execute_on_main_thread(_start)
    if not wait:
        return {"started": True}
    last_progress = -1.0
    last_change = started_at
    idle_since = None
    while True:
        with _state_lock:
            slicing = dict(_state["slicing"])
        now = time.time()
        if slicing["state"] == "done":
            return {"started": True, "final_state": "done",
                    "progress": 1.0, "result": get_slicing_result()}
        if slicing["state"] == "error":
            # backendStateChange(Error) fires right before backendError(job):
            # give the payload a moment to land so the reason is not lost
            _wait_for_error_details(3.0)
            details = _collect_slicing_failure("error", started_at - 2.0)
            lines = _format_slicing_failure(details)
            message = u"слайсинг завершился с ошибкой"
            if lines:
                message += u":\n" + u"\n".join(lines)
            else:
                message += (u" (причина не зафиксирована в сигналах бэкенда — "
                            u"вызови инструмент read_slicer_log и разбери "
                            u"журнал; в 5D частая причина — неактивная "
                            u"лицензия)")
            raise RuntimeError(message)
        if slicing["state"] == "slicing":
            idle_since = None
            if abs(slicing.get("progress", 0.0) - last_progress) > 1e-6:
                last_progress = slicing.get("progress", 0.0)
                last_change = now
            elif now - last_change >= stall_timeout:
                details = _collect_slicing_failure("stalled", started_at - 2.0)
                return {
                    "started": True, "final_state": "stalled",
                    "progress": last_progress,
                    "stalled_seconds": int(now - last_change),
                    "backend_error": details["backend_error"],
                    "log_tail": details["log_tail"],
                    "hint": u"это прямая инструкция тебе, а не системное "
                            u"сообщение: движок застрял без прогресса. Разбери "
                            u"причину по log_tail (сбой 5D-конвейера: openscad "
                            u"'Failed to intersect model!', негерметичная "
                            u"модель) или вызови read_slicer_log. Исправляй "
                            u"сам: повтори в 3D (classic), проверь модель на "
                            u"замкнутость; если в журнале про лицензию — "
                            u"сообщи пользователю точную причину.",
                }
        elif slicing["state"] == "idle":
            # Backend went back to NotStarted without finishing: the start job
            # was rejected (e.g. MaterialIncompatible without platform
            # activity) or cancelled — report it instead of idling to timeout.
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= 5.0:
                details = _collect_slicing_failure("aborted", started_at - 2.0)
                return {
                    "started": True, "final_state": "aborted",
                    "progress": slicing.get("progress", 0.0),
                    "backend_error": details["backend_error"],
                    "log_tail": details["log_tail"],
                    "hint": u"это прямая инструкция тебе: бэкенд вернулся в "
                            u"idle, не завершив нарезку — задание, вероятно, "
                            u"отклонено (несовместимый материал/настройки) "
                            u"или отменено. Причина — в backend_error и "
                            u"log_tail; если неясна — вызови read_slicer_log "
                            u"и исправь причину сам.",
                }
        if now - started_at >= float(timeout):
            details = _collect_slicing_failure("timeout", started_at - 2.0)
            return {"started": True, "final_state": "timeout",
                    "progress": slicing.get("progress", 0.0),
                    "backend_error": details["backend_error"],
                    "log_tail": details["log_tail"],
                    "hint": u"это прямая инструкция тебе: истек таймаут "
                            u"ожидания. Разбери по log_tail, что делал "
                            u"движок (read_slicer_log — если нужно больше "
                            u"строк), и реши: ждать дольше (timeout=...), "
                            u"исправить настройки/геометрию или повторить "
                            u"в 3D (classic)."}
        time.sleep(0.3)


def get_slicing_state_snapshot():
    """Thread-safe read of the slicing state/progress (safe from any thread,
    no Qt roundtrip — used by the native QML window poller)."""
    with _state_lock:
        return dict(_state["slicing"])


def get_slicing_result():
    def _collect():
        info = get_app().getPrintInformation()
        result = {}
        try:
            total = int(info.currentPrintTime)
            hours, remainder = divmod(total, 3600)
            minutes, seconds = divmod(remainder, 60)
            result["print_time"] = "%d:%02d:%02d" % (hours, minutes, seconds)
            result["print_time_seconds"] = total
        except Exception:
            result["print_time"] = None
        try:
            result["material_lengths_m"] = [round(float(x), 2)
                                            for x in (info.materialLengths or [])]
        except Exception:
            result["material_lengths_m"] = []
        try:
            result["material_weights_g"] = [round(float(x), 1)
                                            for x in (info.materialWeights or [])]
        except Exception:
            result["material_weights_g"] = []
        with _state_lock:
            result["slicing"] = dict(_state["slicing"])
        return result
    return execute_on_main_thread(_collect)


def get_status_snapshot():
    def _snapshot():
        data = get_machine_summary()
        try:
            data["printing_mode"] = _read_mode()
        except Exception:
            data["printing_mode"] = None
        try:
            data["scene_objects"] = len(_iter_sliceable())
        except Exception:
            data["scene_objects"] = 0
        try:
            info = get_app().getPrintInformation()
            if info.currentPrintTime is not None:
                total = int(info.currentPrintTime)
                hours, remainder = divmod(total, 3600)
                minutes, seconds = divmod(remainder, 60)
                data["print_time"] = "%d:%02d:%02d" % (hours, minutes, seconds)
            weights = []
            try:
                weights = [round(float(x), 1) for x in (info.materialWeights or [])]
            except Exception:
                pass
            data["material_weights_g"] = weights
        except Exception:
            data["print_time"] = None
        with _state_lock:
            data["slicing"] = dict(_state["slicing"])
        return data
    return execute_on_main_thread(_snapshot)


# ----------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------

def get_results_dir():
    global _results_dir_override
    if _results_dir_override:
        return _results_dir_override
    try:
        value = get_app().getPreferences().getValue("ai_assistant/results_dir")
        if value:
            _results_dir_override = str(value)
            return _results_dir_override
    except Exception:
        pass
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "AIAssistant_results")


def set_results_dir_override(path):
    global _results_dir_override
    if path:
        _results_dir_override = str(path)


def export_project_and_gcode(model_name="model", output_dir=None):
    """Export project.3mf + result.gcode.

    Two phases on purpose:
    - 3MF needs the scene graph -> runs on the Qt main thread;
    - GCodeWriter.write MUST run on a worker thread: its _getPreviewImage is
      decorated @call_on_qt_thread (posts to the Qt thread and blocks until it
      runs). Calling it ON the Qt thread waits for itself -> permanent freeze
      (proved by a live hang dump: main thread stuck in Threading.wait under
      GCodeWriter._getPreviewImage called from our main-thread export).
    """
    app = get_app()
    if threading.current_thread() is app.getMainThread():
        raise RuntimeError(
            u"export_project_and_gcode нельзя вызывать в главном потоке Qt: "
            u"GCodeWriter._getPreviewImage ждёт главный поток (само-дедлок)")

    def _prepare():
        from UM.PluginRegistry import PluginRegistry
        scene = get_app().getController().getScene()
        nodes = _iter_sliceable()
        if not nodes:
            raise RuntimeError("на столе нет моделей для экспорта")

        safe_name = "".join(c if (c.isalnum() or c in " _-.()") else "_"
                            for c in str(model_name)).strip() or "model"
        directory = output_dir or get_results_dir()
        directory = os.path.join(str(directory), safe_name)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            raise RuntimeError("не создать папку %s: %s" % (directory, e))

        project_path = os.path.join(directory, "project.3mf")
        writer_3mf = PluginRegistry.getInstance().getPluginObject("3MFWriter")
        with open(project_path, "wb") as stream:
            ok = writer_3mf.write(stream, [scene.getRoot()])
        if not ok:
            raise RuntimeError("3MFWriter вернул ошибку при записи project.3mf")
        return project_path, os.path.join(directory, "result.gcode")

    project_path, gcode_path = execute_on_main_thread(_prepare)

    # G-code phase: caller thread (agent/HTTP worker) — see the docstring.
    from UM.PluginRegistry import PluginRegistry
    writer_gcode = PluginRegistry.getInstance().getPluginObject("GCodeWriter")
    with open(gcode_path, "w", encoding="utf-8", newline="\n") as stream:
        ok = writer_gcode.write(stream, None)
    if not ok:
        raise RuntimeError("GCodeWriter вернул ошибку. Сначала выполните "
                           "слайсинг (run_slicing) и дождитесь статуса done")

    return {
        "model_name": os.path.basename(os.path.dirname(gcode_path)),
        "directory": os.path.dirname(gcode_path),
        "project_3mf": project_path,
        "gcode": gcode_path,
        "project_3mf_bytes": os.path.getsize(project_path),
        "gcode_bytes": os.path.getsize(gcode_path),
        "hint": "положите скриншоты переписки chat_*.png рядом для сдачи",
    }


# ----------------------------------------------------------------------------
# STL geometry analyzer (killer feature) — pure python
# ----------------------------------------------------------------------------

def _parse_stl(path):
    """Parse binary or ASCII STL -> (triangles, normals) with float lists.

    triangles: flat list of 9 floats per triangle (v1,v2,v3)
    normals:   flat list of 3 floats per triangle
    """
    with open(path, "rb") as f:
        data = f.read()
    size = len(data)
    if size >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if size == 84 + count * 50:
            return _parse_binary_stl(data, count)
    head = data[:512].lstrip()
    if head[:5].lower() == b"solid" and b"facet" in head:
        return _parse_ascii_stl(data)
    if size >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if count > 0 and size >= 84 + count * 50:
            return _parse_binary_stl(data, count)
    raise ValueError("не удалось распознать формат STL: %s" % path)


def _parse_binary_stl(data, count):
    triangles = []
    normals = []
    offset = 84
    end = min(count, (len(data) - 84) // 50)
    for i in range(end):
        base = offset + i * 50
        normals.extend(struct.unpack_from("<3f", data, base))
        triangles.extend(struct.unpack_from("<9f", data, base + 12))
    return triangles, normals


def _parse_ascii_stl(data):
    import re as _re
    text = data.decode("utf-8", errors="ignore")
    triangles = []
    normals = []
    vertex_re = _re.compile(r"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)")
    normal_re = _re.compile(r"normal\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)")
    verts = []
    pending_normal = [0.0, 0.0, 0.0]
    lines = text.replace("\r", "\n").split("\n")
    for line in lines:
        low = line.strip().lower()
        m = normal_re.match(low)
        if m:
            pending_normal = [float(m.group(1)), float(m.group(2)), float(m.group(3))]
            continue
        m = vertex_re.match(low)
        if m:
            verts.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
            if len(verts) == 3:
                for v in verts:
                    triangles.extend(v)
                normals.extend(pending_normal)
                verts = []
    if len(triangles) % 9:
        triangles = triangles[:len(triangles) - len(triangles) % 9]
        normals = normals[:len(normals) - len(normals) % 3]
    return triangles, normals


def _triangle_area(v0, v1, v2):
    ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    vx, vy, vz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def analyze_stl_geometry(file_path):
    """Analyze STL: bbox, volume, overhangs per orientation, axial symmetry,
    3D/5D recommendation. Pure python, fast enough for ~500k triangles."""
    path = str(file_path)
    if not os.path.isfile(path):
        raise ValueError("файл не найден: %s" % path)
    triangles, normals = _parse_stl(path)
    tri_count = len(triangles) // 9
    if tri_count == 0:
        raise ValueError("в STL нет треугольников")

    # bbox + volume via signed tetrahedra
    minx = miny = minz = float("inf")
    maxx = maxy = maxz = float("-inf")
    volume = 0.0
    for i in range(tri_count):
        base = i * 9
        x1, y1, z1 = triangles[base], triangles[base + 1], triangles[base + 2]
        x2, y2, z2 = triangles[base + 3], triangles[base + 4], triangles[base + 5]
        x3, y3, z3 = triangles[base + 6], triangles[base + 7], triangles[base + 8]
        minx = min(minx, x1, x2, x3); maxx = max(maxx, x1, x2, x3)
        miny = min(miny, y1, y2, y3); maxy = max(maxy, y1, y2, y3)
        minz = min(minz, z1, z2, z3); maxz = max(maxz, z1, z2, z3)
        volume += (x1 * (y2 * z3 - y3 * z2) - y1 * (x2 * z3 - x3 * z2)
                   + z1 * (x2 * y3 - x3 * y2)) / 6.0
    volume = abs(volume) / 1000.0  # cm3 (mm3 -> cm3)

    dx, dy, dz = maxx - minx, maxy - miny, maxz - minz
    bbox = {"x_mm": round(dx, 2), "y_mm": round(dy, 2), "z_mm": round(dz, 2)}
    fill_ratio = volume * 1000.0 / max(dx * dy * dz, 1e-9)

    # per-orientation stats (sample triangles for speed on huge meshes)
    step = 1
    if tri_count > 120000:
        step = max(1, tri_count // 120000)
    sampled = list(range(0, tri_count, step))

    def _stats_for_axis(down):
        """down: unit vector pointing to the bed after rotation."""
        overhang_area = 0.0
        total_area = 0.0
        bed_area = 0.0
        for i in sampled:
            base3 = i * 3
            base9 = i * 9
            nx, ny, nz = normals[base3], normals[base3 + 1], normals[base3 + 2]
            # rotate normal: dot products with new axes
            nd = nx * down[0] + ny * down[1] + nz * down[2]
            area = _triangle_area(
                (triangles[base9], triangles[base9 + 1], triangles[base9 + 2]),
                (triangles[base9 + 3], triangles[base9 + 4], triangles[base9 + 5]),
                (triangles[base9 + 6], triangles[base9 + 7], triangles[base9 + 8]))
            if area <= 0:
                continue
            total_area += area
            if nd < -0.7071:           # steeper than 45° facing down
                overhang_area += area
            if nd < -0.985:            # almost flat against the bed
                bed_area += area
        return {
            "overhang_fraction": round(overhang_area / total_area, 4) if total_area else 0.0,
            "bed_contact_area_cm2": round(bed_area / 100.0, 2),
        }

    orientations = {
        "as_is (Z up)": _stats_for_axis((0.0, 0.0, -1.0)),
        "rot_x_+90 (Y down->Z up)": _stats_for_axis((0.0, 1.0, 0.0)),
        "rot_x_-90": _stats_for_axis((0.0, -1.0, 0.0)),
        "rot_y_+90 (X down->Z up)": _stats_for_axis((1.0, 0.0, 0.0)),
        "rot_y_-90": _stats_for_axis((-1.0, 0.0, 0.0)),
    }
    best = min(orientations.items(),
               key=lambda kv: (kv[1]["overhang_fraction"], -kv[1]["bed_contact_area_cm2"]))

    # axis stats: prismaticity (normals ⊥ axis), skew faces (blades/cams),
    # cross-section roundness and central bore detection
    def _axis_stats(axis, axis_center_index):
        """Returns dict of shape metrics relative to the given axis."""
        axis_center = [0.0, 0.0, 0.0]
        for idx in range(3):
            if idx != axis_center_index:
                axis_center[idx] = centers[idx]
        radial_area = 0.0
        skew_area = 0.0
        total_area = 0.0
        side_radii = []      # (radius, area) of faces with normal ⊥ axis
        side_radii_angles = []  # (angle_bin, radius) for the outer profile
        cap_radii = []       # (radius, area) of faces with normal ∥ axis
        for i in sampled:
            base3 = i * 3
            base9 = i * 9
            nx, ny, nz = normals[base3], normals[base3 + 1], normals[base3 + 2]
            dot = abs(nx * axis[0] + ny * axis[1] + nz * axis[2])
            v0 = (triangles[base9], triangles[base9 + 1], triangles[base9 + 2])
            v1 = (triangles[base9 + 3], triangles[base9 + 4], triangles[base9 + 5])
            v2 = (triangles[base9 + 6], triangles[base9 + 7], triangles[base9 + 8])
            area = _triangle_area(v0, v1, v2)
            if area <= 0:
                continue
            centroid = ((v0[0] + v1[0] + v2[0]) / 3.0,
                        (v0[1] + v1[1] + v2[1]) / 3.0,
                        (v0[2] + v1[2] + v2[2]) / 3.0)
            # radial distance in the plane perpendicular to the axis
            dx, dy, dz = (centroid[0] - axis_center[0],
                          centroid[1] - axis_center[1],
                          centroid[2] - axis_center[2])
            offsets = (dx, dy, dz)
            in_plane = [offsets[i] for i in range(3) if i != axis_center_index]
            radius = math.sqrt(in_plane[0] ** 2 + in_plane[1] ** 2)
            if dot < 0.12 and radius > 1e-9:
                angle_bin = int(math.atan2(in_plane[1], in_plane[0])
                                / (2.0 * math.pi) * PROFILE_BINS) % PROFILE_BINS
                side_radii_angles.append((angle_bin, radius))
            total_area += area
            if dot < 0.12:
                radial_area += area
                side_radii.append((radius, area))
            elif 0.15 < dot < 0.85:
                skew_area += area
            elif dot > 0.98:
                cap_radii.append((radius, area))
        radial_share = radial_area / total_area if total_area else 0.0
        skew_share = skew_area / total_area if total_area else 0.0

        # angular max-radius profile: a smooth outer cylinder (roller, tube)
        # has constant max radius in every angular sector; gears/blades make
        # the profile oscillate (teeth) or spiky (discrete blades).
        profile_cv = None
        coverage = 0.0
        if side_radii_angles:
            bins = {}
            for angle_bin, radius in side_radii_angles:
                if angle_bin not in bins or radius > bins[angle_bin]:
                    bins[angle_bin] = radius
            filled = len(bins)
            coverage = float(filled) / PROFILE_BINS
            if filled >= 8:
                values = list(bins.values())
                mean_r = sum(values) / filled
                if mean_r > 1e-6:
                    variance = sum((v - mean_r) ** 2 for v in values) / filled
                    profile_cv = math.sqrt(max(variance, 0.0)) / mean_r

        # roundness of the cross-section: variation of side-face radii
        round_cv = None
        mean_side_r = None
        if side_radii:
            weighted_sum = sum(r * a for r, a in side_radii)
            weighted_area = sum(a for _, a in side_radii)
            mean_side_r = weighted_sum / weighted_area if weighted_area else 0.0
            if mean_side_r > 1e-6:
                variance = sum(a * (r - mean_side_r) ** 2 for r, a in side_radii) / weighted_area
                round_cv = math.sqrt(max(variance, 0.0)) / mean_side_r

        # central bore: share of cap area located near the axis
        bore_share = None
        if cap_radii and mean_side_r:
            inner = sum(a for r, a in cap_radii if r < 0.45 * mean_side_r)
            total_cap = sum(a for _, a in cap_radii)
            bore_share = inner / total_cap if total_cap else 0.0

        return {"radial_share": radial_share, "skew_share": skew_share,
                "round_cv": round_cv, "mean_radius": mean_side_r,
                "bore_share": bore_share, "profile_cv": profile_cv,
                "coverage": coverage}

    # axes: index of the coordinate the axis is aligned with
    axes = [("about_Z", (0.0, 0.0, 1.0), 2),
            ("about_X", (1.0, 0.0, 0.0), 0),
            ("about_Y", (0.0, 1.0, 0.0), 1)]
    centers = [(minx + maxx) / 2.0, (miny + maxy) / 2.0, (minz + maxz) / 2.0]

    sym = {}
    skew = {}
    cross = {}
    for axis_name, axis, idx in axes:
        stats = _axis_stats(axis, idx)
        sym[axis_name] = round(stats["radial_share"], 3)
        skew[axis_name] = round(stats["skew_share"], 3)
        cross[axis_name] = {
            "round_cv": round(stats["round_cv"], 3) if stats["round_cv"] is not None else None,
            "mean_radius_mm": round(stats["mean_radius"], 2) if stats["mean_radius"] else None,
            "central_bore_share": round(stats["bore_share"], 3) if stats["bore_share"] is not None else None,
            "profile_cv": round(stats["profile_cv"], 4) if stats["profile_cv"] is not None else None,
            "profile_coverage": round(stats["coverage"], 3),
        }
    best_axis = max(sym.items(), key=lambda kv: kv[1])

    # recommendation logic (geometry only — the LLM makes the final call
    # using the task requirements)
    recommendation = _recommend_mode(sym, skew, cross, best_axis, bbox, fill_ratio)

    return {
        "file": os.path.basename(path),
        "triangles": tri_count,
        "bbox_mm": bbox,
        "volume_cm3": round(volume, 2),
        "fill_ratio_of_bbox": round(fill_ratio, 3),
        "orientations": orientations,
        "best_orientation": {
            "name": best[0],
            "overhang_fraction": best[1]["overhang_fraction"],
            "bed_contact_area_cm2": best[1]["bed_contact_area_cm2"],
        },
        "axial_symmetry": sym,
        "skew_face_share": skew,
        "cross_section": cross,
        "recommendation": recommendation,
    }


def _recommend_mode(sym, skew, cross, best_axis, bbox, fill_ratio):
    """Provisional geometry-based mode recommendation. The LLM agent makes the
    final decision using the task requirements (loads, medium, finish)."""
    dims = {"X": bbox["x_mm"], "Y": bbox["y_mm"], "Z": bbox["z_mm"]}

    def axis_is_prismatic(axis_name):
        return sym.get(axis_name, 0.0) >= 0.45

    def axis_section(axis_name):
        return cross.get(axis_name, {})

    # primary axis: the most prismatic one
    axis_name, axis_value = best_axis
    axis_letter = axis_name[-1]

    # ...but prefer an axis that is prismatic AND has a smooth outer profile
    # (the part may be modeled with its rotation axis along X or Y)
    for cand_name in sorted(sym.keys(), key=lambda k: -sym[k]):
        if not axis_is_prismatic(cand_name):
            continue
        cand = axis_section(cand_name)
        cand_profile = cand.get("profile_cv")
        cand_round = cand.get("round_cv")
        if (cand_profile is not None and cand_profile < 0.03
                and cand_round is not None and cand_round < 0.2):
            axis_name = cand_name
            axis_letter = axis_name[-1]
            axis_value = sym[cand_name]
            break

    skew_share = skew.get(axis_name, 0.0)
    section = axis_section(axis_name)
    bore_share = section.get("central_bore_share")
    profile_cv = section.get("profile_cv")
    round_cv = section.get("round_cv")
    coverage = section.get("profile_coverage", 0.0)
    axis_len = dims[axis_letter]
    other = sorted(v for k, v in dims.items() if k != axis_letter)
    diameter = max(other[-1], 1e-6)
    aspect = axis_len / diameter

    prismatic = axis_value >= 0.45
    smooth_outer = (profile_cv is not None and profile_cv < 0.03
                    and coverage >= 0.3)
    has_bore = bore_share is not None and bore_share >= 0.25
    clean_cylinder = round_cv is not None and round_cv < 0.15
    tube_like = has_bore or fill_ratio < 0.45 or aspect >= 1.8 or clean_cylinder
    best_skew = max(skew.values()) if skew else 0.0
    best_bore = max([v.get("central_bore_share") or 0.0 for v in cross.values()]) if cross else 0.0

    reasons = []
    if prismatic:
        reasons.append("деталь призматична вдоль оси %s (%.0f%% боковых граней)"
                       % (axis_letter, axis_value * 100))
    if smooth_outer:
        reasons.append("гладкий наружный контур по всему углу (profile_cv=%.4f) — "
                       "поверхность вращения без зубьев/лопаток" % profile_cv)
    elif prismatic:
        reasons.append("наружный контур негладкий (profile_cv=%s) — зубья, лопатки "
                       "или кулачковый профиль" %
                       ("%.4f" % profile_cv if profile_cv is not None else "н/д"))
    if has_bore:
        reasons.append("есть центральное отверстие (%.0f%% площади торцов) — деталь "
                       "сажается на цилиндрическую оправку" % (bore_share * 100))

    if prismatic and smooth_outer and tube_like:
        return {
            "mode": MODE_5D_SPIRAL,
            "mode_label": "5D Spiral (cylindrical)",
            "confidence": "high",
            "alternative": MODE_3D,
            "align_hint": "ось детали %s должна быть вертикальной (вдоль Z)" % axis_letter,
            "reasons": reasons + [
                "спиральная печать на цилиндрической оправке; проверьте "
                "cylindrical_mode_base_diameter под посадочное отверстие",
            ],
        }
    if prismatic and smooth_outer and not tube_like:
        return {
            "mode": MODE_3D,
            "mode_label": "3D (classic)",
            "confidence": "medium",
            "alternative": MODE_5D_SPIRAL,
            "reasons": reasons + [
                "круглое, но сплошное/короткое — обычно хватает 3D; при повышенных "
                "требованиях к прочности на кручение рассмотрите 5D Spiral",
            ],
        }
    if prismatic:  # non-round cross-section: teeth / blades / cam profile
        blade_like = skew_share >= 0.25 or (bore_share or 0.0) >= 0.2
        return {
            "mode": MODE_3D,
            "mode_label": "3D (classic)",
            "confidence": "low",
            "alternative": MODE_5D if blade_like else None,
            "reasons": reasons + [
                "если деталь работает под разнонаправленной нагрузкой, имеет лопатки, "
                "зубья под передачу момента или кулачковый профиль — рассмотрите "
                "5D (cylindrical_full): волокна вдоль силовых линий, нависания без поддержек",
            ],
        }
    # no dominant prismatic axis
    reasons.append("выраженной осевой симметрии нет (%s: %.0f%%)"
                   % (axis_name, axis_value * 100))
    suggest_full = best_skew >= 0.4 and best_bore >= 0.2
    return {
        "mode": MODE_3D,
        "mode_label": "3D (classic)",
        "confidence": "medium",
        "alternative": MODE_5D if suggest_full else None,
        "reasons": reasons + ([
            "много криволинейных граней (%.0f%%) при наличии центральной ступицы — "
            "при силовых требованиях рассмотрите 5D (cylindrical_full)" % (best_skew * 100),
        ] if suggest_full else []) + [
            "ориентируйте по best_orientation; при больших нависаниях рассмотрите "
            "поддержки или 5D",
        ],
    }


def load_and_analyze_model(file_path):
    info = load_model(file_path)
    try:
        analysis = analyze_stl_geometry(info.get("resolved_path") or file_path)
    except ValueError as e:
        # not an STL (obj/3mf) — return scene info only
        analysis = {"note": "геометрический анализ поддержан только для STL: %s" % e}
    return {"loaded": info, "analysis": analysis}
