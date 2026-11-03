"""Template route tests: a loan payment's recurrence is the app's, so its form refuses an opening
or closing bound and a one-time switch, and its preview stops at the loan's payoff.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.services.loan_loaders import load_loan_params
from app.services.installment_calendar import first_installment_date
from app.services.balance_at import BalanceContext
from app.routes._recurrence_preview import PREVIEW_OCCURRENCE_LIMIT, render_preview_html
from app.services.recurrence import (
    ClosesOn,
    EndsAfterOccurrences,
    RecurrenceSpec,
    placed_periods,
    reauthor_rule,
    recurrence_spec,
)
from app.services.recurring_definition import read_definition, resolved_definition
from app.utils.dates import display_today
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    end_bound_payload,
    freeze_today,
    make_cadence_rule,
    make_loan_payment_template,
    make_transfer_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY


def _loan_payment_template(seed_user):
    """Create and COMMIT a recurring LOAN PAYMENT transfer template.

    A loan payment is a transfer template carrying
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` -- decision
    B, and what ``is_loan_payment`` reads.  Its closing bound is the loan's
    projected payoff, written by ``loan_recurrence_sync``, which is what makes
    the "Ends" control on its form DERIVED rather than authored.

    Args:
        seed_user: The seeded owner fixture.

    Returns:
        The committed :class:`~app.models.transfer_template.TransferTemplate`.

    The CADENCE is committed too (plan step balance:X-i3).  It was only
    flushed, so every test here that went on to issue a request was asking the
    route to read a rule no request could see -- a request holds its own
    transaction and an uncommitted row is invisible in it.  The template
    itself was already committed one line above, which is the shape this
    matches rather than departs from.
    """
    loan_account = create_loan_account(seed_user, db.session)
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        name="Loan Payment",
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()
    # Priced BEFORE the settings row is attached, the order ``track_payment``
    # takes, as every real manual payment is: the loan page's plan sums this
    # definition's occurrences since plan step R16-b-2 and refuses a series
    # nobody stated.
    state_template_price(template, Decimal("500.00"))
    template.settings = LoanPaymentSettings(derive_from_loan=False)
    db.session.commit()
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, MONTHLY,
        fires_on_day=1, end_date=date(2030, 1, 1),
    )
    db.session.commit()
    return template


class TestALoanPaymentsClosingBoundIsDerived:
    """Plan step R7b-3: the app owns a loan payment's stop, so the form does not.

    A loan's standing payment stops when the loan's balance folds to zero,
    which the composed door derives on every read (plan step R7d-g; until
    then ``loan_recurrence_sync.sync_recurring_payment_bounds`` wrote that
    payoff into ``end_date`` on every payoff-affecting edit, and a bound
    accepted here would have been discarded without a word).  The loan's own
    payment carries no authored stop (ruling **R-R59**) -- so the control
    renders disabled, and a submission that states one anyway is REFUSED
    rather than silently dropped.

    Both halves are needed and neither is redundant: disabling is the
    affordance a user sees, and the refusal is the rule a crafted POST meets.
    """

    def test_the_control_renders_disabled_on_a_loan_payment(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The user is told where the value comes from rather than shown a box.

        A disabled control posts nothing, so the mode key arrives ABSENT --
        which the update path reads as "this form said nothing about the
        bound" and leaves the stored one alone.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)

            resp = auth_client.get(f"/transfers/{template.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            control = body[body.index('id="field-end-bound"'):]
            control = control[:control.index("</div>", control.index("</select>"))]
            assert 'id="recurrence_end_mode"' in control
            assert "disabled" in control
            assert "projected payoff" in body

    def test_a_crafted_submission_stating_a_bound_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Refused, and the stored bound is untouched.

        The refusal is what keeps the user's stated stop from being thrown
        away in silence -- and it is the door half of what makes a COUNT
        bound and the sync's DATE unable to meet on one row.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)
            before = template.recurrence_rule.end_date

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(),
                    **end_bound_payload(EndsAfterOccurrences(count=6)),
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"paid off" in resp.data
            db.session.expire_all()
            rule = db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule
            assert rule.max_occurrences is None
            assert rule.end_date == before

    def test_an_ordinary_edit_that_states_no_bound_still_saves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the refusal is on a STATED bound, not on loan payments.

        A user renaming their loan payment must not be blocked by a control
        their form does not show them.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)

            auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": "Renamed Payment",
                    "amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(states_a_start=False),
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).name == "Renamed Payment"


class TestALoanPaymentsOpeningBoundIsDerived:
    """Plan step R7b-4: the app owns a loan payment's START, so the form does not.

    The exact mirror of :class:`TestALoanPaymentsClosingBoundIsDerived`, and it
    guards the more expensive half.  ``loan_recurrence_sync._sync_loan_cadence``
    writes that rule's ``start_date`` from the loan's FIRST CONTRACTUAL
    INSTALLMENT on every payoff-affecting edit, so a bound accepted from this
    form would be discarded without a word the next time the loan changed --
    and a bound accepted and KEPT would be worse: generation before origination
    is erased by the fold while the cash side still debits it, measured at
    ``$3,220.92`` of phantom payments on a mortgage closing one month out.

    **Which definitions lock is ``is_standing_loan_payment``, not
    ``is_loan_payment``** (plan step R7b-4; read off the pass since R7d-f),
    and the difference is not
    academic: neither of the developer's real loan payments carries a
    ``loan_payment_settings`` row, so the older predicate answered False for
    both and the sibling class above was passing on a fixture that is not
    shaped like production.  These cases use the same fixture -- it satisfies
    both predicates -- and the three-arm test of the predicate itself is in
    ``tests/test_services/test_loan_recurrence_sync.py``.
    """

    def test_the_control_renders_disabled_on_a_loan_payment(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The user is told where the value comes from rather than shown a box.

        A disabled control posts nothing, so the key arrives ABSENT -- which
        the update path reads as "this form said nothing about the bound" and
        leaves the stored one alone.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)

            resp = auth_client.get(f"/transfers/{template.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            control = body[body.index('id="field-starts-on"'):]
            control = control[:control.index("</div>", control.index("<input"))]
            assert 'id="starts_on"' in control
            assert "disabled" in control
            # The apostrophe arrives HTML-escaped since plan step R7c-b, which
            # renders the copy through a Jinja variable so the swappable form
            # can carry the same sentence as a data attribute.  Asserted on the
            # half that has no entity in it, rather than on ``&#39;``: the
            # subject is the WORDING, and pinning an escape spelling would go
            # red on a markup change that says nothing to the user.
            assert "first payment, and updated when the loan changes" in body

    def test_a_crafted_submission_stating_a_start_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Refused, and the stored opening bound is untouched.

        Reachable only by a crafted POST, because the control is disabled --
        and checked anyway for the reason the closing bound's twin is:
        disabling is the affordance, the refusal is the rule.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)
            before = template.recurrence_rule.starts_on

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(),
                    "starts_on": "2027-06-01",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"first installment" in resp.data
            db.session.expire_all()
            rule = db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule
            assert rule.starts_on == before

    def test_an_ordinary_edit_that_states_no_start_still_saves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the refusal is on a STATED bound, not on loan payments.

        A user renaming their loan payment must not be blocked by a control
        their form does not show them -- and the stored bound must survive the
        edit rather than being cleared by the absent key.

        The payload restates the rule's OWN cadence (monthly), which the form
        would: leaving the default every-paycheck one in place would change the
        unit as a side effect, and a paycheck-space rule's first occurrence is
        normalised onto a payday -- so the bound would move for a reason that
        has nothing to do with what this test measures.

        **MOVED BY RULING at plan step R7d-g-2 (R-R85)**: the fixture's rule
        starts on the schedule's first 1st, NOT the loan's first contractual
        installment -- the stale-cache shape plan ledger row **D35**
        measured -- and this case asserted the rename left that stale date
        in place.  Every door where the standing payment is written now
        brings its start onto the contract afterwards, the update door
        included, so the rename HEALS it: the stored start is the first
        installment, and the absent key still cleared nothing.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)
            params = load_loan_params(template.to_account_id)
            first_installment = first_installment_date(
                params.origination_date, params.payment_day,
            )
            assert template.recurrence_rule.starts_on != first_installment, (
                "precondition: the fixture's start is not the contract's"
            )

            auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": "Renamed Loan Payment",
                    "amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(
                        unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                    ),
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            stored = db.session.get(TransferTemplate, template.id)
            assert stored.name == "Renamed Loan Payment"
            assert stored.recurrence_rule is not None, "the absent key cleared the rule"
            assert stored.recurrence_rule.starts_on == first_installment


class TestALoanPaymentCannotBeMadeOneTime:
    """The refusal covers every loan payment, not only the settings-carrying ones.

    Clearing a loan payment's recurrence nulls ``recurrence_rule_id``, and that
    is how ``recurring_transfer_query.active_recurring_transfer_template``
    FINDS a loan's payment -- so the loan goes on amortizing with nothing
    projecting a payment against it, and its standing ``extra_principal`` (when
    it has one) stops being threaded into the balance seam.

    **The set this covers was measured wrong until plan step R7b-4.** The
    refusal asked ``is_loan_payment`` alone -- does this template carry
    ``LoanPaymentSettings`` -- and on a 2026-08-14 production clone NEITHER of
    the developer's real loan payments carries that row, so both mortgages were
    clearable. The guard now asks the UNION with
    ``balance_at.is_standing_loan_payment``, and the second case below is
    the one that was live: it is the production shape, and it FAILS against the
    predicate this step replaced.
    """

    def test_a_settings_carrying_loan_payment_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The arm that already worked, kept so the union cannot lose it."""
        with app.app_context():
            template = _loan_payment_template(seed_user)
            rule_id = template.recurrence_rule.id

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    "recurrence_unit": "",
                    "recurrence_placement": "",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"repeats for the life of the loan" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule.id == rule_id

    def test_a_loan_payment_with_NO_settings_row_is_refused_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The production shape, and the one that was accepted before R7b-4.

        Identical to the case above except the template carries no
        ``LoanPaymentSettings`` -- which is how BOTH of the developer's real
        loan payments are stored. Its recurrence must survive the clear, or a
        mortgage silently leaves the forward plan.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            template = make_transfer_template(db.session, seed_user, loan)
            # COMMITTED, not flushed (plan step balance:X-i3): this case posts
            # a REFUSAL, so the route redirects without committing, and the
            # followed redirect is a fresh request that cannot see rows this
            # fixture never committed.  The sibling above rides
            # ``_loan_payment_template``, which commits its own.
            db.session.commit()
            assert template.settings is None, "fixture must have no settings row"
            rule_id = template.recurrence_rule.id

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "amount": "200.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    "recurrence_unit": "",
                    "recurrence_placement": "",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"repeats for the life of the loan" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule.id == rule_id

    def test_an_ordinary_transfer_can_still_be_made_one_time(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the refusal is on LOAN payments, not on transfers.

        Without this the union could be satisfied by a guard that refused
        every clear, which would take a real affordance away from every
        savings contribution the user has.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Holiday Fund",
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.flush()

            auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "amount": "200.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    "recurrence_unit": "",
                    "recurrence_placement": "",
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule is None


class TestThePreviewIsBoundedByTheLoansPayoff:
    """Plan ledger row REC-515 (plan step R7d-f-2): the preview takes the door.

    A transfer into a loan stops when the loan does, and that stop is derived
    (``loan_payment_window``), never stored.  The preview composed its walk
    from ``request.args`` alone, so for the loan's own payment -- whose
    locked "Ends" control posts nothing -- it walked ``NEVER_ENDS`` and listed
    dates past the payoff whenever fewer than five remained, on the one
    surface whose contract is "what saving would produce".  It now reads the
    transfer form's ``to_account_id`` through the ownership gate and resolves
    the submission through the composed door
    (``recurring_definition.resolved_submission``).

    Measured at one frozen day so the payoff is a literal: the 24-month
    $12,000 loan at 5% originating 2026-07-01 that the door's own tests use,
    whose derived payoff is 2028-07-01, on a schedule long enough to reach it
    (70 biweekly periods -- the door's walk stops at the SAVED horizon, so a
    schedule short of the payoff could not show the narrowing at all).
    """

    _TODAY = date(2026, 7, 1)
    _PAYOFF = date(2028, 7, 1)

    def _world(self, monkeypatch, seed_user, seed_schedule_at_cadence):
        """Freeze the day, seed the long schedule, build the loan and its payment.

        Returns:
            ``(loan, payment, ctx)`` -- the loan account, its standing
            recurring payment, and a read pass built at the frozen day.
        """
        freeze_today(monkeypatch, self._TODAY)
        seed_schedule_at_cadence(14, num_periods=70)
        loan = create_loan_account(
            seed_user, db.session, name="Preview Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"),
            term=24, origination_date=self._TODAY,
        )
        payment = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, self._TODAY)
        assert resolved_definition(payment, ctx).closing.derived == ClosesOn(
            on=self._PAYOFF,
        ), "precondition: the door derives the payoff these cases are pinned to"
        assert ctx.calendar().horizon() > self._PAYOFF, (
            "precondition: the schedule reaches past the payoff, so the "
            "narrowing is inside the walk's own window"
        )
        return loan, payment, ctx

    def _preview(self, auth_client, starts_on, **extra):
        """GET the preview for a MONTHLY cadence from *starts_on*.

        Returns:
            The response.
        """
        return auth_client.get("/templates/preview-recurrence", query_string={
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=starts_on,
            ),
            **extra,
        })

    def test_the_edit_forms_request_previews_exactly_what_the_door_places(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
        monkeypatch,
    ):
        """The EDIT form's request for a stored transfer into the loan: door == preview.

        The row's remedy as one equality: the dates the preview lists ARE the
        dates the composed walk emits for the stored definition, rendered by
        the fragment's own renderer -- so nothing here re-implements either
        side.  The subject is a SECOND transfer into the loan, authored
        monthly from three months before the payoff, because the loan's own
        payment has its first five occurrences well inside the loan's life
        and the equality would hold for it whether or not the preview
        narrowed (an adversarial review of this step measured that draft
        passing on the pre-step code).  For this definition the door places
        FOUR and an un-narrowed walk five, so the equality fires: the
        expected count is asserted below as the control that says so.  The
        request is what the edit form's script sends -- the stored cadence
        and first occurrence, no "Ends" controls (the owner stated none), and
        the destination.
        """
        with app.app_context():
            loan, _payment, ctx = self._world(
                monkeypatch, seed_user, seed_schedule_at_cadence,
            )
            sweep = make_transfer_template(
                db.session, seed_user, loan, amount="1.00",
            )
            sweep.name = "Sweep into the loan"
            # Re-authored through the write door onto the every-paycheck rule
            # the builder wrote: monthly from three months before the payoff,
            # no stop of its owner's.
            reauthor_rule(
                sweep.recurrence_rule,
                RecurrenceSpec(
                    user_id=seed_user["user"].id,
                    unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2028, 4, 1),
                ),
                ctx.calendar(),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, self._TODAY)
            assert resolved_definition(sweep, ctx).closing.derived == ClosesOn(
                on=self._PAYOFF,
            ), "precondition: a dollar a month moves the payoff nowhere"
            spec = recurrence_spec(sweep.recurrence_rule)
            placed = placed_periods(
                read_definition(sweep, ctx).placements,
                ending_on_or_after=spec.starts_on,
            )
            assert len(placed) == 4 < PREVIEW_OCCURRENCE_LIMIT, (
                "control: the door places fewer than the preview's limit, so "
                "an un-narrowed preview cannot equal it"
            )
            expected = render_preview_html(placed[:PREVIEW_OCCURRENCE_LIMIT])

            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(
                        unit=spec.unit, interval_n=spec.interval_n,
                        placement=spec.placement, starts_on=spec.starts_on,
                        nominal_day=spec.nominal_day,
                    ),
                    "to_account_id": str(loan.id),
                },
            )

            assert resp.status_code == 200
            assert resp.data.decode() == str(expected)

    def test_a_destination_loan_narrows_the_preview_to_its_payoff(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
        monkeypatch,
    ):
        """Inside the loan's last five installments, fewer than five are listed.

        Monthly from three months before the payoff names Apr 1, May 1, Jun 1
        and Jul 1 2028 inside the loan's life and August onwards past it: FOUR
        with the loan as destination, FIVE without one and FIVE into a savings
        account -- the difference is the destination's derived stop and
        nothing else, and "what saving would produce" is four rows and a
        finished loan.
        """
        with app.app_context():
            loan, _payment, _ctx = self._world(
                monkeypatch, seed_user, seed_schedule_at_cadence,
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Not A Loan",
            )
            db.session.commit()
            starts_on = date(2028, 4, 1)

            narrowed = self._preview(
                auth_client, starts_on, to_account_id=str(loan.id),
            )
            unnarrowed = self._preview(auth_client, starts_on)
            savings_bound = self._preview(
                auth_client, starts_on, to_account_id=str(savings.id),
            )

            assert narrowed.status_code == 200
            assert b"Next 4 occurrences" in narrowed.data, narrowed.data
            assert b"Next 5 occurrences" in unnarrowed.data, (
                "the control must differ, or this measures nothing"
            )
            assert b"Next 5 occurrences" in savings_bound.data, (
                "a destination with no derived stop narrows nothing"
            )

    def test_a_loan_finished_before_the_first_occurrence_previews_nothing(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
        monkeypatch,
    ):
        """The EMPTY shape: a rule opening after the payoff names no occurrence."""
        with app.app_context():
            loan, _payment, _ctx = self._world(
                monkeypatch, seed_user, seed_schedule_at_cadence,
            )

            resp = self._preview(
                auth_client, self._PAYOFF + timedelta(days=1),
                to_account_id=str(loan.id),
            )

            assert resp.status_code == 200
            assert b"No matching periods found" in resp.data
            assert b"occurrences" in self._preview(
                auth_client, self._PAYOFF + timedelta(days=1),
            ).data, "control: without the loan the same rule previews dates"

    def test_another_owners_destination_is_404(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user,
    ):
        """An untrusted id becomes a row through the ownership gate: 404.

        The house rule -- one answer for "not found" and "not yours" -- and
        the pass would refuse the foreign loan a second time if the gate did
        not (``ForeignAccountError`` from ``_memoize._memoize_once``), so the
        preview never folds another owner's loan under this owner's budget.
        """
        with app.app_context():
            resp = self._preview(
                auth_client, display_today(),
                to_account_id=str(seed_second_user["account"].id),
            )

            assert resp.status_code == 404

    def test_a_destination_that_does_not_exist_is_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Missing takes the same answer as foreign, so neither is a probe."""
        with app.app_context():
            resp = self._preview(
                auth_client, display_today(), to_account_id="999999",
            )

            assert resp.status_code == 404

    def test_an_unparseable_destination_previews_the_unnarrowed_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?to_account_id=garbage`` is ABSENT, on the two dates' own ground.

        The ``<select>`` cannot post it, so it is a hand-crafted query, and
        the honest preview of "no readable destination" is the rule alone --
        the disposition ``_submitted_iso_date`` states for the two bounds.  A
        parseable id that names no row of the owner's is a different question
        and is refused (the two cases above).  A regression PIN rather than a
        fix's own control: the pre-step preview ignored the argument and
        answered the same, and this says the disposition must stay.
        """
        with app.app_context():
            resp = self._preview(
                auth_client, display_today(), to_account_id="garbage",
            )
            control = self._preview(auth_client, display_today())

            assert resp.status_code == 200
            assert b"occurrences" in control.data, (
                "control: the same request without a destination lists dates"
            )
            assert resp.data == control.data
