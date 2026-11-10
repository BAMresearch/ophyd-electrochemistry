# Testing

```sh
uv run --locked pytest                 # everything except hardware (≈ 1,300 tests, < 1 min)
uv run --locked pytest tests/unit      # fast, no processes started
uv run --locked ruff check . && uv run --locked ruff format --check .
uv run --locked mypy src scripts
```

Run all four before reporting work as done. No test touches an instrument.
Tests marked `queueserver` start `redis-server` and an RE Manager and are
skipped if redis is not installed; tests marked `hardware` are deselected by
default and none exist yet (bench work uses the runners in `bench/`).

## Layout

| Directory | What it tests | How |
|---|---|---|
| `tests/unit/` | Each module of the beamtime path in isolation, plus the bench runners' offline parts | Pure functions, fakes, the simulator; a real RunEngine where a Flyer is involved |
| `tests/contract/` | The pieces working together as a user drives them | A real RunEngine; a real RE Manager with redis (`test_queueserver_manager.py`); the per-run and viewer notebooks executed cell by cell |
| `tests/simulator/` | The archived M2 virtual-clock simulator | Independent reference traces |
| `tests/hardware/` | Placeholder for opt-in bench tests | – |

## What covers what (beamtime path)

| Area | Main tests |
|---|---|
| Step models, validation, protocol builders, Renard series | `test_sequences.py` |
| Run sheets: loading, aggregated errors, template | `test_run_sheet.py` |
| Synthetic cell and its virtual timing | `test_sequence_simulator.py` |
| Compiler: golden block tables, block budget, reachability, call length, delays; replay against the simulator, including noisy and cutoff cases | `test_k2460_sequencer.py` |
| Packaged TSP: Lua 5.0 lint, build digest, execution against the mocked 2460 (HIGH-Z, refusals, OFF confirmation) | `test_k2460_sequencer_runtime.py` |
| Hardware backend against the packaged TSP under Lua: upload, status, reads, clock mapping, abort, recovery, retained records, stale builds | `test_k2460_sequence_backend.py` |
| Flyer lifecycle under a real RunEngine: streaming, external start, timeouts, abort and drain | `test_sequence_flyer.py` |
| HDF5 run file (SWMR, crashes, repair) and callbacks | `test_run_file.py`, `test_sequence_callbacks.py` |
| Viewer helpers | `test_viewer.py` |
| Session files, namespace, plans, enqueue, validate | `test_queueserver.py` |
| Real RE Manager: queue of rows, cutoff continues the queue, abort, recover, archive | `tests/contract/test_queueserver_manager.py` |
| Notebooks | `test_notebook_helpers.py`, `tests/contract/test_run_notebook.py`, `test_view_notebook.py` |
| Bench runners compile their whole matrix offline | `test_bench_b1.py`, `test_bench_b2.py` |

The remaining unit and contract tests (`test_compiler.py`, `test_k2460_backend.py`,
`test_tsp_runtime.py`, `test_k2460_device.py`, …) cover the archived design and
are kept passing while that code remains in the package.

## Conventions

- **Independent references.** Expected values come from an independent path,
  not from the code under test: the replay is checked against the simulator,
  the compiler against golden block tables written out by hand, the backend
  against the real TSP under Lua.
- **Bench facts in fakes.** When the bench shows instrument behaviour (a
  stale ON reading, per-function off mode, the 63-block limit), the Lua mock and
  a test encode it, citing the bench date.
- **Runtime changes.** Any change to `tsp/sequencer.tsp` bumps
  `SEQUENCER_BUILD`; a test pins the build to the source's SHA-256 so a stale
  build cannot reach the instrument unnoticed.
- **Real processes.** QueueServer tests run their own redis with a temporary
  `--dir`, so a `dump.rdb` in the repository is never loaded.

## Bench runs

Hardware checks are scripted runners in `bench/` (for example
`bench/b1_sequencer.py`) driven by a written test matrix (`bench/plan-*.md`).
Each writes one evidence file to `docs/evidence/` and a short entry in
[the validation record](../validation.md). Their offline parts (matrix
compilation, refusal of an unconfirmed load) are unit tested.
