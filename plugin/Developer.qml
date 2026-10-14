pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import "ListSync.js" as ListSync

Scope {
    id: root
    property bool opened: true
    property string mode: Quickshell.env("GDW_MODE") === "services" ? "services" : "projects"
    property string filter: "all"
    property string selectedKey: ""
    property bool syncingList: false
    property var pending: null
    property string notice: ""
    property bool failed: false
    property var snapshot: ({
            projects: [],
            services: [],
            agents: [],
            dockerAvailable: false,
            docker: {
                context: "unresolved",
                source: "",
                target: [],
                message: "",
                status: "unavailable"
            },
            configError: "",
            listenerError: "",
            processControls: true,
            updated: ""
        })
    readonly property string backend: decodeURIComponent(Qt.resolvedUrl("backend.py").toString().substring(7))
    SystemPalette {
        id: desktopPalette
        colorGroup: SystemPalette.Active
    }
    readonly property color fg: desktopPalette.text
    readonly property color bg: desktopPalette.window
    readonly property bool dark: (bg.r + bg.g + bg.b) < 1.5
    readonly property color errorColor: dark ? "#ffb4b4" : "#a81824"
    readonly property color successColor: dark ? "#a1d5ac" : "#246b38"
    readonly property color surface: Qt.rgba(fg.r, fg.g, fg.b, 0.045)
    readonly property color accent: dark ? "#91c8f5" : "#245b9a"
    readonly property var rows: {
        var all = mode === "projects" ? snapshot.projects : snapshot.services;
        var q = search.text.toLowerCase();
        return all.filter(function (r) {
            return (r.name + " " + r.detail + " " + (r.image || "")).toLowerCase().indexOf(q) !== -1 && (mode === "projects" || filter === "all" || (filter === "containers" && r.kind === "container") || (filter === "ports" && r.kind === "port") || (filter === "stopped" && r.kind === "container" && r.state !== "running"));
        });
    }
    readonly property var selected: {
        for (var i = 0; i < rows.length; i++)
            if (key(rows[i]) === selectedKey)
                return rows[i];
        return rows.length ? rows[0] : null;
    }
    function key(r) {
        return ListSync.rowKey(r);
    }
    function syncRows(preserveScroll) {
        if (!list)
            return;
        var savedKey = root.selectedKey;
        var savedIndex = list.currentIndex;
        var savedScroll = list.contentY;
        root.syncingList = true;
        try {
            var index = ListSync.sync(listRows, root.rows, savedKey, savedIndex);
            list.currentIndex = index;
            root.selectedKey = index >= 0 ? ListSync.rowKey(root.rows[index]) : "";
            if (preserveScroll === false)
                list.positionViewAtIndex(index, ListView.Contain);
            else
                list.contentY = savedScroll;
        } finally {
            root.syncingList = false;
        }
    }
    onRowsChanged: syncRows()
    Component.onCompleted: {
        syncRows();
        refresh();
        Qt.callLater(function () {
            search.forceActiveFocus();
        });
    }
    ListModel {
        id: listRows
        dynamicRoles: true
    }
    function chooseMode(next) {
        mode = next;
        filter = "all";
        selectedKey = "";
        search.text = "";
        notice = "";
        Qt.callLater(function () {
            root.syncRows(false);
        });
    }
    function open(nextMode) {
        if (!pending && mode !== nextMode)
            chooseMode(nextMode === "services" ? "services" : "projects");
        root.opened = true;
        panel.visible = true;
        panel.minimized = false;
        refresh();
        Qt.callLater(function () {
            panel.contentItem.Window.window.requestActivate();
            if (!pending)
                search.forceActiveFocus();
        });
    }
    // KWin can send an initial configure smaller than the minimum-size hint.
    // Clamp the backing Qt window after that configure; preserve every larger user size.
    function ensureMinimumSize() {
        var window = panel.contentItem.Window.window;
        if (!window || !panel.visible || panel.maximized || panel.fullscreen)
            return;
        if (window.width < panel.minimumSize.width)
            window.width = panel.minimumSize.width;
        if (window.height < panel.minimumSize.height)
            window.height = panel.minimumSize.height;
    }
    function dismiss() {
        root.opened = false;
        panel.visible = false;
        pending = null;
    }
    function status() {
        return JSON.stringify({
            opened: opened,
            visible: panel.visible,
            minimized: panel.minimized,
            mode: mode,
            rows: rows.length,
            selectedKey: selectedKey,
            currentIndex: list.currentIndex,
            listFocused: list.activeFocus,
            polling: poll.running,
            scanning: scan.running,
            acting: actionProcess.running,
            pending: !!pending
        });
    }
    function refresh() {
        if (root.opened && panel.visible && !panel.minimized && !scan.running && !pending && !actionProcess.running)
            scan.running = true;
    }
    function act(kind, value) {
        if (actionProcess.running || scan.running || pending || snapshot.configError)
            return;
        if (kind === "copy") {
            if (!panel.contentItem.Window.active) {
                notice = "Focus the workspace window, then copy again.";
                failed = true;
                return;
            }
            Quickshell.clipboardText = value;
            notice = "Copied to clipboard.";
            failed = false;
            return;
        }
        notice = "Working…";
        failed = false;
        actionProcess.command = ["python3", backend, "action", kind, value];
        if (["start", "stop", "restart", "remove", "inspect", "logs"].indexOf(kind) !== -1)
            actionProcess.command.push(JSON.stringify(snapshot.docker.target));
        actionProcess.running = true;
    }
    IpcHandler {
        target: "workspace"
        function open(mode: string): bool {
            root.open(mode);
            return true;
        }
        function hide(): void {
            root.dismiss();
        }
        function status(): string {
            return root.status();
        }
        function quit(): void {
            Qt.quit();
        }
    }
    function ask(kind, label, description) {
        if (actionProcess.running || scan.running || !selected || snapshot.configError)
            return;
        var r = selected;
        var value = r.kind === "container" ? r.id : JSON.stringify({
            pid: r.pid,
            identity: r.identity
        });
        pending = {
            kind: kind,
            label: label,
            name: r.name,
            description: description,
            value: value
        };
    }
    component DevButton: Button {
        id: control
        property bool danger: false
        implicitHeight: 36
        implicitWidth: Math.max(44, caption.implicitWidth + 24)
        opacity: enabled ? 1 : 0.4
        contentItem: Text {
            id: caption
            text: control.text
            color: control.danger ? root.errorColor : root.fg
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 8
            color: control.down ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.3) : control.highlighted ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.2) : control.hovered ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12) : root.surface
            border.width: 1
            border.color: control.activeFocus ? root.accent : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08)
        }
    }
    component Detail: ColumnLayout {
        id: detail
        property string title: ""
        property string value: ""
        spacing: 5
        Layout.fillWidth: true
        Label {
            text: detail.title.toUpperCase()
            color: root.fg
            opacity: 0.7
            font.pixelSize: 10
            font.letterSpacing: 1.2
        }
        Label {
            text: detail.value
            color: root.fg
            font.pixelSize: 13
            wrapMode: Text.WrapAnywhere
            Layout.fillWidth: true
            textFormat: Text.PlainText
        }
    }
    Process {
        id: scan
        command: ["python3", root.backend]
        stdout: StdioCollector {
            id: scanOutput
            onStreamFinished: {
                try {
                    var hadConfigError = !!root.snapshot.configError;
                    root.snapshot = JSON.parse(scanOutput.text);
                    if (root.snapshot.configError) {
                        root.notice = root.snapshot.configError;
                        root.failed = true;
                    } else if (hadConfigError) {
                        root.notice = "Configuration loaded.";
                        root.failed = false;
                    }
                } catch (e) {
                    root.notice = "Refresh failed. Try again.";
                    root.failed = true;
                }
            }
        }
    }
    Process {
        id: actionProcess
        stdout: StdioCollector {
            id: actionOutput
            onStreamFinished: {
                try {
                    var result = JSON.parse(actionOutput.text);
                    root.notice = result.message;
                    root.failed = !result.ok;
                } catch (e) {
                    root.notice = "Action failed. Refresh and try again.";
                    root.failed = true;
                }
            }
        }
        onExited: function (code, status) {
            if (code !== 0)
                root.failed = true;
            Qt.callLater(root.refresh);
        }
    }
    Timer {
        id: poll
        interval: 5000
        repeat: true
        running: root.opened && panel.visible && !panel.minimized && root.mode === "services" && !root.pending && !scan.running && !actionProcess.running
        onTriggered: root.refresh()
    }
    FloatingWindow {
        id: panel
        visible: root.opened
        title: "Garuda Developer Workspace"
        implicitWidth: 1060
        implicitHeight: 800
        minimumSize: Qt.size(840, 620)
        color: root.bg
        onWindowConnected: Qt.callLater(root.ensureMinimumSize)
        onWidthChanged: Qt.callLater(root.ensureMinimumSize)
        onHeightChanged: Qt.callLater(root.ensureMinimumSize)
        onClosed: root.dismiss()
        onMinimizedChanged: {
            if (!minimized)
                root.refresh();
        }
        Shortcut {
            sequence: "Escape"
            onActivated: {
                if (root.pending)
                    root.pending = null;
                else
                    root.dismiss();
            }
        }
        Rectangle {
            id: card
            anchors.fill: parent
            radius: 18
            color: root.bg
            border.width: 1
            border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.18)
            MouseArea {
                anchors.fill: parent
            }
            ColumnLayout {
                enabled: !root.pending
                anchors.fill: parent
                anchors.margins: 24
                spacing: 16
                RowLayout {
                    ColumnLayout {
                        spacing: 4
                        Label {
                            text: "Garuda Developer Workspace"
                            color: root.fg
                            font.pixelSize: 22
                            font.bold: true
                        }
                        Label {
                            text: root.mode === "projects" ? "Pick a project. Open your tools. Get to work." : "Your local stack, with the controls you need."
                            color: root.fg
                            opacity: 0.75
                            font.pixelSize: 12
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Label {
                        text: scan.running ? "Refreshing…" : "Updated " + root.snapshot.updated
                        color: root.fg
                        opacity: 0.7
                        font.pixelSize: 11
                    }
                    DevButton {
                        text: "↻ Refresh"
                        enabled: !scan.running && !actionProcess.running
                        onClicked: root.refresh()
                    }
                    DevButton {
                        text: "×"
                        onClicked: root.dismiss()
                    }
                }
                RowLayout {
                    DevButton {
                        text: "Projects  " + root.snapshot.projects.length
                        highlighted: root.mode === "projects"
                        onClicked: root.chooseMode("projects")
                    }
                    DevButton {
                        text: "Services  " + root.snapshot.services.length
                        highlighted: root.mode === "services"
                        onClicked: root.chooseMode("services")
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: root.mode === "services"
                        text: root.snapshot.services.filter(function (r) {
                            return r.kind === "container" && r.state === "running";
                        }).length + " running containers"
                        color: root.successColor
                        font.pixelSize: 12
                    }
                }
                Label {
                    visible: root.mode === "services"
                    text: "Docker: " + root.snapshot.docker.context + " (" + root.snapshot.docker.source + ") · " + root.snapshot.docker.status + (root.snapshot.docker.message ? " · " + root.snapshot.docker.message : "")
                    color: root.snapshot.dockerAvailable ? root.fg : root.errorColor
                    Layout.fillWidth: true
                    wrapMode: Text.WrapAnywhere
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                }
                Label {
                    visible: root.mode === "services"
                    text: "Docker Desktop containers and host TCP processes are separate views. URLs are inferred; no health check is performed." + (!root.snapshot.processControls ? " Process controls disabled: pidfd APIs unavailable." : "") + (root.snapshot.listenerError ? " Listener discovery: " + root.snapshot.listenerError : "")
                    color: root.fg
                    opacity: 0.75
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    font.pixelSize: 11
                    textFormat: Text.PlainText
                }
                TextField {
                    id: search
                    placeholderTextColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.65)
                    Layout.fillWidth: true
                    implicitHeight: 42
                    leftPadding: 14
                    color: root.fg
                    selectByMouse: true
                    font.pixelSize: 14
                    placeholderText: root.mode === "projects" ? "Search projects or folders…" : "Search names, images, or ports…"
                    background: Rectangle {
                        radius: 9
                        color: Qt.rgba(0, 0, 0, 0.16)
                        border.width: 1
                        border.color: search.activeFocus ? root.accent : "#47505e"
                    }
                    onTextChanged: Qt.callLater(function () {
                        root.syncRows(false);
                    })
                    Keys.onEscapePressed: {
                        if (root.pending)
                            root.pending = null;
                        else
                            root.dismiss();
                    }
                    Keys.onDownPressed: {
                        list.forceActiveFocus();
                        if (list.currentIndex < 0)
                            list.currentIndex = 0;
                    }
                    onAccepted: {
                        if (root.selected && root.mode === "projects" && !root.pending)
                            root.act("project", root.selected.path);
                    }
                }
                RowLayout {
                    visible: root.mode === "services"
                    Repeater {
                        model: [
                            {
                                id: "all",
                                label: "All"
                            },
                            {
                                id: "containers",
                                label: "Containers"
                            },
                            {
                                id: "ports",
                                label: "Ports"
                            },
                            {
                                id: "stopped",
                                label: "Stopped"
                            }
                        ]
                        DevButton {
                            required property var modelData
                            text: modelData.label
                            highlighted: root.filter === modelData.id
                            onClicked: {
                                root.filter = modelData.id;
                                root.selectedKey = "";
                                Qt.callLater(function () {
                                    root.syncRows(false);
                                });
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Label {
                        text: root.rows.length + " results"
                        color: root.fg
                        opacity: 0.7
                        font.pixelSize: 12
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 18
                    ListView {
                        id: list
                        Layout.preferredWidth: Math.round(card.width * 0.43)
                        Layout.fillHeight: true
                        clip: true
                        spacing: 7
                        model: listRows
                        currentIndex: -1
                        ScrollBar.vertical: ScrollBar {}
                        Keys.onEscapePressed: root.dismiss()
                        onCurrentIndexChanged: {
                            if (!root.syncingList && currentIndex >= 0 && currentIndex < root.rows.length)
                                root.selectedKey = root.key(root.rows[currentIndex]);
                        }
                        Keys.onReturnPressed: {
                            if (root.selected && root.mode === "projects")
                                root.act("project", root.selected.path);
                        }
                        delegate: Rectangle {
                            id: projectRow
                            required property var rowData
                            readonly property var modelData: rowData
                            required property int index
                            readonly property bool chosen: root.selected && root.key(root.selected) === root.key(projectRow.modelData)
                            width: list.width
                            height: 72
                            radius: 10
                            color: chosen ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.2) : hover.hovered ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.07) : root.surface
                            border.width: chosen ? 1 : 0
                            border.color: "#647f9a"
                            HoverHandler {
                                id: hover
                            }
                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    root.selectedKey = root.key(projectRow.modelData);
                                    list.currentIndex = projectRow.index;
                                }
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 10
                                Rectangle {
                                    width: 7
                                    height: 7
                                    radius: 4
                                    color: projectRow.modelData.kind === "project" ? root.accent : projectRow.modelData.state === "running" || projectRow.modelData.state === "listening" ? "#9ac3a3" : "#858a96"
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Label {
                                        text: projectRow.modelData.name
                                        color: root.fg
                                        font.pixelSize: 14
                                        font.bold: projectRow.chosen
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Label {
                                        text: projectRow.modelData.kind === "project" ? projectRow.modelData.detail : projectRow.modelData.kind === "container" ? projectRow.modelData.state + " · " + projectRow.modelData.image : projectRow.modelData.detail
                                        color: root.fg
                                        opacity: 0.7
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                        elide: Text.ElideMiddle
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: root.rows.length === 0
                            text: scan.running ? "Loading…" : "No matches"
                            color: root.fg
                            opacity: 0.5
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 12
                        color: root.surface
                        ScrollView {
                            anchors.fill: parent
                            anchors.margins: 20
                            clip: true
                            contentWidth: availableWidth
                            ColumnLayout {
                                width: parent.width
                                spacing: 16
                                Label {
                                    text: root.selected ? root.selected.kind === "project" ? "PROJECT" : root.selected.kind === "container" ? "CONTAINER" : "LISTENING PROCESS" : "SELECT AN ITEM"
                                    color: root.accent
                                    font.pixelSize: 10
                                    font.letterSpacing: 1.5
                                }
                                Label {
                                    text: root.selected ? root.selected.name : "Nothing selected"
                                    color: root.fg
                                    font.pixelSize: 22
                                    font.bold: true
                                    Layout.fillWidth: true
                                    wrapMode: Text.WrapAnywhere
                                    textFormat: Text.PlainText
                                }
                                Detail {
                                    title: root.mode === "projects" ? "Folder" : "Status / address"
                                    value: root.selected ? root.selected.path || root.selected.detail : ""
                                }
                                Detail {
                                    visible: root.selected && root.selected.kind === "container"
                                    title: "Image"
                                    value: root.selected ? root.selected.image || "" : ""
                                }
                                Detail {
                                    visible: root.selected && root.selected.kind === "port" && !!root.selected.pid
                                    title: "Process ID"
                                    value: root.selected ? root.selected.pid || "" : ""
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    height: 1
                                    color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.1)
                                }
                                Flow {
                                    visible: root.mode === "projects" && !!root.selected
                                    Layout.fillWidth: true
                                    spacing: 8
                                    DevButton {
                                        text: "Open workspace"
                                        highlighted: true
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("project", root.selected.path)
                                    }
                                    DevButton {
                                        text: "Editor"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("editor", root.selected.path)
                                    }
                                    DevButton {
                                        text: "Terminal"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("terminal", root.selected.path)
                                    }
                                    DevButton {
                                        text: "Files"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("files", root.selected.path)
                                    }
                                    DevButton {
                                        text: "Copy path"
                                        enabled: !actionProcess.running && !scan.running
                                        onClicked: root.act("copy", root.selected.path)
                                    }
                                }
                                ColumnLayout {
                                    visible: root.mode === "projects" && !!root.selected
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Label {
                                        text: "OPEN WITH AI"
                                        color: root.accent
                                        font.pixelSize: 10
                                        font.letterSpacing: 1.2
                                    }
                                    Label {
                                        text: "Starts an interactive session in this project."
                                        color: root.fg
                                        opacity: 0.75
                                        font.pixelSize: 12
                                        wrapMode: Text.WordWrap
                                        Layout.fillWidth: true
                                    }
                                    ComboBox {
                                        id: agent
                                        Layout.fillWidth: true
                                        implicitHeight: 40
                                        model: root.snapshot.agents
                                        textRole: "name"
                                        contentItem: Text {
                                            text: agent.currentText
                                            color: root.fg
                                            font.pixelSize: 13
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: 12
                                        }
                                        background: Rectangle {
                                            radius: 8
                                            color: root.surface
                                            border.width: 1
                                            border.color: "#536479"
                                        }
                                        delegate: ItemDelegate {
                                            id: agentDelegate
                                            required property var modelData
                                            width: agent.width
                                            text: modelData.name + (modelData.available ? "" : " · not installed")
                                            enabled: modelData.available
                                            contentItem: Text {
                                                text: agentDelegate.text
                                                color: root.fg
                                                font.pixelSize: 13
                                            }
                                            background: Rectangle {
                                                color: agentDelegate.highlighted ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.2) : root.bg
                                            }
                                        }
                                        popup: Popup {
                                            y: agent.height + 4
                                            width: agent.width
                                            padding: 4
                                            background: Rectangle {
                                                radius: 8
                                                color: root.bg
                                                border.width: 1
                                                border.color: "#59697e"
                                            }
                                            contentItem: ListView {
                                                implicitHeight: contentHeight
                                                model: agent.popup.visible ? agent.delegateModel : null
                                                currentIndex: agent.highlightedIndex
                                                clip: true
                                            }
                                        }
                                    }
                                    Label {
                                        text: agent.currentIndex >= 0 && root.snapshot.agents[agent.currentIndex] ? root.snapshot.agents[agent.currentIndex].message : "Select an AI tool"
                                        color: root.fg
                                        opacity: 0.75
                                        font.pixelSize: 11
                                        wrapMode: Text.WrapAnywhere
                                        Layout.fillWidth: true
                                        textFormat: Text.PlainText
                                    }
                                    DevButton {
                                        text: "Launch " + agent.currentText
                                        highlighted: true
                                        Layout.fillWidth: true
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError && agent.currentIndex >= 0 && !!root.snapshot.agents[agent.currentIndex] && root.snapshot.agents[agent.currentIndex].available
                                        onClicked: root.act("ai-" + root.snapshot.agents[agent.currentIndex].id, root.selected.path)
                                    }
                                }
                                Flow {
                                    visible: root.selected && root.selected.kind === "container"
                                    Layout.fillWidth: true
                                    spacing: 8
                                    DevButton {
                                        text: "Live logs"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("logs", root.selected.id)
                                    }
                                    DevButton {
                                        text: "Inspect"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("inspect", root.selected.id)
                                    }
                                    DevButton {
                                        text: "Start"
                                        visible: root.selected && ["exited", "created"].indexOf(root.selected.state) !== -1
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.act("start", root.selected.id)
                                    }
                                    DevButton {
                                        text: "Restart"
                                        visible: root.selected && root.selected.state === "running"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.ask("restart", "Restart container", "This interrupts connections while the container restarts.")
                                    }
                                    DevButton {
                                        text: "Stop"
                                        danger: true
                                        visible: root.selected && root.selected.state === "running"
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.ask("stop", "Stop container", "This stops the container and disconnects its clients.")
                                    }
                                    DevButton {
                                        text: "Remove"
                                        danger: true
                                        visible: root.selected && ["exited", "created", "dead"].indexOf(root.selected.state) !== -1
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.ask("remove", "Remove container", "Removes this stopped container and its writable layer. Docker volumes are kept.")
                                    }
                                    DevButton {
                                        text: "Copy name"
                                        enabled: !actionProcess.running && !scan.running
                                        onClicked: root.act("copy", root.selected.name)
                                    }
                                }
                                Flow {
                                    visible: root.selected && root.selected.kind === "port"
                                    Layout.fillWidth: true
                                    spacing: 8
                                    DevButton {
                                        text: "Open inferred URL"
                                        visible: root.selected && !!root.selected.url
                                        enabled: !actionProcess.running && !scan.running
                                        onClicked: root.act("browser", root.selected.url)
                                    }
                                    DevButton {
                                        text: "Copy address"
                                        enabled: !actionProcess.running && !scan.running
                                        onClicked: root.act("copy", root.selected.url || "localhost:" + root.selected.port)
                                    }
                                    DevButton {
                                        text: "Stop process"
                                        danger: true
                                        visible: root.selected && !!root.selected.canStop
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.ask("terminate", "Stop process", "Requests a graceful exit for this project process. All ports owned by it will close.")
                                    }
                                    DevButton {
                                        text: "Force kill"
                                        danger: true
                                        visible: root.selected && !!root.selected.canStop
                                        enabled: !actionProcess.running && !scan.running && !root.snapshot.configError
                                        onClicked: root.ask("kill", "Force kill process", "Immediately kills this project process. Unsaved work in the process may be lost.")
                                    }
                                }
                                Label {
                                    visible: root.selected && root.selected.kind === "port" && !root.selected.canStop
                                    text: "Process controls are available for your own processes running inside your project folders."
                                    color: root.fg
                                    opacity: 0.7
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }
                Label {
                    visible: root.notice !== "" || !!root.snapshot.configError
                    text: root.snapshot.configError || root.notice
                    color: root.failed ? root.errorColor : root.successColor
                    font.pixelSize: 12
                    wrapMode: Text.WrapAnywhere
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                }
                Label {
                    text: "↑ ↓ select   ·   Enter opens workspace   ·   Esc closes"
                    color: root.fg
                    opacity: 0.7
                    font.pixelSize: 11
                }
            }
            Rectangle {
                visible: !!root.pending
                anchors.fill: parent
                color: "#b0000000"
                radius: 18
                z: 10
                focus: visible
                onVisibleChanged: {
                    if (visible)
                        forceActiveFocus();
                    else if (root.opened)
                        search.forceActiveFocus();
                }
                Keys.onEscapePressed: root.pending = null
                Keys.onReturnPressed: function (event) {
                    event.accepted = true;
                }
                Keys.onEnterPressed: function (event) {
                    event.accepted = true;
                }
                MouseArea {
                    anchors.fill: parent
                }
                Rectangle {
                    anchors.centerIn: parent
                    width: Math.min(460, card.width - 60)
                    height: 230
                    radius: 14
                    color: root.bg
                    border.width: 1
                    border.color: "#8c6670"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 22
                        spacing: 12
                        Label {
                            text: root.pending ? root.pending.label + "?" : ""
                            color: root.fg
                            font.bold: true
                            font.pixelSize: 20
                        }
                        Label {
                            text: root.pending ? root.pending.name : ""
                            color: root.accent
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        Label {
                            text: root.pending ? root.pending.description : ""
                            color: root.fg
                            opacity: 0.7
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Item {
                            Layout.fillHeight: true
                        }
                        RowLayout {
                            Item {
                                Layout.fillWidth: true
                            }
                            DevButton {
                                text: "Cancel"
                                onClicked: root.pending = null
                            }
                            DevButton {
                                text: root.pending ? root.pending.label : "Confirm"
                                danger: true
                                onClicked: {
                                    var p = root.pending;
                                    root.pending = null;
                                    root.act(p.kind, p.value);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
