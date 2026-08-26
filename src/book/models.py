from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


PLATFORM_FANQIE = "fanqie"
RANK_KIND_READ = "read"
RANK_KIND_NEW = "new"
SNAPSHOT_OK = "ok"
SNAPSHOT_MISSING = "missing"

SERIAL_ONGOING = "连载"
SERIAL_FINISHED = "完结"

HEAD_METRIC_READERS = "在读"

MAX_ENTRIES_PER_LIST = 100


@dataclass(frozen=True)
class RankList:
    platform: str
    list_id: str
    channel: str
    rank_kind: str
    category: str
    has_occupancy: bool = False


@dataclass(frozen=True)
class RankEntry:
    rank: int
    work_id: str
    title: str
    author: str
    category: str
    serial_status: str | None = None
    metric_name: str | None = None
    metric_value: int | None = None
    updated_at_on_page: str | None = None


@dataclass
class Snapshot:
    platform: str
    list_id: str
    snapshot_date: date
    captured_at: datetime
    entries: tuple[RankEntry, ...] = field(default_factory=tuple)
    status: str = SNAPSHOT_OK
    halt_reason: str | None = None

    @property
    def work_ids(self) -> frozenset[str]:
        return frozenset(entry.work_id for entry in self.entries)


@dataclass(frozen=True)
class Occupancy:
    category: str
    count: int
    average_rank: float


@dataclass(frozen=True)
class EnterLeave:
    entered_ids: tuple[str, ...]
    left_ids: tuple[str, ...]


@dataclass(frozen=True)
class PlatformHalt:
    platform: str
    reason: str
    halted_at: datetime
