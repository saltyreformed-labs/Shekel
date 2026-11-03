"""
Shekel Budget App -- The ledger worked out from the records

The package that outlives the stored ledger (balance:X-ew, plan#2023).  X-ew's
first leaf (plan#2513) builds here, BESIDE the stored journal, the ledger worked
out from today's records, and proves the two equal to the cent; its second leaf
moves every reader onto it, and its third deletes the store.

## Package layout

* :mod:`._chart` -- the chart of accounts worked out from the records (ruling
  plan#2518): the :class:`ChartKey` a line is booked on, the class rule applied
  when one is built, the label rule a statement reads, and ruling R-FO's
  counter dispatch.  The stored chart's writer (``ledger_account_service``)
  reads its class and label from here while the store lives.
* :mod:`._movements` -- the movement rule: whether a dated movement books
  (:func:`purchase_posts`), its two legs (:func:`purchase_target`), and the
  source and leg kinds its parent's shape decides.
* :mod:`._targets` -- the key a correction's legs are filed under
  (:data:`CorrectionKey`), their shape (:data:`ChartLegMap`) and the
  same-key merge.
* :mod:`._account_walk` and :mod:`._account_anchors` -- a non-loan account's
  opening and true-ups: the walk over the records that frames each correction
  (:func:`walk_account_ledger`) and the legs each books
  (:func:`account_correction_targets`).
* :mod:`._loan_anchors`, :mod:`._loan_payments` and :mod:`._loan_corrections`
  -- a loan's opening, true-ups and payment splits off its one walk
  (:func:`loan_correction_targets`).
* :mod:`._worked_out` -- the READ: every :class:`LedgerLine` of one owner's
  ledger in one scenario, worked out by the rules above
  (:func:`worked_out_lines`), issuing no write of its own.

**The rules are the worked-out ledger's, and the writer imports them back**
(plan task plan#2526): every target rule the stored ledger's writers posted
from moved here, so the read sums the very legs the writer posts and the
rules outlive the store.  What stays with the writers is only the store's
part: resolving each chart key to a stored row, reading what is posted, and
emitting the difference.

Flask-isolated per the project architecture rule (``CLAUDE.md`` Architecture
section): plain data in, plain data out; nothing here issues a write of
its own.
"""

from ._account_anchors import account_correction_targets
from ._account_walk import AccountAnchorCorrection, walk_account_ledger
from ._chart import (
    ACCOUNT_COUNTER_KINDS,
    LOAN_KINDS,
    ChartKey,
    account_counter_key,
    anchor_correction_counter_kind,
    category_key,
    chart_label,
    chart_record_type,
    ledger_class_of,
    linked_key,
    linked_ledger_class,
    loan_key,
    owner_bucket_key,
    transit_key,
)
from ._loan_anchors import anchor_correction_targets
from ._loan_corrections import loan_correction_targets
from ._loan_payments import payment_split_targets
from ._movements import (
    is_transfer_leg,
    posting_kind_of,
    purchase_posts,
    purchase_source,
    purchase_target,
)
from ._targets import ChartLegMap, CorrectionKey, merge_target_legs
from ._worked_out import LedgerLine, worked_out_lines

__all__ = [
    "ACCOUNT_COUNTER_KINDS",
    "LOAN_KINDS",
    "AccountAnchorCorrection",
    "ChartKey",
    "ChartLegMap",
    "CorrectionKey",
    "LedgerLine",
    "account_correction_targets",
    "account_counter_key",
    "anchor_correction_counter_kind",
    "anchor_correction_targets",
    "category_key",
    "chart_label",
    "chart_record_type",
    "is_transfer_leg",
    "ledger_class_of",
    "linked_key",
    "linked_ledger_class",
    "loan_correction_targets",
    "loan_key",
    "merge_target_legs",
    "owner_bucket_key",
    "payment_split_targets",
    "posting_kind_of",
    "purchase_posts",
    "purchase_source",
    "purchase_target",
    "transit_key",
    "walk_account_ledger",
    "worked_out_lines",
]
