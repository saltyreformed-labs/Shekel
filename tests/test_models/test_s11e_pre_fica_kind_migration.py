"""Migration ``47beea2b628a`` -- pre-tax splits in two.

Plan step **salary:S11-e** (ruling **plan#2639**, "A new line kind"; ledger row
**SAL-566**): ``pre_fica_deduction`` ("Pre-tax, all payroll taxes") joins
``ref.paycheck_line_kinds``.  Every assertion reads the DATABASE for the rows,
both directions are driven through the migration's own shipped callables, and
its claim about itself -- that the downgrade REFUSES, by name, while a line, a
stub line amount or a one-off carries the kind, and leaves them as it found
them -- is graded with each of the three seeded and shown to name it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.extensions import db
from app.models.pay_stub import PayStubOneOff
from tests._test_helpers import (
    add_test_pay_stub,
    load_migration_module,
    make_flat_paycheck_line,
    make_salary_profile,
    run_migration_callable as _run,
)

_M_S11E = load_migration_module("47beea2b628a_pre_tax_splits_in_two.py")

_BELOW = {"taxable_earning", "pre_tax_deduction", "post_tax_deduction", "after_tax_earning"}

#: A stub's four printed taxes (made-up figures; none is read here).
_TAXES = {
    WithholdingKindEnum.FEDERAL_INCOME: "0.00",
    WithholdingKindEnum.STATE_INCOME: "100.00",
    WithholdingKindEnum.SOCIAL_SECURITY: "150.00",
    WithholdingKindEnum.MEDICARE: "35.00",
}


def _kind_names() -> set[str]:
    """The ref table's row names, read from the database."""
    return {row[0] for row in db.session.execute(text("SELECT name FROM ref.paycheck_line_kinds"))}


def _profile(seed_user):
    """A fresh salary profile, flushed."""
    profile = make_salary_profile(
        seed_user, db.session, name="Day Job", pay=Decimal("3000.00"),
        pay_from=date(2026, 1, 2),
    )
    db.session.flush()
    return profile


class TestTheKindAndItsRoundTrip:
    """Head carries five kinds; down leaves four; up restores five; the enum matches."""

    def test_head_carries_the_five_kinds_and_the_enum_names_them(self, app, db):
        """The rows and the enum spell the same five names, the new one among them."""
        with app.app_context():
            assert _kind_names() == _BELOW | {_M_S11E.KIND}
            assert {member.value for member in PaycheckLineKindEnum} == _kind_names()
            assert _M_S11E.KIND == PaycheckLineKindEnum.PRE_FICA_DEDUCTION.value

    def test_down_then_up_restores_the_row_and_the_upgrade_is_idempotent(self, app, db):
        """With nothing of the kind the downgrade deletes exactly its row; up seeds it once."""
        with app.app_context():
            before = dict(db.session.execute(text(
                "SELECT name, id FROM ref.paycheck_line_kinds"
            )).all())

            _run(_M_S11E.downgrade, db.session)
            assert _kind_names() == _BELOW

            _run(_M_S11E.upgrade, db.session)
            assert _kind_names() == _BELOW | {_M_S11E.KIND}
            after = dict(db.session.execute(text(
                "SELECT name, id FROM ref.paycheck_line_kinds"
            )).all())
            # The four rows below kept their ids (nothing touched them).
            for name in _BELOW:
                assert after[name] == before[name]

            # A second upgrade seeds nothing twice.
            _run(_M_S11E.upgrade, db.session)
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.paycheck_line_kinds"
            )).scalar() == 5


class TestTheDowngradeRefusesWhileTheKindIsHeld:
    """Each of the three rows that names a kind stops the downgrade, named, untouched."""

    def _refusal(self) -> str:
        """Run the downgrade, expecting it to refuse; return its message, rows as found."""
        with pytest.raises(RuntimeError) as excinfo:
            _run(_M_S11E.downgrade, db.session)
        db.session.rollback()
        assert _kind_names() == _BELOW | {_M_S11E.KIND}
        return str(excinfo.value)

    def test_a_payroll_line_of_the_kind_is_named(self, app, db, seed_user):
        """A Health line re-picked to the kind stops it, and the refusal names the line."""
        with app.app_context():
            health = make_flat_paycheck_line(
                _profile(seed_user), "Health", "200.00",
                PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
            )
            db.session.commit()
            message = self._refusal()
            assert f"payroll line {health.id} ('Health')" in message
            assert "move every paycheck they price" in message
            db.session.expire_all()
            assert health.paycheck_line_kind_id == ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
            )

    def test_a_stub_line_amount_of_the_kind_is_named(self, app, db, seed_user):
        """A stub that records Health under the kind stops it, though the line is income-tax only."""
        with app.app_context():
            profile = _profile(seed_user)
            health = make_flat_paycheck_line(
                profile, "Health", "200.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
            )
            db.session.flush()
            stub = add_test_pay_stub(
                profile, date(2026, 1, 16), "3000.00", _TAXES,
                lines=((health, "200.00", PaycheckLineKindEnum.PRE_FICA_DEDUCTION),),
            )
            db.session.commit()
            amount_id = stub.line_amounts[0].id
            message = self._refusal()
            assert f"pay stub line amount {amount_id} ('2026-01-16 Health')" in message

    def test_a_stub_one_off_of_the_kind_is_named(self, app, db, seed_user):
        """A one-off recorded under the kind stops it, named by its stub's payday and its name."""
        with app.app_context():
            profile = _profile(seed_user)
            stub = add_test_pay_stub(profile, date(2026, 1, 16), "3000.00", _TAXES)
            one_off = PayStubOneOff(
                pay_stub_id=stub.id, name="Dental catch-up", amount=Decimal("40.00"),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
                ),
            )
            db.session.add(one_off)
            db.session.commit()
            message = self._refusal()
            assert f"pay stub one-off {one_off.id} ('2026-01-16 Dental catch-up')" in message
