"""Intended state vocabulary; transition logic is not implemented."""

from enum import StrEnum


class DeviceState(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    PREPARED = "prepared"
    ARMING = "arming"
    WAITING_START = "waiting_start"
    RUNNING = "running"
    COMPLETE = "complete"
    ABORTING = "aborting"
    ABORTED = "aborted"
    RECOVERING = "recovering"
    ERROR = "error"
