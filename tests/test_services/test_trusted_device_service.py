"""The trusted-device service: who is trusted, until when, and who may forget.

Plan#2173, ruling **plan#2406**.  A trusted device skips only the two-factor
code, and :mod:`app.services.trusted_device_service` holds the record's four
acts.  Every boundary here is exact, to the second, because the predicate is
the whole of revocation: a device is live while it was trusted less than
thirty days ago AND not before its user's ``session_invalidated_at``.

**The clock is frozen, not approximated.**  The service reads
``datetime.now(timezone.utc)`` once per act, and this directory's autouse
freeze (``tests/test_services/conftest.py``) already replaces every loaded
module's ``datetime`` with a frozen one, which ``time_machine`` cannot reach
past.  So each act re-freezes the service module at the moment the case names
(:func:`tests._test_helpers.freeze_today` with ``at_time``), and the moment IS
the service's now.  The trust and the read are separate acts at separate
moments, which is what lets "thirty days less a second" mean exactly that.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.exceptions import NotFoundError
from app.models.trusted_device import TrustedDevice
from app.services import trusted_device_service
from tests._test_helpers import freeze_today

#: The moment the first trust in each case is made.  Fixed, so no case
#: depends on the day the suite runs; the service never reads the calendar.
_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: Thirty days: ruling plan#2406's trust lifetime, spelled here on purpose
#: rather than imported, so the test is an oracle the model's constant cannot
#: move silently.
_THIRTY_DAYS = timedelta(days=30)

_ONE_SECOND = timedelta(seconds=1)

_ONE_DAY = timedelta(days=1)

_FIREFOX = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
)


class _Acts:
    """The service's four acts, each run with its clock at a named moment."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch

    def _at(self, moment):
        """Freeze the service module's clock at *moment* (UTC)."""
        freeze_today(
            self._monkeypatch, moment.date(),
            modules=(trusted_device_service.__name__,),
            at_time=moment.time(),
        )

    def trust(self, moment, user_id, user_agent=_FIREFOX):
        """Trust a device for *user_id* at *moment*; return its token."""
        self._at(moment)
        return trusted_device_service.trust_device(user_id, user_agent)

    def find(self, moment, user_id, token):
        """Look *token* up for *user_id* at *moment*."""
        self._at(moment)
        return trusted_device_service.find_live_device(user_id, token)

    def listed(self, moment, user_id):
        """List *user_id*'s live devices at *moment*, as their ids in order."""
        self._at(moment)
        return [
            device.id
            for device in trusted_device_service.list_live_devices(user_id)
        ]

    def forget(self, moment, user_id, device_id):
        """Forget *device_id* for *user_id* at *moment*."""
        self._at(moment)
        trusted_device_service.forget_device(user_id, device_id)


@pytest.fixture(name="acts")
def _acts(monkeypatch):
    """The service's acts, each at a moment the case names."""
    return _Acts(monkeypatch)


def _device_of(db, token):
    """Return the row *token* names, read past the service (no liveness)."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return db.session.query(TrustedDevice).filter_by(
        token_hash=token_hash,
    ).one()


def _rows_of(db, user_id):
    """Return the ids of every row *user_id* holds, live or dead."""
    return sorted(
        row.id for row in
        db.session.query(TrustedDevice).filter_by(user_id=user_id)
    )


class TestTrustingADevice:
    """What a trust writes, and what it hands back."""

    def test_the_row_holds_the_tokens_hash_and_never_the_token(
        self, app, db, acts, seed_user,
    ):
        """Only the SHA-256 of the token is stored; the token is the return."""
        user_id = seed_user["user"].id

        token = acts.trust(_T0, user_id)

        [row] = db.session.query(TrustedDevice).filter_by(user_id=user_id)
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in row.token_hash
        assert row.trusted_at == _T0
        assert row.user_agent == _FIREFOX

    def test_the_token_is_256_random_bits(self, app, db, acts, seed_user):
        """32 bytes, URL-safe base64 without padding: ceil(32 * 4 / 3) = 43."""
        user_id = seed_user["user"].id

        first = acts.trust(_T0, user_id)
        second = acts.trust(_T0, user_id)

        assert len(first) == 43
        assert len(second) == 43
        assert first != second
        assert len(_rows_of(db, user_id)) == 2

    def test_a_long_user_agent_is_cut_to_the_column(
        self, app, db, acts, seed_user,
    ):
        """A browser chooses the header's length, so 300 characters keep 255."""
        user_id = seed_user["user"].id

        token = acts.trust(_T0, user_id, user_agent="x" * 300)

        assert _device_of(db, token).user_agent == "x" * 255

    def test_no_user_agent_is_stored_as_empty(self, app, db, acts, seed_user):
        """A browser that sends none is named by the empty string."""
        user_id = seed_user["user"].id

        token = acts.trust(_T0, user_id, user_agent="")

        assert _device_of(db, token).user_agent == ""

    def test_the_trust_purges_the_users_expired_rows_and_nothing_else(
        self, app, db, acts, seed_user, seed_second_user,
    ):
        """At T0 + 30 days, the user's row 30 days old goes; the rest stay.

        The user's rows, judged at the purge (T0 + 30 days), with no bump:

        * trusted at T0: exactly 30 days old, so dead by age -- purged;
        * trusted at T0 + 1 s: 30 days less a second, live -- kept;
        * the purge's own new row -- kept.

        The second user's row trusted at T0 is dead too, and is theirs to
        purge: kept.

        A row dead ONLY by age and one dead ONLY by a bump cannot both be one
        user's at one purge (the first is older than the bump the second
        precedes, so it precedes it too), which is why the bump has a case of
        its own below.
        """
        user_id = seed_user["user"].id
        other_id = seed_second_user["user"].id
        expired = _device_of(db, acts.trust(_T0, user_id)).id
        almost = _device_of(db, acts.trust(_T0 + _ONE_SECOND, user_id)).id
        others = _device_of(db, acts.trust(_T0, other_id)).id

        new = _device_of(db, acts.trust(_T0 + _THIRTY_DAYS, user_id)).id

        assert expired not in _rows_of(db, user_id)
        assert _rows_of(db, user_id) == sorted([almost, new])
        assert _rows_of(db, other_id) == [others]

    def test_the_trust_purges_the_users_forgotten_rows_and_nothing_else(
        self, app, db, acts, seed_user,
    ):
        """At T0 + 3 days, the row trusted before the bump goes; the rest stay.

        The user's rows, judged at the purge (T0 + 3 days), each well inside
        thirty days:

        * trusted at T0, before the bump at T0 + 1 day: dead by the bump --
          purged;
        * trusted at T0 + 2 days, after it: live -- kept;
        * the purge's own new row -- kept.
        """
        user = seed_user["user"]
        revoked = _device_of(db, acts.trust(_T0, user.id)).id
        user.session_invalidated_at = _T0 + _ONE_DAY
        db.session.flush()
        after = _device_of(db, acts.trust(_T0 + 2 * _ONE_DAY, user.id)).id

        new = _device_of(db, acts.trust(_T0 + 3 * _ONE_DAY, user.id)).id

        assert revoked not in _rows_of(db, user.id)
        assert _rows_of(db, user.id) == sorted([after, new])


class TestTheThirtyDays:
    """Live while strictly less than thirty days old."""

    def test_thirty_days_less_a_second_is_live(self, app, db, acts, seed_user):
        """Trusted at T0, judged at T0 + 30 days - 1 s: found and listed."""
        user_id = seed_user["user"].id
        token = acts.trust(_T0, user_id)
        moment = _T0 + _THIRTY_DAYS - _ONE_SECOND

        found = acts.find(moment, user_id, token)

        assert found is not None
        assert found.id == _device_of(db, token).id
        assert acts.listed(moment, user_id) == [found.id]

    def test_thirty_days_is_dead(self, app, db, acts, seed_user):
        """Trusted at T0, judged at T0 + 30 days: neither found nor listed."""
        user_id = seed_user["user"].id
        token = acts.trust(_T0, user_id)
        moment = _T0 + _THIRTY_DAYS

        assert acts.find(moment, user_id, token) is None
        assert acts.listed(moment, user_id) == []

    def test_expires_at_is_thirty_days_after_the_trust(
        self, app, db, acts, seed_user,
    ):
        """The date Settings will show: T0 + 30 days on the row."""
        token = acts.trust(_T0, seed_user["user"].id)

        assert _device_of(db, token).expires_at == _T0 + _THIRTY_DAYS


class TestTheInvalidation:
    """Not before the user's ``session_invalidated_at``: the session rule's boundary."""

    @pytest.mark.parametrize(
        ("invalidated_after_trust", "live"),
        [
            # The bump a second AFTER the trust: trusted before it, dead.
            (_ONE_SECOND, False),
            # At the same instant: not before it, live (the strict
            # less-than ``load_user`` applies to a session's created stamp).
            (timedelta(0), True),
            # A second BEFORE the trust: trusted after it, live.
            (-_ONE_SECOND, True),
        ],
        ids=["trusted-before", "trusted-at", "trusted-after"],
    )
    def test_a_device_trusted_before_the_bump_is_forgotten(
        self, app, db, acts, seed_user, invalidated_after_trust, live,
    ):
        """Trusted at T0, the bump at T0 + offset, judged a day later."""
        user = seed_user["user"]
        token = acts.trust(_T0, user.id)
        user.session_invalidated_at = _T0 + invalidated_after_trust
        db.session.flush()
        moment = _T0 + _ONE_DAY

        found = acts.find(moment, user.id, token)
        listed = acts.listed(moment, user.id)

        if live:
            assert found is not None
            assert listed == [found.id]
        else:
            assert found is None
            assert listed == []

    def test_no_bump_ever_leaves_the_device_live(self, app, db, acts, seed_user):
        """A NULL ``session_invalidated_at`` forgets nothing."""
        user = seed_user["user"]
        assert user.session_invalidated_at is None
        token = acts.trust(_T0, user.id)

        assert acts.find(_T0 + _ONE_DAY, user.id, token) is not None

    def test_another_users_bump_forgets_nothing_of_this_one(
        self, app, db, acts, seed_user, seed_second_user,
    ):
        """The predicate reads the device's OWN user's column."""
        user_id = seed_user["user"].id
        token = acts.trust(_T0, user_id)
        seed_second_user["user"].session_invalidated_at = _T0 + _ONE_SECOND
        db.session.flush()

        assert acts.find(_T0 + _ONE_DAY, user_id, token) is not None


class TestTheToken:
    """A token finds only its own user's live device."""

    def test_another_users_token_finds_nothing(
        self, app, db, acts, seed_user, seed_second_user,
    ):
        """The first user's token, presented for the second user: ``None``."""
        user_id = seed_user["user"].id
        other_id = seed_second_user["user"].id
        token = acts.trust(_T0, user_id)
        moment = _T0 + _ONE_DAY

        assert acts.find(moment, other_id, token) is None
        assert acts.find(moment, user_id, token) is not None

    def test_an_unknown_token_finds_nothing(self, app, db, acts, seed_user):
        """A token no trust minted: ``None``."""
        user_id = seed_user["user"].id
        acts.trust(_T0, user_id)

        assert acts.find(_T0, user_id, "not-a-token") is None

    def test_the_stored_hash_is_not_a_token(self, app, db, acts, seed_user):
        """A leaked row signs nobody in: its hash, presented, finds nothing."""
        user_id = seed_user["user"].id
        token = acts.trust(_T0, user_id)

        assert acts.find(_T0, user_id, _device_of(db, token).token_hash) is None


class TestTheList:
    """A user's live devices, theirs only, newest first."""

    def test_the_newest_trust_lists_first(self, app, db, acts, seed_user):
        """Trusted at T0 and T0 + 1 day, listed at T0 + 2 days: newest first."""
        user_id = seed_user["user"].id
        older = _device_of(db, acts.trust(_T0, user_id)).id
        newer = _device_of(db, acts.trust(_T0 + _ONE_DAY, user_id)).id

        assert acts.listed(_T0 + 2 * _ONE_DAY, user_id) == [newer, older]

    def test_another_users_devices_are_not_listed(
        self, app, db, acts, seed_user, seed_second_user,
    ):
        """Each user's list holds only their own device."""
        user_id = seed_user["user"].id
        other_id = seed_second_user["user"].id
        mine = _device_of(db, acts.trust(_T0, user_id)).id
        theirs = _device_of(db, acts.trust(_T0, other_id)).id

        assert acts.listed(_T0, user_id) == [mine]
        assert acts.listed(_T0, other_id) == [theirs]


class TestForgettingADevice:
    """A user forgets their own device; nobody forgets another's."""

    def test_a_user_forgets_their_own_device(self, app, db, acts, seed_user):
        """The row is deleted, so the token finds nothing."""
        user_id = seed_user["user"].id
        token = acts.trust(_T0, user_id)
        device_id = _device_of(db, token).id

        acts.forget(_T0, user_id, device_id)

        assert _rows_of(db, user_id) == []
        assert acts.find(_T0, user_id, token) is None

    def test_another_users_device_is_refused_and_kept(
        self, app, db, acts, seed_user, seed_second_user,
    ):
        """The second user cannot forget the first user's device."""
        user_id = seed_user["user"].id
        other_id = seed_second_user["user"].id
        device_id = _device_of(db, acts.trust(_T0, user_id)).id

        with pytest.raises(NotFoundError, match=str(device_id)):
            acts.forget(_T0, other_id, device_id)

        assert _rows_of(db, user_id) == [device_id]

    def test_a_missing_device_is_the_same_refusal(
        self, app, db, acts, seed_user,
    ):
        """An id no row holds raises the one refusal "not yours" raises."""
        user_id = seed_user["user"].id
        device_id = _device_of(db, acts.trust(_T0, user_id)).id

        with pytest.raises(NotFoundError, match=str(device_id + 1)):
            acts.forget(_T0, user_id, device_id + 1)

        assert _rows_of(db, user_id) == [device_id]

    def test_a_dead_device_is_still_forgettable(self, app, db, acts, seed_user):
        """Expired since the user saw it listed: deleted, not refused."""
        user_id = seed_user["user"].id
        device_id = _device_of(db, acts.trust(_T0, user_id)).id

        acts.forget(_T0 + _THIRTY_DAYS, user_id, device_id)

        assert _rows_of(db, user_id) == []
