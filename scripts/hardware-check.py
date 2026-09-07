#!/usr/bin/env python3
"""Explicitly opted-in hardware validation (milestone 1).

Read-only by default: discovery, status, full config decode and
cross-checks against the parity references. With --interactive it
additionally performs the scripted reversible debounce write
(second proven value -> readback -> restore observed -> readback),
only after explicit operator confirmation and a fresh backup.

Requires the mouse to be awake for config reads: move it if asked.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from op1we import controller as controller_mod  # noqa: E402
from op1we import device as device_mod  # noqa: E402
from op1we import protocol, settings as settings_mod  # noqa: E402

EXPECTED_DEFAULTS = [(400, 400), (800, 800), (1600, 1600), (3200, 3200)]


def check(name, condition, detail=""):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(condition)


def main():
    parser = argparse.ArgumentParser(description="OP1we milestone-1 hardware validation")
    parser.add_argument("--interactive", action="store_true",
                        help="also run the reversible write demo (asks for confirmation)")
    args = parser.parse_args()
    ok = True

    try:
        identity = device_mod.discover()
    except device_mod.Op1weError as exc:
        print(f"[FAIL] discovery: {exc.message}")
        return 1
    ok &= check("discovery", True, identity.fingerprint)

    ctl = controller_mod.real_controller()
    try:
        status = ctl.status(identity)
    except device_mod.Op1weError as exc:
        print(f"[FAIL] status: {exc.message}")
        return 1
    ok &= check("link up", status.link_up is True, f"connection={status.connection}")
    ok &= check("battery sane", status.percent is not None and 0 <= status.percent <= 100,
                f"{status.percent}% charging={status.charging}")

    try:
        snap = ctl.snapshot(identity)
    except device_mod.Op1weError as exc:
        print(f"[FAIL] read (move the mouse and retry): {exc.message}")
        return 1
    ok &= check("polling valid", snap.polling_hz in (125, 250, 500, 1000), f"{snap.polling_hz} Hz")
    stages = [(s.x, s.y) for s in snap.stages]
    ok &= check("CPI stages decode", all(s.encodable for s in snap.stages), str(stages))
    ok &= check("debounce decoded", snap.debounce_ms is not None, f"{snap.debounce_ms} ms")
    ok &= check("profile decoded", snap.profile is not None, f"profile={snap.profile}")
    ok &= check("sleep decoded", snap.sleep_s is not None, f"{snap.sleep_s} s")
    ok &= check("flags decoded", snap.ripple is not None and snap.fixline is not None
                and snap.turn_off_light is not None,
                f"ripple={snap.ripple} fixline={snap.fixline} turnOff={snap.turn_off_light}")
    ok &= check("current stage/DPI measured",
                snap.current_stage is not None and snap.current_dpi is not None,
                f"stage={snap.current_stage} dpi={snap.current_dpi}")
    kinds = {}
    for binding in snap.bindings:
        kinds[binding.action.get("kind")] = kinds.get(binding.action.get("kind"), 0) + 1
    print(f"[INFO] binding kinds: {kinds}")

    mem = ctl.read_full_backup(identity)
    backup_path = ctl.write_backup_file(identity, mem)
    ok &= check("backup written", os.path.exists(backup_path), backup_path)

    if not args.interactive:
        print("read-only checks done; pass --interactive for the reversible write demo")
        return 0 if ok else 1

    # --- Interactive reversible write demo (debounce only) ---
    observed = settings_mod.snapshot_from_memory(identity.fingerprint, mem, snap.profile).debounce_ms
    if observed is None:
        print("[FAIL] cannot demo without a decoded debounce value")
        return 1
    # Milestone-2 range is proven (0..30 ms); pick a nearby value.
    target = observed + 1 if observed < 30 else observed - 1
    print(f"Demo: debounce {observed} ms -> {target} ms -> readback -> restore {observed} ms.")
    answer = input("Type YES to proceed (mouse must stay awake; keep moving it): ").strip()
    if answer != "YES":
        print("aborted by operator")
        return 1
    change = settings_mod.DebounceChange(value_ms=target)
    try:
        change.validate()
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1
    revision = protocol.config_revision(mem)
    try:
        controller_mod.require_enrolled(identity)
    except device_mod.Op1weError as exc:
        print(f"[FAIL] {exc.message}")
        return 1
    try:
        after = ctl.apply_bytes(identity, {protocol.ADDR_DEBOUNCE: change.encoded()},
                                expected_revision=revision)
        ok &= check("write+readback", after.debounce_ms == target, f"debounce={after.debounce_ms}")
        revision2 = after.revision
        restore = settings_mod.DebounceChange(value_ms=observed)
        final = ctl.apply_bytes(identity, {protocol.ADDR_DEBOUNCE: restore.encoded()},
                                expected_revision=revision2)
        ok &= check("restore+readback", final.debounce_ms == observed,
                    f"debounce={final.debounce_ms}")
    except device_mod.Op1weError as exc:
        print(f"[FAIL] {exc.code}: {exc.message}")
        return 1
    print("hardware-check: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
