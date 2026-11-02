"""The ledger worked out from the records: every line, for one owner and scenario.

The read X-ew's first leaf builds (plan step plan#2513, plan task plan#2526):
for one owner and one scenario, every line the stored journal holds a net for,
worked out from today's records by the very rules the stored ledger's writers
post from -- built BESIDE the store, proven equal to it to the cent by the
parity test (plan#2527) and the production run (plan#2528), and the one read
X-ew's second leaf moves every report onto.  Three kinds of line:

* **the movement lines**: each dated, non-card movement under a contributing
  parent books its cash leg on the account its money moved through and its
  counter leg on its parent's category line or the owner's Transfers in
  transit (:func:`._movements.purchase_target`), filed in its PARENT's
  paycheck on its own bank day -- the owner's budgeting choice, which ruling
  pay_calendar:R-PC53 measured off its date 105 times;
* **the account corrections**: each non-loan account's opening and true-ups
  from the one records walk (:func:`._account_walk.account_corrections`,
  :func:`._account_anchors.account_correction_targets`), each filed by its
  day on the paychecks the owner has when it is read (ruling plan#2519);
* **the loan lines**: each loan's opening, true-ups and settled payment
  splits off its one walk (:func:`._loan_corrections.loan_correction_targets`).

**It writes nothing, flushes nothing and creates no row of any table**: every
line names its chart account by a :class:`~._chart.ChartKey` (ruling
plan#2518), where the writers resolve each key to a stored chart row and mint
the rows they lack.

**The records are read owner-wide, each kind once** (finding plan#2147's
requirement, ruling plan#2136; ruling plan#2623):
:func:`app.services.cash_ledger.owner_cash_records` loads the owner's
movements under the fold's own clause set, and every non-loan account's
opening and assertions, in four statements whatever the number of accounts
or movements, and hands back each account's walk built from its own slice --
so a movement's lines and its account's walk read it once.  Each walk is
then framed and booked in memory (:func:`._account_walk.account_corrections`).
A loan still loads its own records through its walk
(:func:`app.services.loan_ledger.walk_loan_ledger`), so the read's
statements grow with the owner's LOANS alone, until plan task plan#2649
loads them owner-wide too.

**A scenario the store has never reconciled can differ from it by
construction**, and that difference is the store's: its account and loan
writers reconcile the owner's baseline and each scenario already holding a
posting on the account (``account_posting_service``'s
``sync_account_anchor_postings_all_scenarios``, ``loan_posting_service``'s
twin), while this read answers whichever scenario it is asked for.  Today
every owner holds only a baseline.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.extensions import db
from app.models.account import Account
from app.models.transaction_entry import TransactionEntry
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import CashLedgerWalk, owner_cash_records
from app.services.loan_ledger import walk_loan_ledger
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.transfer_legs import PlanItem

from ._account_anchors import account_correction_targets
from ._account_walk import account_corrections
from ._chart import ChartKey
from ._loan_corrections import loan_correction_targets
from ._movements import (
    posting_kind_of,
    purchase_posts,
    purchase_source,
    purchase_target,
)
from ._targets import ChartLegMap, CorrectionKey


@dataclass(frozen=True)
class LedgerLine:
    """One line of the worked-out ledger: an amount on a chart account, when.

    The worked-out twin of one stored posting with its journal header's
    filing (``budget.account_postings`` under ``budget.journal_entries``):
    what a statement nets by key, paycheck and day.  A line of ``$0.00`` is
    never one -- the writers post no zero leg, and a merged correction key can
    hold one (two same-day true-ups that cancel).

    Attributes:
        key: The chart account the line is booked on (ruling plan#2518).
        pay_period_id: The paycheck it is filed in: a movement's parent's
            own, a correction's by its day (ruling plan#2519), a loan payment
            split's its payment's.
        entry_date: The civil day: a movement's ``settled_on``, an
            assertion's ``observed_on``, an opening's ``opened_on``, a loan
            anchor's date, a loan payment's visible day.
        amount: The signed amount, debit-positive.
        source_kind_id: The ``ref.posting_sources`` id of what booked it:
            ``purchase`` or ``transfer_movement`` for a movement, an opening,
            true-up or payment-split kind for a correction.
        posting_kind_id: The ``ref.posting_kinds`` id the line carries.
        movement_id: The ``budget.transaction_entries`` row a movement line
            comes from, ``None`` for a correction, which is a derivation of
            a walk and comes from no row.
    """

    key: ChartKey
    pay_period_id: int
    entry_date: date
    amount: Decimal
    source_kind_id: int
    posting_kind_id: int
    movement_id: int | None


def worked_out_lines(owner_id: int, scenario_id: int) -> list[LedgerLine]:
    """Return every line of *owner_id*'s ledger in *scenario_id*, worked out.

    The movement lines first, ascending by movement id, then each account's
    correction lines, ascending by account id and in its walk's order.
    Every account the owner holds is walked, archived ones included: an
    archived account's corrections still stand, as the writers' account set
    (``account_posting_service._sync._non_loan_accounts_id_query``) keeps
    them.

    The owner's accounts are loaded once, up front, and held for the read:
    each movement's account (its cash line's key) and each walked account
    is one of them, so neither costs a statement of its own -- a movement's
    account is the owner's by ``fk_transaction_entries_owner_account``.  The
    owner's cash records are loaded once too
    (:func:`app.services.cash_ledger.owner_cash_records`): the movements the
    lines are booked from, and each non-loan account's walk built from the
    same movements.

    Args:
        owner_id: The owner whose ledger to read.
        scenario_id: The budget scenario to read it in.

    Returns:
        The :class:`LedgerLine` list, no line of ``$0.00`` in it; empty for an
        owner with no records.

    Raises:
        PayCalendarError: The owner holds no pay schedule (from
            :func:`app.services.pay_calendar.calendar_for`), or a correction
            has no materialised paycheck to file in (from
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`) --
            the refusals the writers raise for the same states.
        ValueError: An account whose projection kind has no true-up counter
            rule (:func:`._chart.anchor_correction_counter_kind`).
    """
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == owner_id)
        .order_by(Account.id)
        .all()
    )
    calendar = calendar_for(owner_id)
    cash = owner_cash_records(owner_id, scenario_id, [
        account for account in accounts if not _is_loan(account)
    ])
    lines = _movement_lines(cash.movements)
    for account in accounts:
        lines.extend(_correction_lines(
            _correction_targets(account, scenario_id, calendar, cash.walks),
        ))
    return [line for line in lines if line.amount != 0]


def _is_loan(account: Account) -> bool:
    """Return whether *account* walks as a loan rather than on its records.

    :func:`app.services.account_projection.classify_account`'s answer, the
    predicate both writers split their account sets by: an amortizing
    account is a loan.

    Args:
        account: The account, with ``account_type`` loaded.

    Returns:
        ``True`` for an amortizing account.
    """
    return classify_account(account) is AccountProjectionKind.AMORTIZING


def _movement_lines(
    movements: list[tuple[TransactionEntry, PlanItem]],
) -> list[LedgerLine]:
    """Return each posting movement's two lines, by the movement rule.

    Every dated movement the owner holds in the scenario
    (:attr:`app.services.cash_ledger.OwnerCashRecords.movements`), kept where
    :func:`._movements.purchase_posts` says it books -- the gate the writer
    posts by, which the loader's transfer arm does not state in SQL -- and
    booked as :func:`._movements.purchase_target`'s two legs under the
    source and leg kinds its parent's shape decides.

    Args:
        movements: The owner's dated movements, each with its parent, in
            movement-id order.

    Returns:
        Two lines per posting movement, in movement-id order.
    """
    lines: list[LedgerLine] = []
    for entry, parent in movements:
        if not purchase_posts(parent, entry):
            continue
        source_kind_id = ref_cache.posting_source_id(purchase_source(parent))
        posting_kind_id = posting_kind_of(parent)
        lines.extend(
            LedgerLine(
                key=key,
                pay_period_id=parent.pay_period_id,
                entry_date=entry.settled_on,
                amount=amount,
                source_kind_id=source_kind_id,
                posting_kind_id=posting_kind_id,
                movement_id=entry.id,
            )
            for key, amount in purchase_target(entry, parent).items()
        )
    return lines


def _correction_targets(
    account: Account,
    scenario_id: int,
    calendar: PayCalendar,
    walks: dict[int, CashLedgerWalk],
) -> dict[CorrectionKey, ChartLegMap]:
    """Return one account's correction legs, by the walk its kind takes.

    An amortizing account is a loan, whose opening, true-ups and payment
    splits come off its loan walk; every other account's opening and
    true-ups come off its records walk, the one the owner's load built
    (*walks*).  The dispatch is :func:`_is_loan`'s, the predicate both
    writers split their account sets by (the account walk refuses a loan by
    it, and the non-loan set's SQL filter reads the column it maps), and the
    one the read chose *walks*' accounts by.  A loan with no terms walks to
    nothing, as it posts nothing.

    Args:
        account: The account, with ``account_type`` loaded.
        scenario_id: The scenario a loan's records are walked in.
        calendar: The owner's whole pay calendar, which files each anchor
            correction by its day.
        walks: Each non-loan account's walk, by account id
            (:attr:`app.services.cash_ledger.OwnerCashRecords.walks`).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {chart key: (amount,
        posting_kind_id)}}``.
    """
    if _is_loan(account):
        return loan_correction_targets(
            walk_loan_ledger(account.id, scenario_id), account, calendar,
        )
    return account_correction_targets(
        account, account_corrections(walks[account.id]), calendar,
    )


def _correction_lines(
    targets: dict[CorrectionKey, ChartLegMap],
) -> list[LedgerLine]:
    """Return a correction target's legs as lines, each filed by its key.

    Args:
        targets: One account's correction legs (:func:`_correction_targets`).

    Returns:
        One line per leg, in the target's order; no movement behind any.
    """
    return [
        LedgerLine(
            key=key,
            pay_period_id=pay_period_id,
            entry_date=entry_date,
            amount=amount,
            source_kind_id=source_kind_id,
            posting_kind_id=posting_kind_id,
            movement_id=None,
        )
        for (source_kind_id, pay_period_id, entry_date), legs in targets.items()
        for key, (amount, posting_kind_id) in legs.items()
    ]
