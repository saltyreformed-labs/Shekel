"""
Shekel Budget App -- Effective-dated resolution

The ONE "which row is in effect on a day" walk for a series of rows that
each carry an ``effective_date`` and supersede one another: the row with the
greatest ``effective_date`` at or before the day, and ``None`` before the
earliest row (the series did not exist yet).  Escrow line versions
(:mod:`app.services.escrow_calculator`) and a card's APR rows
(:mod:`app.services.card_apr`) both resolve through it; it was spelled in
each until plan step credit_card:CC-3's review found the second copy (rule 14:
one walk).  Six more spellings of the family remain, four with a fallback of their
own and two with this leaf's ``None`` -- three in
:mod:`app.services.rate_period_engine` (``_rate_at_date``,
``_recorded_pi_at_date``, ``period_for_date``), one in
:mod:`app.services.amortization_engine._projection` (``_governing_terms``)
and two in :mod:`app.services.template_amount_service`
(``_version_in_effect``, ``build_amount_history``) -- and folding them onto
this leaf is ledger row **BAL-524**'s, owned by plan step ``balance:X-ct``
(filed 2026-09-18; the row lists each with its fallback).

**A series that HOLDS FLAT before its first row** has its walk here too,
:func:`in_effect_or_earliest`, built on :func:`in_effect_on` rather than beside
it, and the span of days it gives each row, :func:`span_in_effect_or_earliest`,
built on that: a card's dated terms read through both (plan#2331, ruling
plan#2333), and
``template_amount_service._version_in_effect`` is the same shape still spelled
in its own module, one of BAL-524's.

A leaf: the standard library only.
"""

from collections.abc import Iterable
from datetime import date
from typing import TypeVar

#: Any row carrying an ``effective_date``; the caller's type comes back.
Row = TypeVar("Row")


def in_effect_on(rows: Iterable[Row], on_date: date) -> Row | None:
    """Return the row in effect on *on_date*, or ``None`` before the first.

    Args:
        rows: Rows each exposing an ``effective_date``, in any order.
        on_date: The day to resolve.

    Returns:
        The row with the greatest ``effective_date <= on_date``; ``None``
        when no row is dated on or before *on_date*.
    """
    candidates = [row for row in rows if row.effective_date <= on_date]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.effective_date)


def in_effect_or_earliest(rows: Iterable[Row], on_date: date) -> Row | None:
    """Return the row in effect on *on_date*; before the first, the first.

    :func:`in_effect_on`'s answer wherever it has one, and the EARLIEST row
    for a day before every row: the series holds flat backwards, ruling
    **R-I**'s shape for a stated figure, so a resolver over a series with
    any row is total.  ``None`` is reserved for an EMPTY series.

    Args:
        rows: Rows each exposing an ``effective_date``, in any order.
        on_date: The day to resolve.

    Returns:
        The row with the greatest ``effective_date <= on_date``; the row with
        the least ``effective_date`` when none is on or before *on_date*;
        ``None`` when *rows* is empty.
    """
    rows = list(rows)
    found = in_effect_on(rows, on_date)
    if found is not None:
        return found
    return min(rows, key=lambda row: row.effective_date, default=None)


def span_in_effect_or_earliest(
    rows: Iterable[Row], on_date: date,
) -> tuple[Row, date | None, date | None] | None:
    """Return :func:`in_effect_or_earliest`'s row and the span ``[start, end)`` it governs.

    The span is every day that walk answers this same row on: from the row's
    own date -- or, for the EARLIEST row, from the beginning of time, because
    the series holds flat backwards -- until the next row's date, or forever
    for the latest.  Stated here, beside the hold-flat rule it follows, so a
    walk that steps from span to span (a card's statement closes,
    :mod:`app.services.card_statement`) never re-derives which days the rule
    gives a row.

    Args:
        rows: Rows each exposing an ``effective_date``, in any order.
        on_date: The day to resolve.

    Returns:
        ``(row, start, end)``: ``start`` is ``None`` for the earliest row and
        ``end`` is ``None`` for the latest; ``None`` when *rows* is empty.
    """
    rows = list(rows)
    row = in_effect_or_earliest(rows, on_date)
    if row is None:
        return None
    has_earlier = any(other.effective_date < row.effective_date for other in rows)
    end = min(
        (other.effective_date for other in rows
         if other.effective_date > row.effective_date),
        default=None,
    )
    return row, (row.effective_date if has_earlier else None), end
