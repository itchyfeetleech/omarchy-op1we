"""Read/apply/verify orchestration, revision checks and recovery backups.

Hardware is authoritative: opening never writes; apply validates,
backs up, writes only verified fields, and verifies by readback.
No success is reported before verification.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from . import protocol, settings as settings_mod
from .device import DeviceIdentity, DeviceLock, HidrawTransport, Op1weError

STATUS_TIMEOUT = 2.0
CONFIG_TIMEOUT = 5.0
APPLY_TIMEOUT = 10.0
BACKUP_RETAIN = 10


def state_dir() -> str:
    """Persistent state dir for backups/enrollment (override for tests)."""
    override = os.environ.get("OP1WE_STATE_DIR")
    if override:
        return override
    base = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(base, "op1we-control")


def _ensure_private_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def enrollment_path() -> str:
    return os.path.join(state_dir(), "enrollment.json")


def load_enrollment() -> dict | None:
    try:
        with open(enrollment_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_enrollment(identity: DeviceIdentity, model: str = "OP1we") -> dict:
    _ensure_private_dir(state_dir())
    record = {
        "apiVersion": protocol.API_VERSION,
        "model": model,
        "fingerprint": identity.fingerprint,
        "identity": identity.describe(),
        "enrolledAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "Operator-confirmed pairing: the mouse on this receiver is the OP1we.",
    }
    path = enrollment_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return record


def is_enrolled(identity: DeviceIdentity) -> bool:
    record = load_enrollment()
    return bool(record) and record.get("fingerprint") == identity.fingerprint


def require_enrolled(identity: DeviceIdentity) -> None:
    record = load_enrollment()
    if record is None:
        raise Op1weError(
            "not-enrolled",
            "no pairing enrollment for this receiver; confirm the paired "
            "mouse is the OP1we and run 'op1we enroll --confirm'",
        )
    if record.get("fingerprint") != identity.fingerprint:
        raise Op1weError(
            "device-changed",
            "receiver fingerprint differs from enrollment; re-enroll only "
            "after confirming the paired mouse is still the OP1we",
        )


@dataclass
class Controller:
    """High-level operations over an injectable transport factory (fake in tests)."""

    open_transport: callable  # (identity) -> context manager with .exchange()

    def status(self, identity: DeviceIdentity) -> settings_mod.Status:
        # Battery first: the receiver answers from cache while the
        # link is momentarily down (docs/protocol.md).
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                percent: int | None = None
                charging: int | None = None
                try:
                    reply = transport.exchange(
                        protocol.OP_BATTERY, b"", STATUS_TIMEOUT
                    )
                    percent, charging = protocol.parse_battery(bytes(reply))
                except Op1weError:
                    percent, charging = None, None
                link: bool | None = None
                try:
                    reply = transport.exchange(protocol.OP_LINK, b"", STATUS_TIMEOUT)
                    link = protocol.parse_link(bytes(reply))
                except Op1weError:
                    link = None
                profile: int | None = None
                try:
                    reply = transport.exchange(protocol.OP_PROFILE, b"", STATUS_TIMEOUT)
                    profile = protocol.parse_profile(bytes(reply))
                except Op1weError:
                    profile = None
        if percent is None and link is None:
            connection = "unavailable"
        elif link is False:
            connection = "receiver-only"
        elif link is True:
            connection = "connected"
        else:
            connection = "receiver-only" if percent is not None else "unavailable"
        return settings_mod.Status(
            connection=connection,
            percent=percent,
            charging=charging,
            profile=profile,
            link_up=link,
        )

    def read_memory(
        self,
        identity: DeviceIdentity,
        ranges: list[tuple[int, int]] | None = None,
        timeout: float = CONFIG_TIMEOUT,
    ) -> dict[int, int]:
        """Read raw config bytes. Raises asleep/unavailable distinctly."""
        ranges = ranges if ranges is not None else [(protocol.CONFIG_LO, protocol.CONFIG_HI)]
        mem: dict[int, int] = {}
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                for lo, hi in ranges:
                    addr = lo
                    while addr <= hi:
                        chunk = min(protocol.MAX_DATA_PER_FRAME, hi - addr + 1)
                        payload = protocol.ee_read_payload(addr, chunk)
                        deadline = time.monotonic() + timeout
                        data: bytes | None = None
                        while time.monotonic() < deadline:
                            try:
                                reply = transport.exchange(
                                    protocol.OP_EEPROM_READ, payload, 0.5
                                )
                            except Op1weError:
                                continue
                            try:
                                data = protocol.ee_parse_data(
                                    bytes(reply), protocol.OP_EEPROM_READ, addr, chunk
                                )
                            except ValueError:
                                continue
                            break
                        if data is None:
                            self._raise_not_readable(transport)
                        for offset, byte in enumerate(data):
                            mem[addr + offset] = byte
                        addr += chunk
        return mem

    def _raise_not_readable(self, transport) -> None:
        # Distinguish sleeping mouse from dead link: battery is cached.
        try:
            reply = transport.exchange(protocol.OP_BATTERY, b"", STATUS_TIMEOUT)
            protocol.parse_battery(bytes(reply))
            raise Op1weError(
                "asleep",
                "mouse is asleep; move it and retry",
                retryable=True,
            )
        except Op1weError as exc:
            if exc.code == "asleep":
                raise
        try:
            reply = transport.exchange(protocol.OP_LINK, b"", STATUS_TIMEOUT)
            if not protocol.parse_link(bytes(reply)):
                raise Op1weError("mouse-offline", "mouse is offline; move or power it on")
        except Op1weError as exc:
            if exc.code == "mouse-offline":
                raise
        raise Op1weError("unavailable", "receiver is not answering", retryable=True)

    def snapshot(
        self, identity: DeviceIdentity
    ) -> settings_mod.SettingsSnapshot:
        mem = self.read_memory(identity)
        profile: int | None = None
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                try:
                    reply = transport.exchange(protocol.OP_PROFILE, b"", STATUS_TIMEOUT)
                    profile = protocol.parse_profile(bytes(reply))
                except Op1weError:
                    profile = None
        return settings_mod.snapshot_from_memory(identity.fingerprint, mem, profile)

    def write_backup_file(self, identity: DeviceIdentity, mem: dict[int, int]) -> str:
        _ensure_private_dir(state_dir())
        stamp = time.strftime("%Y%m%dT%H%M%S")
        payload = {
            "apiVersion": protocol.API_VERSION,
            "kind": "op1we-backup",
            "fingerprint": identity.fingerprint,
            "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ranges": [[protocol.CONFIG_LO, protocol.CONFIG_HI]],
            "bytes": {f"{addr:04x}": value for addr, value in sorted(mem.items())},
        }
        body = json.dumps(payload, indent=1)
        for attempt in range(100):
            suffix = "" if attempt == 0 else f"-{attempt}"
            path = os.path.join(state_dir(), f"backup-{stamp}{suffix}.json")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
            break
        else:
            raise Op1weError("unavailable", "cannot allocate a backup file name")
        self._prune_backups()
        return path

    def _prune_backups(self) -> None:
        try:
            names = sorted(
                n for n in os.listdir(state_dir()) if n.startswith("backup-") and n.endswith(".json")
            )
        except OSError:
            return
        for stale in names[:-BACKUP_RETAIN]:
            try:
                os.remove(os.path.join(state_dir(), stale))
            except OSError:
                pass

    def load_backup_file(self, path: str) -> dict[int, int]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            raise Op1weError("invalid-input", f"cannot read backup file: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("kind") != "op1we-backup":
            raise Op1weError("invalid-input", "not an OP1we backup file")
        raw = payload.get("bytes")
        if not isinstance(raw, dict):
            raise Op1weError("invalid-input", "backup file has no byte map")
        mem: dict[int, int] = {}
        for key, value in raw.items():
            try:
                addr = int(key, 16)
            except ValueError as exc:
                raise Op1weError("invalid-input", f"bad backup address {key!r}") from exc
            if not isinstance(value, int) or not 0 <= value <= 0xFF:
                raise Op1weError("invalid-input", f"bad backup value at {key!r}")
            if not protocol.CONFIG_LO <= addr <= protocol.CONFIG_HI:
                raise Op1weError("invalid-input", f"backup address 0x{addr:04x} outside config region")
            mem[addr] = value
        if not mem:
            raise Op1weError("invalid-input", "backup file is empty")
        return mem

    def apply_bytes(
        self,
        identity: DeviceIdentity,
        writes: dict[int, bytes],
        expected_revision: str | None = None,
        timeout: float = APPLY_TIMEOUT,
    ) -> settings_mod.SettingsSnapshot:
        """Sole production write path: validate → backup → write → verify.

        `writes` maps start address to bytes (each entry ≤10 bytes and
        inside the config region). Rejects stale revisions before
        writing a single byte. Verifies every byte by readback and
        reports partial/uncertain state instead of success.
        """
        for addr, data in writes.items():
            protocol.ee_write_payload(addr, bytes(data))  # bounds checked here
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                current = self._read_locked(transport, [(protocol.CONFIG_LO, protocol.CONFIG_HI)], timeout)
                if expected_revision is not None and protocol.config_revision(current) != expected_revision:
                    raise Op1weError(
                        "stale-revision",
                        "device state changed since it was read; re-read and retry",
                        retryable=True,
                    )
                backup_path = self.write_backup_file(identity, current)
                try:
                    for addr, data in sorted(writes.items()):
                        payload = protocol.ee_write_payload(addr, bytes(data))
                        reply = self._exchange_wake(
                            transport, protocol.OP_EEPROM_WRITE, payload, timeout
                        )
                        if reply is None:
                            raise Op1weError(
                                "write-failed",
                                f"write at 0x{addr:04x} was not acknowledged; "
                                f"state uncertain, backup at {backup_path}",
                            )
                        # Readback-verify before touching the next chunk.
                        check = self._exchange_wake(
                            transport,
                            protocol.OP_EEPROM_READ,
                            protocol.ee_read_payload(addr, len(data)),
                            timeout,
                        )
                        if check is None:
                            raise Op1weError(
                                "verify-failed",
                                f"write at 0x{addr:04x} could not be verified; "
                                f"state uncertain, backup at {backup_path}",
                            )
                        echoed = protocol.ee_parse_data(
                            check, protocol.OP_EEPROM_READ, addr, len(data)
                        )
                        if echoed != bytes(data):
                            raise Op1weError(
                                "verify-failed",
                                f"readback mismatch at 0x{addr:04x}; state uncertain, "
                                f"backup at {backup_path}",
                            )
                except Op1weError:
                    raise
                fresh = self._read_locked(transport, [(protocol.CONFIG_LO, protocol.CONFIG_HI)], timeout)
        return settings_mod.snapshot_from_memory(identity.fingerprint, fresh, None)

    def _read_locked(self, transport, ranges, timeout: float) -> dict[int, int]:
        mem: dict[int, int] = {}
        for lo, hi in ranges:
            addr = lo
            while addr <= hi:
                chunk = min(protocol.MAX_DATA_PER_FRAME, hi - addr + 1)
                reply = self._exchange_wake(
                    transport, protocol.OP_EEPROM_READ,
                    protocol.ee_read_payload(addr, chunk), timeout,
                )
                if reply is None:
                    self._raise_not_readable(transport)
                assert reply is not None  # _raise_not_readable always raises
                data = protocol.ee_parse_data(reply, protocol.OP_EEPROM_READ, addr, chunk)
                for offset, byte in enumerate(data):
                    mem[addr + offset] = byte
                addr += chunk
        return mem

    def _exchange_wake(self, transport, opcode: int, payload: bytes, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return transport.exchange(opcode, payload, 0.5)
            except Op1weError:
                continue
        return None


def real_controller() -> Controller:
    return Controller(open_transport=lambda identity: HidrawTransport(identity))
