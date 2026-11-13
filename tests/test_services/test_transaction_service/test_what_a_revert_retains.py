"""What a revert keeps, a replayed settle, and where the retained map answers."""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.models.account import AccountAnchorHistory
from app.services import status_seam, transaction_service
from app.services.row_valuation import settled_contribution, settled_figure
from tests._test_helpers import amount_basis_for, generate_row_of, settlement_if_settling, typed
from app.services.amount_ownership import state_own_amount

from ._helpers import _make_entry, _make_template


class TestARevertKeepsWhatMovedAndReleasesTheAssertion:
    """The retention round trips, end to end through the real verbs.

    **The whole of plan step X-au-c3's third design**, and the arm the suite
    could not see before: a row carries THREE facts with THREE lifetimes -- the
    PLAN, WHAT MOVED, and the ASSERTION -- and no column belongs to two.  A
    revert releases the assertion (``settled_on``, ``reconciled_by_id``) and
    keeps what moved, so the revert / edit / re-settle round trip the full-edit
    popover INSTRUCTS the user to perform is lossless.

    Every case below drives ``status_seam.apply_status_change`` and
    ``transaction_service.settle_transaction`` -- the doors -- rather than
    assigning columns, because the claim under test is what those doors do.
    """

    @staticmethod
    def _settle(txn, *, submitted=None):
        """Settle *txn* through the verb, returning whether it booked a human's figure."""
        return transaction_service.settle_transaction(txn, submitted=submitted)

    @staticmethod
    def _revert(txn):
        """Put *txn* back to Projected through the ONE status door."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )

    def test_a_revert_keeps_the_figure_and_drops_the_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The asymmetry, stated as an assertion instead of a comment.

        A first version of this step released all four columns together, under
        a CHECK that paired the day with the basis -- and it cost the user real
        data, because the popover tells them to revert in order to edit.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()
            assert txn.settled_on is not None
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            # A statement is recorded as having SHOWN this money, so the
            # release below has something to release.  Asserting
            # ``reconciled_by_id is None`` after a revert without this is
            # vacuous -- it passes on a build that never clears the link, which
            # is half of what "a revert releases the ASSERTION" claims (found
            # by adversarial review, 2026-08-17).
            anchor = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=txn.account_id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            assert anchor is not None, "the fixture account carries no anchor"
            txn.reconciled_by_id = anchor.id
            db.session.flush()
            assert txn.reconciled_by_id is not None

            self._revert(txn)
            db.session.flush()

            # The ASSERTION is withdrawn -- BOTH of its columns ...
            assert txn.settled_on is None
            assert txn.reconciled_by_id is None
            # ... and WHAT MOVED survives it, on the kept movement.
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            # But nothing counts it: the STATUS decides, not the record.
            assert settled_figure(txn) is None

    def test_revert_edit_the_plan_re_settle_books_the_HUMANS_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The round trip the popover instructs, with an edit in the middle.

        The user reverts to change the PLAN, changes it, and marks the row paid
        again without typing an amount.  The re-settle must book the figure they
        read off their statement -- not the plan they just edited, and not a
        fresh derivation of it.  This is the case a ``derived``-only retention
        rule would get wrong in the direction that loses the user's number.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()

            self._revert(txn)
            state_own_amount(txn, Decimal("610.00"))
            db.session.flush()

            booked_a_human_figure = self._settle(txn)
            db.session.flush()

            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            assert settled_figure(txn) == Decimal("245.32")
            # The plan edit stands, and is a different fact from what moved.
            assert txn.estimated_amount == Decimal("610.00")
            # Nobody typed anything at THIS tick, so the reconcile writer's
            # correction count does not count it (finding N-231).
            assert booked_a_human_figure is False

    def test_the_panel_OFFERS_exactly_what_the_re_settle_BOOKS(
        self, app, db, seed_user, seed_periods,
    ):
        """Finding C1, pinned: the offer and the booking are one expression.

        Measured before ``honoured_correction`` answered both: the panel offered
        a reverted row at its ``$500.00`` plan and the tick booked ``$245.32``.
        Worse than the drift, no input meant "book the plan" -- a submitted
        figure counts as a correction only when it DIFFERS from the offer, so
        typing ``$500.00`` was read as an echo of a figure never shown.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()
            self._revert(txn)
            db.session.flush()

            offered = transaction_service.settle_amount(txn, amount_basis_for(txn))
            assert offered == Decimal("245.32")

            self._settle(txn)
            db.session.flush()
            assert settled_figure(txn) == offered

    def test_a_retained_DERIVED_record_is_RE_DERIVED_not_reused(
        self, app, db, seed_user, seed_periods,
    ):
        """Only a HUMAN's figure is honoured; the app's own inference is redone.

        A ``resolved`` record is what the app resolved at a moment that has
        passed, and re-resolving is strictly better -- the plan may legitimately
        have been re-priced meanwhile.  **This is the case a mutant dropping
        ``retained.stated`` from ``Settlement.from_settle`` survives**: with a
        stated record the two arms agree, so only a retained RESOLVED record
        whose plan has since MOVED can tell them apart.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn)
            db.session.flush()
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("500.00"), MovementFigureSourceEnum.RESOLVED,
            )

            self._revert(txn)
            state_own_amount(txn, Decimal("610.00"))
            db.session.flush()
            # Nothing is OFFERED from the retained record either -- the offer
            # and the booking are one expression, so both re-derive.
            assert transaction_service.settle_amount(
                txn, amount_basis_for(txn),
            ) == Decimal("610.00")

            self._settle(txn)
            db.session.flush()

            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("610.00"), MovementFigureSourceEnum.RESOLVED,
            )

    def test_a_reverted_PURCHASES_row_re_sums_its_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """An envelope retains a record that stores NO figure, and re-derives.

        ``purchases`` is the basis with nothing to retain: the row's own
        children state the figure, so a revert leaves the record naming a basis
        and no amount, and a re-settle re-sums whatever the entries now say.
        The re-sum is the point -- an envelope reverted in order to CHANGE its
        purchases must close at what it now holds.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()

            self._settle(txn)
            db.session.flush()
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                None, None,
            )
            assert txn.covering_movements == []
            assert settled_figure(txn) == Decimal("40.00")

            self._revert(txn)
            db.session.flush()
            # The row records nothing of its own to survive the revert: its
            # entries are the record, and they stand (ruling **R-BAL82**).
            assert status_seam.recorded_settlement(txn) is None
            assert txn.covering_movements == []
            assert settled_figure(txn) is None

            _make_entry(txn.id, seed_user["user"].id, "12.50", "Store B")
            db.session.flush()
            # ``_make_entry`` inserts by parent ID rather than through the
            # relationship, so ``txn.entries`` is still the collection the
            # FIRST settle loaded.  Expiring it is what a second REQUEST does
            # for free, and the re-sum is the thing under test.
            db.session.expire(txn, ["entries"])
            self._settle(txn)
            db.session.flush()

            assert settled_figure(txn) == Decimal("52.50")
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                None, None,
            )

    def test_a_reverted_row_that_is_then_CANCELLED_counts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The retained figure never leaks back through a non-settled status.

        Cancelled is ``excludes_from_balance``, so the row is worth ``0`` -- and
        the arm that says so sits ABOVE the settlement read in
        ``row_valuation.fixed_contribution``, which is what makes the order of
        those two arms load-bearing rather than incidental.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()
            self._revert(txn)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.flush()

            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            assert settled_figure(txn) is None
            assert settled_contribution(txn) == Decimal("0")


class TestAReplayedSettleIsANoOp:
    """``is_identity_move``'s firing control -- the guard the suite could not see.

    **Measured 2026-08-17: the FULL suite (9,611 tests) passed with this guard
    disabled.**  A guard whose removal no test can see is not a guard, and this
    class is what makes it one.

    The defect the guard was written for is now closed twice over --
    ``Settlement.from_settle`` honours a retained ``corrected`` record, so a
    replayed manual settle no longer destroys provenance by re-deriving.  What
    it still UNIQUELY protects is the ENVELOPE replay: ``settle_from_entries``
    has preconditions a settled row fails, so without the early return a
    double-clicked Mark Paid on an already-Paid envelope answers 400 where the
    user has done nothing wrong and nothing needs doing.
    """

    def test_a_replayed_close_on_an_ENVELOPE_is_a_no_op_not_a_400(
        self, app, db, seed_user, seed_periods,
    ):
        """The identity move this guard exists for, and the only one that needs it.

        Shown to FIRE: disabling the early return in ``settle_transaction``
        raises ``ValidationError`` here instead of answering ``False``.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()

            transaction_service.settle_transaction(txn)
            db.session.flush()
            recorded = settled_figure(txn)
            day = txn.settled_on

            # The double click / stale tab / re-POST.
            assert transaction_service.settle_transaction(txn) is False
            db.session.flush()

            assert settled_figure(txn) == recorded
            assert txn.settled_on == day

    def test_it_is_NARROWER_than_the_settled_band(
        self, app, db, seed_user, seed_periods,
    ):
        """``Paid -> Received`` still owes the state machine an answer.

        A first version of this guard asked ``status_id in settled_status_ids()``
        and thereby swallowed an ILLEGAL transition, turning a designed 400 into
        a silent 200.  Only the IDENTITY move is nothing to do.

        The specimen was ``Settled -> Paid`` until plan step **balance:X-am**
        deleted the archive.  ``Paid -> Received`` is now the ONLY non-identity
        move inside the settled band -- the band is exactly those two -- so it
        is the one case that can still tell a band test from an identity test.
        Without it this guard's narrowness would be unfalsifiable.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()
            # An INCOME row, so its type-correct settle target is Received and
            # the Paid it holds is the other member of the band.
            txn.transaction_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            db.session.flush()

            # The row holds Paid; its TYPE settles as Received, which is not
            # the status it holds -- so this is a transition, not a re-submit,
            # and the state machine refuses it.
            with pytest.raises(ValidationError):
                transaction_service.settle_transaction(txn)


class TestTheRetainedMapAnswersOnlyWhereTheGapIsREAL:
    """``retained_amounts_by_key``'s status gate, which nothing graded.

    The map's contract is narrow and its whole value is the narrowness: a
    non-``None`` entry means "this row will book something other than what you
    can SEE", so a template draws a marker without deciding anything.  A settled
    row is already showing its recorded figure
    (``row_valuation.settled_amounts_by_key``), so a marker there would tell the
    user a settled row is about to book something -- which it is not.

    **Measured 2026-08-17 by an adversarial mutation pass**: dropping the
    settled-status gate left the whole suite green, so the badge would have
    appeared on every settled envelope and every settled corrected row with
    nothing to say so.
    """

    def test_a_SETTLED_row_is_not_in_the_map(
        self, app, db, seed_user, seed_periods,
    ):
        """The gate, from both sides in one case.

        The same row is asked twice -- once settled, once reverted -- so the
        test cannot pass by answering ``None`` for everything, which is the
        shape a one-sided assertion would admit.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("245.32")),
            )
            db.session.flush()

            settled_map = transaction_service.retained_amounts_by_key(
                [txn],
            )
            assert settled_map[txn.id] is None, (
                "a settled row shows its recorded figure already; a 'will "
                "book' marker on one is a second answer to a settled question"
            )

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()

            reverted_map = transaction_service.retained_amounts_by_key(
                [txn],
            )
            assert reverted_map[txn.id] == Decimal("245.32")


class TestTheRetainedMapAnswersOnlyARetainedCORRECTION:
    """``retained_amounts_by_key`` draws the re-book badge, and it lied.

    The badge's caption is *"the figure recorded before this row was set back to
    Projected"*, so a non-``None`` value must mean exactly that.  The map
    delegated to ``fixed_settle_amount``, whose FIRST arm is the purchases sum
    -- so every unsettled ENVELOPE carrying a purchase came back with a figure
    and got the badge, on a row that has never settled and records nothing
    (neutral review, 2026-08-18).  The function's own docstring already said
    such a row must answer ``None``.
    """

    def test_a_never_settled_envelope_with_a_purchase_gets_no_badge(
        self, app, db, seed_user, seed_periods,
    ):
        """The defect, stated as the row that produced it.

        A `$400.00` envelope with one `$25.00` purchase, never settled.  The
        cell already renders `25 / 400`; the badge would append `$25.00` again
        under a sentence that is false for this row.
        """
        with app.app_context():
            template = _make_template(seed_user, amount="400.00")
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "25.00", "Milk")
            db.session.flush()

            assert status_seam.recorded_settlement(txn) is None, "fixture: nothing recorded"
            assert transaction_service.retained_amounts_by_key(
                [txn],
            ) == {txn.id: None}

    def test_a_reverted_row_holding_a_correction_DOES_get_one(
        self, app, db, seed_user, seed_periods,
    ):
        """The firing control, and the case the badge exists for.

        A revert releases the assertion and KEEPS what moved, so this row's plan
        is what the balance counts while a re-settle books the retained
        `$245.32` -- two numbers about one row, and the second is on no other
        surface.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
                stated=transaction_service.StatedRecord(figure=typed(Decimal("245.32"))),
            )
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert transaction_service.retained_amounts_by_key(
                [txn],
            ) == {txn.id: Decimal("245.32")}

    def test_a_reverted_row_holding_a_DERIVED_record_gets_none(
        self, app, db, seed_user, seed_periods,
    ):
        """The second control: only a HUMAN's figure outlives the revert.

        A ``derived`` record is the app's own inference about a moment that has
        passed, and a re-settle re-resolves it -- so the row's plan IS what a
        tick books and the badge would state a difference that does not exist.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert transaction_service.retained_amounts_by_key(
                [txn],
            ) == {txn.id: None}
