// Native AI chat window inside STE Slicer — light "Pascal" theme.
// Palette from the team's mockup: off-white #F7F6F2, white cards,
// chameleon lime #8DC63F / teal #2EA3A8 accents. QtQuick.Controls 1.4.
import QtQuick 2.2
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4
import QtQuick.Layouts 1.1
import QtQuick.Window 2.2
import QtQuick.Dialogs 1.2

Window
{
    id: chatWindow
    title: "AI-ассистент · STE Slicer 3D/5D Copilot"
    modality: Qt.NonModal
    // WindowSystemMenuHint обязателен на Windows: без него кнопки
    // закрытия/свёртывания не отрисовываются
    flags: Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint

    // 2x scale, clamped to the physical screen
    width: Math.min(1120, Screen.desktopAvailableWidth * 0.95)
    height: Math.min(1480, Screen.desktopAvailableHeight * 0.95)
    minimumWidth: Math.min(880, Screen.desktopAvailableWidth * 0.6)
    minimumHeight: Math.min(1040, Screen.desktopAvailableHeight * 0.6)
    color: "#f7f6f2"

    visible: manager ? manager.chatWindowVisible : false
    onClosing: {
        if (manager) manager.chatWindowVisible = false
    }

    property bool showSettings: false

    // palette
    property color cBg: "#f7f6f2"
    property color cCard: "#ffffff"
    property color cBorder: "#e6e3d9"
    property color cBorder2: "#d8d4c6"
    property color cText: "#2a2f28"
    property color cDim: "#75806f"
    property color cLime: "#8dc63f"
    property color cLimeDeep: "#6fa82e"
    property color cLimeSoft: "#f0f7e3"
    property color cTeal: "#2ea3a8"
    property color cTealSoft: "#e4f4f4"
    property color cDanger: "#e05d5d"

    function stripMd(s) {
        if (!s) return ""
        var t = s
        t = t.replace(/```[a-zA-Z]*\n?/g, "")
        t = t.replace(/#{1,4}\s*/g, "")
        t = t.replace(/\*\*([^*]+)\*\*/g, "$1")
        t = t.replace(/\*([^*\n]+)\*/g, "$1")
        t = t.replace(/`([^`]+)`/g, "$1")
        t = t.replace(/^\s*[-*]\s+/gm, "• ")
        t = t.replace(/\|/g, " ")
        return t
    }

    FileDialog
    {
        id: fileDialog
        title: "Выберите 3D-модель или изображение"
        nameFilters: [
            "Модели и изображения (*.stl *.STL *.obj *.3mf *.png *.jpg *.jpeg)",
            "3D-модели (*.stl *.STL *.obj *.3mf)",
            "Изображения (*.png *.jpg *.jpeg)",
            "Все файлы (*.*)"
        ]
        selectMultiple: true
        onAccepted: {
            for (var i = 0; i < fileUrls.length; i++)
                manager.attachFile(fileUrls[i])
        }
    }

    ColumnLayout
    {
        anchors.fill: parent
        spacing: 0

        // ============ 1. HEADER ============
        Rectangle
        {
            Layout.fillWidth: true
            height: 100
            color: cCard

            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: cBorder }

            RowLayout
            {
                anchors.fill: parent
                anchors.leftMargin: 18
                anchors.rightMargin: 12
                spacing: 14

                ColumnLayout
                {
                    Layout.fillWidth: true
                    spacing: 4

                    Text
                    {
                        text: "STE Copilot · 3D/5D"
                        color: cText
                        font.pixelSize: 28
                        font.bold: true
                    }

                    RowLayout
                    {
                        spacing: 8

                        Rectangle
                        {
                            width: 12; height: 12; radius: 6
                            color: {
                                if (!manager) return cDim
                                if (manager.statusType === "ready") return cLimeDeep
                                if (manager.statusType === "error") return cDanger
                                if (manager.statusType === "processing") return "#e8a13c"
                                return cLime
                            }
                        }

                        Text
                        {
                            text: manager ? manager.statusText : "…"
                            color: cDim
                            font.pixelSize: 22
                        }
                    }
                }

                Button
                {
                    width: 56; height: 56
                    tooltip: "Открыть чат в браузере (полный интерфейс)"
                    onClicked: if (manager) manager.openWebChat()

                    style: ButtonStyle
                    {
                        background: Rectangle
                        {
                            radius: 12
                            color: control.hovered ? cLimeSoft : "#fbfaf6"
                            border.width: 1
                            border.color: control.hovered ? cLime : cBorder
                            Text { anchors.centerIn: parent; text: "🌐"; font.pixelSize: 26; color: cText }
                        }
                    }
                }

                Button
                {
                    width: 56; height: 56
                    tooltip: "Очистить диалог"
                    onClicked: if (manager) manager.clearChat()

                    style: ButtonStyle
                    {
                        background: Rectangle
                        {
                            radius: 12
                            color: control.hovered ? cLimeSoft : "#fbfaf6"
                            border.width: 1
                            border.color: control.hovered ? cLime : cBorder
                            Text { anchors.centerIn: parent; text: "🗑"; font.pixelSize: 26; color: cText }
                        }
                    }
                }

                Button
                {
                    width: 56; height: 56
                    tooltip: "Настройки API"
                    onClicked: chatWindow.showSettings = !chatWindow.showSettings

                    style: ButtonStyle
                    {
                        background: Rectangle
                        {
                            radius: 12
                            color: chatWindow.showSettings ? cLimeSoft : (control.hovered ? cLimeSoft : "#fbfaf6")
                            border.width: 1
                            border.color: chatWindow.showSettings ? cLime : (control.hovered ? cLime : cBorder)
                            Text { anchors.centerIn: parent; text: "⚙"; font.pixelSize: 30; color: chatWindow.showSettings || control.hovered ? cLimeDeep : cDim }
                        }
                    }
                }

                Button
                {
                    width: 56; height: 56
                    tooltip: "Закрыть окно"
                    onClicked: if (manager) manager.hideChatWindow()

                    style: ButtonStyle
                    {
                        background: Rectangle
                        {
                            radius: 12
                            color: control.hovered ? "#fbe7e7" : "#fbfaf6"
                            border.width: control.hovered ? 1 : 1
                            border.color: control.hovered ? cDanger : cBorder
                            Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 28; color: control.hovered ? cDanger : cDim }
                        }
                    }
                }
            }
        }

        // ============ 2. SETTINGS PANEL ============
        Rectangle
        {
            Layout.fillWidth: true
            height: chatWindow.showSettings ? settingsCol.implicitHeight + 28 : 0
            visible: height > 0
            clip: true
            color: cCard

            Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.InOutQuad } }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: cBorder }

            ColumnLayout
            {
                id: settingsCol
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                Text
                {
                    text: "Параметры OpenAI-совместимого API"
                    color: cLimeDeep
                    font.pixelSize: 24
                    font.bold: true
                }

                RowLayout
                {
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: "API Key:"; color: cDim; font.pixelSize: 22; Layout.preferredWidth: 130 }
                    TextField
                    {
                        id: apiKeyInput
                        Layout.fillWidth: true
                        text: manager ? manager.apiKey : ""
                        echoMode: TextInput.Password
                        style: TextFieldStyle
                        {
                            textColor: cText
                            background: Rectangle { color: cBg; border.color: cBorder2; radius: 12 }
                        }
                    }
                }

                RowLayout
                {
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: "Base URL:"; color: cDim; font.pixelSize: 22; Layout.preferredWidth: 130 }
                    TextField
                    {
                        id: baseUrlInput
                        Layout.fillWidth: true
                        text: manager ? manager.apiBaseUrl : ""
                        style: TextFieldStyle
                        {
                            textColor: cText
                            background: Rectangle { color: cBg; border.color: cBorder2; radius: 12 }
                        }
                    }
                }

                RowLayout
                {
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: "Модель:"; color: cDim; font.pixelSize: 22; Layout.preferredWidth: 130 }
                    TextField
                    {
                        id: modelInput
                        Layout.fillWidth: true
                        text: manager ? manager.modelName : ""
                        style: TextFieldStyle
                        {
                            textColor: cText
                            background: Rectangle { color: cBg; border.color: cBorder2; radius: 12 }
                        }
                    }

                    Button
                    {
                        text: "Сохранить"
                        Layout.preferredWidth: 160
                        onClicked:
                        {
                            manager.saveSettings(apiKeyInput.text, baseUrlInput.text, modelInput.text)
                            chatWindow.showSettings = false
                        }
                        style: ButtonStyle
                        {
                            background: Rectangle { color: control.hovered ? "#96cf52" : cLime; radius: 12 }
                            label: Text { text: control.text; color: "#ffffff"; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 22; font.bold: true }
                        }
                    }
                }
            }
        }

        // ============ 3. QUICK ACTIONS ============
        Rectangle
        {
            Layout.fillWidth: true
            height: 72
            color: cCard

            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: cBorder }

            Flickable
            {
                anchors.fill: parent
                contentWidth: quickRow.width + 20
                contentHeight: parent.height
                boundsBehavior: Flickable.StopAtBounds
                clip: true

                Row
                {
                    id: quickRow
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    spacing: 10

                    Repeater
                    {
                        model: [
                            { "id": "check_settings", "label": "⚙ Настройки" },
                            { "id": "analyze_scene", "label": "🔍 Анализ сцены" },
                            { "id": "arrange", "label": "▦ Расставить" },
                            { "id": "slice", "label": "⚡ Слайсинг" },
                            { "id": "export", "label": "📦 Экспорт" }
                        ]

                        delegate: Button
                        {
                            height: 48
                            text: modelData.label
                            enabled: manager && !manager.isThinking
                            onClicked: manager.quickAction(modelData.id)

                            style: ButtonStyle
                            {
                                background: Rectangle
                                {
                                    radius: 24
                                    color: control.hovered ? cLimeSoft : cBg
                                    border.color: control.hovered ? cLime : cBorder2
                                    border.width: 1
                                }
                                label: Text
                                {
                                    text: control.text
                                    color: control.hovered ? cLimeDeep : cDim
                                    font.pixelSize: 22
                                    anchors.centerIn: parent
                                }
                            }
                        }
                    }
                }
            }
        }

        // ============ 4. SLICING PROGRESS BANNER ============
        Rectangle
        {
            Layout.fillWidth: true
            height: manager && manager.isSlicing ? 46 : 0
            visible: height > 0
            color: cLimeSoft
            clip: true

            Behavior on height { NumberAnimation { duration: 150 } }

            RowLayout
            {
                anchors.fill: parent
                anchors.leftMargin: 18
                anchors.rightMargin: 18
                spacing: 14

                Text
                {
                    text: "🧱 Нарезка…"
                    color: cLimeDeep
                    font.pixelSize: 22
                    font.bold: true
                }

                Rectangle
                {
                    Layout.fillWidth: true
                    height: 10; radius: 5
                    color: "#ffffff"

                    Rectangle
                    {
                        height: parent.height; radius: 5
                        color: cLime
                        width: parent.width * ((manager ? manager.slicingProgress : 0) / 100.0)

                        Behavior on width { NumberAnimation { duration: 300 } }
                    }
                }

                Text
                {
                    text: (manager ? Math.round(manager.slicingProgress) : 0) + "%"
                    color: cDim
                    font.pixelSize: 22
                }
            }
        }

        // ============ 5. MESSAGES ============
        Rectangle
        {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: cBg

            ScrollView
            {
                id: msgScroll
                anchors.fill: parent
                anchors.margins: 12

                ListView
                {
                    id: messageListView
                    width: msgScroll.width - 12
                    model: manager ? manager.messages : []
                    spacing: 12
                    clip: true

                    onCountChanged: Qt.callLater(messageListView.positionViewAtEnd)

                    delegate: Item
                    {
                        width: messageListView.width
                        height: bubbleCol.implicitHeight

                        Column
                        {
                            id: bubbleCol
                            width: parent.width
                            spacing: 4

                            Rectangle
                            {
                                anchors.right: modelData.role === "user" ? parent.right : undefined
                                anchors.left: modelData.role !== "user" ? parent.left : undefined
                                width: Math.min(contentCol.implicitWidth + 26, messageListView.width * 0.9)
                                height: contentCol.implicitHeight + 18
                                radius: 18

                                // tail corners: assistant — top-left, user — top-right
                                Rectangle
                                {
                                    visible: modelData.role !== "user"
                                    anchors.top: parent.top; anchors.left: parent.left
                                    width: 18; height: 18; color: parent.color
                                }
                                Rectangle
                                {
                                    visible: modelData.role === "user"
                                    anchors.top: parent.top; anchors.right: parent.right
                                    width: 18; height: 18; color: parent.color
                                }

                                color: {
                                    if (modelData.role === "user") return cLime
                                    if (modelData.role === "tool") return cCard
                                    if (modelData.role === "error") return "#fdf1f1"
                                    return cCard
                                }

                                border.color: {
                                    if (modelData.role === "tool") return cBorder
                                    if (modelData.role === "error") return cDanger
                                    return cBorder
                                }
                                border.width: 1

                                Column
                                {
                                    id: contentCol
                                    anchors.centerIn: parent
                                    width: parent.width - 22
                                    spacing: 5

                                    Row
                                    {
                                        spacing: 7
                                        visible: modelData.role !== "user"

                                        Text
                                        {
                                            text: {
                                                if (modelData.role === "tool") return "🔧 " + (modelData.toolName || "инструмент")
                                                if (modelData.role === "error") return "⚠ Ошибка"
                                                return "🦎 Паскаль"
                                            }
                                            color: {
                                                if (modelData.role === "tool") return cTeal
                                                if (modelData.role === "error") return cDanger
                                                return cLimeDeep
                                            }
                                            font.pixelSize: 20
                                            font.bold: true
                                        }
                                    }

                                    TextEdit
                                    {
                                        width: parent.width
                                        text: chatWindow.stripMd(modelData.content || "")
                                        color: modelData.role === "user" ? "#ffffff" : cText
                                        font.pixelSize: 24
                                        wrapMode: TextEdit.Wrap
                                        readOnly: true
                                        selectByMouse: true
                                    }

                                    Rectangle
                                    {
                                        width: parent.width
                                        height: toolResultText.implicitHeight + 10
                                        visible: modelData.role === "tool" && modelData.toolResult && modelData.toolResult.length > 0
                                        color: cBg
                                        radius: 8

                                        TextEdit
                                        {
                                            id: toolResultText
                                            anchors.centerIn: parent
                                            width: parent.width - 10
                                            text: "→ " + (modelData.toolResult || "")
                                            color: cDim
                                            font.pixelSize: 20
                                            font.family: "Consolas"
                                            wrapMode: TextEdit.Wrap
                                            readOnly: true
                                            selectByMouse: true
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ============ 6. ATTACHED FILES ============
        Rectangle
        {
            Layout.fillWidth: true
            height: manager && manager.attachedFiles.length > 0 ? 48 : 0
            visible: height > 0
            color: cCard
            clip: true

            Row
            {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: 16
                spacing: 10

                Repeater
                {
                    model: manager ? manager.attachedFiles : []

                    delegate: Rectangle
                    {
                        height: 40
                        width: fileRow.implicitWidth + 20
                        radius: 20
                        color: cLimeSoft
                        border.color: cLime
                        border.width: 1

                        Row
                        {
                            id: fileRow
                            anchors.centerIn: parent
                            spacing: 7

                            Text { text: "📎 " + modelData; color: cText; font.pixelSize: 20 }

                            MouseArea
                            {
                                width: 20; height: 20
                                onClicked: manager.removeAttachment(index)
                                Text { anchors.centerIn: parent; text: "×"; color: cDanger; font.pixelSize: 24; font.bold: true }
                            }
                        }
                    }
                }
            }
        }

        // ============ 7. BOTTOM: PASCAL SPEAKS + INPUT ============
        Rectangle
        {
            Layout.fillWidth: true
            Layout.fillHeight: false
            implicitHeight: pascalColumn.implicitHeight + 24
            color: cCard

            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: cBorder }

            RowLayout
            {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 12

                // Pascal: mascot + speech bubble (bottom-left)
                ColumnLayout
                {
                    id: pascalColumn
                    spacing: 8
                    Layout.preferredWidth: 420
                    Layout.maximumWidth: 460
                    Layout.alignment: Qt.AlignBottom

                    Rectangle
                    {
                        id: pascalSpeechBubble
                        Layout.fillWidth: true
                        implicitHeight: speechText.implicitHeight + 34
                        color: cCard
                        border.color: cLime
                        border.width: 2
                        radius: 16
                        visible: manager && manager.pascalSpeech.length > 0

                        // speech tail pointing down-left to Pascal
                        Canvas
                        {
                            id: tail
                            width: 26; height: 18
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: -16
                            anchors.left: parent.left
                            anchors.leftMargin: 28

                            onPaint:
                            {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                ctx.fillStyle = "#ffffff"
                                ctx.strokeStyle = chatWindow.cLime
                                ctx.lineWidth = 2
                                ctx.beginPath()
                                ctx.moveTo(1, 0)
                                ctx.lineTo(width - 8, 0)
                                ctx.lineTo(6, height - 2)
                                ctx.closePath()
                                ctx.fill()
                                ctx.stroke()
                            }
                        }

                        Column
                        {
                            anchors.centerIn: parent
                            width: parent.width - 24
                            spacing: 2

                            Text
                            {
                                text: "🦎 Паскаль"
                                color: cLimeDeep
                                font.pixelSize: 20
                                font.bold: true
                            }

                            TextEdit
                            {
                                id: speechText
                                width: parent.width
                                text: manager ? chatWindow.stripMd(manager.pascalSpeech) : ""
                                color: cText
                                font.pixelSize: 22
                                wrapMode: TextEdit.Wrap
                                readOnly: true
                                selectByMouse: true
                            }
                        }
                    }

                    AnimatedImage
                    {
                        id: pascalGif
                        Layout.alignment: Qt.AlignLeft
                        width: 132; height: 176
                        fillMode: Image.PreserveAspectCrop
                        playing: true
                        cache: true
                        source: {
                            if (manager && manager.isHappy) return "../mascot/mascot_happy_light.gif"
                            if (manager && manager.isThinking) return "../mascot/mascot_think_light.gif"
                            return "../mascot/mascot_idle_light.gif"
                        }

                        MouseArea
                        {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: pascalBounce.start()
                        }

                        SequentialAnimation
                        {
                            id: pascalBounce
                            NumberAnimation { target: pascalGif; property: "scale"; to: 1.12; duration: 140 }
                            NumberAnimation { target: pascalGif; property: "scale"; to: 1.0; duration: 140 }
                        }
                    }
                }

                // input column
                ColumnLayout
                {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 8

                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 10

                        Button
                        {
                            width: 64; height: 64
                            tooltip: "Прикрепить 3D-модель (STL/OBJ/3MF) или изображение"
                            onClicked: fileDialog.open()

                            style: ButtonStyle
                            {
                                background: Rectangle
                                {
                                    radius: 16
                                    color: control.hovered ? cLimeSoft : cBg
                                    border.color: control.hovered ? cLime : cBorder2
                                    border.width: 2
                                    Text { anchors.centerIn: parent; text: "📎"; font.pixelSize: 30; color: control.hovered ? cLimeDeep : cDim }
                                }
                            }
                        }

                        TextArea
                        {
                            id: chatInput
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 90
                            wrapMode: TextEdit.Wrap
                            textColor: cText
                            enabled: manager ? !manager.isThinking : true

                            style: TextAreaStyle
                            {
                                backgroundColor: cBg
                                frame: Rectangle
                                {
                                    radius: 16
                                    border.width: 2
                                    border.color: chatInput.activeFocus ? cLime : cBorder2
                                }
                            }

                            Keys.onReturnPressed:
                            {
                                if (chatInput.text.trim().length > 0 || (manager && manager.attachedFiles.length > 0))
                                {
                                    manager.sendMessage(chatInput.text)
                                    chatInput.text = ""
                                }
                            }
                        }

                        Button
                        {
                            width: 180; height: 64
                            text: manager && manager.isThinking ? "Думает…" : "Отправить"
                            enabled: manager && !manager.isThinking && (chatInput.text.trim().length > 0 || manager.attachedFiles.length > 0)

                            onClicked:
                            {
                                manager.sendMessage(chatInput.text)
                                chatInput.text = ""
                            }

                            style: ButtonStyle
                            {
                                background: Rectangle
                                {
                                    radius: 16
                                    color: {
                                        if (!control.enabled) return "#e9e7df"
                                        if (control.hovered) return "#96cf52"
                                        return cLime
                                    }
                                }
                                label: Text { text: control.text; color: control.enabled ? "#ffffff" : "#a8ab9e"; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 24; font.bold: true }
                            }
                        }
                    }

                    Text
                    {
                        Layout.fillWidth: true
                        text: "Enter — отправить · Паскаль сам загрузит модель, выберет режим 3D/5D и прослайсит"
                        color: cDim
                        font.pixelSize: 18
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }
    }
}
