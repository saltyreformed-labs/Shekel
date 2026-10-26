"""
Shekel Budget App -- Clearing a recurring definition (plan step R2e-1)

The TRANSFER edit form offers "Does not repeat" as a recurrence pattern.
Choosing it must mean what it says: the template stops naming a rule, the rule
row ceases to exist, and the instances that rule already generated stop
occupying future pay periods.

**The TRANSACTION form no longer offers it, and its schema REFUSES the empty
unit** (plan step ``balance:X-bi-7b``, ruling **R-BAL23**, developer
2026-09-13 for the edit form too): a transaction definition with no rule is a
ONE-OFF, made at the Budget grid through the one-off producer, so clearing a
cadence from the form would manufacture rule-less definitions holding
scattered rows listed nowhere.  The transaction class below grades the
REFUSAL -- the rule and every row stand -- and the sweep's own semantics
(what a cleared cadence retires and retains) stay graded on the transfer
twin here and on the engine in ``TestRegenerateForTemplate``
(``test_recurrence_engine/``).

**It meant none of those things.**  Measured on a real edit of an
every-paycheck template before this step::

    rule_id before: 1   rows: 10
    rule_id after:  1   rows: 10
    (log) deleted_count=6  created_count=6

The builder returned ``None`` for an unselected pattern, the resolver assigned
nothing, and the route then regenerated from the rule the user had just asked
it to stop using -- so the option was not merely inert, it re-materialised the
recurrence it was supposed to end.

The properties pinned here, because getting one right at the cost of another is
the failure mode:

1. the rule is DETACHED and DELETED (a detached rule is finding **F-6**'s leak);
2. the future auto-generated rows are swept from the edit's effective date,
   while settled, soft-deleted and hand-edited rows inside that same window
   survive;
3. a template that NEVER recurred is untouched -- a RULE-LESS transfer
   template's single Transfer is an ordinary auto-generated row, so a sweep
   gated only on "has no rule now" would delete it on a rename;
4. the amount chooser is not offered, because it would ask an amount question
   about rows the request is deleting;
5. a LOAN PAYMENT refuses to be cleared at all -- its cadence is what the
   balance seam projects the loan against;
6. only the owner can reach the path (it DELETEs a row, so 404 is asserted
   directly rather than inferred from the shared ``get_or_404``).

The transfer half is covered too: the same helpers serve both kinds, and plan
step R2e-3 points the transfer form's null option at this exact path when it
retires the ``Once`` pattern.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services import (
    account_service,
    recurrence_engine,
    recurring_transfer_query,
    transfer_recurrence,
)
from tests._test_helpers import (
    all_periods,
    create_account_of_type,
    create_loan_account,
    derived_span,
    generate_row_of,
    generate_transfer_of,
    make_cadence_rule,
    open_books_before_the_first_assertion,
    repriced_by_the_owner,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD


# ── Helpers ──────────────────────────────────────────────────────────


def _every_period_rule(template):
    """Author and flush an every-paycheck rule ONTO *template*.

    It takes the owning definition since plan step R-F6: the owning FK is on
    ``budget.recurrence_rules``, so a rule cannot be written before there is a
    definition for it to belong to.
    """
    return make_cadence_rule(template, EVERY_PERIOD)


def _recurring_txn_template(seed_user, recurs=True):
    """Create an expense template, optionally recurring, and generate its rows.

    It AUTHORS the cadence rather than taking a pre-built rule (plan step
    R-F6); a caller that needs the rule reads ``template.recurrence_rule``.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name="Streaming",
        default_amount=Decimal("15.99"),
    )
    db.session.add(template)
    db.session.flush()
    # Priced, as the transfer twin below is: its rows are the engine's and
    # DERIVED, and a derived row of an unpriced definition is the shape
    # ``state_template_price`` says the application cannot build.
    state_template_price(template)
    rule = _every_period_rule(template) if recurs else None
    if rule is not None:
        recurrence_engine.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in all_periods(seed_user["user"].id)},
            ),
            seed_user["scenario"].id,
        )
    db.session.commit()
    return template


def _savings_account(seed_user):
    """Create a Savings destination for the transfer templates below."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
    # developer-confirmed 2026-09-22 ("Yes: open books before rows"): the
    # account's books open before the schedule, as the seeded account's do.
    # ``create_account`` opens them TODAY, after the paychecks these rows are
    # generated into, and since ruling R-PC85 a definition's occurrences start
    # above the books of every account it moves money in.
    open_books_before_the_first_assertion(db.session, acct)
    db.session.commit()
    return acct


def _recurring_transfer_template(seed_user, savings, recurs=True):
    """Create a transfer template, optionally recurring, and generate its rows.

    Authors the cadence itself, for the reason
    :func:`_recurring_txn_template` gives.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep to savings",
        default_amount=Decimal("50.00"),
    )
    db.session.add(template)
    db.session.flush()
    # Its definition STATES a price, as every app-side create door
    # does: since plan step X-au-f a generated transfer stores no
    # figure and reads this series on its own due date.
    state_template_price(template)
    rule = _every_period_rule(template) if recurs else None
    if rule is not None:
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in all_periods(seed_user["user"].id)},
            ),
            seed_user["scenario"].id,
        )
    db.session.commit()
    return template


def _period_indices(rows, periods):
    """Return the sorted period indices the given rows occupy."""
    by_id = {p.id: derived_span(p).period_index for p in periods}
    return sorted(by_id[row.pay_period_id] for row in rows)


# ── Transaction templates ────────────────────────────────────────────


class TestClearingATransactionTemplatesRecurrence:
    """POST /templates/<id> with an empty recurrence pattern is REFUSED."""

    def test_an_empty_cadence_is_refused_and_the_rule_and_every_row_stand(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """R-BAL23: nothing is deleted, nothing is swept, the sentence names why.

        Ten every-paycheck rows occupy period indices 0-9.  The edit posts
        the placeholder's empty unit with an ``effective_from`` that would
        have swept periods 4-9 under the retired clear; the update schema
        refuses it (``TemplateUpdateSchema.validate_a_cadence_is_chosen``)
        before the route reads a field, so the rule row, all ten rows and the
        amount are exactly as they were, and the flash says a recurring
        transaction needs a cadence.
        """
        template = _recurring_txn_template(seed_user)
        rule_id = template.recurrence_rule.id
        version_before = template.version_id
        rows = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(rows, seed_periods) == list(range(10))

        resp = auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "default_amount": "19.99",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"needs a cadence" in resp.data

        db.session.expire_all()
        template = db.session.get(TransactionTemplate, template.id)
        assert template.recurrence_rule is not None
        assert template.recurrence_rule.id == rule_id
        assert db.session.get(RecurrenceRule, rule_id) is not None
        assert template.default_amount == Decimal("15.99")
        assert template.version_id == version_before
        survivors = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == list(range(10))

    def test_another_users_template_is_not_reachable(
        self, app, second_auth_client, seed_user, seed_periods,
    ):
        """A cross-user clear 404s and leaves the rule row standing.

        The clear path DELETES a row, so the security-response rule is checked
        on it directly rather than assumed from the shared ``get_or_404``.
        """
        template = _recurring_txn_template(seed_user)
        rule_id = template.recurrence_rule.id

        resp = second_auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "version_id": str(template.version_id),
        })
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(RecurrenceRule, rule_id) is not None
        assert db.session.get(
            TransactionTemplate, template.id,
        ).recurrence_rule.id == rule_id

    def test_an_edit_that_submits_no_pattern_at_all_leaves_the_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An absent recurrence key is "unchanged", and is NOT refused.

        The update schemas are partial: a caller that submits only an amount
        is asking for an amount change.  The refusal above fires on a
        PRESENT empty unit; an absent one states nothing about the cadence,
        and collapsing the two would make every partial update either
        delete the cadence (the retired reading) or be refused (this one).
        """
        template = _recurring_txn_template(seed_user)
        rule_id = template.recurrence_rule.id

        resp = auth_client.post(f"/templates/{template.id}", data={
            "default_amount": "19.99",
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        template = db.session.get(TransactionTemplate, template.id)
        assert template.default_amount == Decimal("19.99")
        assert template.recurrence_rule.id == rule_id
        assert db.session.get(RecurrenceRule, rule_id) is not None
        rows = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(rows, seed_periods) == list(range(10))


class TestATemplateThatNeverRecurredIsNotSwept:
    """The sweep is gated on the recurrence having PARTICIPATED in the edit."""

    def test_renaming_a_rule_less_template_keeps_its_rows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A row of a template with no rule survives a rename.

        The regeneration gate cannot be "the template has no rule -> sweep":
        a template with no cadence can still own generated rows -- a one-time
        transfer's single Transfer is exactly that shape, and so is a
        definition whose cadence was cleared -- and an unrelated edit must
        not touch them.
        """
        template = _recurring_txn_template(seed_user, recurs=False)
        # A definition that no longer repeats still owns the rows its cadence
        # wrote: the engine's row under a cadence, then the cadence cleared
        # the way the edit door clears one (plan step balance:X-cf-4).
        _every_period_rule(template)
        manual = generate_row_of(template, seed_periods[6])
        template.recurrence_rule = None
        db.session.commit()
        assert template.recurrence_rule is None
        manual_id = manual.id

        resp = auth_client.post(f"/templates/{template.id}", data={
            "name": "Streaming (renamed)",
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        assert db.session.get(Transaction, manual_id) is not None


class TestARuleLessDefinitionsEditReachesItsRows:
    """The transaction twin of ``propagate_to_unruled_template``.

    Plan step ``balance:X-bi-7a`` (ruling **R-BAL20**).  A definition that
    neither has nor had a rule never enters the regeneration, so until this
    step its rows were reached by the bulk RENAME alone: a new category,
    account or type stayed on the Recurring page and never reached the grid.
    The route now hands such a definition's live rows to
    ``recurrence_engine.propagate_to_unruled_definition``, which applies the
    same two refusals the maintain pass makes.  Each case makes the
    definition rule-less the way the edit door does (the engine's row under
    a cadence, then the cadence cleared), which is the one way a
    rule-less transaction definition holds a row today.
    """

    def _rule_less_with_a_row(self, seed_user, seed_periods):
        """Return ``(template, its one live row)``, the cadence cleared."""
        template = _recurring_txn_template(seed_user, recurs=False)
        _every_period_rule(template)
        row = generate_row_of(template, seed_periods[6])
        template.recurrence_rule = None
        db.session.commit()
        assert template.recurs is False
        return template, row

    def _edit(self, auth_client, template, seed_periods, **fields):
        """POST the edit form with *fields*, the way the page submits it."""
        return auth_client.post(f"/templates/{template.id}", data={
            "name": template.name,
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
            **fields,
        }, follow_redirects=True)

    def test_a_category_and_type_change_reaches_the_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The definition speaks to its row: category, type AND name land."""
        with app.app_context():
            template, row = self._rule_less_with_a_row(seed_user, seed_periods)
            groceries = seed_user["categories"]["Groceries"]
            income = db.session.query(TransactionType).filter_by(name="Income").one()
            assert row.category_id != groceries.id
            due_before = row.due_date

            resp = self._edit(
                auth_client, template, seed_periods,
                name="Streaming (renamed)",
                category_id=str(groceries.id),
                transaction_type_id=str(income.id),
            )
            assert resp.status_code == 200

            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.name == "Streaming (renamed)"
            assert row.category_id == groceries.id
            assert row.transaction_type_id == income.id
            # The one field the definition does NOT state: the row's own date.
            assert row.due_date == due_before
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

    def test_an_account_move_reaches_a_row_holding_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A row carrying nothing follows its definition's account freely."""
        with app.app_context():
            template, row = self._rule_less_with_a_row(seed_user, seed_periods)
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Other Checking",
            )
            db.session.commit()

            resp = self._edit(
                auth_client, template, seed_periods, account_id=str(other.id),
            )
            assert resp.status_code == 200
            assert b"kept the value" not in resp.data

            db.session.expire_all()
            assert db.session.get(Transaction, row.id).account_id == other.id

    def test_an_account_move_carries_a_row_holding_a_record_and_says_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The decision the recurring pass makes, made here too -- and it retains nothing.

        RE-EXPRESSED at plan step ``credit_card:CC-5-2`` under CLAUDE.md rule
        5 with the developer's confirmation (2026-09-20, ruling **R-CC36**).
        Through ``CC-5-1`` a row with the owner's own note was RETAINED where
        it was when the definition moved its account, and the owner told --
        the transfer twin still is
        (``test_a_non_repeating_transfer_holding_a_record_is_retained_too``),
        because its endpoint move re-files the shadows' movements.  A
        transaction row's note is filed on no account and its purchases stay
        on their own, so the row follows its definition and the panel prints
        no "kept the value" notice for it.
        """
        with app.app_context():
            template, row = self._rule_less_with_a_row(seed_user, seed_periods)
            row.notes = "paid from the old account already"
            db.session.commit()
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Other Checking",
            )
            db.session.commit()

            resp = self._edit(
                auth_client, template, seed_periods,
                name="Streaming (moved)", account_id=str(other.id),
            )
            assert resp.status_code == 200
            assert b"kept the value it already had" not in resp.data

            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.account_id == other.id
            assert row.notes == "paid from the old account already"
            assert row.name == "Streaming (moved)"

    def test_an_overridden_row_is_left_alone(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A typed figure (OWN, ``is_override``) is the owner's, not the rule's.

        Until ``X-bi-7b`` builds R-BAL21's restate door a typed figure lands
        OWN on the row with the flag beside it (developer 2026-09-13), and
        the flag is what keeps this pass off it: `$99.00` typed survives a
        category change on the definition.
        """
        with app.app_context():
            template, row = self._rule_less_with_a_row(seed_user, seed_periods)
            repriced_by_the_owner(row, "99.00")
            db.session.commit()
            groceries = seed_user["categories"]["Groceries"]

            resp = self._edit(
                auth_client, template, seed_periods,
                category_id=str(groceries.id),
            )
            assert resp.status_code == 200

            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.estimated_amount == Decimal("99.00")
            assert row.amount_source_id is None
            assert row.category_id != groceries.id


# ── Transfer templates ───────────────────────────────────────────────


class TestClearingATransferTemplatesRecurrence:
    """POST /transfers/<id> with an empty recurrence pattern.

    The transfer form has no null option until plan step R2e-3 retires the
    ``Once`` pattern, but the route path it will use is shared with the
    transaction form and is live now -- so it is pinned here rather than
    discovered by R2e-3.
    """

    def test_the_rule_is_deleted_and_the_future_transfers_are_swept(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The rule row is gone and only transfers before the cut survive.

        Same contract as the transaction half, through the transfer service.
        It read each survivor's two shadow transactions, and counted the eight
        left, until plan step ``balance:X-bi-6-4d-2``: a transfer stores no
        leg row since migration 82f7 (ruling **R-BAL167** class 1).
        """
        savings = _savings_account(seed_user)
        template = _recurring_transfer_template(seed_user, savings)
        rule_id = template.recurrence_rule.id
        rows = db.session.query(Transfer).filter_by(
            transfer_template_id=template.id,
        ).all()
        assert _period_indices(rows, seed_periods) == list(range(10))

        resp = auth_client.post(f"/transfers/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        template = db.session.get(TransferTemplate, template.id)
        assert template.recurrence_rule is None
        assert db.session.get(RecurrenceRule, rule_id) is None

        survivors = db.session.query(Transfer).filter_by(
            transfer_template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == [0, 1, 2, 3]

        # The survivors are LIVE, asserted positively.  The period subset above
        # would pass against a sweep that soft-deleted the transfers before
        # the cut too, which keeps every row and hides it.
        assert all(xfer.is_deleted is False for xfer in survivors)

    def test_a_loan_payment_refuses_to_become_one_time(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A loan payment's cadence cannot be cleared, and the reason is money.

        ``recurring_transfer_query.active_recurring_transfer_template`` finds a
        loan's payment by whether a rule names it, and the balance
        seam threads that template's ``extra_principal`` into every projected
        loan trajectory.  Clearing the rule nulls the column, so the standing
        overpayment silently drops to zero and the projected payoff moves --
        while the ``loan_payment_settings`` row goes on asserting it.  Measured
        before the refusal: 250.00 -> 0.00 with the settings row unchanged.
        """
        loan = create_loan_account(seed_user, db.session)
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="Mortgage payment",
            default_amount=Decimal("1000.00"),
        )
        template.settings = LoanPaymentSettings(
            extra_principal=Decimal("250.00"),
        )
        db.session.add(template)
        db.session.flush()
        # Its definition STATES a price, as every app-side create door
        # does: since plan step X-au-f a generated transfer stores no
        # figure and reads this series on its own due date.
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule_id = _every_period_rule(template).id
        db.session.commit()
        assert recurring_transfer_query.loan_payment_config(
            template,
        )[1] == Decimal("250.00")

        resp = auth_client.post(f"/transfers/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"cannot be made" in resp.data

        db.session.expire_all()
        assert db.session.get(
            TransferTemplate, template.id,
        ).recurrence_rule.id == rule_id
        assert db.session.get(RecurrenceRule, rule_id) is not None
        assert recurring_transfer_query.loan_payment_config(
            db.session.get(TransferTemplate, template.id),
        )[1] == Decimal("250.00")

    def test_a_rule_less_transfer_templates_single_transfer_survives_a_rename(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The one-time transfer shape plan step R2e-3 SHIPPED is safe.

        A transfer template with no rule and exactly one materialised Transfer
        is what "one-time transfer" became when ``Once`` was retired.  An
        unrelated edit must leave that Transfer alone.  Built here through the
        service; ``test_transfers/test_one_time.py`` covers the same property end-to-end
        through the create form, which is what now produces this shape.
        """
        savings = _savings_account(seed_user)
        template = _recurring_transfer_template(seed_user, savings, recurs=False)
        # The transfer of a definition that no longer repeats: the engine's
        # transfer under a cadence, then the cadence cleared the way the edit
        # door clears one -- the transaction twin's shape above (plan step
        # balance:X-ch).  A hand-built one OWNED its figure, which the
        # one-time branch of ``routes/transfers/_instances`` has not written
        # since plan step X-au-f: that transfer is derived, like this one.
        _every_period_rule(template)
        xfer = generate_transfer_of(template, seed_periods[6])
        template.recurrence_rule = None
        db.session.commit()
        assert template.recurrence_rule is None
        xfer_id = xfer.id

        resp = auth_client.post(f"/transfers/{template.id}", data={
            "name": "Sweep to savings (renamed)",
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        assert db.session.get(Transfer, xfer_id) is not None
