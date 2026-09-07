"""Typed settings, verified limits, and validation.

Milestone-1 rule: only fields with hardware evidence are modeled, and
only the debounce pair is writable through the internal apply path
(restore uses the same path with backup-file provenance). Everything
else raises instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import protocol


@dataclass(frozen=True)
class Capabilities:
    """Firmware-supported values frozen from milestone-1 evidence."""

    polling_hz: tuple[int, ...] = (125, 250, 500, 1000)
    dpi_min: int = protocol.DPI_MIN
    dpi_knee: int = protocol.DPI_KNEE
    dpi_step_lo: int = protocol.DPI_STEP_LO
    dpi_above: int = protocol.DPI_ABOVE
    dpi_max: int = protocol.DPI_MAX
    dpi_step_hi: int = protocol.DPI_STEP_HI
    dpi_stages: int = 4
    # Debounce range is NOT established (observed 1 ms, tool default
    # 3 ms); milestone-1 writes are limited to values with provenance
    # (previously observed on this device or the documented default).
    debounce_default_ms: int = 3


CAPABILITIES = Capabilities()


@dataclass(frozen=True)
class Status:
    connection: str  # connected | receiver-only | unavailable
    percent: int | None
    charging: int | None
    profile: int | None
    link_up: bool | None


@dataclass(frozen=True)
class CpiStage:
    index: int  # 0-based
    x: int
    y: int


@dataclass(frozen=True)
class SettingsSnapshot:
    fingerprint: str
    revision: str
    polling_hz: int | None
    debounce_ms: int | None
    profile: int | None
    stages: tuple[CpiStage, ...]
    # Raw config bytes (unknown regions preserved verbatim).
    raw: dict[int, int] = field(compare=False)

    def describe(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "revision": self.revision,
            "pollingHz": self.polling_hz,
            "debounceMs": self.debounce_ms,
            "profile": self.profile,
            "stages": [
                {"index": s.index, "x": s.x, "y": s.y} for s in self.stages
            ],
        }


def snapshot_from_memory(fingerprint: str, mem: dict[int, int], profile: int | None) -> SettingsSnapshot:
    """Decode observed memory. Unknown/undecodable fields become None (never guessed)."""
    polling = protocol.decode_polling_hz(mem)
    debounce = protocol.decode_pair(mem, protocol.ADDR_DEBOUNCE)
    stages: list[CpiStage] = []
    for index in range(CAPABILITIES.dpi_stages):
        base = protocol.ADDR_CPI + index * protocol.CPI_RECORD_LEN
        cells = [mem.get(base + k) for k in range(4)]
        if any(cell is None for cell in cells):
            break
        try:
            x, y = protocol.decode_cpi_record(bytes(cells))
        except ValueError:
            break
        stages.append(CpiStage(index=index, x=x, y=y))
    return SettingsSnapshot(
        fingerprint=fingerprint,
        revision=protocol.config_revision(mem),
        polling_hz=polling,
        debounce_ms=debounce,
        profile=profile,
        stages=tuple(stages),
        raw=dict(mem),
    )


@dataclass(frozen=True)
class DebounceChange:
    """The sole milestone-1 forward write: debounce pair with provenance."""

    value_ms: int
    proven_values: frozenset[int]  # observed on device or documented default

    def validate(self) -> None:
        if self.value_ms not in self.proven_values:
            raise ValueError(
                f"debounce {self.value_ms} ms has no provenance "
                f"(known-good: {sorted(self.proven_values)}); refusing to guess a range"
            )

    def encoded(self) -> bytes:
        self.validate()
        return bytes([self.value_ms, protocol.stored_checksum(self.value_ms)])
