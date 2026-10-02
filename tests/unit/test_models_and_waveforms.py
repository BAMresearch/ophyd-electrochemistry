import cmath
import hashlib
import math
from dataclasses import FrozenInstanceError, replace

import pytest

from ophyd_electrochemistry import (
    AcquisitionRequest,
    ArbitraryWaveform,
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    MultisineSpec,
    PotentiostaticHold,
    PRBSWaveform,
    StartMode,
    canonical_request_json,
    generate_multisine,
    prbs_bits,
    request_from_json,
    request_sha256,
)
from ophyd_electrochemistry.exceptions import UnsupportedCapabilityError, ValidationError
from ophyd_electrochemistry.keithley.k2460 import DigitalIOConfig


def hold():
    return PotentiostaticHold(voltage=1, duration_s=1, sample_period_s=0.1, current_limit_a=0.1)


def multisine(**changes):
    values = dict(
        source_function="current",
        voltage_limit_v=5,
        sample_period_s=0.02,
        bias=0.02,
        frequencies_hz=(1.0, 3.0),
        amplitudes=(0.01, 0.005),
        phases_rad=(0.2, 1.1),
        point_period_s=0.01,
        point_count=100,
        repeats=2,
    )
    return MultisineSpec(**(values | changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("voltage", float("nan")),
        ("voltage", float("inf")),
        ("voltage", True),
        ("duration_s", 0),
        ("duration_s", -1),
        ("sample_period_s", "0.1"),
        ("current_limit_a", -0.01),
        ("voltage", 10**1000),
    ],
)
def test_hold_rejects_nonfinite_or_wrong_si_values(field, value):
    with pytest.raises(ValidationError):
        replace(hold(), **{field: value})


def test_structural_validation_is_not_physical_approval():
    assert replace(hold(), voltage=1e6).voltage == 1e6
    with pytest.raises(FrozenInstanceError):
        hold().voltage = 0
    with pytest.raises(ValidationError):
        GalvanostaticHold(
            current_a=0.1,
            duration_s=1,
            sample_period_s=0.1,
            voltage_limit_v=5,
            lower_cutoff_v=4,
            upper_cutoff_v=3,
        )
    with pytest.raises(ValidationError):
        CurrentPulseSequence(
            baseline_current_a=0,
            pulse_current_a=0.1,
            pulse_width_s=1,
            period_s=1,
            count=True,
            sample_period_s=0.1,
            voltage_limit_v=5,
        )
    with pytest.raises(ValidationError):
        CyclicVoltammetry(
            start_voltage_v=2,
            first_vertex_v=1,
            second_vertex_v=0,
            scan_rate_v_per_s=1,
            cycles=1,
            sample_period_s=0.1,
            current_limit_a=0.1,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"levels": [0.0, 0.1]},
        {"levels": ()},
        {"levels": (float("nan"),)},
        {"repeats": True},
        {"repeats": 1.5},
        {"repeats": 0},
        {"current_limit_a": 0.1},
        {"source_function": "power"},
    ],
)
def test_arbitrary_list_is_immutable_finite_and_has_one_source_function(changes):
    values = dict(
        source_function="current",
        voltage_limit_v=5,
        sample_period_s=0.01,
        point_period_s=0.01,
        levels=(0.0, 0.1),
    )
    with pytest.raises(ValidationError):
        ArbitraryWaveform(**(values | changes))


@pytest.mark.parametrize("order,expected", [(2, "101"), (3, "1001011"), (4, "100010011010111")])
def test_prbs_known_bit_vectors_with_fixed_output_convention(order, expected):
    assert prbs_bits(order, 1) == tuple(map(int, expected))


@pytest.mark.parametrize(
    "order,digest",
    [
        (2, "85f90dfea1d8027e1463e5ca971a250110a20df0119d204a74220bc63516d15b"),
        (3, "85698314f8c4984ded9442690adbdc176045a6a9f1ab8178eb5e62266cd60603"),
        (4, "c287557bd8dcfe2006baea4832d3fc64df75831d850d88c94857ad48a0d1fd2a"),
        (5, "695f98093c455948a696b2680aa930ffe93b9929ff9595cd6dc4b80d93399f70"),
        (6, "c5b04ab1e58a6f1a73538e3b2b268e8fc47d0d6385f1a65dbb34cad5c8fe558e"),
        (7, "f6d6dc1183014c54f2758dffe473d52bd5540ca63683e2e23c77a2bf703c4af8"),
        (8, "6c8bcc2fa31b4fc8a3d99635ac96c3ad33b08a9e040445a85d740a8f203a549e"),
        (9, "b176b8dee4caaebbfd7dc78a732d20b73d297ecba8c4d363abb1041c6640dc6b"),
        (10, "4f61330ec42483f82636140150be7b3329baa32c2cc0409b7ebb8beb5bb31a0e"),
    ],
)
def test_prbs_full_vectors_from_independent_polynomial_bit_recurrence(order, digest):
    # Frozen fixtures were calculated from s[n+m] = XOR(s[n+polynomial_exponent]),
    # not the production register/mask algorithm. Hash bytes contain integer 0/1.
    assert hashlib.sha256(bytes(prbs_bits(order, 1))).hexdigest() == digest


def test_multisine_rejects_combined_overflow_and_avoids_intermediate_phase_overflow():
    with pytest.raises(ValidationError, match="overflow"):
        multisine(amplitudes=(1e308, 1e308))
    # Use an exactly coherent single enormous-frequency tone with tiny source dwell.
    spec = multisine(
        point_count=100,
        point_period_s=1e-310,
        frequencies_hz=(1e308,),
        amplitudes=(0.01,),
        phases_rad=(0.2,),
    )
    assert all(math.isfinite(v) for v in generate_multisine(spec).levels)


@pytest.mark.parametrize("order", range(2, 11))
def test_every_supported_prbs_has_maximal_period_balance_and_correlation(order):
    bits = prbs_bits(order, 1)
    length = (1 << order) - 1
    assert len(bits) == length
    assert sum(bits) == 1 << (order - 1)
    # Every nonzero m-bit window appears once: independent period/state check.
    windows = {tuple(bits[(i + j) % length] for j in range(order)) for i in range(length)}
    assert len(windows) == length and (0,) * order not in windows
    bipolar = [2 * bit - 1 for bit in bits]
    for shift in {1, 2, length // 2}:
        assert sum(bipolar[i] * bipolar[(i + shift) % length] for i in range(length)) == -1


def test_prbs_seed_phase_and_rejected_generators():
    assert prbs_bits(3, 3) == (1, 1, 0, 0, 1, 0, 1)
    assert prbs_bits(7, 1)[:16] == tuple(map(int, "1000000100000110"))
    for seed in (0, 8, True, 1.5):
        with pytest.raises(ValidationError):
            prbs_bits(3, seed)
    with pytest.raises(UnsupportedCapabilityError):
        prbs_bits(64, 1)
    with pytest.raises(UnsupportedCapabilityError):
        prbs_bits(3, 1, generator_id="random")
    with pytest.raises(ValidationError):
        prbs_bits(10, 1, max_points=10)


def test_multisine_recovered_by_independent_dft():
    spec = multisine()
    waveform = generate_multisine(spec)
    assert waveform.multisine_spec == spec and len(waveform.levels) == 100
    assert sum(waveform.levels) / 100 == pytest.approx(spec.bias)
    for bin_index, amplitude, phase in zip(
        spec.tone_bins, spec.amplitudes, spec.phases_rad, strict=True
    ):
        coefficient = (
            sum(
                (v - spec.bias) * cmath.exp(-1j * math.tau * bin_index * n / 100)
                for n, v in enumerate(waveform.levels)
            )
            / 100
        )
        assert abs(coefficient) == pytest.approx(amplitude / 2)
        assert (cmath.phase(coefficient) + math.pi / 2) % math.tau == pytest.approx(phase)
    unwanted = (
        sum(v * cmath.exp(-1j * math.tau * 2 * n / 100) for n, v in enumerate(waveform.levels))
        / 100
    )
    assert abs(unwanted) < 1e-15


@pytest.mark.parametrize(
    "changes",
    [
        {"frequencies_hz": (0.0, 3.0)},
        {"frequencies_hz": (1.5, 3.0)},
        {"frequencies_hz": (1.0, 1.0)},
        {"frequencies_hz": (1.0, 50.0)},
        {"frequencies_hz": (1.0, 51.0)},
        {"amplitudes": (0.01,)},
        {"amplitudes": (0.01, -0.1)},
        {"phases_rad": (0.0, float("nan"))},
        {"point_count": True},
        {"coherence_tolerance_bins": 0.1},
    ],
)
def test_multisine_rejects_incoherent_or_invalid_tones(changes):
    with pytest.raises(ValidationError):
        multisine(**changes)


def test_generator_budget_prevents_expansion():
    spec = multisine(point_count=1_000_000, frequencies_hz=(0.0001, 0.0003))
    with pytest.raises(ValidationError, match="budget"):
        generate_multisine(spec, max_points=100)
    with pytest.raises(ValidationError, match="budget"):
        generate_multisine(multisine(), max_tones=1)


@pytest.mark.parametrize(
    "program",
    [
        hold(),
        GalvanostaticHold(current_a=-0.1, duration_s=1, sample_period_s=0.1, voltage_limit_v=5),
        CurrentPulseSequence(
            baseline_current_a=0,
            pulse_current_a=0.1,
            pulse_width_s=0.1,
            period_s=0.2,
            count=3,
            sample_period_s=0.01,
            voltage_limit_v=5,
        ),
        CyclicVoltammetry(
            start_voltage_v=0,
            first_vertex_v=1,
            second_vertex_v=-1,
            scan_rate_v_per_s=1,
            cycles=2,
            sample_period_s=0.1,
            current_limit_a=0.1,
        ),
        ArbitraryWaveform(
            source_function="voltage",
            current_limit_a=0.1,
            sample_period_s=0.01,
            levels=(1, 2),
            point_period_s=0.01,
        ),
        PRBSWaveform(
            source_function="current",
            voltage_limit_v=5,
            sample_period_s=0.01,
            low_level=-0.01,
            high_level=0.01,
            bit_period_s=0.01,
            order=7,
            seed=1,
        ),
        generate_multisine(multisine()),
    ],
)
def test_all_programs_round_trip_through_allowlisted_canonical_json(program):
    request = AcquisitionRequest(
        program=program, experiment_id="experiment-α", start_mode=StartMode.EXTERNAL_TRIGGER
    )
    restored = request_from_json(canonical_request_json(request))
    assert restored == request
    assert canonical_request_json(restored) == canonical_request_json(request)
    assert request_sha256(restored) == request_sha256(request)


def test_canonical_units_negative_zero_and_semantic_hash():
    a = AcquisitionRequest(program=replace(hold(), voltage=-0.0), experiment_id="e")
    b = AcquisitionRequest(program=replace(hold(), voltage=0), experiment_id="e")
    assert request_sha256(a) == request_sha256(b)
    assert request_sha256(a) != request_sha256(replace(a, experiment_id="other"))
    with pytest.raises(ValidationError):
        AcquisitionRequest(program=hold(), experiment_id="e", start_mode="immediate")
    with pytest.raises(ValidationError):
        AcquisitionRequest(program=hold(), experiment_id=" ")


@pytest.mark.parametrize(
    "encoded",
    [
        '{"schema":"wrong","request":{}}',
        '{"schema":"a","schema":"b","request":{}}',
        "null",
        '{"schema":"ophyd-electrochemistry/request-v1","request":{"type":"os.system","fields":{}}}',
        '{"schema":"ophyd-electrochemistry/request-v1","request":{"type":"AcquisitionRequest","fields":{}}}',
    ],
)
def test_decoder_rejects_unknown_schema_duplicate_keys_and_arbitrary_types(encoded):
    with pytest.raises(ValidationError):
        request_from_json(encoded)


@pytest.mark.parametrize(
    "changes",
    [
        {"busy": 1},
        {"ready": 7},
        {"start": True},
        {"abort": None, "external_abort_enabled": True},
        {"start_edge": "both"},
        {"ready_asserted_level": True},
    ],
)
def test_io_rejects_conflicting_lines_or_invalid_levels(changes):
    with pytest.raises(ValidationError):
        DigitalIOConfig(**changes)
