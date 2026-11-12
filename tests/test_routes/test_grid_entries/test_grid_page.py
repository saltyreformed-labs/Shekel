"""The grid page's entries: entry_sums reaches the grid render, the whole entry-list context comes
from entry_list_view everywhere, and no cell template reads the amount column.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.routes._render_helpers import fragment_amounts
from app.services.settle_day import record_settle_day

from tests._test_helpers import an_entered_day, current_pay_period

from ._helpers import _lists, _create_tracked_txn, _add_entry


class TestGridPageEntrySums:
    """Integration test: entry_sums flows through the grid page render."""

    def test_grid_page_shows_progress(self, app, auth_client,
                                       seed_user, seed_periods_today):
        """GET /grid renders progress format for tracked txns with entries.

        Creates the transaction in the current period so it appears in
        the default grid view.
        """
        with app.app_context():
            # Find the current period so the txn is visible in the grid.
            current = current_pay_period(
                seed_user["user"].id,
            )
            # Find which seed_periods_today index matches the current period.
            period_idx = next(
                (i for i, p in enumerate(seed_periods_today) if p.id == current.id),
                0,
            )

            txn, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=period_idx,
            )
            payday = seed_periods_today[period_idx].start_date
            _add_entry(txn, seed_user, Decimal("180.00"), purchased_on=payday)
            _add_entry(
                txn, seed_user, Decimal("70.00"),
                purchased_on=payday - timedelta(days=1),
            )
            db.session.commit()

            resp = auth_client.get("/grid")

            assert resp.status_code == 200
            # The desktop grid cell should show progress format.
            # 180 + 70 = 250 spent on 500 budget.
            assert b"$250 / $500" in resp.data
            # **And the out-of-period badge, which puts /grid's OWN span map
            # under measurement for the first time** (adversarial review of
            # plan step C4-a-3).  The two purchases are dated on the payday and
            # the day before it, so exactly ONE is out of period -- and the
            # route resolves its spans by a different construction from the
            # unit tests above (``_build_entry_maps`` over
            # ``ctx.all_periods``), which nothing else exercises.  Before this,
            # the whole route-level evidence for the warning was one assertion
            # on the entries FRAGMENT.
            assert resp.data.count(b"Date outside pay period range") == 1


class TestTheEntryListContextHasOneProducer:
    """The whole entry-list context comes from ``entry_list_view``, everywhere.

    **These are the controls the bug had none of.**  Every test of the
    posted-purchase indicator drove ``GET /transactions/<id>/entries``
    (``test_entries/test_update_and_delete.py::TestTheDerivedPostedIndicator``)
    -- the ONE render path that supplied its key.  The grid macro, the mobile card,
    the companion view and the full-edit popover all render the same partial
    from :func:`build_entry_lists_dict`, which did not, and Jinja answers
    ``entry.id in Undefined`` as ``False`` silently.  So on every initial
    render an already-posted purchase read *"Still outstanding"* while the
    projection had already released its reservation -- 9 of 9 such purchases
    on the 2026-08-13 production clone.

    A missing key cannot fail loudly in Jinja, so it is graded here instead:
    the producer supplies the whole context, and the macro is asserted to
    unpack every key of it.
    """

    def test_the_dict_carries_the_posted_indicator(
        self, app, seed_user, seed_periods_today,
    ):
        """A purchase whose bank posting day is recorded comes back POSTED.

        Both purchases below carry one, a day apart and either side of the
        account's only assertion, and both come back posted -- because the
        indicator asks the PURCHASE since plan step X-f3b (ruling **R-FM**).
        It asked the account's clearing rule before, so this test expected the
        later one to be absent; that answer would now contradict the
        reservation, which releases both.
        """
        from tests._test_helpers import (
            append_balance_assertion, settle_instant_on,
        )

        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            inside = _add_entry(txn, seed_user, Decimal("150.00"))
            outside = _add_entry(txn, seed_user, Decimal("80.00"))
            unobserved = _add_entry(txn, seed_user, Decimal("20.00"))
            asserted_on = seed_periods_today[0].start_date
            record_settle_day(inside, an_entered_day(asserted_on))
            record_settle_day(outside, an_entered_day(asserted_on + timedelta(days=1)))
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(asserted_on),
            )
            db.session.commit()

            data = _lists([txn])[txn.id]
            unobserved_id = unobserved.id

        assert data["posted_ids"] == {inside.id, outside.id}, (
            "a purchase with a recorded posting day has left the account, "
            "whichever side of a declared balance its day falls on; one with "
            "no posting day has not"
        )
        assert unobserved_id not in data["posted_ids"]

    def test_the_grid_macro_unpacks_every_key_the_producer_returns(
        self, app, seed_user, seed_periods_today,
    ):
        """The macro's ``{% set %}`` list covers the whole context.

        The negative control for the defect itself: delete ``posted_ids``
        from ``_grid_row_macros.html`` and this fails, where the rendered page
        would have gone on looking plausible.
        """
        from pathlib import Path

        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            db.session.commit()
            produced = set(_lists([txn])[txn.id])

        macro = Path(app.root_path) / "templates/grid/_grid_row_macros.html"
        source = macro.read_text(encoding="utf-8")
        unset = {
            key for key in produced
            if f"{{% set {key} = _entry_data.{key} %}}" not in source
        }
        assert not unset, (
            f"_grid_row_macros.html does not unpack {sorted(unset)} from "
            "_entry_data, and Jinja answers a membership test against an "
            "Undefined as False SILENTLY -- so the partial would render the "
            "wrong arm rather than raising"
        )


class TestTheAmountFenceIsGone:
    """No cell template still reads the amount COLUMN, or a silent fallback.

    Plan step X-au-c2b replaced a transient ``txn.live_estimated_amount`` --
    annotated onto each row by ONE route and read everywhere behind
    ``if ... is defined else txn.estimated_amount`` -- with a ``budgets`` map
    every render path publishes.  Both halves had to go together, and the
    reason is the shape ``TestTheEntryListContextHasOneProducer`` above records:
    a Jinja ``Undefined`` answers silently, so a render path that forgot to set
    the attribute showed the stale column with nothing on screen to say so.

    These are SOURCE assertions rather than render assertions, deliberately.
    What is being asserted is that the fence has no way back -- a future edit
    reintroducing either spelling would render correctly on the one path that
    sets it and wrongly everywhere else, which is exactly the state that shipped
    the posted-purchase bug and which no single render test caught.
    """

    # The EDIT forms are in this census, and an adversarial review is why.
    # They were routed off the column in the same commit but onto a SCALAR
    # ``budget``, which renders ``value=""`` in silence when unpublished where a
    # map raises -- on the two surfaces where an empty figure is POSTED BACK.
    # A census that stopped at the display templates said the fence was gone
    # while the doors that matter most still had it.
    _CELL_TEMPLATES = (
        "grid/_transaction_cell.html",
        "grid/_grid_row_macros.html",
        "grid/_mobile_this_period.html",
        "grid/_mobile_plan.html",
        "grid/_mobile_card_single.html",
        "grid/_transaction_quick_edit.html",
        "grid/_transaction_full_edit.html",
    )

    @staticmethod
    def _source(app, name):
        """Return a template's SOURCE text (not its render).

        Args:
            app: The Flask app, for its Jinja loader.
            name: The template's loader name.

        Returns:
            The template file's text.
        """
        source, _path, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, name,
        )
        return source

    def test_no_cell_template_reads_the_transient_attribute(self, app):
        """``live_estimated_amount`` exists nowhere but in prose about it."""
        for name in self._CELL_TEMPLATES:
            for line in self._source(app, name).splitlines():
                if "live_estimated_amount" not in line:
                    continue
                assert line.lstrip().startswith(("{#", "#", "-#", "{%-")) or (
                    "``" in line
                ), (
                    f"{name} still READS the transient attribute: {line!r}"
                )

    def test_no_cell_template_reads_the_amount_column_for_display(self, app):
        """``estimated_amount`` survives only as a form FIELD NAME and in prose.

        The distinction is the point: ``name="estimated_amount"`` is the column
        an edit POSTS to, which is correct and unchanged; ``{{ txn.estimated_amount }}``
        is a READ of a column a derived row does not carry.
        """
        for name in self._CELL_TEMPLATES:
            for line in self._source(app, name).splitlines():
                if "estimated_amount" not in line:
                    continue
                assert (
                    'name="estimated_amount"' in line
                    or "``" in line
                    or "{#" in line
                    or "#}" in line
                    or not ("{{" in line or "{%" in line)
                ), f"{name} still reads the amount column: {line!r}"

    def test_a_render_without_the_map_FAILS_rather_than_falling_back(self, app):
        """The replacement for the fence: a missing map is an error, not a shrug.

        ``budgets[t.id]`` on an absent map raises ``UndefinedError`` where
        ``t.live_estimated_amount if ... is defined`` rendered the stale column.
        That difference IS the fix -- a render path that forgets to publish the
        map now cannot ship a wrong figure -- so it is asserted rather than left
        as a property of Jinja nobody wrote down.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

        import jinja2  # pylint: disable=import-outside-toplevel

        txn = SimpleNamespace(
            id=1, name="Rent", settled_amount=None,
            estimated_amount=Decimal("1200.00"),
            status=SimpleNamespace(is_settled=False, name="Projected"),
            status_id=99, transfer_id=None, credit_payback_for_id=None,
            is_expense=True, tracks_purchases=False, notes=None,
        )
        template = app.jinja_env.get_template("grid/_transaction_cell.html")
        with app.test_request_context("/"):
            with pytest.raises(
                jinja2.exceptions.UndefinedError, match="budgets",
            ):
                template.render(txn=txn)

    def test_an_EDIT_form_without_the_map_fails_too(
        self, app, seed_user, seed_periods_today,
    ):
        """The door a figure is POSTED BACK from raises like the display ones.

        The one that matters most: a display template rendering an empty amount
        is visible, where an edit box opening blank invites a save that books
        whatever is typed over a figure the user never saw.  It was a scalar
        ``budget`` for one commit, which renders ``value=""`` and says nothing.

        A REAL row rather than a stand-in, so ``budgets`` is the only name the
        render can be missing -- which is what lets the ``match`` be specific
        rather than accepting any ``UndefinedError`` the template happens to
        raise first.
        """
        import jinja2  # pylint: disable=import-outside-toplevel

        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()
            template = app.jinja_env.get_template(
                "grid/_transaction_quick_edit.html",
            )
            with app.test_request_context("/"):
                with pytest.raises(
                    jinja2.exceptions.UndefinedError, match="budgets",
                ):
                    template.render(txn=txn, locked=False)
                # And it RENDERS the resolved figure when the map is published,
                # so the raise above is the missing map rather than the
                # template being unrenderable.
                html = template.render(
                    txn=txn, locked=False, budgets=fragment_amounts(txn).budgets,
                )
                # The row is DERIVED, so the figure the box must carry is the
                # price its definition states -- its own column is None.
                assert 'value="500.00"' in html
