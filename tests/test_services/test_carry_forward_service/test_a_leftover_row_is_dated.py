"""A carried-forward leftover row takes the date its definition resolves in the target paycheck
(R-BAL44's arms, R-R97's tie-break), and the refusals the adversarial reviews found.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import carry_forward_service, recurrence_engine, transaction_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.cash_ledger import resolve_transaction_amount
from app.services.one_off import state_due_date
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    amount_basis_for,
    derived_span,
    generate_row_of,
    make_cadence_rule,
    make_expense_template,
)
from tests.oracles.recurrence_baseline import ANNUAL, MONTHLY

from ._helpers import _create_envelope_template, _add_entry


class TestACarriedForwardLeftoverRowIsDated:
    """A leftover row carries the date its definition resolves it on.

    **Developer ruling 2026-09-06** (the balance arc id is reserved and not
    yet minted, so this names the ruling by date rather than by an id that
    does not resolve).
    ``_create_target_override_row`` copied its source's ``template_id`` and
    wrote ``due_date = None``.  That row can be handed BACK to its definition
    -- ``recurrence_engine.resolve_conflicts``'s "use the template's amount"
    empties its figure and declares it derived -- and a derived row is priced
    by amount rule 3, which resolves the definition's series on the ROW's own
    due date.  So the undated row became unpriceable the moment the owner
    pressed that button, and ``AmountUnresolvable`` has no handler on the
    grid, dashboard or companion path: one such row took all three out.

    Measured before the fix: ZERO rows in that state on the dev database and
    on a 1034-row production clone, so the defect was live and unrealised
    rather than sitting in the data.
    """

    def _dated_monthly_envelope(self, seed_user, seed_periods):
        """Return (template, canonical rows) for a monthly-on-the-15th envelope.

        The cadence matters: an every-paycheck rule names no day of the month,
        and ``compute_due_date`` dates such a row from its period's START --
        which would make "the definition's date" and "the paycheck's start"
        the same answer, and a case that cannot tell its two candidates apart
        grades neither.
        """
        template = _create_envelope_template(
            seed_user, name="Monthly Envelope",
        )
        # ``_create_envelope_template`` gives an every-paycheck rule; this
        # cadence has to name a day, so the rule is re-authored -- cleared
        # the way the edit door clears one (dis-associate; delete-orphan).
        template.recurrence_rule = None
        db.session.flush()
        make_cadence_rule(template, MONTHLY, fires_on_day=15)
        db.session.refresh(template)
        created = recurrence_engine.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                BalanceContext.build(seed_user["user"].id),
                {p.id for p in seed_periods},
            ),
            seed_user["scenario"].id,
        )
        db.session.flush()
        return template, created

    def _roll_into_a_period_whose_only_row_is_gone(
        self, seed_user, seed_periods, template, created,
    ):
        """Force the CREATE branch and return the fresh override row.

        Soft-deleting the target's canonical is one of the two states
        ``_classify_leftover_target`` answers CREATE for (the other is a
        finalised row), and it is the one that needs no settlement record.
        """
        # A monthly cadence fires in only some of ten biweekly paychecks, so
        # the two ends are taken from the rows the engine actually placed
        # rather than from fixed period indices.
        by_period = {p.id: p for p in seed_periods}
        placed = sorted(created, key=lambda t: t.due_date)
        source, target_canonical = placed[0], placed[1]
        target_period = by_period[target_canonical.pay_period_id]
        oracle_due = target_canonical.due_date
        # Soft-deleting the target's only row is one of the two states
        # ``_classify_leftover_target`` answers CREATE for; the other is a
        # finalised row, and this one needs no settlement record.
        target_canonical.is_deleted = True
        db.session.commit()

        carry_forward_service.carry_forward_unpaid(
            source.pay_period_id, target_period.id,
            seed_user["scenario"].id,
            balance_ctx=BalanceContext.build(seed_user["user"].id),
        )
        db.session.commit()

        fresh = (
            db.session.query(Transaction)
            .filter_by(
                template_id=template.id,
                pay_period_id=target_period.id,
                scenario_id=seed_user["scenario"].id,
                is_deleted=False,
            ).one()
        )
        assert fresh.id != source.id
        assert fresh.is_override is True
        return fresh, oracle_due, target_period

    def test_the_row_takes_the_date_its_definition_places_in_that_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The leftover is dated like the definition's OWN row in that paycheck.

        Graded against the row the ENGINE generated there.  **That is a
        different call PATH, not a different producer** -- the engine's date
        comes from ``_amounts._derive_row_fields``, which calls the same
        ``compute_due_date`` -- so this equality grades rule 14's "one walk"
        claim (the two sites cannot drift) and NOT the date's correctness.
        The correctness half is the two assertions below it: the rule was
        authored to fire on the 15th, and no paycheck in this schedule opens
        on a 15th, so both are facts about the fixture that hold whatever
        ``compute_due_date`` answers.
        """
        with app.app_context():
            template, created = self._dated_monthly_envelope(
                seed_user, seed_periods,
            )
            fresh, oracle_due, target_period = (
                self._roll_into_a_period_whose_only_row_is_gone(
                    seed_user, seed_periods, template, created,
                )
            )

            assert fresh.due_date is not None
            assert fresh.due_date == oracle_due
            # The rule fires on the 15th and no paycheck in this schedule
            # opens on one -- so "the definition's day" and "the paycheck's
            # start" are genuinely different answers here, which is what makes
            # the equality above a measurement rather than a coincidence.
            assert fresh.due_date.day == 15
            assert fresh.due_date != derived_span(target_period).start_date

    def test_a_rule_less_definitions_envelope_takes_the_rollover(
        self, app, db, seed_user, seed_periods,
    ):
        """A CLEARED cadence's envelope row settles at its purchases; the leftover is PLACED.

        **This case has pinned THREE behaviours in turn.**  Until plan step
        balance:X-bi-7a a rule-less definition's envelope row took the
        rollover through a ``rule is None`` arm of ``_leftover_due_date``,
        giving a definition that places ONE row two -- and R-BAL20's *one
        placed occurrence* made that a defect, so X-bi-7a moved such a row
        WHOLE (option (i) of ``from_scratch_architecture.md`` 10.4 trace 4)
        and this case asserted *one row of the definition, the same one, now
        in the target*.  R-BAL24 then amended the premise -- a rule-less
        definition holds *its placed rows*, one per paycheck -- and a row
        moved whole into a paycheck already holding its sibling collided on
        the occurrence index (finding **BAL-496**).  **Ruling R-BAL44**
        (developer 2026-09-16, leaf 7b-3): a rule-less ENVELOPE takes the
        rollover like a recurring one.  The source settles at its purchases
        (`$30.00`, which is what happened) and the unspent `$70.00` rolls into
        a row of the definition PLACED in the target through
        ``one_off.place_row_of`` -- dated at the target's start, answering it,
        and OWN with the flag (R-BAL43: one occurrence among many).
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, name="Father's Day",
            )
            source = generate_row_of(template, seed_periods[0])
            template.recurrence_rule = None
            _add_entry(source, seed_user, "30.00")
            db.session.commit()
            source_id = source.id
            assert source.is_placed is True

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1
            rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                )
                .order_by(Transaction.id)
                .all()
            )
            assert [row.id for row in rows][0] == source_id and len(rows) == 2
            source, placed = rows
            # The source stayed put, settled at what was spent, purchase kept.
            assert source.pay_period_id == seed_periods[0].id
            assert source.status.is_settled is True
            assert settled_figure(source) == Decimal("30.00")
            assert [entry.amount for entry in source.entries] == [Decimal("30.00")]
            # The leftover's row: the definition's, placed on the target's
            # start, answering it, OWN at exactly the leftover.
            target_start = derived_span(seed_periods[1]).start_date
            assert placed.pay_period_id == seed_periods[1].id
            assert placed.template_id == template.id
            assert placed.due_date == target_start
            assert placed.occurs_on == target_start
            assert placed.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert placed.is_override is True
            assert placed.estimated_amount == Decimal("70.00")
            assert placed.amount_source_id is None
            assert placed.entries == []

    def test_a_rule_less_envelopes_leftover_tops_up_a_projected_sibling(
        self, app, db, seed_user, seed_periods,
    ):
        """R-BAL44's TOP_UP arm: the target already holds the definition's row, open."""
        with app.app_context():
            template = _create_envelope_template(
                seed_user, name="Father's Day",
            )
            source = generate_row_of(template, seed_periods[0])
            sibling = generate_row_of(template, seed_periods[1])
            template.recurrence_rule = None
            _add_entry(source, seed_user, "30.00")
            db.session.commit()
            sibling_id = sibling.id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.expire_all()
            rows = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False)
                .order_by(Transaction.id).all()
            )
            assert [row.id for row in rows] == [source.id, sibling_id]
            sibling = rows[1]
            # `$100.00` of its own budget plus the `$70.00` leftover, OWN.
            assert sibling.estimated_amount == Decimal("170.00")
            assert sibling.is_override is True
            assert rows[0].status.is_settled is True

    def test_a_rule_less_envelope_whose_sibling_has_closed_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """R-BAL44's one refusal: the target's row of the definition has finalised.

        A second row there would answer the occurrence the closed one does
        (R-BAL25: a placed row answers its paycheck's start), and the
        occurrence index refuses it -- so the door refuses first, in its own
        words, and the preview blocks with the same code.  A recurring
        definition in this state takes a fresh override row instead, which
        answers no occurrence; that arm is graded by its own cases above.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, name="Father's Day",
            )
            source = generate_row_of(template, seed_periods[0])
            sibling = generate_row_of(template, seed_periods[1])
            template.recurrence_rule = None
            _add_entry(source, seed_user, "30.00")
            _add_entry(sibling, seed_user, "5.00")
            db.session.commit()
            transaction_service.settle_transaction(sibling, submitted=None)
            db.session.commit()
            assert sibling.status.is_settled is True

            plan = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            blocked = [
                item for item in plan.plans
                if item.block_reason_code == carry_forward_service.BLOCK_CLOSED_TARGET
            ]
            assert len(blocked) == 1 and plan.any_blocked

            with pytest.raises(ValidationError, match="already closed"):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.rollback()
            db.session.refresh(source)
            assert source.pay_period_id == seed_periods[0].id
            assert source.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_a_source_the_owner_dated_on_the_targets_start_is_refused_too(
        self, app, db, seed_user, seed_periods,
    ):
        """CLOSED's second arm (found by 7b-3's adversarial review).

        No row of the definition sits in the target, so the first build
        took CREATE and placed the leftover on the target's start -- the
        day the SOURCE, dated there by its owner, already answers, and the
        occurrence index failed the batch.  The refusal names the day.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, name="Father's Day",
            )
            source = generate_row_of(template, seed_periods[0])
            template.recurrence_rule = None
            _add_entry(source, seed_user, "30.00")
            target_start = derived_span(seed_periods[1]).start_date
            state_due_date(source, target_start)
            db.session.commit()
            assert source.occurs_on == target_start

            plan = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            blocked = [
                item for item in plan.plans
                if item.block_reason_code == carry_forward_service.BLOCK_CLOSED_TARGET
            ]
            assert len(blocked) == 1
            assert target_start.isoformat() in blocked[0].block_reason

            with pytest.raises(
                ValidationError, match=f"already due on {target_start.isoformat()}",
            ):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.rollback()
            assert (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False)
                .count()
            ) == 1

    def test_a_plain_placed_row_carried_onto_its_siblings_paycheck_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The discrete branch's twin of CLOSED (found by 7b-3's adversarial review).

        A bank-born envelope whose *Track individual purchases* the owner
        unticked (the definition's flag, R-BAL36) leaves plain rows, one
        per paycheck, and a plain row moves WHOLE (R-BAL44): re-placed on
        the target's start, it met the occurrence index beside its sibling
        there.  The move refuses in its own words, and nothing moved.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="0.00", name="Amazon",
                category_key="Groceries", is_envelope=True,
            )
            source = generate_row_of(template, seed_periods[0])
            sibling = generate_row_of(template, seed_periods[1])
            template.recurrence_rule = None
            template.is_envelope = False
            db.session.commit()
            assert source.is_placed and not source.tracks_purchases

            with pytest.raises(ValidationError, match="already holds a row"):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.rollback()
            db.session.refresh(source)
            db.session.refresh(sibling)
            assert source.pay_period_id == seed_periods[0].id
            assert sibling.pay_period_id == seed_periods[1].id

    def test_handing_the_leftover_back_to_its_definition_leaves_it_priceable(
        self, app, db, seed_user, seed_periods,
    ):
        """THE CONTROL: the act that used to strand the row now prices it.

        ``resolve_conflicts(action="update")`` is the chooser's "use the
        template's amount".  It clears the override flag and calls
        ``declare_derived``, which empties the row's figure -- so from here
        amount rule 3 prices the row off its definition's series, resolved on
        the row's own due date.

        Before the producer was fixed this raised ``AmountUnresolvable``
        from ``_stated_amount``'s no-due-date arm, and every screen that
        prices a row without catching it went down with it.
        """
        with app.app_context():
            template, created = self._dated_monthly_envelope(
                seed_user, seed_periods,
            )
            fresh, _, _ = self._roll_into_a_period_whose_only_row_is_gone(
                seed_user, seed_periods, template, created,
            )
            fresh_id = fresh.id

            recurrence_engine.resolve_conflicts(
                [fresh_id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            handed_back = db.session.get(Transaction, fresh_id)
            # The hand-back really happened: no figure of its own, and the
            # relation that prices it named.  Without these the pricing
            # assertion below would pass on a row that never became derived.
            assert handed_back.is_override is False
            assert handed_back.estimated_amount is None
            assert handed_back.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

            # THE GRADED LINE.  With the producer mutated back to
            # ``due_date=None`` the case now dies earlier still, at the
            # leftover's INSERT inside ``carry_forward_unpaid`` --
            # ``ck_transactions_template_row_needs_due_date`` (plan step
            # X-bv-2) refuses the undated linked row, which is the storage
            # tier saying what ``_stated_amount``'s deleted no-due-date arm
            # used to say here.  The pricing is still asserted, because the
            # CHECK grades that the row is DATED and only this grades that the
            # date it carries prices the row.
            priced = resolve_transaction_amount(
                handed_back, amount_basis_for(handed_back),
            )
            assert priced == Decimal("100.00")
            assert handed_back.due_date is not None

    def test_the_EARLIEST_occurrence_dates_the_row_where_the_rule_fires_twice(
        self, app, db, seed_user, seed_periods,
    ):
        """R-R97's tie-break: two occurrences in one paycheck, the first one dates it.

        A paycheck of 28 days or more can seat two occurrences of one monthly
        rule (a monthly-paid owner, plan step R13), and the leftover row answers
        neither.  Ruling **R-R97** dates it on the EARLIEST -- the occurrence
        ``_context._leftover_recipient`` would bump -- which is what makes the
        choice a rule and not an accident of iteration order.  Asked of the
        dating function directly, because the occurrences reach it ascending
        from ``recurrence_engine.occurrences_in_period`` and it is this
        function's ``[0]`` that states the rule.
        """
        # pylint: disable=import-outside-toplevel,protected-access
        from app.services.carry_forward_service import _execute

        with app.app_context():
            template = _create_envelope_template(seed_user, name="Twice")
            template.recurrence_rule = None
            db.session.flush()
            make_cadence_rule(template, MONTHLY, fires_on_day=15)
            db.session.refresh(template)
            period = derived_span(seed_periods[1])
            first = period.start_date + timedelta(days=2)
            second = period.start_date + timedelta(days=9)

            assert _execute._leftover_due_date(
                template, period, (first, second),
            ) == first
            # And where the rule fires nowhere in it, the payday.
            assert _execute._leftover_due_date(
                template, period, (),
            ) == period.start_date


    def test_a_definition_that_does_not_fire_in_that_paycheck_still_dates_it(
        self, app, db, seed_user, seed_periods,
    ):
        """THE BRANCH THIS CONSTRUCTOR EXISTS FOR: dated on the target's payday.

        ``_create_target_override_row``'s own docstring names it: "a yearly
        Father's Day envelope rolling into an off-anniversary period".  The
        rule names NO occurrence in the target paycheck, so there is no
        definition-placed row for the leftover to be dated like, and ruling
        **R-R97** (plan step recurrence:R5-a) dates it on that paycheck's
        payday -- the date R-BAL22 gives any row answering no occurrence.
        Until R5-a it was a COUNTERFACTUAL: ``compute_due_date`` answered from
        ``(rule, period)`` alone with the rule's day of month in the month the
        paycheck opened in, which could fall OUTSIDE the target paycheck --
        before it opened, reading overdue at birth.

        What the row gains either way is the thing it had no way to get before
        dating -- a date its definition's price series can be resolved on.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, name="Father's Day",
            )
            # The source is the engine's row under the every-paycheck cadence
            # the definition was created with; the owner then RE-AUTHORS the
            # cadence to one that fires in JUNE, against a schedule running
            # 2026-01-02 to 2026-05-21 -- so the engine places no row in any
            # seed period and the CREATE branch is the only one reachable.
            generate_row_of(template, seed_periods[0])
            template.recurrence_rule = None
            db.session.flush()
            make_cadence_rule(
                template, ANNUAL, fires_on_day=15, fires_in_month=6,
            )
            db.session.refresh(template)
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            fresh = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).one()
            )
            assert fresh.is_override is True
            # The target's payday (R-R97): the rule fires nowhere in it.
            assert fresh.due_date == derived_span(seed_periods[1]).start_date

            # The point of dating it: the hand-back now prices.
            recurrence_engine.resolve_conflicts(
                [fresh.id], action="update", ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            handed_back = db.session.get(Transaction, fresh.id)
            assert handed_back.estimated_amount is None
            assert resolve_transaction_amount(
                handed_back, amount_basis_for(handed_back),
            ) == Decimal("100.00")
