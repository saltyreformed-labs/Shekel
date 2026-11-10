"""
Shekel Budget App -- the suite's reads of the worked-out ledger.

Every figure a test asserts about the ledger is read here, off
:func:`app.services.ledger_lines.worked_out_lines` (plan step plan#2913): a net,
a balance, a total, "this account holds no line".  The stored journal is not
asked; what still reads it grades the store's own machinery, through
``tests/_stored_ledger.py`` or the store's own models, and goes with the store
(``posting_service.account_posting_total`` was a third, until plan task
plan#3133 deleted it).

**A chart account is a** :class:`~app.services.ledger_lines.ChartKey`, built by
its constructor (``linked_key``, ``category_key``, ``account_counter_key``,
``loan_key``, ``transit_key``, ``owner_bucket_key``), never a stored row's id.
A bucket's key names no owner, so a read is always of one owner's ledger: the
scenario's, or the movement's.

**Nets are read, never entries.**  The read holds no reversal and no ``$0.00``
line, so a settle and its revert read as nothing where the store holds a pair
netting to nothing: an entry's shape or count is the store's and has no answer
here.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.models.user import User
from app.services.balance_at._fold import fold_loan_balances
from app.services.ledger_lines import chart_record_type, linked_key, worked_out_lines
from app.services.pay_calendar import calendar_for
from app.utils.money import round_money
from tests._test_helpers import transfer_side_record

_ZERO = Decimal("0.00")


def scenario_lines(db_session, scenario_id):
    """Return every worked-out line of *scenario_id*'s owner in that scenario.

    Args:
        db_session: The test ``db.session``.
        scenario_id: The scenario; its owner is the ledger's.

    Returns:
        The :class:`~app.services.ledger_lines.LedgerLine` list.
    """
    owner_id = db_session.get(Scenario, scenario_id).user_id
    return worked_out_lines(owner_id, scenario_id)


def owner_worked_out_lines(db_session, owner_id):
    """Return every worked-out line of *owner_id*'s, in every scenario.

    Args:
        db_session: The test ``db.session``.
        owner_id: The owner.

    Returns:
        ``[(scenario_id, LedgerLine), ...]``.
    """
    return [
        (scenario_id, line)
        for (scenario_id,) in db_session.query(Scenario.id).filter_by(user_id=owner_id)
        for line in worked_out_lines(owner_id, scenario_id)
    ]


def worked_out_totals(db_session, owner_id):
    """Return *owner_id*'s worked-out total per ``(scenario_id, chart key)``.

    What a schedule change must leave as it was (rulings plan#2517, plan#2782),
    whatever paycheck each line files in.  A key whose lines net to ``$0.00``
    is left out, as a key with no line: both hold no money.

    Args:
        db_session: The test ``db.session``.
        owner_id: The owner.

    Returns:
        ``{(scenario_id, ChartKey): total}``, each total non-zero.
    """
    totals = {}
    for scenario_id, line in owner_worked_out_lines(db_session, owner_id):
        key = (scenario_id, line.key)
        totals[key] = totals.get(key, _ZERO) + line.amount
    return {key: total for key, total in totals.items() if total != 0}


def filed_period_id(line, owner_id):
    """Return the paycheck a reader windowing by paycheck files *line* in.

    :meth:`~app.services.ledger_lines.LedgerLine.filed_period_id` on the
    owner's calendar as it stands: a movement's or a loan split's own
    paycheck, an opening's or true-up's by its day.

    Args:
        line: The :class:`~app.services.ledger_lines.LedgerLine`.
        owner_id: Its owner, whose calendar files it.

    Returns:
        The ``budget.pay_periods`` id.
    """
    return line.filed_period_id(calendar_for(owner_id))


def linked_key_of(db_session, account_id):
    """Return the key of the account's own line, for a test holding only its id.

    Args:
        db_session: The test ``db.session``.
        account_id: The account's id.

    Returns:
        :func:`app.services.ledger_lines.linked_key` of the account.
    """
    return linked_key(db_session.get(Account, account_id))


def ledger_net(db_session, key, scenario_id):
    """Return the net of every line on chart account *key* in a scenario.

    Args:
        db_session: The test ``db.session``.
        key: The chart account.
        scenario_id: The scenario; its owner names a bucket's key.

    Returns:
        The signed net as a ``Decimal``, debit-positive; ``$0.00`` for a key
        holding no line.
    """
    return sum(
        (
            line.amount for line in scenario_lines(db_session, scenario_id)
            if line.key == key
        ),
        _ZERO,
    )


def account_net(db_session, account_id, scenario_id):
    """Return the net of an account's own line in a scenario.

    :func:`ledger_net` of :func:`linked_key_of`: never its correction counters,
    which are keys of their own, so an anchor correction's two legs do not cancel
    here.

    Args:
        db_session: The test ``db.session``.
        account_id: The account's id.
        scenario_id: The scenario.

    Returns:
        The signed net as a ``Decimal``, debit-positive; ``$0.00`` for an account
        holding no line.
    """
    return ledger_net(db_session, linked_key_of(db_session, account_id), scenario_id)


def trial_balance(db_session):
    """Return the net of every worked-out line of every owner, in every scenario.

    ``$0.00`` while everything the read books carries its counter line, so a
    sweep can ask it after any mutation.

    Args:
        db_session: The test ``db.session``.

    Returns:
        The signed net as a ``Decimal``.
    """
    return sum(
        (
            line.amount
            for (owner_id,) in db_session.query(User.id)
            for _scenario_id, line in owner_worked_out_lines(db_session, owner_id)
        ),
        _ZERO,
    )


def source_lines(db_session, key, scenario_id, source_enum):
    """Return the lines one source kind books on chart account *key* in a scenario.

    Args:
        db_session: The test ``db.session``.
        key: The chart account.
        scenario_id: The scenario.
        source_enum: The :class:`~app.enums.PostingSourceEnum` member.

    Returns:
        The :class:`~app.services.ledger_lines.LedgerLine` list, empty when the
        source books nothing there.
    """
    source_kind_id = ref_cache.posting_source_id(source_enum)
    return [
        line for line in scenario_lines(db_session, scenario_id)
        if line.key == key and line.source_kind_id == source_kind_id
    ]


def source_net(db_session, key, scenario_id, source_enum):
    """Return the net of :func:`source_lines`.

    Args:
        db_session: The test ``db.session``.
        key: The chart account.
        scenario_id: The scenario.
        source_enum: The :class:`~app.enums.PostingSourceEnum` member.

    Returns:
        The signed net as a ``Decimal``; ``$0.00`` when the source books nothing.
    """
    return sum(
        (line.amount for line in source_lines(db_session, key, scenario_id, source_enum)),
        _ZERO,
    )


def correction_net_in_period(db_session, key, scenario_id, source_enum, period_id):
    """Return one source kind's net on chart account *key* in ONE paycheck.

    The per-paycheck reading ruling R2 is about: what a period holds is what
    the surviving facts say, with nothing filed against another period.
    :func:`ledger_net` is the scalar counterpart and :func:`linked_net_by_date`
    the per-day one.  Each line is filed as a reader windowing by paycheck
    files it (:meth:`~app.services.ledger_lines.LedgerLine.filed_period_id`),
    on its owner's calendar: an opening or true-up by its day.

    Args:
        db_session: The test ``db.session``.
        key: The chart account (an account's own line, for an anchor correction).
        scenario_id: The scenario.
        source_enum: The :class:`~app.enums.PostingSourceEnum` member naming the
            correction kind.
        period_id: The paycheck the lines are filed in.

    Returns:
        The signed net as a ``Decimal``; ``$0.00`` when the period holds none.
    """
    calendar = calendar_for(db_session.get(Scenario, scenario_id).user_id)
    return sum(
        (
            line.amount
            for line in source_lines(db_session, key, scenario_id, source_enum)
            if line.filed_period_id(calendar) == period_id
        ),
        _ZERO,
    )


def linked_net_by_date(db_session, key, scenario_id):
    """Return chart account *key*'s net per day in a scenario.

    Args:
        db_session: The test ``db.session``.
        key: The chart account.
        scenario_id: The scenario.

    Returns:
        ``{entry_date: Decimal}``, a day only where the key holds a line.
    """
    nets = {}
    for line in scenario_lines(db_session, scenario_id):
        if line.key == key:
            nets[line.entry_date] = nets.get(line.entry_date, _ZERO) + line.amount
    return nets


def _opened_loan_lines(loan_account_id, scenario_id):
    """Return a loan's own lines in a scenario, or ``None`` when it never opened.

    A loan with terms holds one opening line (its origination anchor's); a
    loan with none walks to nothing and has no line to answer from.

    Args:
        loan_account_id: The loan.
        scenario_id: The scenario.

    Returns:
        The lines on the loan's own key, or ``None`` when no opening is among
        them.
    """
    key = linked_key_of(db.session, loan_account_id)
    lines = [
        line for line in scenario_lines(db.session, scenario_id) if line.key == key
    ]
    opening = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
    if not any(line.posting_kind_id == opening for line in lines):
        return None
    return lines


def _owed_at(lines, as_of):
    """Return what a loan's own *lines* say it owed on *as_of*, to the cent.

    Debit-positive ledger: the loan's net is ``-(owed)``.  Written ``0 - net``
    rather than ``-net`` so a zero net (a loan read before its opening's day)
    yields ``0.00``, never ``-0.00``.

    Args:
        lines: The loan's own lines.
        as_of: The evaluation day; a line dated later is not counted.

    Returns:
        The balance owed as a cent-quantized ``Decimal``.
    """
    return round_money(_ZERO - sum(
        (line.amount for line in lines if line.entry_date <= as_of), _ZERO,
    ))


def posted_loan_balance_at(loan_account_id, scenario_id, as_of):
    """Return what the ledger says a loan owed on a date, or ``None``.

    ``-(the sum of the lines on the loan's own key dated on or before as_of)``:
    the opening, every settled payment's net principal and every true-up.  The
    suite's own window onto the loan's ledger, the counterparty the resolver is
    graded against; it shares its walk with the fold, so agreeing with the fold
    grades how the lines are dated and signed.  A future date carries the sum flat.

    A walk of many days reads :func:`posted_loan_balances_on`, not this per day.

    Args:
        loan_account_id: The loan.
        scenario_id: The scenario.
        as_of: The evaluation date.

    Returns:
        The balance owed as a cent-quantized ``Decimal``, or ``None`` for a
        loan with no opening line.
    """
    return posted_loan_balances_on(loan_account_id, scenario_id, [as_of])[as_of]


def posted_loan_balances_on(loan_account_id, scenario_id, days):
    """Return what the ledger says a loan owed on each of *days*, reading it once.

    The read works out the owner's whole ledger, so a walk asking per day works
    it out per day.  Over the 400-odd days of a fold test, measured 2026-11-06,
    that took the test from 2-4 s to 14-31 s on CI (plan#3017).

    Args:
        loan_account_id: The loan.
        scenario_id: The scenario.
        days: The evaluation dates.

    Returns:
        ``{day: owed}``, each as :func:`posted_loan_balance_at` answers that day,
        so ``None`` on every day for a loan with no opening line.
    """
    lines = _opened_loan_lines(loan_account_id, scenario_id)
    return {day: None if lines is None else _owed_at(lines, day) for day in days}


def assert_fold_matches_postings_every_day(
    loan_id, scenario_id, start, end, *, min_days,
):
    """Assert fold == posted sum on EVERY day of ``[start, end]`` inclusive.

    Folds ONCE over the whole domain (the fold takes a date list for exactly
    this), reads the ledger ONCE through :func:`posted_loan_balances_on` and sums
    it per day, then asserts zero mismatches.  Guards the loop so an empty or
    too-short domain -- which would pass vacuously -- fails instead:
    ``min_days`` is the floor the caller knows its domain must clear.

    Args:
        loan_id: The loan account to fold and read.
        scenario_id: The budget scenario.
        start: First day of the domain (inclusive).
        end: Last day of the domain (inclusive), and ASSERTED ``<= today``.
            Not because the equality would break past today -- it holds at any
            date, since both sides key on the same recorded events (the step-E1a
            assert pins that per visible date) -- but because days past today
            add no recorded event, so they are trivially equal and would inflate
            ``min_days`` with padding.  The deleted production reader RAISED for
            a future ``as_of``, which enforced this incidentally; the assertion
            keeps that guard on the oracle's own side, where the anti-sampling
            floor lives.
        min_days: The floor on the domain length -- proves the walk is not a
            handful of days agreeing.
    """
    assert end <= date.today(), (
        f"domain end {end} is past today -- days beyond today carry no recorded "
        f"event, so they pad min_days with trivially equal days"
    )
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    folded = fold_loan_balances(loan_id, scenario_id, days)
    read = posted_loan_balances_on(loan_id, scenario_id, days)
    mismatches = [
        (day.isoformat(), str(folded[day]), str(read[day]))
        for day in days
        if read[day] != folded[day]
    ]
    assert not mismatches, (
        f"fold vs posted sum disagree on {len(mismatches)}/{len(days)} "
        f"days (first 5): {mismatches[:5]}"
    )
    # Guard the loop: a vacuous or tiny domain proves nothing (anti-sampling).
    assert days[0] == start and days[-1] == end
    assert len(days) >= min_days


def posted_loan_balance_map(loan_account_id, scenario_id, periods):
    """Return each period's loan balance, keyed by period id, or ``None``.

    :func:`posted_loan_balance_at` at each period's END, derived off the
    owner's calendar: a period's end is the day before the NEXT payday, so it
    is a property of the whole payday set rather than of one row.  A line in a
    period outside *periods* still counts, each value being a cumulative.

    **It is the scalar per period, not an independent derivation.**  Pin a
    VALUE, never that the two agree.  Nor is it directly comparable to
    :func:`app.services.balance_at.positions_period_map` except over elapsed
    periods: that map clamps a begun period to ``min(period.end, ctx.as_of)``
    and answers a future one from the forward plan, where this carries the sum
    flat.

    Args:
        loan_account_id: The loan.
        scenario_id: The scenario.
        periods: The non-empty ``PayPeriod`` rows to key by, all one owner's,
            each in that owner's saved calendar.

    Returns:
        ``{period.id: Decimal}`` in payday order, or ``None`` for a loan with
        no opening line, so a caller can tell "the ledger cannot answer" from
        "the ledger says zero".
    """
    lines = _opened_loan_lines(loan_account_id, scenario_id)
    if lines is None:
        return None
    # Materialised once: the owner is read off the first row and the id set off
    # all of them, so a generator argument would be half consumed by then.
    rows = tuple(periods)
    assert rows, "posted_loan_balance_map needs at least one period to key by"
    owners = {period.user_id for period in rows}
    assert len(owners) == 1, (
        f"posted_loan_balance_map resolves ONE owner's calendar and was given "
        f"periods from {sorted(owners)}; the rest would be silently dropped"
    )
    wanted = {period.id for period in rows}
    answer = {
        period.period_id: _owed_at(lines, period.end_date)
        for period in calendar_for(rows[0].user_id).saved()
        if period.period_id in wanted
    }
    # One key per REQUESTED period: a period the calendar does not hold would
    # otherwise vanish, and read as a KeyError several lines later.
    assert set(answer) == wanted, (
        f"periods {sorted(wanted - set(answer))} are not in the owner's saved "
        f"calendar, so they were never flushed or belong to someone else"
    )
    return answer


def loan_correction_lines_at(
    db_session, loan_account_id, scenario_id, period_id, day,
):
    """Return a loan payment split's lines at ONE key.

    For a test holding a key's period and day but no dated loan-side record to
    read it off: after a revert or a hard delete, or at a ``$0.00`` close, whose
    split is dated by the installment the payment skips (ruling R-BAL139).

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan, whose keys scope the read.
        scenario_id: The scenario.
        period_id: The key's paycheck, the payment's own (a split line's
            ``budgeted_period_id``).
        day: The key's day.

    Returns:
        The ``loan_payment`` lines at the key on the loan's own keys, empty
        when nothing is split there.
    """
    source_kind_id = ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT)
    return [
        line for line in scenario_lines(db_session, scenario_id)
        if line.source_kind_id == source_kind_id
        and line.budgeted_period_id == period_id
        and line.entry_date == day
        and chart_record_type(line.key) is Account
        and line.key.record_id == loan_account_id
    ]


def loan_correction_lines(db_session, transfer_id, loan_account_id):
    """Return the split's lines at a settled payment's key.

    The key is read off the TRANSFER and the loan side's record.  A side with
    no dated record has no key and is REFUSED rather than answered ``[]``: an
    empty answer for a reverted payment would let "after the revert the split
    is gone" pass without reading the ledger.  A test holding a reverted or
    deleted payment reads :func:`loan_correction_lines_at` with the key it
    captured before the act.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The payment's ``budget.transfers`` id.
        loan_account_id: The loan (to-)account.

    Returns:
        The lines at the payment's key, empty when none.

    Raises:
        ValueError: If the loan side holds no dated record.
    """
    record = transfer_side_record(db_session, transfer_id, loan_account_id)
    if record is None or record.settled_on is None:
        raise ValueError(
            f"loan_correction_lines: transfer {transfer_id}'s side on account "
            f"{loan_account_id} holds no dated record and so no split key; read "
            f"loan_correction_lines_at with the key captured before the act "
            f"that released the day."
        )
    transfer = db_session.get(Transfer, transfer_id)
    return loan_correction_lines_at(
        db_session, loan_account_id, transfer.scenario_id,
        transfer.pay_period_id, record.settled_on,
    )


def family_movement_ids(txn):
    """Return the ids of the movements of *txn*'s family.

    Through ``Transaction.entries``, EVERY movement of the row (ruling
    R-BAL80).  A revert keeps the mirror un-dated (ruling R-BAL61), so a reverted
    row's family still names it, and it books nothing.

    Flushes first, because a movement only the session holds has no id yet, and
    a ``None`` among the ids would match every correction line, whose
    ``movement_id`` is ``None``.

    Args:
        txn: The :class:`~app.models.transaction.Transaction`, or its id.

    Returns:
        The movement ids; none for a hard-deleted row, whose movements left
        the books before it.
    """
    row = txn if isinstance(txn, Transaction) else db.session.get(Transaction, txn)
    if row is None:
        return []
    db.session.flush()
    return [movement.id for movement in row.entries]


def transfer_family_movement_ids(transfer_id):
    """Return the ids of the records of BOTH sides of a transfer.

    Independent SQL over the side links, never ``transfer_legs``, the producer
    the suite grades: a helper selecting money through the producer under test
    cannot catch that producer's fault (ruling R-BAL167 class 4).

    Args:
        transfer_id: The ``budget.transfers`` id.

    Returns:
        The movement ids.
    """
    return [
        movement_id for (movement_id,) in db.session.query(TransactionEntry.id).filter(
            db.or_(
                TransactionEntry.expense_transfer_id == transfer_id,
                TransactionEntry.income_transfer_id == transfer_id,
            ),
        )
    ]


def transfer_side_movement_ids(transfer_id, account_id):
    """Return the id of the record of ONE side of a transfer, as a list.

    The per-side twin of :func:`transfer_family_movement_ids`: a side's record
    is on its endpoint (the side keys), so the side on *account_id* is the
    linked record on that account.

    Args:
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        The movement ids, empty for a side holding no record.
    """
    return [
        movement_id for (movement_id,) in db.session.query(TransactionEntry.id).filter(
            db.or_(
                TransactionEntry.expense_transfer_id == transfer_id,
                TransactionEntry.income_transfer_id == transfer_id,
            ),
            TransactionEntry.account_id == account_id,
        )
    ]


def period_nets_of_movements(movement_ids, scenario_id):
    """Return ``{budgeted_period_id: {chart key: net}}`` over the lines of *movement_ids*.

    A movement line's paycheck is its parent's own
    (:attr:`~app.services.ledger_lines.LedgerLine.budgeted_period_id`), never
    ``None``.

    The read holds no ``$0.00`` line, so a reverted row's paycheck is simply
    absent: what the store held as a pair netting to nothing there.  Lines that
    cancel keep their key at ``$0.00`` (a settled transfer's two legs on
    transit), where :func:`movement_nets_by_key` drops it.

    Args:
        movement_ids: The movements (:func:`family_movement_ids`,
            :func:`transfer_family_movement_ids`).
        scenario_id: The scenario.

    Returns:
        The nets; empty when the movements book nothing.
    """
    wanted = set(movement_ids)
    nets = {}
    for line in scenario_lines(db.session, scenario_id):
        if line.movement_id in wanted:
            per_key = nets.setdefault(line.budgeted_period_id, {})
            per_key[line.key] = per_key.get(line.key, _ZERO) + line.amount
    return nets


def movement_nets_by_key(db_session, movement_ids, scenario_id):
    """Return the net *movement_ids* book on each chart account in a scenario.

    Args:
        db_session: The test ``db.session``.
        movement_ids: The movements (:func:`family_movement_ids`,
            :func:`transfer_family_movement_ids`).
        scenario_id: The scenario.

    Returns:
        ``{chart key: net}``, only keys carrying a non-zero net.
    """
    wanted = set(movement_ids)
    nets = {}
    for line in scenario_lines(db_session, scenario_id):
        if line.movement_id in wanted:
            nets[line.key] = nets.get(line.key, _ZERO) + line.amount
    return {key: net for key, net in nets.items() if net != 0}


def movement_nets_by_date(db_session, movement_ids, key, scenario_id):
    """Return the net *movement_ids* book on chart account *key*, per day.

    The read holds no reversal, so a re-dated row reads as its one day, where
    the store held a reversal on the old day and a posting on the new.

    Args:
        db_session: The test ``db.session``.
        movement_ids: The movements (:func:`family_movement_ids`,
            :func:`transfer_family_movement_ids`).
        key: The chart account.
        scenario_id: The scenario.

    Returns:
        ``{entry_date: net}``, only days carrying a non-zero net.
    """
    wanted = set(movement_ids)
    nets = {}
    for line in scenario_lines(db_session, scenario_id):
        if line.key == key and line.movement_id in wanted:
            nets[line.entry_date] = nets.get(line.entry_date, _ZERO) + line.amount
    return {day: net for day, net in nets.items() if net != 0}


def nets_by_key_and_day(db_session, scenario_id):
    """Return a scenario's net per chart account and day, to compare two runs.

    A key names an account or a category, never a stored chart row, so a row a
    run mints and a rollback removes leaves nothing here to differ on.

    Args:
        db_session: The test ``db.session``.
        scenario_id: The scenario.

    Returns:
        ``{(chart key, entry_date): net}``, only cells carrying a non-zero net.
    """
    nets = {}
    for line in scenario_lines(db_session, scenario_id):
        cell = (line.key, line.entry_date)
        nets[cell] = nets.get(cell, _ZERO) + line.amount
    return {cell: net for cell, net in nets.items() if net != 0}


def net_posted_by_day(movement_ids):
    """Return ``{day: net magnitude}`` over the lines of one source's movements.

    The LEDGER half of a settle-day assertion: where this row's money sits
    right now, so an exact-dict assertion grades both halves of a move at once.

    The reduction is per CHART ACCOUNT, not per day: netting across accounts
    within a day would collapse to ``$0.00`` for any balanced pair, an assertion
    that could never fail.  A day's value is the LARGEST per-account absolute
    net; every line of a balanced movement carries one magnitude, so it reads as
    "the size of this row's effect on that day".

    Args:
        movement_ids: The source's movements (:func:`family_movement_ids`,
            :func:`transfer_family_movement_ids`,
            :func:`transfer_side_movement_ids`).

    Returns:
        ``{entry_date: Decimal}``, only days carrying a non-zero net.
    """
    wanted = set(movement_ids)
    if not wanted:
        return {}
    owner_id = (
        db.session.query(TransactionEntry.owner_id)
        .filter(TransactionEntry.id.in_(wanted))
        .first()
        .owner_id
    )
    per_account = {}
    for _scenario_id, line in owner_worked_out_lines(db.session, owner_id):
        if line.movement_id in wanted:
            cell = (line.entry_date, line.key)
            per_account[cell] = per_account.get(cell, _ZERO) + line.amount
    nets = {}
    for (day, _key), net in per_account.items():
        nets[day] = max(nets.get(day, _ZERO), abs(net))
    return {day: amount for day, amount in nets.items() if amount != 0}
