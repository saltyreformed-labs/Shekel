"""
Shekel Budget App -- Tests for the tax-law notice on every owner page (plan step salary:X-at-4).

From November 1, every page the owner opens carries a banner until next year's
tax law is in the app (rulings salary:R-SAL74 and R-SAL87), naming what is
missing (R-SAL86) and what prices it meanwhile: each missing year once, its
missing states together, priced on "older rules" (ruling plan#3198), and a
whole missing year on the newest year's rules when no state lags (R-SAL87).
It has no close button, and a companion never sees it.

Every law is made up and installed through the ``tax_law`` fixture, and the
day is set by replacing :func:`app.utils.dates.display_today` -- the one clock
the notice reads -- so nothing here moves when a real year is published or
when the weekly calendar sweep fakes the date.
"""

import re
from datetime import date
from pathlib import Path

import pytest
from flask import render_template_string
from flask_login import login_user

from app import ref_cache
from app.context_processors import _current_user_is_owner
from app.utils import dates
from tests._test_helpers import EMPTY_TAX_LAW, made_up_law

_NOTICE_ID = 'id="tax-law-notice"'


_THROUGH_2026 = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))


@pytest.fixture
def on_day(monkeypatch):
    """Return a setter making the notice's clock read the day given."""
    def set_day(day):
        monkeypatch.setattr(dates, "display_today", lambda: day)
    return set_day


def _notice(html):
    """Return the notice's WHOLE element from a page, or ``None`` when the page has none.

    From the ``<div`` carrying the id to the ``</div>`` that closes it, found by
    counting nested divs -- so a control placed anywhere inside the notice,
    a close button beside the text included, is inside what is returned.
    """
    anchor = html.find(_NOTICE_ID)
    if anchor == -1:
        return None
    start = html.rfind("<div", 0, anchor)
    depth = 0
    for tag in re.finditer(r"<div\b|</div>", html[start:]):
        depth += 1 if tag.group() == "<div" else -1
        if depth == 0:
            return html[start:start + tag.end()]
    raise AssertionError("the tax-law notice's element never closes")


def _sentences(html):
    """Return each paragraph of a page's notice as one line of words, in order."""
    notice = _notice(html)
    assert notice is not None, "the page carries no tax-law notice"
    return [" ".join(p.split()) for p in re.findall(r"<p[^>]*>(.*?)</p>", notice, re.S)]


class TestTheNoticeIsOnEveryOwnerPage:
    """R-SAL87: every page, not three, because the in-app notice is the alarm needing no email."""

    @pytest.mark.parametrize("url", [
        "/dashboard", "/grid", "/salary", "/analytics", "/settings", "/savings", "/templates",
    ])
    def test_from_november_1_every_owner_page_names_the_missing_year(
        self, auth_client, tax_law, on_day, url,
    ):
        """The forgotten year, and the year its figures are priced on meanwhile."""
        tax_law(_THROUGH_2026)
        on_day(date(2026, 11, 1))

        response = auth_client.get(url)

        assert response.status_code == 200
        assert _sentences(response.get_data(as_text=True)) == [
            "Shekel doesn't have the 2027 tax rules yet. Paychecks and tax estimates "
            "for 2027 use 2026's rules until an app update adds them."
        ]

    def test_the_notice_has_no_close_button(self, auth_client, tax_law, on_day):
        """It goes away when the law is added, never when dismissed (R-SAL87)."""
        tax_law(_THROUGH_2026)
        on_day(date(2026, 11, 1))

        notice = _notice(auth_client.get("/dashboard").get_data(as_text=True))

        assert notice is not None
        assert "btn-close" not in notice
        assert "data-bs-dismiss" not in notice
        assert "alert-dismissible" not in notice

    def test_a_state_the_year_left_out_is_named_and_priced_on_older_rules(
        self, auth_client, tax_law, on_day,
    ):
        """A year shipped federal-first names its missing state until it lands (R-SAL86, plan#3198)."""
        tax_law(made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}), (2027, {})))
        on_day(date(2027, 3, 1))

        assert _sentences(auth_client.get("/grid").get_data(as_text=True)) == [
            "Shekel's 2027 tax rules are missing NC. NC tax for 2027 uses older rules "
            "until an app update adds them."
        ]

    def test_two_states_the_year_left_out_are_named_together(
        self, auth_client, tax_law, on_day,
    ):
        """One sentence pair for the year, its states named together (ruling plan#3198)."""
        tax_law(made_up_law(
            (2025, {"NC": "0.0425", "SC": "0.0500"}),
            (2026, {"NC": "0.0399", "SC": "0.0500"}),
            (2027, {}),
        ))
        on_day(date(2027, 3, 1))

        assert _sentences(auth_client.get("/grid").get_data(as_text=True)) == [
            "Shekel's 2027 tax rules are missing NC and SC. NC and SC tax for 2027 use "
            "older rules until an app update adds them."
        ]

    def test_each_year_missing_a_state_says_so_once(self, auth_client, tax_law, on_day):
        """NC last listed in 2025: 2026 and 2027 each lack it, so each says so in its own pair."""
        tax_law(made_up_law((2025, {"NC": "0.0425"}), (2026, {}), (2027, {})))
        on_day(date(2027, 3, 1))

        assert _sentences(auth_client.get("/dashboard").get_data(as_text=True)) == [
            "Shekel's 2026 tax rules are missing NC. NC tax for 2026 uses older rules "
            "until an app update adds them.",
            "Shekel's 2027 tax rules are missing NC. NC tax for 2027 uses older rules "
            "until an app update adds them.",
        ]

    def test_a_missing_year_with_a_lagging_state_says_older_rules(
        self, auth_client, tax_law, on_day,
    ):
        """November 2027, with 2027 still missing NC and 2028 not added.

        2028's federal and FICA lines price on 2027 and its NC line on 2026, so
        no one year is true of the whole: its sentence says "older rules"
        (ruling plan#3198, amending R-SAL91's sentence naming each part's year).
        """
        tax_law(made_up_law((2026, {"NC": "0.0399"}), (2027, {})))
        on_day(date(2027, 11, 2))

        assert _sentences(auth_client.get("/dashboard").get_data(as_text=True)) == [
            "Shekel's 2027 tax rules are missing NC. NC tax for 2027 uses older rules "
            "until an app update adds them.",
            "Shekel doesn't have the 2028 tax rules yet. Paychecks and tax estimates "
            "for 2028 use older rules until an app update adds them.",
        ]

    def test_the_rulings_own_two_year_example(self, auth_client, tax_law, on_day):
        """Ruling plan#3198's two sentences, word for word: 2028 lacks NC and SC, 2029 is absent."""
        tax_law(made_up_law((2027, {"NC": "0.0399", "SC": "0.0500"}), (2028, {})))
        on_day(date(2028, 11, 2))

        assert _sentences(auth_client.get("/dashboard").get_data(as_text=True)) == [
            "Shekel's 2028 tax rules are missing NC and SC. NC and SC tax for 2028 use "
            "older rules until an app update adds them.",
            "Shekel doesn't have the 2029 tax rules yet. Paychecks and tax estimates "
            "for 2029 use older rules until an app update adds them.",
        ]

    def test_a_law_with_no_year_says_it_has_nothing_to_use(self, auth_client, tax_law, on_day):
        """No release ships an empty law; a test installs one, and the words stay true of it."""
        tax_law(EMPTY_TAX_LAW)
        on_day(date(2026, 11, 1))

        assert _sentences(auth_client.get("/dashboard").get_data(as_text=True)) == [
            "Shekel doesn't have the 2027 tax rules yet. It has no earlier year's rules "
            "to use for 2027 until an app update adds them."
        ]


class TestTheNoticeIsSilentOtherwise:
    """Before November 1, once the year is in, and for anyone but the owner."""

    def test_a_silent_notice_leaves_the_page_byte_identical(
        self, app, seed_user, tax_law, on_day,
    ):
        """While silent, the notice's block renders to NOTHING -- not even whitespace.

        The release that ships these alarms is graded page for page against
        the one before it, so ``base.html`` renders with the block and with it
        cut out, in ONE request (one CSRF token), and the bytes must match.
        With the notice showing, the same comparison must differ, which is
        what shows it can fail.
        """
        source = (Path(app.root_path) / "templates" / "base.html").read_text(encoding="utf-8")
        # From the start of the comment's line, found without its trim marker,
        # so a lost marker fails the byte comparison rather than this lookup.
        start = source.rindex("\n", 0, source.index("--- Tax-law notice")) + 1
        without = source[:start] + source[source.index("\n  {# --- Welcome Banner"):]
        tax_law(_THROUGH_2026)

        with app.test_request_context("/dashboard"):
            login_user(seed_user["user"])
            on_day(date(2026, 10, 31))
            assert render_template_string(source) == render_template_string(without)
            on_day(date(2026, 11, 1))
            assert render_template_string(source) != render_template_string(without)

    def test_october_31_carries_no_notice(self, auth_client, tax_law, on_day):
        """The notice starts on November 1 (the display-timezone date)."""
        tax_law(_THROUGH_2026)
        on_day(date(2026, 10, 31))

        assert _notice(auth_client.get("/dashboard").get_data(as_text=True)) is None

    def test_adding_the_year_removes_the_notice(self, auth_client, tax_law, on_day):
        """The whole cure is the release that adds the year."""
        tax_law(made_up_law(
            (2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}), (2027, {"NC": "0.0350"}),
        ))
        on_day(date(2026, 12, 15))

        assert _notice(auth_client.get("/dashboard").get_data(as_text=True)) is None

    def test_a_companion_never_sees_it(self, companion_client, tax_law, on_day):
        """Owner only: the companion's pages carry no tax-law notice."""
        tax_law(_THROUGH_2026)
        on_day(date(2026, 11, 1))

        response = companion_client.get("/companion/")

        assert response.status_code == 200
        assert _notice(response.get_data(as_text=True)) is None

    @pytest.mark.parametrize("failure", [RuntimeError, KeyError])
    def test_an_owner_the_role_cache_cannot_confirm_is_not_treated_as_one(
        self, app, seed_user, monkeypatch, failure,
    ):
        """The owner gate both banners read fails CLOSED while the role cache cannot answer.

        Mid-migration or mid-startup the reference cache raises; the gate must
        then answer "not an owner", so neither the tax-law notice nor the MFA
        nag shows on a page it cannot vouch for.
        """
        def unavailable(_member):
            raise failure("the role cache cannot answer")

        with app.test_request_context("/dashboard"):
            login_user(seed_user["user"])
            assert _current_user_is_owner() is True
            monkeypatch.setattr(ref_cache, "role_id", unavailable)
            assert _current_user_is_owner() is False

    def test_a_signed_out_visitor_never_sees_it(self, client, tax_law, on_day):
        """The login page is public; the notice is not."""
        tax_law(_THROUGH_2026)
        on_day(date(2026, 11, 1))

        response = client.get("/login")

        assert response.status_code == 200
        assert _notice(response.get_data(as_text=True)) is None
