"""The conflict chooser's "use the template" restores only what un-archiving would.

Ruling R-BAL253.

Plan step ``balance:X-bi-6-4d-2``, the cp5 leaf review's M-1.  A recurring
definition's edit with an early "effective from" date offers every deleted
occurrence in its window, and "use the template" brought each one back without
asking the books -- the day an account's balance starts -- where un-archiving
the definition leaves a row its books hold deleted and names it (rulings
**R-PC95** and **R-PC99**).  Measured before the fix through the real routes:
a $50.00 every-paycheck Checking -> Savings transfer due on Savings' opening day,
marked Paid and then deleted (so returned to a plan and hidden, ruling
**R-BAL246**), came back from the chooser as a live plan INSIDE the books, its
$50.00 counted a second time.  Deleted bills had the same hole (ledger row
**REC-535**).  Josh ruled 2026-10-09, "Leave it deleted, both kinds": "'Use the
template' brings back only what un-archiving would: an item whose due day sits
inside its books stays deleted, and the page names it the way un-archiving
does".

Every act runs through its ROUTE -- the edit, the chooser it renders, and the
Apply that page posts, built from the controls the page renders -- and every
outcome is graded by re-reading the rows after the request ended.
"""
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser

from sqlalchemy import text

from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.utils.log_events import EVT_RECURRENCE_CONFLICTS_RESOLVED
from tests._test_helpers import repriced_by_the_owner
from tests.test_routes.test_a_revert_below_the_books import _paid_then_held
from tests.test_routes.test_archived_rows_bound_the_books import (
    _flashed,
    _forecast,
    _restate_directly,
    _transfers,
)
from tests.test_routes.test_definition_edit_strands_no_row import (
    _transaction_update_payload,
    _transfer_update_payload,
)
from tests.test_services.test_opening_restatement_planned_rows import (
    _account_with_projected_rows,
)
from tests.test_services.test_service_log_events._helpers import _LogCapture

#: The marker the chooser's Apply form carries, which tells it from the page's other forms.
_APPLY_MARKER = "conflict_apply"


class TestADeletedTransferItsBooksHoldStaysDeleted:
    """The measured case: the deleted ex-Paid $50.00 transfer due on Savings' opening day."""

    def test_use_the_template_leaves_it_deleted_and_names_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Its $50.00 is not counted a second time, and the page says why it stayed."""
        with app.app_context():
            savings, template_id, first_id, first_due = _paid_then_held(
                auth_client, seed_user, seed_periods,
            )
            assert auth_client.delete(
                f"/transfers/instance/{first_id}",
            ).status_code == 200
            _priced_before_its_first_paycheck(
                TemplateAmountVersion.transfer_template_id, template_id, seed_periods,
            )
            others = {row.id for row in _transfers(template_id, deleted=False)}
            # Nine live $50.00 transfers into Savings; Checking opened at
            # $1,000.00 pays them.
            assert len(others) == 9
            assert _forecast(seed_user, savings) == Decimal("450.00")
            assert _forecast(seed_user, seed_user["account"]) == Decimal("550.00")
            edit = _transfer_update_payload(
                _db.session.get(TransferTemplate, template_id),
                default_amount="60.00",
                effective_from=seed_periods[0].start_date.isoformat(),
                # The save has no category: its select posts the empty option.
                category_id="",
            )
            as_found = _transfer_as_stored(seed_user, first_id)

            resp = _apply_use(auth_client, f"/transfers/{template_id}", edit, first_id)

            # Left exactly as found: the transfer's every column (deleted,
            # status, owner flag, amount and its source among them), both
            # twins, and the owner's journal.
            assert _transfer_as_stored(seed_user, first_id) == as_found
            assert as_found[0]["is_deleted"]
            assert {row.id for row in _transfers(template_id, deleted=False)} == others
            # The nine live transfers at the new $60.00 from the first
            # paycheck on, and nothing for the deleted one: 9 x $60.00.
            assert _forecast(seed_user, savings) == Decimal("540.00")
            assert _forecast(seed_user, seed_user["account"]) == Decimal("460.00")
            assert _flashed(
                f"1 item due {first_due.isoformat()} stays deleted: it falls "
                f"inside Revert savings's books, which open "
                f"{first_due.isoformat()}."
            ) in resp.data


class TestADeletedBillItsBooksHoldStaysDeleted:
    """The same rule for a recurring bill, the half REC-535 already held."""

    def test_use_the_template_leaves_it_deleted_and_names_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A rent row deleted by hand, then the books moved onto its day."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            first = min(rows, key=lambda row: row.due_date)
            first_id, first_due, template_id = first.id, first.due_date, first.template_id
            others = {row.id for row in rows} - {first_id}
            assert auth_client.delete(f"/transactions/{first_id}").status_code == 200
            _restate_directly(account, first_due)
            _priced_before_its_first_paycheck(
                TemplateAmountVersion.transaction_template_id, template_id, seed_periods,
            )
            edit = _transaction_update_payload(
                _db.session.get(TransactionTemplate, template_id),
                default_amount="12.00",
                effective_from=seed_periods[0].start_date.isoformat(),
            )

            resp = _apply_use(auth_client, f"/templates/{template_id}", edit, first_id)

            _db.session.expire_all()
            assert _db.session.get(Transaction, first_id).is_deleted
            # Every live rent row at the new $12.00, and nothing for the
            # deleted one; the account opened at $0.00.
            assert _forecast(seed_user, account) == -Decimal("12.00") * len(others)
            assert _flashed(
                f"1 item due {first_due.isoformat()} stays deleted: it falls "
                f"inside Planned-rows account's books, which open "
                f"{first_due.isoformat()}."
            ) in resp.data

    def test_a_re_priced_deleted_row_offered_as_hand_edited_stays_deleted(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A row the owner re-priced and then deleted is offered as OVERRIDDEN.

        "Use" holds it too.
        """
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            first = min(rows, key=lambda row: row.due_date)
            repriced_by_the_owner(first, "15.00")
            _db.session.commit()
            first_id, first_due, template_id = first.id, first.due_date, first.template_id
            assert auth_client.delete(f"/transactions/{first_id}").status_code == 200
            _restate_directly(account, first_due)
            _priced_before_its_first_paycheck(
                TemplateAmountVersion.transaction_template_id, template_id, seed_periods,
            )
            edit = _transaction_update_payload(
                _db.session.get(TransactionTemplate, template_id),
                default_amount="12.00",
                effective_from=seed_periods[0].start_date.isoformat(),
            )

            resp = _apply_use(auth_client, f"/templates/{template_id}", edit, first_id)

            _db.session.expire_all()
            held = _db.session.get(Transaction, first_id)
            assert held.is_deleted
            assert held.is_override, "a row left deleted is left untouched"
            assert _flashed(
                f"1 item due {first_due.isoformat()} stays deleted"
            ) in resp.data


class TestOnlyTheRowsTheBooksHoldStay:
    """The chooser restores what the unarchive would, and names only the rows the owner picked."""

    def test_a_deleted_row_above_the_books_comes_back(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Rows 1 and 2 held by the books, row 3 above them: "use" on 1 and 3, "keep" on 2."""
        with app.app_context():
            account, rows = _account_with_projected_rows(seed_user, seed_periods)
            _db.session.commit()
            first, second, third = sorted(rows, key=lambda row: row.due_date)[:3]
            ids = first.id, second.id, third.id
            first_due, second_due = first.due_date, second.due_date
            template_id = first.template_id
            for row_id in ids:
                assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            _restate_directly(account, second_due)
            _priced_before_its_first_paycheck(
                TemplateAmountVersion.transaction_template_id, template_id, seed_periods,
            )
            edit = _transaction_update_payload(
                _db.session.get(TransactionTemplate, template_id),
                default_amount="12.00",
                effective_from=seed_periods[0].start_date.isoformat(),
            )

            with _LogCapture("app.services.recurrence_engine") as cap:
                resp = _apply_use(
                    auth_client, f"/templates/{template_id}", edit, ids[0], ids[2],
                )

            _db.session.expire_all()
            assert [
                _db.session.get(Transaction, row_id).is_deleted for row_id in ids
            ] == [True, True, False]
            # The audit event accounts for both picks: one handed back, one
            # left deleted by its books, none skipped.
            used = [
                record for record in cap.records
                if getattr(record, "event", None) == EVT_RECURRENCE_CONFLICTS_RESOLVED
                and record.action == "update"
            ]
            assert [
                (record.resolved_count, record.kept_deleted_count, record.skipped_count)
                for record in used
            ] == [(1, 1, 0)]
            # Only the PICKED held row is named: the kept one was not asked
            # back, so the sentence counts one item, not two.
            assert _flashed(
                f"1 item due {first_due.isoformat()} stays deleted: it falls "
                f"inside Planned-rows account's books, which open "
                f"{second_due.isoformat()}."
            ) in resp.data


def _priced_before_its_first_paycheck(column, template_id, seed_periods):
    """Date the definition's one price version the day before its first paycheck.

    The fixtures price a definition on the owner's today, which a back-dated
    edit cannot supersede: the edit then states an OLDER version, the current
    price stays where it was, and the chooser -- which asks only when the
    current price moved -- never renders.  A real definition is priced on the
    day it is made, before the paychecks it fills, and that state is stated
    here directly (a state, not a door under test).  It is the only version,
    and the series holds flat before its earliest, so no row's price moves.

    Args:
        column: ``TemplateAmountVersion``'s column naming the definition.
        template_id: The definition's id.
        seed_periods: The owner's paychecks.
    """
    version = _db.session.query(TemplateAmountVersion).filter(
        column == template_id,
    ).one()
    version.effective_date = seed_periods[0].start_date - timedelta(days=1)
    _db.session.commit()


def _transfer_as_stored(seed_user, transfer_id):
    """The transfer's row, its two twins and the owner's journal-entry count, by SQL.

    Every stored column, read after the session is expired, so a write the
    resolver made to any of them -- or to a twin, or to the ledger -- shows.
    """
    _db.session.expire_all()
    transfer = dict(_db.session.execute(
        text("SELECT * FROM budget.transfers WHERE id = :id"), {"id": transfer_id},
    ).mappings().one())
    twins = [dict(row) for row in _db.session.execute(
        text("SELECT * FROM budget.transactions WHERE transfer_id = :id ORDER BY id"),
        {"id": transfer_id},
    ).mappings()]
    journal = _db.session.execute(
        text("SELECT count(*) FROM budget.journal_entries WHERE user_id = :user"),
        {"user": seed_user["user"].id},
    ).scalar_one()
    return transfer, twins, journal


class _Forms(HTMLParser):
    """Every form on a page, as the list of its ``<input>`` elements' attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.actions = []
        self._open = False

    def handle_starttag(self, tag, attrs):
        """Open a form, or record an input inside the open one."""
        if tag == "form":
            self.forms.append([])
            self.actions.append(dict(attrs).get("action"))
            self._open = True
        elif tag == "input" and self._open:
            self.forms[-1].append(dict(attrs))

    def handle_endtag(self, tag):
        """Close the open form."""
        if tag == "form":
            self._open = False


def _apply_use(client, url, edit, *picked):
    """Submit *edit*, then the chooser's Apply as a browser posts it with "use" on *picked*.

    The first submit must render the chooser.  The Apply is built from the
    page's OWN Apply form, never from *edit*: posted to the form's
    ``action``, with every hidden input it renders (the marker and the
    echoed edit) and, per offered row, the radio it renders checked --
    "keep" -- except on *picked*, where the owner clicks "Follow the
    template".  Each picked row must be offered (a precondition: a row the
    chooser never offered would stay deleted for another reason).  The
    redirect is followed, so the flash is on the page it returns.
    """
    chooser = client.post(url, data=edit)
    assert chooser.status_code == 200
    page = _Forms()
    page.feed(chooser.data.decode())
    ((action, form),) = [
        (action, inputs) for action, inputs in zip(page.actions, page.forms)
        if any(field.get("name") == _APPLY_MARKER for field in inputs)
    ]
    payload = {}
    for field in form:
        if field.get("type") == "hidden" or (
            field.get("type") == "radio" and "checked" in field
        ):
            payload[field["name"]] = field["value"]
    for row_id in picked:
        name = f"conflict_decision_{row_id}"
        assert any(
            field.get("name") == name and field.get("value") == "use"
            for field in form
        ), "precondition: the chooser offers each picked row"
        payload[name] = "use"
    resp = client.post(action, data=payload, follow_redirects=True)
    assert resp.status_code == 200
    return resp
