"""
Shekel Budget App -- Amount-history route tests (plan step X-au-a)

The write DOOR as the two recurring-definition forms drive it, and the panel
that makes what it wrote visible and correctable.

The date the edit form asks for used to be "Regenerate effective from": it
bounded one delete-and-recreate sweep and was then discarded, so the app could
never say what a definition cost last March.  It is now stored as a version of
the amount, and it still bounds the same sweep -- so what the series says a bill
is worth and which bills an edit rebuilds are read off ONE value.  These tests
assert the stored consequence of each form action, not just its status code.

Both kinds are covered here rather than in the two per-kind route suites,
because the door and the panel are ONE implementation over two forms
(:mod:`app.services.template_amount_service`,
:mod:`app.routes._amount_version_actions`) and a per-kind split would test it
twice and its shared rules nowhere.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.utils.dates import display_today
from app.models.ref import TransactionType
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
    create_savings_account,
    make_every_period_rule,
    make_loan_payment_template,
    make_salary_profile,
    resolved_amount,
    transfer_amount,
    transfer_repriced_by_the_owner,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._definition_price import newest_price


# ── Helpers ──────────────────────────────────────────────────────────


class _FormNesting(HTMLParser):
    """Track the deepest ``<form>`` nesting a rendered page reaches."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.deepest = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.depth += 1
            self.deepest = max(self.deepest, self.depth)

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


def _max_form_nesting(html: str) -> int:
    """Return the deepest ``<form>`` nesting in *html* (1 = never nested)."""
    parser = _FormNesting()
    parser.feed(html)
    return parser.deepest


def _template_with_history(seed_user, amounts, name="Geico"):
    """Create a rule-less expense template carrying a stated price history.

    ``amounts`` is ``[(effective_date, "amount"), ...]`` ascending, written
    through the real write door so the stored shape is the one production
    would hold.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    for effective_on, amount in amounts:
        tas.set_amount(template, Decimal(amount), effective_on=effective_on)
    db.session.commit()
    return template


def _transfer_template(seed_user, savings_acct, amount="250.00"):
    """Create a recurring transfer template with an Every-Period rule."""
    # Authored through the write door (plan step R7c-b): the two-axis columns
    # are NOT NULL, so a rule naming only a pattern cannot be stored.
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        name="Money Market Contribution",
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)
    tas.set_amount(template, Decimal(amount), effective_on=date(2020, 4, 9))
    db.session.commit()
    return template


# ── The create doors ─────────────────────────────────────────────────


class TestCreateOpensTheSeries:
    """A definition is born with its price recorded, not just stored."""

    def test_creating_a_recurring_transaction_opens_its_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates records the stated amount as of today.

        Without this the definition would have a column and no history, and the
        resolver plan step X-au-b builds would have nothing to answer from.
        """
        with app.app_context():
            resp = auth_client.post("/templates", data={
                "name": "Geico",
                "amount": "165.30",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": db.session.query(TransactionType)
                    .filter_by(name="Expense").one().id,
                "account_id": seed_user["account"].id,
                # A transaction template REQUIRES a cadence since plan step
                # balance:X-bi-7b (R-BAL23); the series is what this grades.
                **cadence_payload(),
            }, follow_redirects=True)
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Geico",
            ).one()
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("165.30")
            assert versions[0].effective_date == display_today()

    def test_creating_a_recurring_transfer_opens_its_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers records the stated amount as of today."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()

            resp = auth_client.post("/transfers", data={
                "name": "Money Market Contribution",
                "amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "category_id": seed_user["categories"]["Rent"].id,
                "start_period_id": seed_periods_today[0].id,
            }, follow_redirects=True)
            assert resp.status_code == 200

            template = db.session.query(TransferTemplate).filter_by(
                name="Money Market Contribution",
            ).one()
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("250.00")
            assert versions[0].effective_date == display_today()


class TestEligibilityCanBeGAINED:
    """A definition that becomes stated-amount must not be left with no series."""

    def test_archiving_a_salary_profile_opens_its_template_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The paycheck stops pricing the rows, so today's paycheck becomes the price.

        With no ACTIVE profile the template starts owning its amount at exactly
        that moment, so the archive opens its series at today's paycheck, as a
        pass priced it while the profile still did (plan step X-bp; it opened
        at the template's ``default_amount`` copy until then).  Found by
        adversarial review: before this, archiving the profile left an eligible
        template holding ZERO versions -- 58 rows on production's one salary
        template -- which is the empty-series gap plan step X-au-b's resolver is
        specified to refuse rather than fall back on.
        """
        with app.app_context():
            from tests._test_helpers import make_salary_profile
            income = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=income.id,
                name="Data Manager",
            )
            db.session.add(template)
            db.session.flush()
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()
            tid, pid = template.id, profile.id

            assert tas.owns_its_amount(template) is False
            assert tas.amount_versions(template) == []
            paycheck = todays_price(
                template, BalanceContext.build(seed_user["user"].id),
            ).amount

            resp = auth_client.post(
                f"/salary/{pid}/delete", follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert tas.owns_its_amount(template) is True
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == paycheck
            assert tas.amount_as_of(template, date(2026, 4, 1)) == paycheck


# ── The edit door ────────────────────────────────────────────────────


class TestEditRecordsThePrice:
    """The form's date is a stored fact, not a transient sweep bound."""

    def test_a_new_amount_at_a_date_appends_a_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Raising the premium from September leaves August on the old price.

        The defect ruling R-FI names is the opposite: with a bare scalar, this
        edit reprices every past projection too.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2026, 4, 1), "178.00")],
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Geico",
                "amount": "165.30",
                "effective_from": "2026-09-01",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 9, 1), Decimal("165.30")),
            ]
            assert tas.amount_as_of(template, date(2026, 8, 1)) == Decimal("178.00")

    def test_a_rename_records_no_price(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-submitting the same amount states no change, so nothing is written.

        Every edit form posts the amount back, so gating on "the form carried a
        figure" would litter the history with entries that say nothing.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2026, 4, 1), "178.00")],
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Geico Auto",
                "amount": "178.00",
                "effective_from": "2026-09-01",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert template.name == "Geico Auto"
            assert len(tas.amount_versions(template)) == 1

    def test_a_transfer_amount_change_appends_a_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form drives the same door: $500.00 -> $250.00.

        Production's ``Checking -> Fidelity Money Market`` contribution is
        exactly this shape, and it is the only transfer template whose rows
        record two prices.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")

            resp = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Money Market Contribution",
                "amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "category_id": seed_user["categories"]["Rent"].id,
                "effective_from": "2026-05-21",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransferTemplate, template.id)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 9), Decimal("500.00")),
                (date(2026, 5, 21), Decimal("250.00")),
            ]


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
        from tests.test_routes.test_templates import (
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
            # The column still carries the queued price, so the rows that edit
            # rebuilt are regenerated at it.
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
            from tests.test_routes.test_templates import (
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
            from tests.test_routes.test_templates import (
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
        from tests.test_routes.test_templates import _create_template
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


class TestTheOptimisticLockCounter:
    """One edit bumps the template's version counter exactly once."""

    def test_a_rename_plus_amount_submit_bumps_the_counter_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-form guard rests on this, and nothing else pins it.

        The amount door suppresses autoflush, and the update route calls it
        BEFORE the field loop, precisely because a rename issues a bulk UPDATE
        that autoflushes whatever is dirty -- so stating the amount afterwards
        left a second dirty write for the commit and took ``version_id`` from 1
        to 3 on one submit.  An adversarial review noted that removing either
        guard would silently break the stale-form check with a green suite.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id
            before = template.version_id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico Auto",
                "amount": "165.30",
                "version_id": str(before),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.version_id == before + 1
            assert template.name == "Geico Auto"
            assert newest_price(template) == Decimal("165.30")

    def test_a_transfer_rename_plus_amount_submit_bumps_once_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer route calls the same door before its own field loop."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")
            tid = template.id
            before = template.version_id

            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Money Market Savings",
                "amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "category_id": seed_user["categories"]["Rent"].id,
                "version_id": str(before),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransferTemplate, tid)
            assert template.version_id == before + 1
            assert newest_price(template) == Decimal("250.00")


    def test_a_wild_effective_date_is_rejected_by_the_form(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A four-digit-year typo never reaches the series.

        Measured by an adversarial review before the bound existed: ``0202``
        was accepted, became the earliest version, and could not be withdrawn.
        The schema now refuses it and the edit is redirected back unapplied.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico",
                "amount": "165.30",
                "effective_from": "0202-08-11",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2020, 4, 1), Decimal("178.00"))]
            assert newest_price(template) == Decimal("178.00")


# ── The panel ────────────────────────────────────────────────────────


class TestHistoryPanel:
    """What was recorded is visible on the form that recorded it."""

    def test_the_edit_form_renders_the_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Each recorded price and its date appear, newest first.

        A stored fact nobody can see is a fact nobody can find wrong, and until
        plan step X-au-e prices rows from this series a mistake would surface
        only once it started moving money.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 6, 1), "178.32"),
                (date(2026, 9, 1), "165.30"),
            ])

            resp = auth_client.get(f"/templates/{template.id}/edit")
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Amount history" in body
            assert "Amount effective from" in body
            # Each Remove control is its own POST form carrying a CSRF token,
            # and the panel sits OUTSIDE the edit form -- nested forms are not
            # valid HTML and the browser would drop the inner one.
            assert 'name="csrf_token"' in body
            assert _max_form_nesting(body) == 1
            for figure in ("$178.00", "$178.32", "$165.30"):
                assert figure in body
            assert body.index("$165.30") < body.index("$178.00")

    def test_a_definition_with_no_history_renders_no_panel(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A salary-linked template has no stated price, so there is none to show."""
        with app.app_context():
            from tests._test_helpers import make_salary_profile
            expense = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense.id,
                name="Data Manager",
            )
            db.session.add(template)
            db.session.flush()
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()

            resp = auth_client.get(f"/templates/{template.id}/edit")
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Amount history" not in body


# ── The withdrawal control ───────────────────────────────────────────


class TestWithdrawAmountVersion:
    """Removing a mis-dated entry, and the one the route refuses."""

    def test_withdrawing_an_interior_entry(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An interior entry that MOVES a price is refused through the route.

        **This case asserted the removal until plan step balance:X-au-e**, on
        the stated ground that "the newest price the series states is unchanged
        and nothing generation writes moves". The first half is still true and
        the second is not: generation writes no figure now, so what June's
        window prices is read from this series live, and withdrawing the June
        entry re-prices it.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 6, 1), "178.32"),
                (date(2020, 9, 1), "165.30"),
            ])
            tid = template.id
            june_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{june_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert len(tas.amount_versions(template)) == 3
            assert tas.amount_as_of(template, date(2020, 7, 1)) == Decimal("178.32")

    def test_withdrawing_a_REDUNDANT_interior_entry_still_works(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The DOCUMENTED repair path, driven end to end.

        The other half of the rule at the ROUTE tier, and the case that shows
        the refusal above did not swallow the feature: an entry mis-dated to
        June is repaired by stating the same amount at the date it should have
        had, which APPENDS, and the June entry is then withdrawable because
        its new predecessor states the same figure -- the series answers
        identically without it.

        **A first draft of this case seeded ``[Apr 178.00, Jun 178.00, Sep
        165.30]`` directly and found only two versions**: ``set_amount``
        refuses to write a restatement that changes no answer, so a redundant
        entry cannot be created through the write door at all. Building the
        shape the repair actually produces is what makes this a test of the
        repair rather than of a state the app cannot reach.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 6, 1), "178.32"),
                (date(2020, 9, 1), "165.30"),
            ])
            # The repair: the June rise actually happened in May.
            tas.set_amount(
                template, Decimal("178.32"), effective_on=date(2020, 5, 1),
            )
            db.session.commit()
            assert len(tas.amount_versions(template)) == 4
            tid = template.id
            # BY DATE, not by index: the repair above inserted a version
            # ahead of it, and an index would silently select the new May
            # entry -- whose predecessor states a different amount, so the
            # case would assert the refusal while claiming to test the repair.
            june_id = next(
                v.id for v in tas.amount_versions(template)
                if v.effective_date == date(2020, 6, 1)
            )

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{june_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Amount history entry removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert len(tas.amount_versions(template)) == 3
            # Every date answers what it did before the withdrawal, which is
            # the whole licence for allowing it.
            assert tas.amount_as_of(template, date(2020, 7, 1)) == Decimal("178.32")
            assert tas.amount_as_of(template, date(2020, 4, 15)) == Decimal("178.00")
            assert newest_price(template) == Decimal("165.30")

    def test_withdrawing_the_earliest_is_refused_with_the_repair(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every date before the series is priced from it, so it stays.

        The refusal names the repair -- record the amount at the date you want
        first -- rather than only stating the rule.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ])
            earliest_id = tas.amount_versions(template)[0].id

            resp = auth_client.post(
                f"/templates/{template.id}/amount-versions/{earliest_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data
            assert b"record the amount at the date you want first" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert len(tas.amount_versions(template)) == 2

    def test_a_withdrawal_that_would_move_the_price_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """This door corrects the RECORD; it never moves what is budgeted.

        It runs no regeneration and offers no conflict chooser, so a withdrawal
        that changed a price would leave the rows already generated
        disagreeing with the definition, silently.  Refusing keeps the panel a
        safe surface and sends a real price change to the Amount field.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 1, 1), "178.00"),
                (date(2020, 6, 1), "165.30"),
            ])
            tid = template.id
            newest_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{newest_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert newest_price(template) == Decimal("165.30")
            assert len(tas.amount_versions(template)) == 2

    def test_another_users_template_is_404(
        self, app, second_auth_client, seed_user, seed_second_user,
        seed_periods_today,
    ):
        """The security response rule: not-yours and not-found are one answer."""
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ])
            version_id = tas.amount_versions(template)[1].id

            resp = second_auth_client.post(
                f"/templates/{template.id}/amount-versions/{version_id}/delete",
            )
            assert resp.status_code == 404

    def test_a_version_id_from_another_template_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Your own template, someone else's entry id: the same refusal.

        The service looks the id up inside THIS template's collection, so a
        version belonging elsewhere is indistinguishable from one that does not
        exist -- no existence oracle either way.
        """
        with app.app_context():
            mine = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 9, 1), "165.30"),
            ])
            other = _template_with_history(seed_user, [
                (date(2020, 4, 1), "18.14"),
                (date(2020, 9, 1), "19.99"),
            ], name="Apple Music")
            mine_id = mine.id
            foreign_id = tas.amount_versions(other)[1].id

            resp = auth_client.post(
                f"/templates/{mine_id}/amount-versions/{foreign_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            assert len(tas.amount_versions(
                db.session.get(TransactionTemplate, mine_id),
            )) == 2

    def test_a_transfer_entry_is_withdrawn_through_its_own_route(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form's control drives the same shared action."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")
            tas.set_amount(
                template, Decimal("250.00"), effective_on=date(2020, 5, 21),
            )
            tas.set_amount(
                template, Decimal("300.00"), effective_on=date(2020, 6, 4),
            )
            db.session.commit()
            tid = template.id
            # The MIDDLE entry, restated at the SAME figure its predecessor
            # states: since plan step balance:X-au-e a withdrawal is legal only
            # when the series answers identically without it, and this route
            # shares that one rule with the transaction side.
            tas.set_amount(
                template, Decimal("500.00"), effective_on=date(2020, 5, 21),
            )
            db.session.commit()
            middle_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/transfers/{tid}/amount-versions/{middle_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Amount history entry removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransferTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 9), Decimal("500.00")),
                (date(2020, 6, 4), Decimal("300.00")),
            ]
            assert newest_price(template) == Decimal("300.00")
