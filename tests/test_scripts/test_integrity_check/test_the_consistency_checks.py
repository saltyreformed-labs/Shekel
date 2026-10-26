"""The DC-* checks: a self transfer, an active template on an inactive account, two rows answering
one occurrence, a user with no settings or no baseline scenario, a deduction aimed at another
user's account, an un-dated movement under a live leg, and a settled row whose money the cash
fold cannot see.
"""

from decimal import Decimal

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User
from app.enums import StatusEnum
from app.services.auth_service import hash_password
from app.services import account_service
from tests._test_helpers import (
    cover_bare_settled_row,
    create_account_of_type,
    create_settled_transfer,
    definition_firing_twice_in_a_paycheck,
    generate_row_of,
    make_expense_template,
    moved_by_the_owner,
    one_off_row_of,
    open_books_before_the_first_assertion,
    open_owner_calendar,
    populate_in_a_fresh_pass,
    settle_day_columns,
    start_test_pay_list,
)
from scripts.integrity_check import check_data_consistency


# ── Data Consistency ─────────────────────────────────────────────


class TestDataConsistency:
    """Tests for DC-* data consistency checks."""

    def test_clean_database_passes(self, app, db, seed_user, seed_periods):
        """All consistency checks pass on a properly seeded database.

        DC-02 through DC-11: DC-01 was removed 2026-06-11 (settling
        without a manual actual is a designed legal state -- see the
        ``check_data_consistency`` docstring); the remaining IDs keep
        their historical numbers.  DC-11 arrived at plan step
        ``balance:X-bi-4a`` with the alarm the cash walk's row read used to
        raise (a settled row the fold cannot see).  DC-12 arrived at
        ``balance:X-bi-6-3`` with the one the posting writer stopped raising
        (Transfer Invariant 1, a pair missing a shadow) and went at
        ``balance:X-bi-6-4d-2`` with the twin rows it counted (rulings
        **R-BAL166**, **R-BAL258**).
        """
        results = check_data_consistency(db.session)
        assert len(results) == 10
        # Critical checks must pass on clean data.
        critical_results = [r for r in results if r.severity == "critical"]
        assert all(r.passed for r in critical_results), (
            f"Critical failures: {[r.check_id for r in critical_results if not r.passed]}"
        )

    def test_settled_without_a_correction_is_not_flagged(
        self, app, db, seed_user, seed_periods
    ):
        """A Paid transaction nobody corrected passes every check.

        Pins the DC-01 removal: marking a row paid without typing a figure is
        the designed workflow, so no consistency check may flag it.  Before the
        removal this exact row failed DC-01 as critical, turning every backup
        verification red on routine data.

        **What such a row looks like changed at plan step X-au-c3.**  It carried
        no figure at all and every reader fell back to its estimate; it now
        RECORDS what the settle booked, on the ``derived`` basis, and the
        estimate beside it is the plan; since plan step balance:X-bi-4b-2 that
        record IS the covering movement.  The check must pass on that shape,
        which is what routing the fixture through ``cover_bare_settled_row``
        builds.
        """
        status_done = db.session.query(Status).filter_by(name="Paid").one()
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        settled_on = seed_periods[0].start_date
        txn = one_off_row_of(
            seed_periods[0],
            name="Done No Correction",
            amount=Decimal("50.00"),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=txn_type.id,
        )
        txn.status_id = status_done.id
        for _column, _value in settle_day_columns(settled_on).items():
            setattr(txn, _column, _value)
        db.session.flush()
        # The seam's mirror, as every settled row carries it since X-bi-3d:
        # without it DC-11 names the row, and rightly.
        cover_bare_settled_row(db.session, txn, Decimal("50.00"))

        results = check_data_consistency(db.session)
        assert all(r.passed for r in results), (
            f"Failures: {[r.check_id for r in results if not r.passed]}"
        )

    def test_dc02_detects_self_transfer(self, app, db, seed_user, seed_periods):
        """DC-02: Transfers where from_account equals to_account.

        The Transfer model has a CHECK constraint (ck_transfers_different_accounts)
        preventing this at the DB level.  We temporarily drop the constraint,
        insert the anomaly, run the check, then restore it.
        """
        account = seed_user["account"]
        status_projected = db.session.query(Status).filter_by(name="Projected").one()

        # Drop the CHECK constraint so we can insert a self-transfer.
        db.session.execute(db.text(
            "ALTER TABLE budget.transfers "
            "DROP CONSTRAINT ck_transfers_different_accounts"
        ))
        try:
            db.session.execute(db.text("""
                INSERT INTO budget.transfers
                    (user_id, pay_period_id, scenario_id, status_id,
                     from_account_id, to_account_id, name, amount,
                     is_override, is_deleted)
                VALUES (:uid, :pid, :sid, :stid, :aid, :aid,
                        'Self Transfer', 100.00, FALSE, FALSE)
            """), {
                "uid": seed_user["user"].id,
                "pid": seed_periods[0].id,
                "sid": seed_user["scenario"].id,
                "stid": status_projected.id,
                "aid": account.id,
            })
            db.session.flush()

            results = check_data_consistency(db.session)
            dc02 = next(r for r in results if r.check_id == "DC-02")
            assert not dc02.passed
            assert dc02.detail_count == 1
            assert dc02.details[0]["from_account_id"] == account.id
            assert dc02.details[0]["to_account_id"] == account.id
        finally:
            # The INSERT's deferred figure-arm event would block the ALTER.
            db.session.execute(db.text(
                "SET CONSTRAINTS budget.ck_side_record_figure_on_insert IMMEDIATE"
            ))
            # Restore the CHECK constraint.
            db.session.execute(db.text(
                "ALTER TABLE budget.transfers "
                "ADD CONSTRAINT ck_transfers_different_accounts "
                "CHECK (from_account_id != to_account_id) NOT VALID"
            ))

    def test_dc05_detects_active_template_inactive_account(
        self, app, db, seed_user
    ):
        """DC-05 flags an active template referencing an inactive account."""
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]
        category = list(seed_user["categories"].values())[0]

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=account.id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="Active Template",
            default_amount=Decimal("100.00"),
            is_active=True,
        )
        db.session.add(template)
        db.session.flush()

        # Deactivate the account.
        account.is_active = False
        db.session.flush()

        results = check_data_consistency(db.session)
        dc05 = next(r for r in results if r.check_id == "DC-05")
        assert not dc05.passed
        assert dc05.detail_count == 1  # 1 active template on inactive account

    def _template_with_generated_row(self, seed_user, seed_periods):
        """Create a template plus the ENGINE's row of it in the first paycheck.

        The row is the definition's own (:func:`generate_row_of`, plan step
        balance:X-cf), so it answers an occurrence and is derived, exactly as
        the rows DC-06 sweeps on production are.

        Returns:
            tuple: (template, generated Transaction).
        """
        template = make_expense_template(
            db.session, seed_user, amount="100.00", name="DC06 Template",
        )
        return template, generate_row_of(template, seed_periods[0])

    def test_dc06_allows_override_sibling(self, app, db, seed_user, seed_periods):
        """An override sibling next to the generated row is NOT a duplicate.

        Mirrors the schema's own uniqueness contract: the UNDATED generation
        index applies only WHERE ``is_override = FALSE``, precisely so a
        carried-forward unpaid item (flagged ``is_override = TRUE``) can
        legally coexist with the rule-generated row for its target period.
        Before the 2026-06-11 recalibration DC-06 ignored the override
        predicate and flagged this legal pair as critical.

        **Both rows are UNDATED, and that is what makes the override term
        load-bearing.**  A pair answering two different occurrences is legal
        with or without the term (the case two below), so a sibling that kept
        its own ``occurs_on`` would let a DC-06 with the term deleted pass
        here -- measured by adversarial review 2026-09-11.  The shape is the
        one carry-forward actually writes on production: an override with no
        occurrence (``_execute._create_target_override_row``) beside a
        canonical in the legacy undated shape.  Both are the engine's rows,
        in REC-516's undated idiom, the sibling moved in by the move door's
        two acts.
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )
        generated.occurs_on = None
        override_sibling = generate_row_of(template, seed_periods[1])
        override_sibling.occurs_on = None
        moved_by_the_owner(override_sibling, into=seed_periods[0])

        results = check_data_consistency(db.session)
        dc06 = next(r for r in results if r.check_id == "DC-06")
        assert dc06.passed

    def test_dc06_detects_true_duplicate(self, app, db, seed_user, seed_periods):
        """Two undated NON-override rows in one paycheck are flagged.

        The rows carry no ``occurs_on``, so the contract that holds them is the
        UNDATED half of plan step R17's split: a row answering no occurrence
        still holds its paycheck alone
        (``idx_transactions_template_scenario_undated``).  The partial unique
        index blocks this at the DB tier, so (like the DC-02 test) the index is
        dropped to stage the corruption the check exists to catch -- a partial
        restore or manual SQL is the real-world source.  The staged duplicate
        is removed before the index is recreated (CREATE UNIQUE INDEX validates
        existing rows).

        Both rows are the engine's, in the legacy undated shape the way
        REC-516's cases model it (generate, then clear the occurrence): the
        rule's own row, and its row of the next paycheck filed back into the
        first one WITHOUT the override flag a move would have set -- which is
        what makes it the duplicate rather than a legal sibling.
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )
        generated.occurs_on = None
        duplicate = generate_row_of(template, seed_periods[1])
        db.session.flush()

        db.session.execute(db.text(
            "DROP INDEX budget.idx_transactions_template_scenario_undated"
        ))
        try:
            duplicate.occurs_on = None
            duplicate.pay_period_id = generated.pay_period_id
            db.session.flush()

            results = check_data_consistency(db.session)
            dc06 = next(r for r in results if r.check_id == "DC-06")
            assert not dc06.passed
            assert dc06.detail_count == 1
            assert dc06.details[0]["cnt"] == 2
        finally:
            # Remove the staged duplicate first -- recreating the
            # unique index validates existing rows.
            db.session.execute(
                db.text("DELETE FROM budget.transactions WHERE id = :id"),
                {"id": duplicate.id},
            )
            # **Drain the deferred constraint triggers before the DDL** (plan
            # step X-f3c-2b).  The engine's INSERTs above queued an event for
            # ``ck_movement_after_books_open``, and PostgreSQL refuses
            # ``CREATE INDEX`` on a table that has pending trigger events
            # ("cannot CREATE INDEX ... because it has pending trigger
            # events").  Making them immediate runs the check now, inside the
            # same transaction, which is also the honest thing: the rows this
            # index is about to validate are the ones the trigger is about.
            db.session.execute(db.text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.session.execute(db.text("""
                CREATE UNIQUE INDEX idx_transactions_template_scenario_undated
                ON budget.transactions (template_id, scenario_id, pay_period_id)
                WHERE template_id IS NOT NULL
                  AND occurs_on IS NULL
                  AND is_deleted = FALSE
                  AND is_override = FALSE
            """))

    def test_dc06_allows_two_rows_answering_different_occurrences(
        self, app, db, seed_user,
    ):
        """One paycheck, two occurrences, two rows -- and that is CORRECT.

        The state plan step **R17** made storable and this check must not call
        corruption: a cadence that names one paycheck more than once (a monthly
        bill at a pay cadence of 30 days or more) legitimately funds two
        installments from one paycheck.  Asking the OLD question here -- group
        by ``(template, pay_period, scenario)`` -- reports this as a critical
        duplicate, which is exactly the second-fence failure the re-key exists
        to remove.

        The pair is the ENGINE's own output (plan step balance:X-cf): a
        monthly cadence inside a 60-day paycheck names two occurrences there
        (:func:`definition_firing_twice_in_a_paycheck`) and the generate pass
        writes both.
        """
        template, _first, period = definition_firing_twice_in_a_paycheck(
            db.session, seed_user, name="DC06 Template",
        )
        populate_in_a_fresh_pass(seed_user["user"].id, [period.id])
        pair = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert len(pair) == 2, "the cadence names two occurrences here"
        assert pair[0].pay_period_id == pair[1].pay_period_id

        results = check_data_consistency(db.session)
        dc06 = next(r for r in results if r.check_id == "DC-06")
        assert dc06.passed, (
            "two rows answering different occurrences of one cadence are a "
            "correct state, not a duplicate"
        )

    def test_dc06_detects_two_rows_answering_one_occurrence(
        self, app, db, seed_user, seed_periods,
    ):
        """The real duplicate since R17: one occurrence answered twice.

        The companion to the case above, and the one that keeps this check
        from having been weakened into uselessness by the re-key: the DATED
        half of the contract still holds a template to one row per occurrence,
        so staging two rows with the SAME ``occurs_on`` must be flagged
        critical.  Without this, dropping the group key to "anything goes in a
        paycheck" would pass every DC-06 test in the file.

        The second row is the engine's row of the next paycheck -- a real one
        of the SAME owner's, so the row is legal on every axis but the
        occurrence key under test -- rewritten to answer the first row's
        occurrence once the index that forbids it is dropped.
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )
        duplicate = generate_row_of(template, seed_periods[1])

        db.session.execute(db.text(
            "DROP INDEX budget.idx_transactions_template_scenario_occurrence"
        ))
        try:
            duplicate.occurs_on = generated.occurs_on
            db.session.flush()

            results = check_data_consistency(db.session)
            dc06 = next(r for r in results if r.check_id == "DC-06")
            assert not dc06.passed
            assert dc06.detail_count == 1
            assert dc06.details[0]["cnt"] == 2
        finally:
            db.session.execute(
                db.text("DELETE FROM budget.transactions WHERE id = :id"),
                {"id": duplicate.id},
            )
            # **Drain the deferred constraint triggers before the DDL** (plan
            # step X-f3c-2b), the same two lines the sibling case above needs
            # and for the same reason.  The engine's INSERTs queued an event
            # for ``ck_movement_after_books_open`` whatever that trigger would
            # DECIDE -- the event is queued at statement time and the function
            # only runs at COMMIT -- and PostgreSQL refuses ``CREATE INDEX`` on
            # a table carrying pending trigger events.  Making them immediate
            # runs the check now, inside this transaction.
            db.session.execute(db.text("SET CONSTRAINTS ALL IMMEDIATE"))
            # The index as migration ``e7c3a1f9b482`` (plan step X-au-h)
            # ships it: NO ``is_override`` term.  It was recreated here in its
            # pre-X-au-h shape until 2026-09-11, a second spelling of the
            # schema that no test could see because each case clones its own
            # database.
            db.session.execute(db.text("""
                CREATE UNIQUE INDEX idx_transactions_template_scenario_occurrence
                ON budget.transactions (template_id, scenario_id, occurs_on)
                WHERE template_id IS NOT NULL
                  AND occurs_on IS NOT NULL
                  AND is_deleted = FALSE
            """))

    def test_dc07_detects_user_without_settings(self, app, db):
        """DC-07 detects a user without a user_settings row."""
        # Create a user without settings by bypassing the normal seed.
        user = User(
            email="nosettings@shekel.local",
            password_hash=hash_password("testpass"),
            display_name="No Settings",
        )
        db.session.add(user)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc07 = next(r for r in results if r.check_id == "DC-07")
        assert not dc07.passed
        assert dc07.detail_count == 1  # 1 user without settings
        assert any(
            d.get("email") == "nosettings@shekel.local"
            for d in dc07.details
        )

    def test_dc08_detects_user_without_baseline(self, app, db, seed_user):
        """DC-08 detects an owner-role user without a baseline scenario."""
        # Remove the baseline flag from the seed scenario.
        seed_user["scenario"].is_baseline = False
        db.session.flush()

        results = check_data_consistency(db.session)
        dc08 = next(r for r in results if r.check_id == "DC-08")
        assert not dc08.passed
        assert dc08.detail_count == 1  # 1 user without baseline scenario

    def test_dc08_ignores_companion_users(
        self, app, db, seed_user, seed_companion
    ):
        """A companion with no scenario is NOT flagged by DC-08.

        Companions view the linked owner's data and own no budget rows
        of their own (no accounts, periods, or scenarios) by design --
        "no baseline scenario" is their correct steady state.  Before
        the 2026-06-11 recalibration DC-08 flagged every companion as a
        critical failure on every prod run.
        """
        # Precondition: the companion really has no scenarios.
        scenario_count = (
            db.session.query(Scenario)
            .filter_by(user_id=seed_companion["user"].id)
            .count()
        )
        assert scenario_count == 0

        results = check_data_consistency(db.session)
        dc08 = next(r for r in results if r.check_id == "DC-08")
        assert dc08.passed

    def test_dc09_detects_cross_user_deduction_target(
        self, app, db, seed_user
    ):
        """DC-09 flags a deduction targeting another user's account."""
        from app.models.ref import (  # pylint: disable=import-outside-toplevel
            CalcMethod,
            PaycheckLineKind,
            FilingStatus,
        )
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from app.models.paycheck_line import PaycheckLine  # pylint: disable=import-outside-toplevel
        from app.models.user import UserSettings  # pylint: disable=import-outside-toplevel
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel

        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        # Create a second user with their own account.
        user2 = User(
            email="user2@shekel.local",
            password_hash=hash_password("testpass"),
            display_name="User Two",
        )
        db.session.add(user2)
        db.session.flush()
        settings2 = UserSettings(user_id=user2.id)
        db.session.add(settings2)
        scenario2 = Scenario(user_id=user2.id, name="Baseline", is_baseline=True)
        db.session.add(scenario2)
        # user2's calendar, so the account factory has an anchor to assign.
        # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
        _bootstrap2 = open_owner_calendar(user2.id, _date(2024, 1, 5))[0]

        checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
        account2 = account_service.create_account(
            account_service.AccountSpec(
                user_id=user2.id,
                account_type_id=checking_type.id,
                name="User2 Checking",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.flush()
        # Its BOOKS open before anything this fixture dates (plan step
        # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
        # asserts -- the owner's today -- and this suite settles on or before it.
        open_books_before_the_first_assertion(db.session, account2)

        # Create a salary profile for user 1 with a deduction targeting user 2's account.
        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Test Salary",
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("3076.92"))  # $80,000.00 a year / 26
        db.session.flush()

        timing = db.session.query(PaycheckLineKind).first()
        method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckLine(
            salary_profile_id=profile.id,
            paycheck_line_kind_id=timing.id,
            calc_method_id=method.id,
            name="Cross-user deduction",
            amount=Decimal("100.00"),
            target_account_id=account2.id,  # User 2's account!
        )
        db.session.add(deduction)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc09 = next(r for r in results if r.check_id == "DC-09")
        assert not dc09.passed
        assert dc09.detail_count == 1  # 1 deduction targeting another user's account

    def test_dc10_detects_an_un_dated_movement_holding_a_live_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """DC-10 grades what a caller of the BARE status seam would leave.

        Plan step ``balance:X-bi-3e-2``: a revert un-dates the covering
        movement and keeps it, and the ledger is the DOOR's -- the family
        reconcile after the seam reverses the movement's legs.  So the state
        this arm exists for is planted by the seam ALONE: settle through the
        verb (the movement posts), revert through ``apply_status_change``
        with no reconcile.  The arm fires on that movement and passes once the
        door's reconcile runs -- the same row, both directions, so the check
        is graded firing and not merely quiet.
        """
        # pylint: disable=import-outside-toplevel  -- the module convention.
        from app import ref_cache
        from app.services import posting_service, status_seam, transaction_service

        txn = generate_row_of(
            make_expense_template(
                db.session, seed_user, amount="148.32",
                name="Electric", category_key="Rent",
            ),
            seed_periods[0],
        )
        transaction_service.settle_transaction(txn)
        db.session.flush()
        (movement,) = txn.covering_movements
        assert movement.settled_on is not None
        dc10 = next(
            r for r in check_data_consistency(db.session) if r.check_id == "DC-10"
        )
        assert dc10.passed, "a DATED movement's live leg is not a finding"

        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.flush()
        assert movement.settled_on is None

        dc10 = next(
            r for r in check_data_consistency(db.session) if r.check_id == "DC-10"
        )
        assert not dc10.passed
        assert dc10.severity == "critical"
        # One row per live ledger account: the cash leg and its category leg.
        assert {row["entry_id"] for row in dc10.details} == {movement.id}
        assert {row["net"] for row in dc10.details} == {
            Decimal("-148.32"), Decimal("148.32"),
        }
        assert all(row["covers_settlement"] is True for row in dc10.details)

        posting_service.sync_transaction_postings(txn)
        db.session.flush()
        dc10 = next(
            r for r in check_data_consistency(db.session) if r.check_id == "DC-10"
        )
        assert dc10.passed
        assert movement.settled_on is None, "the movement is still un-dated and kept"

    def test_dc10_names_a_transfer_sides_record_by_its_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer side's un-dated record is reported under its TRANSFER, on both sides.

        Plan step ``balance:X-bi-6-4d-2``: a side's record hangs off the
        transfer (ruling **R-BAL88**), so its ``transaction_id`` is ``NULL``
        and the finding names the transfer instead -- it named the record's
        shadow row until then.  Planted around the doors and COMMITTED, so DC-10
        reads a state the database stores: a paid transfer set back to
        Projected with both sides' records un-dated in the same save (a revert's
        shape, ruling **R-BAL61**) while their journal legs stay, which is
        what a revert leaves when nothing reconciles the ledger after it.  Its
        first plant un-dated the to-side alone under the PAID transfer and read
        DC-10 before committing: a state the side-band rule refuses at COMMIT.
        """
        # pylint: disable=import-outside-toplevel  -- the module convention.
        import sqlalchemy
        from app import ref_cache
        from app.models.transaction_entry import TransactionEntry

        savings = create_account_of_type(
            seed_user, db.session, "Savings", "DC-10 Savings",
        )
        db.session.commit()
        transfer = create_settled_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("250.00"),
        )
        db.session.commit()
        records = (
            db.session.query(TransactionEntry)
            .filter(sqlalchemy.or_(
                TransactionEntry.expense_transfer_id == transfer.id,
                TransactionEntry.income_transfer_id == transfer.id,
            ))
            .all()
        )
        assert len(records) == 2, "one record on each side"

        def dc10():
            return next(
                r for r in check_data_consistency(db.session)
                if r.check_id == "DC-10"
            )

        assert dc10().passed

        db.session.execute(sqlalchemy.text(
            "UPDATE budget.transfers SET status_id = :projected WHERE id = :id"
        ), {"projected": ref_cache.status_id(StatusEnum.PROJECTED), "id": transfer.id})
        db.session.execute(sqlalchemy.text(
            "UPDATE budget.transaction_entries "
            "SET settled_on = NULL, settled_day_basis_id = NULL, "
            "reconciled_by_id = NULL "
            "WHERE expense_transfer_id = :id OR income_transfer_id = :id"
        ), {"id": transfer.id})
        db.session.commit()
        fired = dc10()
        assert not fired.passed
        assert {
            (row["entry_id"], row["transaction_id"], row["transfer_id"])
            for row in fired.details
        } == {(record.id, None, transfer.id) for record in records}

    def test_dc11_detects_a_settled_row_the_fold_cannot_see(
        self, app, db, seed_user, seed_periods,
    ):
        """DC-11 grades both arms of the alarm the cash walk lost at X-bi-4a.

        Ruling **R-BAL80**: the fold reads movements alone, by the MOVEMENT's
        day, so a settled row with no settle day, or a covering movement with
        no day, is money the balance silently omits -- the first the state
        ``balance_predicates.settled_day`` raised on when the fold still read
        the row.  Planted around the doors, as the hazard is: a bill settled
        through the verb passes; the row's day cleared by SQL fires the
        row-day arm; restored, the MOVEMENT's day pair cleared by SQL fires
        the movement-day arm -- each arm shown firing and clearing on the one
        row.  A THIRD arm -- a stored non-zero figure with no covering
        movement -- graded the row's own ``settled_amount`` against the
        movement through plan step ``balance:X-bi-4b-1``; that column went at
        ``X-bi-4b-2``, and a settled row holding no movement IS the ``$0.00``
        record (ruling **R-BAL82**), so the same plant now PASSES, which is
        asserted here as the retired arm's negative control.
        """
        # pylint: disable=import-outside-toplevel  -- the module convention.
        import sqlalchemy
        from app.services import transaction_service

        def dc11():
            return next(
                r for r in check_data_consistency(db.session)
                if r.check_id == "DC-11"
            )

        txn = generate_row_of(
            make_expense_template(
                db.session, seed_user, amount="148.32",
                name="Electric", category_key="Rent",
            ),
            seed_periods[0],
        )
        transaction_service.settle_transaction(txn)
        db.session.flush()
        (movement,) = txn.covering_movements
        assert dc11().passed

        # The retired arm's plant: a settled row whose movement is gone is a
        # close of nothing (ruling R-BAL82), not an alarm.
        db.session.execute(sqlalchemy.text(
            "DELETE FROM budget.transaction_entries WHERE id = :id"
        ), {"id": movement.id})
        assert dc11().passed

        # Restore the movement through the seam's own writer.  The record is
        # STATED here rather than read back off the row: ``recorded_settlement``
        # reads the covering movement -- the very row deleted above -- and a
        # settled row holding none reads as a close of nothing, which would
        # withdraw rather than re-cover.
        db.session.expire(txn)
        from app.enums import MovementFigureSourceEnum
        from app.services.status_seam import Settlement
        from app.services.status_seam._covering import (  # noqa: E402
            sync_covering_movement,
        )
        sync_covering_movement(
            txn, was_settled=True, now_settled=True,
            settlement=Settlement(
                amount=Decimal("148.32"),
                source=MovementFigureSourceEnum.RESOLVED,
            ),
        )
        db.session.flush()
        assert dc11().passed

        # The ROW-DAY arm: a settled status with no settle day (the pairing
        # CHECK takes the basis with it; the record may stand without a day).
        db.session.execute(sqlalchemy.text(
            "UPDATE budget.transactions "
            "SET settled_on = NULL, settled_day_basis_id = NULL WHERE id = :id"
        ), {"id": txn.id})
        fired = dc11()
        assert not fired.passed
        assert [row["transaction_id"] for row in fired.details] == [txn.id]
        assert fired.details[0]["settled_on"] is None
        assert fired.details[0]["undated_covering_movements"] == 0

        # Restored; then the MOVEMENT-DAY arm: the row keeps its day while the
        # mirror -- the fold's real input -- loses its pair.
        db.session.execute(sqlalchemy.text(
            "UPDATE budget.transactions t SET settled_on = e.settled_on, "
            "settled_day_basis_id = e.settled_day_basis_id "
            "FROM budget.transaction_entries e "
            "WHERE e.transaction_id = t.id AND e.covers_settlement AND t.id = :id"
        ), {"id": txn.id})
        assert dc11().passed
        db.session.execute(sqlalchemy.text(
            "UPDATE budget.transaction_entries "
            "SET settled_on = NULL, settled_day_basis_id = NULL, "
            "reconciled_by_id = NULL "
            "WHERE transaction_id = :id AND covers_settlement"
        ), {"id": txn.id})
        fired = dc11()
        assert not fired.passed
        assert [row["transaction_id"] for row in fired.details] == [txn.id]
        assert fired.details[0]["settled_on"] is not None
        assert fired.details[0]["undated_covering_movements"] == 1
