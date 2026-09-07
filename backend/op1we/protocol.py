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
OP_MODEL = 0x01
MODEL_QUERY = bytes.fromhex("00 00 00 08 00 00 00 00 00 00 00 00 00 00")
OP1WE_MODEL = (0x35, 0x02)
OP_LINK = 0x03
OP_BATTERY = 0x04
OP_EEPROM_WRITE = 0x07
OP_EEPROM_READ = 0x08
OP_PROFILE = 0x0F
ALLOWED_OPCODES = frozenset({OP_MODEL, OP_LINK, OP_BATTERY, OP_EEPROM_WRITE, OP_EEPROM_READ, OP_PROFILE})

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

# Button-action record types. Simple actions live entirely in the
# 4-byte KeyMatrix record; type 0x05 (keyboard/combo/media) carries a
# payload in the 0x100 area (see TYPE5_*). Meanings marked TENTATIVE
# below were decoded from the vendor binary's action mapper and must
# be confirmed by observed device behavior (docs/parity.md).
KEY_TYPE_UNASSIGNED = 0x00
KEY_TYPE_MOUSE = 0x01
KEY_TYPE_DPI = 0x02  # CPI triad (codes confirmed by behavior tests)
KEY_DPI_TOGGLE = 0x01  # cycles stages with wrap (S2a)
KEY_DPI_PLUS = 0x02  # steps up (S2a)
KEY_DPI_MINUS = 0x03  # steps down (S2a)
KEY_TYPE_SPECIAL4 = 0x04  # generic (code, param); meanings unconfirmed
KEY_TYPE_KEY = 0x05  # keyboard/combo/media + 0x100 payload
KEY_TYPE_MACRO = 0x06  # excluded: macros out of scope
KEY_TYPE_POLLING_SWITCH = 0x07  # cycles polling rate (S2a: 1000->250)
KEY_TYPE_SPECIAL8 = 0x08  # meaning unconfirmed (emits stage echo)
KEY_TYPE_SPECIAL9 = 0x09  # meaning unconfirmed (one polling delta seen)
KEY_TYPE_MACROREF = 0x0A  # excluded: macro reference

# UI-exposed button slots (vendor KM=12). Matrix records exist for 16
# slots; type-5 payloads are restricted to 1..12 so they stay clear
# of the macro area at 0x300+.
KEY_UI_SLOTS = 12

# type-5 payload area: slot s (1-based) -> 0x100 + (s-1)*0x20, 32 bytes.
TYPE5_BASE = 0x100
TYPE5_STRIDE = 0x20
TYPE5_SLOT_LEN = 32

# type-5 event opcodes (vendor key-event builder).
EV_KEY_DOWN = 0x81
EV_KEY_UP = 0x41
EV_MOD_DOWN = 0x80
EV_MOD_UP = 0x40
EV_MEDIA_DOWN = 0x82
EV_MEDIA_UP = 0x42

# Consumer-page (0x0C) media usages from the vendor usage table.
MEDIA_USAGES = frozenset({
    0x183, 0xCD, 0xB7, 0xB6, 0xB5, 0xE9, 0xEA, 0xE2,
    0x18A, 0x192, 0x194, 0x221, 0x223, 0x224, 0x225, 0x226, 0x227, 0x22A,
})
MEDIA_NAMES = {
    0xCD: "play-pause", 0xB7: "stop", 0xB6: "previous", 0xB5: "next",
    0xE9: "volume-up", 0xEA: "volume-down", 0xE2: "mute",
    0x183: "media-select", 0x18A: "mail", 0x192: "calculator",
    0x194: "browser", 0x221: "ac-search", 0x223: "ac-home",
    0x224: "ac-back", 0x225: "ac-forward", 0x226: "ac-stop",
    0x227: "ac-refresh", 0x22A: "ac-favorites",
}

# HID keyboard usages accepted for single-key/combo (standard range).
KEY_USAGE_MIN = 0x04
KEY_USAGE_MAX = 0x65
# Modifier bitmask bits: ctrl=1, shift=2, alt=4, win=8 (vendor order).
MOD_MASK = 0x0F

# Advanced controls (proven both directions unless noted).
ADDR_FIXLINE = 0xAF  # angle snap on/off (name per vendor log string)
ADDR_RIPPLE = 0xB1  # ripple control on/off
ADDR_SLEEP = 0xAD  # dormancy, seconds/10
ADDR_TURN_OFF_LIGHT = 0xB3  # turn off light on moving on/off
ADDR_STAGE_COUNT = 0x02  # TENTATIVE: CPI stage count (value 4 = DM; untested)
ADDR_CURRENT_STAGE = 0x04  # current 0-based CPI stage (S2a: 3 matches + S2b)
ADDR_UNKNOWN_0A = 0x0A  # observed 1->2 without host writes; not a profile mirror
DEBOUNCE_MIN_MS = 0
DEBOUNCE_MAX_MS = 30  # vendor DebounceRange default 0x1E00 (max, min)
SLEEP_MAX_S = 2550  # full byte range, step 10 (vendor slider max unconfirmed)


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


def parse_model(reply: bytes) -> tuple[int, int]:
    """Vendor CheckPsd CID/MID, confirmed on OP1we (docs/protocol.md)."""
    data = parse_reply(reply, OP_MODEL)
    if reply[2] != 0 or len(data) != 8:
        raise ValueError("malformed model reply")
    return data[4], data[5]


def parse_battery(reply: bytes) -> tuple[int, int | None]:
    """Return (percent, charging) from an opcode 0x04 reply.

    A missing charging byte means unknown (None), never "not
    charging".
    """
    data = parse_reply(reply, OP_BATTERY)
    if len(data) < 1 or data[0] > 100:
        raise ValueError(f"invalid battery payload: {data.hex()}")
    charging = data[1] if len(data) > 1 else None
    if charging is not None and charging not in (0, 1):
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
    """Build the EEPROM-write request payload (opcode 0x07).

    Allowed regions: config 0x00-0xB4 and type-5 key payloads
    0x100-0x27F (slots 1..12). Anything else is refused.
    """
    if not 1 <= len(data) <= MAX_DATA_PER_FRAME:
        raise ValueError(f"write length {len(data)} outside 1..{MAX_DATA_PER_FRAME}")
    end = addr + len(data) - 1
    in_config = CONFIG_LO <= addr and end <= CONFIG_HI
    type5_end = TYPE5_BASE + KEY_UI_SLOTS * TYPE5_STRIDE - 1
    in_type5 = TYPE5_BASE <= addr and end <= type5_end
    if not (in_config or in_type5):
        raise ValueError(f"refusing write outside allowed regions at 0x{addr:04x}")
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

    Special types whose behavior is unconfirmed keep neutral kinds
    ("dpi-special", "special4/7/8/9") with raw bytes; they are
    preserved verbatim and never mapped to a named action.
    """
    if len(raw) != 4:
        raise ValueError(f"key record needs 4 bytes, got {len(raw)}")
    typ, code, param, crc = raw
    ok = crc == stored_checksum(typ, code, param)
    hexed = bytes(raw).hex()
    if typ == KEY_TYPE_UNASSIGNED and code == 0 and param == 0:
        return {"kind": "unassigned", "raw": hexed, "checksum_ok": ok}
    if typ == KEY_TYPE_MOUSE:
        names = [name for bit, name in sorted(MOUSE_BUTTON_BITS.items()) if code & bit]
        if code & ~0x1F or not names:
            return {
                "kind": "unknown",
                "detail": f"mouse type with outside-verified bits 0x{code:02x}",
                "raw": hexed,
                "checksum_ok": ok,
            }
        return {"kind": "mouse", "buttons": names, "raw": hexed, "checksum_ok": ok}
    if typ == KEY_TYPE_DPI:
        names = {KEY_DPI_TOGGLE: "dpi-toggle", KEY_DPI_PLUS: "dpi-plus",
                 KEY_DPI_MINUS: "dpi-minus"}
        if code not in names:
            return {"kind": "unknown", "detail": f"dpi code 0x{code:02x}",
                    "raw": hexed, "checksum_ok": ok}
        return {"kind": names[code], "raw": hexed, "checksum_ok": ok}
    if typ == KEY_TYPE_SPECIAL4:
        return {"kind": "special4", "code": code, "param": param,
                "raw": hexed, "checksum_ok": ok, "confirmed": False}
    if typ == KEY_TYPE_KEY:
        return {"kind": "key-ref", "raw": hexed, "checksum_ok": ok}
    if typ == KEY_TYPE_POLLING_SWITCH:
        return {"kind": "polling-switch", "raw": hexed, "checksum_ok": ok}
    if typ in (KEY_TYPE_SPECIAL8, KEY_TYPE_SPECIAL9):
        return {"kind": f"special{typ}", "raw": hexed,
                "checksum_ok": ok, "confirmed": False}
    if typ in (KEY_TYPE_MACRO, KEY_TYPE_MACROREF):
        return {"kind": "macro", "detail": "macros out of scope; preserved",
                "raw": hexed, "checksum_ok": ok}
    return {
        "kind": "unknown",
        "detail": f"undecoded type 0x{typ:02x}",
        "raw": hexed,
        "checksum_ok": ok,
    }


def encode_key_record(kind: str, **fields) -> bytes:
    """Encode a ButtonAction to a 4-byte record (checksum included).

    Only named, proven actions are encodable. Special/unknown kinds
    raise: the backend never constructs an action it cannot explain.
    """
    if kind == "unassigned":
        return bytes([0x00, 0x00, 0x00, stored_checksum(0, 0, 0)])
    if kind == "mouse":
        code = 0
        for name in fields.get("buttons", ()):
            for bit, label in MOUSE_BUTTON_BITS.items():
                if label == name:
                    code |= bit
                    break
            else:
                raise ValueError(f"unknown mouse button: {name!r}")
        if not code:
            raise ValueError("mouse action needs at least one button")
        return bytes([KEY_TYPE_MOUSE, code, 0x00, stored_checksum(KEY_TYPE_MOUSE, code, 0)])
    if kind == "key-ref":
        return bytes([KEY_TYPE_KEY, 0x00, 0x00, stored_checksum(KEY_TYPE_KEY, 0, 0)])
    dpi_codes = {"dpi-toggle": KEY_DPI_TOGGLE, "dpi-plus": KEY_DPI_PLUS,
                 "dpi-minus": KEY_DPI_MINUS}
    if kind in dpi_codes:
        code = dpi_codes[kind]
        return bytes([KEY_TYPE_DPI, code, 0x00, stored_checksum(KEY_TYPE_DPI, code, 0)])
    if kind == "polling-switch":
        typ = KEY_TYPE_POLLING_SWITCH
        return bytes([typ, 0x00, 0x00, stored_checksum(typ, 0, 0)])
    raise ValueError(f"action kind is not encodable: {kind!r}")


def config_revision(mem: dict[int, int]) -> str:
    """Stable revision id: sha256 over addressed (addr, value) bytes.

    Callers pass the canonical map (config plus active type-5
    payloads); addressing the hash keeps sparse and full maps
    distinct so revisions agree across read/backup/apply.
    """
    import hashlib

    digest = hashlib.sha256()
    for addr in sorted(mem):
        digest.update(bytes([(addr >> 8) & 0xFF, addr & 0xFF, mem[addr]]))
    return digest.hexdigest()[:32]


def encode_pair(value: int) -> bytes:
    """Encode a scalar as a value/complement pair."""
    if not 0 <= value <= 0xFF:
        raise ValueError(f"pair value out of range: {value}")
    return bytes([value, stored_checksum(value)])


def encode_polling_mask(hertz: int) -> bytes:
    """Encode a polling rate as its one-hot pair. Rejects anything else."""
    for mask, hz in POLLING_HZ.items():
        if hz == hertz:
            return encode_pair(mask)
    raise ValueError(f"unsupported polling rate: {hertz}")


def encode_cpi_value(cpi: int) -> int:
    """Encode one CPI axis to its register byte (50..10000, mul==0 only).

    Above-knee values (10100..19000) need multiplier-nibble packing
    whose value map is unproven (vendor DPIHW table); they are
    rejected rather than guessed.
    """
    if not DPI_MIN <= cpi <= DPI_KNEE or (cpi - DPI_MIN) % DPI_STEP_LO != 0:
        raise ValueError(f"CPI {cpi} outside encodable 50..10000 step 50")
    return (cpi // DPI_STEP_LO) - 1


def encode_cpi_record(x: int, y: int) -> bytes:
    """Encode one 4-byte CPI record (mul==0 packing only)."""
    xb, yb = encode_cpi_value(x), encode_cpi_value(y)
    return bytes([xb, yb, 0x00, stored_checksum(xb, yb, 0x00)])


def encode_debounce_ms(value: int) -> bytes:
    """Encode debounce milliseconds (vendor slider range 0..30)."""
    if not DEBOUNCE_MIN_MS <= value <= DEBOUNCE_MAX_MS:
        raise ValueError(f"debounce {value} ms outside {DEBOUNCE_MIN_MS}..{DEBOUNCE_MAX_MS}")
    return encode_pair(value)


def encode_sleep_s(seconds: int) -> bytes:
    """Encode dormancy seconds (stored as seconds/10)."""
    if seconds < 0 or seconds > SLEEP_MAX_S or seconds % 10 != 0:
        raise ValueError(f"sleep {seconds}s outside 0..{SLEEP_MAX_S} step 10")
    return encode_pair(seconds // 10)


def decode_sleep_s(mem: dict[int, int]) -> int | None:
    """Dormancy seconds from the 0xAD pair; None when invalid."""
    raw = decode_pair(mem, ADDR_SLEEP)
    return None if raw is None else raw * 10


def type5_addr(slot: int) -> int:
    """Payload address for a 1-based button slot (restricted to KM=12)."""
    if not 1 <= slot <= KEY_UI_SLOTS:
        raise ValueError(f"type-5 payload only for slots 1..{KEY_UI_SLOTS}, not {slot}")
    return TYPE5_BASE + (slot - 1) * TYPE5_STRIDE


def build_key_payload(usages: list[int], modifiers: int = 0) -> bytes:
    """Build a single-key/combo payload: [count, events..., checksum].

    Event order follows the vendor builder: modifier downs, key downs
    (up to 3), modifier ups, key ups (reversed). Total must fit the
    32-byte slot; oversized combos are rejected, never truncated.
    """
    if modifiers & ~MOD_MASK:
        raise ValueError(f"modifier bits outside 0x{MOD_MASK:02x}: 0x{modifiers:02x}")
    if not 1 <= len(usages) <= 3:
        raise ValueError(f"combo needs 1..3 keys, got {len(usages)}")
    for usage in usages:
        if not KEY_USAGE_MIN <= usage <= KEY_USAGE_MAX:
            raise ValueError(f"key usage out of range: 0x{usage:02x}")
    events: list[int] = []
    for bit in (0x01, 0x02, 0x04, 0x08):
        if modifiers & bit:
            events += [EV_MOD_DOWN, bit, 0x00]
    for usage in usages:
        events += [EV_KEY_DOWN, usage, 0x00]
    for bit in (0x01, 0x02, 0x04, 0x08):
        if modifiers & bit:
            events += [EV_MOD_UP, bit, 0x00]
    for usage in reversed(usages):
        events += [EV_KEY_UP, usage, 0x00]
    payload = bytes([len(events) // 3] + events)
    if len(payload) + 1 > TYPE5_SLOT_LEN:
        raise ValueError("combo payload exceeds the 32-byte slot")
    return payload + bytes([stored_checksum(*payload)])


def build_media_payload(usage: int) -> bytes:
    """Build a media-key payload: [2, down..., up..., checksum]."""
    if usage not in MEDIA_USAGES:
        raise ValueError(f"media usage not in the vendor table: 0x{usage:03x}")
    lo, hi = usage & 0xFF, (usage >> 8) & 0xFF
    payload = bytes([0x02, EV_MEDIA_DOWN, lo, hi, EV_MEDIA_UP, lo, hi])
    return payload + bytes([stored_checksum(*payload)])


def parse_type5_payload(raw: bytes) -> dict:
    """Parse a 32-byte type-5 slot into {kind, ...}. Never guesses.

    Returns kind "empty" for erased slots, "key", "media", or
    "unknown" (with raw bytes) for anything else.
    """
    if len(raw) != TYPE5_SLOT_LEN:
        raise ValueError(f"type-5 slot needs {TYPE5_SLOT_LEN} bytes")
    if all(byte == 0xFF for byte in raw):
        return {"kind": "empty"}
    count = raw[0]
    body = raw[1:1 + count * 3]
    if len(body) != count * 3 or count == 0 or count > 10:
        return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": "bad event count"}
    if raw[1 + count * 3] != stored_checksum(*raw[:1 + count * 3]):
        return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": "checksum mismatch"}
    events = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
    if (
        len(events) == 2
        and events[0][0] == EV_MEDIA_DOWN
        and events[1][0] == EV_MEDIA_UP
        and events[0][1:] == events[1][1:]
    ):
        usage = events[0][1] | (events[0][2] << 8)
        if usage in MEDIA_USAGES:
            return {"kind": "media", "usage": usage, "name": MEDIA_NAMES[usage]}
        return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": f"media usage 0x{usage:03x} outside vendor table"}
    keys: list[int] = []
    mods = 0
    for op, code, zero in events:
        if zero != 0x00:
            return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": "event pad byte"}
        if op == EV_MOD_DOWN:
            mods |= code
        elif op == EV_KEY_DOWN:
            keys.append(code)
        elif op in (EV_MOD_UP, EV_KEY_UP):
            continue
        else:
            return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": f"event op 0x{op:02x}"}
    if not keys:
        return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": "no key events"}
    if mods & ~MOD_MASK or any(not KEY_USAGE_MIN <= k <= KEY_USAGE_MAX for k in keys):
        return {"kind": "unknown", "raw": bytes(raw).hex(), "detail": "key range"}
    return {"kind": "combo" if len(keys) > 1 or mods else "key", "keys": keys, "modifiers": mods}
