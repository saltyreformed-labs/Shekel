"""What the Pay Stub page offers when a stub's base differs (plan step salary:X-av-4).

:func:`~app.services.stub_pay_offer.offer` under ruling R-SAL61 and the three
rulings asked at this step: no button on a paycheck a raise starts inside
(plan#3959), a Fix on a payday that already holds a pay amount (plan#3962),
and otherwise a Record the Record door would take.  :func:`~app.services
.pay_list_service.reach_of` is graded here too, as the paychecks each offer
says it moves.

Every figure is made up.  Today is pinned to 2026-03-20, inside the seeded
schedule (biweekly Fridays 01-02 through 05-08; each paycheck pays Sunday
through the Saturday six days before it, so 03-27 pays 03-08..03-21): 03-27
is the next payday.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import pay_list_service, stub_pay_offer
from app.services.balance_at import BalanceContext
from app.services.pay_list_service import PayReach
from tests._test_helpers import freeze_today, make_recurring_raise, make_salary_profile

_TODAY = date(2026, 3, 20)


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: 03-27 is the next payday."""
    freeze_today(monkeypatch, _TODAY)


def _profile(seed_user, seed_periods):
    """$2,000.00 a paycheck from 2026-01-02, committed in the caller's app context.

    Built inside the test's own context, as the pay list's door suite builds
    it, so every write below lands in the session the offer reads.
    """
    profile = make_salary_profile(
        seed_user, db.session, pay=Decimal("2000.00"),
        pay_from=seed_periods[0].start_date,
    )
    db.session.commit()
    return profile


def _record(profile, amount, payday):
    """Record *amount* from *payday* through the pay list's door, committed."""
    pay_list_service.record_entry(
        profile, BalanceContext.build(profile.user_id), Decimal(amount), payday, _TODAY,
    )
    db.session.commit()


def _offer(profile, payday, base):
    """The page's offer for a stub of *base* on *payday*, in a fresh read pass."""
    return stub_pay_offer.offer(
        profile, BalanceContext.build(profile.user_id), payday, Decimal(base), _TODAY,
    )


class TestNoOffer:
    """The page offers nothing where there is nothing to compare or no gap."""

    def test_a_stub_at_the_apps_figure_offers_nothing(
        self, app, seed_user, seed_periods,
    ):
        """A 03-13 stub of $2,000.00 against the app's $2,000.00: ``None``."""
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            assert _offer(profile, date(2026, 3, 13), "2000.00") is None

    def test_a_day_the_app_holds_no_paycheck_on_offers_nothing(
        self, app, seed_user, seed_periods,
    ):
        """03-14 is not a payday: ``None``, whatever the base."""
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            assert _offer(profile, date(2026, 3, 14), "2010.00") is None


class TestARecord:
    """A payday no amount starts on, and the Record door takes, offers a Record."""

    def test_a_free_payday_offers_its_base_as_a_new_amount(
        self, app, seed_user, seed_periods,
    ):
        """A 03-13 stub of $2,010.00 against $2,000.00: a Record reaching 03-13 on.

        No amount starts on 03-13, so ``entry`` is ``None`` and the old figure
        is what the app pays that payday, $2,000.00; nothing follows, and the
        01-02 amount starts before it.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            offer = _offer(profile, date(2026, 3, 13), "2010.00")
            assert offer.button is True
            assert offer.entry is None
            assert (offer.pays_now, offer.stub_base, offer.old_figure) == (
                Decimal("2000.00"), Decimal("2010.00"), Decimal("2000.00"),
            )
            assert offer.reach == PayReach(date(2026, 3, 13), and_before=False, until=None)

    def test_a_later_amount_bounds_what_it_reaches(
        self, app, seed_user, seed_periods,
    ):
        """With $2,100.00 recorded from 03-27, a 03-13 Record reaches 03-13 up to 03-27."""
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            _record(profile, "2100.00", date(2026, 3, 27))
            offer = _offer(profile, date(2026, 3, 13), "2010.00")
            assert offer.reach == PayReach(
                date(2026, 3, 13), and_before=False, until=date(2026, 3, 27),
            )

    def test_a_payday_the_record_door_refuses_offers_its_refusal(
        self, app, seed_user, seed_periods,
    ):
        """A 04-10 stub on 03-20: the door's own words, and no button.

        04-10 is a payday the app holds, but later than the next one, 03-27,
        so the Record door would refuse it; the page says why rather than
        showing a button that fails.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            offer = _offer(profile, date(2026, 4, 10), "2010.00")
            assert offer.button is False
            assert offer.reason == (
                "2026-04-10 has not been paid yet.  Pay can be recorded up to "
                "your next payday, 2026-03-27."
            )
            assert offer.whole_payday is None


class TestAFix:
    """A payday an amount already starts on offers a Fix of it (ruling plan#3962)."""

    def test_the_first_amounts_payday_offers_to_fix_it(
        self, app, seed_user, seed_periods,
    ):
        """A 01-02 stub of $2,001.00 against the first amount's $2,000.00: a Fix.

        The Record door would refuse 01-02, which the first amount holds; the
        Fix replaces $2,000.00, and as the first amount it reaches every
        paycheck before 01-02 too.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            offer = _offer(profile, date(2026, 1, 2), "2001.00")
            assert offer.button is True
            assert offer.entry.id == profile.pay_entries[0].id
            assert offer.old_figure == Decimal("2000.00")
            assert offer.stub_base == Decimal("2001.00")
            assert offer.reach == PayReach(date(2026, 1, 2), and_before=True, until=None)


#: 5% from Wednesday 2026-03-11, inside 03-27's 03-08..03-21.
_RAISE_DAY = date(2026, 3, 11)


class TestARaiseInside:
    """A paycheck a raise starts inside offers no button (ruling plan#3959)."""

    def test_it_says_why_and_names_the_first_paycheck_at_the_new_pay(
        self, app, seed_user, seed_periods,
    ):
        """A 03-27 stub of $2,080.02: no button, and 04-10 named.

        03-27's weekdays: 03-09 and 03-10 before the raise, eight after.
        2,000.00 x 2/10 = 400.00; 2,100.00 x 8/10 = 1,680.00; the app pays
        2,080.00.  04-10 pays 03-22..04-04, wholly after the raise.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            make_recurring_raise(profile.id, db.session, effective_date=_RAISE_DAY)
            db.session.commit()
            assert _offer(profile, date(2026, 3, 27), "2080.00") is None
            offer = _offer(profile, date(2026, 3, 27), "2080.02")
            assert offer.button is False
            assert offer.reason == (
                "Your Merit +5% raise from 2026-03-11 starts during the pay "
                "period this paycheck covers, so this stub's base pay can't be "
                "used as your pay from this payday: the app would add the raise "
                "to it a second time.  Use your 2026-04-10 stub, the first "
                "paycheck paid entirely at the new pay."
            )
            assert offer.whole_payday == date(2026, 4, 10)

    def test_a_held_payday_a_raise_starts_inside_offers_no_fix(
        self, app, seed_user, seed_periods,
    ):
        """$2,000.00 recorded from 03-27 too: still no button, though a Fix would be allowed.

        The amount from 03-27 opens 03-08 and the raise from 03-11 still
        applies on top of it, so 03-27 pays 2,080.00 again and a Fix to a
        stub's blended base is as wrong as a Record of one.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            make_recurring_raise(profile.id, db.session, effective_date=_RAISE_DAY)
            db.session.commit()
            _record(profile, "2000.00", date(2026, 3, 27))
            offer = _offer(profile, date(2026, 3, 27), "2080.02")
            assert offer.button is False
            assert offer.whole_payday == date(2026, 4, 10)
