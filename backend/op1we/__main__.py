"""OP1we helper CLI: one versioned JSON document per invocation.

Stdout carries exactly one JSON object; diagnostics go to stderr.
Exit codes: 0 success, 2 invalid input, 3 unavailable/permission/
unsupported, 4 busy/conflict, 5 protocol/verification failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, controller as controller_mod, device as device_mod, protocol

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_UNAVAILABLE = 3
EXIT_BUSY = 4
EXIT_PROTOCOL = 5

_BUSY_CODES = {"busy", "stale-revision"}
_UNAVAILABLE_CODES = {
    "unavailable",
    "permission",
    "unsupported-device",
    "unsupported",
    "ambiguous-device",
    "not-enrolled",
    "device-changed",
    "asleep",
    "mouse-offline",
}

STDIN_MAX_BYTES = 65536


def _envelope(request_id: str, ok: bool, data=None, error=None) -> dict:
    doc: dict = {"apiVersion": protocol.API_VERSION, "requestId": request_id, "ok": ok}
    doc["data"] = data
    doc["error"] = error
    return doc


def _emit(doc: dict) -> None:
    sys.stdout.write(json.dumps(doc) + "\n")
    sys.stdout.flush()


def _fail(request_id: str, code: str, message: str, retryable: bool = False) -> int:
    _emit(_envelope(request_id, False, None, {"code": code, "message": message, "retryable": retryable}))
    if code in _BUSY_CODES:
        return EXIT_BUSY
    if code in _UNAVAILABLE_CODES or code == "invalid-input":
        return EXIT_INVALID if code == "invalid-input" else EXIT_UNAVAILABLE
    return EXIT_PROTOCOL


def _request_id(args) -> str:
    if args.request_id:
        return args.request_id[:64]
    return f"{os.getpid():x}-{time.time_ns():x}"[-32:]


def cmd_probe(args) -> int:
    rid = _request_id(args)
    try:
        identity = device_mod.discover()
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    record = controller_mod.load_enrollment()
    _emit(
        _envelope(
            rid,
            True,
            {
                "identity": identity.describe(),
                "enrolled": record is not None
                and record.get("fingerprint") == identity.fingerprint,
                "helperVersion": __version__,
            },
            None,
        )
    )
    return EXIT_OK


def cmd_enroll(args) -> int:
    rid = _request_id(args)
    if not args.confirm:
        return _fail(
            rid,
            "invalid-input",
            "refusing to enroll without --confirm; verify the paired mouse is the OP1we first",
        )
    try:
        identity = device_mod.discover()
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    record = controller_mod.save_enrollment(identity)
    _emit(_envelope(rid, True, {"enrollment": record}, None))
    return EXIT_OK


def _discover_or_fail(rid: str):
    try:
        return device_mod.discover(), None
    except device_mod.Op1weError as exc:
        return None, (exc.code, exc.message, exc.retryable)


def cmd_status(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    ctl = controller_mod.real_controller()
    try:
        status = ctl.status(identity)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(
        _envelope(
            rid,
            True,
            {
                "identity": identity.describe(),
                "enrolled": controller_mod.is_enrolled(identity),
                "connection": status.connection,
                "batteryPercent": status.percent,
                "charging": status.charging,
                "profile": status.profile,
                "linkUp": status.link_up,
                "observedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            None,
        )
    )
    return EXIT_OK


def cmd_read(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    ctl = controller_mod.real_controller()
    try:
        snap = ctl.snapshot(identity)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(_envelope(rid, True, snap.describe(), None))
    return EXIT_OK


def cmd_backup(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    ctl = controller_mod.real_controller()
    try:
        mem = ctl.read_full_backup(identity)
        path = ctl.write_backup_file(identity, mem)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(
        _envelope(
            rid,
            True,
            {"path": path, "bytes": len(mem), "revision": protocol.config_revision(mem)},
            None,
        )
    )
    return EXIT_OK


def _restore_from_mem(rid: str, identity, ctl, mem, expected_revision):
    try:
        snap, chunks, changed = ctl.restore_mem(
            identity, mem, expected_revision=expected_revision
        )
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(
        _envelope(
            rid,
            True,
            {"restored": changed, "chunks": chunks, "snapshot": snap.describe()},
            None,
        )
    )
    return EXIT_OK


def cmd_restore(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    ctl = controller_mod.real_controller()
    try:
        controller_mod.require_enrolled(identity)
        if identity.fingerprint != _backup_fingerprint(args.file):
            return _fail(
                rid,
                "invalid-input",
                "backup was captured from a different receiver; refusing cross-device restore",
            )
        mem = ctl.load_backup_file(args.file)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    return _restore_from_mem(rid, identity, ctl, mem, args.expected_revision)


def _read_stdin_json(rid: str):
    try:
        raw = sys.stdin.buffer.read(STDIN_MAX_BYTES + 1)
    except OSError as exc:
        return None, ("invalid-input", f"cannot read stdin: {exc}", False)
    if len(raw) > STDIN_MAX_BYTES:
        return None, ("invalid-input",
                       f"request exceeds {STDIN_MAX_BYTES} bytes", False)
    try:
        return json.loads(raw.decode("utf-8")), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, ("invalid-input", f"malformed JSON: {exc}", False)


def cmd_apply(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    request, failure = _read_stdin_json(rid)
    if failure is not None:
        return _fail(rid, *failure)
    if not isinstance(request, dict):
        return _fail(rid, "invalid-input", "apply request must be a JSON object")
    if request.get("apiVersion") != protocol.API_VERSION:
        return _fail(rid, "invalid-input", "unsupported apiVersion (want 1)")
    if "deviceFingerprint" in request and request["deviceFingerprint"] != identity.fingerprint:
        return _fail(rid, "invalid-input", "deviceFingerprint does not match this receiver")
    changes = request.get("changes")
    if not isinstance(changes, dict):
        return _fail(rid, "invalid-input", "apply request needs a changes object")
    ctl = controller_mod.real_controller()
    try:
        controller_mod.require_enrolled(identity)
        snap, chunks = ctl.apply(identity, changes,
                                 expected_revision=request.get("expectedRevision"))
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(_envelope(rid, True,
                    {"applied": chunks > 0, "chunks": chunks,
                     "snapshot": snap.describe()}, None))
    return EXIT_OK


def cmd_reset(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    ctl = controller_mod.real_controller()
    try:
        controller_mod.require_enrolled(identity)
        current = ctl.read_memory(identity)
        writes = controller_mod.plan_reset(current)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    if not writes:
        _emit(_envelope(rid, True, {"reset": False, "reason": "already at defaults"}, None))
        return EXIT_OK
    try:
        snap = ctl.apply_bytes(identity, writes, expected_revision=args.expected_revision)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(_envelope(rid, True,
                    {"reset": True, "chunks": len(writes),
                     "snapshot": snap.describe()}, None))
    return EXIT_OK


def cmd_listen(args) -> int:
    rid = _request_id(args)
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    try:
        seconds = float(args.seconds)
    except (TypeError, ValueError):
        return _fail(rid, "invalid-input", "seconds must be a number")
    if not 1 <= seconds <= 300:
        return _fail(rid, "invalid-input", "seconds must be within 1..300")
    try:
        with device_mod.DeviceLock(identity):
            with device_mod.HidrawTransport(identity) as transport:
                events = transport.listen_stage(seconds)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(_envelope(rid, True, {"events": events}, None))
    return EXIT_OK


def cmd_profile(args) -> int:
    rid = _request_id(args)
    action = args.profile_action
    if action == "list":
        _emit(_envelope(rid, True, {"profiles": controller_mod.list_profiles()}, None))
        return EXIT_OK
    if action == "delete":
        try:
            controller_mod.delete_profile(args.name)
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        _emit(_envelope(rid, True, {"deleted": args.name}, None))
        return EXIT_OK
    if action == "export":
        try:
            mem, payload = controller_mod.load_profile_bytes(args.name)
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        try:
            with open(args.file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1)
        except OSError as exc:
            return _fail(rid, "invalid-input", f"cannot write {args.file}: {exc}")
        _emit(_envelope(rid, True, {"exported": args.name, "file": args.file,
                                    "bytes": len(mem)}, None))
        return EXIT_OK
    if action == "import":
        try:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            return _fail(rid, "invalid-input", f"cannot read {args.file}: {exc}")
        if not isinstance(payload, dict) or payload.get("kind") != "op1we-profile":
            return _fail(rid, "invalid-input", "not an OP1we profile file")
        raw = payload.get("bytes")
        if not isinstance(raw, dict) or not raw:
            return _fail(rid, "invalid-input", "profile file has no byte map")
        # Validate the byte map by round-tripping through the loader path.
        try:
            tmp = {"kind": "op1we-profile", "bytes": raw}
            _validate_byte_map(tmp)
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        name = args.name or payload.get("name") or "imported"
        try:
            controller_mod.validate_profile_name(name)
            path = controller_mod.profile_path(name)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                payload["name"] = name
                json.dump(payload, handle, indent=1)
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        except OSError as exc:
            return _fail(rid, "invalid-input", f"cannot store profile: {exc}")
        _emit(_envelope(rid, True, {"imported": name}, None))
        return EXIT_OK
    identity, failure = _discover_or_fail(rid)
    if failure is not None:
        return _fail(rid, *failure)
    assert identity is not None
    ctl = controller_mod.real_controller()
    if action == "save":
        try:
            mem = ctl.read_full_backup(identity)
            info = controller_mod.save_profile(identity, args.name, mem)
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        _emit(_envelope(rid, True, info, None))
        return EXIT_OK
    if action == "show":
        try:
            mem, payload = controller_mod.load_profile_bytes(args.name)
            snap = controller_mod.settings_mod.snapshot_from_memory(
                payload.get("fingerprint", ""), mem, None)
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        _emit(_envelope(rid, True, {"profile": payload.get("name"),
                                    "snapshot": snap.describe()}, None))
        return EXIT_OK
    if action == "apply":
        try:
            controller_mod.require_enrolled(identity)
            mem, payload = controller_mod.load_profile_bytes(args.name)
            if payload.get("fingerprint") != identity.fingerprint:
                return _fail(rid, "invalid-input",
                              "profile was captured from a different receiver")
        except device_mod.Op1weError as exc:
            return _fail(rid, exc.code, exc.message, exc.retryable)
        return _restore_from_mem(rid, identity, ctl, mem, args.expected_revision)
    return _fail(rid, "invalid-input", f"unknown profile action {action!r}")


def _validate_byte_map(payload: dict) -> None:
    type5_end = protocol.TYPE5_BASE + protocol.KEY_UI_SLOTS * protocol.TYPE5_STRIDE - 1
    for key, value in payload["bytes"].items():
        try:
            addr = int(key, 16)
        except ValueError as exc:
            raise device_mod.Op1weError("invalid-input", f"bad address {key!r}") from exc
        if not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise device_mod.Op1weError("invalid-input", f"bad value at {key!r}")
        if not ((protocol.CONFIG_LO <= addr <= protocol.CONFIG_HI)
                or (protocol.TYPE5_BASE <= addr <= type5_end)):
            raise device_mod.Op1weError(
                "invalid-input", f"address 0x{addr:04x} outside allowed regions")


def _backup_fingerprint(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload.get("fingerprint") if isinstance(payload, dict) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="op1we", description="OP1we Control helper (milestone 1)")
    parser.add_argument("--request-id", default=None, help="caller-supplied request id")
    sub = parser.add_subparsers(dest="command", required=True)
    # --request-id also accepted after the subcommand so argv order never matters.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--request-id", default=None, help="caller-supplied request id")
    sub.add_parser("probe", parents=[common], help="discover the receiver without touching configuration")
    enroll = sub.add_parser("enroll", parents=[common], help="record explicit OP1we pairing enrollment")
    enroll.add_argument("--confirm", action="store_true", help="confirm the paired mouse is the OP1we")
    sub.add_parser("status", parents=[common], help="battery/link/profile snapshot (reads only)")
    sub.add_parser("read", parents=[common], help="decode full configuration (reads only)")
    sub.add_parser("backup", parents=[common], help="save raw config bytes to a private backup file")
    restore = sub.add_parser("restore", parents=[common], help="verified restore from a backup file")
    restore.add_argument("--file", required=True, help="backup JSON path")
    restore.add_argument("--expected-revision", default=None, help="reject stale device state")
    sub.add_parser("apply", parents=[common], help="validated apply; request JSON on stdin")
    reset = sub.add_parser("reset", parents=[common], help="vendor-documented defaults (covered fields)")
    reset.add_argument("--expected-revision", default=None, help="reject stale device state")
    listen = sub.add_parser("listen", parents=[common], help="listen for CPI-stage notifications")
    listen.add_argument("--seconds", default=10, help="listen window 1..300")
    profile = sub.add_parser("profile", parents=[common], help="host-side profiles")
    profile.add_argument("profile_action",
                         choices=["save", "list", "show", "delete", "export", "import", "apply"])
    profile.add_argument("--name", default=None, help="profile name")
    profile.add_argument("--file", default=None, help="export/import file path")
    profile.add_argument("--expected-revision", default=None, help="reject stale device state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            return cmd_probe(args)
        if args.command == "enroll":
            return cmd_enroll(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "read":
            return cmd_read(args)
        if args.command == "backup":
            return cmd_backup(args)
        if args.command == "restore":
            return cmd_restore(args)
        if args.command == "apply":
            return cmd_apply(args)
        if args.command == "reset":
            return cmd_reset(args)
        if args.command == "listen":
            return cmd_listen(args)
        if args.command == "profile":
            return cmd_profile(args)
    except BrokenPipeError:
        return EXIT_PROTOCOL
    return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
