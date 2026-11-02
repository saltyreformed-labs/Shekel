"""
Shekel Budget App -- the card's terms door and its card (plan step credit_card:CC-2)

``POST /accounts/<id>/card/terms`` (developer ruling **R-CC24**: one door,
one schema) and the "Card terms" card the cash detail page shows a revolving
account (**R-CC25**).  Since plan#2331 (developer ruling **plan#2333**) the
terms are a dated series: each submit states a whole version from the date
it names.  What is pinned:

* the door CREATES a version from a whole form, storing fractions and the
  ACCOUNT's owner; a second submit on the SAME date corrects that version
  (same id, one row) -- the second time, because a producer right on the
  first write can be wrong on the repeat -- and one on a LATER date adds a
  version and leaves the first as it was (the done-when of plan#2331 at the
  door: a change of terms leaves the terms before it standing);
* a submit naming no effective date is refused and writes nothing;
* a nullable term stated once and blanked next is CLEARED (the "clearable
  from the UI" contract every nullable form field in this app holds);
* a refused payload writes nothing: a card with no row stays dormant, and
  one with a row keeps every stored value;
* the door is ownership-gated (404 for another owner's card) and kind-gated
  (404 for a checking account), and both are paired with the case that the
  URL routes for a real card, so a 404 from the map cannot pass for a 404
  from the gate;
* the page renders the card blank for a dormant card, filled (in percents)
  after a save from the version in force on the page's day -- not the newest,
  when the newest is a change still to come -- and not at all for a non-card;
  the terms history lists every version newest first and marks the one in
  force; and saving terms changes NOTHING on the page outside the card --
  dormancy and its end are one render apart, and the band, the reconcile
  panel, the difference card and the history are byte-identical across them.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.credit_card_params import CreditCardParams
from app.utils.dates import display_today
from tests._test_helpers import create_account_of_type

#: A whole valid form, as the browser posts it.
_FORM = {
    "effective_date": "2026-01-01",
    "statement_close_day": "20",
    "payment_due_day": "15",
    "min_payment_percent": "2.5",
    "min_payment_floor": "25.00",
    "cashback_rate": "1.5",
    "auto_redeem_threshold": "25.00",
    "credit_limit": "5000.00",
}

#: What :data:`_FORM` stores.
_STORED = {
    "effective_date": date(2026, 1, 1),
    "statement_close_day": 20,
    "payment_due_day": 15,
    "min_payment_percent": Decimal("0.0250"),
    "min_payment_floor": Decimal("25.00"),
    "cashback_rate": Decimal("0.0150"),
    "auto_redeem_threshold": Decimal("25.00"),
    "credit_limit": Decimal("5000.00"),
}


def _card(owner, name="Visa"):
    """A Credit Card account of *owner*'s (a ``seed_user``-shaped dict), no terms."""
    account = create_account_of_type(owner, db.session, "Credit Card", name)
    db.session.commit()
    return account


def _terms_of(account):
    """The account's terms versions, earliest first (a list, so a duplicate shows)."""
    db.session.expire_all()
    return db.session.query(CreditCardParams).filter_by(
        account_id=account.id,
    ).order_by(CreditCardParams.effective_date).all()


def _rendered_values(page):
    """Each terms control's rendered ``value``, keyed by its ``name``.

    Read off the control itself (``name=... value=...`` inside one tag), so
    an assertion is tied to the control rather than to a substring anywhere
    on the page.
    """
    return {
        name: value
        for name, value in re.findall(
            r'name="([a-z_]+)"\s+value="([^"]*)"', page,
        )
        if name in _FORM
    }


def _without_csrf_tokens(page):
    """*page* with every CSRF token blanked, so two renders can be compared.

    The token is time-salted and minted per request, so two renders can differ
    in it whatever else they share; blanking it leaves the rest to compare.
    """
    page = re.sub(r'name="csrf_token" value="[^"]*"', 'name="csrf_token"', page)
    return re.sub(
        r'name="csrf-token" content="[^"]*"', 'name="csrf-token"', page,
    )


def _post(client, account_id, **overrides):
    """POST the form with *overrides* applied (``None`` drops a key)."""
    form = {**_FORM, **overrides}
    form = {k: v for k, v in form.items() if v is not None}
    return client.post(f"/accounts/{account_id}/card/terms", data=form)


class TestTheDoorWritesTheRow:
    """Create on the first submit; correct on the same date, add on a later one."""

    def test_a_whole_form_creates_the_row(self, auth_client, seed_user):
        """The first submit stores every column, as fractions, owned by the account's owner."""
        account = _card(seed_user)
        assert _terms_of(account) == []

        response = _post(auth_client, account.id)

        assert response.status_code == 302
        assert response.headers["Location"].endswith(
            f"/accounts/{account.id}/details",
        )
        rows = _terms_of(account)
        assert len(rows) == 1
        for column, value in _STORED.items():
            assert getattr(rows[0], column) == value, column
        assert rows[0].user_id == seed_user["user"].id

    def test_a_same_date_submit_corrects_that_version(self, auth_client, seed_user):
        """The repeat on the same date changes every column in place: one row, the same id."""
        account = _card(seed_user)
        _post(auth_client, account.id)
        first_id = _terms_of(account)[0].id

        response = _post(
            auth_client, account.id,
            statement_close_day="5", payment_due_day="1",
            min_payment_percent="1", min_payment_floor="35.00",
            cashback_rate="2", auto_redeem_threshold="50.00",
            credit_limit="12000.00",
        )

        assert response.status_code == 302
        rows = _terms_of(account)
        assert len(rows) == 1
        row = rows[0]
        assert row.id == first_id
        assert row.statement_close_day == 5
        assert row.payment_due_day == 1
        assert row.min_payment_percent == Decimal("0.0100")
        assert row.min_payment_floor == Decimal("35.00")
        assert row.cashback_rate == Decimal("0.0200")
        assert row.auto_redeem_threshold == Decimal("50.00")
        assert row.credit_limit == Decimal("12000.00")

    def test_a_later_date_adds_a_version_and_the_first_stands(
        self, auth_client, seed_user,
    ):
        """Mar 1's change is a second version; January's terms are untouched.

        plan#2333's example at the door: the close moves to the 5th, the due
        day to the 30th and cash back to 1.0% from Mar 1, and the version
        every statement before Mar 1 reads keeps its 20th, 15th and 1.5%.
        """
        account = _card(seed_user)
        _post(auth_client, account.id)
        first_id = _terms_of(account)[0].id

        response = _post(
            auth_client, account.id, effective_date="2026-03-01",
            statement_close_day="5", payment_due_day="30", cashback_rate="1",
        )

        assert response.status_code == 302
        rows = _terms_of(account)
        assert [r.effective_date for r in rows] == [
            date(2026, 1, 1), date(2026, 3, 1),
        ]
        assert rows[0].id == first_id
        for column, value in _STORED.items():
            assert getattr(rows[0], column) == value, column
        assert (rows[1].statement_close_day, rows[1].payment_due_day) == (5, 30)
        assert rows[1].cashback_rate == Decimal("0.0100")

    def test_a_blanked_nullable_term_is_cleared(self, auth_client, seed_user):
        """Stated once, blank next: the threshold and the limit go back to NULL."""
        account = _card(seed_user)
        _post(auth_client, account.id)
        assert _terms_of(account)[0].credit_limit == Decimal("5000.00")

        _post(auth_client, account.id, auto_redeem_threshold="", credit_limit="")

        row = _terms_of(account)[0]
        assert row.auto_redeem_threshold is None
        assert row.credit_limit is None

    def test_a_blank_cashback_is_refused_and_the_stated_rate_stands(
        self, auth_client, seed_user,
    ):
        """A submit carrying no cash-back value rewrites nothing (R-CC24: five required).

        The form pre-fills the control with ``0.00`` and marks it required, so
        a browser never posts it empty; a POST that does is crafted or
        truncated, and the stated rate is not silently zeroed by it.
        """
        account = _card(seed_user)
        _post(auth_client, account.id)
        assert _terms_of(account)[0].cashback_rate == Decimal("0.0150")

        response = _post(auth_client, account.id, cashback_rate="")

        assert response.status_code == 302
        assert _terms_of(account)[0].cashback_rate == Decimal("0.0150")

    def test_a_typed_zero_cashback_is_stored(self, auth_client, seed_user):
        """``0`` typed in the cash-back box is a value: stored as zero (E-12)."""
        account = _card(seed_user)
        _post(auth_client, account.id)

        _post(auth_client, account.id, cashback_rate="0")

        assert _terms_of(account)[0].cashback_rate == Decimal("0")


class TestARefusalWritesNothing:
    """A bad payload leaves the card exactly as it was."""

    def test_a_submit_naming_no_date_is_refused(self, auth_client, seed_user):
        """No effective date, no version: a change of terms must name its day."""
        account = _card(seed_user)

        response = _post(auth_client, account.id, effective_date=None)

        assert response.status_code == 302
        assert _terms_of(account) == []

    def test_a_refused_first_submit_keeps_the_card_dormant(
        self, auth_client, seed_user,
    ):
        """No row is created from a form missing a required term."""
        account = _card(seed_user)

        response = _post(auth_client, account.id, min_payment_percent="")

        assert response.status_code == 302
        assert _terms_of(account) == []

    def test_a_refused_rewrite_keeps_every_stored_value(
        self, auth_client, seed_user,
    ):
        """An out-of-domain day refuses the whole submit; nothing moves."""
        account = _card(seed_user)
        _post(auth_client, account.id)

        response = _post(
            auth_client, account.id,
            statement_close_day="32", min_payment_floor="99.00",
        )

        assert response.status_code == 302
        rows = _terms_of(account)
        assert len(rows) == 1
        for column, value in _STORED.items():
            assert getattr(rows[0], column) == value, column

    def test_the_refusal_is_heard(self, auth_client, seed_user, seed_periods_today):
        """The redirect lands with the validation flash on the page."""
        account = _card(seed_user)

        response = auth_client.post(
            f"/accounts/{account.id}/card/terms",
            data={**_FORM, "statement_close_day": "32"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Please correct the highlighted errors" in response.data


class TestTheGates:
    """404 for not-yours and not-a-card, paired with the route that serves."""

    def test_another_owners_card_is_404_and_untouched(
        self, auth_client, second_user,
    ):
        """The victim's card gains no row and the response names nothing."""
        victim = _card(second_user, name="Other Visa")

        response = _post(auth_client, victim.id)

        assert response.status_code == 404
        assert b"Other Visa" not in response.data
        assert _terms_of(victim) == []

    def test_another_owners_stored_terms_are_untouched(
        self, auth_client, second_user,
    ):
        """A rewrite aimed at the victim's row is refused before it reads the form.

        The victim's row is written directly rather than through a second
        authenticated client, so the case grades the attacker's refusal alone.
        """
        victim = _card(second_user, name="Other Visa")
        db.session.add(CreditCardParams(
            account_id=victim.id, user_id=victim.user_id, **_STORED,
        ))
        db.session.commit()
        before = _terms_of(victim)[0]
        before_values = {c: getattr(before, c) for c in _STORED}

        response = _post(auth_client, victim.id, credit_limit="1.00")

        assert response.status_code == 404
        after = _terms_of(victim)
        assert len(after) == 1
        assert {c: getattr(after[0], c) for c in _STORED} == before_values

    def test_a_checking_account_is_404(self, auth_client, seed_user):
        """The kind gate: a card door reached with a non-card id is refused."""
        checking = seed_user["account"]

        response = _post(auth_client, checking.id)

        assert response.status_code == 404
        assert _terms_of(checking) == []

    def test_a_missing_account_is_404(self, auth_client, seed_user):
        """Not-found and not-yours are one answer."""
        response = _post(auth_client, 999999)
        assert response.status_code == 404

    def test_the_url_routes_for_a_real_card(self, auth_client, seed_user):
        """The pair for every 404 above: the same URL, a real card, serves.

        A 404 from the URL map and a 404 from the gate are indistinguishable
        to the three cases above; this is what tells them apart.
        """
        account = _card(seed_user)
        response = _post(auth_client, account.id)
        assert response.status_code == 302


class TestThePageShowsTheCard:
    """The cash detail page's Card terms card, in each of its states."""

    def test_a_dormant_card_renders_the_card_blank(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """No row: the card renders, says so, and the form posts to the door."""
        account = _card(seed_user)

        response = auth_client.get(f"/accounts/{account.id}/details")

        assert response.status_code == 200
        assert b"Card terms" in response.data
        assert b"No terms recorded yet" in response.data
        assert f"/accounts/{account.id}/card/terms".encode() in response.data
        # The interest-bearing Parameters card is NOT the one rendered.
        assert b"update_interest_params" not in response.data
        assert b"APY (Annual Percentage Yield)" not in response.data

    def test_a_saved_card_renders_its_terms_in_percents(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """After a save the controls carry the stored terms, rates as percents."""
        account = _card(seed_user)
        _post(auth_client, account.id)

        response = auth_client.get(f"/accounts/{account.id}/details")

        assert response.status_code == 200
        assert b"No terms recorded yet" not in response.data
        page = response.data.decode()
        assert _rendered_values(page) == {
            "statement_close_day": "20",
            "payment_due_day": "15",
            "min_payment_percent": "2.50",
            "min_payment_floor": "25.00",
            "cashback_rate": "1.50",
            "auto_redeem_threshold": "25.00",
            "credit_limit": "5000.00",
        }

    def test_the_form_fills_from_the_terms_in_force_today(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """A change still to come is listed, but today's terms fill the form.

        Two versions around the page's day: one 30 days back (close 20th), one
        30 days ahead (close 5th).  The newest is the future one, so a form
        filled from the newest would read 5; the history lists both, newest
        first, and marks only the one in force today.
        """
        account = _card(seed_user)
        today = display_today()
        past, future = today - timedelta(days=30), today + timedelta(days=30)
        _post(auth_client, account.id, effective_date=past.isoformat())
        _post(
            auth_client, account.id, effective_date=future.isoformat(),
            statement_close_day="5", payment_due_day="30",
        )

        response = auth_client.get(f"/accounts/{account.id}/details")

        assert response.status_code == 200
        page = response.data.decode()
        assert _rendered_values(page)["statement_close_day"] == "20"
        assert _rendered_values(page)["payment_due_day"] == "15"
        history = page.split('id="card-terms-history"', 1)[1].split("</ul>", 1)[0]
        items = history.split("<li")[1:]
        assert len(items) == 2
        assert future.strftime("%b %-d, %Y") in items[0]
        assert "Closes on the 5th, due on the 30th" in items[0]
        assert "In effect today" not in items[0]
        assert past.strftime("%b %-d, %Y") in items[1]
        assert "Closes on the 20th, due on the 15th" in items[1]
        assert "minimum 2.50%, at least $25.00" in items[1]
        assert "cash back 1.50%" in items[1]
        assert "auto-redeem at $25.00" in items[1]
        assert "limit $5,000.00" in items[1]
        assert "In effect today" in items[1]

    def test_a_checking_account_shows_no_card_terms(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The card is gated by the one card predicate; checking never sees it."""
        checking = seed_user["account"]

        response = auth_client.get(f"/accounts/{checking.id}/details")

        assert response.status_code == 200
        assert b"Card terms" not in response.data
        assert b"/card/terms" not in response.data

    def test_saving_the_terms_changes_nothing_outside_the_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Dormancy pinned: a terms row moves nothing the page shows outside its card.

        Rendered twice -- before and after a save -- the page is byte-identical
        outside the Card terms card (CSRF tokens blanked), so the row moves no
        figure on the balance band, the reconcile panel, the difference card
        or the history.  What this does NOT grade is the page BEFORE CC-2
        against the page after it; that is the full suite's, through every
        other cash-detail test this leaf left untouched.
        """
        account = _card(seed_user)

        dormant = _without_csrf_tokens(
            auth_client.get(f"/accounts/{account.id}/details").data.decode(),
        )
        _post(auth_client, account.id)
        # The save's flash is consumed by this render, so the pair compared
        # below differs by the terms row alone.
        auth_client.get(f"/accounts/{account.id}/details")
        saved = _without_csrf_tokens(
            auth_client.get(f"/accounts/{account.id}/details").data.decode(),
        )

        marker = '<div class="card acctd-params">'
        assert dormant.count(marker) == 1
        assert saved.count(marker) == 1
        assert dormant.split(marker)[0] == saved.split(marker)[0]
        # ...and the tail after the card's form (the scripts block) too.
        assert dormant.rsplit("</form>", 1)[1] == saved.rsplit("</form>", 1)[1]
        # The two DO differ inside the card, or the comparison graded nothing.
        assert dormant != saved
