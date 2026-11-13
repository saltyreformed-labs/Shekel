"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_analytics.py``, moved verbatim by ``tools/split``.
"""

import json
import re
from decimal import Decimal
from html import unescape

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum

from tests._test_helpers import (
    cover_bare_settled_row,
    one_off_row_of,
    settle_day_columns,
    start_test_pay_list,
)


def _shell_autoload_target(html):
    """Return the hx-get URL of the shell's single auto-loading element.

    D13: the analytics shell auto-loads its active tab from the #tab-content
    spinner (the one element carrying ``hx-trigger="load"``).  Tests use this
    to assert WHICH tab a direct (non-HTMX) GET pre-selected, since the
    active-tab discriminator drives both that loader's target and the active
    pill.  Returns ``None`` when no auto-loader is present.
    """
    before_load = html.split('hx-trigger="load"')[0]
    loader = before_load[before_load.rfind("<div"):]
    match = re.search(r'hx-get="([^"]+)"', loader)
    return match.group(1) if match else None


def _chart_payload(html):
    """Parse the spending chart canvas's ``data-chart`` JSON from a page.

    The attribute value is HTML-escaped by Jinja autoescaping, so it is
    unescaped before parsing.  Raises (failing the test) when the canvas
    or its attribute is missing.

    Args:
        html: The rendered Spending tab HTML.

    Returns:
        The deserialized series dict.
    """
    match = re.search(r'data-chart="([^"]*)"', html)
    assert match is not None, "spending chart data-chart attribute missing"
    return json.loads(unescape(match.group(1)))


# ── Calendar Flow Strip and Day-Cell Hero Tests (slice-1 rebuild) ─────


def _extract_flow_strip_payload(page_html):
    """Parse the flow strip canvas's ``data-chart`` JSON out of a month view.

    The attribute value is HTML-escaped by Jinja autoescaping (quotes as
    ``&#34;``), so it is unescaped before ``json.loads`` -- the same
    decode path the browser's ``getAttribute`` performs.
    """
    import html as html_mod
    import json
    import re

    match = re.search(r"data-chart='([^']*)'", page_html)
    assert match is not None, "flow strip data-chart attribute missing"
    return json.loads(html_mod.unescape(match.group(1)))


# ── Taxes Tab Tests (slice-2 rebuild, T-P4) ──────────────────────────


def _seed_taxes_profile(seed_user, db):
    """Seed a 130k single/NC salary profile; it prices under the shipped law.

    The T-P4 route-test fixture: 130,000 / 26 = 5,000.00 gross per period
    exactly (no rounding residue), no deductions, no pay stub -- so every
    figure ``test_the_taxes_tab.py`` asserts on it is hand-computable from the 2026 law.
    """
    from app.extensions import db as _db
    from app.models.ref import FilingStatus
    from app.models.salary_profile import SalaryProfile

    filing_status = (
        _db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name="Taxes Tab Profile",
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("5000.00"))  # $130,000.00 a year / 26
    db.session.commit()
    return profile


def _settled_spending_txn(db, seed_user, period, name, category_key,
                          estimated, *, actual=None, due_date=None):
    """Create one settled (DONE) expense for the Spending route tests.

    Args:
        db: Database session fixture.
        seed_user: User fixture dict.
        period: The owning pay period.
        name: Transaction name.
        category_key: Key into ``seed_user['categories']`` (or ``None``).
        estimated: Estimated amount (string, Decimal-safe).
        actual: Entered actual (string) or ``None`` (settled-without-actual).
        due_date: Optional due date; ``None`` attributes by the period start.

    Returns:
        The flushed :class:`Transaction`.
    """
    cat = seed_user["categories"].get(category_key)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(estimated),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=cat.id if cat else None,
        due_date=due_date,
    )
    txn.status_id = ref_cache.status_id(StatusEnum.DONE)
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(due_date or period.start_date).items():
        setattr(txn, _column, _value)
    db.session.flush()
    # The record's home is the covering movement (X-bi-4b-1).
    cover_bare_settled_row(
        db.session, txn, Decimal(estimated),
        Decimal(actual) if actual is not None else None,
    )
    return txn
