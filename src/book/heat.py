from __future__ import annotations

from collections import defaultdict

from book.models import EnterLeave, Occupancy, RankEntry


def occupancy(entries: tuple[RankEntry, ...] | list[RankEntry], top_n: int = 100) -> tuple[Occupancy, ...]:
    """占位只对总榜有意义：TopN 中各原生分类的条数和平均名次。"""
    ranked = [entry for entry in entries if 1 <= entry.rank <= top_n]
    grouped: dict[str, list[int]] = defaultdict(list)
    for entry in ranked:
        if entry.category:
            grouped[entry.category].append(entry.rank)
    occupancies = [
        Occupancy(
            category=category,
            count=len(ranks),
            average_rank=sum(ranks) / len(ranks),
        )
        for category, ranks in grouped.items()
    ]
    occupancies.sort(key=lambda item: (-item.count, item.average_rank, item.category))
    return tuple(occupancies)


def enter_leave(
    today_ids: frozenset[str] | set[str] | None,
    yesterday_ids: frozenset[str] | set[str] | None,
) -> EnterLeave | None:
    """没有上一日有效快照时返回 None，调用方不得把这显示成进出为零。"""
    if today_ids is None or yesterday_ids is None:
        return None
    entered = tuple(sorted(today_ids - yesterday_ids))
    left = tuple(sorted(yesterday_ids - today_ids))
    return EnterLeave(entered_ids=entered, left_ids=left)
