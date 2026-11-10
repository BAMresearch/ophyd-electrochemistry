# Shutdown, abort, and recovery

## Protection layers

The backend must combine request validation, instrument compliance, local
termination logic, and validated physical protection appropriate to the cell.
A charge upper cutoff and discharge lower cutoff are protocol termination
criteria; they are not equivalent to a single magnitude compliance setting.
Positive current is defined as entering the positive DUT terminal (charge under
the agreed two-terminal wiring). Verify sign against a dummy load/source.

Cell-specific SafetyConfig is mandatory; there are no universally safe battery
defaults. Validate the instrument's range/power envelope and polarity as well
as nominal magnitude. Source setpoint must not be substituted for measured V/I.

## What OFF means

The chosen source OFF mode must be assessed for loading, discharge current,
and leakage. OFF does not necessarily mean physical disconnection or zero DUT
voltage, especially for a battery. Recovery checks the instrument's source
state and the measured electrical condition appropriate to the setup; it must
not incorrectly require a connected battery to have zero terminal voltage.

## Abort sequence and evidence

Inhibit future source actions, terminate the trigger model, set output OFF,
confirm safe state, deassert flags, and latch reason/outcome. The precise TSP
ordering and scheduling must be proven so no pending action re-enables output.
Do not assume `trigger.model.abort()` alone switches output OFF or runs a final
cleanup block. Preserve any complete records and mark partial data as aborted.

If confirmation is unavailable, report ERROR and UNKNOWN output; fail all
pending statuses and preserve storage. A disconnect cannot be made safe merely
by setting a Python enum. A local finite duration/start timeout and validated
manual protection path cover cases where host cleanup is unavailable.

External ABORT remains a capability gate. An ordinary sequential branch may
only inspect an event when that block is reached. Investigate a supported local
supervisor, or explicitly bounded short blocks with a proven worst-case latency.
Neither event detection nor an event-branch API establishes preemption. Do not
label this input an emergency stop or claim a shutdown latency before measuring.
The dedicated interlock's suitability must also be checked at the actual low
voltage/range; its existence is not proof it protects this battery experiment.

## Cleanup and retained evidence

RunEngine finalizers should collect preserved partial data on interruption when
possible and invoke bounded shutdown even when collection fails. Device
`stop()`/`unstage()` provide a second cleanup path, not a protection against
power loss or a killed worker. Pause aborts the acquisition; resume never replays
it. Outcome, abort reason, and records survive recovery until explicitly
archived/discarded. Repeated abort and recovery must not erase evidence.

## Waveform-specific validation

PRBS/arbitrary/multisine share these protection layers; generator validity is
not battery safety. Check both PRBS levels, every arbitrary sample/transition,
the multisine sum and conservative envelope, repeat boundaries, total duration,
commanded charge and applicable sourcing/sinking/power limits before output.
Positive/negative symmetric PRBS levels need not give zero finite-sequence charge.
Commanded charge does not replace measured current integration or cell monitoring.

Compliance and voltage/current cutoffs remain distinct. A waveform request must
declare applicable cell cutoffs and their termination policy; the backend rejects
it if that policy cannot be enforced locally within its validated latency.
Protection or deadline faults preserve partial data and mark the waveform
incomplete. A locally generated list must not monopolize control/abort handling.
No clipping, waveform normalization or source-OFF substitution is an implicit
safety fallback. See [waveform semantics](waveforms.md) and gate G08.

## Timed resistor commissioning candidate

The new timed-hold backend requires explicit `commissioning_resistor=True` and
keeps `hardware_ready=False`. Its programmed voltage-cutoff and half-slot deadline
checks act between sequential measurements; neither preemption nor a worst-case
latency is established. The `LIMIT_OUTSIDE` command's behavior at the exact cutoff
boundaries must also be verified before claiming inclusive cutoff semantics.
The broader cell-protection contract remains unaccepted for this candidate.
Its first energized timed run stopped with `cadence_overrun` after one reading;
the event-consumption design needs review and no timing gate is promoted.
A known terminal fault with output ON permits one explicit run-cleanup shutdown
attempt; failed/uncertain delivery remains ERROR/UNKNOWN until stopped/OFF
inspection and explicit recovery. It is never retried automatically.

For the scoped 100 ohm / 1 W resistor case, +5 mA predicts 0.5 V and 2.5 mW;
1 V compliance gives a 5 mW request power envelope. These bounds do not authorize
unknown DUTs, battery work, or use of the full resistor rating. Apply the
[commissioning procedure](timed-hold-commissioning.md) before any installation or
energized run.
