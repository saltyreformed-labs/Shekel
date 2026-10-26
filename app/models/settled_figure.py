"""
Shekel Budget App -- A settled transfer's figure, as ONE value rather than two columns.

**The settled amount's ONE home** (plan step ``balance:X-bi-6-4d-2``, its
4d-4 half; rulings **R-BAL365**, **R-BAL366**, **R-BAL367**): what a settled
transfer's money moved and who wrote that figure -- the settle's own pricing
(``resolved``), a person (``typed``) or the bank's line (``observed``) --
stored on ``budget.transfers`` as ``settled_figure`` and
``settled_figure_source_id``.  Until this step each side's payment record
carried the pair, so ONE fact had two homes kept equal by the one writer
that wrote both; a transfer's money is one figure, and its two sides' records
say where and when it left and arrived.

Mapped onto ``budget.transfers`` by a SQLAlchemy ``composite()``, as
:class:`~app.models.amount_ownership.AmountOwnership` is (plan step
**X-au-k**, which states the argument in full): the pair is ONE mapped
attribute a writer assigns whole, so no writer can leave half of it, and
``ck_transfers_settled_figure_source_pairing`` is the backstop for one that
is not this application.

**This type admits EXACTLY the stored shape**: a figure of zero or more and
the ``ref.movement_figure_sources`` id naming who wrote it.  ``$0.00`` is a
figure (ruling **R-BAL365**: a close of nothing stores ``0.00`` and who
typed it, until plan step ``balance:X-db`` refuses a new one and tightens
the column above zero).  "No figure stored" -- a transfer that has never
settled, whose revert of a ``$0.00`` close cleared it, or whose soft delete
took its records off and released it with them -- is ``None`` on the
attribute, never an empty instance, for the factory reason
:func:`app.models.amount_ownership.from_columns` states.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SettledFigure:
    """WHAT a settled transfer's money moved, and who wrote the figure.

    Frozen, so re-stating a transfer's figure REPLACES it rather than editing
    it in place, which is what makes the pair one assignment and lets
    SQLAlchemy see the change.

    Attributes:
        figure: The amount the transfer's money moved, ``0.00`` or more.
        source_id: The ``ref.movement_figure_sources`` id of who wrote it.
            An id rather than a
            :class:`~app.enums.MovementFigureSourceEnum` member because this
            is the storage shape; the status seam decodes it through
            ``ref_cache``, so the model layer reads no cache.
    """

    figure: Decimal
    source_id: int

    def __post_init__(self) -> None:
        """Refuse every shape the two columns may not store.

        Raises:
            ValueError: When either half is missing (the pair is stored whole
                or not at all) or the figure is below zero (a settle verb
                takes a magnitude; the direction is each side's).
        """
        if self.figure is None or self.source_id is None:
            raise ValueError(
                "a settled figure names who wrote it and a writer names a "
                f"figure: got figure {self.figure!r} beside source "
                f"{self.source_id!r}"
            )
        if self.figure < 0:
            raise ValueError(
                f"a settled figure is a magnitude, never negative: got "
                f"{self.figure}"
            )

    def __composite_values__(self) -> tuple[Decimal, int]:
        """Return the two column values, in the order ``composite()`` maps them.

        SQLAlchemy's composite protocol.

        Returns:
            The figure and the source id, in that order.
        """
        return (self.figure, self.source_id)


def settled_figure_from_columns(
    figure: Decimal | None, source_id: int | None,
) -> SettledFigure | None:
    """Return the settled figure those two column values mean, or ``None``.

    **The composite's constructor**, for the reason
    :func:`app.models.amount_ownership.from_columns` gives: SQLAlchemy builds
    a composite from raw column values in machinery no caller enters, and
    the empty pair is "no figure stored", spelled ``None``.

    Args:
        figure: The transfer's stored ``settled_figure``, or ``None``.
        source_id: Its stored ``settled_figure_source_id``, or ``None``.

    Returns:
        The :class:`SettledFigure`, or ``None`` when neither is stored.

    Raises:
        ValueError: When exactly one half is stored.  Reached only for a row
            written around this application -- the pairing CHECK forbids it
            -- and raising is deliberate: half a figure is not one a budget
            may price.
    """
    if figure is None and source_id is None:
        return None
    return SettledFigure(figure, source_id)
