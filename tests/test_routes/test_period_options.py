"""
Shekel Budget App -- the pay periods a row may be MOVED into (plan step C2-f1)

:func:`app.routes._period_options.period_move_options` replaced
``pay_period_service.get_current_and_future_periods``, whose three call sites
rendered the same ``<select>`` from the same query.  The first three tests here
are that reader's own cases, re-pointed at the rule that replaced it -- the
behaviour did not change, so neither did the assertions.

Two things did change and are pinned below rather than left to be
rediscovered:

* the CLOCK is the user's (``display_today``) where the query used the
  process's ``date.today()``.  All three call sites already computed
  ``display_today()`` two lines further down for the settle-day input's
  ``max``, so one render held two clocks that agree only because both compose
  files pin ``TZ: America/New_York``.  Nothing moves in the container; what
  changes is that the rule stops depending on that pin;
* the result is a plain LIST, not a
  :class:`~app.services.pay_calendar.PeriodWindow`.  Forcing an ended period
  back in leaves a hole, and a window refuses one -- so the type would refuse
  the very case the rule exists for.

``bare_periods`` is 10 biweekly periods from 2026-01-02: index 0 = Jan 2-15,
index 1 = Jan 16-29, index 2 = Jan 30 - Feb 12, and so on.
"""

from datetime import date, datetime, timezone

import pytest
import time_machine

from app.models.pay_period import PayPeriod
from app.routes import _period_options
from app.routes._period_options import period_move_options
from app.services.pay_calendar import (
    PayCalendarError,
    PeriodWindow,
    calendar_for,
)
from tests._test_helpers import (
    derived_span,
    last_covered_day,
    add_txn as _add_txn,
    select_option_values as _select_option_values,
)
from tests.test_routes.test_transfers._helpers import (
    _create_savings_account,
    _create_transfer,
)


@pytest.fixture()
def frozen_today(monkeypatch):
    """Pin the rule's clock so an "ended period" case is not a moving target.

    Returns:
        A setter taking the civil date :func:`period_move_options` should read.
    """
    def _set(day: date) -> None:
        monkeypatch.setattr(_period_options, "display_today", lambda: day)
    return _set


class TestTheOfferedPeriods:
    """The three cases ``get_current_and_future_periods`` carried."""

    def test_excludes_ended_periods(
        self, app, db, bare_user, bare_periods, frozen_today,
    ):
        """Periods whose end_date is before today are excluded.

        Today = 2026-02-01 sits in period 2 (Jan 30 - Feb 12); periods 0 and 1
        have ended, so only 2..9 are offered.
        """
        frozen_today(date(2026, 2, 1))
        with app.app_context():
            result = period_move_options(
                calendar_for(bare_user["user"].id), None,
            )
            assert [p.period_index for p in result] == [2, 3, 4, 5, 6, 7, 8, 9]

    def test_current_period_included_on_its_end_date(
        self, app, db, bare_user, bare_periods, frozen_today,
    ):
        """A period whose end_date equals today counts as current.

        Today = 2026-01-15 is period 0's end_date; ``end_date >= today``
        holds, so every period (0..9) is offered.
        """
        frozen_today(date(2026, 1, 15))
        with app.app_context():
            result = period_move_options(
                calendar_for(bare_user["user"].id), None,
            )
            assert [p.period_index for p in result] == list(range(10))

    def test_the_rows_own_period_is_forced_back_in(
        self, app, db, bare_user, bare_periods, frozen_today,
    ):
        """The row's own ended period returns without un-excluding its neighbours.

        With today in period 2 and the row sitting in period 0, the offer is
        ``[0, 2, 3, ..., 9]``: period 0 is forced back so the dropdown can keep
        it SELECTED, while period 1 -- also ended, and not the row's -- stays
        out.  Without the force the browser would default to the first option
        and the save would silently re-point the row to a period the user never
        chose.
        """
        frozen_today(date(2026, 2, 1))
        with app.app_context():
            result = period_move_options(
                calendar_for(bare_user["user"].id), bare_periods[0].id,
            )
            assert [p.period_index for p in result] == [
                0, 2, 3, 4, 5, 6, 7, 8, 9,
            ]


class TestWhatTheResultIsAndIsNot:
    """The two properties C2-f1 changed, each shown rather than asserted once."""

    def test_the_forced_period_leaves_a_hole_a_window_would_refuse(
        self, app, db, bare_user, bare_periods, frozen_today,
    ):
        """The reason this returns a list: the offer set is legitimately gapped.

        Period 0 is offered and period 1 is not, so the days period 1 covers
        belong to no offered option.  ``PeriodWindow`` refuses exactly that
        (its contiguity invariant), which is right for a reporting domain and
        wrong for a list of ``<option>`` rows -- the control here is that
        wrapping the same result in one RAISES.
        """
        frozen_today(date(2026, 2, 1))
        with app.app_context():
            result = period_move_options(
                calendar_for(bare_user["user"].id), bare_periods[0].id,
            )
            with pytest.raises(PayCalendarError) as exc:
                PeriodWindow(periods=tuple(result))
            assert "unbroken span" in str(exc.value)

    def test_it_reads_the_display_clock_not_the_process_clock(
        self, app, db, bare_user, bare_periods, frozen_today,
    ):
        """The offer follows ``display_today``, which is the correction C2-f1 made.

        **The two clocks are SPLIT here, so this fires on the defect rather
        than on the calendar date the suite happens to run**, and the split is
        asserted rather than assumed: the process clock is pinned in mid
        January, the display clock reads 2026-02-01, and the test computes
        what a rule reading the PROCESS clock would have answered and requires
        it to be a different list.

        **The pinned process day is deliberately not named.** ``time_machine``
        reads a naive datetime as UTC, so the LOCAL civil day it produces
        depends on the run's zone -- 2026-01-15 under UTC, 2026-01-16 under
        the ``Pacific/Kiritimati`` the CI clock gate uses.  A first cut
        asserted the day directly and failed under that gate, which is exactly
        the ``date.today()`` / ``display_today()`` mix the gate exists to
        catch, in the test rather than in the code.  Either day lands inside
        the schedule and disagrees with 2026-02-01, so the control holds in
        both zones.

        **The split does not happen in the deployed container**, and this test
        is what makes that a property of the code rather than of a deployment
        setting: both compose files pin ``TZ: America/New_York`` (the
        2026-06-12 parity audit's finding M01, taken because the image default
        of UTC flipped ``date.today()`` at 20:00 Eastern and did exactly this
        to this dropdown), so the two clocks agree there.  Splitting them here
        grades the rule where the pin does not reach -- CI, a script, a bare
        ``flask run`` -- and would catch the pin being dropped.
        """
        frozen_today(date(2026, 2, 1))
        with time_machine.travel(
            datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc), tick=False,
        ):
            with app.app_context():
                # What the retired ``get_current_and_future_periods`` would
                # answer: its predicate, on the process clock.
                process_day = date.today()
                on_the_process_clock = [
                    derived_span(p).period_index for p in bare_periods
                    if last_covered_day(p) >= process_day
                ]
                assert on_the_process_clock != [2, 3, 4, 5, 6, 7, 8, 9]

                result = period_move_options(
                    calendar_for(bare_user["user"].id), None,
                )
                assert [p.period_index for p in result] == [
                    2, 3, 4, 5, 6, 7, 8, 9,
                ]

    def test_an_owner_with_no_paydays_is_offered_nothing(
        self, app, db, bare_user_with_cadence, frozen_today,
    ):
        """An empty calendar answers an empty list rather than raising.

        The owner between their schedule row being written and their first
        batch landing -- the state ``pay_period_admin.reset_pay_periods``
        passes through.  The template's ``{% if periods %}`` then omits the
        selector entirely.

        **``bare_user_with_cadence`` rather than ``bare_user`` since plan step
        ``pay_calendar:C4-d``** (ruling R-PC45): ``calendar_for`` refuses an
        owner with no ``budget.pay_schedule`` row, so a bare owner would raise
        here and this case would stop being about ``period_move_options`` at
        all.  *Its docstring also cited "the state a brand-new owner is in
        since plan step X-ad-a stopped writing a bootstrap payday", which
        X-ad-a itself falsified when it shipped: registration writes the
        schedule row AND ``num_periods`` real paydays.*
        """
        frozen_today(date(2026, 2, 1))
        with app.app_context():
            assert period_move_options(
                calendar_for(bare_user_with_cadence["user"].id), None,
            ) == []

    def test_every_offered_period_carries_an_id_the_form_can_submit(
        self, app, db, bare_user, bare_periods, frozen_today,
    ):
        """The ``<option value>`` is ``period_id``, and it is never ``None``.

        ``DerivedPeriod.period_id`` is nullable in general -- it marks a span
        no foreign key can point at -- and the template submits it straight
        into ``transactions.pay_period_id``.  The offer comes off
        :meth:`PayCalendar.saved`, which filters to materialised periods, so
        every value here is an int.
        """
        frozen_today(date(2026, 2, 1))
        with app.app_context():
            result = period_move_options(
                calendar_for(bare_user["user"].id), None,
            )
            assert result
            assert all(isinstance(p.period_id, int) for p in result)
            assert all(p.label for p in result)


class TestTheRenderedSelect:
    """The popovers' ``<select>``, asserted as MARKUP rather than as a list.

    **Jinja does not raise on a missing attribute, it renders nothing.**  Plan
    step C2-f1 changed both templates from ``p.id`` to ``p.period_id``
    (``DerivedPeriod`` has no ``.id``), and a wrong name there emits
    ``value=""`` on every option -- a dropdown that silently submits an empty
    pay period, with no exception anywhere and every producer-level test still
    green.  Neither template had any markup assertion before this step, so
    these two are what make that change checkable at all.
    """

    def test_the_transaction_popover_offers_the_calendar_ids(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """Every ``<option value>`` is a real period id, and the row's is selected.

        ``seed_periods_today`` puts today in period 4, and the row is put in
        period 1 -- ended, so the ONLY reason it appears is the force.  The
        expected set is therefore ``[1, 4, 5, 6, 7, 8, 9]``: not a suffix, so
        an implementation that dropped the force or dropped the filter would
        both fail here.
        """
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[1],
                "Rent", "1200.00",
            )
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = [seed_periods_today[i] for i in (1, 4, 5, 6, 7, 8, 9)]
            assert _select_option_values(html, "pay_period_id") == [
                str(p.id) for p in expected
            ]
            assert (
                f'<option value="{seed_periods_today[1].id}" selected>'
                in html
            )
            # The label is the shared rule's, so the two types cannot render
            # one paycheck two ways (``utils.dates.pay_period_label``).  Read
            # off the CALENDAR since plan step C4-a-5, which deleted the ORM
            # accessor this line used to call: the derived value is what the
            # page renders, so it is what the assertion may build from.
            assert calendar_for(
                seed_user["user"].id,
            ).period_by_id(seed_periods_today[1].id).label in html

    def test_the_transfer_popover_offers_the_calendar_ids(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The transfer twin, whose own period is ended for the same reason.

        ``_create_transfer`` seats the transfer in period 0, which today has
        left; the force is what keeps it selectable, and without it the save
        would re-point the transfer at period 4.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = [seed_periods_today[i] for i in (0, 4, 5, 6, 7, 8, 9)]
            assert _select_option_values(html, "pay_period_id") == [
                str(p.id) for p in expected
            ]
            assert (
                f'<option value="{seed_periods_today[0].id}" selected>'
                in html
            )

    def test_the_transfer_popover_opened_from_a_grid_leg_agrees(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A grid LEG's cell renders the transfer form, from the leg's own ask.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**:
        ``routes/transactions/forms.get_full_edit`` had two branches and both
        offered periods, the transfer one reached by asking for the SHADOW
        row's id.  That branch is deleted; a leg's cell asks the transfer's
        own door with ``?leg_account_id=``, one door with one period offer,
        and this pins that the grid-origin ask offers the same periods the
        transfers page's does.  The leg is the from-side, named off the
        transfer where it was read off the first twin until plan step
        ``balance:X-bi-6-4d-2``, which births none (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.get(
                f"/transfers/{xfer.id}/full-edit?leg_account_id={xfer.from_account_id}",
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = [seed_periods_today[i] for i in (0, 4, 5, 6, 7, 8, 9)]
            assert _select_option_values(html, "pay_period_id") == [
                str(p.id) for p in expected
            ]


class TestTheCardNamesTheDERIVEDPaycheck:
    """The full-edit card's context line reads the calendar, not the column.

    Pay-calendar plan step **C4-a-5**.  The card printed
    ``txn.pay_period.label`` -- an ORM accessor that formatted the STORED
    ``budget.pay_periods.end_date`` -- while the period ``<select>`` two
    sections below it printed ``DerivedPeriod.label`` for the SAME paycheck.
    Wherever the stored end had gone stale (plan findings **P12** and **P28**
    both moved it), one card named one paycheck two ways.

    **The class lost its FIRING CONTROL at plan step ``pay_calendar:C4-c``,
    and it is worth saying so rather than leaving the survivor looking
    stronger than it is.**  Until that step a case could plant a stored end
    that disagreed with the derivation and assert the stale label appeared
    NOWHERE on the card -- absence being what distinguished the two readers,
    since both put a string in the page and only one was wrong.  C4-c dropped
    the column, ``PayPeriod.label`` went with it at C4-a-5, and there is no
    second label for a card to print: the plant is unconstructible and the
    absence has no subject.

    What survives is the positive property, and it is located rather than
    searched for so it cannot drift onto the ``<select>``'s own option text.

    *A deleted case, named because a gap is worse than a note*:
    ``test_the_context_line_shows_the_derived_span`` was the absence half.  It
    ran green after C4-c while measuring nothing -- ``period.end_date = ...``
    on a model that no longer maps the column sets a plain Python attribute,
    flushes nothing, and leaves the "stale" string one the page could never
    have contained.
    """

    def test_the_context_line_is_the_region_that_carries_it(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """Located rather than searched for, so the assertion cannot drift.

        It proves the DERIVED label is in the context line specifically,
        rather than only in the ``<select>`` that would still render it if the
        context line printed nothing at all.  *It used to say "the test above
        proves the stale label is nowhere on the card"; that sibling was
        deleted at plan step ``pay_calendar:C4-c`` with the plant it rested on,
        and this class's own docstring records why.*  Jinja renders a missing
        attribute as the empty string, so "nothing at all" is a real outcome
        here and not a hypothetical.
        """
        with app.app_context():
            own = db.session.get(PayPeriod, seed_periods_today[1].id)
            derived = calendar_for(seed_user["user"].id).period_by_id(own.id)
            txn = _add_txn(db.session, seed_user, own, "Rent", "1200.00")
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            html = resp.data.decode()

            # ANCHORED ON THE FIRST ``txn-card-meta`` DIV, which is the
            # context line (``grid/_transaction_full_edit.html``, the div
            # directly under the header).  The template holds three divs with
            # that class and this picks the first by DOCUMENT ORDER, not by
            # identity -- so a reorder would silently re-point this at the
            # Actual box's caption.  Named here rather than left implicit
            # (adversarial review of C4-a-5).
            start = html.index('class="txn-card-meta')
            context_line = html[start:html.index("</div>", start)]
            assert derived.label in context_line
