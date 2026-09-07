# 0.4.0 development preview — release checklist

## Prepared release text

OP1we Control 0.4.0 adds a native, theme-aware mouse diagram with annotated
button dropdowns, a focused custom-binding editor, compact DPI stages and
separate Sensitivity and Profiles & device pages. Apply/Cancel stay visible.
Safe development installation/removal, allowlisted archives, checksums and
portable CI are included.

This preview is not a complete replacement for the vendor driver yet.
Remaining blockers: LOD, stage-count writes, DPI above 10000, several special
actions, full physical button mapping, key/media trigger verification,
wired/charging acceptance and paired-model discrimination. See parity.md.

## Marketplace submission draft

- Name: OP1we Control
- ID: hoppcx.op1we
- Category: Hardware
- Tags: mouse, OP1we, battery, settings
- Description: Native Omarchy OP1we battery widget and annotated mouse controls.
- Preview: docs/preview.png (native themed render using fixture data)
- Repository URL: pending — no Git remote configured
- Submission URL: pending — not submitted

Requirements rechecked on 2026-09-07 against the
[marketplace publishing guide](https://plugins.omarchy.org/publish.html):
public GitHub repository, root manifest, README/license and safe install/removal.
Listings undergo automated validation and maintainer review; plugins run
unsandboxed. Publication/submission remains a separate release action as
specified in PLAN.md. Do not publish a full-parity claim for this preview.

## Acceptance record

- Native renderer: isolated Quickshell, current Omarchy theme; live installed panel also checked after one user-approved shell restart, with no plugin QML errors.
- Live receiver currently reports permission denied; udev setup and enrollment remain user setup steps.
- Preview: actual native panel capture with fixture configuration, not a hardware-write result.
- 113 Python + 28 Node tests, native lint/manifest, shell/Python/udev checks passed.
- Extracted archive installed/updated/removed with native validation in an isolated config directory; unrelated file preserved; archive audit/checksum passed.
- Buttons, Sensitivity and Profiles pages visually checked; Buttons also checked at 200%.
- CI workflow delivered; remote execution awaits a GitHub repository.
- Publication readiness rechecked 2026-09-07: 43 tracked files, no
  secrets, `dist/` ignored, manifest valid; blocked only on
  owner/name confirmation, explicit publish authorization, the manual
  issue submission and external review (see PLAN.md).
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
physical checkbox. Full milestone-4 acceptance and publication are still gated
by the earlier hardware blockers.
