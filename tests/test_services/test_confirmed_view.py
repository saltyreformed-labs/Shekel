"""The seam's walk-built CONFIRMED view: guards, shapes, and row economics.

Plan step **E1d-b** (``docs/audits/balance_architecture/README.md``).
:func:`app.services.balance_at.confirmed_view` is the loan resolver's confirmed
SEED -- the balance a loan's recorded events fold to, plus the confirmed schedule
rows the amortization table shows and the forward projection starts from.  It
replaced ``loan_payment_service.confirmed_loan_view``, which read the same view
out of the POSTED ledger, and this file replaced that reader's tests.

**Why the every-day equivalence oracle is gone, and what stands in its place.**
Step E1c built this view ADDITIVELY and parallel-ran it against the posting view
on every day of nine shapes; they agreed byte for byte, which is the proof the
cutover rested on.  Step E1d-b deleted the posting view, so that oracle lost its
counterparty -- and an oracle whose two sides are one implementation proves
nothing (plan Section 7.2).  Every shape is therefore re-anchored HERE on
values computed by hand from the loan's terms, with the arithmetic written into
each docstring: interest = round(balance x annual_rate / 12), principal = cash -
interest - escrow (capped at the balance; the excess is a lender Refund, not
principal), balance -= principal, and the row's P&I split against the governing
period's contractual payment under ``principal + interest == payment +
extra_payment``.  A reader can check every number without running the code.

**The fold is TOTAL, so two things the partial posting reader refused, this
answers** -- both pinned below, not hidden:

* a BROKEN loan (originated, no opening posting: a cold cache or a what-if never
  posted into) folds from SOURCE facts where the reader returned ``None``
  (finding B-12);
* a what-if scenario with no postings answers from the loan's anchors rather
  than falling back to the resolver's money-blind replay.

And one thing it is BLIND to: a raw transaction typed onto a loan account moves
the posted balance but not the walk (finding N-11).  That shape is forbidden at
every write source by step BG, which is why the fold is complete by
construction; the divergence is demonstrated at the end of this file so the
guard's value is visible, and the guard's own home is
``test_transaction_guards.py``.

Fixture: the shared ``SPLIT_LOAN`` -- $250,000 originated 2025-01-01 at 6%
(monthly rate 0.005), trued up to $100,000 on 2026-01-10, ``payment_day`` 1.  The
trueup balance differs from origination on purpose: a correct interest figure
proves the walk seeds from the trueup, not from origination.  Its contractual
P&I is round(250000 x 0.005 x 1.005^360 / (1.005^360 - 1)) = **1498.88**, the
threshold every row's ``extra_payment`` is measured against.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.escrow_line import EscrowComponentVersion
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.scenario import Scenario
from app.services import (
    balance_at,
    loan_loaders,
    loan_payment_service,
    loan_resolver,
    transfer_service,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    SPLIT_LOAN,
    amount_basis_for_scenario,
    clear_loan_ledger,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    insert_trueup_event,
    posted_loan_balance_at,
    seam_confirmed_view,
)

(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN
# The origination a case takes when a payment is walked BEFORE its true-up: the
# month before P1's 02-01 installment (plan step recurrence:R16-c-2, ruling
# R-R101).  Every contractual installment from origination is charged now, so
# SPLIT_LOAN's 2025-01-01 would have that payment clear twelve unpaid months
# first; restated, each figure stays what these cases were written to pin.
_ORIGINATION_BEFORE_P1 = date(2026, 1, 1)

# The evaluation date every "all payments visible" assertion reads at, and the
# frozen today it must stay at or before (the view's domain is as_of <= today).
_AS_OF = date(2026, 12, 31)
_FROZEN_TODAY = date(2027, 1, 1)

# The loan's contractual P&I (see the module docstring's arithmetic): the
# threshold each row's actual P&I is split against into payment + extra.
_CONTRACTUAL_PI = Decimal("1498.88")


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze today after the seed window so ``_AS_OF`` is always in the past."""
    freeze_today(monkeypatch, _FROZEN_TODAY)


def _loan_params(loan) -> LoanParams:
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


def _make_loan(
    seed_user, *, anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
    rate=_RATE, escrow_annual=None, name="Split Loan",
    origination_date=_ORIGINATION_DATE,
):
    """Create the SPLIT_LOAN shape, optionally overriding the anchor or rate.

    A case that walks a payment before its true-up passes
    :data:`_ORIGINATION_BEFORE_P1` as *origination_date* (plan step
    recurrence:R16-c-2, ruling R-R101).
    """
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=origination_date, escrow_annual=escrow_annual,
        name=name,
    )


def _settle(seed_user, loan, period, cash=Decimal("1000.00"), settled_on=None):
    """Settle a Checking -> loan payment through the production transfer path.

    ``settled_on`` pins the payment's settle day, which is what its
    VISIBILITY keys on (plan step C2's one clock); it defaults to the pay
    period's own start so every payment in a shape is visible from a known day.
    """
    return create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period,
        amount=cash,
        settled_on=period.start_date if settled_on is None else settled_on,
    )


def _view(loan, seed_user, as_of=_AS_OF):
    """Return the seam's confirmed view for *loan* at *as_of* (baseline scope)."""
    return seam_confirmed_view(loan.id, seed_user["scenario"].id, as_of)


def _economics(view):
    """Return each row as ``(date, interest, principal, balance)`` -- the pins."""
    return [
        (row.payment_date, row.interest, row.principal, row.remaining_balance)
        for row in view.history_rows
    ]


class TestConfirmedViewGuards:
    """When the view withholds an answer -- and the two cases it no longer does.

    Every ``None`` routes the caller to the resolver's anchor replay, exactly the
    pre-switch behaviour, so a withheld view is never a wrong one.
    """

    def test_no_baseline_scenario_returns_none(self, app, db, seed_user):
        """No scenario -> ``None`` (there is no scenario to scope the walk to)."""
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None, as_of=_AS_OF,
            )
            assert balance_at.confirmed_view(loan, ctx) is None

    def test_a_future_as_of_returns_none(self, app, db, seed_user):
        """A future ``as_of`` -> ``None`` (a projection, out of the view's domain).

        The confirmed view answers only ``as_of <= today``; a later date is the
        forward projection's question, so the view withholds and the resolver
        projects rather than the confirmed history being stretched forward.  The
        deleted ``confirmed_loan_view`` guarded the same way at the same place
        (the LOWER-level posting reader raised, but the guard ran first and it was
        never reached); this keeps that guard, and the resolver's fallback makes
        it safe.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            assert _view(loan, seed_user, _FROZEN_TODAY) is not None
            assert _view(loan, seed_user, date(2027, 6, 1)) is None

    def test_an_account_that_is_not_a_configured_loan_returns_none(
        self, app, db, seed_user,
    ):
        """A non-loan account -> ``None`` (its walk carries no opening fact).

        The view asks the WALK whether the account is a configured loan rather
        than re-loading ``LoanParams``: an account with none walks to an empty
        :class:`LoanLedgerWalk`, which carries no ``is_opening`` anchor.  One
        load, one source -- the origination date the rows are numbered from
        cannot be mismatched to the walk they are folded from.
        """
        with app.app_context():
            checking = seed_user["account"]
            assert loan_loaders.load_loan_params(checking.id) is None
            assert _view(checking, seed_user) is None

    def test_an_as_of_before_origination_returns_none(
        self, app, db, seed_user, seed_periods,
    ):
        """Before the loan exists -> ``None``; its honest 0.00 must not seed (B-1).

        A loan configured before it closes HAS an opening fact, so the fold
        answers ``0.00`` for a date before it -- correct ("nothing has happened")
        and exactly why it must never reach the forward projection: seeding zero
        collapsed a 360-row schedule to none and held $200,000 flat forever
        (outage B-1).  The view withholds instead, and the replay owns the whole
        timeline.

        NEGATIVE CONTROL: delete the origination guard in ``confirmed_view`` and
        this returns ``ConfirmedLedgerView(balance=0.00, history_rows=[])``.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            assert _ORIGINATION_DATE == date(2025, 1, 1)
            assert _view(loan, seed_user, date(2024, 12, 31)) is None
            # ...and on the origination date itself it answers, holding the
            # opening principal flat (nothing has been paid yet).
            opening = _view(loan, seed_user, _ORIGINATION_DATE)
            assert opening.balance == _ORIGINATION_PRINCIPAL
            assert opening.history_rows == []

    def test_a_loan_with_no_opening_posting_still_folds(
        self, app, db, seed_user, seed_periods,
    ):
        """B-12: a cold posting cache no longer costs the loan its confirmed view.

        The posting reader this replaced returned ``None`` for a loan with no
        OPENING posting -- a cold cache, or a what-if the opening was never
        posted into -- which dropped the resolver back to its money-blind anchor
        replay.  The walk reads SOURCE facts, so clearing the ledger moves
        NOTHING: the same balance, the same rows, before and after.  That is the
        repairable-cache decision the balance scalar and the per-period map
        already took (plan steps C3b1 / C3b3), now closed for the confirmed view.

        Hand-computed: one $1,000 payment due 2026-02-01 on the $100,000 anchor
        -- interest round(100000 x 0.005) = 500.00, principal 1000 - 500 =
        500.00, balance 99,500.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            before = _view(loan, seed_user)
            assert _economics(before) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
            ]
            assert before.balance == Decimal("99500.00")
            # The posting reader agrees while the cache is warm (the checked
            # projection, plan step E1a).
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, _AS_OF,
            ) == Decimal("99500.00")

            clear_loan_ledger(loan.id)

            # The reader can no longer answer; the fold is UNCHANGED.
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, _AS_OF,
            ) is None
            assert _view(loan, seed_user) == before

    def test_a_what_if_scenario_folds_its_own_anchors_not_the_baseline_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario scoping holds: a what-if sees the anchors, never the payments.

        The baseline carries a settled $1,000 payment; a second scenario carries
        none.  The walk is scenario-scoped through its settled-shadow query, so
        the what-if folds the loan's ANCHORS alone -- $100,000, the trueup value,
        with no rows -- while the baseline's row is untouched.  (The posting
        reader returned ``None`` here, because the what-if had no postings at
        all; answering from the anchors is the same fact its fallback replay
        would have reconstructed, arrived at directly.)
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(what_if)
            db.session.commit()

            baseline_view = _view(loan, seed_user)
            assert baseline_view.balance == Decimal("99500.00")
            assert len(baseline_view.history_rows) == 1

            what_if_view = seam_confirmed_view(loan.id, what_if.id, _AS_OF)
            assert what_if_view.balance == _ANCHOR_BALANCE
            assert what_if_view.history_rows == []

    def test_a_configured_loan_with_no_payments_has_rows_but_a_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """No confirmed payment yet reads ``[]`` rows -- never ``None``.

        The loan exists and its anchors fold, so the view ANSWERS: the trueup
        balance with an empty confirmed slice.  ``None`` here would wrongly send
        the resolver back to its replay for a loan whose facts are complete.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()

            view = _view(loan, seed_user)
            assert view.history_rows == []
            assert view.balance == _ANCHOR_BALANCE


class TestAWithheldViewResolvesExactlyAsTheUnseededResolver:
    """The load-bearing safety property behind every ``None``: nothing changes.

    Every guard in :class:`TestConfirmedViewGuards` routes the resolver to its
    anchor replay.  This pins what that is WORTH: the resolution is byte-identical
    to calling the pure resolver with no seed at all, so a withheld view can never
    be a wrong one -- it is the pre-read-switch behaviour, exactly.

    Kept from the deleted ``resolve_loan_seeded`` suite, whose version used a loan
    with no OPENING posting; finding B-12 retired that premise (such a loan now
    FOLDS), so the property is re-pinned on a guard that survives -- a loan read
    before it originated.
    """

    def test_a_pre_origination_read_resolves_identically_to_no_seed(
        self, app, db, seed_user, seed_periods,
    ):
        """Read before origination, the bundle equals the un-seeded resolver.

        Non-vacuous: the resolver really ran (a real, positive schedule), and a
        WRONG seed would move the forward rows rather than leave them equal.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            before_origination = date(2024, 12, 31)
            ctx = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"],
                as_of=before_origination,
            )
            assert balance_at.confirmed_view(loan, ctx) is None

            params = _loan_params(loan)
            loan_ctx = loan_payment_service.load_loan_context(
                loan.id, amount_basis_for_scenario(seed_user["scenario"].id),
                params,
            )
            loan_inputs = loan_resolver.LoanInputs(
                params, loan_loaders.load_loan_anchor_facts(params),
                loan_ctx.payments, loan_ctx.rate_changes,
            )
            unseeded = loan_resolver.resolve_loan(
                loan_inputs, before_origination,
            )
            seeded = resolved_loan(loan, ctx)

            assert seeded.state == unseeded
            assert seeded.state.schedule, "resolver produced no schedule rows"
            assert seeded.state.schedule[0].remaining_balance > Decimal("0.00")


class TestConfirmedViewRowEconomics:
    """Each row carries the payment's ACTUAL economics, hand-computed.

    These pins moved here from the deleted posting reader's suite (plan step
    E1d-b): the row shaping is now emitted on the WALK path, and its independent
    value proof has to live where the production path is.  Every expected figure
    is derived in its docstring from the loan's terms alone.
    """

    def test_on_schedule_rows_match_the_contractual_amortization(
        self, app, db, seed_user, seed_periods,
    ):
        """Two exactly-contractual payments amortize the $100,000 anchor.

        Two settled payments of exactly the contractual P&I (1498.88):

          row 1 (due 02-01): interest round(100000 x 0.005) = 500.00,
            principal 1498.88 - 500.00 = 998.88, balance 99,001.12
          row 2 (due 03-01): interest round(99001.12 x 0.005)
            = round(495.0056) = 495.01, principal 1003.87, balance 97,997.25

        Neither exceeds the contractual payment, so ``extra_payment`` is 0.00 and
        ``payment`` is the full 1498.88 on both rows.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period, _CONTRACTUAL_PI)
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("998.88"), Decimal("99001.12")),
                (date(2026, 3, 1), Decimal("495.01"),
                 Decimal("1003.87"), Decimal("97997.25")),
            ]
            assert [
                (row.payment, row.extra_payment) for row in view.history_rows
            ] == [
                (_CONTRACTUAL_PI, Decimal("0.00")),
                (_CONTRACTUAL_PI, Decimal("0.00")),
            ]
            assert all(row.is_confirmed for row in view.history_rows)

            # The compatibility pin, kept from the deleted reader's suite: on an
            # ON-SCHEDULE loan these rows are byte-identical to the resolver's
            # own contractual replay, FIELD BY FIELD.  That is what guarantees
            # the amortization table does not visibly step at the confirmed /
            # projected boundary -- and it is a genuinely independent producer
            # (the replay derives principal as ``period_pi - interest`` from the
            # schedule, never from the cash), which is why it can only be asked
            # of the on-schedule shape.
            params = _loan_params(loan)
            loan_ctx = loan_payment_service.load_loan_context(
                loan.id, amount_basis_for_scenario(seed_user["scenario"].id),
                params,
            )
            replay = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params, loan_loaders.load_loan_anchor_facts(params),
                    loan_ctx.payments, loan_ctx.rate_changes,
                ),
                _AS_OF,
            )
            assert view.history_rows == [
                row for row in replay.schedule if row.is_confirmed
            ]

    def test_extra_payment_row_shows_the_actual_split_and_extra(
        self, app, db, seed_user, seed_periods,
    ):
        """An off-schedule extra payment's row carries its real economics.

        A $2,000 payment on the $100,000 balance: interest round(100000 x
        0.005) = 500.00, principal 2000 - 500 = 1500.00, balance 98,500.00.
        Against the 1498.88 contractual P&I the actual P&I of 2000.00 carries
        extra 2000.00 - 1498.88 = 501.12, leaving payment 1498.88 -- the
        schedule-row invariant ``principal + interest == payment + extra``
        (1500.00 + 500.00 == 1498.88 + 501.12), the same algebra a projected row
        with extra uses, so the table's totals add up unchanged.  A contractual
        replay would show only the scheduled 998.88 principal; this row shows
        what the cash actually did.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("2000.00"))
            db.session.commit()

            (row,) = _view(loan, seed_user).history_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("1500.00")
            assert row.extra_payment == Decimal("501.12")
            assert row.payment == Decimal("1498.88")
            assert row.remaining_balance == Decimal("98500.00")
            assert row.is_confirmed is True

    def test_short_payment_row_shows_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An underpayment's row surfaces the real negative principal.

        A $400 payment against 500.00 accrued interest: principal
        400 - 500 = -100.00 (the balance GROWS to 100,100.00), payment 400.00
        (the actual P&I, under contractual so extra is 0.00).  Surfaced, never
        clamped -- the same ruling-D5 honesty the split walk pins.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("400.00"))
            db.session.commit()

            (row,) = _view(loan, seed_user).history_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("-100.00")
            assert row.extra_payment == Decimal("0.00")
            assert row.payment == Decimal("400.00")
            assert row.remaining_balance == Decimal("100100.00")

    def test_payoff_overpayment_row_caps_principal_and_excludes_the_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payoff overpayment's row ends at 0.00 with the refund excluded.

        A $150,000 payment on the $100,000 balance: interest 500.00, principal
        capped at the 100,000.00 that closes the loan, and the 49,500.00 surplus
        booked as a lender Refund -- NOT in the row (it is not P&I).  The actual
        P&I is 100,500.00, so extra = 100500.00 - 1498.88 = 99,001.12 and
        payment stays the contractual-shaped 1498.88 (principal + interest ==
        payment + extra holds: 100500.00 == 1498.88 + 99001.12).  The balance
        reads a clean 0.00, never -0.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("150000.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            (row,) = view.history_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("100000.00")
            assert row.extra_payment == Decimal("99001.12")
            assert row.payment == Decimal("1498.88")
            assert row.remaining_balance == Decimal("0.00")
            assert not row.remaining_balance.is_signed()
            assert view.balance == Decimal("0.00")

    def test_trueup_between_payments_moves_the_next_row_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history true-up resets the balance the NEXT row reads.

        P1 ($1,000, due 02-01) splits 500.00 / 500.00 leaving 99,500.00; a
        true-up then asserts $95,000 on 02-15; P2 ($1,000, due 03-01) accrues on
        the VERIFIED balance: interest round(95000 x 0.005) = 475.00, principal
        525.00, balance 94,475.00.  The true-up itself emits no row (it is not a
        payment) but its reset moves the running balance between the rows -- the
        pre-true-up row keeps its actual interest (the arc's from-origination
        history), and the post-true-up row lands on the asserted trajectory.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            insert_trueup_event(
                _loan_params(loan), Decimal("95000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("475.00"),
                 Decimal("525.00"), Decimal("94475.00")),
            ]
            assert view.balance == Decimal("94475.00")

    def test_trueup_dated_on_a_due_date_applies_after_that_days_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up dated exactly on a due date subsumes that day's payment.

        The walk's tie-break (a payment sorts BEFORE a same-date anchor) must be
        mirrored by the row accumulation or the row would accrue on the wrong
        balance.  P1 leaves 99,500.00; a true-up asserts $95,000 dated exactly on
        P2's 03-01 due date; P2 is walked FIRST -- interest round(99500 x 0.005)
        = 497.50, principal 502.50, row balance 98,997.50 -- and the reset applies
        after it, so the VIEW's balance is the asserted 95,000.00.

        NEGATIVE CONTROL: an anchor-first order would show 475.00 interest on the
        already-reset balance, which is the mutation this kills.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            insert_trueup_event(
                _loan_params(loan), Decimal("95000.00"), date(2026, 3, 1),
            )
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("497.50"),
                 Decimal("502.50"), Decimal("98997.50")),
            ]
            assert view.balance == Decimal("95000.00")

    def test_a_reverted_payment_leaves_no_trace_in_the_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment reverted to Projected is not a settled fact, so it folds nowhere.

        The walk's payment set is the loan's SETTLED payments, so a
        reverted payment simply is not in it -- there is no residue to classify,
        which is the structural version of the drop the posting reader had to
        perform by recognising payment lineage across two entry dates.  The
        confirmed $1,100 payment reads its exact economics: interest 500.00,
        principal 1100 - 500 = 600.00, balance 100000 - 600 = 99,400.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("1100.00"))
            reverted = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[2], amount=Decimal("1000.00"),
                settled_on=date(2026, 2, 20),
            )
            db.session.commit()
            transfer_service.update_transfer(
                reverted.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("600.00"), Decimal("99400.00")),
            ]
            assert view.balance == Decimal("99400.00")

    def test_the_last_row_balance_is_the_views_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """The rows and the balance are two derivations that must agree.

        Three $1,000 payments on the shrinking real balance:

          due 02-01: interest 500.00, principal 500.00 -> 99,500.00
          due 03-01: interest round(99500 x 0.005) = 497.50, principal
            502.50 -> 98,997.50
          due 04-01: interest round(98997.50 x 0.005) = round(494.9875)
            = 494.99, principal 505.01 -> 98,492.49

        The view's ``balance`` is the PREFIX SUM of the walk's dated deltas, keyed
        by VISIBLE date and rounded once at the end; each row's
        ``remaining_balance`` is a separate re-accumulation in CONTRACT order,
        in the opposite sign convention, rounded per payment.  They are not
        independent oracles -- both read the same walk -- but they are separate
        accumulations with a real cent-level degree of freedom between them, so
        their agreeing is a property rather than a restatement.  The
        hand-computed 98,492.49 is what grades BOTH.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle(seed_user, loan, period)
            db.session.commit()

            view = _view(loan, seed_user)
            assert [row.remaining_balance for row in view.history_rows] == [
                Decimal("99500.00"),
                Decimal("98997.50"),
                Decimal("98492.49"),
            ]
            assert view.balance == Decimal("98492.49")
            assert view.history_rows[-1].remaining_balance == view.balance


class TestConfirmedViewShapeMatrix:
    """The nine shapes plan step E1c proved, each re-anchored on hand arithmetic."""

    def test_escrow_leaves_the_payment_before_it_reaches_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $3,600/yr escrow takes $300 a month off the cash before principal.

        Each $1,000 payment splits interest + $300 escrow + principal, and the
        escrow posts OFF the liability ledger, so the balance moves by principal
        only:

          due 02-01: interest round(100000 x 0.005) = 500.00, principal
            1000 - 500 - 300 = 200.00 -> 99,800.00
          due 03-01: interest round(99800 x 0.005) = 499.00, principal
            201.00 -> 99,599.00
          due 04-01: interest round(99599 x 0.005) = round(497.995) = 498.00,
            principal 202.00 -> 99,397.00

        The row's ``payment`` is the P&I portion (200.00 + 500.00 = 700.00), NOT
        the $1,000 of cash: escrow is not debt service.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, escrow_annual=Decimal("3600.00"),
            )
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle(seed_user, loan, period)
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("200.00"), Decimal("99800.00")),
                (date(2026, 3, 1), Decimal("499.00"),
                 Decimal("201.00"), Decimal("99599.00")),
                (date(2026, 4, 1), Decimal("498.00"),
                 Decimal("202.00"), Decimal("99397.00")),
            ]
            assert [row.payment for row in view.history_rows] == [
                Decimal("700.00"), Decimal("700.00"), Decimal("700.00"),
            ]
            assert view.balance == Decimal("99397.00")

    def test_an_arm_rate_step_lifts_the_interest_and_the_rows_rate(
        self, app, db, seed_user, seed_periods,
    ):
        """A rate change to 9% governs every payment DUE on or after it.

        The rate steps to 9% (monthly 0.0075) effective 2026-02-01.  The split
        resolves each payment's rate period on its DUE date -- contract time,
        ruling D5 (see :class:`TestSplitInputsKeyOnTheDueDate`) -- so all three
        installments, due 02-01 / 03-01 / 04-01, accrue at 9%:

          due 02-01 at 9%:  interest round(100000 x 0.0075) = 750.00, principal
            1000 - 750 = 250.00 -> 99,750.00
          due 03-01 at 9%:  interest round(99750 x 0.0075) = round(748.125)
            = 748.13, principal 251.87 -> 99,498.13
          due 04-01 at 9%:  interest round(99498.13 x 0.0075) = round(746.2360)
            = 746.24, principal 253.76 -> 99,244.37

        The FIRST row is the one that moves with the keying: period 1 starts
        2026-01-16, before the change, while its installment falls on the very
        day the new rate takes effect.  Each row also CARRIES its governing
        rate, so a row's displayed rate is provably the rate its interest
        accrued at (plan step E1c's ruling Q3) -- which is exactly what makes
        the keying visible here rather than silent.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.add(RateHistory(
                account_id=loan.id, effective_date=date(2026, 2, 1),
                interest_rate=Decimal("0.09"),
            ))
            db.session.commit()
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle(seed_user, loan, period)
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("750.00"),
                 Decimal("250.00"), Decimal("99750.00")),
                (date(2026, 3, 1), Decimal("748.13"),
                 Decimal("251.87"), Decimal("99498.13")),
                (date(2026, 4, 1), Decimal("746.24"),
                 Decimal("253.76"), Decimal("99244.37")),
            ]
            assert [row.interest_rate for row in view.history_rows] == [
                Decimal("0.09"), Decimal("0.09"), Decimal("0.09"),
            ]

    def test_a_severe_underpayment_grows_the_balance_every_month(
        self, app, db, seed_user, seed_periods,
    ):
        """Two $200 payments against ~$500 interest leave the debt LARGER (D5).

          due 02-01: interest 500.00, principal 200 - 500 = -300.00 ->
            100,300.00
          due 03-01: interest round(100300 x 0.005) = 501.50, principal
            -301.50 -> 100,601.50

        Delinquency reads honestly: the balance rises, the rows say why, and
        nothing is clamped to zero.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period, Decimal("200.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("-300.00"), Decimal("100300.00")),
                (date(2026, 3, 1), Decimal("501.50"),
                 Decimal("-301.50"), Decimal("100601.50")),
            ]
            assert view.balance == Decimal("100601.50")

    def test_a_late_settled_payment_is_dated_at_its_installment_but_visible_late(
        self, app, db, seed_user, seed_periods,
    ):
        """The split keys on the DUE date; visibility keys on the SETTLED date.

        Both payments satisfy the SAME 2026-02-01 installment (periods 1 and 2
        start 01-16 and 01-30, and payment_day 1 makes both due 02-01), so since
        plan step X-au-g-2c-3b-2 that month is charged ONCE and the walk runs
        them in pay-period order:

          charge 02-01: interest round(100,000.00 x 0.005) = 500.00
          first (period 1): clears it -> principal 500.00 -> 99,500.00
          second (period 2): nothing stands -> principal 1,000.00 -> 98,500.00

        Period 1's payment is settled LATE, on 2026-02-13; period 2's settles on
        its own 01-30 start.  From 02-13 both are visible and the balance is
        98,500.00, with the late payment still dated at the 02-01 installment
        it paid.

        **Read on 2026-02-01, the pass has seen only the SECOND payment, and it
        replays the facts it has seen** (plan step recurrence:R16-c-1, ruling
        **R-R91**, developer 2026-09-19: "a pass's stream holds the facts
        visible by its as_of ... projections follow").  With no other payment
        in the stream, the second OPENS the accrual period: it clears the
        500.00 charge and pays 500.00 of principal, 99,500.00 -- what the
        ledger itself showed on 02-01, before the late payment's settlement
        on 02-13 re-split it.  *Until that ruling the mid read pinned 0.00 /
        1,000.00 / 99,000.00*: the split each payment carried was a fact of
        the WHOLE walk (the first payment ahead of the second, though it had
        not settled), and the view re-accumulated the visible subset of those
        splits -- a figure no ledger displayed on any day.  The old values are
        recorded here rather than deleted because rule 5 forbids moving a
        pinned value without the developer's ruling, and this is the ruling.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[1], settled_on=date(2026, 2, 13),
            )
            _settle(seed_user, loan, seed_periods[2])
            db.session.commit()

            mid = _view(loan, seed_user, date(2026, 2, 1))
            assert _economics(mid) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
            ]
            assert mid.balance == Decimal("99500.00")

            after = _view(loan, seed_user, date(2026, 2, 13))
            assert _economics(after) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 2, 1), Decimal("0.00"),
                 Decimal("1000.00"), Decimal("98500.00")),
            ]
            assert after.balance == Decimal("98500.00")

    def test_two_payments_in_one_due_month_show_as_two_rows_in_that_month(
        self, app, db, seed_user, seed_periods,
    ):
        """A biweekly collision keeps BOTH rows at the true due date, and the
        month is charged ONCE.

        Periods 3 (02-13..) and 4 (02-27..) both start in February, so payments
        budgeted to them satisfy the SAME 2026-03-01 installment.  The confirmed
        view keeps the true due date -- two rows in one month, more truthful --
        where the resolver's DISPLAY redistribution shifts the second to March.

        **Since plan step X-au-g-2c-3b-2 the second payment clears no fresh
        charge** (developer-ruled 2026-09-02: interest is the price of time, not
        of a transaction), so it pays pure principal:

          charge 03-01: interest round(100,000.00 x 0.005) = 500.00
          first: interest 500.00, principal 500.00 -> 99,500.00
          second: interest 0.00, principal 1,000.00 -> 98,500.00

        Per PAYMENT -- the rule this replaced -- the second accrued
        round(99,500.00 x 0.005) = 497.50 and paid only 502.50 down, leaving
        98,997.50.  The owner is 497.50 better off, and that difference is the
        whole of what this step moves.

        The true-up is dated 2026-02-10, after the 02-01 installment no payment
        here pays: every contractual installment is charged since plan step
        recurrence:R16-c-2, so the SPLIT_LOAN true-up's 2026-01-10 would leave
        February standing for the first March payment to clear (the fixture is
        restated so nothing is left unpaid, ruling R-R103).
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 10))
            _settle(seed_user, loan, seed_periods[3])
            _settle(seed_user, loan, seed_periods[4])
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 3, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("0.00"),
                 Decimal("1000.00"), Decimal("98500.00")),
            ]

    def test_a_pre_anchor_payment_accrues_on_origination_then_is_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due BEFORE the latest anchor is subsumed by that anchor's reset.

        The true-up is dated 2026-03-15; the payment is due 2026-02-01, so the
        walk splits it on the ORIGINATION balance and the true-up then resets:

          due 02-01 on $250,000: interest round(250000 x 0.005) = 1250.00,
            principal 2000 - 1250 = 750.00 -> 249,250.00
          true-up 03-15: resets to $100,000 (emits no row)

        So the row keeps its real economics -- from-origination history the
        resolver's replay hides, because the replay starts at the latest anchor
        -- while the view's balance is the asserted 100,000.00.  Its actual P&I
        (2000.00) exceeds the contractual 1498.88, so extra = 501.12.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 3, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle(seed_user, loan, seed_periods[_P1], Decimal("2000.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("1250.00"),
                 Decimal("750.00"), Decimal("249250.00")),
            ]
            (row,) = view.history_rows
            assert row.extra_payment == Decimal("501.12")
            assert row.payment == Decimal("1498.88")
            assert view.balance == Decimal("100000.00")

    def test_a_tracking_start_import_holds_the_opening_flat_then_resets(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-life import opens at ORIGINATION and resets at its tracking start.

        The ledger opens at origination (plan step C1), so the window between
        origination and the tracking start reads the opening principal held FLAT
        -- the honest pre-tracking plateau (finding B-11), never a false $0.00 --
        and the tracking-start assertion resets the balance at its own date like
        any true-up:

          2025-06-01 (pre-tracking): 250,000.00, no rows
          after the 2026-01-10 assertion, due 02-01: interest 500.00, principal
            500.00 -> 99,500.00
          due 03-01: interest 497.50, principal 502.50 -> 98,997.50
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Import View Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                _loan_params(loan), _ANCHOR_BALANCE, _ANCHOR_DATE,
            )
            db.session.commit()
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period)
            db.session.commit()

            plateau = _view(loan, seed_user, date(2025, 6, 1))
            assert plateau.balance == _ORIGINATION_PRINCIPAL
            assert plateau.history_rows == []

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("497.50"),
                 Decimal("502.50"), Decimal("98997.50")),
            ]
            assert view.balance == Decimal("98997.50")

    def test_a_payoff_and_a_payment_after_it_both_read_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Cash arriving after payoff is a Refund, not a negative balance.

        Trued to $1,000, two $1,500 payments:

          due 02-01: interest round(1000 x 0.005) = 5.00, principal capped at
            the 1000.00 that closes the loan (the 495.00 surplus is a Refund),
            balance 0.00; actual P&I 1005.00 is under the contractual 1498.88,
            so extra 0.00 and payment 1005.00
          due 03-01: interest round(0 x 0.005) = 0.00, principal 0.00 (the whole
            $1,500 is Refund), balance still 0.00

        The balance never goes negative and the loan does not resurrect.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("1000.00"))
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period, Decimal("1500.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("5.00"),
                 Decimal("1000.00"), Decimal("0.00")),
                (date(2026, 3, 1), Decimal("0.00"),
                 Decimal("0.00"), Decimal("0.00")),
            ]
            assert [
                (row.payment, row.extra_payment) for row in view.history_rows
            ] == [
                (Decimal("1005.00"), Decimal("0.00")),
                (Decimal("0.00"), Decimal("0.00")),
            ]
            assert view.balance == Decimal("0.00")

    def test_rows_are_numbered_continuously_from_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """A row's ``month`` counts installments from ORIGINATION, not from today.

        Origination 2025-01-01 makes the first contractual installment
        2025-02-01 payment 1, so 2026-02-01 is payment **13** and 2026-03-01 is
        payment 14.  Numbering from origination is what lets a mid-life loan's
        table read "payment 13 of 360" rather than restarting at 1 wherever the
        record happens to begin.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period)
            db.session.commit()

            assert [
                row.month for row in _view(loan, seed_user).history_rows
            ] == [13, 14]


class TestSplitInputsKeyOnTheDueDate:
    """Contract time governs the split's RATE and ESCROW (ruling D5, finding N-34).

    Ruling D5 (and R-A, which restates it) says the split INPUTS -- ordering,
    rate, AND escrow -- key on the payment's DUE date, "so out-of-order or late
    settlement can never re-split an installment".  ORDERING moved to the due
    date at step C2 (``loan_ledger.loan_event_stream``, which was
    ``merge_anchor_and_payment_events`` until plan step X-au-g-2c-3b-2) and
    VISIBILITY to the settled date, but the RATE and the ESCROW were left on the
    payment's ``pay_period.start_date`` until finding **N-34** measured the gap;
    these two tests are the fix's pins, replacing the control that pinned the
    defect.

    A pay period starts up to ~2 weeks BEFORE the installment it pays, so a
    version effective inside that window would govern the wrong side of the
    boundary: the rate case misattributes interest as principal, the escrow case
    misattributes escrow as principal.  Both propagate to the owed balance, the
    posted ledger's interest leg, the payment-history table, the Schedule-A tax
    figure, and the paid-YTD chips -- and E1a's checked-projection assert cannot
    catch either, because both sides derive from the same walk.

    Each test puts the version change STRICTLY inside the window and asserts the
    installment's own date governs; reverting either call site to
    ``pay_period.start_date`` flips it back to the value named in its docstring.
    """

    def test_a_rate_change_inside_the_period_to_due_window_governs(
        self, app, db, seed_user, seed_periods,
    ):
        """A 12% rate effective mid-window splits the payment at 12%, not 6%.

        Period 1 runs 2026-01-16..01-29 and its installment is due 2026-02-01, so
        2026-01-25 is STRICTLY inside the window: after the pay-period start,
        before the due date.  Keyed on the DUE date the payment accrues at 12% --
        interest round(100000 x 0.01) = 1000.00, principal 1000 - 1000 = 0.00,
        balance held at 100,000.00.  Keyed on the pay-period start (the N-34
        defect) it accrued at 6%: interest 500.00, principal 500.00, balance
        99,500.00.

        **$500.00 of interest on ONE payment**, which under the defect was
        misattributed to principal -- and which also moves the Schedule-A
        interest figure the Taxes tab reports.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.add(RateHistory(
                account_id=loan.id, effective_date=date(2026, 1, 25),
                interest_rate=Decimal("0.12"),
            ))
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            # The window really is a window: the change lands strictly inside it.
            period = seed_periods[_P1]
            assert period.start_date < date(2026, 1, 25) < date(2026, 2, 1)

            (row,) = _view(loan, seed_user).history_rows
            assert row.payment_date == date(2026, 2, 1)
            assert row.interest_rate == Decimal("0.12000")
            assert row.interest == Decimal("1000.00")
            assert row.principal == Decimal("0.00")
            assert row.remaining_balance == Decimal("100000.00")

    def test_an_escrow_change_inside_the_period_to_due_window_governs(
        self, app, db, seed_user, seed_periods,
    ):
        """An escrow version effective mid-window is the escrow the split backs out.

        The loan opens with $1,200/yr escrow ($100/mo) from origination; a second
        version on the SAME line takes effect 2026-01-25 at $6,000/yr ($500/mo) --
        again strictly inside period 1's 2026-01-16 start .. 2026-02-01 due
        window.  Keyed on the DUE date the payment backs out $500.00: interest
        round(100000 x 0.005) = 500.00, principal 1000 - 500 - 500 = 0.00,
        balance held at 100,000.00.  Keyed on the pay-period start it backed out
        the superseded $100.00 and booked principal 400.00 -> 99,600.00.

        **$400.00 of escrow on ONE payment**, misattributed to principal.  The
        escrow half matters as much as the rate half because the CASH is built
        from the same figure (``_shadow_live_amount``): if the two ends key on
        different dates, the difference silently lands in principal.
        """
        with app.app_context():
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            opening = (
                db.session.query(EscrowComponentVersion)
                .filter_by(line_id=loan.escrow_lines[0].id)
                .one()
            )
            db.session.add(EscrowComponentVersion(
                line_id=opening.line_id,
                effective_date=date(2026, 1, 25),
                annual_amount=Decimal("6000.00"),
            ))
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            period = seed_periods[_P1]
            assert period.start_date < date(2026, 1, 25) < date(2026, 2, 1)

            (row,) = _view(loan, seed_user).history_rows
            assert row.payment_date == date(2026, 2, 1)
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("0.00")
            assert row.remaining_balance == Decimal("100000.00")


class TestRawLoanTransactionIsInvisibleToTheWalk:
    """N-11: a raw transaction typed onto a loan moves the postings, not the fold."""

    def test_a_forced_raw_loan_transaction_diverges_the_two_derivations(
        self, app, db, seed_user, seed_periods,
    ):
        """The posted balance moves $300; the confirmed view does not.

        Bypassing ruling D4's create guard with a direct settled-cash insert (the
        shape a pre-guard legacy row would be), a $300 transaction posts onto the
        loan's linked ledger.  The sum-of-postings counts it; the walk -- whose
        payment set is transfer-linked shadows only -- cannot see it.  That is
        precisely why the shape is FORBIDDEN AT SOURCE (ruling R-E, shipped at
        step BG: both transaction-create routes, the recurrence-template form,
        and the salary-profile picker all refuse an amortizing account), which is
        what makes the fold complete by construction rather than by luck.  Do not
        read a divergence of this shape as a stale cache to repair.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            before = _view(loan, seed_user)
            assert before.balance == posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[_P1], Decimal("300.00"),
                account=loan, name="Typed On Loan",
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            after = _view(loan, seed_user)
            posted = posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )
            assert after == before
            assert abs(posted - after.balance) == Decimal("300.00")


def test_the_confirmed_view_is_the_resolvers_confirmed_slice(
    app, db, seed_user, seed_periods,
):
    """The cutover pin: the resolved schedule's confirmed rows ARE this view's.

    Plan step E1d-b threads :func:`app.services.balance_at.confirmed_view` into
    the seam's whole-loan read, so the amortization table, the band chart's
    history prefix, and the forward projection's seed all come from it.  A $2,000
    payment makes the check non-vacuous: the view's row carries the REAL 1500.00
    principal (hand-computed above), which a contractual replay could not produce
    -- so equality here proves the production path reads this producer, not that
    two producers happen to agree on an on-schedule loan.
    """
    with app.app_context():
        loan = _make_loan(seed_user)
        _settle(seed_user, loan, seed_periods[_P1], Decimal("2000.00"))
        db.session.commit()

        ctx = BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"],
            as_of=_AS_OF,
        )
        view = balance_at.confirmed_view(loan, ctx)
        resolved = resolved_loan(loan, ctx)
        confirmed = [row for row in resolved.state.schedule if row.is_confirmed]

        assert confirmed == view.history_rows
        assert confirmed[0].principal == Decimal("1500.00")
