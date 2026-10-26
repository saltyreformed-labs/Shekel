"""Recurrence engine tests: which occurrence a generated row answers (occurs_on), through
generation, maintenance and moves.
"""

import pytest
from dataclasses import replace
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType
from app.services import recurrence_engine
from app.services.pay_calendar import calendar_for
from app.services.recurrence import reauthor_rule, recurrence_spec
from app.exceptions import RecurrenceConflict
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine._amounts import DerivedRowFields
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, MONTHLY_FIRST
from tests._test_helpers import make_cadence_rule, state_template_price


class TestARowRecordsItsOccurrence:
    """``occurs_on`` -- WHICH occurrence a generated row answers.

    Plan step **R17**, the first leaf of **R5**, closing ledger row **D57**:
    both engines decided "has this already been created" by asking whether a
    PAY PERIOD held a row, so a row the owner moved to a neighbouring paycheck
    emptied the period its occurrence named and the next whole-schedule pass
    wrote a second one -- 8 rows / $1,482.93 from ONE pass on a production
    clone, seven already ``Paid``.

    The FIRST leaf made the row state its occurrence; the SECOND moved every
    predicate onto it, re-keyed both unique indexes and deleted the D19
    refusal.  So these controls now pin both halves: that the value is written,
    is the CADENCE's date and not the row's ``due_date``, and survives a
    maintain pass and a move -- AND that the generation decision itself reads
    it, which is what closes D57.
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Build a template with an AUTHORED rule.

        The same door ``TestRecurrenceGeneration`` uses; duplicated here rather
        than shared because the two classes seed different owners and pylint's
        duplicate-code checker does not read test bodies.
        """
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Occurrence Recorder',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        make_cadence_rule(template, cadence, **rule_kwargs)
        db.session.flush()
        db.session.refresh(template)
        return template

    def test_generate_writes_the_date_the_cadence_names(
        self, app, db, seed_user, seed_periods
    ):
        """Every created row carries its own occurrence, not its period's date.

        The set equality is the load-bearing half: it fails both if a row is
        written without an occurrence and if two rows are given the same one.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert created, "the control needs the engine to create something"
            assert all(row.occurs_on is not None for row in created)
            assert (
                sorted(row.occurs_on for row in created)
                == sorted(p.occurrence for p in plan.placements)
            )

    def test_the_occurrence_is_not_the_due_date(
        self, app, db, seed_user, seed_periods
    ):
        """A ``Monthly First`` row occurs on the 1st and is DATED on the payday.

        This is the case that refutes reading ``occurs_on`` off ``due_date``:
        ``compute_due_date`` dates a day-less cadence from its period's start,
        so the two columns disagree by design.  Measured on production at 30 of
        780 rows, 27 of them one ``Phone Allowance`` rule.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, MONTHLY_FIRST)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert created, "the control needs a Monthly First row"
            assert all(row.occurs_on.day == 1 for row in created), (
                "a Monthly First occurrence is the 1st of its month"
            )
            assert any(row.occurs_on != row.due_date for row in created), (
                "the control is measuring nothing if the two columns agree"
            )

    def test_occurs_on_is_not_a_derived_field(self):
        """The maintain pass's ``setattr`` loop must not be able to reach it.

        ``_apply_maintain_work`` assigns every member of
        :class:`DerivedRowFields` onto a maintained row.  ``occurs_on`` says
        WHICH occurrence the row answers -- what the row IS, not what its
        template currently says -- so adding it to that tuple would let a
        regeneration rewrite a row's identity.

        **Asserted structurally rather than by mutation, and plan step R17's
        second leaf is why.**  The behavioural version set the column to a
        sentinel date and ran a maintain pass; since a row is now NAMED by its
        occurrence, a sentinel makes the row an orphan and the pass correctly
        RETIRES it, so that control could no longer distinguish "the loop did
        not write it" from "the row is gone".  The membership test asks the
        question directly and cannot be confounded that way.
        """
        assert "occurs_on" not in DerivedRowFields._fields

    def test_a_maintained_row_keeps_its_id_and_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A row the rule still names survives a template edit intact.

        The behavioural half of the control above: an edit that changes every
        derived column while leaving the CADENCE alone must maintain the row in
        place -- same id, same ``occurs_on`` -- rather than replace it.  The id
        is the assertion that matters, because a replaced row loses the
        purchases, notes and envelope state plan step R10-a exists to keep.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            before = {row.id: row.occurs_on for row in created}
            assert before, "no rows to maintain -- the fixture proves nothing"

            # Every derived column moves; the cadence does not.
            template.name = "Renamed"
            template.default_amount = template.default_amount + 1
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            after = {
                row.id: row.occurs_on
                for row in db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False).all()
            }
            assert after == before, (
                "a template edit that left the cadence alone replaced rows "
                "instead of maintaining them"
            )

    def test_a_row_whose_occurrence_the_rule_dropped_is_retired(
        self, app, db, seed_user, seed_periods
    ):
        """The developer's ruling of 2026-08-28, as a control.

        When a rule EDIT moves the occurrence set out from under an existing
        row, that row is NOT re-pointed at whatever occurrence is left in its
        paycheck -- it is retired, and held back as a conflict instead when it
        carries the owner's own records (the neighbouring retain cases).

        Re-pointing was the alternative, and it is the same invalid inference
        an adversarial review cut from the one-time ``occurs_on`` backfill
        (ruling **recurrence:R-R49**, which is that lesson's home now that the
        backfill script is retired, 2026-09-06): it is a deduction only if
        every row answers some occurrence, which a NULL ``occurs_on`` denies.  A wrongly adopted row then SUPPRESSES generation of the real
        bill, which is a payment vanishing with nothing on screen to show it.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows -- the fixture proves nothing"
            original_ids = [row.id for row in created]
            original_occurrences = {row.occurs_on for row in created}

            # Move the cadence's DAY: every occurrence moves with it.
            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    starts_on=rule.starts_on.replace(day=19),
                ),
                calendar_for(template.user_id),
            )
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            # The premise: the edit really did move the occurrence set.
            surviving = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False).all()
            )
            assert {row.occurs_on for row in surviving} != original_occurrences

            # No original row was re-pointed -- each was retired and replaced.
            for row_id in original_ids:
                assert db.session.get(Transaction, row_id) is None

    def test_a_moved_row_is_not_regenerated_in_the_period_it_left(
        self, app, db, seed_user, seed_periods
    ):
        """**Ledger row D57 itself, as a regression control.**

        The defect the whole of plan step R17 exists to close, and until this
        test nothing in the suite failed if the leaf were reverted wholesale:
        an adversarial review restored the pre-R17 pay-period question in
        ``OccurrenceClaims`` and ``_claims_against`` and ran the ten most
        relevant files -- **537 passed**.  Every other second-pass case
        re-runs over an UNMOVED schedule, where "this period holds a row" and
        "this occurrence is answered" are the same set, so none of them can
        tell the two keyings apart.

        Measured on a production clone before the fix: one whole-schedule pass
        wrote **8 rows / $1,482.93**, six of them duplicating a due date a
        ``Paid`` row already covered.

        **The ``is_override = True`` half is load-bearing and asserted
        separately below.**  ``_claims_against`` deliberately does not filter on
        it -- a moved row is the owner's, and it still answers its occurrence.
        An override-filtered fetch would reintroduce D57 exactly, and no other
        test in the suite would notice.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            first = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first) >= 2, "the fixture needs a neighbour to move into"

            # What the PATCH door does to a template-linked row whose period
            # moves: the period changes, the row becomes the owner's, and
            # ``occurs_on`` is untouched.
            moved, neighbour = first[0], first[1]
            vacated = moved.pay_period_id
            answered = moved.occurs_on
            moved.pay_period_id = neighbour.pay_period_id
            moved.is_override = True
            db.session.flush()

            second = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert second == [], (
                f"the pass re-filled period {vacated}, which the moved row "
                f"left -- that is D57"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id, occurs_on=answered,
            ).count() == 1, (
                "the occurrence the moved row answers is now answered twice"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(first)

    def test_an_overridden_row_still_answers_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """The half of the case above that a narrower fetch would drop.

        Stated on its own because it is a one-word change away from being
        wrong: adding ``is_override = FALSE`` to ``_claims_against``'s filter
        would look like tightening the query to "the rule's own rows" and would
        restore D57 in full, since every moved row is an override row.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            row = created[0]
            row.is_override = True
            db.session.flush()

            assert recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            ) == [], "an overridden row stopped claiming its occurrence"

    def test_a_maintained_row_is_derived_from_its_OWN_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A row selected by occurrence must be derived by occurrence.

        **The crash an adversarial review of this leaf found.**
        ``classify_maintain_work`` routes a row to ``update`` when its
        ``occurs_on`` is named; ``_apply_maintain_work`` then read
        ``derived[row.pay_period_id]``, and ``derived`` holds only the periods
        the rule NAMES.  A row whose occurrence is named while its period is
        not raised ``KeyError`` -- a 500 -- and where the landing period
        happened to be named it silently re-derived the row's amount and due
        date from the WRONG paycheck.

        The door is live and needs no corrupt data: move a row, edit the
        template so the chooser reports it, then choose "use the template's
        amount" -- ``resolve_conflicts`` clears ``is_override`` without
        relocating the row or touching ``occurs_on``.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows -- the fixture proves nothing"

            named_periods = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, seed_user["scenario"].id, None,
                    block_message="test",
                ).placements
            }
            unnamed = [
                period.id for period in seed_periods
                if period.id not in named_periods
            ]
            assert unnamed, (
                "a monthly rule must leave some biweekly paycheck unnamed, or "
                "this case cannot be built"
            )

            # The state the conflict chooser's "use the template's amount"
            # leaves behind: moved, and no longer the owner's.
            row = created[0]
            answered, was_due = row.occurs_on, row.due_date
            row.pay_period_id = unnamed[0]
            row.is_override = False
            db.session.flush()

            # No KeyError, and the row keeps its OWN occurrence's figures.
            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            db.session.refresh(row)
            assert row.occurs_on == answered
            assert row.due_date == was_due, (
                "the row was re-derived from the paycheck it was moved into "
                "rather than from the occurrence it answers"
            )

    def test_a_rule_edit_that_moves_the_occurrence_inside_one_paycheck_retires(
        self, app, db, seed_user, seed_periods
    ):
        """The ruling of 2026-08-28, on the fixture that can actually see it.

        A neighbouring case moves the rule's day far enough to move the
        PAY PERIOD too, so a period-keyed classifier and an occurrence-keyed
        one agree and the case discriminates nothing -- an adversarial review
        measured exactly that by reverting ``classify_maintain_work`` to the
        period question and watching the whole class stay green.

        Day 5 to day 7 is the fixture that bites: both fall inside the same
        biweekly paycheck, so the PERIOD is unchanged and only the OCCURRENCE
        moves.  The ruling says such a row is retired and replaced, never
        re-pointed at whatever occurrence is left in its paycheck.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows -- the fixture proves nothing"
            before = {row.id: (row.pay_period_id, row.occurs_on)
                      for row in created}

            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    starts_on=rule.starts_on.replace(day=7),
                ),
                calendar_for(template.user_id),
            )
            db.session.flush()

            after_periods = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, seed_user["scenario"].id, None,
                    block_message="test",
                ).placements
            }
            # The premise that makes this case discriminating: the PERIODS did
            # not move, so a period-keyed classifier would still call every
            # original row named.
            assert after_periods == {p for p, _ in before.values()}, (
                "the edit moved the pay periods too, so this fixture cannot "
                "tell a period-keyed classifier from an occurrence-keyed one"
            )

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            for row_id in before:
                assert db.session.get(Transaction, row_id) is None, (
                    "a row whose occurrence the rule dropped was re-pointed "
                    "instead of retired"
                )

    def test_a_row_moved_OUT_OF_THE_WINDOW_still_answers_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """**D57 on the REGENERATE path**, which the generate fix did not close.

        An adversarial review of this leaf measured it at the service seam.
        ``rows_this_pass_may_maintain`` builds its domain from a PERIOD set --
        the pass's write window, bounded by ``effective_from`` -- while the
        plan bounds PLACEMENTS on the placed period's end.  So a row the owner
        moved to an earlier paycheck drops out of that domain while its
        occurrence stays named, and the create arm answers the occurrence a
        second time.  ``rows_claiming`` is period-unscoped precisely because a
        moved row is not where its occurrence is, and the maintain path was
        still deciding CREATE from the window-scoped read.

        Both variants are bad: while the moved row is still ``is_override`` it
        sits outside both partial indexes and the duplicate is SILENT -- D57
        exactly -- and once the conflict chooser has cleared that flag the
        second write is an unhandled ``IntegrityError`` that rolls the whole
        regeneration back.

        The live door is the salary one: ``salary_regeneration`` regenerates
        with ``effective_from=date.today()`` on every profile save, so an owner
        who moved a paycheck row back one period reaches this by saving a
        salary profile.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(template.user_id),
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            assert len(created) >= 2, "the fixture needs two named paychecks"

            named = sorted(
                {row.period.period_id: row.period
                 for row in recurrence_engine.resolve_generation_plan(
                     template, schedule, scenario_id, None,
                     block_message="test",
                 ).placements}.values(),
                key=lambda period: period.start_date,
            )
            unnamed = [
                period for period in seed_periods
                if period.id not in {p.period_id for p in named}
            ]
            assert unnamed, "a monthly rule must leave a paycheck unnamed"

            # Move the row answering the LAST named occurrence back to the
            # earliest paycheck, and bound the pass so that paycheck is
            # outside its window -- the salary door's own shape.
            latest = max(created, key=lambda row: row.occurs_on)
            answered = latest.occurs_on
            latest.pay_period_id = unnamed[0].id
            latest.is_override = True
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, scenario_id,
                effective_from=named[-1].start_date,
            )
            db.session.flush()

            assert db.session.query(Transaction).filter_by(
                template_id=template.id, occurs_on=answered,
            ).count() == 1, (
                f"occurrence {answered} is answered twice -- the maintain "
                f"pass decided CREATE from a window-scoped read and could not "
                f"see the row that moved out of it"
            )

    def test_an_undated_row_claims_its_whole_paycheck(
        self, app, db, seed_user, seed_periods
    ):
        """The rule that is worth ``$20,500``, with a control of its own.

        A row whose ``occurs_on`` is NULL answers no occurrence, so the only
        claim it can make is the pre-R17 one: it holds the paycheck it sits in.
        Letting it claim NOTHING was measured at the unarchive door on the
        developer's archived ``Emergency Fund`` template -- **52 rows /
        $26,000** written where the correct answer is 11 / $5,500, 41 phantom
        transfers beside rows the owner had deleted.

        **Stated as its own named case because it was resting on a side effect
        of another test's fixture**, which an adversarial review found: the
        extendability case happens to build undated rows, so it was the only
        thing in the suite that would fail if this rule were dropped -- and its
        name and docstring give a future author no reason to keep the fixture
        undated.

        Both states are covered, because the measured scenario is the
        SOFT-DELETED one: the unarchive door restores rows the owner removed
        and then generates, and those rows are undated because the backfill
        does not walk an archived template.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) >= 2

            live, removed = created[0], created[1]
            live.occurs_on = None
            removed.occurs_on = None
            removed.is_deleted = True
            db.session.flush()

            again = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert again == [], (
                "an undated row stopped holding its paycheck -- a live one, a "
                "soft-deleted one, or both"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(created)

    def test_a_maintain_pass_retains_an_undated_row_and_deletes_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """Finding **REC-516**: the MAINTAIN pass, not the generate pass.

        The case above pins that an undated row HOLDS its paycheck on
        ``generate``.  Nothing pinned what ``regenerate`` does to the same row,
        and it hard-deleted it: ``classify_maintain_work`` read *answers no
        occurrence* as *the rule dropped this occurrence* and routed the row to
        ``work.retire``, which is ``db.session.delete`` at ``_maintain`` and
        ``delete_transfer(soft=False)`` at ``transfer_recurrence``.

        **The row COUNT is deliberately not the assertion.**  Reproduced
        2026-09-08, the pass deleted the row and the create arm answered the
        freed occurrence in the same call -- ``deleted_count: 1,
        created_count: 1``, every conflict count zero, and the table exactly
        as large afterwards.  A count-based control passes on the defect, so
        this asserts the ROW'S OWN ID survives.

        **It also grades the claim the fix makes about itself**, which is the
        one thing its own existence cannot: that a retained row STILL CLAIMS
        its paycheck, so the create arm does not answer that period a second
        time.  Retaining without the claim would trade the deletion for a
        duplicate row -- the failure ``test_an_undated_row_claims_its_whole
        _paycheck`` measured at 52 rows / ``$26,000``, and strictly worse than
        the deletion being fixed here.

        The developer ruled RETAIN over silent skip on 2026-09-08: the
        definition has moved past such a row and cannot be applied to it, so
        the owner is told rather than left with a row frozen at its old price.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) >= 2

            # The exact shape both live databases carry: template-linked,
            # Projected, not an override, not soft-deleted, and holding none
            # of the owner's records -- so nothing but the branch under test
            # stands between it and ``work.retire``.
            victim = created[0]
            victim_id = victim.id
            victim_period = victim.pay_period_id
            victim.occurs_on = None
            db.session.flush()
            assert victim.is_override is False
            assert victim.is_deleted is False
            assert victim.status.is_immutable is False
            assert victim.notes is None
            assert victim.entries == []

            with pytest.raises(RecurrenceConflict) as conflict:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert victim_id in conflict.value.retained, (
                "REC-516: the undated row was not reported to the owner, so "
                "a row its definition has moved past is invisible"
            )
            assert db.session.query(Transaction).filter_by(
                id=victim_id,
            ).one_or_none() is not None, (
                f"REC-516: the maintain pass HARD DELETED undated row "
                f"{victim_id}, which answers no occurrence but which no rule "
                f"stopped naming either"
            )
            # The claim the fix rests on: the retained row still HOLDS its
            # paycheck, so nothing was written into it a second time.
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(created), (
                "the retained row stopped claiming its paycheck and the "
                "create arm answered that period again"
            )
            assert db.session.query(Transaction).filter_by(
                template_id=template.id, pay_period_id=victim_period,
            ).count() == 1

    def test_the_predictor_reads_the_claims_of_rows_that_exist(
        self, app, db, seed_user, seed_periods
    ):
        """``can_generate_in_period`` against a POPULATED table.

        The existing agreement case calls the predictor before generation, on a
        table holding no row for the template -- so step 4, the claims read and
        the whole of what plan step R17 changed, is a no-op in every assertion
        it makes.  An adversarial review measured that.

        Both directions R17 added to step 4 are asserted here: a row the owner
        MOVED out of a period must still block that period's occurrence, and a
        row that answers an occurrence must block it wherever it sits.  The
        carry-forward executor acts on this prediction and then calls
        generation, so a predictor that drifts writes real rows -- which is
        defect D22 exactly, and cost 32 of 61 periods when the two last
        disagreed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            assert len(created) >= 2

            # Every period is answered, so the predictor must say NO everywhere.
            for row in created:
                assert recurrence_engine.can_generate_in_period(
                    template, row.pay_period_id, scenario_id, schedule=schedule,
                ) is False, "the predictor offered a period already answered"

            # Move one row out of its period.  The period it LEFT is still
            # answered -- by the row that moved -- so the predictor must still
            # refuse it.  A period-keyed predictor says yes here, and the
            # carry-forward executor would then write a duplicate.
            moved, neighbour = created[0], created[1]
            vacated = moved.pay_period_id
            moved.pay_period_id = neighbour.pay_period_id
            moved.is_override = True
            db.session.flush()

            assert recurrence_engine.can_generate_in_period(
                template, vacated, scenario_id, schedule=schedule,
            ) is False, (
                "the predictor offered the period a moved row left, whose "
                "occurrence that row still answers -- D57 through the "
                "prediction door"
            )

    def test_a_moved_row_keeps_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """Moving a row to another paycheck changes its FUNDING, not its cadence.

        The whole point of the column, and the shape of ledger row **D57**:
        ``pay_period_id`` is what the owner may move and ``occurs_on`` is what
        no move touches.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            row, neighbour = created[0], created[1]
            was = row.occurs_on
            assert was is not None
            # What the PATCH door does to a template-linked row whose period
            # moves (``routes/transactions/mutations``): the period changes and
            # the row becomes the owner's.
            row.pay_period_id = neighbour.pay_period_id
            row.is_override = True
            db.session.flush()
            db.session.refresh(row)

            assert row.occurs_on == was
            assert row.pay_period_id == neighbour.pay_period_id

    def test_maintain_creates_a_missing_row_with_its_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """The maintain pass's CREATE arm states an occurrence too.

        ``MaintainWork.create_in`` held bare period ids before this step, so
        the create arm had nothing to write.  Deleting one generated row and
        re-running the pass is what reaches that arm.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            gap = created[0]
            gap_period_id, gap_occurrence = gap.pay_period_id, gap.occurs_on
            db.session.delete(gap)
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            refilled = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id,
                           pay_period_id=gap_period_id)
                .one()
            )
            assert refilled.occurs_on == gap_occurrence, (
                "the maintain create arm wrote no occurrence, or the wrong one"
            )
