"""Presentation-only Jinja template filters.

Registered on the application in :func:`app.create_app` via
:func:`register_template_filters`.  Extracted from the factory
(``app/__init__.py``) so that module stays under its statement / line
ceiling and so display helpers have a home that is not the
already-large factory.

Every filter here transforms a value the route or service already
computed -- most for DISPLAY, and :func:`reviewed_token` for the WIRE.
None performs financial arithmetic: monetary math lives in the services
per the project's "templates display, never compute" rule (CLAUDE.md).
The two arithmetic helpers below (``to_percent``, ``months_to_years``)
operate on rates and term lengths, not money, and exist precisely so
templates do not inline that math.

**A wire transform belongs here for the same reason a display one does**,
and :func:`reviewed_token` is the first: the value it emits is READ BACK
by :class:`~app.schemas.validation.statements.ReviewedRowField`, so the
format has to be stated once and reached from both sides.  A template
composing those fields itself would be the second spelling, and nothing
in the tree fails when a template and a validator drift apart.
"""

from datetime import datetime
from decimal import Decimal

from flask import Flask

from app.services.grid_view_service import (
    card_dom_id,
    cell_dom_id,
    cell_key,
    drawn_status,
    leg_dom_id,
)
from app.services.salary_cockpit_service import clean_raise_label, is_pay_cut
from app.services.statement_match import (
    CandidateRow,
    MatchProposal,
    ReviewedDifference,
    as_reviewed,
)
from app.services.transfer_legs import TransferLeg
from app.utils.dates import month_name, to_display_tz

# Months in a year -- named so the year conversion is not a bare literal.
_MONTHS_PER_YEAR = 12


def to_percent(value: Decimal | None) -> Decimal | None:
    """Convert a storage-domain decimal-fraction rate into its percent.

    Presentation transformation only (E-16 / MED-04): the rate is
    stored as ``Decimal("0.07")`` for 7 %, the user-facing display is
    ``7.00 %``.  Multiplying by ``100`` in :class:`~decimal.Decimal`
    preserves the stored precision; the older Jinja pattern
    ``value|float * 100`` introduced a binary-float cast on the Decimal
    before the multiply and is no longer used anywhere.

    Args:
        value: Decimal storage-domain rate, or ``None``.

    Returns:
        ``value * 100`` as a Decimal, or ``None`` when ``value`` is
        ``None``.  Numeric formatting (``"%.2f"|format(...)``) is applied
        by the caller; this filter never quantises so the caller's chosen
        precision wins.
    """
    if value is None:
        return None
    return Decimal(str(value)) * Decimal("100")


def local_datetime(value: datetime | None, fmt: str = "%b %-d, %Y") -> str:
    """Render a stored UTC instant in the user's display timezone.

    Presentation-only conversion: every ``timestamptz`` in this app is
    stored UTC; this expresses one in
    :data:`app.utils.dates.DISPLAY_TIMEZONE` (Eastern) before formatting,
    so a late-evening Eastern event does not display on the next UTC day.
    ``fmt`` is a ``strftime`` format (default: ``"Jun 11, 2026"``).
    Returns ``""`` for ``None`` so a template can pipe an absent timestamp
    through without guarding.

    Args:
        value: A stored UTC datetime, or ``None``.
        fmt: A ``strftime`` format string.

    Returns:
        The display-timezone formatted string, or ``""`` when ``value``
        is ``None``.
    """
    if value is None:
        return ""
    return to_display_tz(value).strftime(fmt)


def ordinal(value: int | None) -> str:
    """Render an integer with its English ordinal suffix (``1`` -> ``"1st"``).

    Display helper for day-of-month rendering (polyglot audit TPLB/TPL-12,
    moving the inline ternary out of ``loan/dashboard.html``).  Handles the
    11/12/13 "teens" exception, so 11/12/13 are ``"th"`` while 1/21/31 are
    ``"st"``, 2/22 ``"nd"``, 3/23 ``"rd"``.

    Args:
        value: The integer to suffix, or ``None``.

    Returns:
        ``"<n><suffix>"`` (e.g. ``"21st"``), or ``""`` when ``value`` is
        ``None``.
    """
    if value is None:
        return ""
    number = int(value)
    if 11 <= number % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def months_to_years(months: int | None, digits: int = 1) -> int | float | str:
    """Convert a term length in months to years for display (24 -> ``2.0``).

    Display helper for loan term rendering (polyglot audit TPLB/TPL-12,
    replacing inline ``(term_months / 12)|round(...)`` in the loan
    templates).  Not money: a term length, kept out of the template per
    the "templates display, never compute" rule.

    Args:
        months: Whole-month term length, or ``None``.
        digits: Decimal places to round to.  ``0`` returns a whole-year
            :class:`int` (matching the old ``|round(0)|int`` site); a
            positive value returns a rounded :class:`float` (matching the
            old ``|round(1)`` site).

    Returns:
        The year value as an ``int`` (``digits == 0``) or ``float``
        (``digits > 0``), or ``""`` when ``months`` is ``None``.
    """
    if months is None:
        return ""
    years = round(int(months) / _MONTHS_PER_YEAR, digits)
    if digits == 0:
        return int(years)
    return years


def raise_label(value: str | None) -> str:
    """Display-clean a calculator ``raise_event`` string for templates.

    Thin filter wrapper over
    :func:`app.services.salary_cockpit_service.clean_raise_label` so the
    salary templates (the anatomy card's raise alert, the projection
    ledger's raise badges) render the same cleaned label the cockpit
    producers emit: title-cased type + trailing-zero-trimmed percentage
    (``"MERIT +2.5000%"`` -> ``"Merit +2.5%"``), flat amounts kept to the
    cent.  Presentation-only: the cleaning is pure string manipulation on
    the calculator's own Decimal-derived text.

    Args:
        value: The verbatim ``PeriodInfo.raise_event`` string, or ``None``.

    Returns:
        The cleaned display label, or ``""`` for ``None``/empty input so a
        template can pipe an absent event through without guarding.
    """
    if value is None:
        return ""
    return clean_raise_label(value)


def pay_cut(value: str | None) -> bool:
    """Whether a calculator ``raise_event`` string announces a pay cut (R-SAL85).

    Thin filter wrapper over
    :func:`app.services.salary_cockpit_service.is_pay_cut`, so the anatomy
    banner and the projection ledger's badge choose the amber "Pay cut:"
    styling from one reading of the label.

    Args:
        value: The verbatim ``PeriodInfo.raise_event`` string, or ``None``.

    Returns:
        ``True`` when the label is a recorded pay cut.
    """
    return is_pay_cut(value)


def reviewed_token(row: CandidateRow) -> str:
    """Render one candidate row as the form value a match submits for it.

    Thin filter wrapper over
    :func:`app.services.statement_match.as_reviewed`, the same shape
    :func:`raise_label` is, and for a sharper reason: this string is not
    read by a person, it is read back by
    :class:`~app.schemas.validation.statements.ReviewedRowField` on the
    next request.  The statement review screen emits one per row it
    offers, carrying the row's kind, its id, and the figure and revision
    the owner is looking at -- which is what lets the accept door refuse
    an item whose row has MOVED since the page was rendered (finding
    **N-336**, plan step ``bank_import:X-f6d-3``).

    No arithmetic and no decision: the service builds the value, this
    hands it to the form.

    Args:
        row: A :class:`~app.services.statement_match.CandidateRow` the
            review screen is rendering.

    Returns:
        Its token, ``"<kind>:<row_id>:<cash_amount>:<version_id>"``.
    """
    return as_reviewed(row).token


def stated_difference(proposal: MatchProposal) -> str:
    """Render the difference a proposal states, as the form value it submits.

    Plan step ``bank_import:X-gj-1b``.  The SECOND wire transform here, and it
    is here for :func:`reviewed_token`'s reason one grain up: the accept door
    exempts no shape since the developer's ruling of 2026-08-30, so every
    match states the difference it was reviewed against, and this string is
    read back by
    :class:`~app.schemas.validation.statements.ReviewedDifferenceField` on the
    next request.  ``reviewed_token`` carries the state of one ROW; this
    carries the SUM over them, which is the one figure no per-row guard can
    see being wrong (finding **N-336**).

    **It names no member, and that is the figure's meaning rather than an
    omission** (plan step ``bank_import:X-gp``).  The consent value carries
    the member a difference lands on beside the figure, and a proposal is
    exact, or one line against one row: the first has nothing to land, and
    the second is answered by ruling **R-GD(a)**'s determinacy.  Written
    through :attr:`~app.services.statement_match.ReviewedDifference.token`
    rather than spelling the figure here, so every consent value a surface
    emits has one writer.

    **A filter rather than a property on the proposal**, which is where a
    first version put it.  ``MatchProposal`` already publishes
    :attr:`~app.services.statement_match.MatchProposal.difference` as the
    ``Decimal`` every reader wants; what a template needs is its WIRE
    SPELLING, and that is a fact about the form rather than about the
    proposal -- the same boundary that keeps ``as_reviewed`` in the service
    and its token here.  The module's own line ceiling is what surfaced it:
    adding this there put ``_offers`` at 1,014 lines, which is finding
    **balance:N-365** asking the question the answer to which was that the
    code was in the wrong module.

    No arithmetic and no decision: the service subtracts, this hands the
    result to the form.

    Args:
        proposal: The :class:`~app.services.statement_match.MatchProposal` a
            card is rendering.

    Returns:
        Its plain decimal spelling, ``"0.00"`` for the exact and group tiers.
    """
    return ReviewedDifference(figure=proposal.difference).token


def is_transfer_leg(item) -> bool:
    """Jinja test ``transfer_leg``: is this grid item one side of a transfer?

    The grid's row macros and cell partial draw a plan row and a
    :class:`~app.services.transfer_legs.TransferLeg` through one template
    (leaf ``X-bi-6-1``, ruling **R-BAL87**) and differ only where the item's
    IDENTITY is spelled -- the cell's DOM id, the doors its buttons call, the
    row-only affordances (purchases, credit).  This test is the one predicate
    those sites branch on, so a template never infers the shape from an
    attribute that happens to be absent.

    Args:
        item: What a cell is drawing.

    Returns:
        ``True`` for a leg, ``False`` for a row.
    """
    return isinstance(item, TransferLeg)


def register_template_filters(app: Flask) -> None:
    """Register every presentation filter, test and global on the given app.

    Called once from :func:`app.create_app`.  Idempotent: re-registering
    the same name overwrites it with the same callable, so a repeat call
    has no observable effect.

    Args:
        app: The Flask application whose ``jinja_env`` gains the filters.
    """
    # The grid's two item shapes (leaf X-bi-6-1): the test that tells them
    # apart and the key a page's per-cell maps hold each under.
    app.add_template_test(is_transfer_leg, "transfer_leg")
    app.add_template_global(cell_key, "cell_key")
    app.add_template_global(cell_dom_id, "cell_dom_id")
    app.add_template_global(card_dom_id, "card_dom_id")
    app.add_template_global(leg_dom_id, "leg_dom_id")
    # The status an item is drawn as (plan task plan#3054): Credit for a row
    # put on the card, which is stored Paid.
    app.add_template_global(drawn_status, "drawn_status")
    app.add_template_filter(to_percent, "to_percent")
    app.add_template_filter(local_datetime, "local_datetime")
    app.add_template_filter(ordinal, "ordinal")
    app.add_template_filter(months_to_years, "months_to_years")
    app.add_template_filter(month_name, "month_name")
    app.add_template_filter(raise_label, "raise_label")
    app.add_template_filter(pay_cut, "pay_cut")
    app.add_template_filter(reviewed_token, "reviewed_token")
    app.add_template_filter(stated_difference, "stated_difference")
