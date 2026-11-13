"""
Shekel Budget App -- Cash Ledger: anchor FACT tests (Commit 4 / E-19)

Tests for ``app.services.cash_ledger.resolve_anchor`` (the dated anchor SoT),
which lives in the leaf's ``_facts`` submodule.  Renamed twice, each time
following the code: ``test_balance_resolver_anchor.py`` -> ``test_cash_events.py``
at plan step D1a (which split the anchor out of ``balance_resolver`` into the
module owning the balance fold's INPUT facts), then to this name at D1c, when
those facts became one submodule of the ``cash_ledger`` package.

The leaf's other two concerns now have their own suites, one per submodule --
what one row is WORTH in ``test_cash_amounts/`` and what a set of rows SUMS
TO in ``test_cash_flows.py`` -- which is the relocation this docstring used to
anticipate as "its own commit".  It happened at plan step X-c2c2, and the
promotion it named happened with it: ``tests/_test_helpers.add_entry`` gained
the three bucket flags those tests need, so neither suite carries a private
entry builder.  This file is the FACTS file, and only that.

The resolver reads the most recent ``AccountAnchorHistory`` row as the
dated source of truth for E-19.  It is the ONE answer to "what balance has
this account been asserted to hold" since plan step X-f1c3a, which deleted
the ``Account.current_anchor_*`` cache columns it used to be reconciled
against.  These tests lock the contract:

  * the latest history row wins, even when more than one exists;
  * "latest" is the latest BUSINESS day -- an assertion observed EARLIER but
    RECORDED later is not the current one (the ordering the whole partition
    turns on, and which nothing pinned until X-f1c3a);
  * the day it reports is the SAME day
    :func:`~app.services.cash_ledger.reconciled_through` reports, which three
    "as of" captions depend on since ruling R-EP;
  * the resolver never returns ``None`` for a factory-built account
    (Commit 3 invariant);
  * the returned ``balance`` is a 2-decimal-place ``Decimal``;
  * a zero anchor balance is preserved verbatim per E-12 (zero is a
    value, not "missing").

Test IDs match the remediation plan's Commit 4 specification (C4-1
through C4-6); C4-2 and C4-4 were re-pointed at X-f1c3a when the cache they
graded and the ``scenario_id`` parameter they exercised were deleted.
"""

from dataclasses import FrozenInstanceError
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta
from datetime import timezone as _timezone
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.services.cash_ledger import (
    AnchorPoint,
    reconciled_through,
    resolve_anchor,
)
from app.utils.dates import display_today
from tests._test_helpers import account_never_asserted


def _make_anchor_history(
    *,
    account_id: int,
    anchor_balance: Decimal,
    observed_on: _date | None = None,
) -> AccountAnchorHistory:
    """Insert and flush an ``AccountAnchorHistory`` row.

    Used by the multi-event tests to layer additional true-up rows on
    top of the origination row that ``account_service.create_account``
    writes.  Returns the inserted row so the caller can read
    ``created_at`` (the resolver's tiebreaker is ``created_at desc``).

    **The business day is a PARAMETER and is stated at INSERT** (plan step
    X-f3c-2c).  Callers used to assign ``row.observed_on`` after the flush; the
    table is append-only, so what a row says about which day it is true for is
    decided when it is written.

    Args:
        account_id: The owning account.
        anchor_balance: The asserted balance.
        observed_on: The civil day that balance was TRUE.  ``None``, the
            default, is today in the USER's zone (ruling R-DH (b)) -- the row
            means "asserted now".
    """
    history = AccountAnchorHistory(
        account_id=account_id,
        anchor_balance=anchor_balance,
        observed_on=display_today() if observed_on is None else observed_on,
    )
    db.session.add(history)
    db.session.flush()
    return history


class TestResolveAnchor:
    """Tests for ``resolve_anchor``."""

    # ── C4-1 -----------------------------------------------------------

    def test_resolve_anchor_from_latest_history(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-1: with two history rows, the resolver returns the newest.

        Setup: seed_user gives one origination row (anchor 1000.00, observed
        on a day inside the bootstrap period).  We add a second history row
        asserting 1234.56 today.

        Expected: ``resolve_anchor`` returns the 1234.56 row.
        Arithmetic: the latest row is the dated SoT; 1234.56 is the
        most recent ``anchor_balance`` and its ``observed_on`` is today.

        **It no longer creates a second PAY PERIOD**, and the deletion is the
        point: that period existed only to give the second assertion a
        different ``pay_period_id`` to be resolved to, and ruling R-EO deleted
        the column -- an assertion is a day and a balance.
        """
        with app.app_context():
            account = seed_user["account"]
            new_balance = Decimal("1234.56")
            _make_anchor_history(
                account_id=account.id,
                anchor_balance=new_balance,
            )
            db.session.commit()

            result = resolve_anchor(account)

            assert isinstance(result, AnchorPoint)
            # 1234.56: the most recent AccountAnchorHistory row's
            # anchor_balance.  Hand-computed equality, not "> 0".
            assert result.balance == Decimal("1234.56")
            assert result.observed_on == display_today()

    # ── C4-2 -----------------------------------------------------------

    def test_the_latest_assertion_is_the_latest_BUSINESS_day(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-2: an assertion observed EARLIER but RECORDED later is NOT current.

        The rule the whole anchor/settle partition turns on, and which nothing
        pinned until plan step X-f1c3a made this resolver the single answer for
        twelve surfaces.  ``resolve_anchor`` orders
        ``(observed_on, created_at, id)`` descending -- BUSINESS day first --
        so it names the row the walk replays LAST.  Ordering on ``created_at``
        alone (which ``dashboard_service._get_last_anchor_date`` did until
        ruling R-EP deleted it) names the other row.

        Setup: the seed origination row asserts 1000.00 for its own day.  We add
        an assertion of 2500.00 observed TODAY, then a LATER-RECORDED assertion
        of 4444.44 observed a week BEFORE it.

        Expected: 2500.00.  Hand-computed: today > today - 7, so the row with
        the later BUSINESS day is current even though the 4444.44 row was
        written second.  This test fails with ``created_at``-first ordering,
        which is exactly the mutation it exists to kill.
        """
        with app.app_context():
            account = seed_user["account"]
            period_id = seed_user["bootstrap_period"].id
            today = display_today()

            current = _make_anchor_history(
                account_id=account.id,
                anchor_balance=Decimal("2500.00"),
                observed_on=today,
            )
            superseded = _make_anchor_history(
                account_id=account.id,
                anchor_balance=Decimal("4444.44"),
                observed_on=today - timedelta(days=7),
            )
            db.session.commit()

            # The later-recorded row has the HIGHER id, so id-descending alone
            # would also name it -- which is what makes this control fire.
            assert superseded.id > current.id

            result = resolve_anchor(account)

            # 2500.00: the assertion with the latest BUSINESS day.
            assert result.balance == Decimal("2500.00")
            assert result.observed_on == today

    # ── C4-3 -----------------------------------------------------------

    def test_resolve_anchor_never_none(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-3: a freshly factory-built account resolves to an
        ``AnchorPoint``, never ``None``.

        The Commit 3 invariant is that every account row has at
        least one matching ``AccountAnchorHistory`` row from the
        moment of creation -- the canonical factory writes both in
        one transaction.  This test exercises that invariant via the
        resolver.
        """
        with app.app_context():
            account = seed_user["account"]
            result = resolve_anchor(account)
            assert result is not None
            assert isinstance(result, AnchorPoint)
            # 1000.00: seed_user fixture's origination anchor.
            assert result.balance == Decimal("1000.00")

    # ── C4-4 -----------------------------------------------------------

    def test_resolve_anchor_agrees_with_reconciled_through(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-4: the resolver's day IS ``reconciled_through``'s day.

        Two statements of "when was this balance last asserted": the resolver
        takes the first row of a ``(observed_on, created_at, id)`` DESC ordering,
        and :func:`~app.services.cash_ledger.reconciled_through` takes
        ``MAX(observed_on)``.  Ruling R-EP puts three "as of" captions on them --
        the grid header and the account/investment heroes read the resolver, the
        dashboard pulse reads ``reconciled_through`` because it must answer
        ``None`` rather than raise -- so the equality is load-bearing rather than
        incidental, and this pins it.

        It replaced C4-4 (``resolve_anchor``'s ``scenario_id`` parameter is not
        scenario-scoping) at plan step X-f1c3a, which deleted the parameter: a
        contract nothing can express no longer needs a test.

        Arithmetic: with assertions on two different business days the MAX and
        the DESC-first must both name the LATER one; a resolver that ordered on
        the recording instant would disagree here, which is the mutation that
        kills this test.
        """
        with app.app_context():
            account = seed_user["account"]
            period_id = seed_user["bootstrap_period"].id
            today = display_today()

            newer = _make_anchor_history(
                account_id=account.id,
                anchor_balance=Decimal("2500.00"),
                observed_on=today,
            )
            older = _make_anchor_history(
                account_id=account.id,
                anchor_balance=Decimal("4444.44"),
                observed_on=today - timedelta(days=7),
            )
            db.session.commit()

            assert resolve_anchor(account).observed_on == today
            assert reconciled_through(account.id).observed_day == today
            assert (
                resolve_anchor(account).observed_on
                == reconciled_through(account.id).observed_day
            )

    # ── C4-5 -----------------------------------------------------------

    def test_resolve_anchor_decimal_type(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-5: ``AnchorPoint.balance`` is a ``Decimal`` quantized to
        two decimal places.

        ``Numeric(12, 2)`` is the storage type, so the SQLAlchemy
        adapter already returns ``Decimal`` rows with two fractional
        digits.  ``Decimal(str(...))`` preserves that representation.
        Arithmetic: a Decimal with exponent ``-2`` is the canonical
        cents-precision form; this is the contract every consumer in
        Commits 5-10 relies on.
        """
        with app.app_context():
            account = seed_user["account"]
            result = resolve_anchor(account)
            assert isinstance(result.balance, Decimal)
            # Exponent of -2 means two digits after the decimal point.
            assert result.balance.as_tuple().exponent == -2

    # ── C4-6 -----------------------------------------------------------

    def test_resolve_anchor_zero_balance_is_value(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-6: an anchor of ``Decimal("0.00")`` is honored as a
        value, not coerced to a default or treated as "missing".

        Setup: appending a true-up history row with anchor 0.00 on
        the bootstrap period.  The
        coding-standard rule (E-12 / CLAUDE.md "do not rely on
        truthiness for business logic") is the regression lock: code
        that wrote ``or Decimal("0.00")`` or ``if not balance:`` would
        silently swap the zero for something else; the resolver must
        preserve the zero verbatim.
        """
        with app.app_context():
            account = seed_user["account"]
            bootstrap_period_id = seed_user["bootstrap_period"].id

            _make_anchor_history(
                account_id=account.id,
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()

            result = resolve_anchor(account)

            # 0.00 (E-12): zero is a value; the resolver returns it
            # verbatim instead of falling back to a non-zero default.
            assert result.balance == Decimal("0.00")
            assert result.balance.as_tuple().exponent == -2
            # Make the "value, not missing" assertion explicit: the
            # resolver MUST distinguish Decimal("0.00") from None.
            assert result.balance is not None


class TestResolveAnchorMissingHistory:
    """The defensive ``RuntimeError`` for the no-history edge case.

    Commit 3 makes this state unreachable in production; the
    resolver's loud failure here is the regression trap for any
    future code path that constructs an ``Account`` row outside the
    canonical factory.  Deleting the origination row simulates that
    regression.
    """

    def test_runtime_error_when_no_history_exists(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """Resolver raises ``RuntimeError`` (not silently None) when
        zero history rows exist.

        Arithmetic: not applicable -- this is the defensive failure
        path.  The assertion is that the error message names the
        account id and points at the canonical factory.
        """
        with app.app_context():
            # **Built rather than emptied** (plan step X-f3c-2c): an assertion
            # is append-only at the database tier, so an account that has
            # asserted nothing is one the assertion factory never touched.
            account = account_never_asserted(
                seed_user, db.session, name="Silent",
            )
            db.session.commit()

            with pytest.raises(RuntimeError, match=str(account.id)):
                resolve_anchor(account)


class TestAnchorPointDataclass:
    """Static contract tests for the :class:`AnchorPoint` dataclass."""

    def test_anchor_point_is_frozen(self):
        """``AnchorPoint`` is immutable -- writes to its fields raise.

        Frozen dataclasses are the project's chosen shape for
        canonical-producer return values: a consumer cannot mutate
        the resolver's output and have that mutation silently affect
        a sibling consumer.  It needed a transient ``PayPeriod`` instance
        until ruling R-EO deleted :attr:`AnchorPoint.period`; every field is
        now a plain value, so this stays a pure-Python test for a simpler
        reason than before.
        """
        anchor = AnchorPoint(
            anchor_id=1,
            balance=Decimal("100.00"),
            observed_on=_date(2026, 1, 1),
            created_at=_datetime(2026, 1, 1, 12, tzinfo=_timezone.utc),
        )
        with pytest.raises(FrozenInstanceError):
            anchor.balance = Decimal("999.00")  # type: ignore[misc]
