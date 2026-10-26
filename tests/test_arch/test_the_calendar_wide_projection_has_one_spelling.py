"""Architecture test: the CALENDAR-WIDE paycheck projection has ONE spelling.

Plan step **salary:R14-a**, closing ledger row **N-443**.  Running the paycheck
engine over an owner's WHOLE saved calendar, with tax configs resolved per
period year, was written out longhand in three places --
``income_service.SalaryPricing``, ``routes/salary/views.py`` and
``routes/salary/cockpit.py`` -- each pairing
``tax_config_service.load_tax_configs_for_periods`` (the door plan step
salary:S3-d replaced with :func:`~app.services.tax_config_service
.configs_by_year`) with
:func:`~app.services.paycheck_calculator.project_salary` over the same
``calendar.saved()``.  Ruling **R-IZ**: a second walk is a cache with no
column, agreement is not the test, and where a layer puts the shared leaf out
of reach the remedy is to MOVE THE LEAF.  The leaf is
:class:`app.services.income_service.ProfilePaychecks`.

What this test enforces
-----------------------

For every ``.py`` file under ``app/``: exactly ONE ``ast.Call`` to
``project_salary``, whatever its keywords, and it is in
``app/services/income_service.py``.  **Every call counts since plan step
salary:S11-c-2c**; until then only a call passing ``configs_by_year`` did,
for the reason the next section gives and the step made untrue.

Why it read one keyword, and why it no longer does
--------------------------------------------------

``project_salary`` takes exactly one of two tax-config sources, and they are
two different questions rather than two spellings of one:

* ``configs_by_year=`` -- a ``{tax_year: configs}`` mapping, which is what a
  MULTI-YEAR horizon over the owner's whole calendar needs.  This is N-443's
  subject and the thing that must have one spelling.
* ``tax_configs=`` -- ONE config set for ONE tax year, correct over a year
  SLICE.  ``tax_withholding_service`` priced a year's remainder that way and
  ``tax_report_service`` summed one tax year's pre-tax total that way.

Both of those were checked by hand when this test was written (2026-09-03) and
neither was a calendar-wide walk, so asserting "one caller of ``project_salary``"
would have been a FALSE rule that fired on two correct sites.  A census is only
as good as its predicate, and this one is the narrow true predicate rather than
the wide convenient one.

*Both left at plan step salary:S11-c-2c*, when a pay stub began pricing a
paycheck on its OWN payday's tax year (ruling **R-SAL77**): a year slice can
need two years' law, which is the per-year resolution this test gives one
spelling, so both now ask the read pass's pricer.  No ``app/`` site passes
``tax_configs=`` today, so the wide predicate it argued against became TRUE,
and that step took it: a single-year caller -- the one-payday
``project_salary(basis, [period], tax_configs)`` that was ledger row
**P62**'s shape -- now fails this census as a second spelling does.

Why AST, not grep
-----------------

``project_salary`` appears in prose -- docstrings and ``#`` comments -- in
ten ``app/`` modules after this change (eleven before it, cockpit.py having
dropped its mention), so a text search answers overwhelmingly with the
documentation rather than with the callers.  Walking ``ast.Call`` sees only
invocation, which a grep cannot separate from prose at all.  (Until plan
step salary:S11-c-2c it also read the call's KEYWORDS, to separate the two
modes above.)

What this census CANNOT see
---------------------------

Stated because an unstated limit reads as no limit.
:func:`test_the_blind_spots_are_the_ones_named` pins the FIRST TWO of the
three below, which are call shapes it can parse; the third is a different
code shape entirely and no assertion over this scanner can pin it.  Each of
the two was measured against this scanner.  (A ``**`` unpacking --
``project_salary(b, p, **kw)`` -- was a fourth, invisible to the keyword
test, until plan step salary:S11-c-2c dropped the keyword test; the same
test now pins that the scanner SEES it.)

* ``f = paycheck_calculator.project_salary`` then ``f(...)`` -- an assignment
  alias, where :func:`_local_names` reads only ``ImportFrom``.
* ``getattr(pc, "project_salary")(...)``.
* A hand-rolled tax-config resolution plus a per-period
  :func:`~app.services.paycheck_calculator.calculate_paycheck` loop calls
  ``project_salary`` nowhere, so THIS census cannot see it by construction.
  It was named here as unpinnable until plan step **salary:S3-d**;
  :func:`test_no_app_site_prices_a_paycheck_outside_the_pricer` is the second
  census that pins it, over ``calculate_paycheck`` itself.

**The second census has the SAME first two blind spots**, because it uses the
same matcher: an assignment alias, a ``getattr`` form and a name bound by
anything other than ``ImportFrom`` are all invisible to it exactly as they are
here.
:func:`test_the_per_period_blind_spots_are_the_ones_named` pins them, so the
statement above is executable for both censuses rather than for one.

The first two appear nowhere in ``app/`` today.  They are the shapes a
reviewer must still catch by eye; this test is a floor, not a ceiling.  The census also
reads only ``app/`` -- ``scripts/`` and ``tools/`` are clean, and the
legitimate calls in ``tests/`` are deliberately out of scope: 24 in eight files
(an AST census, 2026-10-22), 15 of them in the
``tests/test_services/test_paycheck_calculator/`` package
(``test_fica_and_the_year.py``, ``test_the_gross.py``).

The negative case
-----------------

:func:`test_the_scanner_fires_on_a_planted_second_spelling` plants the
violation as source and asserts the scanner finds it -- the calendar-wide
form and, since plan step salary:S11-c-2c, the year-slice form too, which it
planted until then to prove the scanner did NOT count it.  A census that
returns "no violations" is indistinguishable from a census that looked in the
wrong place; this repo has measured that failure many separate ways, so the
passing claim is worth exactly what the firing proof is.
"""

import ast
from pathlib import Path


#: The engine entry every ``app/`` use of which is being constrained: since
#: plan step salary:S11-c-2c, a call of any keywords (the module docstring).
_PROJECTION = "project_salary"

#: The ONE module allowed to spell it, relative to the repo root.
_THE_LEAF = "app/services/income_service.py"

#: Where the engine itself defines the function: the PACKAGE since plan step
#: salary:C12 split the one module into leaves (ledger row P64).  Its ``def``
#: is not a call, so it never enters the first census -- named here so a
#: reader is not left wondering whether the scanner is simply blind to it --
#: and the whole package is excluded from the second, because ``_pricing
#: .project_salary`` calls ``calculate_paycheck`` in the loop that IS the
#: batch entry (see :func:`_per_period_census`).
_THE_ENGINE = "app/services/paycheck_calculator"


def _repo_root() -> Path:
    """Return the repository root, from this file's known depth."""
    return Path(__file__).resolve().parents[2]


def _local_names(tree: ast.AST) -> set[str]:
    """Return every local name in *tree* bound to the engine's projection.

    ``project_salary`` itself, plus whatever an ``import ... as`` bound it to.
    Resolved because the ALIAS is what appears at the call site: a census that
    matched the imported name alone would be blind to exactly the module that
    renamed it, and "no violations" would then be indistinguishable from "did
    not look".

    Args:
        tree: The parsed module.

    Returns:
        The set of names that call the projection in this module.
    """
    names = {_PROJECTION}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _PROJECTION and alias.asname:
                    names.add(alias.asname)
    return names


def _projection_calls(tree: ast.AST) -> int:
    """Return how many ``project_salary`` calls *tree* holds.

    A call counts when its callee resolves to the projection -- a bare name
    (including an ``import ... as`` alias, see :func:`_local_names`) or any
    attribute access ending in ``project_salary``, which is the
    ``paycheck_calculator.project_salary(...)`` form -- whatever keywords it
    passes.  It was ``_calendar_wide_calls`` and counted only a call passing
    ``configs_by_year`` until plan step salary:S11-c-2c (the module
    docstring).

    Args:
        tree: The parsed module.

    Returns:
        The number of matching calls.
    """
    names = _local_names(tree)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            matches = func.attr == _PROJECTION
        elif isinstance(func, ast.Name):
            matches = func.id in names
        else:
            matches = False
        found += matches
    return found


def _census(root: Path) -> dict[str, int]:
    """Return ``{relative path: count}`` for every app module with a match."""
    app_dir = root / "app"
    counts: dict[str, int] = {}
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _projection_calls(tree)
        if hits:
            counts[str(path.relative_to(root))] = hits
    return counts


def test_the_calendar_wide_projection_is_spelled_once():
    """Only ``income_service`` runs the engine's projection, over any span.

    The rule N-443 exists to make structural, widened at plan step
    salary:S11-c-2c to every call (the module docstring).  Asserted as the
    WHOLE census rather than as "the other sites are clean", so a second
    spelling appearing in a module nobody thought to name fails this test too.
    """
    census = _census(_repo_root())
    assert census == {_THE_LEAF: 1}, (
        "The paycheck projection must be called exactly once in app/, "
        f"in {_THE_LEAF} (ProfilePaychecks.over). Census: {census}. "
        "A new entry here is ledger row N-443 recurring -- or P62's one-payday "
        "shape: route it through the read pass's pricer, "
        "ctx.paychecks().for_profile(profile), instead of pairing "
        "a tax-config resolution with project_salary again."
    )


def test_the_engine_module_defines_it_without_calling_it():
    """The engine's own package is not a hidden member of the census.

    ``project_salary`` is DEFINED in ``paycheck_calculator._pricing``; a
    ``def`` is not an ``ast.Call``, so no leaf of the package contributes
    anything above.  Stated as a test rather than a comment because "the
    scanner does not see the engine" and "the engine does not call it" look
    identical from the census alone.  Every leaf is read, and the package is
    asserted to HAVE leaves, so a split that left the definition somewhere
    this walk does not reach cannot pass as "clean".
    """
    leaves = sorted((_repo_root() / _THE_ENGINE).glob("*.py"))
    assert leaves, f"{_THE_ENGINE} holds no modules to read"
    for leaf in leaves:
        tree = ast.parse(leaf.read_text(encoding="utf-8"), filename=str(leaf))
        assert _projection_calls(tree) == 0, leaf


def test_the_scanner_fires_on_a_planted_second_spelling():
    """The census FINDS a planted calendar-wide call, and a planted year-slice one.

    Two plants, one per mode, because the predicate dropped its keyword arm
    at plan step salary:S11-c-2c and each mode is a call it must now count:
    the year-slice plant is the MUTATION that proves the widening, since it
    read ``0`` here until then -- the arm ACCEPTED ``configs_by_year`` and
    REJECTED ``tax_configs``, for the two year-slice callers this census was
    written beside (both on the pass's pricer since that step).
    """
    calendar_wide = ast.parse(
        "paycheck_calculator.project_salary(\n"
        "    PayrollBasis(profile, calendar), periods,\n"
        "    configs_by_year=configs,\n"
        ")\n"
    )
    assert _projection_calls(calendar_wide) == 1

    year_slice = ast.parse(
        "paycheck_calculator.project_salary(\n"
        "    basis, remainder, tax_configs,\n"
        ")\n"
    )
    assert _projection_calls(year_slice) == 1


def test_the_scanner_sees_an_aliased_import():
    """An ``import ... as`` alias cannot hide a second spelling.

    :func:`_local_names` resolves the binding, so a module that renames the
    engine's entry is still counted -- the evasion an identifier-exact grep
    misses, and the one a name-only AST census misses too.  Both the aliased
    and the direct import are asserted, because a scanner that had simply
    stopped filtering on the name would also pass the aliased case.
    """
    aliased = ast.parse(
        "from app.services.paycheck_calculator import project_salary as ps\n"
        "ps(basis, periods, configs_by_year=configs)\n"
    )
    assert _projection_calls(aliased) == 1

    direct = ast.parse(
        "from app.services.paycheck_calculator import project_salary\n"
        "project_salary(basis, periods, configs_by_year=configs)\n"
    )
    assert _projection_calls(direct) == 1

    # A bare name that no import bound to the engine is NOT counted, where an
    # ATTRIBUTE ending in the name is -- the two arms are deliberately
    # asymmetric, so this pins each rather than leaving a reader to infer the
    # direction of the scanner's imprecision.
    unbound_bare_name = ast.parse(
        "ps(basis, periods, configs_by_year=configs)\n"
    )
    assert _projection_calls(unbound_bare_name) == 0

    any_attribute = ast.parse(
        "anything.project_salary(basis, periods, configs_by_year=configs)\n"
    )
    assert _projection_calls(any_attribute) == 1


def test_the_blind_spots_are_the_ones_named():
    """Each documented blind spot really is blind, and no other one is claimed.

    The module docstring lists two call shapes this census cannot see.  A
    list like that is worth nothing unless it is executable: an unstated limit
    reads as no limit, and a STATED limit that has quietly been fixed sends
    the next reviewer hunting for a hole that is not there.  Both directions
    fail here -- and the two ``**`` forms it listed until plan step
    salary:S11-c-2c, which the keyword arm could not read, are asserted SEEN.
    """
    for unpacked in (
        "project_salary(b, p, **{'configs_by_year': c})\n",
        "project_salary(b, p, **kw)\n",
    ):
        assert _projection_calls(ast.parse(unpacked)) == 1, unpacked
    blind = {
        "assignment_alias":
            "f = paycheck_calculator.project_salary\n"
            "f(b, p, configs_by_year=c)\n",
        "getattr_form":
            "getattr(pc, 'project_salary')(b, p, configs_by_year=c)\n",
    }
    for label, source in blind.items():
        assert _projection_calls(ast.parse(source)) == 0, (
            f"{label} is no longer a blind spot -- the scanner now sees it. "
            "That is an improvement, but the module docstring still lists it "
            "as unseen: delete that entry."
        )


#: The engine's PER-PERIOD entry.  The second census below is over this name,
#: where the first is over ``project_salary``: a hand-rolled
#: tax-config resolution plus a loop of these calls the first
#: census's subject nowhere, which is the blind spot the module docstring
#: named as unpinnable until plan step **salary:S3-d**.
_PER_PERIOD = "calculate_paycheck"

#: Every ``app/`` site that prices a paycheck by calling the engine's
#: per-period entry directly, as ``{relative path: call count}`` -- and it is
#: EMPTY, which is the rule this census now keeps.
#:
#: **It held six sites when plan step salary:S3-d wrote it, and every one has
#: left through a leaf that said why.**  ``retirement_dashboard_service`` at
#: S3-f-2a (ruling **R-SAL21** as amended 2026-09-12: its door priced the
#: current paycheck without the calibration while the payroll feed on the
#: same page priced the same payday with it, ``$31.29`` apart on one paycheck
#: and ``+$41,562.00`` on the required savings; its own money leaf).  FOUR at
#: C12-a, each byte-identical through the pass's pricer:
#: ``salary_regeneration`` and ``cockpit.anatomy`` already passed
#: ``calibration=profile.calibration``, ``_helpers._compute_total_pre_tax``
#: reads a deductions total a calibration never reaches (``$713.29`` by both
#: doors), and ``profiles.create_profile`` prices a profile flushed in the
#: same request with no calibration row.  The LAST, ``/savings``'
#: ``_metrics._get_current_paycheck_breakdown``, at C12-b (ruling
#: **R-SAL25**): it passed no calibration either, and it was the one that
#: MOVED a figure -- net ``$2,541.49`` by its door against ``$2,572.78`` by
#: the pricer on the developer's 2026-09-10 paycheck, the Emergency Fund
#: target ``$16,519.69 -> $16,723.07`` -- so it shipped as its own money leaf.
#: Ledger row **P62** closed with it.
#:
#: **Kept empty rather than deleted, against the plan its own first draft
#: stated** ("when the map empties delete this test with the finding it
#: tracks"), and the reason is what the OTHER gate cannot see: a direct
#: ``calculate_paycheck`` call builds no
#: :class:`~app.services.income_service.ProfilePaychecks`, so
#: ``TestOnePaycheckProjectionPerProfilePerRender`` sees a new one only where
#: it REPLACES the pricer on a render that built nothing else (its two
#: unfunded-profile cases read 1 -> 0 there) and never one added beside a
#: pricer, nor one on a path that is not a render.  **The structural lever
#: exists and is deferred**: ``calculate_paycheck`` is public only because
#: the package re-exports it, and no ``app/`` site imports it now, so
#: leaving it on the ``_pricing`` leaf would put every future per-period call
#: behind the fail-closed ``shekel-private-module-import`` gate.  That is a
#: rule-8 fork for the developer (about ten test modules import it from the
#: package), not this leaf's; until it is taken this census is the control.
#:
#: **What it could not see, and since plan step salary:S11-c-2c the FIRST
#: census does**: ``project_salary(basis, [period], tax_configs)`` is P62's
#: shape in one line -- one payday priced outside the pricer -- and neither
#: census matched it: the first read ``configs_by_year=`` only, this one
#: reads ``calculate_paycheck`` by name.  The two single-year callers that
#: kept that door open (``tax_withholding_service``, ``tax_report_service``)
#: moved onto the pass's pricer at that step, the first census then counted
#: every ``project_salary`` call, and the shape fails it as a second
#: spelling.
_DIRECT_ENGINE_CALLERS: dict[str, int] = {}


def _per_period_calls(tree: ast.AST) -> int:
    """Return how many direct ``calculate_paycheck`` calls *tree* holds.

    The same matcher :func:`_projection_calls` uses, over the per-period
    name: there is only one mode of this call, so every one of them counts
    (as every projection call has since plan step salary:S11-c-2c).

    Args:
        tree: The parsed module.

    Returns:
        The number of matching calls.
    """
    names = {_PER_PERIOD}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _PER_PERIOD and alias.asname:
                    names.add(alias.asname)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            found += func.attr == _PER_PERIOD
        elif isinstance(func, ast.Name):
            found += func.id in names
    return found


def _per_period_census(root: Path) -> dict[str, int]:
    """Return ``{relative path: count}`` for direct engine callers under app/.

    The ENGINE's own package is excluded: ``_pricing.project_salary`` calls
    ``calculate_paycheck`` once, in the loop that IS the batch entry, and
    counting the definition's own use would put the producer in a census of
    its bypassers.  The exclusion is by PATH PREFIX so every leaf of the
    package is outside the census, and only the package -- a module named
    ``paycheck_calculator_x.py`` beside it would not match the trailing
    separator.
    """
    counts: dict[str, int] = {}
    for path in sorted((root / "app").rglob("*.py")):
        relative = str(path.relative_to(root))
        if relative.startswith(_THE_ENGINE + "/"):
            continue
        hits = _per_period_calls(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )
        if hits:
            counts[relative] = hits
    return counts


def test_no_app_site_prices_a_paycheck_outside_the_pricer():
    """No ``app/`` site calls ``calculate_paycheck`` outside the engine package.

    The second census, added at plan step **salary:S3-d**, over the blind spot
    the first one names: a per-period ``calculate_paycheck`` loop is invisible
    to a ``project_salary`` scanner by construction, and it is the shape a
    future author is most likely to write.  It enumerated six sites and was
    C12's checklist until C12-b emptied it (the constant's comment carries
    each departure); it asserts the WHOLE map still, so the map staying empty
    is the claim and a single new call anywhere fails it.
    """
    census = _per_period_census(_repo_root())
    assert census == _DIRECT_ENGINE_CALLERS, (
        "An app/ site calls paycheck_calculator.calculate_paycheck directly: "
        f"{census}. No per-period call exists outside the engine package "
        "since plan step salary:C12 (ledger row P62): a single period is "
        "priced through the read pass's income_service.PaycheckPricing -- "
        "ctx.paychecks().for_profile(profile).at(period) -- because a direct "
        "call resolves its own tax configs, and until plan step "
        "salary:S11-c-2c could drop the profile's calibration, which is how "
        "/retirement and /savings came to publish "
        "a different paycheck from every other page. Route it through the "
        "pricer."
    )


def test_the_engine_package_calls_its_own_per_period_entry_exactly_once():
    """Inside the excluded package, ``calculate_paycheck`` is called ONCE.

    The prefix exclusion in :func:`_per_period_census` hides six leaves where
    it hid one module, so a helper added INSIDE the package that priced a
    paycheck on its own would be invisible to the census above.  This pins
    the package's own count: exactly one call, in ``_pricing.py``, the loop
    that IS the batch entry.  A second call anywhere in the package fails
    here, and so does the batch entry moving to a leaf this does not name.
    """
    root = _repo_root()
    counts = {
        str(leaf.relative_to(root)): _per_period_calls(
            ast.parse(leaf.read_text(encoding="utf-8"), filename=str(leaf)),
        )
        for leaf in sorted((root / _THE_ENGINE).glob("*.py"))
    }
    assert {path: n for path, n in counts.items() if n} == {
        f"{_THE_ENGINE}/_pricing.py": 1,
    }, counts


def test_the_per_period_scanner_fires_on_a_planted_call():
    """The second census's scanner really sees the shape it claims to.

    The same firing proof :func:`test_the_scanner_fires_on_a_planted_second_spelling`
    gives the first census, and for the reason stated in the module docstring:
    a census that returns "nothing new" is indistinguishable from a census
    that looked in the wrong place until you make it fire.
    """
    attribute_form = ast.parse(
        "paycheck_calculator.calculate_paycheck(basis, period, configs)\n"
    )
    assert _per_period_calls(attribute_form) == 1

    imported_form = ast.parse(
        "from app.services.paycheck_calculator import calculate_paycheck\n"
        "calculate_paycheck(basis, period, configs)\n"
    )
    assert _per_period_calls(imported_form) == 1

    aliased_form = ast.parse(
        "from app.services.paycheck_calculator import "
        "calculate_paycheck as cp\n"
        "cp(basis, period, configs)\n"
    )
    assert _per_period_calls(aliased_form) == 1

    # The batch entry is a DIFFERENT question and must not be counted here;
    # the first census owns it.
    assert _per_period_calls(
        ast.parse("project_salary(basis, periods, configs_by_year=c)\n"),
    ) == 0


def test_the_per_period_blind_spots_are_the_ones_named():
    """The second census's documented blind spots really are blind.

    :func:`test_the_blind_spots_are_the_ones_named`'s twin, over
    ``calculate_paycheck``.  The module docstring says the two censuses share
    their limits; a shared limit stated once and executed once is a claim about
    one of them, which is how the pair would come apart.  Both directions fail
    here, as they do for the first census.
    """
    blind = {
        "assignment_alias":
            "f = paycheck_calculator.calculate_paycheck\n"
            "f(basis, period, configs)\n",
        "getattr_form":
            "getattr(pc, 'calculate_paycheck')(basis, period, configs)\n",
    }
    for label, source in blind.items():
        assert _per_period_calls(ast.parse(source)) == 0, (
            f"{label} is no longer a blind spot of the per-period census -- "
            "the scanner now sees it. That is an improvement, but the module "
            "docstring still lists it as unseen: delete that entry."
        )
