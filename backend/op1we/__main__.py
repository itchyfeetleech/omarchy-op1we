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
    "ambiguous-device",
    "not-enrolled",
    "device-changed",
    "asleep",
    "mouse-offline",
}


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
        mem = ctl.read_memory(identity)
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
        current = ctl.read_memory(identity)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    # Write only differing 10-byte-aligned chunks (fewer flash writes).
    writes: dict[int, bytes] = {}
    addr = protocol.CONFIG_LO
    while addr <= protocol.CONFIG_HI:
        chunk_addrs = [a for a in range(addr, min(addr + 10, protocol.CONFIG_HI + 1)) if a in mem]
        if chunk_addrs:
            lo, hi = chunk_addrs[0], chunk_addrs[-1]
            want = bytes(mem[a] for a in range(lo, hi + 1))
            have = bytes(current.get(a, -1) for a in range(lo, hi + 1))
            if want != have:
                writes[lo] = want
        addr += 10
    if not writes:
        _emit(_envelope(rid, True, {"restored": False, "reason": "already in sync"}, None))
        return EXIT_OK
    try:
        snap = ctl.apply_bytes(identity, writes, expected_revision=args.expected_revision)
    except device_mod.Op1weError as exc:
        return _fail(rid, exc.code, exc.message, exc.retryable)
    _emit(
        _envelope(
            rid,
            True,
            {"restored": True, "chunks": len(writes), "snapshot": snap.describe()},
            None,
        )
    )
    return EXIT_OK


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
    except BrokenPipeError:
        return EXIT_PROTOCOL
    return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
