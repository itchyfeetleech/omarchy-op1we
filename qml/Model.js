// OP1we Control UI model: envelope parsing, draft/diff/validation and labels.
//
// Pure functions shared by BarWidget, Panel and Controller. No Qt imports,
// so the same file runs under Node's test runner (see tests/model.test.cjs).
// Limits mirror backend/op1we/protocol.py exactly; the helper stays
// authoritative and re-validates everything before writing.

var API_VERSION = 1;

var POLLING_RATES = [125, 250, 500, 1000];
var CPI_MIN = 50;
var CPI_MAX = 10000; // Above-knee packing is unproven; never constructed.
var CPI_STEP = 50;
var CPI_STAGES = 4;
var DEBOUNCE_MIN = 0;
var DEBOUNCE_MAX = 30;
var SLEEP_MAX = 2550;
var SLEEP_STEP = 10;
var UI_SLOTS = 12; // Vendor KM=12; slots 13-16 are preserved, never edited.
var KEY_USAGE_MIN = 0x04;
var KEY_USAGE_MAX = 0x65;
var MOD_MASK = 0x0f;

var MOUSE_BUTTONS = ["left", "right", "middle", "back", "forward"];
var MOD_NAMES = ["Ctrl", "Shift", "Alt", "Win"];

// Vendor 18-entry consumer-page table (protocol.MEDIA_NAMES), pretty labels.
var MEDIA_OPTIONS = [
  { usage: 0xCD, name: "play-pause", label: "Play/Pause" },
  { usage: 0xB7, name: "stop", label: "Stop" },
  { usage: 0xB6, name: "previous", label: "Previous track" },
  { usage: 0xB5, name: "next", label: "Next track" },
  { usage: 0xE9, name: "volume-up", label: "Volume up" },
  { usage: 0xEA, name: "volume-down", label: "Volume down" },
  { usage: 0xE2, name: "mute", label: "Mute" },
  { usage: 0x183, name: "media-select", label: "Media select" },
  { usage: 0x18A, name: "mail", label: "Mail" },
  { usage: 0x192, name: "calculator", label: "Calculator" },
  { usage: 0x194, name: "browser", label: "Browser" },
  { usage: 0x221, name: "ac-search", label: "Search" },
  { usage: 0x223, name: "ac-home", label: "Home" },
  { usage: 0x224, name: "ac-back", label: "Back" },
  { usage: 0x225, name: "ac-forward", label: "Forward" },
  { usage: 0x226, name: "ac-stop", label: "Stop (browser)" },
  { usage: 0x227, name: "ac-refresh", label: "Refresh" },
  { usage: 0x22A, name: "ac-favorites", label: "Favorites" }
];

var ACTION_KINDS = [
  { value: "unassigned", label: "Unassigned" },
  { value: "mouse", label: "Mouse click" },
  { value: "key", label: "Single key" },
  { value: "combo", label: "Key combination" },
  { value: "media", label: "Media key" },
  { value: "dpi-toggle", label: "DPI cycle" },
  { value: "dpi-plus", label: "DPI +" },
  { value: "dpi-minus", label: "DPI \u2212" },
  { value: "polling-switch", label: "Polling switch" }
];

// Draft sentinel for a confirmed binding the backend cannot construct
// (special4/8/9, macro, unknown, undecodable payload): kept verbatim,
// never sent. Selecting it back means "no change".
var KEEP_KIND = "keep";

function isInt(value) {
  return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value === undefined ? null : value));
}

// ---------------------------------------------------------------- envelope

// Normalize one helper stdout document. Never throws: malformed output
// becomes a bad-envelope error so the UI can show it instead of crashing.
function parseEnvelope(raw) {
  var bad = function (message) {
    return { ok: false, data: null, requestId: null,
             error: { code: "bad-envelope", message: message, retryable: false } };
  };
  var text = String(raw === undefined || raw === null ? "" : raw).trim();
  if (text === "") return bad("Helper returned no output");
  var doc;
  try {
    doc = JSON.parse(text);
  } catch (e) {
    return bad("Helper returned invalid JSON");
  }
  if (!doc || typeof doc !== "object") return bad("Helper returned invalid JSON");
  if (doc.apiVersion !== API_VERSION) return bad("Helper API version mismatch");
  if (typeof doc.ok !== "boolean") return bad("Helper response missing ok flag");
  return { ok: doc.ok, data: doc.data === undefined ? null : doc.data,
           error: doc.error === undefined ? null : doc.error,
           requestId: doc.requestId === undefined ? null : doc.requestId };
}

// ---------------------------------------------------------------- status

function connectionMeta(status) {
  var connection = status ? status.connection : null;
  if (connection === "connected") return { label: "Connected", tone: "ok" };
  if (connection === "receiver-only") return { label: "Receiver only", tone: "warn" };
  if (connection === "unavailable") return { label: "Unavailable", tone: "muted" };
  return { label: "Unknown", tone: "muted" };
}

function batteryText(status) {
  if (!status || status.batteryPercent === null || status.batteryPercent === undefined)
    return "Battery unknown";
  var text = String(status.batteryPercent) + "%";
  if (status.batteryFresh === false) text += " (last known)";
  if (status.charging === 1) text += ", charging";
  return text;
}

// Single-line bar tooltip: mouse name, battery/freshness, current DPI.
function tooltipFor(status, snapshot) {
  if (!status) return "OP1we";
  var parts = ["OP1we"];
  var meta = connectionMeta(status);
  if (status.connection === "connected") {
    parts.push(batteryText(status));
    var dpi = snapshot ? snapshot.currentDpi : null;
    parts.push(dpi === null || dpi === undefined ? "DPI unknown" : String(dpi) + " DPI");
  } else if (status.connection === "receiver-only") {
    if (status.batteryPercent !== null && status.batteryPercent !== undefined)
      parts.push(batteryText(status));
    parts.push(meta.label + " \u2014 move the mouse");
  } else if (status.connection === "unavailable") {
    parts.push("unavailable \u2014 plug in the receiver");
  } else {
    parts.push(meta.label);
  }
  return parts.join(" \u00B7 ");
}

// ---------------------------------------------------------------- draft

// Normalize a read snapshot into editable draft shape. editable[cpi] holds
// numbers; unencodable stages keep their confirmed value as null with the
// raw record retained for display. keys maps UI slots to draft actions.
function draftFromSnapshot(snapshot) {
  var draft = {
    revision: snapshot.revision,
    fingerprint: snapshot.fingerprint,
    pollingHz: snapshot.pollingHz,
    cpi: [null, null, null, null],
    cpiEncodable: [true, true, true, true],
    debounceMs: snapshot.debounceMs,
    sleepS: snapshot.sleepS,
    ripple: snapshot.ripple,
    fixline: snapshot.fixline,
    turnOffLight: snapshot.turnOffLight,
    keys: {}
  };
  var stages = snapshot.stages || [];
  for (var i = 0; i < CPI_STAGES; i++) {
    var stage = null;
    for (var s = 0; s < stages.length; s++) {
      if (stages[s] && stages[s].index === i) { stage = stages[s]; break; }
    }
    if (stage && stage.encodable !== false && isInt(stage.x)) {
      draft.cpi[i] = stage.x;
    } else {
      draft.cpi[i] = null;
      draft.cpiEncodable[i] = false;
    }
  }
  var bindings = snapshot.bindings || [];
  for (var slot = 1; slot <= UI_SLOTS; slot++) {
    var found = null;
    for (var b = 0; b < bindings.length; b++) {
      if (bindings[b] && bindings[b].slot === slot) { found = bindings[b]; break; }
    }
    draft.keys[String(slot)] = draftActionFromBinding(found);
  }
  return draft;
}

function draftActionFromBinding(binding) {
  if (!binding || !binding.action) return { kind: KEEP_KIND };
  var action = binding.action;
  var kind = action.kind;
  if (kind === "unassigned") return { kind: "unassigned" };
  if (kind === "mouse") return { kind: "mouse", buttons: (action.buttons || []).slice() };
  if (kind === "dpi-toggle" || kind === "dpi-plus" || kind === "dpi-minus"
      || kind === "polling-switch") return { kind: kind };
  if (kind === "key-ref") {
    var payload = binding.payload || {};
    if (payload.kind === "key") return { kind: "key", keys: (payload.keys || []).slice() };
    if (payload.kind === "combo")
      return { kind: "combo", keys: (payload.keys || []).slice(),
               modifiers: payload.modifiers || 0 };
    if (payload.kind === "media") return { kind: "media", usage: payload.usage };
    return { kind: KEEP_KIND };
  }
  return { kind: KEEP_KIND };
}

function actionsEqual(a, b) {
  return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
}

function draftsEqual(a, b) {
  if (!a || !b) return false;
  var fields = ["pollingHz", "debounceMs", "sleepS", "ripple", "fixline",
                "turnOffLight"];
  for (var i = 0; i < fields.length; i++) {
    if (!actionsEqual(a[fields[i]], b[fields[i]])) return false;
  }
  if (!actionsEqual(a.cpi, b.cpi)) return false;
  for (var slot = 1; slot <= UI_SLOTS; slot++) {
    var key = String(slot);
    if (!actionsEqual((a.keys || {})[key], (b.keys || {})[key])) return false;
  }
  return true;
}

// ---------------------------------------------------------------- validation
// Each validator returns "" when valid, else a user-facing message.

function validatePolling(value) {
  if (!isInt(value) || POLLING_RATES.indexOf(value) === -1)
    return "Polling must be one of 125, 250, 500, 1000 Hz";
  return "";
}

function validateCpi(value) {
  if (!isInt(value)) return "DPI must be a whole number";
  if (value >= 10100 && value <= 19000 && (value - 10100) % 100 === 0)
    return "DPI above 10000 needs unproven multiplier packing (preserved, not editable)";
  if (value < CPI_MIN || value > CPI_MAX || (value - CPI_MIN) % CPI_STEP !== 0)
    return "DPI must be 50\u201310000 in steps of 50";
  return "";
}

function validateDebounce(value) {
  if (!isInt(value) || value < DEBOUNCE_MIN || value > DEBOUNCE_MAX)
    return "Debounce must be 0\u201330 ms";
  return "";
}

function validateSleep(value) {
  if (!isInt(value) || value < 0 || value > SLEEP_MAX || value % SLEEP_STEP !== 0)
    return "Sleep must be 0\u20132550 s in steps of 10";
  return "";
}

function validateBool(value, label) {
  if (typeof value !== "boolean") return label + " must be on or off";
  return "";
}

function validateMouseButtons(buttons) {
  if (!(buttons instanceof Array) || buttons.length === 0)
    return "Pick at least one mouse button";
  for (var i = 0; i < buttons.length; i++) {
    if (MOUSE_BUTTONS.indexOf(buttons[i]) === -1)
      return "Unknown mouse button: " + String(buttons[i]);
  }
  return "";
}

function validateKeyUsage(usage) {
  if (!isInt(usage) || usage < KEY_USAGE_MIN || usage > KEY_USAGE_MAX)
    return "Key must be a HID usage 0x04\u20130x65";
  return "";
}

function validateCombo(keys, modifiers) {
  if (!(keys instanceof Array) || keys.length < 1 || keys.length > 3)
    return "A combination holds 1\u20133 keys";
  for (var i = 0; i < keys.length; i++) {
    var err = validateKeyUsage(keys[i]);
    if (err !== "") return err;
  }
  if (!isInt(modifiers) || modifiers < 0 || modifiers > MOD_MASK)
    return "Modifiers must be 0\u201315";
  return "";
}

function mediaUsageByValue(value) {
  for (var i = 0; i < MEDIA_OPTIONS.length; i++) {
    if (MEDIA_OPTIONS[i].usage === value) return MEDIA_OPTIONS[i];
  }
  return null;
}

function mediaUsageByName(name) {
  for (var i = 0; i < MEDIA_OPTIONS.length; i++) {
    if (MEDIA_OPTIONS[i].name === name) return MEDIA_OPTIONS[i];
  }
  return null;
}

function validateMediaUsage(usage) {
  var resolved = usage;
  if (typeof usage === "string") {
    var entry = mediaUsageByName(usage);
    resolved = entry ? entry.usage : -1;
  }
  if (!isInt(resolved) || !mediaUsageByValue(resolved))
    return "Pick a media key from the vendor list";
  return "";
}

// Validate one draft action for a UI slot. "keep" is always valid (no-op).
function validateKeyAction(slot, action) {
  if (!isInt(slot) || slot < 1 || slot > UI_SLOTS)
    return "Slot " + String(slot) + " is outside 1\u2013" + String(UI_SLOTS);
  if (!action || typeof action !== "object") return "Slot " + slot + ": action missing";
  var kind = action.kind;
  if (kind === KEEP_KIND || kind === "unassigned") return "";
  if (kind === "mouse") {
    var err = validateMouseButtons(action.buttons);
    return err === "" ? "" : "Slot " + slot + ": " + err;
  }
  if (kind === "key") {
    if (!(action.keys instanceof Array) || action.keys.length !== 1)
      return "Slot " + slot + ": a single key holds exactly 1 key";
    var keyErr = validateKeyUsage(action.keys[0]);
    return keyErr === "" ? "" : "Slot " + slot + ": " + keyErr;
  }
  if (kind === "combo") {
    var comboErr = validateCombo(action.keys, action.modifiers === undefined ? 0 : action.modifiers);
    return comboErr === "" ? "" : "Slot " + slot + ": " + comboErr;
  }
  if (kind === "media") {
    var mediaErr = validateMediaUsage(action.usage);
    return mediaErr === "" ? "" : "Slot " + slot + ": " + mediaErr;
  }
  if (kind === "dpi-toggle" || kind === "dpi-plus" || kind === "dpi-minus"
      || kind === "polling-switch") return "";
  return "Slot " + slot + ": unknown action " + JSON.stringify(kind);
}

function validateProfileName(name) {
  if (typeof name !== "string" || name.length < 1 || name.length > 64)
    return "Profile name must be 1\u201364 characters";
  if (!/^[A-Za-z0-9._-]+$/.test(name))
    return "Profile name may only use letters, digits, . _ -";
  return "";
}

// ---------------------------------------------------------------- diff

function finalKeyMap(confirmed, draft) {
  var merged = {};
  for (var slot = 1; slot <= UI_SLOTS; slot++) {
    var key = String(slot);
    var action = (draft.keys || {})[key];
    if (!action || action.kind === KEEP_KIND) action = (confirmed.keys || {})[key];
    merged[key] = action;
  }
  return merged;
}

function finalMapHasLeftClick(keyMap) {
  for (var slot = 1; slot <= UI_SLOTS; slot++) {
    var action = keyMap[String(slot)];
    if (action && action.kind === "mouse"
        && (action.buttons || []).indexOf("left") !== -1) return true;
  }
  return false;
}

// Build the backend changes object for fields that differ. Returns
// { changes, errors }: errors is [{field, message}]; callers must send
// nothing when it is non-empty. "keep" actions and equal fields are
// omitted; unencodable CPI stages can never differ (null draft).
function diffToChanges(confirmed, draft) {
  var errors = [];
  var changes = {};
  if (!confirmed || !draft) return { changes: changes, errors: errors };

  if (!actionsEqual(confirmed.pollingHz, draft.pollingHz)) {
    var pollingErr = validatePolling(draft.pollingHz);
    if (pollingErr !== "") errors.push({ field: "pollingHz", message: pollingErr });
    else changes.pollingHz = draft.pollingHz;
  }
  var cpi = [null, null, null, null];
  var cpiTouched = false;
  for (var i = 0; i < CPI_STAGES; i++) {
    var before = (confirmed.cpi || [])[i];
    var after = (draft.cpi || [])[i];
    if (actionsEqual(before, after)) continue;
    if ((confirmed.cpiEncodable || [])[i] === false || after === null) {
      errors.push({ field: "cpi" + (i + 1),
                    message: "Stage " + (i + 1) + " holds an above-knee value (preserved, not editable)" });
      continue;
    }
    var cpiErr = validateCpi(after);
    if (cpiErr !== "") {
      errors.push({ field: "cpi" + (i + 1), message: "Stage " + (i + 1) + ": " + cpiErr });
      continue;
    }
    cpi[i] = after;
    cpiTouched = true;
  }
  if (cpiTouched) changes.cpi = cpi;
  if (!actionsEqual(confirmed.debounceMs, draft.debounceMs)) {
    var debounceErr = validateDebounce(draft.debounceMs);
    if (debounceErr !== "") errors.push({ field: "debounceMs", message: debounceErr });
    else changes.debounceMs = draft.debounceMs;
  }
  if (!actionsEqual(confirmed.sleepS, draft.sleepS)) {
    var sleepErr = validateSleep(draft.sleepS);
    if (sleepErr !== "") errors.push({ field: "sleepS", message: sleepErr });
    else changes.sleepS = draft.sleepS;
  }
  var flags = [["ripple", "Ripple control"], ["fixline", "Angle snapping"],
               ["turnOffLight", "Turn off light while moving"]];
  for (var f = 0; f < flags.length; f++) {
    var field = flags[f][0];
    if (actionsEqual(confirmed[field], draft[field])) continue;
    var flagErr = validateBool(draft[field], flags[f][1]);
    if (flagErr !== "") errors.push({ field: field, message: flagErr });
    else changes[field] = draft[field];
  }
  var keyEntries = [];
  for (var slot = 1; slot <= UI_SLOTS; slot++) {
    var key = String(slot);
    var beforeAction = (confirmed.keys || {})[key];
    var afterAction = (draft.keys || {})[key];
    if (actionsEqual(beforeAction, afterAction)) continue;
    if (!afterAction || afterAction.kind === KEEP_KIND) continue;
    var actionErr = validateKeyAction(slot, afterAction);
    if (actionErr !== "") {
      errors.push({ field: "keys" + slot, message: actionErr });
      continue;
    }
    keyEntries.push({ slot: slot, action: clone(afterAction) });
  }
  if (keyEntries.length > 0) {
    if (!finalMapHasLeftClick(finalKeyMap(confirmed, draft)))
      errors.push({ field: "keys",
                    message: "At least one button must stay bound to left-click" });
    else changes.keys = keyEntries;
  }
  return { changes: changes, errors: errors };
}

function isNoopChanges(changes) {
  return !changes || Object.keys(changes).length === 0;
}

function buildApplyRequest(requestId, revision, fingerprint, changes) {
  return JSON.stringify({ apiVersion: API_VERSION, requestId: requestId,
                          expectedRevision: revision,
                          deviceFingerprint: fingerprint, changes: changes });
}

// ---------------------------------------------------------------- labels

function capitalize(word) {
  var s = String(word || "");
  return s === "" ? s : s.charAt(0).toUpperCase() + s.slice(1);
}

function hexByte(value) {
  var s = Number(value).toString(16).toUpperCase();
  return "0x" + (s.length < 2 ? "0" + s : s);
}

// USB HID keyboard-page names for common usages; unknown ones keep hex.
var KEY_USAGE_NAMES = {
  0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab",
  0x2C: "Space", 0x39: "Caps Lock",
  0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4",
  0x3E: "F5", 0x3F: "F6", 0x40: "F7", 0x41: "F8",
  0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12",
  0x46: "Print Screen", 0x47: "Scroll Lock", 0x48: "Pause",
  0x49: "Insert", 0x4A: "Home", 0x4B: "Page Up",
  0x4C: "Delete", 0x4D: "End", 0x4E: "Page Down",
  0x4F: "Right", 0x50: "Left", 0x51: "Down", 0x52: "Up",
  0x53: "Num Lock", 0x65: "Menu"
};

function keyUsageName(usage) {
  if (KEY_USAGE_NAMES[usage] !== undefined) return KEY_USAGE_NAMES[usage];
  if (isInt(usage) && usage >= 0x04 && usage <= 0x1D)
    return String.fromCharCode("A".charCodeAt(0) + usage - 0x04);
  if (isInt(usage) && usage >= 0x1E && usage <= 0x26)
    return String.fromCharCode("1".charCodeAt(0) + usage - 0x1E);
  if (usage === 0x27) return "0";
  return hexByte(usage);
}

function modifierNames(modifiers) {
  var names = [];
  for (var bit = 0; bit < 4; bit++) {
    if ((modifiers || 0) & (1 << bit)) names.push(MOD_NAMES[bit]);
  }
  return names;
}

// Human label for a draft action (kind "keep" means unchanged).
function actionLabel(action) {
  if (!action || typeof action !== "object") return "Unknown (preserved)";
  var kind = action.kind;
  if (kind === KEEP_KIND) return "Keep current";
  if (kind === "unassigned") return "Unassigned";
  if (kind === "mouse")
    return "Click: " + (action.buttons || []).map(capitalize).join("+");
  if (kind === "key") return "Key " + keyUsageName((action.keys || [])[0]);
  if (kind === "combo") {
    var parts = modifierNames(action.modifiers).concat(
      (action.keys || []).map(keyUsageName));
    return "Combo " + parts.join("+");
  }
  if (kind === "media") {
    var entry = mediaUsageByValue(action.usage);
    if (!entry && typeof action.usage === "string") entry = mediaUsageByName(action.usage);
    return "Media " + (entry ? entry.label : hexByte(action.usage));
  }
  if (kind === "dpi-toggle") return "DPI cycle";
  if (kind === "dpi-plus") return "DPI +";
  if (kind === "dpi-minus") return "DPI \u2212";
  if (kind === "polling-switch") return "Polling switch";
  return String(kind) + " (preserved)";
}

// Human label for a confirmed device binding (snapshot shape).
function bindingLabel(binding) {
  if (!binding || !binding.action) return "Unknown (preserved)";
  var action = binding.action;
  var kind = action.kind;
  if (kind === "unassigned") return "Unassigned";
  if (kind === "mouse") return "Click: " + (action.buttons || []).map(capitalize).join("+");
  if (kind === "dpi-toggle") return "DPI cycle";
  if (kind === "dpi-plus") return "DPI +";
  if (kind === "dpi-minus") return "DPI \u2212";
  if (kind === "polling-switch") return "Polling switch";
  if (kind === "key-ref") {
    var payload = binding.payload || {};
    if (payload.kind === "key") return "Key " + keyUsageName((payload.keys || [])[0]);
    if (payload.kind === "combo") {
      var parts = modifierNames(payload.modifiers).concat(
        (payload.keys || []).map(keyUsageName));
      return "Combo " + parts.join("+");
    }
    if (payload.kind === "media") {
      var entry = mediaUsageByValue(payload.usage);
      return "Media " + (entry ? entry.label : hexByte(payload.usage));
    }
    if (payload.kind === "empty") return "Key (no payload)";
    return "Key (unreadable payload, preserved)";
  }
  if (kind === "special4") return "Special 04 (preserved)";
  if (kind === "special8") return "Special 08 (preserved)";
  if (kind === "special9") return "Special 09 (preserved)";
  if (kind === "macro") return "Macro (preserved)";
  return "Unknown (preserved)";
}

// Vendor button names for slots 1-5; only slot 4 = Back is proven on this
// unit (docs/device.md), so the panel shows one shared footnote instead of
// asserting each mapping.
var SLOT_NAMES = { 1: "Left", 2: "Right", 3: "Middle", 4: "Back", 5: "Forward" };

function slotLabel(slot) {
  var name = SLOT_NAMES[slot];
  return name ? "Button " + slot + " \u00B7 " + name : "Button " + slot;
}

// ---------------------------------------------------------------- errors

var ERROR_HINTS = {
  "permission": "Install the udev rule, then replug the receiver.",
  "unavailable": "Plug in the receiver and retry.",
  "ambiguous-device": "Connect only one compatible device.",
  "not-enrolled": "Confirm the paired mouse is the OP1we, then enroll.",
  "device-changed": "Receiver changed; confirm the pairing and re-enroll.",
  "asleep": "Move the mouse and retry.",
  "mouse-offline": "Move or power on the mouse.",
  "timeout": "No reply; move the mouse and retry.",
  "busy": "Another operation is running; retry in a moment.",
  "stale-revision": "Settings changed on the device; review and apply again.",
  "invalid-input": "Check the highlighted fields.",
  "unsupported": "Not supported by this firmware; the field is preserved.",
  "write-failed": "State uncertain; restore the recovery backup.",
  "verify-failed": "State uncertain; restore the recovery backup.",
  "bad-envelope": "The helper misbehaved; retry."
};

function errorHint(code) {
  return ERROR_HINTS[code] || "Retry; check the receiver connection.";
}

function errorText(error) {
  if (!error) return "";
  var message = String(error.message || error.code || "Unknown error");
  var hint = errorHint(error.code);
  return hint ? message + " " + hint : message;
}

// Node export (QML's engine has no `module`, so this is a no-op there).
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    API_VERSION: API_VERSION,
    POLLING_RATES: POLLING_RATES,
    CPI_MIN: CPI_MIN, CPI_MAX: CPI_MAX, CPI_STEP: CPI_STEP,
    CPI_STAGES: CPI_STAGES,
    DEBOUNCE_MIN: DEBOUNCE_MIN, DEBOUNCE_MAX: DEBOUNCE_MAX,
    SLEEP_MAX: SLEEP_MAX, SLEEP_STEP: SLEEP_STEP,
    UI_SLOTS: UI_SLOTS,
    MOUSE_BUTTONS: MOUSE_BUTTONS, MOD_NAMES: MOD_NAMES,
    MEDIA_OPTIONS: MEDIA_OPTIONS, ACTION_KINDS: ACTION_KINDS,
    KEEP_KIND: KEEP_KIND,
    clone: clone,
    parseEnvelope: parseEnvelope,
    connectionMeta: connectionMeta,
    batteryText: batteryText,
    tooltipFor: tooltipFor,
    draftFromSnapshot: draftFromSnapshot,
    draftActionFromBinding: draftActionFromBinding,
    draftsEqual: draftsEqual,
    validatePolling: validatePolling,
    validateCpi: validateCpi,
    validateDebounce: validateDebounce,
    validateSleep: validateSleep,
    validateMouseButtons: validateMouseButtons,
    validateKeyUsage: validateKeyUsage,
    validateCombo: validateCombo,
    validateMediaUsage: validateMediaUsage,
    validateKeyAction: validateKeyAction,
    validateProfileName: validateProfileName,
    diffToChanges: diffToChanges,
    isNoopChanges: isNoopChanges,
    buildApplyRequest: buildApplyRequest,
    keyUsageName: keyUsageName,
    modifierNames: modifierNames,
    actionLabel: actionLabel,
    bindingLabel: bindingLabel,
    slotLabel: slotLabel,
    mediaUsageByValue: mediaUsageByValue,
    mediaUsageByName: mediaUsageByName,
    errorHint: errorHint,
    errorText: errorText
  };
}
