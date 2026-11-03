"""
Shekel Budget App -- Recurring-definition amount-series tests (plan step X-au-a)

The four rules :mod:`app.services.template_amount_service` states, plus the
storage-tier constraints that make two of them unrepresentable rather than
merely unlikely:

  1. **Which definitions have a series** -- an amount somebody STATED gets one;
     an amount the app DERIVES (a salary-linked template's paycheck, a
     derive-mode loan payment's P&I + escrow) gets none.
  2. **What a definition is worth on a date** -- supersession, holding FLAT
     before the earliest version so the resolver is total.
  3. **How an amount is stated** -- one write door, no version when the series
     already answers that amount on that date, correction in place on a
     same-day restatement, and a refusal for a definition rule 1 gives no
     series.
  4. **How a mis-dated version is withdrawn** -- and why the earliest is not.

The figures here are the production history the backfill was designed against
(``Geico``: ``$178.00`` -> ``$178.32`` -> ``$165.30``), so a reader can check
the rules against a real price change rather than against invented numbers.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.ref import TransactionType
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from tests._test_helpers import create_savings_account, make_salary_profile
from tests._definition_price import newest_price


# ── Helpers ──────────────────────────────────────────────────────────


def _txn_template(seed_user, amount="165.30", name="Geico"):
    """Create a bare (rule-less) transaction template with no amount series."""
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
    return template


def _xfer_template(seed_user, savings_acct, amount="250.00"):
    """Create a bare transfer template with no amount series."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        name="Money Market Contribution",
    )
    db.session.add(template)
    db.session.flush()
    return template


def _seed_geico_history(template):
    """Give *template* the three-version premium history production records."""
    for eff, amount in (
        (date(2026, 4, 1), "178.00"),
        (date(2026, 6, 1), "178.32"),
        (date(2026, 9, 1), "165.30"),
    ):
        template.amount_versions.append(TemplateAmountVersion(
            effective_date=eff, amount=Decimal(amount),
        ))
    db.session.flush()


# ── Rule 2: what a definition is worth on a date ─────────────────────


class TestAmountAsOf:
    """Supersession resolution, and the flat hold before the first version."""

    def test_resolves_the_version_in_effect(self, app, db, seed_user):
        """Each date takes the greatest version at or before it.

        Geico's recorded premium history: $178.00 from Apr 1, $178.32 from
        Jun 1, $165.30 from Sep 1.  A bill due Aug 1 is still on the June
        price -- the September version has not superseded it yet.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)

            assert tas.amount_as_of(template, date(2026, 4, 1)) == Decimal("178.00")
            assert tas.amount_as_of(template, date(2026, 5, 31)) == Decimal("178.00")
            assert tas.amount_as_of(template, date(2026, 6, 1)) == Decimal("178.32")
            assert tas.amount_as_of(template, date(2026, 8, 1)) == Decimal("178.32")
            assert tas.amount_as_of(template, date(2026, 9, 1)) == Decimal("165.30")
            assert tas.amount_as_of(template, date(2028, 7, 1)) == Decimal("165.30")

    def test_holds_flat_before_the_earliest_version(self, app, db, seed_user):
        """A date before the series begins answers the EARLIEST amount.

        Ruling R-I's shape, and what makes the resolver total: generation
        writes rows into historical pay periods as readily as future ones, so
        refusing a date before the first recorded price would refuse to price
        a row the app itself created.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)

            assert tas.amount_as_of(template, date(2026, 3, 31)) == Decimal("178.00")
            assert tas.amount_as_of(template, date(2020, 1, 1)) == Decimal("178.00")

    def test_empty_series_answers_none(self, app, db, seed_user):
        """No version at all is the ONE gap this resolver does not paper over."""
        with app.app_context():
            template = _txn_template(seed_user)
            assert tas.amount_as_of(template, date(2026, 6, 1)) is None


# ── Rule 1: which definitions have a series ──────────────────────────


class TestOwnsItsAmount:
    """A stated amount gets a series; a derived one must not."""

    def test_plain_transaction_template_owns_its_amount(
        self, app, db, seed_user,
    ):
        """An ordinary recurring expense states its own price."""
        with app.app_context():
            assert tas.owns_its_amount(_txn_template(seed_user)) is True

    def test_salary_linked_template_does_not(self, app, db, seed_user):
        """A paycheck-calculated template states no price of its own.

        The recurrence engine prices each of its rows from the paycheck
        calculator, so versioning that column would record a computed figure as
        a price somebody stated.
        """
        with app.app_context():
            template = _txn_template(seed_user, name="Data Manager")
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.flush()

            assert tas.owns_its_amount(template) is False

    def test_manual_loan_payment_owns_its_amount(
        self, app, db, seed_user,
    ):
        """In manual mode the operator owns the base cash, so it is stated."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _xfer_template(seed_user, savings)
            template.settings = LoanPaymentSettings(
                derive_from_loan=False, extra_principal=Decimal("0.00"),
            )
            db.session.flush()

            assert tas.owns_its_amount(template) is True

    def test_derive_mode_loan_payment_does_not(self, app, db, seed_user):
        """A derive payment's amount is a P&I + escrow snapshot, not a price."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _xfer_template(seed_user, savings)
            template.settings = LoanPaymentSettings(
                derive_from_loan=True, extra_principal=Decimal("0.00"),
            )
            db.session.flush()

            assert tas.owns_its_amount(template) is False


# ── Rule 3: how an amount is stated ──────────────────────────────────


class TestSetAmount:
    """The one write door: a stated price is a version of the definition's series."""

    def test_opens_the_series(self, app, db, seed_user):
        """A first statement writes the opening version, which is then the newest price."""
        with app.app_context():
            template = _txn_template(seed_user, amount="178.00")

            tas.set_amount(
                template, Decimal("178.00"), effective_on=date(2026, 4, 1),
            )
            db.session.flush()

            assert newest_price(template) == Decimal("178.00")
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].effective_date == date(2026, 4, 1)
            assert versions[0].amount == Decimal("178.00")

    def test_a_price_change_appends_a_version(self, app, db, seed_user):
        """Restating a different amount at a later date supersedes, not rewrites.

        The defect ruling R-FI names: with a bare scalar, raising the premium in
        June retroactively reprices March.  Here the April version still answers
        for April.
        """
        with app.app_context():
            template = _txn_template(seed_user, amount="178.00")
            tas.set_amount(
                template, Decimal("178.00"), effective_on=date(2026, 4, 1),
            )
            tas.set_amount(
                template, Decimal("165.30"), effective_on=date(2026, 9, 1),
            )
            db.session.flush()

            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 9, 1), Decimal("165.30")),
            ]
            assert tas.amount_as_of(template, date(2026, 5, 1)) == Decimal("178.00")

    def test_restating_the_same_amount_writes_nothing(self, app, db, seed_user):
        """A rename or cadence edit re-submits the amount and must not litter.

        The gate is the SERIES' own answer, not "did the column change": the
        series already says $165.30 on that date, so there is no change to
        record.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)

            tas.set_amount(
                template, Decimal("165.30"), effective_on=date(2027, 1, 1),
            )
            db.session.flush()

            assert len(tas.amount_versions(template)) == 3

    def test_same_day_restatement_corrects_in_place(self, app, db, seed_user):
        """Two prices for one date is not a state this table has.

        The partial unique index makes a second row on the same date
        unrepresentable, so a same-day restatement is a CORRECTION of the one
        standing there.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)

            tas.set_amount(
                template, Decimal("170.00"), effective_on=date(2026, 9, 1),
            )
            db.session.flush()

            versions = tas.amount_versions(template)
            assert len(versions) == 3
            assert versions[-1].amount == Decimal("170.00")
            assert tas.amount_as_of(template, date(2026, 9, 1)) == Decimal("170.00")

    def test_back_dating_inserts_mid_series(self, app, db, seed_user):
        """A price recorded late lands where it belongs and supersedes nothing after.

        Recording $150.00 from May 1 leaves the June and September versions in
        force from their own dates -- the whole point of supersession.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)

            tas.set_amount(
                template, Decimal("150.00"), effective_on=date(2026, 5, 1),
            )
            db.session.flush()

            assert tas.amount_as_of(template, date(2026, 4, 15)) == Decimal("178.00")
            assert tas.amount_as_of(template, date(2026, 5, 15)) == Decimal("150.00")
            assert tas.amount_as_of(template, date(2026, 6, 15)) == Decimal("178.32")
            assert tas.amount_as_of(template, date(2026, 9, 15)) == Decimal("165.30")

    def test_a_scheduled_rise_is_not_pulled_forward_by_a_later_save(
        self, app, db, seed_user,
    ):
        """Stating a FUTURE price, then re-saving the form, records nothing new.

        The whole path an adversarial review walked: state $200.00 from December
        while the current price is $178.00, then submit the edit form again --
        which prefills today's amount beside a blank date meaning today.  Because
        the prefill is what the series says TODAY (``amount_as_of`` on today, the
        edit form's read since plan step X-bp deleted the ``current_amount``
        that fell back to the column) and not the newest price the column
        held, that save restates $178.00 as of today, which the series already answers, so
        no version is written and the December rise stays in December.
        """
        with app.app_context():
            template = _txn_template(seed_user, amount="178.00")
            tas.set_amount(
                template, Decimal("178.00"), effective_on=date(2026, 4, 1),
            )
            tas.set_amount(
                template, Decimal("200.00"), effective_on=date(2026, 12, 9),
            )
            db.session.flush()

            today = date(2026, 8, 11)
            assert tas.amount_as_of(template, today) == Decimal("178.00")
            # The newest price the series states is December's: the figure a
            # prefill reading the newest price, as one reading the dropped
            # column did, would re-save as of today.
            assert newest_price(template) == Decimal("200.00")

            tas.set_amount(
                template, tas.amount_as_of(template, today), effective_on=today,
            )
            db.session.flush()

            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 12, 9), Decimal("200.00")),
            ]

    def test_back_dating_below_a_scheduled_rise_does_not_cancel_it(
        self, app, db, seed_user,
    ):
        """The newest price is the latest-dated version, not the last one typed.

        Recording a correction for June must not undo a rise already queued for
        December -- which assigning the argument to the ``default_amount``
        column did, before plan step X-bp dropped it.
        """
        with app.app_context():
            template = _txn_template(seed_user, amount="178.00")
            tas.set_amount(
                template, Decimal("178.00"), effective_on=date(2026, 4, 1),
            )
            tas.set_amount(
                template, Decimal("200.00"), effective_on=date(2026, 12, 9),
            )
            tas.set_amount(
                template, Decimal("150.00"), effective_on=date(2026, 6, 1),
            )
            db.session.flush()

            assert newest_price(template) == Decimal("200.00")
            assert tas.amount_as_of(template, date(2026, 7, 1)) == Decimal("150.00")
            assert tas.amount_as_of(template, date(2027, 1, 1)) == Decimal("200.00")

    def test_a_salary_linked_template_is_refused(self, app, db, seed_user):
        """A paycheck the engine works out is no price anybody stated (plan step X-bp).

        The door set the column and recorded no version for such a template
        until X-bp, when its one caller -- the salary regeneration's
        re-statement of today's net on every salary save -- went, so a call
        now names a caller's defect: it raises, and writes no version (and,
        until X-bp dropped the column, no copy either).
        """
        with app.app_context():
            template = _txn_template(seed_user, name="Data Manager")
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.flush()

            with pytest.raises(ValueError, match="does not own its amount"):
                tas.set_amount(
                    template, Decimal("2473.38"), effective_on=date(2026, 4, 1),
                )
            db.session.flush()

            assert tas.amount_versions(template) == []

    def test_a_derive_mode_loan_payment_is_refused(self, app, db, seed_user):
        """A payment that tracks its loan states no price either (plan step X-bp).

        Its creation and its Track wrote the loan's P&I plus escrow into the
        template's ``default_amount`` copy through this door until X-bp;
        amount rule 4 prices it live, so both callers went with that step's
        column and the door refuses one, writing nothing.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _xfer_template(seed_user, savings)
            template.settings = LoanPaymentSettings(
                derive_from_loan=True, extra_principal=Decimal("0.00"),
            )
            db.session.flush()

            with pytest.raises(ValueError, match="does not own its amount"):
                tas.set_amount(
                    template, Decimal("1910.95"), effective_on=date(2026, 4, 1),
                )
            db.session.flush()

            assert tas.amount_versions(template) == []


# ── Rule 4: how a mis-dated version is withdrawn ─────────────────────


class TestRestateInEffect:
    """Rule 3's second door (ruling **R-BAL29**): correct the version a date READS."""

    def test_the_version_in_effect_on_the_date_is_corrected_in_place(
        self, app, seed_user,
    ):
        """Geico's three versions, ``on`` inside the June window: June corrected, three stay three."""
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            tas.restate_in_effect(
                template, Decimal("180.00"), on=date(2026, 7, 15),
            )
            db.session.flush()
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 6, 1), Decimal("180.00")),
                (date(2026, 9, 1), Decimal("165.30")),
            ]
            assert tas.amount_as_of(template, date(2026, 7, 15)) == Decimal("180.00")
            assert tas.amount_as_of(template, date(2026, 5, 1)) == Decimal("178.00")
            # The newest price stays September's, untouched by a middle correction.
            assert newest_price(template) == Decimal("165.30")

    def test_before_the_earliest_version_the_earliest_is_corrected(
        self, app, seed_user,
    ):
        """The series holds flat before its first version, so that is what a date there reads."""
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            tas.restate_in_effect(
                template, Decimal("170.00"), on=date(2026, 1, 1),
            )
            db.session.flush()
            versions = tas.amount_versions(template)
            assert (versions[0].effective_date, versions[0].amount) == (
                date(2026, 4, 1), Decimal("170.00"),
            )
            assert len(versions) == 3

    def test_a_one_version_series_stays_one_whatever_the_date(
        self, app, seed_user,
    ):
        """R-BAL29's own walk: the Kindle, ``on`` moved off the version's date.

        ``set_amount(effective_on=05-10)`` would append; this corrects 05-07.
        """
        with app.app_context():
            template = _txn_template(seed_user, amount="162.25", name="Kayla's Kindle")
            tas.set_amount(
                template, Decimal("162.25"), effective_on=date(2026, 5, 7),
            )
            tas.restate_in_effect(
                template, Decimal("170.00"), on=date(2026, 5, 10),
            )
            db.session.flush()
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2026, 5, 7), Decimal("170.00"))]
            assert newest_price(template) == Decimal("170.00")
            assert tas.amount_as_of(template, date(2026, 5, 7)) == Decimal("170.00")

    def test_an_equal_figure_writes_nothing(self, app, seed_user):
        """The no-op arm: the same figure restated emits no UPDATE.

        Graded on the optimistic-lock counter after a COMMIT rather than on
        ``session.dirty``, which cannot tell a net-unchanged assignment from
        a write (``_resync_scalar`` assigns the scalar it re-reads).
        """
        with app.app_context():
            template = _txn_template(seed_user, amount="162.25")
            tas.set_amount(
                template, Decimal("162.25"), effective_on=date(2026, 5, 7),
            )
            db.session.commit()
            before = template.version_id
            tas.restate_in_effect(
                template, Decimal("162.25"), on=date(2026, 5, 9),
            )
            db.session.commit()
            db.session.refresh(template)
            assert template.version_id == before
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2026, 5, 7), Decimal("162.25"))]

    def test_an_empty_series_is_refused(self, app, seed_user):
        """Nothing is in effect on any date of a series with no version."""
        with app.app_context():
            template = _txn_template(seed_user)
            with pytest.raises(ValueError, match="no amount version"):
                tas.restate_in_effect(
                    template, Decimal("1.00"), on=date(2026, 5, 7),
                )

    def test_correcting_the_newest_version_moves_the_newest_price(self, app, seed_user):
        """Correcting the newest version in place moves the newest price.

        ``_resync_scalar`` copied it into the ``default_amount`` column until
        plan step X-bp dropped the column.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            tas.restate_in_effect(
                template, Decimal("160.00"), on=date(2026, 12, 1),
            )
            db.session.flush()
            assert newest_price(template) == Decimal("160.00")


class TestDeleteAmountVersion:
    """Withdrawal, and the one entry it refuses."""

    def test_a_MIDDLE_version_that_moves_a_price_is_REFUSED(
        self, app, db, seed_user,
    ):
        """Removing the June entry would re-price June, so it is refused.

        **This case asserted the OPPOSITE until plan step balance:X-au-e** --
        it was ``test_withdraws_a_later_version``, and its own docstring said
        *"removing the June entry puts June back on the April price"*, which
        is the money movement rather than a description of a repair. That was
        harmless while generation copied ``default_amount`` onto each row: no
        stored figure moved. A generated row resolves through
        :func:`amount_as_of` on its own due date now, so the same withdrawal
        silently re-prices every row inside the June-August window, with no
        regeneration and no conflict chooser.

        Measured on a clone of production 2026-09-03, which is where these
        three versions come from: the 2026-06-01 entry was withdrawable and
        taking it moved one row from ``$178.32`` to ``$178.00`` under a flash
        reading only "Amount history entry removed".

        The rule is now one sentence -- a version may go only when the version
        BEFORE it states the same amount -- and the case below is its other
        half, so the pair is disjoint rather than a single-direction pin.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            june_id = tas.amount_versions(template)[1].id

            assert tas.delete_amount_version(template, june_id) is False
            db.session.flush()

            assert len(tas.amount_versions(template)) == 3
            assert tas.amount_as_of(template, date(2026, 6, 15)) == Decimal("178.32")

    def test_a_MIDDLE_version_that_moves_NOTHING_is_withdrawn(
        self, app, db, seed_user,
    ):
        """A version its predecessor already states is a redundant record.

        The other half of the rule, and what keeps the documented repair path
        open: state the amount at the right date, which appends, then withdraw
        the mis-dated entry -- legal exactly because the series answers the
        same figure without it.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            for eff, amount in (
                (date(2026, 4, 1), "178.00"),
                (date(2026, 6, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ):
                template.amount_versions.append(TemplateAmountVersion(
                    effective_date=eff, amount=Decimal(amount),
                ))
            db.session.flush()
            june_id = tas.amount_versions(template)[1].id

            assert tas.delete_amount_version(template, june_id) is True
            db.session.flush()

            assert len(tas.amount_versions(template)) == 2
            # Every date answers exactly what it answered before.
            assert tas.amount_as_of(template, date(2026, 6, 15)) == Decimal("178.00")
            assert tas.amount_as_of(template, date(2026, 9, 2)) == Decimal("165.30")

    def test_the_REMOVE_button_and_the_service_answer_the_same_rule(
        self, app, db, seed_user,
    ):
        """``is_deletable`` is the service's verdict, not a restatement.

        It was ``index > 0`` in the display builder against a newest-price
        test in the service, so the page offered Remove on the newest entry
        and the service refused it -- two spellings of one rule, which is what
        :class:`~app.services.template_amount_service.AmountVersionRow`'s own
        docstring already claimed was not happening. Both call
        ``_may_withdraw`` now, and this asserts they agree ROW FOR ROW rather
        than that either is right on its own.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            rows = tas.build_amount_history(template, date(2026, 9, 15))

            offered = {row.id: row.is_deletable for row in rows}
            assert offered, "the fixture rendered no history rows"
            for version_id, is_offered in offered.items():
                # A fresh read per probe: the service MUTATES on success.
                probe = _txn_template(seed_user)
                _seed_geico_history(probe)
                mirror = {
                    old.effective_date: new.id for old, new in zip(
                        tas.amount_versions(template),
                        tas.amount_versions(probe),
                    )
                }
                dated = next(
                    v.effective_date for v in tas.amount_versions(template)
                    if v.id == version_id
                )
                assert tas.delete_amount_version(
                    probe, mirror[dated],
                ) is is_offered

    def test_refuses_the_earliest_version(self, app, db, seed_user):
        """Every date before the series begins is priced from it.

        Withdrawing it would silently reprice all of pre-history, so the repair
        is to state the amount at the date you want -- which makes a NEW
        earliest -- and then withdraw this one.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            earliest_id = tas.amount_versions(template)[0].id

            assert tas.delete_amount_version(template, earliest_id) is False
            db.session.flush()
            assert len(tas.amount_versions(template)) == 3

    def test_refuses_a_withdrawal_that_would_change_the_price(
        self, app, db, seed_user,
    ):
        """Withdrawing the newest entry would move what generation writes.

        The newest entry prices every row due on or after its date, and
        this door runs no regeneration and offers no conflict
        chooser -- so a withdrawal that moved it would leave the rows already
        generated disagreeing with the definition, silently.  Cancelling a
        queued rise is a price change and belongs in the Amount field.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            db.session.flush()
            newest_id = tas.amount_versions(template)[2].id

            assert tas.delete_amount_version(template, newest_id) is False
            db.session.flush()

            assert len(tas.amount_versions(template)) == 3
            assert newest_price(template) == Decimal("165.30")

    def test_allows_withdrawing_the_newest_when_the_price_is_unchanged(
        self, app, db, seed_user,
    ):
        """Re-dating an entry: state the same amount earlier, drop the old one.

        The repair the panel documents.  The surviving series still states
        $165.30 as its newest price, so nothing generation writes moves and the
        withdrawal is a pure correction of the record.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            tas.set_amount(
                template, Decimal("165.30"), effective_on=date(2026, 8, 1),
            )
            db.session.flush()
            mis_dated_id = tas.amount_versions(template)[3].id

            assert tas.delete_amount_version(template, mis_dated_id) is True
            db.session.flush()

            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 6, 1), Decimal("178.32")),
                (date(2026, 8, 1), Decimal("165.30")),
            ]
            assert newest_price(template) == Decimal("165.30")

    def test_the_documented_repair_for_a_mis_dated_earliest_works(
        self, app, db, seed_user,
    ):
        """State the amount at the right date, then withdraw the wrong one.

        The whole repair, end to end, because an adversarial review found it was
        a NO-OP: back-projection made the restatement compare equal to what the
        series already answered, so nothing was appended and the mis-dated entry
        stayed earliest and un-withdrawable forever.  A new earliest is now
        always recorded -- it moves where pre-history is anchored.
        """
        with app.app_context():
            template = _txn_template(seed_user, amount="178.00")
            tas.set_amount(
                template, Decimal("178.00"), effective_on=date(2026, 5, 1),
            )
            db.session.flush()
            wrong_id = tas.amount_versions(template)[0].id

            tas.set_amount(
                template, Decimal("178.00"), effective_on=date(2026, 4, 1),
            )
            db.session.flush()
            assert len(tas.amount_versions(template)) == 2

            assert tas.delete_amount_version(template, wrong_id) is True
            db.session.flush()

            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2026, 4, 1), Decimal("178.00"))]

    def test_refuses_a_version_belonging_to_a_different_template(
        self, app, db, seed_user,
    ):
        """Scoped to the template's own collection, which is the authorisation.

        Both templates here belong to ONE user, because this grades the SCOPING
        rather than ownership: the caller owner-checks the template, so a
        version id from any other template -- the same user's or anyone else's --
        is simply not found.  The cross-USER refusal is a route concern and is
        asserted in ``test_template_amount_history/test_the_history_panel.py``.
        """
        with app.app_context():
            mine = _txn_template(seed_user, name="Geico")
            theirs = _txn_template(seed_user, name="Apple Music", amount="18.14")
            _seed_geico_history(mine)
            _seed_geico_history(theirs)
            their_version_id = tas.amount_versions(theirs)[1].id

            assert tas.delete_amount_version(mine, their_version_id) is False
            db.session.flush()
            assert len(tas.amount_versions(theirs)) == 3


# ── The display rows ─────────────────────────────────────────────────


class TestBuildAmountHistory:
    """Newest first, with each row's status and deletability precomputed."""

    def test_rows_are_newest_first_with_statuses(self, app, db, seed_user):
        """On 2026-07-01 the June version is current and September is scheduled.

        Read top to bottom: Sep (scheduled), Jun (current), Apr (earliest).  The
        earliest is the only one not deletable.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            db.session.flush()

            rows = tas.build_amount_history(template, date(2026, 7, 1))

            assert [r.effective_date for r in rows] == [
                date(2026, 9, 1), date(2026, 6, 1), date(2026, 4, 1),
            ]
            assert [r.status_key for r in rows] == [
                "scheduled", "current", "earliest",
            ]
            assert [r.amount for r in rows] == [
                Decimal("165.30"), Decimal("178.32"), Decimal("178.00"),
            ]
            # **None of the three is withdrawable, and that changed at plan
            # step balance:X-au-e.**  It read ``[True, True, False]`` -- the
            # display builder's own ``index > 0``, which offered Remove on two
            # entries the service now refuses because each states a different
            # amount from the one before it, so taking either would re-price
            # the rows in its window.  Both sides read ``_may_withdraw`` now.
            assert [r.is_deletable for r in rows] == [False, False, False]

    def test_a_derived_template_renders_nothing(self, app, db, seed_user):
        """No series, no panel -- a salary template has no price to show."""
        with app.app_context():
            template = _txn_template(seed_user, name="Data Manager")
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.flush()

            assert tas.build_amount_history(template, date(2026, 7, 1)) == []

    def test_a_dormant_series_is_not_rendered_after_a_flip_to_derive(
        self, app, db, seed_user,
    ):
        """A manual loan payment switched to DERIVE keeps its history, hidden.

        Eligibility is read live, so the versions recorded while the operator
        owned the base cash stay as the record of what was stated then -- but
        nothing prices from them any more.  Rendering them states something
        false: an adversarial review measured the form offering "Current
        $531.94" from the dormant series while the app was using the $1,910.95
        the loan derives, with a live Remove button beside it.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _xfer_template(seed_user, savings, amount="531.94")
            template.settings = LoanPaymentSettings(
                derive_from_loan=False, extra_principal=Decimal("0.00"),
            )
            db.session.flush()
            tas.set_amount(
                template, Decimal("531.94"), effective_on=date(2026, 4, 1),
            )
            db.session.flush()
            assert len(tas.build_amount_history(template, date(2026, 7, 1))) == 1

            template.settings.derive_from_loan = True
            db.session.flush()

            assert tas.build_amount_history(template, date(2026, 7, 1)) == []
            assert len(tas.amount_versions(template)) == 1


# ── The storage tier ─────────────────────────────────────────────────


class TestStorageConstraints:
    """What the table refuses, so no writer has to remember it."""

    def test_a_version_must_have_exactly_one_owner(self, app, db, seed_user):
        """Both keys set is refused by the exclusive-arc CHECK."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            txn = _txn_template(seed_user)
            xfer = _xfer_template(seed_user, savings)
            db.session.add(TemplateAmountVersion(
                transaction_template_id=txn.id,
                transfer_template_id=xfer.id,
                effective_date=date(2026, 4, 1),
                amount=Decimal("100.00"),
            ))
            with pytest.raises(IntegrityError, match="one_owner"):
                db.session.flush()
            db.session.rollback()

    def test_an_ownerless_version_is_refused(self, app, db, seed_user):
        """Neither key set is refused by the same CHECK.

        An amount nobody stated for anything is not an amount, which is why the
        arc is exactly-one rather than at-most-one.
        """
        with app.app_context():
            db.session.add(TemplateAmountVersion(
                effective_date=date(2026, 4, 1), amount=Decimal("100.00"),
            ))
            with pytest.raises(IntegrityError, match="one_owner"):
                db.session.flush()
            db.session.rollback()

    def test_two_versions_on_one_date_are_refused(self, app, db, seed_user):
        """The partial unique index makes a same-day pair unrepresentable."""
        with app.app_context():
            template = _txn_template(seed_user)
            for amount in ("178.00", "165.30"):
                template.amount_versions.append(TemplateAmountVersion(
                    effective_date=date(2026, 4, 1), amount=Decimal(amount),
                ))
            with pytest.raises(IntegrityError, match="transaction_effective"):
                db.session.flush()
            db.session.rollback()

    def test_a_zero_transfer_version_is_refused(self, app, db, seed_user):
        """Mirrors ``ck_transfer_templates_positive_amount`` on the template.

        A transfer of $0.00 moves no money and would produce two shadow legs
        that net to zero, so a version could never legally carry one either.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _xfer_template(seed_user, savings)
            template.amount_versions.append(TemplateAmountVersion(
                effective_date=date(2026, 4, 1), amount=Decimal("0.00"),
            ))
            with pytest.raises(IntegrityError, match="transfer_positive_amount"):
                db.session.flush()
            db.session.rollback()

    def test_a_zero_transaction_version_is_allowed(self, app, db, seed_user):
        """A transaction template's own CHECK floors at zero, not above it."""
        with app.app_context():
            template = _txn_template(seed_user, amount="0.00")
            template.amount_versions.append(TemplateAmountVersion(
                effective_date=date(2026, 4, 1), amount=Decimal("0.00"),
            ))
            db.session.flush()

            assert tas.amount_as_of(template, date(2026, 4, 1)) == Decimal("0.00")

    def test_a_four_digit_year_typo_is_refused(self, app, db, seed_user):
        """An HTML date input accepts ``0202``; the column does not.

        The consequence would be permanent, which is why it is a CHECK and not
        only a schema bound: a stray year becomes the series' EARLIEST version,
        anchors every date before the series to it, and the withdrawal door
        refuses to remove an earliest entry.  Found by adversarial review, which
        submitted ``0202-08-11`` through the real form.
        """
        with app.app_context():
            template = _txn_template(seed_user)
            template.amount_versions.append(TemplateAmountVersion(
                effective_date=date(202, 8, 11), amount=Decimal("165.30"),
            ))
            with pytest.raises(IntegrityError, match="effective_date_range"):
                db.session.flush()
            db.session.rollback()

    def test_deleting_a_template_takes_its_series(self, app, db, seed_user):
        """The FK cascades, so a hard-deleted template leaves no orphan history."""
        with app.app_context():
            template = _txn_template(seed_user)
            _seed_geico_history(template)
            db.session.commit()
            template_id = template.id

            db.session.execute(db.text(
                "DELETE FROM budget.transaction_templates WHERE id = :t"
            ), {"t": template_id})
            db.session.commit()

            remaining = db.session.execute(db.text(
                "SELECT count(*) FROM budget.template_amount_versions "
                "WHERE transaction_template_id = :t"
            ), {"t": template_id}).scalar()
            assert remaining == 0
