"""The recurrence form's locked "Ends" row reads the RESOLVER, not the column.

Plan step **R7d-f**.  A loan's standing payment stops when the loan does, and
until this step the only place the edit form could read that stop was the
``end_date`` column -- the chokepoints' CACHE of the derived payoff, measured
stale on live data (plan ledger row **D35**: ``2029-01-22`` stored against
``2029-02-22`` derived).  The locked control now renders the composed door's
answer (``recurring_definition.resolved_definition``) in the words the
Recurring row uses, the identity every lock and refusal turns on is read off
the read pass rather than re-queried (row **N-511**), and the inverted-window
refusal reads the door's own arm instead of carrying a skip for the definition
whose column is the cache.

**Every case about the locked row pins a DIFFERENCE**, not an agreement: the
column is falsified to a date the loan's own fold does not name, and the
assertion is that the falsified date reaches neither the page nor the stored
bound an edit carries through.  A case that asserted the form agrees with the
seam would pass while both read the column.  The refusal and the
baseline-less classes are CONTROLS: their answers are the ones the old code
gave, held so the deleted skip and the new read pass changed nothing they
guard.  The shapes the resolver can answer with are graded in
``tests/test_services/test_loan_recurrence_sync.py``; what a FORM does with each
of them is graded here.
"""
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from flask import get_flashed_messages

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.routes._recurrence_form_refusals import RecurrenceFormContext
from app.routes._recurrence_form_helpers import (
    resolve_recurrence_rule_for_update,
)
from app.routes._recurrence_form_render import edit_form_recurrence_state
from app.services.recurrence import authorable_cadences
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import end_bound_before_start_message
from app.services import balance_at, recurring_transfer_query
from app.services.balance_at import BalanceContext, is_standing_loan_payment
from app.services.loan_recurrence_sync import (
    loan_payment_window,
)
from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    EmptyAuthoredWindowError,
    EndsOnDate,
    reauthor_rule,
    recurrence_spec,
    resolved_recurrence,
)
from app.services.recurring_definition import resolved_definition
from tests._test_helpers import (
    bind_rule_to_loan,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_loan_payment_template,
    make_transfer_template,
    validated_cadence,
)
from tests.oracles.recurrence_baseline import MONTHLY

#: The day every pass in this module is measured at, frozen so the loan fold
#: and the derived payoff are deterministic.  The same day and loan shape the
#: composed door's own tests use, so the payoff below is theirs: a 24-month
#: $12,000 loan at 5% originating today reaches zero on 2028-07-01.
_TODAY = date(2026, 7, 1)

#: A date the loan's fold never names, written into the column as the cache.
#: Chosen EARLIER than every derived stop these cases produce, so a form that
#: still read the column would show it and a form that reads the door cannot.
_STALE = date(2027, 1, 1)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today mid-loan so the projected schedule does not drift."""
    freeze_today(monkeypatch, _TODAY)


def _ctx(seed_user):
    """Return the read pass every direct call here is measured against."""
    return BalanceContext.build(seed_user["user"].id, _TODAY)


def _loan(seed_user, **kwargs):
    """Return a 24-month $12,000 loan at 5%, originating today by default."""
    defaults = {
        "name": "Form Loan",
        "principal": Decimal("12000.00"),
        "rate": Decimal("0.05000"),
        "term": 24,
        "origination_date": _TODAY,
    }
    defaults.update(kwargs)
    return create_loan_account(seed_user, db.session, **defaults)


def _payment_into(seed_user, loan, *, fires_on_day=1):
    """Return a COMMITTED monthly recurring transfer into *loan*, bound to it.

    Bound the way every production door binds a new rule into a loan
    (``bind_rule_to_loan``, from ``materialize_initial_transfers``): its
    ``starts_on`` is the loan's first contractual installment, not the
    fixture's default.  Without it the rule fires before origination, which
    the transfer service refuses on regeneration and which puts the EMPTY
    comparison on the wrong side of the loan's closing date.  Committed rather
    than flushed because the cases below issue requests, and a request holds
    its own transaction: an uncommitted row is invisible in it (plan step
    ``balance:X-i3``'s lesson, restated where it bites).
    """
    tpl = make_loan_payment_template(
        db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=fires_on_day,
    )
    bind_rule_to_loan(tpl.recurrence_rule, loan.id)
    db.session.commit()
    return tpl


def _cache_the_bound(tpl, bound, ctx):
    """Write *bound* into the rule's column through the real write door.

    Named for what it reproduced until plan step R7d-g -- the chokepoints'
    cache -- and kept because the column holds exactly what the door leaves;
    asserted back so a case cannot pass on a write that never landed.  What
    it writes now is a stop the owner authored (ruling **R-R82**).
    """
    reauthor_rule(
        tpl.recurrence_rule,
        replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=bound)),
        ctx.calendar(),
    )
    db.session.commit()
    assert tpl.recurrence_rule.end_date == bound, "precondition: the column is cached"


def _end_bound_help(body):
    """Return the RENDERED help text under the "Ends" row.

    The rendered text and not the page: the transfer form ships the payoff
    sentence as a ``data-locked-text`` attribute for the script's swap on a
    destination change, so a substring test over the whole body cannot tell
    the caption the row shows from the one the script holds in reserve.
    """
    match = re.search(
        r'id="end-bound-help"[^>]*>\s*(.*?)\s*</div>', body, re.S,
    )
    assert match is not None, "the Ends row rendered no help text"
    return match.group(1)


def _selected_mode(body):
    """Return the token of the "Ends" option carrying ``selected``, or ``None``.

    Every open select renders all three options, so a substring test for a
    token cannot fail for the shape it is named after (the first cut of this
    file asserted ``'value="on_date"' in body`` and an adversarial review
    measured it true of every render).  Only the ``selected`` attribute says
    which shape the row preselects.

    Args:
        body: The decoded response body.

    Returns:
        The selected option's ``value``, or ``None`` when none carries it.
    """
    start = body.index('id="recurrence_end_mode"')
    control = body[start:body.index("</select>", start)]
    for option in re.findall(r"<option\b[^>]*>", control):
        if re.search(r"\bselected\b", option):
            return re.search(r'value="([^"]*)"', option).group(1)
    return None


def _form_ctx(end_bound):
    """Return a form context carrying *end_bound* and a real redirect target."""
    return RecurrenceFormContext(
        end_bound=end_bound,
        redirect=RedirectTarget(
            "transfers.edit_transfer_template", {"template_id": 1},
        ),
    )


def _ends_select(body):
    """Return the "Ends" ``<select>``'s source and its ``<option>`` texts.

    Parsed structurally rather than matched as a substring, for the reason
    ``TestTheServerRendersTheEndsControlAlreadyCorrect`` records: a fallback
    substring that every render contains cannot fail for the state it is
    named after.

    Args:
        body: The decoded response body.

    Returns:
        ``(select_tag, options)`` -- the opening ``<select ...>`` tag's text
        and the stripped text of each option inside it, in order.
    """
    start = body.index('id="recurrence_end_mode"')
    open_tag = body[body.rindex("<select", 0, start):body.index(">", start) + 1]
    inner = body[start:body.index("</select>", start)]
    options = [
        re.sub(r"\s+", " ", text).strip()
        for text in re.findall(r"<option\b[^>]*>(.*?)</option>", inner, re.S)
    ]
    return open_tag, options


class TestTheLockedEndsRowReadsTheResolver:
    """What the locked control DISPLAYS, per shape the resolver can answer."""

    def test_a_stored_stop_on_the_standing_payment_is_what_the_locked_row_shows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling **R-R82** on the locked row: a stored stop is the owner's, and it is displayed.

        Until plan step R7d-g this case was plan ledger row **D35**'s shape:
        the column held the chokepoints' cache, the door read it as none
        (ruling **R-R56**), and the locked control rendered the derived
        payoff with the cached date appearing nowhere on the page.  A date in
        that column is the owner's word now, kept from before the definition
        became the loan's standing payment; the door composes it, the row
        stays LOCKED (the standing payment's stop is not editable here:
        archive it, ruling **R-R59**) and displays the composed phrase, which
        names the owner's date.  The derived half is INDEFINITE, because the
        summed plan honours the same stop and the loan then never clears --
        the same fact read from both sides.  **The help text says which of
        the two the row is showing**: an adversarial review of R7d-g-1 found
        the first cut rendering the owner's date under "Set from the loan's
        projected payoff, and updated when the loan changes", neither of which
        is true of it; the row now names it as the owner's, still in force,
        with the archive as its door.  The payoff sentence is pinned on the
        NULL-column case beside this one.
        """
        with app.app_context():
            loan = _loan(seed_user)
            tpl = _payment_into(seed_user, loan)
            _cache_the_bound(tpl, _STALE, _ctx(seed_user))
            closing = resolved_definition(tpl, _ctx(seed_user)).closing
            assert closing.authored == EndsOnDate(on=_STALE)
            assert closing.derived == INDEFINITE, closing

            body = auth_client.get(f"/transfers/{tpl.id}/edit").data.decode()

            select_tag, options = _ends_select(body)
            assert "disabled" in select_tag
            assert options == ["until Jan 01, 2027"], options
            help_text = _end_bound_help(body)
            # Jinja escapes the apostrophe, so the fragments avoid it.
            assert "A stop set before this became the loan" in help_text
            assert "archive the payment to change it" in help_text
            assert "projected payoff" not in help_text, (
                "the owner's stop was captioned as the loan's payoff"
            )

    def test_a_loan_that_never_pays_off_reads_Never(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The INDEFINITE shape takes the control's own word for "no stop".

        A $240,000 / 30-year contract at 6% trued up to $900,000: the level
        payment cannot cover the interest, the fold never reaches zero, and the
        describer words no stop at all.  A form control has to SAY something
        there, and what it says is the same "Never" its open twin offers.  The
        column is NULL, as the R7d-g migration leaves every standing
        payment's: a loan that never pays off has NO date to show.
        """
        with app.app_context():
            loan = _loan(
                seed_user, name="Never Clears",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2026, 1, 1),
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("900000.00"),
            )
            tpl = _payment_into(seed_user, loan)
            assert tpl.recurrence_rule.end_date is None
            ctx = _ctx(seed_user)
            assert loan_payment_window(
                tpl, resolved_recurrence(tpl.recurrence_rule, ctx.calendar()), ctx,
            ) == INDEFINITE, "precondition: the loan never clears"

            body = auth_client.get(f"/transfers/{tpl.id}/edit").data.decode()

            select_tag, options = _ends_select(body)
            assert "disabled" in select_tag
            assert options == ["Never"], options
            help_text = _end_bound_help(body)
            assert "projected payoff" in help_text
            assert "A stop set before this became the loan" not in help_text

    def test_a_loan_cleared_before_its_first_installment_reads_never_runs(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The EMPTY shape, which no Ends shape can spell and the phrase can.

        Originated 2026-06-20 with a ``payment_day`` of 15 -- first
        installment 2026-07-15 -- and trued to zero the day after origination:
        the loan closed before the payment ever fires, the resolver answers
        EMPTY, and the describer says so rather than naming a closing date that
        would read as a stop that had once been a start.  This is the shape
        that made mapping the derived stop onto the control's three shapes
        impossible, and why the locked row carries a phrase instead.
        """
        with app.app_context():
            loan = _loan(
                seed_user, name="Cleared Early",
                origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = _payment_into(seed_user, loan, fires_on_day=15)
            ctx = _ctx(seed_user)
            assert loan_payment_window(
                tpl, resolved_recurrence(tpl.recurrence_rule, ctx.calendar()), ctx,
            ) is EMPTY, "precondition: the loan closed before the first firing"

            body = auth_client.get(f"/transfers/{tpl.id}/edit").data.decode()

            select_tag, options = _ends_select(body)
            assert "disabled" in select_tag
            assert options == ["never runs"], options

    def test_an_owner_with_no_pay_periods_gets_a_locked_row_and_no_phrase(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The door resolves nothing without a schedule, and the row says so by saying nothing.

        A broken invariant (registration bootstraps a period), reachable in a
        test and answered honestly rather than with a 500: the row is still
        locked -- the identity needs no calendar -- and its one option is
        empty, because there is no first occurrence to measure the loan's
        closing date against and nothing derived to name.  The help text
        still says where the value comes from.
        """
        with app.app_context():
            loan = _loan(seed_user, name="No Schedule Loan")
            tpl = _payment_into(seed_user, loan)
            db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).delete(synchronize_session=False)
            db.session.commit()
            assert not _ctx(seed_user).calendar().periods

            resp = auth_client.get(f"/transfers/{tpl.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            select_tag, options = _ends_select(body)
            assert "disabled" in select_tag
            assert options == [""], options
            assert "projected payoff" in body

    def test_a_second_transfer_into_the_same_loan_keeps_an_open_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Ruling **R-R56**, and plan ledger row **D49**'s lock arm dispositioned.

        A second recurring transfer into a loan whose standing payment is an
        older transfer keeps its owner's bound -- the door ANDs it with the
        loan's derived stop -- so its "Ends" control stays OPEN, preselects the
        authored shape and prefills the authored date.  D49 read this control
        as "unlocked for a value the app owns"; the app owns only the derived
        half, and the row shows the half that is the owner's.  The standing
        payment beside it is the locked control the cases above grade, so the
        two rows on one loan are told apart by the identity and not by the
        destination.
        """
        with app.app_context():
            loan = _loan(seed_user)
            first = _payment_into(seed_user, loan)
            first.name = f"App-bounded payment {loan.id}"
            db.session.commit()
            second = _payment_into(seed_user, loan)
            authored = date(2027, 3, 1)
            _cache_the_bound(second, authored, _ctx(seed_user))
            ctx = _ctx(seed_user)
            assert is_standing_loan_payment(first, ctx)
            assert not is_standing_loan_payment(second, ctx)

            body = auth_client.get(f"/transfers/{second.id}/edit").data.decode()

            select_tag, options = _ends_select(body)
            assert "disabled" not in select_tag
            assert len(options) == 3, options
            assert _selected_mode(body) == EndsOnDate.token
            date_input_start = body.index('id="end_date"')
            date_input = body[
                body.rindex("<input", 0, date_input_start):body.index(">", date_input_start)
            ]
            assert f'value="{authored.isoformat()}"' in date_input
            assert "disabled" not in date_input


class TestABaselineLessOwnerIsRefusedTheLoanPaymentsForm:
    """Ruling **R-R30**: the FORM READ path refuses, as every scenario producer does.

    A loan payment's derived stop is a fold in the owner's baseline scenario;
    with no baseline there is nothing to fold and the seam's own guard raises
    ``BaselineMissingError`` to the single application-level handler, which
    answers the recovery page.  The early return this replaces left the
    last-written column standing, and once the form reads the door there is
    nothing stored for it to stand on.  A definition no loan bounds still
    renders for such an owner: the not-a-loan answer is reached first.
    """

    @staticmethod
    def _drop_the_baseline(seed_user):
        """Demote the owner's baseline scenario, the way the policy suite does."""
        scenario = db.session.get(Scenario, seed_user["scenario"].id)
        scenario.is_baseline = False
        db.session.commit()

    def test_the_standing_payments_form_is_the_recovery_page(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The loan payment's edit GET answers the setup card, status 200."""
        with app.app_context():
            loan = _loan(seed_user)
            tpl = _payment_into(seed_user, loan)
            self._drop_the_baseline(seed_user)

            resp = auth_client.get(f"/transfers/{tpl.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Setup Incomplete" in body
            assert 'id="recurrence_end_mode"' not in body

    def test_a_savings_transfers_form_still_renders(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The control: no loan behind it, so nothing asks for a scenario."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            self._drop_the_baseline(seed_user)

            resp = auth_client.get(f"/transfers/{tpl.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Setup Incomplete" not in body
            select_tag, options = _ends_select(body)
            assert "disabled" not in select_tag
            assert len(options) == 3


class TestTheIdentityIsReadOffThePass:
    """Plan ledger row **N-511**: the lookup the pass already holds runs once."""

    def test_a_form_render_looks_the_standing_payment_up_once(
        self, app, seed_user, seed_periods, monkeypatch,
    ):
        """Locks, door arm and resolver share ONE memoised lookup per pass.

        ``owns_validity_window`` ran ``active_recurring_transfer_template``
        itself, after the loan resolution had already memoised the same
        lookup for the resolver -- N-511's measurement, two per
        loan-destination render, which this counter could not have taken (the
        old predicate reached the query through an import alias this patch
        does not see).  What it grades is the NEW path: counted at the query's
        own definition -- ``active_recurring_transfer_templates``, the ONE
        query for every definition into the loan since plan step R16-b-2,
        which the pass's resolution reaches by name at call time and from
        whose first row the standing payment is read; the control is a SECOND
        pass, on which the count must move, so a counter that never fires
        cannot pass this.
        """
        with app.app_context():
            loan = _loan(seed_user)
            tpl = _payment_into(seed_user, loan)
            calls = []
            real = recurring_transfer_query.active_recurring_transfer_templates

            def counting(account_id, user_id):
                calls.append(account_id)
                return real(account_id, user_id)

            monkeypatch.setattr(
                recurring_transfer_query,
                "active_recurring_transfer_templates",
                counting,
            )
            ctx = _ctx(seed_user)

            with app.test_request_context():
                state = edit_form_recurrence_state(tpl, ctx, authorable_cadences())

            assert state.selected_end.locked and state.selected_start.locked
            assert state.selected_end.stop_phrase == "until Jul 01, 2028"
            assert len(calls) == 1, (
                f"one form render looked the standing payment up {len(calls)} "
                f"times on one pass"
            )
            balance_at.loan_figures(loan, _ctx(seed_user))
            assert len(calls) == 2, "the counter must fire on a fresh pass"


class TestTheInvertedWindowRefusalReadsTheDoor:
    """The refusal grades the bound the DOOR reads, and carries no skip."""

    def test_a_second_transfers_authored_bound_is_still_graded(
        self, app, seed_user, seed_periods,
    ):
        """The arm answers ``NEVER_ENDS`` for the standing payment ALONE.

        A second transfer into the same loan authored a stop; moving its start
        past that stop is refused, exactly as it was before the skip went.
        Without this control the deleted skip could have been replaced by an
        arm that read EVERY loan-destination transfer's column as the cache,
        and an owner's word would invert unrefused.
        """
        with app.app_context():
            loan = _loan(seed_user)
            first = _payment_into(seed_user, loan)
            first.name = f"App-bounded payment {loan.id}"
            db.session.commit()
            second = _payment_into(seed_user, loan)
            authored = date(2027, 3, 1)
            ctx = _ctx(seed_user)
            _cache_the_bound(second, authored, ctx)
            starts_on_before = second.recurrence_rule.starts_on
            assert not is_standing_loan_payment(second, ctx), (
                "precondition: this is NOT the definition whose column is "
                "the cache, so its refusal must be the inverted-window one"
            )
            moved_to = date(2027, 6, 1)

            with app.test_request_context():
                refusal = resolve_recurrence_rule_for_update(
                    second,
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH, starts_on=moved_to,
                        ),
                        "starts_on": moved_to,
                    },
                    ctx=_form_ctx(None),
                    pass_ctx=ctx,
                )
                flashed = get_flashed_messages()

            assert refusal is not None, (
                "an owner's stop moved below the start was accepted: the arm "
                "read a second transfer's column as the cache"
            )
            # THIS refusal and not the derived-bound one: the two are told
            # apart by their message, and only the inverted-window door says
            # the stop precedes the start.
            assert end_bound_before_start_message(authored, moved_to) in flashed, (
                flashed
            )
            assert second.recurrence_rule.starts_on == starts_on_before

    def test_the_standing_payment_can_store_no_inverted_pair_to_refuse(
        self, app, seed_user, seed_periods,
    ):
        """The state the deleted skip existed for is unconstructible (plan step R7d-g).

        Until R7d-g the sync wrote an inverted pair for a loan cleared before
        its first installment, the door read that column as the cache, and
        this case held that the refusal let an ordinary cadence edit through
        on it.  Nothing writes that pair now: the write door refuses it off
        the values it would store (``EmptyAuthoredWindowError``, the one
        comparison), and ``ck_recurrence_rules_valid_window`` stands behind
        the door for a writer that never sees it (graded in
        ``test_recurrence_window_check``).  So the refusal never meets the
        pair and carries no arm for it.
        """
        with app.app_context():
            loan = _loan(seed_user)
            tpl = _payment_into(seed_user, loan)
            ctx = _ctx(seed_user)
            inverted = tpl.recurrence_rule.starts_on - date.resolution

            with pytest.raises(EmptyAuthoredWindowError):
                _cache_the_bound(tpl, inverted, ctx)
            db.session.rollback()
            assert tpl.recurrence_rule.end_date is None


class TestAnUnrelatedEditLeavesTheStoredBoundAlone:
    """The re-author carries the stored closing bound through untouched."""

    def test_a_cadence_edit_writes_the_stored_bound_back_unchanged(
        self, app, seed_user, seed_periods,
    ):
        """``update_recurrence_rule_from_form`` re-authors the stored bound on every edit.

        For the standing payment the form locks the control and posts nothing
        about it, so the re-author carries the stored value through
        byte-identical -- the owner's word since plan step R7d-g (ruling
        **R-R82**), where until then it was the chokepoints' cache and this
        was the one reader of the column a NULL-the-column census could not
        see.  Graded at the HELPER, not through the route: the route
        regenerates after the write, and a route-level case would read that
        second producer.
        """
        with app.app_context():
            loan = _loan(seed_user)
            tpl = _payment_into(seed_user, loan)
            ctx = _ctx(seed_user)
            _cache_the_bound(tpl, _STALE, ctx)

            with app.test_request_context():
                outcome = resolve_recurrence_rule_for_update(
                    tpl,
                    validated_cadence(
                        unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                    ),
                    ctx=_form_ctx(None),
                    pass_ctx=ctx,
                )
            db.session.flush()

            assert outcome is None
            assert tpl.recurrence_rule.end_date == _STALE
            assert tpl.recurrence_rule.max_occurrences is None
