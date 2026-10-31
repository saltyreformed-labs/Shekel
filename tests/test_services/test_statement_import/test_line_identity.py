"""A line's identity: what the bank stated, and the surrogate the app mints.

Plan steps **bank_import:X-f6a-1** and **X-f6a-4**.  Ruling **R-FP** named
``FITID`` as the idempotency key; measurement 2026-08-16 refined that to a
POSITIONAL key -- ``(account, posted_on, amount, sequence within that group)``
-- because only some sources carry an id of their own, and across two SECU
exports twelve days apart the positional key reproduced the ``FITID`` key
exactly over 342 shared lines.

**The ordinal is the part that is not obvious, and it carries money in BOTH
directions.**  Without it, a second genuinely distinct charge sharing a day and
an amount -- the same coffee twice -- would be recognised as a duplicate of the
first and silently dropped: money the bank took and the app never recorded, on
the very mechanism built to prevent double-recording.

**And COMPARING against it is the defect X-f6a-4 removes.**  Three of the key's
four terms are facts the bank stated; the ordinal is not, and a re-import that
asked "what sits at this ordinal" refused a whole file on two events the bank
had not restated at all.  Both are reproduced here as firing controls, against
the pairing that now decides:

* a re-ordered pair of same-day same-amount lines -- the bank lists a day's
  card debits by ascending magnitude and ties have no stated tiebreak;
* a genuinely NEW line the bank INSERTED ahead of a recorded one.  The
  insertion behaviour is OBSERVED rather than hypothetical: it is why
  ``_plan._refuse_restatement`` stopped comparing running balances.

What must still refuse is a genuine restatement, and that is graded here too.
"""

from datetime import date
from decimal import Decimal

from app.services.statement_import import (
    GroupPairing,
    StatementLine,
    fresh_ordinals,
    group_indexes,
    group_key,
    pair_by_statement,
)


def _line(day, amount, description="X", running=None):
    """Return one :class:`StatementLine` with the fields identity reads."""
    return StatementLine(
        posted_on=day,
        transaction_on=day,
        amount=Decimal(amount),
        description=description,
        running_balance=None if running is None else Decimal(running),
    )


class TestTheGroupIsWhatTheBankStated:
    """A group is a day and an amount -- the key minus its surrogate half."""

    def test_lines_sharing_day_and_amount_are_one_group(self):
        """The unit a re-import reconciles as a set."""
        lines = [
            _line(date(2026, 3, 2), "-4.75", "COFFEE"),
            _line(date(2026, 3, 2), "-4.75", "TEA"),
        ]

        assert group_indexes(lines) == {
            (date(2026, 3, 2), Decimal("-4.75")): [0, 1],
        }

    def test_lines_differing_in_amount_are_two_groups(self):
        """A group is keyed on the amount, not on the day alone."""
        lines = [
            _line(date(2026, 3, 2), "-4.75"),
            _line(date(2026, 3, 2), "-9.50"),
            _line(date(2026, 3, 2), "-4.75"),
        ]

        assert group_indexes(lines) == {
            (date(2026, 3, 2), Decimal("-4.75")): [0, 2],
            (date(2026, 3, 2), Decimal("-9.50")): [1],
        }

    def test_lines_differing_in_day_are_two_groups(self):
        """Same amount on different days is two groups, not one."""
        lines = [
            _line(date(2026, 3, 2), "-4.75"),
            _line(date(2026, 3, 3), "-4.75"),
        ]

        assert len(group_indexes(lines)) == 2

    def test_it_keeps_each_group_in_the_files_own_order(self):
        """The indexes are what let fresh lines be written back in file order."""
        lines = [
            _line(date(2026, 3, 2), "-4.75", "FIRST"),
            _line(date(2026, 3, 3), "-1.00", "OTHER"),
            _line(date(2026, 3, 2), "-4.75", "SECOND"),
        ]

        assert group_indexes(lines)[
            (date(2026, 3, 2), Decimal("-4.75"))
        ] == [0, 2]

    def test_an_empty_file_groups_to_nothing(self):
        """Total over the empty input rather than raising on it."""
        assert group_indexes([]) == {}

    def test_two_equal_decimals_bucket_together_without_normalising(self):
        """Both sides of a reconciliation land in the same bucket already.

        The incoming amount is a ``Decimal`` the parser built from the file's
        text; the recorded one comes back from a ``Numeric(12, 2)`` column.  A
        key that bucketed them separately would call every recorded line
        unaccounted for and every incoming line new, turning one re-import into
        a duplicate of the whole span.

        **It holds without a normalising wrapper, which is why there is not
        one.**  Decimal's equality is numeric and its hash follows, so the two
        spellings are one dict key -- and a first version wrapped the amount in
        ``Decimal(str(...))`` to "make" that true, which changed nothing and
        would have laundered a float into a plausible wrong value.  This case
        asserts the property the code relies on rather than the wrapper it does
        not have; it cannot fire on the wrapper's absence, and that is the
        point.
        """
        bucketed = {group_key(date(2026, 3, 2), Decimal("-4.7500")): "a"}

        assert group_key(date(2026, 3, 2), Decimal("-4.75")) in bucketed

    def test_the_group_excludes_the_description(self):
        """A richer description of the SAME line must not re-group it.

        Measured 2026-08-16: SECU's OFX truncates a description to 32
        characters where its CSV carries 96, and the CSV text starts with the
        OFX text on 306 of 306 shared lines.  The wording decides which MEMBER
        of a group a line is; it does not decide which group.
        """
        lines = [
            _line(date(2026, 3, 2), "-4.75", "POINT OF SALE DEBIT L340 DATE 12"),
            _line(date(2026, 3, 2), "-4.75",
                  "POINT OF SALE DEBIT L340 DATE 12-31 Amazon.com (Amazon)"),
        ]

        assert len(group_indexes(lines)) == 1


class TestAReImportPairsOnWhatTheBankWrote:
    """The two events a positional compare got wrong, and the one it got right."""

    def test_a_reordered_pair_is_entirely_already_held(self):
        """FIRING CONTROL: the bank lists one day's two equal debits either way.

        Measured against the shipped positional code 2026-08-20: this refused
        the whole file -- thirty days of genuinely new lines with it -- and
        told the owner the bank had restated a line it had not.
        """
        pairing = pair_by_statement(
            incoming=["DUNKIN", "STARBUCKS"],
            recorded=["STARBUCKS", "DUNKIN"],
        )

        assert pairing.fresh == ()
        assert pairing.unclaimed == ()
        assert pairing.restates is False
        assert sorted(pairing.held) == [(0, 1), (1, 0)]

    def test_a_new_line_inserted_FIRST_is_the_only_fresh_one(self):
        """FIRING CONTROL: a swipe finalizes onto an already-recorded day.

        The bank INSERTS it into that day's block rather than appending, so the
        new line arrives where ordinal 0 is recorded.  The positional compare
        refused the whole file for a line that had never been recorded;
        pairing on the wording records exactly the one new line.
        """
        pairing = pair_by_statement(
            incoming=["DUNKIN", "STARBUCKS"], recorded=["STARBUCKS"],
        )

        assert pairing.fresh == (0,)
        assert pairing.held == ((1, 0),)
        assert pairing.unclaimed == ()
        assert pairing.restates is False

    def test_a_genuine_restatement_still_refuses(self):
        """The event the refusal was DESIGNED for is untouched.

        The app holds a line the file no longer states, and the file states one
        at the same day and amount the app cannot account for.  That is the
        bank re-wording an observation, which ruling R-FL refuses to absorb.
        """
        pairing = pair_by_statement(
            incoming=["GROCERY OUTLET"], recorded=["STARBUCKS"],
        )

        assert pairing.restates is True
        assert pairing.fresh == (0,)
        assert pairing.unclaimed == (0,)

    def test_a_restatement_inside_a_larger_group_still_refuses(self):
        """One member re-worded does not hide behind the members that pair."""
        pairing = pair_by_statement(
            incoming=["STARBUCKS", "GROCERY OUTLET"],
            recorded=["STARBUCKS", "DUNKIN"],
        )

        assert pairing.held == ((0, 0),)
        assert pairing.restates is True


class TestNeitherHalfAloneIsAContradiction:
    """A shorter export and a longer one are both ordinary events."""

    def test_a_file_that_omits_a_recorded_line_is_not_a_restatement(self):
        """An export covering less than the app holds states nothing false.

        It is also how a DISAPPEARANCE would look, and nothing here can tell
        the two apart -- finding **N-301** owns that.  This case is what says
        the door does not guess at it either.
        """
        pairing = pair_by_statement(
            incoming=["STARBUCKS"], recorded=["STARBUCKS", "DUNKIN"],
        )

        assert pairing.unclaimed == (1,)
        assert pairing.fresh == ()
        assert pairing.restates is False

    def test_a_file_with_only_new_lines_is_not_a_restatement(self):
        """A first sighting of a group is every line in it."""
        pairing = pair_by_statement(incoming=["A", "B"], recorded=[])

        assert pairing.fresh == (0, 1)
        assert pairing.unclaimed == ()
        assert pairing.restates is False

    def test_an_empty_incoming_group_claims_nothing(self):
        """Total on the empty side rather than raising on it."""
        pairing = pair_by_statement(incoming=[], recorded=["A"])

        assert pairing == GroupPairing(held=(), fresh=(), unclaimed=(0,))
        assert pairing.restates is False


class TestIdenticalWordingsArePairedByCOUNT:
    """The same coffee twice at the same shop is two lines with one wording."""

    def test_two_identical_lines_pair_with_two_recorded_ones(self):
        """Neither is a duplicate of the other and neither is new."""
        pairing = pair_by_statement(
            incoming=["COFFEE", "COFFEE"], recorded=["COFFEE", "COFFEE"],
        )

        assert sorted(pairing.held) == [(0, 0), (1, 1)]
        assert pairing.fresh == ()
        assert pairing.unclaimed == ()

    def test_a_second_identical_charge_is_recorded_rather_than_dropped(self):
        """MONEY: the second $4.75 coffee is real money the bank took.

        A pairing that ignored multiplicity would call it a duplicate of the
        first and drop it -- the exact loss the ordinal exists to prevent,
        re-introduced one layer up.
        """
        pairing = pair_by_statement(
            incoming=["COFFEE", "COFFEE"], recorded=["COFFEE"],
        )

        assert pairing.held == ((0, 0),)
        assert pairing.fresh == (1,)
        assert pairing.restates is False

    def test_a_dropped_duplicate_is_not_a_restatement(self):
        """One of two identical lines vanishing states nothing false."""
        pairing = pair_by_statement(
            incoming=["COFFEE"], recorded=["COFFEE", "COFFEE"],
        )

        assert pairing.fresh == ()
        assert len(pairing.unclaimed) == 1
        assert pairing.restates is False


class TestTheOrdinalIsMintedAndNeverReused:
    """A surrogate owes a distinct, stable address and nothing else."""

    def test_a_first_import_counts_from_zero(self):
        """Nothing recorded means the group's first member is ordinal 0."""
        assert fresh_ordinals([], 3) == [0, 1, 2]

    def test_a_single_line_is_ordinal_zero(self):
        """The ordinary case pays nothing for the ordinal's existence."""
        assert fresh_ordinals([], 1) == [0]

    def test_it_counts_above_what_the_group_already_holds(self):
        """A new line the bank listed FIRST is still the group's NEXT member."""
        assert fresh_ordinals([0, 1], 1) == [2]

    def test_it_does_NOT_fill_an_INTERIOR_gap_a_deleted_line_left(self):
        """Counting above the maximum leaves a hole in the middle alone.

        Plan step X-f6a-4 gives an import a delete door, so a group can hold
        ``[0, 2]`` with 1 freed.
        """
        assert fresh_ordinals([0, 2], 2) == [3, 4]

    def test_it_DOES_reuse_a_gap_at_the_TOP_of_the_range(self):
        """The honest bound: an ordinal free of every SURVIVING member.

        **This pins what the code does, and a first version claimed the
        opposite.**  The docstring said a freed ordinal is never re-used, with
        only the interior case above to hold it -- and the top-of-range gap is
        the ORDINARY shape, because it is what undoing the most recent import
        leaves.  Measured 2026-08-20: ``[0]`` mints ``1``, the address the
        deleted line held.

        Nothing depends on the stronger claim, which is why the claim went
        rather than the code: the ordinal addresses a row WITHIN its group, and
        everything that cites a row from outside -- ``system.audit_log.row_id``,
        every foreign key -- cites the primary key, which a sequence never
        re-uses.  Found by adversarial design review 2026-08-20.
        """
        assert fresh_ordinals([0], 1) == [1]

    def test_it_mints_nothing_when_nothing_is_fresh(self):
        """A whole group already held mints no ordinal at all."""
        assert fresh_ordinals([0, 1], 0) == []

    def test_it_reads_an_ITERATOR_rather_than_requiring_a_sequence(self):
        """The caller passes a generator over the recorded rows."""
        assert fresh_ordinals((n for n in [4, 2]), 1) == [5]
