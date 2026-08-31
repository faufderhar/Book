from __future__ import annotations

import unittest
from book.heat import enter_leave, occupancy
from book.models import Occupancy, RankEntry


def entry(rank: int, work_id: str, category: str = "都市") -> RankEntry:
    return RankEntry(
        rank=rank,
        work_id=work_id,
        title=work_id,
        author="甲",
        category=category,
    )


class OccupancyTest(unittest.TestCase):
    def test_counts_and_average_rank_on_total_list(self) -> None:
        occupancies = occupancy(
            [
                entry(1, "a", "都市"),
                entry(2, "b", "玄幻"),
                entry(3, "c", "都市"),
                entry(101, "d", "都市"),
            ],
            top_n=100,
        )
        self.assertEqual(
            occupancies,
            (
                Occupancy(category="都市", count=2, average_rank=2.0),
                Occupancy(category="玄幻", count=1, average_rank=2.0),
            ),
        )

    def test_skips_blank_category(self) -> None:
        occupancies = occupancy([entry(1, "a", ""), entry(2, "b", "都市")])
        self.assertEqual(occupancies, (Occupancy(category="都市", count=1, average_rank=2.0),))


class EnterLeaveTest(unittest.TestCase):
    def test_missing_yesterday_is_none_not_zero(self) -> None:
        self.assertIsNone(enter_leave(frozenset({"a"}), None))
        self.assertIsNone(enter_leave(None, frozenset({"a"})))

    def test_genuine_zero_movement(self) -> None:
        ids = frozenset({"a", "b"})
        movement = enter_leave(ids, ids)
        self.assertIsNotNone(movement)
        self.assertEqual(movement.entered_ids, ())
        self.assertEqual(movement.left_ids, ())

    def test_entered_and_left(self) -> None:
        movement = enter_leave(frozenset({"b", "c"}), frozenset({"a", "b"}))
        self.assertEqual(movement.entered_ids, ("c",))
        self.assertEqual(movement.left_ids, ("a",))


if __name__ == "__main__":
    unittest.main()
