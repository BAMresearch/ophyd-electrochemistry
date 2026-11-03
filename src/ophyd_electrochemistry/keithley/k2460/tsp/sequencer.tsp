-- ophyd-electrochemistry sequencer runtime (docs/sequencer-design.md).
--
-- The host compiles a validated Sequence into config-list entries and a
-- trigger-model block table (keithley/k2460/sequencer.py) and uploads them
-- with numeric calls to the functions below. Every entry and block is checked
-- here against the session limits given to oe_seq_configure. The model runs
-- instrument-locally and ends with the output OFF. Lua 5.0: no '#', no '%'.

oe_seq_runtime_abi = "oe-k2460-seq-v1"
oe_seq_runtime_build = "seq-v1"

oe_seq_state = "idle"
oe_seq_cfg = nil
oe_seq_buf = nil
oe_seq_acked = 0
oe_seq_lists = false
oe_seq_expect = nil
oe_seq_count = nil

local function oe_seq_number(value, name)
    if type(value) ~= "number" or value ~= value then
        error(name .. " must be a real number")
    end
    return value
end

local function oe_seq_integer(value, name, low, high)
    oe_seq_number(value, name)
    if value ~= math.floor(value) or value < low or value > high then
        error(name .. " must be an integer in " .. low .. ".." .. high)
    end
    return value
end

local function oe_seq_model_active()
    local state = trigger.model.state()
    return state == trigger.STATE_RUNNING or state == trigger.STATE_WAITING
end

local function oe_seq_require_inactive_off()
    if oe_seq_model_active() then
        error("trigger model is active")
    end
    if smu.source.output ~= smu.OFF then
        error("output is not OFF")
    end
end

local function oe_seq_require_building()
    if oe_seq_state ~= "building" then
        error("no program is being built (state " .. oe_seq_state .. ")")
    end
end

local function oe_seq_bit(line)
    return 2 ^ (line - 1)
end

local oe_seq_digio_events = {
    trigger.EVENT_DIGIO1, trigger.EVENT_DIGIO2, trigger.EVENT_DIGIO3,
    trigger.EVENT_DIGIO4, trigger.EVENT_DIGIO5, trigger.EVENT_DIGIO6,
}

local oe_seq_log_levels = {
    trigger.LOG_INFO1, trigger.LOG_INFO2, trigger.LOG_INFO3, trigger.LOG_INFO4,
}

local oe_seq_log_names = { "OE_T0", "OE_END", "OE_STOP", "OE_DONE" }

-- Output OFF with the stale-attribute fallback (design section 10): if
-- smu.source.output still reads ON after OFF, change the source function and
-- back, which forces the output off.
function oe_seq_off()
    trigger.model.abort()
    smu.source.output = smu.OFF
    if smu.source.output ~= smu.OFF then
        local func = smu.source.func
        if func == smu.FUNC_DC_CURRENT then
            smu.source.func = smu.FUNC_DC_VOLTAGE
        else
            smu.source.func = smu.FUNC_DC_CURRENT
        end
        smu.source.func = func
    end
    if oe_seq_cfg ~= nil then
        digio.line[oe_seq_cfg.marker].state = digio.STATE_LOW
        digio.line[oe_seq_cfg.busy].state = digio.STATE_LOW
    end
    if smu.source.output == smu.OFF then
        print("OFF")
    else
        print("ON")
    end
end

function oe_seq_initialize()
    if oe_seq_model_active() or smu.source.output ~= smu.OFF then
        error("sequencer initialization requires an inactive model and output OFF")
    end
    oe_seq_state = "idle"
end

-- Session settings. four_wire, front: 1/0. edge: 1 rising, 2 falling.
function oe_seq_configure(v_min_abs, v_max_abs, i_max_abs, vlimit_max, marker, busy, start,
        four_wire, front, edge)
    oe_seq_require_inactive_off()
    oe_seq_number(v_min_abs, "v_min_abs")
    oe_seq_number(v_max_abs, "v_max_abs")
    if v_min_abs >= v_max_abs then
        error("v_min_abs must be below v_max_abs")
    end
    oe_seq_number(i_max_abs, "i_max_abs")
    if i_max_abs <= 0 or i_max_abs > 7 then
        error("i_max_abs must be in (0, 7] A")
    end
    oe_seq_number(vlimit_max, "vlimit_max")
    if vlimit_max <= 0 or vlimit_max > 100 then
        error("vlimit_max must be in (0, 100] V")
    end
    oe_seq_integer(marker, "marker line", 1, 6)
    oe_seq_integer(busy, "busy line", 1, 6)
    oe_seq_integer(start, "start line", 1, 6)
    if marker == busy or marker == start or busy == start then
        error("marker, busy and start lines must differ")
    end
    oe_seq_integer(four_wire, "four_wire", 0, 1)
    oe_seq_integer(front, "front", 0, 1)
    oe_seq_integer(edge, "edge", 1, 2)
    if front == 1 then
        smu.terminals = smu.TERMINALS_FRONT
    else
        smu.terminals = smu.TERMINALS_REAR
    end
    -- The output-off mode is stored per source function: HIGH-Z for both.
    smu.source.func = smu.FUNC_DC_VOLTAGE
    smu.source.offmode = smu.OFFMODE_HIGHZ
    local voltage_off = smu.source.offmode
    smu.source.func = smu.FUNC_DC_CURRENT
    smu.source.offmode = smu.OFFMODE_HIGHZ
    smu.source.level = 0
    if voltage_off ~= smu.OFFMODE_HIGHZ or smu.source.offmode ~= smu.OFFMODE_HIGHZ then
        error("HIGH-Z output-off mode not confirmed for both source functions")
    end
    digio.line[marker].mode = digio.MODE_DIGITAL_OUT
    digio.line[marker].state = digio.STATE_LOW
    digio.line[busy].mode = digio.MODE_DIGITAL_OUT
    digio.line[busy].state = digio.STATE_LOW
    digio.line[start].mode = digio.MODE_TRIGGER_IN
    if edge == 1 then
        trigger.digin[start].edge = trigger.EDGE_RISING
    else
        trigger.digin[start].edge = trigger.EDGE_FALLING
    end
    trigger.digin[start].clear()
    -- Readings are printed with enough digits to reproduce the limit tests.
    format.asciiprecision = 15
    oe_seq_cfg = {
        v_min = v_min_abs, v_max = v_max_abs, i_max = i_max_abs, vlimit_max = vlimit_max,
        marker = marker, busy = busy, start = start, four_wire = four_wire,
    }
    print("OK")
end

-- Start a program. Refuses while the previous buffer holds unread records.
function oe_seq_begin(n_src, n_meas, n_blocks, capacity)
    oe_seq_require_inactive_off()
    if oe_seq_cfg == nil then
        error("oe_seq_configure first")
    end
    oe_seq_integer(n_src, "n_src", 1, 100)
    oe_seq_integer(n_meas, "n_meas", 1, 100)
    oe_seq_integer(n_blocks, "n_blocks", 1, 255)
    oe_seq_integer(capacity, "capacity", 1, 1000000)
    if oe_seq_buf ~= nil and oe_seq_buf.n > oe_seq_acked then
        error("previous run buffer has unread records; read or discard them first")
    end
    trigger.model.load("Empty")
    if oe_seq_buf ~= nil then
        buffer.delete(oe_seq_buf)
        oe_seq_buf = nil
    end
    if oe_seq_lists then
        smu.source.configlist.delete("OE_SRC")
        smu.measure.configlist.delete("OE_MEAS")
        oe_seq_lists = false
    end
    collectgarbage()
    smu.source.configlist.create("OE_SRC")
    smu.measure.configlist.create("OE_MEAS")
    oe_seq_lists = true
    oe_seq_buf = buffer.make(capacity, buffer.STYLE_STANDARD)
    oe_seq_acked = 0
    oe_seq_expect = { src = n_src, meas = n_meas, blocks = n_blocks, capacity = capacity }
    oe_seq_count = { src = 0, meas = 0, blocks = 0, output_off = 0 }
    oe_seq_state = "building"
    print("OK")
end

local function oe_seq_measure_settings(func, range, nplc)
    -- Callers set the complementary source function first, so measure and
    -- source functions differ and the measure range is not clamped.
    smu.measure.func = func
    smu.measure.sense = smu.SENSE_2WIRE
    if oe_seq_cfg.four_wire == 1 then
        smu.measure.sense = smu.SENSE_4WIRE
    end
    smu.measure.autorange = smu.OFF
    smu.measure.range = range
    smu.measure.nplc = nplc
    smu.measure.autozero.enable = smu.OFF
end

-- mode: 1 current source (limit = vlimit), 2 voltage source (limit = ilimit).
function oe_seq_source(i, mode, level, range, limit)
    oe_seq_require_building()
    oe_seq_integer(i, "source entry", oe_seq_count.src + 1, oe_seq_count.src + 1)
    oe_seq_integer(mode, "mode", 1, 2)
    oe_seq_number(level, "level")
    oe_seq_number(range, "range")
    oe_seq_number(limit, "limit")
    if mode == 1 then
        if math.abs(level) > oe_seq_cfg.i_max then
            error("current level exceeds i_max_abs")
        end
        if limit <= 0 or limit > oe_seq_cfg.vlimit_max then
            error("voltage limit must be in (0, vlimit_max]")
        end
        if range < math.abs(level) then
            error("current range below level")
        end
        smu.source.func = smu.FUNC_DC_CURRENT
        oe_seq_measure_settings(smu.FUNC_DC_VOLTAGE, limit, 1)
        smu.source.autorange = smu.OFF
        smu.source.range = range
        smu.source.vlimit.level = limit
    else
        if level < oe_seq_cfg.v_min or level > oe_seq_cfg.v_max then
            error("voltage level outside the absolute window")
        end
        if limit <= 0 or limit > oe_seq_cfg.i_max then
            error("current limit must be in (0, i_max_abs]")
        end
        if range < math.abs(level) then
            error("voltage range below level")
        end
        smu.source.func = smu.FUNC_DC_VOLTAGE
        oe_seq_measure_settings(smu.FUNC_DC_CURRENT, limit, 1)
        smu.source.autorange = smu.OFF
        smu.source.range = range
        smu.source.ilimit.level = limit
    end
    smu.source.readback = smu.ON
    smu.source.level = level
    smu.source.configlist.store("OE_SRC")
    oe_seq_count.src = i
    print("OK")
end

-- func: 1 measure voltage (current-source steps), 2 measure current.
function oe_seq_measure(i, func, range, nplc)
    oe_seq_require_building()
    oe_seq_integer(i, "measure entry", oe_seq_count.meas + 1, oe_seq_count.meas + 1)
    oe_seq_integer(func, "func", 1, 2)
    oe_seq_number(range, "range")
    oe_seq_number(nplc, "nplc")
    if nplc < 0.01 or nplc > 10 then
        error("nplc must be in [0.01, 10]")
    end
    if func == 1 then
        smu.source.func = smu.FUNC_DC_CURRENT
        oe_seq_measure_settings(smu.FUNC_DC_VOLTAGE, range, nplc)
    else
        smu.source.func = smu.FUNC_DC_VOLTAGE
        oe_seq_measure_settings(smu.FUNC_DC_CURRENT, range, nplc)
    end
    smu.measure.configlist.store("OE_MEAS")
    oe_seq_count.meas = i
    print("OK")
end

local function oe_seq_target(value, name)
    return oe_seq_integer(value, name, 1, oe_seq_expect.blocks)
end

-- Block codes are the ABI of keithley/k2460/sequencer.py BlockKind.
function oe_seq_block(i, code, a, b, c, d)
    oe_seq_require_building()
    oe_seq_integer(i, "block", oe_seq_count.blocks + 1, oe_seq_count.blocks + 1)
    if i > oe_seq_expect.blocks then
        error("more blocks than announced")
    end
    oe_seq_integer(code, "block code", 1, 12)
    local cfg = oe_seq_cfg
    if code == 1 then
        oe_seq_integer(a, "source entry", 1, oe_seq_expect.src)
        oe_seq_integer(b, "measure entry", 1, oe_seq_expect.meas)
        trigger.model.setblock(i, trigger.BLOCK_CONFIG_RECALL, "OE_SRC", a, "OE_MEAS", b)
    elseif code == 2 then
        oe_seq_integer(a, "output", 0, 1)
        if a == 1 then
            trigger.model.setblock(i, trigger.BLOCK_SOURCE_OUTPUT, smu.ON)
        else
            trigger.model.setblock(i, trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
            oe_seq_count.output_off = oe_seq_count.output_off + 1
        end
    elseif code == 3 then
        local allowed = oe_seq_bit(cfg.marker) + oe_seq_bit(cfg.busy)
        oe_seq_integer(a, "pattern", 0, 63)
        oe_seq_integer(b, "mask", 1, 63)
        if b ~= oe_seq_bit(cfg.marker) and b ~= oe_seq_bit(cfg.busy) and b ~= allowed then
            error("digital mask may only cover the marker and busy lines")
        end
        -- Pattern: 0, the whole mask, or one of the two lines under a two-line mask.
        if a ~= 0 and a ~= b and not (b == allowed and
                (a == oe_seq_bit(cfg.marker) or a == oe_seq_bit(cfg.busy))) then
            error("digital pattern outside its mask")
        end
        trigger.model.setblock(i, trigger.BLOCK_DIGITAL_IO, a, b)
    elseif code == 4 then
        oe_seq_number(a, "delay")
        if a < 0 or a > 3600 then
            error("delay must be in [0, 3600] s")
        end
        trigger.model.setblock(i, trigger.BLOCK_DELAY_CONSTANT, a)
    elseif code == 5 then
        oe_seq_integer(a, "measure count", 1, oe_seq_expect.capacity)
        trigger.model.setblock(i, trigger.BLOCK_MEASURE_DIGITIZE, oe_seq_buf, a)
    elseif code == 6 or code == 7 then
        oe_seq_number(a, "limit low")
        oe_seq_number(b, "limit high")
        if a >= b then
            error("limit low must be below limit high")
        end
        oe_seq_target(c, "limit target")
        oe_seq_integer(d, "limit measure block", 1, i - 1)
        if code == 6 then
            -- Voltage cutoffs must lie within the absolute window.
            if a < cfg.v_min or b > cfg.v_max then
                error("voltage cutoff window outside the absolute window")
            end
            trigger.model.setblock(i, trigger.BLOCK_BRANCH_LIMIT_CONSTANT,
                trigger.LIMIT_OUTSIDE, a, b, c, d)
        else
            if math.abs(a) > cfg.i_max or math.abs(b) > cfg.i_max then
                error("current taper window exceeds i_max_abs")
            end
            trigger.model.setblock(i, trigger.BLOCK_BRANCH_LIMIT_CONSTANT,
                trigger.LIMIT_INSIDE, a, b, c, d)
        end
    elseif code == 8 then
        oe_seq_integer(a, "counter count", 1, 1000000)
        oe_seq_target(b, "counter target")
        trigger.model.setblock(i, trigger.BLOCK_BRANCH_COUNTER, a, b)
    elseif code == 9 then
        oe_seq_target(a, "reset counter block")
        trigger.model.setblock(i, trigger.BLOCK_RESET_BRANCH_COUNT, a)
    elseif code == 10 then
        trigger.model.setblock(i, trigger.BLOCK_WAIT, oe_seq_digio_events[cfg.start],
            trigger.CLEAR_ENTER)
    elseif code == 11 then
        oe_seq_integer(a, "log code", 1, 4)
        trigger.model.setblock(i, trigger.BLOCK_LOG_EVENT, oe_seq_log_levels[a],
            oe_seq_log_names[a])
    else
        oe_seq_target(a, "branch target")
        trigger.model.setblock(i, trigger.BLOCK_BRANCH_ALWAYS, a)
    end
    oe_seq_count.blocks = i
    print("OK")
end

-- Finish building: all entries and blocks present, a way to turn the output
-- off, safe active settings (current source, 0 A), HIGH-Z for both functions.
function oe_seq_commit()
    oe_seq_require_building()
    if oe_seq_count.src ~= oe_seq_expect.src or oe_seq_count.meas ~= oe_seq_expect.meas
            or oe_seq_count.blocks ~= oe_seq_expect.blocks then
        error("program incomplete")
    end
    if oe_seq_count.output_off < 1 then
        error("program never turns the output off")
    end
    smu.source.func = smu.FUNC_DC_VOLTAGE
    local voltage_off = smu.source.offmode
    smu.source.func = smu.FUNC_DC_CURRENT
    smu.source.level = 0
    smu.measure.func = smu.FUNC_DC_VOLTAGE
    if voltage_off ~= smu.OFFMODE_HIGHZ or smu.source.offmode ~= smu.OFFMODE_HIGHZ then
        error("HIGH-Z output-off mode not confirmed for both source functions")
    end
    oe_seq_state = "ready"
    print("OK " .. oe_seq_expect.blocks .. " " .. oe_seq_buf.capacity)
end

function oe_seq_arm()
    if oe_seq_state ~= "ready" then
        error("no committed program (state " .. oe_seq_state .. ")")
    end
    oe_seq_require_inactive_off()
    trigger.digin[oe_seq_cfg.start].clear()
    trigger.model.initiate()
    oe_seq_state = "armed"
    print("OK")
end

-- One line: runtime state, model state, model block, output, records, acknowledged.
function oe_seq_status()
    local model, _, block = trigger.model.state()
    local n = 0
    if oe_seq_buf ~= nil then
        n = oe_seq_buf.n
    end
    print(oe_seq_state .. "\t" .. tostring(model) .. "\t" .. tostring(block) .. "\t"
        .. tostring(smu.source.output) .. "\t" .. n .. "\t" .. oe_seq_acked)
end

-- At most 250 records per call (about 28 KB at 15 digits).
function oe_seq_read(first, count)
    if oe_seq_buf == nil then
        error("no run buffer")
    end
    local n = oe_seq_buf.n
    oe_seq_integer(first, "first record", 1, n)
    oe_seq_integer(count, "record count", 1, 250)
    local last = first + count - 1
    if last > n then
        last = n
    end
    printbuffer(first, last, oe_seq_buf.seconds, oe_seq_buf.fractionalseconds,
        oe_seq_buf.readings, oe_seq_buf.sourcevalues, oe_seq_buf.statuses,
        oe_seq_buf.sourcestatuses)
end

-- The host has archived records up to offset; only these may be discarded.
function oe_seq_ack(offset)
    if oe_seq_buf == nil then
        error("no run buffer")
    end
    oe_seq_integer(offset, "acknowledged offset", oe_seq_acked, oe_seq_buf.n)
    oe_seq_acked = offset
    print("OK")
end

-- Explicit operator discard of unread records (the host asks for confirmation).
function oe_seq_discard()
    oe_seq_require_inactive_off()
    if oe_seq_buf ~= nil then
        oe_seq_acked = oe_seq_buf.n
    end
    print("OK")
end

function oe_seq_abort()
    oe_seq_off()
    if oe_seq_state == "armed" then
        oe_seq_state = "aborted"
    end
end

-- Event-log timestamp for mapping the instrument clock to UTC.
function oe_seq_ping()
    eventlog.post("OE_PING", eventlog.SEV_INFO)
    print("OK")
end
