"""Template route tests: the Ends control, which writes a count or a date bound or neither, the
opening bound a create defaults and an edit states or leaves alone, the server's first render of
both, and the refusals the user reads.
"""

import re
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.routes._form_errors import GENERIC_VALIDATION_FLASH
from app.services.pay_calendar import calendar_for
from app.schemas.validation import end_bound_before_start_message
from app.services.recurrence import END_BOUND_KINDS, EndsAfterOccurrences, EndsOnDate, NeverEnds
from app.utils.dates import display_today
from tests._test_helpers import (
    cadence_payload,
    current_pay_period,
    end_bound_payload,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._definition_price import newest_price

from ._helpers import _TODAYS_PERIOD_INDEX


class TestTheEndsControlIsTheFirstWriterOfMaxOccurrences:
    """Plan step R7b-3: a rule can stop after a COUNT, and the form says so.

    ``budget.recurrence_rules.max_occurrences`` had been read by the
    occurrence walk since plan step R3 and written by NOTHING -- 0 of the 46
    live production rules carried one (measured 2026-08-13).  The "Ends"
    control is its first author, and it is ONE control for a bound with three
    shapes, so ``ck_recurrence_rules_single_end_bound`` is expressed by the
    form's shape rather than refused after it.
    """

    def _create(self, auth_client, seed_user, name, **extra):
        """POST a recurring expense template and return the response.

        Args:
            auth_client: The signed-in client.
            seed_user: The seeded owner fixture.
            name: The template's name.
            **extra: Additional form keys, typically an end-bound payload.

        Returns:
            The Flask test response.
        """
        txn_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        return auth_client.post("/templates", data={
            "name": name,
            "amount": "100.00",
            "category_id": seed_user["categories"]["Rent"].id,
            "transaction_type_id": txn_type.id,
            "account_id": seed_user["account"].id,
            **cadence_payload(),
            **extra,
        }, follow_redirects=True)

    def test_a_count_bound_reaches_the_column_and_stops_generation(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Ends after 3" writes the count AND generates exactly three rows.

        The column write alone would prove only that a value landed; what
        makes the control mean anything is that the occurrence walk stops --
        ``seed_periods_today`` builds ten biweekly periods, so an unbounded
        every-paycheck rule generates ten.
        """
        with app.app_context():
            resp = self._create(
                auth_client, seed_user, "Three Payments",
                **end_bound_payload(EndsAfterOccurrences(count=3)),
            )
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Three Payments",
            ).one()
            rule = template.recurrence_rule
            assert rule.max_occurrences == 3
            assert rule.end_date is None

            txns = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).all()
            assert len(txns) == 3

    def test_a_date_bound_still_reaches_its_own_column(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The shape that already existed is unmoved by gaining a sibling."""
        with app.app_context():
            # After today's period (index 4), so the rule this creates
            # can reach it -- see the comment on
            # ``_TODAYS_PERIOD_INDEX``.
            stop = seed_periods_today[_TODAYS_PERIOD_INDEX + 2].start_date
            self._create(
                auth_client, seed_user, "Until Then",
                **end_bound_payload(EndsOnDate(on=stop)),
            )

            rule = db.session.query(TransactionTemplate).filter_by(
                name="Until Then",
            ).one().recurrence_rule
            assert rule.end_date == stop
            assert rule.max_occurrences is None

    def test_the_unbounded_shape_writes_neither_column(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Never" is a positive statement, and it stores as both NULL."""
        with app.app_context():
            self._create(
                auth_client, seed_user, "Forever", **end_bound_payload(),
            )

            rule = db.session.query(TransactionTemplate).filter_by(
                name="Forever",
            ).one().recurrence_rule
            assert rule.end_date is None
            assert rule.max_occurrences is None

    def test_a_paycheck_window_lying_entirely_before_the_schedule_is_refused_not_stored(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The write door's refusal reaches the create form as a sentence (plan step R7d-g).

        An every-paycheck rule from 2025-06-01 to 2025-12-01 is an ordered
        AUTHORED pair the schema admits; on a schedule opening after both
        dates the write door would store the opening payday beside the
        2025 stop -- the inverted pair ``ck_recurrence_rules_valid_window``
        refuses, which until R7d-g-1's review was an unhandled
        ``IntegrityError`` out of this POST.  The door refuses off the stored
        dates, the create helper words it with the schema's own sentence
        naming the payday the rule would actually have started on, and the
        pending template is rolled back rather than left generating nothing.
        """
        with app.app_context():
            opening = calendar_for(seed_user["user"].id).opening_bound()
            assert opening > date(2025, 12, 1), (
                "precondition: the schedule opens after the authored window"
            )

            resp = self._create(
                auth_client, seed_user, "Before The Schedule",
                starts_on="2025-06-01",
                **end_bound_payload(EndsOnDate(on=date(2025, 12, 1))),
            )

            assert resp.status_code == 200
            assert end_bound_before_start_message(
                date(2025, 12, 1), opening,
            ) in resp.data.decode()
            assert db.session.query(TransactionTemplate).filter_by(
                name="Before The Schedule",
            ).one_or_none() is None, "the refused create left a template behind"

    def test_choosing_a_date_and_leaving_it_blank_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Refused rather than read as "never", which would leave a bill running.

        A user who picked "on a date" meant to STOP the recurrence; taking the
        blank box as indefinite would silently do the opposite of what they
        asked for.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Blank Date",
                recurrence_end_mode="on_date",
                end_date="",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Blank Date",
            ).one_or_none() is None

    def test_choosing_a_count_and_leaving_it_blank_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The count shape's own half of the refusal above."""
        with app.app_context():
            self._create(
                auth_client, seed_user, "Blank Count",
                recurrence_end_mode="after_occurrences",
                max_occurrences="",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Blank Count",
            ).one_or_none() is None

    def test_a_zero_count_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``ck_recurrence_rules_positive_max_occurrences``, at the door.

        A count of zero names no occurrence at all.  The number box carries
        ``min="1"``, and a browser that honours it is not a validator.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Zero Count",
                recurrence_end_mode="after_occurrences",
                max_occurrences="0",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Zero Count",
            ).one_or_none() is None

    def test_an_unknown_mode_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A hand-assembled POST naming no shape has nothing to save.

        The dispatch over the closed set IS the validation; there is no
        second statement of which shapes exist for it to disagree with.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Bogus Mode",
                recurrence_end_mode="whenever",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Bogus Mode",
            ).one_or_none() is None

    def test_an_edit_can_move_a_rule_from_one_shape_to_the_other(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both columns cannot be set, because the bound is ONE value.

        Editing count -> date does not merge the two: it REPLACES the shape,
        so the column the old shape wrote is cleared in the same write.  A
        rule carrying both is what ``ck_recurrence_rules_single_end_bound``
        refuses, and this is the edit that would have produced it.
        """
        with app.app_context():
            stop = seed_periods_today[_TODAYS_PERIOD_INDEX + 3].start_date
            self._create(
                auth_client, seed_user, "Switcher",
                **end_bound_payload(EndsAfterOccurrences(count=2)),
            )
            template = db.session.query(TransactionTemplate).filter_by(
                name="Switcher",
            ).one()
            assert template.recurrence_rule.max_occurrences == 2

            auth_client.post(f"/templates/{template.id}", data={
                "name": "Switcher",
                "amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": template.transaction_type_id,
                "account_id": seed_user["account"].id,
                "version_id": template.version_id,
                **cadence_payload(),
                **end_bound_payload(EndsOnDate(on=stop)),
            }, follow_redirects=True)

            db.session.expire_all()
            rule = db.session.get(
                TransactionTemplate, template.id,
            ).recurrence_rule
            assert rule.end_date == stop
            assert rule.max_occurrences is None

    def test_an_edit_that_states_no_bound_leaves_the_stored_one_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An absent mode is "not mine to state", never "ends never".

        What a loan payment's disabled control posts, and what an amount-only
        PATCH posts.  Reading it as "never" would clear a stop the user set
        without ever showing them the control.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Keeper",
                **end_bound_payload(EndsAfterOccurrences(count=4)),
            )
            template = db.session.query(TransactionTemplate).filter_by(
                name="Keeper",
            ).one()

            auth_client.post(f"/templates/{template.id}", data={
                "name": "Keeper renamed",
                "amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": template.transaction_type_id,
                "account_id": seed_user["account"].id,
                "version_id": template.version_id,
                **cadence_payload(states_a_start=False),
            }, follow_redirects=True)

            db.session.expire_all()
            rule = db.session.get(
                TransactionTemplate, template.id,
            ).recurrence_rule
            assert rule.max_occurrences == 4
            assert rule.end_date is None

    def test_an_edit_can_state_a_deliberate_never_and_clear_a_stored_bound(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The third case of present-vs-absent, and the one that CLEARS.

        "Never" is a positive statement and must remove a stored stop; an
        ABSENT mode must not.  Both are asserted, because the whole value of
        keeping them distinguishable is that they do opposite things, and a
        test of only one would pass on a design that conflated them.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Unbounded Again",
                **end_bound_payload(EndsAfterOccurrences(count=5)),
            )
            template = db.session.query(TransactionTemplate).filter_by(
                name="Unbounded Again",
            ).one()
            assert template.recurrence_rule.max_occurrences == 5

            auth_client.post(f"/templates/{template.id}", data={
                "name": "Unbounded Again",
                "amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": template.transaction_type_id,
                "account_id": seed_user["account"].id,
                "version_id": template.version_id,
                **cadence_payload(),
                **end_bound_payload(),
            }, follow_redirects=True)

            db.session.expire_all()
            rule = db.session.get(
                TransactionTemplate, template.id,
            ).recurrence_rule
            assert rule.max_occurrences is None
            assert rule.end_date is None

    def test_the_transfer_create_form_renders_the_control_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both kinds share the partial, so both must show the bound.

        The transfer form is the one that also has a LOCKED case, so a
        regression there would be invisible to the transaction form's tests.
        """
        with app.app_context():
            resp = auth_client.get("/transfers/new")

            assert b'name="recurrence_end_mode"' in resp.data
            assert b'name="max_occurrences"' in resp.data
            for kind in END_BOUND_KINDS:
                assert f'value="{kind.token}"'.encode() in resp.data

    def test_the_form_renders_every_shape_the_dispatch_accepts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The offer set reaches the page, so every shape is choosable.

        Derived from the same closed tuple ``end_bound_from_token`` dispatches
        over, so a shape that renders is a shape that saves -- the property
        plan step R7b-2 gave the cadence controls, applied to the bound.
        """
        with app.app_context():
            resp = auth_client.get("/templates/new")

            assert b'name="recurrence_end_mode"' in resp.data
            assert b'name="max_occurrences"' in resp.data
            for kind in END_BOUND_KINDS:
                assert f'value="{kind.token}"'.encode() in resp.data


def _template_with_starts_on(seed_user, txn_type, starts_on):
    """Create a recurring EXPENSE template whose rule states a first occurrence.

    A transaction template rather than a transfer, deliberately: nothing
    derives a transaction template's validity window, so these cases exercise
    the ordinary authored path rather than the locked one.

    Args:
        seed_user: The seeded owner fixture.
        txn_type: The Expense transaction type row.
        starts_on: The rule's first occurrence.  For this paycheck-space
            cadence the write door normalises it onto the payday of the
            paycheck it falls in, so a mid-period date here is stored as that
            paycheck's own opening day.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=txn_type.id,
        name="Bounded Expense",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("42.00"))
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=starts_on,
    )
    return template


class TestACreateDoesNotBackfillClosedPayPeriods:
    """The create form's opening bound DEFAULTS, and the default is money.

    **The regression an adversarial review of plan step R7b-4 found.**  The
    control this step replaced was a ``<select>`` of pay periods with no empty
    option, preselecting the CURRENT period -- so every definition ever created
    carried an opening bound of "the paycheck I am in".  Replacing it with a
    date box that defaults to EMPTY silently changed that to "unbounded", and
    the create routes generate over ``GenerationSchedule.for_pass`` -- every
    period the owner has, with no lower window bound -- so a rent template
    created today wrote projected debits into every pay period that had already
    closed.

    Asserted on the GENERATED ROWS rather than on the column, which is the
    whole point: the two create tests written with this step both checked
    ``start_date`` and would have passed against the regression.  The fixture
    puts today in period index 4, so four CLOSED periods exist to backfill
    into -- a suite whose fixture had no history could not see this at all.
    """

    def test_a_create_with_no_stated_start_generates_nothing_in_the_past(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Rows land from the current paycheck forward, never behind it."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None, "fixture must cover today"
            closed = [
                p for p in seed_periods_today
                if p.start_date < current.start_date
            ]
            assert closed, "fixture must have a closed period to backfill into"

            # POST what the FORM ITSELF renders, read off the page, rather
            # than a date this test chose.  That is what makes this a
            # regression guard on the DEFAULT: a hardcoded bound here would
            # pass against a form that had stopped rendering one, which is
            # precisely the defect -- measured, it did.
            form = auth_client.get("/templates/new").data.decode()
            control = form[form.index('id="field-starts-on"'):]
            control = control[:control.index("</div>", control.index("<input"))]
            rendered = re.search(r'value="([^"]*)"', control).group(1)

            resp = auth_client.post("/templates", data={
                "name": "Rent Created Today",
                "amount": "2000.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                "start_date": rendered,
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Rent Created Today")
                .one()
            )
            periods = {
                txn.pay_period_id
                for txn in db.session.query(Transaction).filter_by(
                    template_id=template.id,
                )
            }
            assert periods, "the template generated nothing at all"
            backfilled = {p.id for p in closed} & periods
            assert backfilled == set(), (
                f"created rows in {len(backfilled)} pay period(s) that closed "
                f"before today"
            )

    def test_the_create_form_renders_that_default_rather_than_an_empty_box(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The default is what the user SEES, so it is asserted on the page.

        The test above passes the date explicitly, which is what the rendered
        form posts -- so it is only a regression guard while the form really
        does render it.  This is the other half.

        **TODAY, not the current paycheck's payday**, since plan step R7c-b
        renamed the control's meaning: it asked for an opening BOUND and now
        asks when the thing first HAPPENS, and today is the honest reading of
        that for a MONTH or YEAR cadence.  Neither can backdate -- today is
        inside the current pay period, so the earliest row either produces is
        this one -- which is what the class as a whole is about.  See
        ``_recurrence_form_render.create_form_default_starts_on``.
        """
        with app.app_context():
            resp = auth_client.get("/templates/new")

            assert resp.status_code == 200
            body = resp.data.decode()
            control = body[body.index('id="field-starts-on"'):]
            control = control[:control.index("</div>", control.index("<input"))]
            assert f'value="{display_today().isoformat()}"' in control


class TestAnEditStatesTheOpeningBoundOrSaysNothing:
    """PRESENT replaces, ABSENT leaves alone -- and empty is PRESENT.

    ``start_date`` is ``allow_none`` at the schema, so clearing the box arrives
    as a stated ``None`` that MUST overwrite a stored date, while a form that
    rendered the control disabled omits the key entirely and must leave the
    stored date alone.  ``_recurrence_form_helpers`` calls that asymmetry
    load-bearing, and it is: collapsing the two would make a loan edit erase
    the origination bound that keeps its payments from generating before the
    loan exists.

    The closing bound carries the identical pair of cases, which is what says
    the two bounds are one idea rather than two conventions.
    """

    def test_an_edit_that_states_no_start_leaves_the_stored_one_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An amount-only save does not touch a bound it never showed.

        Compared against what was STORED rather than against the date handed
        to the fixture: this is a paycheck-space cadence, so the write door
        normalises the requested date onto the payday of the paycheck that
        hosts it (and onto the schedule's opening payday when the date
        precedes it).  Asserting the requested date would grade the fixture's
        arithmetic instead of the edit.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            template = _template_with_starts_on(
                seed_user, txn_type, date(2026, 5, 7),
            )
            before = template.recurrence_rule.starts_on

            auth_client.post(
                f"/templates/{template.id}",
                data={
                    "name": template.name,
                    "amount": "42.00",
                    "category_id": template.category_id,
                    "transaction_type_id": txn_type.id,
                    "account_id": template.account_id,
                    "version_id": template.version_id,
                    **cadence_payload(states_a_start=False),
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            stored = db.session.get(TransactionTemplate, template.id)
            assert stored.recurrence_rule.starts_on == before

    def test_a_cleared_box_keeps_the_stored_date_rather_than_nulling_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """There is no "no first occurrence" state left to clear TO.

        This asserted the opposite until plan step R7c-b, and the reversal is
        the step: an opening BOUND was optional, so clearing the box removed
        it, and a rule with none started with the schedule.  A first
        OCCURRENCE is what the rule repeats from and
        ``budget.recurrence_rules.starts_on`` is ``NOT NULL``, so the empty
        request has no answer to give.

        ``starts_on`` is not ``allow_none``, so ``_normalize_empty_inputs``
        DROPS an empty box rather than passing a present ``None`` -- which
        makes a cleared control and a locked one the same payload, and one
        meaning is all that payload needs: leave the stored date alone.  The
        create path has no stored date to leave, and refuses (see
        ``TestACreateDoesNotBackfillClosedPayPeriods``).
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            template = _template_with_starts_on(
                seed_user, txn_type, date(2026, 5, 7),
            )
            before = template.recurrence_rule.starts_on

            resp = auth_client.post(
                f"/templates/{template.id}",
                data={
                    "name": template.name,
                    "amount": "42.00",
                    "category_id": template.category_id,
                    "transaction_type_id": txn_type.id,
                    "account_id": template.account_id,
                    "version_id": template.version_id,
                    **cadence_payload(),
                    "starts_on": "",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.expire_all()
            stored = db.session.get(TransactionTemplate, template.id)
            assert stored.recurrence_rule.starts_on == before
            assert newest_price(stored) == Decimal("42.00"), (
                "the rest of the edit must still have landed"
            )


class TestTheServerRendersTheEndsControlAlreadyCorrect:
    """The form is right BEFORE any script runs, and disabled means disabled.

    ``recurrence_form.js`` re-links the value inputs on every change, but the
    first render is the server's -- so a page whose script has not executed
    yet, or has failed, must still post exactly the shape it displays.  The
    same contract the two interval controls hold, which
    ``_recurrence_fields.html`` states for both.

    What this CANNOT see is what the script does afterwards: a control hidden
    by a class and one disabled by the script look identical in rendered HTML.
    That is ``tests/manual/verify_recurrence_form.py``'s, and plan step R7b-2
    shipped two defects of exactly that kind past a green suite.
    """

    def _selected_mode(self, body):
        """Return the token of the "Ends" option carrying ``selected``.

        Parsed structurally rather than matched as a substring, and an
        adversarial review of this step is why: the first version asserted a
        whitespace-normalised ``value="never" data-needs="" selected`` with an
        ``or 'value="never"' in control`` fallback -- and the fallback is true
        of every render, because the option is always emitted.  It could not
        fail for the selection it was named after, which is exactly the R7b-2
        defect the surrounding docstrings cite.

        Args:
            body: The decoded response body.

        Returns:
            The selected option's ``value``, or ``None`` when none carries
            ``selected`` -- which is itself a failure a caller should assert
            on, because a ``<select>`` with no selected option silently
            submits its FIRST.
        """
        start = body.index('id="recurrence_end_mode"')
        control = body[start:body.index("</select>", start)]
        for option in re.findall(r"<option\b[^>]*>", control):
            if re.search(r"\bselected\b", option):
                return re.search(r'value="([^"]*)"', option).group(1)
        return None

    def _input(self, body, element_id):
        """Return one input's tag text from a rendered page.

        Args:
            body: The decoded response body.
            element_id: The input's ``id``.

        Returns:
            The tag's source text.
        """
        start = body.index(f'id="{element_id}"')
        return body[body.rindex("<input", 0, start):body.index(">", start) + 1]

    def test_a_create_form_starts_unbounded_with_both_values_disabled(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Never" is selected, so neither value input may submit.

        An enabled box beside a mode that does not read it would post a value
        the chosen shape ignores -- and the shape a hand-assembled POST then
        names decides which of them the door believes.
        """
        with app.app_context():
            body = auth_client.get("/templates/new").data.decode()

            assert self._selected_mode(body) == NeverEnds.token
            assert "disabled" in self._input(body, "end_date")
            assert "disabled" in self._input(body, "max_occurrences")

    def test_an_edit_form_enables_only_the_stored_shapes_input(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A count-bounded rule prefills the count box and disables the date.

        The prefill comes from ``edit_form_end``, which reads the two
        columns through the one seam rather than letting the template decide
        which shape a row holds.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            auth_client.post("/templates", data={
                "name": "Counted",
                "amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                **end_bound_payload(EndsAfterOccurrences(count=7)),
            }, follow_redirects=True)
            template = db.session.query(TransactionTemplate).filter_by(
                name="Counted",
            ).one()

            body = auth_client.get(
                f"/templates/{template.id}/edit",
            ).data.decode()

            count_input = self._input(body, "max_occurrences")
            assert 'value="7"' in count_input
            assert "disabled" not in count_input
            assert "disabled" in self._input(body, "end_date")
            assert self._selected_mode(body) == EndsAfterOccurrences.token


class TestTheBoundsRefusalsReachTheUser:
    """A refusal nobody reads is copy, not a refusal.

    Plan step R7b-3 authored three sentences for the "Ends" control and
    allowlisted none of them, so a user whose bound was refused was redirected
    to a blank form and told to "correct the highlighted errors" -- on a page
    that highlights nothing.  It is the R7b-2 defect this project already paid
    for, and the gate written against it could not see these because they live
    in a ``@post_load`` hook that ``Schema.validate`` skips.
    """

    def _post(self, auth_client, seed_user, **extra):
        """POST a recurring expense template and return the followed response.

        Args:
            auth_client: The signed-in client.
            seed_user: The seeded owner fixture.
            **extra: Additional form keys.

        Returns:
            The decoded response body.
        """
        txn_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        return auth_client.post("/templates", data={
            "name": "Refused",
            "amount": "100.00",
            "category_id": seed_user["categories"]["Rent"].id,
            "transaction_type_id": txn_type.id,
            "account_id": seed_user["account"].id,
            **cadence_payload(),
            **extra,
        }, follow_redirects=True).data.decode()

    def test_a_blank_date_says_which_control_to_fill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Not the generic prompt: the sentence names the box and the way out."""
        with app.app_context():
            body = self._post(
                auth_client, seed_user,
                recurrence_end_mode="on_date", end_date="",
            )

            assert "Choose the date this stops repeating" in body
            assert GENERIC_VALIDATION_FLASH not in body

    def test_a_blank_count_says_which_control_to_fill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The count shape's half of the same promise."""
        with app.app_context():
            body = self._post(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences", max_occurrences="",
            )

            assert "Enter how many times this repeats" in body
            assert GENERIC_VALIDATION_FLASH not in body

    def test_a_zero_count_gets_the_SHAPES_own_message(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The type's refusal, not marshmallow's "Must be greater than...".

        The schema carries no lower bound on the count precisely so this
        reaches ``EndsAfterOccurrences.__post_init__``, whose message names
        the control and states the offending value.
        """
        with app.app_context():
            body = self._post(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences", max_occurrences="0",
            )

            assert "at least 1, not 0" in body

    def test_a_count_too_large_for_the_column_is_a_400_not_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An unstorable count is a designed refusal, not a database error.

        ``max_occurrences`` is a Postgres ``integer``; a larger value dies at
        the flush with ``NumericValueOutOfRange``, which is the ``MarkDoneSchema``
        defect one schema over.  The schema's upper bound is what keeps it a
        refusal the form can report.
        """
        with app.app_context():
            self._post(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences",
                max_occurrences="2147483648",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Refused",
            ).one_or_none() is None
