"""The sweep's frame: the CheckResult a check returns, and run_all_checks, which runs all four
categories by default or only those a filter names and exits zero with no critical finding on a
clean database.
"""

from scripts.integrity_check import CheckResult, run_all_checks


# ── CheckResult dataclass ────────────────────────────────────────


class TestCheckResult:
    """Tests for the CheckResult dataclass."""

    def test_passing_check(self):
        """A passing CheckResult has passed=True and detail_count=0."""
        result = CheckResult(
            check_id="TEST-01",
            category="test",
            severity="critical",
            description="test check",
            passed=True,
            detail_count=0,
        )
        assert result.passed is True
        assert result.detail_count == 0
        assert result.details == []

    def test_failing_check(self):
        """A failing CheckResult has passed=False and detail_count > 0."""
        result = CheckResult(
            check_id="TEST-02",
            category="test",
            severity="warning",
            description="test check",
            passed=False,
            detail_count=3,
            details=[{"id": 1}, {"id": 2}, {"id": 3}],
        )
        assert result.passed is False
        assert result.detail_count == 3
        assert len(result.details) == 3


# ── run_all_checks ───────────────────────────────────────────────


class TestRunAllChecks:
    """Tests for the top-level run_all_checks() function."""

    def test_runs_all_categories_by_default(
        self, app, db, seed_user, seed_periods
    ):
        """run_all_checks() returns results from all 4 categories."""
        results = run_all_checks(db.session)
        categories = {r.category for r in results}
        assert categories == {"referential", "orphan", "balance", "consistency"}

    def test_category_filter(self, app, db, seed_user, seed_periods):
        """run_all_checks(categories=['referential']) only runs FK checks."""
        results = run_all_checks(db.session, categories=["referential"])
        assert all(r.category == "referential" for r in results)
        # 12 since plan step X-f1c3c: FK-03 ("accounts pointing to a
        # nonexistent anchor period") went with the column it queried; 15
        # since balance:X-bi-6-4c-4's transfer twins FK-14..16 (R-BAL160).
        assert len(results) == 15

    def test_returns_check_result_objects(
        self, app, db, seed_user, seed_periods
    ):
        """All returned items are CheckResult instances."""
        results = run_all_checks(db.session)
        assert all(isinstance(r, CheckResult) for r in results)

    def test_exit_code_zero_on_clean_db(
        self, app, db, seed_user, seed_periods
    ):
        """No critical failures on a properly seeded database."""
        results = run_all_checks(db.session)
        critical = [r for r in results if not r.passed and r.severity == "critical"]
        assert len(critical) == 0, (
            f"Unexpected critical failures: "
            f"{[(r.check_id, r.description) for r in critical]}"
        )

    def test_clean_database_zero_critical_anomalies(
        self, app, db, seed_user, seed_periods
    ):
        """All checks on a clean seeded database report zero critical anomalies.

        This is a regression guard: if a future schema change introduces a
        latent integrity issue, this test catches it immediately.
        """
        results = run_all_checks(db.session)
        critical_failures = [
            r for r in results
            if not r.passed and r.severity == "critical"
        ]
        assert len(critical_failures) == 0, (
            f"Critical anomalies on clean DB: "
            f"{[(r.check_id, r.description, r.detail_count) for r in critical_failures]}"
        )
        # Total check count should cover all 4 categories:
        # 12 FK + 5 OR + 3 BA + 9 DC = 29 checks (DC-01 removed
        # 2026-06-11 -- estimated-only settles are a legal state; DC-10
        # added at plan step balance:X-bi-3e-2, where a revert began
        # keeping the covering movement un-dated and its ledger legs became
        # the door's to reverse).
        # It was 30 from plan step X-f1c3c, where FK-03 and BA-02 both
        # queried ``accounts.current_anchor_*`` and went with the columns;
        # BA-06 was added 2026-08-11 beside the deletion of pay_calendar
        # C3-b's coverage rule, which is what made its state reachable, and
        # BA-07 beside pay_calendar C2-b2, which took away the
        # generation-time report of a pay-period date gap.  It fell to 31 at
        # plan step R-F6, which retired OR-02: the orphaned-rule state it
        # scanned for is refused by ``ck_recurrence_rules_one_owner``.  It
        # fell to 28 at plan step pay_calendar:C4-c, which dropped
        # ``end_date`` and ``period_index`` and took BA-03, BA-04 and BA-07
        # with them -- an ordinal gap, a span overlap and an uncovered day are
        # all unexpressible once a period is one payday.  It rose to 29 at
        # balance:X-bi-3e-2 (DC-10), to 30 at balance:X-bi-4a (DC-11) and to
        # 31 at balance:X-bi-6-3 (DC-12, Transfer Invariant 1, the alarm the
        # posting writer stopped raising), and to 34 at balance:X-bi-6-4c-4
        # (FK-14..16, FK-05..07's transfer twins, ruling R-BAL160).  It fell
        # to 33 at balance:X-bi-6-4d-2, which deleted DC-12 with the twin
        # rows it counted (rulings R-BAL166, R-BAL258).
        assert len(results) == 33
