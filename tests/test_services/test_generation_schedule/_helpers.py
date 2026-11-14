"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_generation_schedule.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.enums import TxnTypeEnum
from app import ref_cache
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import pay_period_write
from tests._test_helpers import (
    rhythm_of,
    last_covered_day,
    make_cadence_rule,
    state_template_price,
)


#: The one database door every pay-calendar derivation goes through, as
#: ``tests/test_arch/test_one_read_pass_per_render/`` names it.  A service
#: that derives one below its caller's door shows up here as a non-zero count.
_CALENDAR_DOOR = ("app.services.pay_calendar._loader", "calendar_for")


# ``seed_periods`` runs 10 biweekly periods from 2026-01-02, so the paydays are
# 01-02, 01-16, 01-30, 02-13, 02-27, 03-13, 03-27, 04-10, 04-24, 05-08.
# Two facts below depend on that shape and are asserted rather than assumed:
#   * JANUARY holds THREE of them (indices 0, 1, 2) -- the shape that produced
#     the $502.45 error;
#   * MAY holds one so far (index 9), so appending period 10 (2026-05-22) lands
#     a NEW period in a month a ``Monthly First`` rule has already covered --
#     the shape that produced the duplicate rows.
_JANUARY_THIRD_PAYCHECK_INDEX = 2


_LAST_SEEDED_INDEX = 9


def _make_template(seed_user, cadence, **rule_kwargs):
    """Create a recurring expense template and its rule.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.
        **rule_kwargs: Extra columns for the rule (``day_of_month``,
            ``start_date``, ...).

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    rule_kwargs.pop("offset_periods", None)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Car Payment"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Phone Allowance",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("39.54"))
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_cadence_rule(
        template, cadence,
        interval_n=rule_kwargs.pop("interval_n", 1),
        fires_on_day=rule_kwargs.pop("day_of_month", None),
        fires_in_month=rule_kwargs.pop("month_of_year", None),
        **rule_kwargs,
    )
    return template


def _rows(template, scenario_id):
    """Return the template's rows keyed by their period's start date.

    Args:
        template: The template whose rows to read.
        scenario_id: The scenario to read within.

    Returns:
        ``{start_date: [Transaction, ...]}``, one entry per period holding a
        row.
    """
    result: dict[date, list[Transaction]] = {}
    rows = (
        db.session.query(Transaction)
        .filter_by(template_id=template.id, scenario_id=scenario_id)
        .all()
    )
    for row in rows:
        result.setdefault(row.pay_period.start_date, []).append(row)
    return result


def _placements(template, scenario_id):
    """Return what a pass DECIDED, free of row identity.

    ``_rows`` keys by period and hands back the ``Transaction`` objects, which
    two passes over two templates can never compare equal.  What a generate
    pass decides is which paycheck each row lands in and what the definition
    says there, so that is what this reduces to.

    Args:
        template: The template whose rows to read.
        scenario_id: The scenario to read within.

    Returns:
        ``{start_date: [(due_date, estimated_amount), ...]}``, the due dates
        sorted so a repeated paycheck compares stably.
    """
    return {
        start: sorted((row.due_date, row.estimated_amount) for row in rows)
        for start, rows in _rows(template, scenario_id).items()
    }


def _append_period(seed_user, seed_periods):
    """Append one 14-day pay period after the seeded schedule.

    Reproduces exactly what ``pay_period_admin.extend_pay_periods`` returns: a
    flushed batch of new, EMPTY periods, and nothing else changed.  The
    repopulation is the caller's second call since ruling **R-R38**, which is
    what :func:`_populate` runs.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        seed_periods: The seeded schedule.

    Returns:
        The new ``[PayPeriod]`` batch, flushed.
    """
    created = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
        num_periods=1,
        rhythm=rhythm_of(14),
    )
    db.session.flush()
    return created
