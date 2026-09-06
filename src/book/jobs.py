from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field

PHASE_PENDING = "pending"
PHASE_RUNNING = "running"
PHASE_DONE = "done"
PHASE_FAILED = "failed"


def buffer_lines(buffer: io.StringIO | None) -> list[str]:
    if buffer is None:
        return []
    return [line for line in buffer.getvalue().splitlines() if line]


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remainder = divmod(total, 60)
    return f"{minutes:02d}:{remainder:02d}"


@dataclass
class Phase:
    key: str
    label: str
    state: str = PHASE_PENDING
    note: str = ""
    done: int = 0
    total: int = 0


@dataclass
class TaskProgress:
    phases: list[Phase]
    started_at: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def begin(self, key: str, *, total: int = 0, note: str = "") -> None:
        with self._lock:
            phase = self._find(key)
            if phase is None:
                return
            phase.state = PHASE_RUNNING
            if total:
                phase.total = total
            if note:
                phase.note = note

    def add_total(self, key: str, count: int) -> None:
        with self._lock:
            phase = self._find(key)
            if phase is None:
                return
            phase.total += count

    def advance(self, key: str, *, step: int = 1, note: str = "") -> None:
        with self._lock:
            phase = self._find(key)
            if phase is None:
                return
            phase.done += step
            if note:
                phase.note = note

    def finish(self, key: str, *, note: str = "") -> None:
        with self._lock:
            phase = self._find(key)
            if phase is None:
                return
            phase.state = PHASE_DONE
            if phase.total:
                phase.done = max(phase.done, phase.total)
            if note:
                phase.note = note

    def fail(self, key: str, *, note: str = "") -> None:
        with self._lock:
            phase = self._find(key)
            if phase is None:
                return
            phase.state = PHASE_FAILED
            if note:
                phase.note = note

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "key": phase.key,
                    "label": phase.label,
                    "state": phase.state,
                    "note": phase.note,
                    "done": phase.done,
                    "total": phase.total,
                }
                for phase in self.phases
            ]

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def elapsed_label(self) -> str:
        return format_elapsed(self.elapsed_seconds())

    def _find(self, key: str) -> Phase | None:
        for phase in self.phases:
            if phase.key == key:
                return phase
        return None


def progress_payload(
    *,
    status: str,
    finished: bool,
    halted: str = "",
    progress: TaskProgress | None = None,
    lines: list[str] | None = None,
) -> dict:
    return {
        "status": status,
        "finished": finished,
        "halted": halted or "",
        "elapsed": int(progress.elapsed_seconds()) if progress else 0,
        "phases": progress.snapshot() if progress else [],
        "lines": list(lines or ()),
    }
