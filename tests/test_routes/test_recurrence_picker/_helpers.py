"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_recurrence_picker.py``, moved verbatim by ``tools/split``.
"""

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import BusinessDayShiftEnum, RecurrenceUnitEnum, TxnTypeEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrenceUnit
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.services.recurrence import modelled_unit
from tests._test_helpers import state_template_price


# ── Helpers ──────────────────────────────────────────────────────────


def _unmodelled_unit_id(name="Blue Moon"):
    """Insert and commit a ``ref.recurrence_units`` row with no enum member.

    Committed rather than flushed because the route under test runs in its own
    request and session.

    **It planted a surplus ``ref.recurrence_patterns`` row until plan step
    R7c-c**, which dropped the column a rule pointed at one with.  The state a
    rule can still reach is a ``unit_id`` the enums do not model -- a seed the
    enums have diverged from, a hand edit, a partial restore -- so the same
    class of unreadable rule is manufactured on the axis that survived.

    Args:
        name: The row's name; asserted not to collide with an enum value.

    Returns:
        int: The new row's primary key.
    """
    assert name not in {member.value for member in RecurrenceUnitEnum}
    row = RecurrenceUnit(name=name)
    db.session.add(row)
    db.session.commit()
    return row.id


def _assert_unreadable(unit_id):
    """Assert the seam cannot read *unit_id*, i.e. the door earns its keep.

    Without this, a test that only asserts "the route refused" cannot tell a
    door that refuses bad input from a door that refuses everything.

    Args:
        unit_id: The id the surface just refused.
    """
    assert modelled_unit(unit_id) is None


def _savings_account(seed_user):
    """Create a second account so a transfer template has a destination."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _tag(html, element_id):
    """Return the opening tag of the element carrying ``id="element_id"``.

    ``field_is_disabled`` keys on ``name=``, which cannot separate the two
    interval controls: both post ``interval_n``, which is the whole point of
    exactly one of them being enabled.

    Args:
        html: The rendered page.
        element_id: The ``id`` attribute to find.

    Returns:
        str: The opening tag, including its attributes.

    Raises:
        AssertionError: The element is absent, so a typo fails loud.
    """
    match = re.search(rf'<(?:input|select)\b[^>]*\bid="{element_id}"[^>]*>', html)
    assert match is not None, f'id="{element_id}" not found in rendered HTML'
    return match.group(0)


def _options(html, select_id):
    """Return ``(value, is_selected)`` for each option of one ``<select>``.

    Args:
        html: The rendered page.
        select_id: The select's ``id``.

    Returns:
        list[tuple[str, bool]]: In document order.
    """
    block = re.search(
        rf'<select\b[^>]*\bid="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL,
    )
    assert block is not None, f'<select id="{select_id}"> not found'
    return [
        (match.group(1), "selected" in match.group(0))
        for match in re.finditer(
            r'<option\b[^>]*\bvalue="([^"]*)"[^>]*>', block.group(1),
        )
    ]


def _option_labels(html, select_id):
    """Return the visible TEXT of each option of one ``<select>``.

    :func:`_options`' sibling, reading the half a value comparison cannot see.
    Whitespace is collapsed because the template wraps long labels across
    lines.

    Args:
        html: The rendered page.
        select_id: The select's ``id``.

    Returns:
        list[str]: In document order.
    """
    block = re.search(
        rf'<select\b[^>]*\bid="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL,
    )
    assert block is not None, f'<select id="{select_id}"> not found'
    return [
        " ".join(text.split())
        for text in re.findall(
            r"<option\b[^>]*>(.*?)</option>", block.group(1), re.DOTALL,
        )
    ]


def _selected(html, select_id):
    """Return the single selected option value of one ``<select>``.

    ``None`` when none is selected -- which is never harmless: a ``<select>``
    with nothing selected silently submits its FIRST option, so "no selection"
    and "the first entry" are the same request.

    Raises:
        AssertionError: More than one option carries ``selected``.
    """
    chosen = [value for value, is_selected in _options(html, select_id) if is_selected]
    assert len(chosen) <= 1, f"multiple options selected in {select_id}: {chosen}"
    return chosen[0] if chosen else None


def _expense_type_id():
    """Return the Expense transaction-type id."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _rule_on_cadence(owner, cadence, interval_n=None, unit_id=None):
    """Create and flush a rule of *cadence* on *owner*, bypassing the write door.

    Deliberately constructed rather than authored: ``author_rule`` resolves
    before it writes, so it REFUSES an unreadable cadence -- which is the guard
    under test -- and only a direct construction can produce the row a diverged
    ``ref`` seed would leave behind.

    **The cadence is a SHARED constant, not hard-coded here**, and the
    difference matters more than the fixture's convenience: a row whose columns
    disagree with each other is one no application path can write, and a sweep
    over such rows measures the fixture rather than the mapping.

    **The OWNER is still stated, even bypassing the door** (plan step R-F6):
    ``ck_recurrence_rules_one_owner`` refuses a rule belonging to nothing, so
    what this fixture bypasses is the RESOLUTION, not the ownership.  Assigned
    through ``owner.recurrence_rule`` so it stays kind-agnostic across the two
    definition kinds, exactly as ``author_rule`` does.

    Args:
        owner: The ``TransactionTemplate`` or ``TransferTemplate`` the rule
            belongs to.  Mutated: its ``recurrence_rule`` is set.
        cadence: One of :data:`BASELINE_CADENCES`.
        interval_n: The interval to store; defaults to the cadence's own.
        unit_id: A ``ref.recurrence_units`` id to store INSTEAD of the
            cadence's, for the unreadable-rule cases.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    own_interval = 1 if cadence.interval_n is None else cadence.interval_n
    rule = RecurrenceRule(
        interval_n=own_interval if interval_n is None else interval_n,
        unit_id=(
            ref_cache.recurrence_unit_id(cadence.unit) if unit_id is None
            else unit_id
        ),
        placement_id=ref_cache.period_placement_id(cadence.placement),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        starts_on=date(2026, 1, 1),
    )
    owner.recurrence_rule = rule
    db.session.add(rule)
    db.session.flush()
    return rule


def _template_with_cadence(
    seed_user, cadence, interval_n=None, unit_id=None,
):
    """Create a committed transaction template carrying a rule of that cadence."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        name="Rent",
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=_expense_type_id(),
        account_id=seed_user["account"].id,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("1200.00"))
    # The definition first, then the cadence onto it (plan step R-F6).
    _rule_on_cadence(template, cadence, interval_n, unit_id)
    db.session.commit()
    return template


def _transfer_template_with_cadence(
    seed_user, destination, cadence, unit_id=None,
):
    """Create a committed transfer template carrying that cadence."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        name="To Savings",
        from_account_id=seed_user["account"].id,
        to_account_id=destination.id,
        category_id=seed_user["categories"]["Rent"].id,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("50.00"))
    # The definition first, then the cadence onto it (plan step R-F6).
    _rule_on_cadence(template, cadence, unit_id=unit_id)
    db.session.commit()
    return template
