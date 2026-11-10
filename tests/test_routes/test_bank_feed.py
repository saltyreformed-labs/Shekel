"""The bank feed panel's four doors, driven the way a browser drives them.

Plan step ``bank_import:X-f6b-2``, leaf (3c); rulings **R-BI12**, **R-BI26**,
**R-BI27**, **R-BI28**, **R-BI29**; ledger row **BI-499**.  Bridge is a fake at
``requests.post`` / ``requests.get`` (the service tests' spy, shared, behind
the same net: every fake URL names Bridge's real host because the pin admits
no other, so an unstubbed request fails at ``Session.send`` rather than
leaving), and every form is POSTED AS THE PANEL RENDERS IT: the controls are
scraped off the rendered panel with
:func:`tests.test_routes._statement_forms.form_fields`, because a
hand-written payload agrees with a template about a mistake as readily as
about the truth.

**The claims these tests grade that the code's own words cannot:**

* **the claim commits BEFORE the listing** (R-BI27): the failing-fetch
  control connects a feed whose listing then fails, and the feed row is
  COMMITTED, the mappings empty, and the panel offers the press again;
* **every refusal is logged with its class** (BI-499): each refused door
  leaves a ``statement_door_refused`` record naming the class;
* **the credential never reaches a record or a body**: with Bridge's
  ``HTTPError`` text carrying the access URL's password, every captured
  record and every response body is asserted free of it;
* **the doors are the OWNER's and answer on the page they were pressed
  on**: a companion is 404, another owner's page is 404, a loan's page is
  404 -- and each success case proves the URL still routes, so an
  ownership 404 cannot be a URL-map 404 in disguise.
"""

import base64
import logging
import re

import pytest
from werkzeug.datastructures import MultiDict

from app.models.bank_feed import BankFeed
from app.models.statement_import import AccountExternalIdentity
from app.utils.log_events import (
    EVT_BANK_FEED_ACCOUNTS_MAPPED,
    EVT_BANK_FEED_CLAIMED,
    EVT_BANK_FEED_DISCONNECTED,
    EVT_STATEMENT_DOOR_REFUSED,
)
from tests.test_routes._statement_forms import form_fields
from tests.test_services.test_bank_feed import (
    _ACCESS_URL,
    _CHECKING,
    _CLAIM_URL,
    _MORTGAGE,
    _SECRET,
    _SETUP_TOKEN,
    _SHARE,
    _FakeResponse,
    _listing_body,
    block_real_requests,
)

_CLAIM = "/accounts/feed/claim"
_LIST = "/accounts/feed/accounts"
_MAP = "/accounts/feed/map"
_DISCONNECT = "/accounts/feed/disconnect"


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch):
    """Every test in this module runs behind the service tests' net."""
    block_real_requests(monkeypatch)


@pytest.fixture
def bridge(monkeypatch):
    """Bridge, faked and spied (the service tests' fake, shared)."""
    # Pylint: ``import-outside-toplevel`` -- the fixture's class is a test
    # helper of the sibling module, reached here rather than re-spelled.
    from tests.test_services.test_bank_feed import _Bridge  # pylint: disable=import-outside-toplevel
    return _Bridge(monkeypatch)


def _page(client, account_id):
    """GET the statements page; return its body."""
    response = client.get(f"/accounts/{account_id}/statements")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _panel_of(body):
    """Return the feed panel's markup from a page or a fragment."""
    start = body.index('id="bank-feed-panel"')
    start = body.rindex("<div", 0, start)
    return body[start:]


def _hx_post(client, path, fields):
    """POST *fields* the way htmx posts a panel form."""
    return client.post(
        path, data=MultiDict(fields), headers={"HX-Request": "true"},
    )


def _post(client, path, fields):
    """POST *fields* as a plain form and follow the redirect."""
    return client.post(path, data=MultiDict(fields), follow_redirects=True)


def _connect(client, seed_user, token=_SETUP_TOKEN):
    """Paste *token* on the seeded account's page; return the response."""
    fields = form_fields(_page(client, seed_user["account"].id), _CLAIM,
                         attribute="hx-post")
    fields = [
        (name, token if name == "setup_token" else value)
        for name, value in fields
    ]
    return _hx_post(client, _CLAIM, fields)


def _records(caplog, event):
    """Return the captured records carrying *event*."""
    return [
        record for record in caplog.records
        if getattr(record, "event", None) == event
    ]


def _no_secret_anywhere(caplog, *bodies):
    """Assert the password is in no captured record and no body."""
    for record in caplog.records:
        rendered = record.getMessage() + repr(vars(record))
        assert _SECRET not in rendered, rendered
    for body in bodies:
        assert _SECRET not in body


class TestThePageCarriesThePanel:
    """The statements page renders the panel in its not-connected state."""

    def test_the_paste_form_posts_the_page_and_a_token(
        self, auth_client, seed_user,
    ):
        """What the form submits: the page's account and the token, nothing
        else, to the claim door by htmx."""
        fields = form_fields(
            _page(auth_client, seed_user["account"].id), _CLAIM,
            attribute="hx-post",
        )

        assert dict(fields) == {
            "account_id": str(seed_user["account"].id),
            "setup_token": "",
        }

    def test_a_companion_sees_no_statements_page_at_all(
        self, companion_client, seed_user,
    ):
        """The page is owner-only already; the panel inherits that."""
        response = companion_client.get(
            f"/accounts/{seed_user['account'].id}/statements",
        )
        assert response.status_code == 404


class TestTheClaimDoor:
    """Paste a token: the feed is stored, then Bridge's list opens."""

    def test_a_claim_stores_the_feed_and_opens_the_mapping_form(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """The whole happy path in one press, graded on the response, the
        database, the log and the absence of the credential everywhere."""
        with caplog.at_level(logging.INFO):
            response = _connect(auth_client, seed_user)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'id="bank-feed-panel"' in body
        assert "Connected" in body
        for external_id in (_CHECKING, _SHARE, _MORTGAGE):
            assert external_id in body
        assert "Checking (3820)" in body
        assert "Not mapped" in body
        assert seed_user["account"].name in body
        db.session.expire_all()
        feed = db.session.query(BankFeed).one()
        assert feed.user_id == seed_user["user"].id
        assert len(_records(caplog, EVT_BANK_FEED_CLAIMED)) == 1
        _no_secret_anywhere(caplog, body)

    def test_the_mapping_form_posts_every_bridge_account_paired(
        self, auth_client, seed_user, bridge,
    ):
        """A browser submits every control the form renders: one
        ``external_id`` and one ``target_account_id`` per Bridge account,
        in document order, plus the page's account."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)

        fields = form_fields(body, _MAP)

        assert [v for n, v in fields if n == "external_id"] == [
            _CHECKING, _SHARE, _MORTGAGE,
        ]
        assert [v for n, v in fields if n == "target_account_id"] == [
            "", "", "",
        ]
        assert ("account_id", str(seed_user["account"].id)) in fields

    def test_an_unreadable_token_is_refused_and_logged(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """A designed 400 carrying the paste form and the sentence; nothing
        stored; and BI-499's record, naming the class."""
        with caplog.at_level(logging.WARNING):
            response = _connect(auth_client, seed_user, token="not a token")
        body = response.get_data(as_text=True)

        assert response.status_code == 400
        assert "does not read as a SimpleFIN setup token" in body
        assert 'name="setup_token"' in body
        assert db.session.query(BankFeed).count() == 0
        assert bridge.posts == []
        refused = _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        assert [record.refusal_class for record in refused] == [
            "SetupTokenUnreadable",
        ]
        assert refused[0].levelno == logging.WARNING

    def test_bridge_refusing_the_claim_stores_nothing_and_names_no_url(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Bridge answers 403 (a token already claimed): the sentence names
        the status, the log names the class, and the claim URL is in
        neither."""
        bridge.post_answer = _FakeResponse(_CLAIM_URL, status_code=403)
        with caplog.at_level(logging.WARNING):
            response = _connect(auth_client, seed_user)
        body = response.get_data(as_text=True)

        assert response.status_code == 400
        assert "HTTP 403" in body
        assert "DEMO-TOKEN-1234" not in body
        assert db.session.query(BankFeed).count() == 0
        refused = _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        assert [record.refusal_class for record in refused] == ["BridgeRefused"]
        assert (refused[0].status, refused[0].error_class) == (403, "HTTPError")
        assert not hasattr(refused[0], "sentence")

    def test_a_failing_listing_after_the_claim_keeps_the_feed(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """FIRING CONTROL for ruling R-BI27's order: the claim COMMITS
        before the listing.  Bridge answers the listing with 401 whose
        text carries the access URL's password; the feed is stored, no
        mapping exists, the panel says so and offers "Map accounts", the
        listing's refusal is logged with its class, and the password is
        nowhere."""
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts?version=2&balances-only=1",
            status_code=401,
        )
        with caplog.at_level(logging.INFO):
            response = _connect(auth_client, seed_user)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Connected" in body
        assert "HTTP 401" in body
        assert "to ask Bridge again" in body
        assert form_fields(body, _LIST, attribute="hx-post")
        db.session.expire_all()
        assert db.session.query(BankFeed).count() == 1
        assert db.session.query(AccountExternalIdentity).count() == 0
        assert len(_records(caplog, EVT_BANK_FEED_CLAIMED)) == 1
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["BridgeRefused"]
        _no_secret_anywhere(caplog, body)

    def test_a_second_claim_while_one_stands_is_refused_before_bridge(
        self, auth_client, seed_user, bridge, caplog,
    ):
        """The spy counts ONE post across two presses.

        The second press comes from a STALE page -- a tab opened before the
        first claim, which is the only way a browser reaches the paste form
        while a feed stands -- so the same scraped form is posted twice.
        """
        fields = form_fields(
            _page(auth_client, seed_user["account"].id), _CLAIM,
            attribute="hx-post",
        )
        fields = [
            (name, _SETUP_TOKEN if name == "setup_token" else value)
            for name, value in fields
        ]
        _hx_post(auth_client, _CLAIM, fields)
        with caplog.at_level(logging.WARNING):
            response = _hx_post(auth_client, _CLAIM, fields)

        assert response.status_code == 400
        assert "already connected" in response.get_data(as_text=True)
        assert len(bridge.posts) == 1
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["FeedAlreadyConnected"]

    def test_a_token_decoding_to_a_malformed_host_is_refused_not_a_500(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """``urlsplit`` raises ``ValueError`` on ``https://[x``; a paste is
        a paste, so it is the same designed 400 as any unreadable token
        (named by adversarial review 2026-09-20: it escaped as a 500 htmx
        could not swap)."""
        with caplog.at_level(logging.WARNING):
            response = _connect(
                auth_client, seed_user, token=_setup_token_for("https://[x"),
            )

        assert response.status_code == 400
        assert "does not read as a SimpleFIN setup token" in (
            response.get_data(as_text=True)
        )
        assert db.session.query(BankFeed).count() == 0
        assert bridge.posts == []
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["SetupTokenUnreadable"]

    def test_a_token_naming_a_foreign_host_is_refused_and_logged(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Ruling R-BI28 at the door: a designed 400 carrying the paste form
        and a sentence naming simplefin.org and not the pasted host; no
        request, nothing stored; and BI-499's record names the pin's own
        class."""
        with caplog.at_level(logging.WARNING):
            response = _connect(
                auth_client, seed_user,
                token=_setup_token_for("https://evil.invalid/simplefin/claim/X"),
            )
        body = response.get_data(as_text=True)

        assert response.status_code == 400
        assert "somewhere other than simplefin.org" in body
        assert "evil.invalid" not in body
        assert 'name="setup_token"' in body
        assert db.session.query(BankFeed).count() == 0
        assert bridge.posts == []
        refused = _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        assert [record.refusal_class for record in refused] == [
            "BridgeHostRefused",
        ]
        assert refused[0].levelno == logging.WARNING

    def test_a_claim_answered_with_a_foreign_access_url_stores_nothing(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Ruling R-BI28's other door: the claim answered 200 with a
        credential on another server.  Refused as ``BridgeRefused`` with
        ``ForeignHost`` in the record, nothing stored, no listing asked of
        that server, the paste form offered again, and neither the host nor
        the password anywhere."""
        bridge.post_answer = _FakeResponse(
            _CLAIM_URL, text=f"https://alice:{_SECRET}@evil.invalid/simplefin",
        )
        with caplog.at_level(logging.WARNING):
            response = _connect(auth_client, seed_user)
        body = response.get_data(as_text=True)

        assert response.status_code == 400
        assert "a server other than simplefin.org" in body
        assert "evil.invalid" not in body
        assert 'name="setup_token"' in body
        assert db.session.query(BankFeed).count() == 0
        assert bridge.gets == []
        refused = _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        assert [record.refusal_class for record in refused] == ["BridgeRefused"]
        assert (refused[0].status, refused[0].error_class) == (200, "ForeignHost")
        _no_secret_anywhere(caplog, body)

    def test_a_redirected_claim_is_refused_and_logged(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Ruling R-BI29 at the door: Bridge answers the claim with a 302;
        a designed 400 saying so, nothing stored, the paste form offered
        again, and BI-499's record carrying ``(302, "Redirect")``."""
        bridge.post_answer = _FakeResponse(_CLAIM_URL, status_code=302)
        with caplog.at_level(logging.WARNING):
            response = _connect(auth_client, seed_user)
        body = response.get_data(as_text=True)

        assert response.status_code == 400
        assert "redirect (HTTP 302)" in body
        assert 'name="setup_token"' in body
        assert db.session.query(BankFeed).count() == 0
        assert bridge.gets == []
        refused = _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        assert [record.refusal_class for record in refused] == ["BridgeRefused"]
        assert (refused[0].status, refused[0].error_class) == (302, "Redirect")
        _no_secret_anywhere(caplog, body)

    def test_another_owners_page_is_a_404(
        self, auth_client, second_user, bridge,
    ):
        """The page named in the form is gated as the page itself is."""
        response = _hx_post(auth_client, _CLAIM, [
            ("account_id", str(second_user["account"].id)),
            ("setup_token", _SETUP_TOKEN),
        ])

        assert response.status_code == 404
        assert bridge.posts == []

    def test_a_companion_is_a_404(self, companion_client, seed_user, bridge):
        """Owner-only, by role."""
        response = _hx_post(companion_client, _CLAIM, [
            ("account_id", str(seed_user["account"].id)),
            ("setup_token", _SETUP_TOKEN),
        ])

        assert response.status_code == 404
        assert bridge.posts == []


class TestTheListingPress:
    """"Map accounts": a live read of Bridge, answered with the panel."""

    def test_it_opens_the_mapping_form_with_the_current_choice_selected(
        self, auth_client, seed_user, bridge,
    ):
        """After a mapping is saved, the form re-opens with it selected --
        read off the option a browser would submit."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        fields = form_fields(body, _MAP)
        fields = _choose(fields, {_CHECKING: str(seed_user["account"].id)})
        _post(auth_client, _MAP, fields)
        page = _page(auth_client, seed_user["account"].id)
        press = form_fields(page, _LIST, attribute="hx-post")

        response = _hx_post(auth_client, _LIST, press)
        reopened = form_fields(response.get_data(as_text=True), _MAP)

        assert response.status_code == 200
        assert dict(
            zip(
                [v for n, v in reopened if n == "external_id"],
                [v for n, v in reopened if n == "target_account_id"],
            )
        ) == {
            _CHECKING: str(seed_user["account"].id), _SHARE: "", _MORTGAGE: "",
        }
        assert len(bridge.gets) == 2

    def test_a_stored_url_the_key_cannot_read_is_refused_not_a_500(
        self, auth_client, db, seed_user, bridge, caplog, monkeypatch,
    ):
        """The rotation hazard: the key the row was written under is gone.
        A designed refusal on the panel with its class logged, where a bare
        ``decrypt_secret`` was a 500 (named by adversarial review
        2026-09-20)."""
        # Pylint: ``import-outside-toplevel`` -- a fresh key minted here.
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
        _connect(auth_client, seed_user)
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.delenv("FIELD_ENCRYPTION_KEY_OLD", raising=False)

        with caplog.at_level(logging.WARNING):
            response = _hx_post(auth_client, _LIST, [
                ("account_id", str(seed_user["account"].id)),
            ])
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "cannot be read under the current encryption key" in body
        assert form_fields(body, _DISCONNECT)
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["FeedUnreadable"]

    def test_a_companion_is_a_404(self, companion_client, seed_user, bridge):
        """Owner-only, by role; the URL still routes (the cases above)."""
        response = _hx_post(companion_client, _LIST, [
            ("account_id", str(seed_user["account"].id)),
        ])
        assert response.status_code == 404
        assert bridge.gets == []

    def test_without_a_feed_it_says_so(
        self, auth_client, seed_user, bridge, caplog,
    ):
        """No feed: the panel's not-connected state, with the sentence,
        and BI-499's record."""
        with caplog.at_level(logging.WARNING):
            response = _hx_post(auth_client, _LIST, [
                ("account_id", str(seed_user["account"].id)),
            ])

        assert response.status_code == 200
        assert "No bank feed is connected" in response.get_data(as_text=True)
        assert bridge.gets == []
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["NoFeedConnected"]


def _choose(fields, choices):
    """Return *fields* with each Bridge account's select set per *choices*.

    Args:
        fields: The scraped mapping form, ``(name, value)`` pairs in order.
        choices: Bridge id -> the option value to submit (``""`` unmapped).

    Returns:
        The pairs with every ``target_account_id`` following an
        ``external_id`` in *choices* replaced.
    """
    chosen = []
    current = None
    for name, value in fields:
        if name == "external_id":
            current = value
        if name == "target_account_id" and current in choices:
            value = choices[current]
        chosen.append((name, value))
    return chosen


class TestTheMappingDoor:
    """Save the mapping: a redirect, a receipt, the rows."""

    def test_saving_two_choices_declares_two_rows_and_receipts_them(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Posted as the form renders it, with two selects changed."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        # Pylint: ``import-outside-toplevel`` -- the sibling's second-account
        # builder, reached rather than re-spelled.
        from tests.test_services.test_statement_import.test_record import (  # pylint: disable=import-outside-toplevel
            _second_account,
        )
        second = _second_account(db, seed_user)
        db.session.commit()
        fields = _choose(form_fields(body, _MAP), {
            _CHECKING: str(seed_user["account"].id),
            _SHARE: str(second.id),
        })

        with caplog.at_level(logging.INFO):
            response = _post(auth_client, _MAP, fields)
        page = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Saved the mapping: mapped 2 Bridge account(s)." in page
        db.session.expire_all()
        declared = {
            row.external_account_id: row.account_id
            for row in db.session.query(AccountExternalIdentity)
        }
        assert declared == {
            _CHECKING: seed_user["account"].id, _SHARE: second.id,
        }
        assert all(
            row.feed_id is not None
            for row in db.session.query(AccountExternalIdentity)
        )
        mapped = _records(caplog, EVT_BANK_FEED_ACCOUNTS_MAPPED)
        assert [(r.declared, r.moved, r.cleared, r.unchanged) for r in mapped] == [
            (2, 0, 0, 1),
        ]
        assert _CHECKING in _panel_of(page)
        assert "Second Checking" in _panel_of(page)

    def test_saving_it_again_unchanged_says_nothing_changed(
        self, auth_client, seed_user, bridge,
    ):
        """The receipt tells a no-op from a save."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        fields = _choose(form_fields(body, _MAP), {
            _CHECKING: str(seed_user["account"].id),
        })
        _post(auth_client, _MAP, fields)

        response = _post(auth_client, _MAP, fields)

        assert "Nothing changed: the mapping already said this for all 3" in (
            response.get_data(as_text=True)
        )

    def test_a_mapping_bridge_no_longer_lists_is_offered_to_keep_or_clear(
        self, auth_client, db, seed_user, bridge,
    ):
        """M3's shape (adversarial review 2026-09-20): X was declared as
        Checking, then Bridge stopped listing X.  The form renders X's
        standing row beside Bridge's list, so ONE submission can hand
        Checking to Y and clear X -- where a form of Bridge's list alone
        left the owner a unique-key error and a disconnect as the only
        repair."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        fields = _choose(form_fields(body, _MAP), {
            _CHECKING: str(seed_user["account"].id),
        })
        _post(auth_client, _MAP, fields)
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(accounts=[
                {"id": _SHARE, "name": "Share (9361)", "currency": "USD",
                 "balance": "25.80"},
            ]),
        )

        reopened = _hx_post(auth_client, _LIST, [
            ("account_id", str(seed_user["account"].id)),
        ]).get_data(as_text=True)
        rows = form_fields(reopened, _MAP)

        assert "No longer listed by Bridge" in reopened
        assert dict(
            zip(
                [v for n, v in rows if n == "external_id"],
                [v for n, v in rows if n == "target_account_id"],
            )
        ) == {_SHARE: "", _CHECKING: str(seed_user["account"].id)}

        handed = _choose(rows, {
            _SHARE: str(seed_user["account"].id), _CHECKING: "",
        })
        response = _post(auth_client, _MAP, handed)

        assert "mapped 1 Bridge account(s), unmapped 1" in (
            response.get_data(as_text=True)
        )
        db.session.expire_all()
        assert {
            row.external_account_id: row.account_id
            for row in db.session.query(AccountExternalIdentity)
        } == {_SHARE: seed_user["account"].id}

    def test_a_hand_built_payload_omitting_a_standing_row_is_refused_in_words(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """M3's guard: the payload names Checking for Y while X, absent from
        the payload, still holds Checking -- the key would refuse it after
        the write; the door refuses it before, in the owner's words."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        fields = _choose(form_fields(body, _MAP), {
            _CHECKING: str(seed_user["account"].id),
        })
        _post(auth_client, _MAP, fields)

        with caplog.at_level(logging.WARNING):
            response = _post(auth_client, _MAP, [
                ("account_id", str(seed_user["account"].id)),
                ("external_id", _SHARE),
                ("target_account_id", str(seed_user["account"].id)),
            ])

        assert "already declared to be a Bridge account this form did not include" in (
            response.get_data(as_text=True)
        )
        db.session.expire_all()
        assert {
            row.external_account_id
            for row in db.session.query(AccountExternalIdentity)
        } == {_CHECKING}
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["MappingRefused"]

    def test_moving_and_clearing_are_receipted_as_such(
        self, auth_client, db, seed_user, bridge,
    ):
        """The receipt's other two clauses, at the route tier."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        # Pylint: ``import-outside-toplevel`` -- the sibling's builder.
        from tests.test_services.test_statement_import.test_record import (  # pylint: disable=import-outside-toplevel
            _second_account,
        )
        second = _second_account(db, seed_user)
        db.session.commit()
        first = _choose(form_fields(body, _MAP), {
            _CHECKING: str(seed_user["account"].id), _SHARE: str(second.id),
        })
        _post(auth_client, _MAP, first)

        response = _post(auth_client, _MAP, _choose(first, {
            _CHECKING: str(second.id), _SHARE: "",
        }))

        assert "moved 1 to a different account, unmapped 1" in (
            response.get_data(as_text=True)
        )

    def test_a_companion_is_a_404(self, companion_client, seed_user):
        """Owner-only, by role; the URL still routes (the cases above)."""
        response = companion_client.post(_MAP, data=MultiDict([
            ("account_id", str(seed_user["account"].id)),
            ("external_id", _CHECKING),
            ("target_account_id", ""),
        ]))
        assert response.status_code == 404

    def test_a_tampered_choice_is_refused_and_logged(
        self, auth_client, db, seed_user, second_user, bridge, caplog,
    ):
        """Another owner's account id submitted by hand: the service's
        refusal, flashed, logged with its class; nothing written."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        fields = _choose(form_fields(body, _MAP), {
            _CHECKING: str(second_user["account"].id),
        })

        with caplog.at_level(logging.WARNING):
            response = _post(auth_client, _MAP, fields)

        assert "not one of your cash accounts" in response.get_data(as_text=True)
        assert db.session.query(AccountExternalIdentity).count() == 0
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["MappingRefused"]

    def test_an_unpaired_submission_is_refused_by_the_schema(
        self, auth_client, db, seed_user, bridge,
    ):
        """Two ids, one choice: the form did not pair them."""
        _connect(auth_client, seed_user)

        response = _post(auth_client, _MAP, [
            ("account_id", str(seed_user["account"].id)),
            ("external_id", _CHECKING),
            ("external_id", _SHARE),
            ("target_account_id", str(seed_user["account"].id)),
        ])

        assert "did not pair every Bridge account" in (
            response.get_data(as_text=True)
        )
        assert db.session.query(AccountExternalIdentity).count() == 0

    def test_a_loans_page_is_a_404(self, auth_client, db, seed_user, bridge):
        """The page named in the form must be one the statements page
        serves: a loan is not."""
        # Pylint: ``import-outside-toplevel`` -- the shared loan builder.
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel
        _connect(auth_client, seed_user)
        loan = create_loan_account(seed_user, db.session)
        db.session.commit()

        response = auth_client.post(_MAP, data=MultiDict([
            ("account_id", str(loan.id)),
            ("external_id", _CHECKING),
            ("target_account_id", str(seed_user["account"].id)),
        ]))

        assert response.status_code == 404


class TestTheDisconnectDoor:
    """Disconnect: the feed and its declared mappings go; the receipt says so."""

    def test_it_deletes_the_feed_and_the_mappings_and_receipts_both(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Posted as the panel renders the form."""
        body = _connect(auth_client, seed_user).get_data(as_text=True)
        fields = _choose(form_fields(body, _MAP), {
            _CHECKING: str(seed_user["account"].id),
        })
        _post(auth_client, _MAP, fields)
        page = _page(auth_client, seed_user["account"].id)
        press = form_fields(page, _DISCONNECT)

        with caplog.at_level(logging.INFO):
            response = _post(auth_client, _DISCONNECT, press)
        after = response.get_data(as_text=True)

        assert "Disconnected: the stored access URL is deleted." in after
        assert "1 account mapping(s) declared under it went with it" in after
        assert "Revoke the token at Bridge" in after
        db.session.expire_all()
        assert db.session.query(BankFeed).count() == 0
        assert db.session.query(AccountExternalIdentity).count() == 0
        assert [
            record.mappings_removed
            for record in _records(caplog, EVT_BANK_FEED_DISCONNECTED)
        ] == [1]
        assert 'name="setup_token"' in _panel_of(after)

    def test_without_a_feed_it_is_refused_and_logged(
        self, auth_client, seed_user, caplog,
    ):
        """Nothing to disconnect: the sentence, and BI-499's record."""
        with caplog.at_level(logging.WARNING):
            response = _post(auth_client, _DISCONNECT, [
                ("account_id", str(seed_user["account"].id)),
            ])

        assert "No bank feed is connected" in response.get_data(as_text=True)
        assert [
            record.refusal_class
            for record in _records(caplog, EVT_STATEMENT_DOOR_REFUSED)
        ] == ["NoFeedConnected"]

    def test_a_companion_is_a_404(self, companion_client, seed_user):
        """Owner-only, by role."""
        response = companion_client.post(_DISCONNECT, data=MultiDict([
            ("account_id", str(seed_user["account"].id)),
        ]))
        assert response.status_code == 404


class TestNoFormPostsWithoutItsPage:
    """A feed door with no page named answers 404, not 500."""

    @pytest.mark.parametrize("path", [_CLAIM, _LIST, _MAP, _DISCONNECT])
    def test_a_missing_account_id_is_a_404(self, auth_client, path):
        """No page of ours posts one; the schema refuses and the door 404s."""
        response = auth_client.post(path, data={"setup_token": "x"})
        assert response.status_code == 404


def _setup_token_for(url: str) -> str:
    """Return the setup token that decodes to *url*."""
    return base64.b64encode(url.encode()).decode()


class TestTheScrubberIsNotWhatKeepsTheSecretOut:
    """The doors never hand the credential to a log line; the test proves
    it with a fake whose error text is the real ``requests`` shape."""

    def test_no_record_or_body_carries_the_password_across_every_door(
        self, auth_client, db, seed_user, bridge, caplog,
    ):
        """Claim (ok), listing (401 with the URL in the text), listing
        press (same), disconnect: nothing captured carries the password,
        and no log line carries any ``requests`` exception text."""
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", status_code=401,
        )
        with caplog.at_level(logging.DEBUG):
            claim = _connect(
                auth_client, seed_user,
                token=_setup_token_for(
                    "https://bridge.simplefin.org/simplefin/claim/T-2",
                ),
            ).get_data(as_text=True)
            press = _hx_post(auth_client, _LIST, [
                ("account_id", str(seed_user["account"].id)),
            ]).get_data(as_text=True)
            gone = _post(auth_client, _DISCONNECT, [
                ("account_id", str(seed_user["account"].id)),
            ]).get_data(as_text=True)

        _no_secret_anywhere(caplog, claim, press, gone)
        assert not any(
            re.search(r"for url:", record.getMessage())
            for record in caplog.records
        )
