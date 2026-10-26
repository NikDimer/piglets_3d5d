# -*- coding: utf-8 -*-
#
# mcp_server.py — lightweight MCP server (JSON-RPC 2.0) for STE Slicer.
#
# The official MCP SDK needs Python 3.10+ which cannot run inside the slicer,
# so the required subset is implemented on the standard library:
#   methods: initialize, tools/list, tools/call  (+ ping)
#   transport: HTTP endpoint POST /mcp + direct in-process call_tool()
#
# Python 3.5 compatible.

import json

from . import slicer_tools as T

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ste-slicer-mcp", "version": "1.0.0"}


class MCPError(Exception):
    def __init__(self, message, code=-32602):
        super(MCPError, self).__init__(message)
        self.rpc_code = code


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": required}


# Tool handlers return JSON-serializable dicts. Handlers that touch the Qt
# world call execute_on_main_thread internally.

def _t_printer_summary(args):
    data = T.get_machine_summary()
    data["settings"] = T.get_settings_summary()
    data["printing_mode"] = T._read_mode()
    return data


def _t_search_settings(args):
    return T.search_settings_definitions(args.get("query", ""))


def _t_set_settings(args):
    settings = args.get("settings") or {}
    if not settings:
        raise MCPError("пустой словарь settings")
    result = T.write_settings_batch(settings)
    if not result["applied"]:
        raise MCPError("не применён ни один ключ: %s" % result["rejected"])
    return result


def _t_set_printing_mode(args):
    return T.set_printing_mode(args.get("mode"))


def _t_list_profiles(args):
    return T.list_profiles_and_materials()


def _t_set_material_profile(args):
    return T.set_material_and_profile(
        material_id=args.get("material_id"),
        quality_type=args.get("quality_type"),
        save_profile_name=args.get("save_profile_name"),
    )


def _t_load_analyze(args):
    path = args.get("file_path")
    if not path:
        raise MCPError("file_path обязателен")
    return T.load_and_analyze_model(path)


def _t_scene_objects(args):
    objects = T.get_scene_objects()
    return {"count": len(objects), "objects": objects}


def _t_transform(args):
    return {"updated": T.transform_model(
        target=args.get("target", "all"),
        translate=args.get("translate"),
        rotate_xyz=args.get("rotate_xyz"),
        scale=args.get("scale"),
        uniform_scale=args.get("uniform_scale"),
        drop_to_bed=bool(args.get("drop_to_bed", True)),
    )}


def _t_arrange_clear(args):
    return T.arrange_and_clear_scene(args.get("action", "arrange"))


def _t_run_slicing(args):
    return T.run_slicing(wait=bool(args.get("wait", True)),
                         timeout=float(args.get("timeout", 600)),
                         stall_timeout=float(args.get("stall_timeout", 180)))


def _t_read_slicer_log(args):
    def _num(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return T.read_slicer_log(
        limit=int(_num(args.get("limit"), 40)),
        errors_only=bool(args.get("errors_only", False)),
        minutes=_num(args.get("minutes"), None) if args.get("minutes") is not None else None,
    )


def _t_slicing_status(args):
    result = T.get_slicing_result()
    return result


def _t_export(args):
    return T.export_project_and_gcode(
        model_name=args.get("model_name") or "model",
        output_dir=args.get("output_dir"),
    )


TOOLS = [
    {
        "name": "get_printer_and_settings_summary",
        "description": "Получить активный принтер, текущий режим печати (classic/3D, "
                       "cylindrical/5D Spiral, cylindrical_full/5D), активный материал, "
                       "профиль качества и значения ~25 ключевых настроек печати.",
        "inputSchema": _schema({}, []),
        "handler": _t_printer_summary,
    },
    {
        "name": "search_settings_definitions",
        "description": "Поиск настроек слайсера по ключевому слову (ключ, название, "
                       "описание, тип, допустимые options, текущее значение).",
        "inputSchema": _schema({"query": {"type": "string",
                                          "description": "подстрока для поиска, напр. 'infill' или 'support'"}},
                               ["query"]),
        "handler": _t_search_settings,
    },
    {
        "name": "set_slicer_settings",
        "description": "Пакетная установка настроек печати (глобальных и экструдера), "
                       "напр. {\"layer_height\":0.12,\"top_layers\":6,"
                       "\"infill_sparse_density\":80,\"material_print_temperature\":290,"
                       "\"material_bed_temperature\":110,\"speed_print\":40,"
                       "\"cool_fan_enabled\":false}. Валидные ключи применяются всегда, "
                       "битые возвращаются в rejected. Скорости — speed_* (speed_print, "
                       "speed_infill, speed_wall), ретракты — retraction_* (retraction_amount), "
                       "обдув — cool_fan_*, плотность поддержек — support_infill_rate. "
                       "Режимные двойники (_classic/_cylindrical) пишутся автоматически.",
        "inputSchema": _schema({
            "settings": {"type": "object",
                         "description": "словарь ключ->значение настроек STE Slicer"},
        }, ["settings"]),
        "handler": _t_set_settings,
    },
    {
        "name": "set_printing_mode",
        "description": "Переключить режим печати: '3D'/'classic', '5D Spiral'/'cylindrical', "
                       "'5D'/'cylindrical_full'.",
        "inputSchema": _schema({
            "mode": {"type": "string", "enum": ["classic", "cylindrical", "cylindrical_full",
                                                "3D", "5D Spiral", "5D"]},
        }, ["mode"]),
        "handler": _t_set_printing_mode,
    },
    {
        "name": "list_profiles_and_materials",
        "description": "Список доступных материалов и профилей качества для текущего "
                       "принтера + активные значения.",
        "inputSchema": _schema({}, []),
        "handler": _t_list_profiles,
    },
    {
        "name": "set_material_and_profile",
        "description": "Установить материал (id из list_profiles_and_materials) и/или "
                       "профиль качества (quality_type). Может сохранить текущие "
                       "пользовательские настройки как новый профиль.",
        "inputSchema": _schema({
            "material_id": {"type": "string"},
            "quality_type": {"type": "string"},
            "save_profile_name": {"type": "string",
                                  "description": "имя для сохранения текущих настроек как профиль"},
        }, []),
        "handler": _t_set_material_profile,
    },
    {
        "name": "load_and_analyze_model",
        "description": "Загрузить 3D-модель (STL/OBJ/3MF) на стол слайсера и сразу "
                       "провести геометрический анализ STL: габариты, объём, нависания "
                       "для 5 ориентаций, осевую симметрию и рекомендацию режима 3D/5D. "
                       "file_path можно передать полным путём ИЛИ просто именем файла — "
                       "плагин сам ищет модель в рабочих папках (AIAssistant_files, "
                       "последняя папка открытия слайсера, Рабочий стол/Документы/"
                       "Загрузки). Если не найдено — в ошибке список доступных моделей.",
        "inputSchema": _schema({
            "file_path": {"type": "string", "description": "полный путь или имя файла модели"},
        }, ["file_path"]),
        "handler": _t_load_analyze,
    },
    {
        "name": "get_scene_objects",
        "description": "Список всех моделей на столе: имя, позиция, габариты (bbox), "
                       "поворот, масштаб.",
        "inputSchema": _schema({}, []),
        "handler": _t_scene_objects,
    },
    {
        "name": "transform_model",
        "description": "Переместить (translate, мм), повернуть (rotate_xyz, градусы) "
                       "и/или масштабировать модель. target: 'all', имя модели или индекс. "
                       "Модель автоматически опускается на стол.",
        "inputSchema": _schema({
            "target": {"type": "string", "description": "'all', имя или индекс модели"},
            "translate": {"type": "array", "items": {"type": "number"},
                          "description": "[x, y, z] в мм (установка абсолютной позиции)"},
            "rotate_xyz": {"type": "array", "items": {"type": "number"},
                           "description": "[rx, ry, rz] в градусах"},
            "scale": {"type": "array", "items": {"type": "number"},
                      "description": "[sx, sy, sz] масштаб по осям"},
            "uniform_scale": {"type": "number", "description": "единый масштаб"},
        }, []),
        "handler": _t_transform,
    },
    {
        "name": "arrange_and_clear_scene",
        "description": "action='arrange' — авто-расстановка всех моделей на столе; "
                       "action='clear' — очистить стол.",
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["arrange", "clear"]},
        }, ["action"]),
        "handler": _t_arrange_clear,
    },
    {
        "name": "run_slicing",
        "description": "Запустить нарезку (слайсинг). При wait=true дожидается "
                       "завершения (по умолчанию) и возвращает финальный статус. "
                       "Если нарезка не удалась, ответ содержит фактическую "
                       "причину сбоя: backend_error (ui_message — текст окна "
                       "ошибки слайсера, job_result — код результата, job_error "
                       "— исключение) и log_tail — последние ошибки из журнала "
                       "слайсера. Это прямая инструкция агенту: разберите "
                       "причину и устраните её (настройки, материал, "
                       "геометрия, лицензия 5D); если строки мало — вызовите "
                       "read_slicer_log.",
        "inputSchema": _schema({
            "wait": {"type": "boolean", "default": True},
            "timeout": {"type": "number", "default": 600, "description": "секунд ожидания"},
            "stall_timeout": {"type": "number", "default": 180,
                              "description": "секунд без прогресса до признания зависания"},
        }, []),
        "handler": _t_run_slicing,
    },
    {
        "name": "read_slicer_log",
        "description": "Прочитать журнал слайсера (steslicer.log): последние "
                       "ошибки и предупреждения. ПРЯМАЯ ИНСТРУКЦИЯ АГЕНТУ: при "
                       "любой ошибке, зависании или прерывании нарезки вызови "
                       "этот инструмент и разбирай причину по строкам журнала. "
                       "Типичные причины: неактивная лицензия 5D (упоминания "
                       "license/ATSS), ошибки настроек, негерметичная модель, "
                       "сбой внешних инструментов 5D-конвейера.",
        "inputSchema": _schema({
            "limit": {"type": "integer", "default": 40,
                      "description": "максимум строк (1-200)"},
            "errors_only": {"type": "boolean", "default": False,
                            "description": "только ошибки, без предупреждений"},
            "minutes": {"type": "number",
                        "description": "окно за последние N минут (по умолчанию весь журнал)"},
        }, []),
        "handler": _t_read_slicer_log,
    },
    {
        "name": "get_slicing_status_and_result",
        "description": "Статус нарезки, прогресс, расчётное время печати и расход "
                       "материала (метры и граммы).",
        "inputSchema": _schema({}, []),
        "handler": _t_slicing_status,
    },
    {
        "name": "export_project_and_gcode",
        "description": "Сохранить проект 3MF и G-code в папку результатов "
                       "results/<model_name>/ (project.3mf + result.gcode). Сначала "
                       "должен быть выполнен успешный слайсинг.",
        "inputSchema": _schema({
            "model_name": {"type": "string"},
            "output_dir": {"type": "string", "description": "базовая папка (по умолчанию AIAssistant_results)"},
        }, ["model_name"]),
        "handler": _t_export,
    },
]

_TOOLS_BY_NAME = dict((t["name"], t) for t in TOOLS)


class MCPServer(object):
    """In-process MCP server. Handles JSON-RPC requests from /mcp and direct
    tool calls from the LLM agent."""

    # ------------------------------------------------------------------
    def handle_request(self, request):
        """Handle a parsed JSON-RPC 2.0 request dict -> result (raises MCPError)."""
        method = request.get("method")
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools_list()}
        if method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                result = self.call_tool(name, arguments)
                return {"content": [{"type": "text",
                                     "text": json.dumps(result, ensure_ascii=False)}],
                        "isError": False}
            except MCPError as e:
                return {"content": [{"type": "text", "text": str(e)}],
                        "isError": True}
            except Exception as e:
                return {"content": [{"type": "text",
                                     "text": "%s: %s" % (type(e).__name__, e)}],
                        "isError": True}
        raise MCPError("method not found: %s" % method, code=-32601)

    # ------------------------------------------------------------------
    def tools_list(self):
        return [{"name": t["name"], "description": t["description"],
                 "inputSchema": t["inputSchema"]} for t in TOOLS]

    def get_openai_tools_schema(self):
        """Convert MCP tools to OpenAI Chat Completions `tools` format."""
        return [{"type": "function",
                 "function": {"name": t["name"],
                              "description": t["description"],
                              "parameters": t["inputSchema"]}}
                for t in TOOLS]

    # ------------------------------------------------------------------
    def call_tool(self, name, arguments):
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            raise MCPError("unknown tool: %s" % name, code=-32602)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPError("arguments must be an object")
        # validate required params
        for req in tool["inputSchema"].get("required", []):
            if req not in arguments or arguments.get(req) in (None, "", []):
                raise MCPError("missing required argument: %s" % req)
        return tool["handler"](arguments)
