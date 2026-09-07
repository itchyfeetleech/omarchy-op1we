import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// OP1we settings popout: status hero, DPI/sensor/button sections, host
// profiles, device actions and an Apply/Cancel footer. All edits land in a
// draft first; nothing is written before Apply, and Apply verifies by
// readback (see Controller). Keyboard: Tab moves through every control,
// Enter/Space activates, Escape closes (dirty forms ask first),
// Ctrl+Enter applies.
Panel {
  id: root
  moduleName: "hoppcx.op1we"
  ipcTarget: "hoppcx.op1we"

  property var anchorItem: null
  property var hostWidget: null
  property var op1we: null
  property bool openedFromHotkey: false
  readonly property var barIdentity: hostWidget || root

  readonly property color ink: bar ? bar.barForeground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.alpha(ink, 0.65)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    openedFromHotkey = false
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
  }

  function openFromHotkey() {
    openedFromHotkey = true
    root.controller.show()
    Qt.callLater(function() {
      if (root.opened) setCenterHoverRevealSuppressed(true)
    })
  }

  // Dirty forms ask before closing; the draft itself survives a close, so
  // "Keep edits" never loses anything. Popout switches bypass the dialog
  // (the bar must keep working) and likewise keep the draft.
  function close() {
    setCenterHoverRevealSuppressed(false)
    if (op1we && op1we.dirty && !closeDialog.opened && !actionDialog.opened) {
      closeDialog.opened = true
      return
    }
    closeDialog.opened = false
    actionDialog.opened = false
    enrollDialog.opened = false
    root.controller.hide()
  }

  function closeForPopoutSwitch() {
    popoutSwitchClosing = true
    closeDialog.opened = false
    actionDialog.opened = false
    enrollDialog.opened = false
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
    Qt.callLater(function() { popoutSwitchClosing = false })
  }

  function toggle() {
    if (root.opened) root.close()
    else root.openFromHotkey()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && "centerHoverRevealSuppressed" in root.bar)
      root.bar.centerHoverRevealSuppressed = value
  }

  // ---- local UI state ------------------------------------------------------

  property int currentTab: 0
  property int selectedSlot: 4
  property bool showBindingDetails: false
  property string selectedProfile: ""
  property string pendingAction: ""
  property string pendingArg: ""

  function _onEscape() {
    if (closeDialog.opened) { closeDialog.opened = false; return }
    if (actionDialog.opened) { actionDialog.opened = false; return }
    if (enrollDialog.opened) { enrollDialog.opened = false; return }
    root.close()
  }

  function _applyFromKeyboard() {
    if (op1we && op1we.dirty && !op1we.foregroundBusy && op1we.enrolled) op1we.applyDraft()
  }

  function requestReset() {
    if (!op1we || op1we.foregroundBusy) return
    pendingAction = "reset"
    pendingArg = ""
    actionDialog.message = op1we.dirty
      ? "Reset the mouse to vendor defaults? Unsaved edits will be discarded."
      : "Reset the mouse to vendor defaults (DPI, buttons, debounce, polling, sleep)?"
    actionDialog.opened = true
  }

  function requestRestore(path) {
    if (!op1we || op1we.foregroundBusy) return
    var file = String(path || "").trim()
    if (file === "") {
      op1we.setNotice("Pick a backup file to restore.", "error")
      return
    }
    pendingAction = "restore"
    pendingArg = file
    actionDialog.message = op1we.dirty
      ? "Restore " + file + "? Unsaved edits will be discarded."
      : "Restore " + file + " to the mouse?"
    actionDialog.opened = true
  }

  function requestProfileApply(name) {
    if (!op1we || op1we.foregroundBusy) return
    var profile = String(name || "")
    if (profile === "") {
      op1we.setNotice("Pick a profile to apply.", "error")
      return
    }
    if (op1we.dirty) {
      pendingAction = "profile-apply"
      pendingArg = profile
      actionDialog.message = "Apply profile \"" + profile
        + "\"? Unsaved edits will be discarded."
      actionDialog.opened = true
      return
    }
    op1we.applyProfile(profile)
  }

  function _runPendingAction() {
    if (!op1we) return
    if (pendingAction === "reset") op1we.resetDevice()
    else if (pendingAction === "restore") op1we.restoreBackup(pendingArg)
    else if (pendingAction === "profile-apply") op1we.applyProfile(pendingArg)
    pendingAction = ""
    pendingArg = ""
  }

  // ---- draft helpers (delegates call these) ---------------------------------

  function draftKeys(slot) {
    if (!op1we || !op1we.draft || !op1we.draft.keys) return { kind: "keep" }
    return op1we.draft.keys[String(slot)] || { kind: "keep" }
  }

  function setDraftKey(slot, action) {
    if (!op1we || !op1we.draft) return
    op1we.draft.keys[String(slot)] = action
    op1we.touchDraft()
  }

  function bindingFor(slot) {
    if (!op1we || !op1we.snapshot || !(op1we.snapshot.bindings instanceof Array)) return null
    var bindings = op1we.snapshot.bindings
    for (var i = 0; i < bindings.length; i++) {
      if (bindings[i] && bindings[i].slot === slot) return bindings[i]
    }
    return null
  }

  function stageFor(index) {
    if (!op1we || !op1we.snapshot || !(op1we.snapshot.stages instanceof Array)) return null
    var stages = op1we.snapshot.stages
    for (var i = 0; i < stages.length; i++) {
      if (stages[i] && stages[i].index === index) return stages[i]
    }
    return null
  }

  function kindOptions(slot) {
    var options = [{ value: "keep", label: "Keep current" }]
    var kinds = Model.ACTION_KINDS
    for (var i = 0; i < kinds.length; i++) options.push(kinds[i])
    return options
  }

  function selectedKind(slot) {
    var action = draftKeys(slot)
    if (!action || action.kind === Model.KEEP_KIND) return "keep"
    return String(action.kind)
  }

  function pickKind(slot, kind) {
    if (kind === "keep") {
      // Revert to the confirmed binding.
      var confirmed = op1we && op1we.confirmed && op1we.confirmed.keys
        ? op1we.confirmed.keys[String(slot)] : null
      setDraftKey(slot, confirmed ? Model.clone(confirmed) : { kind: Model.KEEP_KIND })
      return
    }
    if (kind === "mouse") setDraftKey(slot, { kind: "mouse", buttons: ["left"] })
    else if (kind === "key") setDraftKey(slot, { kind: "key", keys: [0x04] })
    else if (kind === "combo")
      setDraftKey(slot, { kind: "combo", keys: [0x04], modifiers: 0 })
    else if (kind === "media") setDraftKey(slot, { kind: "media", usage: 0xCD })
    else setDraftKey(slot, { kind: kind })
  }

  function toggleMouseButton(slot, name) {
    var action = Model.clone(draftKeys(slot))
    if (!action || action.kind !== "mouse") return
    var buttons = action.buttons instanceof Array ? action.buttons.slice() : []
    var at = buttons.indexOf(name)
    if (at === -1) buttons.push(name)
    else buttons.splice(at, 1)
    action.buttons = buttons
    setDraftKey(slot, action)
  }

  function toggleModifier(slot, bit) {
    var action = Model.clone(draftKeys(slot))
    if (!action || action.kind !== "combo") return
    var mods = typeof action.modifiers === "number" ? action.modifiers : 0
    action.modifiers = mods ^ bit
    setDraftKey(slot, action)
  }

  function setComboKey(slot, index, usage) {
    var action = Model.clone(draftKeys(slot))
    if (!action || action.kind !== "combo") return
    var keys = action.keys instanceof Array ? action.keys.slice() : [0x04]
    while (keys.length <= index) keys.push(0x04)
    keys[index] = usage
    action.keys = keys
    setDraftKey(slot, action)
  }

  function setComboCount(slot, count) {
    var action = Model.clone(draftKeys(slot))
    if (!action || action.kind !== "combo") return
    var keys = action.keys instanceof Array ? action.keys.slice() : [0x04]
    while (keys.length < count) keys.push(0x04)
    action.keys = keys.slice(0, Math.max(1, Math.min(3, count)))
    setDraftKey(slot, action)
  }

  onOpenedChanged: {
    if (!op1we) return
    op1we.panelOpen = opened
    if (opened) {
      closeDialog.opened = false
      actionDialog.opened = false
      enrollDialog.opened = false
      pendingAction = ""
      op1we.refreshSnapshot(true)
      op1we.refreshStatus()
      op1we.refreshProfiles()
      flick.contentY = 0
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: flick
    contentWidth: panel.fittedContentWidth(Style.space(760))
    contentHeight: panel.fittedContentHeight(Math.min(form.implicitHeight, Style.space(720)) + footer.implicitHeight + Style.space(18))

    Flickable {
      id: flick
      anchors.fill: parent
      anchors.bottomMargin: footer.implicitHeight + Style.space(18)
      contentWidth: width
      contentHeight: form.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick
      interactive: contentHeight > height
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

      Keys.onEscapePressed: function(event) {
        root._onEscape()
        event.accepted = true
      }
      Keys.onPressed: function(event) {
        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
            && (event.modifiers & Qt.ControlModifier)) {
          root._applyFromKeyboard()
          event.accepted = true
        }
      }

      Column {
        id: form
        width: flick.width
        spacing: Style.space(14)

        // ---- hero: mouse, connection, battery, current DPI ------------------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroLabels.implicitHeight, heroPercent.implicitHeight)

          Column {
            id: heroLabels
            anchors.left: parent.left
            anchors.right: heroPercent.left
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              textFormat: Text.PlainText
              text: "OP1we"
              color: root.ink
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              textFormat: Text.PlainText
              text: {
                if (!root.op1we || !root.op1we.status) return "WAITING FOR STATUS"
                var meta = Model.connectionMeta(root.op1we.status)
                var extra = root.op1we.snapshot && root.op1we.snapshot.currentDpi != null
                  ? " \u00B7 " + root.op1we.snapshot.currentDpi + " DPI" : ""
                return (meta.label + extra).toUpperCase()
              }
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              visible: text !== ""
              textFormat: Text.PlainText
              text: {
                if (!root.op1we || !root.op1we.status) return ""
                if (root.op1we.status.batteryFresh === false
                    && root.op1we.status.batteryPercent != null) return "Battery: last known value"
                return ""
              }
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
              width: parent.width
            }
          }

          Text {
            id: heroPercent
            textFormat: Text.PlainText
            text: {
              if (!root.op1we || !root.op1we.status) return "--"
              var pct = root.op1we.status.batteryPercent
              return pct === null || pct === undefined ? "--" : pct + "%"
            }
            color: root.op1we && root.op1we.status && root.op1we.status.batteryPercent !== null
              && root.op1we.status.batteryPercent <= 15 ? root.urgent : root.ink
            font.family: root.fontFamily
            font.pixelSize: Style.font.displayLarge
            font.bold: true
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
          }
        }

        // ---- banners: busy / errors / stale / recovery / enrollment --------
        Column {
          width: parent.width
          spacing: Style.space(8)

          Text {
            visible: root.op1we && root.op1we.foregroundBusy
            textFormat: Text.PlainText
            text: "Working\u2026"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            width: parent.width
            elide: Text.ElideRight
          }

          Text {
            visible: text !== ""
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: {
              if (!root.op1we) return ""
              var err = root.op1we.statusError || root.op1we.snapshotError
              return err ? Model.errorText(err) : ""
            }
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Row {
            visible: root.op1we && (root.op1we.statusError || root.op1we.snapshotError)
              && !root.op1we.foregroundBusy
            width: parent.width
            spacing: Style.space(8)

            Button {
              text: "Retry"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: {
                root.op1we.refreshStatus()
                root.op1we.refreshSnapshot(true)
              }
            }
          }

          Text {
            visible: root.op1we && root.op1we.staleNotice
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: "Settings changed on the mouse. The form was re-read; review it and apply again."
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Row {
            visible: root.op1we && root.op1we.recoveryBackup !== ""
            width: parent.width
            spacing: Style.space(8)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: "Recovery backup ready."
              color: root.ink
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              width: Math.max(0, parent.width - restoreRecoveryButton.implicitWidth - parent.spacing)
              elide: Text.ElideRight
            }

            Button {
              id: restoreRecoveryButton
              anchors.verticalCenter: parent.verticalCenter
              text: "Restore it"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: {
                restorePath.text = root.op1we.recoveryBackup
                root.requestRestore(root.op1we.recoveryBackup)
              }
            }
          }

          Column {
            visible: root.op1we && !root.op1we.enrolled && !root.op1we.foregroundBusy
            width: parent.width
            spacing: Style.space(8)

            Text {
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              width: parent.width
              text: "Writes are locked until this receiver is enrolled. Only enroll when the mouse on it is the OP1we."
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Button {
              text: "Enroll this receiver"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: enrollDialog.opened = true
            }
          }
        }



        Row {
          width: parent.width
          spacing: Style.space(8)
          Repeater {
            model: ["Buttons", "Sensitivity", "Profiles & device"]
            delegate: Button {
              required property int index
              required property string modelData
              width: (form.width - Style.space(16)) / 3
              text: modelData
              selected: root.currentTab === index
              bordered: true
              focusable: true
              opacity: enabled ? 1 : 0.4
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: { root.currentTab = index; flick.contentY = 0 }
            }
          }
        }

        MouseMap {
          visible: root.currentTab === 0
          width: parent.width
          ink: root.ink
          fontFamily: root.fontFamily
          draft: root.op1we ? root.op1we.draft : null
          onReverted: function(slot) { root.pickKind(slot, "keep") }
          onAssigned: function(slot, action) { root.setDraftKey(slot, action) }
          onCustomize: function(slot) { root.selectedSlot = slot; root.showBindingDetails = true }
        }

        // ---- DPI stages ------------------------------------------------------
        Column {
          visible: root.currentTab === 1
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "DPI STAGES"
            foreground: root.ink
            fontFamily: root.fontFamily
          }

          Grid {
            width: parent.width
            columns: width < Style.space(520) ? 2 : 4
            spacing: Style.space(12)
          Repeater {
            model: 4
            delegate: Column {
              required property int index
              width: (form.width - Style.space(12) * (parent.columns - 1)) / parent.columns
              spacing: Style.space(10)

              property bool encodable: root.op1we && root.op1we.draft
                && root.op1we.draft.cpiEncodable
                ? root.op1we.draft.cpiEncodable[index] !== false : true
              property bool active: root.op1we && root.op1we.snapshot
                ? root.op1we.snapshot.currentStage === index : false

              Text {

                textFormat: Text.PlainText
                text: "Stage " + (index + 1) + (active ? " \u25CF" : "")
                color: active ? root.ink : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: active
                width: Style.space(78)
                elide: Text.ElideRight
              }

              NumberField {

                enabled: encodable && root.op1we && root.op1we.draft
                value: root.op1we && root.op1we.draft && root.op1we.draft.cpi
                  && root.op1we.draft.cpi[index] != null ? root.op1we.draft.cpi[index] : 50
                from: 50
                to: 10000
                stepSize: 50
                foreground: root.ink
                accent: Color.accent
                fontFamily: root.fontFamily
                onModified: function(v) {
                  if (!root.op1we || !root.op1we.draft) return
                  root.op1we.draft.cpi[index] = v
                  root.op1we.touchDraft()
                }
              }

              Text {
                visible: !encodable

                textFormat: Text.PlainText
                text: "Higher DPI preserved"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                width: parent.width
                elide: Text.ElideRight
                wrapMode: Text.WordWrap
              }
            }
          }

          }

          Text {
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: "50–10000 DPI · 50 DPI increments. Higher existing values are preserved."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }



        // ---- sensor ------------------------------------------------------------
        Column {
          visible: root.currentTab === 1
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "SENSOR"
            foreground: root.ink
            fontFamily: root.fontFamily
          }

          Row {
            width: parent.width
            spacing: Style.space(10)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: "Polling rate"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              width: Style.space(150)
              elide: Text.ElideRight
            }

            Dropdown {
              anchors.verticalCenter: parent.verticalCenter
              enabled: root.op1we && root.op1we.draft ? true : false
              value: root.op1we && root.op1we.draft && root.op1we.draft.pollingHz != null
                ? String(root.op1we.draft.pollingHz) : ""
              options: [
                { value: "125", label: "125 Hz" },
                { value: "250", label: "250 Hz" },
                { value: "500", label: "500 Hz" },
                { value: "1000", label: "1000 Hz" }
              ]
              fontFamily: root.fontFamily
              onChanged: function(v) {
                if (!root.op1we || !root.op1we.draft) return
                root.op1we.draft.pollingHz = parseInt(v, 10)
                root.op1we.touchDraft()
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(10)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: "Debounce (ms)"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              width: Style.space(150)
              elide: Text.ElideRight
            }

            NumberField {
              anchors.verticalCenter: parent.verticalCenter
              enabled: root.op1we && root.op1we.draft ? true : false
              value: root.op1we && root.op1we.draft && root.op1we.draft.debounceMs != null
                ? root.op1we.draft.debounceMs : 0
              from: 0
              to: 30
              stepSize: 1
              foreground: root.ink
              accent: Color.accent
              fontFamily: root.fontFamily
              onModified: function(v) {
                if (!root.op1we || !root.op1we.draft) return
                root.op1we.draft.debounceMs = v
                root.op1we.touchDraft()
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(10)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: "Sleep after (s)"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              width: Style.space(150)
              elide: Text.ElideRight
            }

            NumberField {
              anchors.verticalCenter: parent.verticalCenter
              enabled: root.op1we && root.op1we.draft ? true : false
              value: root.op1we && root.op1we.draft && root.op1we.draft.sleepS != null
                ? root.op1we.draft.sleepS : 0
              from: 0
              to: 2550
              stepSize: 10
              foreground: root.ink
              accent: Color.accent
              fontFamily: root.fontFamily
              onModified: function(v) {
                if (!root.op1we || !root.op1we.draft) return
                root.op1we.draft.sleepS = v
                root.op1we.touchDraft()
              }
            }
          }

          Toggle {
            width: parent.width
            label: "Ripple control"
            enabled: root.op1we && root.op1we.draft ? true : false
            checked: root.op1we && root.op1we.draft ? root.op1we.draft.ripple === true : false
            foreground: root.ink
            accent: Color.accent
            fontFamily: root.fontFamily
            onClicked: {
              if (!root.op1we || !root.op1we.draft) return
              root.op1we.draft.ripple = !(root.op1we.draft.ripple === true)
              root.op1we.touchDraft()
            }
          }

          Toggle {
            width: parent.width
            label: "Angle snapping"
            enabled: root.op1we && root.op1we.draft ? true : false
            checked: root.op1we && root.op1we.draft ? root.op1we.draft.fixline === true : false
            foreground: root.ink
            accent: Color.accent
            fontFamily: root.fontFamily
            onClicked: {
              if (!root.op1we || !root.op1we.draft) return
              root.op1we.draft.fixline = !(root.op1we.draft.fixline === true)
              root.op1we.touchDraft()
            }
          }

          Toggle {
            width: parent.width
            label: "Turn off light while moving"
            enabled: root.op1we && root.op1we.draft ? true : false
            checked: root.op1we && root.op1we.draft
              ? root.op1we.draft.turnOffLight === true : false
            foreground: root.ink
            accent: Color.accent
            fontFamily: root.fontFamily
            onClicked: {
              if (!root.op1we || !root.op1we.draft) return
              root.op1we.draft.turnOffLight = !(root.op1we.draft.turnOffLight === true)
              root.op1we.touchDraft()
            }
          }

          Text {
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: {
              var count = root.op1we && root.op1we.snapshot ? root.op1we.snapshot.stageCount : null
              return "DPI stages: " + (count === null || count === undefined ? "--" : count)
                + " · Read only"
            }
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: "Lift-off distance is not available yet. Your current setting is preserved."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }



        // ---- buttons -------------------------------------------------------------
        Column {
          visible: root.currentTab === 0
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "CUSTOM BINDING"
            foreground: root.ink
            fontFamily: root.fontFamily
          }

          Row {
            spacing: Style.space(10)
            Dropdown {
              width: Style.space(220)
              value: String(root.selectedSlot)
              options: Array.from({length: 12}, function(_, i) {
                return {value: String(i + 1), label: Model.slotLabel(i + 1)}
              })
              onChanged: function(v) { root.selectedSlot = parseInt(v); root.showBindingDetails = true }
            }
            Button {
              text: root.showBindingDetails ? "Hide details" : "Edit binding"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              foreground: root.ink
              onClicked: root.showBindingDetails = !root.showBindingDetails
            }
          }

          Repeater {
            model: root.showBindingDetails ? 1 : 0
            delegate: Column {
              required property int index
              width: form.width
              spacing: Style.space(6)

              property int slot: root.selectedSlot
              property var action: root.draftKeys(slot)
              property var confirmedBinding: root.bindingFor(slot)
              property string kind: action && action.kind ? String(action.kind) : Model.KEEP_KIND
              property bool changed: {
                var keys = root.op1we && root.op1we.confirmed
                  ? root.op1we.confirmed.keys : null
                if (!keys) return false
                return !Model.actionsEqual(keys[String(slot)], action)
              }

              Row {
                width: parent.width
                spacing: Style.space(10)

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: Model.slotLabel(slot) + (changed ? " \u25CF" : "")
                  color: changed ? root.ink : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: changed
                  width: Style.space(150)
                  elide: Text.ElideRight
                }

                Dropdown {
                  anchors.verticalCenter: parent.verticalCenter
                  enabled: root.op1we && root.op1we.draft ? true : false
                  value: kind === Model.KEEP_KIND ? "keep" : kind
                  options: root.kindOptions(slot)
                  fontFamily: root.fontFamily
                  onChanged: function(v) { root.pickKind(slot, v) }
                }
              }

              Text {
                visible: kind === Model.KEEP_KIND
                textFormat: Text.PlainText
                width: parent.width
                text: "On the mouse: " + Model.bindingLabel(confirmedBinding)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }

              // Mouse buttons multi-select.
              Row {
                visible: kind === "mouse"
                width: parent.width
                spacing: Style.space(6)

                Repeater {
                  model: Model.MOUSE_BUTTONS
                  delegate: Button {
                    required property string modelData
                    text: modelData.charAt(0).toUpperCase()
                    tooltipText: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                    focusable: true
              opacity: enabled ? 1 : 0.4
                    bordered: true
                    selected: action && action.buttons instanceof Array
                      && action.buttons.indexOf(modelData) !== -1
                    foreground: root.ink
                    fontFamily: root.fontFamily
                    onClicked: root.toggleMouseButton(slot, modelData)
                  }
                }
              }

              // Single key.
              Row {
                visible: kind === "key"
                width: parent.width
                spacing: Style.space(10)

                NumberField {
                  anchors.verticalCenter: parent.verticalCenter
                  value: action && action.keys instanceof Array && action.keys.length > 0
                    ? action.keys[0] : 4
                  from: 4
                  to: 101
                  stepSize: 1
                  foreground: root.ink
                  accent: Color.accent
                  fontFamily: root.fontFamily
                  onModified: function(v) {
                    root.setDraftKey(slot, { kind: "key", keys: [v] })
                  }
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: action && action.keys instanceof Array && action.keys.length > 0
                    ? Model.keyUsageName(action.keys[0]) : ""
                  color: root.ink
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  elide: Text.ElideRight
                }
              }

              // Key combination.
              Column {
                visible: kind === "combo"
                width: parent.width
                spacing: Style.space(6)

                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "Keys:"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Button {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "\u2212"
                    focusable: true
              opacity: enabled ? 1 : 0.4
                    bordered: true
                    foreground: root.ink
                    fontFamily: root.fontFamily
                    onClicked: {
                      var n = action && action.keys instanceof Array ? action.keys.length : 1
                      root.setComboCount(slot, n - 1)
                    }
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: action && action.keys instanceof Array ? String(action.keys.length) : "1"
                    color: root.ink
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Button {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "+"
                    focusable: true
              opacity: enabled ? 1 : 0.4
                    bordered: true
                    foreground: root.ink
                    fontFamily: root.fontFamily
                    onClicked: {
                      var n = action && action.keys instanceof Array ? action.keys.length : 1
                      root.setComboCount(slot, n + 1)
                    }
                  }

                  Repeater {
                    model: action && action.keys instanceof Array ? action.keys.length : 1
                    delegate: NumberField {
                      required property int index
                      property int keyIndex: index
                      anchors.verticalCenter: parent.verticalCenter
                      value: action && action.keys instanceof Array
                        && action.keys[keyIndex] !== undefined ? action.keys[keyIndex] : 4
                      from: 4
                      to: 101
                      stepSize: 1
                      fieldWidth: Style.space(84)
                      foreground: root.ink
                      accent: Color.accent
                      fontFamily: root.fontFamily
                      onModified: function(v) { root.setComboKey(slot, keyIndex, v) }
                    }
                  }
                }

                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "Modifiers:"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Repeater {
                    model: [1, 2, 4, 8]
                    delegate: Button {
                      required property int modelData
                      property int bit: modelData
                      text: Model.MOD_NAMES[[1, 2, 4, 8].indexOf(bit)]
                      focusable: true
              opacity: enabled ? 1 : 0.4
                      bordered: true
                      selected: action && typeof action.modifiers === "number"
                        && (action.modifiers & bit) !== 0
                      foreground: root.ink
                      fontFamily: root.fontFamily
                      onClicked: root.toggleModifier(slot, bit)
                    }
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: action ? Model.actionLabel(action) : ""
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }
              }

              // Media key.
              Row {
                visible: kind === "media"
                width: parent.width
                spacing: Style.space(10)

                Dropdown {
                  anchors.verticalCenter: parent.verticalCenter
                  value: action && typeof action.usage === "number" ? String(action.usage) : ""
                  options: Model.MEDIA_OPTIONS.map(function(m) {
                    return { value: String(m.usage), label: m.label }
                  })
                  fontFamily: root.fontFamily
                  onChanged: function(v) {
                    root.setDraftKey(slot, { kind: "media", usage: parseInt(v, 10) })
                  }
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: "Keep at least one accessible button assigned to left-click."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }



        // ---- profiles (host-side) ------------------------------------------------
        Column {
          visible: root.currentTab === 2
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "PROFILES"
            foreground: root.ink
            fontFamily: root.fontFamily
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            Dropdown {
              id: profilePicker
              anchors.verticalCenter: parent.verticalCenter
              value: root.selectedProfile
              options: [{ value: "", label: root.op1we && root.op1we.profiles.length
                ? "Choose a profile" : "No saved profiles" }].concat(
                (root.op1we && root.op1we.profiles instanceof Array
                ? root.op1we.profiles : []).map(function(p) {
                  return { value: String(p.name || ""), label: String(p.name || "") }
                }))
              fontFamily: root.fontFamily
              onChanged: function(v) { root.selectedProfile = v }
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Refresh"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.op1we.refreshProfiles()
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            Button {
              text: "Apply"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.selectedProfile !== "" && root.op1we && !root.op1we.foregroundBusy
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.requestProfileApply(root.selectedProfile)
            }

            Button {
              text: "Delete"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.selectedProfile !== "" && root.op1we && !root.op1we.foregroundBusy
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: {
                root.op1we.deleteProfile(root.selectedProfile)
                root.selectedProfile = ""
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            TextField {
              id: profileName
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(200)
              placeholderText: "Profile name"
              font.family: root.fontFamily
              onAccepted: {
                if (root.op1we.saveProfile(text)) text = ""
              }
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Save current"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.op1we && !root.op1we.foregroundBusy && root.op1we.snapshot
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: {
                if (root.op1we.saveProfile(profileName.text)) profileName.text = ""
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            TextField {
              id: profileFile
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(200)
              placeholderText: "/path/to/profile.json"
              font.family: root.fontFamily
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Export"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.selectedProfile !== "" && root.op1we && !root.op1we.foregroundBusy
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.op1we.exportProfile(root.selectedProfile, profileFile.text)
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Import"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.op1we && !root.op1we.foregroundBusy
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.op1we.importProfile(profileName.text, profileFile.text)
            }
          }

          Text {
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: "Profiles live on this host; the mouse holds one device profile. Applying a profile with unsaved edits asks first."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }



        // ---- device ----------------------------------------------------------------
        Column {
          visible: root.currentTab === 2
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "DEVICE"
            foreground: root.ink
            fontFamily: root.fontFamily
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Back up now"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.op1we && !root.op1we.foregroundBusy
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.op1we.backupNow()
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Reset to defaults"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.op1we && !root.op1we.foregroundBusy && root.op1we.enrolled
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.requestReset()
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            TextField {
              id: restorePath
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(240)
              placeholderText: "/path/to/backup.json"
              font.family: root.fontFamily
              text: root.op1we ? root.op1we.lastBackupPath : ""
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: "Restore"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.op1we && !root.op1we.foregroundBusy && root.op1we.enrolled
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.requestRestore(restorePath.text)
            }
          }

          Text {
            visible: text !== ""
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: root.op1we && root.op1we.lastBackupPath !== ""
              ? "Latest backup: " + root.op1we.lastBackupPath : ""
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }



      }
    }
        // ---- footer: errors, notice, Apply/Cancel ----------------------------------
        Column {
          id: footer
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.bottom: parent.bottom
          spacing: Style.space(8)

          Repeater {
            model: root.op1we ? root.op1we.draftErrors : []
            delegate: Text {
              required property var modelData
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              width: form.width
              text: modelData.message || ""
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          Text {
            visible: text !== ""
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            width: parent.width
            text: root.op1we ? root.op1we.notice : ""
            color: root.op1we && root.op1we.noticeTone === "error" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            Text {
              id: unsavedLabel
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              visible: root.op1we && root.op1we.dirty
              text: "\u25CF Unsaved"
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Item {
              anchors.verticalCenter: parent.verticalCenter
              width: Math.max(0, parent.width - (unsavedLabel.visible ? unsavedLabel.implicitWidth : 0)
                - applyButton.implicitWidth - cancelButton.implicitWidth - parent.spacing * 2)
              height: 1
            }

            Button {
              id: applyButton
              anchors.verticalCenter: parent.verticalCenter
              text: "Apply"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              active: true
              enabled: root.op1we && root.op1we.dirty && !root.op1we.foregroundBusy
                && root.op1we.enrolled && root.op1we.snapshot
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.op1we.applyDraft()
            }

            Button {
              id: cancelButton
              anchors.verticalCenter: parent.verticalCenter
              text: "Cancel"
              focusable: true
              opacity: enabled ? 1 : 0.4
              bordered: true
              enabled: root.op1we && root.op1we.dirty && !root.op1we.foregroundBusy
              foreground: root.ink
              fontFamily: root.fontFamily
              onClicked: root.op1we.cancelDraft()
            }
          }

          Text {
            textFormat: Text.PlainText
            text: "Ctrl+Enter applies."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

    ConfirmDialog {
      id: closeDialog
      anchors.fill: parent
      message: "Discard unsaved edits?"
      cancelText: "Keep edits"
      confirmText: "Discard"
      onCanceled: {
        // Keep the draft, just close the panel.
        closeDialog.opened = false
        root.controller.hide()
      }
      onConfirmed: {
        closeDialog.opened = false
        if (root.op1we) root.op1we.cancelDraft()
        root.controller.hide()
      }
    }

    ConfirmDialog {
      id: actionDialog
      anchors.fill: parent
      cancelText: "Cancel"
      confirmText: "Confirm"
      onCanceled: {
        actionDialog.opened = false
        root.pendingAction = ""
      }
      onConfirmed: {
        actionDialog.opened = false
        root._runPendingAction()
      }
    }

    ConfirmDialog {
      id: enrollDialog
      anchors.fill: parent
      message: "Enroll this receiver? Only confirm when the mouse on it is the OP1we. Enrollment enables writes."
      cancelText: "Cancel"
      confirmText: "Enroll"
      onCanceled: enrollDialog.opened = false
      onConfirmed: {
        enrollDialog.opened = false
        if (root.op1we) root.op1we.enroll()
      }
    }
  }
}
