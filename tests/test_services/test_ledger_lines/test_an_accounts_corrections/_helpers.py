"""The helpers every part of this package shares: an account, an assertion, a settle, a read."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.services.pay_calendar import calendar_for
from tests._ledger_reads import (
    correction_net_in_period,
    linked_key_of,
    source_net,
)
from tests._test_helpers import (
    create_account_of_type,
    create_account_via_service,
    create_settled_cash_transaction,
    observed_day_of,
)


def _make_account(
    seed_user, balance, type_name="Savings", name="Anchor Acct",
    observed_on=None,
):
    """Create an account with a controlled opening anchor; commit; return it.

    An account that has ALREADY EXISTED: its books are opened before anything
    a fixture here could date, so a settle may be recorded before the account's
    own creation day (ruling **R-HG** forbids one on or before the books).
    That is what most cases in this package need, and it costs an extra
    ``budget.account_openings`` row -- which is the production shape after a
    restatement.  A case whose subject is the opening ``create_account`` books
    wants :func:`_make_account_as_created` instead.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        balance: The opening anchor balance, as a string.
        type_name: The ``ref.account_types`` name.
        name: The account name.
        observed_on: The civil day the origination assertion is true for, or
            ``None`` for ``create_account_of_type``'s day-before-today default.
            A case whose subject is WHICH day the opening is about states it
            here rather than re-stamping the row afterwards, because the table
            is append-only (plan step X-f3c-2c).
    """
    account = create_account_of_type(
        seed_user, _db.session, type_name, name,
        anchor_balance=Decimal(balance),
        observed_on=observed_on,
    )
    _db.session.commit()
    return account


def _make_account_as_created(
    seed_user, balance, type_name="Savings", name="Anchor Acct",
):
    """Create an account and leave its books where ``create_account`` put them.

    For the cases whose SUBJECT is the opening that factory books -- dated on
    the day the books opened.  They record no movement, so they need none of
    what :func:`_make_account`'s restatement buys.
    """
    account = create_account_via_service(
        seed_user, _db.session, type_name, name,
        anchor_balance=Decimal(balance),
    )
    _db.session.commit()
    return account


# A controlled mid-day instant for the fixtures that must place two events on
# ONE civil day (ruling R-DH).  12:00 EDT, so plus-or-minus an hour is provably
# the same ``America/New_York`` day -- which a whole-DAY offset from
# :func:`_origin_day` cannot express, since the two events must share one day.
# Deriving such an offset from the ambient clock is exactly how
# ``test_a_settle_on_the_openings_own_day_is_absorbed`` (then ``..._either_order``) came to use
# a DAY offset and stop discriminating the rule it names (finding N-133 / F2).
_PINNED_OPENING_AT = datetime(2026, 3, 17, 16, 0, tzinfo=timezone.utc)
_PINNED_OPENING_DAY = date(2026, 3, 17)
_ONE_HOUR = timedelta(hours=1)


def _origin_day(account):
    """Return the civil day the factory origination assertion is ABOUT.

    The day the partition actually reads (``AccountAnchorHistory.observed_on``),
    which is what a fixture placing a settle "the day before" or "the day
    after" the opening must offset from.

    **It is NOT the origination's ``created_at``, and that distinction is this
    helper's whole reason to exist** (finding N-133, the review of the F1
    revert).  The two were the same day by construction while ``observed_on``
    was DERIVED from ``created_at``; plan step 2 made it a stored column and
    ``create_account_of_type`` began opening its accounts the day BEFORE today,
    so a fixture offsetting from ``created_at`` by one day landed on the
    opening's OWN day and silently graded the opposite case.  Measured on two
    tests: ``test_a_settle_on_an_earlier_day_is_inside_the_opening`` and
    ``test_transfer_attribution_uses_the_walked_sides_settle_day`` both stopped
    testing the strictly-earlier arm their docstrings name.  Offsetting from
    the day the rule reads makes the fixture say what it means.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )
    return row.observed_on


def _add_assertion(account, balance, created_at):
    """Append a true-up ``AccountAnchorHistory`` row at a controlled instant.

    The history row ``anchor_service.stage_anchor_true_up`` writes, but with
    ``created_at`` pinned explicitly and ``observed_on`` derived from it, so the
    civil-day partition under test is exact.  The row names no paycheck: the
    one its correction files in is derived from its day (ruling R-EO deleted
    the row's own ``pay_period_id``, and this helper's argument for it went
    unread from then until plan task plan#3079 deleted it).  Flushes.
    """
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created_at,
        # The civil day this assertion is the closing balance FOR, kept in step
        # with the pinned instant by the shared rule (ruling R-DH, plan step 2).
        observed_on=observed_day_of(created_at),
        # The ENTERED day, in step with the pinned instant (**N-299**).
        # The column's default is the wall clock, which a row built to sit in
        # the PAST must not inherit: it would claim to have been typed today.
        recorded_on=observed_day_of(created_at),
    )
    _db.session.add(row)
    _db.session.flush()
    return row


def _period_containing(user_id, day):
    """Return the id of the period a correction observed on ``day`` books in.

    The derivation ``ledger_lines._account_anchors`` makes
    (:meth:`app.services.pay_calendar.PayCalendar.filing_period`), asked here
    rather than read off the assertion row.  These tests compared a journal
    entry's ``pay_period_id`` against ``AccountAnchorHistory.pay_period_id``
    until ruling R-EO deleted that column -- a comparison that graded the
    entry against a CACHE of this same derivation, and which would have
    passed on production's two rows whose stored period does not contain
    their own day (finding N-168).  Asking the derivation is what the
    producer is actually contracted to do.

    **It asked ``loan_ledger.resolve_anchor_pay_period`` until pay-calendar
    plan step C2-d**, which deleted that chain.  This helper re-derives from
    the same door the producer takes rather than transcribing the rule, so it
    cannot drift from it -- the equivalence of the OLD chain to the new clamp
    is graded once, at ``test_pay_calendar_value.py``, where the transcription
    belongs.
    """
    return calendar_for(user_id).filing_period(day).period_id


def _correction_net_in_period(account_id, scenario_id, source_enum, period_id):
    """Sum an account's correction lines on its own line in ONE pay period.

    Defers to the shared ``tests._ledger_reads.correction_net_in_period`` -- the
    same reading the loan anchor suite makes, so the two halves of the R2 fix are
    graded by one read rather than two that happen to agree.
    """
    return correction_net_in_period(
        _db.session, linked_key_of(_db.session, account_id), scenario_id,
        source_enum, period_id,
    )


def _linked_source_net(account_id, scenario_id, source_enum):
    """Return one correction kind's net on the account's own line, worked out."""
    return source_net(
        _db.session, linked_key_of(_db.session, account_id), scenario_id, source_enum,
    )


def _settle_expense(seed_user, account, amount, settled_on):
    """Settle an expense on *account* on a pinned civil DAY; return it.

    ``settled_on`` is the day the cash moved.  The transaction is placed in
    the seed bootstrap period; the walk attributes by the SETTLE DAY (ruling
    R-DH), so the period placement is immaterial.  It took an instant, or
    ``None`` for the period-start fallback, until plan step X-f1 removed both.
    """
    return create_settled_cash_transaction(
        seed_user, _db.session, seed_user["bootstrap_period"],
        Decimal(str(amount)), account=account, settled_on=settled_on,
    )
