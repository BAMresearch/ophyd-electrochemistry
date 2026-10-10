"""Versioned canonical request encoding with an allowlisted, validating decoder."""

import hashlib
import json
import math
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from .acquisition import AcquisitionRequest, StartMode
from .exceptions import ValidationError
from .protocols import (
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    PotentiostaticHold,
    VoltagePulseSequence,
)
from .waveforms import ArbitraryWaveform, MultisineSpec, PRBSWaveform

REQUEST_SCHEMA = "ophyd-electrochemistry/request-v1"
_MODELS = {
    cls.__name__: cls
    for cls in (
        AcquisitionRequest,
        PotentiostaticHold,
        GalvanostaticHold,
        CyclicVoltammetry,
        CurrentPulseSequence,
        VoltagePulseSequence,
        ArbitraryWaveform,
        PRBSWaveform,
        MultisineSpec,
    )
}


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"enum": type(value).__name__, "value": value.value}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("Canonical object keys must be strings")
        return {key: _encode(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return 0.0 if value == 0 else value
    raise ValidationError("Cannot serialize nonfinite or unsupported values")


def canonical_json(value: Any) -> str:
    """Sorted compact UTF-8 JSON; finite floats round-trip through Python JSON."""
    return json.dumps(
        _encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_json(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_request_json(request: AcquisitionRequest) -> str:
    if not isinstance(request, AcquisitionRequest):
        raise ValidationError("Expected AcquisitionRequest")
    return canonical_json({"schema": REQUEST_SCHEMA, "request": request})


def request_sha256(request: AcquisitionRequest) -> str:
    return sha256_json(canonical_request_json(request))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if not isinstance(value, dict):
        return value
    if set(value) == {"enum", "value"} and value["enum"] == "StartMode":
        return StartMode(value["value"])
    if set(value) != {"type", "fields"} or value["type"] not in _MODELS:
        raise ValidationError("Unknown serialized model/type")
    cls = _MODELS[value["type"]]
    body = value["fields"]
    if not isinstance(body, dict) or set(body) != {field.name for field in fields(cls)}:
        raise ValidationError("Serialized model fields do not match schema")
    return cls(**{key: _decode(item) for key, item in body.items()})


def request_from_json(encoded: str) -> AcquisitionRequest:
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
        raise ValidationError("Request JSON must be a string <=16 MiB")
    try:
        payload = json.loads(encoded, object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {"schema", "request"}:
            raise ValidationError("Invalid request envelope")
        if payload["schema"] != REQUEST_SCHEMA:
            raise ValidationError("Unsupported request schema")
        result = _decode(payload["request"])
        if not isinstance(result, AcquisitionRequest):
            raise ValidationError("Envelope does not contain AcquisitionRequest")
        return result
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise ValidationError("Invalid serialized request") from exc
