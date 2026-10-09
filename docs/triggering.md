# Start and I/O contract

## State table

Values below are **logical assertion states**. Physical polarity and buffering
must be commissioned. No claim of atomic simultaneous pin transitions is made.

| State | READY | BUSY | SMU output | Meaning |
|---|---:|---:|---|---|
| IDLE / PREPARED | 0 | 0 | OFF confirmed | Not accepting START |
| WAITING_START | 1 | 0 | OFF confirmed | Armed external acquisition |
| RUNNING | 0 | 1 | Program-controlled | May be OFF during programmed rest |
| ABORTING | 0 | May remain 1 | Shutdown in progress | Terminal outcome not confirmed |
| COMPLETE / ABORTED | 0 | 0 | OFF confirmed | Outcome latched |
| ERROR | Not trusted | Not trusted | UNKNOWN unless verified | No readiness claim |

READY means a usable hardware START wait is already installed. Raise READY
only after stale events have been cleared and the wait is live. A START just
after READY must not be cleared on entry to the wait. Host polling alone is
insufficient to prove this ordering; test repeated race-boundary triggers.

## I/O allocation

Allowed lifecycle: IDLE -> PREPARING -> PREPARED -> ARMING -> WAITING_START
(external) or RUNNING (immediate); WAITING_START -> RUNNING on START;
RUNNING -> COMPLETE on normal termination. Active states may enter ABORTING,
then ABORTED only on confirmed shutdown. Failed confirmation enters ERROR.
ABORTED/ERROR -> RECOVERING -> IDLE requires explicit recovery. COMPLETE may
prepare again only after retained-data disposition. Reject prepare/kickoff while
active, and reject kickoff without a successfully prepared request. Transitional
states must never assert READY prematurely.

| Logical function | Default digital line | Direction | Initial policy |
|---|---:|---|---|
| READY | 1 | Output level | Asserted only in WAITING_START |
| BUSY | 2 | Output level | Execution, not source-output status |
| START | 3 | Trigger input | Rising edge, low before arming |
| ABORT | 4 | Optional trigger input | Rising edge, plus asserted-level checks |
| Reserved | 5, 6 | Unassigned | No side effects |

These are line numbers, not a verified DB-9 wiring/pin diagram. Confirm pinout
against the unit's manual before wiring. Orange rear DUT terminals are separate.
Enable external abort explicitly after bench validation. Programmatic abort
remains available in both start modes.

Ignore START when not waiting; never queue it for the next acquisition. Reject
arming when START/ABORT is already asserted. During arming, prevent a pre-READY
event from causing output. ABORT takes precedence over coincident START; if the
hardware cannot provide that guarantee, document the measured behavior and
reject that configuration. Recover only after ABORT has deasserted.

The default edge input is not a fail-safe manual emergency stop: a latched
button, broken cable, and dropped pulse need a separate electrical design.

Commissioning may explicitly select native `either`-edge START. In that mode a
static high or low level is inert and the first subsequent transition is the
single-shot START; later transitions cannot restart a model that has already
left WAITING_START. Directional rising/falling modes retain their opposite-level
pre-arm check. `either` is useful for manual jumper tests, but a production
integration should normally choose and document one polarity.

## Modes and bounded waits

`prepare()` always reaches PREPARED with output OFF. In immediate mode,
`kickoff()` begins execution without a START input. In external mode it enters
WAITING_START and returns a successful kickoff status while sourcing remains
OFF. A configured finite `external_start_timeout_s` must terminate an unanswered
wait locally; validate that path independently of Ethernet. Program duration,
shutdown, and command timeouts must also be finite.

`complete()` can be issued immediately after kickoff. No graceful-stop protocol
is included in the initial four-line contract. `stop()` and ABORT interrupt and
latch an unsuccessful acquisition.

## Electrical commissioning

The reviewed datasheet gives LOW <=0.7 V and HIGH >=3.7 V, with permitted input
range -0.25 to +5.25 V. A 3.3 V source is not a guaranteed valid HIGH. Outputs
are asymmetric: +2 mA at >2.7 V versus sinking 50 mA at 0.7 V. Load, pull-up,
logic polarity, isolation, and timing source requirements need verification.
These values do not establish direct compatibility with a 24 V or LVDS system.
Source: [Tektronix 2460 datasheet](https://www.tek.com/en/datasheet/2460-source-measure-unit),
Digital I/O Interface.

On 9 October 2026, an Agilent 33120A produced a user-verified 0.01–4.56 V,
1 Hz square wave at the cable end and drove START line 3 through a 1 kohm series
resistor, with its shield connected to pin 9. The 33120A was actually in its
50 ohm display mode, so the oscilloscope readings—not its displayed amplitude—
establish these levels. In ordinary digital-input mode, the 2460 recognized
16 alternating HIGH/LOW transitions over eight seconds with nearly equal state
counts. The mean host-observed half-period was 0.49994 s, output remained OFF/0 A,
and the steady-state monitor added no warning or error. Changing line 3 from
trigger mode to digital mode added the expected trigger-setting-reset warning.
This qualifies the generator's logical levels, not trigger capture or electrical
timing. See the [generator-input evidence](evidence/k2460-2026-10-09-generator-digital-input.json).

A subsequent rising-only trigger test held the same generator LOW before arm,
then raised its front-panel DC setting after READY. The edge started and
completed one five-record acquisition, but `start_overrun` became true and one
warning was added. It was discovered afterward that the 33120A was still in
50 ohm display mode: it displayed 4.5 V for approximately five seconds before
the setting was halved, implying a nominal unloaded target near 9 V. The actual
line-3 voltage is unknown, but the possible exposure exceeds the Keithley's
+5.25 V input limit despite the 1 kohm series resistor. The generator was
physically disconnected. The run is invalid as trigger-acceptance evidence and
line 3 required a conservative functional retest before further triggering. See the
[rising-edge evidence](evidence/k2460-2026-10-09-m5-rising-generator-v12-evidence.json).

The follow-up used a scope-verified 0.10 Hz square wave of approximately
0.01–4.60 V through the same 1 kohm path. With the SMU output off, line 3
recognized LOW, HIGH and LOW over successive half-cycles. The runtime armed
during the latter LOW interval and the next rising edge completed exactly one
five-record acquisition. `start_overrun` remained false through RUNNING,
COMPLETE and recovery; output returned to OFF/0 A. Disconnecting the generator
afterward let the input float HIGH and set the otherwise historical overrun flag,
but this occurred after the clean terminal snapshot. This is a functional
post-incident check and a clean single-run observation, not proof against latent
damage, READY-boundary races, stale/repeated START, or electrical timing. See the
[clean rising-edge evidence](evidence/k2460-2026-10-09-m5-rising-square-clean-v12-evidence.json)
and [archive](evidence/k2460-2026-10-09-m5-rising-square-clean-v12-archive.json).
