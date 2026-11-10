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

# A correction's key: (journal ``source_kind_id``,
# ``pay_period_id``, civil ``entry_date``).  Shared by the loan and account
# anchor reconciles and, since plan step ``balance:X-bi-6-3`` (ruling
# **R-BAL102**), the loan payment SPLIT -- a correction is a DERIVATION of a
# walk and has no concrete source FK to key on, so each is keyed by its source
# kind (opening / true-up / payment split) plus the period / date pair every
# reconcile in this ledger keys on.  The split carried the loan-side shadow's
# ``transaction_id`` until that step, and every teardown of that row owed it
# a reversal first; a key with no row has nothing to lose.
#
# **The period entered the key because R2 was an attribution rule, not a
# convention** (plan step X-ai-r, finding N-161).  A correction "carries the
# PAY PERIOD of the postings it reverses -- read back from the ledger per
# period, never the source row's current period" (the stored ledger's
# ``posting_service``, until plan task plan#3133 deleted it).  The stored
# ledger's other two reconciles obeyed it structurally by carrying the period
# in their key -- ``_posting_write.posted_by_period`` for a movement (a
# transaction's and a transfer's own sources too, until plan#1845 deleted
# them) and the loan payment split's own reader (folded into
# ``posted_correction_legs`` at ``balance:X-bi-6-3``), both
# ``(pay_period_id, entry_date)``.  This one did not: it read
# the posted side keyed ``(source_kind_id, entry_date)`` with no period, so
# it could not know WHICH period it was correcting, and the account reconcile
# re-supplied one from the source row's current period -- verbatim what the
# rule forbade.  Measured on production data, that filed a ``$2,854.36``
# reversal of period-5 postings into period 6.  So the period was not
# decoration on this tuple: it was the half of the key that made a reversal
# land where the postings it undid lived.  The worked-out ledger reads the
# same key and files a correction by the same day-to-period rule when it is
# read (ruling plan#2519), so its period is never a stored fact.
CorrectionKey = tuple[int, int, date]

# A correction target as its rule builds it: {chart key: (signed amount,
# posting_kind_id)} (plan task plan#2524).
ChartLegMap = dict[ChartKey, tuple[Decimal, int]]


def merge_target_legs(bucket: ChartLegMap, legs: ChartLegMap) -> None:
    """Sum a correction's legs into a per-key target *bucket*, in place.

    The same-key merge each correction rule applies when two corrections
    share one key -- same source kind, same pay period AND same civil day.
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
    ruling R-EA replaced: the period is DERIVED from the assertion's day, so
    two assertions sharing a day share a period BY CONSTRUCTION and can never
    key apart.  The correction is kept visible because the claim survived the
    ruling that falsified it, in the shared primitive both halves read.

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
