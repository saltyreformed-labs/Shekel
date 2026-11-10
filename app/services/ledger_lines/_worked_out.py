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
  :func:`._account_anchors.account_correction_targets`), naming no paycheck:
  a reader windowing by paycheck files each by its day on the paychecks the
  owner has when it asks (ruling plan#2519, :meth:`LedgerLine.filed_period_id`);
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
paychecks, a $0.00 close's records are each one statement when any exists).

**It asks no pay calendar** (finding plan#2875): a line carries the paycheck
its record names, a movement's parent's or a loan payment's, and an opening
or true-up names none.  Filing that kind by its day is the concern of a reader
that windows by paycheck (:meth:`LedgerLine.filed_period_id`), so a reader
that asks a line's key, day and amount alone (the balance sheet, the re-type
refusal's :func:`accounts_hold_lines`) answers for an owner whose calendar
could file nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

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
from app.services.pay_calendar import PayCalendar
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
    what a statement nets by key and day, and by paycheck through
    :meth:`filed_period_id`.  A line of ``$0.00`` is never one -- the stored
    ledger's writers posted no zero leg and the read keeps that rule, and a
    merged correction key can hold one (two same-day true-ups that cancel).

    **A line carries the paycheck its RECORD names, and only that**
    (finding plan#2875).  The stored journal's ``NOT NULL pay_period_id``
    made every line name one, so the read filed each opening and true-up by
    its day as it read, and asked the owner's pay calendar to do it, though
    the balance sheet and the re-type refusal read no paycheck at all.  An
    opening or true-up names none now, and a reader that windows by paycheck
    files it by its day when it asks (:meth:`filed_period_id`).

    Attributes:
        key: The chart account the line is booked on (ruling plan#2518).
        budgeted_period_id: The paycheck the owner budgeted the line's record
            in, the owner's budgeting choice (ruling pay_calendar:R-PC53): a
            movement's parent's, a loan payment split's payment's.  ``None``
            for an opening or true-up, which no paycheck budgets.  A reader
            asking which paycheck a line files in asks
            :meth:`filed_period_id`, never this.
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
    budgeted_period_id: int | None
    entry_date: date
    amount: Decimal
    source_kind_id: int
    posting_kind_id: int
    movement_id: int | None

    def filed_period_id(self, calendar: PayCalendar) -> int:
        """Return the paycheck a reader windowing by paycheck files this line in.

        The paycheck the line's record names where it names one
        (:attr:`budgeted_period_id`); else, for an opening or true-up, the one
        its day files under on *calendar* (ruling plan#2519,
        :meth:`~app.services.pay_calendar.PayCalendar.filing_period`), so it
        follows the paychecks the owner holds when it is asked.  The one
        statement of that rule, which the read applied to every correction
        as it read until finding plan#2875 moved it here.  Every leg of one
        booking shares its record's paycheck or its day, so a window by
        paycheck takes a booking whole.

        Args:
            calendar: The line's owner's whole pay calendar.  The line names
                no owner, so a caller handed another owner's would file it in
                that owner's paychecks, and refuses one
                (:func:`app.services.ledger_report_service.compute_income_statement`).

        Returns:
            The ``budget.pay_periods`` id.

        Raises:
            PayCalendarError: An opening or true-up, and a calendar holding no
                materialised paycheck to file it in (from
                :meth:`~app.services.pay_calendar.PayCalendar.filing_period`).
        """
        if self.budgeted_period_id is not None:
            return self.budgeted_period_id
        return calendar.filing_period(self.entry_date).period_id


def worked_out_lines(owner_id: int, scenario_id: int) -> list[LedgerLine]:
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

    **It asks no pay calendar** (finding plan#2875): an opening or true-up
    names no paycheck (:attr:`LedgerLine.budgeted_period_id`), so an owner
    holding a correction and no pay schedule, which
    :func:`~app.services.pay_calendar.calendar_for` refuses, reads its
    ledger.  It took a calendar SOURCE from plan task plan#2771 until then,
    asked only when a correction was to be filed.

    Args:
        owner_id: The owner whose ledger to read.
        scenario_id: The budget scenario to read it in.

    Returns:
        The :class:`LedgerLine` list, no line of ``$0.00`` in it; empty for an
        owner with no records.

    Raises:
        ValueError: An account whose projection kind has no true-up counter
            rule (:func:`._chart.anchor_correction_counter_kind`).
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
    for account in accounts:
        lines.extend(_correction_lines(
            _correction_targets(account, corrections, loan_walks),
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
    fold (:func:`worked_out_lines`).  A line's key decides, never its paycheck,
    so it asks no pay calendar (finding plan#2875): it refused an owner holding
    a correction and no pay schedule until then.

    Args:
        accounts: The accounts to ask about, with ``account_type`` loaded.

    Returns:
        ``True`` when one of them holds a line in any scenario of its owner;
        ``False`` for an empty *accounts*.
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
    scenario_ids = db.session.query(Scenario.id).filter_by(user_id=owner_id).all()
    return any(
        chart_record_type(line.key) is Account
        and line.key.record_id in account_ids
        for (scenario_id,) in scenario_ids
        for line in worked_out_lines(owner_id, scenario_id)
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
                budgeted_period_id=parent.pay_period_id,
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
        corrections: Each non-loan account's corrections, by account id,
            framed off its walk
            (:attr:`app.services.cash_ledger.OwnerCashRecords.walks`,
            :func:`._account_walk.account_corrections`).
        loan_walks: Each loan's walk, by account id
            (:func:`app.services.loan_ledger.walk_loan_ledgers`).

    Returns:
        ``{(source_kind_id, budgeted_period_id, entry_date): {chart key:
        (amount, posting_kind_id)}}``.
    """
    if _is_loan(account):
        return loan_correction_targets(loan_walks[account.id], account)
    return account_correction_targets(account, corrections[account.id])


def _correction_lines(
    targets: dict[CorrectionKey, ChartLegMap],
) -> list[LedgerLine]:
    """Return a correction target's legs as lines, each carrying its key's paycheck and day.

    Args:
        targets: One account's correction legs (:func:`_correction_targets`).

    Returns:
        One line per leg, in the target's order; no movement behind any.
    """
    return [
        LedgerLine(
            key=key,
            budgeted_period_id=budgeted_period_id,
            entry_date=entry_date,
            amount=amount,
            source_kind_id=source_kind_id,
            posting_kind_id=posting_kind_id,
            movement_id=None,
        )
        for (source_kind_id, budgeted_period_id, entry_date), legs in targets.items()
        for key, (amount, posting_kind_id) in legs.items()
    ]
