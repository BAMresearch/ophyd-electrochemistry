# M3 bounded transport and commissioning diagnostic

M3 now provides an optional PyVISA transport and a deliberately read-only
commissioning command. It does not implement an operational ophyd Device, source
control, measurement acquisition, TSP upload, or runtime execution. Hardware gate
G05 remains **NOT RUN** because the required partial-transfer, retrieval/abort,
starvation, and single-controller evidence is incomplete.

## Read-only diagnostic

Prepare the locked optional environment:

```bash
uv sync --locked --python 3.12 --extra visa
```

Run with an explicit resource and backend. The target unit accepted both of these
forms during initial commissioning:

```bash
uv run --locked --extra visa ophyd-electrochemistry-k2460-diagnose \
  --resource 'TCPIP0::169.254.77.125::5025::SOCKET' \
  --backend '@py'

uv run --locked --extra visa ophyd-electrochemistry-k2460-diagnose \
  --resource 'TCPIP0::169.254.77.125::inst0::INSTR' \
  --backend '@py'
```

The command sends exactly three queries reviewed against the Model 2460 Reference
Manual Rev. C:

- `*IDN?`, documented as query-only, for manufacturer, model, serial and firmware;
- `*LANG?`, to read the active SCPI/TSP command set without changing it;
- `:OUTPut:STATe?` in SCPI mode, or `print(smu.source.output)` in TSP mode, to read
  the source-output state without changing it.

The JSON report retains the raw response bodies, parsed identity, resource,
backend/library description, dependency and Python versions, framing, byte bounds,
timeouts and timestamp. It explicitly records that no mutating command was sent.
An ON result is printed and returns exit status 3; the diagnostic never switches
the source off itself. It never sends `*RST`, `*LANG <value>`, a measurement
command, runtime source, or output-control command. Confirm OUTPUT is off at the
front panel before connecting and do not use a second instrument controller.

Automatic resource discovery is not required and may miss a link-local instrument
on a secondary network interface. Use a reviewed explicit resource. Do not probe
TCP port 5030: the manual defines it as the dead-socket termination service, whose
connection close terminates existing Ethernet sessions.

## Transport behavior

`VisaTransportConfig` requires explicit resource and backend strings. It also fixes
finite open/I/O timeouts, LF or CRLF framing, a bounded read chunk, hard response and
command byte limits, expected model and an optional required command language.

`PyVisaKeithley2460Transport`:

- lazy-loads the optional PyVISA dependency;
- owns one resource-manager/session pair and serializes each complete write/read
  transaction with one reentrant lock;
- reads at most `max_response_bytes + 1` so oversized replies are detected without
  unbounded accumulation;
- requires the configured terminator and strict ASCII, and rejects empty,
  unterminated, malformed or incompatible identity/language replies;
- closes and invalidates the session after any timeout, partial write, malformed
  reply or other I/O error so later calls cannot consume stale response bytes;
- performs no automatic retries; a failed or short mutating write raises
  `AmbiguousTransportError` and requires instrument-state reconciliation;
- keeps runtime upload explicitly disabled until the reviewed M4 runtime exists.

The transport's generic `write()` method is a later operational building block,
not a general commissioning API. The coin cell must not be used for exploratory
sourcing. A separate guarded notebook documents the narrow, reviewed 100 ohm
resistor smoke test; it does not constitute an operational device API.

## Initial target-unit evidence

On 5 October 2026 the diagnostic ran from macOS using Python 3.12.13, PyVISA
1.16.2 and pyvisa-py 0.8.1. Raw socket and VXI-11 both returned:

```text
KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a
SCPI
0
```

The final `0` is the SCPI-reported source output state OFF. The unit therefore
needs no communication-setting change for continued read-only SCPI commissioning.
A future TSP runtime may require a deliberate front-panel language change, but M4
must define and review that runtime first. The query does not establish electrical
isolation, configured OFF-mode loading, safety, timing, measurement, shutdown,
abort responsiveness, transfer fairness, or exclusive controller ownership.

## Preliminary resistor smoke test

With the coin cell removed and a nominal 100 ohm resistor connected to the front
terminals in a four-wire configuration, a guarded two-point procedure sourced
100 µA and 1 mA with a 0.2 V voltage limit. Source readback and sensed voltage
gave 102.78 ohm and 102.53 ohm respectively, with no voltage-limit trip. The
procedure switched output off after each point, returned the programmed current
to zero, and verified those conditions again in an independent session.

See `notebooks/keithley_2460_resistor_smoke.ipynb` and the
[raw JSON evidence](evidence/k2460-2026-10-05-resistor-smoke.json). Because the
resistor was not calibrated and no failure injection, transfer fairness, abort,
or OFF-mode behavior was exercised, this result does not pass G05 or any other
hardware gate.

The follow-up six-point bipolar sweep collected five readings at each of −1 mA,
−500 µA, −100 µA, +100 µA, +500 µA, and +1 mA. Its 30 paired source-readback and
four-wire-voltage samples produced a 102.502310 ohm slope, −33.020 µV intercept,
and R-squared 0.999999999914 without a voltage-limit trip. The complete samples
are in the [bipolar sweep evidence](evidence/k2460-2026-10-05-resistor-bipolar-sweep.json).
The guarded procedure is
`notebooks/keithley_2460_resistor_bipolar_sweep.ipynb`.
This remains a bounded commissioning test rather than G05 acceptance.

## Buffered acquisition and host-exception cleanup

The guarded `notebooks/keithley_2460_buffer_cleanup.ipynb` completed a 20-reading
user-buffer acquisition followed by a deliberate host-side exception. All five
fields per record were retrieved, buffer bounds matched 1–20, output was off
before retrieval, and the unique temporary buffer was deleted. Mean readback was
0.999998004 mA and mean voltage was 102.489834 mV. Relative timestamps gave an
82.5695 ms mean interval over 1.568821 s.

The exception path and final independent session both confirmed OFF and 0 A.
The two user-reviewed historical LAN errors were preserved and the error count
did not increase. See the
[complete JSON evidence](evidence/k2460-2026-10-05-buffer-cleanup.json).
This does not cover process termination, Ethernet loss, interrupted retrieval,
abort starvation, or instrument-local timeout, so G05 remains open.

## Offline verification scope

Mocked tests cover configuration bounds, exact parsing, wrong model/language,
connection failures, timeouts, partial writes, missing termination, non-ASCII and
oversized replies, whole-transaction serialization, session invalidation, no
mutation replay, SCPI/TSP output-state queries, ON/ambiguous output responses,
diagnostic evidence fields and disabled runtime upload. They do not emulate
firmware or count as hardware evidence.
