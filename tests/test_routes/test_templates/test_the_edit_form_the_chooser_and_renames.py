"""Template route tests: the edit form and its POST, the hand-edited prompt an amount change
raises, and a rename, which every row follows and the grid's row keys collapse.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    typed,
    all_periods,
    cadence_payload,
    current_pay_period,
    one_off_row_of,
    repriced_by_the_owner,
    resolved_amount,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._definition_price import newest_price

from ._helpers import _create_template, _future_override_txn, _create_other_user_with_template


# ── Update Tests ─────────────────────────────────────────────────────


class TestTemplateUpdate:
    """Tests for GET /templates/<id>/edit and POST /templates/<id>."""

    def test_edit_template_form(self, app, auth_client, seed_user):
        """GET /templates/<id>/edit renders the edit form."""
        with app.app_context():
            template = _create_template(seed_user)

            resp = auth_client.get(f"/templates/{template.id}/edit")
            assert resp.status_code == 200
            assert b"Rent" in resp.data

    def test_update_template_success(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates/<id> updates template fields."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Updated Rent",
                "amount": "1300.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(template)
            assert template.name == "Updated Rent"
            assert newest_price(template) == Decimal("1300.00")

    def test_update_template_cannot_flip_is_active_or_sort_order(
        self, app, auth_client, seed_user,
    ):
        """POST /templates/<id> never writes is_active / sort_order.

        Those columns are owned by the archive / unarchive routes (which
        pair the flag flip with the projected-transaction soft-delete this
        route does not perform).  They are absent from both
        ``TemplateUpdateSchema`` and ``definition_edit.EDITABLE_FIELDS``, so even a
        crafted form that submits them must leave the stored values
        untouched while a legitimate field still updates.
        """
        with app.app_context():
            template = _create_template(seed_user)
            assert template.is_active is True
            assert template.sort_order == 0

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Renamed",
                "is_active": "false",
                "sort_order": "99",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.refresh(template)
            # Legitimate field updated -- the request was processed.
            assert template.name == "Renamed"
            # Crafted is_active / sort_order keys were ignored.
            assert template.is_active is True
            assert template.sort_order == 0

    def test_update_template_validation_error(self, app, auth_client, seed_user):
        """POST /templates/<id> with invalid data shows the error out loud."""
        with app.app_context():
            template = _create_template(seed_user)

            # ``repeats_on`` names no option.  It replaced ``nominal_day``
            # here at plan step recurrence:R8-c (developer ruling 2026-11-02),
            # which replaced ``day_of_month`` at plan step R7c-b: each rename
            # stopped the schemas declaring the old field, so an out-of-domain
            # value for it is dropped by ``EXCLUDE`` and this test would have
            # been asserting the banner for a key nothing reads.
            resp = auth_client.post(f"/templates/{template.id}", data={
                "repeats_on": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # **The field's OWN message, not the generic banner, since plan
            # step R7c-c**: ``nominal_day`` (now ``repeats_on``) joined
            # ``_form_errors.ACTIONABLE_FLASH_FIELDS`` there, because the pair
            # rule beside this domain check authors a real sentence naming the
            # control and it was reaching nobody.  The field's own refusal
            # rides in with it, which is the better of the two answers -- it
            # names the control, where the banner states nothing on a redirect
            # that highlights nothing.  Only a crafted POST sees it: the
            # control is a ``<select>`` whose options post their own tokens.
            assert b"Invalid repeat day." in resp.data

    def test_update_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id> for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}",
                data={"name": "Hijacked"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify original unchanged.
            db.session.refresh(other["template"])
            assert other["template"].name == "Other Rent"

    def test_edit_template_idor(self, app, auth_client, seed_user):
        """GET /templates/<id>/edit for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.get(
                f"/templates/{other['template'].id}/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_amount_change_with_override_shows_chooser(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An amount change colliding with a hand-edited instance shows the
        conflict chooser and does NOT commit the edit (the pending change is
        rolled back until Apply)."""
        with app.app_context():
            template = _create_template(
                seed_user, cadence=EVERY_PERIOD, amount="1200.00",
            )
            _future_override_txn(seed_user, template, amount="1500.00")
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1400.00",
                **cadence_payload(),
            })
            assert resp.status_code == 200
            assert b"hand-edited" in resp.data
            assert b"Keep" in resp.data and b"Use" in resp.data
            # Rolled back: the template keeps its pre-edit amount.
            db.session.expire_all()
            assert newest_price(db.session.get(
                TransactionTemplate, tid,
            )) == Decimal("1200.00")

    # **``test_the_chooser_names_the_DERIVED_paycheck`` was deleted at plan step
    # ``pay_calendar:C4-c``.**  It asserted that the override chooser named the
    # row's paycheck with the DERIVED label and never with the label the STORED
    # ``end_date`` would give -- absence being what distinguished the two
    # readers, since both put a string in the page and only one was wrong.
    #
    # **It planted a stored ``budget.pay_periods.end_date`` that disagreed with
    # the owner's paydays, and plan step ``pay_calendar:C4-c`` dropped that
    # column.**  The plant is not merely unreachable, it is SILENT: assigning to
    # an attribute the model no longer maps sets a plain Python attribute, writes
    # no UPDATE, and survives ``expire_all`` -- so the case and its own premise
    # assertion both went on passing while measuring nothing.  Deleted rather
    # than left green.
    #
    # ``PayPeriod.label`` went at plan step C4-a-5 and the column at C4-c, so
    # there is no second label a page could print: the property survives as a
    # consequence of there being ONE producer rather than as an assertion.
    # ``tests/test_routes/test_period_options.py``'s
    # ``TestTheCardNamesTheDERIVEDPaycheck`` keeps the positive half for the
    # full-edit card, located rather than searched for.

    def test_chooser_apply_use_realigns_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply with 'use' clears the override and moves it to the new
        amount; the template edit commits.  Every-Period amount is the
        template default, so the realigned instance reads $1,400.00."""
        with app.app_context():
            template = _create_template(
                seed_user, cadence=EVERY_PERIOD, amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1400.00",
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert resolved_amount(reloaded) == Decimal("1400.00")
            assert reloaded.is_override is False
            assert newest_price(db.session.get(
                TransactionTemplate, tid,
            )) == Decimal("1400.00")

    def test_the_chooser_PAGE_offers_no_figure_for_a_transaction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Use" reads as *Follow the template*, never as *Use $1,400.00*.

        Plan step balance:X-au-e, ruling **R-JD**.  The chooser is shared
        verbatim with the transfer-template route, whose generated rows still
        STORE their amount until plan step X-au-f, so the page's "use" side is
        per-kind (``RecurrenceConflictKind.use_states_a_figure``).  Rendering
        the transfer's sentence over a transaction would promise a figure no
        writer writes: nothing moves the row to $1,400.00, it stops overriding
        and its definition's series prices it on its own due date.

        **The page says the wrong thing in three places if the flag is
        dropped**, so all three are asserted: the framing sentence, the bulk
        button, and the footnote about the rows that are NOT in the list.  The
        footnote is the one that would be flatly false -- those rows are
        derived already and this page changes nothing about them.
        """
        with app.app_context():
            template = _create_template(
                seed_user, cadence=EVERY_PERIOD, amount="1200.00",
            )
            _future_override_txn(seed_user, template, amount="1500.00")
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1400.00",
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            body = resp.data
            # The chooser really did render -- without this the three
            # absence assertions below pass on any page at all.
            assert b"Some upcoming instances were hand-edited" in body
            assert b"Follow the template" in body
            assert b"Use new value for all" not in body
            assert b"Your other upcoming instances move to" not in body
            # The framing sentence still names the new price, because the
            # DEFINITION's price genuinely did change to it.
            assert b"1,400.00" in body

    def test_chooser_apply_keep_preserves_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply with 'keep' leaves the override untouched at its hand-edited
        $1,500.00; the template amount edit still commits."""
        with app.app_context():
            template = _create_template(
                seed_user, cadence=EVERY_PERIOD, amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1400.00",
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "keep",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.estimated_amount == Decimal("1500.00")
            assert reloaded.is_override is True
            assert newest_price(db.session.get(
                TransactionTemplate, tid,
            )) == Decimal("1400.00")

    def test_name_only_edit_with_override_skips_chooser(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rename that leaves the amount unchanged never shows the chooser,
        even with an override present: it commits, keeping the override's
        amount and propagating the new name to it."""
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", cadence=EVERY_PERIOD,
                amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Apartment Rent",
                "amount": "1200.00",
                **cadence_payload(),
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"hand-edited" not in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.estimated_amount == Decimal("1500.00")
            assert reloaded.is_override is True
            assert reloaded.name == "Apartment Rent"

    def test_is_salary_linked_template_detects_active_link(
        self, app, seed_user, seed_periods_today,
    ):
        """True iff an active salary profile references the template.

        A salary-linked template's instances are paycheck-calculated, so its
        definition states no price and ``owns_its_amount`` refuses it a
        price series on this answer.  The update route passed it to the
        chooser as ``amount_drives_instances=False`` until plan step X-bp;
        the chooser asks only when the price series changed now, which an
        edit to such a template never does
        (``test_template_amount_history/test_the_prompt_and_worked_out_amounts.py``'s
        ``TestTheChooserAsksWhetherThePriceHistoryChanged``).
        """
        with app.app_context():
            from app.services.template_amount_service import (
                is_salary_linked_template,
            )
            from tests._test_helpers import make_salary_profile
            template = _create_template(seed_user, txn_type="Income")
            assert is_salary_linked_template(template) is False

            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()
            db.session.refresh(template)
            assert is_salary_linked_template(template) is True

    def test_chooser_use_on_deleted_conflict_restores_with_current_name(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply 'use' on a soft-deleted conflict restores it at the new
        amount AND with the edit's new name.  The rename now reaches
        soft-deleted rows, so a restored instance is never stale-named."""
        with app.app_context():
            from app.services import recurrence_engine
            template = _create_template(
                seed_user, name="Rent", cadence=EVERY_PERIOD,
                amount="1200.00",
            )
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods},
            ), scenario.id)
            db.session.flush()
            txn = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id)
                .order_by(Transaction.due_date.desc())
                .first()
            )
            txn.is_deleted = True  # a soft-deleted conflict in the window
            db.session.commit()
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Apartment Rent",
                "amount": "1400.00",
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.is_deleted is False
            assert resolved_amount(reloaded) == Decimal("1400.00")
            assert reloaded.name == "Apartment Rent"

    def test_amount_edit_commits_when_the_only_conflict_is_a_retained_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A RETAINED row must not swallow the edit that produced it.

        **This pins a defect that shipped and was caught by adversarial review
        of plan step R10-a.**  ``regenerate_or_conflict_chooser`` branched on
        "the amount changed", not on "there is something to decide", so a
        conflict carrying ONLY ``retained`` rendered the chooser -- whose rows
        come from ``overridden`` and ``deleted`` alone, so the page listed
        NOTHING -- and then rolled the whole pending edit back.  The owner saw
        "Some upcoming instances were hand-edited" over an empty list, and
        their amount change silently did not happen.

        A retained row is not a question: the pass already left it untouched.
        So the edit must COMMIT, and the notice must name what was skipped.
        """
        with app.app_context():
            from app.services import entry_service, recurrence_engine
            template = _create_template(
                seed_user, name="Groceries", cadence=EVERY_PERIOD,
                amount="500.00",
            )
            template.is_envelope = True  # rows track purchases, as production
            db.session.flush()
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ),
                scenario.id,
            )
            db.session.flush()

            # One row carries a purchase, so the pass RETAINS it -- and there
            # is no override or soft-delete anywhere, so retained is the ONLY
            # list populated.  That is the shape the branch got wrong.
            # The CURRENT period's row, which is the only one that satisfies
            # both constraints at once: the update route sweeps from today, so
            # an earlier row is out of the window, and a purchase may not be
            # dated in the future (ruling R-M), so a later row cannot hold one.
            current = current_pay_period(
                seed_user["user"].id,
            )
            txn = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id, pay_period_id=current.id,
                )
                .one()
            )
            entry_service.create_entry(
                txn.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("40.00")),
                    description="Kroger",
                    purchased_on=current.start_date,
                ),
            )
            db.session.commit()
            tid = template.id
            # The cadence the edit posts is what retains the row: every OTHER
            # paycheck from the NEXT one on, so the rule no longer names the
            # current period's occurrence and the row holding the purchase is
            # held back rather than retired (the empty rows the rule drops
            # retire; the ones it keeps are updated).  FIXTURE RE-EXPRESSED at
            # plan step ``credit_card:CC-5-2`` under CLAUDE.md rule 5 with the
            # developer's confirmation (ruling **R-CC36**): through ``CC-5-1``
            # this moved the template's ACCOUNT instead, which retained a row
            # holding purchases because the parent-account key dragged them
            # with it; a moved row now leaves its purchases where their money
            # moved and is not retained, so that mechanism can no longer
            # produce the conflict this case is about.  The claim graded here
            # is unchanged: a retained-only conflict commits and says so.
            calendar = calendar_for(seed_user["user"].id)
            next_start = calendar.span_containing(
                calendar.period_by_id(current.id).end_date + timedelta(days=1),
            ).start_date

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Groceries",
                "amount": "650.00",
                "account_id": str(seed_user["account"].id),
                **cadence_payload(interval_n=2, starts_on=next_start),
            }, follow_redirects=True)

            assert resp.status_code == 200
            # The chooser must NOT have rendered: it had nothing to ask.
            assert b"Some upcoming instances were hand-edited" not in resp.data
            # And the owner is told which rows the pass declined to change.
            assert b"kept the value it already had" in resp.data

            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, tid)
            assert newest_price(reloaded) == Decimal("650.00"), (
                "the edit was rolled back by a conflict that asked nothing"
            )

    def test_rename_template_propagates_to_all_instances(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Renaming a template must sync every non-deleted instance's name.

        Before the fix, update_template() only touched template.name and
        relied on regenerate_for_template() to recreate rows with the
        new label.  Regeneration skips historic rows, overrides, and
        immutable rows, so those would keep the stale name and split
        into a second row in the grid.  This test asserts every
        non-deleted instance ends up with the new name -- across past
        and future periods alike.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", cadence=EVERY_PERIOD,
            )

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.commit()

            original_count = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count()
            assert original_count > 0, (
                "Seed periods should yield generated transactions"
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Apartment Rent",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            names = {
                t.name for t in db.session.query(Transaction)
                .filter(
                    Transaction.template_id == template.id,
                    Transaction.is_deleted.is_(False),
                )
                .all()
            }
            assert names == {"Apartment Rent"}, (
                f"Expected every instance renamed; found {names}"
            )

    def test_rename_template_overridden_instance_follows_template(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An is_override=True row must still pick up a template rename.

        is_override only tracks amount/period customization in this
        codebase, not name edits -- so a rename must propagate through
        overridden rows to keep every view consistent.  Without this,
        the row would keep the stale name and the grid would fall back
        to displaying the old label for the overridden period.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", cadence=EVERY_PERIOD,
            )

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.flush()

            overridden = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            # The owner's row, the way the re-price door makes one: the flag
            # alone was a shape no door writes (plan step balance:X-cf-4).
            repriced_by_the_owner(overridden, "1500.00")
            db.session.commit()
            overridden_id = overridden.id

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Apartment Rent",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            reloaded = db.session.get(Transaction, overridden_id)
            assert reloaded.name == "Apartment Rent"
            assert reloaded.is_override is True

    def test_rename_template_does_not_duplicate_grid_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End-to-end: the old label must not appear in the grid response
        after a rename, and the new label must render as a row header.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", cadence=EVERY_PERIOD,
            )

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.commit()

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Apartment Rent",
            }, follow_redirects=True)
            assert resp.status_code == 200

            grid = auth_client.get("/grid")
            assert grid.status_code == 200
            assert b"Apartment Rent" in grid.data
            # Old label must not reappear as a row header.  The seed
            # category is "Rent" so the word appears in the category
            # group column, but the row <th> label used the template
            # name -- verify it is absent from the rendered row.
            assert b">Rent<" not in grid.data


class TestGridRowKeyBuilder:
    """Defense-in-depth tests for the grid's row-key deduplication.

    These tests hit ``build_row_keys`` directly to verify the grid still
    collapses template-linked rows even if stale names slip through
    (legacy data, direct DB edits, future bugs in the rename flow).

    Import path updated in mobile-first v3 plan Commit 13: the helper
    moved from the private ``app.routes.grid._build_row_keys`` to the
    pure service module ``app.services.grid_view_service.build_row_keys``
    (no leading underscore -- it is now a public service API per
    plan Section 6.1).  Behaviour is unchanged; only the import path
    moved.
    """

    def test_row_key_collapses_template_instances_with_drifted_names(
        self, app, seed_user, seed_periods_today,
    ):
        """Stale txn.name values must not split a template into two rows."""
        from app.services.grid_view_service import build_row_keys
        from app.models.category import Category

        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", cadence=EVERY_PERIOD,
            )

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.flush()

            # Simulate the pre-fix state: template renamed, half the
            # generated instances still carry the old label.
            template.name = "Apartment Rent"
            instances = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).order_by(Transaction.id).all()
            assert len(instances) >= 2, "Need at least 2 instances"
            for idx, txn in enumerate(instances):
                txn.name = "Apartment Rent" if idx % 2 else "Rent"
            db.session.flush()

            all_cats = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
            ).all()

            row_keys = build_row_keys(
                instances, all_cats, is_income_section=False,
            )

            matches = [
                rk for rk in row_keys if rk.template_id == template.id
            ]
            assert len(matches) == 1, (
                f"Expected one row for template; got {len(matches)}: "
                f"{[rk.txn_name for rk in matches]}"
            )
            assert matches[0].txn_name == "Apartment Rent"
            assert matches[0].display_name == "Apartment Rent"

    def test_row_key_keeps_standalone_txns_separate_by_name(
        self, app, seed_user, seed_periods_today,
    ):
        """Rows that do not RECUR (one-offs) dedupe by (category, name), not by the link.

        ``build_row_keys`` forks on ``recurs``, not on the link (ruling
        R-BAL34): every one-off carries a definition of its own, so keyed
        on the link two SAME-named one-offs would split into two rows.
        Three one-offs in one category -- "One-off A" twice, in two
        paychecks, and "One-off B" -- are two rows: the twin folds into A's
        (the half that discriminates the fork; on placed rows a
        different-name pair alone is two rows under either key) and B
        stays its own.
        """
        from app.services.grid_view_service import build_row_keys
        from app.models.category import Category

        with app.app_context():
            rent_cat = seed_user["categories"]["Rent"]
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            period = seed_periods_today[0]
            account = seed_user["account"]
            scenario = seed_user["scenario"]

            # Two one-offs in the same category but different names --
            # these must remain distinct rows -- and A's same-named twin in
            # the next paycheck, which must NOT.
            txn_a = one_off_row_of(
                period,
                name="One-off A",
                amount=Decimal("50.00"),
                user_id=period.user_id,
                account_id=account.id,
                scenario_id=scenario.id,
                transaction_type_id=expense_type.id,
                category_id=rent_cat.id,
            )
            txn_b = one_off_row_of(
                period,
                name="One-off B",
                amount=Decimal("75.00"),
                user_id=period.user_id,
                account_id=account.id,
                scenario_id=scenario.id,
                transaction_type_id=expense_type.id,
                category_id=rent_cat.id,
            )
            twin = one_off_row_of(
                seed_periods_today[1],
                name="One-off A",
                amount=Decimal("60.00"),
                user_id=period.user_id,
                account_id=account.id,
                scenario_id=scenario.id,
                transaction_type_id=expense_type.id,
                category_id=rent_cat.id,
            )
            db.session.flush()
            assert twin.template_id != txn_a.template_id

            all_cats = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
            ).all()

            row_keys = build_row_keys(
                [txn_a, txn_b, twin], all_cats, is_income_section=False,
            )

            labels = sorted(rk.txn_name for rk in row_keys)
            assert labels == ["One-off A", "One-off B"]
            assert all(rk.template_id is None for rk in row_keys)
