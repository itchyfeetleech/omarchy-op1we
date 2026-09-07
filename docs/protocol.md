# Vendor HID protocol (OP1we, reverse-engineered for interop)

Transport and framing were confirmed on the OP1we receiver hardware
(`3367:1961`) on 2026-09-07. The xm2we reference
(`okms/xm2we-linux@4f3fddc`, MIT) provided the initial framing
hypothesis; every behavior below was re-verified on this OP1we —
nothing is taken on trust. No vendor files are redistributed.

## Framing

Commands are HID **feature report `0x08`** (17 bytes) on the
interface exposing usage page `0xFF02` / usage `0x02`
(see `device.md` for discovery). Replies arrive as **input report
`0x09`** (17 bytes) on the same hidraw node.

```text
out: [0]=0x08  [1]=opcode  [2..15]=payload  [16]=checksum
in:  [0]=0x09  [1]=opcode  [2]=status/0  [3..4]=address echo  [5]=data_len  [6..]=data  [16]=checksum
checksum = (0x55 - sum(bytes[0..15])) & 0xFF
```

A reply is valid when it is 17 bytes, `sum(all bytes) & 0xFF == 0x55`,
and byte 1 echoes the requested opcode. Data fits between `[6]` and
the checksum, so **10 bytes per frame** is the ceiling; longer reads
are chunked. Requests larger than 10 data bytes get a truncated
reply and must never be sent (observed during probing).

The vendor binary builds frames the same way (`HidD_SetFeature` of a
17-byte buffer starting with `0x08`, `0x55 - sum` checksum loop) and
reads replies with `ReadFile` on a second handle. It imports
`HidD_SetFeature` but no `HidD_GetFeature`: `GET_FEATURE` is not
part of the command protocol (used here only for read-only length
probing).

## Opcode allowlist

Only these opcodes may be sent by production code. Every other
opcode is forbidden: `0x0E` is implicated in a receiver reset into
bootloader mode (`25a7:fabc`, self-recovering after ~6 s, config
intact), `0x0D` cannot be excluded, and `0x02`/`0x10` are
unexercised vendor writes.

| Op | Direction | Meaning | Evidence |
|----|-----------|---------|----------|
| `0x03` | read | link state: `data[0]` = 1 up / 0 mouse offline | verified: `09 03 00 00 00 01 01 …` |
| `0x04` | read | battery: `data[0]` = percent 0–100, `data[1]` = charging flag | verified: `09 04 00 00 00 02 46 00 …` (70%, not charging). Values above 100 are rejected, matching the vendor's `cmp $0x64; ja` bound |
| `0x06` | — | **forbidden** (unknown, stateful: returned `[01,17]` before a receiver reset and `[00,00]` after) | observed only |
| `0x07` | write | EEPROM write: payload `[00, addr_hi, addr_lo, len, data…]`; reply echoes addr/len/data | verified: debounce `0xA9` write + readback + reconnect persistence + restore (see `hardware-results.md`) |
| `0x08` | read | EEPROM read: payload `[00, addr_hi, addr_lo, len]`; reply echoes addr, `data_len` == requested | verified: full `0x00-0xB4` dump, all stored checksums validate |
| `0x0F` | read | current profile, returns 1 on this unit | verified: `09 0f 00 00 00 01 01 …` |

Read the battery **before** consulting the link: the receiver
answers `0x04` from cache while the link is momentarily down (e.g.
at cable-insert), per the xm2we reference; our captures are
consistent (link 1, battery stable).

Observed but forbidden: `0x00`/`0x01`/`0x0A`/`0x0C` answer an empty
echo with byte 2 set; `0x05`/`0x0D` answer empty; `0x09`/`0x0B` stay
silent. None of these have a known meaning; `0x0D`/`0x0E` are
additionally implicated in the bootloader reset.

## Unsolicited notifications

The mouse emits input reports without a preceding command. The only
established kind is the CPI-stage notification:

```text
09 0a 00 00 00 0a 01 <stage> 01 00 00 00 00 00 00 00 <ck>
```

Four slow mode-button presses produced `<stage>` = `03 00 01 02` in
order (0-based stage index, cycling through `DM=4` stages), each
with a valid checksum. Query logic must filter replies by echoed
opcode so these frames are never mistaken for command replies; a
stage tracker listens for them explicitly. Pressing the button does
not alter header/`0xA0`/`0xA9` fields (byte-identical snapshots
before/after a full 4-press cycle).

## EEPROM map

Whole configuration is `0x00-0xB4`; `0x100+` reads as erased `0xff`
(no combo-key data stored). Stored records reuse the wire checksum
idiom: 4-byte records `[a, b, c, crc]` with
`crc == (0x55 - a - b - c) & 0xFF`; scalars are stored as
value + `0x55`-complement pairs. A validating checksum confirms
record boundaries only, never field meaning.

| Range | Contents | Status |
|---|---|---|
| `0x00-0x0B` | header: 6 value/complement pairs. `0x00` = polling mask one-hot (`01`=1000, `02`=500, `04`=250, `08`=125 Hz). Observed: `01 04 02 00 00 01` | polling proven (1000 Hz on unit); other pairs open (hypothesis: stages count / current stage / profile — one sample only, unconfirmed) |
| `0x0C-0x2B` | CPI: 8 records `[x, y, mul, crc]`, `CPI = (x+1) * (50 or 100)` | proven: slots decode to 400/800/1600/3200/3200/3200/3200/3200, all CRCs valid, matching `DPIRANGE` and factory defaults. Multiplier packing per xm2we (`X_hw`, `mulX/Y` bits) not exercised on OP1we (all `mul=0x00`) |
| `0x2C-0x4B` | CPI indicator colours: 8 records `[r, g, b, crc]` | proven read-only: blue/green/yellow/red + red fill, all CRCs valid. The tool hides LED editing (`ShowDpiLED=0`), so writes are out of scope |
| `0x4C-0x5F` | LED effect, two zones (`GetProfile` reads `0x4C-0x53`; apply path also writes `0x54`/`0x58`) | observed, undecoded; UI hidden (`ShowMainEffect=0`), out of scope |
| `0x60-0x9F` | KeyMatrix: 16 records `[type, code, param, crc]` | `type 0x01` proven (mouse bitmask: `01` left, `02` right, `04` middle, `08` back, `10` forward). Types `0x02`/`0x04`/`0x07`/`0x08` observed with valid CRCs but undecoded — milestone 2 must resolve before button remapping ships |
| `0xA0-0xA7` | 8 bytes, layout unknown (`04 ff 00 ff 03 fc 54 00` on unit) | observed only; vendor apply path writes here for a 6-way sensor/LOD-style control — undecoded |
| `0xA8` | separator `0x55`, excluded from vendor `GetProfile` ranges | observed |
| `0xA9-0xB4` | 6 value/complement pairs: `01 00 06 00 00 01` on unit. `0xA9` = debounce ms | debounce proven incl. write/readback/restore; other pairs open (candidates: LOD, dormancy, report-rate — unconfirmed) |
| `0x100+` | combo-key data (`GetComboKeyData`) | observed erased; populated only when combo keys are stored |

Unit's full dump at capture time (mouse awake, all first-try reads):

```text
0000: 01 54 04 51 02 53 00 55 00 55 01 54 07 07 00 47
0010: 0f 0f 00 37 1f 1f 00 17 3f 3f 00 d7 3f 3f 00 d7
0020: 3f 3f 00 d7 3f 3f 00 d7 3f 3f 00 d7 00 00 ff 56
0030: 00 ff 00 56 ff ff 00 57 ff 00 00 56 ff 00 00 56
0040: ff 00 00 56 ff 00 00 56 ff 00 00 56 02 53 80 d5
0050: 03 52 00 55 ff 00 ff 57 00 55 80 d5 03 52 00 55
0060: 01 01 00 53 01 02 00 52 01 04 00 50 01 08 00 4c
0070: 01 10 00 44 02 01 00 52 04 0a 03 44 08 00 00 4d
0080: 07 00 00 4e 02 02 00 51 02 03 00 50 00 00 00 55
0090: 00 00 00 55 00 00 00 55 00 00 00 55 00 00 00 55
00a0: 04 ff 00 ff 03 fc 54 00 55 01 54 00 55 06 4f 00
00b0: 55 00 55 01 54
```

## Timing, sleep and caching

- The mouse sleeps after a few seconds idle (consistent with the
  ~4 s figure in the xm2we reference; not precisely re-measured).
  EEPROM opcodes (`0x07`/`0x08`) need a live mouse and go
  unanswered while it sleeps; `0x03`/`0x04` keep answering from
  receiver cache. Readers must wake-retry with a deadline and then
  report "asleep, move the mouse" instead of failing silently.
- `0x03`/`0x04` answer in well under 100 ms; EEPROM reads answer
  immediately while awake. Milestone-1 deadlines: status 2 s,
  configuration 5 s per chunk batch with wake extension, apply 10 s.
- Never poll EEPROM continuously; never retry writes blindly.
  Interrupted writes are reported as uncertain and followed by a
  fresh read.

## `GET_FEATURE 0x06` (ancillary)

True length 9 bytes, content stable across hours and a receiver
reset: `06 00 00 64 64 64 65 22 0b`. Meaning unknown; not part of
the command protocol; recorded for future correlation only.

## Transport boundary (implementation)

`discover()` finds the single vendor node (see `device.md`);
`exchange(report, expected_opcode, deadline)` performs one
drained `HIDIOCSFEATURE` + filtered `read()` round-trip;
`close()` releases the node. A per-device `flock` under
`$XDG_RUNTIME_DIR` serializes helper access; busy reads are
skipped, never queued. Codec functions are pure
bytes↔values operations with no I/O. `apply()` is the sole
production write path: validate → backup → write → readback-verify,
with revision (raw-config hash) checks against stale state.
