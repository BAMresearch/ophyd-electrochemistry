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
