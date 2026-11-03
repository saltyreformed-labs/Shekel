"""Every door where a definition BECOMES a loan's standing payment derives its start.

Plan step **R7d-g-2**, ruling **R-R85** (the ONE entry helper) with rulings
**R-R81** (the standing payment's start is the contract's, a second
transfer's its owner's) and **R-R82** (a stop the owner authored earlier is
honoured, never rewritten).  Under ruling **R-R29** the standing payment's
``starts_on`` is a stored derived value; R7d-g-1 left its maintenance
contract with holes -- healed at the next params edit -- and this leaf closes
them at the doors themselves.  Each door, once its write is flushed, calls
``_loan_destination.sync_loan_payment_start_or_refuse``, which asks the seam
who is standing NOW and brings that definition's start onto the contract:

* ARCHIVE and HARD-DELETE (both arms) take the standing payment out of the
  active set, so the next-oldest transfer into the loan is PROMOTED;
* UNARCHIVE puts a definition back -- standing again, or newly when it is
  older than the live payment, since the seam names the oldest active one;
* the transfer UPDATE door re-points the standing payment's cadence, and a
  unit change (every paycheck to monthly) would otherwise store a payday as
  the monthly day.

**A moved definition's ROWS follow its rule** (developer 2026-09-13, after
this leaf's adversarial review): where the sync moved a start, the transfer
maintain pass runs for that definition from today, so a promoted or restored
transfer's rows sit on the contract's dates and never beside them.

The one refusal is the window CHECK's: a promoted or restored definition
whose owner-authored stop the re-derived start would pass refuses the door
WHOLE, naming the transfer, and writes nothing.  Every case here reads the
stored rule back and, for the refusals, the door's every other effect too
(a refusal that flashed and archived anyway would pass a status check).

The same dates as the sibling modules: a mortgage originating 2026-04-15,
payment day 1, first installment 2026-05-01; today frozen ahead of both
inside the schedule ``seed_periods`` builds (2026-01-02 .. 2026-05-21).
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import STALE_ACTION_MESSAGE
from app.services.loan_recurrence_sync import LOAN_START_WOULD_PASS_AN_AUTHORED_STOP
from app.services.pay_calendar import calendar_for
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services import transfer_recurrence
from app.services.recurrence import (
    NEVER_ENDS,
    EndsOnDate,
    RecurrenceSpec,
    author_rule,
    reauthor_rule,
)
from tests._test_helpers import (
    amount_box_payload,
    cadence_payload,
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    state_template_price,
)
from tests._test_helpers import newest_price

#: Frozen so "today" cannot drift past the schedule ``seed_periods`` builds.
_TODAY = date(2026, 3, 1)

#: The loan's origination and the first installment its contract implies.
_ORIGINATION = date(2026, 4, 15)
_FIRST_INSTALLMENT = date(2026, 5, 1)

#: A second transfer's owner-typed start: after origination, so nothing but
#: the derivation under test can move it.
_OWNERS_START = date(2026, 5, 15)

#: A stop the owner authored on the second transfer, AFTER the loan's first
#: installment, so promotion honours it (R-R82) and nothing inverts.
_OWNERS_STOP = date(2027, 6, 1)

#: A stop BEFORE the loan's first installment (and after the owner's own
#: start of 2026-04-20): valid for a second transfer, inverted the moment
#: its start becomes the contract's -- the ONE state the window CHECK
#: refuses that a lifecycle door can reach.
_EARLY_START = date(2026, 4, 20)
_STOP_BEFORE_FIRST_INSTALLMENT = date(2026, 4, 25)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today ahead of the loan's origination."""
    freeze_today(monkeypatch, _TODAY)


def _mortgage(seed_user, name="Mortgage"):
    """Return a mortgage originating after today, payment day 1."""
    return create_loan_account(
        seed_user, db.session, name=name,
        principal=Decimal("200000.00"), rate=Decimal("0.05000"),
        term=360, origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


def _standing_payment(seed_user, loan):
    """Return the loan's standing payment: the loan door's own MONTHLY definition.

    ``make_loan_payment_template`` binds its start to the first contractual
    installment, which the precondition below asserts rather than assumes.
    """
    template = make_loan_payment_template(db.session, seed_user, loan)
    assert template.recurrence_rule.starts_on == _FIRST_INSTALLMENT, (
        "precondition: the standing payment starts at the first installment"
    )
    return template


def _second_transfer(seed_user, loan, name, *, starts_on, stop=None):
    """Return a FLUSHED monthly transfer into *loan* with its OWNER's bounds.

    A second transfer under ruling **R-R81**: its start is the owner's
    (*starts_on*) and its stop, when given, the owner's too -- authored
    through the write door, the way ``POST /transfers`` authors one for a
    loan that already holds a payment.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("50.00"))
    author_rule(
        RecurrenceSpec(
            user_id=seed_user["user"].id,
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=starts_on,
            end_bound=NEVER_ENDS if stop is None else EndsOnDate(on=stop),
        ),
        calendar_for(seed_user["user"].id),
        template,
    )
    return template


def _generate_rows(seed_user, template):
    """Generate *template*'s rows across the owner's schedule, the create path's way."""
    ctx = BalanceContext.build(seed_user["user"].id)
    transfer_recurrence.generate_for_template(
        template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
    )


def _live_dates_of(template_id):
    """Return the template's live projected occurrence dates, ascending."""
    return sorted(row.occurs_on for row in _live_rows_of(template_id))


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _reload(template_id):
    """Return the template's row as the request left it, or ``None``."""
    db.session.expire_all()
    return db.session.get(TransferTemplate, template_id)


def _refusal_sentence(template, *, starts_on, stop):
    """Return the sentence the door flashes for *template*'s inverted pair."""
    return LOAN_START_WOULD_PASS_AN_AUTHORED_STOP.format(
        name=template.name,
        starts_on=starts_on.strftime("%b %-d, %Y"),
        stop=stop.strftime("%b %-d, %Y"),
    )


def _live_rows_of(template_id):
    """Return the template's projected, non-deleted transfers."""
    return (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template_id,
            Transfer.is_deleted.is_(False),
        )
        .all()
    )


class TestArchivePromotesTheNextOldest:
    """POST /transfers/<id>/archive: the standing payment leaves, the next is promoted."""

    def test_the_promoted_transfers_start_becomes_the_contracts_and_its_stop_is_honoured(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Archive the standing payment: the second transfer is standing now.

        Its start moves from the owner's 2026-05-15 to the loan's first
        installment (ruling **R-R81**); its owner-authored stop of 2027-06-01
        stays exactly as stored (ruling **R-R82**: nothing but an owner's
        submission writes that column).  The archive's own effects hold too:
        the standing payment is inactive with its projected rows retired.

        NEGATIVE CONTROL: delete the ``sync_loan_payment_start_or_refuse``
        call from ``lifecycle._archive`` and the promoted start stays the
        owner's -- the hole this leaf closes.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(
                seed_user, loan, "Sweep", starts_on=_OWNERS_START, stop=_OWNERS_STOP,
            )
            db.session.commit()
            standing_id, second_id = standing.id, second.id

            resp = auth_client.post(f"/transfers/{standing_id}/archive")

            assert resp.status_code == 302, _flashes(auth_client)
            assert resp.headers["Location"].endswith("/transfers")
            assert _reload(standing_id).is_active is False
            promoted = _reload(second_id)
            assert promoted.is_active is True
            assert promoted.recurrence_rule.starts_on == _FIRST_INSTALLMENT
            assert promoted.recurrence_rule.end_date == _OWNERS_STOP
            assert promoted.recurrence_rule.max_occurrences is None

    def test_the_promoted_transfers_rows_follow_its_rule(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The second transfer's rows, generated on the 15th, move to the 1st.

        Before the archive its rows sit on the owner's dates; the promotion
        moves its rule to the loan's first installment, and the maintain
        pass the entry helper runs brings the rows along: every live row
        from today forward is on a 1st, none on a 15th, and none doubled.
        Until the review this door moved the rule and left the rows, so the
        grid and the Recurring surface disagreed until the next edit.

        NEGATIVE CONTROL: pass ``rows_follow=False`` from ``_archive`` and
        the 15th rows stay.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(seed_user, loan, "Sweep", starts_on=_OWNERS_START)
            _generate_rows(seed_user, second)
            db.session.commit()
            standing_id, second_id = standing.id, second.id
            before = _live_dates_of(second_id)
            assert before and all(d.day == 15 for d in before), before

            resp = auth_client.post(f"/transfers/{standing_id}/archive")

            assert resp.status_code == 302, _flashes(auth_client)
            after = _live_dates_of(second_id)
            assert after, "the promoted definition has no rows"
            assert all(d.day == 1 for d in after), after
            assert after[0] == _FIRST_INSTALLMENT
            assert len(after) == len(set(after)), "a date is doubled"

    def test_a_promoted_stop_the_contracts_start_would_pass_refuses_the_archive_whole(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The one pair ``ck_recurrence_rules_valid_window`` refuses, at this door.

        The second transfer's owner stop (2026-04-25) falls before the loan's
        first installment (2026-05-01): valid beside its own start, inverted
        the moment promotion makes its start the contract's.  The door is
        refused WHOLE, naming the transfer and both dates: the standing
        payment is still active with its rows, and the second transfer's
        pair is exactly as stored.

        NEGATIVE CONTROL: turn the helper's ``except`` into a pass-through
        and this is an ``IntegrityError`` out of the flush -- a 500.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(
                seed_user, loan, "Sweep",
                starts_on=_EARLY_START, stop=_STOP_BEFORE_FIRST_INSTALLMENT,
            )
            db.session.commit()
            standing_id, second_id = standing.id, second.id
            sentence = _refusal_sentence(
                second, starts_on=_FIRST_INSTALLMENT,
                stop=_STOP_BEFORE_FIRST_INSTALLMENT,
            )

            resp = auth_client.post(f"/transfers/{standing_id}/archive")

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/transfers")
            assert sentence in _flashes(auth_client), _flashes(auth_client)
            assert _reload(standing_id).is_active is True, (
                "the refusal flashed and archived anyway"
            )
            untouched = _reload(second_id).recurrence_rule
            assert untouched.starts_on == _EARLY_START
            assert untouched.end_date == _STOP_BEFORE_FIRST_INSTALLMENT

    def test_a_concurrent_edit_is_a_flash_not_a_500(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """The version-pinned UPDATE lands at the FIRST flush, under the guard.

        Until plan step R7d-g-2 the archive door guarded its commit alone,
        while the transfer service's soft-deletes flushed the ``is_active``
        write earlier -- so with one projected row to retire, a concurrent
        edit surfaced as an unhandled ``StaleDataError``.  The row's version
        is bumped out from under the request (between the load and the
        flush, through the loader the route reads it with) and the door
        answers with the stale flash and the list, nothing written.  The
        same probe against ``b58df005`` answered ``StaleDataError`` (measured
        while this leaf was built).

        NEGATIVE CONTROL: replace ``flush_or_handle_stale`` in ``_archive``
        with a bare ``db.session.flush()`` and this is a 500.
        """
        import app.routes.transfers.lifecycle as lifecycle  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            _generate_rows(seed_user, standing)
            db.session.commit()
            standing_id = standing.id
            assert _live_rows_of(standing_id), "precondition: a row to retire"
            real_get = lifecycle.get_or_404

            def load_then_race(model, row_id):
                template = real_get(model, row_id)
                with db.engine.connect() as conn:
                    conn.execute(
                        text(
                            "UPDATE budget.transfer_templates "
                            "SET version_id = version_id + 1 WHERE id = :id"
                        ),
                        {"id": row_id},
                    )
                    conn.commit()
                return template

            monkeypatch.setattr(lifecycle, "get_or_404", load_then_race)

            resp = auth_client.post(f"/transfers/{standing_id}/archive")

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/transfers")
            assert STALE_ACTION_MESSAGE.format(noun="recurring transfer") in _flashes(
                auth_client,
            )
            assert _reload(standing_id).is_active is True


class TestUnarchiveDerivesTheRestoredStart:
    """POST /transfers/<id>/unarchive: the restored definition is standing, so its start is derived."""

    def test_into_a_payment_less_loan_the_start_is_derived_and_the_stop_honoured(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """REC-522's start half, closed at the door that makes it standing.

        An archived transfer carrying its owner's start (it was a second
        transfer; the standing payment has since gone) is unarchived into a
        loan holding no active payment: it IS the standing payment now, its
        start becomes the contract's, and the stop its owner authored stays
        (ruling **R-R82**).

        NEGATIVE CONTROL: delete the ``sync_loan_payment_start_or_refuse``
        call from ``unarchive_transfer_template`` and the restored rule
        keeps the owner's start -- and regenerates from it.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            archived = _second_transfer(
                seed_user, loan, "Sweep", starts_on=_OWNERS_START, stop=_OWNERS_STOP,
            )
            archived.is_active = False
            db.session.commit()
            archived_id = archived.id

            resp = auth_client.post(f"/transfers/{archived_id}/unarchive")

            assert resp.status_code == 302, _flashes(auth_client)
            assert resp.headers["Location"].endswith("/transfers")
            restored = _reload(archived_id)
            assert restored.is_active is True
            assert restored.recurrence_rule.starts_on == _FIRST_INSTALLMENT
            assert restored.recurrence_rule.end_date == _OWNERS_STOP
            rows = _live_rows_of(archived_id)
            assert rows, "the restored definition generated nothing"
            assert min(row.occurs_on for row in rows) >= _FIRST_INSTALLMENT

    def test_restored_rows_on_the_owners_dates_are_not_doubled(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The review's finding: restored 15th rows beside new 1st rows, two debits a month.

        The transfer was archived THROUGH the door with rows on the 15th
        (soft-deleted, then restored by the unarchive); the sync moves its
        rule to the 1st; the maintain pass then retires the restored 15th
        rows from today forward and creates the 1st ones -- one row per
        month, on the contract's day.  A plain fill-forward left both.

        NEGATIVE CONTROL: replace ``regenerate_or_refuse`` in
        ``unarchive_transfer_template`` with ``generate_transfers_for_all_periods``
        and every month from today holds a 15th row AND a 1st row.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(seed_user, loan, "Sweep", starts_on=_OWNERS_START)
            _generate_rows(seed_user, second)
            db.session.commit()
            standing_id, second_id = standing.id, second.id
            assert auth_client.post(f"/transfers/{second_id}/archive").status_code == 302
            assert auth_client.post(f"/transfers/{standing_id}/hard-delete").status_code == 302
            assert _live_rows_of(second_id) == [], "precondition: archived rows are retired"

            resp = auth_client.post(f"/transfers/{second_id}/unarchive")

            assert resp.status_code == 302, _flashes(auth_client)
            after = _live_dates_of(second_id)
            assert after, "the restored definition has no rows"
            assert all(d.day == 1 for d in after), after
            months = [(d.year, d.month) for d in after]
            assert len(months) == len(set(months)), f"a month is doubled: {after}"

    def test_an_older_transfer_unarchived_beside_a_live_payment_is_standing_by_the_seams_word(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The case the emptiness reading would miss (rejected under R-R85).

        The archived transfer is OLDER than the live payment -- the usual
        shape, since a payment is archived to be replaced -- so once it is
        active again the seam names IT the standing payment (the oldest
        active transfer into the loan).  A door that derived only where the
        loan held no OTHER payment would leave the seam's standing payment
        with an owner's start until the next params edit; this door asks the
        seam who is standing after the flush and derives for that one.  The
        live payment, now second, keeps the contract's start it already had.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            older = _second_transfer(seed_user, loan, "Older", starts_on=_OWNERS_START)
            older.is_active = False
            db.session.flush()
            live = _standing_payment(seed_user, loan)
            db.session.commit()
            older_id, live_id = older.id, live.id
            assert older_id < live_id, "precondition: the archived one is older"

            resp = auth_client.post(f"/transfers/{older_id}/unarchive")

            assert resp.status_code == 302, _flashes(auth_client)
            assert _reload(older_id).recurrence_rule.starts_on == _FIRST_INSTALLMENT
            assert _reload(live_id).recurrence_rule.starts_on == _FIRST_INSTALLMENT

    def test_a_restored_stop_the_contracts_start_would_pass_refuses_the_unarchive_whole(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Refused whole: the transfer stays archived, its rows stay retired."""
        with app.app_context():
            loan = _mortgage(seed_user)
            archived = _second_transfer(
                seed_user, loan, "Sweep",
                starts_on=_EARLY_START, stop=_STOP_BEFORE_FIRST_INSTALLMENT,
            )
            archived.is_active = False
            db.session.commit()
            archived_id = archived.id
            sentence = _refusal_sentence(
                archived, starts_on=_FIRST_INSTALLMENT,
                stop=_STOP_BEFORE_FIRST_INSTALLMENT,
            )

            resp = auth_client.post(f"/transfers/{archived_id}/unarchive")

            assert resp.status_code == 302
            assert sentence in _flashes(auth_client), _flashes(auth_client)
            still = _reload(archived_id)
            assert still.is_active is False, "the refusal flashed and unarchived anyway"
            assert still.recurrence_rule.starts_on == _EARLY_START
            assert _live_rows_of(archived_id) == []


class TestHardDeletePromotesOnBothArms:
    """POST /transfers/<id>/hard-delete: both arms take the standing payment out of the active set."""

    def test_the_no_history_arm_deletes_the_standing_payment_and_promotes(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """No settled history: the template is destroyed, the second is promoted."""
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(
                seed_user, loan, "Sweep", starts_on=_OWNERS_START, stop=_OWNERS_STOP,
            )
            db.session.commit()
            standing_id, second_id = standing.id, second.id

            resp = auth_client.post(f"/transfers/{standing_id}/hard-delete")

            assert resp.status_code == 302, _flashes(auth_client)
            assert _reload(standing_id) is None
            promoted = _reload(second_id)
            assert promoted.recurrence_rule.starts_on == _FIRST_INSTALLMENT
            assert promoted.recurrence_rule.end_date == _OWNERS_STOP

    def test_the_history_arm_archives_the_standing_payment_and_promotes(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Settled history: archived instead, and the promotion runs through the same archive.

        The history predicate is answered as the route reads it
        (``archive_helpers.transfer_template_has_paid_history``), forced
        ``True`` the way the hard-delete defense-in-depth case forces it
        ``False``: what this case grades is that the fallback arm reaches
        ``_archive`` and its promotion, not how history is detected.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(
                seed_user, loan, "Sweep", starts_on=_OWNERS_START, stop=_OWNERS_STOP,
            )
            db.session.commit()
            standing_id, second_id = standing.id, second.id

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "app.routes.transfers.lifecycle.archive_helpers.transfer_template_has_paid_history",
                lambda _template_id: True,
            )
            resp = auth_client.post(f"/transfers/{standing_id}/hard-delete")

        with app.app_context():
            assert resp.status_code == 302, _flashes(auth_client)
            assert any("archived instead" in m for m in _flashes(auth_client))
            fallen_back = _reload(standing_id)
            assert fallen_back is not None and fallen_back.is_active is False
            promoted = _reload(second_id)
            assert promoted.recurrence_rule.starts_on == _FIRST_INSTALLMENT
            assert promoted.recurrence_rule.end_date == _OWNERS_STOP

    def test_a_promoted_stop_the_contracts_start_would_pass_refuses_the_delete_whole(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Refused whole on the no-history arm: the template survives, its rule untouched."""
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = _standing_payment(seed_user, loan)
            second = _second_transfer(
                seed_user, loan, "Sweep",
                starts_on=_EARLY_START, stop=_STOP_BEFORE_FIRST_INSTALLMENT,
            )
            db.session.commit()
            standing_id, second_id = standing.id, second.id
            sentence = _refusal_sentence(
                second, starts_on=_FIRST_INSTALLMENT,
                stop=_STOP_BEFORE_FIRST_INSTALLMENT,
            )

            resp = auth_client.post(f"/transfers/{standing_id}/hard-delete")

            assert resp.status_code == 302
            assert sentence in _flashes(auth_client), _flashes(auth_client)
            survivor = _reload(standing_id)
            assert survivor is not None and survivor.is_active is True, (
                "the refusal flashed and deleted anyway"
            )
            assert _reload(second_id).recurrence_rule.starts_on == _EARLY_START


class TestTheUpdateDoorTranslatesTheServicesRefusal:
    """POST /transfers/<id>: a second transfer's paycheck-dated row the service refuses is a flash."""

    def test_an_every_paycheck_second_transfer_dated_before_origination_is_a_flash_not_a_500(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Ruling R-R81's typed-date refusal passes; the service's row-date floor refuses.

        An every-paycheck rule dates its rows at the START of their paycheck
        (``compute_due_date`` rule 3).  A start typed the day after the
        loan's origination (2026-04-16) passes the door, but its first row
        lands on the start of the paycheck holding it -- before the
        origination -- and ``transfer_service`` refuses it (ruling **R-C**).
        The update door's regeneration translates that into the edit form's
        flash with the service's sentence; nothing is persisted.  Until this
        leaf's review the refusal escaped the door as a 500.

        NEGATIVE CONTROL: replace ``regenerate_or_refuse`` in
        ``templates._regenerate_and_commit_template`` with the bare chooser
        call and this raises ``ValidationError`` out of the request.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            _standing_payment(seed_user, loan)
            one_off = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=loan.id,
                name="One-off",
            )
            db.session.add(one_off)
            db.session.flush()
            state_template_price(one_off, Decimal("50.00"))
            db.session.commit()
            period = calendar_for(seed_user["user"].id).period_containing(
                date(2026, 4, 16),
            )
            assert period is not None and period.start_date <= _ORIGINATION, (
                "precondition: the paycheck holding Apr 16 starts on or before "
                f"origination, got {period}"
            )

            resp = auth_client.post(f"/transfers/{one_off.id}", data={
                "name": one_off.name,
                "amount": str(newest_price(one_off)),
                "from_account_id": str(one_off.from_account_id),
                "to_account_id": str(loan.id),
                "version_id": str(one_off.version_id),
                **cadence_payload(unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 4, 16)),
            })

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith(f"/transfers/{one_off.id}/edit")
            assert any(
                "before it originates" in message for message in _flashes(auth_client)
            ), _flashes(auth_client)
            assert _reload(one_off.id).recurrence_rule is None, "the refusal persisted a rule"


class TestTheUpdateDoorReadsTheSeamsIdentity:
    """An OLDER repeating transfer moved onto a paid loan is the seam's standing payment."""

    def test_an_older_transfer_moved_onto_a_paid_loan_is_derived(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The door reads 'would it be the oldest active', not 'is the loan empty'.

        A savings transfer created BEFORE the loan's payment is moved onto
        the loan: the seam names it standing (the oldest active transfer into
        the loan), so the door derives its start rather than writing the
        owner's for the sync to overwrite one line later (developer
        2026-09-13, after this leaf's review).  Its stored stop is the
        create door's reading, ruling **R-R77**: nothing posted writes the
        unbounded rule.

        NEGATIVE CONTROL: make ``would_be_standing_payment`` answer emptiness
        alone and the start is stored as typed, then re-derived by the sync
        in the same request -- the stored start still reads the first
        installment, but the door's own settle writes the owner's date first
        and the closing bound stays the owner's; assert the stop.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            savings = _second_transfer(
                seed_user, loan, "Older", starts_on=_OWNERS_START, stop=_OWNERS_STOP,
            )
            db.session.flush()
            live = _standing_payment(seed_user, loan)
            db.session.commit()
            assert savings.id < live.id, "precondition: the moved one is older"
            # Park it on savings first so the move is a real destination change.
            from tests._test_helpers import create_account_of_type  # pylint: disable=import-outside-toplevel
            parked = create_account_of_type(
                seed_user, db.session, "Savings", "Sav", anchor_balance=Decimal("100.00"),
            )
            savings.to_account_id = parked.id
            db.session.commit()
            older_id = savings.id

            resp = auth_client.post(f"/transfers/{older_id}", data={
                "name": savings.name,
                "amount": str(newest_price(savings)),
                "from_account_id": str(savings.from_account_id),
                "to_account_id": str(loan.id),
                "version_id": str(savings.version_id),
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH, starts_on=_OWNERS_START),
            })

            assert resp.status_code == 302, _flashes(auth_client)
            assert resp.headers["Location"].endswith("/transfers"), _flashes(auth_client)
            moved = _reload(older_id)
            assert moved.to_account_id == loan.id
            assert moved.recurrence_rule.starts_on == _FIRST_INSTALLMENT
            assert moved.recurrence_rule.end_date is None, (
                "the standing branch writes the unbounded rule (R-R77)"
            )


class TestTheUpdateDoorReDerivesAfterACadenceUnitEdit:
    """POST /transfers/<id>: the standing payment's unit moves, its start follows the contract."""

    def _post_unit(self, auth_client, template, unit):
        """POST the locked standing payment's form with *unit* chosen.

        What the form emits: every enabled control and nothing else (the
        Amount box only where the form shows one, :func:`amount_box_payload`) -- no
        ``to_account_id`` (pinned), no ``starts_on`` and no "Ends" keys (both
        rows server-locked).
        """
        return auth_client.post(f"/transfers/{template.id}", data={
            "name": template.name,
            **amount_box_payload(template),
            "from_account_id": str(template.from_account_id),
            "version_id": str(template.version_id),
            **cadence_payload(unit=unit, states_a_start=False),
        })

    def test_every_paycheck_to_monthly_lands_on_the_first_installment(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """PERIOD -> MONTH: the stored payday would become the monthly day.

        An every-paycheck standing payment stores the payday the resolver
        normalised the first installment onto (not the 1st); re-pointed to a
        monthly cadence with its locked start left alone, the rule would fire
        on that payday's day of the month forever.  The door re-derives after
        the write: the monthly rule starts on the first installment.

        NEGATIVE CONTROL: delete the ``sync_loan_payment_start_or_refuse``
        call from ``templates._regenerate_and_commit_template`` and the
        monthly rule keeps the payday.
        """
        with app.app_context():
            loan = _mortgage(seed_user)
            standing = make_loan_payment_template(db.session, seed_user, loan)
            # Re-cut to every paycheck from the first installment, the way
            # the door leaves such a payment: the stored start is the payday
            # the resolver normalised that date onto.
            reauthor_rule(
                standing.recurrence_rule,
                RecurrenceSpec(
                    user_id=seed_user["user"].id,
                    unit=RecurrenceUnitEnum.PERIOD,
                    starts_on=_FIRST_INSTALLMENT,
                ),
                calendar_for(seed_user["user"].id),
            )
            db.session.commit()
            payday = standing.recurrence_rule.starts_on
            assert payday != _FIRST_INSTALLMENT, (
                "precondition: an every-paycheck rule stores the payday"
            )

            resp = self._post_unit(auth_client, standing, RecurrenceUnitEnum.MONTH)

            assert resp.status_code == 302, _flashes(auth_client)
            assert resp.headers["Location"].endswith("/transfers"), _flashes(auth_client)
            rule = _reload(standing.id).recurrence_rule
            assert rule.starts_on == _FIRST_INSTALLMENT
            assert rule.end_date is None
