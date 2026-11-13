"""Listing and removing passkeys, a reset's removal of all, and the statement of holding any."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.passkey import Passkey
from app.models.user import User
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import ORIGIN, T0, Clock, SoftwareAuthenticator, add
from tests.test_services.test_passkey_service._helpers import add_row


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The account whose passkeys these are."""
    return bare_user["user"]


def test_the_list_is_the_accounts_own_newest_first(user, second_user, clock):
    """Added at T0 + 1 h, then T0 + 2 h, then T0; another account's is not listed.

    Added out of their moments' order, so the ids rise in an order the list
    must not follow: newest first is by when each was added.
    """
    middle, _ = add(clock, user, name="iPhone", at=T0 + timedelta(hours=1))
    newest, _ = add(clock, user, name="iPad", at=T0 + timedelta(hours=2))
    oldest, _ = add(clock, user, name="Security key", at=T0)
    add(clock, second_user["user"], name="Theirs")
    assert [each.id for each in passkey_service.list_passkeys(user.id)] == [
        newest.id, middle.id, oldest.id,
    ]


def test_two_added_in_one_instant_list_in_one_order(user, clock):
    """The later id first, at one created_at."""
    first, _ = add(clock, user)
    second, _ = add(clock, user)
    assert [each.id for each in passkey_service.list_passkeys(user.id)] == [
        second.id, first.id,
    ]


def test_removing_ones_own_passkey_deletes_its_row(user, clock):
    """The row is gone, and the account's other passkey stays."""
    kept, _ = add(clock, user)
    removed, _ = add(clock, user)
    passkey_service.remove_passkey(user.id, removed.id)
    assert db.session.get(Passkey, removed.id) is None
    assert [each.id for each in passkey_service.list_passkeys(user.id)] == [kept.id]


def test_another_accounts_passkey_is_not_found_and_stays(user, second_user, clock):
    """404 for another user's id, and their passkey is untouched."""
    theirs, _ = add(clock, second_user["user"])
    with pytest.raises(NotFoundError):
        passkey_service.remove_passkey(user.id, theirs.id)
    assert db.session.get(Passkey, theirs.id) is not None


def test_a_passkey_that_does_not_exist_is_not_found(user, clock):
    """The same refusal as another account's."""
    with pytest.raises(NotFoundError):
        passkey_service.remove_passkey(user.id, 999_999)


class TestHoldingAPasskey:
    """The one statement of "holds a passkey", which step plan#3542 will close the password sign-in by."""

    def test_an_account_with_none_holds_none(self, user, clock):
        """No row names it."""
        assert passkey_service.has_passkey(user.id) is False

    def test_an_account_holds_one_once_added_and_none_once_its_last_is_removed(self, user, clock):
        """Two added, one removed: still held; the last removed: none."""
        first, _ = add(clock, user)
        second, _ = add(clock, user)
        passkey_service.remove_passkey(user.id, first.id)
        assert passkey_service.has_passkey(user.id) is True
        passkey_service.remove_passkey(user.id, second.id)
        assert passkey_service.has_passkey(user.id) is False

    def test_another_accounts_passkey_is_not_this_accounts(self, user, second_user, clock):
        """Held by the other account only."""
        add(clock, second_user["user"])
        assert passkey_service.has_passkey(user.id) is False
        assert passkey_service.has_passkey(second_user["user"].id) is True

    def test_the_clause_answers_in_the_statement_that_finds_the_account(
        self, user, second_user, clock,
    ):
        """``passkey_held_by(User.id)`` beside each account, in one statement."""
        add(clock, user)
        held = dict(db.session.execute(
            select(User.id, passkey_service.passkey_held_by(User.id))
            .where(User.id.in_([user.id, second_user["user"].id]))
        ).all())
        assert held == {user.id: True, second_user["user"].id: False}


class TestRemovingEvery:
    """A reset's share: every passkey of the account and its add in flight, and no one else's."""

    def test_every_passkey_and_the_add_go_and_another_accounts_stay(
        self, user, second_user, clock,
    ):
        """Two passkeys and an add begun: all gone, True; the other account's two kept."""
        add(clock, user)
        add(clock, user)
        passkey_service.registration_options(user, origin=ORIGIN)
        theirs, _ = add(clock, second_user["user"])
        passkey_service.registration_options(second_user["user"], origin=ORIGIN)

        assert passkey_service.remove_every_passkey(user.id) is True

        assert passkey_service.list_passkeys(user.id) == []
        assert add_row(user) is None
        assert [each.id for each in passkey_service.list_passkeys(
            second_user["user"].id,
        )] == [theirs.id]
        assert add_row(second_user["user"]) is not None

    def test_an_add_in_flight_alone_is_removed(self, user, clock):
        """No passkey yet, an add begun: the add goes, True."""
        passkey_service.registration_options(user, origin=ORIGIN)

        assert passkey_service.remove_every_passkey(user.id) is True

        assert add_row(user) is None

    def test_an_account_holding_neither_removes_nothing(self, user, second_user, clock):
        """False, and the other account's passkey is untouched."""
        theirs, _ = add(clock, second_user["user"])

        assert passkey_service.remove_every_passkey(user.id) is False

        assert db.session.get(Passkey, theirs.id) is not None

    def test_a_passkey_made_before_the_removal_is_never_kept_after_it(self, user, clock):
        """Registered, then removed: its keeping finds no add, is refused, keeps nothing."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        sign_in_options, sign_in_state = passkey_service.complete_registration(
            user, json.dumps(authenticator.create(options)), "iPhone",
            origin=ORIGIN, state=state,
        )

        passkey_service.remove_every_passkey(user.id)

        with pytest.raises(PasskeyRefused) as refused:
            passkey_service.add_passkey(
                user, json.dumps(authenticator.get(sign_in_options)),
                origin=ORIGIN, state=sign_in_state,
            )
        assert refused.value.refusal is PasskeyRefusal.EXPIRED
        assert passkey_service.list_passkeys(user.id) == []
