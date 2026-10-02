"""Transport interface; no PyVISA session or instrument command is implemented."""

from typing import Protocol


class Keithley2460Transport(Protocol):
    def connect(self) -> None:
        """Inspect identity/mode; never reset or energize implicitly."""
        ...

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str:
        """Hold transaction lock across write and the complete bounded response."""
        ...

    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None:
        """Load reviewed packaged runtime with output OFF; verify ABI/digest."""
        ...

    def close(self) -> None:
        """Release session; operational device confirms shutdown before close."""
        ...
