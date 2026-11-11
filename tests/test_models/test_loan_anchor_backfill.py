"""Tests for the Commit 12 / d3d25212504b migration backfill (E-18).

The migration creates ``budget.loan_anchor_events`` then seeds an
``origination`` event for every existing loan account -- one per
loan, with ``(origination_date, original_principal)`` from the
immutable :class:`LoanParams` fields -- so the Commit 13 resolver
reproduces today's displayed principal on first read.

The migration is already at HEAD when these tests run (the
template builder upgraded it), so existing rows show the post-
migration state.  Each test sets up a NEW loan account, then
invokes the migration's backfill helpers directly to assert the
deterministic mapping from ``LoanParams`` shape to event rows.

Test cases pinned to the plan (Commit 12 section E):

  * C12-2: every existing loan has an origination event.
  * C12-6: downgrade drops table + ref values cleanly.

The C12-3/C12-4 ``user_trueup`` divergence tests (a trueup row
inserted iff stored ``current_principal`` differed from a from-
origination confirmed-payment replay) were REMOVED 2026-06-11
(developer-ratified): DH-#56's migration (``b7d2f4a619c5``) dropped
the ``loan_params.interest_rate`` column the trueup backfill's
``_LOAN_ENUMERATION_SQL`` reads, so the helper cannot run against
the post-drop schema these tests use, and no live code shares its
logic to retarget at (the divergence check exists only inside the
historical migration).  The one-time backfill ran correctly
in-chain -- every base->head template build still executes it --
which is the coverage that remains.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import InterestAccrualConventionEnum, LoanAnchorSourceEnum
from app.extensions import db as _db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.services import account_service
from tests._test_helpers import insert_origination_rate


# ---------------------------------------------------------------------------
# Migration module loader -- pattern from
# tests/test_models/test_account_anchor_invariant.py
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename):
    """Load an Alembic migration module by path via importlib."""
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_LOAN_BACKFILL = _load_migration(
    "d3d25212504b_create_loan_anchor_events_table_for_.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _origination_id():
    """Return the ``origination`` source row's integer PK."""
    return ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.ORIGINATION)


def _create_loan_account(seed_user, db_session, *,
                         type_name="Mortgage",
                         original_principal=Decimal("250000.00"),
                         anchor_balance=Decimal("200000.00"),
                         rate=Decimal("0.06500"),
                         term_months=360,
                         origination_date=date(2024, 1, 1),
                         payment_day=1,
                         is_arm=False,
                         name="Test Loan"):
    """Create a loan account + LoanParams for tests.

    Deliberately NOT routed through ``_test_helpers.create_loan_account`` (see the
    same note on ``test_loan_anchor_event.py``'s builder).  This module tests the
    d3d25212504b MIGRATION's origination backfill, which reads the immutable
    :class:`LoanParams` fields, so it constructs the PRE-migration table state by
    hand and touches no balance seam.  (It once also carried a
    ``current_principal`` distinct from ``original_principal`` for that
    migration's true-up backfill; plan step R20 dropped the column and the
    ``anchor_balance`` here is the account's cash anchor, a decoy the
    origination backfill must not read.)  Keep it hand-rolled; it is not an
    oversight.

    Mirrors the helper in ``test_loan_anchor_event.py`` but with a
    runtime-selectable type so individual tests can choose a fixed-
    rate or ARM-eligible account type.
    """
    loan_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name=name,
            anchor_balance=anchor_balance,
        ),
    )
    db_session.flush()
    params = LoanParams(
        account_id=account.id,
        original_principal=original_principal,
        interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
        term_months=term_months,
        origination_date=origination_date,
        payment_day=payment_day,
        is_arm=is_arm,
    )
    db_session.add(params)
    db_session.flush()
    # DH-#56: the loan's rate now lives in an origination RateHistory
    # row (effective at origination_date), not the dropped
    # LoanParams.interest_rate column.
    insert_origination_rate(params, rate)
    db_session.commit()
    return account, params


def _drop_anchor_events_for_account(account_id):
    """Remove any anchor events for ``account_id`` outside the ORM.

    The model blocks ORM-mediated DELETE (append-only invariant),
    so the test setup uses a raw DELETE to wipe events left behind
    by earlier backfill runs (e.g. from the seed_user's existing
    state) before exercising the backfill helpers on a freshly
    created loan.  Direct SQL bypasses the ORM listener.
    """
    _db.session.execute(_db.text(
        "DELETE FROM budget.loan_anchor_events WHERE account_id = :a"
    ), {"a": account_id})
    _db.session.commit()


def _run_origination_backfill():
    """Execute the migration's idempotent origination backfill SQL.

    The SQL is exposed as a module-level constant so tests can re-
    run it on engineered fixtures without duplicating the text.
    """
    _db.session.execute(_db.text(_M_LOAN_BACKFILL._BACKFILL_ORIGINATION_SQL))
    _db.session.commit()


# ---------------------------------------------------------------------------
# C12-2: origination event materialised for every loan
# ---------------------------------------------------------------------------


class TestOriginationBackfill:
    """C12-2: every loan account carries one ``origination`` event."""

    def test_origination_row_inserted_with_immutable_fields(
        self, app, db, seed_user,
    ):
        """Backfill inserts (origination_date, original_principal, origination).

        Arithmetic: the LoanParams row carries
        ``origination_date=2024-01-01`` and
        ``original_principal=Decimal('250000.00')``; the backfill
        copies both verbatim into the event row, tags the source
        with the ``origination`` ref ID, and creates exactly one row.
        """
        with app.app_context():
            account, params = _create_loan_account(seed_user, _db.session)
            _drop_anchor_events_for_account(account.id)

            _run_origination_backfill()

            events = (
                _db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            )
            assert len(events) == 1
            event = events[0]
            assert event.anchor_date == params.origination_date
            assert event.anchor_balance == params.original_principal
            assert event.source_id == _origination_id()

    def test_origination_backfill_is_idempotent(self, app, db, seed_user):
        """Re-running the backfill does not duplicate origination events.

        The migration's ``NOT EXISTS`` guard relies on the natural
        ``(account_id, source=origination)`` key.  Two runs in a row
        must leave exactly one row.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            _drop_anchor_events_for_account(account.id)

            _run_origination_backfill()
            _run_origination_backfill()

            count = (
                _db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    source_id=_origination_id(),
                )
                .count()
            )
            assert count == 1

    def test_every_loan_has_an_origination_event_after_backfill(
        self, app, db, seed_user,
    ):
        """After the full backfill, every loan in the DB has >= 1 origination row.

        Spec gate: "A test asserts every existing loan account has
        at least one origination row after upgrade, with anchor_date
        == origination_date and anchor_balance == original_principal."
        """
        with app.app_context():
            # Create two loans so the assertion is non-trivial.
            account1, params1 = _create_loan_account(
                seed_user, _db.session, name="Loan A",
                original_principal=Decimal("180000.00"),
                anchor_balance=Decimal("180000.00"),
                origination_date=date(2024, 6, 1),
            )
            account2, params2 = _create_loan_account(
                seed_user, _db.session, type_name="Auto Loan",
                name="Loan B",
                original_principal=Decimal("30000.00"),
                anchor_balance=Decimal("30000.00"),
                rate=Decimal("0.05500"),
                term_months=60,
                origination_date=date(2025, 1, 1),
            )
            _drop_anchor_events_for_account(account1.id)
            _drop_anchor_events_for_account(account2.id)

            _run_origination_backfill()

            for account, params in [(account1, params1), (account2, params2)]:
                rows = (
                    _db.session.query(LoanAnchorEvent)
                    .filter_by(
                        account_id=account.id,
                        source_id=_origination_id(),
                    )
                    .all()
                )
                assert len(rows) == 1, (
                    f"Loan account {account.id} missing origination event."
                )
                assert rows[0].anchor_date == params.origination_date
                assert rows[0].anchor_balance == params.original_principal


# ---------------------------------------------------------------------------
# C12-6: downgrade smoke check
# ---------------------------------------------------------------------------


class TestDowngradeSmoke:
    """C12-6: downgrade SQL is exposed; structural elements named correctly.

    A full upgrade -> downgrade -> upgrade round-trip belongs in
    the Alembic-driven environment (it requires re-running the
    entire migration chain in transactional isolation, which is
    out of scope for an in-test xdist worker).  The smoke check
    asserts the migration declares the expected revision pair and
    that the downgrade function references the right artefact
    names -- a stricter check would re-execute the chain, which
    is what the manual verification step in the commit's section F
    covers.
    """

    def test_migration_revision_pair(self):
        """The migration declares the expected (revision, down_revision)."""
        assert _M_LOAN_BACKFILL.revision == "d3d25212504b"
        assert _M_LOAN_BACKFILL.down_revision == "cfb15e782f86"

    def test_downgrade_drops_named_artefacts(self):
        """Downgrade source references the table, indexes, and ref table.

        Surface check on the migration module's text so a future
        edit cannot silently re-route the downgrade past one of
        the artefacts the upgrade materialises.  A missing artefact
        would leave a half-reverted schema -- the bare-pass downgrade
        failure mode the coding standard explicitly forbids.
        """
        downgrade_source = (
            _MIGRATIONS_DIR / "d3d25212504b_create_loan_anchor_events_table_for_.py"
        ).read_text()
        assert "drop_index" in downgrade_source
        assert "uq_loan_anchor_events_acct_date_bal_day" in downgrade_source
        assert "idx_loan_anchor_events_account" in downgrade_source
        # Accept either single or double quotes around the table name
        # since both forms are valid Python and Alembic autogenerate
        # can emit either depending on the local formatter.
        assert 'drop_table("loan_anchor_events"' in downgrade_source \
            or "drop_table('loan_anchor_events'" in downgrade_source
        assert 'drop_table("loan_anchor_sources"' in downgrade_source \
            or "drop_table('loan_anchor_sources'" in downgrade_source


# ---------------------------------------------------------------------------
# C8-4 / C8-6: amortization-engine split Commit 8 (inline replay)
# ---------------------------------------------------------------------------


class TestInlineReplayDecoupling:
    """C8-4 / C8-6: the migration replay is self-contained, math-equivalent.

    Commit 8 of the amortization-engine split implementation
    (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``)
    inlined the migration's replay loop so the migration survives the
    Commit 9 deletion of the legacy engine schedule function.  These
    tests pin two properties: (1) the migration module no longer
    imports or calls the legacy engine function, and (2) the inline
    helper produces bit-for-bit the same balance the original engine-
    backed walk produced for the migration's input shape.
    """

    def test_migration_does_not_reference_engine_module_or_function(self):
        """C8-4: migration source has zero literal references to the engine call.

        Surface check on the migration's text so a future edit
        cannot quietly re-introduce the dependency the engine
        deletion in Commit 9 will break.  ``amortization_engine``
        and ``generate_schedule`` must both be absent from the
        migration source (no import, no call, no docstring mention
        that would mask a real reintroduction during a grep audit).
        """
        migration_source = (
            _MIGRATIONS_DIR
            / "d3d25212504b_create_loan_anchor_events_table_for_.py"
        ).read_text()
        assert "amortization_engine" not in migration_source
        assert "generate_schedule" not in migration_source

    @pytest.mark.parametrize(
        "case_name,original_principal,annual_rate,term_months,"
        "origination_date,payments,expected_balance",
        [
            (
                "no_payments",
                Decimal("200000.00"),
                Decimal("0.06500"),
                360,
                date(2024, 1, 1),
                [],
                # No confirmed payment means ``last_confirmed_balance``
                # is never captured and the helper returns the original
                # principal unchanged.
                Decimal("200000.00"),
            ),
            (
                "three_confirmed_payments",
                Decimal("200000.00"),
                Decimal("0.06500"),
                360,
                date(2024, 1, 1),
                [
                    (date(2024, 2, 1), Decimal("1264.14"), True),
                    (date(2024, 3, 1), Decimal("1264.14"), True),
                    (date(2024, 4, 1), Decimal("1264.14"), True),
                ],
                # Hand arithmetic at $200,000 / 6.5% / 360, monthly
                # rate 0.065 / 12 = 0.00541666...:
                #   month 1: int = 200000 * 0.005416666... = 1083.33
                #             prin = 1264.14 - 1083.33 = 180.81
                #             bal  = 199819.19
                #   month 2: int = 199819.19 * 0.005416666... = 1082.35
                #             prin = 1264.14 - 1082.35 = 181.79
                #             bal  = 199637.40
                #   month 3: int = 199637.40 * 0.005416666... = 1081.37
                #             prin = 1264.14 - 1081.37 = 182.77
                #             bal  = 199454.63
                Decimal("199454.63"),
            ),
            (
                "mixed_confirmed_and_projected",
                Decimal("250000.00"),
                Decimal("0.06000"),
                360,
                date(2024, 6, 1),
                [
                    (date(2024, 7, 1), Decimal("1498.88"), True),
                    (date(2024, 8, 1), Decimal("1498.88"), True),
                    (date(2024, 9, 1), Decimal("1498.88"), False),
                    (date(2024, 10, 1), Decimal("1498.88"), True),
                    (date(2024, 11, 1), Decimal("1498.88"), False),
                ],
                # Hand arithmetic at $250,000 / 6.0% / 360, monthly rate
                # 0.06 / 12 = 0.005:
                #   month 1 (Jul, confirmed): int = 1250.00,
                #     prin = 248.88, bal = 249751.12, last_conf = bal
                #   month 2 (Aug, confirmed): int = 249751.12 * 0.005
                #     = 1248.76 (1248.7556 -> ROUND_HALF_UP 1248.76),
                #     prin = 250.12, bal = 249501.00, last_conf = bal
                #   month 3 (Sep, projected): int = 249501.00 * 0.005
                #     = 1247.51 (1247.505 -> ROUND_HALF_UP 1247.51),
                #     prin = 251.37, bal = 249249.63
                #     (projected month does NOT update last_conf)
                #   month 4 (Oct, confirmed): int = 249249.63 * 0.005
                #     = 1246.25 (1246.24815 -> ROUND_HALF_UP 1246.25),
                #     prin = 252.63, bal = 248997.00, last_conf = bal
                #   month 5 (Nov, projected): does NOT update last_conf.
                Decimal("248997.00"),
            ),
        ],
    )
    def test_inline_replay_hand_anchored_balance(
        self, case_name, original_principal, annual_rate, term_months,
        origination_date, payments, expected_balance,
    ):
        """C8-6: inline replay produces the hand-anchored balance.

        Pre-Commit-9 this test cross-checked against
        ``amortization_engine.generate_schedule``; Commit 9 deletes
        ``generate_schedule`` and the cross-check moves to
        hand-computed values for each parametrized case (every
        ``expected_balance`` is derived inline in the parametrize
        table from the per-month amortization formula).

        Covers three representative loan shapes: no payments at all
        (helper returns ``original_principal``), a short run of three
        confirmed contractual payments (last_confirmed_balance after
        month 3), and a mixed confirmed / projected run (only
        confirmed months capture last_confirmed_balance).
        """
        inline_result = _M_LOAN_BACKFILL._replay_from_origination_inline(
            original_principal=original_principal,
            annual_rate=annual_rate,
            term_months=term_months,
            origination_date=origination_date,
            payments=payments,
        )

        assert inline_result == expected_balance, (
            f"{case_name}: inline {inline_result} != "
            f"hand-anchored {expected_balance}"
        )
