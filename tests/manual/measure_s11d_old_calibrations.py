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
under the job's "stub's gross includes untaxed pay" answer.  So one stub is
found twice, both times by job and payday (:func:`stub_gross` says why); were
the two reads ever to disagree, the measure stops with an error naming the
stub rather than printing a figure.

**What it prints.**  For each held calibration, its Gross Pay and four taxes,
"then", beside its stub's, each flagged ``DIFFERS`` when the two are not the
same amount; a tax the stub does not record reads "none on the stub".  A
calibration with no stub on its date is named so, and the migration's own
refusal follows the list, worded as the upgrade would raise it.

**Exit status**: :data:`EXIT_CLEAR` (0) when every held calibration has its
stub, so the drop will not refuse (a figure that differs is for the developer
to read, not a failure); :data:`EXIT_REFUSES` (3) when the drop would refuse.
Any other status is a broken measure: Python exits 1 on an uncaught exception
and 2 when it cannot open the script, so neither reads as a refusal.

**It is READ-ONLY under production's configuration, as the release kit runs
it** (``FLASK_ENV=production``, in the side environment
``tools/rehearsal/rows.py`` builds): ``create_app`` writes nothing on that
start, and :func:`main` opens its transaction ``READ ONLY``, so the database
refuses any write the measure might send, and rolls it back on the way out.
**Under the development configuration it is not**: with ``FLASK_ENV`` unset or
``development``, as the usage below has it, ``create_app`` first commits its
idempotent schema and reference-row seed before that transaction opens:
``_ensure_schemas``, a ``CREATE SCHEMA IF NOT EXISTS`` per schema, and
``_seed_ref_tables`` (``app.ref_seeds.seed_reference_data``), which inserts
each missing reference row and brings each existing ``ref.account_types``
row's metadata columns to the seed's values.  The suite grades :func:`run` on
a seeded database
(``tests/test_models/test_the_old_calibration_table_goes_migration.py``,
``TestTheRehearsalMeasure``), including that it sends only reads.

**It loads no package only the development set holds**: the release kit runs
it under a venv of the cut's lock, the runtime set and the ``rehearsal``
group's ``time-machine`` (``tools/rehearsal/bench.py``), and production's image
holds the runtime set and never the ``dev`` group.  So it loads the migration
by path itself rather than through ``tests._test_helpers.load_migration_module``,
whose module imports ``pytest``.  The suite grades this by loading the measure
by path in a fresh interpreter and finding no distribution it loaded among
those ``pyproject.toml``'s ``dev`` group names and its ``[project]
dependencies`` do not; a distribution neither names (a dependency's own
dependency) is not graded.

**Usage** (from the repository root, against a database at ``69634349bdce``;
the development configuration, so the seed above runs first)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_s11d_a \\
        PYTHONPATH=. .venv/bin/python tests/manual/measure_s11d_old_calibrations.py
"""

import importlib.util
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Row

from app import create_app, db
from app.models.salary_profile import SalaryProfile
from app.services import pay_stub_service
from app.services.pay_stub_gross import checked_gross

#: The drop's migration, whose collector, stub reads, tax pairing, money words
#: and refusal this reads.  ``migrations/versions`` is no package, so it is
#: loaded by path.
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions"
    / "858f569b2c17_the_old_calibration_table_goes.py"
)
_SPEC = importlib.util.spec_from_file_location(_MIGRATION_PATH.stem, _MIGRATION_PATH)
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)

#: The label the calibration's and the stub's Gross Pay are printed under.
_GROSS_LABEL = "Gross Pay"

#: The exit status when every held calibration has its stub: the drop will not refuse.
EXIT_CLEAR = 0
#: The exit status when the drop would refuse: its own, so neither of Python's
#: (1 on an uncaught exception, 2 when it cannot open the script) reads as one.
EXIT_REFUSES = 3


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


def _figure_line(figure: Figure) -> str:
    """Word one figure: both amounts, in the migration's money words, flagged when they differ."""
    on_stub = "none" if figure.on_stub is None else _MIGRATION.money(figure.on_stub)
    flag = "  DIFFERS" if figure.differs else ""
    return (
        f"  {figure.label}: {_MIGRATION.money(figure.then)} then, "
        f"{on_stub} on the stub{flag}"
    )


def stub_gross(profile_id: int, payday: date) -> Decimal:
    """Return the Gross Pay a saved stub adds up to, by the stub page's own rule.

    Ruling R-SAL127: :func:`~app.services.pay_stub_gross.checked_gross`, the
    rule the entry door checks a stub's printed gross with, over the saved
    stub's totals and the job's answer to whether its stub's gross holds the
    untaxed pay.

    Called only for a stub the migration's ``stub_taxes`` found.  The stub is
    found again here, by the same job and payday, because each half has its
    own owner: whether it exists, and its taxes, are the drop's own SQL read
    (a migration loads no app module), and its Gross Pay is the stub page's
    rule, which adds up the app's model of the stub.  The two reads agree on
    any database; one finding a stub the other does not is a defect in one of
    them, so it raises rather than adding up nothing.

    Args:
        profile_id: The stub's salary profile.
        payday: The stub's payday.

    Returns:
        The Gross Pay.

    Raises:
        RuntimeError: The app's read finds no job or no stub where the
            migration's found the stub.
    """
    profile = db.session.get(SalaryProfile, profile_id)
    stub = None if profile is None else pay_stub_service.stub_on(profile, payday)
    if stub is None:
        raise RuntimeError(
            f"The migration's stub_taxes found a pay stub on {payday.isoformat()} for "
            f"salary profile {profile_id}, and the app's pay_stub_service.stub_on finds "
            "none: the two reads of one stub disagree, so its Gross Pay cannot be added up."
        )
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
        The printout's lines, and the exit status: :data:`EXIT_CLEAR` when
        every held calibration has its stub, :data:`EXIT_REFUSES` when the
        drop would refuse.
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
        return lines, EXIT_REFUSES
    lines.append("S11-d rehearsal: every held calibration has its stub; the drop will not refuse.")
    return lines, EXIT_CLEAR


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
