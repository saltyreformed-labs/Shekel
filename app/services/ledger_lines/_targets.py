"""What a correction's target is keyed by, and how two of them merge.

The shared shape of every correction rule in the worked-out ledger -- an
account's opening and true-ups (:mod:`._account_anchors`), a loan's
(:mod:`._loan_anchors`) and a loan payment's split (:mod:`._loan_payments`):
each answers its legs as a :data:`ChartLegMap` keyed by a
:data:`CorrectionKey`, and two corrections sharing a key merge by
:func:`merge_target_legs`.  They lived in the correction writers' shared leaf
(``app.services._posting_reconcile``) until plan task plan#2526 moved the rules
beside the read that sums them; the writer reconciled the same targets
against what it had posted until plan task plan#3133 deleted it.

Pure: no query, no write.
"""

from datetime import date
from decimal import Decimal

from ._chart import ChartKey

_ZERO_MONEY = Decimal("0.00")

# A correction's key: (journal ``source_kind_id``, the paycheck its record
# budgets it in, civil ``entry_date``).  Shared by the loan and account
# anchor rules and, since plan step ``balance:X-bi-6-3`` (ruling
# **R-BAL102**), the loan payment SPLIT -- a correction is a DERIVATION of a
# walk and has no concrete source FK to key on, so each is keyed by its source
# kind (opening / true-up / payment split) plus its paycheck and day.  The split
# carried the loan-side shadow's ``transaction_id`` until that step, and every
# teardown of that row owed it a reversal first; a key with no row has nothing
# to lose.
#
# **The paycheck is the split's own and an anchor's ``None``** (finding
# plan#2875).  A split is in its payment's paycheck, the owner's budgeting
# choice, so two same-day payments budgeted in two paychecks key apart.  An
# opening or true-up is budgeted in no paycheck: the stored ledger filed it by
# its day and keyed it so (plan step X-ai-r, finding N-161, whose measured
# ``$2,854.36`` reversal of period-5 postings filed into period 6 is why the
# period entered the key while a stored entry carried one), and the read did
# the same until that finding moved the filing to the reader that windows by
# paycheck (:meth:`._worked_out.LedgerLine.filed_period_id`).  Its period was a
# function of its day, so two same-day anchors always shared one: keying them
# on ``None`` merges exactly the groups the derived period did.
CorrectionKey = tuple[int, int | None, date]

# A correction target as its rule builds it: {chart key: (signed amount,
# posting_kind_id)} (plan task plan#2524).
ChartLegMap = dict[ChartKey, tuple[Decimal, int]]


def merge_target_legs(bucket: ChartLegMap, legs: ChartLegMap) -> None:
    """Sum a correction's legs into a per-key target *bucket*, in place.

    The same-key merge each correction rule applies when two corrections
    share one key -- same source kind, same budgeted paycheck (``None`` for
    an anchor) AND same civil day.
    Each chart key's amounts add -- one key is one chart account, so its sum
    is what that account books -- and the merged target
    expresses the anchors' combined jump (landing on the later anchor's value,
    since each correction's delta already accounts for the prior one).  A leg
    that nets to zero stays in the bucket (the stored ledger's writer resolved
    its row, ``_posting_write.resolve_chart_targets``, until plan task
    plan#3133 deleted it), and the read drops its ``$0.00`` line
    (:func:`._worked_out.worked_out_lines`).  The posting kind
    is shared within a key by construction (one key holds one source kind,
    whose legs all carry that kind), so the incoming kind simply wins.

    **Adding the period to the key narrowed NOTHING here, and a draft of this
    docstring claimed it did** ("two same-day assertions filed against
    different periods now key apart").  That described R-DZ's target key, which
    ruling R-EA replaced: the period was DERIVED from the assertion's day, so
    two assertions sharing a day shared a period BY CONSTRUCTION and could
    never key apart -- and an anchor's key carries no period at all since
    finding plan#2875.  The correction is kept visible because the claim
    survived the ruling that falsified it, in the shared primitive both halves
    read.

    It survives only because an assertion has no source FK for the ledger to
    key on, so two assertions genuinely indistinguishable in the key merge
    into one entry.  Giving a correction that identity is what deletes this
    (plan step X-ai-s) -- and it is the ONLY thing that can, since no part of
    the current key can tell two same-day assertions apart.

    Args:
        bucket: The key's accumulated ``{ledger_account_id: (amount,
            posting_kind_id)}`` target, mutated in place.
        legs: One correction's balanced legs to add (may be empty for a
            correction that books nothing).
    """
    for ledger_id, (amount, kind_id) in legs.items():
        prev_amount, _prev_kind = bucket.get(ledger_id, (_ZERO_MONEY, kind_id))
        bucket[ledger_id] = (prev_amount + amount, kind_id)
