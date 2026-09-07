# Device identity, transport and validation evidence

Milestone 1 record. All hardware observations below were taken on the
developer machine unless stated otherwise.

## Pinned versions (milestone 1)

| Item | Value |
|---|---|
| OS / kernel | Omarchy 4.0.2 (`omarchy 4.0.2-1`), kernel 7.1.9-arch1-2 |
| Shell / widget runtime | quickshell 0.3.1-1, qt6-declarative 6.11.2-1 |
| Python / Node | CPython 3.14.7, Node v26.8.1 (package nodejs 26.7.0-1) |
| Vendor baseline tool | `endgame-gear-we-series-configuration-software-v1.0-setup.exe`, 2050412 bytes, sha256 `915716f8dd25aca544e1a6011b69796a7a3bcb9dea90907f899f23c42bcf34f6`, downloaded 2026-09-07 from `https://img.endgamegear.com/downloads/` |
| Tool internals | Inno Setup payload: `Endgame Gear WE Series.exe` (2214400 bytes, 2023-05-18), `Cfg.ini`, `en/text.xml`, skins; `Title=Endgame Gear WE Series`, `Appdir=JM03` |
| Interop reference | `https://github.com/okms/xm2we-linux` at commit `4f3fddc` ("Rewrite the protocol docs"), cloned 2026-09-07, MIT licensed |
| Vendor quick-start guide | `Endgame_Gear_OP1we_QSG.pdf` (527330 bytes, InDesign metadata 2024-02-10) |

## USB identity (observed)

| Item | Value |
|---|---|
| Receiver | USB `3367:1961`, bcdDevice `0101`, USB 2.0 full-speed (12M), bus-powered + remote wakeup, MaxPower 98mA |
| Product string | `Endgame Gear WE Series Gaming Receiver` (generic across the WE series, not model-specific) |
| Serial | none (`iSerial 0`, no sysfs `serial` attribute) |
| Interfaces | if0 HID boot mouse (`0003:3367:1961`, hidraw5 at capture time), if1 HID boot keyboard (`0003:3367:1961`, hidraw6 at capture time) |
| Driver | `usbhid` / `hid-generic`; no detachment is ever required |
| Device access | root-only (`crw-------`) without a udev rule; a narrow `TAG+="uaccess"` rule is provided in `udev/` (not installed in milestone 1) |

Never persist `/dev/hidrawN` as identity: node numbers churn on replug
(proven by the unbind/bind reconnect test, which re-enumerated on new
device numbers).

## Wired mode (unverified)

`Cfg.ini` lists wired PIDs `0x1960,0x1962` and receiver `PID2=0x1961`.
The xm2we reference reports `3367:1960` as XM2we wired mode. No wired
OP1we device was available in milestone 1, so neither wired PID is
claimed for OP1we and the shipped udev rule covers the verified
receiver ID only. The `0x1962` PID is unmapped (suspected OP1we wired,
unconfirmed).

## HID report descriptors (read-only capture)

sysfs `report_descriptor` dumps, Curtis `3367:1961`:

- if0 (87 bytes): standard boot mouse (buttons/X/Y/wheel + consumer
  pan). No feature reports (`GET_FEATURE` on any ID fails with
  `EPIPE`). Pointer reports are 7 bytes:
  `[buttons, dx_lo, dx_hi, dy_lo, dy_hi, wheel, pan]`.
- if1 (189 bytes): keyboard boot reports (ID 1), consumer (ID 5),
  system control (ID 3), plus vendor collections:
  - `FF03/usage 0` input ID 2 (7 bytes),
  - `FF01/usage 0` input ID 9 (16 bytes),
  - `FF04/usage 2` **feature** ID 6 (descriptor says 7 bytes payload,
    device answers 8: true length 9),
  - `FF02/usage 2` input ID 8 (descriptor implies 7 bytes payload).

The vendor command channel does **not** match its descriptor
declaration: `GET_FEATURE` proves report `0x08` is 17 bytes long
(returns zeros) and report `0x09` is 17 bytes long, exactly the
xm2we framing. Discovery must therefore match the interface by
VID/PID plus the descriptor marker
`06 02 ff 09 02 a1 01 85 08` (usage page `FF02`, usage `0x02`,
report ID 8), never by node number or declared lengths.

## Model discrimination (OP1we vs XM2we)

The receiver ID `3367:1961` and product string are shared across the
WE series, and there is no serial number, so USB identity alone
cannot prove the paired mouse is an OP1we. Evidence gathered:

- The vendor tool is a single WE-series binary with two device
  sections: `DEV_1` (`CID=0x35`, `MID=0x02`) and `DEV_2` (`CID=0x35`,
  `MID=0x01`). The xm2we reference identifies `MID=0x01` as XM2we, so
  `MID=0x02` is the OP1we section. Both sections are otherwise
  identical (sensor `0x3370`, `DM=4`, same DPI table, same key
  defaults).
- The tool reads CID/MID from the device (`CheckPsd`, `LoadCID ok`,
  `Psd_Thread: unsupport dev`), but the exact command bytes were not
  isolated in milestone 1 (static analysis reached the `SetFeature`
  sender; the CheckPsd payload layout needs one more pass).
- Behavioral difference observed on this unit: the underside mode
  button cycles **CPI stages** (unsolicited `0x0A` notifications,
  LED blue/green/yellow/red), while the xm2we reference documents the
  XM2we button cycling **polling rate**. Behavior is corroborating,
  not identifying.

Milestone-1 selection rule (implemented in `backend/op1we/device.py`):
accept exactly one candidate matching VID `0x3367`, PID `0x1961`
(receiver) or a verified wired PID, plus the `FF02/usage 2/report 8`
descriptor marker; zero candidates and multiple candidates both fail
closed with distinct errors. Because OP1we-vs-XM2we cannot yet be
proven on the wire, first use requires explicit local pairing
enrollment: the operator confirms the paired mouse is the OP1we, and
the helper records the enrollment (USB path, descriptor hash,
firmware `bcdDevice`) for later mismatch warnings. Lifting the
enrollment requirement needs the CID/MID command (milestone 2).

## Firmware evidence

- `bcdDevice 0101` (USB device release 1.01). The vendor tool's
  "Firmware version" display is this USB `HIDD_ATTRIBUTES`
  `VersionNumber` (proven: the tool calls `HidD_GetAttributes` on the
  version path and imports no other version source). No separate
  firmware-version opcode was found.
- Sensor/MCU per vendor product page: PixArt PAW3370 + CompX CX52850.
- Battery: 335 mAh (vendor product page; XM2we is 410 mAh — the
  models differ here). Percentage is a coarse voltage estimate with
  no fuel gauge (per xm2we reference; consistent with the observed
  long dwell at fixed values).
- No firmware blob or flashing routine ships in the installer, so
  firmware update is out of scope (see `parity.md`).

## Bootloader observation (safety-relevant)

During a read-style opcode sweep, the receiver spontaneously
re-enumerated as `25a7:fabc` (bcdDevice 3.24, no strings) for about
6 seconds, then rebooted itself back to `3367:1961` with the
configuration intact (byte-identical re-read). The last command sent
was opcode `0x0E` (empty payload), implicating it as a
reset/bootloader trigger; `0x0D` cannot be excluded. Production code
must use a strict opcode allowlist and never send `0x0D`/`0x0E`,
`0x02`/`0x10` (unexercised vendor writes), or any unlisted opcode.
Details in `protocol.md`.

## Validation evidence index

- Descriptor hex + `lsusb -v` + udev attributes: captured 2026-09-07
  (see `protocol.md` for the descriptor bytes).
- `GET_FEATURE` length probes: ID 6 = 9 bytes, ID 8 = 17 bytes,
  ID 9 = 17 bytes on the vendor interface; no features on if0.
- Handshake replies: `0x03` link, `0x04` battery, `0x0F` profile —
  real captures in `tests/fixtures/` with provenance.
- Full EEPROM dump `0x00-0xB4` plus `0x100-0x113`: captured and
  decoded (see `protocol.md`); backup procedure in
  `hardware-results.md`.
- Reconnect persistence + restoration: proven in
  `hardware-results.md`.
