"""
Shekel Budget App -- The Tax Law

**The ONE copy of the tax law the app prices against** (ruling
**salary:R-SAL74**, plan step **salary:X-at-1**): federal income tax per filing
status, Social Security and Medicare, and each supported state's income tax,
one module per tax year, each naming the documents it was transcribed from.

It replaced a copy PER USER.  Signup copied the seed constants into five
``salary`` tables, every deploy copied any rows a user lacked, and the Settings
page could overwrite a user's state and FICA rows -- so the law lived once in
the code and once per user in the database, kept equal by nothing.  (On
production, 2026-09-24, both users' 2025 and 2026 copies of the bracket sets
and their ladders, the child deduction tiers and the FICA rows equalled the
code field for field, and the state rows' tax type did; the state rate and
deduction were graded by re-pricing instead: every projected paycheck and the
Taxes tab, whose figures read them, priced from the code as they did from the
copies, and a planted North Carolina rate moved the result.)  A copy could not
reach a year its owner never received, and a correction to the code never
reached a row already copied.  Now nothing in the app writes the law: a new year, or a
correction, is a release that edits these modules.  The copies' five tables went
at plan step salary:X-at-2.

**Adding a tax year** is a new ``_year_<YYYY>.py`` beside the others, listed
in :data:`LAW` below.  Its federal rules must cover every filing status and it
must cite its sources, or importing it fails (:mod:`app.tax_law._types`); the
bracket ladders are checked for gaps, and so are the years (none skipped).
Which year's law prices a given year is the resolver's rule,
:func:`app.services.tax_config_service.resolve_tax_year`, not this package's.

**The states the law lists are the states the app supports** (ruling
salary:R-SAL78, plan step salary:X-at-3): the salary profile form offers only
those, both profile doors refuse another, and the paycheck engine refuses a
profile in another rather than pricing its state tax at $0.00.  It lists North
Carolina alone (ruling salary:R-SAL128).  **Adding a state** lists it from the
law's first year, or importing fails (ruling salary:R-SAL129), and in every
later year, or the tax-law alarms name it; its year modules cite where its
figures come from.  A state with no income tax is an explicit entry of tax
type ``NONE`` with no rate and nothing to deduct, which prices $0.00.

**Forgetting to add one is loud** (rulings salary:R-SAL74, R-SAL86, R-SAL87):
from November 1 every owner page shows a banner and a weekly GitHub run fails
until next year is here, and from December 1 CI refuses every pull request and
no release image is built (salary:R-SAL88).
"Here" means the year, listing every state an earlier year lists -- a year may
ship federal first and a state later, and the alarms name the state until it
lands.  The one check behind every alarm is :mod:`app.services.tax_law_alarm`;
``python scripts/check_tax_law.py notice`` runs it by hand.
"""

from app.tax_law._types import (
    Bracket,
    ChildDeductionTier,
    FederalRules,
    FicaRules,
    StateYearLaw,
    TaxLaw,
    TaxYearLaw,
    ladder,
    name_states,
)
from app.tax_law._year_2025 import YEAR_2025
from app.tax_law._year_2026 import YEAR_2026
from app.tax_law._year_2027 import YEAR_2027

#: Every tax year the app carries, oldest first.
LAW = TaxLaw(years=(YEAR_2025, YEAR_2026, YEAR_2027))

__all__ = [
    "LAW",
    "Bracket",
    "ChildDeductionTier",
    "FederalRules",
    "FicaRules",
    "StateYearLaw",
    "TaxLaw",
    "TaxYearLaw",
    "ladder",
    "name_states",
]
