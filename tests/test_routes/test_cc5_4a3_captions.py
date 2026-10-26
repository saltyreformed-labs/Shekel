"""Plan step ``credit_card:CC-5-4a-3``: the popover says what taking a payment off frees.

Ruling **R-CC56** (developer 2026-09-22), verbatim: *"A caption under the
Actual box ('Recording $0.00 withdraws 1 accepted match, so 1 bank line is
unexplained again: 9/24 HOTEL -$120.00'). A second caption beside
Paid/Received on a reverted row whose purchases would replace its payment.
Both captions come from the one read the door acts on. The grid's one-click
Mark Paid withdraws and logs it without a caption, as the Paid-from change
does today."*  Ruling **R-CC54**'s one act is what the two presses reach
(``status_seam._covering._withdraw`` -> ``movement_removal.remove_movements``).

What is graded here is what the template EMITS and what its presses DO: each
caption over a matched payment and its absence where nothing would be freed;
the popover's own Save carrying ``$0.00`` (the form's controls, read off the
render, not a hand-picked payload); Paid on the reverted envelope; and the
delete dialog's sentence, whose clause this step moved into the macro the
three captions share, byte for byte.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.services import (
    entry_service, transaction_service, transfer_legs, transfer_service,
)
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match, matched_subjects
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    generate_row_of,
    make_expense_template,
    typed,
)
from tests.test_routes._statement_forms import ReconcileFormReader
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def _day(seed_user):
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _settle(seed_user, txn):
    settle_transaction(
        txn, settle_day=SettleDay(
            day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
        ),
    )
    db.session.commit()


def _matched(seed_user, txn):
    """Checking's -$120.00 HOTEL line on :func:`_day`, matched to *txn*'s payment."""
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-120.00",
        posted_on=_day(seed_user), description="HOTEL",
    )
    db.session.commit()
    scope = a_scope(seed_user)
    accept_match(a_submission(scope, lines=[line], transactions=[txn]), scope)
    db.session.commit()
    return line


def _hotel(seed_user):
    """A settled $120.00 Hotel bill on checking, its payment matched."""
    txn = a_transaction(seed_user, name="Hotel", amount="120.00")
    db.session.commit()
    _settle(seed_user, txn)
    return txn, _matched(seed_user, txn)


def _reverted_envelope(seed_user, *, with_purchase, companion_visible=False):
    """A matched $120.00 envelope reverted to Projected, its payment kept un-dated.

    *companion_visible* lets the owner's companion see (and press) its row.
    """
    template = make_expense_template(
        db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent", is_envelope=True,
        companion_visible=companion_visible,
    )
    txn = generate_row_of(template, seed_user["bootstrap_period"])
    db.session.commit()
    _settle(seed_user, txn)
    line = _matched(seed_user, txn)
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    if with_purchase:
        entry_service.create_entry(
            txn.id, seed_user["user"].id, entry_service.EntryDetails(
                figure=typed(Decimal("120.00")), description="Hilton",
                purchased_on=_day(seed_user),
            ),
        )
        db.session.commit()
    return txn, line


def _popover(auth_client, txn_id):
    response = auth_client.get(f"/transactions/{txn_id}/full-edit")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _form_fields(html):
    """What a browser submits for the popover's PATCH form, control by control."""
    start = html.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    return dict(reader.fields)


def _frees(seed_user):
    """The clause every caption shares, for the one HOTEL line."""
    day = _day(seed_user)
    return (
        "withdraws 1 accepted match, so 1 bank line is unexplained again on "
        f"your statement screen: {day.month}/{day.day} HOTEL -$120.00."
    )


def _claimed(seed_user, line):
    return line.id in matched_subjects(seed_user["account"].id).lines


class TestTheActualBoxSaysWhatAZeroWouldFree:
    """The first caption: under the Actual box of a settled, matched row."""

    def test_a_matched_payment_names_its_line_under_the_box(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            txn, _line = _hotel(seed_user)

            html = _popover(auth_client, txn.id)

            assert f'id="settled-amount-{txn.id}"' in html, "the box is drawn"
            assert f'id="zero-withdraws-{txn.id}"' in html
            assert f"Recording $0.00 {_frees(seed_user)}" in html

    def test_an_unmatched_payment_renders_no_caption(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            txn = a_transaction(seed_user, name="Hotel", amount="120.00")
            db.session.commit()
            _settle(seed_user, txn)

            html = _popover(auth_client, txn.id)

            assert f'id="settled-amount-{txn.id}"' in html, "the box is drawn"
            assert "zero-withdraws-" not in html

    def test_saving_zero_from_the_popover_withdraws_the_match(
        self, app, auth_client, seed_user,
    ):
        """The Save the card emits, its Actual set to 0.00: the act is withdrawn."""
        with app.app_context():
            txn, line = _hotel(seed_user)
            payload = _form_fields(_popover(auth_client, txn.id))
            assert payload["settled_amount"] == "120.00"
            payload["settled_amount"] = "0.00"

            response = auth_client.patch(f"/transactions/{txn.id}", data=payload)

            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, line)


class TestPaidSaysWhatReplacingThePaymentWouldFree:
    """The second caption: beside Paid, on a reverted row holding purchases."""

    def test_a_reverted_envelope_with_a_purchase_names_its_line(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            txn, _line = _reverted_envelope(seed_user, with_purchase=True)

            html = _popover(auth_client, txn.id)

            assert f'id="purchases-withdraws-{txn.id}"' in html
            assert (
                f"Marking this paid from its purchases {_frees(seed_user)}"
                in html
            )
            assert "zero-withdraws-" not in html, "a Projected row has no Actual box"

    def test_a_reverted_row_WITHOUT_purchases_renders_no_caption(
        self, app, auth_client, seed_user,
    ):
        """Paid re-dates the kept payment there, and frees nothing."""
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=False)

            html = _popover(auth_client, txn.id)

            assert "purchases-withdraws-" not in html
            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 1
            assert _claimed(seed_user, line)

    def test_pressing_paid_withdraws_the_match(
        self, app, auth_client, seed_user,
    ):
        """A Paid POST with no field: the purchases replace the payment.

        Since plan step CC-5-4a-5 an empty POST is the GRID's one-click Mark
        Paid, silent by ruling R-CC56; the popover's own Paid posts the lines
        its caption named, graded in ``test_cc5_4a5_popover_presses``.
        """
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)
            assert _claimed(seed_user, line)

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")

            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, line)


class TestTheDeleteDialogKeepsItsSentence:
    """The clause moved into the shared macro; the dialog reads the same."""

    def test_the_dialog_names_the_line_a_delete_frees(
        self, app, auth_client, seed_user,
    ):
        """A LINK-LESS row, so the delete is hard and withdraws.

        A row of a recurring definition deletes soft -- it stays as the
        tombstone the engine reads, keeping its payment and its match -- and
        its dialog rightly names no line.  Only the withdrawal CLAUSE is
        pinned here.  The dialog's first sentence counts purchases only, the
        row's own payment not among them (``test_cc5_4a4_hidden_row_doors``
        ``TestTheDialogCountsPurchasesOnly``), read off the delete's own
        preview since ruling R-CC131 (``test_cc5_4a5_delete_dialog``); ledger
        row BAL-504, which this docstring quoted as open, closed at the tick
        of plan step ``credit_card:CC-5-4a-5``'s first leaf.
        """
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            _matched(seed_user, txn)

            html = _popover(auth_client, txn.id)

            assert f"It also {_frees(seed_user)}" in html


def _matched_transfer(seed_user, *, legs=("checking",)):
    """A $500 Checking -> Savings transfer marked Paid, the named legs matched.

    *legs* names which legs' payments are matched, each to its own
    account's line: ``"checking"`` to a -$500.00 TRANSFER OUT line,
    ``"savings"`` to a +$500.00 TRANSFER IN line, posted on that side's
    record's day and ticked as the screen offers a paid side, its record.
    Returns the transfer and the lines matched, in *legs*' order.

    It read and ticked each side's SHADOW row until leaf
    ``balance:X-bi-6-4d-2``, which hangs a side's record off the transfer
    (ruling **R-BAL167**'s class 4, a shared helper re-expressed).
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("2000.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], Decimal("500.00"),
    )
    db.session.commit()
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
    )
    db.session.commit()
    sides = {
        "checking": (seed_user["account"], False, "-500.00", "TRANSFER OUT"),
        "savings": (savings, True, "500.00", "TRANSFER IN"),
    }
    lines = []
    for leg in legs:
        account, is_income, amount, description = sides[leg]
        record = transfer_legs.transfer_side_leg(xfer, is_income=is_income).record
        line = a_bank_line(
            seed_user, an_import(seed_user, account), amount=amount,
            posted_on=record.settled_on, description=description,
        )
        db.session.commit()
        scope = a_scope(seed_user, account)
        accept_match(
            a_submission(scope, lines=[line], transfers=[xfer]), scope,
        )
        db.session.commit()
        lines.append(line)
    return xfer, lines


def _transfer_popover(auth_client, xfer_id):
    response = auth_client.get(f"/transfers/{xfer_id}/full-edit")
    assert response.status_code == 200
    return response.get_data(as_text=True)


class TestTheTransferActualBoxSaysWhatAZeroWouldFree:
    """Ruling R-CC59: the transfer popover's Actual box, the bill's caption.

    It reads BOTH legs' payments, because a ``$0.00`` takes both off: a
    case per leg and one with both matched, so a read over either leg alone
    fails one of them.
    """

    def test_a_matched_checking_leg_names_its_line_under_the_box(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            day = line.posted_on

            html = _transfer_popover(auth_client, xfer.id)

            assert f'id="settled-amount-{xfer.id}"' in html, "the box is drawn"
            assert f'id="zero-withdraws-{xfer.id}"' in html
            assert (
                "Recording $0.00 withdraws 1 accepted match, so 1 bank line "
                "is unexplained again on your statement screen: "
                f"{day.month}/{day.day} TRANSFER OUT -$500.00."
            ) in html

    def test_a_matched_savings_leg_names_its_line_under_the_box(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("savings",))
            day = line.posted_on

            html = _transfer_popover(auth_client, xfer.id)

            assert (
                "Recording $0.00 withdraws 1 accepted match, so 1 bank line "
                "is unexplained again on your statement screen: "
                f"{day.month}/{day.day} TRANSFER IN $500.00."
            ) in html

    def test_both_legs_matched_names_both_lines(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            xfer, _lines = _matched_transfer(
                seed_user, legs=("checking", "savings"),
            )

            html = _transfer_popover(auth_client, xfer.id)

            assert (
                "Recording $0.00 withdraws 2 accepted matches, so 2 bank "
                "lines are unexplained again on your statement screen:"
            ) in html
            assert "TRANSFER OUT -$500.00" in html
            assert "TRANSFER IN $500.00" in html

    def test_an_unmatched_transfer_renders_no_caption(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            xfer, _lines = _matched_transfer(seed_user, legs=())

            html = _transfer_popover(auth_client, xfer.id)

            assert f'id="settled-amount-{xfer.id}"' in html, "the box is drawn"
            assert "zero-withdraws-" not in html

    def test_saving_zero_from_the_popover_withdraws_both_legs_matches(
        self, app, auth_client, seed_user,
    ):
        """The Save the transfer card emits, its Actual set to 0.00."""
        with app.app_context():
            xfer, lines = _matched_transfer(
                seed_user, legs=("checking", "savings"),
            )
            payload = _form_fields(_transfer_popover(auth_client, xfer.id))
            assert payload["settled_amount"] == "500.00"
            payload["settled_amount"] = "0.00"

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}", data=payload,
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, lines[0])
