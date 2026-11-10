"""Keithley 2460 ``SequenceBackend`` on the packaged sequencer runtime (ABI oe-k2460-seq-v1).

Every line sent is a fixed read-only query or a call to a packaged ``oe_seq_*``
function with numeric arguments, run under ``pcall`` so a runtime error comes
back as one ``ERR`` line instead of a silent timeout. Nothing is retried; a
transport failure leaves the backend in ERROR until ``recover``. Timing values
come from the compiled program and the instrument's own timestamps.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

import numpy as np
from numpy.typing import NDArray

from ...exceptions import (
    ElectrochemistryError,
    IncompatibleInstrumentError,
    RetainedDataError,
    ShutdownUnconfirmed,
    TransportError,
    TransportProtocolError,
    ValidationError,
)
from ...sequences.backend import READOUT_FIELDS, SequenceEvent, SequencePoll, SequenceRequest
from ...sequences.models import SafetyLimits
from ...state import DeviceState
from ...validation import integer, text
from .sequencer import (
    FIRMWARE_MAX_BLOCKS,
    Replayer,
    SequencerProfile,
    SequencerProgram,
    compile_program,
)
from .sequencer import (
    _call as runtime_call,
)
from .sequencer_runtime import sequencer_packaged_runtime
from .transport import (
    InstrumentIdentity,
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
    parse_source_output_enabled,
)

_T = TypeVar("_T")
_READ_CHUNK = 250  # oe_seq_read maximum, ~28 KB per reply at 15 digits (T5)
_EVENT_DRAIN_LIMIT = 1000
_MAX_LINE = 900
_CALL = re.compile(r"(oe_seq_[a-z]+)\(([-0-9.e, ]*)\)")
_INACTIVE_MODEL = {
    "trigger.STATE_IDLE",
    "trigger.STATE_ABORTED",
    "trigger.STATE_FAILED",
    "trigger.STATE_EMPTY",
    "trigger.STATE_BUILDING",
}
_LOGGED = {"OE_T0": "t0", "OE_END": "end", "OE_STOP": "stop", "OE_DONE": "done"}
_RAW_FIELDS = ("seconds", "fraction", "reading", "source", "status", "source_status")


class SequenceTransport(Protocol):
    """One connection owned exclusively by this backend."""

    @property
    def identity(self) -> InstrumentIdentity: ...

    @property
    def command_language(self) -> str: ...

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def query(self, command: str) -> str: ...
    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None: ...


@dataclass(frozen=True)
class _Status:
    runtime: str
    model: str
    block: int
    output: bool
    records: int
    acked: int

    @property
    def active(self) -> bool:
        # Unknown model states count as active: never assume the output is idle.
        return self.model not in _INACTIVE_MODEL


@dataclass(frozen=True)
class _LogEntry:
    number: int
    message: str
    severity: int  # 1 error, 2 warning, 4 information
    seconds: int
    fraction: float

    @property
    def time(self) -> float:
        return self.seconds + self.fraction


@dataclass(frozen=True)
class ClockOffset:
    """Instrument clock minus host UTC from bracketed ``eventlog.post`` pings."""

    offset_s: float
    uncertainty_s: float
    pings: int


def guarded(call: str, *, prints: bool = True) -> str:
    """Wrap one packaged runtime call so a Lua error prints ``ERR <message>``.

    ``prints=False`` is for functions that print nothing on success; the
    wrapper then prints ``OK``. Either way the reply is exactly one line.
    """
    match = _CALL.fullmatch(call)
    if match is None:
        raise ValidationError(f"not a numeric packaged runtime call: {call!r}")
    name, args = match.groups()
    report = (
        'if not ok then print("ERR " .. tostring(e)) end'
        if prints
        else 'if ok then print("OK") else print("ERR " .. tostring(e)) end'
    )
    line = f"local ok, e = pcall({name}{', ' + args if args else ''}) {report}"
    if len(line) >= _MAX_LINE:
        raise ValidationError(f"runtime call exceeds {_MAX_LINE} characters")
    return line


def _int(value: str, name: str) -> int:
    try:
        number = float(value)
    except ValueError as exc:
        raise TransportProtocolError(f"invalid {name}: {value!r}") from exc
    if number != int(number):
        raise TransportProtocolError(f"invalid {name}: {value!r}")
    return int(number)


def parse_status(reply: str) -> _Status:
    fields = reply.split("\t")
    if len(fields) != 6:
        raise TransportProtocolError(f"unexpected oe_seq_status reply {reply!r}")
    runtime, model, block, output, records, acked = fields
    return _Status(
        runtime,
        model,
        _int(block, "model block"),
        parse_source_output_enabled(output),
        _int(records, "record count"),
        _int(acked, "acknowledged offset"),
    )


def parse_event(reply: str) -> _LogEntry:
    # eventlog.next: number, message, severity, node, seconds, nanoseconds.
    fields = reply.split("\t")
    if len(fields) < 6:
        raise TransportProtocolError(f"unexpected event-log entry {reply!r}")
    return _LogEntry(
        _int(fields[0], "event number"),
        "\t".join(fields[1:-4]),
        _int(fields[-4], "event severity"),
        _int(fields[-2], "event seconds"),
        _int(fields[-1], "event nanoseconds") * 1e-9,
    )


def parse_records(reply: str, count: int) -> tuple[NDArray[Any], ...]:
    """printbuffer: per record seconds, fraction, reading, source value, status, source status."""
    try:
        values = np.array([float(v) for v in reply.split(",")])
    except ValueError as exc:
        raise TransportProtocolError("non-numeric buffer readout") from exc
    if len(values) != 6 * count:
        raise TransportProtocolError(f"expected {6 * count} buffer values, got {len(values)}")
    table = values.reshape(count, 6)
    return (
        table[:, 0].astype(np.int64),
        table[:, 1],
        table[:, 2],
        table[:, 3],
        table[:, 4].astype(np.int64),
        table[:, 5].astype(np.int64),
    )


class Keithley2460SequenceBackend:
    """Sequences on a 2460 through the packaged sequencer runtime.

    ``connect`` installs the exact packaged runtime with output OFF (it is lost
    at power-off) unless a model is still running from another session.
    A run's records are acknowledged to the instrument when this backend has
    read them all and the run has concluded, at ``close`` (Flyer unstage) or at
    the next ``prepare``. Records nobody acknowledged (a crashed host, a
    ``RE.halt``) make ``prepare`` refuse until ``archive_retained`` saves them.
    """

    def __init__(
        self,
        transport: SequenceTransport,
        *,
        terminals: Literal["front", "rear"],
        four_wire: bool = True,
        start_edge: Literal["rising", "falling"] = "rising",
        profile: SequencerProfile | None = None,
        expected_serial_number: str | None = None,
        expected_firmware: str = "1.7.16a",
        line_frequency_hz: int = 50,
        install_runtime: bool = True,
        pings: int = 5,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if terminals not in ("front", "rear"):
            raise ValidationError("terminals must be front or rear")
        if start_edge not in ("rising", "falling"):
            raise ValidationError("start_edge must be rising or falling")
        for name, flag in (("four_wire", four_wire), ("install_runtime", install_runtime)):
            if not isinstance(flag, bool):
                raise ValidationError(f"{name} must be bool")
        if line_frequency_hz not in (50, 60):
            raise ValidationError("line_frequency_hz must be 50 or 60")
        integer(pings, "pings")
        self.profile = SequencerProfile() if profile is None else profile
        if not isinstance(self.profile, SequencerProfile):
            raise ValidationError("profile must be SequencerProfile")
        if self.profile.max_blocks > FIRMWARE_MAX_BLOCKS:
            raise ValidationError(
                f"profile max_blocks {self.profile.max_blocks} exceeds the 2460's "
                f"{FIRMWARE_MAX_BLOCKS} trigger blocks"
            )
        self._transport = transport
        self.terminals, self.four_wire, self.start_edge = terminals, four_wire, start_edge
        self._serial = (
            None
            if expected_serial_number is None
            else text(expected_serial_number, "expected_serial_number")
        )
        self._firmware = text(expected_firmware, "expected_firmware")
        self._line_frequency = line_frequency_hz
        self._install = install_runtime
        self._pings = pings
        self._wall_clock = wall_clock
        self._artifact = sequencer_packaged_runtime()
        self._lock = threading.RLock()
        self._connected = False
        self._broken = False
        self._identity: InstrumentIdentity | None = None
        self._clock: ClockOffset | None = None
        self._pre_run_events = ""
        self._reset_run(None)

    def _reset_run(self, program: SequencerProgram | None) -> None:
        self._program = program
        self._replayer = None if program is None else Replayer(program)
        self._kicked = False
        self._read = 0
        self._t0: tuple[int, float] | None = None
        self._logged: set[str] = set()
        self._errors: list[str] = []
        self._abort_reason: str | None = None
        # Every other event-log entry seen during the run, for diagnosis (bounded).
        self.instrument_log: deque[str] = deque(maxlen=500)
        self._outcome: tuple[str, str] | None = None
        self._last = (0.0, 0, 0)  # t_s, step, repeat of the last record

    # Transport

    def _io(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except TransportError:
            self._broken = True
            raise

    def _query(self, command: str) -> str:
        return self._io(lambda: self._transport.query(command))

    def _run(self, call: str, *, prints: bool = True) -> str:
        reply = self._query(guarded(call, prints=prints))
        if reply.startswith("ERR "):
            raise ElectrochemistryError(f"{call} refused by the runtime: {reply[4:]}")
        return reply

    def _call(self, call: str, *, prints: bool = True) -> str:
        reply = self._run(call, prints=prints)
        if not reply.startswith("OK"):
            raise TransportProtocolError(f"{call}: unexpected reply {reply!r}")
        return reply

    def _status(self) -> _Status:
        return parse_status(self._run("oe_seq_status()"))

    def _off(self, call: str) -> None:
        reply = self._run(call)
        if reply != "OFF":
            raise ShutdownUnconfirmed(f"{call} did not confirm output OFF: {reply!r}")

    def _drain_events(self) -> list[_LogEntry]:
        count = _int(self._query("print(eventlog.getcount(eventlog.SEV_ALL))"), "event count")
        return [
            parse_event(self._query("print(eventlog.next(eventlog.SEV_ALL))"))
            for _ in range(min(count, _EVENT_DRAIN_LIMIT))
        ]

    def _require_connected(self) -> None:
        if not self._connected:
            raise ElectrochemistryError("Backend is not connected")
        if self._broken:
            raise ElectrochemistryError("Transport failed; recover() first")

    # SequenceBackend

    def connect(self) -> None:
        with self._lock:
            if self._connected and not self._broken:
                return
            # A new session does not know which records an earlier one read.
            self._reset_run(None)
            self._io(self._transport.connect)
            try:
                identity = self._verify()
            except Exception:
                self._connected = False
                try:
                    self._transport.close()
                except TransportError:
                    pass
                raise
            self._identity = identity
            self._connected, self._broken = True, False

    def _verify(self) -> InstrumentIdentity:
        identity = self._transport.identity
        if identity.firmware_version != self._firmware:
            raise IncompatibleInstrumentError(
                f"firmware {identity.firmware_version} is not the reviewed {self._firmware}"
            )
        if self._serial is not None and identity.serial_number != self._serial:
            raise IncompatibleInstrumentError("instrument serial number differs")
        if self._transport.command_language != "TSP":
            raise IncompatibleInstrumentError("the 2460 must already use the TSP language")
        frequency = _int(self._query("print(localnode.linefreq)"), "line frequency")
        if frequency != self._line_frequency:
            raise IncompatibleInstrumentError(
                f"line frequency {frequency} Hz; the timing profile is for "
                f"{self._line_frequency} Hz"
            )
        model = self._query("print(trigger.model.state())").split("\t")[0]
        # A model still running from another session is observed, never reinstalled under.
        if self._install and model in _INACTIVE_MODEL:
            artifact = self._artifact
            self._io(
                lambda: self._transport.load_runtime(
                    artifact.source, abi=artifact.abi, sha256=artifact.sha256
                )
            )
        build = self._query("print(oe_seq_runtime_build)")
        if build != self._artifact.build:
            raise IncompatibleInstrumentError(
                f"sequencer runtime build {build!r} is not {self._artifact.build!r}"
            )
        return identity

    def close(self) -> None:
        with self._lock:
            if not self._connected:
                return
            if not self._broken:
                status = self._status()
                if status.active:
                    raise ElectrochemistryError("Cannot close while the trigger model is active")
                # Closing (Flyer unstage) follows the RunEngine's last document, so a
                # concluded, fully read run has been handed to the run file.
                if self._outcome is not None and status.acked < self._read == status.records:
                    self._call(f"oe_seq_ack({status.records})")
            self._connected = False
            self._transport.close()

    def _observe(self, status: _Status) -> tuple[DeviceState, bool | None]:
        program = self._program
        if status.active:
            waiting = (
                status.model == "trigger.STATE_WAITING"
                and program is not None
                and program.external_start
                and status.block == 2  # READY high, then WAIT_START
            )
            return (DeviceState.WAITING_START if waiting else DeviceState.RUNNING), status.output
        state = {
            "aborted": DeviceState.ABORTED,
            "armed": DeviceState.COMPLETE,
            "ready": DeviceState.PREPARED if program is not None else DeviceState.IDLE,
        }.get(status.runtime, DeviceState.IDLE)
        return state, status.output

    def inspect(self) -> tuple[DeviceState, bool | None]:
        with self._lock:
            if not self._connected:
                raise ElectrochemistryError("Backend is not connected")
            if self._broken:
                return DeviceState.ERROR, None
            return self._observe(self._status())

    def equipment(self) -> dict[str, str | int | float | bool]:
        with self._lock:
            result: dict[str, str | int | float | bool] = {
                "backend": "k2460-sequencer",
                "runtime_build": self._artifact.build,
                "runtime_sha256": self._artifact.sha256,
                "terminals": self.terminals,
                "four_wire": self.four_wire,
                "start_edge": self.start_edge,
                "line_frequency_hz": self._line_frequency,
            }
            if self._identity is not None:
                result["identity"] = self._identity.raw
                result["serial_number"] = self._identity.serial_number
                result["firmware"] = self._identity.firmware_version
            if self._clock is not None:
                result["clock_offset_s"] = self._clock.offset_s
                result["clock_uncertainty_s"] = self._clock.uncertainty_s
                result["clock_method"] = f"eventlog.post bracket, best of {self._clock.pings}"
            if self._pre_run_events:
                result["pre_run_instrument_events"] = self._pre_run_events
            if self._program is not None:
                result.update(self._program.settings())
            return result

    def _configure(self, limits: SafetyLimits) -> None:
        p = self.profile
        vlimit = max(abs(limits.v_min_abs), abs(limits.v_max_abs)) + p.vlimit_margin_v
        self._call(
            runtime_call(
                "configure",
                limits.v_min_abs,
                limits.v_max_abs,
                limits.i_max_abs,
                vlimit,
                p.ready_line,
                p.busy_line,
                p.marker_line,
                p.start_line,
                int(self.four_wire),
                int(self.terminals == "front"),
                1 if self.start_edge == "rising" else 2,
            )
        )

    def secure_off_mode(self, limits: SafetyLimits) -> None:
        """Connect, set HIGH-Z with read-back, disconnect: the 2460 powers on in NORMAL.

        An active model or unread records mean a prepare has already set HIGH-Z
        since power-on, so both are left untouched.
        """
        with self._lock:
            self.connect()
            try:
                status = self._status()
                if not status.active and status.records == status.acked:
                    self._configure(limits)
            finally:
                # Not close(): it refuses while a model from an earlier session runs.
                self._connected = False
                try:
                    self._transport.close()
                except TransportError:
                    pass

    def prepare(self, request: SequenceRequest) -> None:
        with self._lock:
            self._require_connected()
            status = self._status()
            state, output = self._observe(status)
            if status.active or output is not False:
                raise ShutdownUnconfirmed(f"Prepare requires an idle model and output OFF: {state}")
            if state not in (DeviceState.IDLE, DeviceState.PREPARED, DeviceState.COMPLETE):
                raise ElectrochemistryError(f"Prepare requires IDLE, PREPARED or COMPLETE: {state}")
            if status.records > status.acked:
                if self._program is not None and self._read == status.records:
                    self._call(f"oe_seq_ack({status.records})")
                else:
                    raise RetainedDataError(
                        f"the instrument holds {status.records - status.acked} unread records "
                        "of an earlier run; archive_retained() them first"
                    )
            program = compile_program(
                request.sequence,
                request.limits,
                self.profile,
                external_start=request.start_mode == "external",
            )
            if len(program.blocks) > FIRMWARE_MAX_BLOCKS:
                blocks = len(program.blocks)
                raise ValidationError(
                    f"{blocks} trigger blocks exceed {FIRMWARE_MAX_BLOCKS}; split the run"
                )
            self._reset_run(None)
            earlier = [e for e in self._drain_events() if e.severity <= 2]
            self._pre_run_events = " | ".join(f"{e.number} {e.message}" for e in earlier[-10:])
            self._configure(request.limits)
            reply = ""
            for call in program.runtime_calls():
                reply = self._call(call)
            if reply != f"OK {len(program.blocks)} {program.capacity}":
                raise TransportProtocolError(f"unexpected commit reply {reply!r}")
            # Errors or warnings while configuring mean a setting did not take.
            problems = [e for e in self._drain_events() if e.severity <= 2]
            if problems:
                raise ElectrochemistryError(
                    "instrument events during upload: "
                    + " | ".join(f"{e.number} {e.message}" for e in problems)
                )
            self._clock = self._map_clock()
            self._reset_run(program)

    def _map_clock(self) -> ClockOffset:
        brackets = []
        for _ in range(self._pings):
            before = self._wall_clock()
            self._call("oe_seq_ping()")
            brackets.append((before, self._wall_clock()))
        stamps = [e.time for e in self._drain_events() if e.message == "User: OE_PING"]
        if len(stamps) != len(brackets):
            raise TransportProtocolError(f"{len(stamps)} OE_PING events for {len(brackets)} pings")
        stamp, (before, after) = min(
            zip(stamps, brackets, strict=True), key=lambda item: item[1][1] - item[1][0]
        )
        return ClockOffset(stamp - (before + after) / 2, (after - before) / 2, len(stamps))

    def kickoff(self) -> None:
        with self._lock:
            self._require_connected()
            if self._program is None or self._kicked:
                raise ElectrochemistryError("Kickoff requires a prepared sequence")
            self._call("oe_seq_arm()")
            self._kicked = True

    def time_origin_epoch_s(self) -> float | None:
        with self._lock:
            if self._t0 is None or self._clock is None:
                return None
            return self._t0[0] + self._t0[1] - self._clock.offset_s

    def _relative(self, seconds: int, fraction: float) -> float:
        assert self._t0 is not None
        return (seconds - self._t0[0]) + (fraction - self._t0[1])

    def _read_events(self) -> list[SequenceEvent]:
        events: list[SequenceEvent] = []
        for entry in self._drain_events():
            name = entry.message.rpartition("logged: ")[2]
            if entry.message.startswith("TM path") and name in _LOGGED:
                if name == "OE_T0":
                    self._t0 = (entry.seconds, entry.fraction)
                self._logged.add(_LOGGED[name])
                continue
            self.instrument_log.append(f"{entry.number}\t{entry.severity}\t{entry.message}")
            if entry.severity == 1:
                detail = f"instrument {entry.number}: {entry.message}"
                self._errors.append(detail)
                t = 0.0 if self._t0 is None else self._relative(entry.seconds, entry.fraction)
                events.append((t, self._last[1], self._last[2], "error", detail))
        return events

    def _read_records(self, count: int) -> tuple[NDArray[Any], ...]:
        parts = []
        first = self._read + 1
        while count:
            size = min(count, _READ_CHUNK)
            reply = self._query(guarded(f"oe_seq_read({first}, {size})"))
            if reply.startswith("ERR "):
                raise ElectrochemistryError(f"oe_seq_read refused: {reply[4:]}")
            parts.append(parse_records(reply, size))
            first, count = first + size, count - size
        if not parts:
            return tuple(np.empty(0, dtype=t) for t in ("i8", "f8", "f8", "f8", "i8", "i8"))
        return tuple(np.concatenate(columns) for columns in zip(*parts, strict=True))

    def poll(self, max_records: int) -> SequencePoll:
        integer(max_records, "max_records")
        with self._lock:
            self._require_connected()
            if self._program is None or self._replayer is None or not self._kicked:
                raise ElectrochemistryError("Poll requires kickoff")
            # Status first: a finished model has logged all its events and records.
            status = self._status()
            events = self._read_events()
            count = min(status.records - self._read, max_records)
            seconds, fraction, reading, source, measure_status, source_status = self._read_records(
                count
            )
            t_s = fraction
            if count:
                if self._t0 is None:
                    raise TransportProtocolError("readings without an OE_T0 event")
                t_s = (seconds - self._t0[0]) + (fraction - self._t0[1])
            readout, replayed = self._replayer.feed(
                t_s, reading, source, measure_status, source_status
            )
            self._read += count
            if count:
                self._last = (
                    float(t_s[-1]),
                    int(readout["step_index"][-1]),
                    int(readout["repeat_index"][-1]),
                )
            events = sorted(events + replayed, key=lambda event: event[0])
            if self._outcome is None and not status.active and self._read == status.records:
                events.extend(self._conclude(status))
            outcome, detail = self._outcome if self._outcome is not None else (None, "")
            return SequencePoll(readout, events, outcome, detail)

    def _conclude(self, status: _Status) -> list[SequenceEvent]:
        assert self._replayer is not None
        t, step, repeat = self._last
        if self._abort_reason is not None:
            self._outcome = ("aborted", self._abort_reason)
            return [(t, step, repeat, "abort", self._abort_reason)]
        logged: Literal["end", "stop"] | None = (
            "stop" if "stop" in self._logged else "end" if "end" in self._logged else None
        )
        outcome, detail = self._replayer.finish(logged)
        extra = list(self._errors)
        if status.model == "trigger.STATE_FAILED":
            extra.append("trigger model failed")
        if status.output:
            self._off("oe_seq_off()")
            extra.append("output ON after the model ended; forced OFF")
        if extra and outcome in ("completed", "stopped_cutoff"):
            outcome = "error"
        self._outcome = (outcome, "; ".join(filter(None, [detail, *extra])))
        return []

    def abort(self, reason: str) -> None:
        with self._lock:
            self._require_connected()
            status = self._status()
            if not status.active and status.runtime != "armed":
                return
            self._off("oe_seq_abort()")
            if self._outcome is None:
                self._abort_reason = reason

    def recover(self) -> None:
        with self._lock:
            if not self._connected:
                # The Flyer unstages (closes) after an abort; reconnect by inspection.
                self.connect()
            elif self._broken:
                # Reconnect by inspection only; the OFF below is the one mutation.
                self._io(self._transport.connect)
                self._broken = False
            self._off("oe_seq_off()")
            self._call("oe_seq_initialize()", prints=False)
            self._kicked = False

    # Retained data

    def archive_retained(self, destination: str | Path) -> Path:
        """Save unread records of an earlier run (raw instrument fields), then release them."""
        with self._lock:
            self._require_connected()
            status = self._status()
            if status.active or status.output:
                raise ShutdownUnconfirmed("Archive requires an idle model and output OFF")
            first, count = status.acked, status.records - status.acked
            self._read = first
            columns = self._read_records(count)
            path = Path(destination)
            metadata = {
                "identity": None if self._identity is None else self._identity.raw,
                "runtime_build": self._artifact.build,
                "status": [status.runtime, status.model, status.records, status.acked],
                "first_record": first + 1,
                "archived_utc_s": self._wall_clock(),
            }
            arrays: dict[str, Any] = dict(zip(_RAW_FIELDS, columns, strict=True))
            arrays["metadata"] = np.array(json.dumps(metadata))
            with path.open("xb") as handle:
                np.savez(handle, **arrays)
            self._call(f"oe_seq_ack({status.records})")
            self._read = status.records
            return path


def k2460_sequence_backend(
    resource: str,
    *,
    terminals: Literal["front", "rear"],
    expected_serial_number: str | None = None,
    four_wire: bool = True,
    start_edge: Literal["rising", "falling"] = "rising",
    visa_backend: str = "@py",
    open_timeout_ms: int = 5_000,
    io_timeout_ms: int = 10_000,
) -> Keithley2460SequenceBackend:
    """A sequence backend on a PyVISA session; nothing is sent until ``connect``.

    Needs the ``visa`` extra. The open timeout covers a cold link-local link
    (bench, 3 Nov); the I/O timeout covers a 250-record read (~20 ms) with margin.
    """
    config = VisaTransportConfig(
        resource_name=resource,
        backend=visa_backend,
        open_timeout_ms=open_timeout_ms,
        io_timeout_ms=io_timeout_ms,
        required_language="TSP",
    )
    return Keithley2460SequenceBackend(
        PyVisaKeithley2460Transport(config),
        terminals=terminals,
        four_wire=four_wire,
        start_edge=start_edge,
        expected_serial_number=expected_serial_number,
    )


__all__ = [
    "READOUT_FIELDS",
    "ClockOffset",
    "Keithley2460SequenceBackend",
    "guarded",
    "k2460_sequence_backend",
]
