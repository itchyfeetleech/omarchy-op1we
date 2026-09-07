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

  // Original mouse glyph: silhouette in bar ink with a status dot. Canvas
  // (2x backing store) keeps it crisp and font-independent at any scale.
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
      width: 32
      height: 32
      renderTarget: Canvas.Image

      // Rounded-rect path via arcs (no roundRect/reset: both are missing
      // from older Canvas implementations).
      function rr(ctx, x, y, w, h, r) {
        ctx.beginPath()
        ctx.moveTo(x + r, y)
        ctx.arcTo(x + w, y, x + w, y + h, r)
        ctx.arcTo(x + w, y + h, x, y + h, r)
        ctx.arcTo(x, y + h, x, y, r)
        ctx.arcTo(x, y, x + w, y, r)
        ctx.closePath()
      }

      onPaint: {
        var ctx = getContext("2d")
        var s = 2 // 32px backing for a 16px slot
        ctx.setTransform(1, 0, 0, 1, 0, 0)
        ctx.clearRect(0, 0, 32, 32)
        ctx.scale(s, s)
        ctx.globalCompositeOperation = "source-over"
        var body = mouseIcon.dim ? Qt.darker(mouseIcon.ink, 1.8) : mouseIcon.ink
        // Body.
        ctx.fillStyle = body.toString()
        glyph.rr(ctx, 4.2, 1.2, 7.6, 13.6, 3.4)
        ctx.fill()
        // Split between buttons + wheel, knocked out.
        ctx.globalCompositeOperation = "destination-out"
        ctx.fillRect(7.7, 1.4, 0.6, 4.6)
        glyph.rr(ctx, 7.1, 2.6, 1.8, 2.6, 0.9)
        ctx.fill()
        // Status badge tucked into the corner: a knocked-out ring keeps it
        // legible on any theme while leaving the silhouette intact.
        ctx.beginPath()
        ctx.arc(13.1, 13.1, 2.4, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalCompositeOperation = "source-over"
        ctx.fillStyle = mouseIcon.dot.toString()
        ctx.beginPath()
        ctx.arc(13.1, 13.1, 1.7, 0, Math.PI * 2)
        ctx.fill()
        if (mouseIcon.charging) {
          ctx.fillStyle = body.toString()
          ctx.beginPath()
          ctx.moveTo(13.5, 11.7)
          ctx.lineTo(12.5, 13.3)
          ctx.lineTo(13.1, 13.3)
          ctx.lineTo(12.7, 14.5)
          ctx.lineTo(13.8, 12.8)
          ctx.lineTo(13.2, 12.8)
          ctx.closePath()
          ctx.fill()
        }
      }
    }

    onInkChanged: glyph.requestPaint()
    onDimChanged: glyph.requestPaint()
    onDotChanged: glyph.requestPaint()
    onChargingChanged: glyph.requestPaint()
    Component.onCompleted: glyph.requestPaint()
  }
}
