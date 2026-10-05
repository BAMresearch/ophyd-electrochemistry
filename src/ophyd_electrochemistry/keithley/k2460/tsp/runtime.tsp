-- ophyd-electrochemistry M4 finite-hold proof runtime.
--
-- This is intentionally not the M5 acquisition runtime. It supports only a
-- bounded current hold on an explicitly configured resistive commissioning
-- load. External ABORT, measurement cutoffs, and arbitrary waveforms remain
-- disabled. The host accepts only this packaged source and verifies the ABI.

oe_m4_runtime_abi = "oe-k2460-m4-hold-v1"
oe_m4_runtime_build = "m4-finite-current-hold-v1"

oe_m4_state = "idle"
oe_m4_mode = "none"
oe_m4_ready_line = 1
oe_m4_busy_line = 2
oe_m4_start_line = 3
oe_m4_ready_asserted = 1
oe_m4_busy_asserted = 1
oe_m4_complete_block = 0
oe_m4_timeout_block = 0

local function oe_m4_require_number(value, name)
    if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
        error(name .. " must be a finite number")
    end
end

local function oe_m4_require_line(value, name)
    oe_m4_require_number(value, name)
    if value < 1 or value > 6 or value ~= math.floor(value) then
        error(name .. " must be an integer in 1..6")
    end
end

local function oe_m4_bit(line)
    return 2 ^ (line - 1)
end

local function oe_m4_digio_event(line)
    if line == 1 then return trigger.EVENT_DIGIO1 end
    if line == 2 then return trigger.EVENT_DIGIO2 end
    if line == 3 then return trigger.EVENT_DIGIO3 end
    if line == 4 then return trigger.EVENT_DIGIO4 end
    if line == 5 then return trigger.EVENT_DIGIO5 end
    if line == 6 then return trigger.EVENT_DIGIO6 end
    error("digital trigger line must be in 1..6")
end

local function oe_m4_level(asserted, asserted_level)
    if asserted then
        return asserted_level
    end
    return 1 - asserted_level
end

local function oe_m4_pattern(ready, busy)
    local pattern = 0
    if oe_m4_level(ready, oe_m4_ready_asserted) == 1 then
        pattern = pattern + oe_m4_bit(oe_m4_ready_line)
    end
    if oe_m4_level(busy, oe_m4_busy_asserted) == 1 then
        pattern = pattern + oe_m4_bit(oe_m4_busy_line)
    end
    return pattern
end

local function oe_m4_mask()
    return oe_m4_bit(oe_m4_ready_line) + oe_m4_bit(oe_m4_busy_line)
end

local function oe_m4_set_flags(ready, busy)
    digio.line[oe_m4_ready_line].state = oe_m4_level(ready, oe_m4_ready_asserted)
    digio.line[oe_m4_busy_line].state = oe_m4_level(busy, oe_m4_busy_asserted)
end

local function oe_m4_model_active()
    local model_state = trigger.model.state()
    return model_state == trigger.STATE_RUNNING or
        model_state == trigger.STATE_WAITING or
        model_state == trigger.STATE_ABORTING
end

function oe_m4_initialize()
    smu.source.output = smu.OFF
    oe_m4_state = "idle"
    oe_m4_mode = "none"
end

function oe_m4_prepare_current_hold(
    current_a,
    source_range_a,
    voltage_limit_v,
    duration_s,
    external_start,
    start_timeout_s,
    event_poll_s,
    front_terminals,
    remote_sense,
    ready_line,
    busy_line,
    start_line,
    ready_asserted,
    busy_asserted,
    rising_start)

    oe_m4_require_number(current_a, "current_a")
    oe_m4_require_number(source_range_a, "source_range_a")
    oe_m4_require_number(voltage_limit_v, "voltage_limit_v")
    oe_m4_require_number(duration_s, "duration_s")
    oe_m4_require_number(start_timeout_s, "start_timeout_s")
    oe_m4_require_number(event_poll_s, "event_poll_s")
    oe_m4_require_line(ready_line, "ready_line")
    oe_m4_require_line(busy_line, "busy_line")
    oe_m4_require_line(start_line, "start_line")

    if math.abs(current_a) > 0.01 then
        error("M4 proof current is limited to 10 mA")
    end
    if source_range_a <= 0 or source_range_a > 0.01 or math.abs(current_a) > source_range_a then
        error("M4 proof source range is invalid")
    end
    if voltage_limit_v < 0.01 or voltage_limit_v > 2 then
        error("M4 proof voltage limit must be in 0.01..2 V")
    end
    if duration_s < 0.001 or duration_s > 60 then
        error("M4 proof duration must be in 0.001..60 s")
    end
    if start_timeout_s < 0.001 or start_timeout_s > 60 then
        error("M4 proof start timeout must be in 0.001..60 s")
    end
    if event_poll_s < 0.001 or event_poll_s > 0.1 then
        error("M4 proof event poll must be in 0.001..0.1 s")
    end
    if ready_line == busy_line or ready_line == start_line or busy_line == start_line then
        error("M4 proof digital lines must be unique")
    end
    if (ready_asserted ~= 0 and ready_asserted ~= 1) or
        (busy_asserted ~= 0 and busy_asserted ~= 1) then
        error("M4 proof flag levels must be 0 or 1")
    end
    if oe_m4_model_active() then
        error("cannot prepare while trigger model is active")
    end
    if smu.source.output ~= smu.OFF then
        error("cannot prepare while source output is on")
    end

    trigger.model.abort()
    trigger.timer[1].enable = trigger.OFF
    smu.source.output = smu.OFF
    smu.source.level = 0

    oe_m4_ready_line = ready_line
    oe_m4_busy_line = busy_line
    oe_m4_start_line = start_line
    oe_m4_ready_asserted = ready_asserted
    oe_m4_busy_asserted = busy_asserted

    digio.line[ready_line].mode = digio.MODE_DIGITAL_OUT
    digio.line[busy_line].mode = digio.MODE_DIGITAL_OUT
    oe_m4_set_flags(false, false)

    if external_start == 1 then
        digio.line[start_line].mode = digio.MODE_TRIGGER_IN
        if rising_start == 1 then
            trigger.digin[start_line].edge = trigger.EDGE_RISING
        else
            trigger.digin[start_line].edge = trigger.EDGE_FALLING
        end
        trigger.digin[start_line].clear()
    end

    if front_terminals == 1 then
        smu.terminals = smu.TERMINALS_FRONT
    else
        smu.terminals = smu.TERMINALS_REAR
    end
    smu.source.func = smu.FUNC_DC_CURRENT
    smu.source.level = 0
    smu.source.autorange = smu.OFF
    smu.source.range = source_range_a
    smu.source.vlimit.level = voltage_limit_v
    smu.source.readback = smu.ON
    smu.source.offmode = smu.OFFMODE_NORMAL
    smu.measure.func = smu.FUNC_DC_VOLTAGE
    if remote_sense == 1 then
        smu.measure.sense = smu.SENSE_4WIRE
    else
        smu.measure.sense = smu.SENSE_2WIRE
    end
    smu.source.level = current_a

    trigger.model.load("Empty")
    if external_start == 1 then
        trigger.timer[1].reset()
        trigger.timer[1].delay = start_timeout_s
        trigger.timer[1].start.stimulus = trigger.EVENT_NOTIFY1
        trigger.timer[1].start.generate = trigger.OFF
        trigger.timer[1].count = 1
        trigger.timer[1].enable = trigger.ON

        trigger.model.setblock(1, trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(true, false), oe_m4_mask())
        trigger.model.setblock(2, trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY1)
        trigger.model.setblock(3, trigger.BLOCK_BRANCH_ON_EVENT, trigger.EVENT_TIMER1, 13)
        trigger.model.setblock(4, trigger.BLOCK_BRANCH_ON_EVENT, oe_m4_digio_event(start_line), 7)
        trigger.model.setblock(5, trigger.BLOCK_DELAY_CONSTANT, event_poll_s)
        trigger.model.setblock(6, trigger.BLOCK_BRANCH_ALWAYS, 3)
        trigger.model.setblock(7, trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, true), oe_m4_mask())
        trigger.model.setblock(8, trigger.BLOCK_SOURCE_OUTPUT, smu.ON)
        trigger.model.setblock(9, trigger.BLOCK_DELAY_CONSTANT, duration_s)
        trigger.model.setblock(10, trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
        trigger.model.setblock(11, trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
        trigger.model.setblock(12, trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY2)
        trigger.model.setblock(13, trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
        trigger.model.setblock(14, trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
        trigger.model.setblock(15, trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY3)
        oe_m4_mode = "external"
        oe_m4_complete_block = 12
        oe_m4_timeout_block = 15
    else
        trigger.model.setblock(1, trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, true), oe_m4_mask())
        trigger.model.setblock(2, trigger.BLOCK_SOURCE_OUTPUT, smu.ON)
        trigger.model.setblock(3, trigger.BLOCK_DELAY_CONSTANT, duration_s)
        trigger.model.setblock(4, trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
        trigger.model.setblock(5, trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
        trigger.model.setblock(6, trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY2)
        oe_m4_mode = "immediate"
        oe_m4_complete_block = 6
        oe_m4_timeout_block = 0
    end

    oe_m4_state = "prepared"
end

function oe_m4_arm()
    if oe_m4_state ~= "prepared" then
        error("M4 proof runtime is not prepared")
    end
    if smu.source.output ~= smu.OFF then
        error("M4 proof output is not off before arm")
    end
    if oe_m4_mode == "external" then
        local expected_inactive = 0
        if trigger.digin[oe_m4_start_line].edge == trigger.EDGE_FALLING then
            expected_inactive = 1
        end
        if digio.line[oe_m4_start_line].state ~= expected_inactive then
            error("START input is asserted before arm")
        end
        trigger.digin[oe_m4_start_line].clear()
        trigger.timer[1].clear()
        oe_m4_state = "waiting_start"
    else
        oe_m4_state = "running"
    end
    trigger.model.initiate()
end

function oe_m4_refresh_state()
    local model_state, model_substate, block = trigger.model.state()
    if oe_m4_state == "running" or oe_m4_state == "waiting_start" then
        if model_state == trigger.STATE_IDLE then
            if block == oe_m4_complete_block then
                oe_m4_state = "complete"
            elseif block == oe_m4_timeout_block then
                oe_m4_state = "start_timeout"
            else
                oe_m4_state = "error"
            end
        elseif model_state == trigger.STATE_ABORTED then
            oe_m4_state = "aborted"
        elseif model_state == trigger.STATE_FAILED then
            oe_m4_state = "error"
        end
    end
    return model_state, model_substate, block
end

function oe_m4_status()
    local model_state, model_substate, block = oe_m4_refresh_state()
    local start_state = -1
    local start_overrun = false
    if oe_m4_mode == "external" then
        start_state = digio.line[oe_m4_start_line].state
        start_overrun = trigger.digin[oe_m4_start_line].overrun
    end
    return oe_m4_runtime_abi,
        oe_m4_state,
        model_state,
        model_substate,
        block,
        smu.source.output,
        digio.line[oe_m4_ready_line].state,
        digio.line[oe_m4_busy_line].state,
        start_state,
        start_overrun,
        trigger.timer[1].start.overrun
end

function oe_m4_abort()
    trigger.model.abort()
    trigger.timer[1].enable = trigger.OFF
    smu.source.output = smu.OFF
    oe_m4_set_flags(false, false)
    smu.source.level = 0
    oe_m4_state = "aborted"
end

function oe_m4_force_safe()
    trigger.model.abort()
    trigger.timer[1].enable = trigger.OFF
    smu.source.output = smu.OFF
    oe_m4_set_flags(false, false)
    smu.source.level = 0
    oe_m4_state = "aborted"
end

function oe_m4_recover()
    local model_state = trigger.model.state()
    if model_state == trigger.STATE_RUNNING or model_state == trigger.STATE_WAITING or
        model_state == trigger.STATE_ABORTING then
        error("cannot recover while trigger model is active")
    end
    if smu.source.output ~= smu.OFF then
        error("cannot recover with output on")
    end
    trigger.timer[1].enable = trigger.OFF
    oe_m4_set_flags(false, false)
    smu.source.level = 0
    oe_m4_state = "idle"
end
