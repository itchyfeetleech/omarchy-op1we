// Hardware-free tests for qml/Model.js (Node built-in runner).
// Run: node --test tests/model.test.cjs

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const Model = require("../qml/Model.js");

function fakeSnapshot() {
  const bindings = [];
  const kinds = [
    { kind: "mouse", buttons: ["left"] },
    { kind: "mouse", buttons: ["right"] },
    { kind: "mouse", buttons: ["middle"] },
    { kind: "mouse", buttons: ["back"] },
    { kind: "mouse", buttons: ["forward"] },
    { kind: "dpi-toggle" },
    { kind: "special8", confirmed: false },
    { kind: "key-ref" },
    { kind: "unassigned" },
    { kind: "unassigned" },
    { kind: "polling-switch" },
    { kind: "macro" },
  ];
  for (let slot = 1; slot <= 12; slot++) {
    const action = { ...kinds[slot - 1], raw: "00000000", checksum_ok: true };
    let payload = null;
    if (slot === 8) payload = { kind: "key", keys: [0x04], modifiers: 0 };
    bindings.push({ slot, action, payload });
  }
  return {
    fingerprint: "3367:1961/bcd0101/descabc",
    revision: "r".repeat(32),
    pollingHz: 1000,
    debounceMs: 1,
    sleepS: 60,
    ripple: false,
    fixline: false,
    turnOffLight: true,
    profile: 1,
    stageCount: 4,
    currentStage: 2,
    currentDpi: 1600,
    stages: [
      { index: 0, x: 400, y: 400, raw: "", encodable: true },
      { index: 1, x: 800, y: 800, raw: "", encodable: true },
      { index: 2, x: 1600, y: 1600, raw: "", encodable: true },
      { index: 3, x: 3200, y: 3200, raw: "", encodable: true },
    ],
    bindings,
  };
}

describe("parseEnvelope", () => {
  it("passes through a valid success envelope", () => {
    const doc = Model.parseEnvelope('{"apiVersion":1,"requestId":"a","ok":true,"data":{"x":1},"error":null}');
    assert.equal(doc.ok, true);
    assert.deepEqual(doc.data, { x: 1 });
    assert.equal(doc.requestId, "a");
  });

  it("passes through a valid error envelope", () => {
    const doc = Model.parseEnvelope('{"apiVersion":1,"requestId":"b","ok":false,"data":null,"error":{"code":"asleep","message":"zzz","retryable":true}}');
    assert.equal(doc.ok, false);
    assert.equal(doc.error.code, "asleep");
  });

  for (const [name, raw] of [
    ["empty", ""],
    ["blank", "   "],
    ["not json", "not json"],
    ["wrong version", '{"apiVersion":2,"ok":true}'],
    ["missing ok", '{"apiVersion":1}'],
    ["non-object", "[1,2]"],
  ]) {
    it(`maps ${name} to bad-envelope without throwing`, () => {
      const doc = Model.parseEnvelope(raw);
      assert.equal(doc.ok, false);
      assert.equal(doc.error.code, "bad-envelope");
    });
  }
});

describe("status text", () => {
  it("labels connections", () => {
    assert.deepEqual(Model.connectionMeta({ connection: "connected" }), { label: "Connected", tone: "ok" });
    assert.deepEqual(Model.connectionMeta({ connection: "receiver-only" }).tone, "warn");
    assert.deepEqual(Model.connectionMeta({ connection: "unavailable" }).tone, "muted");
    assert.deepEqual(Model.connectionMeta(null).label, "Unknown");
  });

  it("marks stale battery as last known, never fresh", () => {
    assert.equal(Model.batteryText({ batteryPercent: 70, batteryFresh: true, charging: 0 }), "70%");
    assert.equal(Model.batteryText({ batteryPercent: 70, batteryFresh: false, charging: 0 }), "70% (last known)");
    assert.equal(Model.batteryText({ batteryPercent: 70, batteryFresh: true, charging: 1 }), "70%, charging");
    assert.equal(Model.batteryText({ batteryPercent: null }), "Battery unknown");
  });

  it("builds tooltips without fabricated values", () => {
    const connected = { connection: "connected", batteryPercent: 70, batteryFresh: true, charging: 0 };
    assert.equal(Model.tooltipFor(connected, { currentDpi: 1600 }), "OP1we \u00B7 70% \u00B7 1600 DPI");
    assert.equal(Model.tooltipFor(connected, { currentDpi: null }), "OP1we \u00B7 70% \u00B7 DPI unknown");
    const stale = { connection: "receiver-only", batteryPercent: 70, batteryFresh: false };
    assert.match(Model.tooltipFor(stale, null), /last known/);
    assert.match(Model.tooltipFor(stale, null), /Receiver only/);
    assert.match(Model.tooltipFor({ connection: "unavailable" }, null), /plug in the receiver/);
    assert.equal(Model.tooltipFor(null, null), "OP1we");
  });
});

describe("draft", () => {
  it("round-trips a snapshot with no dirty flag", () => {
    const draft = Model.draftFromSnapshot(fakeSnapshot());
    assert.deepEqual(draft.cpi, [400, 800, 1600, 3200]);
    assert.deepEqual(draft.keys["1"], { kind: "mouse", buttons: ["left"] });
    assert.deepEqual(draft.keys["8"], { kind: "key", keys: [0x04] });
    assert.deepEqual(draft.keys["7"], { kind: "keep" });
    assert.deepEqual(draft.keys["12"], { kind: "keep" });
    assert.equal(Model.draftsEqual(draft, Model.draftFromSnapshot(fakeSnapshot())), true);
  });

  it("marks unencodable stages preserved, never editable", () => {
    const snap = fakeSnapshot();
    snap.stages[1] = { index: 1, x: null, y: null, raw: "deadbeef", encodable: false };
    const draft = Model.draftFromSnapshot(snap);
    assert.equal(draft.cpi[1], null);
    assert.equal(draft.cpiEncodable[1], false);
  });

  it("detects edits", () => {
    const a = Model.draftFromSnapshot(fakeSnapshot());
    const b = Model.draftFromSnapshot(fakeSnapshot());
    assert.equal(Model.draftsEqual(a, b), true);
    b.pollingHz = 500;
    assert.equal(Model.draftsEqual(a, b), false);
  });
});

describe("validators mirror the backend", () => {
  it("polling allows only the four rates", () => {
    for (const hz of [125, 250, 500, 1000]) assert.equal(Model.validatePolling(hz), "");
    assert.notEqual(Model.validatePolling(2000), "");
    assert.notEqual(Model.validatePolling("500"), "");
  });

  it("CPI enforces the 10000 knee", () => {
    for (const v of [50, 400, 9950, 10000]) assert.equal(Model.validateCpi(v), "");
    for (const v of [0, 25, 75, 10025, 20000]) assert.notEqual(Model.validateCpi(v), "");
    assert.match(Model.validateCpi(10100), /multiplier/);
    assert.match(Model.validateCpi(19000), /multiplier/);
  });

  it("debounce and sleep ranges", () => {
    assert.equal(Model.validateDebounce(0), "");
    assert.equal(Model.validateDebounce(30), "");
    assert.notEqual(Model.validateDebounce(31), "");
    assert.notEqual(Model.validateDebounce(-1), "");
    assert.equal(Model.validateSleep(0), "");
    assert.equal(Model.validateSleep(2550), "");
    assert.notEqual(Model.validateSleep(2551), "");
    assert.notEqual(Model.validateSleep(61), "");
  });

  it("key actions", () => {
    assert.equal(Model.validateKeyAction(1, { kind: "mouse", buttons: ["left", "right"] }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "mouse", buttons: [] }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "mouse", buttons: ["nope"] }), "");
    assert.equal(Model.validateKeyAction(1, { kind: "key", keys: [0x04] }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "key", keys: [0x03] }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "key", keys: [0x04, 0x05] }), "");
    assert.equal(Model.validateKeyAction(1, { kind: "combo", keys: [0x04, 0x05], modifiers: 1 }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "combo", keys: [], modifiers: 0 }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "combo", keys: [1, 2, 3, 4], modifiers: 0 }), "");
    assert.equal(Model.validateKeyAction(1, { kind: "media", usage: 0xcd }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "media", usage: 0x01 }), "");
    assert.equal(Model.validateKeyAction(1, { kind: "keep" }), "");
    assert.notEqual(Model.validateKeyAction(13, { kind: "mouse", buttons: ["left"] }), "");
    assert.notEqual(Model.validateKeyAction(1, { kind: "special8" }), "");
  });

  it("profile names mirror the backend", () => {
    assert.equal(Model.validateProfileName("work"), "");
    assert.notEqual(Model.validateProfileName(""), "");
    assert.notEqual(Model.validateProfileName("has space"), "");
    assert.notEqual(Model.validateProfileName("x".repeat(65)), "");
  });

  it("media table has the 18 vendor entries", () => {
    assert.equal(Model.MEDIA_OPTIONS.length, 18);
    const usages = new Set(Model.MEDIA_OPTIONS.map((m) => m.usage));
    assert.equal(usages.size, 18);
  });
});

describe("diffToChanges", () => {
  it("emits only differing validated fields", () => {
    const confirmed = Model.draftFromSnapshot(fakeSnapshot());
    const draft = Model.clone(confirmed);
    draft.pollingHz = 500;
    draft.cpi[0] = 450;
    draft.keys["9"] = { kind: "dpi-plus" };
    const { changes, errors } = Model.diffToChanges(confirmed, draft);
    assert.deepEqual(errors, []);
    assert.deepEqual(changes, {
      pollingHz: 500,
      cpi: [450, null, null, null],
      keys: [{ slot: 9, action: { kind: "dpi-plus" } }],
    });
  });

  it("reports every bad field and sends nothing for it", () => {
    const confirmed = Model.draftFromSnapshot(fakeSnapshot());
    const draft = Model.clone(confirmed);
    draft.pollingHz = 2000;
    draft.cpi[1] = 10100;
    draft.debounceMs = 99;
    const { changes, errors } = Model.diffToChanges(confirmed, draft);
    assert.equal(errors.length, 3);
    assert.deepEqual(changes, {});
  });

  it("keeps are no-ops even over specials", () => {
    const confirmed = Model.draftFromSnapshot(fakeSnapshot());
    const draft = Model.clone(confirmed);
    const { changes, errors } = Model.diffToChanges(confirmed, draft);
    assert.deepEqual(errors, []);
    assert.equal(Model.isNoopChanges(changes), true);
  });

  it("protects the last left-click", () => {
    const confirmed = Model.draftFromSnapshot(fakeSnapshot());
    const draft = Model.clone(confirmed);
    draft.keys["1"] = { kind: "unassigned" };
    const blocked = Model.diffToChanges(confirmed, draft);
    assert.equal(blocked.errors.length, 1);
    assert.match(blocked.errors[0].message, /left-click/);
    assert.deepEqual(blocked.changes, {});
    draft.keys["9"] = { kind: "mouse", buttons: ["left"] };
    const allowed = Model.diffToChanges(confirmed, draft);
    assert.deepEqual(allowed.errors, []);
    assert.equal(allowed.changes.keys.length, 2);
  });

  it("builds the apply request envelope", () => {
    const req = JSON.parse(Model.buildApplyRequest("r1", "rev", "fp", { pollingHz: 500 }));
    assert.equal(req.apiVersion, 1);
    assert.equal(req.requestId, "r1");
    assert.equal(req.expectedRevision, "rev");
    assert.equal(req.deviceFingerprint, "fp");
    assert.deepEqual(req.changes, { pollingHz: 500 });
  });
});

describe("labels", () => {
  it("names common keys and falls back to hex", () => {
    assert.equal(Model.keyUsageName(0x04), "A");
    assert.equal(Model.keyUsageName(0x1e), "1");
    assert.equal(Model.keyUsageName(0x28), "Enter");
    assert.equal(Model.keyUsageName(0x2c), "Space");
    assert.equal(Model.keyUsageName(0x3a), "F1");
    assert.equal(Model.keyUsageName(0x50), "Left");
    assert.equal(Model.keyUsageName(0x06), "C");
    assert.equal(Model.keyUsageName(0x60), "0x60");
  });

  it("labels draft and confirmed actions", () => {
    assert.equal(Model.actionLabel({ kind: "mouse", buttons: ["left"] }), "Click: Left");
    assert.equal(Model.actionLabel({ kind: "key", keys: [0x04] }), "Key A");
    assert.equal(Model.actionLabel({ kind: "combo", keys: [0x04], modifiers: 1 }), "Combo Ctrl+A");
    assert.equal(Model.actionLabel({ kind: "media", usage: 0xe9 }), "Media Volume up");
    assert.equal(Model.actionLabel({ kind: "keep" }), "Keep current");
    assert.equal(Model.bindingLabel({ action: { kind: "special8" } }), "Special 08 (preserved)");
    assert.equal(
      Model.bindingLabel({ action: { kind: "key-ref" }, payload: { kind: "media", usage: 0xe2 } }),
      "Media Mute"
    );
    assert.match(Model.bindingLabel({ action: { kind: "weird" } }), /preserved/);
  });

  it("gives per-code remedies", () => {
    assert.match(Model.errorHint("permission"), /udev/);
    assert.match(Model.errorHint("asleep"), /Move the mouse/);
    assert.match(Model.errorHint("stale-revision"), /review/);
    assert.match(Model.errorText({ code: "busy", message: "busy" }), /Another operation/);
    assert.notEqual(Model.errorHint("nope"), "");
  });
});
