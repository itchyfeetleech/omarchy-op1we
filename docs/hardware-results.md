# Physical acceptance results

Versioned record of on-hardware verification. v0.1 = milestone 1
(2026-09-07, receiver `3367:1961` bus 1 port 7, mouse on 2.4 GHz
link, operator present for movement/button tests).

## v0.1 — transport, reads, reversible write (milestone 1)

| # | Check | Result |
|---|---|---|
| 1 | Receiver identity: `3367:1961`, no serial, two hidraw nodes, root-only access | pass (see `device.md`) |
| 2 | Descriptor marker `FF02/usage 2/report 8` locates the vendor node; `GET_FEATURE` lengths 6→9 B, 8→17 B, 9→17 B | pass |
| 3 | `0x03` link = 1, `0x04` battery = 70% charging=0, `0x0F` profile = 1, all checksums valid | pass (captures in `tests/fixtures/`) |
| 4 | Full EEPROM `0x00–0xB4` dump while awake; every stored record checksum validates; `0x100+` erased | pass (dump in `protocol.md`) |
| 5 | Decoded values match independent references: polling 1000 Hz, CPI 400/800/1600/3200 = Cfg defaults = QSG, colours blue/green/yellow/red, mouse-button bitmasks, debounce pair shape | pass |
| 6 | Backup of 181 config bytes to a file before any write | pass |
| 7 | Write debounce `0xA9`: `01 54` → `02 53`; ACK `09 07 00 00 a9 02 02 53 …`; readback `02 53` | pass |
| 8 | Reconnect (USB unbind/bind, device re-enumerated): written `02 53` persisted | pass |
| 9 | Restore `01 54` with ACK; readback `01 54`; battery/link unchanged afterwards | pass — device left in its original state |
| 10 | Mode button: 4 slow presses → 4 unsolicited `0x0A` notifications with stages `03 00 01 02`, valid checksums; config bytes unchanged across the cycle | pass |
| 11 | Receiver reset observation: opcode sweep implicated `0x0E` in a reset to bootloader `25a7:fabc` with self-recovery and intact config | recorded as safety evidence; production allowlist excludes it (see `protocol.md`) |
| 12 | Charging flag = 1 | not observed (mouse never cabled during the session) — open for milestone 2/4 |
| 13 | Wired-mode PIDs / behavior | no cable attached — open |

Suspected-firmware note: none of the checks above modified unknown
bytes; the only writes were the documented debounce pair, restored
afterwards. The bootloader incident (11) was followed by a
byte-identical re-read of header/CPI/debounce.

## v0.2 — button meanings + full backend acceptance (milestone 2)

| # | Check | Result |
|---|---|---|
| 14 | S1: slot 4 disable → BACK presses produce no HID; restore verified | pass — slot 4 = Back |
| 15 | S1: `0xB5–0xD0` reads fully erased (`0xFF`) | pass — preserved, never written |
| 16 | S2a: `(02,01/02/03)` bound to BACK in turn → stages cycle+wrap / step up / step down; `0x04` matches each end stage (3/3) | pass — toggle/plus/minus + `0x04` current-stage read |
| 17 | S2a: `(07,00,00)` → polling 1000→250, read back | pass — polling-switch |
| 18 | S2a/S2b: `(08,00,00)` → stage-echo notifications only, no config/HID delta | recorded — meaning unconfirmed |
| 19 | S2a/S2b: `(09,00,00)` → one polling delta 250→1000 over 2 presses (S2a); silence on tap+hold (S2b, no polling re-read) | recorded — unconfirmed, needs a clean single-press test |
| 20 | Backend accept via delivered CLI: enroll, backup, one 14-chunk apply (polling 500, CPI 800/1600/3200/6400, debounce 2, sleep 120, ripple+fixline on, turn-off off, slot12 mouse-left, slot11 dpi-plus, slot10 key `a` + payload) | pass — snapshot verifies every field; type-5 payload parses back (`key [0x04]`) |
| 21 | Same session: restore backup → revision returns to pre-apply value; full read verifies originals | pass — 13-chunk restore, byte-identical revision |
| 22 | Debounce restored 3→1 ms via `apply` (1 chunk, verified) | pass |
| 23 | Post-session diff vs milestone-1 backup: `0x0A` 1→2, `0xA0` 04→00, `0xA6` 54→58 changed without host writes (audited); `0xA9` 1→3 likewise (restored) | recorded as firmware-managed drift (see `protocol.md`); device healthy, all controls decode |

Type-5 note: write+readback+parse proven (check 20); the physical
press→HID observation was deliberately not scheduled (operator
fatigue) and is queued as a 30-second follow-up. Stage restored to
2 and slot 4 to `(01,08,00)` at session end.

## v0.3 — read-only state confirmation + sleep behavior (2026-09-07)

Privileged read-only session (`sudo`, zero mouse writes, no udev or
system change). The user-space permission path still reports honest
`permission` (exit 3); the udev rule remains uninstalled.

| # | Check | Result |
|---|---|---|
| 24 | `probe` via sudo: identity `3367:1961` bcdDevice `0101`, usbPath `1-7:1.1`, hidraw `/dev/hidraw6`, enrolled true, helper 0.4.0 | pass — matches milestone-1 identity |
| 25 | `status` via sudo while the mouse was awake: connected, link up, battery 70% fresh, charging=0, profile 1 | pass — charging=1 still unobserved (wireless link, never cabled) |
| 26 | Full `read` via sudo: polling 1000, debounce 1, sleep 60, stages 400/800/1600/3200, currentStage 2 (1600 DPI), bindings identical to the milestone-1 dump incl. `(04,0A,03)` slot 7 and `(08,00,00)` slot 8 | pass — config matches the v0.1 capture; no drift in covered fields since |
| 27 | `backup` ×4 over ~16 s after the mouse went idle | honest `asleep` (retryable) every time, zero writes, no state pollution (scratch state dir) |
| 28 | Input-device census: `mouse0` (OP1we, event13) plus `mouse1`/`mouse2` (other devices) | recorded — explains v0.3 sleep: the user drives another mouse while the OP1we idles; timed drift re-reads need an operator wake of the OP1we specifically |

No writes, no new opcodes, and no enrollment/permission changes were
made in v0.3. The statically isolated CheckPsd query (opcode `0x01`,
see `protocol.md`) was deliberately not sent: new-opcode work waits
for an authorized operator session.


## Release closeout — 2026-09-07

Installed the scoped receiver uaccess rule, reloaded udev, and triggered the
connected receiver. Normal-user status and full configuration reads passed:
70% battery, 1600 DPI, 1000 Hz. No mouse settings changed in this session.

Vendor CheckPsd model query (read-only) returned a checksum-valid frame:
`09 01 00 00 00 08 35 02 00 00 35 02 00 00 00 00 d5`.
CID/MID is `35:02`, matching OP1we. Repeated through the production parser
with the same result. Production writes now require that result while holding
the device lock. Physical wrong-model/charging/LOD tests remain unperformed.

The user confirmed periodic panel shifting stopped after installing the fix
and restarting the shell. Background polls no longer change foreground busy
state or shift controls.
