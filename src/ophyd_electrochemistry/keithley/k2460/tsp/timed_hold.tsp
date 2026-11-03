-- Candidate timed resistor hold extension; concatenate after exact packaged v14.
-- NO TIMING/LATENCY ACCEPTANCE: sequential measurement cannot be preempted here.
-- Reference: Keithley 2460 Reference Manual Rev. C, TSP sections 14-248,
-- 14-251, 14-267..269, 14-271..278. In particular WAIT/CLEAR_NEVER acts on
-- retained events, and exit clears its recorded event. BRANCH_ON_EVENT does
-- not document event clearing; this model never uses it to consume cadence.
-- Timer4 deadlines are finite {P/2,P,...,P}, not alternating half/full delays.
-- Deadline-event consumption, local cutoff, abort responsiveness, clock origin,
-- physical duration and sample timing require resistor commissioning evidence.
oe_m4_runtime_build = "m4-finite-current-hold-v17"
oe_timed_enabled = false
oe_timed_cause = "not_started"
oe_timed_count = 0
oe_timed_lower_cutoff = 0
oe_timed_upper_cutoff = 0
oe_timed_complete_block = 0
oe_timed_timeout_block = 0
oe_timed_cutoff_block = 0
oe_timed_deadline_block = 0
oe_timed_overrun_block = 0
local oe_timed_base_initialize = oe_m4_initialize
local oe_timed_base_refresh = oe_m4_refresh_state
local oe_timed_base_abort = oe_m4_abort
local oe_timed_base_force_safe = oe_m4_force_safe
local oe_timed_base_recover = oe_m4_recover
local oe_timed_base_prepare = oe_m4_prepare_current_hold
local function oe_timed_disable_timers()
    trigger.timer[2].enable = trigger.OFF
    trigger.timer[3].enable = trigger.OFF
    trigger.timer[4].enable = trigger.OFF
end
function oe_m4_initialize()
    oe_timed_disable_timers()
    oe_timed_base_initialize()
    trigger.model.load("Empty")
    oe_timed_enabled = false
    oe_timed_cause = "not_started"
end
function oe_m4_prepare_current_hold(
    current_a, source_range_a, voltage_limit_v, duration_s, external_start,
    start_timeout_s, event_poll_s, front_terminals, remote_sense,
    ready_line, busy_line, start_line, ready_asserted, busy_asserted,
    start_edge_mode)
    -- Keep the original proof entry points usable without interpreting their
    -- block numbers as timed-hold outcomes. The base guard runs before changes.
    oe_timed_base_prepare(
        current_a, source_range_a, voltage_limit_v, duration_s, external_start,
        start_timeout_s, event_poll_s, front_terminals, remote_sense,
        ready_line, busy_line, start_line, ready_asserted, busy_asserted,
        start_edge_mode)
    oe_timed_disable_timers()
    oe_timed_enabled = false
    oe_timed_cause = "not_started"
end
function oe_timed_prepare_current_hold(
    current_a, source_range_a, voltage_limit_v, duration_s, sample_period_s,
    measurement_count, lower_cutoff_v, upper_cutoff_v, external_start,
    start_timeout_s, event_poll_s, front_terminals, remote_sense,
    ready_line, busy_line, start_line, ready_asserted, busy_asserted,
    start_edge_mode, measurement_profile)
    oe_m4_require_number(duration_s, "duration_s")
    oe_m4_require_number(sample_period_s, "sample_period_s")
    oe_m4_require_number(measurement_count, "measurement_count")
    oe_m4_require_number(lower_cutoff_v, "lower_cutoff_v")
    oe_m4_require_number(upper_cutoff_v, "upper_cutoff_v")
    if sample_period_s < 0.1 or duration_s < sample_period_s or duration_s > 60 then
        error("timed hold requires 0.1 <= period <= duration <= 60 s")
    end
    if measurement_count < 1 or measurement_count > 600 or
        measurement_count ~= math.floor(measurement_count) or
        math.abs(duration_s - sample_period_s * measurement_count) > 1e-12 then
        error("timed hold requires a finite whole number of sample periods")
    end
    local measurement_range = 0.2
    local setup_voltage_limit = voltage_limit_v
    local setup_profile = measurement_profile
    if measurement_profile == 4 then
        measurement_range = 2
        setup_voltage_limit = math.min(voltage_limit_v, 0.2)
        setup_profile = 1
    end
    if voltage_limit_v < 0.106 * measurement_range or voltage_limit_v > measurement_range then
        error("timed hold compliance exceeds the selected measurement range")
    end
    if lower_cutoff_v < -measurement_range or upper_cutoff_v > measurement_range or
        lower_cutoff_v < -voltage_limit_v or upper_cutoff_v > voltage_limit_v or
        lower_cutoff_v >= upper_cutoff_v then
        error("timed hold cutoff bounds are invalid")
    end
    -- The shared preparation guards inactive/OFF state and retained records,
    -- allocates fill-once storage, and applies the immutable measurement profile.
    -- Rev. C 4-73/14-194: set measure range BEFORE its compatible source limit.
    -- First transition safely to the unchanged v14 initializer's 0.2 V range.
    if oe_m4_model_active() or smu.source.output ~= smu.OFF then
        error("timed setup requires stopped model and output OFF")
    end
    if oe_m5_buffer ~= nil and oe_m5_buffer.n > 0 then
        error("retained data must be explicitly dispositioned before timed setup")
    end
    smu.source.func = smu.FUNC_DC_CURRENT
    smu.source.level = 0
    smu.measure.func = smu.FUNC_DC_VOLTAGE
    smu.measure.range = 0.2
    oe_m5_prepare_profiled_current_hold_acquisition(
        current_a, source_range_a, setup_voltage_limit, measurement_count,
        external_start, start_timeout_s, event_poll_s, front_terminals,
        remote_sense, ready_line, busy_line, start_line, ready_asserted,
        busy_asserted, start_edge_mode, setup_profile)
    if measurement_profile == 4 then
        -- New 2 V / 1 NPLC candidate profile: configure and autozero only OFF.
        -- The unchanged v14 proof initializer never receives profile code 4.
        smu.measure.range = 2
        smu.source.vlimit.level = voltage_limit_v
        smu.measure.autozero.once()
        oe_m5_measurement_profile_code = 4
    end
    oe_timed_disable_timers()
    trigger.timer[2].reset()
    trigger.timer[2].delay = duration_s
    trigger.timer[2].start.stimulus = trigger.EVENT_NOTIFY4
    trigger.timer[2].start.generate = trigger.OFF
    trigger.timer[2].count = 1
    trigger.timer[2].enable = trigger.ON
    trigger.timer[3].reset()
    trigger.timer[3].delay = sample_period_s
    trigger.timer[3].start.stimulus = trigger.EVENT_NOTIFY4
    trigger.timer[3].start.generate = trigger.OFF
    trigger.timer[3].count = measurement_count
    trigger.timer[3].enable = trigger.ON
    local deadlines = {sample_period_s / 2}
    for index = 2, measurement_count do
        deadlines[index] = sample_period_s
    end
    trigger.timer[4].reset()
    trigger.timer[4].delaylist = deadlines
    trigger.timer[4].start.stimulus = trigger.EVENT_NOTIFY4
    trigger.timer[4].start.generate = trigger.OFF
    trigger.timer[4].count = measurement_count
    trigger.timer[4].enable = trigger.ON
    trigger.model.load("Empty")
    -- Build a contiguous graph for each mode; omit external-only entry/timeout
    -- blocks from immediate mode rather than leaving unreachable islands.
    local function block(logical)
        if logical == 0 or external_start == 1 then return logical end
        if logical >= 36 then return logical - 10 end
        return logical - 6
    end
    if external_start == 1 then
        trigger.model.setblock(block(1), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(true, false), oe_m4_mask())
        trigger.model.setblock(block(2), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY1)
        trigger.model.setblock(block(3), trigger.BLOCK_BRANCH_ON_EVENT, trigger.EVENT_TIMER1, block(32))
        trigger.model.setblock(block(4), trigger.BLOCK_BRANCH_ON_EVENT, oe_m4_digio_event(start_line), block(7))
        trigger.model.setblock(block(5), trigger.BLOCK_DELAY_CONSTANT, event_poll_s)
        trigger.model.setblock(block(6), trigger.BLOCK_BRANCH_ALWAYS, block(3))
    end
    trigger.model.setblock(block(7), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, true), oe_m4_mask())
    trigger.model.setblock(block(8), trigger.BLOCK_BUFFER_CLEAR, oe_m5_buffer)
    trigger.model.setblock(block(9), trigger.BLOCK_SOURCE_OUTPUT, smu.ON)
    trigger.model.setblock(block(10), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY4)
    -- Pending half-slot deadline before measurement means no catch-up sample.
    trigger.model.setblock(block(11), trigger.BLOCK_BRANCH_ON_EVENT, trigger.EVENT_TIMER4, block(36))
    trigger.model.setblock(block(12), trigger.BLOCK_MEASURE_DIGITIZE, oe_m5_buffer, 1)
    trigger.model.setblock(block(13), trigger.BLOCK_BRANCH_LIMIT_CONSTANT,
        trigger.LIMIT_OUTSIDE, lower_cutoff_v, upper_cutoff_v, block(24), block(12))
    trigger.model.setblock(block(14), trigger.BLOCK_BRANCH_ON_EVENT, trigger.EVENT_TIMER4, block(28))
    -- Consume this deadline only after proving measurement finished before it.
    trigger.model.setblock(block(15), trigger.BLOCK_WAIT, trigger.EVENT_TIMER4, trigger.CLEAR_NEVER)
    trigger.model.setblock(block(16), trigger.BLOCK_BRANCH_COUNTER, measurement_count, block(22))
    trigger.model.setblock(block(17), trigger.BLOCK_WAIT, trigger.EVENT_TIMER2, trigger.CLEAR_NEVER)
    trigger.model.setblock(block(18), trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
    trigger.model.setblock(block(19), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
    trigger.model.setblock(block(20), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY2)
    trigger.model.setblock(block(21), trigger.BLOCK_BRANCH_ALWAYS, block(0))
    trigger.model.setblock(block(22), trigger.BLOCK_WAIT, trigger.EVENT_TIMER3, trigger.CLEAR_NEVER)
    trigger.model.setblock(block(23), trigger.BLOCK_BRANCH_ALWAYS, block(11))
    trigger.model.setblock(block(24), trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
    trigger.model.setblock(block(25), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
    trigger.model.setblock(block(26), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY5)
    trigger.model.setblock(block(27), trigger.BLOCK_BRANCH_ALWAYS, block(0))
    trigger.model.setblock(block(28), trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
    trigger.model.setblock(block(29), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
    trigger.model.setblock(block(30), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY6)
    trigger.model.setblock(block(31), trigger.BLOCK_BRANCH_ALWAYS, block(0))
    if external_start == 1 then
        trigger.model.setblock(block(32), trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
        trigger.model.setblock(block(33), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
        trigger.model.setblock(block(34), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY3)
        trigger.model.setblock(block(35), trigger.BLOCK_BRANCH_ALWAYS, block(0))
    end
    trigger.model.setblock(block(36), trigger.BLOCK_SOURCE_OUTPUT, smu.OFF)
    trigger.model.setblock(block(37), trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false), oe_m4_mask())
    trigger.model.setblock(block(38), trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY7)
    trigger.model.setblock(block(39), trigger.BLOCK_BRANCH_ALWAYS, block(0))
    oe_timed_complete_block = block(20)
    oe_timed_timeout_block = 0
    if external_start == 1 then oe_timed_timeout_block = block(34) end
    oe_timed_cutoff_block = block(26)
    oe_timed_deadline_block = block(30)
    oe_timed_overrun_block = block(38)
    oe_timed_enabled = true
    oe_timed_cause = "prepared"
    oe_timed_count = measurement_count
    oe_timed_lower_cutoff = lower_cutoff_v
    oe_timed_upper_cutoff = upper_cutoff_v
    oe_m4_complete_block = oe_timed_complete_block
    oe_m4_timeout_block = oe_timed_timeout_block
    if external_start == 1 then
        oe_m4_mode = "external"
    else
        oe_m4_mode = "timed_immediate"
    end
    oe_m4_state = "prepared"
end
function oe_timed_plan_status()
    local deadlines = trigger.timer[4].delaylist
    return trigger.timer[2].delay, trigger.timer[3].delay, oe_timed_count,
        oe_timed_lower_cutoff, oe_timed_upper_cutoff, deadlines[1]
end
function oe_m4_refresh_state()
    local was_active = oe_m4_state == "running" or oe_m4_state == "waiting_start"
    local model_state, model_substate, block = oe_timed_base_refresh()
    if oe_timed_enabled and was_active then
        if model_state == trigger.STATE_IDLE then
            if block == oe_timed_complete_block then
                oe_timed_cause = "normal_completion"
            elseif oe_timed_timeout_block > 0 and block == oe_timed_timeout_block then
                oe_timed_cause = "start_timeout"
            elseif block == oe_timed_cutoff_block then
                oe_timed_cause = "voltage_cutoff"
                oe_m4_state = "error"
            elseif block == oe_timed_deadline_block then
                oe_timed_cause = "measurement_deadline"
                oe_m4_state = "error"
            elseif block == oe_timed_overrun_block then
                oe_timed_cause = "cadence_overrun"
                oe_m4_state = "error"
            else
                oe_timed_cause = "runtime_error"
            end
        elseif model_state == trigger.STATE_FAILED then
            oe_timed_cause = "runtime_error"
        elseif model_state == trigger.STATE_ABORTED then
            oe_timed_cause = "operator_abort"
        elseif oe_m4_mode ~= "external" or block >= 7 then
            oe_m4_state = "running"
            oe_timed_cause = "running"
            oe_m4_track_flags(false, true)
        end
    end
    -- Status inspection must not mutate timer hardware. All timers are finite;
    -- explicit abort/force-safe/recovery and preparation disable them as needed.
    return model_state, model_substate, block
end
function oe_timed_terminal_cause()
    oe_m4_refresh_state()
    return oe_timed_cause
end
function oe_m4_abort()
    oe_timed_disable_timers()
    oe_timed_base_abort()
    if oe_timed_enabled then oe_timed_cause = "operator_abort" end
end
function oe_m4_force_safe()
    oe_timed_disable_timers()
    oe_timed_base_force_safe()
    if oe_timed_enabled then oe_timed_cause = "operator_abort" end
end
function oe_m4_recover()
    -- Base recovery checks stopped/OFF before the extension changes timers.
    oe_timed_base_recover()
    oe_timed_disable_timers()
    trigger.model.load("Empty")
end
