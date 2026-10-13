"""Acquisition intent, kept separate from electrochemical protocol parameters."""

from dataclasses import dataclass
from enum import StrEnum

from .exceptions import ValidationError
from .protocols import ElectrochemicalProgram
from .validation import integer, positive, text

_SHA256_LENGTH = 64


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


class StartMode(StrEnum):
    IMMEDIATE = "immediate"
    EXTERNAL_TRIGGER = "external_trigger"


@dataclass(frozen=True, kw_only=True)
class PreparedAcquisition:
    """Backend-reviewed identity, storage and timing for one prepared request.

    ``completion_bound_s`` runs from successful kickoff/arming until the local
    runtime must be terminal. ``communication_margin_s`` covers one bounded
    host observation transaction after that local deadline. Both are operational
    watchdog inputs, not requested scientific timing.
    """

    request_sha256: str
    program_sha256: str
    expected_records: int
    capacity_records: int
    completion_bound_s: float
    communication_margin_s: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_sha256", _sha256(self.request_sha256, "request_sha256"))
        object.__setattr__(self, "program_sha256", _sha256(self.program_sha256, "program_sha256"))
        object.__setattr__(
            self, "expected_records", integer(self.expected_records, "expected_records")
        )
        object.__setattr__(
            self, "capacity_records", integer(self.capacity_records, "capacity_records")
        )
        if self.expected_records > self.capacity_records:
            raise ValidationError("expected_records must not exceed capacity_records")
        object.__setattr__(
            self,
            "completion_bound_s",
            positive(self.completion_bound_s, "completion_bound_s"),
        )
        object.__setattr__(
            self,
            "communication_margin_s",
            positive(self.communication_margin_s, "communication_margin_s"),
        )


@dataclass(frozen=True, kw_only=True)
class AcquisitionRequest:
    """Immutable intent; physical validation still precedes operational arming."""

    program: ElectrochemicalProgram
    experiment_id: str
    start_mode: StartMode = StartMode.IMMEDIATE

    def __post_init__(self) -> None:
        text(self.experiment_id, "experiment_id")
        if not isinstance(self.start_mode, StartMode):
            raise ValidationError("start_mode must be a StartMode")
        from .protocols import (
            CurrentPulseSequence,
            CyclicVoltammetry,
            GalvanostaticHold,
            PotentiostaticHold,
            VoltagePulseSequence,
        )
        from .waveforms import ArbitraryWaveform, PRBSWaveform

        if not isinstance(
            self.program,
            (
                PotentiostaticHold,
                GalvanostaticHold,
                CyclicVoltammetry,
                CurrentPulseSequence,
                VoltagePulseSequence,
                ArbitraryWaveform,
                PRBSWaveform,
            ),
        ):
            raise ValidationError("Unsupported electrochemical program type")
