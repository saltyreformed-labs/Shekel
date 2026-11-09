"""The guess record service: a stranger's guesses count by the email typed.

Plan#2772, ruling **plan#3110**: every email can have a record, so an email
with no account is counted, locked and stamped as a real one is, and the
record holds the email's keyed digest, never the address.  Ruling **plan#3113**:
the record is audited, one row per try.

**The clock is frozen, not approximated**, as in
``tests/test_services/test_known_device_service.py``: each act freezes the
service module at the moment the case names, and that moment IS its now.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.guess_record import GuessRecord
from app.services import guess_record_service
from tests._guess_records import count_of, locked_until_of, record_of
from tests._test_helpers import freeze_today
from tests.conftest import SEED_USER_EMAIL

#: The moment each case's first try is made.
_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: The threshold and lock length every case judges with, spelled here so the
#: tests are an oracle the configuration cannot move.
_THRESHOLD = 3
_DURATION = timedelta(minutes=15)

_ONE_SECOND = timedelta(seconds=1)

#: An email no account has.
_NO_ACCOUNT = "nobody@shekel.local"


@pytest.fixture(name="at")
def _at(monkeypatch):
    """Freeze the service module's clock at a moment the case names."""
    def freeze(moment: datetime) -> None:
        freeze_today(
            monkeypatch, moment.date(),
            modules=(guess_record_service.__name__,),
            at_time=moment.time(),
        )
    return freeze


def _judge(email: str, *, wrong: bool = True) -> guess_record_service.TryOutcome:
    """Judge one try at *email* with this module's threshold and length, then commit."""
    outcome = guess_record_service.judge_try(
        email, wrong=wrong, threshold=_THRESHOLD, duration=_DURATION,
    )
    db.session.commit()
    return outcome


class TestTheDigest:
    """The record names the email by a keyed digest, never by the address."""

    def test_it_is_sixty_four_hex_characters_and_not_a_plain_hash(self, app):
        """Lowercase hex, holding no address, and not the email's unkeyed SHA-256."""
        digest = guess_record_service.email_digest(SEED_USER_EMAIL)

        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert digest != hashlib.sha256(SEED_USER_EMAIL.encode()).hexdigest()

    def test_another_key_names_another_record(self, app, monkeypatch):
        """The key is ``SECRET_KEY``'s: a new one gives every email a new digest."""
        before = guess_record_service.email_digest(SEED_USER_EMAIL)
        monkeypatch.setenv("SECRET_KEY", "another-key-of-at-least-thirty-two-characters")

        assert guess_record_service.email_digest(SEED_USER_EMAIL) != before

    def test_no_key_refuses(self, app, monkeypatch):
        """With no ``SECRET_KEY`` nothing can be counted, and it says why."""
        monkeypatch.delenv("SECRET_KEY")

        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            guess_record_service.email_digest(SEED_USER_EMAIL)

    def test_the_table_refuses_an_address(self, app, db):
        """A writer that stored the address is refused at the database."""
        db.session.add(GuessRecord(
            email_digest=SEED_USER_EMAIL.ljust(64, "x"),
            last_attempt_at=_T0,
        ))
        with pytest.raises(IntegrityError, match="ck_guess_records_email_digest_sha256_hex"):
            db.session.flush()
        db.session.rollback()


class TestTheJudgment:
    """One judgment for every try, at an email with an account or without."""

    def test_an_email_with_no_account_counts_as_a_real_one_does(self, app, db, seed_user, at):
        """One wrong try each: 1 on each record, each stamped with the try's time."""
        at(_T0)
        _judge(_NO_ACCOUNT)
        _judge(SEED_USER_EMAIL)

        for email in (_NO_ACCOUNT, SEED_USER_EMAIL):
            record = record_of(email)
            assert (record.failed_login_count, record.locked_until) == (1, None)
            assert record.last_attempt_at == _T0

    def test_the_threshold_opens_the_lock_and_zeroes_the_count(self, app, db, seed_user, at):
        """The third wrong try opens a lock ending 15 minutes after it, and says so."""
        at(_T0)
        outcomes = [_judge(SEED_USER_EMAIL) for _ in range(_THRESHOLD)]

        assert [o.opened_lock_until for o in outcomes] == [None, None, _T0 + _DURATION]
        assert not any(o.refused for o in outcomes)
        assert count_of(SEED_USER_EMAIL) == 0
        assert locked_until_of(SEED_USER_EMAIL) == _T0 + _DURATION

    @pytest.mark.parametrize("wrong", [True, False], ids=["wrong", "right"])
    def test_a_try_while_locked_is_refused_uncounted_and_stamped(
        self, app, db, seed_user, at, wrong,
    ):
        """A minute into the lock: refused, the count still 0, the try's time stamped."""
        at(_T0)
        for _ in range(_THRESHOLD):
            _judge(SEED_USER_EMAIL)
        at(_T0 + timedelta(minutes=1))

        assert _judge(SEED_USER_EMAIL, wrong=wrong).refused
        record = record_of(SEED_USER_EMAIL)
        assert record.failed_login_count == 0
        assert record.last_attempt_at == _T0 + timedelta(minutes=1)

    def test_the_lock_is_over_at_its_end(self, app, db, seed_user, at):
        """At exactly 15 minutes the lock is over, and a wrong try counts again: 1."""
        at(_T0)
        for _ in range(_THRESHOLD):
            _judge(SEED_USER_EMAIL)
        at(_T0 + _DURATION)

        assert not guess_record_service.is_locked(SEED_USER_EMAIL)
        assert not _judge(SEED_USER_EMAIL).refused
        assert count_of(SEED_USER_EMAIL) == 1

    def test_a_second_lock_opens_once_the_first_has_run_out(self, app, db, seed_user, at):
        """Three wrong, the lock runs out, three more: the last opens a second lock, and says so.

        The outcome names a lock this try opened by the lock's end moving, so
        a record whose first lock has run out (not NULL) still reports the
        second: it ends 15 minutes after the try at 15 minutes, 30 after _T0.
        """
        at(_T0)
        for _ in range(_THRESHOLD):
            _judge(SEED_USER_EMAIL)
        at(_T0 + _DURATION)

        outcomes = [_judge(SEED_USER_EMAIL) for _ in range(_THRESHOLD)]

        assert [o.opened_lock_until for o in outcomes] == [
            None, None, _T0 + _DURATION + _DURATION,
        ]
        assert locked_until_of(SEED_USER_EMAIL) == _T0 + _DURATION + _DURATION

    def test_the_lock_is_open_a_second_before_its_end(self, app, db, seed_user, at):
        """The control: a second before the end, the lock is open."""
        at(_T0)
        for _ in range(_THRESHOLD):
            _judge(SEED_USER_EMAIL)
        at(_T0 + _DURATION - _ONE_SECOND)

        assert guess_record_service.is_locked(SEED_USER_EMAIL)

    def test_a_right_try_counts_nothing(self, app, db, seed_user, at):
        """Two wrong, then a right one: still 2, not refused, the record stamped."""
        at(_T0)
        _judge(SEED_USER_EMAIL)
        _judge(SEED_USER_EMAIL)
        at(_T0 + _ONE_SECOND)

        assert not _judge(SEED_USER_EMAIL, wrong=False).refused
        record = record_of(SEED_USER_EMAIL)
        assert record.failed_login_count == 2
        assert record.last_attempt_at == _T0 + _ONE_SECOND

    def test_no_record_is_no_lock(self, app, db, seed_user):
        """An email never tried is not locked."""
        assert not guess_record_service.is_locked(SEED_USER_EMAIL)


class TestTheCleanUp:
    """Each judgment deletes the records quiet for the lock's length that no one needs."""

    def test_a_quiet_record_of_no_account_goes_and_an_accounts_count_stays(
        self, app, db, seed_user, at,
    ):
        """Fifteen quiet minutes: the made-up email's record goes, the account's 1 stays."""
        at(_T0)
        _judge(_NO_ACCOUNT)
        _judge(SEED_USER_EMAIL)
        at(_T0 + _DURATION)
        _judge("someone-else@shekel.local")

        assert record_of(_NO_ACCOUNT) is None
        assert count_of(SEED_USER_EMAIL) == 1

    def test_a_quiet_record_holding_nothing_goes(self, app, db, seed_user, at):
        """An account's record with no count and no lock is the same as none."""
        at(_T0)
        _judge(SEED_USER_EMAIL, wrong=False)
        at(_T0 + _DURATION)
        _judge(_NO_ACCOUNT)

        assert record_of(SEED_USER_EMAIL) is None

    def test_a_record_quiet_a_second_less_stays(self, app, db, seed_user, at):
        """The control: a second short of the lock's length, the made-up email's record stays."""
        at(_T0)
        _judge(_NO_ACCOUNT)
        at(_T0 + _DURATION - _ONE_SECOND)
        _judge("someone-else@shekel.local")

        assert record_of(_NO_ACCOUNT) is not None


class TestTheClear:
    """The clear deletes the record, and says whether there was one."""

    def test_it_deletes_and_says_so(self, app, db, seed_user):
        """A record: True and gone.  None: False."""
        _judge(SEED_USER_EMAIL)

        assert guess_record_service.clear(SEED_USER_EMAIL) is True
        db.session.commit()
        assert record_of(SEED_USER_EMAIL) is None
        assert guess_record_service.clear(SEED_USER_EMAIL) is False


def _audit_rows() -> list[tuple[str, str]]:
    """Return each audit row the guess records wrote: its operation and its new data as text."""
    return [
        (operation, new_data or "")
        for operation, new_data in db.session.execute(text(
            "SELECT operation, new_data::text FROM system.audit_log "
            "WHERE table_schema = 'auth' AND table_name = 'guess_records' "
            "ORDER BY id"
        ))
    ]


class TestTheAuditTrail:
    """One audit row per try (ruling plan#3113), holding the digest and never the address."""

    def test_each_try_writes_one_row(self, app, db, seed_user, at):
        """Three wrong tries (the third opens the lock), then one the lock refuses: four rows."""
        at(_T0)
        _judge(SEED_USER_EMAIL)
        _judge(SEED_USER_EMAIL)
        _judge(SEED_USER_EMAIL)
        at(_T0 + _ONE_SECOND)
        _judge(SEED_USER_EMAIL)

        rows = _audit_rows()
        assert [operation for operation, _ in rows] == ["INSERT", "UPDATE", "UPDATE", "UPDATE"]
        assert not any(SEED_USER_EMAIL in new_data for _, new_data in rows)
