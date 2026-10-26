"""Grid route tests: a row matched by its own period, and a one-off's rows grouped by name."""

from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction

from tests._test_helpers import (
    current_pay_period,
    derived_span,
    generate_row_of,
    make_expense_template,
    make_income_template,
    one_off_row_of,
)

from ._helpers import _grid_route_source


class TestGridMatchedByRowPeriod:
    """Commit 2 (mobile-first v3): ``matched_by_row_period`` route context.

    The matching predicate previously hand-coded in four blocks of
    Jinja (``grid.html`` income + expense, ``_mobile_grid.html`` income +
    expense) is precomputed once in the route as a dict keyed by
    ``(category_id, template_id, txn_name, period_id)``.  Commit 1
    introduced the macros that read it; Commit 2 (this commit) adds
    the dict to the route context; Commits 3 and 4 wire the templates
    to consume it.

    These tests lock in the route contract: the dict is in the
    rendered context, its keys are 4-tuples, its values are non-empty
    lists of ``Transaction`` ORM objects, and its contents mirror the
    Jinja predicate text-for-text (category match, income/expense per
    section, not-deleted, not-cancelled, a recurring definition's row
    matched by the definition and every other row by name -- ruling
    R-BAL34's fork on ``recurs``).
    """

    @staticmethod
    def _capture_grid_context(app, auth_client):
        """Return the (template, context) tuple captured from /grid.

        Uses Flask's ``template_rendered`` signal to record what the
        grid route handed to ``render_template`` so the test can
        inspect ``matched_by_row_period`` (and any other context key)
        without parsing rendered HTML.  Returns the first
        ``grid/grid.html`` record; raises ``AssertionError`` if the
        route rendered a different template (the ``no_setup`` or
        ``no_periods`` branch) so the test fails loud rather than
        silently inspecting the wrong context.
        """
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded: list[tuple] = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get("/grid")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"GET /grid returned {response.status_code}; expected 200"
        )
        grid_records = [
            (t, c) for t, c in recorded if t.name == "grid/grid.html"
        ]
        assert grid_records, (
            "GET /grid did not render grid/grid.html; templates "
            f"rendered: {[t.name for t, _ in recorded]!r}"
        )
        return grid_records[0]

    def test_index_renders_with_new_context(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-1: GET /grid still returns 200 with the new precomputation.

        Pure smoke test that adding the precomputation and the new
        ``matched_by_row_period`` kwarg to ``render_template`` did not
        break the existing rendering pipeline.  No assertion on the
        dict's contents -- C2-2 and C2-3 cover that.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_matched_by_row_period_in_context(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-2: ``matched_by_row_period`` is present in the render context.

        Setup: seed one Projected expense in the current period so the
        dict has at least one entry.  Asserts the dict exists, is a
        ``dict``, every key is a 4-tuple of ``(int, int | None, str,
        int)``, and every value is a non-empty list of ``Transaction``
        ORM objects.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            one_off_row_of(
                current,
                name="Weekly Groceries",
                amount=Decimal("123.45"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            _, context = self._capture_grid_context(app, auth_client)

            assert "matched_by_row_period" in context, (
                "render_template kwargs missing matched_by_row_period; "
                "Commit 2 of mobile-first v3 adds it as the canonical "
                "matching producer for the grid macros"
            )
            matched = context["matched_by_row_period"]
            assert isinstance(matched, dict), (
                f"matched_by_row_period must be a dict, got {type(matched)!r}"
            )
            assert matched, (
                "Seeded one txn in the current period; "
                "matched_by_row_period should be non-empty"
            )
            for key, value in matched.items():
                assert isinstance(key, tuple) and len(key) == 4, (
                    f"matched_by_row_period key {key!r} is not a 4-tuple"
                )
                category_id, template_id, txn_name, period_id = key
                assert isinstance(category_id, int)
                assert template_id is None or isinstance(template_id, int)
                assert isinstance(txn_name, str)
                assert isinstance(period_id, int)
                assert isinstance(value, list) and value, (
                    "matched_by_row_period values must be non-empty lists"
                )
                for matched_txn in value:
                    assert isinstance(matched_txn, Transaction), (
                        "matched_by_row_period values must contain "
                        f"Transaction ORM objects, got {type(matched_txn)!r}"
                    )

    def test_matched_dict_mirrors_jinja_predicate(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-3: dict contents mirror the Jinja matching predicate.

        Seeds four transactions exercising each predicate branch:
          (a) a template-linked income (Salary template) in the
              current period -- must match via the template-id branch.
          (b) a one-off in Groceries -- must match via the name arm
              (its definition does not recur, so its key carries no
              template_id although the row has one).
          (c) a cancelled expense in Groceries -- must NOT appear (the
              ``status_id != STATUS_CANCELLED`` guard).
          (d) a soft-deleted expense in Groceries -- must NOT appear
              (the ``not is_deleted`` guard).

        Asserts: matched_by_row_period contains the expected keys for
        (a) and (b); the matched lists include only the correct txn;
        (c) and (d) do not appear in any matched list.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            salary_cat = seed_user["categories"]["Salary"]
            groceries_cat = seed_user["categories"]["Groceries"]

            # (a) Template-linked income: the definition's own row (the
            # engine's, plan step balance:X-cf-4).
            salary_template = make_income_template(
                db.session, seed_user, amount="2500.00",
                name="Biweekly Salary", category_key="Salary",
            )
            txn_a = generate_row_of(salary_template, current)
            # (b) A one-off (a rule-less definition's row).
            txn_b = one_off_row_of(
                current,
                name="Adhoc Groceries",
                amount=Decimal("85.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type_id,
                category_id=groceries_cat.id,
            )
            # (c) Cancelled expense.
            txn_c = one_off_row_of(
                current,
                name="Cancelled Groceries",
                amount=Decimal("50.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type_id,
                category_id=groceries_cat.id,
            )
            txn_c.status_id = cancelled_id
            # (d) Soft-deleted expense.
            txn_d = one_off_row_of(
                current,
                name="Deleted Groceries",
                amount=Decimal("60.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type_id,
                category_id=groceries_cat.id,
            )
            txn_d.is_deleted = True
            db.session.commit()
            txn_a_id = txn_a.id
            txn_b_id = txn_b.id
            txn_c_id = txn_c.id
            txn_d_id = txn_d.id
            template_id = salary_template.id

            _, context = self._capture_grid_context(app, auth_client)
            matched = context["matched_by_row_period"]

            # (a) Template-linked: key uses template_id, txn_name from row
            # key is the template name; the matched list contains txn_a.
            key_a = (
                salary_cat.id, template_id, "Biweekly Salary", current.id,
            )
            assert key_a in matched, (
                f"Template-linked match missing; expected key {key_a!r} "
                f"in dict; got keys {list(matched.keys())!r}"
            )
            assert [t.id for t in matched[key_a]] == [txn_a_id], (
                f"Template-linked match must contain only txn_a "
                f"(id={txn_a_id}); got "
                f"{[t.id for t in matched[key_a]]!r}"
            )

            # (b) The one-off: key uses template_id=None (the name arm),
            # txn_name from row key is the instance name; matched list
            # contains txn_b.
            key_b = (
                groceries_cat.id, None, "Adhoc Groceries", current.id,
            )
            assert key_b in matched, (
                f"One-off match missing; expected key {key_b!r} "
                f"in dict; got keys {list(matched.keys())!r}"
            )
            assert [t.id for t in matched[key_b]] == [txn_b_id], (
                "One-off match must contain only txn_b "
                f"(id={txn_b_id}); got "
                f"{[t.id for t in matched[key_b]]!r}"
            )

            # (c) Cancelled: must not appear in any matched list.  Also
            # row-key for "Cancelled Groceries" should be absent because
            # _build_row_keys filters cancelled txns at row-key time.
            all_matched_ids = {
                t.id for v in matched.values() for t in v
            }
            assert txn_c_id not in all_matched_ids, (
                f"Cancelled txn (id={txn_c_id}) must not appear in "
                "matched_by_row_period (status_id != STATUS_CANCELLED "
                "guard)"
            )

            # (d) Soft-deleted: must not appear in any matched list.
            assert txn_d_id not in all_matched_ids, (
                f"Soft-deleted txn (id={txn_d_id}) must not appear in "
                "matched_by_row_period (not is_deleted guard)"
            )

    def test_no_balance_resolver_reads(self):
        """C2-4: NO direct reads of canonical-producer source columns.

        Plan Section 1 rule 2 ("Canonical producers only for monetary
        values"): the route must not read
        ``Account.current_anchor_balance`` /
        ``Account.current_anchor_period_id`` /
        ``LoanParams.current_principal`` / ``LoanParams.interest_rate``.

        **The baseline is ZERO since plan step X-f1c3a**, and that is the
        finding rather than a tightening.  It was ONE -- the header's
        ``anchor_balance = account.current_anchor_balance`` -- carried as a
        legitimate exception because the header's starting figure is a display
        value rather than a projection.  Ruling R-EH deleted the column: the
        header now reads the account's latest ASSERTION through
        ``cash_ledger.resolve_anchor``, which is a canonical producer, so the
        exception has nothing left to except and every count here is zero.

        Complements the existing
        ``test_grid_balance_computation_routed_through_resolver``
        (F-6 lock) by pinning the *count* of legacy reads rather
        than the presence/absence of the canonical-producer symbol.

        Scoped to the whole ``app/routes/grid/`` package since plan step
        C2-f2b; see :func:`_grid_route_source`.
        """
        import re  # pylint: disable=import-outside-toplevel

        grid_source = _grid_route_source()
        current_anchor_balance_reads = len(
            re.findall(r"\.current_anchor_balance\b", grid_source),
        )
        current_anchor_period_id_reads = len(
            re.findall(r"\.current_anchor_period_id\b", grid_source),
        )
        current_principal_reads = len(
            re.findall(r"\.current_principal\b", grid_source),
        )
        interest_rate_reads = len(
            re.findall(r"\.interest_rate\b", grid_source),
        )

        assert current_anchor_balance_reads == 0, (
            "app/routes/grid/ contains "
            f"{current_anchor_balance_reads} reads of "
            "``.current_anchor_balance`` (expected 0 -- ruling R-EH deleted "
            "the column, and the header reads the account's latest assertion "
            "through ``cash_ledger.resolve_anchor``); route all monetary "
            "values through a canonical producer"
        )
        assert current_anchor_period_id_reads == 0, (
            "app/routes/grid/ reads "
            "``.current_anchor_period_id`` directly; route through "
            "``balance_resolver`` instead"
        )
        assert current_principal_reads == 0, (
            "app/routes/grid/ reads ``.current_principal`` directly; "
            "route through ``loan_resolver`` instead"
        )
        assert interest_rate_reads == 0, (
            "app/routes/grid/ reads ``.interest_rate`` directly; "
            "route through ``loan_resolver`` instead"
        )


class TestTheGridGroupsAOneOffsRowsByName:
    """Ruling **R-BAL34** (plan step ``balance:X-bi-7b``, leaf 7b-2).

    Every one-off carries a definition since leaf 7b-1, and keyed on the
    LINK ``build_row_keys`` made two same-named one-offs -- one grid row
    with two cells before -- two rows both labelled by the name.  The grid
    keeps the grouping the owner had: a rule-less definition's rows group by
    ``(category_id, name)``, a recurring definition's by the definition, and
    the two arms exclude each other's rows (the six-cell double-display of
    ``from_scratch_architecture.md`` 10.4 trace 5 stays fixed).  Graded on
    the CONTEXT the grid route renders, so the real producers over the real
    row set are what is measured -- in paychecks 4 and 5, the current one
    and the next, which the grid's default window draws.
    """

    @staticmethod
    def _place(seed_user, period, name, **spec):
        """Place a one-off called *name* in *period* through the producer."""
        from app.services.one_off import OneOffToPlace, place_one_off  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel
        from app import ref_cache  # pylint: disable=import-outside-toplevel

        fields = {
            "user_id": seed_user["user"].id,
            "account_id": seed_user["account"].id,
            "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            "name": name,
            "amount": Decimal("4.50"),
            "category_id": seed_user["categories"]["Groceries"].id,
        }
        fields.update(spec)
        return place_one_off(
            OneOffToPlace(**fields), derived_span(period),
            scenario_id=seed_user["scenario"].id,
        )

    @staticmethod
    def _expense_rows(app, auth_client):
        """Return ``(expense_row_keys, matched_by_row_period)`` off ``/grid``."""
        _, context = TestGridMatchedByRowPeriod._capture_grid_context(  # pylint: disable=protected-access
            app, auth_client,
        )
        return context["expense_row_keys"], context["matched_by_row_period"]

    def test_two_same_named_one_offs_are_one_row_with_two_cells(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two "Coffee" one-offs, one category, two paychecks: ONE row."""
        with app.app_context():
            first = self._place(seed_user, seed_periods_today[4], "Coffee")
            second = self._place(seed_user, seed_periods_today[5], "Coffee")
            db.session.commit()
            assert first.template_id != second.template_id

            row_keys, matched = self._expense_rows(app, auth_client)
            coffee = [rk for rk in row_keys if rk.txn_name == "Coffee"]
            assert len(coffee) == 1
            rk = coffee[0]
            assert rk.template_id is None
            cells = {
                period_id: [t.id for t in txns]
                for (cat, tid, name, period_id), txns in matched.items()
                if (cat, tid, name) == (rk.category_id, None, "Coffee")
            }
            assert cells == {
                seed_periods_today[4].id: [first.id],
                seed_periods_today[5].id: [second.id],
            }

    def test_a_one_off_sharing_a_recurring_definitions_name_is_its_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Trace 5's six cells: the recurring row matches by template, the one-off by name.

        One-off "Streaming" in paycheck 4 beside recurring "Streaming" with a
        row in paychecks 4 and 5, same category.  Two row keys; the recurring
        row's cells hold ITS rows only, the one-off's cell holds the one-off
        only -- where the name-match fallback drew each into the other's.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="15.00", name="Streaming",
                category_key="Groceries",
            )
            gen_0 = generate_row_of(template, seed_periods_today[4])
            gen_1 = generate_row_of(template, seed_periods_today[5])
            one_off = self._place(seed_user, seed_periods_today[4], "Streaming")
            db.session.commit()

            row_keys, matched = self._expense_rows(app, auth_client)
            streaming = [rk for rk in row_keys if rk.txn_name == "Streaming"]
            assert sorted((rk.template_id for rk in streaming), key=str) == (
                sorted([template.id, None], key=str)
            )
            by_key = {
                (tid, period_id): [t.id for t in txns]
                for (cat, tid, name, period_id), txns in matched.items()
                if name == "Streaming"
            }
            assert by_key == {
                (template.id, seed_periods_today[4].id): [gen_0.id],
                (template.id, seed_periods_today[5].id): [gen_1.id],
                (None, seed_periods_today[4].id): [one_off.id],
            }
