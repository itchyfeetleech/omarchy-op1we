"""Pure vendor-protocol codecs: framing, checksums, opcodes, record decoders.

No I/O, no UI, no file access. All functions operate on bytes and raise
ValueError on malformed input. Evidence for every constant lives in
docs/protocol.md; anything unproven is rejected, never guessed.
"""

from __future__ import annotations

API_VERSION = 1

REPORT_COMMAND = 0x08
REPORT_REPLY = 0x09
FRAME_LEN = 17
MAX_DATA_PER_FRAME = 10
CHECKSUM_BASE = 0x55

# Strict opcode allowlist (docs/protocol.md). Anything else must never
# be sent: 0x0E is implicated in a receiver bootloader reset.
OP_LINK = 0x03
OP_BATTERY = 0x04
OP_EEPROM_WRITE = 0x07
OP_EEPROM_READ = 0x08
OP_PROFILE = 0x0F
ALLOWED_OPCODES = frozenset({OP_LINK, OP_BATTERY, OP_EEPROM_WRITE, OP_EEPROM_READ, OP_PROFILE})

# Unsolicited notification opcode (CPI-stage reports). Never sent, but
# valid frames with this opcode arrive on their own and must be
# skipped — never mistaken for command replies.
OP_NOTIFY_STAGE = 0x0A

# Proven configuration region (docs/protocol.md EEPROM map).
CONFIG_LO = 0x00
CONFIG_HI = 0xB4

# EEPROM layout landmarks (proven reads; see parity.md for write status).
ADDR_CPI = 0x0C
CPI_SLOTS = 8
CPI_RECORD_LEN = 4
ADDR_KEYS = 0x60
KEY_SLOTS = 16
KEY_RECORD_LEN = 4
ADDR_DEBOUNCE = 0xA9
ADDR_POLLING = 0x00

# Polling one-hot mask -> Hz (verified by report timing upstream).
POLLING_HZ = {0x08: 125, 0x04: 250, 0x02: 500, 0x01: 1000}

# DPI range from vendor Cfg.ini DPIRANGE (authoritative for the tool).
DPI_MIN, DPI_KNEE, DPI_STEP_LO = 50, 10000, 50
DPI_ABOVE, DPI_MAX, DPI_STEP_HI = 10100, 19000, 100

# type 0x01 mouse-button bitmask (verified).
MOUSE_BUTTON_BITS = {0x01: "left", 0x02: "right", 0x04: "middle", 0x08: "back", 0x10: "forward"}


def checksum(payload16: bytes) -> int:
    """Wire/stored checksum byte for the preceding 16 frame bytes."""
    if len(payload16) != 16:
        raise ValueError(f"checksum needs 16 bytes, got {len(payload16)}")
    return (CHECKSUM_BASE - sum(payload16)) & 0xFF


def frame(opcode: int, payload: bytes = b"") -> bytes:
    """Build a 17-byte command frame. Rejects unlisted opcodes and oversize payloads."""
    if opcode not in ALLOWED_OPCODES:
        raise ValueError(f"opcode 0x{opcode:02x} is not in the allowlist")
    if not 0 <= opcode <= 0xFF:
        raise ValueError(f"bad opcode {opcode!r}")
    if len(payload) > 15:
        raise ValueError(f"payload too long: {len(payload)} > 15")
    buf = bytearray(FRAME_LEN)
    buf[0] = REPORT_COMMAND
    buf[1] = opcode
    buf[2:2 + len(payload)] = payload
    buf[16] = checksum(bytes(buf[:16]))
    return bytes(buf)


def frame_is_valid(reply: bytes) -> bool:
    """True when reply is a well-formed 17-byte frame with valid checksum."""
    return (
        len(reply) == FRAME_LEN
        and reply[0] == REPORT_REPLY
        and (sum(reply) & 0xFF) == CHECKSUM_BASE
    )


def parse_reply(reply: bytes, expected_opcode: int) -> bytes:
    """Validate a reply frame and return its data bytes.

    Raises ValueError unless the frame is well-formed, echoes the
    expected opcode, and its length field matches the bytes carried.
    """
    if not frame_is_valid(reply):
        raise ValueError("malformed reply frame (length/header/checksum)")
    if reply[1] != expected_opcode:
        raise ValueError(f"opcode mismatch: want 0x{expected_opcode:02x}, got 0x{reply[1]:02x}")
    length = reply[5]
    if length > MAX_DATA_PER_FRAME:
        raise ValueError(f"reply length {length} exceeds {MAX_DATA_PER_FRAME}")
    return bytes(reply[6:6 + length])


def parse_battery(reply: bytes) -> tuple[int, int]:
    """Return (percent, charging) from an opcode 0x04 reply."""
    data = parse_reply(reply, OP_BATTERY)
    if len(data) < 1 or data[0] > 100:
        raise ValueError(f"invalid battery payload: {data.hex()}")
    charging = data[1] if len(data) > 1 else 0
    if charging not in (0, 1):
        raise ValueError(f"invalid charging flag: {charging}")
    return (data[0], charging)


def parse_link(reply: bytes) -> bool:
    """Return True when the link is up (opcode 0x03, data[0] == 1)."""
    data = parse_reply(reply, OP_LINK)
    if len(data) < 1 or data[0] not in (0, 1):
        raise ValueError(f"invalid link payload: {data.hex()}")
    return data[0] == 1


def parse_profile(reply: bytes) -> int:
    """Return the current profile number (opcode 0x0F)."""
    data = parse_reply(reply, OP_PROFILE)
    if len(data) < 1 or data[0] == 0:
        raise ValueError(f"invalid profile payload: {data.hex()}")
    return data[0]


def parse_stage_notification(report: bytes) -> int:
    """Return the 0-based CPI stage from an unsolicited 0x0A report."""
    if not frame_is_valid(report) or report[1] != OP_NOTIFY_STAGE:
        raise ValueError("not a stage notification frame")
    if report[5] != 10 or report[6] != 0x01 or report[8] != 0x01:
        raise ValueError(f"unrecognized notification shape: {report.hex()}")
    stage = report[7]
    if stage > 7:
        raise ValueError(f"stage out of range: {stage}")
    return stage


def ee_read_payload(addr: int, length: int) -> bytes:
    """Build the EEPROM-read request payload (opcode 0x08)."""
    if not 1 <= length <= MAX_DATA_PER_FRAME:
        raise ValueError(f"read length {length} outside 1..{MAX_DATA_PER_FRAME}")
    if not 0 <= addr <= 0xFFFF:
        raise ValueError(f"address out of range: 0x{addr:x}")
    return bytes([0x00, (addr >> 8) & 0xFF, addr & 0xFF, length])


def ee_write_payload(addr: int, data: bytes) -> bytes:
    """Build the EEPROM-write request payload (opcode 0x07)."""
    if not 1 <= len(data) <= MAX_DATA_PER_FRAME:
        raise ValueError(f"write length {len(data)} outside 1..{MAX_DATA_PER_FRAME}")
    if not (CONFIG_LO <= addr and addr + len(data) - 1 <= CONFIG_HI):
        raise ValueError(
            f"refusing write outside config region 0x{CONFIG_LO:02x}-0x{CONFIG_HI:02x}"
        )
    return bytes([0x00, (addr >> 8) & 0xFF, addr & 0xFF, len(data)]) + bytes(data)


def ee_parse_data(reply: bytes, opcode: int, addr: int, length: int) -> bytes:
    """Validate an EEPROM reply's address/length echo and return its data."""
    if not frame_is_valid(reply) or reply[1] != opcode:
        raise ValueError("not a valid EEPROM reply frame")
    echo = (reply[3] << 8) | reply[4]
    if echo != addr:
        raise ValueError(f"address echo 0x{echo:04x} != request 0x{addr:04x}")
    if reply[5] != length:
        raise ValueError(f"length echo {reply[5]} != request {length}")
    return bytes(reply[6:6 + length])


def stored_checksum(*values: int) -> int:
    """Stored-record checksum idiom (same 0x55 - sum as wire frames)."""
    return (CHECKSUM_BASE - sum(values)) & 0xFF


def decode_pair(mem: dict[int, int], addr: int) -> int | None:
    """Decode a value/complement pair; None when missing or invalid."""
    value = mem.get(addr)
    comp = mem.get(addr + 1)
    if value is None or comp is None:
        return None
    if comp != stored_checksum(value):
        return None
    return value


def decode_polling_hz(mem: dict[int, int]) -> int | None:
    """Polling rate from the one-hot mask at 0x00; None when invalid."""
    mask = decode_pair(mem, ADDR_POLLING)
    if mask is None:
        return None
    return POLLING_HZ.get(mask)


def decode_cpi_record(raw: bytes) -> tuple[int, int]:
    """Decode one 4-byte CPI record to (x_cpi, y_cpi).

    Only the proven mul==0x00 packing ((x+1)*50) is accepted; records
    with multiplier bits set raise ValueError instead of guessing.
    """
    if len(raw) != 4:
        raise ValueError(f"CPI record needs 4 bytes, got {len(raw)}")
    x, y, mul, crc = raw
    if crc != stored_checksum(x, y, mul):
        raise ValueError(f"CPI record checksum mismatch: {raw.hex()}")
    if mul != 0x00:
        raise ValueError(f"unproven CPI multiplier byte 0x{mul:02x}")
    return ((x + 1) * 50, (y + 1) * 50)


def cpi_value_is_legal(cpi: int) -> bool:
    """True when cpi is representable per DPIRANGE (tool constraint)."""
    if DPI_MIN <= cpi <= DPI_KNEE:
        return (cpi - DPI_MIN) % DPI_STEP_LO == 0
    if DPI_ABOVE <= cpi <= DPI_MAX:
        return (cpi - DPI_ABOVE) % DPI_STEP_HI == 0
    return False


def decode_key_record(raw: bytes) -> dict:
    """Decode one 4-byte KeyMatrix record without guessing.

    Returns {"kind": "unassigned" | "mouse" | "unknown", ...}. Unknown
    types are reported with their raw bytes and never mapped to an
    action.
    """
    if len(raw) != 4:
        raise ValueError(f"key record needs 4 bytes, got {len(raw)}")
    typ, code, param, crc = raw
    ok = crc == stored_checksum(typ, code, param)
    if typ == 0 and code == 0 and param == 0:
        return {"kind": "unassigned", "raw": bytes(raw).hex(), "checksum_ok": ok}
    if typ == 0x01:
        names = [name for bit, name in sorted(MOUSE_BUTTON_BITS.items()) if code & bit]
        if code & ~0x1F:
            return {
                "kind": "unknown",
                "detail": f"mouse type with outside-verified bits 0x{code:02x}",
                "raw": bytes(raw).hex(),
                "checksum_ok": ok,
            }
        return {
            "kind": "mouse",
            "buttons": names,
            "raw": bytes(raw).hex(),
            "checksum_ok": ok,
        }
    return {
        "kind": "unknown",
        "detail": f"undecoded type 0x{typ:02x}",
        "raw": bytes(raw).hex(),
        "checksum_ok": ok,
    }


def config_revision(mem: dict[int, int]) -> str:
    """Stable revision id: sha256 over the raw config bytes (stdlib hashlib)."""
    import hashlib

    ordered = bytes(mem[a] for a in sorted(mem))
    return hashlib.sha256(ordered).hexdigest()[:32]
