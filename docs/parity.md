# Parity matrix vs OP1we Configuration Tool V1.0

Baseline: vendor `WE Series Configuration Tool V1.0`
(`Endgame Gear WE Series.exe` + `Cfg.ini` + `en/text.xml`; pinned in
`device.md`). "1:1" means every non-macro control and behavior,
arranged in equivalent groups with native Omarchy styling — not a
pixel copy. Sources: `[Cfg]` = Cfg.ini, `[UI]` = en/text.xml label,
`[Bin]` = binary string/code, `[HW]` = observed on this OP1we,
`[QSG]` = quick-start guide.

Status values: **proven** (read+decode verified on hardware),
**write-proven** (additionally written, read back, persistence- and
restore-verified), **observed** (seen on hardware or in config, not
fully decoded), **open** (known to exist, encoding unknown),
**hidden** (present in the tool but disabled for this device),
**excluded** (macros — out of scope for this plugin),
**absent** (demonstrably not in the baseline for this device),
**host** (host-side behavior, reproduced in the plugin, not on the
wire).

## Device control groups

### Key Settings page (`tc_page1` [UI])

| # | Control | Disposition | Evidence / status |
|---|---|---|---|
| K1 | Button slots 1–5: left/right/middle/back/forward (`KM=12` [Cfg], `tc_click…tc_back` [UI]) | required | **proven** for `type 0x01` mouse-bitmask encoding [HW]; factory identity mapping on unit |
| K2 | Button slots 6–11 (slot 4 = Back proven by disable test [HW S1]) | required | **proven** for `02` triad, `07`, `00`, `01`; **open** for `04` generic, `08`, `09` (preserved + reported, never constructed). Behavior tests: `(02,01)`=toggle cycles+wrap, `(02,02)`=plus, `(02,03)`=minus, `(07,00)`=polling-switch 1000→250 [HW S2a]; `(08,00)` emits stage echo, `(09,00)` one unexplained polling delta [HW S2a/S2b] — follow-up single-press tests queued |
| K3 | Actions: single key, key combination, multimedia + kbd-media list (`tc_singlekey`, `tc_combokey`, `tc_media`, `tc_kbmedia_*` [UI]) | required | **format proven, trigger unobserved**: type-5 record + `0x100` event payload fully decoded from the vendor builder [Bin] (≤3 keys + modifiers, 18-entry media table); write+readback+parse verified on hardware [HW accept]. Physical press observation is a follow-up |
| K4 | Actions: CPI+/CPI−/CPI toggle, DPI lock, polling switch, profile switch (`tc_dpiadd…tc_prosw` [UI]) | required | **proven**: CPI triad + polling-switch (see K2). DPI lock / profile switch candidates live behind unconfirmed `08`/`09` — open follow-up; three-click/sleep likewise unobserved |
| K5 | Actions: disable, sleep, three-click, double-click variants (`tc_key_off`, `tc_msg27`, `tc_three`, `tc_dbclick` [UI]) | required | **open**: same gap as K2 (disable = zero record `00 00 00 55` is **observed** on slots 12–16 [HW]) |
| K6 | Constraint "at least one button must be click" (`tc_msg1` [UI]) | required | **backend-enforced** (apply rejects a key plan with no left-click; UI hint in milestone 3) |
| K7 | Fire key (`tc_fire` [UI], `ShowFireKey=0` [Cfg]) | absent | **hidden** by config for this tool/device; no control to reproduce |
| K8 | RGB on/off + RGB switch (`tc_rgb`, `tc_rgbsw` [UI]) | absent | **hidden**: OP1we has no RGB (`ShowMainEffect=0` [Cfg]); LED effect bytes observed but out of scope |

### CPI Settings page (`tc_page2` [UI])

| # | Control | Disposition | Evidence / status |
|---|---|---|---|
| C1 | 4 CPI stages, values 50–10000 step 50 then 10100–19000 step 100 (`DM=4`, `DPIRANGE=50,10000,50,10100,19000,100`, defaults 400/800/1600/3200 [Cfg]) | required | **write-proven** for 50..10000 step 50 via backend apply+readback [HW accept]. Above-knee 10100..19000 rejected: multiplier-nibble value map unproven — narrowed 2026-09-07: the `DPIHW` Cfg table mechanism is decoded (optional key, absent in the shipped Cfg → `HW[i]=i+1` default; `DPIH=64` is a UI skin metric, not a table — see `protocol.md`), but the `mul` packing in the stage-record apply path is still unisolated (exact next static target), and any candidate still needs a hardware write/readback session |
| C2 | Active stage + "CPI toggle" cycle incl. mode button (`SyncChangeDpiLevel`, `g_nCurDpiLevel` [Bin]; short-press cycles 400/800/1600/3200 [QSG]) | required | **proven** both paths: `0x04` header byte reads the 0-based stage (4 matches [HW S2a/S2b]) and unsolicited `0x0A` notifies changes [HW]. Backend `read` reports measured `currentDpi`; `listen` streams changes |
| C3 | CPI stage count ("CPI Stages", `tc_adv_str19` [UI]) | required | **open**: `0x02` tentative (value 4 = DM); write test never ran. Read-only in backend |
| C4 | X/Y independent CPI (`tc_msg19 "XY Independent"` [UI]; records carry separate x/y) | required | **absent**: `ShowXY=0` hides the toggle [Cfg]. Backend always writes x=y; decode shows both axes |
| C5 | Polling rate 125/250/500/1000 Hz (BCD RATE1-4 + DR default [Cfg]; one-hot mask at `0x00` [Bin+HW]) | required | **write-proven**: backend apply 1000→500→1000 with readbacks [HW accept]; firmware cycled 1000→250→1000 via polling-switch action [HW S2a]. Report-timing cross-check still open (follow-up) |
| C6 | Debounce ms (slider range `DebounceRange=0x1E00` = 0..30 [Bin]; default 3 [Cfg]; `0xA9` pair) | required | **write-proven** for 0..30 ms via backend apply+readback [HW accept] |
| C7 | Lift-off distance, 2 options (`ShowLOD=2` [Cfg]; 1/2 mm per vendor specs, page now unre-fetchable) | required | **open**: header index byte unidentified; `0xA0` option bytes are sensor-register programming without the dialog map [Bin]. Writes explicitly unsupported in backend; follow-up needs a captured LOD toggle |
| C8 | Dormancy/sleep time (byte = seconds/10, default 60 s [Bin+Cfg]) | required | **write-proven** for 0..2550 s step 10 via backend apply+readback [HW accept]. Vendor slider max unconfirmed (custom skin control) — backend takes the full byte range, documented |
| C9 | Angle snapping (FixLine `0xAF`), ripple (`0xB1`), motion sync | required | **write-proven** for FixLine + ripple on/off via backend [HW accept]. Motion sync **absent** (`ShowMotionSync=0` [Cfg]). Sensor-mode register block (`0xA0`) open — see C7 |
| C10 | CPI indicator colours/LED (`DC=` [Cfg]; `tc_msg22/23`, `DpiColorReadOnly` [UI+Bin]; blue/green/yellow/red [QSG]) | absent | **proven** read (colours decode [HW]) but editing is **hidden** (`ShowDpiLED=0` [Cfg]) — display only, no editor to reproduce |
| C11 | Battery indicator (tray/skins `power*.png`; long-press mode button shows level by LED colour [QSG]) | required | **proven**: `0x04` percent + charging flag [HW]. Charging=1 still unobserved: fresh read-only `status` 2026-09-07 reports 70% fresh, charging=0 (mouse on wireless link, never cabled) — needs an operator cable session |
| C12 | Windows pointer settings (double-click speed, sensitivity, scrolling, precision: `tc_adv_str1–5` [UI]) | absent | OS settings, not device controls — nothing to reproduce on Linux |

### Macro page (`tc_page3` [UI]) — excluded

Macro list/key list/record/import/export/loop options
(`tc_macro_*`, `tc_mac_def` [UI]) exist in the tool but
`ShowMacro=0` [Cfg] hides the page for this device, and this plugin
excludes macros. **Excluded** — no wire work, no UI.

### Lighting page (`tc_page4` [UI]) — absent

Steady/breathing/streaming/neon/flow/off modes, brightness, speed,
predefined colours (`tc_led_mode1–7`, `tc_adv_str7/8/10/11` [UI])
belong to RGB devices. OP1we has no RGB lighting
(`ShowMainEffect=0`, `ShowDpiLED=0` [Cfg]); the Lighting page has no
function here. **Absent** — no UI. (EEPROM LED bytes `0x4C–0x5F`
are preserved untouched by read-modify-write.)

### Profiles, persistence, service behaviors

| # | Behavior | Disposition | Evidence / status |
|---|---|---|---|
| P1 | Current profile (`tc_msg14`, `GetCurProfile`/`SetCurProfile` opcodes `0x0F`/`0x10` [Bin]) | required | **device count = 1** (`0x0F` always returns 1 [HW]; `0x10` never sent — untested vendor write). Backend reads `0x0F`; switching is host-side profile apply |
| P2 | Export/import/rename profile, host `.dct` files (`tc_msg32–35`, `%sPRO_*.dct`, `cfg.ini` [UI+Bin]) | required | **implemented host-side**: `profile save/list/show/delete/export/import/apply` (0600 store, fingerprint-bound, verified writes) with fake-transport tests |
| P3 | Apply/OK/Cancel/Reset/Restore buttons (`tc_apply…tc_restore` [UI]; `ApplyNow=1` [Cfg]) | required | **implemented**: stdin `apply` (validate-all-first, revision-checked, readback-verified) + `reset` (Cfg-documented defaults for covered fields: DPI 1-4, keys 1-10, debounce 3, polling 1000, sleep 60; everything else preserved — the vendor dialog can only default Cfg-covered fields). Verified on hardware [HW accept] |
| P4 | Offline/disconnect handling (`tc_msg10/15/36` [UI]: move-or-power-on, disconnected, single-device rule) | required | **host** + wire: link opcode `0x03` **proven** [HW]; multi-device fail-closed implemented in discovery now |
| P5 | Firmware version display + "Firmware Update" (`tc_fwver2`, `tc_msg8` [UI]) | absent (display: informational) | Display = USB bcdDevice 1.01 via `HidD_GetAttributes` (**proven** [Bin]); no blob/flash routine ships — no updater to reproduce |
| P6 | Battery-low / charging tray states (`power_empty/charging/full` skins) | required | **host** UI states driven by `0x04` (milestone 3) |
| P7 | Single-device enforcement ("more than one … connect only one" [UI]) | required | **host**: implemented — discovery fails closed on ambiguity now |

## Out-of-parity non-goals (confirmed)

- Macros (out of scope and hidden by the vendor tool).
- RGB/lighting editing, fire key, Windows pointer settings.
- Firmware flashing (nothing ships to reproduce).
- Other mouse models (XM2we shares the receiver ID; OP1we-vs-XM2we
  wire query confirmed on OP1we; checked before writes, with enrollment in
  `device.md`).
- Wired-mode PIDs until observed on hardware.

## Follow-up backlog (after milestone 2)

Resolved in milestone 2: K2 (triad + polling-switch + slot4=Back),
K3 format, K4 (triad + polling-switch), C1 (≤10000), C2, C5, C6,
C8, C9 (FixLine/ripple), P1/P2/P3, K6 backend rule.
Still open, each with a concrete next step (never guessing):
- `08`/`09`/`04`-generic meanings: one single-press + config-delta
  test each (S2a/S2b evidence recorded in `hardware-results.md`).
  Needs a physical operator pressing the bound button; static note:
  shipped-Cfg K6–K10 defaults are type-`0x08` records, but the Cfg
  encoding differs from EEPROM (e.g. K1 `01 11` vs stored `01 01`),
  so the type-8 builder is still the static target.
- Type-5 physical trigger: bind media/key, press once, capture the
  HID report (format already proven). Needs a physical operator.
- C3 (`0x02` stage count): write 2, cycle, count stages, restore.
  Unexercised vendor write — authorized operator session only.
- C1 above-knee: `DPIHW` mechanism decoded 2026-09-07 (see
  `protocol.md`); next static target is the `mul` packing in the
  stage-record apply path, then one hardware write/readback to
  validate. Still rejected until then.
- C7 LOD + `0xA0` block: capture a vendor LOD toggle (Windows), or
  isolate the `0xA0` apply writer statically. No change.
- C5 report-timing cross-check for an absolute polling proof. OP1we
  motion node identified read-only 2026-09-07; needs operator movement,
  access to the input device, and a polling write session.
- C11 charging=1 observation: needs an operator cable session.
- CID/MID: resolved 2026-09-07. Read-only query confirmed `35:02` on
  OP1we; writes now require that model reply under the device lock.
  Rejection of other identifiers is tested with fake transport; physical
  XM2we re-pairing remains untested. Enrollment stays as explicit consent.
- `0x06`/`0xAB` meanings; firmware-drift trigger (`0x0A`/`0xA0`/
  `0xA6` changed without host writes): timed re-reads blocked on
  waking the OP1we; 4/4 backup attempts on 2026-09-07
  returned `asleep`.
