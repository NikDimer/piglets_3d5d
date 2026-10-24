// Kali overlay — lives INSIDE the main STE Slicer window (bottom-right).
// Click the chameleon -> chat panel expands above her. No separate window.
// Light messenger theme, gradient assistant bubbles. QtQuick.Controls 1.4.
import QtQuick 2.2
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4
import QtQuick.Layouts 1.1
import QtQuick.Dialogs 1.2

Item
{
    id: overlay

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
    property color cDanger: "#e05d5d"

    // geometry: panel size 2x of the comfortable chat
    property int panelW: Math.min(760, overlay.parent ? overlay.parent.width * 0.42 : 760)
    property int panelH: overlay.parent ? Math.max(420, Math.min(1100, overlay.parent.height - 440)) : 900

    width: kaliCard.width
    height: panel.open ? panelH + 16 + kaliCard.height : kaliCard.height

    // parent (main window contentItem) is set right after creation; guard bindings
    anchors.right: parent ? parent.right : undefined
    anchors.rightMargin: 48      // чуть правее, почти у тулбара
    anchors.bottom: parent ? parent.bottom : undefined
    anchors.bottomMargin: 120

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

    // ================= CHAT PANEL (above Kali) =================
    Rectangle
    {
        id: panel
        property bool open: manager ? manager.chatWindowVisible : false

        width: panelW
        height: panel.open ? panelH : 0
        anchors.right: parent.right
        anchors.bottom: kaliCard.top
        anchors.bottomMargin: 16
        radius: 22
        color: cBg
        border.color: cBorder2
        border.width: 1
        clip: true
        visible: height > 4
        z: 5

        Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

        ColumnLayout
        {
            anchors.fill: parent
            anchors.margins: 2
            spacing: 0
            visible: panel.open

            // ---- header ----
            Rectangle
            {
                Layout.fillWidth: true
                implicitHeight: 96
                radius: 20
                color: cCard

                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 22; color: cCard }
                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: cBorder }

                RowLayout
                {
                    anchors.fill: parent
                    anchors.leftMargin: 18
                    anchors.rightMargin: 12
                    spacing: 12

                    ColumnLayout
                    {
                        spacing: 2
                        Text { text: "Кали · 3D/5D Copilot"; color: cText; font.pixelSize: 36; font.bold: true }
                        Row
                        {
                            spacing: 8
                            Rectangle
                            {
                                width: 14; height: 14; radius: 7; anchors.verticalCenter: parent.verticalCenter
                                color: {
                                    if (!manager) return cDim
                                    if (manager.statusType === "ready") return cLimeDeep
                                    if (manager.statusType === "error") return cDanger
                                    if (manager.statusType === "processing") return "#e8a13c"
                                    return cLime
                                }
                            }
                            Text { text: manager ? manager.statusText : "…"; color: cDim; font.pixelSize: 27 }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    Button
                    {
                        text: "В браузере"
                        tooltip: "Открыть полную веб-версию чата"
                        onClicked: if (manager) manager.openWebChat()
                        style: ButtonStyle
                        {
                            padding { left: 14; right: 14; top: 10; bottom: 10 }
                            background: Rectangle { implicitHeight: 58; radius: 12; color: control.hovered ? cLimeSoft : "#fbfaf6"; border.width: 1; border.color: control.hovered ? cLime : cBorder }
                            label: Text { text: control.text; color: control.hovered ? cLimeDeep : cText; font.pixelSize: 27; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                        }
                    }

                    Button
                    {
                        text: "Очистить"
                        tooltip: "Стереть историю диалога"
                        onClicked: if (manager) manager.clearChat()
                        style: ButtonStyle
                        {
                            padding { left: 14; right: 14; top: 10; bottom: 10 }
                            background: Rectangle { implicitHeight: 58; radius: 12; color: control.hovered ? cLimeSoft : "#fbfaf6"; border.width: 1; border.color: control.hovered ? cLime : cBorder }
                            label: Text { text: control.text; color: control.hovered ? cLimeDeep : cText; font.pixelSize: 27; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                        }
                    }

                    Button
                    {
                        text: "Настройки"
                        tooltip: "API-ключ, адрес и модель нейросети"
                        onClicked: settingsRect.showSettings = !settingsRect.showSettings
                        style: ButtonStyle
                        {
                            padding { left: 14; right: 14; top: 10; bottom: 10 }
                            background: Rectangle { implicitHeight: 58; radius: 12; color: settingsRect.showSettings || control.hovered ? cLimeSoft : "#fbfaf6"; border.width: 1; border.color: settingsRect.showSettings || control.hovered ? cLime : cBorder }
                            label: Text { text: control.text; color: settingsRect.showSettings || control.hovered ? cLimeDeep : cText; font.pixelSize: 27; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                        }
                    }

                    Button
                    {
                        text: "✕"
                        tooltip: "Свернуть чат (Кали останется на месте)"
                        onClicked: if (manager) manager.hideChatWindow()
                        style: ButtonStyle
                        {
                            padding { left: 14; right: 14; top: 10; bottom: 10 }
                            background: Rectangle { implicitHeight: 58; radius: 12; color: control.hovered ? "#fbe7e7" : "#fbfaf6"; border.width: 1; border.color: control.hovered ? cDanger : cBorder }
                            label: Text { text: control.text; color: control.hovered ? cDanger : cDim; font.pixelSize: 33; font.bold: true; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                        }
                    }
                }
            }

            // ---- settings (collapsible) ----
            Rectangle
            {
                id: settingsRect
                Layout.fillWidth: true
                implicitHeight: settingsRect.showSettings ? settingsCol.implicitHeight + 24 : 0
                visible: implicitHeight > 0
                clip: true
                color: cCard
                Behavior on implicitHeight { NumberAnimation { duration: 180 } }

                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: cBorder }

                property bool showSettings: false

                ColumnLayout
                {
                    id: settingsCol
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    visible: parent.showSettings

                    Text { text: "Параметры OpenAI-совместимого API"; color: cLimeDeep; font.pixelSize: 27; font.bold: true }

                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "API Key:"; color: cDim; font.pixelSize: 24; Layout.preferredWidth: 120 }
                        TextField
                        {
                            id: apiKeyInput
                            Layout.fillWidth: true
                            text: manager ? manager.apiKey : ""
                            echoMode: TextInput.Password
                            style: TextFieldStyle { textColor: cText; background: Rectangle { color: cBg; border.color: cBorder2; radius: 8 } }
                        }
                    }
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "Base URL:"; color: cDim; font.pixelSize: 24; Layout.preferredWidth: 120 }
                        TextField
                        {
                            id: baseUrlInput
                            Layout.fillWidth: true
                            text: manager ? manager.apiBaseUrl : ""
                            style: TextFieldStyle { textColor: cText; background: Rectangle { color: cBg; border.color: cBorder2; radius: 8 } }
                        }
                    }
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "Модель:"; color: cDim; font.pixelSize: 24; Layout.preferredWidth: 120 }
                        TextField
                        {
                            id: modelInput
                            Layout.fillWidth: true
                            text: manager ? manager.modelName : ""
                            style: TextFieldStyle { textColor: cText; background: Rectangle { color: cBg; border.color: cBorder2; radius: 8 } }
                        }
                        Button
                        {
                            text: "Сохранить"
                            onClicked:
                            {
                                manager.saveSettings(apiKeyInput.text, baseUrlInput.text, modelInput.text)
                                settingsRect.showSettings = false
                            }
                            style: ButtonStyle
                            {
                                padding { left: 16; right: 16; top: 10; bottom: 10 }
                                background: Rectangle { implicitHeight: 50; color: control.hovered ? "#96cf52" : cLime; radius: 10 }
                                label: Text { text: control.text; color: "#ffffff"; font.pixelSize: 24; font.bold: true; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                            }
                        }
                    }
                }
            }

            // ---- slicing progress ----
            Rectangle
            {
                Layout.fillWidth: true
                implicitHeight: manager && manager.isSlicing ? 44 : 0
                visible: implicitHeight > 0
                color: cLimeSoft
                clip: true
                Behavior on implicitHeight { NumberAnimation { duration: 150 } }

                RowLayout
                {
                    anchors.fill: parent
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    spacing: 12
                    Text { text: "🧱 Нарезка…"; color: cLimeDeep; font.pixelSize: 24; font.bold: true }
                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: 10; radius: 5; color: "#ffffff"
                        Rectangle
                        {
                            height: parent.height; radius: 4; color: cLime
                            width: parent.width * ((manager ? manager.slicingProgress : 0) / 100.0)
                            Behavior on width { NumberAnimation { duration: 300 } }
                        }
                    }
                    Text { text: (manager ? Math.round(manager.slicingProgress) : 0) + "%"; color: cDim; font.pixelSize: 24 }
                }
            }

            // ---- messages ----
            Rectangle
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: cBg

                ScrollView
                {
                    id: msgScroll
                    anchors.fill: parent
                    anchors.margins: 10

                    ListView
                    {
                        id: messageListView
                        width: msgScroll.width - 8
                        model: manager ? manager.messages : []
                        spacing: 10
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
                                    width: Math.min(contentCol.implicitWidth + 24, messageListView.width * 0.92)
                                    height: contentCol.implicitHeight + 16
                                    radius: 16

                                    Rectangle
                                    {
                                        visible: modelData.role === "assistant"
                                        anchors.top: parent.top; anchors.left: parent.left
                                        width: 16; height: 16; color: "#65b04b"
                                    }
                                    Rectangle
                                    {
                                        visible: modelData.role === "user"
                                        anchors.top: parent.top; anchors.right: parent.right
                                        width: 16; height: 16; color: cCard
                                    }

                                    gradient: modelData.role === "assistant" ? bubbleGradient : null
                                    color: {
                                        if (modelData.role === "user") return cCard
                                        if (modelData.role === "tool") return cCard
                                        if (modelData.role === "error") return "#fdf1f1"
                                        return "#ffffff"
                                    }
                                    border.color: {
                                        if (modelData.role === "tool") return cBorder
                                        if (modelData.role === "error") return cDanger
                                        return cBorder
                                    }
                                    border.width: modelData.role === "assistant" ? 0 : 1

                                    Gradient
                                    {
                                        id: bubbleGradient
                                        GradientStop { position: 0.0; color: "#65b04b" }
                                        GradientStop { position: 0.52; color: "#2e9e8f" }
                                        GradientStop { position: 1.0; color: "#1f7fa8" }
                                    }

                                    Column
                                    {
                                        id: contentCol
                                        anchors.centerIn: parent
                                        width: parent.width - 22
                                        spacing: 4

                                        Row
                                        {
                                            spacing: 6
                                            visible: modelData.role !== "user"
                                            Text
                                            {
                                                text: {
                                                    if (modelData.role === "tool") return "🔧 " + (modelData.toolName || "инструмент")
                                                    if (modelData.role === "error") return "⚠ Ошибка"
                                                    return "🦎 Кали"
                                                }
                                                color: {
                                                    if (modelData.role === "tool") return cTeal
                                                    if (modelData.role === "error") return cDanger
                                                    return "#ffffff"
                                                }
                                                font.pixelSize: 21
                                                font.bold: true
                                            }

                                            Button
                                            {
                                                visible: modelData.role === "assistant"
                                                tooltip: "Скопировать ответ в буфер"
                                                onClicked:
                                                {
                                                    if (manager) manager.copyToClipboard(modelData.content || "")
                                                    copyLbl.text = "✓"
                                                    copyReset.restart()
                                                }
                                                style: ButtonStyle
                                                {
                                                    padding { left: 10; right: 10; top: 4; bottom: 4 }
                                                    background: Rectangle { implicitHeight: 32; radius: 8; color: control.hovered ? "rgba(255,255,255,0.30)" : "rgba(255,255,255,0.16)"; border.width: 1; border.color: control.hovered ? "#ffffff" : "rgba(255,255,255,0.45)" }
                                                    label: Text { id: copyLbl; text: "⧉"; color: "#ffffff"; font.pixelSize: 24; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                                                }
                                                Timer { id: copyReset; interval: 1200; onTriggered: copyLbl.text = "⧉" }
                                            }
                                        }

                                        TextEdit
                                        {
                                            width: parent.width
                                            text: overlay.stripMd(modelData.content || "")
                                            color: modelData.role === "assistant" ? "#ffffff" : cText
                                            font.pixelSize: 24
                                            wrapMode: TextEdit.Wrap
                                            readOnly: true
                                            selectByMouse: true
                                        }

                                        Rectangle
                                        {
                                            width: parent.width
                                            height: toolResultText.implicitHeight + 8
                                            visible: modelData.role === "tool" && modelData.toolResult && modelData.toolResult.length > 0
                                            color: cBg
                                            radius: 6
                                            TextEdit
                                            {
                                                id: toolResultText
                                                anchors.centerIn: parent
                                                width: parent.width - 8
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

            // ---- attached files ----
            Rectangle
            {
                Layout.fillWidth: true
                implicitHeight: manager && manager.attachedFiles.length > 0 ? 44 : 0
                visible: implicitHeight > 0
                color: cCard
                clip: true

                Row
                {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    spacing: 8
                    Repeater
                    {
                        model: manager ? manager.attachedFiles : []
                        delegate: Rectangle
                        {
                            height: 36
                            width: fileRow.implicitWidth + 18
                            radius: 16
                            color: cLimeSoft
                            border.color: cLime
                            border.width: 1
                            Row
                            {
                                id: fileRow
                                anchors.centerIn: parent
                                spacing: 6
                                Text { text: "📎 " + modelData; color: cText; font.pixelSize: 21 }
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

            // ---- composer ----
            Rectangle
            {
                Layout.fillWidth: true
                implicitHeight: 112
                color: cCard
                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: 1; color: cBorder }

                RowLayout
                {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8

                    Button
                    {
                        text: "📎 Файл"
                        tooltip: "3D-модель (STL/OBJ/3MF) или изображение"
                        onClicked: fileDialog.open()
                        style: ButtonStyle
                        {
                            padding { left: 14; right: 14; top: 10; bottom: 10 }
                            background: Rectangle { implicitHeight: 58; radius: 12; color: control.hovered ? cLimeSoft : cBg; border.width: 1; border.color: control.hovered ? cLime : cBorder2 }
                            label: Text { text: control.text; color: control.hovered ? cLimeDeep : cText; font.pixelSize: 24; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                        }
                    }

                    // fixed-height input: never collapses
                    TextArea
                    {
                        id: chatInput
                        Layout.fillWidth: true
                        Layout.preferredHeight: 78
                        wrapMode: TextEdit.Wrap
                        textColor: cText
                        enabled: manager ? !manager.isThinking : true

                        style: TextAreaStyle
                        {
                            backgroundColor: cBg
                            frame: Rectangle
                            {
                                radius: 12
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
                        text: manager && manager.isThinking ? "…" : "Отправить"
                        enabled: manager && !manager.isThinking && (chatInput.text.trim().length > 0 || manager.attachedFiles.length > 0)
                        onClicked:
                        {
                            manager.sendMessage(chatInput.text)
                            chatInput.text = ""
                        }
                        style: ButtonStyle
                        {
                            padding { left: 16; right: 16; top: 10; bottom: 10 }
                            background: Rectangle
                            {
                                implicitHeight: 58
                                radius: 12
                                color: {
                                    if (!control.enabled) return "#e9e7df"
                                    if (control.hovered) return "#96cf52"
                                    return cLime
                                }
                            }
                            label: Text { text: control.text; color: control.enabled ? "#ffffff" : "#a8ab9e"; font.pixelSize: 26; font.bold: true; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
                        }
                    }
                }
            }
        }
    }

    // ================= KALI BUTTON (bottom-right, always visible) =================
    Rectangle
    {
        id: kaliCard
        width: 216
        height: 280
        radius: 28
        color: "#ffffff"
        border.color: manager && manager.isThinking ? cLime : (manager && manager.isHappy ? cTeal : cBorder2)
        border.width: 3
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        z: 6

        // unread pulse when the panel is closed and Kali has something to say
        SequentialAnimation on scale
        {
            running: manager && !manager.chatWindowVisible && (manager.isThinking || manager.isHappy)
            loops: Animation.Infinite
            NumberAnimation { to: 1.08; duration: 450 }
            NumberAnimation { to: 1.0; duration: 450 }
        }

        AnimatedImage
        {
            id: kaliGif
            anchors.centerIn: parent
            width: 192; height: 256
            fillMode: Image.PreserveAspectCrop
            playing: true
            cache: true
            source: {
                if (manager && manager.isHappy) return "../mascot/mascot_happy_light.gif"
                if (manager && manager.isThinking) return "../mascot/mascot_think_light.gif"
                return "../mascot/mascot_idle_light.gif"
            }
        }

        MouseArea
        {
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked:
            {
                if (manager)
                {
                    if (manager.chatWindowVisible) manager.hideChatWindow()
                    else manager.showChatWindow()
                }
            }
        }

        // small badge with speech preview
        Rectangle
        {
            visible: manager && !manager.chatWindowVisible && manager.kaliSpeech.length > 0
            anchors.top: parent.top
            anchors.topMargin: -16
            anchors.right: parent.right
            anchors.rightMargin: -6
            width: Math.min(speechBadgeText.implicitWidth + 28, 560)
            height: 34
            radius: 17
            color: cCard
            border.color: cLime
            border.width: 2

            Text
            {
                id: speechBadgeText
                anchors.centerIn: parent
                text: {
                    var s = manager ? overlay.stripMd(manager.kaliSpeech) : ""
                    return s.length > 24 ? s.substring(0, 24) + "…" : s
                }
                color: cText
                font.pixelSize: 20
            }
        }
    }
}
