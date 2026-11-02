"""Tests for the shared ID-derived Jinja-globals registration helper.

Locks the F-7 / Commit 6 invariant: the single source of truth for
the Jinja globals lives in :mod:`app.jinja_globals`, both
``create_app()`` and the conftest's per-test re-seat route through
it, and every previously-missing constant is now present so any
template that references one no longer raises ``UndefinedError``
at request time.
"""

# pylint: disable=import-outside-toplevel

import pathlib
import re

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum, CalcMethodEnum, PaycheckLineKindEnum, GoalModeEnum,
    IncomeUnitEnum,
)
from app.jinja_globals import _REF_ID_GLOBALS, register_ref_id_globals

_TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent.joinpath(
    "app", "templates",
)
_REC_GLOBAL_PATTERN = re.compile(r"\bREC_[A-Z0-9_]+\b")
#: Jinja comments, stripped before the scan: a ``{# ... REC_ANNUAL ... #}``
#: block is documentation, not a read, and counting it would let a global with
#: no live reader satisfy the equality below.
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def _rec_globals_referenced_by_templates() -> set[str]:
    """Return every ``REC_*`` name any template actually reads.

    Scanned from the templates rather than mirrored, so this set and the
    registered set are two INDEPENDENT enumerations of one fact and asserting
    them equal catches drift in either direction: a global a template reads but
    nothing registers (Jinja is not configured with ``StrictUndefined``, so
    ``rr.pattern_id == REC_ANNUAL`` would evaluate silently False and a yearly
    bill would take the wrong branch), and a global nothing reads (dead
    apparatus a later reader would take for a live contract).

    Deriving the expected set from the closed pattern set's enum is what
    this replaced, and plan step R7a is why: with the ``recurrence_cell``
    macro's per-pattern branches gone, two members legitimately had no
    global, so an enum-derived assertion would have demanded that dead code
    be kept.  That enum is deleted from plan step R9, so the scan is the
    only statement of the set that is left.

    Returns:
        The ``REC_*`` identifiers appearing anywhere under ``app/templates``.
    """
    found: set[str] = set()
    for path in _TEMPLATE_ROOT.rglob("*.html"):
        source = _JINJA_COMMENT.sub("", path.read_text(encoding="utf-8"))
        found.update(_REC_GLOBAL_PATTERN.findall(source))
    return found


def test_register_ref_id_globals_populates_previously_missing_entries(app):
    """All eight C-28-era missing constants are present after registration.

    The conftest list pre-Commit-6 omitted these; templates that
    referenced any of them at test time raised UndefinedError.
    """
    with app.app_context():
        register_ref_id_globals(app)

        assert app.jinja_env.globals["LINE_KIND_PRE_TAX_DEDUCTION"] == (
            ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION)
        )
        assert app.jinja_env.globals["LINE_KIND_POST_TAX_DEDUCTION"] == (
            ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION)
        )
        assert app.jinja_env.globals["CALC_PERCENTAGE"] == (
            ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
        )
        assert app.jinja_env.globals["CALC_FLAT"] == (
            ref_cache.calc_method_id(CalcMethodEnum.FLAT)
        )
        assert app.jinja_env.globals["GOAL_MODE_FIXED"] == (
            ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        )
        assert app.jinja_env.globals["GOAL_MODE_INCOME_RELATIVE"] == (
            ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        )
        assert app.jinja_env.globals["INCOME_UNIT_PAYCHECKS"] == (
            ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        )
        assert app.jinja_env.globals["INCOME_UNIT_MONTHS"] == (
            ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        )


def test_register_ref_id_globals_is_idempotent(app):
    """Calling the helper twice in succession is a no-op (same values).

    Each per-test ref-cache reseat calls the helper; subsequent
    invocations during a session must produce identical Jinja
    globals dicts so templates rendered between calls see a
    consistent view.
    """
    with app.app_context():
        register_ref_id_globals(app)
        first = dict(app.jinja_env.globals)

        register_ref_id_globals(app)
        second = dict(app.jinja_env.globals)

        # Every key the registration table declares, DERIVED from the table
        # rather than mirrored by hand.  The hand-written copy this replaced
        # had drifted from the module it was checking: it listed 45 names and
        # asserted that count, while ``_REF_ID_GLOBALS`` registered 49 at the
        # time -- ``REC_EVERY_PERIOD`` and all three ``EMPLOYER_TYPE_*`` were
        # absent, and nothing could notice, because the loop below only
        # asserts that each LISTED key is present.  A second copy of a
        # single-source-of-truth list is the exact defect the F-7 extraction
        # removed from ``create_app`` and the conftest; re-introducing it here
        # as a test fixture made it a place a constant could hide unverified.
        registered_keys = {
            name
            for _accessor, members in _REF_ID_GLOBALS
            for name in members
        }
        assert len(registered_keys) == sum(
            len(members) for _accessor, members in _REF_ID_GLOBALS
        ), "a global name is declared twice under different accessors"

        # Deriving the names makes them self-maintaining but costs the one
        # thing the hand-written list DID have: a derived set shrinks along
        # with the module, so on its own it cannot notice a global that was
        # DELETED.  Two pins restore that, and neither is hand-maintained:
        #
        #   * the total, which changes only on a deliberate edit here;
        #   * exact coverage for the recurrence patterns, the one group steps
        #     have actually narrowed -- R2e-3 removed ``ONCE`` from the enum,
        #     R7a removed two more globals with the ``recurrence_cell`` macro's
        #     per-pattern branches, and plan step R7b-2 removed the last five
        #     with the pattern ``<select>`` whose ``data-*`` attributes read
        #     them.  **That arm is still live at zero, not vacuous**: it fails
        #     the moment either side moves alone -- a REC_* global registered
        #     with no template reader (which is what caught these five) or a
        #     template naming one nothing registers, which Jinja would evaluate
        #     as a silent ``False`` rather than raise.
        # 41 until plan step **balance:X-am**, which deleted ``STATUS_SETTLED``
        # along with the status it named.  That global had ZERO template readers
        # for its whole life and the arm below could not see it: the
        # registered-vs-read check is scoped to ``REC_*``.  41 again since plan
        # step **salary:S11-e** added ``LINE_KIND_PRE_FICA_DEDUCTION``, which the
        # salary page's kind chip reads (``salary/_lines_section.html``).
        assert len(registered_keys) == 41, (
            "the ID-derived globals changed count -- update this number "
            "deliberately, and check the template that reads the new or "
            "removed constant"
        )
        assert _rec_globals_referenced_by_templates() == {
            key for key in registered_keys if key.startswith("REC_")
        }, (
            "the REC_* globals registered and the REC_* globals the templates "
            "read have diverged"
        )

        for key in registered_keys:
            assert key in first, f"first pass missing {key}"
            assert first[key] == second[key], (
                f"value drifted across idempotent calls for {key}"
            )
        # Each is a resolved ``ref`` row id, not an Undefined or a name string.
        for key in registered_keys:
            assert isinstance(first[key], int), (
                f"{key} is {first[key]!r}, not a ref-row id"
            )


def test_goal_form_renders_with_fixed_mode_id_constant(auth_client, app):
    """GET /savings/goals/new embeds the GOAL_MODE_FIXED id in the form.

    ``app/templates/savings/goal_form.html`` consumes the
    ``GOAL_MODE_FIXED`` global at two sites: the
    ``data-fixed-mode-id="..."`` attribute on the mode selector and
    a ``selected``-by-default branch.  Before Commit 6 the
    conftest's reseat helper omitted this constant; the route
    response would have rendered an empty ``data-fixed-mode-id=""``
    attribute (Jinja Undefined coerces to empty string in
    expression context with the default environment) and the
    JavaScript that gates the income-relative fields on the id
    comparison would have silently broken.
    """
    with app.app_context():
        expected_fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    resp = auth_client.get("/savings/goals/new")
    assert resp.status_code == 200
    html = resp.data.decode()
    # The id must be embedded as an integer literal in the attribute.
    assert f'data-fixed-mode-id="{expected_fixed_id}"' in html


class TestThePaydayShiftGlobals:
    """The four schedule forms' shared wording and option list.

    Plan step ``pay_calendar:C14-b``, ruling **R-PC56**.  These four globals
    sit OUTSIDE ``_REF_ID_GLOBALS`` on purpose -- that table maps one name to
    one id, and ``PAYDAY_SHIFT_OPTIONS`` is an ordered list while
    ``PAYDAY_SHIFT_LABEL`` / ``PAYDAY_SHIFT_HELP`` are module constants with no
    ref-cache precondition at all.  The cost of staying out of it is that the
    idempotence test above, which derives its key set from that table so a
    constant cannot hide unverified, does not cover them.  This class is what
    pays that cost, and an adversarial review of 2026-09-05 is why it exists.
    """

    def test_the_options_are_the_three_modelled_conventions_as_ids(self, app):
        """Every option is a ``ref`` id, and all three members are offered.

        Ids rather than names, because that is what a ``<select>`` posts and
        what ``BusinessDayShiftField`` deserializes; a global carrying member
        names would render a form whose every submission the schema refuses.
        """
        with app.app_context():
            options = app.jinja_env.globals["PAYDAY_SHIFT_OPTIONS"]
            expected = {
                ref_cache.business_day_shift_id(member): member
                for member in BusinessDayShiftEnum
            }

            assert [value for value, _label in options] == list(expected)
            assert all(isinstance(value, int) for value, _label in options)
            assert len(options) == len(BusinessDayShiftEnum) == 3

    def test_none_is_offered_FIRST_and_is_what_SHIFT_NONE_names(self, app):
        """The OFF answer leads, and the fallback global agrees with it.

        Order is asserted rather than left to a dict: ``none`` is what every
        form preselects for an owner who has stated nothing (**R-PC56**), and a
        list that led with ``prior`` would put "pay early" under the cursor of
        every owner who never answered -- which is the defaulting that ruling
        refused, arriving through the option order instead of the default.
        """
        with app.app_context():
            options = app.jinja_env.globals["PAYDAY_SHIFT_OPTIONS"]
            none_id = ref_cache.business_day_shift_id(
                BusinessDayShiftEnum.NONE,
            )

            assert options[0][0] == none_id
            assert app.jinja_env.globals["SHIFT_NONE"] == none_id

    def test_no_label_describes_an_option_as_usual_or_recommended(self, app):
        """**R-PC56** refused stating as fact what no owner was asked.

        That ruling rejected defaulting the convention to ``prior`` on the
        reasoning that real payroll usually pays early, because it states as
        fact what no owner was asked -- the error ``balance:R-IF`` was written
        to correct.  A label or help text saying the same thing would
        reintroduce the claim one layer up, where no ruling is watching.
        """
        with app.app_context():
            wording = " ".join(
                label for _value, label in
                app.jinja_env.globals["PAYDAY_SHIFT_OPTIONS"]
            ) + " " + app.jinja_env.globals["PAYDAY_SHIFT_HELP"]

            lowered = wording.lower()
            for steer in ("usual", "recommend", "most ", "typical", "default"):
                assert steer not in lowered, (
                    f"the payday-convention wording steers the owner with "
                    f"{steer!r}, which R-PC56 refused"
                )

    def test_the_wording_globals_are_registered_and_non_empty(self, app):
        """The question and its help text exist for every form to read.

        Registered unconditionally beside the pay-calendar bounds rather than
        with the options, because neither needs ``ref_cache``.  Asserted
        because four templates now render them and a missing global is an
        empty label rather than an error.
        """
        with app.app_context():
            assert app.jinja_env.globals["PAYDAY_SHIFT_LABEL"].strip()
            assert app.jinja_env.globals["PAYDAY_SHIFT_HELP"].strip()
