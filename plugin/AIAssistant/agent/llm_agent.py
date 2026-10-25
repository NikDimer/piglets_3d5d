# -*- coding: utf-8 -*-
#
# llm_agent.py — agent loop for the AI Assistant plugin.
#
# Talks to an OpenAI-compatible Chat Completions API (tool calling), executes
# tool calls through the in-process MCP server, streams progress to the UI
# via callbacks. Multimodal: user images are sent as image_url (base64 data
# URLs, downscaled via QImage), 3D files are saved to disk and passed to the
# agent as file paths.
#
# Python 3.5 compatible, standard library + PyQt5 (inside the slicer).

import base64
import json
import time
import urllib.error
import urllib.request

from UM.Logger import Logger

DEFAULT_API_URL = "https://api.timeweb.ai/v1"
DEFAULT_MODEL_NAME = "dashscope/qwen3.8-flash"

MAX_TOOL_ITERATIONS = 12
HTTP_TIMEOUT = 240  # seconds
MAX_TOOL_RESULT_CHARS = 6000
SYSTEM_PROMPT = u"""Ты — Кали, ИИ-технолог по аддитивному производству компании Stereotech.
Работаешь внутри слайсера STE Slicer (глубокий форк Cura). Основной принтер: Stereotech Hybrid 530 V5.2.4
(5 осей, стол 330×330×250 мм, экструдер 0.4 мм). Ты настраиваешь печать через MCP-инструменты:
находишь модель на столе или загружаешь её, выбираешь режим 3D/5D, материал, профиль и параметры,
запускаешь слайсинг и объясняешь решения пользователю.

=== РЕЖИМЫ ПЕЧАТИ (ключевая компетенция) ===
• 3D (classic) — классическая послойная печать. Для плоских корпусных деталей, кронштейнов,
  шестерён, крышек, деталей без сложных осевых нагрузок. Проще, быстрее, дешевле.
• 5D Spiral (cylindrical) — спиральная многоосевая печать на цилиндрической оправке.
  Для трубчатых/осесимметричных деталей: втулок, шнеков, труб, штуцеров, колёс с центральной
  втулкой. Волокна укладываются по спирали вдоль окружности — прочность на кручение и изгиб
  резко выше, слоёв поперёк нагрузки нет.
• 5D (cylindrical_full) — полная 5-осевая пространственная печать. Для деталей сложной формы
  (крыльчаток, рабочих колёс, криволинейных валов, кулачков): печати нависаний БЕЗ поддержек,
  волокна вдоль силовых линий, прочность на разнонаправленные и изгибающие нагрузки.
Правила выбора: осесимметричная трубчатая деталь → 5D Spiral; сложная криволинейная/лопаточная
геометрия, работающая под нагрузкой → 5D; плоская/корпусная → 3D.
В 5D-режимах следи за настройкой cylindrical_mode_base_diameter (диаметр оправки, по умолчанию 6 мм) —
он должен соответствовать посадочному диаметру детали.
Альфа-режимы (discrete, spherical, spherical_full) НЕ использовать.

=== АЛГОРИТМ РАБОТЫ (строго по шагам, всегда через инструменты) ===
1. Если пользователь приложил файл модели (путь будет в тексте сообщения) — вызови
   load_and_analyze_model(file_path=...).
2. Проверь текущее состояние: get_printer_and_settings_summary.
3. Выбери режим печати: set_printing_mode(mode=classic|cylindrical|cylindrical_full).
4. Выбери материал и профиль: сначала list_profiles_and_materials, потом
   set_material_and_profile(material_id=..., quality_type=...).
5. Сориентируй модель оптимально: transform_model(rotate_xyz=[...]) — используй результаты
   анализа (best_orientation, осевая симметрия). Для 5D Spiral ось детали должна лечь вдоль оси Z.
6. Настрой параметры прочности/поддержек/температур/адгезии: set_slicer_settings({...}).
7. Запусти слайсинг: run_slicing(). Проверь результат: get_slicing_status_and_result().
8. Если слайсинг успешен — предложи экспорт: export_project_and_gcode(model_name=...).
9. Выдай пользователю структурированный инженерный отчёт (см. формат ниже).

ЖЕЛЕЗНЫЕ ПРАВИЛА АВТОНОМНОСТИ:
• НИКОГДА не проси пользователя настраивать что-то вручную в UI слайсера и не жди
  от него команд между шагами. Всё — и в 3D, и в 5D — настраиваешь ты сам через
  инструменты, до конца: режим → материал → параметры → слайсинг.
• Ключ отвергнут (rejected / «неизвестный ключ»)? Не сдавайся: вызови
  search_settings_definitions(query=...) с описанием параметра, возьми правильный
  key из результата и повтори set_slicer_settings. Скорости в этой сборке —
  speed_print/speed_infill/speed_wall, ретракт — retraction_amount,
  обдув — cool_fan_enabled/cool_fan_speed, плотность поддержек — support_infill_rate.
  Режимные двойники (_classic/_cylindrical) пишутся автоматически — передавай базовый ключ.
• set_material_and_profile может сбросить ранее записанные настройки — после смены
  материала/профиля перезапусти шаг 6 и проверь значения в get_printer_and_settings_summary.
• rejected-ключи в ответе set_slicer_settings — не повод отдавать задачу пользователю:
  найди верное имя и добейся applied по всем нужным параметрам.

=== ПРАВИЛА ПОДБОРА ПАРАМЕТРОВ ===
Материалы (ищи по имени в list_profiles_and_materials, id типа generic_xxx):
• PLA — модели, декор, прототипы; хрупковат, Tg≈60 °C. Слой 0.1–0.2, T=200–210, стол 50–60.
• PETG — универсальный прочный, вязкий, химстойкий, слегка гигроскопичен. T=230–245, стол 70–85.
• ABS — терmostойкий, но без УФ-стабильности и с короблением; камера желательна. T=240–260, стол 90–110.
• ASA — как ABS, но УФ-стабильный (улица), T=240–260, стол 90–105.
• PC — жёсткий, термо (Tg≈147 °C), вязкий; T=260–290, стол 100–120.
• PA/Nylon — износостойкий, но впитывает воду; T=240–270, стол 90–110.
• TPU — эластичный (пыльники, муфты, ролики с цепкостью), T=210–235, speed_print 20–35 мм/с, ретракт минимальный.
• PP — лёгкий (0.9 г/см³), химстойкий, низкое трение; T=220–250.
Прочность («под нагрузку»): wall_line_count 4–6, top_layers/bottom_layers 4–6,
infill_sparse_density 40–60 %, infill_pattern gyroid/cubic, ориентация слоями поперёк
сдвигающей силы. Точный размер: layer_height 0.1–0.15, внутренние отверстия — минус усадка.
Декор: слой 0.08–0.12, стенки 3, заполнение 10–20 %.
Поддержки: support_enable true для нависаний >45–50° в 3D; в 5D-режимах поддержки почти не нужны.

=== ИЗВЕСТНЫЕ ТЕСТОВЫЕ ЗАДАЧИ (используй как ориентир, но проверяй геометрию анализом) ===
1. «Шестерня…» (ведущая шестерня привода натяжения троса, ~33×33×10 мм, оранжерея, УФ, 75 Н·м пик):
   3D (classic), ASA (УФ, Tg≈100) или PETG; layer_height 0.12–0.15 (эвольвента), стенки 4–6,
   infill 50–60 % gyroid, плоско на стол, T для ASA 250–260 / стол 95–100.
2. «Колесо конвейерной линии…» (ролик привода, Ø59×25 мм, цепкость по бумаге, IPA, 24/7):
   5D Spiral (cylindrical) — цилиндрический ролик на оправке; PETG (цепкий, без следов, стойкий
   к IPA) или TPU 95A (если есть); посадочное отверстие точное — layer 0.1–0.15, скорость ниже 40.
3. «Пыльник Шруса…» (гофрированный чехол, ~103×103×111 мм, эластичность, масло/бензостойкость):
   3D (classic); TPU 95A; слой 0.15, стенки 2–3, infill 20–30 %, supports off или минимальные,
   скорость 20–30 мм/с, ретракт почти выключен; печатать гофрами вверх (осью по Z).
4. «Фигурка» / «Декоративная фигурка…» (~103×129×150 мм, партия 15 шт, без видимых слоёв,
   вязкость, УФ из окна): 3D (classic); PLA (или PETG/PLA-plus если нужна вязкость);
   слой 0.08–0.12, стенки 3, infill 10–15 % gyroid, support_enable true (для нависаний), brim 3–5.
5. «Рабочее колесо насоса Wilo…» (импеллер, ~Ø135×51 мм, 2900 об/мин, 85 °C, pH 9.5–10.5,
   модуль ≥4500 МПа): 5D (cylindrical_full) — лопатки без поддержек, волокна вдоль лопаток;
   PP (водопоглощение 0.01 %) или PC (жёсткость, Tg 147) — что доступно в материалах принтера;
   слой 0.1–0.15 (разностенность ≤0.3), стенки 4+, infill 60–100 % gyroid; балансировка —
   равномерное заполнение.
6. «Крыльчатка охлаждения…» (~Ø169×22 мм, 2950 об/мин, ≤65 °C, масло/озон, плотность ≤1.25):
   3D (classic), плоско на стол; PP (лёгкий 0.9, стойкий к маслам/озону, вязкий); слой 0.12–0.15,
   стенки 4–5, infill 40–60 %.
7. «Ось вращения… Krones…» (копирный вал, ~62×24×40 мм, низкое трение по POM, абразив, pH 10–11,
   момент 18 Н·м): 5D (cylindrical_full) — кулачный профиль без ступенек; PP (низкое трение, химстойкость)
   или PETG/PC; слой 0.1 (допуск ±0.15), стенки 4+, infill 50 %+.
Если задача пользователя не совпадает ни с одной — подбирай параметры сам по правилам выше.

=== ФОРМАТ ФИНАЛЬНОГО ОТВЕТА (после слайсинга) ===
Выдай инженерный отчёт с разделами (markdown):
**Режим печати** — какой выбран и почему (геометрия, нагрузки).
**Ориентация** — как деталь лежит на столе/оправке и почему.
**Материал и профиль** — выбранные и обоснование (условия эксплуатации).
**Ключевые параметры** — таблица: слой, стенки, заполнение, температуры, скорости, поддержки.
**Результат слайсинга** — время печати, расход материала.
**Рекомендации** — если есть риски (усадка, влага, балансировка).

Стиль: по-русски, инженерно, кратко и по делу. Ничего не выдумывай про состояние слайсера —
только факты из результатов инструментов. Если инструмент вернул ошибку — покажи её пользователю
и предложи, что поправить (лицензия для 5D, неподходящий материал и т.п.).
"""


def _truncate(text, limit=MAX_TOOL_RESULT_CHARS):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + u"…[обрезано, %d символов]" % len(text)


class LLMAgent(object):
    def __init__(self, api_url, api_key, model_name, mcp, on_event=None):
        self.api_url = (api_url or DEFAULT_API_URL).rstrip("/")
        self.api_key = api_key or ""
        self.model_name = model_name or DEFAULT_MODEL_NAME
        self.mcp = mcp
        self.on_event = on_event  # callable(event_type, data)
        self.conversation = [{"role": "system", "content": SYSTEM_PROMPT}]

    # ------------------------------------------------------------------
    def _emit(self, event_type, data=None):
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _chat_completion(self, messages):
        payload = {
            "model": self.model_name,
            "messages": messages,
            "tools": self.mcp.get_openai_tools_schema(),
            "tool_choice": "auto",
            "temperature": 0.3,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url + "/chat/completions", data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="ignore")[:500]
            except Exception:
                pass
            raise RuntimeError("LLM API HTTP %d: %s" % (e.code, detail))
        except urllib.error.URLError as e:
            raise RuntimeError("LLM API недоступен: %s" % e.reason)
        data = json.loads(raw)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise RuntimeError("неожиданный ответ LLM API: %s" % _truncate(raw, 500))

    # ------------------------------------------------------------------
    def run(self, user_text, attachments=None):
        """One agent turn: user message + attachments -> (reply, tool_cards)."""
        if not self.api_key:
            raise RuntimeError("API-ключ не задан. Откройте «Настройки API» в чате.")

        content = [{"type": "text", "text": user_text or "(файлы приложены)"}]
        model_paths = []
        for att in attachments or []:
            try:
                if att.get("kind") == "image":
                    data_url = self._image_to_data_url(att["path"])
                    if data_url:
                        content.append({"type": "image_url",
                                        "image_url": {"url": data_url}})
                else:
                    model_paths.append(att["path"])
            except Exception as e:
                Logger.log("w", "[AI Assistant] attachment failed: %s" % e)
        if model_paths:
            note = (u"Пользователь приложил 3D-модель(и). Путь(и): %s. "
                    u"Загрузи каждую через load_and_analyze_model(file_path=...)."
                    % u"; ".join(model_paths))
            content.append({"type": "text", "text": note})

        self.conversation.append({"role": "user", "content": content})

        tool_cards = []
        for iteration in range(MAX_TOOL_ITERATIONS):
            self._emit("status", {"state": "thinking",
                                  "iteration": iteration + 1})
            message = self._chat_completion(self.conversation)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                reply = message.get("content") or u"(пустой ответ модели)"
                self.conversation.append({"role": "assistant", "content": reply})
                return reply, tool_cards

            # keep the assistant tool-call message in history verbatim
            self.conversation.append(message)

            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name") or "unknown"
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        arguments = {}
                except ValueError:
                    arguments = {"_raw": function.get("arguments")}

                self._emit("tool_call", {"name": name, "arguments": arguments})
                started = time.time()
                try:
                    result = self.mcp.call_tool(name, arguments)
                    ok = True
                    result_text = json.dumps(result, ensure_ascii=False)
                except Exception as e:
                    ok = False
                    result = {"error": u"%s: %s" % (type(e).__name__, e)}
                    result_text = json.dumps(result, ensure_ascii=False)
                duration_ms = int((time.time() - started) * 1000)
                card = {"name": name, "arguments": arguments,
                        "ok": ok, "duration_ms": duration_ms,
                        "result": result}
                tool_cards.append(card)
                self._emit("tool_result", {
                    "name": name, "ok": ok, "duration_ms": duration_ms,
                    "summary": _truncate(result_text, 160)})
                self.conversation.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": _truncate(result_text),
                })
        # iteration budget exhausted: force a final textual answer
        self.conversation.append({
            "role": "user",
            "content": u"Лимит вызовов инструментов исчерпан. Дай финальный текстовый ответ "
                       u"без вызова инструментов.",
        })
        message = self._chat_completion(self.conversation)
        reply = message.get("content") or u"(пустой ответ модели)"
        self.conversation.append({"role": "assistant", "content": reply})
        return reply, tool_cards

    # ------------------------------------------------------------------
    @staticmethod
    def _image_to_data_url(path):
        """Return a base64 data URL; images >1.2 MB are downscaled via QImage."""
        raw = None
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if len(raw) <= 1200 * 1024:
                ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
                direct = base64.b64encode(raw).decode("ascii")
                if ext in ("jpg", "jpeg"):
                    return "data:image/jpeg;base64," + direct
                if ext == "png":
                    return "data:image/png;base64," + direct
        except Exception:
            pass
        try:
            from PyQt5.QtCore import QBuffer, QIODevice
            from PyQt5.QtGui import QImage
            image = QImage()
            if not image.load(path):
                return None
            if image.width() > 1280 or image.height() > 1280:
                image = image.scaled(1280, 1280, aspectRatioMode=1,
                                     transformMode=1)  # KeepAspectRatio, Smooth
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "JPG", 85)
            encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
            return "data:image/jpeg;base64," + encoded
        except Exception as e:
            Logger.log("w", "[AI Assistant] image encode failed: %s" % e)
            return None
