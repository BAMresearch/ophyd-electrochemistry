"""Acquisition intent, kept separate from electrochemical protocol parameters."""

from dataclasses import dataclass
from enum import StrEnum

from .exceptions import ValidationError
from .protocols import ElectrochemicalProgram
from .validation import text


class StartMode(StrEnum):
    IMMEDIATE = "immediate"
    EXTERNAL_TRIGGER = "external_trigger"


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
