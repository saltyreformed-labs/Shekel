"""The pay list's write doors, below the route (plan step salary:X-av-3b).

:func:`~app.services.pay_list_service.record_entry` refuses a payday another
entry holds IN WORDS, before ``uq_pay_entries_profile_payday`` could refuse it
at a flush (a database refusal is a backstop, never the check), and STAGES
the entry it writes, so the flush that can meet that key runs inside the
route's guard.  The route suite cannot grade either: the route reports the
key's refusal in the same words, and a door that flushed would pass every
route test whose payday is free.  So both are graded here, at the door.

Every figure is made up.  Today is pinned to 2026-03-20, inside the seeded
schedule (biweekly Fridays from 2026-01-02): 03-27 the next payday.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.services import pay_list_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import freeze_today, make_salary_profile


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: 03-27 is the next payday."""
    freeze_today(monkeypatch, date(2026, 3, 20))


def _profile(seed_user, seed_periods):
    """$2,000.00 a paycheck from 2026-01-02, committed."""
    profile = make_salary_profile(
        seed_user, db.session, pay=Decimal("2000.00"),
        pay_from=seed_periods[0].start_date,
    )
    db.session.commit()
    return profile


class TestRecordEntry:
    """``record_entry`` refuses in words and stages what it writes."""

    def test_a_held_payday_is_refused_before_any_write(self, app, seed_user, seed_periods):
        """01-02 holds the first entry: a ``ValidationError`` in words, nothing staged.

        Input: a Record of $2,100.00 on 01-02, the first entry's payday.
        Expected: "Your pay from 2026-01-02 is already recorded; fix that
        entry instead.", and the profile's pay list holds the one entry, with
        nothing pending in the session.
        Why: with the door's check deleted the unique key refuses the same
        payday at the route's flush in the same words, so only this test can
        see that the check is the door's own.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            ctx = BalanceContext.build(seed_user["user"].id)

            with pytest.raises(ValidationError) as refused:
                pay_list_service.record_entry(
                    profile, ctx, Decimal("2100.00"), date(2026, 1, 2),
                    date(2026, 3, 20),
                )

            assert str(refused.value) == (
                "Your pay from 2026-01-02 is already recorded; fix that entry "
                "instead."
            )
            assert not db.session.new
            assert [entry.payday for entry in profile.pay_entries] == [date(2026, 1, 2)]

    def test_the_entry_is_staged_not_flushed(self, app, seed_user, seed_periods):
        """A Record of 03-27 is pending in the session, on the profile's list, unflushed.

        Input: a Record of $2,100.00 on 03-27, the next payday.
        Expected: the entry is on the profile's loaded pay list and in the
        session's pending set, with no id yet; the database holds one entry
        until the caller flushes, then two.
        Why: the INSERT that can meet ``uq_pay_entries_profile_payday`` in a
        race must run at the route's guarded flush, never inside the door,
        where nothing would report it.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            ctx = BalanceContext.build(seed_user["user"].id)

            entry = pay_list_service.record_entry(
                profile, ctx, Decimal("2100.00"), date(2026, 3, 27),
                date(2026, 3, 20),
            )

            assert entry in db.session.new
            assert entry.id is None
            assert entry in profile.pay_entries
            with db.session.no_autoflush:
                stored = db.session.query(SalaryPayEntry).filter_by(
                    salary_profile_id=profile.id,
                ).count()
            assert stored == 1
            db.session.flush()
            assert db.session.query(SalaryPayEntry).filter_by(
                salary_profile_id=profile.id,
            ).count() == 2


class TestRemoveEntry:
    """``remove_entry`` never takes a profile's only entry."""

    def test_the_only_entry_is_refused_before_any_write(self, app, seed_user, seed_periods):
        """A Remove of the one entry: a ``ValidationError`` in words, nothing staged.

        Input: a profile whose pay list holds $2,000.00 from 01-02 alone.
        Expected: the door's sentence, the entry still on the profile's list
        and not marked for deletion.
        Why: every paycheck is priced from the pay list (ruling R-SAL68), and
        the door's refusal is the check; the database's rule is the backstop.
        """
        with app.app_context():
            profile = _profile(seed_user, seed_periods)
            only = profile.pay_entries[0]

            with pytest.raises(ValidationError) as refused:
                pay_list_service.remove_entry(only)

            assert str(refused.value) == (
                "Your pay from 2026-01-02 is your only pay entry, and every "
                "paycheck is priced from it, so it can't be removed.  Fix it "
                "instead."
            )
            assert profile.pay_entries == [only]
            assert only not in db.session.deleted
