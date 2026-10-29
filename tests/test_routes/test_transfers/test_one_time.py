"""Transfer route tests: creating a transfer that does not repeat, through the template form."""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services import transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    cadence_payload,
    override_anchor,
    transfer_side_figure,
    transfer_side_record,
    typed,
)
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import transfer_amount

from ._helpers import _create_savings_account


# ── One-Time Transfer Tests ────────────────────────────────────────────


class TestOneTimeTransfer:
    """Creating a transfer that does NOT repeat, via the template form.

    Its shape is ``recurrence_rule_id IS NULL`` -- the same one a
    non-repeating transaction template has always used -- since plan step
    R2e-3 retired the ``Once`` PATTERN that was the second spelling.  It is
    also the create form's DEFAULT selection, so this is the ordinary path,
    not an edge.  Unlike a rule-less TRANSACTION template, which generates
    nothing, this materialises exactly one Transfer in the chosen pay
    period.

    **Every case here 500'd before R2e-3** (defect **D13**): the route
    dereferenced ``rule.id`` with no null branch, on a comment claiming the
    schema made the recurrence field required.  It does not -- the field is
    ``allow_none`` -- so both the empty and the absent spelling raised
    ``AttributeError``.  ``test_absent_cadence_is_the_same_path`` covers the
    second spelling, which no form emits but a client may.

    The field is ``recurrence_unit`` since plan step R7b-2, which renamed what
    "does not repeat" is spelled as without changing what it MEANS: an empty
    unit, kept as a present ``None`` by ``_normalize_empty_inputs``.
    """

    def test_non_repeating_creates_one_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers with an EMPTY pattern creates a rule-less template
        AND a single Transfer.

        Named ``..._and_two_shadows`` until plan step
        ``balance:X-bi-6-4d-2``, which births no shadow: its count of two
        and their expense / income types went, the sides being the
        transfer's projection by endpoint (``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer``; ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "Once Payment",
                "default_amount": "500.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_unit": "",
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data.lower()

            # Template was created with NO recurrence rule.
            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="Once Payment",
                )
                .one()
            )
            assert tmpl.recurrence_rule is None
            assert tmpl.recurrence_rule is None

            # Transfer was created via the service.
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            assert transfer_amount(xfer) == Decimal("500.00")
            assert xfer.pay_period_id == seed_periods_today[1].id
            # The due date is the period's own start.  A ``Once`` rule used to
            # supply it through ``compute_due_date``, which returned exactly
            # this for a day-less rule -- verified against both live ``Once``
            # transfers on production.
            assert xfer.due_date == seed_periods_today[1].start_date

    def test_non_repeating_transfer_accounts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-repeating transfer runs from the form's from-account to its to-account.

        Re-read from the database.  Named ``test_non_repeating_shadow_accounts``
        until plan step ``balance:X-bi-6-4d-2``, which births no shadow: it
        read the expense twin's account (the from-account) and the income
        twin's (the to-account), which are the transfer's two endpoint
        columns read here (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            checking_id = seed_user["account"].id

            auth_client.post("/transfers", data={
                "name": "Account Check",
                "default_amount": "300.00",
                "from_account_id": str(checking_id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "start_period_id": str(seed_periods_today[0].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            db.session.expire_all()
            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(name="Account Check")
                .one()
            )
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )

            # The money leaves checking and lands in savings.
            assert (xfer.from_account_id, xfer.to_account_id) == (
                checking_id, savings.id,
            )

    def test_non_repeating_balance_impact(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-repeating transfer moves both accounts' balances.

        The checking balance should decrease and savings balance should
        increase by the transfer amount.  Each balance reads the transfer's
        LEG on its account (Transfer Invariant 5).  The case also asserted a
        shadow existed on each account until plan step
        ``balance:X-bi-6-4d-2``, which births none; the transfer the create
        placed is asserted instead (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  The two balances are unchanged.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Asserted rather than assigned (ruling R-EH deleted the column).
            override_anchor(
                db.session, savings, seed_periods_today[0], Decimal("0.00"),
            )
            db.session.commit()

            auth_client.post("/transfers", data={
                "name": "Balance Test",
                "default_amount": "250.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            # The create placed the one transfer the balances below read.
            placed = (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id, is_deleted=False)
                .one()
            )
            assert (placed.pay_period_id, transfer_amount(placed)) == (
                seed_periods_today[1].id, Decimal("250.00"),
            )
            # ``as_of`` is pinned inside period 0: the transfer lands in period
            # 1, which is in the PAST relative to this fixture's today, and
            # ruling R-G clamps a still-Projected row forward past a reader's
            # own now rather than letting it sit in a period that has gone by.
            ctx = BalanceContext.build(
                seed_user["user"].id,
                as_of=seed_periods_today[0].start_date,
            )
            checking_balances = balance_at.cash_balance_map(
                seed_user["account"], ctx,
            )
            # Checking decreased by 250 in period 2.
            assert checking_balances[seed_periods_today[1].id] == Decimal("750.00")

            savings_balances = balance_at.cash_balance_map(
                savings, ctx,
            )
            # Savings increased by 250 in period 2.
            assert savings_balances[seed_periods_today[1].id] == Decimal("250.00")

    def test_one_time_transfer_idor_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /transfers with another user's period is rejected.

        The cross-user start_period is caught by the F-24 builder's universal
        ownership probe (deep-quality-hunt #21), which plan step R2e-3 moved
        AHEAD of that helper's no-pattern early return.  It had sat below it,
        so a submission naming no pattern skipped the probe entirely -- which
        was harmless while no-pattern meant "generate nothing", and is not
        once no-pattern is what materialises a Transfer into exactly the
        submitted period.  ``_materialize_one_time_transfer`` re-checked as
        defence in depth ("Invalid pay period for one-time transfer.") until
        plan step ``pay_calendar:C13-b``, which deleted that second resolution
        and threads the route's own down instead; the FIRST guard is the only
        one now, and this asserts it fires before any row is written.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "IDOR Attempt",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
                # Use second user's period.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid start period" in response.data

            # Neither a template nor a transfer was persisted.
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
            assert (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0

    def test_absent_cadence_is_the_same_path(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A POST that OMITS ``recurrence_unit`` behaves identically.

        The form always submits the key (empty for "Does not repeat"), but a
        client need not.  Both spellings reached the same unguarded
        ``rule.id`` before plan step R2e-3 and both 500'd; both must now
        produce the same rule-less template and its single Transfer.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "No Pattern Key",
                "default_amount": "425.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="No Pattern Key")
                .one()
            )
            assert tmpl.recurrence_rule is None
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            assert transfer_amount(xfer) == Decimal("425.00")
            assert xfer.pay_period_id == seed_periods_today[1].id

    def test_non_repeating_without_a_period_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-repeating transfer naming no pay period writes nothing.

        It has nowhere to land, and reporting success would state that money
        moved when no Transfer and no shadow pair exist.  Before plan step
        R2e-3 the ``Once`` path took exactly that branch silently: the
        materialization required BOTH the pattern and a period, so without one
        it created the template, no transfer, and flashed "created."
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "Nowhere To Land",
                "default_amount": "75.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"has to land in one pay period" in response.data
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
            assert (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0

    def _create_non_repeating(
        self, auth_client, seed_user, savings, period, *, name, amount="500.00",
    ):
        """POST the create form's default (non-repeating) selection."""
        auth_client.post("/transfers", data={
            "name": name,
            "default_amount": amount,
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(savings.id),
            "recurrence_unit": "",
            "start_period_id": str(period.id),
            "category_id": str(seed_user["categories"]["Rent"].id),
        }, follow_redirects=True)
        return (
            db.session.query(TransferTemplate)
            .filter_by(user_id=seed_user["user"].id, name=name)
            .one()
        )

    def test_changing_accounts_on_a_non_repeating_transfer_moves_the_pair(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An account change reaches the Transfer's two endpoints.

        **RE-RULED at plan step R10-b** (developer decision, 2026-08-20).  This
        case asserted a REFUSAL -- "cannot be moved between accounts" -- and
        that refusal existed for one reason: ``transfer_service.update_transfer``
        could not move a transfer between accounts, so the propagation door
        carried amount, name and category and nothing else.  It was a limit of
        the door, not a rule about transfers: a RECURRING template with the
        identical edit had it applied, by a regeneration that destroyed and
        rebuilt every generated row to do it.  The door moves a pair now
        (``transfer_service._endpoints``), so the edit applies here too and one
        edit stops meaning two different things.

        It asserted all three rows until plan step ``balance:X-bi-6-4d-2``,
        which births no shadow: each side is the transfer's projection by
        endpoint now (``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer``), and a side's display NAME is
        composed from the endpoints at read time (its label is pinned by
        ``TestALegsDisplayProjections`` there), so the transfer's two
        endpoint columns, re-read, are what this asserts (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = Account(
                user_id=seed_user["user"].id, name="Other Savings",
                account_type_id=savings.account_type_id, is_active=True,
            )
            db.session.add(other)
            db.session.commit()
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Fixed Accounts",
            )

            resp = auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Fixed Accounts",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(other.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            tmpl = db.session.get(TransferTemplate, tmpl.id)
            assert tmpl.to_account_id == other.id
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            assert xfer.to_account_id == other.id
            assert xfer.from_account_id == seed_user["account"].id

    def test_a_non_repeating_transfer_holding_a_record_is_retained_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The rule-less path asks the same question the recurring one does.

        **Found by an adversarial review of plan step R10-b.**  Removing the
        "cannot be moved between accounts" refusal made the two account columns
        propagate here -- and this door applied the definition unconditionally,
        so a non-repeating transfer carrying a settlement record retained
        through a revert had its pair moved in SILENCE, while the identical edit
        on a RECURRING template was retained and reported.  That is the very
        inconsistency the step removed, re-created in the opposite direction.

        The record is the one ``status_seam`` keeps when the owner reverts in
        order to edit, beside the figure the TRANSFER keeps (its one home since
        plan step ``balance:X-bi-6-4d-2``'s 4d-4 half, ruling **R-BAL366**;
        each record carried a copy until then): a figure read off the OLD
        destination's statement, which re-pointing the pair would re-file
        against an account nobody asserted it on.  Each side's kept record
        hangs off the transfer since plan step ``balance:X-bi-6-4d-2`` and is
        read there, not off the twins (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  It also counted
        the twins on the old destination until plan step
        ``balance:X-bi-6-4d-2``, which births none; the transfer's own
        destination column, re-read, says where the side is (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = Account(
                user_id=seed_user["user"].id, name="Other Savings",
                account_type_id=savings.account_type_id, is_active=True,
            )
            db.session.add(other)
            db.session.commit()
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Has A Record",
            )
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id, submitted=typed(Decimal("412.90")),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            tmpl_id, xfer_id, other_id = tmpl.id, xfer.id, other.id
            savings_id = savings.id

            resp = auth_client.post(f"/transfers/{tmpl_id}", data={
                "name": "Has A Record",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(other_id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"kept the value it already had" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tmpl_id,
            ).to_account_id == other_id, "the definition itself still moves"
            held = db.session.get(Transfer, xfer_id)
            assert held.to_account_id == savings_id, (
                "the row carrying a recorded figure was re-filed anyway"
            )
            assert all(
                transfer_side_record(db.session, xfer_id, account_id) is not None
                for account_id in (held.from_account_id, held.to_account_id)
            ), "the retained record on the reverted pair"
            assert held.settled_figure == Decimal("412.90"), (
                "the retained figure on the reverted pair"
            )

    def test_changing_accounts_is_allowed_with_no_live_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rule-less template with NO live Transfer re-points cleanly.

        The propagation loop has nothing to iterate here -- a template whose
        recurrence was CLEARED keeps no upcoming Transfer -- so the edit must
        land on the template alone and raise nothing.  It is the empty half of
        the case above, and it is the half that would break if the propagation
        ever assumed a row was there to write.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = Account(
                user_id=seed_user["user"].id, name="Other Savings",
                account_type_id=savings.account_type_id, is_active=True,
            )
            db.session.add(other)
            db.session.commit()
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Repointable",
            )
            # Remove the Transfer, leaving the rule-less template with none.
            for xfer in db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id,
            ).all():
                transfer_service.delete_transfer(
                    xfer.id, seed_user["user"].id, soft=False,
                )
            db.session.commit()

            resp = auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Repointable",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(other.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tmpl.id,
            ).to_account_id == other.id
            # The propagation had nothing to write and said nothing about it:
            # no retained notice, and no Transfer came back.
            assert b"kept the value it already had" not in resp.data
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id,
            ).count() == 0

    def test_a_settled_transfer_does_not_follow_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Propagation never rewrites immutable history.

        A Paid transfer carries posting-ledger entries and real money that
        already moved; the same rule
        ``_recurrence_common.classify_maintain_work`` applies to every
        recurring template's regeneration applies here.

        **What "history" IS moved one column at plan step X-au-f**, and the
        assertion follows it.  This case asserted the transfer's stored
        ``amount`` had not moved, which was history only while that column was
        where a settled transfer's figure lived.  A settled transfer is declared
        like any other now (ruling **R-JB**, the same rule the transaction
        cutover took), so its PLAN reads its definition's current price -- and
        what MOVED is recorded on its two sides, in each side's payment record,
        which is the thing that must not change and the thing asserted below.
        No balance reader is affected either way:
        ``row_valuation.fixed_contribution`` of a settled side's parent
        answers from its record before any producer runs.  The record hangs off the
        transfer since plan step ``balance:X-bi-6-4d-2`` and is read there
        rather than off the twins (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2); their count of two went at
        ``balance:X-bi-6-4d-2``, which births none (ruling R-BAL167 class 1).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Already Paid",
            )
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            xfer_id = xfer.id

            auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Already Paid",
                "default_amount": "900.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tmpl.id,
            ).default_amount == Decimal("900.00")
            # The PLAN follows the definition ...
            assert transfer_amount(
                db.session.get(Transfer, xfer_id),
            ) == Decimal("900.00")
            # ... and what MOVED is untouched, which is the immutability this
            # case is named for.
            held = db.session.get(Transfer, xfer_id)
            assert {
                transfer_side_figure(db.session, xfer_id, account_id)
                for account_id in (held.from_account_id, held.to_account_id)
            } == {Decimal("500.00")}

    def test_a_hand_edited_transfer_does_not_follow_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An override is a deliberate per-instance choice and is preserved.

        The same partition the regeneration sweep uses: an overridden row is
        the user's, not the template's.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Hand Edited",
            )
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("123.45")),  is_override=True,
            )
            db.session.commit()
            xfer_id = xfer.id

            auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Hand Edited",
                "default_amount": "900.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            db.session.expire_all()
            assert db.session.get(Transfer, xfer_id).amount == Decimal("123.45")

    def test_renaming_a_non_repeating_transfer_keeps_its_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Defect **D16**: a rename must not destroy the single Transfer.

        A ``Once``-ruled transfer template DID reach the regeneration sweep on
        any edit, because it named a rule: the sweep hard-deleted the
        projected row and the pattern's own suppression guard then generated
        nothing back.  Measured at HEAD before plan step R2e-3, with the
        transfer in a FUTURE period so it fell inside the sweep window:
        1 transfer + 2 shadows -> 0 + 0.  A rule-less template is skipped by
        ``regenerate_or_conflict_chooser``'s "neither has nor had a rule"
        gate, which is what closes it.

        The period must be in the future: ``query_rows_from_effective_date``
        bounds the sweep at ``PayPeriod.end_date >= effective_from``, so a
        past-period transfer survives a rename for an unrelated reason and
        would pass this test against the broken code.

        It also counted the transfer's two shadows before and after, and read
        their amounts, until plan step ``balance:X-bi-6-4d-2``, which births
        none; the surviving transfer's id, name and price are read instead
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            future = [
                p for p in seed_periods_today
                if p.start_date > display_today()
            ]
            assert future, "fixture must materialise a future pay period"

            auth_client.post("/transfers", data={
                "name": "Rename Me",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "start_period_id": str(future[0].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Rename Me")
                .one()
            )
            before = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id).one()
            )

            auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Renamed",
                "default_amount": "650.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            db.session.expire_all()
            after = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id).all()
            )
            assert len(after) == 1, "the rename destroyed the transfer (D16)"
            assert after[0].id == before.id

            # SURVIVING is only half of it: the edit must also REACH the row.
            # Asserting only that the id still exists passes against a route
            # that silently ignores the edit -- measured before
            # ``propagate_to_non_repeating_transfers``, this same edit left the
            # template at $650.00 while the Transfer and both shadows stayed at
            # $500.00, under a plain "updated." flash.
            assert after[0].name == "Renamed"
            assert transfer_amount(after[0]) == Decimal("650.00")

    def test_recurring_transfer_idor_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /transfers rejects a foreign start_period on a RECURRING pattern.

        deep-quality-hunt #21/#24: before the universal probe, the
        start_period ownership check ran ONLY for EVERY_N_PERIODS, so a
        recurring pattern (here "Every Period") persisted a foreign
        ``start_period_id`` unchecked -- and ``recurrence_engine`` then
        read that victim period's ``start_date`` as the generation
        boundary.

        **The probe MOVED at plan step R7b-4 and this test is now its
        primary coverage.**  It sat in the kind-agnostic F-24 builder
        (``recurrence_spec_from_form``) because that ``<select>`` was
        also the recurrence's "First paycheck"; the recurrence takes a DATE
        now, so the field has one job -- which period a NON-repeating transfer
        lands in -- and ``create_transfer_template`` owner-checks it before
        anything is written.  The transaction-template twin of this case is
        gone with the surface: that schema no longer declares the field at all
        (``test_templates.py::test_create_recurring_template_ignores_a_foreign_start_period``).

        This POST names a RECURRING cadence, which is the shape that matters:
        the recurrence has no use for the period, so the check must still run
        rather than being skipped as irrelevant.  A regression that only
        checked the non-repeating branch would reopen the IDOR here.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            response = auth_client.post("/transfers", data={
                "name": "Recurring IDOR Attempt",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                **cadence_payload(),
                # Second user's period on a recurring (non-EVERY_N) pattern.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid start period" in response.data

            # Neither a template nor a transfer was persisted.
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
            assert (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
