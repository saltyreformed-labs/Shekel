"""Companions lists each companion's passkeys, each removable, and a reset removes them.

Plan#2538, task plan#3487: on Settings > Companions each active companion's
passkeys are listed beside her two-factor and trusted devices, drawn by the
macro the Security page draws one's own with (``auth/_passkey.html``), and the
owner can remove each, as ruled for her trusted devices (plan#3439).  The
owner's Remove runs the act her own Remove does
(``app/routes/_passkeys.remove_passkey_or_404``): the passkey goes, her "was
this you?" banner is raised, and the log names the owner as who pressed it.
The owner's reset of her two-factor (ruling plan#3441) removes her passkeys
and her add in flight with her code, and its confirmation says so while she
holds any.

Another owner's companion, a user who is not a companion, a passkey of another
user and an id no passkey holds are one 404, each paired with a case proving
the URL still routes (a URL-map 404 would pass the same assertion while
guarding nothing).  A companion cannot reach the owner's Remove.

Every Remove and reset is posted as the page renders its form
(:func:`~tests._form_controls.rendered_form_controls`).  The passkeys are
added through the service's own three acts (:mod:`tests._passkeys`).
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.extensions import db
from app.models.passkey import Passkey
from app.models.passkey_add import PasskeyAdd
from app.models.user import User
from app.services import passkey_service
from app.utils.security_events import SecurityEventKind
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from tests._companions import (
    COMPANIONS_PAGE, another_owners_companion, companion_row,
    landed_on_companions, text_of,
)
from tests._csrf_tokens import without_csrf_tokens
from tests._form_controls import rendered_form_controls
from tests._passkeys import ORIGIN, Clock, add, add_committed
from tests._test_helpers import enable_mfa
from tests._two_factor_sign_in import two_factor_row_of

#: A passkey added at 03:30 on 2 March, UTC: still 1 March in New York (EST,
#: UTC-5), so a page that showed the UTC day would show the 2nd.
_ADDED = datetime(2026, 3, 2, 3, 30, tzinfo=timezone.utc)

#: A second, added at 15:00 on 4 March, UTC: 4 March in New York too.
_NEWER = datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc)

#: How old a fresh-login stamp is made, so a step-up would ask again.
_STALE = timedelta(minutes=6)

#: The heading her row's passkey list opens with.
_HEADING = ">Passkeys</div>"


def _remove_url(companion_id, passkey_id):
    """Return the owner's Remove URL for one of a companion's passkeys."""
    return f"/settings/companions/{companion_id}/passkeys/{passkey_id}/remove"


def _reset_url(companion_id):
    """Return the owner's Reset two-factor URL for a companion."""
    return f"/settings/companions/{companion_id}/reset-two-factor"


def _companions_page(client):
    """GET Settings > Companions; return its text."""
    response = client.get(COMPANIONS_PAGE)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _passkey_entries(row):
    """Return the HTML of each passkey listed in a companion's row, in order.

    Each entry is a passkey's summary and its Remove form, which the form's
    closing tag ends; the list ends where her trusted devices begin.
    """
    if _HEADING not in row:
        return []
    listed = row.split(_HEADING, 1)[1].split(">Trusted devices</div>", 1)[0]
    return listed.split("</form>")[:-1]


def _attribute(entry, name):
    """Return the value of the first *name* attribute in *entry*."""
    return re.search(rf'{name}="([^"]+)"', entry).group(1)


def _remove(client, page, companion_id, passkey_id):
    """Post the Remove form *page* renders for one of a companion's passkeys."""
    url = _remove_url(companion_id, passkey_id)
    return client.post(url, data=rendered_form_controls(page, url))


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


class TestHerPasskeys:
    """Her passkeys, named, dated, newest first, each with the owner's Remove."""

    def test_each_passkey_shows_its_name_and_its_two_days_newest_first(
        self, auth_client, seed_user, seed_companion, monkeypatch,
    ):
        """Days in the display time zone; the owner's own passkey is not on her row."""
        companion = seed_companion["user"]
        enable_mfa(db.session, companion.id)
        clock = Clock(monkeypatch)
        older, _ = add(clock, companion, name="Kitchen iPad", at=_ADDED)
        newer, _ = add(clock, companion, name="Her iPhone", at=_NEWER)
        add(clock, seed_user["user"], name="His iPhone", at=_NEWER)
        older_id, newer_id = older.id, newer.id
        db.session.commit()

        row = companion_row(_companions_page(auth_client))

        entries = _passkey_entries(row)
        texts = [text_of(entry) for entry in entries]
        assert len(entries) == 2
        assert texts[0].startswith(
            "Her iPhone Added Mar 4, 2026; last used Mar 4, 2026",
        )
        assert texts[1].startswith(
            "Kitchen iPad Added Mar 1, 2026; last used Mar 1, 2026",
        )
        assert [_attribute(entry, "action") for entry in entries] == [
            _remove_url(companion.id, newer_id), _remove_url(companion.id, older_id),
        ]
        assert _attribute(entries[0], "data-confirm") == (
            "Remove this passkey? It will no longer sign Companion User in."
        )
        assert "His iPhone" not in row
        assert "No passkeys." not in row

    def test_with_two_factor_and_none_the_row_says_so(
        self, auth_client, seed_companion,
    ):
        """No passkey list, and the line saying she holds none."""
        enable_mfa(db.session, seed_companion["user"].id)

        row = companion_row(_companions_page(auth_client))

        assert _HEADING not in row
        assert "No passkeys." in text_of(row)

    def test_without_two_factor_nothing_is_said(self, auth_client, seed_companion):
        """She cannot hold one before her setup, so her row says nothing of them."""
        assert seed_companion["user"].has_two_factor is False

        row = companion_row(_companions_page(auth_client))

        assert _HEADING not in row
        assert "No passkeys." not in text_of(row)


class TestTheOwnerRemovesOne:
    """The owner's Remove deletes that passkey, raises her banner and logs who pressed it."""

    def test_remove_deletes_that_passkey_and_no_other(
        self, auth_client, seed_user, seed_companion, caplog,
    ):
        """Her other passkey and the owner's own stay; the page and the log say so."""
        companion_id, owner_id = seed_companion["user"].id, seed_user["user"].id
        enable_mfa(db.session, companion_id)
        kept_id, _ = add_committed(seed_companion["user"])
        removed_id, _ = add_committed(seed_companion["user"])
        owners_id, _ = add_committed(seed_user["user"])
        owners_banner = _banner_of(owner_id)
        page = _companions_page(auth_client)
        # What the page's own form posts to, not a hand-built URL.
        first = _passkey_entries(companion_row(page))[0]
        assert _attribute(first, "action") == _remove_url(companion_id, removed_id)
        caplog.set_level(logging.INFO)

        response = _remove(auth_client, page, companion_id, removed_id)

        assert response.status_code == 302
        after = landed_on_companions(auth_client.get(response.headers["Location"]))
        assert "Passkey removed. It no longer signs Companion User in." in text_of(after)
        assert _ids_of(companion_id) == [kept_id]
        assert _ids_of(owner_id) == [owners_id]
        assert [
            _attribute(entry, "action")
            for entry in _passkey_entries(companion_row(after))
        ] == [_remove_url(companion_id, kept_id)]
        # Her banner, as when she removes one herself; the owner's is untouched.
        assert _banner_of(companion_id) == SecurityEventKind.PASSKEY_REMOVED.value
        assert _banner_of(owner_id) == owners_banner
        # The owner pressed it; the passkey was hers.
        assert [
            (record.user_id, record.passkey_id, record.passkey_user_id)
            for record in caplog.records
            if getattr(record, "event", None) == "passkey_removed"
        ] == [(owner_id, removed_id, companion_id)]

    def test_remove_asks_no_re_confirm(self, auth_client, seed_companion):
        """A stale session still removes, as the owner's Forget of her device does."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        passkey_id, _ = add_committed(seed_companion["user"])
        page = _companions_page(auth_client)
        with auth_client.session_transaction() as session:
            session[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) - _STALE
            ).isoformat()

        response = _remove(auth_client, page, companion_id, passkey_id)

        assert response.status_code == 302
        assert _ids_of(companion_id) == []


class TestTheRefusals:
    """Every door but the owner's own companion and her passkey is one 404."""

    def test_another_owners_companion_is_404(
        self, auth_client, seed_companion, seed_second_user,
    ):
        """Refused, her passkey kept; the owner's own companion's URL routes."""
        foreign = another_owners_companion(seed_second_user["user"].id)
        theirs, _ = add_committed(db.session.get(User, foreign))
        companion_id = seed_companion["user"].id
        mine, _ = add_committed(seed_companion["user"])

        response = auth_client.post(_remove_url(foreign, theirs))

        assert response.status_code == 404
        assert _ids_of(foreign) == [theirs]
        # The pair: the same URL, with the owner's own companion and her passkey.
        assert auth_client.post(_remove_url(companion_id, mine)).status_code == 302
        assert _ids_of(companion_id) == []

    def test_a_user_who_is_not_a_companion_is_404(
        self, auth_client, seed_user, seed_companion,
    ):
        """The owner's own id names no companion: refused, his passkey kept."""
        owner_id = seed_user["user"].id
        his, _ = add_committed(seed_user["user"])
        companion_id = seed_companion["user"].id
        hers, _ = add_committed(seed_companion["user"])

        response = auth_client.post(_remove_url(owner_id, his))

        assert response.status_code == 404
        assert _ids_of(owner_id) == [his]
        # The pair.
        assert auth_client.post(_remove_url(companion_id, hers)).status_code == 302
        assert _ids_of(companion_id) == []

    def test_another_users_passkey_and_a_missing_one_are_one_404(
        self, auth_client, seed_user, seed_companion,
    ):
        """Both refused alike, nothing removed and no banner raised; hers still routes.

        "Alike" is the two pages with their CSRF tokens blanked: each token is
        signed with the second it is minted in (finding plan#3244).
        """
        companion_id, owner_id = seed_companion["user"].id, seed_user["user"].id
        hers, _ = add_committed(seed_companion["user"])
        his, _ = add_committed(seed_user["user"])
        missing = db.session.execute(select(func.max(Passkey.id))).scalar_one() + 1
        banners = (_banner_of(owner_id), _banner_of(companion_id))

        his_response = auth_client.post(_remove_url(companion_id, his))
        missing_response = auth_client.post(_remove_url(companion_id, missing))

        assert his_response.status_code == 404
        assert missing_response.status_code == 404
        assert without_csrf_tokens(
            his_response.get_data(as_text=True),
        ) == without_csrf_tokens(missing_response.get_data(as_text=True))
        assert _ids_of(owner_id) == [his]
        assert _ids_of(companion_id) == [hers]
        assert (_banner_of(owner_id), _banner_of(companion_id)) == banners
        # The pair: the same URL, with her own passkey, routes and removes.
        assert auth_client.post(_remove_url(companion_id, hers)).status_code == 302
        assert _ids_of(companion_id) == []

    def test_a_companion_cannot_reach_the_owners_remove(
        self, auth_client, companion_client, seed_companion,
    ):
        """Her own passkey by the owner's URL: 404 to her, a removal to the owner."""
        companion_id = seed_companion["user"].id
        hers, _ = add_committed(seed_companion["user"])

        response = companion_client.post(_remove_url(companion_id, hers))

        assert response.status_code == 404
        assert _ids_of(companion_id) == [hers]
        # The pair: the owner's browser, the same URL.
        assert auth_client.post(_remove_url(companion_id, hers)).status_code == 302
        assert _ids_of(companion_id) == []


class TestTheResetRemovesThem:
    """The owner's reset of her two-factor removes her passkeys and her add in flight."""

    def test_the_confirmation_names_her_passkeys_while_she_holds_any(
        self, auth_client, seed_companion,
    ):
        """The reset's confirmation says her passkeys are removed.

        Without any, it says what it said before a companion could hold one
        (``test_companion_two_factor_reset.py``, TestTheButton).
        """
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        add_committed(seed_companion["user"])

        page = _companions_page(auth_client)

        assert (
            'data-confirm="Reset two-factor authentication for Companion User? '
            "They are signed out everywhere, their passkeys are removed, their "
            "trusted devices are forgotten, and they set it up again with their "
            'authenticator app the next time they use Shekel."'
        ) in companion_row(page)

    def test_the_reset_removes_her_passkeys_and_her_add_and_no_one_elses(
        self, auth_client, seed_user, seed_companion,
    ):
        """Hers are gone with her code; the owner's passkey stays; her row lists none."""
        companion_id, owner_id = seed_companion["user"].id, seed_user["user"].id
        enable_mfa(db.session, companion_id)
        add_committed(seed_companion["user"])
        add_committed(seed_companion["user"])
        his, _ = add_committed(seed_user["user"])
        passkey_service.registration_options(seed_companion["user"], origin=ORIGIN)
        db.session.commit()
        page = _companions_page(auth_client)
        url = _reset_url(companion_id)

        response = auth_client.post(url, data=rendered_form_controls(page, url))

        assert response.status_code == 302
        assert _ids_of(companion_id) == []
        assert db.session.execute(
            select(func.count()).select_from(PasskeyAdd)
            .where(PasskeyAdd.user_id == companion_id)
        ).scalar_one() == 0
        assert two_factor_row_of(companion_id).is_enabled is False
        assert _ids_of(owner_id) == [his]
        row = companion_row(_companions_page(auth_client))
        assert _HEADING not in row
        assert "Two-factor not set up" in text_of(row)
