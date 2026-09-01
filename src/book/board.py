from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from book.heat import enter_leave, occupancy
from book.models import SNAPSHOT_OK, Occupancy, RankList, Snapshot
from book.store import Store

CHANNEL_LABELS = {"male": "男频", "female": "女频"}
KIND_LABELS = {"read": "阅读榜", "new": "新书榜"}


@dataclass
class ListSummary:
    rank_list: RankList
    snapshot: Snapshot | None
    occupancy: tuple[Occupancy, ...]
    entered_count: int | None
    left_count: int | None
    missing: bool
    missing_reason: str | None
    top_metric_label: str | None
    top_metric_value: int | None


@dataclass
class DayBoard:
    snapshot_date: date
    halt_reason: str | None
    groups: list[tuple[str, list[ListSummary]]]


def channel_label(channel: str) -> str:
    return CHANNEL_LABELS.get(channel, channel)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def list_title(rank_list: RankList) -> str:
    return f"{channel_label(rank_list.channel)}{kind_label(rank_list.rank_kind)} · {rank_list.category}"


def format_metric(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 10000:
        text = f"{value / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return f"{value:,}"


def format_sync_status(snapshot_date: date, captured_at: datetime) -> str:
    stamp = captured_at.strftime("%H:%M")
    capture_date = captured_at.date()
    return f"{snapshot_date.isoformat()} 榜 · {capture_date.month}月{capture_date.day}日 {stamp} 采入"


def build_day_board(store: Store, snapshot_date: date) -> DayBoard:
    halt = store.get_halt("fanqie")
    summaries: list[ListSummary] = []
    for rank_list in store.list_rank_lists():
        snapshot = store.get_snapshot(rank_list.platform, rank_list.list_id, snapshot_date)
        missing = snapshot is None or snapshot.status != SNAPSHOT_OK
        occupancies: tuple[Occupancy, ...] = ()
        entered_count: int | None = None
        left_count: int | None = None
        top_metric_label = None
        top_metric_value = None
        if snapshot and snapshot.status == SNAPSHOT_OK:
            if rank_list.has_occupancy:
                occupancies = occupancy(snapshot.entries)
            previous = store.previous_ok_snapshot(rank_list.platform, rank_list.list_id, snapshot_date)
            movement = enter_leave(
                snapshot.work_ids,
                previous.work_ids if previous else None,
            )
            if movement is not None:
                entered_count = len(movement.entered_ids)
                left_count = len(movement.left_ids)
            if snapshot.entries:
                first = snapshot.entries[0]
                top_metric_label = first.metric_name
                top_metric_value = first.metric_value
        summaries.append(
            ListSummary(
                rank_list=rank_list,
                snapshot=snapshot,
                occupancy=occupancies,
                entered_count=entered_count,
                left_count=left_count,
                missing=missing,
                missing_reason=None if snapshot is None else snapshot.halt_reason,
                top_metric_label=top_metric_label,
                top_metric_value=top_metric_value,
            )
        )

    grouped: dict[str, list[ListSummary]] = {}
    order: list[str] = []
    for summary in summaries:
        key = f"{channel_label(summary.rank_list.channel)}{kind_label(summary.rank_list.rank_kind)}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(summary)
    groups = [(key, grouped[key]) for key in order]
    halt_reason = None
    if halt is not None and any(item.missing for item in summaries):
        halt_reason = halt.reason
    return DayBoard(
        snapshot_date=snapshot_date,
        halt_reason=halt_reason,
        groups=groups,
    )


def entry_movement(store: Store, snapshot: Snapshot) -> dict[str, str]:
    previous = store.previous_ok_snapshot(snapshot.platform, snapshot.list_id, snapshot.snapshot_date)
    movement = enter_leave(snapshot.work_ids, previous.work_ids if previous else None)
    marks: dict[str, str] = {}
    if movement is None:
        return marks
    for work_id in movement.entered_ids:
        marks[work_id] = "进"
    for work_id in movement.left_ids:
        marks[work_id] = "掉"
    return marks
