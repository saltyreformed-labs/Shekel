"""Plan step ``credit_card:CC-5-4a-5b``: the purchase X and the Credit doors say what they free.

Ruling **R-CC80** (developer 2026-09-23, "Every door warns"): *"The purchase X
and all three card-payback buttons name the bank line before you press, using
the same data the button acts on, as the popovers do. Mark Paid stays the only
silent door. [The] X would ask: 'Delete [the purchase] [its figure]? The
bank's 9/21 -[figure] line will be unexplained again on your statement
screen.'"*  Ruling **R-CC127** (2026-09-30): each warning sends back the lines
it named and the act compares them.  Ruling **R-CC132** (2026-10-04, "Refuse,
shown first"): *"On the companion page a matched purchase has no X; in its
place: 'Matched to the bank statement: only the account owner can delete
it.' A page drawn before the match still shows the X; pressing it is
refused: 'Kroger is matched to a line on the bank statement, so only the
account owner can delete it.' ... The last-card-purchase case is refused on
press the same way."*

What is graded is what each control POSTS -- read off the rendered page (the
X's ``hx-confirm`` and ``hx-vals``, the edit form's controls, the popover's
buttons and form), never a hand-picked payload -- and what the door does with
it, read back after a commit.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qsl

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    credit_workflow,
    entry_service,
    match_withdrawal,
    transaction_service,
)
from tests._test_helpers import generate_row_of, make_expense_template, typed
from tests.test_routes._statement_forms import ReconcileFormReader
from tests.test_routes.test_cc5_4a3_captions import (
    _claimed,
    _day,
    _form_fields,
    _popover,
)
from tests.test_routes.test_cc5_4a5_popover_presses import _vals
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_purchase_in_a_minted_envelope,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)
from app.services.statement_match import accept_match, release_match
from tests._legacy_credit import plant_old_credit

#: The bank's own words for the Kroger line -- distinct from the purchase's
#: description, so a page that leaked the line would be caught naming it.
_KROGER_LINE = "KROGER #4471 ATLANTA GA"

#: The sentence a companion's press is refused with over Groceries' PAYBACK's
#: line (ruling R-CC132's "refused on press the same way";
#: ``entry_credit_workflow.payback_refusal``).
_PAYBACK_REFUSAL = (
    "Groceries's card payback is matched to a line on the bank statement, so "
    "only the account owner can make this change."
)


def _committed():
    """End the request's work and read everything afresh, as a new page would."""
    db.session.commit()
    db.session.expire_all()


def _accept(seed_user, line, *, entries=(), transactions=()):
    """Accept *line* against *entries* / *transactions*, as the statement screen does."""
    scope = a_scope(seed_user)
    accept_match(
        a_submission(
            scope, lines=[line], entries=list(entries),
            transactions=list(transactions),
        ),
        scope,
    )
    db.session.commit()


def _groceries(seed_user, *, companion_visible=False):
    """A Projected $300.00 Groceries envelope on checking."""
    template = make_expense_template(
        db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
        companion_visible=companion_visible,
    )
    txn = generate_row_of(template, seed_user["bootstrap_period"])
    db.session.commit()
    return txn


def _purchase(seed_user, txn, amount, description, *, is_credit=False):
    entry = entry_service.create_entry(
        txn.id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=_day(seed_user), is_credit=is_credit,
        ),
    )
    db.session.commit()
    return entry


def _matched_kroger(seed_user, *, companion_visible=False):
    """Groceries holding a $12.34 Kroger purchase matched to the bank's -$12.34 line."""
    txn = _groceries(seed_user, companion_visible=companion_visible)
    kroger = _purchase(seed_user, txn, "12.34", "Kroger")
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-12.34",
        posted_on=_day(seed_user), description=_KROGER_LINE,
    )
    db.session.commit()
    _accept(seed_user, line, entries=[kroger])
    return txn, kroger, line


def _card_payback_matched(seed_user, *, companion_visible=False):
    """Groceries' one $60.00 card purchase, its payback's payment matched, the payback reverted.

    The payback is settled by the match (its payment the member), then set
    back to Projected, which keeps the payment un-dated and the act on it
    (ruling R-BAL61) -- the one shape in which removing the last card
    purchase deletes a payback a match still names.
    """
    txn = _groceries(seed_user, companion_visible=companion_visible)
    a_later_period(seed_user)
    db.session.commit()
    card = _purchase(seed_user, txn, "60.00", "Kroger", is_credit=True)
    payback = credit_workflow.get_active_payback(txn.id)
    assert payback is not None
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-60.00",
        posted_on=_day(seed_user), description="CARD PAYMENT",
    )
    db.session.commit()
    _accept(seed_user, line, transactions=[payback])
    transaction_service.apply_requested_status(
        payback, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    assert _claimed(seed_user, line)
    return txn, card, payback, line


def _credit_row_matched(seed_user):
    """A $200.00 bill marked Credit, its payback's payment matched to the card payment line."""
    source = a_transaction(
        seed_user, name="Rogue Equipment", amount="200.00", template=False,
    )
    db.session.flush()
    a_later_period(seed_user)
    plant_old_credit(source)
    db.session.commit()
    payback = credit_workflow.get_active_payback(source.id)
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-200.00",
        posted_on=_day(seed_user), description="CARD PAYMENT",
    )
    db.session.commit()
    _accept(seed_user, line, transactions=[payback])
    return source, payback, line


def _list(client, txn_id, *, editing=None):
    """The purchase list as the popover and the phone card draw it."""
    query = {"editing": editing} if editing is not None else {}
    response = client.get(
        f"/transactions/{txn_id}/entries", query_string=query,
    )
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _x(page, txn_id, entry_id):
    """The X button's tag for *entry_id*, or ``None`` when the list draws none."""
    tag = re.search(
        rf'<button[^>]*hx-delete="[^"]*/transactions/{txn_id}/entries/'
        rf'{entry_id}(?:\?[^"]*)?"[^>]*>',
        page, re.S,
    )
    return tag.group(0) if tag else None


def _x_press(page, txn_id, entry_id):
    """What pressing the X sends: its path and its query.

    The query is the URL's own (``?host=``) with the X's ``hx-vals`` added,
    as htmx sends a DELETE's values.  Returns ``(path, query, vals)``, *vals*
    being the ``hx-vals`` alone (``{}`` for a button carrying none).
    """
    tag = _x(page, txn_id, entry_id)
    assert tag is not None, "the list draws no X for this purchase"
    url = html_lib.unescape(re.search(r'hx-delete="([^"]*)"', tag).group(1))
    path, _sep, query = url.partition("?")
    found = re.search(r"hx-vals='([^']*)'", tag)
    vals = json.loads(found.group(1)) if found else {}
    return path, {**dict(parse_qsl(query, keep_blank_values=True)), **vals}, vals


def _confirm(tag):
    """The sentence the X's browser confirm asks."""
    return html_lib.unescape(re.search(r'hx-confirm="([^"]*)"', tag).group(1))


def _edit_form(page):
    """What a browser submits for the inline edit form, control by control."""
    start = page.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(page[start:page.index("</form>", start)])
    return list(reader.fields), re.search(
        r'hx-patch="([^"]*)"', page[start:],
    ).group(1)


def _add_form(page, txn_id):
    """The add-purchase form's controls as rendered, and its URL."""
    start = page.index(f'hx-post="/transactions/{txn_id}/entries')
    start = page.rindex("<form", 0, start)
    reader = ReconcileFormReader()
    reader.feed(page[start:page.index("</form>", start)])
    url = html_lib.unescape(re.search(r'hx-post="([^"]*)"', page[start:]).group(1))
    return list(reader.fields), reader.offerable, url


def _cc380(seed_user, *, companion_visible=False, payback_line=False):
    """Kroger, matched to its checking line, then ticked CC (finding CC-380's state).

    Ticking CC on a matched purchase is admitted (CC-380, owned by
    ``credit_card:CC-7``): Kroger is now the envelope's last card purchase
    AND matched itself.  With *payback_line* its payback's payment is matched
    to a CARD PAYMENT line too and the payback reverted, so the X frees both
    lines and the un-tick frees the payback's alone.

    Returns:
        ``(txn, kroger, kroger_line, payback, payback_line_or_None)``.
    """
    txn, kroger, kroger_line = _matched_kroger(
        seed_user, companion_visible=companion_visible,
    )
    a_later_period(seed_user)
    db.session.commit()
    entry_service.update_entry(kroger.id, seed_user["user"].id, is_credit=True)
    db.session.commit()
    payback = credit_workflow.get_active_payback(txn.id)
    assert payback is not None
    second = None
    if payback_line:
        # The next day: one account's line is keyed by its day and figure.
        second = a_bank_line(
            seed_user, an_import(seed_user), amount="-12.34",
            posted_on=_day(seed_user) + timedelta(days=1),
            description="CARD PAYMENT",
        )
        db.session.commit()
        _accept(seed_user, second, transactions=[payback])
        transaction_service.apply_requested_status(
            payback, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
    return txn, kroger, kroger_line, payback, second


def _line_day(seed_user):
    day = _day(seed_user)
    return f"{day.month}/{day.day}"


class TestTheOwnersXNamesWhatItFrees:
    """The owner's X: R-CC80's sentence, the lines sent back, the act comparing them."""

    def test_the_x_names_the_purchases_line_and_its_press_withdraws_it(
        self, app, auth_client, seed_user,
    ):
        """Kroger's X asks in R-CC80's words, sends the line, and the press frees it."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)

            page = _list(auth_client, txn.id)
            tag = _x(page, txn.id, kroger.id)
            assert _confirm(tag) == (
                f"Delete Kroger $12.34? The bank's {_line_day(seed_user)} "
                "-$12.34 line will be unexplained again on your statement "
                "screen."
            )
            path, query, vals = _x_press(page, txn.id, kroger.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(path, query_string=query)

            assert response.status_code == 200
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is None
            assert not _claimed(seed_user, line)

    def test_an_unmatched_purchase_asks_as_before_and_sends_nothing(
        self, app, auth_client, seed_user,
    ):
        """A purchase no match names: "Delete this entry?", an empty field, the press goes ahead."""
        with app.app_context():
            txn = _groceries(seed_user)
            plain = _purchase(seed_user, txn, "8.00", "Publix")

            page = _list(auth_client, txn.id)
            assert _confirm(_x(page, txn.id, plain.id)) == "Delete this entry?"
            path, query, vals = _x_press(page, txn.id, plain.id)
            assert vals == {"shown_lines": ""}

            assert auth_client.delete(path, query_string=query).status_code == 200
            _committed()
            assert db.session.get(TransactionEntry, plain.id) is None

    def test_a_list_drawn_before_the_match_is_refused_and_drawn_again(
        self, app, auth_client, seed_user,
    ):
        """The X read before another tab matched Kroger: refused, redrawn, the redrawn X goes ahead."""
        with app.app_context():
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            stale_path, stale_query, stale_vals = _x_press(
                _list(auth_client, txn.id), txn.id, kroger.id,
            )
            assert stale_vals == {"shown_lines": ""}
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-12.34",
                posted_on=_day(seed_user), description=_KROGER_LINE,
            )
            db.session.commit()
            _accept(seed_user, line, entries=[kroger])

            refused = auth_client.delete(stale_path, query_string=stale_query)

            assert refused.status_code == 400
            assert refused.headers["Shekel-Designed-Fragment"] == "1"
            body = refused.get_data(as_text=True)
            assert "Nothing was saved: this page was out of date." in body
            assert "Here it is as it is now; press again to go ahead." in body
            assert "Reload the page" not in body, "the list below IS current"
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert _claimed(seed_user, line)

            path, query, vals = _x_press(body, txn.id, kroger.id)
            assert vals == {"shown_lines": str(line.id)}
            assert auth_client.delete(path, query_string=query).status_code == 200
            _committed()
            assert not _claimed(seed_user, line)

    def test_a_request_naming_no_line_is_refused(
        self, app, auth_client, seed_user,
    ):
        """A DELETE carrying no field named nothing (R-CC127), so it frees nothing."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)
            path, query, vals = _x_press(
                _list(auth_client, txn.id), txn.id, kroger.id,
            )
            assert vals == {"shown_lines": str(line.id)}
            query.pop("shown_lines")

            refused = auth_client.delete(path, query_string=query)

            assert refused.status_code == 400
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert _claimed(seed_user, line)


class TestTheLastCardPurchaseNamesItsPaybacksLine:
    """The X and the CC un-tick on an envelope's last card purchase (R-CC80)."""

    def test_the_x_names_the_paybacks_line_and_one_press_takes_both(
        self, app, auth_client, seed_user,
    ):
        """The X deletes Kroger AND its payback; its sentence names the payback's line.

        The case two removal acts refused at every try (the payback is on the
        envelope's account, so the purchase's act met the payback's named
        line): one act takes both.
        """
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)

            page = _list(auth_client, txn.id)
            assert _confirm(_x(page, txn.id, card.id)) == (
                f"Delete Kroger $60.00? The bank's {_line_day(seed_user)} "
                "-$60.00 line will be unexplained again on your statement "
                "screen."
            )
            path, query, vals = _x_press(page, txn.id, card.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(path, query_string=query)

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(TransactionEntry, card.id) is None
            assert db.session.get(Transaction, payback.id) is None
            assert credit_workflow.get_active_payback(txn.id) is None
            assert not _claimed(seed_user, line)

    def test_the_un_tick_names_the_paybacks_line_and_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """The edit form names the line under CC and posts it; un-ticking frees it."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)

            page = _list(auth_client, txn.id, editing=card.id)
            assert (
                "Un-ticking CC deletes this envelope's CC payback and withdraws "
                "1 accepted match, so 1 bank line is unexplained again on your "
                f"statement screen: {_line_day(seed_user)} CARD PAYMENT -$60.00."
                in " ".join(page.split())
            )
            fields, url = _edit_form(page)
            assert ("shown_lines", str(line.id)) in fields
            unticked = [pair for pair in fields if pair != ("is_credit", "true")]
            assert ("is_credit", "false") in unticked

            response = auth_client.patch(url, data=MultiDict(unticked))

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is False
            assert db.session.get(Transaction, payback.id) is None
            assert not _claimed(seed_user, line)

    def test_an_un_tick_from_a_form_that_named_nothing_is_refused(
        self, app, auth_client, seed_user,
    ):
        """The form read before the payback was matched: refused, nothing changes."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)
            fields, url = _edit_form(_list(auth_client, txn.id, editing=card.id))
            stale = [
                ("shown_lines", "") if name == "shown_lines" else (name, value)
                for name, value in fields if (name, value) != ("is_credit", "true")
            ]

            refused = auth_client.patch(url, data=MultiDict(stale))

            assert refused.status_code == 400
            assert "Nothing was saved: this page was out of date." in (
                refused.get_data(as_text=True)
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is True
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)

    def test_the_un_tick_names_only_the_paybacks_line(
        self, app, auth_client, seed_user,
    ):
        """Kroger matched AND its payback matched: the un-tick frees the payback's line alone.

        The X would free both (its confirmation names both); the un-tick
        keeps Kroger and its own match, so its caption and its posted field
        name the CARD PAYMENT line only.  Reached through CC-380's state.
        """
        with app.app_context():
            txn, kroger, kroger_line, payback, card_line = _cc380(
                seed_user, payback_line=True,
            )

            page = _list(auth_client, txn.id)
            _path, _query, x_vals = _x_press(page, txn.id, kroger.id)
            assert x_vals == {
                "shown_lines": ",".join(
                    str(i) for i in sorted((kroger_line.id, card_line.id))
                ),
            }
            fields, url = _edit_form(_list(auth_client, txn.id, editing=kroger.id))
            assert ("shown_lines", str(card_line.id)) in fields
            unticked = [pair for pair in fields if pair != ("is_credit", "true")]

            response = auth_client.patch(url, data=MultiDict(unticked))

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(Transaction, payback.id) is None
            assert not _claimed(seed_user, card_line)
            assert _claimed(seed_user, kroger_line), "Kroger keeps its own match"


class TestACompanionIsNeverShownTheOwnersLines:
    """Ruling R-CC132 ("Refuse, shown first"), and R-CC130 before it."""

    def test_a_matched_purchase_has_no_x_and_says_why(
        self, app, companion_client, seed_user,
    ):
        """No X over Kroger, the ruling's note in its place, and the line named nowhere."""
        with app.app_context():
            txn, kroger, _line = _matched_kroger(
                seed_user, companion_visible=True,
            )
            publix = _purchase(seed_user, txn, "8.00", "Publix")

            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)

            assert _x(page, txn.id, kroger.id) is None
            assert (
                "Matched to the bank statement: only the account owner can "
                "delete it." in " ".join(page.split())
            )
            assert _KROGER_LINE not in page
            assert "unexplained" not in page
            plain = _x(page, txn.id, publix.id)
            assert plain is not None, "an unmatched purchase keeps its X"
            assert _confirm(plain) == "Delete this entry?"
            assert "hx-vals" not in plain, "no companion surface names a line"

    def test_a_companions_x_is_refused_with_the_rulings_sentence(
        self, app, companion_client, seed_user,
    ):
        """A page drawn before the match still shows the X: the press is refused.

        Posting the right line id changes nothing: a companion's field is
        dropped, never read.
        """
        with app.app_context():
            txn = _groceries(seed_user, companion_visible=True)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)
            path, query, vals = _x_press(page, txn.id, kroger.id)
            assert not vals
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-12.34",
                posted_on=_day(seed_user), description=_KROGER_LINE,
            )
            db.session.commit()
            _accept(seed_user, line, entries=[kroger])

            refused = companion_client.delete(
                path, query_string={**query, "shown_lines": str(line.id)},
            )

            assert refused.status_code == 400
            body = refused.get_data(as_text=True)
            assert (
                "Kroger is matched to a line on the bank statement, so only "
                "the account owner can delete it." in body
            )
            assert _KROGER_LINE not in body
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert _claimed(seed_user, line)

    def test_a_companions_x_on_the_last_card_purchase_is_refused(
        self, app, companion_client, seed_user,
    ):
        """Kroger frees no line of its own; its payback's would go: refused, the payback named."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(
                seed_user, companion_visible=True,
            )
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)
            path, query, vals = _x_press(page, txn.id, card.id)
            assert not vals
            assert _confirm(_x(page, txn.id, card.id)) == "Delete this entry?"

            refused = companion_client.delete(path, query_string=query)

            assert refused.status_code == 400
            assert (
                _PAYBACK_REFUSAL
                in html_lib.unescape(refused.get_data(as_text=True))
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id) is not None
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)

    def test_a_companions_un_tick_is_refused(
        self, app, companion_client, seed_user,
    ):
        """The companion's edit form names nothing; its un-tick is refused, nothing changes."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(
                seed_user, companion_visible=True,
            )
            page = _list(companion_client, txn.id, editing=card.id)
            assert "Un-ticking CC" not in page
            fields, url = _edit_form(page)
            assert all(name != "shown_lines" for name, _value in fields)
            unticked = [pair for pair in fields if pair != ("is_credit", "true")]

            refused = companion_client.patch(url, data=MultiDict(unticked))

            assert refused.status_code == 400
            assert (
                _PAYBACK_REFUSAL
                in html_lib.unescape(refused.get_data(as_text=True))
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is True
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)

    def test_a_companions_equal_card_refund_is_refused(
        self, app, companion_client, seed_user,
    ):
        """The add form's card refund that would delete the payback: R-CC132's refusal, not "out of date".

        The add form names nothing, and a companion's page could not name the
        line anyway, so the refusal is the payback's own sentence and says
        nothing of a statement screen the companion does not have.
        """
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(
                seed_user, companion_visible=True,
            )
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)
            fields, offerable, url = _add_form(page, txn.id)
            # The CC box carries no ``value``, so a browser sends "on" when it
            # is ticked (the reader records the attribute as rendered: "").
            assert ("is_credit", "") in offerable
            posted = dict(fields)
            posted.update(
                amount="60.00", direction="refund", description="Return",
                is_credit="on",
            )

            refused = companion_client.post(url, data=posted)

            assert refused.status_code == 400
            body = html_lib.unescape(refused.get_data(as_text=True))
            assert _PAYBACK_REFUSAL in body
            assert "unexplained" not in body and "out of date" not in body
            _committed()
            assert [e.id for e in db.session.get(Transaction, txn.id).purchases] == [
                card.id,
            ]
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)

    def test_a_companions_re_price_to_a_zero_card_total_is_refused(
        self, app, companion_client, seed_user,
    ):
        """A re-price that would delete the payback: R-CC132's refusal, posted from the rendered form.

        Kroger $60.00 and a -$20.00 card return leave a $40.00 payback whose
        payment a match names; re-pricing the return to $60.00 brings the card
        total to $0.00 and would delete it.  The companion's edit form names
        nothing, so the press is refused with the payback's sentence -- never
        as "out of date" -- and nothing changes.
        """
        with app.app_context():
            txn = _groceries(seed_user, companion_visible=True)
            a_later_period(seed_user)
            db.session.commit()
            _purchase(seed_user, txn, "60.00", "Kroger", is_credit=True)
            back = entry_service.create_entry(
                txn.id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("-20.00")), description="Return",
                    purchased_on=_day(seed_user), is_credit=True,
                ),
            )
            db.session.commit()
            payback = credit_workflow.get_active_payback(txn.id)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-40.00",
                posted_on=_day(seed_user), description="CARD PAYMENT",
            )
            db.session.commit()
            _accept(seed_user, line, transactions=[payback])
            transaction_service.apply_requested_status(
                payback, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            fields, url = _edit_form(
                _list(companion_client, txn.id, editing=back.id),
            )
            repriced = [
                ("amount", "60.00") if name == "amount" else (name, value)
                for name, value in fields
            ]
            assert ("direction", "refund") in repriced

            refused = companion_client.patch(url, data=MultiDict(repriced))

            assert refused.status_code == 400
            body = html_lib.unescape(refused.get_data(as_text=True))
            assert _PAYBACK_REFUSAL in body
            assert "unexplained" not in body and "out of date" not in body
            _committed()
            assert db.session.get(TransactionEntry, back.id).amount == (
                Decimal("-20.00")
            )
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)

    def test_a_crafted_phone_card_draws_no_line_for_a_companion(
        self, app, companion_client, seed_user,
    ):
        """The phone card's ``can_edit`` comes back from the browser; it decides nothing here.

        A companion's Mark Paid crafted with ``can_edit=1`` and a bad figure
        answers the re-drawn phone card (422).  Its purchase list is drawn
        for the SESSION's user, so Kroger's X stays withheld and no line is
        named or posted (rulings R-CC130 / R-CC132).
        """
        with app.app_context():
            txn, kroger, _line = _matched_kroger(
                seed_user, companion_visible=True,
            )

            response = companion_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={
                    "render": "mobile_card", "card_prefix": "tp",
                    "can_edit": "1", "settled_amount": "-5",
                },
            )

            assert response.status_code == 422
            body = response.get_data(as_text=True)
            assert "Kroger" in body, "the card and its purchase list are drawn"
            assert _x(body, txn.id, kroger.id) is None
            assert (
                "Matched to the bank statement: only the account owner can "
                "delete it." in " ".join(body.split())
            )
            assert "shown_lines" not in body
            assert "unexplained" not in body
            assert _KROGER_LINE not in body

    def test_a_companions_x_over_a_matched_last_card_purchase_names_the_purchase(
        self, app, companion_client, seed_user,
    ):
        """Kroger's own line AND its payback would go: the refusal is the PURCHASE's sentence.

        Reached through finding CC-380's state (CC ticked on a matched
        purchase).  The X is withheld on the page; the crafted press is
        refused, nothing changes.
        """
        with app.app_context():
            txn, kroger, line, payback, _second = _cc380(
                seed_user, companion_visible=True,
            )
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            ).get_data(as_text=True)
            assert _x(page, txn.id, kroger.id) is None

            refused = companion_client.delete(
                f"/transactions/{txn.id}/entries/{kroger.id}",
                query_string={"host": "tp"},
            )

            assert refused.status_code == 400
            assert (
                "Kroger is matched to a line on the bank statement, so only "
                "the account owner can delete it."
                in html_lib.unescape(refused.get_data(as_text=True))
            )
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)


class TestLeavingCreditNamesThePaybacksLine:
    """Undo CC and the popover's Status leaving Credit (R-CC80)."""

    def test_the_card_names_the_line_and_undo_cc_sends_it(
        self, app, auth_client, seed_user,
    ):
        """The caption beside Undo CC; the button's line; the press frees it."""
        with app.app_context():
            source, payback, line = _credit_row_matched(seed_user)

            page = _popover(auth_client, source.id)
            assert f'id="payback-withdraws-{source.id}"' in page
            assert (
                "Undo CC, or setting Status back to Projected, deletes this "
                "row's CC payback and withdraws 1 accepted match, so 1 bank "
                "line is unexplained again on your statement screen: "
                f"{_line_day(seed_user)} CARD PAYMENT -$200.00."
                in " ".join(page.split())
            )
            path = f"/transactions/{source.id}/unmark-credit"
            vals = _vals(page, "delete", path)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.delete(path, query_string=vals)

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(Transaction, payback.id) is None
            assert db.session.get(Transaction, source.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
            assert not _claimed(seed_user, line)

    def test_an_undo_cc_from_a_card_drawn_before_the_match_is_redrawn(
        self, app, auth_client, seed_user,
    ):
        """The card read before the match: refused, the card redrawn naming the line."""
        with app.app_context():
            source = a_transaction(
                seed_user, name="Rogue Equipment", amount="200.00",
                template=False,
            )
            db.session.flush()
            a_later_period(seed_user)
            plant_old_credit(source)
            db.session.commit()
            path = f"/transactions/{source.id}/unmark-credit"
            stale = _vals(_popover(auth_client, source.id), "delete", path)
            assert stale == {"shown_lines": ""}
            payback = credit_workflow.get_active_payback(source.id)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-200.00",
                posted_on=_day(seed_user), description="CARD PAYMENT",
            )
            db.session.commit()
            _accept(seed_user, line, transactions=[payback])

            refused = auth_client.delete(path, query_string=stale)

            assert refused.status_code == 400
            body = refused.get_data(as_text=True)
            assert "Here it is as it is now; press again to go ahead." in body
            assert f'id="payback-withdraws-{source.id}"' in body
            _committed()
            assert db.session.get(Transaction, payback.id) is not None
            assert _claimed(seed_user, line)
            assert _vals(body, "delete", path) == {"shown_lines": str(line.id)}

    def test_status_leaving_credit_posts_the_line_and_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """The popover's Save, Status set to Projected: the form posts the line, the payback goes."""
        with app.app_context():
            source, payback, line = _credit_row_matched(seed_user)
            fields = _form_fields(_popover(auth_client, source.id))
            assert fields["shown_lines"] == str(line.id)
            fields["status_id"] = str(ref_cache.status_id(StatusEnum.PROJECTED))

            response = auth_client.patch(
                f"/transactions/{source.id}", data=fields,
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            _committed()
            assert db.session.get(Transaction, payback.id) is None
            assert not _claimed(seed_user, line)


def _undone_in_another_tab(seed_user, line):
    """Another tab undoes the act naming *line*: the Undo's service door, ``release_match``."""
    match_id = db.session.query(StatementMatchMember.match_id).filter(
        StatementMatchMember.bank_statement_line_id == line.id,
    ).scalar()
    release_match(match_id, seed_user["user"].id, seed_user["account"].id)
    db.session.commit()
    assert not _claimed(seed_user, line)


class TestALineAnotherTabFreedRedrawsTheDoor:
    """R-CC135: *"a warning naming a line another tab has already freed now redraws"*.

    Each door's page is drawn while the match stands, so its warning names
    the line and posts it; another tab then undoes that match.  The press
    frees nothing, which is not what its page named, so the save's close
    refuses it and nothing changes.  These doors refused it before leaf
    5c-2b too, by the per-call comparison inside their one match step: the
    four here and the delete dialog's
    ``test_cc5_4a5_delete_dialog::...::test_a_delete_naming_a_line_another_tab_freed_is_redrawn``
    all pass on the pre-leaf tree, measured.  Since that leaf the CLOSE
    refuses it, and these are the cases that grade the close at those five
    doors.
    """

    def test_the_x_is_refused_and_the_redrawn_x_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """Kroger's X named its line; the line is unexplained already: redrawn, then deleted."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)
            path, query, vals = _x_press(
                _list(auth_client, txn.id), txn.id, kroger.id,
            )
            assert vals == {"shown_lines": str(line.id)}
            _undone_in_another_tab(seed_user, line)

            refused = auth_client.delete(path, query_string=query)

            assert refused.status_code == 400
            body = refused.get_data(as_text=True)
            assert "Nothing was saved: this page was out of date." in body
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is not None

            path, query, vals = _x_press(body, txn.id, kroger.id)
            assert vals == {"shown_lines": ""}
            assert auth_client.delete(path, query_string=query).status_code == 200
            _committed()
            assert db.session.get(TransactionEntry, kroger.id) is None

    def test_the_un_tick_is_refused(self, app, auth_client, seed_user):
        """The edit form named the payback's line under CC; that match is gone: nothing changes."""
        with app.app_context():
            txn, card, payback, line = _card_payback_matched(seed_user)
            fields, url = _edit_form(_list(auth_client, txn.id, editing=card.id))
            assert ("shown_lines", str(line.id)) in fields
            unticked = [pair for pair in fields if pair != ("is_credit", "true")]
            _undone_in_another_tab(seed_user, line)

            refused = auth_client.patch(url, data=MultiDict(unticked))

            assert refused.status_code == 400
            assert "Nothing was saved: this page was out of date." in (
                refused.get_data(as_text=True)
            )
            _committed()
            assert db.session.get(TransactionEntry, card.id).is_credit is True
            assert db.session.get(Transaction, payback.id) is not None

    def test_undo_cc_is_redrawn_and_the_redrawn_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """The card named the CARD PAYMENT line beside Undo CC; that match is gone."""
        with app.app_context():
            source, payback, line = _credit_row_matched(seed_user)
            path = f"/transactions/{source.id}/unmark-credit"
            vals = _vals(_popover(auth_client, source.id), "delete", path)
            assert vals == {"shown_lines": str(line.id)}
            _undone_in_another_tab(seed_user, line)

            refused = auth_client.delete(path, query_string=vals)

            assert refused.status_code == 400
            body = refused.get_data(as_text=True)
            assert "Here it is as it is now; press again to go ahead." in body
            _committed()
            assert db.session.get(Transaction, payback.id) is not None

            again = _vals(body, "delete", path)
            assert again == {"shown_lines": ""}
            assert auth_client.delete(path, query_string=again).status_code == 200
            _committed()
            assert db.session.get(Transaction, payback.id) is None

    def test_status_leaving_credit_is_redrawn(self, app, auth_client, seed_user):
        """The popover's Save, Status set to Projected, posted the line; that match is gone."""
        with app.app_context():
            source, payback, line = _credit_row_matched(seed_user)
            fields = _form_fields(_popover(auth_client, source.id))
            assert fields["shown_lines"] == str(line.id)
            fields["status_id"] = str(ref_cache.status_id(StatusEnum.PROJECTED))
            _undone_in_another_tab(seed_user, line)

            refused = auth_client.patch(f"/transactions/{source.id}", data=fields)

            assert refused.status_code == 400
            assert "Here it is as it is now; press again to go ahead." in (
                refused.get_data(as_text=True)
            )
            _committed()
            assert db.session.get(Transaction, payback.id) is not None
            assert db.session.get(Transaction, source.id).status_id == (
                ref_cache.status_id(StatusEnum.CREDIT)
            )


class TestOneReadAnswersManyRemovals:
    """``match_withdrawal.pending_for_each``: the grid's one read is each removal's own."""

    def test_each_answer_is_the_single_reads(self, app, seed_user):
        """Two purchases, one matched: each key answers as ``pending_for_movements`` alone."""
        with app.app_context():
            txn, kroger, line = _matched_kroger(seed_user)
            publix = _purchase(seed_user, txn, "8.00", "Publix")

            each = match_withdrawal.pending_for_each(
                {"kroger": [kroger], "publix": [publix]},
            )

            assert each["kroger"] == match_withdrawal.pending_for_movements(
                [kroger],
            )
            assert each["kroger"].line_ids == frozenset({line.id})
            assert each["publix"] == match_withdrawal.pending_for_movements(
                [publix],
            )
            assert not each["publix"].frees_a_line

    def test_a_row_leaving_with_its_movements_is_not_counted_as_kept(
        self, app, seed_user,
    ):
        """``rows_leaving`` parity: a minted envelope deleted with its purchase is not "kept".

        The answer the act gives a press that deletes the row too
        (``take_out_of_matches(rows_leaving=...)``, the read
        ``pending_for_rows`` makes for a row delete): the creation naming the
        envelope stays only while the envelope does.
        """
        with app.app_context():
            _line, created = a_purchase_in_a_minted_envelope(seed_user)
            db.session.commit()
            purchase = db.session.get(TransactionEntry, created.entry_id)
            envelope = db.session.get(Transaction, created.transaction_id)

            alone = match_withdrawal.pending_for_each({"x": [purchase]})["x"]
            leaving = match_withdrawal.pending_for_each(
                {"x": [purchase]}, {"x": [envelope]},
            )["x"]

            assert alone.kept_rows == 1, "the envelope stays"
            assert leaving.kept_rows == 0
            assert leaving == match_withdrawal.pending_for_rows([envelope])
