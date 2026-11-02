"""Show every old calibration's five figures beside its pay stub's, before the drop.

Plan step **salary:S11-d** (card plan#1155, task plan#2584), the rehearsal half
of ruling **R-SAL127** ("Taxes in the update, gross at rehearsal"): "Before the
release, the rehearsal shows you all five, the Gross Pay added up by the same
code the stub page uses, while the old entries still exist."  Migration
``858f569b2c17`` drops ``salary.calibration_overrides``; this is run on a copy
of production at ``69634349bdce``, the revision before it, so the old rows can
still be read.

**What it reads, each through ONE spelling.**  The calibrations are the ones
the drop counts as held, read by the migration's own collector
(``held_calibrations``: the live rows and every state the change log still
keeps of the table, each distinct state once).  Whether a calibration has its
stub, and that stub's four taxes, are the migration's own reads too
(``stub_taxes``, keyed by job and payday), set beside the calibration's taxes
by the migration's own pairing of column and withholding kind (``TAXES``).  A
stub's Gross Pay is never stored, so it is added up here by the stub page's own
rule: :func:`app.services.pay_stub_gross.checked_gross` over
:func:`app.services.pay_stub_service.totals_of` of the saved stub's figures,
under the job's "stub's gross includes untaxed pay" answer.

**What it prints.**  For each held calibration, its Gross Pay and four taxes,
"then", beside its stub's, each flagged ``DIFFERS`` when the two are not the
same amount; a tax the stub does not record reads "none on the stub".  A
calibration with no stub on its date is named so, and the migration's own
refusal follows the list, worded as the upgrade would raise it.

**Exit status**: 0 when every held calibration has its stub, so the drop will
not refuse (a figure that differs is for the developer to read, not a failure);
1 when the drop would refuse.

**It is READ-ONLY**: :func:`main` opens its transaction ``READ ONLY``, so the
database refuses any write the measure might send, and rolls it back on the way
out.  The app's development start (``create_app``'s idempotent schema and
reference-row seed, which ``FLASK_ENV=development`` runs) commits before that
transaction opens.  The suite grades :func:`run` on a seeded database
(``tests/test_models/test_the_old_calibration_table_goes_migration.py``,
``TestTheRehearsalMeasure``), including that it sends only reads.

**Usage** (from the repository root, against a database at ``69634349bdce``)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_s11d_a \\
        PYTHONPATH=. .venv/bin/python tests/manual/measure_s11d_old_calibrations.py
"""

import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Row

from app import create_app, db
from app.models.salary_profile import SalaryProfile
from app.services import pay_stub_service
from app.services.pay_stub_gross import checked_gross
from tests._test_helpers import load_migration_module

_MIGRATION = load_migration_module("858f569b2c17_the_old_calibration_table_goes.py")

#: The label the calibration's and the stub's Gross Pay are printed under.
_GROSS_LABEL = "Gross Pay"


@dataclass(frozen=True)
class Figure:
    """One of a calibration's five figures beside its stub's.

    Attributes:
        label: The figure's name in the printout.
        then: The calibration's amount.
        on_stub: The stub's amount, or ``None`` for a tax the stub does not
            record.
    """

    label: str
    then: Decimal
    on_stub: Decimal | None

    @property
    def differs(self) -> bool:
        """Whether the stub's amount is not the calibration's (a missing tax differs)."""
        return self.on_stub != self.then


def _money(amount: Decimal) -> str:
    """Word an amount as the app does: ``$1,234.56``."""
    return f"${amount:,.2f}"


def _figure_line(figure: Figure) -> str:
    """Word one figure: both amounts, flagged when they differ."""
    on_stub = "none" if figure.on_stub is None else _money(figure.on_stub)
    flag = "  DIFFERS" if figure.differs else ""
    return f"  {figure.label}: {_money(figure.then)} then, {on_stub} on the stub{flag}"


def stub_gross(profile_id: int, payday: date) -> Decimal:
    """Return the Gross Pay a saved stub adds up to, by the stub page's own rule.

    Ruling R-SAL127: :func:`~app.services.pay_stub_gross.checked_gross`, the
    rule the entry door checks a stub's printed gross with, over the saved
    stub's totals and the job's answer to whether its stub's gross holds the
    untaxed pay.

    Args:
        profile_id: The stub's salary profile.
        payday: The stub's payday.

    Returns:
        The Gross Pay.
    """
    profile = db.session.get(SalaryProfile, profile_id)
    stub = pay_stub_service.stub_on(profile, payday)
    totals = pay_stub_service.totals_of(profile, pay_stub_service.figures_of(stub))
    return checked_gross(totals, includes_after_tax=profile.stub_gross_includes_after_tax)


def five_figures(calibration: Row, taxes: dict[str, Decimal]) -> tuple[Figure, ...]:
    """Return a held calibration's five figures beside its stub's.

    Args:
        calibration: One row of the migration's ``held_calibrations``.
        taxes: Its stub's withholdings by kind name, from the migration's
            ``stub_taxes``.

    Returns:
        Gross Pay first, then the four taxes in the migration's order.
    """
    gross = Figure(
        _GROSS_LABEL, calibration.actual_gross_pay,
        stub_gross(calibration.salary_profile_id, calibration.pay_stub_date),
    )
    return (gross, *(
        Figure(label, getattr(calibration, column), taxes.get(kind))
        for column, kind, label in _MIGRATION.TAXES
    ))


def run() -> tuple[list[str], int]:
    """Measure every held calibration against its stub, reading only.

    Returns:
        The printout's lines, and the exit status: 0 when every held
        calibration has its stub, 1 when the drop would refuse.
    """
    bind = db.session.connection()
    held = _MIGRATION.held_calibrations(bind)
    stubs = _MIGRATION.stub_taxes(bind)
    names = _MIGRATION.profile_names(bind)
    lines = [
        f"S11-d rehearsal: {len(held)} old calibration(s) held in "
        "salary.calibration_overrides and the change log it still keeps, each "
        "beside its pay stub; the stub's Gross Pay is added up by the stub "
        "page's rule (ruling R-SAL127).",
    ]
    differing = 0
    for calibration in held:
        which = (
            f"The old calibration of {calibration.pay_stub_date.isoformat()} "
            f"({_MIGRATION.job_words(calibration.salary_profile_id, names)}, "
            f"calibration {calibration.calibration_id})"
        )
        taxes = stubs.get((calibration.salary_profile_id, calibration.pay_stub_date))
        if taxes is None:
            lines.append(f"{which}: no pay stub on its date, so the drop refuses.")
            continue
        figures = five_figures(calibration, taxes)
        if any(figure.differs for figure in figures):
            differing += 1
        lines.append(f"{which}:")
        lines.extend(_figure_line(figure) for figure in figures)
    lines.append(
        f"S11-d rehearsal: {differing} of {len(held)} held calibration(s) differ "
        "from their stub in at least one figure."
    )
    try:
        _MIGRATION.refuse_a_calibration_without_its_stub(held, stubs, names)
    except RuntimeError as refusal:
        lines.append(str(refusal))
        return lines, 1
    lines.append("S11-d rehearsal: every held calibration has its stub; the drop will not refuse.")
    return lines, 0


def main() -> int:
    """Print the measure over ``DATABASE_URL``'s database in a read-only transaction."""
    app = create_app()
    with app.app_context():
        db.session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            lines, status = run()
        finally:
            db.session.rollback()
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
