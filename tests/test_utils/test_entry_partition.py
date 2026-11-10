"""Tests for the CC Payback's flagged-vs-unflagged entry partition (deep-hunt #75).

``partition_entries`` is the single definition of "which purchases does the
CC Payback repay," read by ``entry_credit_workflow.sync_entry_payback``
(which needs the flagged list to size and link the CC Payback).
``entry_service.compute_entry_sums`` read it too until plan task plan#3143,
which splits the cell's "on CC" sum on ``movement_account.on_a_card``
instead.  This file pins the partition contract:

- an entry is a credit iff ``entry.is_credit`` is true;
- the return order is ``(debit_entries, credit_entries)``, mirroring
  ``compute_entry_sums``'s ``(sum_debit, sum_credit)`` convention;
- input order is preserved within each group.
"""
from collections import namedtuple

from app.utils.entry_partition import partition_entries

# The partition reads only ``is_credit``; ``eid`` lets the assertions name
# exactly which entries landed in which group and in what order.
_FakeEntry = namedtuple("_FakeEntry", ["eid", "is_credit"])


class TestPartitionEntries:
    """Pin the flagged-vs-unflagged partition the CC Payback is sized from."""

    def test_empty_list_returns_two_empty_lists(self):
        """No entries -> two empty groups (the no-op base case)."""
        debits, credits = partition_entries([])
        assert debits == []
        assert credits == []

    def test_mixed_entries_split_by_is_credit(self):
        """Each entry lands in exactly one group, keyed on is_credit."""
        d1 = _FakeEntry(1, False)
        c1 = _FakeEntry(2, True)
        d2 = _FakeEntry(3, False)
        c2 = _FakeEntry(4, True)
        debits, credits = partition_entries([d1, c1, d2, c2])
        # Debits are the non-credit entries; credits are the is_credit ones.
        assert debits == [d1, d2]
        assert credits == [c1, c2]

    def test_return_order_is_debits_then_credits(self):
        """The tuple is (debit_entries, credit_entries), not the reverse."""
        credit = _FakeEntry(1, True)
        debit = _FakeEntry(2, False)
        first, second = partition_entries([credit, debit])
        assert first == [debit]   # debits first
        assert second == [credit]  # credits second

    def test_all_credit(self):
        """All-credit input -> empty debit group, every entry in credit."""
        entries = [_FakeEntry(1, True), _FakeEntry(2, True)]
        debits, credits = partition_entries(entries)
        assert debits == []
        assert credits == entries

    def test_all_debit(self):
        """All-debit input -> every entry in debit, empty credit group."""
        entries = [_FakeEntry(1, False), _FakeEntry(2, False)]
        debits, credits = partition_entries(entries)
        assert debits == entries
        assert credits == []

    def test_input_order_preserved_within_groups(self):
        """Within each group the original relative order is kept."""
        entries = [
            _FakeEntry(1, False),
            _FakeEntry(2, False),
            _FakeEntry(3, True),
            _FakeEntry(4, True),
            _FakeEntry(5, False),
        ]
        debits, credits = partition_entries(entries)
        assert [e.eid for e in debits] == [1, 2, 5]
        assert [e.eid for e in credits] == [3, 4]
