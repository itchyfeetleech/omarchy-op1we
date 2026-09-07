import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Original vector drawing, kept in theme ink. Callouts use the vendor
// default physical layout; no additional protocol mapping is inferred.
Item {
  id: root
  property color ink: Color.foreground
  property string fontFamily: Style.font.family
  property var draft: null
  signal assigned(int slot, var action)
  signal customize(int slot)
  signal reverted(int slot)
  readonly property bool compact: width < Style.space(570)
  implicitHeight: compact ? Style.space(520) : Style.space(338)
  readonly property var points: [
    {slot: 1, name: "Left click", x: 0.445, y: 0.22, left: true, row: 0},
    {slot: 2, name: "Right click", x: 0.555, y: 0.22, left: false, row: 0},
    {slot: 3, name: "Scroll click", x: 0.5, y: 0.27, left: false, row: 1},
    {slot: 5, name: "Forward", x: 0.393, y: 0.40, left: true, row: 1},
    {slot: 4, name: "Back", x: 0.383, y: 0.52, left: true, row: 2}
  ]
  function options() {
    return [{value: "keep", label: "Keep current"}].concat(
      Model.MOUSE_BUTTONS.map(function(b) {return {value: b, label: b.charAt(0).toUpperCase() + b.slice(1) + " click"}}),
      [{value:"dpi-toggle",label:"Cycle DPI"}, {value:"dpi-plus",label:"DPI up"},
       {value:"dpi-minus",label:"DPI down"}, {value:"polling-switch",label:"Cycle polling"},
       {value:"unassigned",label:"Disabled"}, {value:"custom",label:"Custom binding…"}])
  }
  function value(slot) {
    var a = draft && draft.keys ? draft.keys[String(slot)] : null
    if (!a || a.kind === Model.KEEP_KIND) return "keep"
    if (a.kind === "mouse") return a.buttons && a.buttons.length === 1 ? a.buttons[0] : "custom"
    return ["dpi-toggle","dpi-plus","dpi-minus","polling-switch","unassigned"].indexOf(a.kind) >= 0 ? a.kind : "custom"
  }
  Rectangle {
    anchors.fill: parent
    color: Qt.alpha(root.ink, 0.025)
    border.color: Qt.alpha(root.ink, 0.13)
    radius: Style.cornerRadius
  }
  Canvas {
    id: drawing
    width: parent.width
    height: root.compact ? Style.space(245) : parent.height
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
      var c = getContext("2d")
      c.reset()
      var w = width, h = height
      // slender, symmetrical OP1we shell with a wider rear and narrow waist
      c.save(); c.translate(w/2, h*0.06); c.scale(h/330, h/330)
      c.strokeStyle = root.ink.toString(); c.lineWidth = 1.8
      c.fillStyle = Qt.alpha(root.ink, 0.04).toString()
      c.beginPath(); c.moveTo(0, 6)
      c.bezierCurveTo(51, 6, 69, 15, 70, 49)
      c.bezierCurveTo(72, 96, 67, 135, 73, 180)
      c.bezierCurveTo(87, 247, 68, 285, 0, 288)
      c.bezierCurveTo(-68, 285, -87, 247, -73, 180)
      c.bezierCurveTo(-67, 135, -72, 96, -70, 49)
      c.bezierCurveTo(-69, 15, -51, 6, 0, 6)
      c.closePath(); c.fill(); c.stroke()
      c.strokeStyle = Qt.alpha(root.ink,0.45).toString(); c.lineWidth = 1
      c.beginPath(); c.moveTo(0,7); c.lineTo(0,46); c.moveTo(0,90); c.lineTo(0,125)
      c.moveTo(-69,126); c.bezierCurveTo(-37,140,37,140,69,126); c.stroke()
      c.strokeStyle = Color.accent.toString(); c.strokeRect(-8,47,16,37)
      for(var y=53;y<80;y+=6) {c.beginPath();c.moveTo(-5,y);c.lineTo(5,y);c.stroke()}
      c.beginPath();c.moveTo(-74,112);c.lineTo(-76,143);c.moveTo(-76,151);c.lineTo(-77,180);c.stroke()
      c.fillStyle = Qt.alpha(root.ink,0.45).toString()
      c.font = "12px sans-serif"; c.textAlign = "center";c.fillText("OP1we",0,232)
      c.restore()
      if (!root.compact) {
        root.points.forEach(function(p) {
          var x=p.x*w, y=p.y*h, edge=p.left ? Style.space(194) : w-Style.space(194)
          var endY=Style.space(43+p.row*90)
          c.strokeStyle=Qt.alpha(root.ink,0.30).toString();c.lineWidth=1
          c.beginPath();c.moveTo(x,y);c.lineTo((x+edge)/2,y);c.lineTo(edge,endY);c.stroke()
          c.fillStyle=Color.accent.toString();c.beginPath();c.arc(x,y,3,0,Math.PI*2);c.fill()
        })
      }
    }
  }
  Connections { target: Color; function onAccentChanged() { drawing.requestPaint() } }
  onInkChanged: drawing.requestPaint()
  Repeater {
    model: root.points
    delegate: Column {
      required property var modelData
      required property int index
      x: root.compact ? Style.space(14) + (index % 2) * (root.width / 2)
        : modelData.left ? Style.space(14) : root.width - width - Style.space(14)
      y: root.compact ? Style.space(250 + Math.floor(index/2)*86) : Style.space(18 + modelData.row*90)
      width: root.compact ? root.width/2-Style.space(28) : Style.space(180)
      spacing: Style.space(7)
      Text {
        text: modelData.name.toUpperCase()
        color: Qt.alpha(root.ink,0.65)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
      }
      Dropdown {
        width: parent.width
        enabled: !!root.draft
        value: root.value(modelData.slot)
        options: root.options()
        foreground: root.ink
        fontFamily: root.fontFamily
        onChanged: function(v) {
          if (v === "custom") {root.customize(modelData.slot);return}
          if (v === "keep") {root.reverted(modelData.slot);return}
          root.assigned(modelData.slot, Model.MOUSE_BUTTONS.indexOf(v)>=0
            ? {kind:"mouse",buttons:[v]} : {kind:v})
        }
      }
    }
  }
  Text {
    visible: !root.compact
    x: parent.width - width - Style.space(18)
    y: Style.space(222)
    width: Style.space(168)
    text: "OP1we\n2.4 GHz WIRELESS"
    lineHeight: 1.4
    color: Qt.alpha(root.ink,0.3)
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    font.letterSpacing: 1.1
  }
}
