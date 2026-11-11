"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_transfer_update_loan_destination.py``, moved verbatim by
``tools/split``.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._loan_destination import SECOND_TRANSFER_STARTS_AFTER_ORIGINATION
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    EndsOnDate,
    RecurrenceSpec,
    reauthor_rule,
    recurrence_spec,
    resolve,
)
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
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
