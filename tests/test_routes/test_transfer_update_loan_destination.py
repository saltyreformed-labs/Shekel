"""The transfer UPDATE door takes the create door's two loan-destination rules.

Plan step **R7d-f-4**, plan ledger row **REC-521**, rulings **R-R76** and
**R-R77**.  The create door derives a loan destination's first occurrence and
refuses a stop stated where the loan holds no active payment; the update door
enforced neither, in two shapes: a "does not repeat" transfer into a
payment-less loan given a cadence (no rule, so ``is_standing_loan_payment``
answered ``False`` and the authoring branch wrote the owner's typed start and
stop), and a recurring transfer into savings MOVED onto a payment-less loan
(the identity was judged against savings, then the field loop moved the
column).  Either way the row became the loan's standing payment with an
owner's word in both bound columns.  ``settle_destination_for_update`` now
runs ahead of the recurrence step and settles the definition the edit LEAVES.

**Re-cut at plan step R7d-g-2 under ruling R-R81**: the door derives the
start ONLY where the destination loan holds no active payment; a SECOND
transfer's start is its owner's, required and refused at or before the
loan's origination (``SECOND_TRANSFER_STARTS_AFTER_ORIGINATION``), and its
stop is its owner's on the update door's ordinary reading.  The paid-loan
cases below moved with that ruling and say so.

**Every case reads the stored rule back**, never the redirect alone: a door
that flashed the right sentence and saved anyway, or saved the typed start
beside the right stop, passes a status-code assertion.  Every refusal asserts
the destination column and the rule are exactly as they were.  The dates are
the create door's own (``test_transfer_create_derived_stop``): a mortgage
originating 2026-04-15 with payment day 1 derives its first installment on
2026-05-01, and today is frozen ahead of both.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import RecurrenceUnit
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import LOAN_PAYMENT_BOUND_IS_DERIVED
from app.routes._loan_destination import (
    LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
    SECOND_TRANSFER_STARTS_AFTER_ORIGINATION,
)
from app.schemas.validation import end_bound_before_start_message
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    UNREADABLE_CADENCE_MESSAGE,
    EndsOnDate,
    RecurrenceSpec,
    reauthor_rule,
    recurrence_spec,
    resolve,
    stored_cadence,
)
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_transfer_template,
    state_template_price,
)
from tests._definition_price import amount_box_payload

#: The day every request here is made on, frozen ahead of the loan's
#: origination and inside the schedule ``seed_periods`` builds.
_TODAY = date(2026, 3, 1)

#: The loan's origination and the first installment its contract implies
#: (payment day 1 of the month after origination).
_ORIGINATION = date(2026, 4, 15)
_FIRST_INSTALLMENT = date(2026, 5, 1)

#: The start an OWNER types into the open "Starts on" box.  After origination,
#: so the only thing separating it from the derived date is the derivation
#: itself -- a typed date before origination would also be refused by the
#: transfer service at generation (ruling R-C), which would hide a missing
#: derivation behind a different failure.
_TYPED_START = date(2026, 5, 15)

#: A stop AFTER the derived start, so the inverted-window twin stays quiet and
#: only the rule under test can refuse it.
_LATER_STOP = date(2030, 1, 1)

#: A stop BEFORE the derived start: both refusals apply, and which one speaks
#: is the order under test.
_EARLIER_STOP = date(2026, 1, 1)

#: The stop a savings transfer STORES before it is moved onto a loan.  After
#: the derived start, so a move onto a paid loan keeps it without inverting.
_STORED_STOP = date(2027, 1, 1)

#: A stored stop that PRECEDES the derived start: valid for the savings
#: transfer that stores it, inverted the moment the start is derived.
_STORED_STOP_BEFORE_START = date(2026, 4, 1)

#: A start a savings transfer STORES that falls after the loan's origination,
#: so a move onto a PAID loan keeps it (ruling **R-R81**): the every-paycheck
#: fixture's own start is the schedule's opening payday, before origination,
#: which such a move refuses.
_STORED_START_AFTER_ORIGINATION = date(2026, 5, 15)

#: The refusal a second transfer meets for a start at or before origination.
_ORIGINATION_REFUSAL = SECOND_TRANSFER_STARTS_AFTER_ORIGINATION.format(
    loan="Mortgage", origination="Apr 15, 2026",
)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today ahead of the loan's origination."""
    freeze_today(monkeypatch, _TODAY)


def _mortgage(seed_user, name="Mortgage"):
    """Return a mortgage originating after today, payment day 1."""
    return create_loan_account(
        seed_user, db.session, name=name,
        principal=Decimal("200000.00"), rate=Decimal("0.05000"),
        term=360, origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


def _savings(seed_user, name="Savings"):
    """Return a savings account of the owner's."""
    return create_account_of_type(
        seed_user, db.session, "Savings", name, anchor_balance=Decimal("100.00"),
    )


def _one_off_into(seed_user, account, name="R7d-f-4 one-off"):
    """Return a COMMITTED transfer template into *account* that does not repeat.

    The definition ``POST /transfers`` creates for its default "Does not
    repeat" selection: a template with no rule, priced through the one write
    door.  Committed because the cases issue requests, and a request holds
    its own transaction.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=account.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("200.00"))
    db.session.commit()
    return template


def _recurring_into(seed_user, account, *, stop=None, start=None):
    """Return a COMMITTED every-paycheck transfer template into *account*.

    With *stop* stored as its authored closing bound when given -- the
    owner's own word about a savings transfer, written through the write
    door and asserted back -- and *start* as its first occurrence when given
    (the fixture's own is the schedule's opening payday).
    """
    template = make_transfer_template(db.session, seed_user, to_account=account)
    if stop is not None or start is not None:
        rule = template.recurrence_rule
        spec = recurrence_spec(rule)
        if stop is not None:
            spec = replace(spec, end_bound=EndsOnDate(on=stop))
        if start is not None:
            spec = replace(spec, starts_on=start)
        reauthor_rule(rule, spec, calendar_for(seed_user["user"].id))
        if stop is not None:
            assert rule.end_date == stop, "precondition: the stop is stored"
    db.session.commit()
    return template


def _first_installment_as_stored_by_an_every_paycheck_rule(seed_user):
    """Return the ``starts_on`` an every-paycheck rule stores for the derived start.

    A ``PERIOD``-unit rule's occurrences are PAYDAYS, so the resolver
    normalises its first occurrence onto the payday of the paycheck that
    funds it and that is what the column holds (plan ledger row **D39**
    records the drift; ``loan_recurrence_sync._sync_loan_cadence`` the
    two-stage comparison it forces).  Asked of the resolver rather than
    hand-computed, because which paycheck covers 2026-05-01 is the
    schedule's answer and not this module's.
    """
    return resolve(
        RecurrenceSpec(
            user_id=seed_user["user"].id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=_FIRST_INSTALLMENT,
        ),
        calendar_for(seed_user["user"].id),
    ).starts_on


def _cadence(unit=RecurrenceUnitEnum.MONTH, *, starts_on=_TYPED_START):
    """Return the cadence keys a browser posts, with the OWNER's typed start."""
    return cadence_payload(unit=unit, starts_on=starts_on)


def _post_update(auth_client, template, **fields):
    """POST the edit form for *template*; redirect unfollowed so the flash stays.

    Carries what the form's non-recurrence controls emit for an unchanged
    definition -- name, the Amount box where the form shows one
    (:func:`amount_box_payload`), source account, the optimistic-lock counter --
    so every case walks the route the browser does (the amount write door,
    the source-account ownership probe, the version check), and *fields*
    states what THIS case changes or posts for the recurrence controls.  A
    case that omits a recurrence key is a partial submission on purpose.
    """
    return auth_client.post(f"/transfers/{template.id}", data={
        "name": template.name,
        **amount_box_payload(template),
        "from_account_id": str(template.from_account_id),
        "version_id": str(template.version_id),
        **fields,
    })


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _reload(template):
    """Return *template*'s row as the request left it."""
    db.session.expire_all()
    return db.session.get(TransferTemplate, template.id)


def _generated_into(account):
    """Return the live generated transfers into *account*, earliest first."""
    return (
        db.session.query(Transfer)
        .filter(
            Transfer.to_account_id == account.id,
            Transfer.transfer_template_id.isnot(None),
            Transfer.is_deleted.is_(False),
        )
        .order_by(Transfer.occurs_on)
        .all()
    )


def _assert_saved(resp, auth_client):
    """The shared shape of every acceptance here: the list page, no refusal."""
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/transfers"), _flashes(auth_client)


def _assert_refused_to_the_edit_form(resp, template, message, auth_client):
    """The shared shape of every refusal here."""
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/transfers/{template.id}/edit")
    assert message in _flashes(auth_client)


class TestACadenceAddedToAOneTimeTransferIntoALoan:
    """REC-521's first shape: the authoring branch takes the create rules."""

    @pytest.mark.parametrize("bound", [
        pytest.param(
            {"recurrence_end_mode": "on_date",
             "end_date": _LATER_STOP.isoformat()},
            id="on a date",
        ),
        pytest.param(
            {"recurrence_end_mode": "after_occurrences", "max_occurrences": "12"},
            id="after a count",
        ),
    ])
    def test_a_real_stop_is_refused_where_the_loan_holds_no_payment(
        self, auth_client, seed_user, seed_periods, bound,  # pylint: disable=unused-argument
    ):
        """Both stop shapes, and NOTHING is authored.

        The loan holds no active recurring transfer, so the definition would
        be its payment the moment it repeats, and a loan payment carries no
        authored stop (ruling **R-R59**).  Before this step the authoring
        branch wrote the typed start and this stop.

        NEGATIVE CONTROL: delete the ``settle_destination_for_update`` call
        from ``update_transfer_template`` and both cases save with the posted
        bound and ``starts_on == _TYPED_START``.
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(), **bound,
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_BOUND_IS_DERIVED, auth_client,
        )
        template = _reload(template)
        assert template.recurrence_rule is None, "a refused edit authored a rule"
        assert template.to_account_id == loan.id
        assert _generated_into(loan) == []

    def test_never_writes_the_unbounded_rule_from_the_loans_first_installment(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The typed start is replaced by the loan's, and "Never" is the rule.

        The edit form's "Starts on" box is open for a definition with no
        rule, so an ordinary browser posts the date in it; the loan's first
        contractual installment is what the rule fires from, whatever was
        typed -- exactly what the create door does for a browser whose lock
        did not run.

        NEGATIVE CONTROL: replace the ``settle_loan_start`` call in
        ``settle_destination_for_update`` with a bare ``loan_cadence_start``
        (deriving without writing into the payload) and ``starts_on`` reads
        ``_TYPED_START``.
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(),
            recurrence_end_mode="never",
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None
        generated = _generated_into(loan)
        assert generated, "the definition generated nothing"
        assert generated[0].occurs_on == _FIRST_INSTALLMENT

    def test_an_absent_bound_writes_the_unbounded_rule(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """What a locked "Ends" row will post once R7d-f-5 locks it: nothing.

        The authoring branch already read an absent bound as "ends never";
        this pins that the settle leaves that reading standing beside the
        derived start (ruling **R-R77**'s authoring case).
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template, to_account_id=str(loan.id), **_cadence(),
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None

    def test_into_a_paid_loan_both_bounds_are_the_owners(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A loan already holding a payment makes this a SECOND transfer.

        Its stop is its owner's (ruling **R-R77**'s second-transfer clause)
        and, since plan step R7d-g-2, so is its start (ruling **R-R81**):
        the typed date is stored, not the loan's first installment.  MOVED
        BY RULING: until R7d-g-2 this case asserted the derived start beside
        the owner's stop, the derive-then-discard shape plan ledger row
        **D50** measured.

        NEGATIVE CONTROL: delete the ``loan_holds_no_active_payment`` gate
        from ``settle_destination_for_update`` and the start comes back
        derived.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(),
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _TYPED_START
        assert rule.end_date == _LATER_STOP
        generated = [
            row for row in _generated_into(loan)
            if row.transfer_template_id == template.id
        ]
        assert generated, "the second transfer generated nothing"
        assert generated[0].occurs_on == _TYPED_START, (
            "a second transfer must generate from its owner's start, not the "
            "loan's first installment"
        )

    def test_into_a_paid_loan_a_start_at_or_before_origination_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The one thing the loan still says about a second transfer's start.

        Ruling **R-R81**: refused at or before the loan's origination, with
        the door's own sentence -- the transfer service's R-C floor is the
        same fact, but it would refuse the first generated row after the
        definition was flushed.  Both the boundary day and one before it,
        because the service's boundary is ``<=`` and this must match it.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        template = _one_off_into(seed_user, loan)

        for typed in (_ORIGINATION, date(2026, 4, 14)):
            resp = _post_update(
                auth_client, template,
                to_account_id=str(loan.id), **_cadence(starts_on=typed),
            )
            _assert_refused_to_the_edit_form(
                resp, template, _ORIGINATION_REFUSAL, auth_client,
            )
            assert _reload(template).recurrence_rule is None

        # The control: the day after originates.
        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=date(2026, 4, 16)),
        )
        _assert_saved(resp, auth_client)
        assert _reload(template).recurrence_rule.starts_on == date(2026, 4, 16)

    def test_on_a_paid_loan_the_schema_grades_the_owners_pair(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A second transfer's window is graded against ITS OWN start.

        MOVED BY RULING at plan step R7d-g-2 (**R-R81**): this case posted a
        typed start before origination and a stop after it, and asserted the
        window sentence against the DERIVED start.  Nothing is derived for a
        second transfer now, so the schema's own comparison
        (``require_end_bound_after_start``) grades the posted pair: a stop
        before the typed start is refused with the same sentence, and neither
        the derived-stop nor the origination sentence speaks.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=_TYPED_START),
            recurrence_end_mode="on_date", end_date=_EARLIER_STOP.isoformat(),
        )

        _assert_refused_to_the_edit_form(
            resp, template,
            end_bound_before_start_message(_EARLIER_STOP, _TYPED_START),
            auth_client,
        )
        assert LOAN_PAYMENT_BOUND_IS_DERIVED not in _flashes(auth_client)
        assert _ORIGINATION_REFUSAL not in _flashes(auth_client)
        assert _reload(template).recurrence_rule is None

    def test_a_stop_before_the_derived_start_is_refused_as_a_stop_not_as_a_window(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """On a payment-less loan the derived-stop sentence speaks; no window sentence exists.

        The create door's twin case says why: since plan step R7d-g-2 the
        doors grade no stop against a derived start (ruling **R-R81**), so a
        real stop here is refused for being stated at all, whatever its date.
        """
        loan = _mortgage(seed_user)
        template = _one_off_into(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=date(2025, 12, 1)),
            recurrence_end_mode="on_date", end_date=_EARLIER_STOP.isoformat(),
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_BOUND_IS_DERIVED, auth_client,
        )
        assert end_bound_before_start_message(
            _EARLIER_STOP, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)
        assert _reload(template).recurrence_rule is None

    def test_a_savings_destination_keeps_the_owners_start_and_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL for the rule's reach: nothing is derived for a savings account."""
        savings = _savings(seed_user)
        template = _one_off_into(seed_user, savings)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(savings.id), **_cadence(),
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule is not None, _flashes(auth_client)
        assert rule.starts_on == _TYPED_START
        assert rule.end_date == _LATER_STOP


class TestARecurringTransferMovedOntoALoan:
    """REC-521's second shape: the destination move takes the create rules."""

    def test_the_stored_stop_re_posted_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """What an ordinary browser posts: the stored stop, still in the open control.

        Refused, and the definition is exactly as it was: still into savings,
        its start and its stop untouched.

        NEGATIVE CONTROL: delete the ``settle_destination_for_update`` call
        and the move proceeds with the savings transfer's own start (the
        schedule's opening payday); regeneration then meets the transfer
        service's R-C refusal of a payment before origination, unhandled on
        this route -- measured: ``ValidationError: Cannot pay 'Mortgage'
        before it originates`` -- which is REC-521's own "a refusal on an
        ordinary edit" price, and this case fails on it.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
            recurrence_end_mode="on_date", end_date=_STORED_STOP.isoformat(),
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_BOUND_IS_DERIVED, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.starts_on == stored_start
        assert template.recurrence_rule.end_date == _STORED_STOP
        assert _generated_into(loan) == []

    def test_never_moves_it_with_the_derived_start_and_no_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The owner sets "Never" and the move lands as the loan's payment.

        The re-point branch reads the PRESENT ``starts_on`` the settle wrote,
        so the stored savings start is replaced by the loan's first
        installment; the generated rows into the loan begin there and not
        before origination (the $3,220.92 phantom-debit shape).
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id),
            **_cadence(starts_on=stored_start),
            recurrence_end_mode="never",
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None
        generated = _generated_into(loan)
        assert generated, "the moved definition generated nothing"
        assert generated[0].occurs_on == _FIRST_INSTALLMENT

    def test_an_absent_bound_writes_the_unbounded_rule_over_a_stored_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Ruling **R-R77**: nothing stated makes the stop the loan's.

        The update door's ordinary reading of an absent bound -- leave the
        stored one alone -- would carry ``_STORED_STOP`` onto the loan's
        standing payment, where the door reads it as the chokepoints' cache
        and the next loan edit overwrites it (the N-512 shape).  The stored
        stop was the owner's word about a savings transfer, not about the
        loan; and a locked "Ends" row posts nothing, so this is the reading
        the affordance R7d-f-5 adds must be able to lock under.

        NEGATIVE CONTROL: return ``end_bound`` instead of ``NEVER_ENDS`` from
        ``_settle_standing_payment`` and ``end_date`` reads ``_STORED_STOP``
        (the arm is one line since plan step R7d-g-2: the standing branch
        hands the unbounded rule on unconditionally, the stored bound being
        graded nowhere).
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_partial_submission_is_completed_from_the_stored_cadence(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A move that restates no cadence still derives the start.

        The re-point branch runs only when the cadence is submitted, so the
        settle completes the payload from the stored row: the cadence is
        unchanged, the start is the loan's, the stop is the loan's (ruling
        **R-R77**).

        NEGATIVE CONTROL: delete the three ``data[...] =`` writes from
        ``_cadence_unit_to_settle`` and the row moves with its savings start.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        before = stored_cadence(template.recurrence_rule)

        resp = _post_update(auth_client, template, to_account_id=str(loan.id))

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert stored_cadence(rule) == before
        assert rule.starts_on == (
            _first_installment_as_stored_by_an_every_paycheck_rule(seed_user)
        )
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_partial_move_of_an_unreadable_cadence_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A cadence the app cannot read cannot be completed, so the move is refused.

        The state ``UNREADABLE_CADENCE_MESSAGE`` names -- a ``unit_id`` the
        enums do not model -- manufactured the way ``test_recurrence_picker``
        manufactures it.  Without the refusal this would be an
        ``AttributeError`` on the reading, a 500.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings)
        surplus = RecurrenceUnit(name="Blue Moon")
        db.session.add(surplus)
        db.session.flush()
        template.recurrence_rule.unit_id = surplus.id
        db.session.commit()
        assert stored_cadence(template.recurrence_rule) is None, (
            "precondition: the cadence is unreadable"
        )

        resp = _post_update(auth_client, template, to_account_id=str(loan.id))

        _assert_refused_to_the_edit_form(
            resp, template, UNREADABLE_CADENCE_MESSAGE, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.unit_id == surplus.id

    def test_onto_a_paid_loan_both_stored_bounds_stay_the_owners(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A second transfer keeps its stored start AND stop; nothing is derived.

        CONTROL for ruling **R-R77**'s reach (the unbounded rule is written
        only where the loan derives the stop) and, since plan step R7d-g-2,
        for ruling **R-R81**'s: the stored start rides through the move
        untouched.  MOVED BY RULING: this case asserted the derived start
        beside the stored stop.  The stored start is after origination on
        purpose -- the fixture's own, the schedule's opening payday, is
        before it and the move would be refused for that (the next case).
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        savings = _savings(seed_user)
        template = _recurring_into(
            seed_user, savings,
            stop=_STORED_STOP, start=_STORED_START_AFTER_ORIGINATION,
        )
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == stored_start
        assert rule.end_date == _STORED_STOP

    def test_onto_a_paid_loan_a_stored_start_before_origination_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A stored start the loan predates refuses the move, not the window.

        The every-paycheck fixture's start is the schedule's opening payday
        (2026-01-02), before the loan originates; moving it onto a paid loan
        would generate rows the transfer service refuses (R-C), so the door
        refuses first with the origination sentence (ruling **R-R81**).
        Posted as a PARTIAL submission -- no cadence keys, the shape a form
        that changes only the destination sends -- so the start graded is the
        STORED one, which is the branch's second read.  MOVED BY RULING: this
        case stored a stop before the derived start and asserted the window
        sentence; nothing derives a start for a second transfer now, and a
        stored pair that was valid stays valid.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        savings = _savings(seed_user)
        template = _recurring_into(
            seed_user, savings, stop=_STORED_STOP_BEFORE_START,
        )
        stored_start = template.recurrence_rule.starts_on
        assert stored_start < _ORIGINATION, "precondition: the fixture's start"

        resp = _post_update(auth_client, template, to_account_id=str(loan.id))

        _assert_refused_to_the_edit_form(
            resp, template, _ORIGINATION_REFUSAL, auth_client,
        )
        assert end_bound_before_start_message(
            _STORED_STOP_BEFORE_START, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.starts_on == stored_start
        assert template.recurrence_rule.end_date == _STORED_STOP_BEFORE_START

    def test_a_stored_stop_before_the_derived_start_is_replaced_where_the_loan_derives_the_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The one input where ruling **R-R77** and the window rule pull apart.

        A payment-less loan, a stored stop that PRECEDES the derived start,
        and nothing posted for "Ends": the stop is the loan's now, so the
        stored one is REPLACED by the unbounded rule and never graded against
        the start -- a door that let the stored bound through to the window
        rule would refuse this edit with a sentence about a stop the owner
        cannot see.  This is the case that makes the graded half of the
        R-R77 arm visible (its sibling above passes with that half disabled).

        Since plan step R7d-g-2 the standing branch grades no stored bound at
        all (``_settle_standing_payment`` refuses a REAL stated stop and hands
        ``NEVER_ENDS`` on; the window comparison went with ruling **R-R81**),
        so what this case pins is that the stored stop is neither graded nor
        kept.  NEGATIVE CONTROL: return ``end_bound`` instead of
        ``NEVER_ENDS`` there and ``end_date`` reads the stored stop.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template = _recurring_into(
            seed_user, savings, stop=_STORED_STOP_BEFORE_START,
        )
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
        )

        _assert_saved(resp, auth_client)
        assert end_bound_before_start_message(
            _STORED_STOP_BEFORE_START, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)
        template = _reload(template)
        assert template.to_account_id == loan.id
        rule = template.recurrence_rule
        assert rule.starts_on == _FIRST_INSTALLMENT
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_partial_move_onto_savings_leaves_the_rule_alone(
        self, auth_client, seed_user, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """CONTROL for the completion's reach: only a LOAN destination completes.

        A partial submission moving a repeating transfer onto another savings
        account submits no cadence, so the recurrence step takes no branch
        and the rule is not re-authored -- the partial-update contract for
        every non-loan destination.  Completed, the re-point branch would run
        a full ``reauthor_rule`` with the stored triple, which for a
        paycheck-cadence rule under a rebuilt schedule can re-normalise the
        start (plan ledger row **D39**'s shape).  The write door is replaced
        by a spy that fails the case if it is reached at all; an adversarial
        review of this step found the first cut completing before it knew the
        destination was a loan.

        NEGATIVE CONTROL: move the ``load_loan_params`` read in
        ``settle_destination_for_update`` below the ``_cadence_unit_to_settle``
        call and the spy fires.
        """
        savings = _savings(seed_user)
        other = _savings(seed_user, name="Other Savings")
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)
        stored_start = template.recurrence_rule.starts_on

        def _never(rule, spec, calendar):
            raise AssertionError(
                f"rule {rule.id} re-authored as {spec!r} on a move onto "
                f"savings ({calendar!r})",
            )

        # The name the re-point branch calls through; the fixture above wrote
        # its stop through the service's own binding before this runs.
        monkeypatch.setattr(
            "app.routes._recurrence_form_helpers.reauthor_rule", _never,
        )

        resp = _post_update(auth_client, template, to_account_id=str(other.id))

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == other.id
        assert template.recurrence_rule.starts_on == stored_start
        assert template.recurrence_rule.end_date == _STORED_STOP

    def test_a_one_time_transfer_moved_onto_a_loan_still_does_not_repeat(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A move that names no cadence authors none, whatever the destination.

        Both wire shapes: the browser's "Does not repeat" (a present empty
        unit) and a partial submission with no cadence key at all.  Nothing
        is derived because nothing repeats; the move itself lands.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        for shape in ({"recurrence_unit": ""}, {}):
            template = _one_off_into(
                seed_user, savings, name=f"one-off {len(shape)}",
            )

            resp = _post_update(
                auth_client, template, to_account_id=str(loan.id), **shape,
            )

            _assert_saved(resp, auth_client)
            template = _reload(template)
            assert template.to_account_id == loan.id
            assert template.recurrence_rule is None

    def test_a_clamped_payment_day_reaches_the_re_pointed_rule(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The nominal day travels through the RE-POINT branch, not only the authoring one.

        A servicer's day 31 first billing in a 30-day month: the derived pair
        is the month's last day plus ``nominal_day = 31``, and on a move the
        re-point branch reads ``nominal_day`` beside the PRESENT ``starts_on``
        the settle wrote (``update_recurrence_rule_from_form``).  A door that
        wrote the date alone would model every later installment a day early.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Day 31",
            principal=Decimal("200000.00"), rate=Decimal("0.05000"),
            term=360, origination_date=date(2026, 5, 15), payment_day=31,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings)
        stored_start = template.recurrence_rule.starts_on

        resp = _post_update(
            auth_client, template,
            to_account_id=str(loan.id), **_cadence(starts_on=stored_start),
            recurrence_end_mode="never",
        )

        _assert_saved(resp, auth_client)
        rule = _reload(template).recurrence_rule
        assert rule.starts_on == date(2026, 6, 30)
        assert rule.nominal_day == 31

    def test_moved_between_non_loan_accounts_nothing_is_derived(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL: a move onto another savings account keeps the owner's word."""
        savings = _savings(seed_user)
        other = _savings(seed_user, name="Other Savings")
        template = _recurring_into(seed_user, savings, stop=_STORED_STOP)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(other.id),
            **_cadence(),
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.to_account_id == other.id, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.starts_on == _TYPED_START
        assert rule.end_date == _LATER_STOP


class TestTheStandingPaymentCannotChangeDestination:
    """Ruling **R-R76**: a loan's standing payment stays on its loan."""

    def _standing_payment(self, seed_user, loan):
        """Return the loan's COMMITTED standing payment and its stored start."""
        template = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        return template, template.recurrence_rule.starts_on

    @pytest.mark.parametrize("elsewhere", ["savings", "another loan"])
    def test_moved_off_its_loan_is_refused(
        self, auth_client, seed_user, seed_periods, elsewhere,  # pylint: disable=unused-argument
    ):
        """Whatever the new destination is, and nothing is changed.

        What the browser posts for the standing payment: the cadence, and
        neither bound (both rows render locked).  Before this step the row
        moved, the loan was left with no payment projecting against it, and
        the old loan's cached payoff travelled with the row.

        NEGATIVE CONTROL: delete the ``is_standing_loan_payment`` arm from
        ``settle_destination_for_update`` and the row moves.
        """
        loan = _mortgage(seed_user)
        destination = (
            _savings(seed_user) if elsewhere == "savings"
            else _mortgage(seed_user, name="Van")
        )
        template, stored_start = self._standing_payment(seed_user, loan)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(destination.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            ),
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == loan.id
        assert template.recurrence_rule.starts_on == stored_start
        assert _generated_into(destination) == []

    def test_a_settings_carrying_second_payment_may_not_move_either(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The union the twin refusal covers, not the identity alone (**R-R76**).

        A generic recurring transfer is the loan's standing payment (older
        id); the loan dashboard's own door then writes a SECOND, DERIVE-mode
        payment with a settings row.  Not standing -- but its amount is
        priced off the loan it pays into, so moved to savings every read of
        its cash would refuse.  An adversarial review of this step found the
        first cut asking the identity alone; the developer ruled the union.

        NEGATIVE CONTROL: drop ``is_loan_payment(template)`` from the rule-1
        test in ``settle_destination_for_update`` and this moves.
        """
        loan = _mortgage(seed_user)
        standing = _recurring_into(seed_user, loan)
        second = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        assert second.id > standing.id, "precondition: the generic one is standing"
        savings = _savings(seed_user)

        resp = _post_update(
            auth_client, second,
            to_account_id=str(savings.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            ),
        )

        _assert_refused_to_the_edit_form(
            resp, second, LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, auth_client,
        )
        assert _reload(second).to_account_id == loan.id

    @pytest.mark.parametrize("shape", [
        pytest.param({}, id="a partial submission with no cadence key"),
        pytest.param({"recurrence_unit": ""}, id="a clear beside the move"),
    ])
    def test_the_move_is_refused_before_the_cadence_is_read(
        self, auth_client, seed_user, seed_periods, shape,  # pylint: disable=unused-argument
    ):
        """Rule 1 speaks first, whatever the submission says about the cadence.

        A partial submission moving the standing payment is refused before
        any cadence is read (the completion never runs); a "Does not repeat"
        beside the move is refused with THIS sentence and not
        ``LOAN_PAYMENT_CANNOT_BE_ONE_TIME`` -- the destination settle runs
        ahead of the recurrence refusals, and nothing else pins that order.
        """
        loan = _mortgage(seed_user)
        savings = _savings(seed_user)
        template, stored_start = self._standing_payment(seed_user, loan)

        resp = _post_update(
            auth_client, template, to_account_id=str(savings.id), **shape,
        )

        _assert_refused_to_the_edit_form(
            resp, template, LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == loan.id
        assert template.recurrence_rule.starts_on == stored_start

    def test_a_second_transfer_into_a_paid_loan_may_move(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL for the identity: only the STANDING payment is held.

        A second recurring transfer into a paid loan is not the loan's
        payment, so moving it onto savings is an ordinary edit and the
        loan keeps its payment.
        """
        loan = _mortgage(seed_user)
        standing, _ = self._standing_payment(seed_user, loan)
        savings = _savings(seed_user)
        second = _recurring_into(seed_user, loan)

        resp = _post_update(
            auth_client, second,
            to_account_id=str(savings.id),
            **_cadence(),
        )

        _assert_saved(resp, auth_client)
        assert _reload(second).to_account_id == savings.id, _flashes(auth_client)
        assert _reload(standing).to_account_id == loan.id

    def test_an_edit_that_keeps_the_destination_is_not_a_move(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL: a destination posted UNCHANGED is not a move.

        What every rename posted until plan step R7d-f-5; since then the
        standing payment's form renders its destination control disabled and
        posts NO key, and that payload is
        ``test_transfer_edit_form_locks.py``'s.  This case keeps the
        posted-unchanged shape: an API client, or a form rendered before the
        control locked.
        """
        loan = _mortgage(seed_user)
        template, stored_start = self._standing_payment(seed_user, loan)
        renamed = f"{template.name}, renamed"

        resp = _post_update(
            auth_client, template,
            name=renamed,
            to_account_id=str(loan.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            ),
        )

        _assert_saved(resp, auth_client)
        template = _reload(template)
        assert template.name == renamed, _flashes(auth_client)
        assert template.to_account_id == loan.id
        assert template.recurrence_rule.starts_on == stored_start


class TestTheDestinationIsOwnerCheckedBeforeItIsRead:
    """The settle reads the destination's loan terms, so ownership comes first."""

    def test_a_foreign_loan_as_the_new_destination_is_refused_unread(
        self, auth_client, seed_user, seed_second_user, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """Another owner's loan is refused before its terms are loaded.

        ``test_audit_fixes.py::test_update_transfer_template_with_foreign_to_account``
        already probes this route's destination; what this adds is the READ
        that made the order matter: ``load_loan_params`` is replaced by a spy
        that fails the case if the settle reaches it at all.  The refusal is
        the ownership sentence, to the edit form, and the definition is
        unchanged.

        NEGATIVE CONTROL: move the ``_first_unowned_template_fk`` block back
        below the settle in ``update_transfer_template`` and the spy fires.
        """
        foreign_loan = _mortgage(seed_second_user, name="Not Yours")
        savings = _savings(seed_user)
        template = _recurring_into(seed_user, savings)
        stored_start = template.recurrence_rule.starts_on

        def _never(account_id):
            raise AssertionError(
                f"loan terms read for account {account_id} before ownership",
            )

        # The package attribute the settle reads through
        # (``loan_loaders.load_loan_params``); the seam's own ``from``-import
        # binding is untouched, and nothing else on this path reaches it.
        monkeypatch.setattr("app.services.loan_loaders.load_loan_params", _never)

        resp = _post_update(
            auth_client, template,
            to_account_id=str(foreign_loan.id),
            **_cadence(),
        )

        _assert_refused_to_the_edit_form(
            resp, template, "Invalid destination account.", auth_client,
        )
        template = _reload(template)
        assert template.to_account_id == savings.id
        assert template.recurrence_rule.starts_on == stored_start
