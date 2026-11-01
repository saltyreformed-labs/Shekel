"""
Shekel Budget App -- The ledger worked out from the records

The package that outlives the stored ledger (balance:X-ew, plan#2023).  X-ew's
first leaf (plan#2513) builds here, BESIDE the stored journal, the ledger worked
out from today's records, and proves the two equal to the cent; its second leaf
moves every reader onto it, and its third deletes the store.

## Package layout

* :mod:`._chart` -- the chart of accounts worked out from the records (ruling
  plan#2518): the :class:`ChartKey` a line is booked on, the class rule applied
  when one is built, and the label rule a statement reads.  The stored chart's
  writer (``ledger_account_service``) reads its class and label from here while
  the store lives.

Flask-isolated per the project architecture rule (``CLAUDE.md`` Architecture
section): plain data in, plain data out; nothing here writes.
"""

from ._chart import (
    ChartKey,
    account_counter_key,
    category_key,
    chart_label,
    ledger_class_of,
    linked_key,
    linked_ledger_class,
    loan_key,
    owner_bucket_key,
    transit_key,
)

__all__ = [
    "ChartKey",
    "account_counter_key",
    "category_key",
    "chart_label",
    "ledger_class_of",
    "linked_key",
    "linked_ledger_class",
    "loan_key",
    "owner_bucket_key",
    "transit_key",
]
