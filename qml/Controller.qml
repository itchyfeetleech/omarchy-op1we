import QtQuick
import Quickshell.Io
import "Model.js" as Model

// Async helper calls, refresh timers and draft/confirmed state shared by
// BarWidget (status) and Panel (settings form). All helper invocations are
// argv arrays; apply data goes over stdin as one JSON line. Reads that find
// a running helper are skipped, never queued; mutations refuse to overlap.
Item {
  id: root
  visible: false

  property QtObject bar: null
  property var settings: ({})
  property bool panelOpen: false

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  // <plugin>/qml/Controller.qml -> <plugin>; backend ships beside qml/.
  readonly property string pluginDir: decodeURIComponent(
    Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "").replace(/\/qml\/$/, ""))
  property string backendDir: String(setting("backendDir", pluginDir + "/backend"))

  // ---- observable state -------------------------------------------------
  property var status: null
  property var statusError: null
  property double statusAt: 0
  property var snapshot: null
  property var snapshotError: null
  property double snapshotAt: 0
  property var confirmed: null // draft-shaped, decoded from snapshot
  property var draft: null // user edits; survives panel close
  property bool dirty: false
  property var draftErrors: []
  property var profiles: []
  property bool enrolled: false
  property string notice: ""
  property string noticeTone: "info" // info | error
  property bool staleNotice: false
  property string lastBackupPath: ""
  property string recoveryBackup: ""
  property bool busy: statusProc.running || readProc.running
    || queryProc.running || mutateProc.running
  // Battery polling must not hide banners, shift the form or disable
  // actions every two seconds. Only foreground work affects the panel.
  property bool foregroundBusy: readProc.running || queryProc.running || mutateProc.running
  property bool mutating: mutateProc.running

  signal snapshotUpdated()
  signal statusUpdated()
  signal applied()
  signal mutationFailed(var error)

  property int _seq: 0
  property var _applyBase: null

  function _requestId() {
    _seq += 1
    return Date.now().toString(36) + "-" + _seq
  }

  function _helperArgs(cmd) {
    var args = ["python3", "-B", "-m", "op1we"].concat(cmd)
    return args.concat(["--request-id", _requestId()])
  }

  // ---- refresh ----------------------------------------------------------

  function refreshStatus() {
    if (busy) return false
    statusProc.command = _helperArgs(["status"])
    statusProc.running = true
    return true
  }

  // Full configuration read. Interaction-driven only (open/hover/after a
  // write); never on the fast timer, so EEPROM is not polled continuously.
  // force=false (hover path) skips when the last read is under 10 s old.
  function refreshSnapshot(force) {
    if (readProc.running) return false
    if (force !== true && snapshotAt > 0 && Date.now() - snapshotAt < 10000) return false
    readProc.command = _helperArgs(["read"])
    readProc.running = true
    return true
  }

  function refreshProfiles() {
    return _runQuery("profile-list", ["profile", "list"])
  }

  Timer {
    id: statusTimer
    interval: 30000
    repeat: true
    running: true
    triggeredOnStart: false
    onTriggered: root.refreshStatus()
  }

  Timer {
    id: openTimer
    interval: 2000
    repeat: true
    running: root.panelOpen
    onTriggered: root.refreshStatus()
  }

  Component.onCompleted: refreshStatus()

  // ---- draft ------------------------------------------------------------

  function touchDraft() {
    draft = Model.clone(draft)
  }

  function cancelDraft() {
    if (!confirmed) return
    draft = Model.clone(confirmed)
    draftErrors = []
    staleNotice = false
    setNotice("", "info")
  }

  function setNotice(text, tone) {
    notice = String(text || "")
    noticeTone = tone === "error" ? "error" : "info"
  }

  function clearRecovery() {
    recoveryBackup = ""
  }

  onSnapshotChanged: {
    if (!snapshot) {
      confirmed = null
      return
    }
    confirmed = Model.draftFromSnapshot(snapshot)
    // Adopt fresh state unless the user is mid-edit.
    if (!draft || !dirty) {
      draft = Model.clone(confirmed)
      draftErrors = []
    }
    dirty = draft ? !Model.draftsEqual(confirmed, draft) : false
  }

  onDraftChanged: {
    dirty = confirmed && draft ? !Model.draftsEqual(confirmed, draft) : false
  }

  // ---- mutations (single-flight) -----------------------------------------

  function _mutationStarting() {
    if (mutateProc.running || queryProc.running) {
      setNotice("Another operation is running; retry in a moment.", "error")
      return false
    }
    return true
  }

  function applyDraft() {
    if (!confirmed || !draft) return false
    if (!_mutationStarting()) return false
    var diff = Model.diffToChanges(confirmed, draft)
    draftErrors = diff.errors
    if (diff.errors.length > 0) {
      setNotice("Fix the highlighted fields before applying.", "error")
      return false
    }
    if (Model.isNoopChanges(diff.changes)) {
      setNotice("No changes to apply.", "info")
      return false
    }
    _applyBase = Model.clone(draft)
    staleNotice = false
    recoveryBackup = ""
    mutateProc.tag = "apply"
    mutateProc.stdinText = Model.buildApplyRequest(
      _requestId(), draft.revision, draft.fingerprint, diff.changes) + "\n"
    mutateProc.command = _helperArgs(["apply"])
    mutateProc.running = true
    return true
  }

  function _revisionArgs(cmd) {
    if (snapshot && snapshot.revision)
      return cmd.concat(["--expected-revision", String(snapshot.revision)])
    return cmd
  }

  function resetDevice() {
    if (!_mutationStarting()) return false
    mutateProc.tag = "reset"
    mutateProc.stdinText = ""
    mutateProc.command = _helperArgs(_revisionArgs(["reset"]))
    mutateProc.running = true
    return true
  }

  function restoreBackup(path) {
    if (!_mutationStarting()) return false
    var file = String(path || "").trim()
    if (file === "") {
      setNotice("Pick a backup file to restore.", "error")
      return false
    }
    recoveryBackup = ""
    mutateProc.tag = "restore"
    mutateProc.stdinText = ""
    mutateProc.command = _helperArgs(_revisionArgs(["restore", "--file", file]))
    mutateProc.running = true
    return true
  }

  function applyProfile(name) {
    if (!_mutationStarting()) return false
    var profile = String(name || "")
    if (profile === "") {
      setNotice("Pick a profile to apply.", "error")
      return false
    }
    mutateProc.tag = "profile-apply"
    mutateProc.stdinText = ""
    mutateProc.command = _helperArgs(_revisionArgs(["profile", "apply", "--name", profile]))
    mutateProc.running = true
    return true
  }

  // ---- queries (single-flight) --------------------------------------------

  function _runQuery(tag, cmd, stdinText) {
    if (queryProc.running || mutateProc.running) return false
    queryProc.tag = tag
    queryProc.stdinText = stdinText || ""
    queryProc.command = _helperArgs(cmd)
    queryProc.running = true
    return true
  }

  function backupNow() {
    return _runQuery("backup", ["backup"])
  }

  function enroll() {
    return _runQuery("enroll", ["enroll", "--confirm"])
  }

  function saveProfile(name) {
    var err = Model.validateProfileName(String(name || ""))
    if (err !== "") {
      setNotice(err, "error")
      return false
    }
    return _runQuery("profile-save", ["profile", "save", "--name", String(name)])
  }

  function deleteProfile(name) {
    return _runQuery("profile-delete", ["profile", "delete", "--name", String(name || "")])
  }

  function exportProfile(name, file) {
    if (String(file || "").trim() === "") {
      setNotice("Pick a destination file for the export.", "error")
      return false
    }
    return _runQuery("profile-export",
      ["profile", "export", "--name", String(name || ""), "--file", String(file).trim()])
  }

  function importProfile(name, file) {
    if (String(file || "").trim() === "") {
      setNotice("Pick a profile file to import.", "error")
      return false
    }
    var cmd = ["profile", "import", "--file", String(file).trim()]
    if (String(name || "") !== "") cmd = cmd.concat(["--name", String(name)])
    return _runQuery("profile-import", cmd)
  }

  // ---- result handling -----------------------------------------------------

  function _describeFailure(tag, output, exitCode) {
    var doc = Model.parseEnvelope(output)
    if (!doc.ok && doc.error && doc.error.code === "bad-envelope") {
      var detail = output.trim() === ""
        ? "Helper produced no output (exit " + exitCode + "). Is python3 installed?"
        : "Helper output was not valid JSON (exit " + exitCode + ")."
      return { code: "bad-envelope", message: detail, retryable: false }
    }
    return doc.error || { code: "internal", message: "Helper failed", retryable: false }
  }

  function _adoptStatus(data) {
    var prev = status ? String(status.connection || "") : ""
    status = data
    statusError = null
    statusAt = Date.now()
    enrolled = data && data.enrolled === true
    statusUpdated()
    // Reconnect: re-read configuration when the panel is watching.
    if (data && String(data.connection || "") === "connected"
        && prev !== "" && prev !== "connected" && panelOpen) {
      refreshSnapshot(true)
    }
  }

  function _onStatusFinished(output, exitCode) {
    var doc = Model.parseEnvelope(output)
    if (doc.ok) {
      _adoptStatus(doc.data)
      return
    }
    // "busy" means another helper (or monitor) holds the lock: keep the
    // last status and retry on the next tick, without an error banner.
    if (doc.error && doc.error.code === "busy") return
    statusError = _describeFailure("status", output, exitCode)
  }

  function _onReadFinished(output, exitCode) {
    var doc = Model.parseEnvelope(output)
    if (doc.ok) {
      snapshot = doc.data
      snapshotError = null
      snapshotAt = Date.now()
      snapshotUpdated()
      return
    }
    if (doc.error && doc.error.code === "busy") return
    snapshotError = _describeFailure("read", output, exitCode)
  }

  function _onQueryFinished(tag, output, exitCode) {
    var doc = Model.parseEnvelope(output)
    if (!doc.ok) {
      // Reads skipped on lock contention stay silent (see status path).
      if (doc.error && doc.error.code === "busy") {
        if (tag === "profile-list") return
      }
      var failure = _describeFailure(tag, output, exitCode)
      // Enrollment/profile errors surface in the panel notice area.
      if (tag === "enroll" || tag.indexOf("profile") === 0 || tag === "backup")
        setNotice(Model.errorText(failure), "error")
      return
    }
    var data = doc.data || {}
    if (tag === "backup") {
      lastBackupPath = String(data.path || "")
      setNotice("Backup saved: " + lastBackupPath, "info")
    } else if (tag === "enroll") {
      setNotice("Receiver enrolled. Writes are now enabled.", "info")
      refreshStatus()
    } else if (tag === "profile-list") {
      profiles = data.profiles instanceof Array ? data.profiles : []
    } else if (tag === "profile-save") {
      setNotice("Profile saved: " + String(data.name || ""), "info")
      refreshProfiles()
    } else if (tag === "profile-delete") {
      setNotice("Profile deleted.", "info")
      refreshProfiles()
    } else if (tag === "profile-export") {
      setNotice("Profile exported: " + String(data.file || ""), "info")
    } else if (tag === "profile-import") {
      setNotice("Profile imported: " + String(data.imported || ""), "info")
      refreshProfiles()
    }
  }

  function _onMutateFinished(tag, output, exitCode) {
    var doc = Model.parseEnvelope(output)
    if (!doc.ok) {
      var failure = doc.error || _describeFailure(tag, output, exitCode)
      if (failure.code === "bad-envelope") failure = _describeFailure(tag, output, exitCode)
      if (failure.detail && failure.detail.backup)
        recoveryBackup = String(failure.detail.backup)
      if (failure.code === "stale-revision") {
        // Never auto-retry a write: re-read and let the user review.
        staleNotice = true
        setNotice(Model.errorText(failure), "error")
        refreshSnapshot(true)
        refreshStatus()
        mutationFailed(failure)
        return
      }
      setNotice(Model.errorText(failure)
        + (recoveryBackup !== "" ? " Recovery backup: " + recoveryBackup : ""), "error")
      refreshSnapshot(true)
      refreshStatus()
      mutationFailed(failure)
      return
    }
    var data = doc.data || {}
    if (tag === "apply") {
      var changed = data.applied === true
      // Adopt the verified state unless the user edited mid-flight.
      if (data.snapshot) {
        snapshot = data.snapshot
        snapshotAt = Date.now()
        snapshotUpdated()
      }
      if (changed) {
        if (Model.draftsEqual(draft, _applyBase)) draft = Model.clone(confirmed)
        draftErrors = []
        staleNotice = false
        setNotice("Applied and verified.", "info")
      } else {
        setNotice("No changes to apply.", "info")
      }
      applied()
    } else if (tag === "reset") {
      if (data.snapshot) {
        snapshot = data.snapshot
        snapshotAt = Date.now()
        snapshotUpdated()
      }
      draft = confirmed ? Model.clone(confirmed) : draft
      draftErrors = []
      setNotice(data.reset === true ? "Reset to vendor defaults." : "Already at defaults.", "info")
    } else if (tag === "restore") {
      if (data.snapshot) {
        snapshot = data.snapshot
        snapshotAt = Date.now()
        snapshotUpdated()
      }
      draft = confirmed ? Model.clone(confirmed) : draft
      draftErrors = []
      recoveryBackup = ""
      setNotice(data.restored === true ? "Backup restored and verified." : "Already matches the backup.", "info")
    } else if (tag === "profile-apply") {
      if (data.snapshot) {
        snapshot = data.snapshot
        snapshotAt = Date.now()
        snapshotUpdated()
      }
      draft = confirmed ? Model.clone(confirmed) : draft
      draftErrors = []
      setNotice("Profile applied and verified.", "info")
    }
    _applyBase = null
    refreshStatus()
  }

  // One short-lived helper invocation. stdout is collected whole; the
  // finished signal carries tag + output + exit code for dispatch.
  component HelperProc: Process {
    id: proc
    property string tag: ""
    property string stdinText: ""
    property string helperDir: ""
    property string collected: ""
    property bool gotOutput: false
    signal finished(string tag, string output, int exitCode)

    workingDirectory: proc.helperDir
    stdinEnabled: true

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        proc.collected = text
        proc.gotOutput = true
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
    onStarted: {
      proc.collected = ""
      proc.gotOutput = false
      if (proc.stdinText !== "") proc.write(proc.stdinText)
    }
    onExited: function(exitCode) {
      proc.finished(proc.tag, proc.gotOutput ? proc.collected : "", exitCode)
    }
  }

  HelperProc {
    id: statusProc
    helperDir: root.backendDir
    onFinished: function(tag, output, exitCode) { root._onStatusFinished(output, exitCode) }
  }
  HelperProc {
    id: readProc
    helperDir: root.backendDir
    onFinished: function(tag, output, exitCode) { root._onReadFinished(output, exitCode) }
  }
  HelperProc {
    id: queryProc
    helperDir: root.backendDir
    onFinished: function(tag, output, exitCode) { root._onQueryFinished(tag, output, exitCode) }
  }
  HelperProc {
    id: mutateProc
    helperDir: root.backendDir
    onFinished: function(tag, output, exitCode) { root._onMutateFinished(tag, output, exitCode) }
  }
}
