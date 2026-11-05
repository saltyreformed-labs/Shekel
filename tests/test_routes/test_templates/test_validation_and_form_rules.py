"""Template route tests: the edge cases and refusals of the doors, an envelope refused on income,
the recurrence cell's source-level lock, and the per-month ceiling on the cadence.
"""

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.services.recurrence import recurrence_spec
from tests._test_helpers import cadence_payload, end_bound_payload, state_template_price
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from tests._repo_root import REPO_ROOT

from ._helpers import _create_template


# ── Negative Path Tests ─────────────────────────────────────────────


class TestTemplateNegativePaths:
    """Tests for template edge cases, validation gaps, and idempotent operations."""

    def test_archive_already_archived_template(self, app, auth_client, seed_user, seed_periods_today):
        """Archiving an already-archived template is idempotent."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            # No projected transactions exist, so 0 are soft-deleted.
            assert b"0 projected transaction(s) removed" in resp.data

            db.session.refresh(template)
            assert template.is_active is False
            # NOTE: archive_template is idempotent -- no guard against
            # archiving an already-inactive template.

    def test_unarchive_already_active_template(self, app, auth_client, seed_user, seed_periods_today):
        """Unarchiving an already-active template restores nothing and says why.

        Since plan step ``pay_calendar:C18-a`` (ruling R-PC95) the door
        refuses an active template: its soft-deleted rows are its owner's
        own deletions, which an unarchive used to bring back.  The expected
        message changed with the developer's rule-5 confirmation
        (2026-09-23); the rest of the test is as written.
        """
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)
            assert template.is_active is True

            resp = auth_client.post(
                f"/templates/{template.id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert (
                b"Recurring transaction &#39;Rent&#39; is not archived, so "
                b"nothing was restored."
            ) in resp.data

            db.session.refresh(template)
            assert template.is_active is True

    def test_create_template_missing_name(self, app, auth_client, seed_user):
        """Creating a template without name fails schema validation."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_template_missing_category(self, app, auth_client, seed_user):
        """Creating a template without category_id fails schema validation."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/templates", data={
                "name": "No Category Template",
                "amount": "100.00",
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_template_negative_amount(self, app, auth_client, seed_user):
        """Negative amount rejected by schema Range(min=0) validator."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Negative Test",
                "amount": "-100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="Negative Test",
            ).count()
            assert count == 0

    def test_edit_nonexistent_template(self, app, auth_client, seed_user):
        """GET /templates/999999/edit for missing template returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/999999/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_update_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999 for missing template returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999",
                data={"name": "Ghost"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_create_template_xss_in_name(self, app, auth_client, seed_user):
        """XSS payload in template name is escaped by Jinja2 auto-escaping."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "<script>alert(1)</script>",
                "amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200

            # Verify template was created with the XSS payload.
            template = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="<script>alert(1)</script>",
            ).first()
            assert template is not None

            # Jinja2 auto-escaping prevents raw script tags in output.
            assert b"<script>alert(1)</script>" not in resp.data
            assert b"&lt;script&gt;" in resp.data

    def test_create_template_with_other_users_category_idor(
        self, app, auth_client, seed_user, second_user,
    ):
        """Template creation with another user's category is rejected and DB unchanged."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            other_cat = second_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "IDOR Category Test",
                "amount": "100.00",
                "category_id": other_cat.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid category." in resp.data

            # Verify no template was created.
            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="IDOR Category Test",
            ).count()
            assert count == 0

    def test_create_template_with_other_users_account_idor(
        self, app, auth_client, seed_user, second_user,
    ):
        """Template creation with another user's account is rejected and DB unchanged."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "IDOR Account Test",
                "amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": second_user["account"].id,
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account." in resp.data

            # Verify no template was created.
            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="IDOR Account Test",
            ).count()
            assert count == 0


# ── Envelope/Income Cross-Field Validation Tests (Phase 2) ──────────


class TestEnvelopeIncomeRejection:
    """Phase 2 of the carry-forward aftermath plan: reject is_envelope=True
    on income templates at the Marshmallow input boundary.

    Mirrors the spec in
    ``docs/carry-forward-aftermath-implementation-plan.md`` Phase 2.
    Schema-layer coverage and exhaustive partial-update edge cases
    live in ``tests/test_routes/test_template_flags.py``
    (``TestEnvelopeOnlyOnExpenseSchema`` and
    ``TestTrackingExpenseOnlyValidation``).  These three tests serve
    as the canonical end-to-end checkpoints for Phase 2.
    """

    def test_post_income_template_with_envelope_rejected(
        self, app, auth_client, seed_user,
    ):
        """POST /templates with income type + is_envelope=on rejects.

        After Phase 2, the cross-field schema validator catches this
        combination at the input boundary; the route surfaces the
        validator's specific message instead of the generic prompt.
        Verifies the rejection by asserting the actionable flash and
        confirming no template row was persisted.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Phase2 Bad Income Envelope",
                "amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": income_type.id,
                "account_id": seed_user["account"].id,
                "is_envelope": "on",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert (
                b"Purchase tracking is only available for expense templates"
                in resp.data
            )

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Phase2 Bad Income Envelope",
            ).count()
            assert count == 0

    def test_post_expense_template_with_envelope_succeeds(
        self, app, auth_client, seed_user,
    ):
        """POST /templates with expense type + is_envelope=on succeeds.

        Positive control for the cross-field rule: the same checkbox
        on the supported transaction type creates the template and
        sets ``is_envelope = True``.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Phase2 Good Expense Envelope",
                "amount": "150.00",
                "category_id": category.id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
                "is_envelope": "on",
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data
            assert (
                b"Purchase tracking is only available"
                not in resp.data
            )

            template = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Phase2 Good Expense Envelope",
            ).one()
            assert template.is_envelope is True
            assert template.transaction_type_id == expense_type.id

    def test_patch_envelope_to_true_on_income_template_rejected(
        self, app, auth_client, seed_user,
    ):
        """POST /templates/<id> flipping is_envelope=on for an income template rejects.

        Reproduces the plan's PATCH scenario: an existing income
        template (envelope=False) gets a form submission that ticks
        the envelope checkbox without changing the type.  The schema
        validator catches the resulting state and the template is
        unchanged.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            category = seed_user["categories"]["Rent"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Phase2 Income Template",
                is_envelope=False,
            )
            db.session.add(template)
            db.session.commit()
            state_template_price(template, Decimal("250.00"))
            db.session.commit()

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Phase2 Income Template",
                "amount": "250.00",
                "category_id": category.id,
                "transaction_type_id": income_type.id,
                "account_id": seed_user["account"].id,
                "is_envelope": "on",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert (
                b"Purchase tracking is only available for expense templates"
                in resp.data
            )

            db.session.refresh(template)
            assert template.is_envelope is False
            assert template.transaction_type_id == income_type.id


class TestRecurrenceCellLock:
    """C7 source-level locks for the ``recurrence_cell`` macro.

    The macro's whole history is a template computing what a service should
    have.  It first compared ``rr.pattern.name`` strings through an
    intermediate ``pname`` variable; the mobile-followup commit 7 (F-8) rewired
    those onto the ``REC_*`` integer id globals; the polyglot cleanup
    (TPLB/TPL-07) merged two byte-identical copies into one shared macro.  Each
    fixed the spelling and left the shape.

    **Plan step R7a removed the shape.**  The phrase is produced by
    ``app.services.recurrence.describe`` from what a recurrence MEANS -- one
    function over ``(interval_n, unit)`` -- and the macro receives it already
    worded.  So the lock is no longer "compare ids, not names"; it is that the
    macro reads NOTHING but its argument.  That is what keeps it alive through
    plan step R7c, which drops every column the old branches read.
    """

    _MACRO_PATH = ("app", "templates", "_recurrence_macros.html")

    # Every site that renders the cell: the two active sections of the
    # swappable body, and the Archived drawer on the full page.  The former
    # transfers/list.html was retired when /transfers folded into /templates.
    _RENDER_SITE_PATHS = (
        ("templates/list.html", ("app", "templates", "templates", "list.html")),
        (
            "templates/_recurring_body.html",
            ("app", "templates", "templates", "_recurring_body.html"),
        ),
    )

    # Identifiers whose presence in the macro BODY would mean the template had
    # started reading the recurrence rule again, paired with what each one
    # costs.  ``pattern`` covers ``pattern_id``, ``pattern.name`` and the
    # ``REC_*`` comparisons in one.
    _FORBIDDEN_IN_BODY = (
        ("pattern", "the closed pattern set, which plan step R7c drops"),
        ("day_of_month", "a rule column, which plan step R7c drops"),
        ("month_of_year", "a rule column, which plan step R7c drops"),
        ("interval_n", "a rule column the describer already reads"),
        ("recurrence_rule", "the rule itself; the macro takes a description"),
        (".name", "a ref-table name string used for display"),
    )

    def _read_template_source(self, parts):
        """Return the contents of a template file under ``app/templates``."""
        return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def _macro_body(self):
        """Return the ``recurrence_cell`` macro's body, without its comment.

        Scoped to the body deliberately: the file's leading ``{# ... #}`` block
        NAMES the identifiers the body must not contain, because explaining
        what was removed is the point of it.  A whole-file scan would therefore
        fail on its own documentation, and the usual fix -- deleting the
        explanation -- is the wrong one.
        """
        src = self._read_template_source(self._MACRO_PATH)
        start = src.index("{% macro recurrence_cell(")
        end = src.index("{% endmacro %}", start)
        return src[start:end]

    def test_the_extractor_finds_the_real_macro_body(self):
        """The body scan is not vacuous.

        Without this, a mis-sliced extractor would return an empty string and
        every forbidden-identifier assertion below would pass while checking
        nothing -- the exact failure mode this arc's reviews keep finding in
        controls that were never shown to fire.
        """
        body = self._macro_body()

        assert "description.cadence" in body, (
            "the extracted body does not contain the phrase the macro renders; "
            "the slice bounds are wrong, so the locks below check nothing"
        )
        assert "One-time" in body, (
            "the extracted body does not contain the rule-less branch"
        )

    def test_the_macro_reads_nothing_but_its_argument(self):
        """The cell displays a produced value; it does not compute one.

        Locks plan step R7a.  Any of these identifiers reappearing means the
        template has gone back to deriving the phrase from the rule's columns
        -- which is both the "templates display, never compute" violation and
        a surface that breaks the moment plan step R7c drops those columns.
        """
        body = self._macro_body()

        for identifier, why in self._FORBIDDEN_IN_BODY:
            assert identifier not in body, (
                f"the recurrence_cell macro body reads {identifier!r} "
                f"({why}); it must render only the RecurrenceDescription it "
                "is passed"
            )

    def test_every_render_site_imports_the_shared_macro(self):
        """All four cells come from one macro, imported without context.

        The macro lives once in ``_recurrence_macros.html``; locking the import
        keeps a render site from re-inlining a private copy that could diverge.
        ``with context`` is deliberately ABSENT: it was required while the
        else-branch read the ``recurrence_pattern_labels`` context processor,
        and plan step R7a deleted both, so the macro now reads nothing from the
        render context at all.
        """
        for label, parts in self._RENDER_SITE_PATHS:
            src = self._read_template_source(parts)

            assert (
                'from "_recurrence_macros.html" import recurrence_cell' in src
            ), (
                f"{label} must import recurrence_cell from "
                "_recurrence_macros.html, not define its own copy."
            )
            assert "import recurrence_cell with context" not in src, (
                f"{label} imports recurrence_cell with context; the macro "
                "reads nothing from the context since plan step R7a, and the "
                "clause would hide a re-introduced context dependency."
            )


class TestThePerMonthCeilingOnTheTemplateForm:
    """The cadence's third value, posted as the browser posts it (plan step salary:R15-a).

    The control renders and ENABLES only beside a unit whose occurrences can
    repeat within a month, so a real submission carries ``max_per_month``
    beside a paycheck cadence and omits it beside a monthly one.  Every case
    below posts one of those two shapes, plus the empty-box shape an enabled
    control produces when the user clears it.
    """

    def _create(self, auth_client, seed_user, name, **cadence):
        """POST a recurring expense template named *name* and return its rule."""
        txn_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        resp = auth_client.post("/templates", data={
            "name": name,
            "amount": "24.99",
            "category_id": seed_user["categories"]["Rent"].id,
            "transaction_type_id": txn_type.id,
            "account_id": seed_user["account"].id,
            **cadence_payload(**cadence),
            **end_bound_payload(),
        }, follow_redirects=True)
        assert resp.status_code == 200
        return db.session.query(TransactionTemplate).filter_by(
            name=name,
        ).one().recurrence_rule

    def test_a_create_stores_the_ceiling_and_the_cell_words_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every paycheck at most 2 a month is stored as 2 and read as such."""
        with app.app_context():
            rule = self._create(
                auth_client, seed_user, "Benefit Premium", max_per_month=2,
            )

            assert rule.max_per_month == 2
            assert recurrence_spec(rule).max_per_month == 2

            listing = auth_client.get("/templates").data.decode()
            assert "at most 2 a month" in listing

    def test_a_create_with_the_box_empty_stores_no_ceiling(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An enabled, untouched box posts ``""`` and authors no ceiling."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            resp = auth_client.post("/templates", data={
                "name": "No Ceiling",
                "amount": "24.99",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                "max_per_month": "",
                **end_bound_payload(),
            }, follow_redirects=True)
            assert resp.status_code == 200

            rule = db.session.query(TransactionTemplate).filter_by(
                name="No Ceiling",
            ).one().recurrence_rule
            assert rule.max_per_month is None

    def test_a_ceiling_beside_a_monthly_cadence_is_refused_at_the_door(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A crafted POST pairing the ceiling with a month unit is REFUSED, and heard.

        Two assertions, because one is not enough: with the schema's
        cross-field rule deleted the pair reaches ``RecurrenceSpec``, which
        refuses it as a broken invariant -- an unhandled 500 that also
        creates nothing.  The designed answer is a 200 after the redirect
        carrying the refusal's own sentence.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            resp = auth_client.post("/templates", data={
                "name": "Contradiction",
                "amount": "24.99",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH, max_per_month=2,
                ),
                **end_bound_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert (
                "a per-month limit has nothing to limit" in resp.data.decode()
            )
            assert db.session.query(TransactionTemplate).filter_by(
                name="Contradiction",
            ).one_or_none() is None

    def test_an_update_that_states_the_ceiling_replaces_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PRESENT replaces: 2 becomes 1."""
        with app.app_context():
            rule = self._create(
                auth_client, seed_user, "Replaced", max_per_month=2,
            )
            template_id = rule.transaction_template_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                **cadence_payload(max_per_month=1),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(
                TransactionTemplate, template_id,
            ).recurrence_rule.max_per_month == 1

    def test_an_update_that_omits_the_key_leaves_the_ceiling_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """ABSENT leaves alone: a disabled control states nothing.

        The shape a partial edit produces, and the shape the update door
        must not read as a clear -- the same rule the due day and the
        opening bound already run on.
        """
        with app.app_context():
            rule = self._create(
                auth_client, seed_user, "Kept", max_per_month=2,
            )
            template_id = rule.transaction_template_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                **cadence_payload(),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(
                TransactionTemplate, template_id,
            ).recurrence_rule.max_per_month == 2

    def test_an_update_with_the_box_cleared_removes_the_ceiling(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A present ``""`` is a stated clear."""
        with app.app_context():
            rule = self._create(
                auth_client, seed_user, "Cleared", max_per_month=2,
            )
            template_id = rule.transaction_template_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                **cadence_payload(),
                "max_per_month": "",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(
                TransactionTemplate, template_id,
            ).recurrence_rule.max_per_month is None

    def test_re_cadencing_to_monthly_drops_a_stored_ceiling(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The edit form's repair path: a month unit posts no ceiling and keeps none.

        The control is disabled beside a monthly unit, so the key is absent;
        the stored 2 cannot ride through onto a cadence that cannot hold it,
        or the re-author would build the pair the spec refuses -- a 500 out
        of an ordinary edit.
        """
        with app.app_context():
            rule = self._create(
                auth_client, seed_user, "Re-cadenced", max_per_month=2,
            )
            template_id = rule.transaction_template_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2026, 4, 15),
                ),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            stored = db.session.get(
                TransactionTemplate, template_id,
            ).recurrence_rule
            assert stored.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )
            assert stored.max_per_month is None

    def test_the_edit_form_prefills_the_stored_ceiling_enabled(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The server renders the box with the stored value and NOT disabled."""
        with app.app_context():
            rule = self._create(
                auth_client, seed_user, "Prefilled", max_per_month=2,
            )

            body = auth_client.get(
                f"/templates/{rule.transaction_template_id}/edit",
            ).data.decode()

            control = re.search(
                r'<input[^>]*id="max_per_month"[^>]*>', body,
            ).group(0)
            assert 'value="2"' in control
            assert "disabled" not in control

    def test_the_edit_form_renders_the_box_disabled_beside_a_monthly_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A calendar-month rule's box starts disabled, so it posts nothing."""
        with app.app_context():
            template = _create_template(seed_user, cadence=MONTHLY)
            db.session.commit()

            body = auth_client.get(
                f"/templates/{template.id}/edit",
            ).data.decode()

            control = re.search(
                r'<input[^>]*id="max_per_month"[^>]*>', body,
            ).group(0)
            assert "disabled" in control

    def test_the_preview_skips_the_third_paycheck_under_a_ceiling(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The preview lists what the save would generate: January's third payday goes.

        ``seed_periods`` opens on 2026-01-02 at 14 days, so January holds
        three paydays (the 2nd, 16th and 30th).  Unceilinged, the first five
        occurrences are Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27; at most two
        a month, Jan 30 drops out and Mar 13 comes in.
        """
        with app.app_context():
            query = {
                **cadence_payload(starts_on=date(2026, 1, 2)),
                **end_bound_payload(),
            }

            plain = auth_client.get(
                "/templates/preview-recurrence", query_string=query,
            ).data.decode()
            ceilinged = auth_client.get(
                "/templates/preview-recurrence",
                query_string={**query, "max_per_month": "2"},
            ).data.decode()

            assert "Jan 30, 2026" in plain
            assert "Mar 13, 2026" not in plain
            assert "Jan 30, 2026" not in ceilinged
            assert "Jan 16, 2026" in ceilinged
            assert "Mar 13, 2026" in ceilinged
