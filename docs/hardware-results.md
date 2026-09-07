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
