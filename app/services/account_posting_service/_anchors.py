"""Account-anchor correction posting: the opening and true-up reconcile.

Posts a non-loan account's anchor corrections -- the once-per-account OPENING
(its earliest :class:`~app.models.account.AccountAnchorHistory` row) and a
TRUE-UP per later row -- into the append-only double-entry ledger, so the
account's linked ledger sums to an ABSOLUTE balance:  the latest anchor
assertion plus the settled facts recorded after that assertion moment.  This
is the shipped loan genesis pattern generalized to every non-loan account
(Build-Order Step 5); after it, the trial balance closes app-wide.

Every anchor the account carries posts one balanced correction
(:func:`app.services.ledger_lines.walk_account_ledger` computes its
``ledger_before``)::

    linked ledger         (anchor_balance - ledger_before)   [opening | trueup]
    counter ledger        (ledger_before - anchor_balance)   [opening | trueup]
                          -----------------------------------
                          0

The delta is ledger-native sign -- it holds for Asset AND Liability non-loan
accounts with no class branch, exactly like the engine.  A zero delta books
nothing (a fresh $0 account mints no entries and no counter row, staying
hard-deletable).

**WHICH counter ledger is a total dispatch over the account's projection kind**
(ruling **R-FO**, plan step X-f3d), resolved by
:func:`app.services.ledger_lines.anchor_correction_counter_kind` and
keyed once per correction by the worked-out ledger's correction rule.  The targets name chart
KEYS and touch no stored chart row; the shared reconcile loop resolves them to
rows at its emit step (plan task plan#2524).  An
OPENING books to the account's equity row whatever its kind -- capital brought
onto the books is not something earned -- and a TRUE-UP books to what the
difference WAS: interest income for an ``INTEREST`` account, a change in
value for an ``INVESTMENT`` or ``APPRECIATING`` one, and (until plan step X-f3c makes
that residual a transaction the user accepts, ruling **R-FN**) the equity row
for a ``PLAIN`` one.  Before it, every kind plugged to equity, which is why
``$10,653.91`` of return earned over 4.5 months was invisible on the income
statement (measured on a production clone 2026-08-13).

**Reconciled to target, keyed by (source kind, pay period, entry date).**  An
anchor correction has no concrete source FK to key on, so the reconcile keys
each anchor's entry by its ``source_kind_id`` (``account_opening`` vs.
``account_trueup``), the pay period CONTAINING the assertion's day, and its
``entry_date`` -- the fact's own
:attr:`~app.services.cash_ledger.CashAnchorFact.observed_on`, the civil day the
assertion is the CLOSING BALANCE for (ruling R-DH).  It was
``_utc_civil_date(asserted_at)`` until 2026-07-31: a second statement of a rule
the fact already resolves, and one that dated a late-evening Eastern true-up
into the next day, putting the correction on a different day from the settles it
corrects.  Two anchors merge to one target, landing on the later value, only
when they share ALL THREE key parts.  A pre-true-up source whose net later
changes moves the walk's ``ledger_before``; re-running the sync re-derives the
target and posts the balancing delta, so a stale correction self-heals.
Flushes but never commits -- the caller owns the transaction.

**The target is the worked-out ledger's** (plan task plan#2526): which
correction each assertion books, on which counter line, keyed by which
``(source kind, pay period, entry date)``, is
:func:`app.services.ledger_lines.account_correction_targets` -- the rule the
worked-out read sums too, together with the period-derivation history this
docstring carried until then -- and this module reconciles the stored ledger
to it.
"""

from app import ref_cache
from app.enums import PostingSourceEnum
from app.models.account import Account
from app.services._posting_reconcile import (
    emit_correction_deltas,
    filing_calendar_for,
    posted_correction_legs,
)
from app.services.ledger_lines import (
    AccountAnchorCorrection,
    account_correction_targets,
)


def reconcile_account_anchor_corrections(
    account: Account,
    scenario_id: int,
    corrections: list[AccountAnchorCorrection],
) -> None:
    """Reconcile an account's opening + true-up corrections to a PRE-WALKED list.

    The reconcile half of the account anchor sync, taking the corrections
    already produced by :func:`app.services.ledger_lines.walk_account_ledger` (the
    :mod:`._sync` entry points drive both halves).  Builds the
    per-``(source kind, pay period, date)`` target legs
    (:func:`app.services.ledger_lines.account_correction_targets`), reads back what is posted
    (:func:`app.services._posting_reconcile.posted_correction_legs`, scoped
    to the account's linked ledger), and emits ONE balanced delta per key
    that differs
    (:func:`app.services._posting_reconcile.emit_correction_deltas`, the loop
    shared with the loan twin) -- posting a new opening / true-up, adjusting
    a correction whose ``ledger_before`` moved (a pre-assertion source
    changed), or reversing one a matching balance retired.

    Idempotent and self-healing: a re-run at the same state writes nothing
    (every delta is zero).  Touches ONLY the account's own linked ledger and
    the counter rows its own corrections book into.  An empty *corrections*
    list (a missing or history-less account) or an owner that cannot be
    resolved is a no-op.  Flushes but does not commit (the caller owns the
    transaction).

    **This is also what MIGRATES ruling R-FO's re-pointing, with no backfill.**
    The posted side is read per correction key across EVERY ledger the key
    touches, so an account whose true-ups are posted against ``anchor_equity``
    while the dispatch now names ``unrealized_change`` produces exactly one
    balanced delta per key -- reversing the equity leg, posting the new one --
    and the linked leg's delta is zero and is dropped.  The deploy ran this
    for every non-loan account (the account backfill, deleted at plan task
    plan#3077 with the deploy's hooks), so the move landed in the same deploy
    as the migration that seeded the ref rows.

    **It takes the ACCOUNT, not its id**, since ruling R-FO gave the counter
    leg a dispatch over the account's projection kind: the caller
    (:func:`._sync.sync_account_anchor_postings`) has already loaded and
    classified the row to decide whether to sync at all, so taking the id here
    would re-query it -- and passing the object rather than an ``(id, object)``
    pair leaves nothing for a caller to mis-pair, which is the argument
    :func:`app.services._posting_reconcile.filing_calendar_for` makes for
    handing back the owner WITH the calendar.

    Args:
        account: The non-loan :class:`~app.models.account.Account` whose
            corrections to reconcile, with ``account_type`` loaded.
        scenario_id: The budget scenario to reconcile within.
        corrections: The account's corrections from
            :func:`app.services.ledger_lines.walk_account_ledger`.

    Raises:
        PostingError: If a correction books and the account has no linked
            ledger account (a broken chart-of-accounts pairing), at the emit
            step that resolves the target's keys.  A reconcile in which no
            correction books names no key and so never reaches the pairing;
            until plan task plan#2524 it looked the linked row up first and
            raised either way.
        PayCalendarError: The owner has no MATERIALISED pay period, so a
            correction's ``NOT NULL`` ``pay_period_id`` has nothing to point at
            -- refused by
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`, which
            is the ONE place that question is asked (developer ruling
            2026-08-10).  Finding **N-192** is why it fails loud.
        ValueError: If the counter dispatch has no rule for the account's
            projection kind, or, at the emit step, the
            counter resolver rejects the account.
    """
    if not corrections:
        return
    # The owner's whole calendar, loaded ONCE for the target keys through the
    # same door the loan twin takes (plan step C2-d), so the two halves cannot
    # come to file an anchor correction under different periods OR against
    # differently-loaded calendars.  It resolves the OWNER from the account and
    # returns both, so the two cannot be paired wrongly.  It refuses nothing:
    # ``filing_period`` below is the one place "is there a period to point at"
    # is asked, and a second copy of that test here had already drifted from it
    # (developer ruling 2026-08-10).
    resolved = filing_calendar_for(account.id)
    if resolved is None:
        return
    owner_id, calendar = resolved
    emit_correction_deltas(
        owner_id,
        scenario_id,
        target=account_correction_targets(account, corrections, calendar),
        posted=posted_correction_legs(
            account.id,
            scenario_id,
            [
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
            ],
        ),
    )
