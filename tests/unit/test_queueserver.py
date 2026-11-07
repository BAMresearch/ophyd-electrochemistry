"""QueueServer session, startup namespace and enqueue confirmation (no RE Manager)."""

import math
from pathlib import Path

import pytest
import yaml

pytest.importorskip("bluesky_queueserver_api")

from ophyd_electrochemistry.exceptions import (  # noqa: E402
    ElectrochemistryError,
    UnsupportedCapabilityError,
    ValidationError,
)
from ophyd_electrochemistry.sequences import SimulatedSequenceBackend  # noqa: E402
from ophyd_electrochemistry.sequences.queueserver import (  # noqa: E402
    Session,
    archive_main,
    build_namespace,
    enqueue,
    load_session,
    queue_items,
    sheet_digest,
)
from ophyd_electrochemistry.sequences.run_file import read_run_file  # noqa: E402
from ophyd_electrochemistry.sequences.run_sheet import make_template  # noqa: E402
from ophyd_electrochemistry.state import DeviceState  # noqa: E402

QSERVER = Path(__file__).parents[2] / "qserver"


@pytest.fixture
def session(tmp_path):
    example = load_session(QSERVER / "session-simulator.toml")
    return Session(**{**vars(example), "data_dir": tmp_path / "data"})


@pytest.fixture
def sheet(tmp_path):
    return make_template(tmp_path / "runs.xlsx")


def hardware(session):
    return Session(**{**vars(session), "backend": "k2460", "resource": "TCPIP0::x::5025::SOCKET"})


def test_example_sessions_parse():
    sim = load_session(QSERVER / "session-simulator.toml")
    assert sim.backend == "simulator" and not sim.hardware
    assert sim.data_dir == Path.home() / "k2460-data" / "qserver-sim"
    k2460 = load_session(QSERVER / "session-k2460.example.toml")
    assert k2460.hardware and k2460.terminals == "front" and k2460.serial == "04686198"


@pytest.mark.parametrize(
    "text,message",
    [
        ('backend = "simulator"\ndata_dir = "x"\n', r"\[limits\]"),
        (
            'backend = "simulator"\ndata_dir = "x"\nspeed = 2\n[limits]\n',
            "unknown session settings",
        ),
        ('backend = "k2460"\ndata_dir = "x"\n[limits]\n', "invalid session file"),
        (
            'backend = "k2460"\ndata_dir = "x"\n[limits]\nv_min_abs = 0\nv_max_abs = 2\n'
            "i_max_abs = 0.01\nmax_duration_s = 10\nmax_records = 100\n",
            "resource",
        ),
        ('data_dir = "x"\n[limits]\n', "invalid session file"),
    ],
)
def test_bad_session_files(tmp_path, text, message):
    path = tmp_path / "session.toml"
    path.write_text(text)
    with pytest.raises(ValidationError, match=message):
        load_session(path)


def test_run_from_sheet_runs_the_enqueued_row(session, sheet):
    namespace = build_namespace(session, backend=SimulatedSequenceBackend(speed=math.inf))
    plan = namespace["run_from_sheet"](str(sheet), "example-rest", sheet_digest(sheet))
    namespace["RE"](plan)
    saved = read_run_file(namespace["run_file_writer"].path)
    assert saved["run"]["outcome"] == "completed"
    assert saved["run_sheet_row"]["measurement_id"] == "example-rest"


def test_run_from_sheet_refuses_a_changed_sheet(session, sheet):
    namespace = build_namespace(session, backend=SimulatedSequenceBackend(speed=math.inf))
    digest = sheet_digest(sheet)
    sheet.write_bytes(sheet.read_bytes() + b"\0")
    with pytest.raises(ValidationError, match="changed since"):
        namespace["RE"](namespace["run_from_sheet"](str(sheet), "example-rest", digest))
    assert namespace["run_file_writer"].path is None


def test_hardware_session_plan_requires_confirmation(session, sheet):
    backend = SimulatedSequenceBackend(speed=math.inf)
    namespace = build_namespace(hardware(session), backend=backend)
    with pytest.raises(ValidationError, match="CONFIRM_MEASUREMENT_ID"):
        namespace["RE"](
            namespace["run_from_sheet"](str(sheet), "example-rest", sheet_digest(sheet))
        )
    plan = namespace["run_from_sheet"](
        str(sheet), "example-rest", sheet_digest(sheet), confirm_measurement_id="example-rest"
    )
    namespace["RE"](plan)
    assert read_run_file(namespace["run_file_writer"].path)["run"]["outcome"] == "completed"


def test_recover_plan_returns_an_aborted_backend_to_idle(session, sheet):
    from ophyd_electrochemistry.sequences.queueserver import load_request

    backend = SimulatedSequenceBackend(speed=1.0)
    namespace = build_namespace(session, backend=backend)
    backend.connect()
    backend.prepare(load_request(session, sheet, "example-rest", sheet_digest(sheet)))
    backend.kickoff()
    backend.abort("test")
    assert backend.inspect() == (DeviceState.ABORTED, False)
    namespace["RE"](namespace["recover_sequencer"]())
    assert backend.inspect() == (DeviceState.IDLE, False)


class SecuringBackend(SimulatedSequenceBackend):
    """Records the off-mode call the 2460 backend serves when the environment opens."""

    def __init__(self, error=None):
        super().__init__()
        self.secured = []
        self.error = error

    def secure_off_mode(self, limits):
        if self.error is not None:
            raise self.error
        self.secured.append(limits)


def test_opening_the_environment_secures_the_off_mode(session):
    backend = SecuringBackend()
    build_namespace(session, backend=backend)
    assert backend.secured == [session.limits]


def test_environment_does_not_open_without_a_confirmed_off_mode(session):
    with pytest.raises(ElectrochemistryError, match="not confirmed"):
        build_namespace(session, backend=SecuringBackend(ElectrochemistryError("not confirmed")))


class RetainingBackend(SimulatedSequenceBackend):
    """Records the archive call the 2460 backend would serve."""

    def __init__(self) -> None:
        super().__init__(speed=1.0)
        self.archived: list[Path] = []
        self.connected = False

    def connect(self) -> None:
        super().connect()
        self.connected = True

    def close(self) -> None:
        super().close()
        self.connected = False

    def archive_retained(self, destination: Path) -> Path:
        assert self.connected
        destination.write_bytes(b"")
        self.archived.append(destination)
        return destination


def test_archive_plan_saves_into_the_data_folder_and_disconnects(session):
    backend = RetainingBackend()
    namespace = build_namespace(session, backend=backend)
    namespace["RE"](namespace["archive_retained"]())
    [path] = backend.archived
    assert path.parent == session.data_dir and path.name.startswith("retained-")
    assert path.suffix == ".npz" and not backend.connected


def test_archive_plan_needs_a_backend_that_keeps_records(session):
    namespace = build_namespace(session, backend=SimulatedSequenceBackend(speed=1.0))
    with pytest.raises(Exception) as caught:
        namespace["RE"](namespace["archive_retained"]())
    assert isinstance(caught.value, UnsupportedCapabilityError) or isinstance(
        caught.value.__cause__, UnsupportedCapabilityError
    )


def test_archive_command_uses_the_session(tmp_path, session, monkeypatch, capsys):
    from ophyd_electrochemistry.sequences import queueserver

    backend = RetainingBackend()
    monkeypatch.setattr(queueserver, "make_backend", lambda _: backend)
    monkeypatch.setattr(queueserver, "load_session", lambda _: session)
    assert archive_main(["--session", str(tmp_path / "s.toml")]) == 0
    assert str(backend.archived[0]) in capsys.readouterr().out


def test_simulator_items_need_no_prompt(session, sheet):
    shown = []
    items = queue_items(session, sheet, ["example-rest"], prompt=pytest.fail, show=shown.append)
    assert items == [
        {
            "name": "run_from_sheet",
            "kwargs": {
                "run_sheet": str(sheet.resolve()),
                "measurement_id": "example-rest",
                "sheet_sha256": sheet_digest(sheet),
                "confirm_measurement_id": "",
            },
        }
    ]
    assert "measurement_id: example-rest" in shown[0]


def test_hardware_items_need_every_run_typed(session, sheet):
    answers = iter(["example-rest", "wrong"])
    with pytest.raises(ValidationError, match="not confirmed; nothing was enqueued"):
        queue_items(
            hardware(session),
            sheet,
            ["example-rest", "example-rest"],
            prompt=lambda _: next(answers),
            show=lambda _: None,
        )
    items = queue_items(
        hardware(session), sheet, ["example-rest"], prompt=lambda q: " example-rest ", show=print
    )
    assert items[0]["kwargs"]["confirm_measurement_id"] == "example-rest"


def test_unknown_ids_are_reported_before_any_prompt(session, sheet):
    with pytest.raises(ValidationError, match="Not in the sheet: nope"):
        queue_items(hardware(session), sheet, ["nope"], prompt=pytest.fail)


def test_enqueue_adds_plans_in_order(session, sheet):
    class Api:
        added = []

        def item_add(self, item):
            self.added.append(item)

    api = Api()
    items = queue_items(session, sheet, ["example-rest", "example-rest"], show=lambda _: None)
    enqueue(items, "tcp://unused", api=api)
    assert [(p.name, p.kwargs["measurement_id"]) for p in api.added] == [
        ("run_from_sheet", "example-rest")
    ] * 2


def test_permissions_allow_only_the_sequencer_plans():
    groups = yaml.safe_load((QSERVER / "startup" / "user_group_permissions.yaml").read_text())
    for group in groups["user_groups"].values():
        assert group["allowed_plans"] == [
            "run_from_sheet",
            "recover_sequencer",
            "archive_retained",
        ]
        assert group["allowed_functions"] == []


def session_file(tmp_path, **limits):
    values = {
        "v_min_abs": 0.0,
        "v_max_abs": 3.0,
        "i_max_abs": 0.01,
        "max_duration_s": 200000.0,
        "max_records": 1000000,
    }
    values.update(limits)
    path = tmp_path / "session.toml"
    path.write_text(
        f'backend = "simulator"\ndata_dir = "{tmp_path}"\n[limits]\n'
        + "".join(f"{k} = {v}\n" for k, v in values.items())
    )
    return path


def test_validate_compiles_every_row(tmp_path, sheet, capsys):
    from ophyd_electrochemistry.sequences.queueserver import validate_main

    assert validate_main([str(sheet), "--session", str(session_file(tmp_path))]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("37 of 37 run(s) valid")
    assert any(
        line.startswith("ok   example-pulse: bipolar_pulse") and "blocks" in line for line in out
    )


def test_validate_reports_rows_the_sequencer_cannot_run(tmp_path, sheet, capsys):
    from ophyd_electrochemistry.sequences.queueserver import validate_main

    path = session_file(tmp_path, i_max_abs=10.0)  # valid limits, beyond the 2460's 7 A
    assert validate_main([str(sheet), "example-rest", "nope", "--session", str(path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL example-rest: i_max_abs exceeds runtime maximum 7 A" in out
    assert "FAIL nope: not in the sheet" in out
    assert "0 of 2 run(s) valid" in out


def test_validate_reports_sheet_level_errors(tmp_path, sheet, capsys):
    from ophyd_electrochemistry.sequences.queueserver import validate_main

    path = session_file(tmp_path, i_max_abs=0.00005)  # template rows use 0.1 mA
    assert validate_main([str(sheet), "--session", str(path)]) == 1
    assert capsys.readouterr().out.startswith(f"INVALID {sheet}:")
