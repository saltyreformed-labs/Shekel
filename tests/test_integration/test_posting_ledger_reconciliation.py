"""The posting-ledger reconciliation oracle (Build-Order Step 2, Commit 6).

This is the correctness gate for the double-entry posting ledger.  Reads stay
on the ``balance_at`` seam over ``budget.transactions`` (Step 2 changes no read
path), so the ledger is validated against the **settled-transfer subset** of
transactions rather than against full projected balances.  The invariants
below are exactly plan Section 6:

  1. **Per-account reconciliation** (asset AND liability legs): for each real
     account A, the net of A's posting legs equals the net effect of A's
     sides of settled, non-deleted transfers -- the transfer's settled figure
     ``+figure`` for a to-side record (money in -> a debit), ``-figure`` for
     a from-side record (money out -> a credit), the record being the side's
     covering movement.  It hung off the side's shadow until plan step
     ``balance:X-bi-6-4d-2`` and hangs off the transfer since, and the figure
     is stored once on the transfer since that step's 4d-4 half; the oracle
     keeps the name "settled-shadow effect" (ruling R-BAL167 class 4).
  2. **Per-entry balance**: every journal entry's legs ``SUM(amount) = 0`` and
     ``COUNT(*) >= 2`` (also DB-enforced by ``ck_account_postings_balanced``).
  3. **Trial balance**: ``SUM(account_postings.amount) = 0`` across the whole
     ledger (follows from 2, asserted directly as a cheap self-check).
  4. **Multi-scenario isolation**: postings in scenario X never reconcile
     against transactions in scenario Y (the ``scenario_id`` denorm is honored).
  5. **Owner isolation via ``journal_entry.user_id``**: a posting carries no
     ``user_id`` of its own; its owner is reached only through its journal
     entry, and one owner's reconciliation never picks up another owner's
     postings.  (Plan Section 6 calls this the "companion-owner case".  A
     companion user cannot in fact OWN a transfer -- ``transfer_service``
     refuses any actor that does not own the from/to accounts, and a companion
     owns none -- so the property the plan actually names, ownership inherited
     via ``journal_entry.user_id``, is proven here with a second independent
     owner, which exercises the identical join.)

The oracle also holds over BOTH historical-backfilled postings (the raw-SQL
migration builder) and go-forward postings (the ``posting_service`` Python
builder): a dedicated case posts one transfer each way and asserts the two
producers agree leg-for-leg and reconcile identically.

**Non-tautological by construction.**  Each invariant is checked three
independent ways that must all agree:

  * **hand-computed literals** -- the expected ledger sums are the test
    author's arithmetic over the seeded transfer amounts (e.g. Checking sends
    $100 + $250, so its ledger MUST be exactly ``-350.00``), owing nothing to
    either producer or either service helper;
  * **independent cross-table reconciliation** -- the ledger side
    (``_independent_ledger_sum``) reads the ``account_postings`` table through a
    different join shape than ``account_posting_total``, and the transaction
    side (``_independent_txn_effect``) reads the ``transfers`` and
    ``transaction_entries`` tables;
    asserting the two equal reconciles what the producers WROTE against the
    transaction source of truth.  (The transaction side necessarily restates the
    one correct definition of the settled-shadow effect, so it mirrors
    ``settled_transfer_effect``'s semantics; the hand-computed literals, not this
    query, are what make the oracle non-tautological -- this layer adds the
    cross-table, whole-DB sweep the literals cannot.)
  * **the production service helpers** -- ``account_posting_total`` and
    ``settled_transfer_effect`` (the readers Steps 4-5 will switch balances
    onto) must match the hand-computed literals too.

Two adversarial cases prove the oracle is not vacuous: tampering a settled
shadow makes the per-account reconciliation FAIL -- driven through the real
``_assert_full_reconciliation`` sweep helper under ``pytest.raises``, so a
regression in the helper itself is caught, not only in an inline re-derivation
(so a real ledger drift would be caught) -- and injecting one extra leg makes the
trial balance go non-zero (so the ``= 0`` assertion is a real check, not one the
per-entry trigger makes unconditionally true).

All money is ``Decimal`` from strings, with the arithmetic shown per the
testing standard.  Transfers are built through ``transfer_service`` (the sole
writer), so every shadow obeys the transfer invariants exactly as production
produces them; Commit 5 wiring auto-posts each settle.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import case

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import posting_service, transfer_service
from app.utils.balance_predicates import settled_status_ids
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    linked_ledger_account,
    transfer_family_journal_filter,
)
from app.services import cash_ledger
from app.models.amount_ownership import AmountOwnership


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These deliberately re-derive each side from scratch so the oracle is a
# genuine second opinion: a bug shared by the two service helpers cannot hide,
# because the ledger side here reads ``account_postings`` and the transaction
# side reads ``transfers`` and their side records with independently-written
# SQL, and both are also pinned to hand-computed literals.


def _independent_ledger_sum(account_id: int, scenario_id: int) -> Decimal:
    """Sum a real account's LINKED posting legs in a scenario (independent query).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` (for the real ``account_id``), summing the signed
    ``amount``.  Keyed off the REAL account via ``ledger_accounts.account_id``,
    a different join shape than ``posting_service.account_posting_total`` (which
    resolves the ledger account first), so the two cannot share a lookup bug.

    Filtered to the LINKED kind (Step 5): an anchor correction lands
    ``+delta`` on the linked row and ``-delta`` on the ``anchor_equity``
    twin, which shares the ``account_id`` column -- a bare-``account_id``
    sum would cancel the correction pairwise and silently reproduce the
    pre-Step-5 changes-only figure, making the absolute assertions
    vacuous.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _opening_anchor(account_id: int) -> Decimal:
    """Return an account's anchor balance -- its posted opening's target.

    The Step-5 opening correction drives the linked ledger to exactly the
    account's asserted anchor at creation (every account in this suite
    carries only its origination assertion, and every settle is stamped at
    server-now, after it), so the absolute reconciliation is
    ``linked ledger == anchor + settled effect``.
    """
    # The account's asserted balance, read from the assertion itself: ruling
    # R-EH deleted the ``accounts.current_anchor_balance`` column this queried,
    # which was a copy of the same row.
    return cash_ledger.resolve_anchor(
        _db.session.get(Account, account_id),
    ).balance


def _independent_txn_effect(account_id: int, scenario_id: int) -> Decimal:
    """Sum an account's settled transfer-side effect (independent query).

    The balance-side truth the ledger must equal: over the RECORDS on the
    account filed under a side of a settled (``status.is_settled``),
    non-deleted transfer in *scenario_id*, add the TRANSFER's settled figure
    (``budget.transfers.settled_figure``) as ``+figure`` for a to-side record
    (``income_transfer_id``, money in) and ``-figure`` for a from-side record
    (``expense_transfer_id``, money out).  The figure is stored once on the
    transfer since plan step ``balance:X-bi-6-4d-2`` (its 4d-4 half); each
    side's record carried it until then and states no figure now.  A settled
    transfer holding no record is a ``$0.00`` close and adds nothing
    (R-BAL82).  It summed the settled transfer SHADOWS' figures by each
    shadow's status and type until plan step ``balance:X-bi-6-4d-2`` stopped
    keeping a shadow's status and moved the record onto the transfer; it
    reads the transfer's status, figure and the side links by its own SQL
    now, never through ``transfer_legs``, the producer
    ``settled_transfer_effect`` joins (ruling R-BAL167 class 4, plan step
    balance:X-bi-6-4d-2).  Reads the row tables -- different tables than
    :func:`_independent_ledger_sum` -- so asserting the two equal reconciles
    what the producers wrote against the transaction source of truth.
    """
    signed = case(
        (
            TransactionEntry.income_transfer_id.isnot(None),
            Transfer.settled_figure,
        ),
        else_=-Transfer.settled_figure,
    )
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(signed), Decimal("0"))
        )
        .select_from(Transfer)
        .join(
            TransactionEntry,
            _db.or_(
                TransactionEntry.income_transfer_id == Transfer.id,
                TransactionEntry.expense_transfer_id == Transfer.id,
            ),
        )
        .filter(
            TransactionEntry.account_id == account_id,
            Transfer.scenario_id == scenario_id,
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def _trial_balance() -> Decimal:
    """Return ``SUM(account_postings.amount)`` over the whole ledger."""
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .scalar()
    )


def _entries_violating_balance() -> list[tuple[int, Decimal, int]]:
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.
    Any row returned here is a violation -- the per-entry invariant the
    deferred trigger also enforces, re-checked from the ORM side.
    """
    rows = (
        _db.session.query(
            Posting.journal_entry_id,
            _db.func.sum(Posting.amount),
            _db.func.count(Posting.id),
        )
        .group_by(Posting.journal_entry_id)
        .all()
    )
    return [
        (entry_id, leg_sum, leg_count)
        for entry_id, leg_sum, leg_count in rows
        if leg_sum != 0 or leg_count < 2
    ]


def _assert_full_reconciliation(scenario_id: int) -> None:
    """Assert every non-loan linked ledger reconciles ABSOLUTELY in *scenario_id*.

    The production-wide sweep in its Step-5 absolute form: for each non-loan
    real account (its LINKED ledger row), the independent ledger sum equals
    the account's opening anchor plus the independent settled-shadow effect
    (every settle in this suite is stamped at server-now, after the
    origination assertion).  Amortizing loans are excluded -- their absolute
    invariant couples on the amortization split and is the loan oracle's job
    (the ``test_posting_ledger_loan_reconciliation`` package).  Holds over every
    account that has postings, not only the ones a given test hand-computes.

    Scenario caveat: a NON-baseline scenario carries the openings here only
    because these fixtures settle on the same UTC day the accounts were
    created, so the effect-time self-heal fires alongside the scenario's
    first settle.  That is NOT a general guarantee -- a what-if whose first
    settle lands on a later day gets its openings only at the next
    account-global sync; R8 owns the residual multi-scenario policy, and a
    future scenario-clone path must sync the new scenario explicitly.
    """
    scenario_owner_id = (
        _db.session.query(Scenario.user_id)
        .filter(Scenario.id == scenario_id)
        .scalar()
    )
    linked = (
        _db.session.query(LedgerAccount)
        .join(Account, LedgerAccount.account_id == Account.id)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            AccountType.has_amortization.is_(False),
            # Scoped to the scenario OWNER's accounts: another owner's
            # account has neither postings nor an opening in this scenario,
            # so the absolute form is not a statement about it.  See the
            # docstring's scenario caveat for why the swept scenarios all
            # carry their owner's openings HERE.
            Account.user_id == scenario_owner_id,
        )
        .all()
    )
    # Every caller settles at least one transfer, which mints both legs' linked
    # ledger accounts, so an empty result means the query silently found nothing
    # (a minting or filter regression) and the loop below would pass vacuously --
    # assert non-empty so the sweep cannot be a no-op.
    assert linked, (
        "no linked ledger accounts to reconcile -- the sweep would be vacuous "
        "(expected at least the transfer's two linked ledgers)"
    )
    for ledger_account in linked:
        ledger = _independent_ledger_sum(ledger_account.account_id, scenario_id)
        effect = _independent_txn_effect(ledger_account.account_id, scenario_id)
        opening = _opening_anchor(ledger_account.account_id)
        assert ledger == opening + effect, (
            f"account {ledger_account.account_id}: ledger {ledger} != "
            f"opening {opening} + settled-shadow effect {effect} in "
            f"scenario {scenario_id}"
        )


def _legs_by_account(account_id: int, transfer_id: int) -> dict[int, Decimal]:
    """Return ``{journal_entry_id: leg_amount}`` for one transfer's legs on an account.

    Used to compare a transfer posted go-forward against the same transfer
    posted by the backfill: both must land the same signed amount on the
    account's LINKED ledger.  Scoped by the transfer's FAMILY (its two
    per-movement entries since plan step ``balance:X-bi-6-3``,
    ``transfer_family_journal_filter``) AND the LINKED kind: a Step-5 anchor
    correction also lands legs sharing the ``account_id`` (its equity twin
    carries the negation), so a bare-``account_id`` map would fold
    correction legs into the transfer comparison.
    """
    return {
        entry_id: amount
        for entry_id, amount in _db.session.query(
            Posting.journal_entry_id, Posting.amount
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            transfer_family_journal_filter(transfer_id),
        )
        .all()
    }


def _build_asset_and_liability_books(seed_user) -> tuple:
    """Settle Checking -> Savings $100 and Checking -> Mortgage $250 (committed).

    Assumes an app context is active.  Builds a Savings (asset) and a Mortgage
    (liability) destination so a caller can hand-check BOTH an asset leg and a
    liability leg, plus one unsettled (Projected) $40 transfer that must
    contribute nothing to either side.  Hand-computed ledger expectations in
    the baseline scenario:

        Checking  -100.00 - 250.00 = -350.00   (the $40 Projected posts nothing)
        Savings   +100.00
        Mortgage  +250.00
        trial     -350.00 + 100.00 + 250.00 = 0.00

    Returns ``(savings, mortgage)``.
    """
    checking = seed_user["account"]
    period = seed_user["bootstrap_period"]
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Oracle Savings",
    )
    mortgage = create_account_of_type(
        seed_user, _db.session, "Mortgage", "Oracle Mortgage",
    )
    _db.session.commit()

    create_settled_transfer(
        seed_user, _db.session, checking, savings, period,
        amount=Decimal("100.00"),
    )
    create_settled_transfer(
        seed_user, _db.session, checking, mortgage, period,
        amount=Decimal("250.00"),
    )
    # An unsettled (Projected) transfer: it must post nothing and drop out of
    # the settled-shadow effect, so neither side of the oracle sees it.
    transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=checking.id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("40.00")),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
        ),
    )
    _db.session.commit()
    return savings, mortgage


# ---------------------------------------------------------------------------
# 1. Per-account reconciliation (asset + liability legs)
# ---------------------------------------------------------------------------


class TestPerAccountReconciliation:
    """Each account's ledger sum equals its settled transfer-shadow effect."""

    @pytest.mark.server_clock
    def test_asset_and_liability_legs_reconcile_three_ways(
        self, app, db, seed_user,
    ):
        """Checking/Savings/Mortgage reconcile by hand, query, and helper.

        Arithmetic (baseline scenario): Checking sends $100 to Savings and $250
        to Mortgage, so its settled effect is -350.00; Savings receives
        +100.00; Mortgage (a liability paid down) receives +250.00.  The
        unsettled $40 Projected transfer posts nothing.  On the LEDGER side
        each effect rides on the account's Step-5 opening -- Checking
        1000 - 350 = 650.00, Savings 100 + 100 = 200.00; the Mortgage is
        amortizing (no account-walk opening; it carries no LoanParams, so no
        loan genesis either), leaving its ledger at the bare +250.00.  All
        three independent computations -- hand-computed literal, independent
        cross-table query, and the production service helper -- must agree
        on every account.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings, mortgage = _build_asset_and_liability_books(seed_user)

            expected = {
                checking.id: (Decimal("650.00"), Decimal("-350.00")),
                savings.id: (Decimal("200.00"), Decimal("100.00")),
                mortgage.id: (Decimal("250.00"), Decimal("250.00")),
            }
            for account_id, (want_ledger, want_effect) in expected.items():
                # (a) hand-computed literal == independent ledger-table query.
                assert _independent_ledger_sum(
                    account_id, scenario_id,
                ) == want_ledger
                # (b) independent txn-table query == the hand-computed effect
                #     (postings reconcile to the transaction source of truth
                #     through the absolute sweep below).
                assert _independent_txn_effect(
                    account_id, scenario_id,
                ) == want_effect
                # (c) the production service helpers agree too (the readers
                #     Steps 4-5 will switch balances onto).
                assert posting_service.account_posting_total(
                    account_id, scenario_id,
                ) == want_ledger
                assert posting_service.settled_transfer_effect(
                    account_id, scenario_id,
                ) == want_effect

            # Production-wide sweep: every linked ledger account reconciles.
            _assert_full_reconciliation(scenario_id)

    def test_reverted_transfer_reconciles_at_zero(self, app, db, seed_user):
        """A settled-then-reverted transfer reconciles to zero on both sides.

        Arithmetic: settle +100 (Savings), then revert.  The transfer nets to
        zero (+100 settle, -100 reversal -- append-only), and the reverted
        income shadow is no longer ``is_settled`` so it drops from the
        settled-shadow effect too.  Each ledger lands back on its opening
        (Checking 1000.00, Savings 100.00) with a zero effect, and two
        transfer entries survive (the original is never edited).  This is
        the append-only correction discipline proven through the oracle.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Revert Savings",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Revert to Projected (Commit-5 wiring auto-reverses the posting).
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            for account_id, opening in (
                (checking.id, Decimal("1000.00")),
                (savings.id, Decimal("100.00")),
            ):
                assert _independent_ledger_sum(
                    account_id, scenario_id,
                ) == opening
                assert _independent_txn_effect(
                    account_id, scenario_id,
                ) == Decimal("0.00")
            # Four entries survive (a settle and a reversal per side, plan
            # step ``balance:X-bi-6-3``); none was edited.
            assert (
                _db.session.query(JournalEntry)
                .filter(transfer_family_journal_filter(transfer.id))
                .count()
            ) == 4
            _assert_full_reconciliation(scenario_id)

    @pytest.mark.server_clock
    def test_divergent_actual_amount_reconciles_on_effective(
        self, app, db, seed_user,
    ):
        """A settled actual that differs from the estimate reconciles on actual.

        This exercises the ``effective = COALESCE(actual, estimated)`` property
        the WHOLE ledger correction rests on: the posted amount is the side's
        recorded figure, never ``transfers.amount`` / the estimate.  The two
        diverge exactly when a settle records a figure other than the plan
        (``settled_amount`` here), so without this case every other test -- where
        ``actual`` is NULL and ``effective == estimated == amount`` -- would
        stay green even against a producer that wrongly posted the estimate.

        Arithmetic: a $100 nominal Checking -> Savings transfer settles with an
        actual of $97.50, so the transfer's settled figure is 97.50 (stored
        once on the transfer since plan step balance:X-bi-6-4d-2's 4d-4 half;
        each side's record carried it until then, and the side's shadow
        before that; ruling R-BAL167 class 4).  The posting MUST be
        -97.50 / +97.50,
        NOT -100 / +100 -- on the ledger side riding each opening (Savings
        100 + 97.50 = 197.50, Checking 1000 - 97.50 = 902.50).  A producer
        that posted the $100 estimate would leave Savings' ledger at +200
        while its opening + settled-shadow effect is +197.50 -- a divergence
        this case catches and the others cannot.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Divergent Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()

            # effective = COALESCE(97.50, 100.00) = 97.50 (actual over estimate),
            # NOT the $100 nominal amount; the ledger carries it on each
            # account's opening.
            expected = {
                savings.id: (Decimal("197.50"), Decimal("97.50")),
                checking.id: (Decimal("902.50"), Decimal("-97.50")),
            }
            for account_id, (want_ledger, want_effect) in expected.items():
                assert _independent_ledger_sum(
                    account_id, scenario_id,
                ) == want_ledger
                assert _independent_txn_effect(
                    account_id, scenario_id,
                ) == want_effect
                assert posting_service.account_posting_total(
                    account_id, scenario_id,
                ) == want_ledger
                assert posting_service.settled_transfer_effect(
                    account_id, scenario_id,
                ) == want_effect
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# Per-transfer completeness: no settled transfer is silently unposted
# ---------------------------------------------------------------------------


class TestEverySettledTransferPosts:
    """Every settled, non-deleted transfer posts at least one journal entry."""

    def test_no_settled_transfer_is_silently_unposted(
        self, app, db, seed_user,
    ):
        """Each settled transfer has >= 1 entry; none is silently skipped.

        The direct per-transfer backstop (the localized companion to the
        aggregate per-account reconciliation): the per-account check catches a
        SHORT account, but this pinpoints a settled transfer that posted
        NOTHING -- e.g. one whose account lacked a paired ledger account, which
        the historical backfill's INNER JOIN on ``ledger_accounts`` would
        silently skip.  The Commit-2 pairing guarantees coverage, so this holds
        in normal operation; the assertion is the production backstop the
        Commit-4 review asked the oracle to carry.

        Arithmetic: the two simple settles ($100 to Savings, $250 to Mortgage)
        each post exactly one entry, so the 2 settled non-deleted transfers map
        one-to-one onto the 2 TRANSFER-sourced journal entries (the Step-5
        account openings carry their own source kinds and are excluded); the
        Projected $40 transfer is not settled and posts none.
        """
        with app.app_context():
            _build_asset_and_liability_books(seed_user)
            settled = (
                _db.session.query(Transfer)
                .filter(
                    Transfer.user_id == seed_user["user"].id,
                    Transfer.is_deleted.is_(False),
                    Transfer.status_id.in_(settled_status_ids()),
                )
                .all()
            )
            # Two settled transfers; the Projected $40 is excluded.
            assert len(settled) == 2
            for xfer in settled:
                entry_count = (
                    _db.session.query(JournalEntry)
                    .filter(transfer_family_journal_filter(xfer.id))
                    .count()
                )
                assert entry_count >= 1, (
                    f"settled transfer {xfer.id} posted no journal entry"
                )
            # Simple settles post exactly TWO entries each -- one per side,
            # against transit (plan step ``balance:X-bi-6-3``) -- so the
            # settled transfers and the transfer-movement-sourced journal
            # entries are in a two-to-one bijection: no skip (an entry-less
            # settled transfer) and no stray double-post.
            assert (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                )
                .count()
            ) == 2 * len(settled)


# ---------------------------------------------------------------------------
# 2. Per-entry balance + 3. global trial balance
# ---------------------------------------------------------------------------


class TestPerEntryAndTrialBalance:
    """Every entry sums to zero with >= 2 legs; the whole ledger sums to zero."""

    def test_every_entry_balances_and_trial_balance_is_zero(
        self, app, db, seed_user,
    ):
        """Two settled transfers -> four balanced entries; trial balance 0.

        Arithmetic: the $100 and $250 settles each post two two-leg entries
        (one per side, against transit -- plan step ``balance:X-bi-6-3``)
        summing to zero, and the Step-5 openings (Checking +1000/-1000,
        Savings +100/-100 against their equity twins) are balanced pairs too,
        so no entry violates ``SUM = 0`` / ``COUNT >= 2`` and the
        whole-ledger total stays 0.00.
        """
        with app.app_context():
            _build_asset_and_liability_books(seed_user)

            # Exactly the two settled transfers produced transfer-movement-
            # sourced entries, two each (the $40 Projected posted none).
            assert (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                )
                .count()
            ) == 4
            # No entry violates the per-entry balanced invariant.
            assert _entries_violating_balance() == []
            # Whole-ledger trial balance is zero.
            assert _trial_balance() == Decimal("0.00")


# ---------------------------------------------------------------------------
# 4. Multi-scenario isolation
# ---------------------------------------------------------------------------


class TestMultiScenarioIsolation:
    """Postings in one scenario never reconcile against another scenario."""

    @pytest.mark.server_clock
    def test_postings_are_isolated_per_scenario(self, app, db, seed_user):
        """A $100 baseline and a $70 what-if transfer never bleed together.

        Arithmetic: Savings receives $100 in the baseline scenario and $70 in a
        separate what-if scenario, each riding the $100.00 opening its
        scenario carries (the opening posts per scenario: in the baseline at
        create time, in the what-if via the effect-time self-heal when the
        $70 settle posts there).  Scoped to baseline the Savings ledger is
        100 + 100 = 200.00 (NOT 270); scoped to the what-if it is
        100 + 70 = 170.00.  The ``scenario_id`` denorm on the journal entry
        keeps the two apart, and each scenario reconciles independently.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            baseline = seed_user["scenario"]
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Scenario Savings",
            )
            # A second, non-baseline scenario for the same user (the partial
            # unique index permits only one baseline, so is_baseline=False).
            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            _db.session.add(whatif)
            _db.session.commit()

            create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                scenario=baseline,
            )
            create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("70.00"),
                scenario=whatif,
            )
            _db.session.commit()

            # Savings: opening + 100 in baseline, opening + 70 in the
            # what-if -- never the cross-scenario 270.
            assert _independent_ledger_sum(
                savings.id, baseline.id,
            ) == Decimal("200.00")
            assert _independent_ledger_sum(
                savings.id, whatif.id,
            ) == Decimal("170.00")
            # Checking mirrors on its $1000 opening: 900 baseline, 930 what-if.
            assert _independent_ledger_sum(
                checking.id, baseline.id,
            ) == Decimal("900.00")
            assert _independent_ledger_sum(
                checking.id, whatif.id,
            ) == Decimal("930.00")
            # The service helper agrees, and each scenario reconciles alone.
            assert posting_service.account_posting_total(
                savings.id, baseline.id,
            ) == Decimal("200.00")
            assert posting_service.account_posting_total(
                savings.id, whatif.id,
            ) == Decimal("170.00")
            _assert_full_reconciliation(baseline.id)
            _assert_full_reconciliation(whatif.id)


# ---------------------------------------------------------------------------
# 5. Owner isolation via journal_entry.user_id (the "companion-owner" case)
# ---------------------------------------------------------------------------


class TestOwnerIsolationViaJournalEntry:
    """A posting's owner is its journal entry's; owners never cross-contaminate."""

    @pytest.mark.server_clock
    def test_two_owners_reconcile_independently_and_posting_has_no_user_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two independent owners settle transfers; neither sees the other's.

        Arithmetic: owner 1 settles $100 (their Checking -> their Savings);
        owner 2 settles $200 (their Checking -> their Savings).  Owner 1's
        Savings ledger is 100 (opening) + 100 = 200.00 and owner 2's is
        100 (opening) + 200 = 300.00 with no leakage.
        Every journal entry's ``user_id`` matches its account owner, the leg's
        owner is reachable only via ``Posting.journal_entry.user_id`` (a
        ``Posting`` carries no ``user_id`` column), and each owner's books
        reconcile in their own baseline scenario.
        """
        with app.app_context():
            # Owner 1.
            savings1 = create_account_of_type(
                seed_user, _db.session, "Savings", "Owner1 Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings1,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            # Owner 2 (fully independent user, accounts, scenario).
            savings2 = create_account_of_type(
                seed_second_user, _db.session, "Savings", "Owner2 Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_second_user, _db.session,
                seed_second_user["account"], savings2,
                seed_second_user["bootstrap_period"], amount=Decimal("200.00"),
            )
            _db.session.commit()

            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            # No leakage: each owner's Savings ledger holds only their own.
            assert _independent_ledger_sum(
                savings1.id, scenario1,
            ) == Decimal("200.00")
            assert _independent_ledger_sum(
                savings2.id, scenario2,
            ) == Decimal("300.00")

            # A Posting has no user_id of its own (ownership is normalized onto
            # the journal entry); the owner is reached only via the entry.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            for posting in _db.session.query(Posting).all():
                entry_owner = posting.journal_entry.user_id
                assert entry_owner in (owner1_id, owner2_id)
                # The leg's ledger account belongs to the same owner as its
                # journal entry -- the normalization holds end to end.
                assert posting.ledger_account.user_id == entry_owner

            # Every entry's user_id matches the owner whose account it touches.
            for entry in _db.session.query(JournalEntry).all():
                assert entry.user_id in (owner1_id, owner2_id)

            _assert_full_reconciliation(scenario1)
            _assert_full_reconciliation(scenario2)
# ---------------------------------------------------------------------------
# THE BACKFILL-VS-GO-FORWARD CASE WAS DELETED AT PLAN STEP X-f1
# ---------------------------------------------------------------------------
#
# ``TestBackfillAndGoForwardAgree`` drove the historical migration's frozen raw
# SQL, which reads a ``paid_at`` column migration ``a3f7c8e21b64`` DROPS.  It is
# the same class the developer ruled on for the two dedicated backfill suites
# (2026-08-03): the path is UNREACHABLE with data -- the migration runs only at
# its own point in the chain, long before the drop, over an empty table, and
# ``a3f7c8e21b64``'s downgrade REFUSES so Alembic cannot rewind past the drop --
# so the case graded a producer that can never run again against one that runs
# every day.  What it asserted, and what the rest of this oracle still asserts
# without it: the two producers post leg-for-leg identical entries for one
# settled transfer, and the ledger reconciles either way.  The GO-FORWARD half of
# that is covered by every other case here; the historical half has no future.


# ---------------------------------------------------------------------------
# Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    @pytest.mark.server_clock
    def test_per_account_reconciliation_catches_a_tampered_shadow(
        self, app, db, seed_user,
    ):
        """Tampering a settled shadow makes ledger != settled-shadow effect.

        A reconciled $100 Checking -> Savings settle has ledger +100 and
        settled-shadow effect +100 on Savings.  Forcing the TRANSFER's settled
        figure -- the cash the Savings side's record moved, stored once on the
        transfer since plan step balance:X-bi-6-4d-2 (its 4d-4 half; the
        record carried it before, and the income shadow before that, ruling
        R-BAL167 class 4) -- to 999 by raw SQL leaves
        the ledger at +100 but pushes the settled-shadow effect to +999 -- so
        the per-account reconciliation, which the oracle relies on, now
        FAILS.  This proves the check is a real comparison, not one that
        passes unconditionally.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Tamper Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Reconciled (absolutely) before tampering.
            assert _independent_ledger_sum(
                savings.id, scenario_id,
            ) == _opening_anchor(savings.id) + _independent_txn_effect(
                savings.id, scenario_id,
            )

            # Tamper the Savings side's RECORDED figure, not its estimate
            # (plan step X-au-c3): a settled row's effect is what it recorded as
            # having moved, so moving the plan on one is now inert.  The record
            # is the side's covering movement (plan step balance:X-bi-4b-2),
            # filed under the transfer's to-side on Savings rather than under
            # the income shadow (ruling R-BAL167 class 4, plan step
            # balance:X-bi-6-4d-2), and the figure it moved is stored once on
            # the TRANSFER since that step's 4d-4 half.  A settled transfer
            # holding two dated records and a figure above zero is a state the
            # commit grade admits, so the tamper commits.
            _db.session.execute(_db.text(
                "UPDATE budget.transfers SET settled_figure = 999 "
                "WHERE id IN ("
                "  SELECT income_transfer_id FROM budget.transaction_entries "
                "  WHERE income_transfer_id IS NOT NULL AND covers_settlement "
                "    AND account_id = :a)"
            ), {"a": savings.id})
            _db.session.commit()

            ledger = _independent_ledger_sum(savings.id, scenario_id)
            effect = _independent_txn_effect(savings.id, scenario_id)
            assert ledger == Decimal("200.00")  # ledger unchanged
            assert effect == Decimal("999.00")  # transaction truth drifted
            # opening (100) + effect (999) != ledger (200): the drift shows.
            assert ledger != _opening_anchor(savings.id) + effect
            # Drive the REAL production-wide sweep helper (not just the inline
            # re-derivation above) so a regression that broke the helper itself --
            # e.g. one that stopped comparing the ledger to its source -- would
            # fail here.  ``match`` pins the per-account reconciliation message
            # specifically, so a future edit that weakened THAT comparison but left
            # the non-empty guard or trial balance firing under tamper no longer
            # keeps this test green -- the tooth cannot be lost undetected.
            with pytest.raises(AssertionError, match="settled-shadow effect"):
                _assert_full_reconciliation(scenario_id)

    def test_trial_balance_catches_an_injected_leg(self, app, db, seed_user):
        """Injecting one extra leg pushes the trial balance off zero.

        A balanced book has trial balance 0.00.  Inserting one unmatched +50
        leg (raw SQL, flushed but never committed so the deferred per-entry
        trigger never fires) makes the whole-ledger sum 0 + 50 = 50.00 -- so
        the trial-balance ``= 0`` assertion is a real check, not one the
        per-entry trigger makes vacuously true.  Rolled back so the leg never
        lands.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "TrialBalance Savings",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto one of the transfer's two
            # entries (picked by its family link -- the Step-5 openings mean
            # several entries exist).  Flush (not commit) makes it visible to
            # the query; the DEFERRED balanced trigger validates only at
            # COMMIT, which we never reach.
            entry_id = (
                _db.session.query(JournalEntry.id)
                .filter(transfer_family_journal_filter(transfer.id))
                .order_by(JournalEntry.id)
                .limit(1)
                .scalar()
            )
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked_ledger_account(_db.session, savings.id).id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
            })
            _db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()
