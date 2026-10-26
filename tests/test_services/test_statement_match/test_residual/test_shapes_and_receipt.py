"""The members a group can take, the act's own writes that cannot move a member under it, the
receipt naming the minted row, and correcting a purchase onto the bank's figure.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum, SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import statement_match
from app.services.pay_calendar import calendar_for
from app.services.statement_match._valuation import purchase_candidate
from app.services.statement_match._landing import corrected_figure

from tests.test_services.test_statement_match._builders import (
    accepted_acts,
    a_bank_line,
    a_later_period,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)
from tests._test_helpers import last_covered_day, resolved_amount
from app.models.amount_ownership import AmountOwnership

from ._helpers import _submit, _minted, _uncategorized_net, _payroll


class TestTheSHAPESAGroupCanTake:
    """Members this arc's other row kinds contribute, and the name's edges.

    Every case here is one an adversarial mutation sweep found ungraded on
    2026-08-23: the tie-break between two same-day lines, the name's own
    length bound, a PURCHASE member, and a group straddling a pay period.
    """

    def test_a_PURCHASE_can_be_a_member_of_a_group_with_a_difference(
        self, app, db, seed_user,
    ):
        """The other matchable row kind, which no residual case had named.

        A purchase settles through a different verb from a transaction
        (``entry_service.update_entry``), so a group holding one exercises a
        path the transaction-only cases cannot.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-100.06", posted_on=bank_day,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="60.00", is_envelope=True,
        )
        # A purchase stores a positive figure; its CASH effect is the
        # negation (``_valuation.purchase_candidate``), so this contributes
        # -60.00 to the app side.
        purchase = a_purchase(
            seed_user, envelope, amount="60.00", purchased_on=bank_day,
        )
        other = a_transaction(seed_user, name="Power", amount="40.00")

        accepted = _submit(
            seed_user, lines=[line], transactions=[other],
            entries=[purchase], residual="-0.06",
        )

        assert accepted.residual == Decimal("-0.06")
        assert resolved_amount(_minted(seed_user)[0]) == Decimal("0.06")
        assert purchase.settled_on == bank_day

    def test_the_name_is_CUT_to_what_the_column_holds(
        self, app, db, seed_user,
    ):
        """``transactions.name`` is 200 characters and this door writes it.

        **Reached through the DESCRIPTION rather than the merchant**, and the
        difference is the point: ``bank_statement_lines.merchant`` is 100
        characters, so 22 + 100 always fits -- but ``merchant_label`` falls
        back to the 200-character description for a source that names no
        merchant, and 22 + 200 does not.  A test that reached for the merchant
        would have graded an unreachable branch and called the cut covered.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
            description="Statement of record " + "X" * 180, merchant=None,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        name = _minted(seed_user)[0].name
        assert len(name) == 200
        assert name.startswith("Statement difference: Statement of record XXX")

    def test_two_lines_on_ONE_day_break_the_tie_by_id(
        self, app, db, seed_user,
    ):
        """The name may not depend on which row a query happened to return.

        ``max`` over the posted day alone is satisfied by either line, so
        without the id the row's caption would change without the record
        changing.  Every other multi-line case here uses different days, which
        is why a sweep found this ungraded.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        first = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
            description="POINT OF SALE DEBIT LOWES", merchant="Lowe's",
        )
        second = a_bank_line(
            seed_user, statement, amount="-80.06", posted_on=bank_day,
            description="POINT OF SALE DEBIT WALMART", merchant="Walmart",
        )
        assert second.id > first.id, "the fixture must stage a real tie-break"
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        _submit(
            seed_user, lines=[first, second], transactions=[one, two],
            residual="-0.06",
        )

        assert _minted(seed_user)[0].name == "Statement difference: Walmart"

    def test_a_group_STRADDLING_a_period_lands_by_the_day_it_POSTS_on(
        self, app, db, seed_user,
    ):
        """The match's own day decides, and the two candidates differ here.

        A residual has no budget clock -- it IS the movement -- so it belongs
        to the paycheck holding the day the match posts on, which is the
        LATEST of its lines.  Staged across a period boundary so that
        ``happened_on`` (the earliest stated day) would file it in the other
        paycheck, which is the mutation a sweep found nothing catching.
        """
        later = a_later_period(seed_user)
        statement = an_import(seed_user)
        earlier = a_bank_line(
            seed_user, statement, amount="-100.00",
            posted_on=last_covered_day(seed_user["bootstrap_period"]),
        )
        crossing = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=later.start_date,
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        accepted = _submit(
            seed_user, lines=[earlier, crossing], transactions=[one, two],
            residual="-0.06",
        )

        assert accepted.posts_on == later.start_date
        assert _minted(seed_user)[0].pay_period_id == later.id


class TestTheACTS_OWN_WRITES_CannotMoveAMemberUnderIt:
    """The identity holds BY CONSTRUCTION, and this is what makes that a fact.

    Both adversarial reviews of 2026-08-23 found the same hole independently:
    the two sides are measured before any settle verb runs, and settling a
    matched PURCHASE re-derives a SIBLING CC Payback's ``estimated_amount``
    through ``sync_entry_payback`` -- a row ``_reject_parent_and_its_own_purchase``
    could not see, because it is the purchase's sibling under one envelope
    rather than its parent (that guard is deleted since plan step
    ``credit_card:CC-5-4a-5``, leaf 5c-2c-1: finding **CC-386**, ruling
    **R-CC144**).  A first version of this step carried a comment asserting
    no settle verb could do that, which is the claim ``_scope`` and
    ``_resolve`` both record as MEASURED FALSE one tier up.
    """

    @staticmethod
    def _drifted_payback(seed_user):
        """Stage an envelope, a card entry, its drifted payback, and a debit.

        **Built the way ``credit_workflow._create_payback`` builds one**:
        ``ck_transactions_one_pricing_link`` admits at most one of
        ``template_id`` / ``transfer_id`` / ``credit_payback_for_id``, so a
        payback carrying a template is a row the database refuses -- and a
        fixture the app could not produce would grade an unreachable case.

        Args:
            seed_user: The seeded user bundle.

        Returns:
            ``(debit purchase, payback)``.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="50.00", description="Card",
            purchased_on=day, is_credit=True,
        )
        debit = a_purchase(
            seed_user, envelope, amount="25.00", description="Aldi",
            purchased_on=day,
        )
        payback = Transaction(
            account_id=seed_user["account"].id,
            template_id=None,
            user_id=seed_user['bootstrap_period'].user_id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            # DRIFTED off the sum of its card entries, which is what an owner
            # correcting a projected payback to their card statement produces.
            # Settling the debit purchase is what snaps it back to 50.00.
            amount_ownership=AmountOwnership.own(Decimal("60.00")),
            credit_payback_for_id=envelope.id,
        )
        db.session.add(payback)
        db.session.flush()
        return debit, payback

    def test_a_group_whose_own_settle_moves_a_SIBLING_is_refused(
        self, app, db, seed_user,
    ):
        """One match naming both halves of the interaction.

        At submit time the two sides agree to the cent: `-25.00` of purchase
        plus `-60.00` of payback against an `-85.00` line.  Settling the
        purchase rewrites the payback to `-50.00`, so the members come to
        `-75.00` and the match no longer explains the line it named.  Without
        the post-write check this records a group that does not add up --
        silently, because every refusal it was tested against had already
        passed.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        debit, payback = self._drifted_payback(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-85.00", posted_on=day,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[payback],
                entries=[debit],
            )

        assert "moved one of its own rows" in str(caught.value)
        assert "-85.00" in str(caught.value)
        # Nothing survives the refusal: no match, and no minted row.
        assert not accepted_acts(seed_user)
        assert not _minted(seed_user)

    def test_the_SAME_group_without_the_sibling_still_lands(
        self, app, db, seed_user,
    ):
        """The control: the check refuses drift, not groups.

        Without it, a check that refused everything would pass the case above
        and be indistinguishable from one that works.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        debit, _ = self._drifted_payback(seed_user)
        other = a_transaction(seed_user, name="Power", amount="60.00")
        line = a_bank_line(
            seed_user, statement, amount="-85.00", posted_on=day,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[other], entries=[debit],
        )

        assert accepted.residual is None
        assert accepted.settled_count == 2


class TestTheReceiptNamesIt:
    """A receipt silent about a row this act CREATED would be false.

    The same argument ``repriced_count`` was added under 2026-08-22, when the
    panel said *"Nothing moved."* over a rewritten figure.
    """

    def test_the_minted_row_is_NOT_counted_as_settled(
        self, app, db, seed_user,
    ):
        """It is born on the bank's day, so counting it as a row the bank's
        evidence settled would claim work on a record that did not exist."""
        line, salary, allowance = _payroll(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert accepted.settled_count == 2

    def test_a_pass_that_ONLY_records_a_difference_did_not_move_nothing(
        self, app, db, seed_user,
    ):
        """``moved_nothing`` decides the panel's headline sentence.

        **Both members are staged ALREADY settled on the bank's own day, with
        the bank's own basis**, so ``_apply_day`` returns early for each and
        every day count is zero.  That is the whole point of the case: the
        recorded difference is then the ONLY thing the pass did, so a
        ``moved_nothing`` blind to it would render *"Nothing moved."* over a
        row this pass created -- the same false sentence ``repriced_count`` was
        added to stop in 2026-08-22.  A mutation sweep is what showed a first
        version of this case grading nothing: it mutated the two rows after
        staging them, and ``Transaction.status`` is a RELATIONSHIP that a flush
        does not refresh, so the door still saw them Projected and settled them.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        settled = {
            "status": StatusEnum.RECEIVED,
            "settled_on": bank_day,
            "settle_day_basis": SettledDayBasisEnum.OBSERVED,
        }
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True, **settled,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
            **settled,
        )
        scope = a_scope(seed_user)

        outcome = statement_match.apply_reviewed(
            statement_match.ReviewedBatch(
                skips=(),
                consent=statement_match.Consent.TICKED,
                incomes=(),
                matches=(a_submission(
                    scope, lines=[line], transactions=[salary, allowance],
                    residual="0.05",
                ),),
                creations=(),
            ),
            scope,
        )

        assert outcome.refused == ()
        # Nothing about the two members moved: they already recorded the
        # bank's day, on the bank's own basis.
        assert (
            outcome.settled_count, outcome.corrected_count,
            outcome.redated_count, outcome.repriced_count,
        ) == (0, 0, 0, 0)
        assert outcome.residual_count == 1
        assert outcome.residual_total == Decimal("0.05")
        assert outcome.moved_nothing is False
        assert "recorded the +0.05 difference" in outcome.applied[0].summary

    def test_a_pass_ACCUMULATES_over_several_recorded_differences(
        self, app, db, seed_user,
    ):
        """The receipt is a pass total, not the last item's.

        Every other case here applies exactly ONE item, so ``+=`` and a plain
        assignment read identically -- which is how a mutation sweep found the
        accumulation ungraded.  Two groups, and the total is signed and netted
        because that is what ``residual_total`` claims to be.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        deposit = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        debit = a_bank_line(
            seed_user, statement, amount="-180.02", posted_on=bank_day,
        )
        power = a_transaction(seed_user, name="Power", amount="100.00")
        water = a_transaction(seed_user, name="Water", amount="80.00")
        scope = a_scope(seed_user)

        outcome = statement_match.apply_reviewed(
            statement_match.ReviewedBatch(
                skips=(),
                consent=statement_match.Consent.TICKED,
                incomes=(),
                matches=(
                    a_submission(
                        scope, lines=[deposit],
                        transactions=[salary, allowance], residual="0.05",
                    ),
                    a_submission(
                        scope, lines=[debit], transactions=[power, water],
                        residual="-0.02",
                    ),
                ),
                creations=(),
            ),
            scope,
        )

        assert outcome.refused == ()
        assert outcome.residual_count == 2
        # +0.05 and -0.02 net to +0.03, which is what reached the buckets.
        assert outcome.residual_total == Decimal("0.03")
        assert len(_minted(seed_user)) == 2
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.EXPENSE,
        ) == Decimal("0.02")


class TestCorrectingAPurchaseOntoTheBanksFigure:
    """``corrected_figure`` inverts the purchase candidate's own negation.

    Plan step ``bank_import:X-gj-2b``, ruling **R-II**.  A purchase's cash is
    ``-entry.amount`` (:func:`~._valuation.purchase_candidate`), so the figure
    that moves its cash onto the bank's is ``-bank_cash``.

    **It was ``abs(bank_cash)``, and the two agree only for an OUTFLOW.**
    While every purchase was positive its cash was negative and the magnitude
    WAS the negation; a merchant refund is a negative purchase whose cash is
    POSITIVE, and there ``abs()`` books ``+X`` where the row must store ``-X``
    -- turning a refund into a charge of the same size.

    **Both directions are asserted**, because a case staging only the refund
    would pass against a naive ``-bank_cash`` that had broken every outflow,
    and a case staging only the outflow is what let the defect ship.
    """

    def test_an_OUTFLOW_purchase_still_corrects_to_the_magnitude(
        self, app, db, seed_user,
    ):
        """The case that was already right, pinned so the fix cannot break it."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="60.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="60.00",
                purchased_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            row = purchase_candidate(
                purchase, calendar_for(seed_user["user"].id),
            )
            assert row.cash_amount == Decimal("-60.00")

            # The bank took 60.06, not 60.00.
            assert corrected_figure(row, Decimal("-60.06")) == Decimal("60.06")

    def test_a_REFUND_purchase_corrects_to_a_NEGATIVE_figure(
        self, app, db, seed_user,
    ):
        """The case ``abs()`` inverted.

        A refund stored at ``-28.29`` has cash ``+28.29``; a bank line stating
        ``+30.00`` must move the STORED figure to ``-30.00``, not ``+30.00``.
        """
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Amazon", amount="0.00", is_envelope=True,
            )
            refund = a_purchase(
                seed_user, envelope, amount="-28.29",
                purchased_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            row = purchase_candidate(
                refund, calendar_for(seed_user["user"].id),
            )
            assert row.cash_amount == Decimal("28.29")

            assert corrected_figure(row, Decimal("30.00")) == Decimal("-30.00")
