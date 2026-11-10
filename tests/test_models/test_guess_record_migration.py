"""Migration ``3748145b83e3`` carries each account's count and lock, both ways.

Plan#2772, rulings **plan#3110** and **plan#3115**.  The revision moves a
stranger's wrong-guess count and lock off ``auth.users`` into
``auth.guess_records``, named by the email's keyed digest, and its downgrade
puts them back on the account's row.  The test template is built at head from
an empty database, so neither carry runs a single row there: this test runs
the revision's own ``downgrade()`` and then its ``upgrade()`` on this test's
own database, over records two accounts and one email with no account hold.

Each direction is graded on its own.  The downgrade's carry is read off the
account rows before the upgrade runs, so a downgrade that dropped a count or a
lock fails there; a downgrade that carried them and an upgrade that did not
fails on the records after.
"""

from datetime import datetime, timezone

from sqlalchemy import text

from app.extensions import db
from tests._guess_records import count_of, locked_until_of, record_of, set_record
from tests._test_helpers import load_migration_module, run_migration_callable

_MIGRATION = load_migration_module(
    "3748145b83e3_a_stranger_s_guesses_count_on_a_record.py",
)

#: An email no account has: its record has no row to go back to.
_NO_ACCOUNTS_EMAIL = "nobody@example.com"

#: The last try's time each seeded record holds.  Fixed, so the test reads the
#: same on every day; the carry never reads it.
_LAST_TRY = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: A lock's end with microseconds, so a carry that rounded it would show.
_LOCK_ENDS = datetime(2026, 3, 1, 12, 10, 0, 123456, tzinfo=timezone.utc)

_OWNERS_COUNT = 7

_NO_ACCOUNTS_COUNT = 3


def _account_columns(email):
    """Return ``(failed_login_count, locked_until)`` on the account row of *email*.

    Read by SQL: the columns exist only between the downgrade and the upgrade,
    and the model at head has neither.
    """
    return tuple(db.session.execute(
        text(
            "SELECT failed_login_count, locked_until FROM auth.users "
            "WHERE email = :email"
        ),
        {"email": email},
    ).one())


class TestTheCarry:
    """Down, then up, over records two accounts and a made-up email hold."""

    def test_each_accounts_count_and_lock_go_down_and_come_back(
        self, seed_user, seed_second_user,
    ):
        """The owner's 7 and the second user's lock go to their rows and come back exactly.

        The made-up email's record has no row to go to, so it is gone after the
        round trip, and the table returns with its audit trigger.  Run in the
        fixtures' own app context and session, which the migration's runner
        commits before its DDL: a nested context would leave this one's read of
        the users open, holding the lock the downgrade's ``ALTER TABLE`` waits on.
        """
        owner = seed_user["user"].email
        second = seed_second_user["user"].email
        set_record(owner, count=_OWNERS_COUNT, locked_until=None, last_attempt_at=_LAST_TRY)
        set_record(second, count=0, locked_until=_LOCK_ENDS, last_attempt_at=_LAST_TRY)
        set_record(
            _NO_ACCOUNTS_EMAIL, count=_NO_ACCOUNTS_COUNT, locked_until=None,
            last_attempt_at=_LAST_TRY,
        )
        db.session.commit()

        run_migration_callable(_MIGRATION.downgrade, db.session)

        assert _account_columns(owner) == (_OWNERS_COUNT, None)
        assert _account_columns(second) == (0, _LOCK_ENDS)
        assert db.session.execute(
            text("SELECT to_regclass('auth.guess_records')"),
        ).scalar() is None

        run_migration_callable(_MIGRATION.upgrade, db.session)
        db.session.expire_all()

        assert (count_of(owner), locked_until_of(owner)) == (_OWNERS_COUNT, None)
        assert (count_of(second), locked_until_of(second)) == (0, _LOCK_ENDS)
        assert record_of(_NO_ACCOUNTS_EMAIL) is None
        assert db.session.execute(text(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_guess_records'"
        )).scalar() == 1
