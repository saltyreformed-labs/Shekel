"""The R-SAL5 backfill picks the RIGHT salary profile, and refuses the wrong ones.

Plan step **salary:R14-a**, migration ``e7c4b9a2f350``.  The migration gives
``budget.investment_params`` a ``salary_profile_id`` -- which job's paycheck
funds this account's payroll feed -- and backfills it in two steps: the single
active same-owner profile whose active deduction names the account, else the
owner's single active profile, else NULL.

**Why these tests exist rather than the migration's hand measurement alone.**
Two defects in the first draft of that backfill were found by an adversarial
review and proven by running the pre-fix predicate against constructed data.
Both are the class a green suite is silent about BY CONSTRUCTION: the rows are
written, every constraint holds, and nothing raises until somebody reads a
figure that belongs to a job they left or to another person entirely.  The full
suite was green at 12,849 tests while both defects were live, so for these two
the suite was not an instrument -- it is one now.  A peer session working the
same shape put the standard well: the question is not "does the suite pass" but
"does a case exist that would have FAILED before the fix".  Each test below is
that case, and :class:`TestTheBackfillIsGraded` proves each one fires.

**The SQL is executed, not re-implemented.**  The tests import the migration
module and run its own ``_BACKFILL`` statement, so a test cannot pass against a
correct re-statement of a broken migration -- the tautology this repository's
lessons file records.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from tests._test_helpers import (
    load_migration_module,
    replay_paycheck_lines_rename,
    rewind_paycheck_lines_rename,
    start_test_pay_list,
)

_MIGRATION = load_migration_module(
    "e7c4b9a2f350_an_employer_contribution_names_the_profile.py"
)

#: The pre-fix step-1 predicate, RECONSTRUCTED from the migration comment
#: that describes it -- the broken SQL was never committed, so there is no
#: artifact to recover it from and nothing can diff the two.  It is here so
#: each defect test can show what the broken backfill WOULD have written
#: for the same state; the grading power is in the POST-fix assertions.  It differs from the
#: shipped one in exactly the two ways the review found: it tests the
#: DEDUCTION's ``is_active`` and never the PROFILE's, and it never scopes the
#: profile to the account's owner.
_PRE_FIX_STEP_1 = text("""
SELECT (SELECT count(DISTINCT d.salary_profile_id)
          FROM salary.paycheck_deductions d
         WHERE d.target_account_id = :account_id
           AND d.is_active) AS naming_profiles,
       (SELECT min(d.salary_profile_id)
          FROM salary.paycheck_deductions d
         WHERE d.target_account_id = :account_id
           AND d.is_active) AS would_write
""")


def _none_type_id() -> int:
    """Return ``ref.employer_contribution_types``' ``none`` row id."""
    return db.session.execute(text(
        "SELECT id FROM ref.employer_contribution_types WHERE name = 'none'"
    )).scalar()


def _as_the_migration_saw_it() -> None:
    """Put the schema under plan step ``salary:R18-a``'s downgrade, for the migration's SQL.

    The backfill and the reconstructed pre-fix predicate both read
    ``salary.paycheck_deductions``, which head renamed to
    ``salary.paycheck_lines`` (ruling **R-SAL38**); the rewind restores the
    name the statements were written against
    (:func:`~tests._test_helpers.rewind_paycheck_lines_rename`, the stacked
    newest-first shape).  Called AFTER a case has seeded its rows through the
    models this tree maps; :func:`_run_backfill` replays the rename before a
    case reads its result back through them.
    """
    rewind_paycheck_lines_rename(db.session)


def _run_backfill() -> None:
    """Execute the migration's own backfill against the test session.

    Ends by replaying the R18-a rename (:func:`_as_the_migration_saw_it`'s
    inverse), so the ``refresh`` each case makes afterwards -- an ORM load
    through the head models, whose joined recurrence rule maps the renamed
    arm -- finds the schema those models map.
    """
    db.session.execute(
        text(_MIGRATION._BACKFILL),  # pylint: disable=protected-access
        {"none_id": _none_type_id()},
    )
    db.session.flush()
    replay_paycheck_lines_rename(db.session)


def _profile(user_id: int, scenario_id: int, name: str, *, is_active: bool):
    """Create and flush one salary profile.

    The filing status comes off the ``ref`` table rather than off an existing
    profile: the seeded user has no salary profile, so a "copy the first one"
    helper reads ``None`` and every case here dies in setup.
    """
    filing_status_id = db.session.execute(text(
        "SELECT id FROM ref.filing_statuses WHERE name = 'single'"
    )).scalar()
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing_status_id,
        name=name,
        is_active=is_active,
    )
    db.session.add(profile)
    db.session.flush()
    start_test_pay_list(profile, Decimal("1923.08"))  # $50,000.00 a year / 26
    return profile


def _deduction_naming(profile, account_id: int, name: str):
    """Create an ACTIVE deduction on *profile* that funds *account_id*."""
    timing_id = db.session.execute(text(
        "SELECT id FROM ref.paycheck_line_kinds WHERE name = 'pre_tax_deduction'"
    )).scalar()
    method_id = db.session.execute(text(
        "SELECT id FROM ref.calc_methods WHERE name = 'flat'"
    )).scalar()
    deduction = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=timing_id,
        calc_method_id=method_id,
        name=name,
        amount=Decimal("100.00"),
        target_account_id=account_id,
        is_active=True,
    )
    db.session.add(deduction)
    db.session.flush()
    return deduction


def _investment_account(user_id: int, scenario_id: int, employer_type_id: int):
    """Create an investment account with params carrying *employer_type_id*."""
    account = Account(
        user_id=user_id,
        name="Employer 401(k)",
        account_type_id=db.session.query(Account).filter_by(
            user_id=user_id,
        ).first().account_type_id,
    )
    db.session.add(account)
    db.session.flush()
    params = InvestmentParams(
        account_id=account.id,
        employer_contribution_type_id=employer_type_id,
        assumed_annual_return=Decimal("0.07000"),
    )
    db.session.add(params)
    db.session.flush()
    return account, params


@pytest.fixture(name="employer_type_ids")
def _employer_type_ids(app):
    """Return ``(none_id, flat_percentage_id)`` for the employer ref table."""
    with app.app_context():
        rows = dict(db.session.execute(text(
            "SELECT name, id FROM ref.employer_contribution_types"
        )).all())
        return rows["none"], rows["flat_percentage"]


class TestTheBackfillPicksTheRightProfile:
    """Each constructed case, run through the migration's own SQL."""

    def test_an_archived_jobs_deduction_does_not_win(
        self, app, seed_user, employer_type_ids,
    ):
        """An ARCHIVED profile's still-active deduction must not be chosen.

        The FIRING CONTROL for the first defect.  ``delete_profile`` archives a
        profile by setting ``is_active = False`` on it and on its template, and
        touches its DEDUCTIONS not at all -- so an archived job's deductions
        stay active forever.  The pre-fix step 1 counted them, wrote the
        archived profile, and the step-2 fallback that finds the job the owner
        actually holds never ran.

        The owner here holds exactly one ACTIVE profile, so the correct answer
        is that one, and it is reached through step 2.
        """
        _none_id, flat_id = employer_type_ids
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            active = _profile(
                user_id, scenario_id, "Current Job", is_active=True,
            )
            archived = _profile(
                user_id, scenario_id, "Archived Job", is_active=False,
            )
            # Every OTHER profile of this owner is archived, so "the owner's
            # single active profile" is unambiguous.
            for other in db.session.query(SalaryProfile).filter(
                SalaryProfile.user_id == user_id,
                SalaryProfile.id.notin_([active.id, archived.id]),
            ):
                other.is_active = False
            account, params = _investment_account(
                user_id, scenario_id, flat_id,
            )
            _deduction_naming(archived, account.id, "Stale 401k Feed")
            # Ids BEFORE the rewind: the rewind's commit expires every loaded
            # row, and a profile reloads through the head models' joined
            # recurrence rule, which maps the column the rewind renamed.
            account_id, archived_id, active_id = account.id, archived.id, active.id
            _as_the_migration_saw_it()

            # What the BROKEN backfill would have written, on this same state.
            pre_fix = db.session.execute(
                _PRE_FIX_STEP_1, {"account_id": account_id},
            ).one()
            assert pre_fix.naming_profiles == 1
            assert pre_fix.would_write == archived_id, (
                "the pre-fix predicate must reproduce the defect here, or this "
                "test is not the firing control it claims to be"
            )

            _run_backfill()
            db.session.refresh(params)
            assert params.salary_profile_id == active_id

    def test_another_owners_deduction_is_ignored_entirely(
        self, app, seed_user, seed_second_user, employer_type_ids,
    ):
        """A CROSSED PAIR -- another owner's deduction naming this account.

        The FIRING CONTROL for the second defect, and the pairing that gets
        past an ``account_id``-only filter: the deduction row is real, active
        and names this account, and only its profile's OWNER says it does not
        belong here.  ``paycheck_lines.target_account_id`` has no
        ownership validation at its write door (ledger row **N-534**, closed in
        this same step), so the state is reachable through the app.

        This owner is given TWO active profiles so step 2 cannot resolve
        either: the correct answer is NULL, and any non-NULL result means the
        stranger's profile was taken.
        """
        _none_id, flat_id = employer_type_ids
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            stranger_id = seed_second_user["user"].id
            stranger = _profile(
                stranger_id,
                seed_second_user["scenario"].id,
                "Stranger Job",
                is_active=True,
            )
            # TWO active profiles for this owner, so step 2 is ambiguous and
            # NULL is the only correct answer.  With one, step 2 resolves
            # legitimately and the test would pass whether or not the
            # cross-owner deduction was excluded -- it would prove nothing.
            _profile(user_id, scenario_id, "Day Job", is_active=True)
            _profile(user_id, scenario_id, "Night Job", is_active=True)
            account, params = _investment_account(
                user_id, scenario_id, flat_id,
            )
            _deduction_naming(stranger, account.id, "Forged Feed")
            account_id, stranger_id = account.id, stranger.id
            _as_the_migration_saw_it()

            pre_fix = db.session.execute(
                _PRE_FIX_STEP_1, {"account_id": account_id},
            ).one()
            assert pre_fix.would_write == stranger_id, (
                "the pre-fix predicate must reproduce the cross-owner write "
                "here, or this test is not the firing control it claims to be"
            )

            _run_backfill()
            db.session.refresh(params)
            assert params.salary_profile_id is None

    def test_the_owners_own_naming_deduction_still_wins(
        self, app, seed_user, employer_type_ids,
    ):
        """Step 1 still RESOLVES for the case it exists for.

        The positive control, and it is not optional: both tests above assert
        that a profile is NOT written, and a backfill that wrote nothing at all
        would satisfy both.  This is the case the column exists for -- an
        active deduction, on the owner's own active profile, naming the
        account -- and it must be answered by STEP 1.

        **The decoy profile is created FIRST, and that ordering is the whole
        test.**  A second adversarial review measured the first draft of this
        case grading nothing: it created the funder first, so the funder held
        the lowest id, and step 2's fallback is ``min(p.id)`` -- the same
        answer by a different route.  Deleting step 1 from the shipped SQL
        outright left all five tests in this file GREEN.  With the decoy
        lower, step 2 would answer the DECOY, so the assertion can only pass
        when step 1 actually ran.  Step 2 is reachable here (this owner has
        two active profiles but the ``naming_profiles = 1`` arm already
        satisfies the WHERE), which is exactly why the two arms have to be
        told apart by their ANSWERS rather than by reachability.
        """
        _none_id, flat_id = employer_type_ids
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            # The DECOY first, so it holds the lower id and therefore IS
            # step 2's ``min(p.id)`` answer.  Reverse these two lines and this
            # test stops grading step 1 at all -- measured.
            decoy = _profile(user_id, scenario_id, "Other Job", is_active=True)
            funder = _profile(
                user_id, scenario_id, "Funding Job", is_active=True,
            )
            assert decoy.id < funder.id, (
                "the decoy must sort below the funder, or step 2's min(p.id) "
                "returns the funder and this test grades nothing"
            )
            account, params = _investment_account(
                user_id, scenario_id, flat_id,
            )
            _deduction_naming(funder, account.id, "401k Feed")
            _as_the_migration_saw_it()

            _run_backfill()
            db.session.refresh(params)
            assert params.salary_profile_id == funder.id

    def test_an_account_with_no_payroll_feed_is_left_null(
        self, app, seed_user, employer_type_ids,
    ):
        """No employer contribution and no naming deduction means no fact.

        NULL is the honest answer rather than the owner's only profile: the
        column records which job funds this account's payroll feed, and an
        account with no feed has none.  Without this case the backfill could
        stamp every investment account with a profile and all three tests
        above would still pass.
        """
        none_id, _flat_id = employer_type_ids
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            _profile(user_id, scenario_id, "Only Job", is_active=True)
            _account, params = _investment_account(
                user_id, scenario_id, none_id,
            )
            _as_the_migration_saw_it()

            _run_backfill()
            db.session.refresh(params)
            assert params.salary_profile_id is None


class TestTheBackfillIsGraded:
    """The migration's SQL is the thing under test, not a copy of it."""

    def test_the_tests_run_the_migrations_own_statement(self):
        """``_run_backfill`` executes the shipped SQL, not a re-statement.

        A test that re-implemented the backfill would grade one derivation
        against itself and pass against a broken migration.  Asserted on the
        module identity rather than trusted.
        """
        assert _MIGRATION.revision == "e7c4b9a2f350"
        assert _MIGRATION.down_revision == "c8f3a5d2e714"
        # The two qualifiers the review added are IN the NAMING join.
        #
        # ``p.is_active`` is asserted as a COUNT rather than by membership: the
        # ``owner_profile`` CTE carries its own ``WHERE p.is_active``
        # independently, so a bare ``in`` test passes even with the naming
        # join's copy deleted -- measured by a second adversarial review, which
        # dropped that clause and watched this assertion stay green while the
        # archived-profile control failed alone.
        backfill = _MIGRATION._BACKFILL  # pylint: disable=protected-access
        assert backfill.count("p.is_active") == 2, (
            "expected p.is_active in BOTH the naming join and the "
            "owner_profile CTE; one of them has been dropped"
        )
        assert "p.user_id = a.user_id" in backfill
