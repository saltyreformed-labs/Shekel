"""
Shekel Budget App -- The bank FEED's doors: claim, list, map, disconnect

Plan step ``bank_import:X-f6b-2``, leaf (3c), under rulings **R-BI12** (the
access URL is per-owner ciphertext in ``budget.bank_feeds``, claimed in the
app; the owner maps each Bridge account to a Shekel account through the
existing ``account_external_identities`` row under the ``simplefin`` source;
disconnect deletes the feed row), **R-BI26** (a mapping declared here dies
with the feed, and deleting imports never touches it) and **R-BI27**
(Bridge's account list is fetched live and never stored; the claim commits
BEFORE the list is fetched, so a failed fetch never loses a consumed token).

**What a SimpleFIN setup token is, and why the claim is one request.**  Bridge
issues the owner a setup token: base64 of a one-time CLAIM URL.  Claiming --
one bodiless ``POST`` to that URL -- consumes the token at Bridge and answers
with the ACCESS URL, ``https://<user>:<password>@bridge.../simplefin``, which
is the credential every later request carries.  A token can be claimed once,
so :func:`claim_feed` refuses everything it can BEFORE the request (an
unreadable token, a token naming a host other than Bridge's, a feed already
standing) and stages the ciphertext the
instant Bridge answers; the route commits before anything else is asked of
Bridge.  A second claim racing the first past the pre-check is refused by
``uq_bank_feeds_user`` at the commit and that token is lost -- Bridge issues
another on request, and the docstring says so rather than adding a lock for
a race one owner cannot run against themselves except by double-submitting.

Every request these doors make goes through :mod:`._bridge`: the host pin
(ruling **R-BI28**), the one request site that follows no redirect (ruling
**R-BI29**) and the refusal that never repeats a credential are spelled
there, once, and that module's docstring is their argument.  This leaf reads
Bridge's answers and writes the owner's rows, and it logs nothing itself:
every refusal it raises -- its own and :mod:`._bridge`'s -- is logged once,
with its class, by the door that catches it
(:func:`app.routes.accounts._statement_doors.log_refusal`, ledger row
**BI-499**).  Born in the package's move-only split; the package docstring
says how the move was graded.

Services boundary: plain data in, plain data out, no Flask import.  Nothing
here commits; the route owns the unit of work.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from cryptography.fernet import InvalidToken

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import (
    BridgeHostRefused,
    BridgeRefused,
    FeedAlreadyConnected,
    FeedUnreadable,
    MappingRefused,
    NoFeedConnected,
    SetupTokenUnreadable,
)
from app.extensions import db
from app.models.account import Account
from app.models.bank_feed import BankFeed
from app.models.statement_import import AccountExternalIdentity
from app.services.account_resolver import serves_cash_detail
from app.services.account_service import list_active_accounts
from app.services.bank_feed._bridge import (
    BRIDGE_HOST,
    _ask_bridge,
    _refuse_unless_bridge,
)
from app.services.statement_import import record_identity
from app.utils.field_encryption import decrypt_secret, encrypt_secret

#: The listing every claim and every "Map accounts" press asks for: accounts
#: without their transactions (``balances-only``), in Bridge's v2 shape
#: (``version=2``; measured 2026-09-18 on the developer's own connection).
_LISTING_PARAMS = {"version": "2", "balances-only": "1"}

#: The longest Bridge account id the mapping column holds, READ off the
#: column rather than spelled: ``account_external_identities
#: .external_account_id`` is ``String(64)`` and the mapping schema reads
#: this same value, so the bound has one home.  Bridge's ids are ``ACT-`` +
#: a uuid, 40 characters (measured 2026-09-18).
EXTERNAL_ID_LIMIT = (
    AccountExternalIdentity.__table__.c.external_account_id.type.length
)


@dataclass(frozen=True)
class BridgeAccount:
    """One account as Bridge lists it, for the owner to recognise.

    Attributes:
        external_id: Bridge's id, what the mapping records.
        name: Bridge's label (``Checking (3820)``).
        currency: Bridge's currency code.
        balance: Bridge's ``balance`` at the listing instant, shown so the
            owner can tell two accounts apart; never recorded here.
    """

    external_id: str
    name: str
    currency: str
    balance: Decimal


@dataclass(frozen=True)
class BridgeListing:
    """What Bridge answered to a listing request.

    Attributes:
        accounts: Bridge's accounts, in Bridge's order.
        notices: Bridge's ``errlist`` and ``x-api-message`` strings, in that
            order, for the panel to show verbatim.
    """

    accounts: tuple[BridgeAccount, ...]
    notices: tuple[str, ...]


@dataclass(frozen=True)
class FeedMapping:
    """One declared mapping, as the panel lists it.

    Attributes:
        external_id: Bridge's account id.
        account_id: The Shekel account it is declared to be.
        account_name: That account's name, for display.
    """

    external_id: str
    account_id: int
    account_name: str


@dataclass(frozen=True)
class FeedState:
    """The owner's feed as the panel renders it.

    Attributes:
        feed_id: The ``budget.bank_feeds`` row.
        claimed_at: When the setup token was claimed.
        mappings: The declared mappings, ordered by Bridge id.
    """

    feed_id: int
    claimed_at: datetime
    mappings: tuple[FeedMapping, ...]


@dataclass(frozen=True)
class MappingOutcome:
    """What one mapping form submission did.

    Attributes:
        declared: Mappings written for a Bridge account that had none.
        moved: Mappings whose Shekel account changed.
        cleared: Mappings removed because the owner chose "not mapped".
        unchanged: Bridge accounts whose submission matched what stood.
    """

    declared: int
    moved: int
    cleared: int
    unchanged: int


@dataclass(frozen=True)
class DisconnectOutcome:
    """What the disconnect removed, counted before the rows went.

    Attributes:
        mappings_removed: Declared mappings the cascade took with the feed.
    """

    mappings_removed: int


def _feed_source_id() -> int:
    """The ``simplefin`` ref row's id, resolved once per call through the cache."""
    return ref_cache.statement_source_id(StatementSourceEnum.SIMPLEFIN)


def _feed_of(user_id: int) -> BankFeed | None:
    """Return the owner's feed row, or ``None``: the ONE spelling of that read."""
    return (
        db.session.query(BankFeed).filter(BankFeed.user_id == user_id)
        .one_or_none()
    )


def _mappings_of(feed: BankFeed) -> list[AccountExternalIdentity]:
    """Return the rows declared under *feed*, ordered by Bridge id."""
    return (
        db.session.query(AccountExternalIdentity)
        .filter(AccountExternalIdentity.feed_id == feed.id)
        .order_by(AccountExternalIdentity.external_account_id)
        .all()
    )


def feed_state(user_id: int) -> FeedState | None:
    """Return the owner's feed as the panel renders it, or ``None``.

    A READ: it decrypts nothing and asks Bridge nothing.

    Args:
        user_id: The owner.

    Returns:
        The :class:`FeedState`, or ``None`` when no feed is connected.
    """
    feed = _feed_of(user_id)
    if feed is None:
        return None
    rows = _mappings_of(feed)
    names = {
        account.id: account.name
        for account in db.session.query(Account).filter(
            Account.id.in_([row.account_id for row in rows]),
        )
    } if rows else {}
    return FeedState(
        feed_id=feed.id,
        claimed_at=feed.claimed_at,
        mappings=tuple(
            FeedMapping(
                external_id=row.external_account_id,
                account_id=row.account_id,
                account_name=names[row.account_id],
            )
            for row in rows
        ),
    )


def mappable_accounts(user_id: int) -> list[Account]:
    """Return the accounts a Bridge account may be declared to be.

    Exactly the accounts the statements page serves and the importer records
    into -- :func:`~app.services.account_resolver.serves_cash_detail`, the
    ONE predicate -- among the owner's active accounts.  Bridge lists the
    owner's mortgage beside their checking; the mapping form offers no
    Shekel loan to declare it as, because no statement is recorded against
    one.

    Args:
        user_id: The owner.

    Returns:
        The accounts, in the owner's own display order.
    """
    return [
        account for account in list_active_accounts(user_id)
        if serves_cash_detail(account)
    ]


def _claim_url_from(setup_token: str) -> str:
    """Decode the pasted setup token into the claim URL, or refuse.

    Args:
        setup_token: What the owner pasted, whitespace tolerated.

    Returns:
        The ``https`` claim URL, on Bridge's host.

    Raises:
        SetupTokenUnreadable: When the paste is not base64, does not decode
            to text, or decodes to something other than an ``https`` URL.
        BridgeHostRefused: When it decodes to an ``https`` URL on a host
            other than Bridge's (ruling **R-BI28**).
    """
    try:
        decoded = base64.b64decode(
            "".join(setup_token.split()), validate=True,
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise SetupTokenUnreadable() from exc
    _refuse_unless_bridge(
        decoded,
        unreadable=SetupTokenUnreadable(),
        foreign=BridgeHostRefused(BRIDGE_HOST),
    )
    return decoded


def _access_url_from(body: str, status: int) -> str:
    """Return Bridge's claim answer as the access URL, or refuse.

    Args:
        body: The claim response's text.
        status: The status it came with -- past ``raise_for_status``, so
            not an error, but not necessarily 200 -- carried on the refusal
            as what Bridge answered rather than assumed.

    Returns:
        The ``https`` access URL on Bridge's host, whitespace stripped.

    Raises:
        BridgeRefused: When the body is not one ``https`` URL -- Bridge
            answered with something that is not a credential, which nothing
            here should store as one (``UnexpectedBody``); or when it is one
            on a host other than Bridge's, which nothing here should ever
            send the feed's requests to (``ForeignHost``, ruling
            **R-BI28**).
    """
    url = body.strip()
    unreadable = BridgeRefused(
        "Bridge answered the claim with something other than an access "
        "URL, so nothing was stored.",
        status=status, error_class="UnexpectedBody",
    )
    # An access URL is ONE token; a body with whitespace inside it is prose,
    # whatever ``requests`` would percent-encode the whitespace into.
    if len(url.split()) != 1:
        raise unreadable
    _refuse_unless_bridge(
        url,
        unreadable=unreadable,
        foreign=BridgeRefused(
            f"Bridge answered the claim with an access URL on a server other "
            f"than {BRIDGE_HOST}, so nothing was stored.",
            status=status, error_class="ForeignHost",
        ),
    )
    return url


def claim_feed(user_id: int, setup_token: str) -> BankFeed:
    """Claim *setup_token* at Bridge and stage the access URL as ciphertext.

    Refuses everything it can BEFORE the request, because the request
    consumes the token (module docstring); then ONE bodiless ``POST`` to the
    claim URL, and the answer is staged encrypted.  Does not commit, and asks
    Bridge nothing else: the route commits and only then lists the accounts
    (ruling **R-BI27**), so a listing that fails cannot lose the credential.

    Args:
        user_id: The owner.
        setup_token: What the owner pasted.

    Returns:
        The staged :class:`~app.models.bank_feed.BankFeed`.

    Raises:
        SetupTokenUnreadable: The paste does not decode to an ``https`` URL.
        BridgeHostRefused: It decodes to one on a host other than Bridge's
            (ruling **R-BI28**); refused before the request like the rest.
        FeedAlreadyConnected: A feed stands; disconnect first.  Checked
            before the request so a standing feed never burns a token.
        BridgeRefused: Bridge could not be reached, answered an error or a
            redirect (ruling **R-BI29**: never followed), or answered
            something other than an access URL on its own host.
    """
    claim_url = _claim_url_from(setup_token)
    if _feed_of(user_id) is not None:
        raise FeedAlreadyConnected()
    response = _ask_bridge("post", claim_url, "the claim")
    access_url = _access_url_from(response.text, response.status_code)
    feed = BankFeed(
        user_id=user_id, access_url_encrypted=encrypt_secret(access_url),
    )
    db.session.add(feed)
    db.session.flush()
    return feed


def _account_from(item: object) -> BridgeAccount:
    """Read one account object of Bridge's listing, or refuse the listing.

    Args:
        item: One member of the answer's ``accounts`` list.

    Returns:
        The :class:`BridgeAccount`.

    Raises:
        BridgeRefused: When the member is not the measured shape.
    """
    try:
        external_id = str(item["id"])
        name = str(item["name"])
        currency = str(item["currency"])
        balance = Decimal(str(item["balance"]))
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise BridgeRefused(
            "Bridge's account list was not in the shape this app reads, so "
            "nothing was changed.",
            status=200, error_class="UnexpectedShape",
        ) from exc
    if not external_id or len(external_id) > EXTERNAL_ID_LIMIT:
        raise BridgeRefused(
            f"Bridge named an account by an id this app cannot record (empty "
            f"or longer than {EXTERNAL_ID_LIMIT} characters), so nothing was "
            f"changed.",
            status=200, error_class="UnexpectedShape",
        )
    return BridgeAccount(
        external_id=external_id, name=name, currency=currency, balance=balance,
    )


def _strings(value: object) -> tuple[str, ...]:
    """Return *value* as a tuple of strings when it is a list of them, else empty."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def list_bridge_accounts(user_id: int) -> BridgeListing:
    """Ask Bridge which accounts the owner's feed can see, live.

    Ruling **R-BI27**: fetched on each press and never stored.  The access
    URL is decrypted for this request and held no longer.

    **No host check of its own** (ruling **R-BI28**): the stored URL passed
    :func:`_refuse_unless_bridge` at the claim, and the claim is the only
    door that stores one -- :func:`claim_feed` is the one construction site
    of :class:`~app.models.bank_feed.BankFeed` under ``app/`` and
    ``scripts/`` (grep, 2026-09-20), and ``rotate_field_key.py`` re-wraps
    the plaintext it decrypts -- so a foreign access URL is a state no door
    writes rather than one this read has to catch.

    Args:
        user_id: The owner.

    Returns:
        The :class:`BridgeListing`.

    Raises:
        NoFeedConnected: No feed stands for the owner.
        FeedUnreadable: The stored URL cannot be decrypted under the current
            key list.
        BridgeRefused: Bridge could not be reached, answered an error or a
            redirect (ruling **R-BI29**: never followed), or answered a
            shape this app does not read.
    """
    feed = _feed_of(user_id)
    if feed is None:
        raise NoFeedConnected()
    try:
        access_url = decrypt_secret(feed.access_url_encrypted)
    except (RuntimeError, InvalidToken) as exc:
        # The two states ``/mfa/confirm`` handles for the TOTP secret: the
        # key unset (``RuntimeError``) or the ciphertext written under a
        # key since pruned (``InvalidToken``).  A designed refusal, so the
        # panel can say so; a 500 is one htmx cannot swap.
        raise FeedUnreadable() from exc
    response = _ask_bridge(
        "get", f"{access_url}/accounts", "the account listing",
        params=_LISTING_PARAMS,
    )
    try:
        answer = response.json()
    except requests.JSONDecodeError as exc:
        # Raised WITHOUT the response attached (requests 2.34.2, measured),
        # so through ``_refused`` it would read as "could not be reached"
        # with no status: Bridge answered, with something that is not JSON.
        raise BridgeRefused(
            f"Bridge answered HTTP {response.status_code} to the account "
            f"listing with something other than its account list, so "
            f"nothing was changed.",
            status=response.status_code, error_class=type(exc).__name__,
        ) from None
    accounts = answer.get("accounts") if isinstance(answer, dict) else None
    if not isinstance(accounts, list):
        raise BridgeRefused(
            "Bridge's answer carried no account list, so nothing was changed.",
            status=response.status_code, error_class="UnexpectedShape",
        )
    return BridgeListing(
        accounts=tuple(_account_from(item) for item in accounts),
        notices=_strings(answer.get("errlist"))
        + _strings(answer.get("x-api-message")),
    )


def _refuse_bad_choices(
    user_id: int,
    standing: "dict[str, AccountExternalIdentity]",
    choices: "dict[str, int | None]",
) -> None:
    """Refuse a mapping submission the keys would refuse, in the owner's words.

    Args:
        user_id: The owner.
        standing: The declared rows by Bridge id, before the submission.
        choices: Bridge account id -> the Shekel account id, or ``None``.

    Raises:
        MappingRefused: A choice names an account that is not one of the
            owner's own cash accounts (a tampered option, or one archived
            since the form rendered -- unless the choice merely repeats
            what already stands for that Bridge account, which is no
            choice at all and must stay saveable); two Bridge accounts name
            one Shekel account; an account already declared for a Bridge
            account this submission does not mention, so the key
            ``uq_account_external_identities_account_source`` would refuse
            the declaration after the write (the form renders every standing
            mapping, so only a hand-built payload reaches this); a Bridge id
            the column cannot hold.
    """
    mappable = {account.id for account in mappable_accounts(user_id)}
    changed = {
        external_id: account_id
        for external_id, account_id in choices.items()
        if account_id and (
            external_id not in standing
            or standing[external_id].account_id != account_id
        )
    }
    if any(account_id not in mappable for account_id in changed.values()):
        raise MappingRefused(
            "One of the accounts chosen is not one of your cash accounts, so "
            "nothing was changed.",
        )
    chosen = [account_id for account_id in choices.values() if account_id]
    if len(chosen) != len(set(chosen)):
        raise MappingRefused(
            "Two of Bridge's accounts were declared to be the same account "
            "here, and one account here can be one Bridge account at most, "
            "so nothing was changed.",
        )
    held_elsewhere = {
        row.account_id: external_id
        for external_id, row in standing.items()
        if external_id not in choices
    }
    if any(account_id in held_elsewhere for account_id in changed.values()):
        raise MappingRefused(
            "One of the accounts chosen is already declared to be a Bridge "
            "account this form did not include; unmap that one first.  "
            "Nothing was changed.",
        )
    if any(
        not external_id or len(external_id) > EXTERNAL_ID_LIMIT
        for external_id in choices
    ):
        raise MappingRefused(
            "One of Bridge's account ids could not be recorded, so nothing "
            "was changed.",
        )


def _end_stale_mappings(
    standing: "dict[str, AccountExternalIdentity]",
    choices: "dict[str, int | None]",
) -> tuple[int, int]:
    """Delete every standing mapping the submission clears or moves.

    Args:
        standing: The declared rows by Bridge id, before the submission.
        choices: The submission.

    Returns:
        ``(cleared, moved)``: rows deleted because the owner chose "not
        mapped", and rows deleted because the owner chose another account
        (their replacement is :func:`_declare_new_mappings`' to write).
    """
    cleared = moved = 0
    for external_id, row in standing.items():
        if external_id not in choices:
            continue
        account_id = choices[external_id]
        if account_id is None:
            db.session.delete(row)
            cleared += 1
        elif row.account_id != account_id:
            db.session.delete(row)
            moved += 1
    return cleared, moved


def _declare_new_mappings(
    feed: BankFeed,
    standing: "dict[str, AccountExternalIdentity]",
    choices: "dict[str, int | None]",
) -> int:
    """Write a declared row for every choice that names an account not already
    declared for that Bridge account.

    Args:
        feed: The owner's feed, which every row written here names.
        standing: The declared rows by Bridge id, before the submission
            (the endings have been flushed; a moved row is gone).
        choices: The submission.

    Returns:
        How many Bridge accounts that had NO mapping gained one.
    """
    declared = 0
    for external_id, account_id in choices.items():
        row = standing.get(external_id)
        if account_id is None or (row is not None and row.account_id == account_id):
            continue
        record_identity(
            account_id, feed.user_id, _feed_source_id(), external_id,
            feed_id=feed.id,
        )
        if row is None:
            declared += 1
    return declared


def map_accounts(
    user_id: int, choices: "dict[str, int | None]",
) -> MappingOutcome:
    """Declare, move or clear the mapping of each Bridge account submitted.

    The form submits EVERY Bridge account Bridge listed AND every standing
    mapping Bridge no longer lists (rendered with its current account, so it
    can be kept or cleared), each with the Shekel account the owner chose or
    none, so one submission states the whole mapping the owner can see: a
    Bridge account left unmapped is a decision, and a mapping the owner
    cleared is removed.  A Bridge id absent from the submission altogether
    -- only a hand-built payload omits one -- is left alone, and a choice
    that would collide with it is refused in words before the key would.

    **Refuses before it writes** (:func:`_refuse_bad_choices`), then
    **every ending first, ONE flush, then every declaration**.  A change of
    mind is a DELETE and a fresh declaration rather than an UPDATE of
    ``account_id``, so the audit trail reads as what happened (one
    declaration ended, another made); and the endings go first because a
    submission may hand an account from one Bridge account to another -- X
    was Checking, now Y is Checking and X is unmapped -- which
    ``uq_account_external_identities_account_source`` refuses if Y's
    declaration reaches the database while X's still stands.  The same
    Bridge account submitted twice is refused by the schema before this runs.

    Args:
        user_id: The owner.
        choices: Bridge account id -> the Shekel account id, or ``None`` for
            "not mapped".

    Returns:
        The :class:`MappingOutcome`.

    Raises:
        NoFeedConnected: No feed stands for the owner.
        MappingRefused: A choice names an account the owner may not map, two
            Bridge accounts name one Shekel account, or a Bridge id cannot
            be recorded.
    """
    feed = _feed_of(user_id)
    if feed is None:
        raise NoFeedConnected()
    standing = {
        row.external_account_id: row for row in _mappings_of(feed)
    }
    _refuse_bad_choices(user_id, standing, choices)
    cleared, moved = _end_stale_mappings(standing, choices)
    db.session.flush()
    declared = _declare_new_mappings(feed, standing, choices)
    db.session.flush()
    return MappingOutcome(
        declared=declared, moved=moved, cleared=cleared,
        unchanged=len(choices) - declared - moved - cleared,
    )


def disconnect_feed(user_id: int) -> DisconnectOutcome:
    """Delete the owner's feed; its declared mappings go with it.

    Ruling **R-BI12**: disconnect deletes the row, and revoking the token at
    Bridge is the owner's own act.  Ruling **R-BI26**: the mappings declared
    under it are removed by ``fk_account_external_identities_feed_owner``'s
    cascade in the same statement, counted here first so the receipt can
    say so.  The imports the feed recorded stand.

    Args:
        user_id: The owner.

    Returns:
        The :class:`DisconnectOutcome`.

    Raises:
        NoFeedConnected: No feed stands for the owner.
    """
    feed = _feed_of(user_id)
    if feed is None:
        raise NoFeedConnected()
    mappings_removed = len(_mappings_of(feed))
    db.session.delete(feed)
    db.session.flush()
    return DisconnectOutcome(mappings_removed=mappings_removed)
