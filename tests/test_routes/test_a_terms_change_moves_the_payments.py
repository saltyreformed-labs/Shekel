"""
Shekel Budget App -- a change of a card's terms moves its unpaid payments

Plan step **credit_card:CC-6**, its task plan#2675 (ruling **plan#2671**: "When
you change the card's terms, the unpaid payments move to the new dates.  If you
had typed over one of them, the app asks you first.").  Pinned at both terms
doors, through what the pages post:

* a correction, and a new version dated before a statement's close, move the
  unpaid payments; one dated after that close leaves that statement's payment;
* a paid payment never moves;
* a typed-over or deleted payment on a moved date is asked about first, and
  asking saves nothing: the terms and every row stand;
* one on a date that did not move is not asked about, and one left on an old
  date by an earlier answer is not asked about again;
* "move" drops the typed figure and leaves one payment, on the new date;
  "keep" leaves the row and adds the new date's;
* a deleted payment is offered no decision, and a crafted one is ignored;
* a row holding a note is retained and reported;
* only an active definition repeating on this card's due dates moves;
* a save moving no date runs no pass.

**The calendar and the card** are ``test_due_date_repeat_read_pass``'s: ten
biweekly paychecks from Fri 2026-01-02 (P1 Jan 16-29, P3 Feb 13-26, P4 Feb
27-Mar 12, P5 Mar 13-26, P6 Mar 27-Apr 9, P7 Apr 10-23, P8 Apr 24-May 7, P9 May
8-21), and a card closing on the 5th, due on the 27th from Dec 1 2025.  Today is
held at Jan 2, so every paycheck is in the pass's window.  A due day of 20 falls
on Jan 20, Feb 20, Mar 20, Apr 20 and May 20.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from app import ref_cache
from app.enums import RecurrenceUnitEnum, StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.amount_ownership import AmountOwnership
from app.models.credit_card_params import CreditCardParams
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence, transfer_service
from app.services.balance_at import BalanceContext
from app.services.card_statement import DueDateSchedule
from app.services.card_terms import load_card_term_versions, set_terms
from app.services.cash_ledger import resolve_transfer_amount
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from tests._test_helpers import state_template_price
from tests.test_routes.test_card_payment_doors import _flashes
from tests.test_services.test_due_date_repeat_read_pass import (
    _DUE_ON_27,
    _TERMS_FROM,
    _card,
    _dormant_card,
    _payment,
    _terms,
)

#: The form's every control, due on the 20th from the first version's own day.
_DUE_ON_20_FORM = {
    "effective_date": _TERMS_FROM.isoformat(),
    "statement_close_day": "5",
    "payment_due_day": "20",
    "min_payment_percent": "2.00",
    "min_payment_floor": "25.00",
    "cashback_rate": "1.50",
    "auto_redeem_threshold": "",
    "credit_limit": "",
}

#: The dates a due day of 20 falls on inside the saved horizon.
_DUE_ON_20 = [
    date(2026, 1, 20), date(2026, 2, 20), date(2026, 3, 20),
    date(2026, 4, 20), date(2026, 5, 20),
]


@pytest.fixture(autouse=True)
def _today_is_the_first_payday(monkeypatch):
    """Hold the pass's window open at Jan 2, ahead of every row here."""
    monkeypatch.setattr(
        "app.routes.card._payments_follow.display_today",
        lambda: date(2026, 1, 2),
    )


def _generated(seed_user, template):
    """Generate *template*'s rows for the whole horizon and commit."""
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
        seed_user["scenario"].id,
    )
    db.session.commit()


def _card_with_payment(seed_user, name="Pay the Visa"):
    """A card and one due-date payment into it, its rows generated."""
    card = _card(seed_user)
    template = _payment(seed_user, card, name=name)
    _generated(seed_user, template)
    return card, template


def _live_dates(template):
    """The dates of *template*'s rows that are not deleted, ascending."""
    db.session.expire_all()
    return sorted(
        row.occurs_on for row in db.session.query(Transfer).filter_by(
            transfer_template_id=template.id, is_deleted=False,
        )
    )


def _row_on(template, day):
    """*template*'s one row answering *day*."""
    db.session.expire_all()
    return db.session.query(Transfer).filter_by(
        transfer_template_id=template.id, occurs_on=day,
    ).one()


def _typed_over(seed_user, template, day, figure="300.00"):
    """Type *figure* over the payment on *day*, as the grid's edit does; its id."""
    row = _row_on(template, day)
    transfer_service.update_transfer(
        row.id, seed_user["user"].id,
        amount_ownership=AmountOwnership.own(Decimal(figure)), is_override=True,
    )
    db.session.commit()
    return row.id


def _deleted(seed_user, template, day):
    """Delete the payment on *day*, as the grid's delete does; its id."""
    row = _row_on(template, day)
    transfer_service.delete_transfer(row.id, seed_user["user"].id, soft=True)
    db.session.commit()
    return row.id


def _figure(row_id, seed_user):
    """What the row with *row_id* is worth."""
    db.session.expire_all()
    return resolve_transfer_amount(
        db.session.get(Transfer, row_id),
        BalanceContext.build(seed_user["user"].id).amounts(),
    )


def _due_days(card):
    """The card's versions as ``(effective date, due day)``, read fresh."""
    db.session.expire_all()
    return [
        (version.effective_date, version.payment_due_day)
        for version in load_card_term_versions(card.id)
    ]


class _ChooserForm(HTMLParser):
    """What the chooser page's form posts: its hidden inputs and checked radios.

    A browser sends exactly these, so a test posting them cannot pass on a
    payload the page does not emit.
    """

    def __init__(self):
        """Start with nothing read."""
        super().__init__()
        self.action = None
        self.fields = {}
        self.radio_names = set()

    def handle_starttag(self, tag, attrs):
        """Record the form's action, each hidden input and each checked radio."""
        named = dict(attrs)
        if tag == "form" and named.get("method") == "POST" and "action" in named:
            self.action = named["action"]
        if tag != "input":
            return
        if named.get("type") == "radio":
            self.radio_names.add(named["name"])
        if named.get("type") == "hidden" or (
            named.get("type") == "radio" and "checked" in named
        ):
            self.fields[named["name"]] = named.get("value", "")


def _read_chooser(response):
    """Parse the chooser page in *response*."""
    assert response.status_code == 200
    page = _ChooserForm()
    page.feed(response.data.decode())
    return page


def _apply(auth_client, page, decisions=None):
    """Post the chooser's form as the browser would, with *decisions* picked."""
    return auth_client.post(page.action, data={**page.fields, **(decisions or {})})


class TestTheUnpaidPaymentsMove:
    """The ruling's first sentence: the payments follow the terms, with nothing to ask."""

    def test_a_correction_moves_every_unpaid_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Due day 27 corrected to 20 on the same date: every row is on a 20th.

        The second-time trap too: the door read the versions before its write
        and the write goes around the ORM, so a held row would answer with the
        old day and read as no change at all.
        """
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            assert _live_dates(template) == _DUE_ON_27

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == ["Card terms saved."]
            assert _due_days(card) == [(_TERMS_FROM, 20)]
            assert _live_dates(template) == _DUE_ON_20
            moved = _row_on(template, date(2026, 2, 20))
            assert moved.due_date == date(2026, 2, 20)
            assert moved.pay_period_id == seed_periods[3].id
            assert moved.is_override is False

    def test_a_version_this_session_still_holds_does_not_hide_the_correction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A held version is refreshed: the door's write goes around the ORM.

        The session's identity map is weak, so the door's own read is usually
        gone by the time it reads again and only a holder makes this bite (the
        test shares the request's session).  Unrefreshed, the held row answers
        the old due day, the two readings compare equal, and nothing moves.
        """
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            held = load_card_term_versions(card.id)

            auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert held[0].payment_due_day == 20
            assert _live_dates(template) == _DUE_ON_20

    def test_a_version_dated_before_a_close_moves_that_statements_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """From Mar 1: the Mar 5 close falls due Mar 20, and the two before stand."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)

            auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "effective_date": "2026-03-01"},
            )

            assert _live_dates(template) == [
                date(2026, 1, 27), date(2026, 2, 27), date(2026, 3, 20),
                date(2026, 4, 20), date(2026, 5, 20),
            ]

    def test_a_version_dated_after_a_close_leaves_that_statements_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """From Mar 10: the Mar 5 close stands, due Mar 27; April's is the first to move."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)

            auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "effective_date": "2026-03-10"},
            )

            assert _live_dates(template) == [
                date(2026, 1, 27), date(2026, 2, 27), date(2026, 3, 27),
                date(2026, 4, 20), date(2026, 5, 20),
            ]

    def test_a_paid_payment_never_moves(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Jan 27 paid, then the correction: it stays paid on Jan 27, the rest move."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            paid = _row_on(template, date(2026, 1, 27))
            paid_id = paid.id
            assert auth_client.post(
                f"/transfers/instance/{paid_id}/mark-done",
            ).status_code == 200

            auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            db.session.expire_all()
            stays = db.session.get(Transfer, paid_id)
            assert stays.occurs_on == date(2026, 1, 27)
            assert stays.due_date == date(2026, 1, 27)
            assert stays.status_id == ref_cache.status_id(StatusEnum.DONE)
            # No row answers Jan 20, so the pass writes one beside the paid
            # row: open finding plan#2850, pinned so its fix is seen.
            assert _live_dates(template) == sorted(
                [*_DUE_ON_20, date(2026, 1, 27)],
            )

    def test_removing_a_version_moves_the_payments_back(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The Mar 1 version removed: its statements fall due on the 27th again."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "effective_date": "2026-03-01"},
            )
            march = load_card_term_versions(card.id)[1]

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms/{march.id}/delete",
            )

            assert response.status_code == 302
            assert _due_days(card) == [(_TERMS_FROM, 27)]
            assert _live_dates(template) == _DUE_ON_27


class TestOnlyThePaymentsFollowingTheseDatesMove:
    """Which definitions the doors reach, and when they reach none."""

    def test_a_monthly_transfer_into_the_card_stays_on_its_own_day(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A repeat stating its own day does not follow the card's dates."""
        with app.app_context():
            card, _template = _card_with_payment(seed_user)
            monthly = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=card.id,
                name="Monthly extra",
            )
            db.session.add(monthly)
            db.session.flush()
            state_template_price(monthly, Decimal("50.00"))
            author_rule(
                RecurrenceSpec(
                    user_id=seed_user["user"].id,
                    unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2026, 2, 3),
                ),
                calendar_for(monthly.user_id),
                monthly,
            )
            db.session.flush()
            _generated(seed_user, monthly)
            before = _live_dates(monthly)
            assert before

            auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert _live_dates(monthly) == before

    def test_another_cards_payment_stays_on_its_cards_dates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The Visa's change of terms reaches no payment into the Amex."""
        with app.app_context():
            visa, _template = _card_with_payment(seed_user)
            amex = _card(seed_user, name="Amex")
            into_amex = _payment(seed_user, amex, name="Pay the Amex")
            _generated(seed_user, into_amex)

            auth_client.post(
                f"/accounts/{visa.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert _live_dates(into_amex) == _DUE_ON_27

    def test_an_archived_payment_is_not_regenerated(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An archived definition recurs nowhere, and a pass would retire its rows.

        Its rows stay exactly as the archive left them; its unarchive runs a
        pass of its own, on the terms of that day.
        """
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            assert auth_client.post(
                f"/transfers/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()
            rows = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            as_found = sorted((r.id, r.occurs_on, r.is_deleted) for r in rows)
            assert as_found

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert response.status_code == 302
            assert _due_days(card) == [(_TERMS_FROM, 20)]
            db.session.expire_all()
            assert sorted(
                (r.id, r.occurs_on, r.is_deleted)
                for r in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                )
            ) == as_found

    def test_a_save_that_moves_no_date_runs_no_pass(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """A new credit limit: nothing regenerated, nothing asked, the terms saved."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            _typed_over(seed_user, template, date(2026, 2, 27))

            def no_pass(*_args, **_kwargs):
                raise AssertionError("a save moving no date ran the maintain pass")

            monkeypatch.setattr(
                "app.routes.card._payments_follow._maintained", no_pass,
            )
            response = auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={
                    **_DUE_ON_20_FORM, "payment_due_day": "27",
                    "credit_limit": "9000.00",
                },
            )

            assert response.status_code == 302
            db.session.expire_all()
            version = db.session.query(CreditCardParams).filter_by(
                account_id=card.id,
            ).one()
            assert version.credit_limit == Decimal("9000.00")
            assert _live_dates(template) == _DUE_ON_27

    def test_an_owner_with_no_baseline_scenario_still_saves(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No baseline: no pass can run, and the terms are saved as ever."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert response.status_code == 302
            assert _due_days(card) == [(_TERMS_FROM, 20)]
            assert _live_dates(template) == _DUE_ON_27

    def test_the_first_terms_of_a_dormant_card_ask_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No terms, so no repeat could follow them: the save only saves."""
        with app.app_context():
            dormant = _dormant_card(seed_user)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{dormant.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert response.status_code == 302
            assert _due_days(dormant) == [(_TERMS_FROM, 20)]


class TestATypedOverPaymentIsAskedAboutFirst:
    """The ruling's second sentence."""

    def test_asking_saves_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The first post asks: the terms and every row stand as they were."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            page = _read_chooser(response)
            text = response.data.decode()
            assert "Some payments you changed are on dates that are moving" in text
            assert "You changed the terms of Visa from Dec 1, 2025." in text
            assert "Pay the Visa" in text
            assert "$300.00" in text
            assert "Move to the new due date" in text
            assert "Follow the template" not in text
            assert page.action == f"/accounts/{card.id}/card/terms"
            assert f"/accounts/{card.id}" in text
            assert page.radio_names == {f"conflict_decision_{typed}"}
            assert _due_days(card) == [(_TERMS_FROM, 27)]
            assert _live_dates(template) == _DUE_ON_27
            assert _figure(typed, seed_user) == Decimal("300.00")

    def test_move_drops_the_figure_and_leaves_one_payment_on_the_new_date(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The row leaves Feb 27; Feb 20 is the one February payment, at $200.00.

        It takes the second pass: the first ran while the row was typed over,
        and handing it back alone leaves both Feb 27 and Feb 20 on the grid.
        """
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))
            page = _read_chooser(auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            ))

            response = _apply(
                auth_client, page, {f"conflict_decision_{typed}": "use"},
            )

            assert response.status_code == 302
            assert _due_days(card) == [(_TERMS_FROM, 20)]
            assert _live_dates(template) == _DUE_ON_20
            assert db.session.get(Transfer, typed) is None
            assert _figure(
                _row_on(template, date(2026, 2, 20)).id, seed_user,
            ) == Decimal("200.00")

    def test_keep_leaves_the_row_and_the_new_date_gets_its_own(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Keep is the page's default: $300.00 stays on Feb 27, beside Feb 20's $200.00."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))
            page = _read_chooser(auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            ))

            assert _apply(auth_client, page).status_code == 302

            assert _due_days(card) == [(_TERMS_FROM, 20)]
            assert _live_dates(template) == sorted(
                [*_DUE_ON_20, date(2026, 2, 27)],
            )
            kept = db.session.get(Transfer, typed)
            assert kept.is_override is True
            assert _figure(typed, seed_user) == Decimal("300.00")

    def test_a_typed_over_payment_on_a_date_that_did_not_move_is_not_asked_about(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """From Mar 1: Feb 27 is still a due date, so nothing asks, and it stands."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "effective_date": "2026-03-01"},
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == ["Card terms saved."]
            assert date(2026, 3, 20) in _live_dates(template)
            assert _figure(typed, seed_user) == Decimal("300.00")

    def test_a_row_kept_on_an_old_date_is_not_asked_about_again(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The second change: Feb 27 was no due date before it, so nothing it moved.

        Asked of the new dates alone, this row would stop every later change
        of terms for as long as it stood.
        """
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))
            page = _read_chooser(auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            ))
            _apply(auth_client, page)

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "payment_due_day": "22"},
            )

            assert response.status_code == 302
            assert _due_days(card) == [(_TERMS_FROM, 22)]
            assert _figure(typed, seed_user) == Decimal("300.00")
            assert date(2026, 2, 22) in _live_dates(template)
            assert date(2026, 2, 20) not in _live_dates(template)

    def test_the_remove_door_asks_too_and_its_apply_removes(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A version removed moves Apr 20 back to Apr 27: asked, then applied."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "effective_date": "2026-03-01"},
            )
            typed = _typed_over(seed_user, template, date(2026, 4, 20))
            march = load_card_term_versions(card.id)[1]
            march_id = march.id

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms/{march_id}/delete",
            )

            page = _read_chooser(response)
            assert "You removed a set of terms from Visa." in response.data.decode()
            assert page.action == f"/accounts/{card.id}/card/terms/{march_id}/delete"
            assert len(_due_days(card)) == 2

            assert _apply(
                auth_client, page, {f"conflict_decision_{typed}": "use"},
            ).status_code == 302

            assert _due_days(card) == [(_TERMS_FROM, 27)]
            assert _live_dates(template) == _DUE_ON_27
            assert db.session.get(Transfer, typed) is None

    def test_one_page_asks_about_every_payment_into_the_card(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two payments into one card: each named, each answered on its own."""
        with app.app_context():
            card, first = _card_with_payment(seed_user)
            second = _payment(seed_user, card, name="Second payment")
            _generated(seed_user, second)
            moves = _typed_over(seed_user, first, date(2026, 2, 27))
            stays = _typed_over(seed_user, second, date(2026, 3, 27), "111.00")

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            page = _read_chooser(response)
            text = response.data.decode()
            assert "Pay the Visa" in text
            assert "Second payment" in text
            assert page.radio_names == {
                f"conflict_decision_{moves}", f"conflict_decision_{stays}",
            }

            _apply(auth_client, page, {f"conflict_decision_{moves}": "use"})

            assert _live_dates(first) == _DUE_ON_20
            assert _live_dates(second) == sorted(
                [*_DUE_ON_20, date(2026, 3, 27)],
            )
            assert _figure(stays, seed_user) == Decimal("111.00")


class TestADeletedPaymentIsNamedAndOfferedNoDecision:
    """Its date moved, so the new date gets a payment whichever way it is answered."""

    def test_it_is_named_first_with_no_buttons(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A page of only deleted rows: no radio, no bulk buttons, and nothing saved."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            _deleted(seed_user, template, date(2026, 2, 27))

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            page = _read_chooser(response)
            text = response.data.decode()
            assert "Stays deleted" in text
            assert "A payment you deleted stays deleted" in text
            assert page.radio_names == set()
            assert "data-conflict-bulk" not in text
            assert "for the amount you typed" not in text
            assert _due_days(card) == [(_TERMS_FROM, 27)]

    def test_apply_leaves_it_deleted_and_the_new_date_gets_a_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """It stays deleted on Feb 27; Feb 20 gets the payment the page said it would."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            gone = _deleted(seed_user, template, date(2026, 2, 27))
            page = _read_chooser(auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            ))

            assert _apply(auth_client, page).status_code == 302

            assert _live_dates(template) == _DUE_ON_20
            assert db.session.get(Transfer, gone).is_deleted is True

    def test_a_crafted_decision_cannot_restore_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The page offers no decision on it, and a post naming one is ignored."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            gone = _deleted(seed_user, template, date(2026, 2, 27))
            page = _read_chooser(auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            ))

            _apply(auth_client, page, {f"conflict_decision_{gone}": "use"})

            assert db.session.get(Transfer, gone).is_deleted is True

    def test_a_payment_typed_over_and_then_deleted_reads_as_deleted(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The pass holds it as typed over; to the owner it is a deleted payment."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))
            _deleted(seed_user, template, date(2026, 2, 27))

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            page = _read_chooser(response)
            assert "Stays deleted" in response.data.decode()
            assert page.radio_names == set()
            _apply(auth_client, page)
            assert db.session.get(Transfer, typed).is_deleted is True

    def test_a_deleted_payment_on_a_date_that_did_not_move_is_not_named(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """From Mar 1: Feb 27 is still a due date, its deletion holds, nothing asks."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            gone = _deleted(seed_user, template, date(2026, 2, 27))

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms",
                data={**_DUE_ON_20_FORM, "effective_date": "2026-03-01"},
            )

            assert response.status_code == 302
            assert db.session.get(Transfer, gone).is_deleted is True
            assert date(2026, 2, 27) not in _live_dates(template)


class TestWhatTheDoorsDoNotMove:
    """The owner's records, and a refusal from the transfer service."""

    def test_a_payment_holding_a_note_stays_and_is_reported(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A note is retained on its date, and the new date still gets a row."""
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            row = _row_on(template, date(2026, 2, 27))
            noted = row.id
            transfer_service.update_transfer(
                noted, seed_user["user"].id, notes="Confirmed with the bank",
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert response.status_code == 302
            flashes = _flashes(auth_client)
            assert any("purchases or notes recorded" in f for f in flashes)
            assert "Card terms saved." in flashes
            assert db.session.get(Transfer, noted).occurs_on == date(2026, 2, 27)
            assert date(2026, 2, 20) in _live_dates(template)

    def test_a_refused_row_rolls_the_change_back_whole(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The paying account became a card: the new rows are refused, in words.

        The account-type door has no refusal for the SOURCE of a due-date
        repeat, so a definition can stand whose new rows the transfer service
        will not write.  The terms stay as they were, and so do the rows.
        """
        with app.app_context():
            card, template = _card_with_payment(seed_user)
            source = db.session.get(Account, template.from_account_id)
            source.account_type_id = db.session.query(AccountType).filter_by(
                name="Credit Card",
            ).one().id
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            )

            assert response.status_code == 302
            flashes = _flashes(auth_client)
            assert len(flashes) == 1
            assert flashes[0].startswith("Could not move this card's payments: ")
            assert _due_days(card) == [(_TERMS_FROM, 27)]
            assert _live_dates(template) == _DUE_ON_27


class TestTheScheduleSaysWhenAStatementFallsDue:
    """``DueDateSchedule.falls_due_on``, the question a moved date asks."""

    def test_a_due_date_and_the_day_beside_it(self, app, seed_user):
        """Under close 5, due 27: Feb 27 is a due date, Feb 26 and Feb 28 are not."""
        with app.app_context():
            card = _card(seed_user)
            schedule = DueDateSchedule.of(load_card_term_versions(card.id))

            assert schedule.falls_due_on(date(2026, 2, 27)) is True
            assert schedule.falls_due_on(date(2026, 2, 26)) is False
            assert schedule.falls_due_on(date(2026, 2, 28)) is False

    def test_a_short_transitional_cycle_is_asked_in_date_order(self, app, seed_user):
        """Close 20 due 19, then close 5 due 6 from Mar 1: Mar 6 falls before Mar 19."""
        with app.app_context():
            card = _card(seed_user, close_day=20, due_day=19)
            set_terms(card.id, card.user_id, date(2026, 3, 1), _terms(5, 6))
            schedule = DueDateSchedule.of(load_card_term_versions(card.id))

            assert schedule.falls_due_on(date(2026, 3, 6)) is True
            assert schedule.falls_due_on(date(2026, 3, 19)) is True
            assert schedule.falls_due_on(date(2026, 3, 7)) is False

