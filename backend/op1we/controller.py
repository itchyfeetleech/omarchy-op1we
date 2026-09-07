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

# Whole-operation budgets (F-010): each bounds one command end to
# end — all chunks, probes and recovery — never per chunk. Values
# are unchanged from milestone 1; only the semantics tightened
# (replies answer in well under 100 ms while awake).
STATUS_TIMEOUT = 2.0
CONFIG_TIMEOUT = 5.0
APPLY_TIMEOUT = 10.0
# Share of a read budget reserved for asleep/offline classification
# probes so a dead budget still reports the cause, inside the total.
PROBE_RESERVE = 1.0
# Single write-attempt ceiling (F-002): writes are never retried, so
# one attempt must cover a slow-but-live ACK on its own.
WRITE_ACK_TIMEOUT = 2.0
# Read-only recovery probe ceiling after a failed write (F-002).
RECOVERY_PROBE_TIMEOUT = 1.0
BACKUP_RETAIN = 10
PROFILE_RETAIN = 20


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _read_until(deadline: float) -> float:
    """Read-phase limit leaving a classification reserve (F-010)."""
    reserve = min(PROBE_RESERVE, _remaining(deadline) / 2)
    return deadline - reserve


def _type5_addr_map(type5: dict[int, bytes]) -> dict[int, int]:
    """Flatten slot payloads to {address: byte} for maps/revisions."""
    out: dict[int, int] = {}
    for slot, payload in type5.items():
        base = protocol.type5_addr(slot)
        for offset, byte in enumerate(payload):
            out[base + offset] = byte
    return out


def state_dir() -> str:
    """Persistent state dir for backups/enrollment (override for tests)."""
    override = os.environ.get("OP1WE_STATE_DIR")
    if override:
        return override
    base = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(base, "op1we-control")


def _ensure_private_dir(path: str) -> None:
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise Op1weError(
            "unavailable", f"cannot create state dir {path}: {exc}"
        ) from exc
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
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError as exc:
        raise Op1weError(
            "unavailable", f"cannot write enrollment: {exc}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
    except OSError as exc:
        raise Op1weError(
            "unavailable", f"cannot write enrollment: {exc}"
        ) from exc
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return record


def _enrollment_matches(record: dict | None, identity: DeviceIdentity) -> bool:
    """Enrollment continuity (F-006): fingerprint plus USB path.

    The fingerprint is receiver-class attributes (shared across the
    WE series), so a same-descriptor replacement on another port must
    not inherit confirmation. Same-port replacement stays
    undetectable until CID/MID discrimination lands (deferred).
    """
    if not record or record.get("fingerprint") != identity.fingerprint:
        return False
    enrolled_path = (record.get("identity") or {}).get("usbPath")
    return enrolled_path == identity.usb_path


def is_enrolled(identity: DeviceIdentity) -> bool:
    return _enrollment_matches(load_enrollment(), identity)


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
    enrolled_path = (record.get("identity") or {}).get("usbPath")
    if enrolled_path != identity.usb_path:
        raise Op1weError(
            "device-changed",
            f"receiver USB path changed ({enrolled_path} -> "
            f"{identity.usb_path}); if it was replugged or replaced, "
            "confirm the paired mouse is still the OP1we and re-enroll",
        )


@dataclass
class Controller:
    """High-level operations over an injectable transport factory (fake in tests)."""

    open_transport: callable  # (identity) -> context manager with .exchange()

    @staticmethod
    def _require_op1we(transport, deadline: float) -> tuple[int, int]:
        try:
            reply = transport.exchange(protocol.OP_MODEL, protocol.MODEL_QUERY,
                                       min(0.6, _remaining(deadline)))
            model = protocol.parse_model(bytes(reply))
        except ValueError as exc:
            raise Op1weError("unsupported", "receiver model reply is invalid; refusing writes") from exc
        if model != protocol.OP1WE_MODEL:
            raise Op1weError("unsupported", "paired device is not an OP1we; refusing writes")
        return model

    def verify_model(self, identity: DeviceIdentity) -> tuple[int, int]:
        deadline = time.monotonic() + STATUS_TIMEOUT
        with DeviceLock(identity), self.open_transport(identity) as transport:
            return self._require_op1we(transport, deadline)

    def status(self, identity: DeviceIdentity) -> settings_mod.Status:
        # Battery first: the receiver answers from cache while the
        # link is momentarily down (docs/protocol.md). The three
        # probes share one whole-operation budget; a malformed reply
        # degrades its value to unknown instead of escaping (F-009).
        deadline = time.monotonic() + STATUS_TIMEOUT
        percent: int | None = None
        charging: int | None = None
        link: bool | None = None
        profile: int | None = None
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                try:
                    reply = transport.exchange(
                        protocol.OP_BATTERY, b"", _remaining(deadline)
                    )
                    percent, charging = protocol.parse_battery(bytes(reply))
                except (Op1weError, ValueError):
                    percent, charging = None, None
                try:
                    reply = transport.exchange(
                        protocol.OP_LINK, b"", _remaining(deadline)
                    )
                    link = protocol.parse_link(bytes(reply))
                except (Op1weError, ValueError):
                    link = None
                try:
                    reply = transport.exchange(
                        protocol.OP_PROFILE, b"", _remaining(deadline)
                    )
                    profile = protocol.parse_profile(bytes(reply))
                except (Op1weError, ValueError):
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
            battery_fresh=percent is not None and link is True,
        )

    def read_memory(
        self,
        identity: DeviceIdentity,
        ranges: list[tuple[int, int]] | None = None,
        timeout: float = CONFIG_TIMEOUT,
    ) -> dict[int, int]:
        """Read raw config bytes. Raises asleep/unavailable distinctly.

        `timeout` bounds the whole read: every chunk shares one
        deadline instead of renewing it (F-010).
        """
        ranges = ranges if ranges is not None else [(protocol.CONFIG_LO, protocol.CONFIG_HI)]
        deadline = time.monotonic() + timeout
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                return self._read_ranges_locked(transport, ranges, deadline)

    def _raise_not_readable(self, transport, deadline: float) -> None:
        # Distinguish sleeping mouse from dead link: battery is cached.
        # Probes are capped slices of the remaining operation budget so
        # classification never exceeds it (F-010); malformed replies
        # simply fail the probe instead of escaping (F-009).
        try:
            reply = transport.exchange(
                protocol.OP_BATTERY, b"", min(0.5, _remaining(deadline))
            )
            protocol.parse_battery(bytes(reply))
            raise Op1weError(
                "asleep",
                "mouse is asleep; move it and retry",
                retryable=True,
            )
        except Op1weError as exc:
            if exc.code == "asleep":
                raise
        except ValueError:
            pass
        try:
            reply = transport.exchange(
                protocol.OP_LINK, b"", min(0.5, _remaining(deadline))
            )
            if not protocol.parse_link(bytes(reply)):
                raise Op1weError("mouse-offline", "mouse is offline; move or power it on")
        except Op1weError as exc:
            if exc.code == "mouse-offline":
                raise
        except ValueError:
            pass
        raise Op1weError("unavailable", "receiver is not answering", retryable=True)

    def _read_config_and_payloads(
        self, transport, deadline: float
    ) -> tuple[dict[int, int], dict[int, bytes]]:
        """Canonical locked read: config plus every active payload.

        Active = slots 1..12 whose record references a type-5
        payload. One lock, one transport, one budget: snapshots and
        backups can no longer mix generations (F-003), and recovery
        captures stop depending on the write plan (F-005).
        """
        config = self._read_ranges_locked(
            transport, [(protocol.CONFIG_LO, protocol.CONFIG_HI)], deadline
        )
        type5: dict[int, bytes] = {}
        for slot in range(1, protocol.KEY_UI_SLOTS + 1):
            record_type = config.get(
                protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
            )
            if record_type != protocol.KEY_TYPE_KEY:
                continue
            base = protocol.type5_addr(slot)
            chunk = self._read_ranges_locked(
                transport, [(base, base + protocol.TYPE5_SLOT_LEN - 1)], deadline
            )
            type5[slot] = bytes(
                chunk.get(base + k, 0xFF)
                for k in range(protocol.TYPE5_SLOT_LEN)
            )
        return config, type5

    def snapshot(
        self, identity: DeviceIdentity
    ) -> settings_mod.SettingsSnapshot:
        deadline = time.monotonic() + CONFIG_TIMEOUT
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                config, type5 = self._read_config_and_payloads(transport, deadline)
                profile: int | None = None
                if _remaining(deadline) > 0:
                    try:
                        reply = transport.exchange(
                            protocol.OP_PROFILE, b"", _remaining(deadline)
                        )
                        profile = protocol.parse_profile(bytes(reply))
                    except (Op1weError, ValueError):
                        profile = None
        return settings_mod.snapshot_from_memory(
            identity.fingerprint, config, profile, type5 or None
        )

    def read_full_backup(self, identity: DeviceIdentity) -> dict[int, int]:
        """Canonical map: config region plus every active payload."""
        deadline = time.monotonic() + CONFIG_TIMEOUT
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                config, type5 = self._read_config_and_payloads(transport, deadline)
        return {**config, **_type5_addr_map(type5)}

    def write_backup_file(self, identity: DeviceIdentity, mem: dict[int, int]) -> str:
        _ensure_private_dir(state_dir())
        stamp = time.strftime("%Y%m%dT%H%M%S")
        payload = {
            "apiVersion": protocol.API_VERSION,
            "kind": "op1we-backup",
            "seq": self._next_backup_seq(),
            "fingerprint": identity.fingerprint,
            "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ranges": [[protocol.CONFIG_LO, protocol.CONFIG_HI]],
            "bytes": {f"{addr:04x}": value for addr, value in sorted(mem.items())},
        }
        body = json.dumps(payload, indent=1)
        path = ""
        for attempt in range(100):
            suffix = "" if attempt == 0 else f"-{attempt}"
            path = os.path.join(state_dir(), f"backup-{stamp}{suffix}.json")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            except OSError as exc:
                raise Op1weError(
                    "unavailable", f"cannot write backup file: {exc}"
                ) from exc
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(body)
            except OSError as exc:
                raise Op1weError(
                    "unavailable", f"cannot write backup file: {exc}"
                ) from exc
            break
        else:
            raise Op1weError("unavailable", "cannot allocate a backup file name")
        self._prune_backups(keep=path)
        return path

    def _backup_files(self) -> list[str]:
        try:
            names = sorted(
                n for n in os.listdir(state_dir())
                if n.startswith("backup-") and n.endswith(".json")
            )
        except OSError:
            return []
        return [os.path.join(state_dir(), name) for name in names]

    @staticmethod
    def _backup_seq(path: str) -> int:
        """Creation order of a backup file (F-008).

        Sequence numbers allocate monotonically, so same-second
        bursts order correctly where filenames and mtimes cannot.
        Pre-sequence files read as 0 and prune first, which is
        correct — they predate every sequenced capture.
        """
        try:
            with open(path, "r", encoding="utf-8") as handle:
                seq = json.load(handle).get("seq", 0)
        except (OSError, ValueError, AttributeError):
            return 0
        return seq if isinstance(seq, int) and seq >= 0 else 0

    def _next_backup_seq(self) -> int:
        return max([0] + [self._backup_seq(p) for p in self._backup_files()]) + 1

    def _prune_backups(self, keep: str) -> None:
        """Retain the latest backups, always including `keep` (F-008)."""
        others: list[tuple[int, int, str]] = []
        for path in self._backup_files():
            if path == keep:
                continue
            try:
                mtime = os.stat(path).st_mtime_ns
            except OSError:
                mtime = 0
            others.append((self._backup_seq(path), mtime, path))
        others.sort()
        for _, _, stale in others[:max(0, len(others) - (BACKUP_RETAIN - 1))]:
            try:
                os.remove(stale)
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
            type5_end = (protocol.TYPE5_BASE
                         + protocol.KEY_UI_SLOTS * protocol.TYPE5_STRIDE - 1)
            in_config = protocol.CONFIG_LO <= addr <= protocol.CONFIG_HI
            in_type5 = protocol.TYPE5_BASE <= addr <= type5_end
            if not (in_config or in_type5):
                raise Op1weError("invalid-input", f"backup address 0x{addr:04x} outside allowed regions")
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
        inside an allowed region). Rejects stale revisions before
        writing a single byte. Every write is sent exactly once and
        its ACK fully validated; failures stop all mutation and carry
        the recovery backup plus a read-only observation (F-002).
        `timeout` bounds the whole operation (F-010).
        """
        for addr, data in writes.items():
            protocol.ee_write_payload(addr, bytes(data))  # bounds checked here
        merged = _merge_adjacent(writes)
        deadline = time.monotonic() + timeout
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                self._require_op1we(transport, deadline)
                config, type5 = self._read_config_and_payloads(transport, deadline)
                current = {**config, **_type5_addr_map(type5)}
                if (expected_revision is not None
                        and protocol.config_revision(current) != expected_revision):
                    raise Op1weError(
                        "stale-revision",
                        "device state changed since it was read; re-read and retry",
                        retryable=True,
                    )
                backup_path = self.write_backup_file(identity, current)
                ordered = _order_click_safe(config, merged)
                for addr, data in ordered:
                    want = bytes(data)
                    payload = protocol.ee_write_payload(addr, want)
                    ack: bytes | None = None
                    if _remaining(deadline) > 0:
                        try:
                            ack = bytes(transport.exchange(
                                protocol.OP_EEPROM_WRITE, payload,
                                min(WRITE_ACK_TIMEOUT, _remaining(deadline)),
                            ))
                        except Op1weError:
                            ack = None
                    if ack is None:
                        raise self._write_failed(
                            transport, deadline, backup_path, addr, want,
                            "was not acknowledged",
                        )
                    try:
                        echoed = protocol.ee_parse_data(
                            ack, protocol.OP_EEPROM_WRITE, addr, len(want)
                        )
                    except ValueError as exc:
                        raise self._write_failed(
                            transport, deadline, backup_path, addr, want,
                            f"has an invalid acknowledgement ({exc})",
                        ) from exc
                    if echoed != want:
                        raise self._write_failed(
                            transport, deadline, backup_path, addr, want,
                            "acknowledgement data mismatch",
                        )
                    # Readback-verify before touching the next chunk.
                    try:
                        check_mem = self._read_ranges_locked(
                            transport, [(addr, addr + len(want) - 1)], deadline
                        )
                    except Op1weError as exc:
                        raise Op1weError(
                            "verify-failed",
                            f"write at 0x{addr:04x} could not be verified "
                            f"({exc.code}); state uncertain, "
                            f"backup at {backup_path}",
                            detail={"backup": backup_path,
                                    "address": f"{addr:04x}",
                                    "observed": None},
                        ) from exc
                    check = bytes(check_mem[addr + k] for k in range(len(want)))
                    if check != want:
                        raise Op1weError(
                            "verify-failed",
                            f"readback mismatch at 0x{addr:04x}; state uncertain, "
                            f"backup at {backup_path}",
                            detail={"backup": backup_path,
                                    "address": f"{addr:04x}",
                                    "observed": check.hex()},
                        )
                fresh_config, fresh_type5 = self._read_config_and_payloads(
                    transport, deadline
                )
        return settings_mod.snapshot_from_memory(
            identity.fingerprint, fresh_config, None, fresh_type5 or None
        )

    def _write_failed(
        self, transport, deadline: float, backup_path: str,
        addr: int, want: bytes, what: str,
    ) -> Op1weError:
        """Build a write-failed error with a read-only observation (F-002)."""
        return Op1weError(
            "write-failed",
            f"write at 0x{addr:04x} {what}; state uncertain, "
            f"backup at {backup_path}",
            detail={"backup": backup_path,
                    "address": f"{addr:04x}",
                    "observed": self._recover_read(
                        transport, addr, len(want), deadline)},
        )

    def _recover_read(
        self, transport, addr: int, length: int, deadline: float
    ) -> str | None:
        """Best-effort read-only probe after a failed write (F-002).

        One chunk re-read, capped well inside the operation budget;
        never mutates. Returns observed bytes as hex, or None when
        unreadable.
        """
        probe_until = min(deadline, time.monotonic() + RECOVERY_PROBE_TIMEOUT)
        try:
            mem = self._read_ranges_locked(
                transport, [(addr, addr + length - 1)], probe_until
            )
        except Op1weError:
            return None
        cells = [mem.get(addr + k) for k in range(length)]
        if any(cell is None for cell in cells):
            return None
        return bytes(c for c in cells if c is not None).hex()

    def apply(
        self,
        identity: DeviceIdentity,
        changes: dict,
        expected_revision: str | None = None,
    ) -> tuple[settings_mod.SettingsSnapshot, int]:
        """Validated apply: plan against fresh memory, then write.

        `expected_revision` is required: a draft without a conflict
        token cannot prove it is fresh (F-003). Returns (snapshot,
        chunk_count). Empty plans compare the token against a fresh
        atomic snapshot and perform zero writes.
        """
        current = self.read_memory(identity)
        writes = plan_apply(current, changes)
        if expected_revision is None:
            raise Op1weError(
                "invalid-input",
                "apply requires expectedRevision; read first, then retry",
            )
        if not writes:
            snap = self.snapshot(identity)
            if snap.revision != expected_revision:
                raise Op1weError(
                    "stale-revision",
                    "device state changed since it was read; re-read and retry",
                    retryable=True,
                )
            return (snap, 0)
        return (self.apply_bytes(identity, writes, expected_revision), len(writes))

    def restore_mem(
        self,
        identity: DeviceIdentity,
        mem: dict[int, int],
        expected_revision: str | None = None,
    ) -> tuple[settings_mod.SettingsSnapshot, int, bool]:
        """Restore previously captured bytes (backup/profile target).

        The target is translated into supported, validated setting
        changes against fresh memory — never replayed raw — so unknown
        regions are preserved and invalid/unsupported targets fail
        with zero writes (F-001). Returns (snapshot, chunks, changed).
        """
        current = self.read_full_backup(identity)
        changes, direct = plan_restore(current, mem)
        writes = plan_apply(
            {a: v for a, v in current.items() if a <= protocol.CONFIG_HI},
            changes,
        )
        overlap = set(writes) & set(direct)
        if overlap:
            raise Op1weError(
                "internal",
                f"restore planner overlap at {sorted(overlap)}",
            )
        writes.update(direct)
        if not writes:
            return (self.snapshot(identity), 0, False)
        return (self.apply_bytes(identity, writes, expected_revision), len(writes), True)

    def _read_ranges_locked(
        self, transport, ranges: list[tuple[int, int]], deadline: float
    ) -> dict[int, int]:
        """Read ranges within the operation deadline (F-010).

        Reads — and only reads — retry to ride out a dozing mouse;
        corrupt echoes retry the chunk within budget instead of
        escaping (F-009). Exhaustion raises asleep/offline/unavailable
        via classification probes inside the same budget.
        """
        read_until = _read_until(deadline)
        mem: dict[int, int] = {}
        for lo, hi in ranges:
            addr = lo
            while addr <= hi:
                chunk = min(protocol.MAX_DATA_PER_FRAME, hi - addr + 1)
                payload = protocol.ee_read_payload(addr, chunk)
                data: bytes | None = None
                while data is None and time.monotonic() < read_until:
                    reply = self._exchange_until(
                        transport, protocol.OP_EEPROM_READ, payload, read_until
                    )
                    if reply is None:
                        break
                    try:
                        data = protocol.ee_parse_data(
                            reply, protocol.OP_EEPROM_READ, addr, chunk
                        )
                    except ValueError:
                        continue
                if data is None:
                    self._raise_not_readable(transport, deadline)
                    raise AssertionError("unreachable")  # always raises above
                for offset, byte in enumerate(data):
                    mem[addr + offset] = byte
                addr += chunk
        return mem

    def _exchange_until(
        self, transport, opcode: int, payload: bytes, deadline: float
    ) -> bytes | None:
        """Retry a read until the deadline; None when it expires.

        Reads are side-effect-free, so wake-retry is safe here. Writes
        never use this helper: one attempt, then stop (F-002).
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return bytes(transport.exchange(
                    opcode, payload, min(0.5, remaining)
                ))
            except Op1weError:
                continue


def real_controller() -> Controller:
    return Controller(open_transport=lambda identity: HidrawTransport(identity))


def _merge_adjacent(writes: dict[int, bytes]) -> dict[int, bytes]:
    """Merge contiguous chunks into ≤10-byte units (F-004).

    Whole records become single atomic writes, so click-safe
    ordering reasons about complete bindings, never record halves.
    """
    merged: dict[int, bytes] = {}
    last: int | None = None
    for addr in sorted(writes):
        data = bytes(writes[addr])
        if (last is not None and addr == last + len(merged[last])
                and len(merged[last]) + len(data) <= protocol.MAX_DATA_PER_FRAME):
            merged[last] = merged[last] + data
            continue
        merged[addr] = data
        last = addr
    return merged


def _left_click_slots(mem: dict[int, int]) -> set[int]:
    """Slots holding a usable left-click binding (F-004).

    Only vendor-exposed slots 1..12 with checksum-valid mouse
    records count. Slots 13–16 sit outside the KM=12 UI with zero
    physical evidence, and a corrupt record's behavior is unknown.
    """
    found: set[int] = set()
    for slot in range(1, protocol.KEY_UI_SLOTS + 1):
        base = protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
        cells = [mem.get(base + k) for k in range(protocol.KEY_RECORD_LEN)]
        if any(cell is None for cell in cells):
            continue
        decoded = protocol.decode_key_record(bytes(c for c in cells if c is not None))
        if (decoded.get("checksum_ok") and decoded.get("kind") == "mouse"
                and "left" in decoded.get("buttons", ())):
            found.add(slot)
    return found


def _order_click_safe(
    config: dict[int, int], writes: dict[int, bytes]
) -> list[tuple[int, bytes]]:
    """Order chunks: a replacement click lands before the old one leaves.

    Record writes installing a left-click go first, writes removing
    one go last, everything else keeps address order between them
    (F-004). With whole-record atomic writes, a mid-apply failure can
    no longer strand the device clickless when the plan itself keeps
    a click: every verified prefix state still holds one.
    """
    final = dict(config)
    for addr, data in writes.items():
        for offset, byte in enumerate(bytes(data)):
            final[addr + offset] = byte
    before = _left_click_slots(config)
    after = _left_click_slots(final)

    def touched(addr: int, data: bytes) -> set[int]:
        end = addr + len(data)
        return {
            slot for slot in range(1, protocol.KEY_UI_SLOTS + 1)
            if addr < protocol.ADDR_KEYS + slot * protocol.KEY_RECORD_LEN
            and end > protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
        }

    first: list[tuple[int, bytes]] = []
    mid: list[tuple[int, bytes]] = []
    last: list[tuple[int, bytes]] = []
    for addr in sorted(writes):
        data = bytes(writes[addr])
        slots = touched(addr, data)
        installs = any(s not in before and s in after for s in slots)
        removes = any(s in before and s not in after for s in slots)
        if installs and not removes:
            first.append((addr, data))
        elif removes and not installs:
            last.append((addr, data))
        else:
            mid.append((addr, data))
    return first + mid + last


# ---------------------------------------------------------------------------
# Milestone 2: validated apply planning (pure: current memory + changes -> writes)
# ---------------------------------------------------------------------------

def _invalid(message: str) -> Op1weError:
    return Op1weError("invalid-input", message)


def coerce_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise _invalid(f"{name} must be true/false, got {value!r}")


def coerce_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(f"{name} must be an integer, got {value!r}")
    return value


def plan_key_binding(slot, action) -> tuple[bytes, bytes | None]:
    """Validate one binding change -> (record, type5-payload|None).

    Raises invalid-input/unsupported for anything unencodable. Special
    and unknown kinds are never constructed.
    """
    slot = coerce_int(slot, "keys[].slot")
    if not 1 <= slot <= protocol.KEY_SLOTS:
        raise _invalid(f"slot {slot} outside 1..{protocol.KEY_SLOTS}")
    if not isinstance(action, dict):
        raise _invalid(f"slot {slot}: action must be an object")
    kind = action.get("kind")
    if kind == "unassigned":
        return (protocol.encode_key_record("unassigned"), None)
    if kind == "mouse":
        buttons = action.get("buttons")
        if not isinstance(buttons, list) or not buttons:
            raise _invalid(f"slot {slot}: mouse needs a non-empty buttons list")
        try:
            return (protocol.encode_key_record("mouse", buttons=buttons), None)
        except ValueError as exc:
            raise _invalid(f"slot {slot}: {exc}") from exc
    if kind in ("key", "combo"):
        keys = action.get("keys")
        modifiers = action.get("modifiers", 0)
        if not isinstance(keys, list):
            raise _invalid(f"slot {slot}: key/combo needs a keys list")
        modifiers = coerce_int(modifiers, f"slot {slot} modifiers")
        if kind == "key" and (len(keys) != 1 or modifiers != 0):
            raise _invalid(f"slot {slot}: single key needs exactly 1 key, no modifiers")
        try:
            payload = protocol.build_key_payload(
                [coerce_int(k, f"slot {slot} keys[]") for k in keys], modifiers
            )
            protocol.type5_addr(slot)
        except ValueError as exc:
            raise _invalid(f"slot {slot}: {exc}") from exc
        return (protocol.encode_key_record("key-ref"), payload)
    if kind == "media":
        usage = action.get("usage")
        if isinstance(usage, str):
            matches = [u for u, n in protocol.MEDIA_NAMES.items() if n == usage]
            if not matches:
                raise _invalid(f"slot {slot}: unknown media name {usage!r}")
            usage = matches[0]
        usage = coerce_int(usage, f"slot {slot} usage")
        try:
            payload = protocol.build_media_payload(usage)
            protocol.type5_addr(slot)
        except ValueError as exc:
            raise _invalid(f"slot {slot}: {exc}") from exc
        return (protocol.encode_key_record("key-ref"), payload)
    if kind in ("dpi-toggle", "dpi-plus", "dpi-minus", "polling-switch"):
        try:
            return (protocol.encode_key_record(kind), None)
        except ValueError as exc:
            raise _invalid(f"slot {slot}: {exc}") from exc
    if kind in ("special4", "special8", "special9", "macro", "unknown"):
        raise Op1weError(
            "unsupported",
            f"slot {slot}: action kind {kind!r} cannot be constructed "
            "(meaning unconfirmed or out of scope); existing bindings are preserved",
        )
    raise _invalid(f"slot {slot}: unknown action kind {kind!r}")


def plan_apply(mem: dict[int, int], changes: dict) -> dict[int, bytes]:
    """Validate a changes object against current memory -> {addr: bytes}.

    Raises before returning anything on the first problem, so callers
    perform zero writes for invalid plans. Only differing bytes are
    emitted; unknown regions are never touched.
    """
    if not isinstance(changes, dict):
        raise _invalid("changes must be an object")
    known = {"pollingHz", "cpi", "debounceMs", "sleepS", "ripple", "fixline",
             "turnOffLight", "keys"}
    for key in changes:
        if key not in known:
            raise _invalid(f"unknown change field: {key!r}")
    writes: dict[int, bytes] = {}

    def put(addr: int, data: bytes) -> None:
        cells = [mem.get(addr + k) for k in range(len(data))]
        if any(cell is None for cell in cells):
            raise Op1weError("unavailable", f"current bytes at 0x{addr:04x} unknown; re-read first")
        if bytes(c for c in cells if c is not None) != bytes(data):
            writes[addr] = bytes(data)

    if "pollingHz" in changes:
        hertz = coerce_int(changes["pollingHz"], "pollingHz")
        try:
            put(protocol.ADDR_POLLING, protocol.encode_polling_mask(hertz))
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
    if "cpi" in changes:
        stages = changes["cpi"]
        if not isinstance(stages, list) or len(stages) > settings_mod.CAPABILITIES.dpi_stages:
            raise _invalid("cpi must be a list of up to 4 stage values")
        for index, value in enumerate(stages):
            if value is None:
                continue
            cpi = coerce_int(value, f"cpi[{index}]")
            try:
                record = protocol.encode_cpi_record(cpi, cpi)  # x=y (ShowXY=0)
            except ValueError as exc:
                raise _invalid(f"cpi[{index}]: {exc}") from exc
            put(protocol.ADDR_CPI + index * protocol.CPI_RECORD_LEN, record)
    if "debounceMs" in changes:
        value = coerce_int(changes["debounceMs"], "debounceMs")
        try:
            put(protocol.ADDR_DEBOUNCE, protocol.encode_debounce_ms(value))
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
    if "sleepS" in changes:
        value = coerce_int(changes["sleepS"], "sleepS")
        try:
            put(protocol.ADDR_SLEEP, protocol.encode_sleep_s(value))
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
    for field, addr in (("ripple", protocol.ADDR_RIPPLE),
                        ("fixline", protocol.ADDR_FIXLINE),
                        ("turnOffLight", protocol.ADDR_TURN_OFF_LIGHT)):
        if field in changes:
            put(addr, protocol.encode_pair(1 if coerce_bool(changes[field], field) else 0))
    key_writes: dict[int, tuple[bytes, bytes | None]] = {}
    if "keys" in changes:
        bindings = changes["keys"]
        if not isinstance(bindings, list):
            raise _invalid("keys must be a list")
        seen: set[int] = set()
        for entry in bindings:
            if not isinstance(entry, dict) or "slot" not in entry or "action" not in entry:
                raise _invalid("keys[] entries need {slot, action}")
            slot = coerce_int(entry["slot"], "keys[].slot")
            if slot in seen:
                raise _invalid(f"slot {slot} listed twice")
            seen.add(slot)
            key_writes[slot] = plan_key_binding(slot, entry["action"])
        # Vendor constraint (tc_msg1): at least one click must remain.
        remaining = dict(mem)
        for slot, (record, _payload) in key_writes.items():
            base = protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
            for k, byte in enumerate(record):
                remaining[base + k] = byte
        if not _left_click_slots(remaining):
            raise _invalid("at least one button must stay bound to left-click")
        for slot, (record, payload) in key_writes.items():
            put(protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN, record)
            if payload is not None:
                base = protocol.type5_addr(slot)
                # Payload writes bypass the config-region gate by design
                # (they live at 0x100+); chunked by the transport callers.
                for chunk_off in range(0, len(payload), protocol.MAX_DATA_PER_FRAME):
                    chunk = payload[chunk_off:chunk_off + protocol.MAX_DATA_PER_FRAME]
                    writes[base + chunk_off] = bytes(chunk)
    # Chunk large writes to the 10-byte frame ceiling.
    chunked: dict[int, bytes] = {}
    for addr, data in writes.items():
        if len(data) <= protocol.MAX_DATA_PER_FRAME:
            chunked[addr] = data
        else:
            for off in range(0, len(data), protocol.MAX_DATA_PER_FRAME):
                chunked[addr + off] = data[off:off + protocol.MAX_DATA_PER_FRAME]
    return chunked


def plan_restore(
    current: dict[int, int], target: dict[int, int]
) -> tuple[dict, dict[int, bytes]]:
    """Translate a backup/profile target into validated changes (F-001).

    Returns (changes, direct_writes). Only verified fields whose
    target bytes are fully specified and valid become changes;
    unknown regions (header unknowns, CPI slots 4-7, colours/LED,
    the 0xA0 block, 0xAB, non-key-ref payload tails) are preserved
    from current even when they differ. A differing verified field
    that cannot be validated rejects the whole restore before any
    write. Pure: current memory + target map -> plan, no I/O.
    """
    changes: dict = {}
    direct: dict[int, bytes] = {}

    def target_bytes(addr: int, length: int) -> bytes | None:
        cells = [target.get(addr + k) for k in range(length)]
        if any(cell is None for cell in cells):
            return None
        return bytes(c for c in cells if c is not None)

    def differs(addr: int, data: bytes) -> bool:
        return any(current.get(addr + k) != byte for k, byte in enumerate(data))

    def pair_at(raw: bytes) -> int | None:
        return protocol.decode_pair({0: raw[0], 1: raw[1]}, 0)

    raw = target_bytes(protocol.ADDR_POLLING, 2)
    if raw is not None:
        mask = pair_at(raw)
        hertz = protocol.POLLING_HZ.get(mask) if mask is not None else None
        if hertz is None:
            if differs(protocol.ADDR_POLLING, raw):
                raise _invalid(f"target polling bytes {raw.hex()} are not a valid rate")
        else:
            changes["pollingHz"] = hertz

    cpi: list[int | None] = [None] * settings_mod.CAPABILITIES.dpi_stages
    for index in range(settings_mod.CAPABILITIES.dpi_stages):
        base = protocol.ADDR_CPI + index * protocol.CPI_RECORD_LEN
        raw = target_bytes(base, protocol.CPI_RECORD_LEN)
        if raw is None:
            continue
        try:
            x, y = protocol.decode_cpi_record(raw)
        except ValueError:
            if differs(base, raw):
                raise _invalid(f"target cpi[{index}] record invalid: {raw.hex()}")
            continue
        if x == y:
            cpi[index] = x
        elif differs(base, raw):
            # Proven packing (decode passed) but x != y has no changes
            # form, so the exact validated bytes go direct.
            if protocol.encode_cpi_record(x, y) != raw:
                raise _invalid(f"target cpi[{index}] record not re-encodable")
            direct[base] = raw
    if any(value is not None for value in cpi):
        changes["cpi"] = cpi

    raw = target_bytes(protocol.ADDR_DEBOUNCE, 2)
    if raw is not None:
        value = pair_at(raw)
        valid = value is not None
        if valid:
            assert value is not None
            try:
                valid = protocol.encode_debounce_ms(value) == raw
            except ValueError:
                valid = False
        if not valid:
            if differs(protocol.ADDR_DEBOUNCE, raw):
                raise _invalid(f"target debounce bytes {raw.hex()} are invalid")
        else:
            assert value is not None
            changes["debounceMs"] = value

    raw = target_bytes(protocol.ADDR_SLEEP, 2)
    if raw is not None:
        value = pair_at(raw)
        if value is None:
            if differs(protocol.ADDR_SLEEP, raw):
                raise _invalid(f"target sleep bytes {raw.hex()} are invalid")
        else:
            changes["sleepS"] = value * 10

    for key, addr in (("ripple", protocol.ADDR_RIPPLE),
                      ("fixline", protocol.ADDR_FIXLINE),
                      ("turnOffLight", protocol.ADDR_TURN_OFF_LIGHT)):
        raw = target_bytes(addr, 2)
        if raw is None:
            continue
        value = pair_at(raw)
        if value not in (0, 1):
            if differs(addr, raw):
                raise _invalid(f"target {key} bytes {raw.hex()} are invalid")
        else:
            changes[key] = bool(value)

    key_changes: list[dict] = []
    for slot in range(1, protocol.KEY_SLOTS + 1):
        base = protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
        raw = target_bytes(base, protocol.KEY_RECORD_LEN)
        if raw is None:
            continue
        record_same = not differs(base, raw)
        decoded = protocol.decode_key_record(raw)
        kind = decoded.get("kind")
        if kind == "key-ref":
            if not record_same and not decoded.get("checksum_ok"):
                raise _invalid(f"target slot {slot}: record checksum invalid: {raw.hex()}")
            if slot > protocol.KEY_UI_SLOTS:
                if not record_same:
                    raise Op1weError(
                        "unsupported",
                        f"target slot {slot}: key binding outside slots 1..12 "
                        "cannot be restored",
                    )
                continue
            payload_base = protocol.type5_addr(slot)
            payload_raw = target_bytes(payload_base, protocol.TYPE5_SLOT_LEN)
            if payload_raw is None:
                if record_same:
                    continue  # payload unspecified: preserve current
                raise _invalid(f"target slot {slot}: key record without payload bytes")
            if record_same and not differs(payload_base, payload_raw):
                continue
            parsed = protocol.parse_type5_payload(payload_raw)
            parsed_kind = parsed.get("kind")
            if parsed_kind == "key":
                action = {"kind": "key", "keys": parsed["keys"]}
            elif parsed_kind == "combo":
                action = {"kind": "combo", "keys": parsed["keys"],
                          "modifiers": parsed["modifiers"]}
            elif parsed_kind == "media":
                action = {"kind": "media", "usage": parsed["usage"]}
            else:
                raise _invalid(
                    f"target slot {slot}: payload undecodable "
                    f"({parsed.get('detail')})"
                )
            key_changes.append({"slot": slot, "action": action})
            continue
        if record_same:
            # Payload bytes for non-key-ref slots are unmanaged: ignore.
            continue
        if not decoded.get("checksum_ok"):
            raise _invalid(f"target slot {slot}: record checksum invalid: {raw.hex()}")
        if kind == "unassigned":
            action = {"kind": "unassigned"}
        elif kind == "mouse":
            action = {"kind": "mouse", "buttons": decoded["buttons"]}
        elif kind in ("dpi-toggle", "dpi-plus", "dpi-minus", "polling-switch"):
            action = {"kind": kind}
        else:
            raise Op1weError(
                "unsupported",
                f"target slot {slot}: {kind} binding cannot be restored "
                "(meaning unconfirmed or out of scope); rebind it explicitly",
            )
        key_changes.append({"slot": slot, "action": action})
    if key_changes:
        changes["keys"] = key_changes
    return changes, direct


def plan_reset(mem: dict[int, int]) -> dict[int, bytes]:
    """Vendor-documented defaults for covered fields; everything else preserved."""
    changes: dict = {
        "pollingHz": settings_mod.RESET_POLLING_HZ,
        "cpi": list(settings_mod.RESET_CPI),
        "debounceMs": settings_mod.RESET_DEBOUNCE_MS,
        "sleepS": settings_mod.RESET_SLEEP_S,
        "keys": [
            {"slot": 1, "action": {"kind": "mouse", "buttons": ["left"]}},
            {"slot": 2, "action": {"kind": "mouse", "buttons": ["right"]}},
            {"slot": 3, "action": {"kind": "mouse", "buttons": ["middle"]}},
            {"slot": 4, "action": {"kind": "mouse", "buttons": ["back"]}},
            {"slot": 5, "action": {"kind": "mouse", "buttons": ["forward"]}},
            # Slots 6-10 per Cfg K6-K10: K6 (08,AA) -> (02,01,00) is the
            # confirmed dpi-toggle; K7-K10 (08,A2) -> unassigned.
            {"slot": 6, "action": {"kind": "dpi-toggle"}},
            {"slot": 7, "action": {"kind": "unassigned"}},
            {"slot": 8, "action": {"kind": "unassigned"}},
            {"slot": 9, "action": {"kind": "unassigned"}},
            {"slot": 10, "action": {"kind": "unassigned"}},
        ],
    }
    return plan_apply(mem, changes)


# ---------------------------------------------------------------------------
# Host-side profiles (device holds a single profile; library lives here)
# ---------------------------------------------------------------------------

def profiles_dir() -> str:
    path = os.path.join(state_dir(), "profiles")
    _ensure_private_dir(path)
    return path


def validate_profile_name(name: str) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= 64:
        raise _invalid("profile name must be 1..64 characters")
    if not all(ch.isalnum() or ch in "._-" for ch in name):
        raise _invalid(f"profile name {name!r} uses outside-allowed characters")
    return name


def profile_path(name: str) -> str:
    return os.path.join(profiles_dir(), validate_profile_name(name) + ".json")


def save_profile(identity: DeviceIdentity, name: str, mem: dict[int, int]) -> dict:
    """Save current memory as a named host-side profile (0600)."""
    validate_profile_name(name)
    payload = {
        "apiVersion": protocol.API_VERSION,
        "kind": "op1we-profile",
        "name": name,
        "fingerprint": identity.fingerprint,
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ranges": [[protocol.CONFIG_LO, protocol.CONFIG_HI]],
        "bytes": {f"{addr:04x}": value for addr, value in sorted(mem.items())},
    }
    path = profile_path(name)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError as exc:
        raise Op1weError(
            "unavailable", f"cannot write profile {name}: {exc}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
    except OSError as exc:
        raise Op1weError(
            "unavailable", f"cannot write profile {name}: {exc}"
        ) from exc
    return {"name": name, "path": path, "bytes": len(mem)}


def list_profiles() -> list[dict]:
    out: list[dict] = []
    try:
        names = sorted(os.listdir(profiles_dir()))
    except OSError:
        return out
    for filename in names:
        if not filename.endswith(".json"):
            continue
        path = os.path.join(profiles_dir(), filename)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != "op1we-profile":
            continue
        out.append({
            "name": payload.get("name", filename[:-5]),
            "capturedAt": payload.get("capturedAt"),
            "fingerprint": payload.get("fingerprint"),
            "bytes": len(payload.get("bytes", {})),
        })
    return out


def load_profile_bytes(name: str) -> tuple[dict[int, int], dict]:
    """Load a profile's byte map (validates format + regions)."""
    try:
        with open(profile_path(name), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise Op1weError("invalid-input", f"no such profile: {name}") from exc
    except (OSError, ValueError) as exc:
        raise Op1weError("invalid-input", f"cannot read profile {name}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "op1we-profile":
        raise Op1weError("invalid-input", f"{name} is not an OP1we profile")
    raw = payload.get("bytes")
    if not isinstance(raw, dict) or not raw:
        raise Op1weError("invalid-input", f"profile {name} has no byte map")
    type5_end = protocol.TYPE5_BASE + protocol.KEY_UI_SLOTS * protocol.TYPE5_STRIDE - 1
    mem: dict[int, int] = {}
    for key, value in raw.items():
        try:
            addr = int(key, 16)
        except ValueError as exc:
            raise Op1weError("invalid-input", f"bad profile address {key!r}") from exc
        if not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise Op1weError("invalid-input", f"bad profile value at {key!r}")
        if not ((protocol.CONFIG_LO <= addr <= protocol.CONFIG_HI)
                or (protocol.TYPE5_BASE <= addr <= type5_end)):
            raise Op1weError("invalid-input", f"profile address 0x{addr:04x} outside allowed regions")
        mem[addr] = value
    return mem, payload


def delete_profile(name: str) -> None:
    try:
        os.remove(profile_path(name))
    except FileNotFoundError as exc:
        raise Op1weError("invalid-input", f"no such profile: {name}") from exc
    except OSError as exc:
        raise Op1weError("unavailable", f"cannot delete profile {name}: {exc}") from exc
