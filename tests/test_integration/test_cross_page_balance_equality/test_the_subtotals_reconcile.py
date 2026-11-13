"""The grid column's subtotal rows reconcile to its balance row on every parameter case, the
remainder pinned per case as the fixture's own true-up.
"""

from decimal import Decimal

import pytest

from app.services import balance_at
from app.services.cash_flow_set import CashFlowSet

from ._helpers import _CASES, _bctx


# ── Parameter matrix (cases 1..5 of the plan's Commit 11 spec) ─────


# The ``seed_user`` factory's origination anchor for the checking account
# (``conftest`` creates it with ``anchor_balance=Decimal("1000.00")``).
# ``seed_cross_page_account`` leaves that assertion in place and APPENDS the
# case's own, so the difference between the two is a true-up that lands in the
# anchor period -- which ruling R-K's Reconciliation row carries and
# ``TestSubtotalReconciliation`` pins per case.
_FACTORY_ORIGINATION_BALANCE = Decimal("1000.00")


# ── C11-5: subtotal reconciles to balance delta on every page ──────


class TestSubtotalReconciliation:
    """The grid column's rows reconcile to its balance on every parameter case.

    E-25 / Q-10 / F-002 Pair C: ONE valued row set drives both the per-period
    subtotal rows and the balance row, so the period-to-period balance delta
    must equal the column's net plus ruling R-K's remainder plus the accrual, to
    the penny.  Before Commit 10 the grid's inline ``sum(...
    effective_amount ...)`` subtotal loop violated this whenever a
    Projected envelope expense carried cleared entries -- the subtotal
    row reported $500 while the balance row reflected the entries-aware
    $454.29 reduction.  This test fires the moment a future edit
    re-grows that divergence.

    **Read off ONE ``GridBalanceView`` since plan step X-c2b3.**  It differenced
    ``_cash_engine.balances_for`` against ``cash_ledger.period_subtotal``, and
    both deleted -- the balance replaced by the fold at X-c2b2, the subtotal by
    ``cash_period_view``, which is R-K's basis and carries the remainder term the
    old two-producer form had no name for.  The identity's SHAPE therefore
    changed with it: what a subtotal counts is no longer "the unpaid rows" but
    "every row attributed here", and what the balance counts is money that
    MOVED, so the two reconcile through a named remainder rather than exactly.
    """

    @pytest.mark.parametrize(
        "case", _CASES, ids=[c["id"] for c in _CASES],
    )
    def test_subtotal_reconciles_balance_delta(
        self,
        app,
        seed_cross_page_account,
        case,
    ):
        """C11-5: ``balance delta == net[p] + timing[p] + book_vs_bank[p]``.

        For each case, read ONE grid view over the whole period list and
        reconcile the anchor period's column against its predecessor's, then the
        post-anchor period's against the anchor's.

        The post-anchor period carries NO transactions of its own, so its net is
        exactly zero and its balance equals the anchor period's -- the
        no-transaction reconciliation step, and the case that would catch a
        remainder quietly absorbing a mis-grouped row.

        **The anchor period's remainder is the fixture's own TRUE-UP, and
        pinning its exact value is what keeps the identity from being
        untestable.**  ``seed_cross_page_account`` leaves the ``seed_user``
        factory's ``$1,000.00`` origination assertion in place and appends a
        SECOND one at the case's ``anchor_balance`` (latest-wins, E-19), both
        landing in the anchor period.  The opening books nothing in its own
        period, so the re-assertion's correction --
        ``anchor_balance - $1,000.00`` -- is money that moved through the column
        with no row to explain it: exactly what ruling R-K's remainder rows
        exist to show, here on five different anchor balances.  Asserting it as
        a computed figure rather than letting it fall out of the identity is
        Section 7.2's rule: a remainder read as a residual makes the identity
        arithmetically true, and would silently absorb a mis-grouped row.

        **It lands on ``book_vs_bank`` specifically** (plan step S1-c, ruling
        R-DH (f)).  The single remainder split in two, and this fixture makes
        exactly ONE of them non-zero: a balance ASSERTION with no row beside it,
        which is a book-vs-bank fact.  ``period_timing`` is pinned at ``$0.00``
        on both columns, and that assertion is what would catch a producer
        attributing a true-up to a timing difference -- a different diagnosis
        with different advice attached, and one the combined figure could not
        tell apart.

        The POST-anchor period carries neither an assertion nor a row, so both
        its remainders and its net are exactly zero -- the case that would catch
        a remainder leaking forward.  A PLAIN account accrues nothing, asserted
        on both columns.
        """
        with app.app_context():
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )

            columns = balance_at.grid_balance_view(
                CashFlowSet.single(ctx["account"]), _bctx(ctx),
            ).columns

            anchor_idx = next(
                i for i, p in enumerate(ctx["all_periods"])
                if p.id == ctx["anchor_period"].id
            )
            assert anchor_idx > 0, (
                "fixture invariant: the anchor period must have a predecessor "
                "to difference against"
            )
            assert anchor_idx + 1 < len(ctx["all_periods"]), (
                "fixture invariant: anchor period must not be the last "
                "period in the projected window"
            )
            prior_period = ctx["all_periods"][anchor_idx - 1]
            next_period = ctx["all_periods"][anchor_idx + 1]

            anchor_column = columns[ctx["anchor_period"].id]
            next_column = columns[next_period.id]

            # The anchor column's remainder holds BOTH assertions the fixture
            # puts in this period, and naming them separately is what keeps
            # this a hand-computed claim (plan step X-f3c-2a).
            #
            #   (a) the fixture's RE-ASSERTION: the factory's $1,000.00
            #       origination corrected to the case's anchor;
            #   (b) the ORIGINATION's own correction, which is the sum of the
            #       case's CLEARED DEBIT entries.  A cleared purchase is a cash
            #       fact in its own right (ruling R-FM) dated before this
            #       fixture's origination assertion, so the books carry it and
            #       the origination then corrects it straight back out.  That
            #       is the pre-opening double count plan step X-f3c-2b closes,
            #       and until X-f3c-2a it was invisible here: the fold moved
            #       the origination's whole delta into its seed (ruling R-I)
            #       and this row excluded it by construction.
            #
            # The cases with no cleared debit -- ``negative_overdraft`` and
            # ``credit_only`` -- put $0.00 in (b), which is why they read the
            # same figure before and after that step.
            expected_trueup = (
                case["anchor_balance"] - _FACTORY_ORIGINATION_BALANCE
            )
            expected_opening = sum(
                (
                    amount
                    for amount, is_credit, is_cleared in case["entries"]
                    if is_cleared and not is_credit
                ),
                Decimal("0.00"),
            )
            assert anchor_column.book_vs_bank == (
                expected_trueup + expected_opening
            ), (
                f"case {case['id']!r}: anchor column book-vs-bank "
                f"{anchor_column.book_vs_bank!r} != the fixture's true-up "
                f"{expected_trueup!r} plus its origination's own correction "
                f"{expected_opening!r} "
                f"({case['anchor_balance']!r} asserted over the factory's "
                f"{_FACTORY_ORIGINATION_BALANCE!r} origination).  R-K's "
                f"book-vs-bank row exists to carry exactly this."
            )
            # Every row in this fixture is budgeted to, and lands in, the
            # anchor period, so NOTHING is a timing difference.  A figure
            # here would mean the true-up had been booked to the wrong row.
            assert anchor_column.period_timing == Decimal("0.00"), (
                f"case {case['id']!r}: anchor column carries a "
                f"{anchor_column.period_timing!r} timing remainder, but every "
                f"row is budgeted to and lands in this period"
            )
            # The post-anchor period has neither an assertion nor a row, so
            # nothing can leak into either of its remainders.
            assert next_column.period_timing == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor column has a "
                f"{next_column.period_timing!r} timing remainder, but no row "
                f"is attributed to it"
            )
            assert next_column.book_vs_bank == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor column has a "
                f"{next_column.book_vs_bank!r} book-vs-bank remainder, but "
                f"no assertion falls inside it"
            )
            for label, column in (
                ("anchor", anchor_column), ("next", next_column),
            ):
                assert column.accrual == Decimal("0.00"), (
                    f"case {case['id']!r}: {label} column carries an accrual "
                    f"({column.accrual!r}) on a PLAIN account"
                )
                assert column.contribution == Decimal("0.00"), (
                    f"case {case['id']!r}: {label} column carries a modelled "
                    f"contribution ({column.contribution!r}) on a PLAIN account"
                )

            anchor_delta = (
                anchor_column.balance - columns[prior_period.id].balance
            )
            assert anchor_delta == (
                anchor_column.net + anchor_column.period_timing
                + anchor_column.book_vs_bank
            ), (
                f"case {case['id']!r}: anchor-period balance delta "
                f"{anchor_delta!r} != net {anchor_column.net!r} + "
                f"period_timing {anchor_column.period_timing!r} + "
                f"book_vs_bank {anchor_column.book_vs_bank!r}.  The "
                f"balance row and the subtotal rows disagree on the "
                f"entries-aware formula -- the F-002 Pair C / F-004 same-page "
                f"divergence has re-grown."
            )

            forward_delta = next_column.balance - anchor_column.balance
            assert forward_delta == (
                next_column.net + next_column.period_timing
                + next_column.book_vs_bank
            ), (
                f"case {case['id']!r}: post-anchor balance delta "
                f"{forward_delta!r} != net {next_column.net!r} + "
                f"period_timing {next_column.period_timing!r} + "
                f"book_vs_bank {next_column.book_vs_bank!r} "
                f"-- carry-forward broken"
            )
            # And with no transactions in the post-anchor period the
            # net is exactly zero, locking the empty-period case.
            assert next_column.net == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor period has no "
                f"transactions but its net is {next_column.net!r}; "
                f"expected 0.00"
            )
