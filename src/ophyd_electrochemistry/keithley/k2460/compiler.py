"""Pure compiler boundary; compilation/resource validation remains M1 work."""

from dataclasses import dataclass
from typing import Protocol

from ...acquisition import AcquisitionRequest
from .config import Keithley2460Config


@dataclass(frozen=True, kw_only=True)
class CompiledProgram:
    canonical_request_json: str
    program_sha256: str
    source_function: str
    source_steps: tuple[float, ...]
    achieved_period_s: float
    expected_record_count: int
    trigger_block_count: int
    runtime_abi: str


class Keithley2460Compiler(Protocol):
    def compile(self, request: AcquisitionRequest, config: Keithley2460Config) -> CompiledProgram:
        """Validate limits/capabilities/budgets and compile without network access."""
        ...
