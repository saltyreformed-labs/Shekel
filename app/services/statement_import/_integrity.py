"""Does the statement agree with ITSELF?

A source carrying a per-line running balance states the same account twice --
once as a sequence of amounts, once as a sequence of balances -- and the two
must agree.  That redundancy is the only self-check the app has over a record it
did not author, and it is worth more than it looks: it catches a missing line, a
mis-ordered parse and an edited file, none of which any later stage could
distinguish from the truth.

**The chain is an EXPORT OPTION, so most files arrive without it**, and what
stands in its place is two other self-checks rather than nothing: the adapter
refuses a file that disagrees with its own ``Totals:`` summary
(``_secu_csv._verify_against_totals``), which catches a dropped line, and
:mod:`._anchor` solves the file's stated balance against a known opening, which
catches a file whose header and lines cannot both be true.  SECU stopped
offering the running-balance column on its standard download between the
2026-07-19 and 2026-08-16 exports, so every file the app sees today takes that
path.

**The header is not a substitute for this chain and never was** (ruling
**R-FP**'s adapter question, 2026-08-16): a bank writes it as of the export
INSTANT, so the developer's 2026-08-16 export reads ``$4,747.63`` -- 2026-08-13's
closing -- while listing two 2026-08-14 lines worth ``-$1,006.72``.  What
:mod:`._anchor` does with it is solve WHICH DAY it is the balance for rather
than assume it is the last one, which is the difference between reading a
header as a closing balance and reading it as an observation.

Services-boundary discipline: plain data in, nothing written, no clock read.
"""

from __future__ import annotations

from decimal import Decimal

from ._errors import StatementIntegrityError, StatementParseError


def carries_running_balance(lines: list) -> bool:
    """Return whether *lines* carry a running balance, refusing a mixture.

    Args:
        lines: :class:`~._line.StatementLine` values.

    Returns:
        True when every line carries a running balance, False when none does.

    Raises:
        StatementParseError: When only SOME lines carry one.  A partial column
            is not a source that lacks the fact -- it is a parse that lost it
            on some rows, and silently downgrading to "no self-check available"
            would turn a broken adapter into a missing feature.
    """
    if not lines:
        return False
    with_balance = sum(1 for line in lines if line.running_balance is not None)
    if with_balance == 0:
        return False
    if with_balance == len(lines):
        return True
    raise StatementParseError(
        f"This file carries a running balance on {with_balance} of "
        f"{len(lines)} lines.  A statement either states its balance for "
        f"every line or for none; a partial column means the file was read "
        f"wrongly.  Nothing was imported."
    )


def verify_running_balance(lines: list) -> None:
    """Refuse *lines* unless each balance follows from the one before it.

    The rule is one equation, applied to every consecutive pair:
    ``previous.running_balance + this.amount == this.running_balance``.

    **It is checked over the whole file rather than sampled**, and both ends
    matter: the FIRST line is the only one with no predecessor, so it is
    unchecked by construction and its own opening is derived from it
    (:func:`opening_balance`), while every later line is pinned twice.

    Measured on the developer's own SECU export (2026-07-19 pull, 306 lines):
    305 of 305 consecutive pairs satisfy it.  That is what licenses treating a
    break as a defect rather than as a quirk of the format.

    Args:
        lines: :class:`~._line.StatementLine` values in CHRONOLOGICAL order --
            the order every adapter returns.  Ordering is the precondition and
            it is load-bearing: the chain is a prefix sum, so a file read
            newest-first fails on every pair.

    Raises:
        StatementIntegrityError: When any pair disagrees.  It carries the count
            and the earliest break, because the earliest one is where the
            explanation is; the ones after it are usually the same defect seen
            again.
    """
    if not carries_running_balance(lines):
        return
    breaks = []
    for previous, current in zip(lines, lines[1:]):
        expected = previous.running_balance + current.amount
        if expected != current.running_balance:
            breaks.append(
                f"{current.posted_on} '{current.description[:40]}': the line "
                f"before it left {previous.running_balance}, this line moves "
                f"{current.amount}, so the balance should read {expected} and "
                f"the file says {current.running_balance}"
            )
    if breaks:
        raise StatementIntegrityError(len(breaks), breaks[0])


def opening_balance(lines: list) -> Decimal | None:
    """Return the balance BEFORE the first line, or ``None``.

    Derived from the first line's own two facts rather than read from a header,
    for the reason this module's docstring gives: the header was measured to
    disagree with the lines.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.

    Returns:
        The balance the account held before the earliest line, or ``None`` when
        the source carries no running balance.
    """
    if not lines or lines[0].running_balance is None:
        return None
    return lines[0].running_balance - lines[0].amount
