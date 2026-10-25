"""Price every saved paycheck through the app's one producer and dump the whole breakdown.

Plan step **salary:R18-a** (shipped at ``ef0dc831``;
ruling **R-SAL38**): the RENAME of ``salary.paycheck_deductions``
to ``salary.paycheck_lines`` claims to move no figure.  This harness is how
that claim is graded rather than quoted: run it on the tree BEFORE the rename
against a database at the prior migration head, upgrade the database, run it
on the tree AFTER, and ``diff`` the two files.  Byte-identical output over
every saved paycheck of every active profile is the whole claim; one moved
cent anywhere in a breakdown refutes it.

**It compiles on both sides** because it names nothing the rename touches:
no line model, no kind enum, no ref accessor.  It reaches the engine through
:func:`app.services.income_service.paycheck_pricing` -- the ONE producer of a
profile's projection (ledger row **N-443**), the door every page prices
through -- over :meth:`app.services.pay_calendar.PayCalendar.saved`, so what
is compared is what the app shows and not a re-statement of it.  Every field
of every :class:`~app.services.paycheck_calculator.PaycheckBreakdown` is
written: the period identity, the earnings figures (base pay, gross,
taxable income, net -- ``base_biweekly`` and the two earning-line lists since
plan step salary:R18-b, so a run on a tree before that leaf lacks those keys
and the diff is read for FIGURES), the four withholding lines, and each
line's name, amount and target account, in the engine's own order.

**It is READ-ONLY**: nothing is assigned, nothing is committed, and the
session is rolled back on the way out.

**Usage** (from the repository root, on EACH tree)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_r18a_probe \\
        PYTHONPATH=. /home/josh/projects/Shekel/.venv/bin/python \\
        tests/manual/measure_paycheck_lines_rename.py --json before.json

    # flask db upgrade on the AFTER tree, then the same command there:
    #   ... --json after.json
    diff before.json after.json && echo BYTE-IDENTICAL
"""

import argparse
import json
import logging
import sys

from app import create_app, db
from app.models.salary_profile import SalaryProfile
from app.services import income_service
from app.services.pay_calendar import calendar_for


def _line(line) -> dict:
    """One deduction line as plain data, in the engine's field order."""
    return {
        "name": line.name,
        "amount": str(line.amount),
        "target_account_id": line.target_account_id,
    }


def _breakdown(breakdown) -> dict:
    """One priced paycheck as plain data, every field the engine returns."""
    return {
        "payday": breakdown.period.payday.isoformat(),
        "period_id": breakdown.period.period_id,
        "is_third_paycheck": breakdown.period.is_third_paycheck,
        "raise_event": breakdown.period.raise_event,
        "annual_salary": str(breakdown.earnings.annual_salary),
        "base_biweekly": str(breakdown.earnings.base_biweekly),
        "gross_biweekly": str(breakdown.earnings.gross_biweekly),
        "taxable_income": str(breakdown.earnings.taxable_income),
        "net_pay": str(breakdown.earnings.net_pay),
        "taxable_lines": [_line(line) for line in breakdown.earnings.taxable],
        "after_tax_lines": [_line(line) for line in breakdown.earnings.after_tax],
        "federal": str(breakdown.taxes.federal),
        "state": str(breakdown.taxes.state),
        "social_security": str(breakdown.taxes.social_security),
        "medicare": str(breakdown.taxes.medicare),
        "pre_tax": [_line(line) for line in breakdown.deductions.pre_tax],
        "post_tax": [_line(line) for line in breakdown.deductions.post_tax],
    }


def _profile_record(profile) -> dict:
    """Every saved paycheck of *profile*, priced through the app's producer."""
    calendar = calendar_for(profile.user_id)
    periods = calendar.saved()
    priced = income_service.paycheck_pricing(
        calendar.user_id, lambda: calendar,
    ).for_profile(profile).over(periods)
    return {
        "profile_id": profile.id,
        "user_id": profile.user_id,
        "profile_name": profile.name,
        "paychecks": [_breakdown(breakdown) for breakdown in priced],
    }


def main(argv=None) -> int:
    """Write every active profile's saved paychecks to ``--json``."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--json", required=True, help="where to write the record")
    args = parser.parse_args(argv)

    # The tax calculator logs six DEBUG lines per paycheck; the file is the
    # output here.
    logging.disable(logging.INFO)

    app = create_app()
    with app.app_context():
        try:
            records = [
                _profile_record(profile)
                for profile in db.session.query(SalaryProfile)
                .filter(SalaryProfile.is_active.is_(True))
                .order_by(SalaryProfile.id)
            ]
        finally:
            db.session.rollback()
    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=1, sort_keys=True)
    paychecks = sum(len(record["paychecks"]) for record in records)
    print(f"wrote {len(records)} profile(s), {paychecks} paycheck(s) to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
