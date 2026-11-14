"""Whether an account is a CONFIGURED loan: the one Python statement of it.

Card plan#2436, task plan#3964, ruling **plan#3954**.  A configured loan is an
account whose KIND amortizes and that CARRIES loan terms; its balance comes
from an amortization schedule and its books start on its origination.  Two
readers ask that question in two tiers that cannot import each other's
answer:

* the balance seam (:func:`app.services.balance_at._resolution.configured_loan`),
  which every balance surface dispatches on;
* the books boundary (:func:`app.services.cash_ledger.books_start`), where the
  day money may first move into an account is decided.

The seam's answer is private to :mod:`app.services.balance_at`, and the
boundary sits below it, so until this module the boundary could not ask it
and read the opening record instead: the second home ruling plan#3954
deleted.  The question lives HERE, in the loan leaf both tiers already
import, and the seam's function is built on it (``CLAUDE.md`` rule 14, the
placement clause).  The database tier states it once more, in SQL
(``app.opening_infrastructure._base._CONFIGURED_LOAN_ORIGINATION_SQL``), which is
that tier's one statement and reads the same two halves.

Reads only, no commits, no Flask symbol.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

#: What the caller's terms lookup answers for a configured loan: its
#: :class:`~app.models.loan_params.LoanParams`, or a value built from them.
_Terms = TypeVar("_Terms")


def when_configured_loan(
    account: Account, terms_lookup: Callable[[], _Terms | None],
) -> _Terms | None:
    """Return *terms_lookup*'s answer iff *account* is a configured loan.

    **Both halves are load-bearing, and neither implies the other.**  The
    KIND half is :func:`~app.services.account_projection.classify_account`:
    loan terms on an account whose type does not amortize are a data defect
    (an account type's ``has_amortization`` turned off under accounts that
    carry terms), and the balance does not amortize such an account.  The
    TERMS half is the lookup: an amortizing account whose terms were never
    entered is cash to the balance, which is an ordinary state (create a
    Mortgage, do not finish the loan setup form).

    **The terms are the CALLER's lookup, asked only for an amortizing
    account**, because the two callers hold them in different places.  The
    balance seam reads them off its read pass's memoized resolution, so a
    pass issues no query here that it did not issue before.  The books
    boundary loads them directly
    (:func:`app.services.loan_loaders.load_loan_params`).  A cash account
    is answered by its kind alone, with no lookup.

    Args:
        account: The account, with its ``account_type`` loaded or loadable.
        terms_lookup: A zero-argument callable answering the account's loan
            terms, or a value built from them, and ``None`` exactly when the
            account carries no :class:`~app.models.loan_params.LoanParams`
            row.  Called at most once, and only when the account's kind
            amortizes.

    Returns:
        The lookup's answer when *account* is a configured loan; ``None``
        when its kind does not amortize or it carries no terms.
    """
    if classify_account(account) is not AccountProjectionKind.AMORTIZING:
        return None
    return terms_lookup()
