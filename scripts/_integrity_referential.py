"""
Shekel Budget App -- Data Integrity Check: the referential family (FK-*)

One of the four family modules ``scripts/integrity_check.py`` runs, split
along its ``--category`` values at plan step ``balance:X-bi-6-4c-4`` (ruling
**R-BAL161**); ``scripts/_integrity_core.py`` says why.
"""

from scripts._integrity_core import CheckSpec, run_check


# ── Category 1: Referential Integrity ────────────────────────────


def check_referential_integrity(session):
    """Run all FK-* referential integrity checks.

    Verifies that foreign key references point to existing rows.
    While PostgreSQL enforces FK constraints on write, data corruption,
    partial restores, or manual SQL operations could introduce violations.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks FK-01 through FK-16 (FK-03 deleted;
        FK-14..16 added at plan step ``balance:X-bi-6-4c-4``).
    """
    checks = [
        ("FK-01", "Accounts without a valid user", """
            SELECT a.id, a.name, a.user_id
            FROM budget.accounts a
            LEFT JOIN auth.users u ON a.user_id = u.id
            WHERE u.id IS NULL
        """),
        ("FK-02", "Accounts with invalid account type", """
            SELECT a.id, a.name, a.account_type_id
            FROM budget.accounts a
            LEFT JOIN ref.account_types t ON a.account_type_id = t.id
            WHERE t.id IS NULL
        """),
        # FK-03 was "accounts pointing to a nonexistent anchor period" until
        # plan step X-f1c3c (ruling R-EH) deleted
        # ``accounts.current_anchor_period_id``.  An account references no pay
        # period now, so the dangling-reference class it looked for cannot
        # exist; the check is deleted rather than answered vacuously.  What
        # replaces it in spirit is BA-01 below: an account with no balance
        # ASSERTION, which is the state that actually breaks a producer.
        ("FK-04", "Transactions referencing nonexistent templates", """
            SELECT t.id, t.name, t.template_id
            FROM budget.transactions t
            LEFT JOIN budget.transaction_templates tt ON t.template_id = tt.id
            WHERE t.template_id IS NOT NULL
              AND tt.id IS NULL
        """),
        ("FK-05", "Transactions in nonexistent pay periods", """
            SELECT t.id, t.name, t.pay_period_id
            FROM budget.transactions t
            LEFT JOIN budget.pay_periods p ON t.pay_period_id = p.id
            WHERE p.id IS NULL
        """),
        ("FK-06", "Transactions in nonexistent scenarios", """
            SELECT t.id, t.name, t.scenario_id
            FROM budget.transactions t
            LEFT JOIN budget.scenarios s ON t.scenario_id = s.id
            WHERE s.id IS NULL
        """),
        ("FK-07", "Transactions with invalid category", """
            SELECT t.id, t.name, t.category_id
            FROM budget.transactions t
            LEFT JOIN budget.categories c ON t.category_id = c.id
            WHERE t.category_id IS NOT NULL
              AND c.id IS NULL
        """),
        ("FK-08", "Transfers from nonexistent accounts", """
            SELECT tr.id, tr.name, tr.from_account_id
            FROM budget.transfers tr
            LEFT JOIN budget.accounts a ON tr.from_account_id = a.id
            WHERE a.id IS NULL
        """),
        ("FK-09", "Transfers to nonexistent accounts", """
            SELECT tr.id, tr.name, tr.to_account_id
            FROM budget.transfers tr
            LEFT JOIN budget.accounts a ON tr.to_account_id = a.id
            WHERE a.id IS NULL
        """),
        # FK-10 reads a category-LESS definition as legal, as FK-07 always
        # has for a row (plan step ``balance:X-bi-6-4c-4``, ruling
        # **R-BAL162**, finding **BAL-574**, fixed here).  Its column went
        # nullable at plan step ``balance:X-bi-7b`` (ruling **R-BAL24**) for
        # the definitions statement matching mints for money the app cannot
        # name, and without the ``IS NOT NULL`` term this reported each of
        # them as a dangling key: 8 on the 2026-09-30 00:11 production dump,
        # which failed the sweep CRITICAL for a legal state.
        ("FK-10", "Templates with invalid category", """
            SELECT tt.id, tt.name, tt.category_id
            FROM budget.transaction_templates tt
            LEFT JOIN budget.categories c ON tt.category_id = c.id
            WHERE tt.category_id IS NOT NULL
              AND c.id IS NULL
        """),
        ("FK-11", "Templates for nonexistent accounts", """
            SELECT tt.id, tt.name, tt.account_id
            FROM budget.transaction_templates tt
            LEFT JOIN budget.accounts a ON tt.account_id = a.id
            WHERE a.id IS NULL
        """),
        ("FK-12", "Salary profiles in nonexistent scenarios", """
            SELECT sp.id, sp.name, sp.scenario_id
            FROM salary.salary_profiles sp
            LEFT JOIN budget.scenarios s ON sp.scenario_id = s.id
            WHERE s.id IS NULL
        """),
        ("FK-13", "Salary profiles linked to nonexistent templates", """
            SELECT sp.id, sp.name, sp.template_id
            FROM salary.salary_profiles sp
            LEFT JOIN budget.transaction_templates tt ON sp.template_id = tt.id
            WHERE sp.template_id IS NOT NULL
              AND tt.id IS NULL
        """),
        # FK-14..16 are FK-05..07's TRANSFER twins (plan step
        # ``balance:X-bi-6-4c-4``, ruling **R-BAL160**).  A transfer's pay
        # period, scenario and category were graded only through its two
        # shadow rows, which carried copies of them, and ``X-bi-6-4d`` stopped
        # writing shadows for new transfers: each key is asked of
        # ``budget.transfers`` itself.  FK-05..07 read every row of
        # ``budget.transactions``, which held the shadows until plan step
        # ``balance:X-bi-6-4d-2`` deleted them.
        ("FK-14", "Transfers in nonexistent pay periods", """
            SELECT tr.id, tr.name, tr.pay_period_id
            FROM budget.transfers tr
            LEFT JOIN budget.pay_periods p ON tr.pay_period_id = p.id
            WHERE p.id IS NULL
        """),
        ("FK-15", "Transfers in nonexistent scenarios", """
            SELECT tr.id, tr.name, tr.scenario_id
            FROM budget.transfers tr
            LEFT JOIN budget.scenarios s ON tr.scenario_id = s.id
            WHERE s.id IS NULL
        """),
        ("FK-16", "Transfers with invalid category", """
            SELECT tr.id, tr.name, tr.category_id
            FROM budget.transfers tr
            LEFT JOIN budget.categories c ON tr.category_id = c.id
            WHERE tr.category_id IS NOT NULL
              AND c.id IS NULL
        """),
    ]
    return [
        run_check(session, CheckSpec(cid, "referential", "critical", desc, sql))
        for cid, desc, sql in checks
    ]
