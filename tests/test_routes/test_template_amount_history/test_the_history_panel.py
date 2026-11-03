"""Amount-history route tests: the panel that shows a definition's recorded prices on its edit
form, and the control that withdraws a mis-dated entry and refuses one that would move a price.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from tests._test_helpers import create_savings_account
from tests._definition_price import newest_price

from ._helpers import _template_with_history, _transfer_template


# ── Helpers ──────────────────────────────────────────────────────────


class _FormNesting(HTMLParser):
    """Track the deepest ``<form>`` nesting a rendered page reaches."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.deepest = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.depth += 1
            self.deepest = max(self.deepest, self.depth)

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


def _max_form_nesting(html: str) -> int:
    """Return the deepest ``<form>`` nesting in *html* (1 = never nested)."""
    parser = _FormNesting()
    parser.feed(html)
    return parser.deepest


# ── The panel ────────────────────────────────────────────────────────


class TestHistoryPanel:
    """What was recorded is visible on the form that recorded it."""

    def test_the_edit_form_renders_the_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Each recorded price and its date appear, newest first.

        A stored fact nobody can see is a fact nobody can find wrong, and until
        plan step X-au-e prices rows from this series a mistake would surface
        only once it started moving money.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 6, 1), "178.32"),
                (date(2026, 9, 1), "165.30"),
            ])

            resp = auth_client.get(f"/templates/{template.id}/edit")
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Amount history" in body
            assert "Amount effective from" in body
            # Each Remove control is its own POST form carrying a CSRF token,
            # and the panel sits OUTSIDE the edit form -- nested forms are not
            # valid HTML and the browser would drop the inner one.
            assert 'name="csrf_token"' in body
            assert _max_form_nesting(body) == 1
            for figure in ("$178.00", "$178.32", "$165.30"):
                assert figure in body
            assert body.index("$165.30") < body.index("$178.00")

    def test_a_definition_with_no_history_renders_no_panel(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A salary-linked template has no stated price, so there is none to show."""
        with app.app_context():
            from tests._test_helpers import make_salary_profile
            expense = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense.id,
                name="Data Manager",
            )
            db.session.add(template)
            db.session.flush()
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()

            resp = auth_client.get(f"/templates/{template.id}/edit")
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Amount history" not in body


# ── The withdrawal control ───────────────────────────────────────────


class TestWithdrawAmountVersion:
    """Removing a mis-dated entry, and the one the route refuses."""

    def test_withdrawing_an_interior_entry(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An interior entry that MOVES a price is refused through the route.

        **This case asserted the removal until plan step balance:X-au-e**, on
        the stated ground that "the newest price the series states is unchanged
        and nothing generation writes moves". The first half is still true and
        the second is not: generation writes no figure now, so what June's
        window prices is read from this series live, and withdrawing the June
        entry re-prices it.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 6, 1), "178.32"),
                (date(2020, 9, 1), "165.30"),
            ])
            tid = template.id
            june_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{june_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert len(tas.amount_versions(template)) == 3
            assert tas.amount_as_of(template, date(2020, 7, 1)) == Decimal("178.32")

    def test_withdrawing_a_REDUNDANT_interior_entry_still_works(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The DOCUMENTED repair path, driven end to end.

        The other half of the rule at the ROUTE tier, and the case that shows
        the refusal above did not swallow the feature: an entry mis-dated to
        June is repaired by stating the same amount at the date it should have
        had, which APPENDS, and the June entry is then withdrawable because
        its new predecessor states the same figure -- the series answers
        identically without it.

        **A first draft of this case seeded ``[Apr 178.00, Jun 178.00, Sep
        165.30]`` directly and found only two versions**: ``set_amount``
        refuses to write a restatement that changes no answer, so a redundant
        entry cannot be created through the write door at all. Building the
        shape the repair actually produces is what makes this a test of the
        repair rather than of a state the app cannot reach.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 6, 1), "178.32"),
                (date(2020, 9, 1), "165.30"),
            ])
            # The repair: the June rise actually happened in May.
            tas.set_amount(
                template, Decimal("178.32"), effective_on=date(2020, 5, 1),
            )
            db.session.commit()
            assert len(tas.amount_versions(template)) == 4
            tid = template.id
            # BY DATE, not by index: the repair above inserted a version
            # ahead of it, and an index would silently select the new May
            # entry -- whose predecessor states a different amount, so the
            # case would assert the refusal while claiming to test the repair.
            june_id = next(
                v.id for v in tas.amount_versions(template)
                if v.effective_date == date(2020, 6, 1)
            )

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{june_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Amount history entry removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert len(tas.amount_versions(template)) == 3
            # Every date answers what it did before the withdrawal, which is
            # the whole licence for allowing it.
            assert tas.amount_as_of(template, date(2020, 7, 1)) == Decimal("178.32")
            assert tas.amount_as_of(template, date(2020, 4, 15)) == Decimal("178.00")
            assert newest_price(template) == Decimal("165.30")

    def test_withdrawing_the_earliest_is_refused_with_the_repair(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every date before the series is priced from it, so it stays.

        The refusal names the repair -- record the amount at the date you want
        first -- rather than only stating the rule.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ])
            earliest_id = tas.amount_versions(template)[0].id

            resp = auth_client.post(
                f"/templates/{template.id}/amount-versions/{earliest_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data
            assert b"record the amount at the date you want first" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert len(tas.amount_versions(template)) == 2

    def test_a_withdrawal_that_would_move_the_price_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """This door corrects the RECORD; it never moves what is budgeted.

        It runs no regeneration and offers no conflict chooser, so a withdrawal
        that changed a price would leave the rows already generated
        disagreeing with the definition, silently.  Refusing keeps the panel a
        safe surface and sends a real price change to the Amount field.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 1, 1), "178.00"),
                (date(2020, 6, 1), "165.30"),
            ])
            tid = template.id
            newest_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{newest_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert newest_price(template) == Decimal("165.30")
            assert len(tas.amount_versions(template)) == 2

    def test_another_users_template_is_404(
        self, app, second_auth_client, seed_user, seed_second_user,
        seed_periods_today,
    ):
        """The security response rule: not-yours and not-found are one answer."""
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ])
            version_id = tas.amount_versions(template)[1].id

            resp = second_auth_client.post(
                f"/templates/{template.id}/amount-versions/{version_id}/delete",
            )
            assert resp.status_code == 404

    def test_a_version_id_from_another_template_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Your own template, someone else's entry id: the same refusal.

        The service looks the id up inside THIS template's collection, so a
        version belonging elsewhere is indistinguishable from one that does not
        exist -- no existence oracle either way.
        """
        with app.app_context():
            mine = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 9, 1), "165.30"),
            ])
            other = _template_with_history(seed_user, [
                (date(2020, 4, 1), "18.14"),
                (date(2020, 9, 1), "19.99"),
            ], name="Apple Music")
            mine_id = mine.id
            foreign_id = tas.amount_versions(other)[1].id

            resp = auth_client.post(
                f"/templates/{mine_id}/amount-versions/{foreign_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            assert len(tas.amount_versions(
                db.session.get(TransactionTemplate, mine_id),
            )) == 2

    def test_a_transfer_entry_is_withdrawn_through_its_own_route(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form's control drives the same shared action."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")
            tas.set_amount(
                template, Decimal("250.00"), effective_on=date(2020, 5, 21),
            )
            tas.set_amount(
                template, Decimal("300.00"), effective_on=date(2020, 6, 4),
            )
            db.session.commit()
            tid = template.id
            # The MIDDLE entry, restated at the SAME figure its predecessor
            # states: since plan step balance:X-au-e a withdrawal is legal only
            # when the series answers identically without it, and this route
            # shares that one rule with the transaction side.
            tas.set_amount(
                template, Decimal("500.00"), effective_on=date(2020, 5, 21),
            )
            db.session.commit()
            middle_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/transfers/{tid}/amount-versions/{middle_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Amount history entry removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransferTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 9), Decimal("500.00")),
                (date(2020, 6, 4), Decimal("300.00")),
            ]
            assert newest_price(template) == Decimal("300.00")
