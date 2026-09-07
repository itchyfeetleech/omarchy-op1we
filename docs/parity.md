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
**excluded** (macros — out of scope per PROJECT.md),
**absent** (demonstrably not in the baseline for this device),
**host** (host-side behavior, reproduced in the plugin, not on the
wire).

## Device control groups

### Key Settings page (`tc_page1` [UI])

| # | Control | Disposition | Evidence / status |
|---|---|---|---|
| K1 | Button slots 1–5: left/right/middle/back/forward (`KM=12` [Cfg], `tc_click…tc_back` [UI]) | required | **proven** for `type 0x01` mouse-bitmask encoding [HW]; factory identity mapping on unit |
| K2 | Button slots 6–11 incl. mode button (`K6_1…K10_1` [Cfg], unit stores types `02/04/07/08`) | required | **open**: records observed with valid CRCs [HW], but types `0x02/0x04/0x07/0x08` are undecoded. Milestone 2 must resolve via targeted RE of the SetKey path (identified near `Set Key --------`) or a captured Windows session before remapping ships. No guessing |
| K3 | Actions: single key, key combination, multimedia + kbd-media list (`tc_singlekey`, `tc_combokey`, `tc_media`, `tc_kbmedia_*` [UI]) | required | **open**: same decode gap as K2. `0x100+` combo area reads erased [HW] (populated only when combos are stored [Bin `GetComboKeyData`]) |
| K4 | Actions: CPI+/CPI−/CPI toggle, DPI lock, polling switch, profile switch (`tc_dpiadd…tc_prosw` [UI]) | required | **open**: same gap as K2 |
| K5 | Actions: disable, sleep, three-click, double-click variants (`tc_key_off`, `tc_msg27`, `tc_three`, `tc_dbclick` [UI]) | required | **open**: same gap as K2 (disable = zero record `00 00 00 55` is **observed** on slots 12–16 [HW]) |
| K6 | Constraint "at least one button must be click" (`tc_msg1` [UI]) | required | **host**: enforced in UI validation (milestone 3) |
| K7 | Fire key (`tc_fire` [UI], `ShowFireKey=0` [Cfg]) | absent | **hidden** by config for this tool/device; no control to reproduce |
| K8 | RGB on/off + RGB switch (`tc_rgb`, `tc_rgbsw` [UI]) | absent | **hidden**: OP1we has no RGB (`ShowMainEffect=0` [Cfg]); LED effect bytes observed but out of scope |

### CPI Settings page (`tc_page2` [UI])

| # | Control | Disposition | Evidence / status |
|---|---|---|---|
| C1 | 4 CPI stages, values 50–10000 step 50 then 10100–19000 step 100 (`DM=4`, `DPIRANGE=50,10000,50,10100,19000,100`, defaults 400/800/1600/3200 [Cfg]) | required | **proven** read/decode: slots 1–4 = defaults, 5–8 = 3200, all CRCs valid [HW]. Slot-1 write proven on sibling XM2we only — OP1we write test is milestone 2 |
| C2 | Active stage + "CPI toggle" cycle incl. mode button (`SyncChangeDpiLevel`, `g_nCurDpiLevel` [Bin]; short-press cycles 400/800/1600/3200 [QSG]) | required | **proven** event path: unsolicited `0x0A` notification carries the 0-based stage [HW]. Initial stage at plugin start is **open** (no getter found; candidates in header pairs unconfirmed — see `protocol.md` H1); milestone 3 seeds from notifications + last-known |
| C3 | CPI stage count ("CPI Stages", `tc_adv_str19` [UI]) | required | **open**: storage location unknown (suspected header pair — one sample only). Milestone 2 |
| C4 | X/Y independent CPI (`tc_msg19 "XY Independent"` [UI]; records carry separate x/y) | required | **observed**: x/y fields decode independently and are equal on unit [HW]. UI toggle is milestone 3 |
| C5 | Polling rate 125/250/500/1000 Hz (`tc_adv_str6` [UI]; one-hot mask at `0x00` [Bin+HW]) | required | **proven** read (1000 Hz on unit [HW]). Write untested on OP1we (also untested upstream); milestone 2 with report-timing verification |
| C6 | Debounce ms (`tc_adv_str21`, `tc_debounce_tips` [UI]; `Debounce=3` default [Cfg]; `0xA9` pair) | required | **write-proven**: 1 ms on unit; 1→2→1 with ACKs, readbacks, reconnect persistence and restore [HW]. Full slider range unconfirmed (warning text implies ≥8 ms exists); milestone 2 |
| C7 | Lift-off distance, 2 options (`tc_adv_str9`, `ShowLOD=2` [Cfg]; 1/2 mm per vendor specs, page now unre-fetchable) | required | **open**: storage undecoded (`0xA0` region implicated by the 6-way apply path [Bin]). Values + encoding are milestone 2 |
| C8 | Dormancy/sleep time (`tc_adv_str20`, `SleepTime`, `ShowAutoSleep` [UI+Bin]) | required | **open**: storage undecoded (suspected `0xA9+` pair, e.g. value 6 — unconfirmed). Milestone 2 |
| C9 | Angle snapping, motion sync, sensor mode (`tc_adv_str13/17`, `tc_msg4`, `ptSensorMode` [UI+Bin]) | required | **open**: no storage identified; may be sensor-register (RAM) rather than EEPROM. Milestone 2 |
| C10 | CPI indicator colours/LED (`DC=` [Cfg]; `tc_msg22/23`, `DpiColorReadOnly` [UI+Bin]; blue/green/yellow/red [QSG]) | absent | **proven** read (colours decode [HW]) but editing is **hidden** (`ShowDpiLED=0` [Cfg]) — display only, no editor to reproduce |
| C11 | Battery indicator (tray/skins `power*.png`; long-press mode button shows level by LED colour [QSG]) | required | **proven**: `0x04` percent + charging flag [HW]. Charging=1 never observed on OP1we (mouse never cabled during session) — verify in milestone 2/4 |
| C12 | Windows pointer settings (double-click speed, sensitivity, scrolling, precision: `tc_adv_str1–5` [UI]) | absent | OS settings, not device controls — nothing to reproduce on Linux |

### Macro page (`tc_page3` [UI]) — excluded

Macro list/key list/record/import/export/loop options
(`tc_macro_*`, `tc_mac_def` [UI]) exist in the tool but
`ShowMacro=0` [Cfg] hides the page for this device, and PROJECT.md
excludes macros regardless. **Excluded** — no wire work, no UI.

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
| P1 | Current profile (`tc_msg14`, `GetCurProfile`/`SetCurProfile` opcodes `0x0F`/`0x10` [Bin]) | required | **observed**: `0x0F` returns 1 [HW]. Profile count and `0x10` semantics untested — milestone 2 |
| P2 | Export/import/rename profile, host `.dct` files (`tc_msg32–35`, `%sPRO_*.dct`, `cfg.ini` [UI+Bin]) | required | **host**: profiles are host-side files + one device profile; plugin reproduces with its own profile store (milestones 2–3) |
| P3 | Apply/OK/Cancel/Reset/Restore buttons (`tc_apply…tc_restore` [UI]; `ApplyNow=1` [Cfg]) | required | **host**: draft/apply/verify flow per architecture; device reset semantics need the vendor Reset path (milestone 2: confirm whether Reset = Cfg defaults rewrite) |
| P4 | Offline/disconnect handling (`tc_msg10/15/36` [UI]: move-or-power-on, disconnected, single-device rule) | required | **host** + wire: link opcode `0x03` **proven** [HW]; multi-device fail-closed implemented in discovery now |
| P5 | Firmware version display + "Firmware Update" (`tc_fwver2`, `tc_msg8` [UI]) | absent (display: informational) | Display = USB bcdDevice 1.01 via `HidD_GetAttributes` (**proven** [Bin]); no blob/flash routine ships — no updater to reproduce |
| P6 | Battery-low / charging tray states (`power_empty/charging/full` skins) | required | **host** UI states driven by `0x04` (milestone 3) |
| P7 | Single-device enforcement ("more than one … connect only one" [UI]) | required | **host**: implemented — discovery fails closed on ambiguity now |

## Out-of-parity non-goals (confirmed)

- Macros (PROJECT.md exclusion + tool-hides-them).
- RGB/lighting editing, fire key, Windows pointer settings.
- Firmware flashing (nothing ships to reproduce).
- Other mouse models (XM2we shares the receiver ID; OP1we-vs-XM2we
  wire discrimination is open — enrollment + fail-closed in
  `device.md`).
- Wired-mode PIDs until observed on hardware.

## Milestone-2 decode backlog (from this matrix)

K2/K3/K4/K5 (button action types), C3 (stage count), C7 (LOD),
C8 (dormancy), C9 (snapping/sync), P1 (profile count), C6 range,
C5/C1 write verification, C11 charging=1 observation. Each needs
either the identified binary function or a captured Windows session
— never guessing.
