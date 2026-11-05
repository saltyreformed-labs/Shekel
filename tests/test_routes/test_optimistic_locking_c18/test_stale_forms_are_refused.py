"""C-18 optimistic-lock tests: each PATCH or POST endpoint that ships a row's version_id refuses a
stale submission without writing, an HTMX endpoint with a 409 conflict cell and a full-page form
with a flash and a redirect.
"""

from decimal import Decimal

from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transaction_service

from ._helpers import (
    _bump_version_outside_session,
    _make_template,
    _make_transaction,
    _make_envelope_template_and_txn,
    _make_entry,
    _make_savings_account,
    _make_savings_goal,
    _make_salary_profile,
    _make_salary_raise,
    _make_paycheck_line,
    _make_transfer,
    _make_transfer_template,
)


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- HTMX endpoints (Transaction)
# ═════════════════════════════════════════════════════════════════════


class TestTransactionStaleFormPrevention:
    """``update_transaction`` (PATCH /transactions/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Submitting the row's current ``version_id`` updates and bumps."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v0 = txn.version_id

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "estimated_amount": "75.00",
                    "estimated_amount_as_rendered": "50.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == Decimal("75.00")
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A submitted version older than the row's current counter returns 409.

        The route MUST short-circuit before touching the database;
        the financial value (``estimated_amount``) stays unchanged
        and the version counter is not bumped.  The 409 body
        carries the conflict cell so the user sees the latest value
        and a warning indicator.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            stale = txn.version_id

            _bump_version_outside_session("budget", "transactions", txn_id)
            db.session.expire_all()
            current = db.session.get(Transaction, txn_id).version_id
            assert current == stale + 1

            amount_before = db.session.get(Transaction, txn_id).estimated_amount

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "estimated_amount": "999.99",
                    "estimated_amount_as_rendered": "50.00",
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409, response.data
            body = response.data.decode()
            # Conflict cell carries the warning indicator.
            assert "exclamation-triangle" in body
            assert "text-warning" in body

            # No mutation, no version bump.
            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == amount_before
            assert persisted.version_id == current

    def test_route_catches_stale_data_error_as_409(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A StaleDataError raised at flush time becomes a 409 + conflict cell.

        Engineers a true race: the test session loads the row at
        version N, mutates it, then a SQLAlchemy mapper event fires
        during the UPDATE and bumps the row from a separate
        connection, defeating the version-pinned WHERE clause.
        SQLAlchemy raises StaleDataError and the route's except
        clause converts it into the same 409 + conflict cell the
        form-side check produces.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            amount_before = txn.estimated_amount

            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                if fired["flag"] or target.id != txn_id:
                    return
                fired["flag"] = True
                _bump_version_outside_session(
                    "budget", "transactions", txn_id,
                )

            event.listen(Transaction, "before_update", make_stale)
            try:
                response = auth_client.patch(
                    f"/transactions/{txn_id}",
                    data={"estimated_amount": "555.55", "estimated_amount_as_rendered": "50.00"},
                )
            finally:
                event.remove(Transaction, "before_update", make_stale)

            assert response.status_code == 409, response.data

            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == amount_before

    def test_mark_done_catches_a_stale_settle_as_409(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """A race during a mark-done settle is a 409, not an unhandled 500.

        New coverage at plan step X-f2-c2, on a door that had none: the sibling
        above grades the PATCH edit, and nothing graded ``mark-done``.  The row
        is envelope-tracked WITH an entry so the settle takes the
        ``settle_from_entries`` branch rather than the manual one.

        The race is engineered as another writer's commit landing as the
        settle verb begins, after the door read the row: the row's version
        moves from a separate connection, so the session holds the stale
        version and the version-pinned WHERE fails.  That is where the settle
        verb took the row's lock until plan step ``balance:X-bn``, and where
        the developer placed the change (Round 12 Q1, 2026-09-23, "Move the
        change earlier": just before that lock, plan step
        ``credit_card:CC-5-4a-4``, ruling **R-CC96**).  Since X-bn no request
        of the owner's can land there -- every one waits on the owner's write
        lock (ruling **R-CC106**) -- so the other writer is one that holds no
        owner lock, which the separate connection is; a stale page's version
        is layer 2's to check (plan step ``balance:X-da``).  It was a
        ``before_update`` mapper event bumping the version DURING the UPDATE,
        as :meth:`test_route_catches_stale_data_error_as_409` still does,
        until the row lock made that connection wait on this request.  Shown
        to FIRE: deleting ``mark_done``'s ``except StaleDataError`` arm fails
        it.

        **It is deliberately NOT labelled the control for that step's
        exception-topology change, because it is not one, and the measurement
        is worth more than the label.**  Folding the settle into one verb put
        the amount-and-status phase inside the ``StaleDataError`` /
        ``IntegrityError`` net it used to sit outside, and the obvious claim --
        "that fixes a reachable 500" -- was tested against the pre-change route
        reconstructed from git and found FALSE: this test passes on both
        topologies.  The reason is worth recording, because it is not visible
        by reading the phases.  **The first flush of a settle is
        ``txn.status.is_settled``**, the ARGUMENT to the posting reconcile,
        evaluated after ``apply_status_change`` has both dirtied the row and
        expired its ``status`` relationship -- so the refresh autoflushes.  In
        both topologies that expression sits inside the net.  Everything
        earlier reads columns or already-loaded relationships: ``txn.entries``
        loads while the row is still clean, and ``settle_from_entries``' owner
        read is ``txn.user_id`` -- a COLUMN on the row already in the session,
        which loads nothing at all.  It walked ``txn.pay_period.user_id`` until
        plan step ``pay_calendar:C13-b``, and the conclusion is STRONGER for
        the move rather than merely surviving it: the read that had to be
        already-loaded is now one that cannot load.
        """
        with app.app_context():
            _template, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            txn_id = txn.id
            _make_entry(txn_id, seed_user["user"].id)
            status_before = txn.status_id

            fired = {"flag": False}
            real_settle = transaction_service.settle_transaction

            def make_stale_then_settle(row, *args, **kwargs):
                if not fired["flag"] and row.id == txn_id:
                    fired["flag"] = True
                    _bump_version_outside_session(
                        "budget", "transactions", txn_id,
                    )
                return real_settle(row, *args, **kwargs)

            monkeypatch.setattr(
                transaction_service, "settle_transaction", make_stale_then_settle,
            )
            response = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            )

            # The listener must have reached the row, or this is grading an
            # ordinary settle and would pass over the defect it exists for.
            assert fired["flag"], "the race never fired -- the control is dead"
            assert response.status_code == 409, response.data

            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.status_id == status_before
            assert persisted.settled_on is None


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- HTMX endpoints (Transfer)
# ═════════════════════════════════════════════════════════════════════


class TestTransferStaleFormPrevention:
    """``update_transfer`` (PATCH /transfers/instance/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Submitting the row's current ``version_id`` updates and bumps."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v0 = xfer.version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "300.00",
                    "amount_as_rendered": "250.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(Transfer, xfer_id)
            assert persisted.amount == Decimal("300.00")
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ``version_id`` returns 409 + conflict cell, no mutation."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            stale = xfer.version_id

            _bump_version_outside_session("budget", "transfers", xfer_id)
            db.session.expire_all()
            amount_before = db.session.get(Transfer, xfer_id).amount

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "999.99",
                    "amount_as_rendered": "250.00",
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409
            body = response.data.decode()
            assert "exclamation-triangle" in body
            assert "text-warning" in body

            db.session.expire_all()
            persisted = db.session.get(Transfer, xfer_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- form-page endpoints (TransactionTemplate)
# ═════════════════════════════════════════════════════════════════════


class TestTransactionTemplateStaleFormPrevention:
    """``update_template`` (POST /templates/<id>) optimistic locking.

    Form-page route: stale-form check redirects with a flash
    warning rather than rendering an HTMX 409 partial.  Same
    semantics: no mutation, user reloads to retry.
    """

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user,
    ):
        """Matching version updates the template and bumps the counter."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v0 = tmpl.version_id
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            response = auth_client.post(
                f"/templates/{tmpl_id}",
                data={
                    "name": "Renamed Template",
                    "amount": "150.00",
                    "category_id": str(cat.id),
                    "transaction_type_id": str(expense_type.id),
                    "account_id": str(seed_user["account"].id),
                    "version_id": str(v0),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.expire_all()
            persisted = db.session.get(TransactionTemplate, tmpl_id)
            assert persisted.name == "Renamed Template"
            assert persisted.version_id == v0 + 1

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """A stale version flashes a warning and rolls the form back."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            stale = tmpl.version_id

            _bump_version_outside_session(
                "budget", "transaction_templates", tmpl_id,
            )
            db.session.expire_all()
            name_before = db.session.get(
                TransactionTemplate, tmpl_id,
            ).name
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            response = auth_client.post(
                f"/templates/{tmpl_id}",
                data={
                    "name": "Should Not Apply",
                    "amount": "999.99",
                    "category_id": str(cat.id),
                    "transaction_type_id": str(expense_type.id),
                    "account_id": str(seed_user["account"].id),
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(TransactionTemplate, tmpl_id)
            assert persisted.name == name_before, (
                "Stale-form must NOT mutate the template."
            )


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- form-page endpoints (TransferTemplate)
# ═════════════════════════════════════════════════════════════════════


class TestTransferTemplateStaleFormPrevention:
    """``update_transfer_template`` (POST /transfers/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on transfer-template update flashes a warning."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            tmpl_id = tmpl.id
            stale = tmpl.version_id

            _bump_version_outside_session(
                "budget", "transfer_templates", tmpl_id,
            )
            db.session.expire_all()
            name_before = db.session.get(TransferTemplate, tmpl_id).name

            response = auth_client.post(
                f"/transfers/{tmpl_id}",
                data={
                    "name": "Should Not Apply",
                    "amount": "9999.99",
                    "from_account_id": str(seed_user["account"].id),
                    "to_account_id": str(savings.id),
                    "category_id": str(cat.id),
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(TransferTemplate, tmpl_id)
            assert persisted.name == name_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- SavingsGoal
# ═════════════════════════════════════════════════════════════════════


class TestSavingsGoalStaleFormPrevention:
    """``update_goal`` (POST /savings/goals/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on savings-goal update flashes a warning."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            goal = _make_savings_goal(seed_user["user"].id, savings.id)
            goal_id = goal.id
            stale = goal.version_id

            _bump_version_outside_session("budget", "savings_goals", goal_id)
            db.session.expire_all()
            target_before = db.session.get(SavingsGoal, goal_id).target_amount

            response = auth_client.post(
                f"/savings/goals/{goal_id}",
                data={
                    "account_id": str(savings.id),
                    "name": "Renamed",
                    "target_amount": "99999.99",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SavingsGoal, goal_id)
            assert persisted.target_amount == target_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- SalaryProfile / SalaryRaise / PaycheckLine
# ═════════════════════════════════════════════════════════════════════


class TestSalaryProfileStaleFormPrevention:
    """``update_profile`` (POST /salary/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on salary-profile update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            profile_id = profile.id
            stale = profile.version_id
            filing_status = profile.filing_status_id

            _bump_version_outside_session(
                "salary", "salary_profiles", profile_id,
            )
            db.session.expire_all()
            # The update takes no salary since plan step salary:X-av-3a, so
            # the witness is the column the stale form tries to rename.
            name_before = db.session.get(
                SalaryProfile, profile_id,
            ).name

            response = auth_client.post(
                f"/salary/{profile_id}",
                data={
                    "name": "Renamed Job",
                    "filing_status_id": str(filing_status),
                    "state_code": "NC",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SalaryProfile, profile_id)
            assert persisted.name == name_before


class TestSalaryRaiseStaleFormPrevention:
    """``update_raise`` (POST /salary/raises/<id>/edit) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on raise update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            sraise = _make_salary_raise(profile.id)
            raise_id = sraise.id
            stale = sraise.version_id
            raise_type = sraise.raise_type_id

            _bump_version_outside_session(
                "salary", "salary_raises", raise_id,
            )
            db.session.expire_all()
            pct_before = db.session.get(SalaryRaise, raise_id).percentage

            response = auth_client.post(
                f"/salary/raises/{raise_id}/edit",
                data={
                    "raise_type_id": str(raise_type),
                    "effective_month": "6",
                    "effective_year": "2027",
                    "percentage": "10",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SalaryRaise, raise_id)
            assert persisted.percentage == pct_before


class TestPaycheckLineStaleFormPrevention:
    """``update_line`` (POST /salary/lines/<id>/edit) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on deduction update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            ded = _make_paycheck_line(profile.id)
            ded_id = ded.id
            stale = ded.version_id
            timing_id = ded.paycheck_line_kind_id
            method_id = ded.calc_method_id

            _bump_version_outside_session(
                "salary", "paycheck_lines", ded_id,
            )
            db.session.expire_all()
            amount_before = db.session.get(
                PaycheckLine, ded_id,
            ).amount

            response = auth_client.post(
                f"/salary/lines/{ded_id}/edit",
                data={
                    "name": "Renamed Deduction",
                    "paycheck_line_kind_id": str(timing_id),
                    "calc_method_id": str(method_id),
                    "amount": "999.99",
                    "amount_as_rendered": "250.00",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(PaycheckLine, ded_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- TransactionEntry
# ═════════════════════════════════════════════════════════════════════


class TestTransactionEntryStaleFormPrevention:
    """``update_entry`` (PATCH /transactions/<id>/entries/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Matching version updates the entry and bumps the counter."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            v0 = entry.version_id

            response = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={
                    "amount": "30.00",
                    "amount_as_rendered": "250.00",
                    # The edit form emits the direction beside the amount
                    # since plan step ``bank_import:X-gj-2b-3``, and the
                    # route refuses one without the other rather than
                    # guessing a sign.  Posted here because this case is
                    # about the VERSION pin, and a payload the form cannot
                    # produce would make it about something else.
                    "direction": "charge",
                    "description": "Walmart",
                    "purchased_on": entry.purchased_on.isoformat(),
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(TransactionEntry, entry_id)
            assert persisted.amount == Decimal("30.00")
            assert persisted.description == "Walmart"
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ``version_id`` returns 409 + entry list with warning banner."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            stale = entry.version_id

            _bump_version_outside_session(
                "budget", "transaction_entries", entry_id,
            )
            db.session.expire_all()
            amount_before = db.session.get(
                TransactionEntry, entry_id,
            ).amount

            response = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={
                    "amount": "9999.99",
                    "amount_as_rendered": "250.00",
                    "description": "Should Not Apply",
                    "purchased_on": entry.purchased_on.isoformat(),
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409, response.data
            body = response.data.decode()
            assert "changed by another action" in body.lower()

            db.session.expire_all()
            persisted = db.session.get(TransactionEntry, entry_id)
            assert persisted.amount == amount_before
