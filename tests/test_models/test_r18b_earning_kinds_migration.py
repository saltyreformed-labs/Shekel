"""Migration ``6c15d2a97b78`` -- a paycheck has earning lines.

Plan step **salary:R18-b** (ruling **R-SAL38**; ledger row **D59**): the two
EARNING kinds join ``ref.paycheck_line_kinds``.  Every assertion reads the
DATABASE for the rows, both directions are driven through the migration's
own shipped callables, and its claim about itself -- that the downgrade
REFUSES, by name, while any line carries an earning kind -- is graded with
a seeded line and shown to name it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.enums import PaycheckLineKindEnum
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, PaycheckLineKind
from tests._test_helpers import (
    load_migration_module,
    make_salary_profile,
    run_migration_callable as _run,
)

_M_R18B = load_migration_module("6c15d2a97b78_a_paycheck_has_earning_lines.py")

_DEDUCTION_KINDS = {"pre_tax_deduction", "post_tax_deduction"}
_EARNING_KINDS = {"taxable_earning", "after_tax_earning"}
#: The kinds a LATER revision adds, which this one neither seeds nor deletes:
#: ``pre_fica_deduction`` (``47beea2b628a``, plan step salary:S11-e).  Head
#: carries them, so every head-state assertion here names them too.
_LATER_KINDS = {"pre_fica_deduction"}


def _kind_names() -> set[str]:
    """The ref table's row names, read from the database."""
    return {row[0] for row in db.session.execute(text("SELECT name FROM ref.paycheck_line_kinds"))}


def _seed_line(seed_user, kind_name, name="Phone Allowance", amount="45.00") -> int:
    """One flat line of *kind_name* on a fresh profile; returns its id."""
    profile = make_salary_profile(seed_user, db.session)
    db.session.flush()
    kind = db.session.query(PaycheckLineKind).filter_by(name=kind_name).one()
    method = db.session.query(CalcMethod).filter_by(name="flat").one()
    line = PaycheckLine(
        salary_profile=profile, paycheck_line_kind_id=kind.id,
        calc_method_id=method.id, name=name, amount=Decimal(amount),
    )
    db.session.add(line)
    db.session.commit()
    return line.id


@pytest.mark.xdist_group("recurrence_rules_ddl")
class TestTheKindsAndTheirRoundTrip:
    """Head carries this revision's kinds; down removes its two; up restores them; the enum matches."""

    def test_head_carries_the_four_kinds_and_the_enum_names_them(self, app, db):
        """The rows and the enum spell the same names: this revision's four and the later ones."""
        with app.app_context():
            assert _kind_names() == _DEDUCTION_KINDS | _EARNING_KINDS | _LATER_KINDS
            assert {member.value for member in PaycheckLineKindEnum} == _kind_names()
            assert set(_M_R18B.EARNING_KINDS) == _EARNING_KINDS

    def test_down_then_up_restores_the_rows_and_the_upgrade_is_idempotent(self, app, db):
        """With no earning line the downgrade deletes exactly the two rows; up seeds them once."""
        with app.app_context():
            before = dict(db.session.execute(text(
                "SELECT name, id FROM ref.paycheck_line_kinds"
            )).all())

            _run(_M_R18B.downgrade, db.session)
            assert _kind_names() == _DEDUCTION_KINDS | _LATER_KINDS

            _run(_M_R18B.upgrade, db.session)
            assert _kind_names() == _DEDUCTION_KINDS | _EARNING_KINDS | _LATER_KINDS
            # The deduction rows kept their ids (nothing touched them).
            after = dict(db.session.execute(text(
                "SELECT name, id FROM ref.paycheck_line_kinds"
            )).all())
            for name in _DEDUCTION_KINDS:
                assert after[name] == before[name]

            # A second upgrade seeds nothing twice.
            _run(_M_R18B.upgrade, db.session)
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.paycheck_line_kinds"
            )).scalar() == 4 + len(_LATER_KINDS)

    @pytest.mark.parametrize("kind_name", sorted(_EARNING_KINDS))
    def test_the_downgrade_refuses_by_name_while_a_line_carries_an_earning_kind(
        self, app, db, seed_user, kind_name,
    ):
        """A seeded earning line stops the downgrade, and the refusal names it.

        The migration's own claim about itself: the FK would refuse the
        delete anyway, but silently; the shipped downgrade says WHICH line
        and WHY before touching a row, and leaves everything as it found it.
        """
        with app.app_context():
            line_id = _seed_line(seed_user, kind_name)

            with pytest.raises(RuntimeError) as excinfo:
                _run(_M_R18B.downgrade, db.session)
            db.session.rollback()

            message = str(excinfo.value)
            assert f"line {line_id} ('Phone Allowance', {kind_name})" in message
            assert "moves the paycheck" in message or "move the paycheck" in message
            # Refused means untouched.
            assert _kind_names() == _DEDUCTION_KINDS | _EARNING_KINDS | _LATER_KINDS
            db.session.expire_all()
            assert db.session.get(PaycheckLine, line_id).paycheck_line_kind.name == kind_name
