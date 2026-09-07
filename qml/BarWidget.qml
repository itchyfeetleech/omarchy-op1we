import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Mouse battery/status pill for the bar; hosts the settings popup.
// Left click opens the panel, middle click refreshes, right click toggles
// the percent label. Hover refreshes status (and configuration when stale).
BarWidget {
  id: root
  moduleName: "hoppcx.op1we"

  readonly property bool showPercent: setting("showPercent", true) !== false
  readonly property var status: controller.status
  readonly property var snapshot: controller.snapshot
  readonly property string connection: status ? String(status.connection || "") : ""
  readonly property int pct: status
    && (typeof status.batteryPercent === "number") ? Math.round(status.batteryPercent) : -1
  readonly property bool charging: status ? status.charging === 1 : false
  readonly property bool lowBattery: pct >= 0 && pct <= 15 && connection === "connected"

  readonly property color ink: bar ? bar.barForeground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dotColor: {
    if (connection === "connected") return lowBattery ? urgent : ink
    if (connection === "receiver-only") return Color.accent
    return Qt.darker(ink, 1.8)
  }
  readonly property string tooltip: Model.tooltipFor(status, snapshot)
    + (controller.dirty ? " \u00B7 unsaved edits" : "")

  function refresh() {
    controller.refreshStatus()
    controller.refreshSnapshot(true)
  }

  function toggleShowPercent() {
    var entry = { id: root.moduleName }
    for (var key in root.settings) if (key !== "id") entry[key] = root.settings[key]
    entry.showPercent = !root.showPercent
    root.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    // Named op1we: the Panel base already owns `controller` (PanelController).
    if ("op1we" in target) target.op1we = controller
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  // Shape contract for shell.summon/hide/toggle routing (Bar.findPanelWidget
  // requires open/close/opened on the bar-widget root).
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item && panelLoader.item.openFromHotkey) panelLoader.item.openFromHotkey()
    else if (panelLoader.item && panelLoader.item.open) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Controller {
    id: controller
    bar: root.bar
    settings: root.settings
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    labelVisible: false
    hasVisualContent: true
    dimmed: root.connection === "unavailable" || root.connection === ""
    fixedWidth: root.vertical ? -1 : Math.round(content.implicitWidth + Style.spaceReal(8.5) * 2)
    fixedHeight: root.vertical ? content.implicitHeight + Style.spaceReal(12) : -1
    tooltipText: root.tooltip

    onPressed: function(b) {
      if (b === Qt.RightButton) root.toggleShowPercent()
      else if (b === Qt.MiddleButton) root.refresh()
      else root.togglePanel()
    }
    onTooltipHoveredChanged: {
      if (tooltipHovered) {
        controller.refreshStatus()
        controller.refreshSnapshot(false)
      }
    }

    Row {
      id: content
      anchors.centerIn: parent
      spacing: Style.space(5)

      MouseIcon {
        anchors.verticalCenter: parent.verticalCenter
        ink: button.foreground
        dim: button.dimmed
        dot: root.dotColor
        charging: root.charging
      }

      Text {
        visible: root.showPercent && !root.vertical
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.pct >= 0 ? root.pct + "%" : "--"
        color: root.lowBattery ? root.urgent : button.foreground
        font.family: button.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
    }
  }

  // Normalized 24px outline fitted to the actual canvas bounds.
  component MouseIcon: Item {
    id: mouseIcon
    property color ink: Color.foreground
    property bool dim: false
    property color dot: Color.foreground
    property bool charging: false
    width: Style.bar.iconCanvas
    height: Style.bar.iconCanvas

    Canvas {
      id: glyph
      anchors.fill: parent
      antialiasing: true
      onWidthChanged: requestPaint()
      onHeightChanged: requestPaint()
      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        ctx.scale(width / 24, height / 24)
        ctx.strokeStyle = mouseIcon.ink.toString()
        ctx.lineWidth = 1.8
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
        ctx.beginPath()
        ctx.moveTo(12, 2)
        ctx.bezierCurveTo(7.5, 2, 6, 4.8, 6, 8)
        ctx.lineTo(6, 15.5)
        ctx.bezierCurveTo(6, 19.5, 8.4, 22, 12, 22)
        ctx.bezierCurveTo(15.6, 22, 18, 19.5, 18, 15.5)
        ctx.lineTo(18, 8)
        ctx.bezierCurveTo(18, 4.8, 16.5, 2, 12, 2)
        ctx.closePath()
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(12, 5)
        ctx.lineTo(12, 9)
        ctx.stroke()
        if (mouseIcon.charging) {
          ctx.beginPath()
          ctx.moveTo(12.8, 12)
          ctx.lineTo(10.3, 16)
          ctx.lineTo(13.4, 16)
          ctx.lineTo(11.2, 19.5)
          ctx.stroke()
        } else {
          ctx.fillStyle = mouseIcon.dot.toString()
          ctx.beginPath()
          ctx.arc(12, 16.2, 1.5, 0, Math.PI * 2)
          ctx.fill()
        }
      }
    }
    onInkChanged: glyph.requestPaint()
    onDimChanged: glyph.requestPaint()
    onDotChanged: glyph.requestPaint()
    onChargingChanged: glyph.requestPaint()
  }
}
