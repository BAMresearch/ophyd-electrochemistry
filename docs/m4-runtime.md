# M4 finite local runtime proof

M4 has an offline-reviewed implementation with successful target
compile/load/initialize, idle-status, and narrow immediate-hold state-path
evidence. The implementation is deliberately smaller than the eventual
acquisition backend: one bounded current hold, NORMAL output-off mode, immediate
or digital START, and programmatic abort. It does not collect measurements,
enforce measurement-derived voltage cutoffs, play arbitrary waveforms, or expose
an ophyd Device.

## Packaged runtime and host adapter

`src/ophyd_electrochemistry/keithley/k2460/tsp/runtime.tsp` is a packaged TSP
script with ABI `oe-k2460-m4-hold-v1`. The host calculates its SHA-256 digest and
uses a 96-bit digest prefix in the temporary script name. The full SHA-256 remains
the host-side identity. The PyVISA transport accepts only the exact packaged
source, ABI, and full digest; arbitrary TSP text is rejected.
It requires TSP command language and confirmed source output OFF before entering
`loadscript` mode. Every source line is sent once under the transaction lock.
An ambiguous or partial upload invalidates the session and is never retried.

The script is held in runtime memory and is not saved to nonvolatile storage.
Its top level installs definitions and returns without touching hardware. The
host verifies ABI/build, then calls the initializer separately; initialization
forces output OFF but does not reset the instrument or clear its event log. A
digest-named script already present in runtime memory is reused, then its
ABI/build and final output state are queried.

The readable packaged resource contains blank lines, but the hashed wire
artifact omits them. Live testing on firmware 1.7.16a showed that empty messages
inside `loadscript` produced a script that compiled but remained running when
invoked. The identical 301 nonempty lines returned normally. The digest covers
the exact blank-free representation sent to the instrument, and the transport
also paces each line by 10 ms.

The same firmware also remained running when a second script redefined the
runtime globals established by an earlier diagnostic script. A first install
therefore requires both the digest-named script and its globals to be absent. An
already-installed matching script is verified and initialized without rerunning
its top level. Conflicting globals are never overwritten automatically. A stuck
script requires a reboot, but a responsive idle/aborted runtime can be replaced
without reboot: force-safe, delete the owned volatile script, set its known
globals to `nil`, verify script/global absence plus OFF/0 A, then install the new
digest.

Firmware 1.7.16a also remained running when the 70-character name containing a
full SHA-256 was invoked, despite the documented 256-character limit. The same
script returned under a 29-character diagnostic name. Production names are
therefore 30 characters (`oe_m4_` plus 24 hexadecimal digest characters), while
the complete digest is still checked before upload.

The first guarded prepare attempt exposed another dialect difference: this TSP
runtime does not provide Lua's `math.huge`. No output was enabled and force-safe
confirmed OFF/0 A. The runtime now rejects non-numbers and NaN directly; every
accepted numeric argument then passes a bounded range check that also rejects
positive or negative infinity.

`CurrentHoldProof` applies hard backstops independently of the caller's safety
configuration:

- absolute current no greater than 10 mA;
- source range no greater than 10 mA and large enough for the setpoint;
- voltage limit from 0.01 V through 2 V;
- finite hold and external-start timeout from 1 ms through 60 s;
- external event polling from 1 ms through 100 ms;
- unique READY, BUSY, and START line numbers in 1 through 6;
- NORMAL output-off mode only; external ABORT remains rejected.

The host additionally checks directional current, voltage-envelope, and
worst-case power against the explicit `SafetyConfig`. These bounds make the
runtime suitable only for controlled commissioning. They are not claimed 2460
capability limits or safe battery defaults.

## Local TriggerFlow behavior

Immediate mode locally asserts BUSY, turns output on, executes an abortable
constant-delay block, turns output off, deasserts BUSY/READY, and emits a terminal
notify event. Once initiated, normal finite shutdown no longer depends on the
host or Ethernet connection.

External mode locally asserts READY and starts trigger timer 1. It checks timeout
before START on each bounded polling loop. START deasserts READY, asserts BUSY,
and enters the same finite hold. Timeout reaches an explicit source-OFF block and
deasserts both flags without sourcing. A programmatic abort separately aborts
the trigger model, disables the timer, sets output OFF, deasserts flags, and
programs 0 A.

The host's status query reports the runtime ABI/state, both trigger-model state
values, last block, output state, commanded READY/BUSY levels, START input, and
detector overruns. Commanded output levels are tracked in software instead of
read back from output-configured pins because firmware 1.7.16a posts warning
1808 for such reads. Polling observes behavior; it does not pace the hold or
provide its timeout.

## Evidence boundary

The implementation follows the Model 2460 Reference Manual Rev. C, which applies
to firmware 1.7.0 and later. The reviewed commands are `loadscript`/`endscript`,
digital-line mode/state and event detection, trigger timer 1, and TriggerFlow
digital-I/O, notify, branch, delay, source-output, and abort operations. The
target unit runs firmware 1.7.16a. Compilation, initialization, and the
immediate state path have run successfully; external START, abort/failure paths,
electrical values, and independent timing remain untested.

The external START construction necessarily clears the detector before
initiating a model whose first block asserts READY. Bench traces must determine
whether a pre-READY edge can survive that boundary or a just-post-READY edge can
be lost. Until repeated boundary tests prove the required ordering, external
START is an experiment and G01 remains NOT RUN. External ABORT is absent rather
than inferred from a sequential event branch.

The immediate local path reached PREPARED/output-off, RUNNING/BUSY/output-on,
COMPLETE/output-off, and recovery to IDLE/output-off/0 A on the nominal 100 ohm
front-terminal four-wire resistor at +1 mA, 0.2 V limit, and 250 ms. This is
state-path evidence, not proof of electrical current/voltage, duration accuracy,
OFF impedance, abort latency, process-kill behavior, or network-loss behavior.
G01, G02, and G07 remain NOT RUN until the required traces and failure tests
exist.

## Command-language transition and live installation

The instrument was changed from SCPI to TSP using the front panel and rebooted;
the active target address at installation was `169.254.113.151`. The reference
manual states that changing the command set requires a reboot and that SCPI and
TSP cannot be combined. The commissioning workflow therefore never sends
`*LANG TSP` remotely.

After reboot, the query-only diagnostic returned `TSP` and `smu.OFF`. The
blank-free short-name runtime then compiled, initialized, and reported `idle`.
After a separate physical confirmation, the guarded immediate hold completed as
described above. `notebooks/keithley_2460_m4_runtime.ipynb` reproduces both
boundaries with independent opt-in flags and does not clear the retained event
log.

Primary source: [Tektronix Model 2460 Reference Manual Rev. C](https://download.tek.com/manual/2460-901-01C_Sept_2019_Ref.pdf),
sections 2, 8, 13, 14, and 15.
