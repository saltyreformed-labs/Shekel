"""A profile's raises load in one order on every read: by start date, then by id (task plan#3128).

Two raises may start on one day when their types differ
(``uq_salary_raises_profile_type_date``), and the order they load in is the
order the walk compounds them in and the banner names them in
(:func:`~app.services.salary_raises.applications_between` keeps input order
between two applications on one date by one method).  Each step rounds to the
cent (ruling R-SAL60), so two percentage raises compounded in the other order
can land a cent apart.  ``SalaryProfile.raises`` ordered by ``effective_date``
alone until this task, which left two raises on one day in whatever order the
database returned them.

**How the database's order parts from the order they were written**, measured
2026-11-09 on the suite's cluster, where the planner read the profile's raises
through a bitmap scan of ``uq_salary_raises_profile_type_date``: PostgreSQL
returned the two rows that tie on the sort key in the order it read them, page
slot order.  An edit that changes a column that index holds (the start date or
the type) gives the row's new version a new index entry further along the page,
so the edited raise then read AFTER its twin.  An edit to the notes, which no
index holds, did NOT move it under that plan, because the index still reached
the row through its first slot; so a case built on a notes edit passes with
the defect in place, which is why the case below corrects a start date, an edit
the raise's own edit door allows.

**The worked case**, every figure made up (R-BAL132): $2,000.24 a paycheck
from 2026-01-02; a 3% merit raise typed first, from Tuesday 2026-02-03; a 2%
cola raise from Monday 02-02; then the merit's date corrected to 02-02.

* merit, then cola: 2,000.24 x 1.03 = 2,060.2472 -> 2,060.25; x 1.02 =
  2,101.455 -> 2,101.46.
* cola, then merit: 2,000.24 x 1.02 = 2,040.2448 -> 2,040.24; x 1.03 =
  2,101.4472 -> 2,101.45.

The paycheck of 2026-02-27 pays 02-08..02-21, wholly after the raises, so it
pays the compounded figure; the paycheck of 02-13 pays 01-25..02-07, which
holds 02-02, so it is the one badged.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis
from app.services.projection_inputs import load_active_salary_profiles
from tests._test_helpers import make_salary_profile

#: The day both raises start, a Monday inside the 02-13 paycheck's 01-25..02-07.
_STARTS = date(2026, 2, 2)


def _raise(profile_id, raise_type, percentage, starts):
    """Add a one-time percentage raise of *raise_type* from *starts* (uncommitted)."""
    row = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=ref_cache.raise_type_id(raise_type),
        effective_date=starts,
        percentage=Decimal(percentage),
        is_recurring=False,
    )
    db.session.add(row)
    return row


def _two_raises_the_first_corrected(seed_user):
    """The worked case's profile: the merit typed first with the wrong day, then corrected.

    Returns:
        ``(profile, merit, cola)``, committed; the profile is expired, so
        its raises load from the database when first read.  The merit's id
        is the lower.
    """
    profile = make_salary_profile(
        seed_user, db.session, pay=Decimal("2000.24"), pay_from=date(2026, 1, 2),
    )
    db.session.flush()
    merit = _raise(profile.id, RaiseTypeEnum.MERIT, "0.0300", date(2026, 2, 3))
    db.session.flush()
    cola = _raise(profile.id, RaiseTypeEnum.COLA, "0.0200", _STARTS)
    db.session.commit()
    merit.effective_date = _STARTS
    db.session.commit()
    assert merit.id < cola.id
    return profile, merit, cola


def test_the_relationship_orders_by_start_date_then_id():
    """``SalaryProfile.raises`` states its order as start date, then id, for every loader.

    The cases below catch a date-only order only while the planner reads the
    raises in slot order (the bitmap scan the module docstring measured): a
    plain scan of ``uq_salary_raises_profile_type_date`` would return a tie
    in raise-type order, which can hide the defect.  This holds on every plan.
    """
    assert [column.key for column in SalaryProfile.raises.property.order_by] == [
        "effective_date", "id",
    ]


@pytest.mark.usefixtures("seed_periods")
class TestTwoRaisesOnOneDay:
    """Two raises starting on one day load, compound and badge in the order they were written."""

    def test_they_load_by_id_after_the_first_is_corrected(self, app, seed_user):
        """The lazy read and the engine's eager loader both answer merit, then cola."""
        with app.app_context():
            profile, merit, cola = _two_raises_the_first_corrected(seed_user)
            assert [row.id for row in profile.raises] == [merit.id, cola.id]
            db.session.expire_all()
            (loaded,) = load_active_salary_profiles(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert [row.id for row in loaded.raises] == [merit.id, cola.id]

    def test_the_walk_compounds_them_in_that_order(self, app, seed_user):
        """02-27 pays $2,101.46, merit then cola; cola then merit is $2,101.45."""
        with app.app_context():
            profile, _merit, _cola = _two_raises_the_first_corrected(seed_user)
            basis = PayrollBasis(profile, calendar_for(profile.user_id))
            assert basis.base_pay_on(date(2026, 2, 27)).per_paycheck == Decimal("2101.46")

    def test_the_banner_names_them_in_that_order(self, app, seed_user):
        """02-13, whose pay period holds 02-02, names the merit first."""
        with app.app_context():
            profile, _merit, _cola = _two_raises_the_first_corrected(seed_user)
            calendar = calendar_for(profile.user_id)
            basis = PayrollBasis(profile, calendar)
            assert basis.pay_event_on(calendar.span_containing(date(2026, 2, 13))) == (
                "MERIT +3.0000%, COLA +2.0000%"
            )
