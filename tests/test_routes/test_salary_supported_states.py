"""The supported states (plan step salary:X-at-3; ruling salary:R-SAL78).

The tax law lists every state the app supports, a state with no income tax as
an explicit ``$0.00`` entry; the profile form offers only those, both profile
doors refuse any other, and the paycheck engine refuses a profile in any
other rather than pricing it.  Finding **salary:SAL-575**, re-measured on the
base tree before this step with the made-up law below: both doors saved
``ZZ`` and lower-case ``nc`` as typed, the formulas priced each at ``$0.00``
state tax against NC's ``$150.00``, a paycheck a stub priced carried the
stub's printed ``$145.00`` unmoved by a ``$100.00`` raise (NC: ``$150.00``),
and the Taxes tab priced ``$0.00`` state liability and read "ZZ · no state
income tax priced".  A profile written in such a state around the doors
now meets one page naming it and linking its edit page, wherever it is
priced (the developer, 2026-10-08, "One fix-it page").

**The made-up law** (every figure made up): one year, 2026, federal and FICA
at ``$0.00``; North Carolina at a flat 5% with no standard deduction, so the
formulas' state line on a ``$3,000.00`` paycheck is ``3,000.00 x 5% =
150.00`` exactly; and ``ZZ``, a made-up state with no income tax, the
explicit ``$0.00`` entry.  ``QQ`` is listed by no law, and ``nc`` is North
Carolina typed in lower case, which the law does not list either.
"""

from datetime import date
from decimal import Decimal

import pytest
from flask_login import login_user

from app import ref_cache
from app.enums import StatusEnum, WithholdingKindEnum
from app.exceptions import UnsupportedStateError
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.services.balance_at import BalanceContext
from app.services.obligations_aggregator import todays_price
from app.services.salary_paydays import paycheck_on
from app.services.tax_report_service import compute_tax_report
from app.tax_law import TaxLaw
from tests._stub_period import stub_period_on
from tests._test_helpers import (
    add_test_pay_stub,
    freeze_today,
    made_up_state,
    made_up_year,
    make_salary_profile,
    no_income_tax_state,
)

#: The refusal's flash on both doors (the route's one message for a payload
#: its schema refuses).
_REFUSED = "Please correct the highlighted errors and try again."


def _law():
    """2026 alone: $0.00 federal and FICA, NC 5% flat with no deduction, ZZ untaxed."""
    return TaxLaw(years=(
        made_up_year(2026, states={
            "NC": made_up_state(Decimal("0.0500"), Decimal("0.00")),
            "ZZ": no_income_tax_state(),
        }),
    ))


def _profile_form(state_code):
    """Every control the profile form renders, at its rendered defaults, naming *state_code*."""
    single = db.session.query(FilingStatus).filter_by(name="single").one()
    return {
        "name": "Day Job",
        "filing_status_id": str(single.id),
        "state_code": state_code,
        "stub_gross_includes_after_tax": "false",
        "qualifying_children": "0",
        "other_dependents": "0",
        "additional_income": "0",
        "additional_deductions": "0",
        "extra_withholding": "0",
    }


def _create_form(state_code):
    """The NEW profile form: the profile's controls plus its first pay entry."""
    return {
        **_profile_form(state_code), "pay_amount": "3000.00", "pay_payday": "2026-01-02",
        **stub_period_on("2026-01-02"),
    }


def _update_form(profile, state_code):
    """The EDIT form: the profile's controls plus its version pin."""
    return {**_profile_form(state_code), "version_id": str(profile.version_id)}


def _priced(profile, day):
    """The paycheck the read pass's pricer prices *profile*'s paycheck on *day* at."""
    ctx = BalanceContext.build(profile.user_id)
    period = paycheck_on(ctx.calendar(), day)
    assert period is not None, f"{day} is not one of the owner's paydays"
    return ctx.paychecks().for_profile(profile).at(period)


def _todays_figure(profile):
    """Today's paycheck for *profile*, the figure the Recurring and savings pages show."""
    return todays_price(profile.template, BalanceContext.build(profile.user_id)).amount


def _stored_profiles(user_id):
    """The owner's profiles as the DATABASE holds them, after the session is expired."""
    db.session.expire_all()
    return db.session.query(SalaryProfile).filter_by(user_id=user_id).all()


def _state_options(html):
    """The ``state_code`` select's option values in page order, and the one marked selected."""
    start = html.index('<select id="state_code"')
    block = html[start:html.index("</select>", start)]
    values, selected = [], []
    for option in block.split("<option")[1:]:
        value = option.split('value="', 1)[1].split('"', 1)[0]
        values.append(value)
        if " selected" in option.split(">", 1)[0]:
            selected.append(value)
    return values, selected


@pytest.fixture(name="law")
def _install_law(tax_law):
    """Install the module docstring's made-up law."""
    return tax_law(_law())


@pytest.fixture(autouse=True)
def _today_is_a_seeded_paycheck(monkeypatch):
    """Today is 2026-03-20, inside the paycheck of 03-13: ``seed_periods`` holds it.

    The salary pages and today's paycheck
    (``obligations_aggregator.todays_price``) price the paycheck holding
    TODAY, so a today past the ten seeded paydays prices nothing and every
    refusal below would pass by never being reached.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


class TestTheCreateDoor:
    """POST /salary saves a state the law lists and refuses any other."""

    def test_a_listed_state_is_saved(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """The control: NC is saved, and the template is born at its net, $3,000.00 - $150.00."""
        with app.app_context():
            response = auth_client.post("/salary", data=_create_form("NC"))

            assert response.status_code == 302
            (saved,) = _stored_profiles(seed_user["user"].id)
            assert saved.state_code == "NC"
            # 3,000.00 gross, 150.00 NC (5%), $0.00 federal and FICA.
            assert _todays_figure(saved) == Decimal("2850.00")

    @pytest.mark.parametrize("state_code", ["QQ", "nc"])
    def test_a_state_the_law_does_not_list_is_refused(
        self, app, auth_client, seed_user, seed_periods, law, state_code,
    ):  # pylint: disable=unused-argument
        """QQ and lower-case nc: the form comes back refused, and no profile or template exists."""
        with app.app_context():
            response = auth_client.post(
                "/salary", data=_create_form(state_code), follow_redirects=True,
            )

            assert response.status_code == 200
            assert _REFUSED in response.data.decode()
            assert not _stored_profiles(seed_user["user"].id)
            assert not db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).all()


class TestTheUpdateDoor:
    """POST /salary/<id> saves a listed state and refuses any other.

    Every profile here is created through the create door, so it has a
    template, which states no price (plan step X-bp).  What an update moves
    is today's paycheck, priced live under the state it saved
    (:func:`_todays_figure`); the update door's regeneration re-stated the
    template's ``default_amount`` at it until plan step X-bp, and these cases
    read that copy until then.
    """

    @staticmethod
    def _created(auth_client, seed_user):
        """An NC profile created through the create door: $3,000.00 gross, $2,850.00 net."""
        assert auth_client.post("/salary", data=_create_form("NC")).status_code == 302
        (profile,) = _stored_profiles(seed_user["user"].id)
        assert _todays_figure(profile) == Decimal("2850.00")
        return profile

    def test_moving_to_the_untaxed_state_is_saved_and_reprices_at_zero(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """NC to ZZ, the explicit $0.00 entry: saved, and re-priced to $3,000.00 net."""
        with app.app_context():
            profile = self._created(auth_client, seed_user)

            response = auth_client.post(
                f"/salary/{profile.id}", data=_update_form(profile, "ZZ"),
            )

            assert response.status_code == 302
            (stored,) = _stored_profiles(seed_user["user"].id)
            assert stored.state_code == "ZZ"
            # 3,000.00 gross, ZZ withholds $0.00, federal and FICA $0.00.
            assert _todays_figure(stored) == Decimal("3000.00")

    @pytest.mark.parametrize("state_code", ["QQ", "nc"])
    def test_a_state_the_law_does_not_list_is_refused(
        self, app, auth_client, seed_user, seed_periods, law, state_code,
    ):  # pylint: disable=unused-argument
        """QQ and nc: refused; the database still holds NC, its version and $2,850.00 net."""
        with app.app_context():
            profile = self._created(auth_client, seed_user)
            form = _update_form(profile, state_code)

            response = auth_client.post(
                f"/salary/{profile.id}", data=form, follow_redirects=True,
            )

            assert response.status_code == 200
            assert _REFUSED in response.data.decode()
            (stored,) = _stored_profiles(seed_user["user"].id)
            assert stored.state_code == "NC"
            assert stored.version_id == int(form["version_id"])
            assert _todays_figure(stored) == Decimal("2850.00")

    def test_an_unlisted_stored_state_is_fixed_on_the_edit_page(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """The repair the refusal names: a QQ row, written around the doors, saved as ZZ.

        Its edit page opens (it prices nothing), and today's paycheck is
        priced under the state the update door saves: ZZ's $3,000.00 net.
        """
        with app.app_context():
            profile = self._created(auth_client, seed_user)
            profile.state_code = "QQ"
            db.session.commit()

            assert auth_client.get(f"/salary/{profile.id}/edit").status_code == 200
            response = auth_client.post(
                f"/salary/{profile.id}", data=_update_form(profile, "ZZ"),
            )

            assert response.status_code == 302
            (stored,) = _stored_profiles(seed_user["user"].id)
            assert stored.state_code == "ZZ"
            assert _todays_figure(stored) == Decimal("3000.00")

    def test_an_edit_that_keeps_an_unlisted_state_saves_and_pricing_still_refuses(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """A QQ row's rename, posted without a state, is saved; every page that prices refuses.

        The form cannot submit without a state; a POST that leaves it out keeps
        QQ.  **The rename was refused and rolled back until plan step X-bp**,
        by the update door's regeneration, which priced today's paycheck to
        re-state the template's copy of it and met the engine's refusal.  X-bp
        deleted the re-statement (an edge case decided under R-BAL207): a
        write that prices nothing refuses nothing, so the rename is saved (the
        edit page it lands on prices nothing), and the refusal stays where the
        money is -- the next page that prices the profile answers the fix-it
        page.
        """
        with app.app_context():
            profile = self._created(auth_client, seed_user)
            profile.state_code = "QQ"
            db.session.commit()
            form = _update_form(profile, "QQ")
            del form["state_code"]
            form["name"] = "Night Job"

            response = auth_client.post(f"/salary/{profile.id}", data=form)

            assert response.status_code == 302
            (stored,) = _stored_profiles(seed_user["user"].id)
            assert (stored.name, stored.state_code) == ("Night Job", "QQ")
            priced_page = auth_client.get("/salary")
            assert priced_page.status_code == 200
            assert "State Not Supported" in priced_page.data.decode()


class TestTheForm:
    """The profile form offers the law's states and chooses none for its owner."""

    def test_a_new_profile_offers_the_listed_states_and_opens_on_the_placeholder(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """Options: the placeholder, then NC and ZZ; only the placeholder is selected."""
        with app.app_context():
            html = auth_client.get("/salary/new").data.decode()

            assert _state_options(html) == (["", "NC", "ZZ"], [""])

    def test_the_edit_page_selects_the_stored_state(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """A ZZ profile's edit page has ZZ selected, and nothing else."""
        with app.app_context():
            profile = make_salary_profile(
                seed_user, db.session, state_code="ZZ", pay_from=date(2026, 1, 2),
            )
            db.session.commit()

            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()

            assert _state_options(html) == (["", "NC", "ZZ"], ["ZZ"])

    def test_an_unlisted_stored_state_opens_on_the_placeholder(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """A QQ row opens on the placeholder: the required select holds the save for a pick."""
        with app.app_context():
            profile = make_salary_profile(
                seed_user, db.session, state_code="QQ", pay_from=date(2026, 1, 2),
            )
            db.session.commit()

            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()

            assert _state_options(html) == (["", "NC", "ZZ"], [""])
            assert (
                '<select id="state_code" name="state_code" class="form-select" required>'
            ) in html


class TestTheEngine:
    """The paycheck engine prices a listed state and refuses any other."""

    @pytest.mark.parametrize(("state_code", "expected"), [
        ("NC", Decimal("150.00")),  # 3,000.00 x 5%
        ("ZZ", Decimal("0.00")),  # the explicit $0.00 entry
    ])
    def test_a_listed_state_is_priced(
        self, app, db, seed_user, seed_periods, law, state_code, expected,
    ):  # pylint: disable=unused-argument
        """NC withholds $150.00 and ZZ, listed with no income tax, $0.00."""
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3000.00"),
            state_code=state_code, pay_from=date(2026, 1, 2),
        )
        db.session.commit()

        assert _priced(profile, date(2026, 2, 13)).taxes.state == expected

    def test_an_unlisted_state_is_refused_by_name(
        self, app, db, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """QQ was priced $0.00; now the refusal names the profile, its state and the law's.

        The law's two states read "NC and ZZ", the one wording of a list of
        states (ruling plan#3202; "NC, ZZ" until then).
        """
        profile = make_salary_profile(
            seed_user, db.session, name="Day Job", pay=Decimal("3000.00"),
            state_code="QQ", pay_from=date(2026, 1, 2),
        )
        db.session.commit()

        with pytest.raises(UnsupportedStateError) as refused:
            _priced(profile, date(2026, 2, 13))

        assert str(refused.value) == (
            f'Salary profile "Day Job" (id {profile.id}) is in \'QQ\', which '
            "Shekel's tax law does not list (it lists NC and ZZ).  Choose a state "
            "the law lists on the profile's edit page."
        )

    def test_lower_case_nc_is_refused(
        self, app, db, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """nc was priced $0.00 state tax while NC priced $150.00: it is not NC to the law."""
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3000.00"),
            state_code="nc", pay_from=date(2026, 1, 2),
        )
        db.session.commit()

        with pytest.raises(UnsupportedStateError, match="is in 'nc'"):
            _priced(profile, date(2026, 2, 13))

    def test_a_stub_priced_paycheck_is_refused_too(
        self, app, db, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """A stub printing $145.00 at $2,900.00 no longer carries $145.00 into a QQ paycheck.

        On the base tree the formulas priced both sides at $0.00, so the
        paycheck withheld ``145.00 + 0.00 - 0.00 = 145.00`` whatever its pay;
        the refusal comes before any stub is read.
        """
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3000.00"),
            state_code="QQ", pay_from=date(2026, 1, 2),
        )
        db.session.flush()
        add_test_pay_stub(profile, date(2026, 1, 16), "2900.00", {
            WithholdingKindEnum.FEDERAL_INCOME: "0.00",
            WithholdingKindEnum.STATE_INCOME: "145.00",
            WithholdingKindEnum.SOCIAL_SECURITY: "0.00",
            WithholdingKindEnum.MEDICARE: "0.00",
        })
        db.session.commit()

        with pytest.raises(UnsupportedStateError, match="is in 'QQ'"):
            _priced(profile, date(2026, 2, 13))


class TestTheTaxesTab:
    """The Taxes tab prices a listed state and refuses any other."""

    def test_an_untaxed_listed_state_prices_no_liability_and_says_so(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """ZZ: $0.00 state liability and withholding, and the card names ZZ as untaxed."""
        with app.app_context():
            make_salary_profile(
                seed_user, db.session, pay=Decimal("3000.00"),
                state_code="ZZ", pay_from=date(2026, 1, 2),
            )
            db.session.commit()

            report = compute_tax_report(seed_user["user"].id, 2026, date(2026, 3, 1))
            html = auth_client.get(
                "/analytics/taxes?year=2026", headers={"HX-Request": "true"},
            ).data.decode()

            assert report.liability.state.liability == Decimal("0")
            assert report.withholding.total.state == Decimal("0.00")
            assert "ZZ &middot; no state income tax priced" in html

    def test_an_unlisted_state_is_refused(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """QQ: the report refuses, and the tab's load answers nothing rather than show $0.00.

        An htmx GET -- this tab's load, or a press of its pill -- is answered
        with a 204 by the shared recovery rule: the first load stays on its
        spinner, and a pressed pill highlights over the previous tab's
        figures (ledger row salary:SAL-600; the developer, 2026-10-08, "Keep
        the rule; file it").
        """
        with app.app_context():
            make_salary_profile(
                seed_user, db.session, pay=Decimal("3000.00"),
                state_code="QQ", pay_from=date(2026, 1, 2),
            )
            db.session.commit()

            with pytest.raises(UnsupportedStateError, match="is in 'QQ'"):
                compute_tax_report(seed_user["user"].id, 2026, date(2026, 3, 1))
            response = auth_client.get(
                "/analytics/taxes?year=2026", headers={"HX-Request": "true"},
            )
            assert (response.status_code, response.data) == (204, b"")


class TestTheFixItPage:
    """One page answers a profile in an unlisted state, wherever it is priced.

    The developer, 2026-10-08, "One fix-it page" (amending ruling R-SAL130's
    generic error page): it names the profile and links its edit page, which
    prices nothing, and the log names the profile.
    """

    @staticmethod
    def _qq_profile(seed_user):
        """A committed profile in QQ, written around the doors, as a row older than a release."""
        profile = make_salary_profile(
            seed_user, db.session, name="Day Job", pay=Decimal("3000.00"),
            state_code="QQ", pay_from=date(2026, 1, 2),
        )
        db.session.commit()
        return profile

    def test_a_page_that_prices_the_profile_answers_the_repair(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """/salary names the profile and its state and links its edit page, which opens.

        Asserted on the CARD, not the status alone: the bare 500 page this
        replaces also renders a page.
        """
        with app.app_context():
            profile = self._qq_profile(seed_user)

            response = auth_client.get("/salary")

            assert response.status_code == 200
            html = response.data.decode()
            words = " ".join(html.split())
            assert "State Not Supported" in html
            assert (
                'Your salary profile "Day Job" is in QQ, which Shekel has no tax '
                "rules for. Choose your state on its edit page."
            ) in words
            edit_url = f"/salary/{profile.id}/edit"
            assert f'href="{edit_url}"' in html
            assert auth_client.get(edit_url).status_code == 200

    def test_the_quiet_page_is_a_loud_log_line(
        self, app, auth_client, seed_user, seed_periods, law, caplog,
    ):  # pylint: disable=unused-argument
        """The page is calm; the ERROR event names the profile, its state and the path."""
        with app.app_context():
            profile = self._qq_profile(seed_user)

            with caplog.at_level("ERROR"):
                auth_client.get("/salary")

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == "salary_state_unsupported"
            ]
            assert events, (
                "the unsupported-state handler answered without its event; "
                f"records seen: {[record.message for record in caplog.records]}"
            )
            assert events[0].levelname == "ERROR"
            assert (events[0].profile_id, events[0].state_code, events[0].path) == (
                profile.id, "QQ", "/salary",
            )

    def test_a_pressed_htmx_post_gets_the_page(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """An htmx POST that prices the QQ profile gets the page, never a silent 204.

        Marking today's paycheck received on the grid settles it at what it
        resolves to, which prices the profile; for a QQ row that refuses, and
        the button the owner pressed answers with the page (the shared rule's
        MUTATING branch), the row not settled.  The page is base.html's whole
        document, which htmx swaps in place of its target: ledger row
        salary:SAL-600.  **This case pressed a raise's Add until plan step
        X-bp**, whose regeneration priced the profile to re-state the
        template's copy of today's paycheck; X-bp deleted the re-statement,
        so a raise is saved without pricing anything, and a settle is the
        mutating htmx request that prices.
        """
        with app.app_context():
            assert auth_client.post("/salary", data=_create_form("NC")).status_code == 302
            (profile,) = _stored_profiles(seed_user["user"].id)
            profile.state_code = "QQ"
            db.session.commit()
            (paycheck,) = [
                row for row in profile.template.transactions
                if row.occurs_on == date(2026, 3, 13)
            ]

            response = auth_client.post(
                f"/transactions/{paycheck.id}/mark-done",
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert "State Not Supported" in response.data.decode()
            db.session.expire_all()
            assert db.session.get(type(paycheck), paycheck.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )

    def test_deleting_the_profile_answers_the_page_and_archives_nothing(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """The delete door freezes each row at its price first, so a QQ profile refuses.

        ``archive_profile`` prices every derived row before the archive, and
        the refusal is not the ``AmountUnresolvable`` it skips: the page
        answers, and the profile and its template are still active once
        re-read (committed state).  The owner picks a listed state, then
        deletes.
        """
        with app.app_context():
            assert auth_client.post("/salary", data=_create_form("NC")).status_code == 302
            (profile,) = _stored_profiles(seed_user["user"].id)
            profile.state_code = "QQ"
            db.session.commit()

            response = auth_client.post(f"/salary/{profile.id}/delete")

            assert response.status_code == 200
            html = response.data.decode()
            assert "State Not Supported" in html
            assert f'href="/salary/{profile.id}/edit"' in html
            (stored,) = _stored_profiles(seed_user["user"].id)
            assert (stored.is_active, stored.template.is_active) == (True, True)
            # No row was frozen at a stated figure: every generated row still
            # derives its amount from the profile.
            assert stored.template.transactions
            assert all(
                row.amount_source_id is not None
                for row in stored.template.transactions
            )

    def test_a_companion_is_told_to_ask_the_owner(
        self, app, db, seed_user, seed_companion,
    ):  # pylint: disable=unused-argument
        """The companion's page names the owner's profile and offers no edit button.

        A companion reaches the page through the companion view, which prices
        the owner's paychecks; the edit page is the owner's, so the companion
        is told to ask the owner (the developer, 2026-10-08, "Companion
        wording").  The handler is reached through Flask's own dispatch.
        """
        refused = UnsupportedStateError(7, "Day Job", "QQ", ("NC", "ZZ"))
        with app.test_request_context("/companion/"):
            login_user(seed_companion["user"])
            html = app.handle_user_exception(refused)

        words = " ".join(html.split())
        assert (
            'The budget owner\'s salary profile "Day Job" is in QQ, which Shekel '
            "has no tax rules for. Ask the owner to choose its state."
        ) in words
        assert "/salary/7/edit" not in html
        assert "Choose your state on its edit page" not in words

    def test_a_listed_state_never_meets_the_page(
        self, app, auth_client, seed_user, seed_periods, law,
    ):  # pylint: disable=unused-argument
        """The negative control: an NC profile's /salary renders normally."""
        with app.app_context():
            make_salary_profile(
                seed_user, db.session, name="Day Job", pay=Decimal("3000.00"),
                pay_from=date(2026, 1, 2),
            )
            db.session.commit()

            response = auth_client.get("/salary")

            assert response.status_code == 200
            assert "State Not Supported" not in response.data.decode()
