"""Adding a passkey from the Security page (plan#2538, task plan#3486).

Adding asks the device twice, each at a tap of its own: to make the passkey,
then to sign in with it, and only a passkey that so signs is kept (ruling
plan#3525), so it is listed only once the second succeeds.  The add has a page
of its own behind the re-confirm, and every one of its requests is behind it
too.  Each case runs the add as ``passkey.js`` does: it reads the page's add
form, asks the add's options at the URL the form names (:func:`ask_options`),
posts the new passkey and the name the form holds to its
``data-registration-url``, and posts the sign-in to its action
(:func:`post_answer`), each a form the page renders.  The device is
:class:`~tests._passkeys.SoftwareAuthenticator`.

A refusal answers the service's sentence with 422 and two things the page does
next: ``retry`` (the same options ask again) and ``forget`` (no account keeps
the passkey the answer names, so the page tells the device it is kept
nowhere).  The cases grade each against what the database holds: ``forget``
is never true for a passkey an account keeps, nor for an answer naming no
passkey that can be read.
"""

import json
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models.passkey import NAME_MAX_LENGTH, Passkey
from app.models.passkey_add import PasskeyAdd
from app.models.user import User
from app.services.passkey_service import CHALLENGE_LIFETIME, MAX_PASSKEYS
from app.utils.security_events import SecurityEventKind
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from tests._passkeys import (
    ORIGIN,
    T0,
    Clock,
    SoftwareAuthenticator,
    add,
    add_committed,
    ask_options,
    passkey_row,
    post_answer,
)
from tests._form_controls import rendered_form_controls
from tests._seed_owner_fixtures import log_in_seed_user
from tests._trusted_devices import SAFARI_IPHONE

#: The add's page, which the Security page's "Add a Passkey" opens.
_ADD_PAGE = "/security/passkeys/add"

#: How old a fresh-login stamp is made, to need a re-confirm: past
#: ``FRESH_LOGIN_MAX_AGE_MINUTES``'s five.
_STALE = timedelta(minutes=6)

#: The status of every refusal the add's requests answer.
_REFUSED = 422

#: The service's sentences, spelled here as the oracle.
_TOO_MANY = (
    f"An account can hold up to {MAX_PASSKEYS} passkeys. Remove one to add another."
)
_NOT_THE_NEW_ONE = (
    "That was a different passkey. Please try again and choose the one you just made."
)
_TOO_LONG = "That took too long. Please try again."
_TRY_AGAIN = "That passkey could not be checked. Please try again."
_ALREADY_ADDED = "That passkey has already been added."
_NO_NAME = "Give the passkey a name."


class _AddPage(HTMLParser):
    """The add page's form: its three URLs, its name's attributes, and the scripts it loads.

    Attributes:
        action: The add form's ``action``, where its sign-in posts, or
            ``None`` if there is none.
        options_url: Its ``data-options-url``.
        registration_url: Its ``data-registration-url``.
        name: The attributes of its ``name`` input.
        actions: The ``data-action`` of each button inside it.
        scripts: The path of each ``<script src>`` the page loads.
    """

    def __init__(self):
        super().__init__()
        self.action = None
        self.options_url = None
        self.registration_url = None
        self.name = None
        self.actions = []
        self.scripts = []
        self._inside = False

    def handle_starttag(self, tag, attrs):
        """Read the add form's URLs, its name and buttons, and each script's source."""
        attributes = dict(attrs)
        if tag == "form" and "data-passkey-add" in attributes:
            self._inside = True
            self.action = attributes.get("action")
            self.options_url = attributes.get("data-options-url")
            self.registration_url = attributes.get("data-registration-url")
        elif self._inside and tag == "input" and attributes.get("name") == "name":
            self.name = attributes
        elif self._inside and tag == "button":
            self.actions.append(attributes.get("data-action"))
        elif tag == "script" and attributes.get("src"):
            # The path alone: each script's URL carries its version's query.
            self.scripts.append(urlsplit(attributes["src"]).path)

    def handle_endtag(self, tag):
        """Leave the add form."""
        if tag == "form":
            self._inside = False


def _page(client, **request):
    """GET the add page; assert it renders the add form; return its text and form."""
    response = client.get(_ADD_PAGE, **request)
    assert response.status_code == 200, response.status_code
    page = response.get_data(as_text=True)
    form = _AddPage()
    form.feed(page)
    assert form.action is not None, "the add page renders no add form"
    return page, form


def _register(client, authenticator, *, typed=None, **create):
    """Open the add page, ask its options and post the passkey *authenticator* makes.

    Args:
        client: The browser adding.
        authenticator: The device.
        typed: What the person typed over the rendered controls (the name).
        **create: Passed to :meth:`SoftwareAuthenticator.create`.

    Returns:
        The page's text, its form, and the registration's response.
    """
    page, form = _page(client)
    options = ask_options(client, page, form)
    response = post_answer(
        client, page, form, authenticator.create(options, **create),
        action=form.registration_url, typed=typed,
    )
    return page, form, response


def _add(client, authenticator, *, typed=None):
    """Run the whole add through the page; return the keeping's response."""
    page, form, registered = _register(client, authenticator, typed=typed)
    assert registered.status_code == 200, registered.get_json()
    return post_answer(client, page, form, authenticator.get(registered.get_json()))


def _ask_refused(client, page, form):
    """Ask the add's options as passkey.js does, its form token sent; return the response."""
    token = rendered_form_controls(page, form.action)["csrf_token"][0]
    return client.post(form.options_url, headers={"X-CSRFToken": token})


def _refusal(response):
    """Assert *response* is a refusal of the add; return its JSON."""
    assert response.status_code == _REFUSED, response.status_code
    return response.get_json()


def _make_stale(client):
    """Age the browser's fresh-login stamp past the window, so a step-up asks again."""
    with client.session_transaction() as session:
        session[FRESH_LOGIN_AT_KEY] = (datetime.now(timezone.utc) - _STALE).isoformat()


def _sent_to_re_confirm(response):
    """Assert *response* sends the browser to the re-confirm."""
    assert response.status_code == 302, response.status_code
    assert urlsplit(response.headers["Location"]).path == "/reauth"


def _passkeys_of(user_id):
    """Return the names of *user_id*'s passkeys, read afresh."""
    return list(db.session.execute(
        select(Passkey.name).where(Passkey.user_id == user_id).order_by(Passkey.id)
    ).scalars())


def _adds():
    """Return every add in flight, committed: the test's own transaction rolled back first.

    The rollback is what tells a committed delete from a flushed one, which
    a rollback would undo.
    """
    db.session.rollback()
    return list(db.session.execute(select(PasskeyAdd)).scalars())


def _added_events(caplog):
    """Return each passkey added that was logged, as ``(user_id, passkey_id)``."""
    return [
        (record.user_id, record.passkey_id) for record in caplog.records
        if getattr(record, "event", None) == "passkey_added"
    ]


def _refusals(caplog):
    """Return each refused step of the add that was logged, as ``(step, refusal)``."""
    return [
        (record.step, record.refusal) for record in caplog.records
        if getattr(record, "event", None) == "passkey_add_refused"
    ]


@pytest.fixture(name="iphone_client")
def _iphone_client(client, seed_user):
    """The seed owner, signed in from an iPhone's Safari, whose every request says so.

    A browser's User-Agent does not change between its requests, and the
    session protection drops a session whose does, so the browser signs in
    with it.  The test client's own (``Werkzeug/...``) names no system.
    """
    # Pylint: ``unused-argument`` -- ``seed_user`` is asked for so the owner
    # exists before the browser signs in as them.
    # pylint: disable=unused-argument
    client.environ_base["HTTP_USER_AGENT"] = SAFARI_IPHONE
    return log_in_seed_user(client)


class TestThePage:
    """The add page: behind the re-confirm, its name suggested, its two steps."""

    def test_the_security_page_opens_it(self, auth_client):
        """The Security page's "Add a Passkey" links the add page."""
        page = auth_client.get("/security").get_data(as_text=True)

        assert f'href="{_ADD_PAGE}"' in page

    def test_it_renders_the_add_form_and_its_script(self, auth_client):
        """The form names its three URLs and its two steps, and the page loads passkey.js."""
        _, form = _page(auth_client)

        assert form.action == "/security/passkeys/add"
        assert form.options_url == "/security/passkeys/add/options"
        assert form.registration_url == "/security/passkeys/add/registration"
        assert form.actions == ["passkey-add", "passkey-add-sign-in"]
        assert "/static/js/passkey.js" in form.scripts

    def test_the_name_asks_what_the_service_asks(self, auth_client):
        """Required, at most the column's length, and not blank: what the service refuses."""
        _, form = _page(auth_client)

        assert "required" in form.name
        assert form.name["maxlength"] == str(NAME_MAX_LENGTH)
        assert form.name["pattern"] == r".*\S.*"

    def test_the_name_is_suggested_from_the_browsers_system(self, iphone_client):
        """An iPhone's browser is offered "iPhone" (the card's own example)."""
        _, form = _page(iphone_client)

        assert form.name["value"] == "iPhone"

    def test_no_name_is_suggested_for_a_system_it_does_not_know(self, auth_client):
        """The test client's header names no system: nothing is suggested rather than a guess."""
        _, form = _page(auth_client)

        assert form.name["value"] == ""

    def test_it_asks_the_re_confirm(self, auth_client):
        """A stale session is sent to re-confirm before the page renders."""
        _make_stale(auth_client)

        _sent_to_re_confirm(auth_client.get(_ADD_PAGE))


class TestAdding:
    """The whole add, through the page, for an owner and a companion alike."""

    def test_an_owner_adds_a_passkey(self, auth_client, seed_user, caplog):
        """Kept with the name the form holds, signed in once, listed, its banner raised."""
        user_id = seed_user["user"].id
        caplog.set_level("INFO")

        response = _add(auth_client, SoftwareAuthenticator(), typed={"name": "Work phone"})

        assert response.status_code == 200, response.get_json()
        assert response.get_json() == {"location": "/security"}
        passkey = db.session.execute(
            select(Passkey).where(Passkey.user_id == user_id)
        ).scalar_one()
        assert passkey.name == "Work phone"
        # Kept by its sign-in (ruling plan#3525): its first use is its adding.
        assert passkey.last_used_at == passkey.created_at
        user = db.session.get(User, user_id)
        db.session.refresh(user)
        assert user.last_security_event_kind == SecurityEventKind.PASSKEY_ADDED.value
        # The banner and the row say one instant.
        assert user.last_security_event_at == passkey.created_at
        assert _added_events(caplog) == [(user_id, passkey.id)]
        page = auth_client.get("/security").get_data(as_text=True)
        assert "Passkey &#34;Work phone&#34; added." in page
        assert "A passkey was added to your account." in page
        assert ">Work phone</div>" in page

    def test_the_suggested_name_is_kept_when_left_as_it_is(self, iphone_client, seed_user):
        """The name the page suggests, posted as it renders, is the one kept."""
        response = _add(iphone_client, SoftwareAuthenticator())

        assert response.get_json() == {"location": "/security"}
        assert _passkeys_of(seed_user["user"].id) == ["iPhone"]

    def test_a_companion_adds_hers(self, companion_client, seed_companion):
        """A companion adds her own, on her own page, and it is listed there."""
        companion_id = seed_companion["user"].id

        response = _add(companion_client, SoftwareAuthenticator(), typed={"name": "Her iPhone"})

        assert response.get_json() == {"location": "/security"}
        assert _passkeys_of(companion_id) == ["Her iPhone"]
        page = companion_client.get("/security").get_data(as_text=True)
        assert ">Her iPhone</div>" in page

    def test_nothing_is_kept_or_listed_until_the_sign_in(self, auth_client, seed_user):
        """After the passkey is made and checked, the account holds none, and none is listed."""
        _, _, registered = _register(
            auth_client, SoftwareAuthenticator(), typed={"name": "Pending"},
        )

        assert registered.status_code == 200
        assert "publicKey" in registered.get_json()
        assert _passkeys_of(seed_user["user"].id) == []
        page = auth_client.get("/security").get_data(as_text=True)
        assert "Pending" not in page
        assert "No passkeys yet." in page

    def test_a_kept_add_is_finished_once(self, auth_client, seed_user):
        """The keeping's answer posted again keeps nothing more."""
        authenticator = SoftwareAuthenticator()
        page, form, registered = _register(auth_client, authenticator, typed={"name": "Once"})
        answer = authenticator.get(registered.get_json())
        assert post_answer(auth_client, page, form, answer).status_code == 200

        again = _refusal(post_answer(auth_client, page, form, answer))

        # The replay names the passkey the first keeping kept, so the page is
        # never told to have the device forget it.
        assert again == {"message": _TOO_LONG, "retry": False, "forget": False}
        assert _passkeys_of(seed_user["user"].id) == ["Once"]

    def test_a_sign_in_past_its_life_tells_the_page_to_forget_the_passkey(
        self, auth_client, seed_user, monkeypatch,
    ):
        """The keeping's own refusal of a lapsed sign-in: no account keeps it, so forget it."""
        clock = Clock(monkeypatch)
        authenticator = SoftwareAuthenticator()
        page, form, registered = _register(auth_client, authenticator, typed={"name": "Slow"})
        clock.at(T0 + CHALLENGE_LIFETIME + timedelta(seconds=1))

        refusal = _refusal(post_answer(
            auth_client, page, form, authenticator.get(registered.get_json()),
        ))

        assert refusal == {"message": _TOO_LONG, "retry": False, "forget": True}
        assert _passkeys_of(seed_user["user"].id) == []
        assert _adds() == []


class TestEveryStepAsksTheReConfirm:
    """The options, the registration and the keeping each sit behind ``@fresh_login_required()``."""

    def test_the_options(self, auth_client):
        """A stale session's options are refused, and no add is begun."""
        page, form = _page(auth_client)
        _make_stale(auth_client)

        _sent_to_re_confirm(_ask_refused(auth_client, page, form))
        assert _adds() == []

    def test_the_registration(self, auth_client):
        """A stale session's new passkey is refused, and the add is not moved on."""
        authenticator = SoftwareAuthenticator()
        page, form = _page(auth_client)
        options = ask_options(auth_client, page, form)
        _make_stale(auth_client)

        _sent_to_re_confirm(post_answer(
            auth_client, page, form, authenticator.create(options),
            action=form.registration_url, typed={"name": "Late"},
        ))
        (add_in_flight,) = _adds()
        assert add_in_flight.credential_id is None

    def test_the_keeping(self, auth_client, seed_user):
        """A stale session's sign-in keeps nothing."""
        authenticator = SoftwareAuthenticator()
        page, form, registered = _register(auth_client, authenticator, typed={"name": "Late"})
        _make_stale(auth_client)

        _sent_to_re_confirm(post_answer(
            auth_client, page, form, authenticator.get(registered.get_json()),
        ))
        assert _passkeys_of(seed_user["user"].id) == []


class TestRefusals:
    """Each refusal answers the service's sentence, and what the page does next."""

    def test_the_eleventh_is_refused_with_the_services_message(
        self, auth_client, seed_user, monkeypatch, caplog,
    ):
        """An account holding ten is refused at the options, before the device is asked."""
        user = seed_user["user"]
        clock = Clock(monkeypatch)
        for number in range(MAX_PASSKEYS):
            add(clock, user, name=f"Key {number}")
        db.session.commit()
        page, form = _page(auth_client)

        refusal = _refusal(_ask_refused(auth_client, page, form))

        assert refusal == {"message": _TOO_MANY, "retry": False, "forget": False}
        assert _refusals(caplog) == [("options", "too_many")]
        assert _adds() == []

    def test_another_passkey_answering_leaves_the_add_for_a_retry(self, auth_client, seed_user):
        """The device's older passkey answers: the add waits, and the new one then keeps it."""
        _, older = add_committed(seed_user["user"])
        newer = SoftwareAuthenticator()
        page, form, registered = _register(auth_client, newer, typed={"name": "Newer"})
        sign_in = registered.get_json()

        refusal = _refusal(post_answer(auth_client, page, form, older.get(sign_in)))

        assert refusal == {"message": _NOT_THE_NEW_ONE, "retry": True, "forget": False}
        kept = post_answer(auth_client, page, form, newer.get(sign_in))
        assert kept.get_json() == {"location": "/security"}
        assert _passkeys_of(seed_user["user"].id) == ["iPhone", "Newer"]

    def test_a_registration_refused_tells_the_page_to_forget_the_passkey(
        self, auth_client, seed_user, caplog,
    ):
        """Another site's origin is refused; the device's new passkey is kept nowhere."""
        _, _, registered = _register(
            auth_client, SoftwareAuthenticator(), typed={"name": "Elsewhere"},
            origin="https://elsewhere.test",
        )

        refusal = _refusal(registered)

        assert refusal == {"message": _TRY_AGAIN, "retry": False, "forget": True}
        assert _refusals(caplog) == [("registration", "not_verified")]
        assert _passkeys_of(seed_user["user"].id) == []

    def test_a_passkey_an_account_keeps_is_never_forgotten(self, auth_client, seed_user):
        """A device answering with the id of a kept passkey: refused, and not to be forgotten."""
        passkey_id, _ = add_committed(seed_user["user"])
        held = SoftwareAuthenticator(credential_id=passkey_row(passkey_id).credential_id)

        _, _, registered = _register(auth_client, held, typed={"name": "Twice"})

        assert _refusal(registered) == {
            "message": _ALREADY_ADDED, "retry": False, "forget": False,
        }

    def test_a_name_the_service_refuses_is_said(self, auth_client, seed_user):
        """A blank name past the page's own check is refused in the service's words."""
        _, _, registered = _register(
            auth_client, SoftwareAuthenticator(), typed={"name": "   "},
        )

        assert _refusal(registered) == {"message": _NO_NAME, "retry": False, "forget": True}
        assert _passkeys_of(seed_user["user"].id) == []

    def test_an_expired_add_is_refused_and_its_delete_committed(
        self, auth_client, monkeypatch, caplog,
    ):
        """A registration past the challenge's life: refused, and the dead add is gone for good."""
        clock = Clock(monkeypatch)
        authenticator = SoftwareAuthenticator()
        page, form = _page(auth_client)
        options = ask_options(auth_client, page, form)
        assert len(_adds()) == 1
        clock.at(T0 + CHALLENGE_LIFETIME + timedelta(seconds=1))

        refusal = _refusal(post_answer(
            auth_client, page, form, authenticator.create(options),
            action=form.registration_url, typed={"name": "Slow"},
        ))

        assert refusal == {"message": _TOO_LONG, "retry": False, "forget": True}
        assert _refusals(caplog) == [("registration", "expired")]
        assert _adds() == []

    def test_a_begun_add_replaces_the_one_in_flight(self, auth_client, seed_user):
        """Options asked again begin a new add: the first one's passkey completes nothing."""
        first = SoftwareAuthenticator()
        page, form = _page(auth_client)
        first_options = ask_options(auth_client, page, form)
        ask_options(auth_client, page, form)

        refusal = _refusal(post_answer(
            auth_client, page, form, first.create(first_options),
            action=form.registration_url, typed={"name": "First"},
        ))

        assert refusal == {"message": _TRY_AGAIN, "retry": False, "forget": True}
        assert _passkeys_of(seed_user["user"].id) == []


@pytest.mark.parametrize("given", ["", json.dumps({"type": "public-key"})])
def test_an_answer_that_is_not_a_passkey_is_refused(auth_client, given):
    """An empty or shapeless answer is refused, and names no passkey the page may forget."""
    page, form = _page(auth_client)
    ask_options(auth_client, page, form)

    refusal = _refusal(post_answer(
        auth_client, page, form, given, action=form.registration_url,
        typed={"name": "Nothing"},
    ))

    assert refusal == {"message": _TRY_AGAIN, "retry": False, "forget": False}
