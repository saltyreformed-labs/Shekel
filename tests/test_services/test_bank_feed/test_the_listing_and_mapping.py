"""The bank feed after the claim: the live listing, the owner's mapping, the disconnect and the
panel's read.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import BridgeRefused, FeedUnreadable, MappingRefused, NoFeedConnected
from app.extensions import db
from app.models.bank_feed import BankFeed
from app.models.statement_import import AccountExternalIdentity
from app.services import bank_feed
from app.services.statement_import import record_identity
from tests._test_helpers import create_loan_account
from tests.test_services.test_statement_import.test_record import _second_account

from ._helpers import (
    _ACCESS_URL,
    _CHECKING,
    _SHARE,
    _MORTGAGE,
    _listing_body,
    _FakeResponse,
    _connect,
)


class TestTheListingIsLiveAndRead:
    """``GET <access url>/accounts`` with the measured parameters, parsed."""

    def test_it_needs_a_feed(self, app, db, seed_user, bridge):
        """No feed, no listing, no request."""
        with pytest.raises(NoFeedConnected):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert bridge.gets == []

    def test_it_asks_for_balances_only_in_the_v2_shape(
        self, app, db, seed_user, bridge,
    ):
        """The decrypted access URL, ``/accounts``, the two parameters and
        the timeout -- what Bridge is asked, exactly."""
        _connect(seed_user, bridge)
        db.session.flush()

        bank_feed.list_bridge_accounts(seed_user["user"].id)

        url, kwargs = bridge.gets[0]
        assert url == f"{_ACCESS_URL}/accounts"
        assert kwargs["params"] == {"version": "2", "balances-only": "1"}
        assert kwargs["timeout"] == bank_feed.BRIDGE_TIMEOUT_SECONDS

    def test_it_reads_the_accounts_and_the_notices(
        self, app, db, seed_user, bridge,
    ):
        """Three accounts in Bridge's order with their balances; errlist
        first, then x-api-message, verbatim."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(
                errlist=["Connection to SECU needs attention"],
                api_message=["Provide a 'start-date' parameter"],
            ),
        )

        listing = bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert [account.external_id for account in listing.accounts] == [
            _CHECKING, _SHARE, _MORTGAGE,
        ]
        assert listing.accounts[0].name == "Checking (3820)"
        assert listing.accounts[0].currency == "USD"
        assert listing.accounts[0].balance == Decimal("2073.40")
        assert listing.accounts[2].balance == Decimal("-176719.77")
        assert listing.notices == (
            "Connection to SECU needs attention",
            "Provide a 'start-date' parameter",
        )

    def test_an_answer_with_no_account_list_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """JSON, but not Bridge's shape."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", json_body={"errlist": []},
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.list_bridge_accounts(seed_user["user"].id)
        assert excinfo.value.error_class == "UnexpectedShape"

    def test_a_non_json_answer_is_refused_with_its_status(
        self, app, db, seed_user, bridge,
    ):
        """An HTML error page at 200 is a ``requests`` decode error, raised
        WITHOUT the response attached (requests 2.34.2), so it is caught
        apart from the transport failures and carries the status Bridge
        did answer with rather than reading as "could not be reached"."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", text="<html>maintenance</html>",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.list_bridge_accounts(seed_user["user"].id)
        assert excinfo.value.error_class == "JSONDecodeError"
        assert excinfo.value.status == 200
        assert "HTTP 200" in str(excinfo.value)
        assert excinfo.value.log_details == {
            "status": 200, "error_class": "JSONDecodeError",
        }

    def test_a_stored_url_the_key_cannot_read_is_refused(
        self, app, db, seed_user, bridge, monkeypatch,
    ):
        """The rotation hazard: the row's key is gone from the key list.
        ``FeedUnreadable``, not ``InvalidToken`` escaping as a 500; and no
        request is made."""
        # Pylint: ``import-outside-toplevel`` -- a fresh key minted here.
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
        _connect(seed_user, bridge)
        db.session.flush()
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.delenv("FIELD_ENCRYPTION_KEY_OLD", raising=False)

        with pytest.raises(FeedUnreadable):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert bridge.gets == []

    def test_an_account_missing_its_id_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """One malformed member refuses the listing rather than skipping
        it: a listing missing an account would let the owner map the rest
        and never learn one was dropped."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(accounts=[
                {"name": "Checking", "currency": "USD", "balance": "1.00"},
            ]),
        )

        with pytest.raises(BridgeRefused):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

    def test_an_id_longer_than_the_column_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """``external_account_id`` is ``String(64)``; an id the mapping
        could not record is refused at the listing, before a form offers
        it."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(accounts=[
                {"id": "ACT-" + "x" * 61, "name": "Checking",
                 "currency": "USD", "balance": "1.00"},
            ]),
        )

        with pytest.raises(BridgeRefused):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

    def test_a_balance_that_is_not_a_recordable_decimal_string_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """Bridge's ``balance`` is read through the ONE walk from a source's
        text to a figure (``money_from_text``, plan step
        ``bank_import:X-f6b-2``), as the feed's sync reads the same field.
        Until then the listing read ``Decimal(str(item["balance"]))``, which
        took a JSON number (a float by the time it is parsed) and a quiet
        ``NaN`` (which renders, then raises in the money macro).  A figure past
        the money columns' width is refused too, though the listing stores
        nothing: the one walk answers one way for every reader.  Each case
        refuses the whole listing, with Bridge's shape named."""
        _connect(seed_user, bridge)
        db.session.flush()
        for balance in (2073.40, "NaN", "10000000000.00"):
            bridge.get_answer = _FakeResponse(
                f"{_ACCESS_URL}/accounts", json_body=_listing_body(accounts=[
                    {"id": _CHECKING, "name": "Checking (3820)",
                     "currency": "USD", "balance": balance,
                     "available-balance": "2073.40",
                     "balance-date": 1789775211, "transactions": [],
                     "holdings": []},
                ]),
            )

            with pytest.raises(BridgeRefused) as excinfo:
                bank_feed.list_bridge_accounts(seed_user["user"].id)
            assert excinfo.value.error_class == "UnexpectedShape", balance


def _declared(seed_user):
    """The owner's declared mappings as ``{external_id: account_id}``."""
    return {
        row.external_account_id: row.account_id
        for row in db.session.query(AccountExternalIdentity).filter(
            AccountExternalIdentity.user_id == seed_user["user"].id,
            AccountExternalIdentity.feed_id.isnot(None),
        )
    }


class TestTheMappingIsTheOwnersDeclaration:
    """One submission states the whole mapping the owner can see."""

    def test_it_needs_a_feed(self, app, db, seed_user):
        """No feed, nothing to declare under."""
        with pytest.raises(NoFeedConnected):
            bank_feed.map_accounts(
                seed_user["user"].id, {_CHECKING: seed_user["account"].id},
            )

    def test_it_declares_a_mapping_under_the_feed(
        self, app, db, seed_user, bridge,
    ):
        """The row carries the feed, the simplefin source and Bridge's id."""
        feed = _connect(seed_user, bridge)
        db.session.flush()

        outcome = bank_feed.map_accounts(
            seed_user["user"].id,
            {_CHECKING: seed_user["account"].id, _MORTGAGE: None},
        )

        assert outcome == bank_feed.MappingOutcome(
            declared=1, moved=0, cleared=0, unchanged=1,
        )
        row = db.session.query(AccountExternalIdentity).one()
        assert row.feed_id == feed.id
        assert row.source_id == ref_cache.statement_source_id(
            StatementSourceEnum.SIMPLEFIN,
        )
        assert row.external_account_id == _CHECKING
        assert row.account_id == seed_user["account"].id

    def test_resubmitting_the_same_mapping_changes_nothing(
        self, app, db, seed_user, bridge,
    ):
        """The receipt says so rather than "saved"."""
        _connect(seed_user, bridge)
        db.session.flush()
        choices = {_CHECKING: seed_user["account"].id}
        bank_feed.map_accounts(seed_user["user"].id, choices)
        before = db.session.query(AccountExternalIdentity).one().id

        outcome = bank_feed.map_accounts(seed_user["user"].id, choices)

        assert outcome == bank_feed.MappingOutcome(
            declared=0, moved=0, cleared=0, unchanged=1,
        )
        assert db.session.query(AccountExternalIdentity).one().id == before

    def test_a_change_of_mind_moves_the_mapping(
        self, app, db, seed_user, bridge,
    ):
        """Checking's Bridge account is now the second account's."""
        _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        outcome = bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: second.id},
        )

        assert outcome.moved == 1
        assert _declared(seed_user) == {_CHECKING: second.id}

    def test_choosing_not_mapped_clears_the_mapping(
        self, app, db, seed_user, bridge,
    ):
        """A cleared mapping is a deleted row."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        outcome = bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: None},
        )

        assert outcome.cleared == 1
        assert _declared(seed_user) == {}

    def test_handing_an_account_from_one_bridge_account_to_another_lands(
        self, app, db, seed_user, bridge,
    ):
        """FIRING CONTROL for the endings-first order: X was Checking; now
        Y is Checking and X is unmapped, in ONE submission.  Declaring Y
        before X's row is gone would trip
        ``uq_account_external_identities_account_source``."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        outcome = bank_feed.map_accounts(
            seed_user["user"].id,
            {_CHECKING: None, _SHARE: seed_user["account"].id},
        )

        assert outcome.cleared == 1
        assert outcome.declared == 1
        assert _declared(seed_user) == {_SHARE: seed_user["account"].id}

    def test_two_bridge_accounts_for_one_account_here_are_refused(
        self, app, db, seed_user, bridge,
    ):
        """Said in the owner's words before the key would say it."""
        _connect(seed_user, bridge)
        db.session.flush()

        with pytest.raises(MappingRefused) as excinfo:
            bank_feed.map_accounts(
                seed_user["user"].id,
                {_CHECKING: seed_user["account"].id,
                 _SHARE: seed_user["account"].id},
            )

        assert "one account here can be one Bridge account" in str(excinfo.value)
        assert _declared(seed_user) == {}

    def test_another_owners_account_is_refused(
        self, app, db, seed_user, bridge, second_user,
    ):
        """IDOR at the service tier: the choice names a row that exists and
        is not the caller's."""
        _connect(seed_user, bridge)
        db.session.flush()

        with pytest.raises(MappingRefused):
            bank_feed.map_accounts(
                seed_user["user"].id, {_CHECKING: second_user["account"].id},
            )

        assert _declared(seed_user) == {}

    def test_a_loan_is_not_a_mappable_account(
        self, app, db, seed_user, bridge,
    ):
        """The mapping offers exactly the accounts the statements page
        serves; a loan has no bank statement to record against."""
        _connect(seed_user, bridge)
        db.session.flush()
        loan = create_loan_account(seed_user, db.session)

        assert loan not in bank_feed.mappable_accounts(seed_user["user"].id)
        with pytest.raises(MappingRefused):
            bank_feed.map_accounts(seed_user["user"].id, {_MORTGAGE: loan.id})

    def test_an_account_held_by_a_bridge_id_absent_from_the_submission_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """The guard beside the key: X holds Checking and the submission
        names only Y -> Checking, so the declaration would trip
        ``uq_account_external_identities_account_source`` after the write;
        it is refused in words before it."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        with pytest.raises(MappingRefused) as excinfo:
            bank_feed.map_accounts(
                seed_user["user"].id, {_SHARE: seed_user["account"].id},
            )

        assert "this form did not include" in str(excinfo.value)
        assert _declared(seed_user) == {_CHECKING: seed_user["account"].id}

    def test_a_choice_that_repeats_what_stands_needs_no_mappable_account(
        self, app, db, seed_user, bridge,
    ):
        """A mapping to an account archived since it was declared is kept
        by resubmitting it unchanged: the mappable check applies to a
        CHANGE, and repeating what stands is no change."""
        # Pylint: ``import-outside-toplevel`` -- the sibling's builder.
        from tests.test_services.test_statement_import.test_record import (  # pylint: disable=import-outside-toplevel
            _second_account,
        )
        _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(seed_user["user"].id, {_SHARE: second.id})
        second.is_active = False
        db.session.flush()
        assert second not in bank_feed.mappable_accounts(seed_user["user"].id)

        outcome = bank_feed.map_accounts(
            seed_user["user"].id, {_SHARE: second.id},
        )

        assert outcome.unchanged == 1
        assert _declared(seed_user) == {_SHARE: second.id}

    def test_a_bridge_id_the_column_cannot_hold_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """The service's own guard, beside the schema's."""
        _connect(seed_user, bridge)
        db.session.flush()

        with pytest.raises(MappingRefused):
            bank_feed.map_accounts(
                seed_user["user"].id, {"A" * 65: seed_user["account"].id},
            )


class TestDisconnectTakesTheDeclaredMappingsWithIt:
    """Ruling R-BI12's delete, ruling R-BI26's cascade, one receipt."""

    def test_it_needs_a_feed(self, app, db, seed_user):
        """Nothing to disconnect."""
        with pytest.raises(NoFeedConnected):
            bank_feed.disconnect_feed(seed_user["user"].id)

    def test_the_feed_and_its_mappings_go_and_a_learned_row_stays(
        self, app, db, seed_user, bridge,
    ):
        """Two declared rows counted and gone; the CSV pairing untouched."""
        _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(
            seed_user["user"].id,
            {_CHECKING: seed_user["account"].id, _SHARE: second.id},
        )
        record_identity(
            seed_user["account"].id, seed_user["user"].id,
            ref_cache.statement_source_id(StatementSourceEnum.SECU_CHECKING_CSV),
            "******3820",
        )
        db.session.flush()

        outcome = bank_feed.disconnect_feed(seed_user["user"].id)

        assert outcome.mappings_removed == 2
        assert db.session.query(BankFeed).count() == 0
        assert _declared(seed_user) == {}
        assert db.session.query(AccountExternalIdentity).count() == 1


class TestTheFeedStateIsARead:
    """What the panel renders, without decrypting or asking Bridge."""

    def test_none_without_a_feed(self, app, db, seed_user):
        """The panel's not-connected state."""
        assert bank_feed.feed_state(seed_user["user"].id) is None

    def test_the_state_carries_the_mappings_by_bridge_id(
        self, app, db, seed_user, bridge,
    ):
        """Ordered by Bridge id, each with its account's name; and the read
        asks Bridge nothing."""
        feed = _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(
            seed_user["user"].id,
            {_SHARE: second.id, _CHECKING: seed_user["account"].id},
        )
        gets_before = len(bridge.gets)

        state = bank_feed.feed_state(seed_user["user"].id)

        assert state.feed_id == feed.id
        assert state.claimed_at is not None
        assert [m.external_id for m in state.mappings] == sorted(
            [_CHECKING, _SHARE],
        )
        assert {m.account_name for m in state.mappings} == {
            seed_user["account"].name, "Second Checking",
        }
        assert len(bridge.gets) == gets_before
