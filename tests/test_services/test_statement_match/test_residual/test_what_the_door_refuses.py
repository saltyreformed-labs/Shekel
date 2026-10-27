"""What a group's difference door refuses: a figure the owner accepted that disagrees with its own,
a reprice by unticking, a correction of one row by its lines with no figure stated or onto a row
that cannot take one, a difference beside one half of a transfer, a pair in two directions, and
a figure this app cannot store.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.services import status_seam
from app.services.row_valuation import settled_figure

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_transaction,
    an_import,
)
from tests._test_helpers import resolved_amount

from ._helpers import _submit, _minted, _a_transfer, _a_cc_payback, _payroll


class TestTheFigureTheOwnerAcceptedIsReconciled:
    """Finding **N-336**'s lesson on the one number no per-row guard sees."""

    def test_a_difference_the_screen_got_WRONG_is_refused(
        self, app, db, seed_user,
    ):
        """The control that makes carrying the figure worth anything.

        A browser that sums wrong states a correction the per-row staleness
        guard cannot see -- every row agrees with what was reviewed, and the
        SUM is the thing that moved.
        """
        line, salary, allowance = _payroll(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="-1006.00",
            )

        assert "-1,006.00" in str(caught.value)
        assert "+0.05" in str(caught.value)
        assert not _minted(seed_user)
        assert salary.settled_on is None

    def test_a_SUB_CENT_figure_is_not_the_doors_own_and_is_refused(
        self, app, db, seed_user,
    ):
        """The half-cent hole a quantizer opened, closed at the door.

        The schema used to round ``0.054`` to ``0.05`` and hand the door a
        figure that agreed -- so the consent gate the whole design rests on had
        a tolerance, asymmetric and in the rounding mode
        ``app.utils.money`` forbids.  The reader passes it through now and this
        is what turns it away.  Found by adversarial design review 2026-08-23.
        """
        line, salary, allowance = _payroll(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.054",
            )

        assert "reviewed against a difference of +0.05" in str(caught.value)
        assert not _minted(seed_user)

    def test_a_difference_stated_where_there_is_NONE_is_refused(
        self, app, db, seed_user,
    ):
        """A screen that said `+0.05` over rows that now add up exactly.

        **Its own arm, because equality does NOT catch it**: the door's
        difference is ``0.00``, so the comparison below would read a submitted
        ``0.05`` as merely unequal and send the owner a sentence about a figure
        that "now comes to +0.00", which is not what happened.  What happened
        is that the rows moved under a page stating a correction, so *reload*
        is the remedy.  Two independent reviews measured a first version's
        docstring claiming the equality caught this at all.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.38")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.05",
            )

        assert "reviewed against a difference of +0.05" in str(caught.value)
        assert "now adds up exactly" in str(caught.value)
        assert not _minted(seed_user)
        assert salary.settled_on is None

    def test_a_ZERO_difference_stated_on_an_agreeing_group_is_RECORDED(
        self, app, db, seed_user,
    ):
        """``0.00`` is an ordinary value of the field, not a contradiction.

        **This case INVERTED at plan step ``bank_import:X-gj-1b``** (developer,
        2026-08-30).  It was refused until then, on the reading that consenting
        to a difference where there is none describes an act the door will not
        perform.  Deleting the consent gate's exempt shape made the field the
        thing that says *this is the figure I was shown*, and a match whose
        sides agree was shown a difference of nothing -- so an agreeing group
        states ``0.00`` and this door records it.  Every exact match the
        Reconcile page and the review queue submit is this shape.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.38")

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.00",
        )

        assert accepted.residual is None
        assert accepted.settled_count == 2
        # Nothing is written FROM a difference of nothing, which is why the
        # gate has nothing to ask about here.
        assert not _minted(seed_user)
        assert accepted.repriced_count == 0

    def test_an_agreeing_group_that_states_NOTHING_is_still_recorded(
        self, app, db, seed_user,
    ):
        """Scripting off: the panel never re-renders, so it states no figure.

        The gate turns on what THIS DOOR derives, not on what the submission
        looks like, and a zero difference writes nothing at all -- so
        requiring a figure here would have refused every exact match built with
        scripting off, removing a real capability to gate a write that does not
        happen.  The companion of the case above, and the reason the rule is
        *a difference this door would WRITE must have been reviewed* rather
        than *every match must carry a figure*.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.38")

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
        )

        assert accepted.residual is None
        assert accepted.settled_count == 2
        assert not _minted(seed_user)

    def test_ONE_row_takes_the_banks_figure_and_mints_NOTHING(
        self, app, db, seed_user,
    ):
        """The two remedies are exclusive, and this is the other one.

        One row apportions nothing, so the difference is written to it.  A
        first version of this step gated the mint on the owner's consent
        alone, so a one-row match carrying one did BOTH -- the row corrected
        AND the same difference booked again to Uncategorized.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-178.29",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        txn = a_transaction(seed_user, amount="178.32")

        # The bank took LESS than the row says, so the difference is POSITIVE
        # on the cash convention both sides use: -178.29 - (-178.32) = +0.03.
        accepted = _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert accepted.repriced_count == 1
        assert accepted.residual is None
        assert settled_figure(txn) == Decimal("178.29")
        assert not _minted(seed_user)

    def test_the_SAME_group_without_a_residual_still_refuses(
        self, app, db, seed_user,
    ):
        """The firing control for the whole arm: delete the consent and the
        act is refused exactly as it was before this step."""
        line, salary, allowance = _payroll(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(seed_user, lines=[line], transactions=[salary, allowance])

        assert "+0.05" in str(caught.value)
        assert not _minted(seed_user)


class TestUntickingAProposedRowCannotRepriceTheSurvivor:
    """The defect the developer's 2026-08-30 ruling deletes the shape for.

    A tier proposes one deposit against two of the owner's rows and the
    Reconcile card renders them ticked.  Untick one and what reaches this door
    is ONE line against ONE row -- byte-identical to the near tier's own
    offer -- so the exempt shape was manufacturable by a checkbox, and the
    survivor took the whole deposit with nothing asked.

    **Reproduced on a clone of the developer's production data before it was
    fixed** (2026-08-30, 137 proposals on his checking account): three carried
    more than one row, and the worst wrote `$2,572.36` -- his bank's
    `$2,611.90` payroll deposit onto a `$39.54` ``Phone Allowance`` row.  The
    exemption it travelled through existed for two proposals worth `-$0.15`
    and `-$0.04`.
    """

    def test_the_survivor_is_NOT_repriced_when_a_row_is_unticked(
        self, app, db, seed_user,
    ):
        """The money case.  Submitting one row of a proposed two is refused.

        Both halves are asserted: the refusal, and that the survivor's figure
        did not move.  A refusal alone would pass against a door that raised
        AFTER repricing, which is the ordering
        ``_accept.record_match`` states its whole gate sequence to keep.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.43")

        with pytest.raises(ValidationError) as caught:
            _submit(seed_user, lines=[line], transactions=[salary])

        # The gap the survivor would have absorbed: 2573.43 - 2473.38.
        assert "+100.05" in str(caught.value)
        assert status_seam.recorded_settlement(salary) is None
        assert salary.settled_on is None
        assert allowance.settled_on is None
        assert not _minted(seed_user)

    def test_it_IS_recorded_once_the_owner_states_the_new_figure(
        self, app, db, seed_user,
    ):
        """Deliberately taking one row of a proposal is still allowed.

        The ruling removes a shape from the ALLOWLIST, not an act from the
        owner: the Reconcile panel re-prices as rows are unticked and offers
        the new difference to agree to.  What is gone is doing it silently.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.43")

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary], residual="100.05",
        )

        assert accepted.repriced_count == 1
        assert settled_figure(salary) == Decimal("2573.43")
        assert allowance.settled_on is None


class TestOneRowIsDeterminateHoweverManyLinesExplainIt:
    """Developer's decision 2026-08-23, widening ``bank_cash_for``.

    Two lines against one row apportion nothing either: their SUM is what the
    bank says that row is worth.  Before this step such a match was refused as
    a group, and once a group's difference became recordable it would have
    minted a residual beside a row the bank had just priced exactly.
    """

    def test_two_lines_CORRECT_the_one_row_they_name(
        self, app, db, seed_user,
    ):
        """The row takes the lines' total; nothing is minted."""
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=1),
        )
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(
            seed_user, lines=[line_a, line_b], transactions=[txn],
            residual="-0.06",
        )

        assert accepted.repriced_count == 1
        assert accepted.residual is None
        assert settled_figure(txn) == Decimal("180.06")
        assert not _minted(seed_user)

    def test_two_lines_with_NO_accepted_figure_are_refused(
        self, app, db, seed_user,
    ):
        """The widening needed a consent gate and a first version had none.

        Nothing bounds which lines an owner may tick, so without one a mis-tick
        rewrites a row's amount to an arbitrary sum in a single press with no
        undo.  Only the near tier's single-line one-row shape is exempt.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=1),
        )
        txn = a_transaction(seed_user, amount="180.00")

        with pytest.raises(ValidationError) as caught:
            _submit(seed_user, lines=[line_a, line_b], transactions=[txn])

        assert "-0.06" in str(caught.value)
        assert status_seam.recorded_settlement(txn) is None

    def test_ONE_line_against_ONE_row_needs_THE_FIGURE_TOO(
        self, app, db, seed_user,
    ):
        """The shape that was exempt until 2026-08-30, and no longer is.

        **This case INVERTED at plan step ``bank_import:X-gj-1b``.**  The
        exemption existed because the near tier's tick carried no difference
        field and its card stated the correction, so the SHAPE was read as a
        proxy for *the screen already disclosed this*.  The proxy is what the
        Reconcile page's tickable proposal rows learned to manufacture --
        untick one row of a proposed group and this is exactly what arrives --
        so the tier states its figure on the wire now
        (the ``stated_difference`` filter) and no shape is exempt.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-178.29",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        txn = a_transaction(seed_user, amount="178.32")

        with pytest.raises(ValidationError) as caught:
            _submit(seed_user, lines=[line], transactions=[txn])

        assert "+0.03" in str(caught.value)
        assert status_seam.recorded_settlement(txn) is None

    def test_ONE_line_against_ONE_row_records_when_it_STATES_the_figure(
        self, app, db, seed_user,
    ):
        """The near tier's own act, still one press once the figure travels.

        The half that proves the inversion above cost the tier nothing: what
        changed is that the correction is on the wire, not that it is refused.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-178.29",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        txn = a_transaction(seed_user, amount="178.32")

        accepted = _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert accepted.repriced_count == 1
        assert settled_figure(txn) == Decimal("178.29")

    def test_a_TRANSFER_LEG_in_a_group_refuses_the_difference(
        self, app, db, seed_user,
    ):
        """A transfer's one figure, carried into the group arm.

        A leg is one half of its transfer and moves the transfer's one
        figure, so correcting it means correcting the TRANSFER -- and
        recording the gap beside it as an uncategorized row would book money
        the transfer already accounts for.  A first version of this step
        asked this only of a LONE row, so ticking one extra turned the
        refusal into a residual.  The transfer was reached through its
        checking shadow until plan step ``balance:X-bi-6-4d-2``, and the case
        was named for that shadow.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.06", posted_on=bank_day,
        )
        transfer = _a_transfer(seed_user, amount="100.00")
        other = a_transaction(seed_user, name="Power", amount="80.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[other],
                transfers=[transfer], residual="-0.06",
            )

        assert "one half of a transfer" in str(caught.value)
        assert not _minted(seed_user)

    def test_two_lines_against_a_row_that_CANNOT_be_corrected_still_refuse(
        self, app, db, seed_user,
    ):
        """The widening moved the SHAPE test, not the row refusals.

        The uncorrectable row is a CC payback (:func:`_a_cc_payback`): through
        plan step ``balance:X-bi-4a``'s first cut it was an envelope holding
        a purchase, which ruling **R-BAL81** keeps out of the offer
        altogether.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=1),
        )
        payback = _a_cc_payback(seed_user, amount="180.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line_a, line_b], transactions=[payback],
            )

        assert "no figure of its own" in str(caught.value)
        assert payback.settled_on is None
        assert not _minted(seed_user)


class TestThePairMustBeTheSameDirection:
    """The sign check, which a GROUP could not reach until this step.

    It was short-circuited by the group's own refusal, and that stopped being
    harmless the moment a group's difference became something this door
    WRITES: a `-100.00` line against a `+50.00` row would otherwise have
    recorded `-150.00` of spending nobody claimed.
    """

    def test_a_GROUP_whose_sides_oppose_is_refused(self, app, db, seed_user):
        """Two income rows against a debit line."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-100.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        one = a_transaction(
            seed_user, name="Refund", amount="30.00", income=True,
        )
        two = a_transaction(
            seed_user, name="Rebate", amount="20.00", income=True,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[one, two],
                residual="-150.00",
            )

        assert "not the same movement" in str(caught.value)
        assert not _minted(seed_user)

    def test_a_ZERO_side_opposes_NOTHING(self, app, db, seed_user):
        """A side with no money in it has no direction.

        A bare ``(bank < 0) != (app < 0)`` reads zero as "money in", so rows
        netting to nothing opposed a DEBIT line and not a CREDIT one -- the
        same submission refused or accepted according to the bank's sign.
        Found by adversarial security review 2026-08-23; graded on the arm
        that was wrongly refusing, because the other one already passed.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-500.00", posted_on=bank_day,
        )
        out = a_transaction(seed_user, name="Power", amount="30.00")
        back = a_transaction(
            seed_user, name="Refund", amount="30.00", income=True,
        )

        # The rows net to 0.00, so the whole line is the difference.
        accepted = _submit(
            seed_user, lines=[line], transactions=[out, back],
            residual="-500.00",
        )

        assert accepted.residual == Decimal("-500.00")
        assert resolved_amount(_minted(seed_user)[0]) == Decimal("500.00")

    def test_MIXED_signs_inside_a_group_stay_legal(self, app, db, seed_user):
        """A net deposit really is a gross income row less a deduction, so
        the check is over the two SUMS and never over a member."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2400.05",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        gross = a_transaction(
            seed_user, name="Gross Pay", amount="2500.00", income=True,
        )
        deduction = a_transaction(
            seed_user, name="Union Dues", amount="100.00",
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[gross, deduction],
            residual="0.05",
        )

        assert accepted.residual == Decimal("0.05")
        assert len(_minted(seed_user)) == 1


class TestAFigureThisAppCannotStoreIsRefused:
    """A door that SUMS is not bounded by the columns it sums.

    Every figure descends from a ``Numeric(12, 2)`` column, but a match may
    name ANY NUMBER of them per side -- plan step ``bank_import:X-go`` deleted
    the schema's member cap, so this refusal is the only bound on that SUM --
    and the total, and the difference derived from it, can leave the domain any
    one of them lives in.  Reaching the
    database with one is ``NumericValueOutOfRange``, which is not a
    ``ValidationError`` and therefore escapes the per-item SAVEPOINT: it kills
    the WHOLE pass and every item that had landed beside it.  Reproduced by
    adversarial security review 2026-08-23.
    """

    @staticmethod
    def _at_the_ceiling(seed_user, count):
        """Stage *count* bank lines each at the money columns' maximum.

        Args:
            seed_user: The seeded user bundle.
            count: How many to stage.

        Returns:
            The lines.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        return [
            a_bank_line(
                seed_user, statement, amount="-9999999999.99",
                posted_on=bank_day, sequence_in_group=index,
            )
            for index in range(count)
        ]

    def test_a_sum_past_the_column_is_a_REFUSAL_and_not_a_500(
        self, app, db, seed_user,
    ):
        """Three lines at the ceiling, against one row.

        Staged as OUTFLOWS against an expense row, so the sign check passes
        and this arm is what fires.  Without the bound the sum reaches
        the record's figure as ``-29,999,999,999.97`` and dies at the database
        -- an unhandled 500 that takes every other item in the pass with it.
        (The column was the row's own ``settled_amount`` through plan step
        ``balance:X-bi-4b-1``; the covering movement's ``amount`` is
        ``Numeric(12, 2)`` too.)
        """
        lines = self._at_the_ceiling(seed_user, 3)
        txn = a_transaction(seed_user, amount="1.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=lines, transactions=[txn],
                residual="-29999999997.97",
            )

        assert "larger than this app can record" in str(caught.value)
        assert status_seam.recorded_settlement(txn) is None

    def test_a_DIFFERENCE_past_the_column_is_refused_too(
        self, app, db, seed_user,
    ):
        """The other write the bound protects: the row this door mints.

        The bank side alone fits here; it is what the group leaves over that
        does not, so a bound on the sums alone would let this through.
        """
        lines = self._at_the_ceiling(seed_user, 2)
        one = a_transaction(seed_user, name="Power", amount="1.00")
        two = a_transaction(seed_user, name="Water", amount="1.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=lines, transactions=[one, two],
                residual="-19999999997.98",
            )

        assert "larger than this app can record" in str(caught.value)
        assert not _minted(seed_user)
