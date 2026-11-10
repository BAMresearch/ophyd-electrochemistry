"""Sequencer runtime: packaging, Lua 5.0 lint, and execution against a mocked 2460.

The mock reproduces behaviour measured on the bench (3 Nov 2026): off mode is
stored per source function, a source-function change forces the output off,
and smu.source.output can stay ON after an OFF command until such a change.
"""

import re

import pytest

lupa = pytest.importorskip("lupa.lua51")

from ophyd_electrochemistry.keithley.k2460.sequencer_runtime import (  # noqa: E402
    SEQUENCER_ABI,
    SEQUENCER_BUILD,
    lint_lua50,
    sequencer_packaged_runtime,
)

MOCK = r"""
outputs = {}
print = function(...)
    local parts = {}
    for i = 1, select('#', ...) do parts[i] = tostring(select(i, ...)) end
    table.insert(outputs, table.concat(parts, "\t"))
end
log = {}
local function rec(...) table.insert(log, {...}) end

smu = {
    ON = "smu.ON", OFF = "smu.OFF",
    FUNC_DC_CURRENT = "smu.FUNC_DC_CURRENT", FUNC_DC_VOLTAGE = "smu.FUNC_DC_VOLTAGE",
    OFFMODE_HIGHZ = "smu.OFFMODE_HIGHZ", OFFMODE_NORMAL = "smu.OFFMODE_NORMAL",
    SENSE_2WIRE = "smu.SENSE_2WIRE", SENSE_4WIRE = "smu.SENSE_4WIRE",
    TERMINALS_FRONT = "smu.TERMINALS_FRONT", TERMINALS_REAR = "smu.TERMINALS_REAR",
    terminals = "smu.TERMINALS_FRONT",
}
src = { func = "smu.FUNC_DC_VOLTAGE", output = "smu.OFF", level = 0 }
offmodes = { ["smu.FUNC_DC_CURRENT"] = "smu.OFFMODE_NORMAL",
             ["smu.FUNC_DC_VOLTAGE"] = "smu.OFFMODE_NORMAL" }
stale_on = false
meas = { func = "smu.FUNC_DC_VOLTAGE" }
src_store, meas_store = {}, {}
smu.source = setmetatable({ vlimit = { level = 0 }, ilimit = { level = 0 }, configlist = {
    create = function(name) rec("src_create", name) end,
    delete = function(name) rec("src_delete", name) end,
    store = function(name)
        table.insert(src_store, { func = src.func, meas = meas.func, level = src.level,
            readback = src.readback, range = src.range,
            vlimit = smu.source.vlimit.level, ilimit = smu.source.ilimit.level })
    end,
} }, {
    __index = function(t, k)
        if k == "offmode" then return offmodes[src.func] end
        return src[k]
    end,
    __newindex = function(t, k, v)
        if k == "offmode" then offmodes[src.func] = v
        elseif k == "func" then
            if v ~= src.func then src.output = "smu.OFF"; stale_on = false end
            src.func = v
        elseif k == "output" then
            if not (v == "smu.OFF" and stale_on) then src.output = v end
        else src[k] = v end
    end,
})
smu.measure = setmetatable({ autozero = {}, configlist = {
    create = function(name) rec("meas_create", name) end,
    delete = function(name) rec("meas_delete", name) end,
    store = function(name)
        table.insert(meas_store, { func = meas.func, src = src.func, range = meas.range,
            nplc = meas.nplc, sense = meas.sense, autozero = smu.measure.autozero.enable })
    end,
} }, { __index = meas, __newindex = meas })

model_state = "trigger.STATE_EMPTY"
blocks = {}
trigger = {
    STATE_RUNNING = "trigger.STATE_RUNNING", STATE_WAITING = "trigger.STATE_WAITING",
    STATE_IDLE = "trigger.STATE_IDLE", STATE_EMPTY = "trigger.STATE_EMPTY",
    BLOCK_CONFIG_RECALL = "RECALL", BLOCK_SOURCE_OUTPUT = "OUTPUT",
    BLOCK_DIGITAL_IO = "DIGIO", BLOCK_DELAY_CONSTANT = "DELAY",
    BLOCK_MEASURE_DIGITIZE = "MEASURE", BLOCK_BRANCH_LIMIT_CONSTANT = "LIMIT",
    BLOCK_BRANCH_COUNTER = "COUNTER", BLOCK_RESET_BRANCH_COUNT = "RESET",
    BLOCK_WAIT = "WAIT", BLOCK_LOG_EVENT = "LOG", BLOCK_BRANCH_ALWAYS = "ALWAYS",
    LIMIT_OUTSIDE = "OUTSIDE", LIMIT_INSIDE = "INSIDE", CLEAR_ENTER = "CLEAR_ENTER",
    EDGE_RISING = "RISING", EDGE_FALLING = "FALLING",
    LOG_INFO1 = "INFO1", LOG_INFO2 = "INFO2", LOG_INFO3 = "INFO3", LOG_INFO4 = "INFO4",
    EVENT_DIGIO1 = "DIGIO1", EVENT_DIGIO2 = "DIGIO2", EVENT_DIGIO3 = "DIGIO3",
    EVENT_DIGIO4 = "DIGIO4", EVENT_DIGIO5 = "DIGIO5", EVENT_DIGIO6 = "DIGIO6",
    digin = {},
    model = {
        state = function() return model_state, model_state, 0 end,
        load = function(name) rec("load", name); blocks = {} end,
        setblock = function(i, ...) blocks[i] = {...} end,
        initiate = function() rec("initiate"); model_state = "trigger.STATE_RUNNING" end,
        abort = function() rec("abort"); model_state = "trigger.STATE_IDLE" end,
    },
}
for i = 1, 6 do
    trigger.digin[i] = { clear = function() rec("digin_clear", i) end }
end
digio = { MODE_DIGITAL_OUT = "OUT", MODE_TRIGGER_IN = "TRIGIN",
          STATE_LOW = "LOW", STATE_HIGH = "HIGH", line = {} }
for i = 1, 6 do digio.line[i] = {} end
buffers = {}
buffer = {
    STYLE_STANDARD = "STD",
    make = function(capacity, style)
        local b = { n = 0, capacity = capacity, seconds = "S", fractionalseconds = "F",
            readings = "R", sourcevalues = "V", statuses = "ST", sourcestatuses = "SS" }
        table.insert(buffers, b)
        return b
    end,
    delete = function(b) rec("buffer_delete"); b.deleted = true end,
}
eventlog = { SEV_INFO = "INFO", post = function(m, s) rec("post", m, s) end }
format = { asciiprecision = 0 }
printbuffer = function(...) rec("printbuffer", ...) end
"""


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(MOCK)
    runtime.execute(sequencer_packaged_runtime().source)
    runtime.globals().oe_seq_initialize()
    return runtime


def out(lua) -> list[str]:
    return list(lua.globals().outputs.values())


def configure(lua, **overrides):
    # Lines decided 3 Nov: outputs READY 1, BUSY 2, MARKER 4; START input 6.
    args = dict(
        v_min=0.0,
        v_max=2.5,
        i_max=0.005,
        vlimit_max=2.6,
        ready=1,
        busy=2,
        marker=4,
        start=6,
        four_wire=1,
        front=1,
        edge=1,
    )
    args.update(overrides)
    lua.globals().oe_seq_configure(*args.values())


def build(lua, *, n_src=1, n_meas=1, n_blocks=4, capacity=10):
    g = lua.globals()
    configure(lua)
    g.oe_seq_begin(n_src, n_meas, n_blocks, capacity)
    return g


def minimal_program(lua):
    """Recall, output on, measure, output off: the smallest committable program."""
    g = build(lua)
    g.oe_seq_source(1, 1, 0.001, 0.01, 2.6)
    g.oe_seq_measure(1, 1, 7, 1)
    g.oe_seq_block(1, 1, 1, 1, 0, 0)
    g.oe_seq_block(2, 2, 1, 0, 0, 0)
    g.oe_seq_block(3, 5, 1, 0, 0, 0)
    g.oe_seq_block(4, 2, 0, 0, 0, 0)
    g.oe_seq_commit()
    return g


# Packaging and lint


# The build string is what the backend checks on an instrument that already
# holds a runtime, so every change to sequencer.tsp needs a new build.
BUILD_DIGESTS = {"seq-v2": "2f7010bc22895e881cac03355bbe1918745ecc835a488107d65a3112e393d6ae"}


def test_runtime_changes_come_with_a_new_build():
    artifact = sequencer_packaged_runtime()
    assert BUILD_DIGESTS.get(artifact.build) == artifact.sha256, (
        "sequencer.tsp changed: bump SEQUENCER_BUILD and oe_seq_runtime_build, "
        "then record the new digest here"
    )


def test_artifact_identity_and_wire_form():
    artifact = sequencer_packaged_runtime()
    assert (artifact.abi, artifact.build) == (SEQUENCER_ABI, SEQUENCER_BUILD)
    assert artifact.script_name == f"oe_seq_{artifact.sha256[:24]}"
    assert artifact.initializer == "oe_seq_initialize"
    assert artifact.source.endswith("\n") and "\n\n" not in artifact.source
    assert artifact.source.isascii()
    assert lint_lua50(artifact.source) == []


@pytest.mark.parametrize(
    "line,problem",
    [
        ("local n = #items", "'#'"),
        ("local r = a % b", "'%'"),
        ("local q = a // b", "integer division"),
        ("goto done", "goto"),
        ("x = 1 " + "-" * 0 + "y" * 900, "characters"),
        ("x = 'é'", "non-ASCII"),
    ],
)
def test_lint_rejects_lua51_syntax_and_long_lines(line, problem):
    assert any(problem in message for message in lint_lua50(line))


def test_lint_ignores_comments_and_strings():
    assert lint_lua50("-- no '#' or '%' here\nprint(\"100% #1\")  -- fine") == []


def test_runtime_defines_the_documented_api(lua):
    for name in (
        "configure",
        "begin",
        "source",
        "measure",
        "block",
        "commit",
        "arm",
        "status",
        "read",
        "ack",
        "discard",
        "abort",
        "off",
        "ping",
        "initialize",
    ):
        assert lua.eval(f"type(oe_seq_{name})") == "function"


def test_block_codes_cover_the_brief_07_table():
    codes = {int(c) for c in re.findall(r"code == (\d+)", sequencer_packaged_runtime().source)}
    assert codes == set(range(1, 12))  # 12 is the final else branch (ALWAYS)


# Configuration


def test_configure_sets_highz_for_both_functions_and_lines(lua):
    configure(lua)
    g = lua.globals()
    assert dict(g.offmodes.items()) == {
        "smu.FUNC_DC_CURRENT": "smu.OFFMODE_HIGHZ",
        "smu.FUNC_DC_VOLTAGE": "smu.OFFMODE_HIGHZ",
    }
    assert all(g.digio.line[line].mode == "OUT" for line in (1, 2, 4))
    assert g.digio.line[3].mode is None and g.digio.line[5].mode is None
    assert g.digio.line[6].mode == "TRIGIN" and g.trigger.digin[6].edge == "RISING"
    assert g.format.asciiprecision == 15
    assert g.src.func == "smu.FUNC_DC_CURRENT" and g.src.level == 0
    assert out(lua)[-1] == "OK"


@pytest.mark.parametrize(
    "overrides",
    [
        dict(v_min=2.5, v_max=0),
        dict(i_max=0),
        dict(i_max=8),
        dict(marker=2),
        dict(ready=6),
        dict(start=7),
        dict(four_wire=2),
        dict(edge=3),
        dict(vlimit_max=0),
    ],
)
def test_configure_rejects_bad_settings(lua, overrides):
    with pytest.raises(lupa.LuaError):
        configure(lua, **overrides)


def test_configure_refuses_with_output_on_or_active_model(lua):
    lua.globals().src.output = "smu.ON"
    with pytest.raises(lupa.LuaError, match="output"):
        configure(lua)
    lua.globals().src.output = "smu.OFF"
    lua.globals().model_state = "trigger.STATE_WAITING"
    with pytest.raises(lupa.LuaError, match="active"):
        configure(lua)


# Entries


def test_entries_are_stored_with_differing_source_and_measure_functions(lua):
    g = build(lua, n_src=2, n_meas=2)
    g.oe_seq_source(1, 1, -0.004, 0.01, 2.6)
    g.oe_seq_source(2, 2, 1.2, 2, 0.005)
    g.oe_seq_measure(1, 1, 7, 0.1)
    g.oe_seq_measure(2, 2, 0.01, 1)
    current, voltage = g.src_store[1], g.src_store[2]
    assert (current.func, current.meas, current.vlimit, current.readback) == (
        "smu.FUNC_DC_CURRENT",
        "smu.FUNC_DC_VOLTAGE",
        2.6,
        "smu.ON",
    )
    assert (voltage.func, voltage.meas, voltage.ilimit) == (
        "smu.FUNC_DC_VOLTAGE",
        "smu.FUNC_DC_CURRENT",
        0.005,
    )
    v_meas, i_meas = g.meas_store[1], g.meas_store[2]
    assert (v_meas.func, v_meas.src, v_meas.nplc, v_meas.sense, v_meas.autozero) == (
        "smu.FUNC_DC_VOLTAGE",
        "smu.FUNC_DC_CURRENT",
        0.1,
        "smu.SENSE_4WIRE",
        "smu.OFF",
    )
    assert (i_meas.func, i_meas.src) == ("smu.FUNC_DC_CURRENT", "smu.FUNC_DC_VOLTAGE")


@pytest.mark.parametrize(
    "args,message",
    [
        ((1, 1, 0.006, 0.01, 2.6), "i_max_abs"),
        ((1, 1, 0.001, 0.01, 2.7), "voltage limit"),
        ((1, 1, 0.001, 0.0001, 2.6), "range"),
        ((1, 2, 2.6, 7, 0.005), "absolute window"),
        ((1, 2, 1.0, 2, 0.006), "current limit"),
        ((2, 1, 0.001, 0.01, 2.6), "source entry"),
    ],
)
def test_source_entries_are_validated_against_session_limits(lua, args, message):
    g = build(lua)
    with pytest.raises(lupa.LuaError, match=message):
        g.oe_seq_source(*args)


def test_measure_entry_nplc_bounds(lua):
    g = build(lua)
    with pytest.raises(lupa.LuaError, match="nplc"):
        g.oe_seq_measure(1, 1, 7, 0.001)


# Blocks


def test_each_block_code_builds_the_documented_trigger_block(lua):
    g = build(lua, n_src=2, n_meas=2, n_blocks=12, capacity=100)
    for i in (1, 2):
        g.oe_seq_source(i, 1, 0.001 * i, 0.01, 2.6)
        g.oe_seq_measure(i, 1, 7, 1)
    calls = [
        (1, 2, 1, 0, 0),  # RECALL
        (2, 1, 0, 0, 0),  # OUTPUT on
        (3, 3, 3, 0, 0),  # DIGIO both lines high
        (4, 0.0012, 0, 0, 0),  # DELAY
        (5, 1, 0, 0, 0),  # MEASURE
        (6, 0.0, 2.5, 12, 5),  # LIMIT_OUTSIDE
        (7, -0.001, 0.001, 12, 5),  # LIMIT_INSIDE
        (8, 16, 4, 0, 0),  # COUNTER
        (9, 8, 0, 0, 0),  # RESET_COUNTER
        (10, 0, 0, 0, 0),  # WAIT_START
        (11, 1, 0, 0, 0),  # LOG T0
        (12, 2, 0, 0, 0),  # ALWAYS -> 2
    ]
    for i, (code, a, b, c, d) in enumerate(calls, start=1):
        g.oe_seq_block(i, code, a, b, c, d)
    blocks = {i: list(b.values()) for i, b in g.blocks.items()}
    assert blocks[1] == ["RECALL", "OE_SRC", 2, "OE_MEAS", 1]
    assert blocks[2] == ["OUTPUT", "smu.ON"]
    assert blocks[3] == ["DIGIO", 3, 3]
    assert blocks[4] == ["DELAY", 0.0012]
    assert blocks[5][0] == "MEASURE" and blocks[5][2] == 1
    assert blocks[6] == ["LIMIT", "OUTSIDE", 0.0, 2.5, 12, 5]
    assert blocks[7] == ["LIMIT", "INSIDE", -0.001, 0.001, 12, 5]
    assert blocks[8] == ["COUNTER", 16, 4]
    assert blocks[9] == ["RESET", 8]
    assert blocks[10] == ["WAIT", "DIGIO6", "CLEAR_ENTER"]
    assert blocks[11] == ["LOG", "INFO1", "OE_T0"]
    assert blocks[12] == ["ALWAYS", 2]


@pytest.mark.parametrize(
    "call,message",
    [
        ((1, 1, 3, 1, 0, 0), "source entry"),
        ((1, 3, 4, 4, 0, 0), "mask"),  # line 3 is not an output
        ((1, 3, 32, 32, 0, 0), "mask"),  # line 6 is the START input
        ((1, 3, 2, 8, 0, 0), "pattern"),  # busy bit under a marker-only mask
        ((1, 3, 1, 10, 0, 0), "pattern"),  # ready bit under a busy+marker mask
        ((1, 4, -1, 0, 0, 0), "delay"),
        ((1, 5, 11, 0, 0, 0), "measure count"),
        ((2, 6, -0.1, 2.5, 3, 1), "absolute window"),
        ((2, 6, 0, 2.6, 3, 1), "absolute window"),
        ((2, 7, -0.006, 0.006, 3, 1), "i_max_abs"),
        ((2, 6, 0, 2.5, 3, 2), "measure block"),  # must precede the limit block
        ((1, 8, 2, 9, 0, 0), "counter target"),
        ((1, 11, 5, 0, 0, 0), "log code"),
        ((1, 13, 0, 0, 0, 0), "block code"),
        ((3, 2, 1, 0, 0, 0), "block"),  # skips block 2
    ],
)
def test_blocks_are_validated(lua, call, message):
    g = build(lua, n_blocks=3)
    g.oe_seq_source(1, 1, 0.001, 0.01, 2.6)
    g.oe_seq_measure(1, 1, 7, 1)
    if call[0] >= 2:
        g.oe_seq_block(1, 5, 1, 0, 0, 0)
    with pytest.raises(lupa.LuaError, match=message):
        g.oe_seq_block(*call)


# Commit, arm, data, shutdown


@pytest.mark.parametrize("pattern,mask", [(0, 11), (11, 11), (8, 11), (2, 3), (10, 10), (1, 1)])
def test_digital_blocks_accept_any_pattern_within_the_output_lines(lua, pattern, mask):
    g = build(lua, n_blocks=1)
    g.oe_seq_block(1, 3, pattern, mask, 0, 0)
    assert list(g.blocks[1].values()) == ["DIGIO", pattern, mask]


def test_commit_requires_complete_program_with_output_off(lua):
    g = build(lua, n_blocks=2)
    g.oe_seq_source(1, 1, 0.001, 0.01, 2.6)
    g.oe_seq_measure(1, 1, 7, 1)
    g.oe_seq_block(1, 2, 1, 0, 0, 0)
    with pytest.raises(lupa.LuaError, match="incomplete"):
        g.oe_seq_commit()
    g.oe_seq_block(2, 5, 1, 0, 0, 0)
    with pytest.raises(lupa.LuaError, match="never turns the output off"):
        g.oe_seq_commit()


def test_commit_and_arm(lua):
    g = minimal_program(lua)
    assert g.oe_seq_state == "ready" and out(lua)[-1] == "OK 4 10"
    assert g.src.func == "smu.FUNC_DC_CURRENT" and g.src.level == 0
    g.oe_seq_arm()
    assert g.oe_seq_state == "armed" and g.model_state == "trigger.STATE_RUNNING"
    events = [list(e.values()) for e in g.log.values()]
    assert events[-2:] == [["digin_clear", 6], ["initiate"]]
    with pytest.raises(lupa.LuaError):
        g.oe_seq_arm()


def test_arm_requires_committed_program_and_output_off(lua):
    g = build(lua)
    with pytest.raises(lupa.LuaError, match="no committed program"):
        g.oe_seq_arm()
    g = minimal_program(lua)
    g.src.output = "smu.ON"
    with pytest.raises(lupa.LuaError, match="output"):
        g.oe_seq_arm()


def test_unread_records_block_the_next_program_until_acknowledged(lua):
    g = minimal_program(lua)
    g.oe_seq_buf.n = 7
    with pytest.raises(lupa.LuaError, match="unread records"):
        g.oe_seq_begin(1, 1, 4, 10)
    with pytest.raises(lupa.LuaError):
        g.oe_seq_ack(8)
    g.oe_seq_ack(7)
    with pytest.raises(lupa.LuaError):
        g.oe_seq_ack(6)  # acknowledgements only move forward
    g.oe_seq_begin(1, 1, 4, 10)
    assert g.buffers[1].deleted


def test_discard_is_explicit(lua):
    g = minimal_program(lua)
    g.oe_seq_buf.n = 3
    g.oe_seq_discard()
    g.oe_seq_begin(1, 1, 4, 10)


def test_read_prints_six_fields_and_bounds_chunks(lua):
    g = minimal_program(lua)
    g.oe_seq_buf.n = 300
    g.oe_seq_read(1, 250)
    call = list(g.log[len(g.log)].values())
    assert call == ["printbuffer", 1, 250, "S", "F", "R", "V", "ST", "SS"]
    g.oe_seq_read(251, 250)
    assert list(g.log[len(g.log)].values())[1:3] == [251, 300]
    with pytest.raises(lupa.LuaError):
        g.oe_seq_read(1, 251)
    with pytest.raises(lupa.LuaError):
        g.oe_seq_read(301, 1)


def test_status_line(lua):
    g = minimal_program(lua)
    g.oe_seq_buf.n = 5
    g.oe_seq_status()
    assert out(lua)[-1] == "ready\ttrigger.STATE_EMPTY\t0\tsmu.OFF\t5\t0"


def test_off_handles_the_stale_output_attribute(lua):
    g = minimal_program(lua)
    g.oe_seq_arm()
    g.src.output = "smu.ON"
    g.stale_on = True
    g.oe_seq_abort()
    assert out(lua)[-1] == "OFF"
    assert g.src.output == "smu.OFF" and g.src.func == "smu.FUNC_DC_CURRENT"
    assert g.oe_seq_state == "aborted" and g.model_state == "trigger.STATE_IDLE"
    assert all(g.digio.line[line].state == "LOW" for line in (1, 2, 4))


def test_ping_posts_a_timestamped_event(lua):
    lua.globals().oe_seq_ping()
    assert list(lua.globals().log[len(lua.globals().log)].values()) == ["post", "OE_PING", "INFO"]


def test_begin_refuses_more_blocks_than_the_firmware_takes(lua):
    g = minimal_program(lua)  # a committed program the refusal must leave alone
    g.oe_seq_buf.n = 5  # unread records of that run
    lua.execute("before_buf = oe_seq_buf")
    before = (g.oe_seq_state, g.oe_seq_expect.blocks, g.oe_seq_buf.n, g.oe_seq_acked)
    with pytest.raises(lupa.LuaError, match=r"n_blocks must be an integer in 1\.\.63"):
        g.oe_seq_begin(1, 1, 64, 10)
    assert (g.oe_seq_state, g.oe_seq_expect.blocks, g.oe_seq_buf.n, g.oe_seq_acked) == before
    assert lua.eval("before_buf == oe_seq_buf")  # the same buffer, not a new one
    assert g.src.output == "smu.OFF"
    g.oe_seq_buf.n = 0  # records read; 63 blocks is accepted
    build(lua, n_blocks=63)
