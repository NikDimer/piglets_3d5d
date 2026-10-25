# -*- coding: utf-8 -*-
#
# AIAssistantWidgets.py — native chat UI for the AI Assistant plugin built
# with QWidgets (QPainter text path) instead of Qt Quick.
#
# Why: the Qt build shipped with STE Slicer has a deep-recursion bug in the
# Qt Quick scene-graph text rendering (see DESKTOP_VERSION_POSTMORTEM.md):
# any QML page with heavy wrapped text can wedge the whole main thread.
# Widgets draw text through QWidget.paintEvent/QPainter — a different code
# path that bypasses that bug entirely. This module renders the same agent
# machinery (EventBus/ChatState via the extension object) as the web SPA.
#
# Layout: a draggable frameless "Kali" mascot card pinned to the bottom-right
# corner of the slicer main window + a frameless chat panel that opens above
# it. Both are Qt.Tool windows: they never inject anything into the slicer's
# QML scene graph.
#
# Python 3.5 compatible; depends only on PyQt5 (already shipped with the
# slicer) and the extension object passed in.

import os
from PyQt5.QtCore import Qt, QTimer, QObject, QPoint, QEvent, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFontMetrics, QLinearGradient,
                         QMovie, QPainter, QPainterPath, QPen, QPixmap)
from PyQt5.QtWidgets import (
    QApplication, QFrame, QLabel, QPushButton, QTextEdit, QTextBrowser,
    QVBoxLayout, QHBoxLayout, QFileDialog, QProgressBar, QDialog, QLineEdit,
    QDialogButtonBox, QFormLayout, QListWidget, QListWidgetItem, QMenu
)

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
MASCOT_DIR = os.path.join(MODULE_DIR, "resources", "mascot")

# brand palette (same as the web SPA / old QML theme)
C_BG = "#f7f6f2"
C_CARD = "#ffffff"
C_BORDER = "#e6e3d9"
C_BORDER2 = "#d8d4c6"
C_TEXT = "#2a2f28"
C_DIM = "#75806f"
C_LIME = "#8dc63f"
C_LIME_DEEP = "#6fa82e"
C_LIME_SOFT = "#f0f7e3"
C_TEAL = "#2ea3a8"
C_DANGER = "#e05d5d"

MASCOT_MOVIES = {
    "idle": os.path.join(MASCOT_DIR, "mascot_idle_light.gif"),
    "think": os.path.join(MASCOT_DIR, "mascot_think_light.gif"),
    "happy": os.path.join(MASCOT_DIR, "mascot_happy_light.gif"),
}
# static fallback frames for Qt builds whose QtGui lacks the GIF handler
MASCOT_FRAMES = {
    "idle": os.path.join(MASCOT_DIR, "mascot_idle_light_frame.png"),
    "think": os.path.join(MASCOT_DIR, "mascot_think_light_frame.png"),
    "happy": os.path.join(MASCOT_DIR, "mascot_happy_light_frame.png"),
}

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
MODEL_EXT = (".stl", ".obj", ".3mf")

PRESETS = [
    (u"Кронштейн", u"Кронштейн под нагрузку, PETG, без поддержек. Приложу STL — "
                    u"настрой печать полностью и запусти слайсинг."),
    (u"Втулка 5D", u"Втулка/штуцер: приложен STL. Проанализируй геометрию, выбери "
                    u"режим (рассмотри 5D Spiral), материал и параметры, сориентируй "
                    u"по оси Z и запусти слайсинг."),
    (u"Анализ сцены", u"Проанализируй текущую сцену и текущие настройки слайсера: "
                      u"предложи и примени оптимизации (режим, ориентация, параметры), "
                      u"затем запусти слайсинг."),
    (u"3D vs 5D", u"Сравни для детали на столе печать в 3D и 5D: геометрия, поддержки, "
                  u"прочность, время. Выбери лучший режим, настрой и прослайсь."),
]


def _log(level, message):
    """UM.Logger when inside the slicer, stderr in standalone preview."""
    try:
        from UM.Logger import Logger
        Logger.log(level, "[AI Assistant] " + message)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# light markdown -> HTML for QTextBrowser (no external deps)
# ----------------------------------------------------------------------------

def _esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline_md(text):
    text = text.replace("`", "\x01")  # protect code spans from other rules
    parts = text.split("\x01")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append("<code>%s</code>" % part)
        else:
            part = _esc(part)
            part = part.replace("**", "\x02")
            bold_parts = part.split("\x02")
            part = "".join(
                ("<b>%s</b>" % p if j % 2 == 1 else p)
                for j, p in enumerate(bold_parts))
            # links [text](url)
            while "](" in part:
                head, _, rest = part.partition("](")
                label_end = head.rfind("[")
                url_end = rest.find(")")
                if label_end < 0 or url_end < 0:
                    break
                label = head[label_end + 1:]
                url = rest[:url_end]
                part = (head[:label_end] +
                        '<a href="%s">%s</a>' % (_esc(url), label) + rest[url_end + 1:])
            out.append(part)
    return "".join(out)


def md_to_html(md):
    """Convert the subset of markdown the LLM actually emits to QTextBrowser
    HTML: fenced code, headings, tables, lists, bold, code, links, quotes."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    html = []
    code_block = None
    list_mode = None      # None / "ul" / "ol"
    table_rows = None

    def close_list():
        if list_mode is not None:
            html.append("</%s>" % list_mode)

    def close_table():
        if table_rows is not None:
            html.append("</tbody></table>")
            del table_rows[:]

    for raw in lines:
        stripped = raw.strip()

        # --- fenced code ---
        if stripped.startswith("```"):
            if code_block is None:
                close_list()
                close_table()
                code_block = []
            else:
                html.append("<pre>%s</pre>" % _esc("\n".join(code_block)))
                code_block = None
            continue
        if code_block is not None:
            code_block.append(raw)
            continue

        # --- table ---
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue  # separator row
            if table_rows is None:
                close_list()
                table_rows = [[]]
                html.append('<table width="100%%" cellspacing="0" cellpadding="8" '
                            'bgcolor="%s"><thead><tr>' % C_CARD)
                for c in cells:
                    html.append('<th align="left" bgcolor="%s"><b>%s</b></th>'
                                % (C_LIME_SOFT, _inline_md(c)))
                html.append("</tr></thead><tbody>")
            else:
                html.append("<tr>")
                for c in cells:
                    html.append("<td align=\"left\">%s</td>" % _inline_md(c))
                html.append("</tr>")
            continue
        else:
            close_table()

        # --- headings ---
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            level = max(1, min(4, level))
            close_list()
            html.append("<h%d>%s</h%d>" % (level, _inline_md(stripped.lstrip("# ")), level))
            continue

        # --- blockquote ---
        if stripped.startswith(">"):
            close_list()
            html.append('<p style="color:%s">&gt; %s</p>' % (C_DIM, _inline_md(stripped.lstrip("> "))))
            continue

        # --- lists ---
        if stripped.startswith(("- ", "* ", "• ")) or (len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)" and stripped[2] == " "):
            ordered = stripped[0].isdigit()
            mode = "ol" if ordered else "ul"
            if list_mode != mode:
                close_list()
                html.append("<%s>" % mode)
                list_mode = mode
            html.append("<li>%s</li>" % _inline_md(stripped.lstrip("-*• ").lstrip("0123456789.) ")))
            continue
        else:
            close_list()
            list_mode = None

        if not stripped:
            html.append("<br>")
        else:
            html.append("<p>%s</p>" % _inline_md(stripped))
    close_list()
    close_table()
    if code_block is not None:
        html.append("<pre>%s</pre>" % _esc("\n".join(code_block)))
    return "".join(html)


# ----------------------------------------------------------------------------
# cross-thread bridge: agent threads -> Qt main thread (queued signal)
# ----------------------------------------------------------------------------

class ChatBridge(QObject):
    """EventBus listeners run on agent/HTTP threads; emitting this signal
    marshals the event into the Qt main-thread event loop automatically
    (auto connection -> queued because the bridge lives in the main thread)."""

    eventReceived = pyqtSignal(object)


# ----------------------------------------------------------------------------
# mascot card
# ----------------------------------------------------------------------------

class MascotWidget(QFrame):
    """Frameless draggable chameleon card. QMovie renders the GIF through
    QLabel paint events (QPainter), not the Qt Quick scene graph."""

    clicked = pyqtSignal()

    def __init__(self):
        super(MascotWidget, self).__init__(None,
                                           Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(210, 262)
        self._drag_offset = None
        self._state = "idle"
        self._static = False

        self._movie_label = QLabel(self)
        self._movie_label.setGeometry(19, 40, 173, 211)
        self._movie_label.setAlignment(Qt.AlignCenter)
        self._movie_label.setScaledContents(True)   # GIFs are 240x320
        self._movie = None

        self.set_state("idle")

    # --- state / speech ---

    def set_state(self, state):
        if state == self._state and (self._movie is not None or self._static):
            return
        self._state = state
        path = MASCOT_MOVIES.get(state) or MASCOT_MOVIES["idle"]
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        movie = QMovie(path)
        if movie.isValid() and movie.frameCount() != 0:
            self._movie = movie
            self._movie_label.setMovie(self._movie)
            self._movie.start()
        else:
            # Qt build without the GIF handler -> static PNG frame
            frame_path = MASCOT_FRAMES.get(state) or MASCOT_FRAMES["idle"]
            self._movie_label.setPixmap(QPixmap(frame_path))
        self._static = not movie.isValid()
        self.update()

    # --- interaction ---

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPos() - self._drag_offset)
            self.manual_position = self.pos()

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            moved = (event.globalPos() - self._drag_offset - self.pos()).manhattanLength() > 3
            self._drag_offset = None
            if not moved:
                self.clicked.emit()

    def mouseDoubleClickEvent(self, event):
        # double-click forgets the manual position and re-pins to the corner
        self.manual_position = None

    manual_position = None


# ----------------------------------------------------------------------------
# speech bubble (chat-style, floats to the left of the mascot)
# ----------------------------------------------------------------------------

class SpeechBubble(QFrame):
    """The mascot's speech as a messenger-style message floating ABOVE the
    chameleon: lime->teal->blue gradient, white text, tail pointing down
    at him."""

    TAIL = 12
    BODY_H = 46
    MAX_TEXT = 430

    def __init__(self):
        super(SpeechBubble, self).__init__(None,
                                           Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._body_h = self.BODY_H
        self._label = QLabel(self)
        self._label.setStyleSheet(
            "color:#ffffff; font-size:20px; font-weight:600; "
            "background:transparent;")
        self._label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def say(self, text, sticky=False):
        text = (text or u"").strip()
        if not text:
            self._timer.stop()
            self.hide()
            return
        metrics = QFontMetrics(self._label.font())
        # explicit \n lines are kept as-is (no eliding) so e.g. the greeting
        # always shows its full text wrapped over two lines
        lines = [l for l in text.split(u"\n") if l.strip()]
        if len(lines) > 1 and max(metrics.width(l) for l in lines) <= self.MAX_TEXT:
            shown_lines = lines
        else:
            shown_lines = [metrics.elidedText(
                text.replace(u"\n", u" ") if len(text) <= 90 else text[:90] + u"…",
                Qt.ElideRight, self.MAX_TEXT)]
        text_w = max(metrics.width(l) for l in shown_lines)
        line_h = metrics.height()
        self._label.setText(u"\n".join(shown_lines))
        self._body_h = self.BODY_H + (len(shown_lines) - 1) * (line_h + 2)
        self._label.setGeometry(20, 0, text_w + 4, self._body_h)
        self.setFixedSize(text_w + 40, self._body_h + self.TAIL)
        if sticky:
            self._timer.stop()      # e.g. the greeting: stays until replaced
        else:
            self._timer.start(9000)
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self._body_h
        gradient = QLinearGradient(0, 0, w, 0)
        gradient.setColorAt(0.0, QColor("#65b04b"))
        gradient.setColorAt(0.52, QColor("#2e9e8f"))
        gradient.setColorAt(1.0, QColor("#1f7fa8"))
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, 16, 16)
        cx = w / 2.0
        path.moveTo(cx - 9, h - 1)
        path.lineTo(cx, h + self.TAIL - 1)
        path.lineTo(cx + 9, h - 1)
        path.closeSubpath()
        painter.fillPath(path, QBrush(gradient))


# ----------------------------------------------------------------------------
# chat panel
# ----------------------------------------------------------------------------

class ChatPanel(QFrame):
    """Frameless chat window: header, presets, slicing progress, transcript
    (QTextBrowser with markdown), attachments, composer."""

    sendMessageRequested = pyqtSignal(str)
    attachFilesRequested = pyqtSignal(list)
    removeAttachmentRequested = pyqtSignal(int)
    actionRequested = pyqtSignal(str)   # browser/clear/settings/tools/close

    def __init__(self):
        super(ChatPanel, self).__init__(None,
                                        Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(1000, 1320)
        self._drag_offset = None
        self._attachments = []      # [(name, path)]
        self._last_assistant_text = u""

        self.setStyleSheet("""
            QLabel { color: %s; font-size: 24px; }
            QPushButton {
                background: %s; border: 1px solid %s; border-radius: 18px;
                padding: 12px 24px; color: %s; font-size: 24px;
            }
            QPushButton:hover { background: %s; border-color: %s; color: %s; }
            QPushButton:disabled { color: #a8ab9e; background: #efede5; }
            QPushButton#sendBtn {
                background: %s; color: #ffffff; font-weight: bold; border: none;
                border-radius: 20px; padding: 16px 32px;
            }
            QPushButton#sendBtn:hover { background: #96cf52; }
            QPushButton#sendBtn:disabled { background: #e9e7df; }
            QPushButton#closeBtn:hover { background: #fbe7e7; border-color: %s; color: %s; }
        """ % (C_TEXT, C_CARD, C_BORDER2, C_TEXT,
               C_LIME_SOFT, C_LIME, C_LIME_DEEP,
               C_LIME, C_DANGER, C_DANGER))

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        root.addWidget(self._build_header())
        root.addWidget(self._build_presets())
        root.addWidget(self._build_progress())
        root.addWidget(self._build_transcript(), 1)
        root.addWidget(self._build_status_strip())
        root.addWidget(self._build_attachments())
        root.addWidget(self._build_composer())

        self._add_intro()

    def paintEvent(self, event):
        # one solid white card behind every section: no see-through gaps
        # between header / transcript / composer
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(C_BORDER2), 2))
        painter.setBrush(QColor(C_CARD))
        painter.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2,
                                24, 24)

    # --- builders ---

    def _build_header(self):
        header = QFrame()
        header.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s; border-radius: 12px; }"
            % (C_CARD, C_BORDER))
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 16, 16, 16)
        layout.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(u"Кали · 3D/5D Copilot")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: %s;" % C_TEXT)
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self._status_dot = QLabel(u"●")
        self._status_dot.setFixedWidth(32)
        self._status_dot.setStyleSheet("color: %s; font-size: 24px;" % C_LIME_DEEP)
        self._status_label = QLabel(u"Готов к работе")
        self._status_label.setStyleSheet("color: %s; font-size: 22px;" % C_DIM)
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_label)
        status_row.addStretch(1)
        title_box.addLayout(status_row)

        layout.addLayout(title_box)
        layout.addStretch(1)

        for text, action, tooltip in (
                (u"🌐 В браузере", "browser", u"Открыть полную веб-версию чата"),
                (u"🛠", "tools", u"MCP-инструменты"),
                (u"⚙", "settings", u"API-ключ, адрес и модель"),
                (u"🧹", "clear", u"Стереть историю диалога"),
                (u"✕", "close", u"Свернуть чат (Кали останется на месте)")):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            if action == "close":
                button.setObjectName("closeBtn")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, a=action: self.actionRequested.emit(a))
            layout.addWidget(button)
        header.installEventFilter(self)
        self._header = header
        return header

    def _build_presets(self):
        row = QFrame()
        row.setStyleSheet("QFrame { background: %s; border: 1px solid %s; "
                          "border-radius: 10px; }" % (C_CARD, C_BORDER))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)
        for index, (label, prompt) in enumerate(PRESETS):
            button = QPushButton(label)
            button.setToolTip(prompt)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _=False, p=prompt: self.sendMessageRequested.emit(p))
            layout.addWidget(button)
        return row

    def _build_progress(self):
        self._progress_frame = QFrame()
        self._progress_frame.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s; border-radius: 10px; }"
            % (C_LIME_SOFT, C_LIME))
        layout = QHBoxLayout(self._progress_frame)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(16)
        label = QLabel(u"🧱 Нарезка")
        label.setStyleSheet("color: %s; font-weight: bold;" % C_LIME_DEEP)
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(24)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #ffffff; border: none; border-radius: 5px; }"
            "QProgressBar::chunk { background: %s; border-radius: 5px; }" % C_LIME)
        self._progress_label = QLabel("0%")
        self._progress_label.setStyleSheet("color: %s;" % C_DIM)
        layout.addWidget(label)
        layout.addWidget(self._progress_bar, 1)
        layout.addWidget(self._progress_label)
        self._progress_frame.hide()
        return self._progress_frame

    def _build_transcript(self):
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s; border-radius: 12px; }"
            % (C_CARD, C_BORDER))
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        self._transcript = QTextBrowser()
        self._transcript.setOpenExternalLinks(True)
        self._transcript.setStyleSheet(
            "QTextBrowser { border: none; background: %s; }" % C_CARD)
        self._transcript.document().setDefaultStyleSheet("""
            body { font-family: 'Segoe UI'; color: %s; }
            h1, h2, h3, h4 { margin: 8px 0; }
            code { background-color: #f0f0ec; }
            pre { background-color: #f0f0ec; }
            p { margin: 4px 0; }
            table { font-size: 22px; }
        """ % C_TEXT)
        self._transcript.setContextMenuPolicy(Qt.CustomContextMenu)
        self._transcript.customContextMenuRequested.connect(self._transcript_menu)
        layout.addWidget(self._transcript)
        return frame

    def _build_status_strip(self):
        self._typing_label = QLabel(u"")
        self._typing_label.setStyleSheet(
            "color: %s; font-style: italic; font-size: 22px;" % C_DIM)
        self._typing_label.hide()
        return self._typing_label

    def _build_attachments(self):
        self._attach_row = QFrame()
        self._attach_row.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s; border-radius: 10px; }"
            % (C_CARD, C_BORDER))
        self._attach_layout = QHBoxLayout(self._attach_row)
        self._attach_layout.setContentsMargins(16, 8, 16, 8)
        self._attach_layout.setSpacing(12)
        self._attach_row.hide()
        return self._attach_row

    def _build_composer(self):
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s; border-radius: 12px; }"
            % (C_CARD, C_BORDER))
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        attach_button = QPushButton(u"📎 Файл")
        attach_button.setToolTip(u"3D-модель (STL/OBJ/3MF) или изображение")
        attach_button.setCursor(Qt.PointingHandCursor)
        attach_button.clicked.connect(self._pick_files)

        self._input = QTextEdit()
        self._input.setPlaceholderText(
            u"Опишите задачу… (Enter — отправить, Shift+Enter — новая строка)")
        self._input.setFixedHeight(128)
        self._input.setStyleSheet(
            "QTextEdit { background: %s; border: 1px solid %s; border-radius: 18px; "
            "color: %s; font-size: 24px; padding: 8px; }" % (C_BG, C_BORDER2, C_TEXT))
        self._input.installEventFilter(self)

        self._send_button = QPushButton(u"➤")
        self._send_button.setObjectName("sendBtn")
        self._send_button.setToolTip(u"Отправить")
        self._send_button.setCursor(Qt.PointingHandCursor)
        self._send_button.setFixedSize(96, 88)
        self._send_button.clicked.connect(self._submit)

        layout.addWidget(attach_button)
        layout.addWidget(self._input, 1)
        layout.addWidget(self._send_button)
        return frame

    # --- transcript blocks ---

    def _add_intro(self):
        hello = (u"Привет! Я **Кали** 🦎 — ИИ-технолог по 3D/5D-печати.\n\n"
                 u"Опишите задачу и приложите модель (📎) — я сам выберу режим, "
                 u"материал, параметры, сориентирую деталь и запущу слайсинг.")
        self._append_html(
            '<table width="100%%" cellspacing="0" cellpadding="16"><tr>'
            '<td bgcolor="%s">%s</td></tr></table>' % (C_LIME_SOFT, md_to_html(hello)))

    def _append_html(self, html):
        bar = self._transcript.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 24
        self._transcript.append(html)
        if at_bottom:
            # scroll AFTER the document relayouts, not right after append()
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def append_user(self, text, names):
        files = u""
        if names:
            files = u"<br>" + u" ".join(u"📎 %s" % _esc(n) for n in names)
        self._append_html(
            '<table width="80%%" align="right" cellspacing="0" cellpadding="14"><tr>'
            '<td bgcolor="%s"><b>Вы</b><br>%s%s</td></tr></table>'
            % (C_CARD, _inline_md(text) or u"<i>(файлы)</i>", files))

    def append_assistant(self, text):
        self._last_assistant_text = text or u""
        self._append_html(
            '<table width="94%%" align="left" cellspacing="0" cellpadding="14"><tr>'
            '<td bgcolor="%s"><span style="color:%s;"><b>🦎 Кали</b></span><br>%s</td>'
            '</tr></table><br>'
            % (C_LIME_SOFT, C_LIME_DEEP, md_to_html(text)))

    def append_error(self, text):
        self._append_html(
            '<table width="94%%" align="left" cellspacing="0" cellpadding="14"><tr>'
            '<td bgcolor="#fdf1f1"><span style="color:%s;"><b>⚠ Ошибка</b></span><br>'
            '%s</td></tr></table><br>' % (C_DANGER, _inline_md(text)))

    def append_tool_result(self, name, ok, duration_ms, summary):
        mark = u"✓" if ok else u"✗"
        color = C_LIME_DEEP if ok else C_DANGER
        suffix = u" · %s мс" % duration_ms if duration_ms else u""
        self._append_html(
            '<table width="94%%" align="left" cellspacing="0" cellpadding="6"><tr>'
            '<td bgcolor="#ffffff"><span style="color:%s;">→ %s %s%s</span><br>'
            '<span style="font-family: Consolas; font-size: 22px; color:%s;">%s</span>'
            '</td></tr></table><br>'
            % (color, mark, _esc(name), suffix, C_DIM, _esc((summary or "")[:400])))

    # --- status / progress / busy ---

    def set_status(self, text, kind):
        self._status_label.setText(text)
        colors = {"ready": C_LIME_DEEP, "thinking": C_LIME,
                  "processing": "#e8a13c", "error": C_DANGER}
        self._status_dot.setStyleSheet(
            "color: %s; font-size: 12px;" % colors.get(kind, C_DIM))

    def set_typing(self, active):
        self._typing_label.setText(u"Кали думает…" if active else u"")
        self._typing_label.setVisible(bool(active))

    def set_busy(self, busy):
        self._send_button.setEnabled(not busy)
        self._send_button.setText(u"…" if busy else u"➤")
        self._input.setEnabled(not busy)

    def set_slicing(self, active, percent):
        self._progress_frame.setVisible(bool(active))
        if active:
            self._progress_bar.setValue(int(max(0.0, min(100.0, percent))))
            self._progress_label.setText(u"%d%%" % int(percent))

    # --- attachments ---

    def set_attachments(self, names):
        while self._attach_layout.count():
            item = self._attach_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, name in enumerate(names):
            chip = QLabel(u"📎 %s" % name)
            chip.setStyleSheet(
                "background: %s; border: 1px solid %s; border-radius: 20px; "
                "padding: 6px 16px; color: %s;" % (C_LIME_SOFT, C_LIME, C_TEXT))
            remove = QPushButton(u"×")
            remove.setFixedSize(36, 36)
            remove.setStyleSheet(
                "QPushButton { border: none; color: %s; font-weight: bold; "
                "background: transparent; } QPushButton:hover { color: %s; }"
                % (C_DANGER, C_DANGER))
            remove.setCursor(Qt.PointingHandCursor)
            remove.clicked.connect(lambda _=False, i=index:
                                    self.removeAttachmentRequested.emit(i))
            self._attach_layout.addWidget(chip)
            self._attach_layout.addWidget(remove)
        self._attach_row.setVisible(bool(names))

    # --- composer behaviour ---

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, u"Выберите 3D-модель или изображение", "",
            u"Модели и изображения (*.stl *.STL *.obj *.3mf *.png *.jpg *.jpeg);;"
            u"Все файлы (*.*)")
        if paths:
            self.attachFilesRequested.emit(list(paths))

    def _submit(self):
        text = self._input.toPlainText().strip()
        if text or self._attachments:
            self.sendMessageRequested.emit(text)
            self._input.clear()

    def eventFilter(self, obj, event):
        # fires during construction too (header events) — guard late attrs
        input_edit = getattr(self, "_input", None)
        if input_edit is not None and obj is input_edit and \
                event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and \
                    not event.modifiers() & Qt.ShiftModifier:
                self._submit()
                return True
        if obj is getattr(self, "_header", None):
            if event.type() == QEvent.MouseButtonPress:
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            elif event.type() == QEvent.MouseMove and self._drag_offset is not None:
                self.move(event.globalPos() - self._drag_offset)
            elif event.type() == QEvent.MouseButtonRelease:
                self._drag_offset = None
        return super(ChatPanel, self).eventFilter(obj, event)

    def _transcript_menu(self, point):
        menu = QMenu(self)
        copy_sel = menu.addAction(u"Копировать выделение")
        copy_last = menu.addAction(u"Копировать последний ответ Кали")
        chosen = menu.exec_(self._transcript.mapToGlobal(point))
        if chosen == copy_sel:
            self._transcript.copy()
        elif chosen == copy_last:
            QApplication.clipboard().setText(self._last_assistant_text)

    def get_last_assistant_text(self):
        return self._last_assistant_text



# ----------------------------------------------------------------------------
# orchestrator: extension <-> widgets
# ----------------------------------------------------------------------------

class WidgetsUI(QObject):
    """Owns the mascot card and chat panel, feeds them from the shared
    EventBus (same events the web SPA polls) and posts user input back to
    the extension's shared submit_chat entry point."""

    def __init__(self, extension):
        super(WidgetsUI, self).__init__()
        self._extension = extension
        self._attached_paths = []
        self._busy = False
        self._happy_timer = QTimer(self)
        self._happy_timer.setSingleShot(True)
        self._happy_timer.timeout.connect(self._stop_celebrating)

        self._bridge = ChatBridge()
        self._bridge.eventReceived.connect(self._handle_event)
        extension.bus.subscribe(self._on_bus_event)

        self._panel = ChatPanel()
        self._panel.sendMessageRequested.connect(self._send)
        self._panel.attachFilesRequested.connect(self._attach_files)
        self._panel.removeAttachmentRequested.connect(self._remove_attachment)
        self._panel.actionRequested.connect(self._on_action)
        self._panel.installEventFilter(self)   # Alt+F4 / external close

        self._mascot = MascotWidget()
        self._mascot.clicked.connect(self.toggle_panel)
        self._bubble = SpeechBubble()

        self._panel_visible = False
        self._panel_position = None

        # preload conversation shared with the web SPA
        for message in extension.chat.history():
            if message.get("role") == "user":
                self._panel.append_user(message.get("text", ""), [])
            else:
                for card in message.get("tool_cards") or []:
                    self._panel.append_tool_result(
                        card.get("name", "?"), card.get("ok"),
                        card.get("duration_ms"), u"")
                text = message.get("text")
                if text:
                    self._panel.append_assistant(text)

        # slicing progress poller (thread-safe snapshot via the extension)
        self._slice_timer = QTimer(self)
        self._slice_timer.setInterval(1000)
        self._slice_timer.timeout.connect(self._refresh_slicing)
        self._slice_timer.start()

        # keep the mascot glued to the slicer main window corner; also track
        # activation/minimization so our windows behave like owned children:
        # above the slicer when it is focused, hidden when it is minimized,
        # never floating over unrelated applications.
        self._host_minimized = False
        self._mascot_started = False
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(400)
        self._follow_timer.timeout.connect(self._follow_host)
        self._follow_timer.start()

    # ------------- lifecycle -------------

    def startup(self):
        """Called once the slicer is up: show the mascot in the corner."""
        self._mascot_started = True
        self._follow_host()
        self._mascot.show()
        self._say(u"Привет, я Кали!\nТебе с чем-то помочь?", sticky=True)

    def show_panel(self):
        self._panel_visible = True
        self._panel_position = None   # re-anchor above the mascot on open
        self._bubble.hide()           # the chat itself takes over the speech
        self._follow_host()
        self._panel.show()
        self._panel.raise_()

    def hide_panel(self):
        self._panel.hide()
        self._panel_visible = False

    def toggle_panel(self):
        if self._panel_visible:
            self.hide_panel()
        else:
            self.show_panel()

    def eventFilter(self, obj, event):
        if obj is self._panel and event.type() == QEvent.Close:
            # keep the widget alive; just mark it hidden (mascot stays out)
            self._panel_visible = False
        return super(WidgetsUI, self).eventFilter(obj, event)

    # ------------- host window geometry -------------

    def _host_geometry(self):
        geo = None
        try:
            geo = self._extension.get_main_window_geometry()
        except Exception:
            geo = None
        if not geo:
            screen = QApplication.primaryScreen()
            if screen is None:
                return (100, 100, 1280, 800)
            g = screen.availableGeometry()
            return (g.x(), g.y(), g.width(), g.height())
        return geo

    def _say(self, text, sticky=False):
        self._bubble.say(text, sticky=sticky)
        self._position_bubble()

    def _position_bubble(self):
        if not (self._bubble.isVisible() and self._mascot.isVisible()):
            return
        geo = self._mascot.geometry()
        # messenger-style message: centered ABOVE the chameleon, tail down
        self._bubble.move(geo.x() + geo.width() // 2 -
                          self._bubble.width() // 2,
                          geo.y() - self._bubble.height() - 8)

    def _follow_host(self):
        if self._mascot.manual_position is not None:
            self._position_bubble()
            self._sync_host_state()
            return
        x, y, w, h = self._host_geometry()
        mascot_x = x + w - self._mascot.width() - 190
        # mascot is pinned: never moves when the panel opens/closes
        mascot_y = y + h - self._mascot.height() - 192
        if self._mascot.pos() != QPoint(mascot_x, mascot_y):
            self._mascot.move(mascot_x, mascot_y)
        self._position_bubble()
        if not self._panel_visible:
            self._panel_position = None
        elif self._panel_position is None:
            # panel opens just above Kali's head, right-aligned with him:
            # the card's top 40px are empty, so sinking the panel 28px into
            # it leaves ~12px of air over the gif. Nominal 1000x1320 (2x);
            # height is clamped to what fits without covering the chameleon.
            max_w = w - 32
            max_h = mascot_y + 28 - (y + 8)
            panel_w = min(1000, max_w)
            panel_h = min(1320, max_h)
            if (self._panel.width(), self._panel.height()) != (panel_w, panel_h):
                self._panel.setFixedSize(panel_w, panel_h)
            panel_x = mascot_x + self._mascot.width() - panel_w
            if panel_x < x + 8:
                panel_x = x + 8
            panel_y = mascot_y + 28 - panel_h
            self._panel.move(panel_x, panel_y)
            self._panel_position = QPoint(panel_x, panel_y)
        self._sync_host_state()

    def _sync_host_state(self):
        """The mascot is pinned to the slicer corner: visible whenever the
        slicer window is. Parentless Qt.Tool windows drop below the main
        window whenever a dialog opens/closes or focus moves, so re-show
        and re-raise them on every follow tick (400 ms)."""
        try:
            state = self._extension.get_main_window_state()
        except Exception:
            state = "unknown"
        if state == "minimized":
            if not self._host_minimized:
                self._host_minimized = True
                self._mascot.hide()
                self._panel.hide()
                self._bubble.hide()
            return
        self._host_minimized = False
        if self._mascot_started:
            if not self._mascot.isVisible():
                self._mascot.show()
            self._mascot.raise_()
        if self._panel_visible:
            if not self._panel.isVisible():
                self._panel.show()
            self._panel.raise_()
        if self._bubble.isVisible():
            self._bubble.raise_()

    # ------------- user input -------------

    def _send(self, text):
        text = (text or "").strip()
        paths = list(self._attached_paths)
        if not text and not paths:
            return
        if self._busy:
            self._panel.append_error(u"Агент ещё занят предыдущей задачей")
            return
        self._attached_paths = []
        self._panel.set_attachments([])
        attachments = []
        for path in paths:
            lower = path.lower()
            kind = ("image" if lower.endswith(IMAGE_EXT)
                    else "model" if lower.endswith(MODEL_EXT) else "file")
            attachments.append({"name": os.path.basename(path),
                                "path": path, "kind": kind})
        try:
            ok, error = self._extension.submit_chat(text, attachments)
        except Exception as e:
            _log("e", "submit_chat failed: %s" % e)
            ok, error = False, str(e)
        if not ok and error:
            hint = (u"Сначала задайте API-ключ (⚙ Настройки)"
                    if error == "api_key_missing" else error)
            self._panel.append_error(u"%s" % hint)

    def _attach_files(self, paths):
        for path in paths:
            if os.path.isfile(path) and path not in self._attached_paths:
                if len(self._attached_paths) >= 6:
                    break
                self._attached_paths.append(path)
        self._panel.set_attachments(
            [os.path.basename(p) for p in self._attached_paths])

    def _remove_attachment(self, index):
        if 0 <= index < len(self._attached_paths):
            self._attached_paths.pop(index)
            self._panel.set_attachments(
                [os.path.basename(p) for p in self._attached_paths])

    def _on_action(self, action):
        if action == "browser":
            try:
                self._extension.showChat()
            except Exception as e:
                _log("e", "openWebChat failed: %s" % e)
        elif action == "clear":
            try:
                self._extension.chat.clear()
            except Exception:
                pass
            self._panel._transcript.clear()
            self._panel._add_intro()
        elif action == "settings":
            self._open_settings()
        elif action == "tools":
            self._open_tools()
        elif action == "close":
            self.hide_panel()

    # ------------- dialogs -------------

    def _open_settings(self):
        dialog = QDialog(self._panel)
        dialog.setWindowTitle(u"Настройки API (OpenAI-совместимый)")
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setEchoMode(QLineEdit.Password)
        url_edit = QLineEdit()
        model_edit = QLineEdit()
        try:
            values = self._extension.get_settings_values() or {}
        except Exception:
            values = {}
        key_edit.setText(values.get("api_key") or u"")
        url_edit.setText(values.get("api_url") or u"")
        model_edit.setText(values.get("model_name") or u"")
        form.addRow(u"API Key:", key_edit)
        form.addRow(u"Base URL:", url_edit)
        form.addRow(u"Модель:", model_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec_() == QDialog.Accepted:
            try:
                self._extension.save_settings(
                    url_edit.text().strip(), key_edit.text().strip(),
                    model_edit.text().strip())
                self._panel.set_status(u"Настройки сохранены", "ready")
            except Exception as e:
                _log("e", "saveSettings failed: %s" % e)
                self._panel.set_status(u"Ошибка сохранения настроек", "error")

    def _open_tools(self):
        dialog = QDialog(self._panel)
        dialog.setWindowTitle(u"MCP-инструменты агента")
        dialog.resize(840, 760)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        try:
            tools = self._extension.mcp.tools_list()
        except Exception:
            tools = []
        for tool in tools or []:
            name = tool.get("name", "?")
            description = (tool.get("description") or u"").strip()
            listing.addItem(QListWidgetItem(u"%s\n%s" % (name, description[:150])))
        if not tools:
            listing.addItem(QListWidgetItem(u"(недоступно вне слайсера)"))
        layout.addWidget(listing)
        close = QPushButton(u"Закрыть")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()

    # ------------- events from the agent (already on the Qt main thread) -------------

    def _on_bus_event(self, event):
        # runs on agent/HTTP threads -> queued signal into the main thread
        self._bridge.eventReceived.emit(event)

    def _handle_event(self, event):
        try:
            self._process_event(event)
        except Exception as e:
            _log("w", "widget event failed: %s" % e)

    def _process_event(self, event):
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "user":
            self._panel.append_user(data.get("text", ""),
                                    data.get("attachments") or [])
        elif etype == "assistant":
            self._panel.set_typing(False)
            self._panel.set_busy(False)
            self._panel.append_assistant(data.get("text", ""))
            self._mascot.set_state("happy")
            self._happy_timer.start(4200)
            snippet = " ".join((data.get("text") or "").replace("\n", " ").split())
            for mark in ("**", "`", "#", ">", "|"):
                snippet = snippet.replace(mark, " ")
            snippet = " ".join(snippet.split())
            if len(snippet) > 80:
                snippet = snippet[:80] + u"…"
            if snippet:
                self._say(snippet)
        elif etype == "tool_call":
            self._busy = True
            self._panel.set_busy(True)
            self._panel.set_typing(False)
            self._panel.set_status(u"Выполняет: %s…" % data.get("name", ""), "processing")
            self._mascot.set_state("think")
            self._say(u"Работаю со слайсером: %s…" % data.get("name", "?"))
        elif etype == "tool_result":
            self._panel.append_tool_result(
                data.get("name", "?"), data.get("ok"),
                data.get("duration_ms"), data.get("summary"))
        elif etype == "status":
            if data.get("state") == "thinking":
                self._busy = True
                self._panel.set_busy(True)
                self._panel.set_typing(True)
                self._panel.set_status(u"Анализирует запрос…", "thinking")
                self._mascot.set_state("think")
                self._say(u"Хм-хм… анализирую задачу…")
            elif data.get("state") == "idle":
                self._busy = False
                self._panel.set_busy(False)
                self._panel.set_typing(False)
                self._panel.set_status(u"Готов к работе", "ready")
                self._mascot.set_state("idle")

    def _stop_celebrating(self):
        if not self._busy:
            self._mascot.set_state("idle")

    # ------------- slicing progress -------------

    def _refresh_slicing(self):
        try:
            try:
                snapshot = self._extension.get_slicing_snapshot()
            except Exception:
                snapshot = None
            active = bool(snapshot) and snapshot.get("state") == "slicing"
            self._panel.set_slicing(
                active, float((snapshot or {}).get("progress", 0.0)) * 100.0)
        except Exception as e:
            # a UI poller must never take the whole slicer down
            _log("e", "slicing poll failed: %s" % e)
