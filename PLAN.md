# OP1we Control — implementation plan

Planning date: 2026-09-07. Milestone 1 implementation: **complete
2026-09-07** (evidence in `docs/`, minimal helper in `backend/`,
33 hardware-free tests green). Milestones 2–4: **not started**.
Pinned versions and evidence index: `docs/device.md`.

## Scope and evidence

Build **OP1we Control**, plugin ID `hoppcx.op1we`: a native Omarchy mouse-status widget and anchored settings popout. “1:1” means every non-macro control and behavior in the OP1we Configuration Tool V1.0, arranged in equivalent groups using native Omarchy styling; not a pixel copy of Windows chrome. Macros and other mouse models are excluded. Do not silently reduce parity to battery and DPI.

Repository inspection: only `PROJECT.md` and workflow templates; no code, dependencies, tests, assets, AGENTS.md or Git remote. `PLAN.md`, `README.md` and `REVIEW.md` were already untracked. Installed target is Omarchy **4.0.2-1**, using Quickshell, not Waybar. `lsusb` identifies receiver **3367:1961**, with two hidraw interfaces, no serial, and root-only device access. This identifies a WE receiver, not conclusively its paired mouse model. No HID commands or system changes were performed during planning.

Sources checked (versions pinned in milestone 1, see `docs/device.md`):

- [Vendor OP1we downloads](https://endgamegear.com/pages/op1we-downloads): Configuration Tool V1.0 is the parity baseline.
- [Vendor specifications](https://www.endgamegear.com/en-nl/op1we/gaming-mice): DPI 50–19000 (50 increments through 10000, then 100); polling 125/250/500/1000 Hz; LoD 1/2 mm; configurable debounce. These are starting constraints, to verify against the actual tool and firmware.
- [XM2we interoperability reference](https://github.com/okms/xm2we-linux): related-model Python/hidraw implementation with partial configuration coverage. Research input only; neither OP1we compatibility nor complete writes are established. Preserve attribution/license for any reused code; record the exact upstream commit.
- Installed contracts: `/usr/share/omarchy/shell/plugins/panels/weather/BarWidget.qml`, `panels/tailscale/Panel.qml`, and `/usr/share/omarchy/bin/omarchy-plugin-validate` (read-only).
- [Marketplace publishing requirements](https://plugins.omarchy.org/publish.html): root manifest, public GitHub repository, README/license, safe installation/removal, submission form and maintainer review. Plugins are unsandboxed; validation is not a security audit.

## Architecture and rationale

**QML bar widget + native popout + a small Python standard-library HID helper.** Root manifest uses schemaVersion 1, `kinds: ["bar-widget"]`, `entryPoints.barWidget: "qml/BarWidget.qml"`, `allowMultiple: false`, default section `right`.

- QML reuses `qs.Ui`/`qs.Commons` bar, tooltip, panel and theme components from the installed shell. It owns presentation, a draft settings form, timers and asynchronous `Quickshell.Io.Process` calls. Expose `open()`, `close()`, `opened` and popout-switch behavior as required by the shell.
- Python uses `os`, `fcntl`, `select`, `struct` and sysfs/hidraw for discovery and bounded vendor transactions. No kernel module, daemon, HTTP server, database, Python packages, USB driver detachment or input interception. Normal mouse input stays with `hid-generic`.
- Short-lived helper commands (`probe`, `status`, `read`, `apply`, `backup`, `restore`) return one versioned JSON document. QML launches an argv array, sends apply data through stdin, and never constructs shell code. A per-device `flock` under `$XDG_RUNTIME_DIR` serializes helper access, including multiple monitors/CLI use; busy reads are skipped rather than queued.
- Refresh status every 30 seconds, immediately on hover/open, and every 2 seconds while the panel is open. Read configuration on open and after writes; refresh active DPI with status if the protocol supports a cheap getter. All calls have deadlines (initially 2 seconds for status, 5 for configuration, 10 for apply); record hardware-driven adjustments. Do not poll EEPROM continuously. A sleeping mouse may require movement; distinguish cached data from a fresh reply.
- Hardware is authoritative. Opening the plugin never writes settings. Apply validates the complete draft, rereads the device, rejects stale revisions, preserves unknown bytes, saves a private backup, writes only verified configuration fields, and verifies by readback. No success message before verification. Interrupted writes are reported as uncertain/partial and followed by a fresh read; never blindly retry or roll back.
- Persistent local data is limited to explicit host-side profiles if the baseline tool has them, and bounded recovery backups under `$XDG_STATE_HOME/op1we-control` (default `~/.local/state/op1we-control`, directory 0700/files 0600; retain latest 10). No automatic replay on reconnect. UI preferences use Omarchy widget settings.
- Install narrowly scoped `TAG+="uaccess"` hidraw rules for verified vendor/product/interface combinations; never grant all-HID or world-write access. Runtime is unprivileged. Permission setup is an explicit documented administrator step, not a plugin-enable hook.

Alternatives rejected: Electron/web UI adds a runtime and loses shell integration; Piper/ratbag or Solaar cannot be assumed to cover this exact protocol and full parity; a persistent service/socket adds lifecycle and IPC complexity unnecessary for occasional configuration; input remapping through evdev/uinput changes semantics and adds privileged input access.

## Directory and module structure

```text
manifest.json                 # Marketplace/install root
qml/
  BarWidget.qml               # Icon, percent/connection state, hover, shell routing
  Panel.qml                   # Native anchored settings form, Apply/Cancel/reset
  Controller.qml              # Async helper calls, refresh, draft/confirmed state
  Model.js                    # JSON validation and UI state transitions
backend/op1we/
  __init__.py
  __main__.py                 # CLI and JSON envelope
  device.py                   # Discovery, identity allowlist, hidraw, lock, deadlines
  protocol.py                 # Proven report encoders/decoders; no UI/file access
  settings.py                 # Typed settings, limits, actions, diff/validation
  controller.py               # Read/apply/verify, revision checks, recovery backups
udev/70-op1we-control.rules
scripts/
  install-dev.sh              # Copy files into user plugin directory, no symlinks
  uninstall-dev.sh            # Remove only owned dev files; preserve user data
  check.sh                    # Python/JS/shell/QML and manifest checks
  package.sh                  # Allowlisted source archive + SHA256 checksum
  hardware-check.py           # Explicitly opted-in hardware validation
tests/
  fixtures/                   # Proven OP1we reports + synthetic failures, provenance
  test_*.py                   # Standard-library unittest
  model.test.cjs              # Node built-in tests for Model.js
docs/
  device.md                   # Identity, firmware, transport and validation evidence
  parity.md                   # Every baseline control/action, range, storage, test
  protocol.md                 # Reports, offsets, checksums, timing, unknowns
  hardware-results.md         # Versioned physical acceptance results
  preview.png                 # Actual themed UI screenshot for listing
LICENSE                       # MIT; retain any third-party notices
README.md                     # Replace workflow README at documentation milestone
.gitignore
.github/workflows/check.yml
PROJECT.md / PLAN.md / REVIEW.md
```

## Data model and interfaces

- `DeviceIdentity`: VID/PID, interface/usage descriptor, transport, firmware/model evidence, runtime path and fingerprint. Never persist `/dev/hidrawN` as identity. Shared receiver IDs require proven model identification or an explicit local pairing enrollment backed by milestone-1 evidence; ambiguous/multiple devices fail closed. Never claim that a USB-port binding proves the paired model.
- `Status`: connection (`connected`, `receiver-only`, `unavailable`, `unknown`), transport, battery percent or null, charging or null, current DPI/stage or null, observation time and freshness per value. Permission, sleeping/timeout and unsupported-device errors remain distinguishable; unknown battery is not 0%.
- `Capabilities`: exact firmware-supported fields, ranges, steps, enums, number of stages/profiles and permitted button actions. Freeze verified values in the OP1we codec; do not infer them from XM2we or OP1 8K. Unverified required controls block release.
- `SettingsSnapshot`: identity/fingerprint, revision (hash of relevant raw configuration), active profile/stage, DPI stages, polling Hz, debounce ms, LoD, button bindings, and any further baseline sensor/power/profile fields established in `docs/parity.md`. Unknown bytes stay in the internal raw snapshot.
- `ButtonAction`: tagged action (`mouse`, `keyboard`, `consumer`, `dpi`, `profile`, `disabled`, plus only proven baseline actions), with bounded HID usage/modifier/action parameters. No shell commands or macro sequences.
- `ApplyRequest`: `{apiVersion:1, requestId, deviceFingerprint, expectedRevision, changes}`. Helper rejects unknown keys, invalid ranges, unsupported firmware/actions, stale state and oversized input before writing.
- CLI response: `{apiVersion:1, requestId, ok, data, error}`; `error` has stable `code`, concise `message`, `retryable`, and optional verified post-failure snapshot. Stdout is JSON only, stderr diagnostics; exit 0 success, 2 invalid input, 3 unavailable/permission/unsupported, 4 busy/conflict, 5 protocol/verification failure.
- Transport boundary: `discover()`, `exchange(report, expected_reply, deadline)`, `close()`. Inject a fake transport/clock for tests. Codec functions are pure bytes-to-values/value-to-bytes operations. `apply()` is the sole production path for writes; restore validates identity/format and uses the same path.

## Milestones (dependency order)

### 1. Prove OP1we support and freeze parity — complete 2026-09-07

Inventory the actual tool's tabs, controls, defaults, ranges, actions and storage behavior, including profiles/import/export/reset and sensor/power controls if present. Record every item as required, macro-excluded or demonstrably absent. Inspect read-only descriptors; obtain protocol evidence from upstream/vendor documentation and controlled captures where necessary. Establish exact identity, wired/receiver behavior, feature reports, battery/charging/link/current-DPI reads and all required configuration encodings. Add the minimal helper transport, fixtures, targeted permission rule and recovery backup support. Once safe, demonstrate a reversible setting write/readback and restoration on this OP1we.

**Accept:** `docs/parity.md`, `docs/device.md` and `docs/protocol.md` contain evidence and no unresolved architectural questions; battery/link/config readings agree with hardware/reference; model selection excludes unrelated devices; all required control encodings have defensible evidence; reversible write survives reconnect and restores the original value. Unknown fields must not be guessed. If evidence/tool access is unavailable or identification cannot enforce scope, record the blocker and stop dependent work rather than building a speculative UI.

**Result: met with scoped follow-ups.** Delivered: `docs/device.md`,
`docs/parity.md` (27-row matrix), `docs/protocol.md`,
`docs/hardware-results.md` (v0.1, 13 checks), minimal helper
(`probe`/`enroll`/`status`/`read`/`backup`/`restore` JSON CLI),
fixtures from real captures, 33 `unittest` tests green,
`scripts/check.sh`, `scripts/hardware-check.py`,
`udev/70-op1we-control.rules` (syntax-verified, not installed),
MIT `LICENSE`. Live proof on the OP1we: link/battery(70%)/profile
reads, full `0x00–0xB4` decode matching Cfg defaults (polling
1000 Hz, CPI 400/800/1600/3200, debounce 1 ms), debounce
1→2→1 with ACKs/readbacks, USB-reconnect persistence, restoration,
and `0x0A` CPI-stage notifications from mode-button presses.
Device left in its original state. No speculative UI was built.

**Material decisions (milestone 1):**
- Strict opcode allowlist `{0x03,0x04,0x07,0x08,0x0F}` enforced in
  `protocol.frame()`: a read-style sweep implicated `0x0E` in a
  receiver reset to bootloader mode `25a7:fabc` (self-recovered,
  config intact); `0x0D` cannot be excluded. Never send anything
  unlisted (`docs/protocol.md`).
- OP1we-vs-XM2we wire discrimination is unresolved (shared receiver
  ID, no serial; CID/MID command not yet isolated), so writes
  require explicit local pairing enrollment plus fail-closed
  discovery (zero/multiple candidates error; fingerprint-mismatch
  blocks restore). CID/MID isolation is milestone-2 RE.
- Forward writes in milestone 1 are limited to a provenance gate
  (device-observed or Cfg-documented values); there is no generic
  `apply` CLI yet — `restore` (verified, chunk-diffed, revision-
  checked) is the only CLI write path. Full apply/profile API is
  milestone 2.
- Button action types `0x02/0x04/0x07/0x08`, LOD, dormancy, stage
  count, angle-snapping/motion-sync, profile count and the debounce
  range are recorded **open** in `docs/parity.md` with a concrete
  remediation path (targeted RE of identified binary functions or a
  captured Windows session). They block only their own controls,
  not the proven architecture.
- Current CPI stage is tracked via unsolicited `0x0A`
  notifications; no initial-stage getter was found (one-sample
  header-pair hypothesis documented, unconfirmed). Seeding is a
  milestone-3 decision.
- "Firmware version" is the USB `bcdDevice` (`0101`) via
  `HidD_GetAttributes` (proven in the binary); no firmware blob or
  flash routine ships, so firmware update is out of scope.
- Profiles are host-side `.dct` files plus one device profile
  (`0x0F` returns 1); plugin reproduces them host-side.
- udev rule covers verified `3367:1961` only; wired PIDs
  `0x1960/0x1962` stay out until observed on OP1we hardware.
- Macro page is excluded (PROJECT.md) and tool-hidden
  (`ShowMacro=0`); lighting/RGB editing is absent for this device
  (pages hidden); Windows pointer settings are not device controls.

### 2. Complete configuration backend — not started

Implement all proven non-macro settings and actions, profiles according to their observed device/host storage semantics, reset, explicit backup/restore, conflict checks, bounded I/O, and verified writes. Implement JSON API and fake-transport test suite.

**Accept:** every required parity row has a backend operation and fixture test; invalid/stale/unsupported requests perform zero writes; unknown bytes survive edits; failed/partial writes never report success; physical changes work, persist as the original tool does, and can be restored. Current DPI is measured/read, never guessed from the first stage.

### 3. Native widget and full popout — not started

Implement themed mouse icon with percent/connection state, hover name/battery/current DPI, baseline-equivalent control groups, draft editing and Apply/Cancel. Include keyboard navigation, Escape/outside-click dismissal, keyboard-accessible restore/reset, busy/error/recovery states and reconnect refresh. No edits lost silently when switching profiles or closing a dirty form.

**Accept:** complete install → status → hover → click → edit → apply workflow works on target hardware; all required non-macro controls are present and effective; physical DPI changes refresh on hover/open; off/sleep/unplug states do not show fabricated fresh values; theme changes apply live; panel fits the current display and remains usable at 100%/200% scale; repeated opens and multi-monitor use do not race writes or leak processes.

### 4. Package and release verification — not started

Provide safe dev install/removal, source archive, dependency checks, CI, MIT license/attribution, concise user README, complete hardware checklist and a real themed screenshot. Validate current marketplace requirements, then prepare GitHub release and marketplace submission content. Publication/submission occurs only in a later authorized release task, not this planning task.

**Accept:** clean Omarchy 4 installation using documented commands works without development tooling at runtime; removal leaves unrelated shell configuration and user data intact; missing permissions/dependencies explain the remedy; archive validates and matches its checksum; tests pass; every parity row has physical acceptance evidence. Completion additionally requires the public GitHub URL and marketplace submission URL recorded here; acceptance into the marketplace is externally controlled.

## Testing strategy

Use `unittest` with independent captured reports for framing, checksums, DPI boundaries (including 10000 transition), actions and unknown-byte preservation. Fake transport tests cover wrong opcode/report lengths, out-of-range battery, stale replies, timeouts, lock contention, unplug during apply, partial writes and stale revisions. Test subprocess JSON envelopes end to end, including malformed/oversized stdin. Node's built-in runner checks observable UI state transitions and draft/error behavior; it is development-only.

Run manifest validation, Python compile checks, shell syntax checks and QML lint with installed shell imports. QML warnings must be resolved or individually explained; no blanket suppression. CI runs hardware-free tests; native shell loading and physical tests run on Omarchy. Real-hardware checks are explicitly opted in, save settings first and restore them afterward. Validate battery charging/unplug/reconnect/suspend, every remapped action in an input-event viewer, DPI by physical movement, polling by report timing, sleep/power behavior, and profile persistence. A mock pass never proves device parity.

## Exact command contract

These commands are **to be implemented and verified** in the milestones above; only the existing Omarchy CLI was inspected. Run from repository root. Runtime requires Omarchy 4.0.2-compatible shell and Python 3; no pip install/build step. Development additionally requires Node, Qt declarative tools, Bash and tar.

```bash
# Development prerequisites (on an up-to-date Omarchy system)
sudo pacman -S --needed python nodejs qt6-declarative

# Hardware permission setup, only after milestone 1 verifies the rule
sudo install -m 0644 udev/70-op1we-control.rules /etc/udev/rules.d/70-op1we-control.rules
sudo udevadm control --reload-rules
# Physically replug the receiver/cable to obtain active-session ACLs.

# Direct backend diagnostics; probe does not mutate mouse configuration
PYTHONPATH=backend python3 -m op1we probe
PYTHONPATH=backend python3 -m op1we status
PYTHONPATH=backend python3 -m op1we read

# Copy development plugin, validate, load, enable and open
bash scripts/install-dev.sh
omarchy plugin validate "$HOME/.config/omarchy/plugins/hoppcx.op1we"
omarchy-shell shell rescanPlugins
omarchy plugin enable hoppcx.op1we --section right
omarchy-shell shell toggle hoppcx.op1we

# Hardware-free tests and checks
PYTHONPATH=backend python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/model.test.cjs
bash scripts/check.sh

# Explicit physical verification (interactive; backup and restore required)
PYTHONPATH=backend python3 scripts/hardware-check.py --interactive

# Package working-tree runtime files, excluding tests, caches and private captures
bash scripts/package.sh
sha256sum -c dist/op1we-control-1.0.0.tar.gz.sha256

# Remove development install (preserves backups/profiles)
omarchy plugin disable hoppcx.op1we
bash scripts/uninstall-dev.sh
```

`check.sh` runs the two test commands, `python3 -m compileall -q backend`, `bash -n` on scripts, `/usr/lib/qt6/bin/qmllint -I /usr/share/omarchy/shell qml/*.qml`, and `omarchy plugin validate .`; verify/adjust QML import-root handling against the installed shell in milestone 3. `package.sh` derives the archive version from the manifest (initial release 1.0.0), stages only runtime files/docs/license/rule, rejects symlinks, validates the stage and emits archive/checksum. Dev install uses copies (validator forbids symlinks), refuses collisions with a non-dev installation, and updates only its owned files. Marketplace installation uses `omarchy plugin add` with the eventual public repository URL; record that literal URL and final command during release rather than inventing a remote now.

## Material risks and assumptions

| Risk/assumption | Consequence and treatment |
|---|---|
| Related WE protocol may differ; baseline inventory incomplete | Milestone 1 is a real feasibility gate. Do not extrapolate OP1 8K/XM2we features or claim full support prematurely. |
| Shared receiver ID and no serial | Prove paired-model discrimination; reject ambiguity. If impossible, require an explicit architecture/scope decision before enabling writes. |
| Original tool/captures or wired-mode access may require another environment | Windows is a possible development reference only, never a runtime dependency. Document missing evidence promptly. |
| Partial writes, reserved bytes, flash wear | Apply only, bounded config addresses, backups, read-modify-write/verify, no automatic retry and no firmware flashing. Confirm whether firmware update is a separate utility; if embedded in baseline, resolve scope before milestone 2. |
| Receiver caches or sleep can hide actual state | Carry freshness/nulls and query on interaction; measure poll effects and recovery on this firmware. |
| Shell API/marketplace changes | Target installed 4.0.2 contracts, test upgrades before claiming support, recheck publishing requirements at release. |
| Proprietary assets and upstream licensing | Ship original UI/icon and own screenshots; no vendor binaries; retain applicable upstream notices. |
| GitHub owner and listing not established | Default ID uses local username; confirm availability before release. No branding service, website or marketing infrastructure required. |

## Final verification (pending implementation)

| Check | Evidence required | Result |
|---|---|---|
| Protocol and non-macro parity | Completed parity matrix + hardware results on recorded firmware | Pending |
| Automated tests/checks | Commands above, CI and native QML loading | Pending |
| Fresh install/removal | Clean Omarchy user; narrowly scoped permissions; unrelated config preserved | Pending |
| Main workflow and appearance | Real mouse, theme switch, scaling, reconnect, screenshots | Pending |
| Package | Valid staged manifest, archive checksum, install from archive | Pending |
| Publication | Public GitHub release URL and marketplace submission URL | Pending |
