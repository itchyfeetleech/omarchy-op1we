# Milestones 1 & 2 review

Reviewed `8f90c80..924fd37bfa5956071d4b0da9688f92fc98e704b6` against `PROJECT.md` and `PLAN.md` on 2026-09-07. Assessment: **Blocked**. All findings below are open. Milestones 3–4 were outside this review.

`bash scripts/check.sh` passed: 67 tests, Python compilation, shell syntax and udev syntax. Focused checks used the existing fake transport, temporary state directories and mocked time; no hardware commands or system configuration changes were made. They reproduced the failures below despite the passing suite. Only this review file was changed.

## Remediation record (2026-09-07)

Each finding was re-validated against the code (8 reproduced by
execution, F-007 against `docs/parity.md` vs the PLAN.md accept
criteria, F-010 by inspection) and fixed in severity order. No
hardware commands were run; all verification is hardware-free.
`bash scripts/check.sh` passes: 108 tests (67 original, updated to
the corrected contracts, + 41 new acceptance tests), Python
compilation, shell syntax and udev syntax.

| Finding | Severity | Disposition |
|---|---|---|
| F-001 | High | Fixed |
| F-002 | High | Fixed |
| F-003 | High | Fixed (mandatory `deviceFingerprint` rejected, see note) |
| F-004 | High | Fixed |
| F-005 | High | Fixed |
| F-006 | High | Partially fixed; full fix deferred (needs CID/MID RE + hardware) |
| F-007 | High | Status fixed; control implementations deferred (need hardware RE + operator sessions) |
| F-008 | Medium | Fixed |
| F-009 | Medium | Fixed |
| F-010 | Medium | Fixed |
| F-011 | Medium | Fixed |

Notes:

- F-001 trade-off: a capture holding an unsupported binding that was
  later removed can no longer re-apply that binding (fail closed);
  unknown-region drift is preserved, never restored.
- F-003 rejected suggestion: mandatory `deviceFingerprint` on Apply.
  Writes are already bound by enrollment plus fail-closed discovery,
  and identical descriptors share fingerprints, so requiring it adds
  friction without new safety. The `expectedRevision` requirement
  (including no-ops) was implemented.
- F-006/F-007 deferrals are hardware-blocked, not skipped: the
  unsafe claims were corrected now (continuity enforcement, honest
  messages, PLAN.md verdicts), and the remaining work is itemized in
  `docs/parity.md` and `PLAN.md`.

## F-001 — Profile/restore bypasses the verified-field write gate

- **Severity:** High
- **Affected files:** `backend/op1we/controller.py:443` (`restore_mem`, backup/profile loaders), `backend/op1we/protocol.py` (`ee_write_payload`), `backend/op1we/__main__.py` (profile import/apply and restore).
- **Evidence:** Loaders accept arbitrary byte values throughout `0x00–0xB4` and `0x100–0x27F`; restore diffs them and calls `apply_bytes` with only address bounds enforced. A profile containing only `{"00a0":153}` loaded successfully and wrote `0x99` to the explicitly unverified sensor block. An ordinary saved profile can likewise overwrite unknown firmware-managed bytes that drifted after capture. Invalid checksums, unsupported actions and removal of every left-click bypass normal Apply validation. A matching public fingerprint establishes neither provenance nor validity.
- **Expected behaviour:** Every write path obeys the plan's verified-field, unknown-byte-preservation and invalid/unsupported-input rules.
- **Suggested fix:** Translate profiles/restores into supported, validated setting changes against fresh memory; preserve current unknown regions. If raw recovery is needed, separate it and restrict it to authenticated local captures and explicitly justified recoverable fields, rather than arbitrary imported JSON.
- **Acceptance check:** Import/apply and restore files changing `0xA0`, an invalid checksum or an unsupported action must perform zero writes. Applying an old profile must preserve current unknown bytes.
- **Disposition:** Fixed. `restore_mem` now translates targets via `plan_restore` into supported, validated changes against fresh memory; unknown regions are preserved even when they differ, and a differing verified field that cannot be validated rejects the whole restore (`invalid-input`/`unsupported`) before any write. Restore also inherits Apply's last-click validation. Tests: `RestoreGateTest`, CLI restore rejection/no-op tests.

## F-002 — A lost write ACK causes automatic retransmission

- **Severity:** High
- **Affected files:** `backend/op1we/controller.py:370`, `backend/op1we/controller.py:499`, `tests/test_apply.py` (`FlakyTransport`).
- **Evidence:** Writes use `_exchange_wake`, which retries every `Op1weError` until timeout. A fake that committed the first write and lost only its ACK received the same write twice, and Apply returned success. The existing dropped-write test drops every attempt before committing. Failure branches also omit the planned post-failure read.
- **Expected behaviour:** Per `PLAN.md` and `docs/protocol.md`, uncertain writes are never blindly replayed; partial/uncertain state includes the recovery backup and a fresh observation when possible.
- **Suggested fix:** Separate retryable reads from single-attempt writes. On missing/invalid ACK, stop mutations and perform bounded read-only recovery. Validate ACK address, length and data, rather than accepting any matching opcode.
- **Acceptance check:** Inject a committed write with a lost ACK, a wrong-address ACK, and unplug after one chunk. Assert one send per write, no subsequent mutation, structured failure and available post-failure state.
- **Disposition:** Fixed. Writes are single-attempt with full ACK validation (address, length and data echoes); any failure stops mutation and raises with a bounded read-only observation plus the recovery backup path in `error.detail`. Reads keep wake-retry (side-effect-free, documented). Tests: `WriteOnceTest`, CLI recovery-detail test.

## F-003 — Revision protection excludes keyboard payloads and is optional

- **Severity:** High
- **Affected files:** `backend/op1we/controller.py:209`, `backend/op1we/controller.py:352`, `backend/op1we/controller.py:418`, `backend/op1we/settings.py` (`snapshot_from_memory`), `backend/op1we/protocol.py:385`, `backend/op1we/__main__.py:249`.
- **Evidence:** Snapshots and pre-write checks hash only the main config region. Changing an existing type-5 binding from key `a` to `b` left its revision unchanged; a subsequent `c` edit with the old `a` revision succeeded. Conversely, `backup` hashes its complete map, so its revision differs from `read` once a type-5 slot exists. Missing/null `expectedRevision` disables protection, and no-op paths skip comparison. Snapshot reads also release the lock between main config and payload reads.
- **Expected behaviour:** Every user-editable value participates in a consistent revision; stale drafts cannot overwrite newer bindings, and returned snapshots are internally consistent.
- **Suggested fix:** Read/hash one canonical addressed configuration including active payloads under one lock; use it consistently for read/backup/apply. Require a valid revision and fingerprint for Apply, including no-ops. Keep planning, comparison and writing in the same locked transaction.
- **Acceptance check:** Two readers of binding `a`: first applies `b`, second attempts `c` with its old token and gets `stale-revision` with zero writes. Backup/read tokens agree; missing tokens are rejected; interleaved helpers cannot produce mixed snapshots.
- **Disposition:** Fixed, except mandatory `deviceFingerprint` (rejected: enrollment plus fail-closed discovery already bind writes, and identical descriptors share fingerprints, so it adds no safety). Revision is now an addressed hash over config plus every active payload; snapshot/backup/apply share one atomic locked read; `expectedRevision` is required for Apply including no-ops. Planning stays outside the lock but comparison plus writing are atomic inside it, which closes the TOCTOU. Tests: `RevisionTest`, `AtomicReadTest`, CLI no-op/missing-revision tests.

## F-004 — Last-click protection counts inaccessible slots and ignores write order

- **Severity:** High
- **Affected files:** `backend/op1we/controller.py:678`, `backend/op1we/controller.py:368`, `backend/op1we/protocol.py` (`KEY_SLOTS`, `KEY_UI_SLOTS`), `tests/test_apply.py` (`test_last_click_protected`).
- **Evidence:** Validation counts all 16 matrix entries, although the UI limit is 12 and `docs/device.md` acknowledges unconnected/inert slots. Disabling slot 1 while assigning left-click to slot 16 was accepted. Even moving click to another real button removes slot 1 first because writes sort by address; failure before installing the replacement leaves no click. The test checks only a final map lacking any left-click records.
- **Expected behaviour:** Parity row K6 preserves an actually usable left-click, including during a partially failed remap.
- **Suggested fix:** Count only verified physical, accessible bindings with valid records. Install and verify the replacement click before removing the old one; reject migrations that cannot maintain that invariant.
- **Acceptance check:** Moving the sole click to an unverified/nonphysical slot is rejected. Inject failure at every chunk of a supported migration and confirm an accessible click remains.
- **Disposition:** Fixed. The quorum counts only vendor-exposed slots 1–12 with checksum-valid mouse records (slots 13–16 excluded: outside KM=12, zero physical evidence); adjacent chunks merge so records are atomic, and click-installing writes are verified before click-removing ones. Tests: `ClickSafetyTest` (slots 13/16, corrupt click, order, failure at each chunk).

## F-005 — Automatic recovery backups omit existing keyboard bindings

- **Severity:** High
- **Affected files:** `backend/op1we/controller.py:345`, `backend/op1we/controller.py:361`, `backend/op1we/controller.py:244`.
- **Evidence:** Automatic backups capture payloads only for slots whose payload addresses occur in the current write plan. With a key on slot 4, a debounce edit produced a backup with zero type-5 bytes. After another operation changed the key, restoring that recovery backup left the newer payload and reported success: original key `0x06` remained `0x07`. Explicit `backup` already attempts to capture active payloads, giving the two backup paths different recovery guarantees.
- **Expected behaviour:** A recovery snapshot contains everything needed to restore its supported settings, including original key/combo/media actions.
- **Suggested fix:** Capture all active type-5 payloads under the same lock as the main config before writing, plus additional payload bytes about to be overwritten. Share the complete snapshot/backup implementation.
- **Acceptance check:** Create an automatic backup during an unrelated edit with existing key/media bindings, change those bindings, then restore it; original payloads and decoded actions must return.
- **Disposition:** Fixed. The pre-write backup is the full canonical map (config plus every active payload) from the same locked read, independent of the write plan; explicit and automatic backups share the implementation. Tests: `RecoveryBackupTest`. Note: restore rewrites the validated payload prefix; inert tail bytes beyond it are preserved, not replayed.

## F-006 — Enrollment authorizes replacement receivers with the same descriptor

- **Severity:** High
- **Affected files:** `backend/op1we/device.py:63`, `backend/op1we/controller.py:79`, `backend/op1we/controller.py:84`, `docs/device.md` (model discrimination).
- **Evidence:** Enrollment compares only VID/PID, USB release and descriptor hash: device-class attributes, not receiver/pairing identity. The stored USB path is not checked. Changing the test identity's USB path and hidraw node while retaining descriptor/release still yielded `is_enrolled=True`. The repository documents the shared WE receiver ID and unresolved OP1we/XM2we discrimination, yet restore messages claim cross-device protection.
- **Expected behaviour:** Confirming this OP1we must not silently authorize another indistinguishable receiver or newly paired model, contrary to the exact-device scope in `PROJECT.md`.
- **Suggested fix:** Implement verified paired-model identification. Until then, bind confirmation to an established session/pairing and require renewed confirmation whenever continuity cannot be established. Do not present a class fingerprint as receiver identity; USB-port binding alone cannot solve same-port replacement or re-pairing.
- **Acceptance check:** Simulate replacement/re-pairing with identical descriptors, including same-port replacement. Writes stay blocked until the model/pairing is re-established.
- **Disposition:** Partially fixed; full fix deferred. Enrollment now enforces fingerprint-plus-USB-path continuity (a same-descriptor receiver on another port no longer inherits confirmation; re-enroll re-confirms), and restore/profile messages no longer claim exact-device protection. Same-port replacement and true paired-model identification stay undetectable until the CID/MID command is isolated — deferred, needs hardware RE (open follow-up in `docs/parity.md`). Tests: `EnrollmentTest`, CLI replug test.

## F-007 — Milestones are marked complete despite required parity blockers

- **Severity:** High
- **Affected files:** `PLAN.md:3`, `docs/parity.md` (K2–K5, C1, C3, C7), `docs/hardware-results.md` (checks 20–23), `backend/op1we/controller.py` (`plan_apply`, `plan_key_binding`).
- **Evidence:** Required DPI 10100–19000, stage count, LOD and several non-macro actions have no supported operation. Only slot 4 has demonstrated physical mapping; the type-5 acceptance exercise wrote slot 10 without observing its physical trigger. Readback plus parsing one's own encoding can pass while a button does nothing. Wired mode and charging remain unverified. Nevertheless, both milestones are called complete and their criteria met, despite unchanged acceptance criteria requiring every required row, defensible encodings and physical effectiveness.
- **Expected behaviour:** Full non-macro replacement remains the acceptance gate. Failing closed safely does not complete a missing control.
- **Suggested fix:** Reopen both milestones and retain these items as blockers. Resolve encodings/mappings, implement required controls, and record independent behavior/persistence/restore evidence before claiming completion or starting dependent full-parity UI work.
- **Acceptance check:** Every required row has a backend operation, independent fixture coverage and relevant physical acceptance. Observe actual key/combo/media HID output on a proven physical button, rather than only EEPROM readback.
- **Disposition:** Status fixed; control implementations deferred. `PLAN.md` milestones 1–2 are corrected to blocked with itemized blockers (K4/K5/C1-above-knee/C3/C7 operations; K3/C5/C11/wired evidence), and milestone-3 full-parity UI stays gated behind them. Implementing the missing controls needs hardware RE and operator sessions — deferred, already itemized in `docs/parity.md`.

## F-008 — Pruning can delete the recovery backup just returned to Apply

- **Severity:** Medium
- **Affected files:** `backend/op1we/controller.py:260`, `backend/op1we/controller.py:286`, `tests/test_controller.py` (`test_backup_prunes_to_latest_10`).
- **Evidence:** Collision suffixes sort lexicographically, not chronologically. With `strftime` fixed to one second, the twelfth backup call returned `backup-20260907T120000-1.json` after pruning had deleted it. Apply can therefore proceed with a nonexistent recovery path. The existing test checks only that ten names remain.
- **Expected behaviour:** Retain the latest ten backups, always including the successfully returned pre-write backup.
- **Suggested fix:** Use unique sortable timestamps/sequences or reliable creation ordering, and explicitly exclude the new backup from pruning.
- **Acceptance check:** Create more than ten backups in one second with distinct contents. Every newest returned path exists and retained contents are the ten most recent captures.
- **Disposition:** Fixed. Backups carry a monotonic creation sequence; pruning retains by sequence and explicitly excludes the just-written path, so same-second bursts keep the ten newest captures. Tests: `PruneBurstTest`.

## F-009 — Malformed device data escapes the JSON error boundary

- **Severity:** Medium
- **Affected files:** `backend/op1we/controller.py:106`, `backend/op1we/controller.py:392`, `backend/op1we/__main__.py:462`, `backend/op1we/protocol.py` (parsers), `tests/test_cli.py`.
- **Evidence:** Parsers raise `ValueError`, while controller/CLI handlers generally catch only `Op1weError`. A checksum-valid battery reply containing 101 made `cli.main(['status'])` raise `ValueError` with empty stdout. Invalid EEPROM address/length echoes during verification have the same uncaught path, potentially after a write. Missing `--file` on profile import/export reaches `open(None)` instead of input validation.
- **Expected behaviour:** Invalid input and protocol failures return one documented JSON envelope and exit code; failures after writes retain uncertainty and backup context.
- **Suggested fix:** Translate parser errors at the controller boundary into structured protocol errors, retaining recovery context. Validate action-specific CLI arguments before I/O and convert expected filesystem failures similarly.
- **Acceptance check:** Exercise malformed battery/link/profile/verification replies, missing profile arguments and file-write failures through the CLI. Assert one error envelope, correct exit status and no traceback.
- **Disposition:** Fixed. Malformed replies degrade their value to unknown (status) or become structured failures with recovery context (verify path); profile argument presence is validated before I/O; state/lock filesystem failures convert to `unavailable`; a last-resort handler envelopes unexpected exceptions (traceback to stderr). Tests: `MalformedReplyTest`, CLI missing-args/freshness tests.

## F-010 — Timeouts are renewed per chunk instead of bounding an operation

- **Severity:** Medium
- **Affected files:** `backend/op1we/controller.py:148`, `backend/op1we/controller.py:329`, `backend/op1we/controller.py:499`, `backend/op1we/device.py` (`_drain`, `exchange`).
- **Evidence:** Every config chunk receives another five seconds; Apply gives every read/write/verify another ten seconds. Status independently waits two seconds for each of three commands. A fake clock with config replies taking 0.49 seconds completed a nominal five-second read after 9.31 seconds. Intermittent responses can hold the device lock for minutes. Transport draining also lacks a deadline.
- **Expected behaviour:** Planned command deadlines bound the entire operation and lock duration, preventing scheduled reads from remaining blocked behind long operations.
- **Suggested fix:** Establish one monotonic deadline at command entry and pass remaining time through all reads, sends, recovery probes and draining. Document hardware-justified command-level budget changes.
- **Acceptance check:** Inject slow replies, sleep between chunks and continuously pending input with a fake clock. Each command must finish within its whole-operation budget and release the lock.
- **Disposition:** Fixed. Status/config/apply each run under one monotonic deadline shared by all chunks, probes and recovery reads (values unchanged: 2/5/10 s); a reserved share keeps asleep/offline classification inside the budget; transport draining is capped. Tests: `BudgetTest` (slow transport fails fast under a small budget, succeeds under a sufficient one).

## F-011 — Cached battery values have no freshness information

- **Severity:** Medium
- **Affected files:** `backend/op1we/settings.py` (`Status`), `backend/op1we/controller.py:106`, `backend/op1we/__main__.py:114`, `backend/op1we/protocol.py` (`parse_battery`), `tests/test_controller.py` (`test_receiver_only_when_link_down`).
- **Evidence:** Status deliberately returns cached battery data with the link down, but JSON supplies only a freshly generated `observedAt` for the entire response. There is no battery freshness/source or last-known measurement time. The test asserts cached 70% is returned without checking whether consumers can distinguish it from a fresh measurement. `parse_battery` also turns an absent charging byte into zero.
- **Expected behaviour:** The planned Status contract distinguishes cached/unknown battery and charging values from fresh mouse measurements; a receiver response must not refresh apparent measurement age.
- **Suggested fix:** Supply per-value freshness/source metadata and unknown measurement age where the protocol cannot establish it. Separate receiver-response time from measurement time; represent absent charging data as null.
- **Acceptance check:** Repeated receiver-only responses retain cached/unknown battery freshness while response timestamps advance. Missing charging data yields null charging.
- **Disposition:** Fixed. Status carries `batteryFresh` (true only when the reply was parsed on that call with the link up); a missing charging byte decodes to null instead of 0; `observedAt` remains the response time. Tests: freshness assertions in `StatusTest`, `MalformedReplyTest`, CLI status test, protocol charging test.
