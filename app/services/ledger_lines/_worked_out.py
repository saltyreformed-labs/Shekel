"""The ledger worked out from the records: every line, for one owner and scenario.

The read X-ew's first leaf built (plan step plan#2513, plan task plan#2526):
for one owner and one scenario, every line the stored journal held a net for,
worked out from today's records by the very rules the stored ledger's writers
posted from -- built BESIDE the store, proven equal to it to the cent by the
parity test (plan#2527) and the production run (plan#2528), and the one read
X-ew's second leaf (plan#2515) moved every report onto before plan task
plan#3133 deleted the writers.  Three kinds of line:

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

**It issues no write of its own and creates no row of any table**: every
line names its chart account by a :class:`~._chart.ChartKey` (ruling
plan#2518), never by a stored chart row.  **Its queries read the caller's pending rows as
flushed**: its first query autoflushes whatever the session holds pending,
as every query in the session does, so a balance check or a movement a
write has staged is read rather than missed -- which is why the read leaves
autoflush on, and why "it flushes nothing" is true only of a clean session.
A loan's terms are read by the loans' ids for the same reason, never off
``Account.loan_params`` as the session loaded it: terms written by their
``account_id`` after that load were missed until finding plan#2691 moved the
read (:func:`app.services.loan_ledger.walk_loan_ledgers`).

**The records are read owner-wide, each kind once** (finding plan#2147's
requirement, ruling plan#2136; ruling plan#2623):
:func:`app.services.cash_ledger.owner_cash_records` loads the owner's
movements under the fold's own clause set, and every non-loan account's
opening and assertions, in four statements whatever the number of accounts
or movements, and hands back each account's walk built from its own slice --
so a movement's lines and its account's walk read it once.  Each walk is
then framed and booked in memory (:func:`._account_walk.account_corrections`).
Every loan is walked from one load too
(:func:`app.services.loan_ledger.walk_loan_ledgers`, plan task plan#2649):
its terms, statements, rate history, escrow lines and settled payments
each loaded once for all of the owner's loans.  So the read's statements do not grow with
the number of accounts, loans or movements: they depend only on which kinds of
record the owner holds (an escrow line's versions, a settled payment's
paychecks, a $0.00 close's records are each one statement when any exists,
and the owner's pay calendar is loaded when any correction is to be filed).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache, partial

from app import ref_cache
from app.extensions import db
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transaction_entry import TransactionEntry
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import owner_cash_records
from app.services.loan_ledger import LoanLedgerWalk, walk_loan_ledgers
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.transfer_legs import PlanItem

from ._account_anchors import account_correction_targets
from ._account_walk import AccountAnchorCorrection, account_corrections
from ._chart import ChartKey, chart_record_type
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
    never one -- the stored ledger's writers posted no zero leg and the read
    keeps that rule, and a merged correction key can hold one (two same-day
    true-ups that cancel).

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


def worked_out_lines(
    owner_id: int,
    scenario_id: int,
    calendar_source: Callable[[], PayCalendar],
) -> list[LedgerLine]:
    """Return every line of *owner_id*'s ledger in *scenario_id*, worked out.

    The movement lines first, ascending by movement id, then each account's
    correction lines, ascending by account id and in its walk's order.
    Every account the owner holds is walked, archived ones included: an
    archived account's corrections still stand, as the stored ledger's
    writers' account set kept them
    (``account_posting_service._sync._non_loan_accounts_id_query``, until
    plan task plan#3133 deleted it).

    The owner's accounts are loaded once, up front, and held for the read:
    each movement's account (its cash line's key) and each walked account
    is one of them, so neither costs a statement of its own -- a movement's
    account is the owner's by ``fk_transaction_entries_owner_account``.  The
    owner's cash records are loaded once too
    (:func:`app.services.cash_ledger.owner_cash_records`): the movements the
    lines are booked from, and each non-loan account's walk built from the
    same movements; and so are the loans'
    (:func:`app.services.loan_ledger.walk_loan_ledgers`), the loans' terms
    in one statement by their ids.

    **The owner's pay calendar is asked of *calendar_source* only when a
    correction is to be filed** (an account framed a correction, or a loan's
    walk is not :attr:`~app.services.loan_ledger.LoanLedgerWalk.is_empty`):
    an owner with no correction to file asks it nothing, so one holding neither a pay
    schedule (which :func:`~app.services.pay_calendar.calendar_for` refuses)
    nor a correction reads its ledger rather than that refusal.

    **It takes a SOURCE rather than deriving its own** (plan task plan#2771),
    the shape :func:`~app.services.income_service.paycheck_pricing` takes
    for the same reason: the income statement's route derives the owner's
    calendar for its own selector, so a read deriving another was a second
    derivation of one schedule on one render
    (``tests/test_arch/test_one_read_pass_per_render.py``).  A caller that
    holds a calendar hands it; one that holds none hands
    :func:`~app.services.pay_calendar.calendar_for` bound to the owner,
    still asked only when a correction is to be filed.

    Args:
        owner_id: The owner whose ledger to read.
        scenario_id: The budget scenario to read it in.
        calendar_source: A zero-argument callable answering *owner_id*'s
            :class:`~app.services.pay_calendar.PayCalendar`, which files each
            correction by its day; called at most once.

    Returns:
        The :class:`LedgerLine` list, no line of ``$0.00`` in it; empty for an
        owner with no records.

    Raises:
        PayCalendarError: A correction is to be filed and the owner holds no
            pay schedule (from :func:`app.services.pay_calendar.calendar_for`
            behind *calendar_source*), or a correction has no materialised
            paycheck to file in (from
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`).
        ValueError: *calendar_source* answered another owner's calendar,
            whose paychecks would file this owner's corrections; or an
            account whose projection kind has no true-up counter rule
            (:func:`._chart.anchor_correction_counter_kind`).
    """
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == owner_id)
        .order_by(Account.id)
        .all()
    )
    cash = owner_cash_records(owner_id, scenario_id, [
        account for account in accounts if not _is_loan(account)
    ])
    loan_walks = walk_loan_ledgers(
        [account.id for account in accounts if _is_loan(account)], scenario_id,
    )
    corrections = {
        account_id: account_corrections(walk)
        for account_id, walk in cash.walks.items()
    }
    lines = _movement_lines(cash.movements)
    if any(corrections.values()) or not all(
        walk.is_empty for walk in loan_walks.values()
    ):
        calendar = calendar_source()
        if calendar.user_id != owner_id:
            raise ValueError(
                f"the worked-out read of user {owner_id}'s ledger was handed "
                f"user {calendar.user_id}'s pay calendar: a correction is "
                f"filed in its owner's own paychecks"
            )
        for account in accounts:
            lines.extend(_correction_lines(
                _correction_targets(account, calendar, corrections, loan_walks),
            ))
    return [line for line in lines if line.amount != 0]


def accounts_hold_lines(accounts: Iterable[Account]) -> bool:
    """Return whether any of *accounts* holds a line, in any scenario of its owner.

    The one question the re-type refusal asks (ruling plan#2785, task plan#2778),
    at both of its doors: a line is an account's when its key names the account
    as its record (:func:`._chart.chart_record_type`), which is the account's own
    line, its correction counters and a loan's components, and never a category
    or an owner bucket.  A line is worked out from the records as they stand,
    so a settle and its revert leave an account holding none.

    Each owner's ledger is read once per scenario, by the one read the statements
    fold (:func:`worked_out_lines`), and its calendar loaded at most once.

    Args:
        accounts: The accounts to ask about, with ``account_type`` loaded.

    Returns:
        ``True`` when one of them holds a line in any scenario of its owner;
        ``False`` for an empty *accounts*.

    Raises:
        PayCalendarError: From :func:`worked_out_lines`, when a correction has
            no paycheck to file in.  No door leaves an owner there: an account
            is refused until its owner has paychecks, and no schedule change
            leaves none.
    """
    account_ids_by_owner: dict[int, set[int]] = {}
    for account in accounts:
        account_ids_by_owner.setdefault(account.user_id, set()).add(account.id)
    return any(
        _holds_a_line_on(owner_id, account_ids)
        for owner_id, account_ids in account_ids_by_owner.items()
    )


def _holds_a_line_on(owner_id: int, account_ids: set[int]) -> bool:
    """Return whether *owner_id*'s ledger holds a line on one of *account_ids*.

    Args:
        owner_id: The owner, whose every scenario is read.
        account_ids: The owner's accounts to ask about.

    Returns:
        ``True`` at the first line found on one of them.
    """
    calendar_source = cache(partial(calendar_for, owner_id))
    scenario_ids = db.session.query(Scenario.id).filter_by(user_id=owner_id).all()
    return any(
        chart_record_type(line.key) is Account
        and line.key.record_id in account_ids
        for (scenario_id,) in scenario_ids
        for line in worked_out_lines(owner_id, scenario_id, calendar_source)
    )


def _is_loan(account: Account) -> bool:
    """Return whether *account* walks as a loan rather than on its records.

    :func:`app.services.account_projection.classify_account`'s answer, the
    predicate the read splits the owner's accounts by: an amortizing account
    is a loan.

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
    :func:`._movements.purchase_posts` says it books -- a gate the loader's
    transfer arm does not state in SQL -- and
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
    calendar: PayCalendar,
    corrections: dict[int, list[AccountAnchorCorrection]],
    loan_walks: dict[int, LoanLedgerWalk],
) -> dict[CorrectionKey, ChartLegMap]:
    """Return one account's correction legs, by the walk its kind takes.

    An amortizing account is a loan, whose opening, true-ups and payment
    splits come off its loan walk (*loan_walks*); every other account's
    opening and true-ups are framed off its records walk (*corrections*),
    each walk built by the owner's one load of its kind.  The dispatch is
    :func:`_is_loan`'s, the one the read chose each map's accounts by (the
    stored ledger's two writers split their account sets by it until plan
    task plan#3133 deleted them).  A loan with no terms walks to nothing, so
    it books nothing.

    Args:
        account: The account, with ``account_type`` loaded.
        calendar: The owner's whole pay calendar, which files each anchor
            correction by its day.
        corrections: Each non-loan account's corrections, by account id,
            framed off its walk
            (:attr:`app.services.cash_ledger.OwnerCashRecords.walks`,
            :func:`._account_walk.account_corrections`).
        loan_walks: Each loan's walk, by account id
            (:func:`app.services.loan_ledger.walk_loan_ledgers`).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {chart key: (amount,
        posting_kind_id)}}``.
    """
    if _is_loan(account):
        return loan_correction_targets(loan_walks[account.id], account, calendar)
    return account_correction_targets(
        account, corrections[account.id], calendar,
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
