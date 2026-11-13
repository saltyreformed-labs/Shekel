"""The Security page lists each user's passkeys, and removes one (plan#2538, task plan#3486).

Each passkey shows its name, the day it was added and the day it last signed
in, both in the display time zone, and a Remove that asks no re-confirm, as
forgetting a trusted device asks none.  Removing raises the "was this you?"
banner.  An owner's and a companion's are each their own.  Every Remove is
posted as the page renders its form (:func:`rendered_form_controls`).

The passkeys are added through the service's own three acts, at moments the
case names (:func:`~tests._passkeys.add`), and used by its sign-in, so their
two instants are ones a real add and sign-in write.
"""

import json
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from sqlalchemy import func, select

from app.extensions import db
from app.models.passkey import Passkey
from app.models.user import User
from app.services import passkey_service
from app.utils.security_events import SecurityEventKind
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from tests._companions import text_of
from tests._form_controls import rendered_form_controls
from tests._passkeys import ORIGIN, Clock, add, add_committed

#: A passkey added at 03:30 on 2 March, UTC: still 1 March in New York
#: (EST, UTC-5), so a page that showed the UTC day would show the 2nd.
_ADDED = datetime(2026, 3, 2, 3, 30, tzinfo=timezone.utc)

#: Its last sign-in, at 03:10 on 6 March, UTC: 5 March in New York (EST;
#: daylight time begins on the 8th).
_USED = datetime(2026, 3, 6, 3, 10, tzinfo=timezone.utc)

#: How old a fresh-login stamp is made, so a step-up would ask again.
_STALE = timedelta(minutes=6)


class _Removes(HTMLParser):
    """The Security page's Remove forms: each one's action and confirmation.

    Attributes:
        forms: ``{action: data-confirm}`` for each form posting to a
            passkey's remove.
    """

    def __init__(self):
        super().__init__()
        self.forms = {}

    def handle_starttag(self, tag, attrs):
        """Read each Remove form."""
        attributes = dict(attrs)
        action = attributes.get("action") or ""
        if tag == "form" and action.startswith("/security/passkeys/") and action.endswith("/remove"):
            self.forms[action] = attributes.get("data-confirm")


def _security_page(client):
    """GET the Security page; return its text and its Remove forms."""
    response = client.get("/security")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    removes = _Removes()
    removes.feed(page)
    return page, removes.forms


def _remove_url(passkey_id):
    """Return the Remove URL of passkey *passkey_id*."""
    return f"/security/passkeys/{passkey_id}/remove"


def _remove(client, page, passkey_id):
    """Post the Remove form the page renders for passkey *passkey_id*."""
    url = _remove_url(passkey_id)
    return client.post(url, data=rendered_form_controls(page, url))


def _signed_in_by(clock, user, authenticator, moment):
    """Sign *user* in with *authenticator*'s passkey at *moment*, through the service; commit."""
    clock.at(moment)
    options, state = passkey_service.sign_in_options(origin=ORIGIN)
    passkey_service.complete_sign_in(
        user, json.dumps(authenticator.get(options)), origin=ORIGIN, state=state,
    )
    db.session.commit()


def _ids_of(user_id):
    """Return the ids of *user_id*'s passkeys, read afresh."""
    return list(db.session.execute(
        select(Passkey.id).where(Passkey.user_id == user_id).order_by(Passkey.id)
    ).scalars())


def _banner_of(user_id):
    """Return *user_id*'s last security event kind, read afresh."""
    user = db.session.get(User, user_id)
    db.session.refresh(user)
    return user.last_security_event_kind


class TestTheList:
    """Each passkey's name and two days, the user's own alone."""

    def test_each_passkey_shows_its_name_and_its_two_days(
        self, auth_client, seed_user, monkeypatch,
    ):
        """The day added and the day last used, each in the display time zone."""
        user = seed_user["user"]
        clock = Clock(monkeypatch)
        _, authenticator = add(clock, user, name="Kitchen iPad", at=_ADDED)
        _signed_in_by(clock, user, authenticator, _USED)

        page, _ = _security_page(auth_client)

        text = text_of(page)
        assert "Kitchen iPad Added Mar 1, 2026; last used Mar 5, 2026" in text
        assert "Mar 2, 2026" not in text
        assert "Mar 6, 2026" not in text

    def test_the_most_recently_added_is_first(self, auth_client, seed_user, monkeypatch):
        """Two passkeys list newest first, each with its own Remove."""
        user = seed_user["user"]
        clock = Clock(monkeypatch)
        older, _ = add(clock, user, name="Older", at=_ADDED)
        newer, _ = add(clock, user, name="Newer", at=_ADDED + timedelta(days=1))
        older_id, newer_id = older.id, newer.id
        db.session.commit()

        page, removes = _security_page(auth_client)

        assert page.index(">Newer</div>") < page.index(">Older</div>")
        assert set(removes) == {_remove_url(older_id), _remove_url(newer_id)}

    def test_a_user_with_none_is_told_so(self, auth_client):
        """No passkey: the card says so and still offers to add one."""
        page, removes = _security_page(auth_client)

        assert "No passkeys yet." in page
        assert removes == {}
        assert 'href="/security/passkeys/add"' in page

    def test_a_companion_sees_hers_and_not_the_owners(
        self, companion_client, seed_companion, seed_user, monkeypatch,
    ):
        """Each Security page lists its own user's passkeys alone."""
        clock = Clock(monkeypatch)
        hers, _ = add(clock, seed_companion["user"], name="Her phone")
        his, _ = add(clock, seed_user["user"], name="His phone")
        her_id, his_id = hers.id, his.id
        db.session.commit()

        page, removes = _security_page(companion_client)

        assert ">Her phone</div>" in page
        assert "His phone" not in page
        assert set(removes) == {_remove_url(her_id)}
        assert _remove_url(his_id) not in removes

    def test_remove_asks_before_it_posts(self, auth_client, seed_user):
        """The Remove form carries the page's confirmation, as Forget's does."""
        passkey_id, _ = add_committed(seed_user["user"])

        _, removes = _security_page(auth_client)

        assert removes[_remove_url(passkey_id)] == (
            "Remove this passkey? It will no longer sign you in."
        )


class TestRemoving:
    """Remove deletes the user's own passkey, raises the banner, and asks no re-confirm."""

    def test_an_owner_removes_his(self, auth_client, seed_user, caplog):
        """It is gone, the banner is raised, the page says so, and the log records it."""
        user_id = seed_user["user"].id
        passkey_id, _ = add_committed(seed_user["user"])
        kept_id, _ = add_committed(seed_user["user"])
        page, _ = _security_page(auth_client)
        caplog.set_level("INFO")

        response = _remove(auth_client, page, passkey_id)

        assert response.status_code == 302
        assert response.headers["Location"] == "/security"
        assert _ids_of(user_id) == [kept_id]
        assert _banner_of(user_id) == SecurityEventKind.PASSKEY_REMOVED.value
        after = auth_client.get("/security").get_data(as_text=True)
        assert "Passkey removed. It no longer signs you in." in after
        assert "A passkey was removed from your account." in after
        assert [
            (record.user_id, record.passkey_id, record.passkey_user_id)
            for record in caplog.records
            if getattr(record, "event", None) == "passkey_removed"
        ] == [(user_id, passkey_id, user_id)]

    def test_a_companion_removes_hers(self, companion_client, seed_companion):
        """A companion removes her own, and lands on her own Security page."""
        companion_id = seed_companion["user"].id
        passkey_id, _ = add_committed(seed_companion["user"])
        page, _ = _security_page(companion_client)

        response = _remove(companion_client, page, passkey_id)

        assert response.headers["Location"] == "/security"
        assert _ids_of(companion_id) == []
        assert _banner_of(companion_id) == SecurityEventKind.PASSKEY_REMOVED.value

    def test_remove_asks_no_re_confirm(self, auth_client, seed_user):
        """A stale session still removes: removing takes a way in away and grants none."""
        passkey_id, _ = add_committed(seed_user["user"])
        page, _ = _security_page(auth_client)
        with auth_client.session_transaction() as session:
            session[FRESH_LOGIN_AT_KEY] = (datetime.now(timezone.utc) - _STALE).isoformat()

        response = _remove(auth_client, page, passkey_id)

        assert response.headers["Location"] == "/security"
        assert _ids_of(seed_user["user"].id) == []

    def test_another_users_passkey_is_404_and_kept(
        self, auth_client, seed_user, seed_companion,
    ):
        """The owner's Remove of his companion's passkey: 404, kept, and neither banner raised.

        Paired with his own passkey's Remove at the same URL shape, which the
        URL still routes, so the 404 is the ownership's and not the map's.
        """
        his_id, _ = add_committed(seed_user["user"])
        hers_id, _ = add_committed(seed_companion["user"])
        owner_id, companion_id = seed_user["user"].id, seed_companion["user"].id
        banners_before = (_banner_of(owner_id), _banner_of(companion_id))

        refused = auth_client.post(_remove_url(hers_id))

        assert refused.status_code == 404
        assert _ids_of(companion_id) == [hers_id]
        assert (_banner_of(owner_id), _banner_of(companion_id)) == banners_before
        page, _ = _security_page(auth_client)
        assert _remove(auth_client, page, his_id).status_code == 302
        assert _ids_of(seed_user["user"].id) == []

    def test_an_id_no_passkey_holds_is_404(self, auth_client, seed_user):
        """An id past every passkey's is the same 404, and nothing is written."""
        passkey_id, _ = add_committed(seed_user["user"])
        user_id = seed_user["user"].id
        banner_before = _banner_of(user_id)

        unheld = db.session.execute(select(func.max(Passkey.id))).scalar_one() + 1

        refused = auth_client.post(_remove_url(unheld))

        assert refused.status_code == 404
        assert _ids_of(user_id) == [passkey_id]
        assert _banner_of(user_id) == banner_before
