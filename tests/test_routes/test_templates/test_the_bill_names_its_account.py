"""Template route tests: the recurring bill's form names the account that pays it.

Task plan#2931 (rulings plan#2926, plan#3225): the form's account box says it is
the account the bill is "Paid from" ("Paid into" for income), a new bill opens on
the owner's primary account rather than whichever account sorts first, an edit
says what a change of account moves, and a bill whose account was archived keeps
it on an untouched save.
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    all_periods,
    create_account_of_type,
    create_savings_account,
    make_salary_profile,
    repriced_by_the_owner,
    set_default_grid_account,
    settle_cash_row,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

from ._helpers import _TODAYS_PERIOD_INDEX, _create_template


def _account_options(html):
    """Return ``[(id, selected, text)]`` for the bill form's account select.

    Args:
        html: The rendered form.

    Returns:
        One tuple per ``<option>`` of ``<select id="account_id">``, in order.
    """
    start = html.index('<select id="account_id"')
    select = html[start:html.index("</select>", start)]
    return [
        (int(value), "selected" in rest, " ".join(text.split()))
        for value, rest, text in re.findall(
            r'<option value="(\d+)"([^>]*)>(.*?)</option>', select, re.S,
        )
    ]


def _account_label(html):
    """Return the account box's label text, whitespace collapsed."""
    match = re.search(r'<label for="account_id"[^>]*>(.*?)</label>', html, re.S)
    return " ".join(match.group(1).split())


class TestANewBillOpensOnThePrimary:
    """GET /templates/new: the box is "Paid from", on the owner's primary."""

    def test_a_new_bill_preselects_checking_though_another_account_sorts_first(
        self, app, auth_client, seed_user,
    ):
        """A savings account ahead of checking in the list is offered, not picked.

        ``list_active_accounts`` orders by ``sort_order`` then name, so an
        account placed first is the option a browser picks when nothing is
        selected: before task plan#2931 a new bill opened on it.
        """
        with app.app_context():
            checking = seed_user["account"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            savings.sort_order = -1
            db.session.commit()
            savings_id = savings.id
            html = auth_client.get("/templates/new").get_data(as_text=True)
        options = _account_options(html)
        assert [account_id for account_id, _, _ in options] == [savings_id, checking]
        assert [account_id for account_id, selected, _ in options if selected] == [checking]
        assert _account_label(html) == "Paid from"
        # A new bill has no rows, so the note on what a change moves is not drawn.
        assert 'id="account-help"' not in html

    def test_the_primary_is_the_owners_saved_default_not_the_first_checking(
        self, app, auth_client, seed_user,
    ):
        """The primary is the grid's: the saved default account wins over checking."""
        with app.app_context():
            second = create_account_of_type(
                seed_user, db.session, "Checking", "Joint Checking",
            )
            db.session.commit()
            second_id = second.id
            set_default_grid_account(db.session, seed_user["user"].id, second_id)
            db.session.commit()
            html = auth_client.get("/templates/new").get_data(as_text=True)
        assert [
            account_id for account_id, selected, _ in _account_options(html) if selected
        ] == [second_id]

    @pytest.mark.usefixtures("seed_user")
    def test_a_new_income_says_paid_into_and_marks_the_income_type(
        self, app, auth_client,
    ):
        """``?type=income``: the label is "Paid into", and the script's hook is on Income.

        The label follows the Type box as it changes through
        ``bill_account_label.js``, which reads ``data-pays-into`` on the income
        option and the two wordings on the label; both are graded here because
        a script reading a missing attribute fails silently.
        """
        with app.app_context():
            income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            html = auth_client.get(
                "/templates/new", query_string={"type": "income"},
            ).get_data(as_text=True)
        assert _account_label(html) == "Paid into"
        assert f'<option value="{income_id}" data-pays-into' in html
        assert f'<option value="{expense_id}" data-pays-into' not in html
        assert 'data-from-label="Paid from" data-into-label="Paid into"' in html
        assert "js/bill_account_label.js" in html


class TestTheEditFormSaysWhatAMoveDoes:
    """GET /templates/<id>/edit: the note under the box, and an archived account."""

    def test_the_edit_form_names_the_box_and_says_what_a_change_moves(
        self, app, auth_client, seed_user,
    ):
        """An expense's edit: "Paid from", its own account selected, and the note."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)
            checking = seed_user["account"].id
            html = auth_client.get(
                f"/templates/{template.id}/edit",
            ).get_data(as_text=True)
        assert _account_label(html) == "Paid from"
        assert [
            account_id for account_id, selected, _ in _account_options(html) if selected
        ] == [checking]
        assert 'aria-describedby="account-help"' in html
        note = " ".join(
            html[html.index('id="account-help"'):].split("</div>", 1)[0].split()
        )
        assert (
            'Changing this moves the unpaid entries from the paycheck holding the '
            '"Amount effective from" date onward, or from today\'s paycheck if '
            "that date is blank. Entries already paid, received or cancelled stay "
            "where they are, and so do entries whose amount or paycheck you changed "
            "by hand. Purchases already recorded stay on the account they were "
            "paid from."
        ) in note

    @pytest.mark.usefixtures("seed_periods_today")
    def test_a_worked_out_amount_has_no_date_box_so_the_note_says_today(
        self, app, auth_client, seed_user,
    ):
        """A salary profile's paycheck has no "Amount effective from" box to name.

        Its amount is worked out (ruling plan#2694), so the form draws no date
        box and the update moves rows from today's paycheck; the note says
        that rather than pointing at a box that is not there.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Day Job", txn_type="Income",
                cadence=EVERY_PERIOD, amount="2000.00",
            )
            make_salary_profile(seed_user, db.session).template_id = template.id
            db.session.commit()
            html = auth_client.get(
                f"/templates/{template.id}/edit",
            ).get_data(as_text=True)
        assert 'id="effective_from"' not in html
        note = " ".join(
            html[html.index('id="account-help"'):].split("</div>", 1)[0].split()
        )
        assert "Changing this moves the unpaid entries from today's paycheck onward." in note
        assert "Amount effective from" not in note

    def test_an_income_edit_says_paid_into(self, app, auth_client, seed_user):
        """The edit form reads the bill's own type for its label."""
        with app.app_context():
            template = _create_template(
                seed_user, name="Paycheck", txn_type="Income", cadence=EVERY_PERIOD,
            )
            html = auth_client.get(
                f"/templates/{template.id}/edit",
            ).get_data(as_text=True)
        assert _account_label(html) == "Paid into"

    def test_an_archived_account_stays_selected_and_an_untouched_save_keeps_it(
        self, app, auth_client, seed_user,
    ):
        """A bill on an account archived since: offered as itself, and kept on Save.

        Without the bill's own account among the options, the select's
        stored value is missing and a browser submits the FIRST option, so an
        untouched Save moved the bill (and its unpaid rows) onto checking.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Old Savings", Decimal("5000.00"),
            )
            db.session.flush()
            template = _create_template(seed_user, cadence=EVERY_PERIOD)
            template.account_id = savings.id
            savings.is_active = False
            db.session.commit()
            tid, savings_id = template.id, savings.id
            html = auth_client.get(f"/templates/{tid}/edit").get_data(as_text=True)
            options = _account_options(html)
            assert (savings_id, True, "Old Savings (archived)") in options
            assert [account_id for account_id, selected, _ in options if selected] == [
                savings_id,
            ]
            # What the untouched form posts for its account: the option the
            # rendered select has selected, read off the page, so this half
            # fails with the GET half when the bill's account is missing.
            (posted,) = [account_id for account_id, selected, _ in options if selected]
            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Rent", "account_id": str(posted),
            })
            assert resp.status_code == 302, resp.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(TransactionTemplate, tid).account_id == savings_id


def _rows_by_paycheck(template_id):
    """Return ``{pay_period_id: row}`` for a definition's live rows."""
    return {
        row.pay_period_id: row
        for row in db.session.query(Transaction).filter_by(
            template_id=template_id, is_deleted=False,
        )
    }


def _generated_every_paycheck(seed_user):
    """A $1,200 Rent bill on checking with a row in every paycheck, flushed.

    Returns:
        The :class:`TransactionTemplate`.
    """
    template = _create_template(seed_user, cadence=EVERY_PERIOD)
    recurrence_engine.generate_for_template(
        template, GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id),
            {p.id for p in all_periods(seed_user["user"].id)},
        ), seed_user["scenario"].id,
    )
    db.session.flush()
    return template


class TestTheMoveDoesWhatTheNoteSays:
    """POST /templates/<id> with a new account: the note's three claims, measured."""

    def test_a_move_takes_the_unpaid_rows_from_todays_paycheck_and_no_others(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Unpaid rows from today's paycheck on move; paid, hand-changed and earlier stay.

        The note under the box says a blank "Amount effective from" moves the
        unpaid entries from today's paycheck onward, and that paid entries and
        entries changed by hand stay.  One bill on checking, every paycheck:
        the next paycheck's row paid early, the one after re-priced by hand,
        then the bill moved to savings with the date left blank.  Both kept
        rows sit inside the moved range, so neither stays merely by its date.
        """
        today = _TODAYS_PERIOD_INDEX
        earlier, paid, hand = (seed_periods_today[i].id for i in (today - 1, today + 1, today + 2))
        moving = [
            period.id for period in seed_periods_today[today:]
            if period.id not in (paid, hand)
        ]
        with app.app_context():
            checking = seed_user["account"].id
            # Its books open on the first paycheck here, well before any row
            # the move reaches, so no moved row lands inside them (the save's
            # stranding refusal, rulings R-PC90 / R-PC99).
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
                observed_on=seed_periods_today[0].start_date,
            ).id
            tid = _generated_every_paycheck(seed_user).id
            rows = _rows_by_paycheck(tid)
            assert {earlier, paid, hand, *moving} <= set(rows)
            settle_cash_row(rows[paid])
            repriced_by_the_owner(rows[hand], "1500.00")
            db.session.commit()

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Rent", "account_id": str(savings),
            })
            assert resp.status_code == 302, resp.get_data(as_text=True)

            db.session.expire_all()
            rows = _rows_by_paycheck(tid)
            assert rows[paid].status_id == ref_cache.status_id(StatusEnum.DONE)
            assert rows[paid].account_id == checking, "a paid row moved"
            assert rows[hand].account_id == checking, "a row changed by hand moved"
            assert rows[earlier].account_id == checking, (
                "an unpaid row in a paycheck before today's moved"
            )
            assert {rows[pid].account_id for pid in moving} == {savings}, (
                "an unpaid row from today's paycheck on stayed on checking"
            )

    def test_a_dated_move_starts_at_the_paycheck_holding_that_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Amount effective from" two paychecks out: rows before that paycheck stay.

        The date named is a day inside the paycheck two after today's, so the
        rows of today's paycheck and the next stay on checking and every row
        from that paycheck on moves, the one holding the day included.
        """
        today = _TODAYS_PERIOD_INDEX
        target = seed_periods_today[today + 2]
        with app.app_context():
            checking = seed_user["account"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
                observed_on=seed_periods_today[0].start_date,
            ).id
            tid = _generated_every_paycheck(seed_user).id
            db.session.commit()

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Rent", "account_id": str(savings),
                "effective_from": (target.start_date + timedelta(days=3)).isoformat(),
            })
            assert resp.status_code == 302, resp.get_data(as_text=True)

            db.session.expire_all()
            rows = _rows_by_paycheck(tid)
            assert {
                rows[p.id].account_id for p in seed_periods_today[today:today + 2]
            } == {checking}, "a row before the dated paycheck moved"
            assert {
                rows[p.id].account_id for p in seed_periods_today[today + 2:]
            } == {savings}, "a row from the dated paycheck on stayed"
