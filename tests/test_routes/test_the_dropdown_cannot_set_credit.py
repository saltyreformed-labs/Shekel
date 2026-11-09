"""
Shekel Budget App -- no Status pick puts a row in Credit (plan task plan#3057)

Finding plan#2133 (review E of plan#2890): the popover's Status dropdown
offered Credit on a Projected row, and a Save picking it set the bare Credit
status -- no CC Payback, no movement on the card -- so the row's $800 left
checking's projection and nothing owed it anywhere.  Since plan#2929 the
Credit button puts a row on the card as Paid with its movement there (rulings
plan#2887, plan#2888), so the stored status is the old rows' alone: the state
machine admits no move into it, the dropdown offers Credit only on a row drawn
Credit (a row on the card, an old Credit row), and a Save posting it anywhere
else -- a crafted request, or a page drawn before this task -- is refused in
words and writes nothing.

**The example**: a $800.00 Groceries row on checking in the paycheck holding
today, dated today, with a Visa in the app.

Review L4 of plan#3054 is here too: a status a row on the card cannot take is
refused naming Credit, the status it is drawn as, not the stored Paid.
"""

import re
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import credit_workflow, status_seam, transaction_service
from app.utils.balance_predicates import is_balance_contributing
from app.utils.dates import display_today
from tests._cards import add_card
from tests._legacy_credit import plant_old_credit
from tests._test_helpers import add_txn, one_off_row_of
from tests.test_routes._statement_forms import ReconcileFormReader

_EIGHT_HUNDRED = Decimal("800.00")
# The paycheck ``seed_periods_today`` puts today in.
_TODAY_PERIOD = 4


def _status(member):
    """The ``ref.statuses.id`` of *member*."""
    return ref_cache.status_id(member)


def _groceries(seed_user, periods, *, is_income=False):
    """The $800.00 Groceries row on checking, dated today, with a Visa, committed."""
    add_card(seed_user)
    db.session.commit()
    row = add_txn(
        db.session, seed_user, periods[_TODAY_PERIOD], "Groceries", _EIGHT_HUNDRED,
        due_date=display_today(), is_income=is_income,
    )
    db.session.commit()
    return row


def _fresh(row_id):
    """The row read again from the database."""
    db.session.expire_all()
    return db.session.get(Transaction, row_id)


def _popover(auth_client, row_id):
    """The full-edit popover's HTML."""
    response = auth_client.get(f"/transactions/{row_id}/full-edit")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _status_option(html, member):
    """The opening tag of the Status dropdown's option for *member*."""
    select = re.search(r'<select name="status_id".*?</select>', html, re.S).group(0)
    return re.search(rf'<option value="{_status(member)}"[^>]*>', select, re.S).group(0)


def _credit_option(html):
    """The opening tag of the Status dropdown's Credit option."""
    return _status_option(html, StatusEnum.CREDIT)


def _the_save(html, **overrides):
    """What a browser's Save posts for the popover's form, with *overrides* set.

    Every control the form renders, in order, as werkzeug reads a browser's
    post (a repeated name keeps its first value); an override replaces a
    control's value, which is how a page drawn before this task, whose
    dropdown still offered Credit, posts it.
    """
    start = html.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    fields = MultiDict(reader.fields)
    for name, value in overrides.items():
        fields.setlist(name, [value])
    return fields


class TestTheDropdownsCredit:
    """Credit is offered only as the status a row is drawn as; elsewhere it points to the button."""

    def test_a_projected_expense_points_credit_at_the_button(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Disabled, titled for the Credit button; Paid and Cancel stay offered."""
        row = _groceries(seed_user, seed_periods_today)

        html = _popover(auth_client, row.id)

        option = _credit_option(html)
        assert "disabled" in option
        assert 'title="Press Credit above to put this on a card"' in option
        assert 'aria-label="Mark as credit card"' in html, "the button is the door"
        for member in (StatusEnum.PROJECTED, StatusEnum.DONE, StatusEnum.CANCELLED):
            assert "disabled" not in _status_option(html, member), member

    def test_an_income_row_says_it_is_not_reachable(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Income offers no Credit button, so the option names no button."""
        row = _groceries(seed_user, seed_periods_today, is_income=True)

        option = _credit_option(_popover(auth_client, row.id))

        assert "disabled" in option
        assert 'title="Not reachable from Projected"' in option

    def test_an_old_credit_row_still_selects_credit(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """An old Credit row keeps Credit, selected and offered: staying put is its move."""
        row = _groceries(seed_user, seed_periods_today)
        plant_old_credit(row)
        db.session.commit()

        option = _credit_option(_popover(auth_client, row.id))

        assert "selected" in option and "disabled" not in option


class TestASaveCannotSetCredit:
    """A Save posting Credit for a row not in it is refused, and nothing is written."""

    def test_a_stale_save_posting_credit_writes_nothing(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Finding plan#2133's Save: 400 in words, the row Projected and still counted.

        Before this task: 200, status Credit, no payback, no movement, and the
        $800 out of checking's projection (review E).
        """
        row = _groceries(seed_user, seed_periods_today)
        version = row.version_id

        response = auth_client.patch(
            f"/transactions/{row.id}",
            data=_the_save(
                _popover(auth_client, row.id),
                status_id=str(_status(StatusEnum.CREDIT)),
                notes="went on the Visa",
            ),
        )

        assert response.status_code == 400
        body = response.get_data(as_text=True)
        assert (
            "Status cannot put Groceries on a credit card: open it and press "
            "Credit instead."
        ) in body
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.PROJECTED)
        assert (after.notes, after.version_id) == (None, version), "nothing written"
        assert not after.entries, "no movement on the card"
        assert credit_workflow.get_active_payback(row.id) is None
        assert is_balance_contributing(after), "the $800 stays in checking's projection"

    def test_a_row_paid_from_checking_is_told_the_same(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Paid on checking: Credit refused in the same words; it stays Paid on checking."""
        row = _groceries(seed_user, seed_periods_today)
        transaction_service.settle_transaction(row)
        db.session.commit()

        response = auth_client.patch(
            f"/transactions/{row.id}",
            data=_the_save(
                _popover(auth_client, row.id),
                status_id=str(_status(StatusEnum.CREDIT)),
            ),
        )

        assert response.status_code == 400
        assert (
            "Status cannot put Groceries on a credit card: set it back to "
            "Projected, then open it and press Credit."
        ) in response.get_data(as_text=True)
        after = _fresh(row.id)
        assert after.status_id == _status(StatusEnum.DONE)
        movement = status_seam.covering_movement_of(after)
        assert (movement.account_id, movement.amount) == (
            seed_user["account"].id, _EIGHT_HUNDRED,
        )


    def test_an_income_row_is_told_it_goes_on_no_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Income: refused in its own words; it stays Projected."""
        row = _groceries(seed_user, seed_periods_today, is_income=True)

        response = auth_client.patch(
            f"/transactions/{row.id}",
            data=_the_save(
                _popover(auth_client, row.id),
                status_id=str(_status(StatusEnum.CREDIT)),
            ),
        )

        assert response.status_code == 400
        assert (
            "Status cannot put Groceries on a credit card: only an expense can "
            "go on a card."
        ) in response.get_data(as_text=True)
        assert _fresh(row.id).status_id == _status(StatusEnum.PROJECTED)

class TestAnOldCreditRowOnATrackedItem:
    """The deleted gate refused a Save posting Credit for an old Credit row whose item tracks purchases.

    A browser's Save posted no status there before this task: the dropdown
    drew the selected Credit option disabled, and a disabled option is not
    submitted.  It is offered now, as on any old Credit row, so the Save
    posts Credit and the door reads the identity move.
    """

    def test_its_untouched_save_keeps_it_and_its_payback(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Credit to Credit moves nothing: the Save writes its note, and the payback stays."""
        add_card(seed_user)
        row = one_off_row_of(
            seed_periods_today[_TODAY_PERIOD],
            name="Groceries",
            amount=_EIGHT_HUNDRED,
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Groceries"].id,
            due_date=display_today(),
        )
        payback = plant_old_credit(row)
        db.session.commit()
        # Tracking turned on after the press: the popover's checkbox is drawn
        # on a placed row whatever its status.
        row.template.is_envelope = True
        db.session.commit()
        assert _fresh(row.id).tracks_purchases, "precondition: its item tracks purchases"

        html = _popover(auth_client, row.id)
        assert "disabled" not in _credit_option(html), "a browser posts only an enabled option"
        save = _the_save(html, notes="from the old press")
        assert save["status_id"] == str(_status(StatusEnum.CREDIT)), "the Save posts Credit"
        response = auth_client.patch(f"/transactions/{row.id}", data=save)

        assert response.status_code == 200, response.get_data(as_text=True)
        after = _fresh(row.id)
        assert (after.status_id, after.notes) == (
            _status(StatusEnum.CREDIT), "from the old press",
        )
        assert credit_workflow.get_active_payback(row.id).id == payback.id


class TestARowOnTheCardIsRefusedInItsDrawnWords:
    """Review L4: a move a row on the card cannot make names Credit, never the stored Paid."""

    def test_cancelled_is_refused_naming_credit(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelled posted for a row on the card: 400, it stays on the card."""
        row = _groceries(seed_user, seed_periods_today)
        transaction_service.mark_as_credit(row)
        db.session.commit()

        response = auth_client.patch(
            f"/transactions/{row.id}",
            data=_the_save(
                _popover(auth_client, row.id),
                status_id=str(_status(StatusEnum.CANCELLED)),
            ),
        )

        assert response.status_code == 400
        body = response.get_data(as_text=True)
        assert (
            "Groceries is on the card, so its Status can only stay Credit or go "
            "back to Projected, which takes it off the card."
        ) in body
        assert "from Paid" not in body
        assert status_seam.settled_on_a_card(_fresh(row.id))
