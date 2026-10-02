"""Acquisition intent, kept separate from electrochemical protocol parameters."""

from dataclasses import dataclass
from enum import StrEnum

from .protocols import ElectrochemicalProgram


class StartMode(StrEnum):
    IMMEDIATE = "immediate"
    EXTERNAL_TRIGGER = "external_trigger"


@dataclass(frozen=True, kw_only=True)
class AcquisitionRequest:
    """Inert request model; operational prepare MUST validate it before arming."""

    program: ElectrochemicalProgram
    experiment_id: str
    start_mode: StartMode = StartMode.IMMEDIATE
