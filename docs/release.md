# 0.4.0 development preview — verification record

## Supported preview

OP1we Control 0.4.0 adds a native, theme-aware mouse diagram with annotated
button dropdowns, a focused custom-binding editor, compact DPI stages and
separate Sensitivity and Profiles & device pages. Apply/Cancel stay visible.
Safe development installation/removal, allowlisted archives, checksums and
portable CI are included.

This preview is not a complete replacement for the vendor driver yet.
Remaining blockers: LOD, stage-count writes, DPI above 10000, several special
actions, full physical button mapping, key/media trigger verification,
wired/charging acceptance. Paired-model querying is now verified on OP1we
and enforced before writes. See parity.md.

## Acceptance record

- Native renderer: isolated Quickshell, current Omarchy theme; live installed panel also checked after a shell restart, with no plugin QML errors.
- Live receiver access fixed on 2026-09-07: installed the narrow uaccess rule,
  reloaded udev and triggered the connected hidraw device. Normal-user status
  and configuration reads pass (already enrolled, 70% battery, 1600 DPI,
  1000 Hz); no mouse settings were changed.
- Periodic panel shifting fixed by excluding background status polls from
  foreground busy state and skipping polls during foreground operations.
  Plugin rescan retained cached QML; an Omarchy shell restart loaded the fix,
  and the shifting stopped on 2026-09-07.
- Preview: actual native panel capture with fixture configuration, not a hardware-write result.
- 115 Python + 28 Node tests, native lint/manifest, shell/Python/udev checks passed.
- Extracted archive installed/updated/removed with native validation in an isolated config directory; unrelated file preserved; archive audit/checksum passed.
- Buttons, Sensitivity and Profiles pages visually checked; Buttons also checked at 200%.
- GitHub Actions runs the portable verification workflow on pushes and pull requests.
- Full driver parity is not claimed. Marketplace listing requires maintainer approval.
- Model query `35:02` confirmed twice read-only; all production writes now
  verify it under the device lock. Wrong-model, malformed and timeout replies
  are tested to perform zero writes.
- Bar icon clipping fixed with a normalized mouse outline fitted to the canvas bounds.
  All three native panel pages captured and visually reviewed; clearer empty
  profile state and disabled buttons, shorter labels and updated screenshots.
- Fresh-user physical installation, every remapped action, charging/wired,
  suspend/reconnect, report timing and full parity: pending operator/hardware work.

## Physical acceptance checklist

Use scripts/hardware-check.py for the explicitly opted-in reversible debounce
exercise, with enrollment, backup and restoration. Do not run write experiments
on an unknown model or guess unverified encodings.

Record firmware, transport, before/after revision and result for each:

- Permission absent/present; receiver-only, mouse asleep/off, unplug/reconnect.
- Battery low/full, charging on cable, cable removal; never show invented freshness.
- Each physical button -> expected input event; retain an accessible left-click.
- Keyboard, modifier combinations and all media actions -> actual HID/input events.
- Four DPI stages and cycling -> movement measurement, persistence, restoration.
- Polling -> measured report timing, debounce and sleep -> physical behavior.
- Sensor settings -> expected movement behavior; unknown settings stay unchanged.
- Profile apply, reset and restore -> supported settings recovered; unknown bytes preserved.
- Suspend/resume, repeated panel opens, multi-monitor drafts/conflicts.
- Light/dark themes and 100%/200% scaling, keyboard and pointer navigation,
  dirty close/profile switch, dropdown dismissal and visible recovery errors.

Record results in hardware-results.md. An automated mock pass cannot close a
physical checkbox. Full driver parity remains limited by the hardware backlog;
the supported preview can be released independently with these limits disclosed.
