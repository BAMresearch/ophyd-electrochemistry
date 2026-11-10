-- Execute only the packaged extension against inert recorders, never hardware.
local blocks = {}
local function noop() end
local function constants(t, key) return key end
trigger = setmetatable({timer={}, model={}}, {__index=constants})
for i=1,4 do trigger.timer[i]={start={},reset=noop} end
trigger.model.load = function() blocks={} end
trigger.model.setblock = function(number, kind, ...)
    assert(blocks[number]==nil, "duplicate block")
    blocks[number]={kind=kind,parameters=arg}
end
smu={OFF=0,ON=1,FUNC_DC_CURRENT=2,FUNC_DC_VOLTAGE=3,source={output=0,vlimit={}},measure={autozero={once=noop}}}
oe_m4_initialize=noop
oe_m4_refresh_state=noop
oe_m4_abort=noop
oe_m4_force_safe=noop
oe_m4_recover=noop
oe_m4_prepare_current_hold=noop
oe_m4_model_active=function() return false end
oe_m4_require_number=function(value) assert(type(value)=="number") end
oe_m4_pattern=function() return 0 end
oe_m4_mask=function() return 3 end
oe_m4_digio_event=function() return "EVENT_DIGIO3" end
oe_m5_prepare_profiled_current_hold_acquisition=function() oe_m5_buffer={n=0} end
assert(loadfile(arg[1]))()
for mode=0,1 do
    oe_timed_prepare_current_hold(0.005,0.01,1,1,0.1,10,-1,1,mode,2,0.01,1,1,1,2,3,1,1,1,4)
    local count=29
    if mode==1 then count=39 end
    for i=1,count do assert(blocks[i], "missing block "..i) end
    assert(blocks[count+1]==nil)
    local seen={}
    local function visit(number)
        if number==0 or seen[number] then return end
        local b=blocks[number]
        assert(b,"branch to missing block "..number)
        seen[number]=true
        if b.kind==trigger.BLOCK_BRANCH_ALWAYS then
            visit(b.parameters[1])
        elseif b.kind==trigger.BLOCK_BRANCH_ON_EVENT or b.kind==trigger.BLOCK_BRANCH_COUNTER then
            visit(b.parameters[2]); visit(number+1)
        elseif b.kind==trigger.BLOCK_BRANCH_LIMIT_CONSTANT then
            assert(blocks[b.parameters[5]].kind==trigger.BLOCK_MEASURE_DIGITIZE)
            visit(b.parameters[4]);visit(number+1)
        else
            visit(number+1)
        end
    end
    visit(1)
    for i=1,count do assert(seen[i],"unreachable block "..i) end
    -- Each terminal outcome is preceded by an explicit local output-OFF block.
    local notifications={oe_timed_complete_block,oe_timed_cutoff_block,oe_timed_deadline_block,oe_timed_overrun_block}
    if mode==1 then table.insert(notifications,oe_timed_timeout_block) end
    for _,number in ipairs(notifications) do
        assert(blocks[number].kind==trigger.BLOCK_NOTIFY)
        assert(blocks[number-2].kind==trigger.BLOCK_SOURCE_OUTPUT)
        assert(blocks[number-2].parameters[1]==smu.OFF)
        assert(blocks[number+1].kind==trigger.BLOCK_BRANCH_ALWAYS)
        assert(blocks[number+1].parameters[1]==0)
    end
    assert(smu.source.output==smu.OFF)
    print("mode="..mode.." contiguous="..count.." all_reachable=true terminal_off_paths="..table.getn(notifications))
end
