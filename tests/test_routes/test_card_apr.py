"""Tests for the card's APR doors and the APR section of the Card terms card.

Plan step **credit_card:CC-3** (developer rulings **R-CC27**, the set-by-date
door plus a remove door, and **R-CC28**, the section inside R-CC25's card
once terms exist).  What is pinned:

* the set door creates the row for a date (the percent stored as a
  fraction), REWRITES the same date on a second submit, and refuses a bad
  payload writing nothing;
* the remove door deletes this card's row and 404s for any other row;
* every gate -- not yours, not a card, not found, and a card with NO terms
  (dormant: the section does not render, so the door does not serve) --
  each paired with the routing case a URL-map 404 would otherwise hide;
* the page: no APR section on a dormant card, the empty section on a
  configured card, and the series newest first with the rate in effect
  today as a percent and a remove form per row.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models.credit_card_params import CreditCardParams
from app.models.loan_features import RateHistory
from app.utils.dates import display_today
from tests._test_helpers import create_account_of_type

#: Terms for a configured card (the values do not matter to the APR).
_TERMS = {
    "effective_date": date(2026, 1, 1),
    "statement_close_day": 20,
    "payment_due_day": 15,
    "min_payment_percent": Decimal("0.0250"),
    "min_payment_floor": Decimal("25.00"),
    "cashback_rate": Decimal("0.0150"),
    "auto_redeem_threshold": None,
    "credit_limit": None,
}


def _card(owner, name="Visa"):
    """A Credit Card account of *owner*'s (a ``seed_user``-shaped dict), no terms."""
    account = create_account_of_type(owner, db.session, "Credit Card", name)
    db.session.commit()
    return account


def _configured_card(owner, name="Visa"):
    """A Credit Card with its terms row: the state the APR section renders in."""
    account = _card(owner, name)
    db.session.add(CreditCardParams(
        account_id=account.id, user_id=account.user_id, **_TERMS,
    ))
    db.session.commit()
    return account


def _rows_of(account):
    """The account's rate rows, oldest first, as ``(date, rate)`` pairs."""
    db.session.expire_all()
    return [
        (r.effective_date, r.interest_rate)
        for r in db.session.query(RateHistory)
        .filter_by(account_id=account.id)
        .order_by(RateHistory.effective_date)
    ]


def _row(account, effective_date, rate):
    """Seed one rate row directly and return its id."""
    row = RateHistory(
        account_id=account.id, effective_date=effective_date,
        interest_rate=Decimal(rate),
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def _set(client, account_id, effective_date, rate):
    """POST the APR form as the browser does: a date and a PERCENT."""
    return client.post(
        f"/accounts/{account_id}/card/rate",
        data={"effective_date": effective_date.isoformat(), "interest_rate": rate},
    )


def _remove(client, account_id, row_id):
    """POST the remove form."""
    return client.post(f"/accounts/{account_id}/card/rate/{row_id}/delete")


@pytest.fixture(name="apr_days")
def _apr_days():
    """The three days the APR cases use, read off the app's today ONCE per test.

    ``past`` is well inside the past and ``future`` well ahead, so "in effect
    today" holds on every day the suite runs -- read when the test RUNS, never
    at import, so a clock moved before the test body (the calendar sweep's
    session-scoped one, or an autouse freeze) moves them too.  A
    function-scoped freeze requested AFTER ``apr_days`` would not.
    """
    today = display_today()
    past = today - timedelta(days=60)
    return SimpleNamespace(
        past=past, earlier=past - timedelta(days=90),
        future=today + timedelta(days=60),
    )


class TestTheSetDoor:
    """Create the row for a date, or rewrite its rate."""

    def test_a_new_date_creates_the_row_as_a_fraction(self, apr_days, auth_client, seed_user):
        """24.99 on the form is 0.24990 in the column; then back to the page."""
        card = _configured_card(seed_user)

        response = _set(auth_client, card.id, apr_days.past, "24.99")

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/accounts/{card.id}/details")
        assert _rows_of(card) == [(apr_days.past, Decimal("0.24990"))]

    def test_the_same_date_again_rewrites_the_rate(self, apr_days, auth_client, seed_user):
        """A second submit for the date is the correction: one row, new rate,
        same id."""
        card = _configured_card(seed_user)
        _set(auth_client, card.id, apr_days.past, "24.99")
        first_id = db.session.query(RateHistory).filter_by(account_id=card.id).one().id

        response = _set(auth_client, card.id, apr_days.past, "27.99")

        assert response.status_code == 302
        assert _rows_of(card) == [(apr_days.past, Decimal("0.27990"))]
        assert db.session.query(RateHistory).filter_by(
            account_id=card.id,
        ).one().id == first_id

    def test_a_second_date_is_a_second_row(self, apr_days, auth_client, seed_user):
        """Two dates, two rows."""
        card = _configured_card(seed_user)
        _set(auth_client, card.id, apr_days.earlier, "19.99")
        _set(auth_client, card.id, apr_days.past, "24.99")

        assert _rows_of(card) == [
            (apr_days.earlier, Decimal("0.19990")), (apr_days.past, Decimal("0.24990")),
        ]

    def test_a_rate_above_100_percent_is_refused_and_nothing_is_written(
        self, apr_days, auth_client, seed_user,
    ):
        """The schema's unit-interval bound, in the form's percent domain."""
        card = _configured_card(seed_user)

        response = _set(auth_client, card.id, apr_days.past, "101")

        assert response.status_code == 302
        assert _rows_of(card) == []

    def test_a_blank_date_is_refused_and_nothing_is_written(
        self, auth_client, seed_user,
    ):
        """A required control left empty."""
        card = _configured_card(seed_user)

        response = auth_client.post(
            f"/accounts/{card.id}/card/rate",
            data={"effective_date": "", "interest_rate": "24.99"},
        )

        assert response.status_code == 302
        assert _rows_of(card) == []

    def test_a_four_digit_year_typo_is_refused(self, auth_client, seed_user):
        """``0202-03-01`` would become the series' earliest row for good."""
        card = _configured_card(seed_user)

        response = auth_client.post(
            f"/accounts/{card.id}/card/rate",
            data={"effective_date": "0202-03-01", "interest_rate": "24.99"},
        )

        assert response.status_code == 302
        assert _rows_of(card) == []

    def test_the_refusal_is_heard(self, apr_days, auth_client, seed_user, seed_periods_today):
        """The redirect lands with the validation flash on the page, and not
        the success flash."""
        card = _configured_card(seed_user)

        response = auth_client.post(
            f"/accounts/{card.id}/card/rate",
            data={"effective_date": apr_days.past.isoformat(), "interest_rate": "101"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Please correct the highlighted errors" in response.data
        assert b"APR saved" not in response.data


class TestTheRemoveDoor:
    """Delete this card's row; 404 for any other."""

    def test_this_cards_row_is_removed(self, apr_days, auth_client, seed_user):
        """The named row goes, its sibling stays, back to the page."""
        card = _configured_card(seed_user)
        doomed = _row(card, apr_days.earlier, "0.1999")
        _row(card, apr_days.past, "0.2499")

        response = _remove(auth_client, card.id, doomed)

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/accounts/{card.id}/details")
        assert _rows_of(card) == [(apr_days.past, Decimal("0.24990"))]

    def test_an_unknown_row_is_404(self, auth_client, seed_user):
        """No row carries the id."""
        card = _configured_card(seed_user)

        assert _remove(auth_client, card.id, 999_999).status_code == 404

    def test_another_accounts_row_is_404_and_survives(self, apr_days, auth_client, seed_user):
        """The owner's OTHER card's row, named through this card's URL: not
        this card's to remove."""
        card = _configured_card(seed_user)
        other = _configured_card(seed_user, name="Amex")
        theirs = _row(other, apr_days.past, "0.2999")

        response = _remove(auth_client, card.id, theirs)

        assert response.status_code == 404
        assert _rows_of(other) == [(apr_days.past, Decimal("0.29990"))]


class TestTheGates:
    """404 for not-yours, not-a-card, not-found and DORMANT, each paired
    with the route that serves."""

    def test_another_owners_card_is_404_and_gains_no_row(
        self, apr_days, auth_client, second_user,
    ):
        """The victim's configured card, written directly, gains nothing
        through either door."""
        victim = _configured_card(second_user, name="Other Visa")
        theirs = _row(victim, apr_days.past, "0.2999")

        set_response = _set(auth_client, victim.id, apr_days.earlier, "1.00")
        remove_response = _remove(auth_client, victim.id, theirs)

        assert set_response.status_code == 404
        assert remove_response.status_code == 404
        assert b"Other Visa" not in set_response.data
        assert _rows_of(victim) == [(apr_days.past, Decimal("0.29990"))]

    def test_a_checking_account_is_404(self, apr_days, auth_client, seed_user):
        """The kind gate, on both doors."""
        checking = seed_user["account"]

        assert _set(auth_client, checking.id, apr_days.past, "24.99").status_code == 404
        assert _remove(auth_client, checking.id, 1).status_code == 404
        assert _rows_of(checking) == []

    def test_a_missing_account_is_404(self, apr_days, auth_client, seed_user):
        """Not-found and not-yours are one answer."""
        assert _set(auth_client, 999_999, apr_days.past, "24.99").status_code == 404
        assert _remove(auth_client, 999_999, 1).status_code == 404

    def test_a_dormant_card_is_404_on_both_doors(self, apr_days, auth_client, seed_user):
        """No terms row: the section never rendered, so a POST to either
        door is a forged or stale request, and nothing is written."""
        card = _card(seed_user)
        stray = _row(card, apr_days.past, "0.2499")

        assert _set(auth_client, card.id, apr_days.earlier, "19.99").status_code == 404
        assert _remove(auth_client, card.id, stray).status_code == 404
        assert _rows_of(card) == [(apr_days.past, Decimal("0.24990"))]

    def test_both_urls_route_for_a_configured_card(self, apr_days, auth_client, seed_user):
        """The pair for every 404 above: the same URLs, a configured card,
        serve.  A 404 from the URL map and one from a gate look alike."""
        card = _configured_card(seed_user)
        assert _set(auth_client, card.id, apr_days.past, "24.99").status_code == 302
        row_id = db.session.query(RateHistory).filter_by(account_id=card.id).one().id
        assert _remove(auth_client, card.id, row_id).status_code == 302


class TestThePageShowsTheApr:
    """The APR section of the Card terms card, in each of its states."""

    def test_a_dormant_card_renders_no_apr_section(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Terms first: no section, no form, no door named."""
        card = _card(seed_user)

        response = auth_client.get(f"/accounts/{card.id}/details")

        assert response.status_code == 200
        assert b"Card terms" in response.data
        assert b"card-apr-today" not in response.data
        assert f"/accounts/{card.id}/card/rate".encode() not in response.data

    def test_a_configured_card_with_no_rows_renders_the_empty_section(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The section, its two empty states, and the set form's door."""
        card = _configured_card(seed_user)

        response = auth_client.get(f"/accounts/{card.id}/details")

        assert response.status_code == 200
        assert b"No APR in effect today." in response.data
        assert b"No APR recorded." in response.data
        assert f'action="/accounts/{card.id}/card/rate"'.encode() in response.data
        assert f"/accounts/{card.id}/card/rate/".encode() not in response.data

    def test_the_series_renders_newest_first_with_todays_rate(
        self, apr_days, auth_client, seed_user, seed_periods_today,
    ):
        """Three rows -- one future -- list newest first as percents, each
        with its remove form; today's rate is the latest PAST row's, not
        the future one's (24.990%, not 29.990%)."""
        card = _configured_card(seed_user)
        earlier = _row(card, apr_days.earlier, "0.1999")
        past = _row(card, apr_days.past, "0.2499")
        future = _row(card, apr_days.future, "0.2999")

        response = auth_client.get(f"/accounts/{card.id}/details")

        assert response.status_code == 200
        page = response.data.decode()
        today = re.search(
            r'id="card-apr-today">(.*?)</p>', page, re.DOTALL,
        ).group(1)
        assert "In effect today:" in today
        assert "24.990%" in today
        assert "29.990%" not in today
        history = re.search(
            r'id="card-apr-history">(.*?)</ul>', page, re.DOTALL,
        ).group(1)
        assert re.findall(r"(\d+\.\d{3})%", history) == ["29.990", "24.990", "19.990"]
        assert [
            int(m) for m in re.findall(r"/card/rate/(\d+)/delete", history)
        ] == [future, past, earlier]
