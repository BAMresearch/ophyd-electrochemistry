# ADR 0001 — Contract-first 2460 backend

Date: 2026-10-02. Status: accepted baseline; hardware mechanisms unverified.

## Context

Operando electrochemistry must run independently of detector frame timing.
The installation intends two QueueServers; the follower owns the 2460 and the
leader triggers through READY/BUSY/START/optional ABORT. Standalone immediate
start and programmatic abort/recovery are also required.

## Decision

Use classic ophyd Device/Flyer protocols, immutable instrument-independent
programs, an acquisition request for start mode, a pure 2460 compiler, bounded
PyVISA transport, and packaged versioned TSP/TriggerFlow. Prepare leaves output
OFF and unarmed. External kickoff succeeds on armed readiness before START.
Complete awaits finite acquisition outcome. Cleanup never resumes sourcing.

Preserve separate runs in the two-server deployment and correlate experiment
IDs. Treat measured/readback V/I, external abort preemption/latency, OFF behavior,
and clock mapping as explicit acceptance gates. The template contains no driver.

## Consequences

Pure validation/simulation can start immediately. Runtime support remains
conditional on firmware characterization. No unverified event/callback mechanism
or fixed emergency-stop latency enters the API as a capability claim. A future
ophyd-async migration needs a new ADR and compatibility tests, not mixed status
or event-loop semantics within this backend.
