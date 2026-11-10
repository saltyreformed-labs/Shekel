"""Loan-anchor correction posting: the opening and true-up TARGET legs.

The genesis balance corrections -- the once-per-loan OPENING and every user
balance TRUE-UP -- a loan books into the double-entry ledger, so its
confirmed balance is fully reconstructable as ``-(sum of its linked lines)``
with no external anchor read.  **This module builds the TARGET and nothing
else** (plan step ``balance:X-bi-6-3``, ruling **R-BAL102**), for the
worked-out read; it was ``loan_posting_service._anchors`` until plan task
plan#2526 moved it beside the read.  The stored ledger's reconcile -- what
was posted, the delta, the emission -- was ``loan_posting_service._corrections``'
one loop over the anchor and the payment-split targets together, until plan
task plan#3133 deleted it.

Every anchor a loan carries books one balanced correction (:mod:`app.services.loan_ledger` computes
its ``owed_before``)::

    loan-linked ledger   (owed_before - anchor_balance)   [opening | trueup]
    opening-equity ledger (anchor_balance - owed_before)  [opening | trueup]
                          -------------------------------
                          0

The origination anchor's ``owed_before`` is zero, so its correction is the
opening ``-original_principal`` onto the loan and ``+original_principal`` onto its
per-loan opening-equity (Equity) account.  A user-trueup's correction is
``owed_before - verified`` -- the jump that reproduces the resolver's true-up
on top of every line before it.

**Keyed by (source kind, pay period, entry date).**  A correction is a
derivation and has no row to key on, so each anchor's correction is keyed by
its ``source_kind_id`` (opening vs. true-up), the pay period the anchor books
in, and its ``entry_date`` (the anchor date), on the loan's own chart keys.
The payment split keys the same way since ruling **R-BAL102** (it carried the
loan-side shadow's ``transaction_id`` before it, the one derivation in the
ledger with a row key).  A change to a pre-true-up payment moves a true-up's
``owed_before``, and the read works the true-up out again from the walk as it
stands (the stored ledger's sync re-derived the target and posted the
balancing delta until plan task plan#3133 deleted it).  Names each leg by its
chart KEY and touches no stored chart row (plan task plan#2524).

**The period entered the key at plan step X-ai-r (finding N-161), and on this
side it closed a LATENT hole in the stored ledger rather than a live one.**
The key was ``(source kind, entry date)`` and the entry's period was resolved
inside the emission loop from the anchor date, so on a fixed calendar the
target and the posted entries always agreed and no loan figure moved
(measured on a production clone: zero deltas across every loan).  They
stopped agreeing when the calendar GREW A PERIOD around an anchor that had
none.  :meth:`app.services.pay_calendar.PayCalendar.filing_period` clamps to
the last period that OPENED on or before the anchor date when none contains
it, so a loan asserted past the end of the user's schedule files against that
clamp; the next ``extend_pay_periods`` -- or the rolling-window top-up
(``pay_period_rolling.top_up_rolling_window``, which appends through the same
function on an ordinary grid or dashboard load) -- then creates the period
that really contains it.  A period-blind key compared the two as equal, so the
delta was zero and the stored correction sat in the clamped period
permanently, with any later adjustment filed there too; with the period in
the key the stale key reversed to zero and the real one posted fresh.  The
worked-out read keeps no copy to go stale: it files each correction by its day
on whichever paycheck stands when it is read (ruling plan#2519).

*The truncating paths were NOT the mechanism.*  Only a period APPEARING under
an existing correction reached this.  A period being DELETED took the stored
correction with it through the ``pay_period_id`` cascade, so no stale key was
left to reverse and the next reconcile posted the target fresh.
``reset_pay_periods`` wiped and paired itself with a full resync
(``resync_user_loan_postings`` / ``resync_user_account_anchor_postings``)
until plan task plan#3076, and ``truncate_pay_periods`` and
``regenerate_pay_periods`` ran none, so since ruling plan#2782 deleted the lock
that refused them the stored copy was short until that next re-sync.

**Where the period comes from is the SAME on both halves, and this paragraph
used to say it was not.**  A loan anchor's period is DERIVED from its date
through :meth:`app.services.pay_calendar.PayCalendar.filing_period`, because
``budget.loan_anchor_events`` carries no ``pay_period_id`` column at all (and
the origination anchor is SYNTHESIZED from ``LoanParams``, so it has no row to
carry one).  **The account twin makes the identical call** (ruling R-EA): a
cash assertion does store a period, and the twin deliberately does not read it,
because that column is a cache of this same derivation and a clock split made
it disagree with its own day on real data.  An earlier draft of this paragraph
read *"a cash assertion stores its own period and the account twin reads it"*;
that was true of the first build of plan step X-ai-r and was left standing when
the ruling changed, which is exactly the stale-sentence-beside-a-rewritten-one
class this arc keeps paying for.

**The rule became ONE clamp at plan step C2-d, and the fold no longer shares
it.**  It was ``loan_ledger.resolve_anchor_pay_period`` -- containment, else
the latest period ENDING before the date, else the earliest -- three branches
over two functions, and the fold's visibility rule was its second consumer.
Ruling **D5**'s one clock had already taken the fold off it (an anchor counts
from its OWN date, needing no calendar), leaving the two posting writers as the
only callers, and the 2026-08-10 ruling then named what they were really
asking: *the latest period STARTING on or before the day, else the earliest*.
The equivalence to the deleted chain, its PRECONDITION, and the proofs that
cover each half are stated once at
:meth:`app.services.pay_calendar.PayCalendar.filing_period` rather than
repeated here and in the account twin.
"""

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.models.account import Account
from app.services.loan_ledger import LoanAnchorCorrection
from app.services.loan_loaders import LoanAnchorFact
from app.services.pay_calendar import PayCalendar

from ._chart import linked_key, loan_key
from ._targets import ChartLegMap, CorrectionKey, merge_target_legs


def _anchor_correction_kinds(
    anchor: LoanAnchorFact,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for an anchor's correction.

    The origination anchor books the loan's OPENING (source ``loan_opening``, leg
    kind ``opening``); every other anchor is a user balance assertion and books a
    TRUE-UP (source ``loan_trueup``, leg kind ``trueup``).  Keyed off the fact's
    ``is_opening`` flag -- the synthesized origination fact is the only opening
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`), so "not
    opening" is exactly "user-trueup".

    Args:
        anchor: The :class:`~app.services.loan_loaders.LoanAnchorFact` whose
            correction kinds to resolve.

    Returns:
        ``(PostingSourceEnum, PostingKindEnum)`` -- ``(LOAN_OPENING, OPENING)`` for
        the origination anchor, else ``(LOAN_TRUEUP, TRUEUP)``.
    """
    if anchor.is_opening:
        return PostingSourceEnum.LOAN_OPENING, PostingKindEnum.OPENING
    return PostingSourceEnum.LOAN_TRUEUP, PostingKindEnum.TRUEUP


def _loan_anchor_correction_target(
    correction: LoanAnchorCorrection, loan: Account,
) -> ChartLegMap:
    """Build the two-leg target for one anchor correction, or empty when it books nothing.

    The loan-linked leg is ``owed_before - anchor_balance`` (tagged ``opening`` or
    ``trueup``); the per-loan opening-equity leg is its negative, so the two sum
    to zero and the ledger's implied ``owed`` moves from ``owed_before`` to the
    verified value.  A correction whose ``owed_before`` already equals the anchor
    balance (a true-up that matches the walked balance) books NOTHING -- an empty
    target, so no zero leg is booked.

    Each leg is named by its :class:`~app.services.ledger_lines.ChartKey` (the
    loan's own line and its ``equity_opening`` line); until plan task plan#2524
    this resolved and minted the stored opening-equity row as it went, through
    ``ledger_account_service.get_or_create_loan_ledger_account``.

    Args:
        correction: The anchor correction from :func:`walk_loan_ledger`.
        loan: The loan the walk is of, with ``account_type`` loaded: the
            account every one of its anchors is asserted for.  It was read off
            the anchor (``anchor.account_id``), with the owner beside it, until
            plan task plan#2524, when the loan's own line's key came to need
            the account's type.

    Returns:
        ``{chart key: (amount, posting_kind_id)}`` (the two balanced legs, or
        empty when the correction books nothing).
    """
    anchor = correction.anchor
    verified = anchor.anchor_balance
    linked_amount = correction.owed_before - verified
    if linked_amount == 0:
        return {}
    _source_enum, posting_kind_enum = _anchor_correction_kinds(anchor)
    posting_kind_id = ref_cache.posting_kind_id(posting_kind_enum)
    return {
        linked_key(loan): (linked_amount, posting_kind_id),
        loan_key(LedgerAccountKindEnum.EQUITY_OPENING, loan.id): (
            -linked_amount, posting_kind_id,
        ),
    }


def anchor_correction_targets(
    corrections: list[LoanAnchorCorrection],
    loan: Account,
    calendar: PayCalendar,
) -> dict[CorrectionKey, ChartLegMap]:
    """Merge a loan's anchor corrections into per-(source, period, date) targets.

    Groups every anchor correction by its ``(source_kind_id, pay_period_id,
    anchor_date)`` key and sums the legs within each group
    (:func:`._targets.merge_target_legs`), so two same-day
    same-kind anchors (the unique index permits two true-ups on one day with
    different balances) net to a single balanced target that lands owed on the
    LATER value -- exactly the combined jump they express.  A correction that
    books nothing still creates its key with an empty leg map, which books no
    line (the stored ledger's reconcile reversed an entry it had posted under
    that key to zero, until plan task plan#3133 deleted it).

    **The period is RESOLVED here, from the calendar, rather than in the
    emission loop** (plan step X-ai-r).  It is part of the key, so the stored
    ledger's reconcile knew it before comparing the target and the posted
    side: a boundary that moved under an already-posted correction showed up as
    two keys (the old one reversing, the new one posting) instead of one target
    silently re-filed.  Two same-day anchors always resolve to the same period,
    so this narrows no group that was merged before.

    Args:
        corrections: The loan's anchor corrections from
            :func:`~app.services.loan_ledger.walk_loan_ledger`.
        loan: The loan the walk is of, with ``account_type`` loaded
            (:func:`_loan_anchor_correction_target`).
        calendar: The owner's whole pay calendar, from
            :func:`app.services.pay_calendar.calendar_for`.
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period` is the
            one place the "is there a period to point at" question is asked and
            refused, so this carries no precondition of its own -- an earlier
            draft claimed the loader had already checked, which was a second
            statement of one predicate and the two had drifted.

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {chart key: (amount,
        kind_id)}}``.
    """
    target: dict[CorrectionKey, ChartLegMap] = {}
    for correction in corrections:
        source_enum, _posting_kind = _anchor_correction_kinds(correction.anchor)
        anchor_date = correction.anchor.anchor_date
        key = (
            ref_cache.posting_source_id(source_enum),
            calendar.filing_period(anchor_date).period_id,
            anchor_date,
        )
        bucket = target.setdefault(key, {})
        merge_target_legs(
            bucket, _loan_anchor_correction_target(correction, loan),
        )
    return target
