"""Loan route tests: payment drift between a loan's terms and its recurring transfer.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.models.escrow_line import EscrowComponentVersion
from app.services.balance_at import BalanceContext
from app.services import balance_at, escrow_calculator, loan_loaders
from app.services.obligations_aggregator import todays_price

from tests._test_helpers import add_escrow_line, make_cadence_rule, state_template_price
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import _payment_template, _create_mortgage
from tests._definition_price import newest_price


def _todays_figure(template, seed_user):
    """What the Recurring and savings pages show for *template* today.

    The figure Track's reset of the stored base existed to keep current, read
    from the producer those pages read since plan step X-bp
    (``obligations_aggregator.todays_price``): amount rule 4, the standing
    extra included.
    """
    return todays_price(template, BalanceContext.build(seed_user["user"].id)).amount


# ── Payment Drift Warning + Auto-Track Switch Tests (C7 / ruling D3) ──


class TestPaymentDrift:
    """The loan detail underpayment-drift warning and its one-click auto-track switch.

    A MANUAL recurring payment whose stored base has fallen short of the
    contractual monthly payment (P&I + today's escrow) -- after an escrow or rate
    change the stored amount never absorbed -- warns on the loan detail page and
    offers to switch the payment to ``derive_from_loan`` so it tracks the contract
    automatically (step C7, ruling D3).  A derive payment already tracks and never
    warns; an at- or above-contract payment never warns (a deliberate overpayment
    does not trip it).
    """

    @staticmethod
    def _contract(acct, user_id):
        """The loan's contractual monthly payment today (P&I + escrow) via the seam.

        The same figure the route's ``_contractual_monthly_payment`` writes and the
        loan card displays as "Total Monthly": the seam ``loan_terms`` P&I plus
        today's ``resolve_active_lines`` escrow.  Used to size a drift precisely
        without hard-coding the resolver-derived P&I.
        """
        terms = balance_at.loan_terms(acct, BalanceContext.build(user_id))
        components = escrow_calculator.resolve_active_lines(
            loan_loaders.load_escrow_lines(acct.id), date.today(),
        )
        return escrow_calculator.calculate_total_payment(
            terms.monthly_payment, components,
        )

    @staticmethod
    def _legacy_manual_transfer(seed_user, db_session, acct, amount):
        """Create a legacy manual recurring payment with NO settings row.

        Mirrors the two real production loans (a payment created before the
        loan_payment_settings feature): an active monthly TransferTemplate with a
        recurrence rule and a stated price, and no 1:1 settings row --
        which every reader treats as manual mode (derive_from_loan False, no extra).
        Its price is STATED, as both production definitions' are (the
        template-amount backfill gave every existing template a version):
        the loan page's plan sums this definition's occurrences since plan
        step R16-b-2 and refuses a series nobody stated.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            name="Legacy Mortgage Payment",
            is_active=True,
        )
        db_session.add(tpl)
        db_session.commit()
        state_template_price(tpl, amount)
        db_session.commit()
        # The definition first, then the cadence onto it (plan step R-F6),
        # then COMMITTED (plan step X-i3) -- every caller of this helper goes
        # on to issue a request, and a request cannot see uncommitted rows.
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        db_session.commit()
        return tpl

    def test_dashboard_warns_when_manual_payment_short(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A manual payment $50 below contract warns with the exact shortfall.

        The transfer is created via the amount override (manual mode,
        derive_from_loan False) at ``contract - $50.00``, so the shortfall is
        exactly $50.00.  The warning names the stored amount, the shortfall, and
        the contract, and offers the auto-track action.  The stored amount is
        warning-only on this page (the projection uses the contractual schedule),
        so matching it proves the warning renders it.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        stored = contract - Decimal("50.00")
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id), "amount": str(stored)},
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        tpl = _payment_template(db.session, seed_user, acct)
        assert "short of the" in html
        assert f"${stored:,.2f}" in html          # stored transfer amount
        assert "$50.00" in html                    # the exact shortfall
        assert f"${contract:,.2f}" in html         # the contractual payment
        assert f"/accounts/{acct.id}/loan/payments/{tpl.id}/track" in html
        assert "Track the loan" in html

    def test_dashboard_warns_when_legacy_manual_payment_short(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The real-loan shape -- a manual payment with NO settings row -- warns too.

        Both production loans predate loan_payment_settings, so their recurring
        payment has no settings row.  A missing row is manual mode, so a stored
        amount below contract must warn exactly as an amount-override manual
        payment does.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        self._legacy_manual_transfer(
            seed_user, db.session, acct, contract - Decimal("50.00"),
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "short of the" in html
        assert "$50.00" in html

    def test_dashboard_warns_when_base_short_despite_standing_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A short BASE warns even when a standing extra pushes total cash over contract.

        The firing control for ruling D3's ``extra_principal``-cancellation
        property: the warning compares the extra-free BASE (its stated price) to
        the contractual P&I + escrow, so a manual payment with base = contract-$50
        and a $75 standing extra -- total live cash contract+$25, ABOVE contract --
        must STILL warn, because the base under-covers P&I + escrow.  A regression
        that added the extra to the compared (stated) side (``stated =
        base + extra``) would suppress this warning and still pass every
        other test in the class; this asserts the $50.00 base shortfall renders.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
                "extra_principal": "75.00",
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "short of the" in html
        assert "$50.00" in html          # the BASE shortfall, extra excluded

    @pytest.mark.parametrize("delta", [Decimal("0.00"), Decimal("100.00")])
    def test_dashboard_no_warning_when_manual_at_or_above_contract(
        self, auth_client, seed_user, db, seed_periods, delta,
    ):
        """A payment exactly at or above contract never warns.

        Ruling D3: a deliberate overpayment does not trip the drift warning; the
        warning is underpayment-only.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract + delta),
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert "short of the" not in resp.data.decode()

    def test_dashboard_no_warning_when_derive_even_after_escrow_rise(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A DERIVE payment never warns, even while its next row is below today's contract.

        The control that proves derive mode is EXCLUDED, not merely free of
        drift.  A derive transfer is created, then a $200/mo escrow line is
        added so TODAY's contract rises $200.00 above what it was at creation
        (both figures asserted), and the line is removed from the next
        installment's due date on.  Amount rule 4 prices that installment on
        its own due date, so the payment's next row is the creation-day
        contract: its base sits $200.00 BELOW today's contract, the shape a
        fixed payment warns on (:meth:`test_dashboard_warns_when_manual_payment_short`).
        No warning shows, because the card never asks a payment that tracks
        the loan for its drift (``routes/loan/dashboard.py``,
        ``_payment_strips``).  An escrow rise alone cannot grade that: with
        nothing changing between today and the due date, rule 4 prices the
        row at today's contract and ``_payment_drift`` answers ``None`` with
        or without the exclusion, which is how this case stayed green with
        the exclusion deleted (plan#2686's review).
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        creation_contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},   # derive default
        )
        # Today is 2026-03-20 (this package's conftest) and the mortgage pays
        # on the 1st, so the next installment is due 2026-04-01.  A $2,400 a
        # year escrow line in effect since origination raises today's
        # contract; its removal from that due date on leaves the installment
        # at the creation-day contract.
        next_due = date(2026, 4, 1)
        taxes = add_escrow_line(db.session, acct.id, "Taxes", Decimal("2400.00"))
        db.session.add(EscrowComponentVersion(
            line_id=taxes.line.id, effective_date=next_due,
            annual_amount=Decimal("0.00"), is_removed=True,
        ))
        db.session.commit()

        tpl = _payment_template(db.session, seed_user, acct)
        todays_contract = self._contract(acct, seed_user["user"].id)
        # The contract rose: $2,400.00 / 12 = $200.00 a month of escrow.
        assert todays_contract == creation_contract + Decimal("200.00")
        assert newest_price(tpl) is None
        assert tpl.settings.derive_from_loan is True

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The precondition the claim rests on: the strip prices the next
        # installment at the creation-day contract, $200.00 below today's.
        assert re.search(
            rf'loan-payment__figure">\s*\${creation_contract:,.2f}\s*<', html,
        ), "the derive payment's next row is not priced at the creation-day contract"
        assert "next payment, Apr 1" in html
        assert f"${todays_contract:,.2f}" in html   # the loan card's Total Monthly
        assert "short of the" not in html
        assert "loan-payment--short" not in html

    def test_track_payment_flips_to_derive_and_clears_warning(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The one-click switch flips a short manual payment to auto-track.

        A manual payment $50 below contract warns; POSTing track-payment sets
        derive_from_loan True, so a re-render shows no warning and the payment's
        figure on the Recurring and savings pages is the contract.  The door
        reset the stored base to the contract until plan step X-bp; those pages
        price the payment by amount rule 4 now
        (``obligations_aggregator.todays_price``), so the figure is read there.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
            },
        )
        tpl = _payment_template(db.session, seed_user, acct)
        # Precondition: the warning is showing.
        pre = auth_client.get(f"/accounts/{acct.id}/loan")
        assert "short of the" in pre.data.decode()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

        db.session.expire(tpl)
        assert tpl.settings.derive_from_loan is True
        assert _todays_figure(tpl, seed_user) == contract

        post = auth_client.get(f"/accounts/{acct.id}/loan")
        assert "short of the" not in post.data.decode()

    def test_track_payment_creates_settings_row_for_legacy_manual(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Switching a legacy no-settings payment creates the settings row (derive).

        The real-loan shape has no loan_payment_settings row; the switch must
        create one with derive_from_loan True (a missing row is manual mode) and
        keep the extra at zero, so the payment's figure is the contract.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        tpl = self._legacy_manual_transfer(
            seed_user, db.session, acct, contract - Decimal("50.00"),
        )
        assert tpl.settings is None   # legacy shape: no settings row

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302

        db.session.expire(tpl)
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is True
        assert tpl.settings.extra_principal == Decimal("0.00")
        assert _todays_figure(tpl, seed_user) == contract

    def test_track_payment_preserves_standing_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Switching to auto-track preserves the standing extra principal.

        A manual payment with a $75 standing extra, $50 below contract on its base:
        the switch flips derive True and keeps extra at $75 (the extra rides on top
        of the tracked base, unchanged), so the payment's figure is the contract
        plus $75.00.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
                "extra_principal": "75.00",
            },
        )
        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302

        db.session.expire(tpl)
        assert tpl.settings.derive_from_loan is True
        assert tpl.settings.extra_principal == Decimal("75.00")
        assert _todays_figure(tpl, seed_user) == contract + Decimal("75.00")

    def test_track_payment_unknown_template_404s(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A template id naming none of this loan's payments is a 404, no 500."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/999999/track",
        )
        assert resp.status_code == 404

    def test_track_payment_flips_the_named_definition_not_the_oldest(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments into one loan: the door flips the one its URL names.

        The shape ruling R-R83 was asked for (plan ledger row D49): a fixed
        sweep created BEFORE the real payment, both fixed (the payment was
        created with a typed amount).  The old loan-keyed door took
        ``active_recurring_transfer_template`` -- the OLDEST -- and flipped
        the sweep to derive, so the loan was projected paying the contract
        twice a month.  Naming the payment in the URL makes the oldest
        irrelevant: the sweep stays fixed at its own figure and the named
        payment is the one that tracks.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        # The sweep first, so it is the oldest active definition into the loan.
        sweep = self._legacy_manual_transfer(
            seed_user, db.session, acct, Decimal("50.00"),
        )
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
            },
        )
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        payment = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .filter(TransferTemplate.id != sweep.id)
            .one()
        )
        assert sweep.id < payment.id

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{payment.id}/track",
        )
        assert resp.status_code == 302

        db.session.expire_all()
        assert payment.settings.derive_from_loan is True
        assert _todays_figure(payment, seed_user) == contract
        assert sweep.settings is None
        assert newest_price(sweep) == Decimal("50.00")

    def test_track_payment_refuses_another_loans_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The owner's payment into a DIFFERENT loan is a 404 on this loan's track door.

        The same gate the settings door proves
        (``TestTransferPrompt.test_update_payment_settings_refuses_another_loans_template``),
        asked of the second door: one function, two doors, each measured.
        """
        acct = _create_mortgage(seed_user, db.session)
        other = _create_mortgage(seed_user, db.session, name="Other Mortgage")
        contract = self._contract(other, seed_user["user"].id)
        other_tpl = self._legacy_manual_transfer(
            seed_user, db.session, other, contract - Decimal("50.00"),
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{other_tpl.id}/track",
        )
        assert resp.status_code == 404
        db.session.expire(other_tpl)
        assert other_tpl.settings is None
        assert newest_price(other_tpl) == contract - Decimal("50.00")

    def test_the_card_renders_one_strip_per_definition_with_its_own_controls(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments into one loan: two strips, each with ITS figure and doors.

        Ruling R-R83 (plan step R7d-g-3): the card lists every recurring
        transfer into the loan.  A fixed ``$50`` sweep (the older definition,
        no settings row) and the dashboard-created payment (tracks the loan,
        ``$125`` extra).  Each strip prefills its OWN extra, posts to its OWN
        settings door, and only the fixed one offers the Track button -- the
        tracking one has nothing to flip.  The sweep, at ``$50`` against a
        four-figure contract, is short, so its strip carries the shortfall
        sentence and the warning tint while the tracking strip carries none.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        sweep = self._legacy_manual_transfer(
            seed_user, db.session, acct, Decimal("50.00"),
        )
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "125.00",
            },
        )
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        payment = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .filter(TransferTemplate.id != sweep.id)
            .one()
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()

        # Two strips, in definition order (oldest first), each named (the
        # payment's name carries a ``->`` the template escapes).
        assert (
            html.index(f'data-template-id="{sweep.id}"')
            < html.index(f'data-template-id="{payment.id}"')
        )
        assert sweep.name in html
        assert payment.name.replace(">", "&gt;") in html
        assert "2 into this loan" in html
        # Each strip's own doors.
        for tpl in (sweep, payment):
            assert f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings" in html
        # One tracker per loan: the payment tracks, so neither strip offers
        # Track (the sweep's strip names the tracker in its shortfall).
        assert f"/accounts/{acct.id}/loan/payments/{sweep.id}/track" not in html
        assert f"/accounts/{acct.id}/loan/payments/{payment.id}/track" not in html
        # Each strip's own extra, prefilled on its own input.
        assert re.search(
            rf'id="extra-principal-{sweep.id}"[^>]*value="0\.00"', html,
        )
        assert re.search(
            rf'id="extra-principal-{payment.id}"[^>]*value="125\.00"', html,
        )
        # The modes, one each.
        assert html.count("Tracks the loan") == 1
        assert html.count("Fixed amount") == 1
        # Each strip's next payment, priced by the amount model: the sweep's
        # $50.00 stated price (rule 3 -- no settings row), the tracking
        # payment's contract + its $125 extra (rule 4's derive arm).
        assert "$50.00" in html
        assert f"${contract + Decimal('125.00'):,.2f}" in html
        assert "next payment," in html
        # The sweep is short; the tracking payment is not.
        assert html.count("loan-payment--short") == 1
        assert "short of the" in html
        # The page-top alerts are gone: the card is the one home (the base
        # layout's own MFA nag is the one warning alert a page may carry).
        assert "Switch to automatic payment" not in html
        assert html.count("alert-warning") == html.count("mfa-nag-banner")
        assert "No recurring payment set up" not in html

    def test_the_strip_prices_the_next_payment_not_the_newest_stated_price(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A fixed payment's figure and drift are its NEXT row's price, per the amount model.

        The owner states a future price on a fixed payment ($9,999.00 from
        2028).  ``default_amount`` followed the NEWEST statement
        (``_resync_scalar``, until plan step X-bp), so the first cut of the
        card -- and the drift before it -- would have shown $9,999.00 as what
        the loan is paid and judged the drift on it.  The strip prices the
        next occurrence through ``cash_ledger.definition_cash`` (ruling
        R-R67's one producer), so it shows the price in effect for that date,
        and the shortfall sentence names the same figure.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        stored = contract - Decimal("50.00")
        tpl = self._legacy_manual_transfer(seed_user, db.session, acct, stored)
        state_template_price(
            tpl, Decimal("9999.00"), effective_on=date(2028, 1, 1),
        )
        db.session.commit()
        db.session.expire(tpl)
        assert newest_price(tpl) == Decimal("9999.00"), (
            "precondition: the newest stated price is the $9,999.00 one"
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$9,999.00" not in html
        assert f"${stored:,.2f}" in html
        assert "next payment," in html
        assert "$50.00" in html and "short of the" in html

    def test_a_second_tracker_is_refused_at_the_door_and_hidden_on_the_card(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """One definition tracks a loan (developer, 2026-09-14).

        A tracking payment plus a fixed $50 sweep: the sweep's strip offers no
        Track control and its shortfall sentence names the tracker; a crafted
        POST to the sweep's track door is refused with the flash naming the
        tracker and writes nothing -- no settings row, the $50 untouched.  The
        tracker's own door still answers (a re-track writes the same mode).
        """
        from app.routes.loan.payment_transfer import (  # pylint: disable=import-outside-toplevel
            ANOTHER_PAYMENT_TRACKS_THE_LOAN,
        )
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        tracker = _payment_template(db.session, seed_user, acct)
        sweep = self._legacy_manual_transfer(
            seed_user, db.session, acct, Decimal("50.00"),
        )

        html = auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        assert f"/accounts/{acct.id}/loan/payments/{sweep.id}/track" not in html
        assert f"/accounts/{acct.id}/loan/payments/{tracker.id}/track" not in html
        assert "tracks the loan, so this" in html
        assert tracker.name.replace(">", "&gt;") in html

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{sweep.id}/track",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        page = resp.data.decode()
        # The door's own sentence, HTML-escaped as the flash renders it.
        assert "already tracks this loan" in ANOTHER_PAYMENT_TRACKS_THE_LOAN
        assert "already tracks this loan" in page
        assert tracker.name.replace(">", "&gt;") in page
        assert "cannot track it too" in page
        db.session.expire_all()
        assert sweep.settings is None
        assert newest_price(sweep) == Decimal("50.00")

        # The tracker's own door is not refused by its own tracking.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tracker.id}/track",
        )
        assert resp.status_code == 302
        db.session.expire_all()
        assert tracker.settings.derive_from_loan is True

    def test_track_payment_idor(
        self, second_auth_client, seed_user, db, seed_periods,
    ):
        """A non-owner switching a loan's payment gets a 404 (not-yours == not-found).

        With the owner's real template id in the URL, so the 404 is the
        ownership gate's and not the URL map's (the owner's 302 on the same
        URL shape is ``test_track_payment_flips_to_derive_and_clears_warning``).
        The owner's payment is built through the ORM, not the owner's client:
        see ``test_update_payment_settings_idor`` for why a second client's
        request after the owner's runs as the owner.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        tpl = self._legacy_manual_transfer(
            seed_user, db.session, acct, contract - Decimal("50.00"),
        )
        resp = second_auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 404
        db.session.expire(tpl)
        assert tpl.settings is None
        assert newest_price(tpl) == contract - Decimal("50.00")
