"""Reviewed measurement profiles for the narrow K2460 commissioning runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from ...exceptions import ValidationError


class K2460MeasurementProfile(StrEnum):
    """Versioned selectors; arbitrary target measurement settings are not accepted."""

    NPLC_1 = "k2460_0p2v_1nplc_v1"
    NPLC_0_1 = "k2460_0p2v_0p1nplc_v1"
    NPLC_0_01 = "k2460_0p2v_0p01nplc_v1"


@dataclass(frozen=True, kw_only=True)
class K2460MeasurementSettings:
    """Fully resolved, immutable settings applied while source output is off."""

    profile: K2460MeasurementProfile
    instrument_code: int
    nplc: float
    voltage_range_v: float = 0.2
    autorange_enabled: bool = False
    filter_enabled: bool = False
    relative_enabled: bool = False
    autozero: Literal["once_then_off"] = "once_then_off"


_SETTINGS = {
    K2460MeasurementProfile.NPLC_1: K2460MeasurementSettings(
        profile=K2460MeasurementProfile.NPLC_1,
        instrument_code=1,
        nplc=1.0,
    ),
    K2460MeasurementProfile.NPLC_0_1: K2460MeasurementSettings(
        profile=K2460MeasurementProfile.NPLC_0_1,
        instrument_code=2,
        nplc=0.1,
    ),
    K2460MeasurementProfile.NPLC_0_01: K2460MeasurementSettings(
        profile=K2460MeasurementProfile.NPLC_0_01,
        instrument_code=3,
        nplc=0.01,
    ),
}


def resolve_measurement_profile(
    profile: K2460MeasurementProfile,
) -> K2460MeasurementSettings:
    """Resolve one exact allowlisted selector without coercing strings."""

    if not isinstance(profile, K2460MeasurementProfile):
        raise ValidationError("measurement_profile must be a K2460MeasurementProfile")
    return _SETTINGS[profile]
