"""
Shekel Budget App -- A definition's edit is judged on the rows its own regeneration leaves

Plan step ``pay_calendar:C18-a``, the round-7 review's M1.  The edit doors
refuse a save that leaves a still-projected row of the definition inside the
books of the account it sits on (rulings **R-PC91** and **R-PC99**), and the
state a save leaves includes the regeneration it runs: a row that pass brings
into line takes the rewritten due day and accounts (in the paycheck it
keeps), a row it retires is gone, and every other row -- a paycheck ending
before the edit's effective date, a row the pass keeps as a conflict -- is
left as stored.  Asked of the STORED rows instead, one save that unticked an
envelope and moved its due day was refused over a day the save moves the row
off, where the same two edits as two saves passed (a regression against
``53d463ba``).

**That regression's lever went at plan step recurrence:R5-a**: the rule's
due day was dropped (ruling **R-R96**), and a row is now due on its
occurrence or its funding payday.  So the three cases that moved the due day
now MOVE THE ACCOUNT instead, the other half of what a rewrite carries, and
the rewritten DUE DAY is graded by the one save that still re-dates a row the
pass keeps: a switch of the paycheck that funds it (developer, 2026-09-24,
rule 5).

The refusal reads the pass's own decision
(``RecurrenceConflictKind.preview_fn``), never a second spelling of its
window, and each case below fails under one of the shortcuts that would
re-spell it: judging every row as stored (the regression), judging a
rewritten row on its stored due day, skipping every rewritten row, ignoring
the effective date, treating a row the pass keeps as a conflict as
rewritten, asking a retired row, or naming the books of the account a
rewritten row leaves.

Dates are the fixed ``seed_periods`` calendar (paychecks from 2026-01-02,
every 14 days), so the effective date each save states picks the window:
02-27..03-12 is the fifth paycheck, 03-13..03-26 the sixth.  Every POST
carries what the edit form renders, the envelope box only when ticked.
"""

from datetime import date
from decimal import Decimal

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services.amount_ownership import state_own_amount
from app.utils.balance_predicates import is_projected_clause
from tests._test_helpers import (
    cadence_payload,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from tests.test_routes.test_an_orphan_is_judged_by_its_own_day import (
    _open_books_directly,
)
from tests.test_routes.test_archived_rows_bound_the_books import _flashed
from tests.test_routes.test_definition_edit_strands_no_row import (
    _account_opened_on,
    _live_row_answering,
    _live_rows,
    _reload,
    _schedule,
    _transaction_template_with_rows,
    _transaction_update_payload,
    _transfer_update_payload,
)
from tests.test_services.test_opening_restatement_planned_rows import (
    _account_opened_early,
)

# The fifth paycheck's first and last days, and the sixth's first.
_FIFTH_PAYCHECK = date(2026, 2, 27)
_FIFTH_PAYCHECK_ENDS = date(2026, 3, 12)
_SIXTH_PAYCHECK = date(2026, 3, 13)

_REFUSED = "This change cannot be saved"
_INSIDE_IT = (
    "An opening is the balance at the END of its day, so that unpaid item "
    "would sit inside it.  Mark it paid or cancel it first, then make the "
    "change."
)


def _groceries_on_books_opening_on_its_payday(seed_user):
    """A monthly $100 Groceries envelope from 02-27, on an account whose books open 02-27.

    Its first row answers 02-27, in the paycheck 02-27..03-12, stored due
    02-27.  As an envelope it is compared on its paycheck's last day, 03-12,
    after the opening, so nothing sits inside the books before the edit.

    Returns:
        ``(account, template)``.
    """
    account = _account_opened_on(seed_user, "Envelope books", _FIFTH_PAYCHECK)
    template = _transaction_template_with_rows(
        seed_user, "Groceries", account_id=account.id, is_envelope=True,
        cadence=MONTHLY, starts_on=_FIFTH_PAYCHECK,
    )
    return account, template


def _untick_and_move_to(template, account, effective_from):
    """The one save: the envelope box unticked AND the account moved, from *effective_from*."""
    payload = _transaction_update_payload(
        template, unit=RecurrenceUnitEnum.MONTH, account_id=str(account.id),
        effective_from=effective_from.isoformat(),
    )
    del payload["is_envelope"]
    return payload


class TestTheEnvelopeUntickedAndMoved:
    """The measured regression and its control: one edit, two effective dates."""

    def test_one_save_the_pass_moves_off_the_books_is_saved(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """From 02-27 the pass moves the 02-27 row onto Early books, whose books open before it.

        Judged as stored it was a bill due 02-27 on Envelope books, inside
        them, and refused; the same two edits made as two saves, the account
        move first, are accepted.  Saved, every row is a bill on Early books,
        due as before.
        """
        with app.app_context():
            _account, template = _groceries_on_books_opening_on_its_payday(seed_user)
            early = _account_opened_early(seed_user, name="Early books")

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_untick_and_move_to(template, early, _FIFTH_PAYCHECK),
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _REFUSED.encode() not in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_envelope is False
            assert saved.account_id == early.id
            rows = _live_rows(saved)
            assert {row.account_id for row in rows} == {early.id}
            assert sorted(row.due_date for row in rows) == [
                date(2026, 2, 27), date(2026, 3, 27), date(2026, 4, 27),
            ]

    def test_the_same_save_from_the_next_paycheck_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """From 03-13 no pass reaches the 02-27 row: a bill left on Envelope books, inside them.

        The edit door's own-day question is KEPT for the rows the save
        leaves as stored -- deleting it reopens a $100 counted twice.
        """
        with app.app_context():
            account, template = _groceries_on_books_opening_on_its_payday(seed_user)
            early = _account_opened_early(seed_user, name="Early books")

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_untick_and_move_to(template, early, _SIXTH_PAYCHECK),
                follow_redirects=True,
            )

            assert _flashed(
                f'{_REFUSED}: "Groceries" is still projected and due '
                f"2026-02-27, and Envelope books's books open 2026-02-27.  "
                f"{_INSIDE_IT}"
            ).replace(b'"', b"&#34;") in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_envelope is True
            assert saved.account_id == account.id

    def test_a_row_the_pass_keeps_as_a_conflict_is_judged_as_stored(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The owner re-priced the 02-27 row, so the pass keeps it where it is: refused.

        An overridden row is a conflict the regeneration does not rewrite,
        so after this save it is a bill still due 02-27 on Envelope books,
        inside them.
        """
        with app.app_context():
            account, template = _groceries_on_books_opening_on_its_payday(seed_user)
            row = _live_row_answering(template, _FIFTH_PAYCHECK)
            state_own_amount(row, Decimal("120.00"))
            row.is_override = True
            _db.session.commit()
            early = _account_opened_early(seed_user, name="Early books")

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_untick_and_move_to(template, early, _FIFTH_PAYCHECK),
                follow_redirects=True,
            )

            assert _flashed(
                f'{_REFUSED}: "Groceries" is still projected and due '
                f"2026-02-27, and Envelope books's books open 2026-02-27.  "
                f"{_INSIDE_IT}"
            ).replace(b'"', b"&#34;") in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_envelope is True
            assert saved.account_id == account.id


class TestTheEnvelopeUntickedAndReFunded:
    """The rewrite's DUE DAY is judged too: the one save that still re-dates a kept row."""

    def test_one_save_the_pass_re_dates_past_the_books_is_saved(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """From 02-27 the pass re-dates the 03-01 row onto 03-13, outside books opening 03-01.

        A monthly envelope scheduled 03-01, funded from the paycheck
        CONTAINING it (02-27..03-12), due 03-01 and compared on 03-12.  One
        save unticks it and funds it from the first paycheck starting on or
        after 03-01 instead: the pass rewrites the row due 03-13, that
        paycheck's payday (ruling R-R95).  Judged on its stored day it was a
        bill due 03-01, inside the books, and refused.  The saved rows'
        dates are NOT asserted: the rewrite leaves the row in 02-27..03-12
        while due 03-13, ledger row REC-537.
        """
        with app.app_context():
            account = _account_opened_on(
                seed_user, "Envelope books", date(2026, 3, 1),
            )
            template = _transaction_template_with_rows(
                seed_user, "Groceries", account_id=account.id, is_envelope=True,
                cadence=MONTHLY, starts_on=date(2026, 3, 1),
            )
            assert _live_row_answering(template, date(2026, 3, 1)).due_date == (
                date(2026, 3, 1)
            ), "precondition: the stored due day sits inside the books"
            payload = _transaction_update_payload(
                template, unit=RecurrenceUnitEnum.MONTH,
                placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                effective_from=_FIFTH_PAYCHECK.isoformat(),
            )
            del payload["is_envelope"]

            resp = auth_client.post(
                f"/templates/{template.id}", data=payload, follow_redirects=True,
            )

            assert resp.status_code == 200
            assert _REFUSED.encode() not in resp.data
            assert _reload(TransactionTemplate, template.id).is_envelope is False


class TestARowThePassRetires:
    """A row the save's own regeneration deletes is asked nothing about where it sits (R-PC92)."""

    def test_unticking_and_moving_the_day_retires_the_old_row_and_saves(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The rule moves to 03-03: the pass deletes the 02-27 row and writes one due 03-03.

        Judged as stored, the old row was a bill due 02-27 inside the books
        and the save was refused on both 53d463ba and checkpoint 10, though
        the save leaves no such row.
        """
        with app.app_context():
            _account, template = _groceries_on_books_opening_on_its_payday(seed_user)
            old_id = _live_row_answering(template, _FIFTH_PAYCHECK).id
            payload = _transaction_update_payload(
                template, unit=RecurrenceUnitEnum.MONTH,
                effective_from=_FIFTH_PAYCHECK.isoformat(),
            )
            payload.update(cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 3, 3),
            ))
            del payload["is_envelope"]

            resp = auth_client.post(
                f"/templates/{template.id}", data=payload, follow_redirects=True,
            )

            assert _REFUSED.encode() not in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_envelope is False
            assert _db.session.get(Transaction, old_id) is None
            assert sorted(row.due_date for row in _live_rows(saved)) == [
                date(2026, 3, 3), date(2026, 4, 3), date(2026, 5, 3),
            ]


class TestARowTheRewriteLeavesInItsPaycheck:
    """The pass rewrites a row's due day and account, never its paycheck."""

    def test_an_envelope_moved_into_an_early_paycheck_is_refused_on_its_new_books(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Onto books opening 03-12, the row the owner moved into 02-27..03-12 sits inside them.

        The state is the one a grid move leaves once the conflict chooser's
        "use" hands the row back to its definition: the 03-27 row sits in
        the paycheck ending 03-12, no longer overridden.  The account move
        from 02-27 rewrites it onto New books, due 03-27, but leaves it in
        that paycheck, so as an envelope it is compared on 03-12 -- inside
        New books's books.  Judged as stored (on Old account, whose books
        open early) or skipped as rewritten, the save went through.
        """
        with app.app_context():
            old = _account_opened_early(seed_user, name="Old account")
            template = _transaction_template_with_rows(
                seed_user, "Groceries", account_id=old.id, is_envelope=True,
                cadence=MONTHLY, starts_on=date(2026, 3, 27),
            )
            moved = _live_row_answering(template, date(2026, 3, 27))
            moved.pay_period_id = next(
                period.id for period in seed_periods
                if period.start_date == _FIFTH_PAYCHECK
            )
            _db.session.commit()
            new = _account_opened_on(seed_user, "New books", _FIFTH_PAYCHECK_ENDS)

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, unit=RecurrenceUnitEnum.MONTH,
                    account_id=str(new.id),
                    effective_from=_FIFTH_PAYCHECK.isoformat(),
                ),
                follow_redirects=True,
            )

            assert _flashed(
                f'{_REFUSED}: "Groceries" is still projected in the paycheck '
                f"ending 2026-03-12, and New books's books open 2026-03-12.  "
                f"{_INSIDE_IT}"
            ).replace(b'"', b"&#34;") in resp.data
            assert _reload(TransactionTemplate, template.id).account_id == old.id


def _sweep(seed_user, source, destination):
    """An every-paycheck $200 transfer from *source* to *destination*, its rows generated."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=source.id,
        to_account_id=destination.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep",
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template, Decimal("200.00"))
    make_cadence_rule(template, EVERY_PERIOD)
    transfer_recurrence.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    _db.session.commit()
    return template


def _sweep_inside_its_source_books(seed_user):
    """A sweep whose source's books were recorded as opening 02-27 after its rows were made.

    The rows due 01-02 through 02-27 then sit inside the source's books: a
    state no door writes (the restatement refuses it), recorded directly as
    the scope tests record a books opening, to grade the TRANSFER engine's
    preview -- a transfer the pass rewrites onto another account is judged
    there.

    Returns:
        ``(template, new_source)`` -- *new_source* an account whose books
        open before the schedule.
    """
    source = _account_opened_early(seed_user, name="Old source")
    destination = _account_opened_early(seed_user, name="Early savings")
    template = _sweep(seed_user, source, destination)
    _open_books_directly(source, _FIFTH_PAYCHECK)
    _db.session.commit()
    return template, _account_opened_early(seed_user, name="New source")


class TestTheTransferDoor:
    """The transfer edit door reads the transfer engine's preview."""

    def test_moving_every_row_off_the_books_is_saved(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """From the first paycheck the pass moves every sweep onto New source: saved."""
        with app.app_context():
            template, new_source = _sweep_inside_its_source_books(seed_user)

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, from_account_id=str(new_source.id),
                    effective_from=date(2026, 1, 2).isoformat(),
                ),
                follow_redirects=True,
            )

            assert _REFUSED.encode() not in resp.data
            assert {
                row.from_account_id
                for row in _db.session.query(Transfer).filter(
                    Transfer.transfer_template_id == template.id,
                    is_projected_clause(Transfer),
                    Transfer.is_deleted.is_(False),
                )
            } == {new_source.id}

    def test_rows_the_pass_does_not_reach_are_refused_where_they_sit(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """From 02-27 the sweeps due 01-02 to 02-13 stay on Old source, inside its books."""
        with app.app_context():
            template, new_source = _sweep_inside_its_source_books(seed_user)

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, from_account_id=str(new_source.id),
                    effective_from=_FIFTH_PAYCHECK.isoformat(),
                ),
                follow_redirects=True,
            )

            assert _flashed(
                f'{_REFUSED}: "Sweep" is still projected and due 2026-01-02, '
                f"and Old source's books open 2026-02-27.  {_INSIDE_IT}"
            ).replace(b'"', b"&#34;") in resp.data
            assert _reload(TransferTemplate, template.id).from_account_id != new_source.id
