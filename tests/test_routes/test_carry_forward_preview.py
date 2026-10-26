"""
Shekel Budget App -- Carry-Forward Preview Modal Route Tests

Phase 5 of ``docs/historical/carry-forward-aftermath-implementation-plan.md``
introduces a pre-flight confirmation modal: clicking "Carry Fwd" on
a past period now opens
``GET /pay-periods/<id>/carry-forward-preview`` instead of POSTing
the mutation directly.  The user reviews the planned actions and
confirms via the modal's button, which then posts to the existing
``carry_forward`` endpoint.

These tests exercise the route layer:

  * Returns 200 + rendered modal HTML for the user's own period.
  * Lists every source row with a kind label (envelope / discrete /
    transfer).
  * Disables the Confirm button when an envelope target is blocked --
    now only the corrupt doubled-row (ambiguous) state; the formerly
    blocking cases (finalised, inactive, soft-deleted) are actionable.
  * Returns 404 for unknown periods and other-user periods
    (security response rule).
  * Returns 400 when the user has no current period or no baseline
    scenario (mirrors the existing carry_forward POST).
  * Companion users are blocked by ``@require_owner``.

The service-layer tests in
``tests/test_services/test_carry_forward_service/test_the_preview.py``
and ``test_the_preview_agrees.py`` (``TestPreviewCarryForward*``) cover
the data correctness; this file focuses on HTTP-status, rendering, and
security.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, Status
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import pay_period_write, transfer_service
from app.services.pay_calendar import calendar_for
from app.services.row_valuation import settled_figure
from app.utils.dates import display_today
from app.services import account_service
from tests._test_helpers import (
    figure_source_columns,
    current_pay_period,
    generate_row_of,
    last_covered_day,
    make_expense_template,
    moved_by_the_owner,
    resolved_amount,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_envelope_template(
    seed_user, *, name="Envelope Spending", default_amount="100.00",
    category_key="Groceries",
):
    """Create a priced envelope expense definition on an every-paycheck rule.

    Its rows come from the engine (:func:`generate_row_of`, plan step
    balance:X-cf-4), which is what the preview under test walks.
    """
    return make_expense_template(
        db.session, seed_user, amount=default_amount, name=name,
        category_key=category_key, is_envelope=True,
    )


def _add_entry(txn, seed_user, amount):
    """Attach a debit entry to *txn*."""
    db.session.add(TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="Test purchase",
        purchased_on=date(2026, 1, 5),
    ))
    db.session.flush()


def _make_discrete_template(seed_user, *, name="Recurring Bill",
                            amount="500.00", category_key="Rent"):
    """Create a priced discrete (is_envelope=False) expense definition.

    Every-paycheck like the envelope one: the preview sorts a row into the
    discrete bucket on ``tracks_purchases`` alone, never on its cadence.
    """
    return make_expense_template(
        db.session, seed_user, amount=amount, name=name,
        category_key=category_key,
    )


def _make_savings_account(seed_user):
    """Create a Savings account for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="CFP Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _make_transfer(seed_user, period):
    """Create a transfer in *period*; returns the parent Transfer."""
    savings = _make_savings_account(seed_user)
    projected = db.session.query(Status).filter_by(name="Projected").one()
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("75.00")),
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="CFP Transfer",
        ),
    )


def _login_companion(app):
    """Authenticate a companion test client."""
    comp = app.test_client()
    resp = comp.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302
    return comp


# ── Status code + content tests ──────────────────────────────────────


class TestCarryForwardPreviewSuccess:
    """Happy-path response: 200 + rendered modal partial."""

    def test_returns_200_with_modal_html_for_owned_period(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """GET preview returns 200 and a renderable modal partial.

        The response body must contain the marker attribute used by
        ``app.js`` (``data-modal-auto-show``) so the modal opens
        itself after HTMX swaps it in -- without this attribute the
        modal would render but never appear.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            generate_row_of(template, seed_periods_today[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Modal markers required for the JS auto-show pattern.
            assert "data-modal-auto-show" in html
            assert "carryForwardPreviewModal" in html
            # Source row appears in the modal.
            assert template.name in html
            # Confirm button posts to the existing carry_forward route.
            assert (
                f'/pay-periods/{seed_periods_today[0].id}/carry-forward'
                in html
            )

    def test_envelope_row_labeled_as_envelope(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Envelope source row gets the 'Envelope' badge in the modal.

        The badge tells the user the row will settle-and-roll instead
        of move-whole.  Asserting on the visible text keeps the test
        from coupling to the exact CSS classes used.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            generate_row_of(template, seed_periods_today[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert ">Envelope<" in html, (
                "Envelope rows must be visibly labeled in the modal."
            )

    def test_discrete_row_labeled_as_discrete(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Discrete source row gets the 'Discrete' badge."""
        with app.app_context():
            template = _make_discrete_template(seed_user)
            generate_row_of(template, seed_periods_today[0])
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert ">Discrete<" in html

    def test_transfer_row_labeled_as_transfer(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Transfer source row gets the 'Transfer' badge."""
        with app.app_context():
            _make_transfer(seed_user, seed_periods_today[0])
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert ">Transfer<" in html

    def test_summary_line_includes_counts(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Modal summary line names the per-kind action counts.

        The count words ("envelope", "discrete", "transfer") must
        appear so the user can quickly see the batch composition
        before confirming.
        """
        with app.app_context():
            envelope_t = _make_envelope_template(
                seed_user, category_key="Groceries",
            )
            envelope_source = generate_row_of(envelope_t, seed_periods_today[0])
            generate_row_of(envelope_t, seed_periods_today[1])
            _add_entry(envelope_source, seed_user, "20.00")

            discrete_t = _make_discrete_template(seed_user)
            generate_row_of(discrete_t, seed_periods_today[0])

            _make_transfer(seed_user, seed_periods_today[0])
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            # Counts text appears in the summary.
            assert "envelope" in html.lower()
            assert "discrete" in html.lower()
            assert "transfer" in html.lower()


# ── Confirm-button gating ────────────────────────────────────────────


class TestCarryForwardPreviewConfirmGating:
    """Confirm button enabled vs disabled by preview state."""

    def test_confirm_enabled_when_all_actionable(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """All-actionable preview: Confirm button is NOT disabled.

        ``hx-disabled-elt`` is unrelated to the HTML disabled
        attribute -- HTMX uses it to grey out the button while the
        request is in flight.  Use ``aria-disabled="true"`` (which
        only renders when the modal's gating condition fires) as
        the canonical marker so the assertion does not false-match
        on the HTMX attribute name.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            generate_row_of(template, seed_periods_today[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert "data-carry-forward-confirm" in html
            assert 'aria-disabled="true"' not in html, (
                "Actionable preview must NOT mark Confirm as disabled."
            )

    def test_confirm_disabled_when_envelope_target_ambiguous(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Ambiguous doubled-row target -> Confirm is disabled.

        The only remaining block is a target holding two mutable rows for
        one template+period that the leftover cannot be handed to honestly
        -- here the paycheck's own canonical beside a sibling the owner
        moved in, which ``_leftover_recipient`` refuses on its
        ``is_override`` rule.  Blocked rows propagate to the Confirm button
        so the user must resolve them manually before retrying; the
        disabled attribute prevents HTMX from posting the mutation that
        would just refuse anyway.

        The target rows must live in the period the route resolves as
        ``current_period`` -- the paycheck CONTAINING the owner's day --
        because placing them in any other period leaves the actual target
        period empty and the branch creates a row, producing an
        actionable plan that misses the assertion.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            current_period = current_pay_period(
                seed_user["user"].id,
            )
            assert current_period is not None
            # Two mutable rows in the target -> AMBIGUOUS: the paycheck's own
            # canonical, and a sibling the owner moved in from the paycheck
            # after it -- the way an override comes to stand beside one.
            generate_row_of(template, current_period)
            following = next(
                p for p in seed_periods_today
                if p.start_date > current_period.start_date
            )
            moved_by_the_owner(
                generate_row_of(template, following), into=current_period,
            )
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert "data-carry-forward-confirm" in html
            assert 'aria-disabled="true"' in html, (
                "Blocked envelope row must disable the Confirm button."
            )
            # And the blocked alert appears in the body.
            assert "blocked" in html.lower()

    @pytest.mark.usefixtures("db", "seed_user")
    def test_confirm_disabled_when_period_is_empty(
        self, app, auth_client, seed_periods_today,
    ):
        """Empty source period: Confirm has nothing to do, disabled.

        The modal still renders so the user sees "no items to carry
        forward"; Confirm is disabled to prevent a no-op POST.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert "data-carry-forward-confirm" in html
            assert 'aria-disabled="true"' in html


# ── Security ─────────────────────────────────────────────────────────


class TestCarryForwardPreviewSecurity:
    """404 for unowned/missing periods; companion blocked."""

    @pytest.mark.usefixtures(
        "db", "seed_user", "seed_periods_today", "seed_second_user",
    )
    def test_returns_404_for_unowned_period(
        self, app, auth_client, seed_second_periods,
    ):
        """Cross-user period -> 404 (security response rule).

        Mirrors the POST carry_forward route's behaviour to avoid
        leaking which periods exist for other users.
        """
        with app.app_context():
            other_period_id = seed_second_periods[0].id

            resp = auth_client.get(
                f"/pay-periods/{other_period_id}/carry-forward-preview"
            )
            assert resp.status_code == 404

    @pytest.mark.usefixtures("db", "seed_user", "seed_periods_today")
    def test_returns_404_for_missing_period(self, app, auth_client):
        """Unknown period_id -> 404."""
        with app.app_context():
            resp = auth_client.get(
                "/pay-periods/9999999/carry-forward-preview"
            )
            assert resp.status_code == 404

    @pytest.mark.usefixtures("db", "seed_user", "seed_companion")
    def test_companion_blocked(self, app, seed_periods_today):
        """Companion gets 404 from @require_owner.

        The preview endpoint is not relevant to companions and
        leaking even read-only carry-forward data could expose
        envelope-tracked spending the owner has not chosen to
        share.  The decorator returns the same 404 the POST route
        does so the two are consistent.
        """
        comp = _login_companion(app)
        resp = comp.get(
            f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
        )
        assert resp.status_code == 404


# ── Configuration errors ─────────────────────────────────────────────


class TestCarryForwardPreviewConfiguration:
    """Mirrors the POST route's configuration checks."""

    def test_returns_400_when_no_baseline_scenario(
        self, app, db, seed_user, seed_periods_today, auth_client,
    ):
        """No baseline scenario -> 400 (matches POST route's check).

        Carry-forward is scoped to the baseline scenario; without
        one there is no defined target for the operation.  The
        message is the same as the POST route's so the modal
        could display it cleanly if rendered (in practice the UI
        only ever calls preview when a scenario exists, but the
        defensive check makes both routes safe).
        """
        with app.app_context():
            # Remove the baseline scenario set up by the seed.
            scenario = (
                db.session.query(Scenario)
                .filter_by(user_id=seed_user["user"].id, is_baseline=True)
                .one()
            )
            scenario.is_baseline = False
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 400
            assert b"baseline scenario" in resp.data.lower()

    def test_returns_400_when_no_current_period(
        self, app, db, seed_user, auth_client, seed_periods_today,
    ):
        """No current period -> 400 (matches POST route's check).

        **It now REACHES that state instead of stubbing it** (plan step
        C2-f3a).  This patched ``pay_period_service.get_current_period`` to
        return ``None``, and said so: "so the test does not have to construct a
        non-overlapping period range".  That reader is deleted, and the stub
        was the weaker grade anyway -- it asserts the route branches on
        whatever the producer says, where the question is whether the PRODUCER
        answers ``None`` for an owner whose schedule has run out.

        So the schedule really runs out: every period from the one containing
        today onward is retired through ``pay_period_write``, the one door in
        ``app/`` that removes a pay period, leaving a calendar whose horizon is
        in the past.  The SOURCE period the route is asked about
        (``seed_periods_today[0]``) survives, so the 400 is the missing TARGET
        and not a 404 for the source.

        **Freezing the clock forward was tried first and does not work here**:
        the session's idle bound is measured against the same ``datetime.now``
        the freeze moves, so a client authenticated before the jump is logged
        out and the route answers 302 rather than 400.
        """
        with app.app_context():
            periods = (
                db.session.query(PayPeriod)
                .filter_by(user_id=seed_user["user"].id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            today = display_today()
            doomed = [p for p in periods if last_covered_day(p) >= today]
            assert doomed, "the fixture must cover today for this to remove it"
            pay_period_write.retire_paydays(
                seed_user["user"].id, {period.id for period in doomed},
            )
            db.session.commit()
            assert calendar_for(
                seed_user["user"].id,
            ).period_containing(today) is None, (
                "the retire left a period still covering today, so the 400 "
                "below would not be the branch this case is about"
            )

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 400
            assert b"current period" in resp.data.lower()


# ── Read-only invariant ──────────────────────────────────────────────


class TestCarryForwardPreviewReadOnly:
    """Repeated GETs do not mutate any state."""

    def test_repeated_preview_does_not_change_database(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Calling the preview twice produces identical state -- proof of read-only.

        The whole point of Phase 5 is to let users review before
        committing.  If preview mutated, repeated clicks would alter
        the very state the user is trying to inspect.  Catch that
        by snapshotting transaction state, calling preview twice,
        and asserting equality after each call.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            target = generate_row_of(template, seed_periods_today[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            def _snapshot():
                """Capture relevant state for the test fixture."""
                db.session.expire_all()
                src = db.session.get(Transaction, source.id)
                tgt = db.session.get(Transaction, target.id)
                return (
                    src.status_id, settled_figure(src), src.settled_on,
                    src.estimated_amount, src.is_override,
                    tgt.estimated_amount, tgt.is_override,
                    tgt.status_id,
                )

            before = _snapshot()
            for _ in range(3):
                resp = auth_client.get(
                    f"/pay-periods/{seed_periods_today[0].id}"
                    f"/carry-forward-preview"
                )
                assert resp.status_code == 200
            after = _snapshot()

            assert before == after, (
                "preview must be read-only -- repeated GETs cannot "
                "mutate transaction state."
            )

    def test_preview_does_not_generate_canonical_for_missing_target(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Engine-would-generate case must not actually generate.

        Specifically guards against the trap where the preview's
        ``can_generate_in_period`` check could be misimplemented as a
        ``generate_for_template`` call -- that would create the
        canonical at preview time rather than confirm time, breaking
        the read-only contract.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            assert (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods_today[1].id,
                ).count()
                == 0
            )

            resp = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 200

            assert (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods_today[1].id,
                ).count()
                == 0
            ), (
                "Preview must not trigger the recurrence engine; "
                "canonical generation should happen only on confirm."
            )


# ── End-to-end through both endpoints ────────────────────────────────


class TestCarryForwardPreviewEndToEnd:
    """Preview -> Confirm POST flow produces the predicted state."""

    def test_preview_then_confirm_runs_carry_forward(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """GET preview, then POST carry_forward, sees expected state.

        Two-step flow: the GET preview must not mutate, the POST
        confirm must execute the same plan the preview described.
        Asserts on the final state matches the preview's prediction.

        Note on the target period: both routes resolve it via
        ``current_pay_period(user_id)`` so the
        test must place its target row in the period that contains
        the test-runner's "today".  Pre-creating the canonical in
        a hard-coded index would be wrong -- the route ignores it
        and uses the current period instead.  Letting the engine
        auto-generate the canonical at confirm time is cleaner and
        matches the realistic flow (the user clicks Carry Fwd from
        a past period; current_period may not yet have the row).
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods_today[0])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            # The route resolves the target as the period containing the
            # owner's day; this reads it the same way.
            current_period = current_pay_period(
                seed_user["user"].id,
            )
            assert current_period is not None, (
                "Test fixture must include the current period."
            )

            # Step 1: preview.
            resp_preview = auth_client.get(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward-preview"
            )
            assert resp_preview.status_code == 200

            # Step 2: confirm POST -- the route the modal's button
            # would hit when the user clicks Confirm.
            resp_confirm = auth_client.post(
                f"/pay-periods/{seed_periods_today[0].id}/carry-forward"
            )
            assert resp_confirm.status_code == 200
            assert resp_confirm.headers.get("HX-Trigger") == "gridRefresh"

            # The state matches the preview's prediction.
            db.session.refresh(source)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source.status_id == done_id
            assert settled_figure(source) == Decimal("65.00")

            # Engine auto-generated the canonical at the current
            # period's default ($100) and the carry-forward bumped
            # it by the leftover (35) -> 135.
            target = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=current_period.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).one()
            )
            assert resolved_amount(target) == Decimal("135.00")
            assert target.is_override is True
