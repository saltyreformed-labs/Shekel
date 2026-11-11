"""The salary pages say which year's tax rules priced a paycheck (plan step X-at-6).

Ruling R-SAL75: "each screen showing tax lines prints one line from it", read
off what priced the figure rather than worked out by the screen; ruling
R-SAL76 keeps a missing year on the nearest year's rules unchanged, and these
words are what make that visible (finding N-235).  The words are
``_tax_rules_macros.html``'s, the Taxes tab's too.

The cockpit prints them in the two places it names the pay stub that priced
the paycheck (ruling R-SAL100's placement): the hero caption gets the year
phrase, and the "Where this paycheck goes" card's foot the phrase and the
note.  Both only when some part was priced on another year's rules.  The
projection ledger prints the phrase and the note as one line above its table
for each tax year whose paychecks were priced so.

Every case installs a made-up law (ruling R-SAL80), so none moves when a real
year is published.  ``seed_periods`` saves only 2026's paydays, and the
cockpit reads saved periods, so the substituted year is 2026 under a law
ending 2025.  Each case reads the hero caption and the card alone: the full
page also carries the tax-law notice, whose words depend on today's date.
The ledger's case reads the whole page for its one exact line, which the
notice's words ("the 2027 tax rules") cannot match.
"""

import re
from decimal import Decimal
from html import unescape

from app.enums import WithholdingKindEnum
from app.extensions import db
from app.services.tax_config_service import TaxLawBasis
from app.tax_law import TaxLaw
from tests._test_helpers import add_test_pay_stub, made_up_state, made_up_year

from ._helpers import _create_profile

#: North Carolina in 2025 only: a 2026 paycheck is priced on 2025's rules.
_NC_TO_2025 = TaxLaw(years=(
    made_up_year(2025, states={"NC": made_up_state(Decimal("0.0425"))}),
))

#: North Carolina in 2025 and 2026: a 2026 paycheck is priced on its own.
_NC_TO_2026 = TaxLaw(years=(
    made_up_year(2025, states={"NC": made_up_state(Decimal("0.0425"))}),
    made_up_year(2026, states={"NC": made_up_state(Decimal("0.0399"))}),
))

#: 2026 shipped without its state (ruling R-SAL86): a South Carolina 2026
#: paycheck is federal and FICA 2026's, its state 2025's.
_SC_2026_WITHOUT_ITS_STATE = TaxLaw(years=(
    made_up_year(2025, states={"SC": made_up_state(Decimal("0.0500"))}),
    made_up_year(2026),
))


def _visible(html):
    """Return *html*'s visible text, entities decoded, one space apart."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _hero_caption(html):
    """Return the visible text of the cockpit hero's caption."""
    match = re.search(r'<div class="nw-hero__cap">(.*?)</div>', html, re.DOTALL)
    return _visible(match.group(1))


def _card_html(html):
    """Return the HTML of the "Where this paycheck goes" card."""
    start = html.index('id="anatomy-composition"')
    return html[start:html.index('<div class="card" id="anatomy-lines"')]


def _card(html):
    """Return the visible text of the "Where this paycheck goes" card."""
    return _visible(_card_html(html))


def _cockpit(auth_client, period):
    """GET the cockpit focused on *period*; return its HTML."""
    resp = auth_client.get(f"/salary?period={period.id}")
    assert resp.status_code == 200
    return resp.data.decode()


def _fragment(auth_client, profile, period):
    """GET the anatomy fragment for *profile* at *period*; return its HTML."""
    resp = auth_client.get(f"/salary/{profile.id}/anatomy/{period.id}")
    assert resp.status_code == 200
    return resp.data.decode()


class TestTheCockpitSaysWhichYearsRules:
    """The hero caption and the card name a substituted year's rules, and only one."""

    def test_a_year_past_the_newest_prints_the_newest_years_rules(
        self, app, auth_client, seed_user, seed_periods, tax_law,
    ):
        """A stub-priced 2026 paycheck under a law ending 2025: both places say so.

        The hero carries the phrase after the stub it already names; the note
        is the card's alone.
        """
        tax_law(_NC_TO_2025)
        with app.app_context():
            profile = _create_profile(seed_user)
            stub_day = seed_periods[2].start_date
            add_test_pay_stub(
                profile, stub_day, "2884.62",
                taxes={
                    WithholdingKindEnum.FEDERAL_INCOME: "250.00",
                    WithholdingKindEnum.STATE_INCOME: "120.00",
                    WithholdingKindEnum.SOCIAL_SECURITY: "178.85",
                    WithholdingKindEnum.MEDICARE: "41.83",
                },
            )
            db.session.commit()
            named = stub_day.strftime("%b %-d, %Y")

            html = _cockpit(auth_client, seed_periods[5])
            hero = _hero_caption(html)

            assert hero.endswith(
                f"taxes from your {named} pay stub · 2026 on 2025's tax rules"
            )
            assert "Shekel doesn't have" not in hero
            assert (
                f"Taxes from your {named} pay stub "
                "Tax year 2026 on 2025's tax rules. "
                "Shekel doesn't have 2026's tax rules."
            ) in _card(html)

    def test_the_stepped_card_says_so_for_its_own_paycheck(
        self, app, auth_client, seed_user, seed_periods, tax_law,
    ):
        """The anatomy fragment, the card a step swaps in, says it too.

        No stub here, so the tax formulas priced the paycheck: the words
        follow the rules, whichever path priced the taxes.
        """
        tax_law(_NC_TO_2025)
        with app.app_context():
            profile = _create_profile(seed_user)

            card = _card(_fragment(auth_client, profile, seed_periods[3]))

            assert card.endswith(
                "Tax year 2026 on 2025's tax rules. "
                "Shekel doesn't have 2026's tax rules."
            )

    def test_a_year_the_law_carries_prints_neither(
        self, app, auth_client, seed_user, seed_periods, tax_law,
    ):
        """2026 is in the law: neither the hero nor the card says a word of rules."""
        tax_law(_NC_TO_2026)
        with app.app_context():
            profile = _create_profile(seed_user)

            html = _cockpit(auth_client, seed_periods[5])
            fragment = _fragment(auth_client, profile, seed_periods[3])

            for text in (_hero_caption(html), _card(html), _card(fragment)):
                assert "tax rules" not in text
                assert "Shekel doesn't have" not in text
            # The card's sentence renders nothing here by itself, so what the
            # card's own guard keeps out is markup the visible text cannot
            # show: the empty line and its icon.  An unguarded hero would end
            # in "· 2026".
            for card in (_card_html(html), _card_html(fragment)):
                assert "bi-calendar2-x" not in card
            assert _hero_caption(html).endswith("taxes from the tax formulas")

    def test_a_year_without_its_state_names_each_part_and_the_profiles_state(
        self, app, auth_client, seed_user, seed_periods, tax_law,
    ):
        """2026 lacks South Carolina: each part's year, the state the profile's own.

        Federal and FICA resolve on 2026, the state on 2025, so the parts
        differ and the words name each.  Some of 2026's rules are the app's,
        so the note says "all of".  A South Carolina profile, so a literal
        "NC" at the state's place cannot pass.
        """
        tax_law(_SC_2026_WITHOUT_ITS_STATE)
        with app.app_context():
            profile = _create_profile(seed_user)
            profile.state_code = "SC"
            db.session.commit()
            per_part = (
                "2026: federal tax on 2026's rules, Social Security and "
                "Medicare on 2026's, SC tax on 2025's"
            )

            html = _cockpit(auth_client, seed_periods[5])
            hero, card = _hero_caption(html), _card(html)

            assert hero.endswith(f"taxes from the tax formulas · {per_part}")
            assert card.endswith(
                f"Tax year {per_part}. Shekel doesn't have all of 2026's tax rules."
            )
            assert re.search(r"\bNC\b", hero + card) is None


class TestTheProjectionLedgerSaysWhichYearsRules:
    """One line above the ledger per tax year priced on another year's rules."""

    def test_a_year_past_the_newest_has_its_line_and_a_carried_year_none(
        self, app, auth_client, seed_user, seed_periods_52, tax_law,
    ):
        """Paydays through 2027 under a law ending 2026: a 2027 line, no 2026 line.

        ``seed_periods_52`` saves 52 biweekly paydays from 2026-01-02, the last
        2027-12-17, so the ledger holds both years.  2027's paychecks are on
        2026's rules (ruling R-SAL76), one line for all of them, above the
        table; 2026's are on their own and say nothing.
        """
        tax_law(_NC_TO_2026)
        assert seed_periods_52[-1].start_date.year == 2027
        with app.app_context():
            profile = _create_profile(seed_user)

            resp = auth_client.get(f"/salary/{profile.id}/projection")
            assert resp.status_code == 200
            html = resp.data.decode()
            above_the_table = _visible(html[:html.index("<table")])

            line = (
                "Tax year 2027 on 2026's tax rules. "
                "Shekel doesn't have 2027's tax rules."
            )
            assert above_the_table.count(line) == 1
            assert _visible(html).count(line) == 1
            assert "Tax year 2026" not in _visible(html)


class TestTheWordsSayNothingOfAYearOnItsOwnRules:
    """The macros' own contract, rendered directly: every caller guards before it."""

    def test_a_basis_that_substituted_nothing_has_no_note_and_no_sentence(self, app):
        """2026 on 2026's rules: the phrase is the bare year; the note and sentence are empty.

        Every page guards or filters before calling the note and the
        sentence, so no route test reaches their own guard; it is what keeps
        a future caller from printing "Shekel doesn't have 2026's tax rules"
        about a year whose rules the app has.
        """
        own = TaxLawBasis(
            tax_year=2026, federal_year=2026, state_year=2026, fica_year=2026,
        )
        template = app.jinja_env.from_string(
            '{% from "_tax_rules_macros.html" import rules_year_phrase, '
            'rules_note, rules_sentence %}'
            "[{{ rules_year_phrase(basis, 'NC') }}]"
            "[{{ rules_note(basis) }}]"
            "[{{ rules_sentence(basis, 'NC') }}]"
        )

        assert template.render(basis=own) == "[2026][][]"
