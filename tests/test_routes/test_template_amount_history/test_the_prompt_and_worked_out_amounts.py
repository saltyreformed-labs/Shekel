"""Amount-history route tests: the hand-edited prompt's round trip and when it asks, and a
worked-out definition's edit page, which shows its figure with no Amount box and refuses a typed
one.
"""

import re
from datetime import date, time, timedelta
from decimal import Decimal

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.utils.dates import display_today
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.obligations_aggregator import todays_price
from tests._test_helpers import (
    all_periods,
    cadence_payload,
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_salary_profile,
    resolved_amount,
    transfer_amount,
    transfer_repriced_by_the_owner,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._definition_price import newest_price

from ._helpers import (
    _enabled_form_controls,
    _template_with_history,
    _transfer_template,
)


class TestTheConflictChooserRoundTrip:
    """The chooser rolls the pending edit back, so the price must roll back too.

    A collision with a hand-edited upcoming row renders a full-page chooser and
    then ``db.session.rollback()``s the whole pending edit; Apply re-submits the
    identical form, ``effective_from`` included, and the edit runs again.  The
    price recorded on that first pass therefore has to VANISH with it, and the
    Apply pass has to record it exactly once -- otherwise a user who thinks
    better of a change leaves a version behind, or one who confirms it gets two.
    """

    @staticmethod
    def _series(template):
        """The template's price series as ``[(date, amount), ...]``."""
        return [
            (v.effective_date, v.amount) for v in tas.amount_versions(template)
        ]

    def _template_with_conflict(self, seed_user):
        """A recurring template whose latest upcoming instance is hand-edited.

        Returns the template, the overridden row, and the series as the
        FIXTURE left it.  **Both cases below assert a DELTA against that
        third value rather than a hard-coded list**, and the reason is worth
        stating: `_create_template` opens the price series the way the real
        create door does -- ``set_amount`` at the owner's today, which plan
        step balance:X-au-e made load-bearing because a template with no
        series generates rows nothing can price.  An absolute expectation
        therefore encodes the clock, and CI runs at ``Pacific/Kiritimati``.
        The claim these cases make is about what the EDIT adds and removes,
        which is exactly what a delta says.

        **The edit's date is FUTURE for the same reason, and a fixed
        ``2026-06-01`` was silently wrong once the opening version moved to
        today.**  ``default_amount`` meant the NEWEST stated price, so a
        statement dated before an existing version did not move the scalar --
        and the chooser was gated on the scalar having moved
        (``regenerate_or_conflict_chooser``).  With a back-dated edit the first
        case stopped rendering the chooser at all and returned 302: it was
        still asserting something true, about a path that no longer reached the
        code the class exists to grade.  *The gate is the price history since
        plan step X-bp, which a back-dated statement changes too
        (:class:`TestTheChooserAsksWhetherThePriceHistoryChanged`); the date
        stays future, after every existing version, so the delta below is an
        append in sorted order.*
        """
        from tests.test_routes.test_templates._helpers import (
            _create_template, _future_override_txn,
        )
        template = _create_template(
            seed_user, cadence=EVERY_PERIOD, amount="1200.00",
        )
        tas.set_amount(
            template, Decimal("1200.00"), effective_on=date(2026, 1, 1),
        )
        db.session.commit()
        txn = _future_override_txn(seed_user, template, amount="1500.00")
        return template, txn, self._series(template)

    def test_the_chooser_leaves_no_price_behind(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The first submit renders the chooser and records nothing."""
        with app.app_context():
            template, _txn, before = self._template_with_conflict(seed_user)
            tid = template.id

            rise = display_today() + timedelta(days=30)
            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1400.00",
                "effective_from": rise.isoformat(),
            })
            assert resp.status_code == 200
            assert b"hand-edited" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert newest_price(template) == Decimal("1200.00")
            # Nothing recorded and nothing withdrawn: the rollback took the
            # whole pending edit, price included.
            assert self._series(template) == before
            assert (rise, Decimal("1400.00")) not in before

    def test_apply_records_the_price_exactly_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply re-runs the same edit, so the price lands once at its date."""
        with app.app_context():
            template, txn, before = self._template_with_conflict(seed_user)
            tid, txn_id = template.id, txn.id

            rise = display_today() + timedelta(days=30)
            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1400.00",
                "effective_from": rise.isoformat(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "keep",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            # Exactly one version added, at the date the edit stated.  A
            # second pass recording it twice shows up as a longer list; a
            # pass recording it at the wrong date shows up in the pair.
            # The rise is dated after every existing version, so appending is
            # the sorted order rather than an accident of it.
            assert self._series(template) == before + [
                (rise, Decimal("1400.00")),
            ]


    def test_a_scheduled_rise_survives_a_rename_end_to_end(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Schedule a December rise, then rename: December stays December.

        The full path an adversarial review walked through the real form: the
        edit page prefills the AMOUNT it shows as "Current", so a rename posts
        today's price with a blank date and states nothing new.  Before the fix
        the page offered the December figure instead, and the rename recorded
        that rise as having happened today -- while the same page's history
        panel still labelled it "Scheduled".
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id
            future = display_today() + timedelta(days=120)

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico",
                "amount": "200.00",
                "effective_from": future.isoformat(),
            }, follow_redirects=True)
            assert resp.status_code == 200

            # The form now offers TODAY's price, not the queued one.
            body = auth_client.get(f"/templates/{tid}/edit").get_data(as_text=True)
            assert 'name="amount"' in body
            assert 'value="178.00"' in body

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico Auto",
                "amount": "178.00",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.name == "Geico Auto"
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 1), Decimal("178.00")),
                (future, Decimal("200.00")),
            ]
            # The newest price the series states is still December's: the
            # rename restated and withdrew nothing.
            assert newest_price(template) == Decimal("200.00")


class TestTheChooserAsksWhetherThePriceHistoryChanged:
    """The hand-edited prompt asks when the edit changed the price HISTORY.

    Plan step X-bp (plan#1045), an edge case decided under ruling R-BAL207.
    The prompt was gated on ``default_amount`` moving -- the NEWEST stated
    price -- and named that figure.  The newest price is not what an edit
    changes when a later price is already scheduled: a price stated under it
    reprices every row between the two dates and leaves the newest where it
    was, so the owner's hand-edited rows in that window were never asked
    about.  The gate is the series itself now, the price before the edit
    against the price after it, and the figure the page names is the one the
    edit stated.

    A definition whose amount is WORKED OUT -- a salary profile's paycheck, a
    loan payment that follows its loan -- has no history the edit door
    writes, so its edit never asks.  That replaces the salary-only flag the
    update route passed, which never covered the loan payment: its edit
    asked about a figure no row reads.  Each case posts what that form
    emits, which since the same step is no Amount at all (ruling plan#2694:
    a worked-out definition's form shows a line, not a box, and a typed
    amount is refused, :class:`TestAWorkedOutAmountIsShownNotTyped`).
    """

    @staticmethod
    def _chooser_names(body, name, figure):
        """Whether the page's framing sentence names *figure* for *name*."""
        return re.search(
            rf"You changed <strong>{re.escape(name)}</strong> to\s*"
            rf'<span class="font-mono">{re.escape(figure)}</span>',
            body,
        ) is not None

    def test_a_price_stated_under_a_scheduled_one_asks_and_names_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """$1,300.00 stated under a scheduled $1,800.00: the page asks about $1,300.00.

        The newest price is $1,800.00 before the edit and after it, so the
        old gate stayed shut over a row whose definition the edit repriced
        from $1,200.00 to $1,300.00.  The scheduled rise is dated after every
        generated row, so the hand-edited one sits inside the window the edit
        moved.
        """
        with app.app_context():
            from tests.test_routes.test_templates._helpers import (
                _create_template, _future_override_txn,
            )
            template = _create_template(
                seed_user, cadence=EVERY_PERIOD, amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tas.set_amount(
                template, Decimal("1800.00"),
                effective_on=txn.due_date + timedelta(days=30),
            )
            db.session.commit()
            tid = template.id
            before = TestTheConflictChooserRoundTrip._series(template)

            resp = auth_client.post(f"/templates/{tid}", data={
                "amount": "1300.00",
                "effective_from": (
                    display_today() + timedelta(days=1)
                ).isoformat(),
            })

            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "Some upcoming instances were hand-edited" in body
            assert self._chooser_names(body, "Rent", "$1,300.00")
            assert "$1,800.00" not in body
            # The page rolled the pending edit back, price included.
            db.session.expire_all()
            assert TestTheConflictChooserRoundTrip._series(
                db.session.get(TransactionTemplate, tid),
            ) == before

    def test_a_salary_profiles_definition_never_asks(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A paycheck's edit commits without asking, its hand-edited row kept.

        The definition is priced before the profile names it, so it HOLDS a
        series, dormant while the profile is active: the door writes it no
        version (``template_amount_service.owns_its_amount``), so the history
        before and after the edit is the same one.  The edit renames it,
        which is how the commit is read back, and posts no Amount: the form
        shows none.
        """
        with app.app_context():
            from tests.test_routes.test_templates._helpers import (
                _create_template, _future_override_txn,
            )
            template = _create_template(
                seed_user, name="Day Job", txn_type="Income",
                cadence=EVERY_PERIOD, amount="2000.00",
            )
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            db.session.commit()
            txn = _future_override_txn(seed_user, template, amount="2500.00")
            tid, txn_id = template.id, txn.id
            before = TestTheConflictChooserRoundTrip._series(template)
            assert before, "precondition: the definition holds a dormant series"

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Day Job, renamed",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Some upcoming instances were hand-edited" not in resp.data
            db.session.expire_all()
            saved = db.session.get(TransactionTemplate, tid)
            assert saved.name == "Day Job, renamed"
            assert TestTheConflictChooserRoundTrip._series(saved) == before
            kept = db.session.get(Transaction, txn_id)
            assert kept.is_override is True
            assert resolved_amount(kept) == Decimal("2500.00")

    def test_a_loan_payment_that_follows_its_loan_never_asks(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A derive-mode payment's edit asks nothing about a price it does not state.

        The transfer door passed the flag as always-true, so an edit typing
        $999.00 in the Amount box asked "You changed it to $999.00" over a
        hand-edited payment while every row of it reads the loan's own
        figure (measured failing so before plan step X-bp).  The form shows
        no box now, so the case renames it and posts no Amount.  The payment is priced before
        it is put in derive mode, the order the loan door takes, so it holds
        a dormant series as the salary case does.
        """
        with app.app_context():
            from app.services import transfer_recurrence
            loan = create_loan_account(seed_user, db.session)
            template = make_loan_payment_template(
                db.session, seed_user, loan, amount="500.00",
            )
            assert template.settings.derive_from_loan is True
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id),
                    {p.id for p in all_periods(seed_user["user"].id)},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            latest = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            assert latest.due_date > display_today(), (
                "precondition: a future payment the edit's window reaches"
            )
            transfer_repriced_by_the_owner(latest, "650.00")
            db.session.commit()
            tid, latest_id = template.id, latest.id
            before = TestTheConflictChooserRoundTrip._series(template)
            assert before, "precondition: the payment holds a dormant series"

            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Loan payment, renamed",
                "from_account_id": str(template.from_account_id),
                "version_id": str(template.version_id),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Some upcoming instances were hand-edited" not in resp.data
            db.session.expire_all()
            saved = db.session.get(TransferTemplate, tid)
            assert saved.name == "Loan payment, renamed"
            assert TestTheConflictChooserRoundTrip._series(saved) == before
            kept = db.session.get(Transfer, latest_id)
            assert kept.is_override is True
            assert transfer_amount(kept) == Decimal("650.00")


class TestAWorkedOutAmountIsShownNotTyped:
    """A worked-out definition's edit page names its source, with no Amount box.

    Plan step X-bp, ruling plan#2694, fixing finding plan#2689.  A salary
    profile's definition and a loan payment that follows its loan were shown
    an Amount box prefilled from the stored copy, or from a history they
    held from before (a paycheck priced before its profile named it, a
    payment fixed before it tracked), and saving the page wrote the box into
    the copy the Recurring page and the savings page counted.  Each kind
    here holds such a DORMANT history, priced before it was worked out, so a
    form that offered it would show its figure.
    """

    _BOX = 'name="amount"'

    @staticmethod
    def _salary_definition(seed_user):
        """A paycheck definition priced at $2,000.00 before its profile named it."""
        from tests.test_routes.test_templates._helpers import _create_template
        template = _create_template(
            seed_user, name="Day Job", txn_type="Income",
            cadence=EVERY_PERIOD, amount="2000.00",
        )
        profile = make_salary_profile(seed_user, db.session)
        profile.template_id = template.id
        db.session.commit()
        return template

    @staticmethod
    def _tracking_payment(seed_user):
        """A loan payment priced at $500.00, then put in derive mode."""
        loan = create_loan_account(seed_user, db.session)
        template = make_loan_payment_template(
            db.session, seed_user, loan, amount="500.00",
        )
        db.session.commit()
        assert template.settings.derive_from_loan is True
        return template

    @staticmethod
    def _flashes(auth_client):
        """The unconsumed flash messages an unfollowed redirect left."""
        with auth_client.session_transaction() as sess:
            return [message for _category, message in sess.get("_flashes", [])]

    def _assert_the_line(self, body, template, sentence, dormant):
        """The page shows *sentence* with today's figure, and no box."""
        today = todays_price(
            template, BalanceContext.build(template.user_id),
        ).amount
        assert today != Decimal(dormant), (
            "precondition: today's figure is not the dormant history's"
        )
        assert self._BOX not in body
        assert "data-worked-out-amount" in body
        assert re.search(
            rf'<span class="font-mono">\${today:,.2f}</span> today\.\s*'
            rf"{re.escape(sentence)}",
            body,
        ), body[body.find("data-worked-out-amount"):][:400]

    def test_a_salary_profiles_definition_shows_todays_paycheck(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Today's paycheck and its source, never the dormant $2,000.00."""
        with app.app_context():
            template = self._salary_definition(seed_user)

            body = auth_client.get(
                f"/templates/{template.id}/edit",
            ).get_data(as_text=True)

            self._assert_the_line(
                body, template, "Your salary profile works out this amount.",
                "2000.00",
            )

    def test_a_tracking_payment_shows_the_loans_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The figure the loan works out today, never the dormant $500.00."""
        with app.app_context():
            template = self._tracking_payment(seed_user)

            body = auth_client.get(
                f"/transfers/{template.id}/edit",
            ).get_data(as_text=True)

            self._assert_the_line(
                body, template, "This payment follows the loan.", "500.00",
            )

    def test_a_typed_amount_on_a_paycheck_definition_is_refused_whole(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A crafted Amount is refused with the reason; the rename beside it is not saved."""
        with app.app_context():
            template = self._salary_definition(seed_user)
            tid = template.id
            before = TestTheConflictChooserRoundTrip._series(template)

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Day Job, renamed", "amount": "2600.00",
            })

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith(f"/templates/{tid}/edit")
            assert self._flashes(auth_client) == [
                "Your salary profile works out this amount, so it cannot be "
                "typed here. Change the salary profile to change it.",
            ]
            db.session.expire_all()
            saved = db.session.get(TransactionTemplate, tid)
            assert saved.name == "Day Job"
            assert TestTheConflictChooserRoundTrip._series(saved) == before

    def test_a_typed_amount_on_a_tracking_payment_is_refused_whole(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer twin: refused with the loan's reason, nothing saved."""
        with app.app_context():
            template = self._tracking_payment(seed_user)
            tid, name = template.id, template.name
            before = TestTheConflictChooserRoundTrip._series(template)

            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Loan payment, renamed",
                "amount": "999.00",
                "from_account_id": str(template.from_account_id),
                "version_id": str(template.version_id),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                ),
            })

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith(f"/transfers/{tid}/edit")
            assert self._flashes(auth_client) == [
                "This payment follows the loan, so its amount cannot be typed "
                "here. It changes when the loan's payment does.",
            ]
            db.session.expire_all()
            saved = db.session.get(TransferTemplate, tid)
            assert saved.name == name
            assert TestTheConflictChooserRoundTrip._series(saved) == before


class TestTheBoxIsReadOnTheOwnersDay:
    """The Amount box is read on the day a save with a blank date restates it.

    Plan#2732 (Review A of plan#2686, finding 1).  A save that leaves "Amount
    effective from" blank restates the box on the owner's day,
    ``display_today()``, and the history panel names Current and Scheduled by
    that day too; the read pass's day is the process clock's
    (``BalanceContext.build``).  The box was read on the pass's day from
    plan#2698 until plan#2732, so where the process day ran ahead of the
    owner's, a rise scheduled for the owner's tomorrow prefilled the box and
    a plain rename restated it as of the owner's today.
    :meth:`TestTheConflictChooserRoundTrip.test_a_scheduled_rise_survives_a_rename_end_to_end`
    walks this path on one clock, which cannot tell the two days apart.

    Each case splits the clocks, schedules a rise on the owner's tomorrow
    (the process day), and saves what the browser would: the box's own
    value, with the date left blank.
    """

    _PRICE = "178.00"
    _RISE = "200.00"

    @staticmethod
    def _split_the_clocks(monkeypatch):
        """Freeze the process day one ahead of the owner's; return the owner's day.

        1:00 UTC on the process day is the previous evening in the owner's
        timezone (America/New_York, UTC-4 or UTC-5), so ``date.today()``,
        the read pass's day, answers one day after ``display_today()``.
        """
        process_day = date.today()
        freeze_today(monkeypatch, process_day, at_time=time(1, 0))
        owners_day = display_today()
        assert owners_day == process_day - timedelta(days=1), (
            "precondition: the process day runs one ahead of the owner's"
        )
        return owners_day

    @staticmethod
    def _box_value(body):
        """The value the edit form's Amount box is prefilled with."""
        box = re.search(r'<input[^>]*\bname="amount"[^>]*>', body)
        assert box is not None, "the edit form renders an Amount box"
        value = re.search(r'\bvalue="([^"]*)"', box.group(0))
        assert value is not None, box.group(0)
        return value.group(1)

    def _assert_the_form_offers_todays_price(self, body):
        """The box offers today's price, and the panel calls the rise Scheduled."""
        assert self._box_value(body) == self._PRICE
        # The rise is the one version that can be Scheduled (the other is
        # dated 2020), and only on a day before the rise's.
        assert "amount-ver--scheduled" in body, (
            "the history panel names Current and Scheduled by the owner's day"
        )

    def test_a_transaction_definitions_rename_restates_nothing(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """$178.00 today, $200.00 from the owner's tomorrow: a rename keeps both as they are."""
        with app.app_context():
            owners_day = self._split_the_clocks(monkeypatch)
            template = _template_with_history(seed_user, [
                (date(2020, 4, 1), self._PRICE),
                (owners_day + timedelta(days=1), self._RISE),
            ])
            tid = template.id
            before = TestTheConflictChooserRoundTrip._series(template)

            body = auth_client.get(f"/templates/{tid}/edit").get_data(as_text=True)
            self._assert_the_form_offers_todays_price(body)

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico Auto",
                "amount": self._box_value(body),
                "effective_from": "",
                "version_id": str(template.version_id),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            saved = db.session.get(TransactionTemplate, tid)
            assert saved.name == "Geico Auto"
            assert TestTheConflictChooserRoundTrip._series(saved) == before

    def test_a_transfer_definitions_rename_restates_nothing(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The transfer twin: the box offers $178.00, and the rename restates nothing."""
        with app.app_context():
            owners_day = self._split_the_clocks(monkeypatch)
            template = _transfer_template(seed_user, amount=self._PRICE)
            tas.set_amount(
                template, Decimal(self._RISE),
                effective_on=owners_day + timedelta(days=1),
            )
            db.session.commit()
            tid = template.id
            before = TestTheConflictChooserRoundTrip._series(template)

            body = auth_client.get(f"/transfers/{tid}/edit").get_data(as_text=True)
            self._assert_the_form_offers_todays_price(body)

            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Money Market Contribution, renamed",
                "amount": self._box_value(body),
                "effective_from": "",
                "from_account_id": str(template.from_account_id),
                "to_account_id": str(template.to_account_id),
                "category_id": "",
                "version_id": str(template.version_id),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            saved = db.session.get(TransferTemplate, tid)
            assert saved.name == "Money Market Contribution, renamed"
            assert TestTheConflictChooserRoundTrip._series(saved) == before


class TestTheBoxLeavesOutAStandingExtra:
    """A manual loan payment's Amount box is its stated price alone (plan#2740).

    Review B of plan#2686, finding 3.  ``edit_form_amount`` prefills the box
    from the payment's price history, never with the standing extra
    (``loan_payment_settings.extra_principal``) added, because saving the box
    restates it and amount rule 4 adds the extra on top of the stated price
    to every payment.  A box showing the two together, saved unchanged, would
    state their sum as the price.  The worked-out line is the other way round
    (:class:`TestAWorkedOutAmountIsShownNotTyped`): a tracking payment's
    figure includes the extra, because that is what leaves checking.
    """

    @staticmethod
    def _manual_payment(seed_user):
        """A manual loan payment stating $500.00, with a $100.00 standing extra.

        Returns:
            ``(template, series)``, committed; *series* is its one stated
            price, ``[(today, $500.00)]``.
        """
        loan = create_loan_account(seed_user, db.session)
        template = make_loan_payment_template(
            db.session, seed_user, loan, amount="500.00",
            derive_from_loan=False, extra_principal="100.00",
        )
        db.session.commit()
        series = TestTheConflictChooserRoundTrip._series(template)
        assert series == [(display_today(), Decimal("500.00"))], (
            "precondition: one stated price, $500.00"
        )
        return template, series

    def test_the_box_offers_the_stated_price(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The box offers $500.00, never $500.00 + $100.00."""
        with app.app_context():
            template, _series = self._manual_payment(seed_user)

            body = auth_client.get(
                f"/transfers/{template.id}/edit",
            ).get_data(as_text=True)

            assert TestTheBoxIsReadOnTheOwnersDay._box_value(body) == "500.00"

    def test_saving_the_form_unchanged_restates_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The form posted as rendered keeps the history, and $600.00 leaves checking.

        Every control a browser posts, unchanged, so the Amount is the box's
        own figure.  Regressed, it would state $600.00 from today, and every
        later payment would then leave checking at $700.00.
        """
        with app.app_context():
            template, before = self._manual_payment(seed_user)
            tid = template.id
            body = auth_client.get(f"/transfers/{tid}/edit").get_data(as_text=True)

            resp = auth_client.post(
                f"/transfers/{tid}",
                data=_enabled_form_controls(body, f"/transfers/{tid}"),
            )

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/transfers"), (
                resp.headers["Location"]
            )
            db.session.expire_all()
            saved = db.session.get(TransferTemplate, tid)
            assert TestTheConflictChooserRoundTrip._series(saved) == before
            # Amount rule 4's manual arm: $500.00 stated + $100.00 extra =
            # $600.00 leaves checking.
            assert todays_price(
                saved, BalanceContext.build(saved.user_id),
            ).amount == Decimal("600.00")
