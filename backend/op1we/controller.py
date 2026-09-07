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
PROFILE_RETAIN = 20


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
        type5: dict[int, bytes] = {}
        key_ref_slots = [
            slot for slot in range(1, protocol.KEY_UI_SLOTS + 1)
            if mem.get(protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN)
            == protocol.KEY_TYPE_KEY
        ]
        if key_ref_slots:
            ranges = [
                (protocol.type5_addr(slot),
                 protocol.type5_addr(slot) + protocol.TYPE5_SLOT_LEN - 1)
                for slot in key_ref_slots
            ]
            payload_mem = self.read_memory(identity, ranges)
            for slot in key_ref_slots:
                base = protocol.type5_addr(slot)
                type5[slot] = bytes(
                    payload_mem.get(base + k, 0xFF)
                    for k in range(protocol.TYPE5_SLOT_LEN)
                )
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                try:
                    reply = transport.exchange(protocol.OP_PROFILE, b"", STATUS_TIMEOUT)
                    profile = protocol.parse_profile(bytes(reply))
                except Op1weError:
                    profile = None
        return settings_mod.snapshot_from_memory(
            identity.fingerprint, mem, profile, type5 or None
        )

    def read_full_backup(self, identity: DeviceIdentity) -> dict[int, int]:
        """Config region plus type-5 payloads of bound slots."""
        mem = self.read_memory(identity)
        key_ref_slots = [
            slot for slot in range(1, protocol.KEY_UI_SLOTS + 1)
            if mem.get(protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN)
            == protocol.KEY_TYPE_KEY
        ]
        for slot in key_ref_slots:
            base = protocol.type5_addr(slot)
            mem.update(self.read_memory(
                identity, [(base, base + protocol.TYPE5_SLOT_LEN - 1)]
            ))
        return mem

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
        writing a single byte. Verifies every byte by readback and
        reports partial/uncertain state instead of success.
        """
        for addr, data in writes.items():
            protocol.ee_write_payload(addr, bytes(data))  # bounds checked here
        type5_slots = sorted({
            (addr - protocol.TYPE5_BASE) // protocol.TYPE5_STRIDE + 1
            for addr in writes
            if addr >= protocol.TYPE5_BASE
        })
        with DeviceLock(identity):
            with self.open_transport(identity) as transport:
                current = self._read_locked(
                    transport, [(protocol.CONFIG_LO, protocol.CONFIG_HI)], timeout
                )
                if expected_revision is not None and protocol.config_revision(current) != expected_revision:
                    raise Op1weError(
                        "stale-revision",
                        "device state changed since it was read; re-read and retry",
                        retryable=True,
                    )
                backup_mem = dict(current)
                for slot in type5_slots:
                    base = protocol.type5_addr(slot)
                    backup_mem.update(self._read_locked(
                        transport, [(base, base + protocol.TYPE5_SLOT_LEN - 1)], timeout
                    ))
                backup_path = self.write_backup_file(identity, backup_mem)
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
                fresh = self._read_locked(
                    transport, [(protocol.CONFIG_LO, protocol.CONFIG_HI)], timeout
                )
                type5: dict[int, bytes] = {}
                for slot in type5_slots:
                    base = protocol.type5_addr(slot)
                    chunk = self._read_locked(
                        transport, [(base, base + protocol.TYPE5_SLOT_LEN - 1)], timeout
                    )
                    type5[slot] = bytes(
                        chunk.get(base + k, 0xFF)
                        for k in range(protocol.TYPE5_SLOT_LEN)
                    )
        return settings_mod.snapshot_from_memory(
            identity.fingerprint, fresh, None, type5 or None
        )

    def apply(
        self,
        identity: DeviceIdentity,
        changes: dict,
        expected_revision: str | None = None,
    ) -> tuple[settings_mod.SettingsSnapshot, int]:
        """Validated apply: plan against fresh memory, then write.

        Returns (snapshot, chunk_count). Empty plans return the current
        snapshot with chunk_count 0 and perform zero writes.
        """
        current = self.read_memory(identity)
        writes = plan_apply(current, changes)
        if not writes:
            profile: int | None = None
            with DeviceLock(identity):
                with self.open_transport(identity) as transport:
                    try:
                        reply = transport.exchange(protocol.OP_PROFILE, b"", STATUS_TIMEOUT)
                        profile = protocol.parse_profile(bytes(reply))
                    except Op1weError:
                        profile = None
            return (settings_mod.snapshot_from_memory(identity.fingerprint, current, profile), 0)
        return (self.apply_bytes(identity, writes, expected_revision), len(writes))

    def restore_mem(
        self,
        identity: DeviceIdentity,
        mem: dict[int, int],
        expected_revision: str | None = None,
    ) -> tuple[settings_mod.SettingsSnapshot, int, bool]:
        """Restore previously captured bytes (backup/profile/reset target).

        Writes only differing chunks; returns (snapshot, chunks, changed).
        """
        current = self.read_memory(identity)
        # Include type-5 current bytes for bound slots so diffs are exact.
        type5_addrs = sorted(a for a in mem if a >= protocol.TYPE5_BASE)
        if type5_addrs:
            lo, hi = type5_addrs[0], type5_addrs[-1]
            current.update(self.read_memory(identity, [(lo, hi)]))
        writes: dict[int, bytes] = {}
        for addr in sorted(mem):
            if current.get(addr) != mem[addr]:
                writes[addr] = bytes([mem[addr]])
        # Merge adjacent single bytes into ≤10-byte chunks.
        merged: dict[int, bytes] = {}
        for addr in sorted(writes):
            if merged:
                last = max(merged)
                if addr == last + len(merged[last]) and len(merged[last]) < protocol.MAX_DATA_PER_FRAME:
                    merged[last] = merged[last] + writes[addr]
                    continue
            merged[addr] = writes[addr]
        if not merged:
            snap = settings_mod.snapshot_from_memory(
                identity.fingerprint,
                {a: v for a, v in current.items() if a <= protocol.CONFIG_HI}, None,
            )
            return (snap, 0, False)
        return (self.apply_bytes(identity, merged, expected_revision), len(merged), True)

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
        has_click = False
        for slot in range(1, protocol.KEY_SLOTS + 1):
            base = protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
            raw = bytes(remaining.get(base + k, 0) for k in range(4))
            decoded = protocol.decode_key_record(raw)
            if decoded.get("kind") == "mouse" and "left" in decoded.get("buttons", ()):
                has_click = True
                break
        if not has_click:
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
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
