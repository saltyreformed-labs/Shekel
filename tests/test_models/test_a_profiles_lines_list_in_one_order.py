"""A profile's payroll lines list in one order on every read: by sort order, then id (plan#3148).

No door sets a line's ``sort_order``: ``PaycheckLineCreateSchema`` declares
no such field and drops unknown keys (``BaseSchema``'s ``unknown = EXCLUDE``),
the edit door writes only its ``_LINE_UPDATE_FIELDS``, which leave it out, and
no form in the history ever set it.  So every line holds the column's default
of 0 and all of a profile's lines tie on it.  ``SalaryProfile.lines`` ordered
by ``sort_order`` alone until this task, which left the salary page's lines
section, the pay stub form and its report listing them, and the cockpit
listing lines of one kind and amount, in whatever order the database read
them.  The order moves no money: each line is priced on its own and the
results are only summed (``paycheck_calculator._lines._priced_lines``).

**How the database's order parts from the order they were added**, measured
by plan#3148's review on 2026-11-09: the planner read a profile's lines through
an index scan of ``idx_paycheck_lines_profile``, so ties reached the sort in
that index's order, which is page slot order.  A line's name is in
``uq_paycheck_lines_profile_name``, so a rename is not a HOT update: it writes
the row's new version to a later slot, with a new entry in every index, and
the renamed line then reads after its twin.  The names are chosen so that name
order disagrees with the order added too, so the case also fails under a plan
reading ``uq_paycheck_lines_profile_name`` and under a tie broken by name.
"""

from decimal import Decimal

import pytest

from app.enums import PaycheckLineKindEnum
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services.projection_inputs import load_active_salary_profiles
from tests._test_helpers import make_flat_paycheck_line, make_salary_profile


def test_the_relationship_orders_by_sort_order_then_id():
    """``SalaryProfile.lines`` states its order as sort order, then id, for every loader.

    The case below catches a sort-order-only order only while the planner reads
    the lines in an order other than the one they were added in; this holds on
    every plan.
    """
    assert [column.key for column in SalaryProfile.lines.property.order_by] == [
        "sort_order", "id",
    ]


@pytest.mark.usefixtures("seed_periods")
def test_two_lines_list_in_the_order_they_were_added_after_the_first_is_renamed(app, seed_user):
    """Health added first and Dental second list Health Plan, Dental after Health is renamed.

    Both lines hold sort order 0.  Renaming Health to Health Plan writes its
    new version after Dental's row, and Dental sorts before Health Plan by
    name; the lazy read and the engine's eager loader both still list the
    renamed line first.
    """
    with app.app_context():
        profile = make_salary_profile(seed_user, db.session, pay=Decimal("2000.00"))
        db.session.flush()
        pre_tax = PaycheckLineKindEnum.PRE_TAX_DEDUCTION
        health = make_flat_paycheck_line(profile, "Health", "280.00", pre_tax)
        dental = make_flat_paycheck_line(profile, "Dental", "35.00", pre_tax)
        db.session.commit()
        health.name = "Health Plan"
        db.session.commit()
        assert (health.sort_order, dental.sort_order) == (0, 0)
        assert health.id < dental.id
        db.session.expire_all()
        assert [line.id for line in profile.lines] == [health.id, dental.id]
        db.session.expire_all()
        (loaded,) = load_active_salary_profiles(
            seed_user["user"].id, seed_user["scenario"].id,
        )
        assert [line.id for line in loaded.lines] == [health.id, dental.id]
