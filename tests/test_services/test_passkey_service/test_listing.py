"""Listing and removing one's own passkeys, and the one statement of holding any."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.passkey import Passkey
from app.models.user import User
from app.services import passkey_service
from tests.test_services.test_passkey_service._helpers import T0, Clock, add


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
