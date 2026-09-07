"""Device discovery, identity, hidraw transport, locking and deadlines.

Normal mouse input stays with hid-generic: this module only opens the
vendor hidraw node for short command transactions. Discovery matches
VID/PID plus the vendor-collection descriptor marker, never a node
number. Zero or multiple candidates fail closed.
"""

from __future__ import annotations

import fcntl
import glob
import hashlib
import os
import re
import select
import time
from dataclasses import dataclass

from . import protocol

VID = 0x3367
# Verified receiver PID. Wired PIDs (0x1960/0x1962 per Cfg.ini) are NOT
# listed until observed on OP1we hardware (docs/device.md).
PIDS = (0x1961,)

# Usage page FF02 / usage 0x02 / application collection / report ID 8.
VENDOR_MARKER = bytes([0x06, 0x02, 0xFF, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x08])

SYSFS_HIDRAW = "/sys/class/hidraw/hidraw*"


class Op1weError(Exception):
    """Stable, user-actionable failure with a machine-readable code.

    `detail` carries optional verified post-failure context (recovery
    backup path, observed bytes); the CLI surfaces it in the error
    envelope without changing the stable code/message contract.
    """

    def __init__(self, code: str, message: str, retryable: bool = False,
                 detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail


def _ioc(direction: int, tor: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(tor) << 8) | nr


def _hidiocsfeature(size: int) -> int:
    return _ioc(3, "H", 0x06, size)


@dataclass(frozen=True)
class DeviceIdentity:
    vid: int
    pid: int
    bcd_device: str
    manufacturer: str
    product: str
    usb_path: str  # sysfs USB path, e.g. "1-7:1.1" (not stable identity)
    descriptor_sha256: str
    hidraw: str  # runtime node only, never identity

    @property
    def fingerprint(self) -> str:
        return (
            f"{self.vid:04x}:{self.pid:04x}"
            f"/bcd{self.bcd_device}"
            f"/desc{self.descriptor_sha256[:16]}"
        )

    def describe(self) -> dict:
        return {
            "vid": f"{self.vid:04x}",
            "pid": f"{self.pid:04x}",
            "bcdDevice": self.bcd_device,
            "manufacturer": self.manufacturer,
            "product": self.product,
            "usbPath": self.usb_path,
            "descriptorSha256": self.descriptor_sha256,
            "hidraw": self.hidraw,
            "fingerprint": self.fingerprint,
        }


def _read_sysfs_text(path: str) -> str:
    with open(path, "r", errors="replace") as handle:
        return handle.read()


def _read_sysfs_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _usb_string(sysfs_hidraw: str, name: str) -> str:
    # Walk up to the usb_device node (hidraw -> hid -> interface -> device).
    try:
        node = os.path.realpath(os.path.join(sysfs_hidraw, "device"))
    except OSError:
        return ""
    for _ in range(6):
        candidate = os.path.join(node, name)
        if os.path.isfile(candidate):
            try:
                return _read_sysfs_text(candidate).strip()
            except OSError:
                return ""
        parent = os.path.dirname(node)
        if parent == node:
            break
        node = parent
    return ""


def _usb_path(sysfs_hidraw: str) -> str:
    try:
        real = os.path.realpath(os.path.join(sysfs_hidraw, "device"))
    except OSError:
        return ""
    match = re.search(r"(\d+-[\d.]+:\d+\.\d+)", real)
    return match.group(1) if match else ""


def discover(sysfs_pattern: str = SYSFS_HIDRAW) -> DeviceIdentity:
    """Find the single vendor command node or raise Op1weError."""
    candidates: list[DeviceIdentity] = []
    for path in sorted(glob.glob(sysfs_pattern)):
        try:
            uevent = _read_sysfs_text(os.path.join(path, "device/uevent"))
            descriptor = _read_sysfs_bytes(os.path.join(path, "device/report_descriptor"))
        except OSError:
            continue
        wanted = any(
            f"HID_ID=0003:{VID:08X}:{pid:08X}" in uevent for pid in PIDS
        )
        if not wanted or VENDOR_MARKER not in descriptor:
            continue
        hidraw = "/dev/" + os.path.basename(path)
        usb_path = _usb_path(path)
        hid_id = re.search(r"HID_ID=\w+:(\w+):(\w+)", uevent)
        pid = int(hid_id.group(2), 16) if hid_id else 0
        candidates.append(
            DeviceIdentity(
                vid=VID,
                pid=pid,
                bcd_device=_usb_string(path, "bcdDevice") or "unknown",
                manufacturer=_usb_string(path, "manufacturer"),
                product=_usb_string(path, "product"),
                usb_path=usb_path,
                descriptor_sha256=hashlib.sha256(descriptor).hexdigest(),
                hidraw=hidraw,
            )
        )
    if not candidates:
        raise Op1weError(
            "unavailable",
            "no OP1we receiver found (VID 0x3367 + vendor HID marker); "
            "plug in the receiver and retry",
            retryable=True,
        )
    if len(candidates) > 1:
        nodes = ", ".join(c.hidraw for c in candidates)
        raise Op1weError(
            "ambiguous-device",
            f"more than one compatible device found ({nodes}); "
            "connect only one device",
        )
    return candidates[0]


class HidrawTransport:
    """Short-lived command transport over one hidraw node."""

    def __init__(self, identity: DeviceIdentity):
        self.identity = identity
        self._fd: int | None = None

    def open(self) -> None:
        try:
            self._fd = os.open(self.identity.hidraw, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise Op1weError(
                "permission",
                f"{self.identity.hidraw}: permission denied; install the udev "
                "rule (docs/device.md) or run with elevated privileges",
            ) from exc
        except FileNotFoundError as exc:
            raise Op1weError(
                "unavailable",
                f"{self.identity.hidraw} disappeared; replug the receiver",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise Op1weError("unavailable", f"cannot open {self.identity.hidraw}: {exc}") from exc

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "HidrawTransport":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # Stale-input hygiene per exchange: bounded so a chatty node
    # cannot spin a drain forever. Overflowing reports are
    # still filtered by opcode on the read path.
    _DRAIN_CAP = 64

    def _drain(self) -> None:
        assert self._fd is not None
        for _ in range(self._DRAIN_CAP):
            if not select.select([self._fd], [], [], 0)[0]:
                break
            try:
                os.read(self._fd, 64)
            except OSError:
                break

    def listen_stage(self, timeout: float) -> list[dict]:
        """Pure listen for unsolicited CPI-stage notifications (sends nothing).

        Returns [{stage, at}] for each valid 0x0A frame within timeout.
        """
        if self._fd is None:
            raise Op1weError("unavailable", "transport is not open")
        self._drain()
        found: list[dict] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            if not select.select([self._fd], [], [], min(0.2, remaining))[0]:
                continue
            try:
                report = os.read(self._fd, 64)
            except OSError:
                continue
            if len(report) != protocol.FRAME_LEN or not protocol.frame_is_valid(report):
                continue
            if report[1] != protocol.OP_NOTIFY_STAGE:
                continue
            try:
                stage = protocol.parse_stage_notification(bytes(report))
            except ValueError:
                continue
            found.append({"stage": stage, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        return found

    def exchange(self, opcode: int, payload: bytes = b"", timeout: float = 2.0) -> bytes:
        """One drained command round-trip; skips unsolicited notifications.

        Returns the raw 17-byte reply. Raises Op1weError("timeout") when
        nothing arrives (sleeping mouse or unplugged receiver are
        distinguished by the caller via link/battery probes).
        """
        if self._fd is None:
            raise Op1weError("unavailable", "transport is not open")
        frame = protocol.frame(opcode, payload)  # allowlist enforced here
        self._drain()
        try:
            fcntl.ioctl(self._fd, _hidiocsfeature(len(frame)), bytearray(frame), True)
        except OSError as exc:
            raise Op1weError(
                "unavailable", f"command send failed: {exc}", retryable=True
            ) from exc
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            if not select.select([self._fd], [], [], min(0.1, remaining))[0]:
                continue
            try:
                reply = os.read(self._fd, 64)
            except OSError:
                continue
            if len(reply) != protocol.FRAME_LEN or not protocol.frame_is_valid(reply):
                continue
            if reply[1] != opcode:
                # Unsolicited notification (e.g. 0x0A stage reports):
                # never mistake it for our reply.
                continue
            return bytes(reply)
        raise Op1weError("timeout", f"no reply to opcode 0x{opcode:02x}", retryable=True)


def runtime_dir() -> str:
    """Per-user runtime dir for the device lock (override for tests)."""
    override = os.environ.get("OP1WE_RUNTIME_DIR")
    if override:
        return override
    base = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/op1we-{os.getuid()}")
    return os.path.join(base, "op1we-control")


class DeviceLock:
    """Non-blocking per-device flock; busy callers fail instead of queueing."""

    def __init__(self, identity: DeviceIdentity):
        safe = identity.fingerprint.replace("/", "_")
        self.path = os.path.join(runtime_dir(), f"{safe}.lock")
        self._fd: int | None = None

    def __enter__(self) -> "DeviceLock":
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise Op1weError(
                "unavailable", f"cannot create lock dir: {exc}"
            ) from exc
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise Op1weError(
                "busy", "another OP1we operation is in progress", retryable=True
            ) from exc
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
