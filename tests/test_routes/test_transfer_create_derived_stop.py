"""A stop stated at CREATE for a loan whose payment this would be is refused.

Plan step **R7d-f-3**, ruling **R-R60**.  The generic transfer form offers
every active account as a destination, so a loan's recurring payment can be
created there -- and until this step the form accepted a closing bound for it
that the next loan edit silently overwrote with the payoff, while the composed
door read the stored date as the chokepoints' cache from the start (plan
ledger row **N-512**).  The edit form has refused such a bound since plan step
R7b-4 (``LOAN_PAYMENT_BOUND_IS_DERIVED``); this is the create door's half,
landed beside ``settle_first_occurrence`` (in ``_loan_destination`` since plan
step R7d-f-5) because the route module stands at pylint's line cap.

**What "stated" means at create is a REAL stop -- a date or a count -- and
not the key's presence** (developer ruling 2026-09-12).  The create form's
server render cannot lock the "Ends" control, so it emits the select enabled
with "Never" preselected and an ordinary browser posts ``never`` unless the
script's lock ran; refusing on presence would make the script the control,
which ruling **R-R60** demotes to an affordance.  Two cases here pin that
reading, and their docstrings say what a presence rule would do to them.

**Re-cut at plan step R7d-g-2 under ruling R-R81**: the door derives the
start ONLY where the loan holds no active payment; a SECOND transfer's start
is its owner's -- required, and refused at or before the loan's origination
-- and its stop its owner's.  The second-transfer case and the affordance's
cases moved with that ruling and say so.

Every refusal case asserts that NOTHING was persisted, and every acceptance
case reads the stored bound back: a door that flashed the right sentence and
saved anyway, or saved a bound other than the one posted, would pass a
status-code assertion.  The browser's half -- that the row actually locks --
is ``tests/manual/verify_recurrence_form.py``'s, because rendered HTML cannot
tell a control a script disabled from one it never touched.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.category import Category
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import LOAN_PAYMENT_BOUND_IS_DERIVED
from app.routes._loan_destination import (
    SECOND_TRANSFER_STARTS_AFTER_ORIGINATION,
    loan_destination_locks,
)
from app.schemas.validation import RECURRENCE_NEEDS_A_START
from app.schemas.validation import end_bound_before_start_message
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_transfer_template,
)

#: The day every request here is made on, frozen so "today" cannot drift past
#: the schedule ``seed_periods`` builds (2026-01-02 through May) and so the
#: loan below has not originated yet when the payment is created -- the
#: shape ``test_loan_recurrence_start_bound`` measures the derived start on.
_TODAY = date(2026, 3, 1)

#: The loan's origination and its derived first installment (payment day 1 of
#: the month after origination, ``installment_calendar.first_installment_date``).
_ORIGINATION = date(2026, 4, 15)
_FIRST_INSTALLMENT = date(2026, 5, 1)

#: A stop AFTER the derived start, so the inverted-window twin stays quiet
#: and only the rule under test can refuse it.
_LATER_STOP = date(2030, 1, 1)

#: A stop BEFORE the derived start: both refusals apply, and which one speaks
#: is the order under test.
_EARLIER_STOP = date(2026, 1, 1)

#: The name every POST here gives its template, so "nothing was persisted"
#: is one query rather than a count that a fixture's own rows could move.
_NAME = "R7d-f-3 payment"


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


def _post_monthly_transfer(
    auth_client, seed_user, to_account, *, starts_on=None, **bound,
):
    """POST /transfers: a MONTHLY cadence, *starts_on* or none, and *bound*.

    What the create form posts for a payment-less loan destination: the
    "Starts on" control is locked and sends no key.  A SECOND transfer's
    row is open (ruling **R-R81**) and posts *starts_on*.  *bound* is the
    "Ends" control's three keys as the browser posts them -- nothing at all
    for a locked row, ``never`` for the untouched default, a mode and its one
    value for a real stop.

    Args:
        auth_client: The signed-in test client.
        seed_user: The owner fixture.
        to_account: The destination :class:`Account`.
        starts_on: The owner's typed start, or ``None`` for a locked row.
        **bound: ``recurrence_end_mode`` and its value input, if any.

    Returns:
        The Flask response, redirect unfollowed so the flash stays readable.
    """
    # One category per owner for every POST here: a case that posts more than
    # once (the origination sweep) must not trip
    # ``uq_categories_user_group_item`` on its second request.
    category = (
        db.session.query(Category)
        .filter_by(
            user_id=seed_user["user"].id, group_name="Debt", item_name="Loan",
        )
        .one_or_none()
    )
    if category is None:
        category = Category(
            user_id=seed_user["user"].id, group_name="Debt", item_name="Loan",
        )
        db.session.add(category)
        db.session.commit()
    with auth_client.application.app_context():
        monthly = cadence_payload(
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=starts_on, states_a_start=starts_on is not None,
        )
    return auth_client.post("/transfers", data={
        "name": _NAME,
        "from_account_id": str(seed_user["account"].id),
        "to_account_id": str(to_account.id),
        "amount": "1200.00",
        "category_id": str(category.id),
        **monthly,
        **bound,
    })


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _persisted(to_account):
    """Return the template this module's POST created into *to_account*, or None."""
    return (
        db.session.query(TransferTemplate)
        .filter_by(to_account_id=to_account.id, name=_NAME)
        .one_or_none()
    )


def _assert_refused_and_nothing_persisted(auth_client, resp, to_account):
    """The shared shape of every refusal here."""
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/transfers/new")
    assert LOAN_PAYMENT_BOUND_IS_DERIVED in _flashes(auth_client)
    assert _persisted(to_account) is None, (
        "the refusal flashed and the template was saved anyway"
    )
    assert not (
        db.session.query(Transfer)
        .filter(Transfer.to_account_id == to_account.id)
        .all()
    ), "a refused create must generate nothing"


class TestAStopStatedForANewLoanPayment:
    """The door: which "Ends" submissions a payment-less loan destination refuses."""

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
        """Both stop shapes, because the rule is about the stop and not its form.

        The loan holds no active recurring transfer, so the definition being
        created is its payment the moment it exists, and a loan payment
        carries no authored stop (ruling **R-R59**).

        NEGATIVE CONTROL: delete the ``_refuse_stop_on_a_new_loan_payment``
        call from ``settle_first_occurrence`` and both cases save -- the
        route redirects to the list and the template holds the posted bound.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(auth_client, seed_user, loan, **bound)

        _assert_refused_and_nothing_persisted(auth_client, resp, loan)

    def test_never_is_accepted_as_the_unbounded_rule(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The form's own default is not a stop the owner stated.

        The server renders the "Ends" select with "Never" preselected, so a
        browser whose script did not lock the row posts ``never`` for a stop
        it never chose -- and ``never`` authors exactly the unbounded rule
        the loan's payment carries.  A presence rule would refuse this create
        outright (developer ruling 2026-09-12, the value reading).

        NEGATIVE CONTROL: change ``bound == NEVER_ENDS`` to ``False`` in
        ``_refuse_stop_on_a_new_loan_payment`` and this is refused.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan, recurrence_end_mode="never",
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(loan)
        assert template is not None, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.end_date is None and rule.max_occurrences is None
        # The start was derived beside it: the same door, both bounds.
        assert rule.starts_on == _FIRST_INSTALLMENT

    def test_an_absent_bound_is_accepted(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """What the locked row posts: nothing, which the create reads as never.

        The affordance disables the control, and a disabled control sends no
        key at all.  This is the submission the browser produces when the
        script ran, and it must be the one the door was written for.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(auth_client, seed_user, loan)

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(loan)
        assert template is not None, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_second_transfer_into_a_paid_loan_keeps_its_stop_and_its_start(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A loan that already holds a payment makes both bounds the owner's.

        The standing payment is the loan's OLDEST active recurring transfer,
        so a second one is not the payment: its authored stop binds (ruling
        **R-R60**'s second-transfer clause) and, since plan step R7d-g-2, so
        does its authored START (ruling **R-R81**) -- the typed date is
        stored and the rows generate from it, not from the loan's first
        installment.  MOVED BY RULING: this case posted no start (the locked
        row's shape) and asserted the stop alone, while the door wrote the
        contract's start underneath.  Both are read back off the stored rule
        and the generated rows, not inferred from the redirect.

        NEGATIVE CONTROL: delete the ``loan_holds_no_active_payment`` gate
        from ``settle_first_occurrence``'s loan branch and this is refused
        (the stop) and, with the stop dropped, stored with the derived start.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        typed_start = date(2026, 5, 15)

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan, starts_on=typed_start,
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(loan)
        assert template is not None, _flashes(auth_client)
        assert template.recurrence_rule.end_date == _LATER_STOP
        assert template.recurrence_rule.starts_on == typed_start
        generated = (
            db.session.query(Transfer)
            .filter(Transfer.transfer_template_id == template.id)
            .order_by(Transfer.occurs_on)
            .all()
        )
        assert generated, "the second transfer generated nothing"
        assert generated[0].occurs_on == typed_start, (
            "a second transfer must generate from its owner's start"
        )

    def test_a_second_transfer_must_state_its_start(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A second transfer's start is required, like a savings transfer's.

        Ruling **R-R81**: nothing derives it, so a submission naming a
        cadence and no first occurrence is refused with the schema's own
        sentence -- the one a savings destination meets -- rather than
        silently given the loan's.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()

        resp = _post_monthly_transfer(auth_client, seed_user, loan)

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers/new")
        flashed = _flashes(auth_client)
        for message in RECURRENCE_NEEDS_A_START["starts_on"]:
            assert message in flashed, flashed
        assert _persisted(loan) is None

    def test_a_second_transfers_start_at_or_before_origination_is_refused(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The one thing the loan still says about a second transfer's start.

        Ruling **R-R81**: refused at or before the loan's origination with
        the door's own sentence.  The transfer service's R-C floor is the
        same fact, but it refuses the first generated ROW after the template
        and its rule are flushed; the door refuses the DATE.  Both the
        boundary day and one before it, because the service's boundary is
        ``<=`` and this must match it; the day after is the control.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        refusal = SECOND_TRANSFER_STARTS_AFTER_ORIGINATION.format(
            loan="Mortgage", origination="Apr 15, 2026",
        )

        for typed in (_ORIGINATION, date(2026, 4, 14)):
            resp = _post_monthly_transfer(
                auth_client, seed_user, loan, starts_on=typed,
            )
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/transfers/new")
            assert refusal in _flashes(auth_client)
            assert _persisted(loan) is None

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan, starts_on=date(2026, 4, 16),
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers"), (
            _flashes(auth_client)
        )
        assert _persisted(loan).recurrence_rule.starts_on == date(2026, 4, 16)

    def test_an_archived_payment_does_not_count(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Archiving is the door to stop a payment early; the next one is the payment.

        The identity reads ACTIVE transfers, so a loan whose only payment is
        archived holds none, and the definition created next is its payment
        -- with no stop of its own.  A door that counted archived rows would
        let a stop through here that the next loan edit overwrites.
        """
        loan = _mortgage(seed_user)
        archived = make_loan_payment_template(db.session, seed_user, loan)
        archived.is_active = False
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan,
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_refused_and_nothing_persisted(auth_client, resp, loan)

    def test_a_stop_before_the_derived_start_is_refused_as_a_stop_not_as_a_window(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A stop both before the start and not the owner's to state: one message.

        On a payment-less loan the owner cannot state a stop at all, whatever
        its date, so the derived-stop sentence speaks and the window sentence
        never does.  Until plan step R7d-g-2 the door also graded the stop
        against the derived start (for a SECOND transfer, whose start was
        derived then too) and this case pinned which of the two refusals
        spoke first; the window comparison went with the derivation of a
        second transfer's start (ruling **R-R81**), so what it pins now is
        that no window sentence is minted here at all.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan,
            recurrence_end_mode="on_date", end_date=_EARLIER_STOP.isoformat(),
        )

        _assert_refused_and_nothing_persisted(auth_client, resp, loan)
        assert end_bound_before_start_message(
            _EARLIER_STOP, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)

    def test_a_non_loan_destination_keeps_its_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The rule is scoped to loans; a savings transfer's stop is its owner's.

        CONTROL for the refusal's reach: ``loan_holds_no_active_payment``
        answers ``True`` of a savings account too, and ``settle_first_occurrence``
        asks it only of a configured loan.  A savings destination states
        its own start (nothing derives one), so the payload carries one.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Sav",
            anchor_balance=Decimal("100.00"),
        )
        db.session.commit()
        with auth_client.application.app_context():
            monthly = cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=_TODAY,
            )
        category = Category(
            user_id=seed_user["user"].id, group_name="Savings", item_name="Sav",
        )
        db.session.add(category)
        db.session.commit()

        resp = auth_client.post("/transfers", data={
            "name": _NAME,
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(savings.id),
            "amount": "50.00",
            "category_id": str(category.id),
            **monthly,
            "recurrence_end_mode": "on_date",
            "end_date": _LATER_STOP.isoformat(),
        })

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(savings)
        assert template is not None, _flashes(auth_client)
        assert template.recurrence_rule.end_date == _LATER_STOP


class TestTheCreateFormNamesTheDestinationsThatDeriveTheBounds:
    """The affordance's data: which loans lock both bound rows, from the server."""

    def test_the_one_set_the_form_emits(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Only a payment-less loan derives the bounds, and it is ONE set.

        Two loans: one already holding a payment, one not.  Only the
        payment-less one is in ``data-loan-account-ids-without-payment``,
        and the wider ``data-loan-account-ids`` the form carried for the
        "Starts on" row alone is gone.  MOVED BY RULING at plan step R7d-g-2
        (**R-R81**): the paid loan was in that wider set because the door
        derived a second transfer's start (plan ledger row **D50**); a
        second transfer's start is its owner's now, so no row locks for it.

        NEGATIVE CONTROL: drop the ``loan_holds_no_active_payment`` filter
        from ``_loan_destination_lock_set`` and the paid loan appears, so the
        script would lock two rows the door leaves the owner's.
        """
        with app.app_context():
            paid = _mortgage(seed_user, name="Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, name="Unpaid")
            db.session.commit()

            html = auth_client.get("/transfers/new").data.decode()

            container = html.split('id="recurrence-fields"')[1].split(">")[0]
            assert 'data-loan-account-ids="' not in container
            derived_set = container.split(
                'data-loan-account-ids-without-payment="',
            )[1].split('"')[0]
            assert derived_set == str(unpaid.id)

    def test_the_locks_producer(self, app, seed_user, seed_periods):  # pylint: disable=unused-argument
        """The value the route emits, read directly: ascending, payment-less only.

        An owner with no loan gets an empty set, so the script listens for
        nothing.  An archived payment does not keep a loan out of the set,
        for the reason the door gives.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            empty = loan_destination_locks(user_id)
            assert empty.derived_for == ()

            paid = _mortgage(seed_user, name="Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, name="Unpaid")
            was_paid = _mortgage(seed_user, name="Was Paid")
            archived = make_loan_payment_template(db.session, seed_user, was_paid)
            archived.is_active = False
            db.session.commit()

            locks = loan_destination_locks(user_id)
            assert locks.derived_for == (unpaid.id, was_paid.id)
            assert locks.pinned_reason is None

    def test_the_ends_help_carries_both_sentences_on_the_transfer_forms_alone(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The copy the script swaps rides on the row only where the row can lock.

        The transfer form's destination is chosen in the form on a create and
        can be changed on an edit, so its "Ends" help carries the locked and
        the open sentence for the script to choose between -- on BOTH renders
        since plan step R7d-f-5.  **REVERSED for the edit form by ruling**
        (developer 2026-09-12, ruling **R-R79**; CLAUDE.md rule 5's
        exception): until then the edit render passed
        ``swappable=(template is none)`` and this case asserted it carried
        neither, on the premise that an edit form "already knows" its
        destination -- false once the update door derives bounds for a
        destination MOVE (plan step R7d-f-4).  The transaction form still
        carries neither: it has no destination and can never be a loan
        payment.  The locked sentence holds the word "updated", which
        ``test_template_flags`` asserts no transaction page renders without a
        save behind it.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            stored = make_transfer_template(
                db.session, seed_user, to_account=savings,
            )
            db.session.commit()

            create_form = auth_client.get("/transfers/new").data.decode()
            edit_form = auth_client.get(
                f"/transfers/{stored.id}/edit",
            ).data.decode()
            transaction_form = auth_client.get("/templates/new").data.decode()

        def ends_help_tag(html):
            return html.split('id="end-bound-help"')[1].split(">")[0]

        for page in (create_form, edit_form):
            assert "data-locked-text=" in ends_help_tag(page)
            assert "data-open-text=" in ends_help_tag(page)
        assert "data-locked-text=" not in ends_help_tag(transaction_form)
        assert "data-open-text=" not in ends_help_tag(transaction_form)
