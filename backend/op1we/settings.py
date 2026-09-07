"""Typed settings, verified limits, and validation.

Only fields with hardware or binary evidence are modeled. Anything
else raises instead of guessing. TENTATIVE header-byte meanings stay
read-only until the behavior tests in docs/parity.md confirm them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import protocol


@dataclass(frozen=True)
class Capabilities:
    """Firmware-supported values frozen from milestone-1/2 evidence."""

    polling_hz: tuple[int, ...] = (125, 250, 500, 1000)
    dpi_min: int = protocol.DPI_MIN
    dpi_max_encodable: int = protocol.DPI_KNEE  # mul packing unproven above
    dpi_step: int = protocol.DPI_STEP_LO
    dpi_stages: int = 4
    debounce_min_ms: int = protocol.DEBOUNCE_MIN_MS
    debounce_max_ms: int = protocol.DEBOUNCE_MAX_MS
    debounce_default_ms: int = 3
    sleep_max_s: int = protocol.SLEEP_MAX_S
    sleep_step_s: int = 10
    sleep_default_s: int = 60
    key_slots: int = protocol.KEY_SLOTS
    key_ui_slots: int = protocol.KEY_UI_SLOTS

    def describe(self) -> dict:
        return {
            "pollingHz": list(self.polling_hz),
            "dpiMin": self.dpi_min,
            "dpiMaxEncodable": self.dpi_max_encodable,
            "dpiStep": self.dpi_step,
            "dpiStages": self.dpi_stages,
            "debounceMinMs": self.debounce_min_ms,
            "debounceMaxMs": self.debounce_max_ms,
            "sleepMaxS": self.sleep_max_s,
            "sleepStepS": self.sleep_step_s,
            "keySlots": self.key_slots,
            "keyUiSlots": self.key_ui_slots,
            "mediaUsages": sorted(protocol.MEDIA_USAGES),
        }


CAPABILITIES = Capabilities()


@dataclass(frozen=True)
class Status:
    connection: str  # connected | receiver-only | unavailable
    percent: int | None
    charging: int | None
    profile: int | None
    link_up: bool | None
    # True only when the battery reply was parsed on this call with
    # the link up. The receiver answers from cache while the link is
    # down, so a present percent with fresh=False is stale/unknown
    # age — never a fresh measurement (F-011).
    battery_fresh: bool = False


@dataclass(frozen=True)
class CpiStage:
    index: int  # 0-based
    x: int | None  # None when the record uses unproven packing
    y: int | None
    raw: str = ""  # hex of the 4-byte record when undecodable
    encodable: bool = True


@dataclass(frozen=True)
class ButtonBinding:
    slot: int  # 1-based
    action: dict  # ButtonAction JSON (see protocol.decode_key_record)
    payload: dict | None = None  # type-5 payload decode (slots 1..12)


@dataclass(frozen=True)
class SettingsSnapshot:
    fingerprint: str
    revision: str
    polling_hz: int | None
    debounce_ms: int | None
    sleep_s: int | None
    ripple: bool | None
    fixline: bool | None
    turn_off_light: bool | None
    profile: int | None
    stage_count: int | None  # TENTATIVE (0x02) until behavior test
    current_stage: int | None  # TENTATIVE (0x04) until behavior test
    current_dpi: int | None  # stages[current_stage].x when both known
    stages: tuple[CpiStage, ...]
    bindings: tuple[ButtonBinding, ...] = ()
    # Raw config bytes (unknown regions preserved verbatim).
    raw: dict[int, int] = field(default_factory=dict, compare=False)

    def describe(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "revision": self.revision,
            "pollingHz": self.polling_hz,
            "debounceMs": self.debounce_ms,
            "sleepS": self.sleep_s,
            "ripple": self.ripple,
            "fixline": self.fixline,
            "turnOffLight": self.turn_off_light,
            "profile": self.profile,
            "stageCount": self.stage_count,
            "currentStage": self.current_stage,
            "currentDpi": self.current_dpi,
            "stages": [
                {"index": s.index, "x": s.x, "y": s.y,
                 "raw": s.raw, "encodable": s.encodable}
                for s in self.stages
            ],
            "bindings": [
                {"slot": b.slot, "action": b.action, "payload": b.payload}
                for b in self.bindings
            ],
        }


def _flag(mem: dict[int, int], addr: int) -> bool | None:
    value = protocol.decode_pair(mem, addr)
    if value is None or value not in (0, 1):
        return None
    return bool(value)


def snapshot_from_memory(
    fingerprint: str,
    mem: dict[int, int],
    profile: int | None,
    type5: dict[int, bytes] | None = None,
) -> SettingsSnapshot:
    """Decode observed memory. Unknown/undecodable fields become None (never guessed)."""
    polling = protocol.decode_polling_hz(mem)
    debounce = protocol.decode_pair(mem, protocol.ADDR_DEBOUNCE)
    sleep = protocol.decode_sleep_s(mem)
    ripple = _flag(mem, protocol.ADDR_RIPPLE)
    fixline = _flag(mem, protocol.ADDR_FIXLINE)
    turn_off = _flag(mem, protocol.ADDR_TURN_OFF_LIGHT)
    stage_count = protocol.decode_pair(mem, protocol.ADDR_STAGE_COUNT)
    current_stage = protocol.decode_pair(mem, protocol.ADDR_CURRENT_STAGE)
    stages: list[CpiStage] = []
    for index in range(CAPABILITIES.dpi_stages):
        base = protocol.ADDR_CPI + index * protocol.CPI_RECORD_LEN
        cells = [mem.get(base + k) for k in range(4)]
        if any(cell is None for cell in cells):
            break
        raw = bytes(c for c in cells if c is not None)
        try:
            x, y = protocol.decode_cpi_record(bytes(raw))
        except ValueError:
            stages.append(CpiStage(index=index, x=None, y=None,
                                   raw=bytes(raw).hex(), encodable=False))
            continue
        stages.append(CpiStage(index=index, x=x, y=y, encodable=True))
    current_dpi: int | None = None
    if (
        current_stage is not None
        and 0 <= current_stage < len(stages)
        and stages[current_stage].x is not None
    ):
        stage = stages[current_stage]
        assert stage.x is not None
        current_dpi = stage.x
    bindings: list[ButtonBinding] = []
    for slot in range(1, protocol.KEY_SLOTS + 1):
        base = protocol.ADDR_KEYS + (slot - 1) * protocol.KEY_RECORD_LEN
        cells = [mem.get(base + k) for k in range(4)]
        if any(cell is None for cell in cells):
            continue
        action = protocol.decode_key_record(bytes(c for c in cells if c is not None))
        payload = None
        if action.get("kind") == "key-ref" and type5 and slot in type5:
            try:
                payload = protocol.parse_type5_payload(type5[slot])
            except ValueError:
                payload = {"kind": "unknown", "detail": "short read"}
        bindings.append(ButtonBinding(slot=slot, action=action, payload=payload))
    # Canonical revision: config plus active type-5 payloads, so a
    # binding edit always changes the token (F-003). `mem` itself may
    # already carry payload keys (backup/profile maps); the merge is
    # idempotent for those.
    canonical = dict(mem)
    if type5:
        for slot, payload_bytes in type5.items():
            base = protocol.type5_addr(slot)
            for offset, byte in enumerate(payload_bytes):
                canonical[base + offset] = byte
    return SettingsSnapshot(
        fingerprint=fingerprint,
        revision=protocol.config_revision(canonical),
        polling_hz=polling,
        debounce_ms=debounce,
        sleep_s=sleep,
        ripple=ripple,
        fixline=fixline,
        turn_off_light=turn_off,
        profile=profile,
        stage_count=stage_count,
        current_stage=current_stage,
        current_dpi=current_dpi,
        stages=tuple(stages),
        bindings=tuple(bindings),
        raw=dict(mem),
    )


@dataclass(frozen=True)
class DebounceChange:
    """Debounce write within the proven 0..30 ms slider range."""

    value_ms: int

    def validate(self) -> None:
        protocol.encode_debounce_ms(self.value_ms)

    def encoded(self) -> bytes:
        return protocol.encode_debounce_ms(self.value_ms)


# Vendor-documented factory defaults (Cfg.ini + OPT defaults) used by
# reset for covered fields only. Everything else is preserved.
RESET_CPI = (400, 800, 1600, 3200)
RESET_POLLING_HZ = 1000  # DR=0x1000
RESET_DEBOUNCE_MS = 3  # Debounce=3
RESET_SLEEP_S = 60  # SleepTime default
