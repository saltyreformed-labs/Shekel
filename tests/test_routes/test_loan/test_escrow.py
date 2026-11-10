"""Loan route tests: the escrow routes.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.services import escrow_calculator, loan_loaders

from tests._test_helpers import add_escrow_line, create_settled_transfer, read_pass

from ._helpers import _create_mortgage, _create_other_loan


# ── Escrow Tests ─────────────────────────────────────────────────────


class TestEscrow:
    """Tests for escrow component management."""

    def test_escrow_add(self, auth_client, seed_user, db, seed_periods):
        """POST escrow creates a line + opening version, percent -> decimal."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00", "inflation_rate": "3"},
        )
        assert resp.status_code == 200
        assert b"Property Tax" in resp.data

        line = (
            db.session.query(EscrowLine)
            .filter_by(account_id=acct.id, name="Property Tax").one()
        )
        version = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line.id).one()
        )
        assert version.annual_amount == Decimal("4800.00")
        # 3% form percent stored as the 0.03 decimal fraction.
        assert version.inflation_rate == Decimal("0.03")
        assert version.is_removed is False

    def test_dashboard_next_year_escrow_note_is_one_annual_step(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The loan card projects next-year escrow one full annual step from today.

        An escrow line of $4,800/yr ($400.00/mo) at a 3% rate shows a "next year"
        note of 4800 * 1.03 / 12 = $412.00/mo -- one annual step (spec Sec. 8),
        NOT the old elapsed-span-since-created_at figure (which on a same-day-
        seeded line rounded to roughly $406).  The projection takes no date
        argument, so this figure is viewing-date independent by construction.
        """
        acct = _create_mortgage(seed_user, db.session)
        add_escrow_line(
            db.session, acct.id, "Property Tax", Decimal("4800.00"),
            inflation_rate=Decimal("0.03"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"may increase" in resp.data
        # Current escrow $400.00/mo is projected to $412.00/mo next year.
        assert b"$412.00" in resp.data

    def _build_split_escrow(self, db, account_id, boundary):
        """Seed the account-3 rename-split: removed "Old Tax" + active "New Tax".

        "Old Tax" is $6,000/yr ($500.00/mo) from the loan's origination, tombstoned
        on ``boundary``; "New Tax" is $7,200/yr ($600.00/mo) from ``boundary``.  The
        two tile the timeline (no overlap), so escrow resolves to $500 before the
        boundary and $600 on/after it.  Returns ``(old_line, new_line)``.
        """
        old = add_escrow_line(db.session, account_id, "Old Tax", Decimal("6000.00")).line
        db.session.add(EscrowComponentVersion(
            line_id=old.id, effective_date=boundary,
            annual_amount=Decimal("0.00"), is_removed=True,
        ))
        add_escrow_line(
            db.session, account_id, "New Tax", Decimal("7200.00"),
            effective_date=boundary,
        )
        db.session.commit()
        new = (
            db.session.query(EscrowLine)
            .filter_by(account_id=account_id, name="New Tax").one()
        )
        return old, new

    def test_merge_reunifies_split_line(self, auth_client, seed_user, db, seed_periods):
        """POST merge folds a removed line's history into the active line, escrow intact.

        Merging "Old Tax" into "New Tax" leaves only the new line, carrying BOTH
        versions, and the escrow resolved on every date is unchanged ($500 before
        the boundary, $600 on/after) -- so no settled split can move, since the split
        reads escrow solely via escrow_monthly_as_of.
        """
        acct = _create_mortgage(seed_user, db.session)
        boundary = date(2026, 3, 1)
        old, new = self._build_split_escrow(db, acct.id, boundary)

        # The removed predecessor is hidden from the card but offered as a merge
        # source in the active line's drawer (labelled by its span + removed state).
        page = auth_client.get(f"/accounts/{acct.id}/loan")
        assert b"Merge in" in page.data
        assert b"Old Tax (Jun 2023 - Mar 2026, removed)" in page.data

        lines_before = loan_loaders.load_escrow_lines(acct.id)
        before_early = escrow_calculator.escrow_monthly_as_of(
            lines_before, date(2026, 1, 1),
        )
        before_late = escrow_calculator.escrow_monthly_as_of(
            lines_before, date(2026, 4, 1),
        )
        assert before_early == Decimal("500.00")
        assert before_late == Decimal("600.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{new.id}/merge",
            data={"source_line_id": old.id},
        )
        assert resp.status_code == 200

        # Only the merged line survives, carrying both versions in date order.
        remaining = db.session.query(EscrowLine).filter_by(account_id=acct.id).all()
        assert [line.id for line in remaining] == [new.id]
        assert db.session.get(EscrowLine, old.id) is None
        versions = sorted(
            db.session.query(EscrowComponentVersion).filter_by(line_id=new.id).all(),
            key=lambda v: v.effective_date,
        )
        assert [(v.effective_date, v.annual_amount) for v in versions] == [
            (date(2023, 6, 1), Decimal("6000.00")),
            (boundary, Decimal("7200.00")),
        ]
        # Escrow-per-date is unchanged, so no derived split moves.
        lines_after = loan_loaders.load_escrow_lines(acct.id)
        assert escrow_calculator.escrow_monthly_as_of(
            lines_after, date(2026, 1, 1),
        ) == before_early
        assert escrow_calculator.escrow_monthly_as_of(
            lines_after, date(2026, 4, 1),
        ) == before_late

    def test_merge_rejects_overlap(self, auth_client, seed_user, db, seed_periods):
        """POST merge on two concurrent lines returns 400; both lines survive.

        "Tax" ($6,000/yr from origination) and "Insurance" ($7,200/yr from 2024)
        overlap in time, so merging would drop a charge.  The route rejects it with
        an actionable message and leaves both lines intact.
        """
        acct = _create_mortgage(seed_user, db.session)
        tax = add_escrow_line(db.session, acct.id, "Tax", Decimal("6000.00")).line
        ins = add_escrow_line(
            db.session, acct.id, "Insurance", Decimal("7200.00"),
            effective_date=date(2024, 1, 1),
        ).line
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{ins.id}/merge",
            data={"source_line_id": tax.id},
        )
        assert resp.status_code == 400
        assert b"overlap" in resp.data.lower()
        assert db.session.get(EscrowLine, tax.id) is not None
        assert db.session.get(EscrowLine, ins.id) is not None

    def test_merge_source_idor(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Merging in a source line owned by another user returns 404, merges nothing."""
        mine = _create_mortgage(seed_user, db.session)
        target = add_escrow_line(db.session, mine.id, "My Tax", Decimal("6000.00")).line
        other = _create_other_loan(second_user, db.session)
        foreign = add_escrow_line(
            db.session, other.id, "Foreign Tax", Decimal("6000.00"),
        ).line
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{mine.id}/loan/escrow/{target.id}/merge",
            data={"source_line_id": foreign.id},
        )
        assert resp.status_code == 404
        assert db.session.get(EscrowLine, target.id) is not None
        assert db.session.get(EscrowLine, foreign.id) is not None

    def test_merge_into_self_rejected(self, auth_client, seed_user, db, seed_periods):
        """Merging a line into itself returns 400 and changes nothing."""
        acct = _create_mortgage(seed_user, db.session)
        line = add_escrow_line(db.session, acct.id, "Tax", Decimal("6000.00")).line
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line.id}/merge",
            data={"source_line_id": line.id},
        )
        assert resp.status_code == 400
        assert db.session.get(EscrowLine, line.id) is not None
        assert db.session.query(EscrowComponentVersion).filter_by(
            line_id=line.id,
        ).count() == 1

    def test_merge_preserves_settled_payment_split(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A settled payment's split survives merging its escrow line.

        The proof that a merge moves no settled split: a payment settled in
        period 0 (2026-01-02, before the 2026-03-01 boundary) splits with an
        escrow leg of "Old Tax" $6,000/yr = $500.00/mo.  Merging "Old Tax" into
        "New Tax" preserves escrow-per-date, so the split the history reads
        off the walk after the merge -- derived from the merged line -- is the
        SAME principal / interest / escrow.  A deploy backfill re-posted the
        stored split before and after the merge until plan task plan#3077
        deleted it; the history reads the walk, never that store.
        """
        from app.services.loan_walk_reads import (  # pylint: disable=import-outside-toplevel
            confirmed_loan_payment_history,
        )
        acct = _create_mortgage(seed_user, db.session)
        old, new = self._build_split_escrow(db, acct.id, date(2026, 3, 1))
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("2000.00"),
            settled_on=seed_periods[0].start_date,
        )
        db.session.commit()

        # A pass per read: a pass memoizes the loan's walk, so the second read
        # needs its own to see the merge.
        before = confirmed_loan_payment_history(
            acct, read_pass(acct, seed_user["scenario"], date.today()),
        )
        assert len(before) == 1
        assert before[0].escrow == Decimal("500.00")  # Old Tax: 6000 / 12

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{new.id}/merge",
            data={"source_line_id": old.id},
        )
        assert resp.status_code == 200

        # The split is identical, derived from the merged line, escrow-per-date
        # being preserved.
        after = confirmed_loan_payment_history(
            acct, read_pass(acct, seed_user["scenario"], date.today()),
        )
        assert after[0].escrow == before[0].escrow
        assert after[0].principal == before[0].principal
        assert after[0].interest == before[0].interest

    def test_escrow_add_duplicate_name(self, auth_client, seed_user, db, seed_periods):
        """Duplicate ACTIVE line name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        add_escrow_line(db.session, acct.id, "Insurance", Decimal("2400.00"))
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Insurance", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data

    def test_escrow_delete(self, auth_client, seed_user, db, seed_periods):
        """POST delete appends a removal tombstone; the line resolves inactive."""
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Old Insurance", Decimal("1200.00"),
        ).line_id
        db.session.commit()

        resp = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line_id}/delete")
        assert resp.status_code == 200
        db.session.expire_all()
        # Removal appends an is_removed tombstone (supersession) at today, so the
        # line's latest version is that tombstone and it contributes nothing now.
        line = db.session.get(EscrowLine, line_id)
        latest = max(line.versions, key=lambda v: v.effective_date)
        assert latest.is_removed is True

    def test_escrow_delete_idor(self, auth_client, second_user, db, seed_periods):
        """DELETE another user's escrow returns 404 and leaves it active."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        line_id = add_escrow_line(
            db.session, other.id, "Tax", Decimal("3000.00"),
        ).line_id
        db.session.commit()

        resp = auth_client.post(f"/accounts/{other.id}/loan/escrow/{line_id}/delete")
        assert resp.status_code == 404

        db.session.expire_all()
        # No tombstone appended: the line's only version is the original, active.
        line = db.session.get(EscrowLine, line_id)
        latest = max(line.versions, key=lambda v: v.effective_date)
        assert latest.is_removed is False

    def test_escrow_delete_same_day_add_converts_in_place(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Add then delete the SAME day converts the today-version in place.

        The route add opens the line's version AT today, so a same-day delete
        cannot append a second tombstone at today (the
        ``uq_escrow_component_versions_line_effective_date`` unique forbids two
        versions on one date).  It must convert that version in place --
        ``is_removed`` True, ``annual_amount`` 0.00 (satisfying the
        tombstone-zero CHECK) -- leaving exactly one version and an inactive line.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Add via the route so the opening version is effective TODAY.
        add = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "PMI", "annual_amount": "1200.00"},
        )
        assert add.status_code == 200
        line = (
            db.session.query(EscrowLine)
            .filter_by(account_id=acct.id, name="PMI").one()
        )

        resp = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line.id}/delete")
        assert resp.status_code == 200
        db.session.expire_all()
        line = db.session.get(EscrowLine, line.id)
        # Converted in place: still ONE version, now a zero-amount tombstone.
        assert len(line.versions) == 1
        assert line.versions[0].is_removed is True
        assert line.versions[0].annual_amount == Decimal("0.00")

    def test_escrow_delete_twice_is_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A repeat delete is a no-op: still exactly one tombstone, no 500.

        After the first delete the line resolves inactive today, so the second
        delete's resolve-active-today guard short-circuits before appending
        another tombstone (a double-click must not stack tombstones or error).
        """
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Old Insurance", Decimal("1200.00"),
        ).line_id
        db.session.commit()

        first = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line_id}/delete")
        second = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line_id}/delete")
        assert first.status_code == 200
        assert second.status_code == 200
        db.session.expire_all()
        line = db.session.get(EscrowLine, line_id)
        # Exactly one tombstone appended (the origination version + one tombstone).
        tombstones = [v for v in line.versions if v.is_removed]
        assert len(tombstones) == 1

    def test_escrow_oob_payment_update(self, auth_client, seed_user, db, seed_periods):
        """Adding escrow returns OOB fragments for payment summary."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="total-payment-chip"' in html
        assert 'hx-swap-oob="true"' in html
        assert "$400.00/mo" in html

    # ── Effective-date field + forward-only guard ────────────────────

    def test_escrow_add_with_effective_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST add with an explicit effective_date opens the version at that date."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={
                "name": "Property Tax", "annual_amount": "7200.00",
                "effective_date": "2026-02-01",
            },
        )
        assert resp.status_code == 200
        line = (
            db.session.query(EscrowLine)
            .filter_by(account_id=acct.id, name="Property Tax").one()
        )
        version = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line.id).one()
        )
        assert version.effective_date == date(2026, 2, 1)
        assert version.annual_amount == Decimal("7200.00")

    def test_escrow_add_rejects_before_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An effective date before origination (2023-06-01) is rejected; no line."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={
                "name": "Tax", "annual_amount": "1200.00",
                "effective_date": "2023-05-01",
            },
        )
        assert resp.status_code == 400
        assert b"origination" in resp.data
        assert (
            db.session.query(EscrowLine).filter_by(account_id=acct.id).count() == 0
        )

    def test_forward_guard_rejects_on_or_before_latest_settled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Add-version on/before the latest settled payment's DUE date is rejected.

        A settled payment in seed_periods[0] (start 2026-01-02) satisfies the
        2026-02-01 installment (``payment_day`` 1), and its split resolves escrow
        on that DUE date -- contract time, ruling D5 / finding N-34 -- so that is
        the frozen boundary.  A version effective 2026-02-01 (== the due date)
        would move that settled split, so it is rejected; 2026-02-02 (strictly
        after) is allowed.

        **A period-start boundary is what this must not be:** the payment's period
        starts 2026-01-02, a full month before the installment it pays, so the old
        boundary admitted every date in between -- each of which still governs the
        settled split.  2026-01-03 is asserted rejected for exactly that reason.
        """
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("1500.00"),
        )
        db.session.commit()
        assert seed_periods[0].start_date == date(2026, 1, 2)

        # Inside the period-start .. due-date window: the old guard allowed this.
        inside_window = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-01-03"},
        )
        assert inside_window.status_code == 400
        assert b"latest recorded payment" in inside_window.data

        on_boundary = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-02-01"},
        )
        assert on_boundary.status_code == 400
        assert b"latest recorded payment" in on_boundary.data

        after = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-02-02"},
        )
        assert after.status_code == 200
        assert (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id, effective_date=date(2026, 2, 2)).count() == 1
        )

    def test_forward_guard_boundary_is_latest_settled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The guard boundary is the LATEST settled payment's due date, not the earliest.

        Two settled payments whose installments differ: seed_periods[0] (start
        2026-01-02, due 2026-02-01) and seed_periods[3] (start 2026-02-13, due
        2026-03-01).  The boundary is the LATER due date (2026-03-01), so a version
        effective 2026-02-15 -- after the first payment's installment but before the
        second's -- is STILL rejected (a min-based guard would wrongly allow it, the
        exact bug this correction avoids); only strictly after 2026-03-01 is allowed.

        The two periods are chosen so their DUE dates differ: periods 0, 1 and 2 all
        satisfy the same 2026-02-01 installment, which would make max and min
        coincide and leave this test no teeth.
        """
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("1500.00"),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[3], amount=Decimal("1500.00"),
        )
        db.session.commit()
        assert seed_periods[3].start_date == date(2026, 2, 13)

        between = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-02-15"},
        )
        assert between.status_code == 400
        # Rejected by the SETTLED-payment boundary, not by the origination bound
        # or a same-date collision (which would satisfy a bare 400).
        assert b"latest recorded payment" in between.data

        after = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-03-02"},
        )
        assert after.status_code == 200

    # ── Version drawer CRUD: schedule / edit / delete a version ───────

    def test_add_version_schedules_change(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST add-version appends a future-dated version under the existing line."""
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7403.88"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8003.88", "effective_date": "2026-08-01"},
        )
        assert resp.status_code == 200
        versions = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id)
            .order_by(EscrowComponentVersion.effective_date).all()
        )
        assert len(versions) == 2
        assert versions[1].effective_date == date(2026, 8, 1)
        assert versions[1].annual_amount == Decimal("8003.88")

    def test_add_version_duplicate_date_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A second version on a date the line already carries is rejected."""
        acct = _create_mortgage(seed_user, db.session)
        # add_escrow_line opens the version at origination (2023-06-01).
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2023-06-01"},
        )
        assert resp.status_code == 400
        assert b"already has a version" in resp.data
        assert (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id).count() == 1
        )

    def _line_with_scheduled(self, db, account_id):
        """Build a line with an origination version + a scheduled 2026-08-01 version.

        Returns ``(line_id, scheduled_version)`` for the edit / delete tests.
        """
        v1 = add_escrow_line(db.session, account_id, "Tax", Decimal("7200.00"))
        db.session.add(EscrowComponentVersion(
            line_id=v1.line_id, effective_date=date(2026, 8, 1),
            annual_amount=Decimal("8003.88"),
        ))
        db.session.commit()
        sched = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=v1.line_id, effective_date=date(2026, 8, 1)).one()
        )
        return v1.line_id, sched

    def test_edit_version_updates_scheduled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Editing a scheduled (unfrozen) version updates amount + date in place."""
        acct = _create_mortgage(seed_user, db.session)
        _line_id, sched = self._line_with_scheduled(db, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{sched.id}/edit",
            data={"annual_amount": "8500.00", "effective_date": "2026-09-01"},
        )
        assert resp.status_code == 200
        db.session.refresh(sched)
        assert sched.annual_amount == Decimal("8500.00")
        assert sched.effective_date == date(2026, 9, 1)

    def test_edit_version_frozen_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A version at/before the latest settled payment is frozen -- edit rejected."""
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(db.session, acct.id, "Tax", Decimal("7200.00"))
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("1500.00"),
        )
        db.session.commit()
        # The origination version (2023-06-01) is <= the settled payment's period
        # start (2026-01-02), so it underpins that settled split and is read-only.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{version.id}/edit",
            data={"annual_amount": "9000.00", "effective_date": "2023-06-01"},
        )
        assert resp.status_code == 400
        assert b"settled payment" in resp.data
        db.session.refresh(version)
        assert version.annual_amount == Decimal("7200.00")

    def test_delete_version_removes_scheduled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Deleting a scheduled (unfrozen, non-sole) version removes just that one."""
        acct = _create_mortgage(seed_user, db.session)
        line_id, sched = self._line_with_scheduled(db, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{sched.id}/delete",
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowComponentVersion, sched.id) is None
        # The origination version survives.
        assert (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id).count() == 1
        )

    def test_delete_version_current_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A current (non-scheduled) version can't be per-version deleted.

        The origination version (2023-06-01) is on/before today, so it is not a
        scheduled future change; the route rejects the delete (edit it, or remove
        the whole line, instead).
        """
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(db.session, acct.id, "Tax", Decimal("7200.00"))
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{version.id}/delete",
        )
        assert resp.status_code == 400
        assert b"scheduled future change" in resp.data
        assert db.session.get(EscrowComponentVersion, version.id) is not None

    def test_delete_upcoming_only_scheduled_drops_line(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Deleting an upcoming-only line's sole scheduled version removes the line.

        A line added with a FUTURE effective date (2026-12-01, after the frozen
        today of 2026-03-20) has no current version; deleting that sole scheduled
        version leaves the line empty, so the orphan line is dropped too.
        """
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(
            db.session, acct.id, "Future", Decimal("6000.00"),
            effective_date=date(2026, 12, 1),
        )
        line_id = version.line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{version.id}/delete",
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowComponentVersion, version.id) is None
        assert db.session.get(EscrowLine, line_id) is None

    # ── Rename in place ──────────────────────────────────────────────

    def test_rename_line_updates_name(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Renaming a line updates its label; the version amount is untouched."""
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(
            db.session, acct.id, "Property Tax & Insurance", Decimal("7200.00"),
        )
        line_id = version.line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/rename",
            data={"name": "Tax and Insurance"},
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowLine, line_id).name == "Tax and Insurance"
        # Rename is display-only: the version amount does not move.
        db.session.refresh(version)
        assert version.annual_amount == Decimal("7200.00")

    def test_rename_duplicate_active_name_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Renaming onto another ACTIVE line's name is rejected."""
        acct = _create_mortgage(seed_user, db.session)
        add_escrow_line(db.session, acct.id, "Insurance", Decimal("1200.00"))
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/rename",
            data={"name": "Insurance"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data
        assert db.session.get(EscrowLine, line_id).name == "Tax"

    def test_rename_to_same_name_allowed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Renaming a line to its own current name is a no-op, allowed."""
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/rename",
            data={"name": "Tax"},
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowLine, line_id).name == "Tax"

    def test_dashboard_escrow_drawer_renders(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The dashboard GET renders the escrow card: summary, scheduled badge, drawer.

        A line with an origination version (current) + a 2026-08-01 version
        (scheduled) must render the summary amount, the 'Scheduled' badge, the
        collapsible drawer, both status labels, the inline edit inputs for the
        scheduled row, and the schedule-a-change form -- proving the version-drawer
        template renders inline exactly as the HTMX routes re-render it.
        """
        acct = _create_mortgage(seed_user, db.session)
        v1 = add_escrow_line(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        db.session.add(EscrowComponentVersion(
            line_id=v1.line_id, effective_date=date(2026, 8, 1),
            annual_amount=Decimal("8003.88"),
        ))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Property Tax" in html
        # Collapsed summary flags a queued change.
        assert "Scheduled" in html
        assert f'id="escrow-drawer-{v1.line_id}"' in html
        # Status labels for the two versions.
        assert "Current" in html
        # The scheduled version renders as an inline edit form (its amount input).
        assert 'name="annual_amount"' in html
        assert "Schedule a change" in html
        # The rejected-change error surface is present (hidden until a 4xx).
        assert 'id="escrow-error"' in html

    def test_version_routes_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """Another user's escrow version routes return 404 (no existence oracle)."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        version = add_escrow_line(db.session, other.id, "Tax", Decimal("7200.00"))
        db.session.commit()
        edit = auth_client.post(
            f"/accounts/{other.id}/loan/escrow/version/{version.id}/edit",
            data={"annual_amount": "9000.00", "effective_date": "2026-08-01"},
        )
        delete = auth_client.post(
            f"/accounts/{other.id}/loan/escrow/version/{version.id}/delete",
        )
        rename = auth_client.post(
            f"/accounts/{other.id}/loan/escrow/{version.line_id}/rename",
            data={"name": "Hacked"},
        )
        assert edit.status_code == 404
        assert delete.status_code == 404
        assert rename.status_code == 404

    def test_version_edit_cross_account_version_id_is_404(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Passing MY account_id with another user's version_id returns 404.

        The subtler IDOR that ``_owned_version`` exists for: ``@require_owner``
        passes (I own the URL account), but the version belongs to the victim's
        line, so the account-mismatch check must still 404 -- no cross-account
        version edit / delete.
        """
        mine = _create_mortgage(seed_user, db.session)
        victim = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        victim_version = add_escrow_line(
            db.session, victim.id, "Tax", Decimal("7200.00"),
        )
        db.session.commit()
        edit = auth_client.post(
            f"/accounts/{mine.id}/loan/escrow/version/{victim_version.id}/edit",
            data={"annual_amount": "9000.00", "effective_date": "2026-08-01"},
        )
        delete = auth_client.post(
            f"/accounts/{mine.id}/loan/escrow/version/{victim_version.id}/delete",
        )
        assert edit.status_code == 404
        assert delete.status_code == 404
        db.session.refresh(victim_version)
        assert victim_version.annual_amount == Decimal("7200.00")

    # ── Early-settle regime: boundary can be in the FUTURE ────────────

    def test_delete_version_blocked_by_early_settle_boundary(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A version in (today, boundary] can't be deleted when a payment is paid ahead.

        Frozen today is 2026-03-20; seed_periods[6] starts 2026-03-27 and its
        installment falls 2026-04-01 (``payment_day`` 1).  Settling a payment for
        period[6] BEFORE its period begins (an early-settle) puts the boundary at
        that DUE date -- 2026-04-01, in the FUTURE (ruling D5 / finding N-34; it
        was the 2026-03-27 period start before).  A version effective 2026-03-25
        (after today, but on/before the boundary) underpins that settled payment's
        escrow split, so deleting it must be rejected even though it is "after
        today" -- the exact bypass the ``> today``-only guard allowed.
        """
        acct = _create_mortgage(seed_user, db.session)
        origin = add_escrow_line(db.session, acct.id, "Tax", Decimal("3600.00"))
        # Schedule a version at 2026-03-25 (allowed now: no settled payment yet).
        gap_version = EscrowComponentVersion(
            line_id=origin.line_id, effective_date=date(2026, 3, 25),
            annual_amount=Decimal("4800.00"),
        )
        db.session.add(gap_version)
        db.session.commit()
        # Early-settle period[6] (starts 2026-03-27, after today) -> boundary future.
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[6], amount=Decimal("1500.00"),
        )
        db.session.commit()
        assert seed_periods[6].start_date == date(2026, 3, 27)
        lines = loan_loaders.load_escrow_lines(acct.id)
        escrow_at_installment = escrow_calculator.escrow_monthly_as_of(
            lines, date(2026, 4, 1),
        )
        # 4800 / 12 = 400.00 (the 2026-03-25 version wins as of the 04-01
        # installment -- the date the settled payment's split reads).
        assert escrow_at_installment == Decimal("400.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{gap_version.id}/delete",
        )
        assert resp.status_code == 400
        assert b"settled payment" in resp.data
        # The version survives and the settled payment's escrow is unmoved.
        assert db.session.get(EscrowComponentVersion, gap_version.id) is not None
        lines = loan_loaders.load_escrow_lines(acct.id)
        assert escrow_calculator.escrow_monthly_as_of(
            lines, date(2026, 4, 1),
        ) == Decimal("400.00")

    def test_delete_line_blocked_by_early_settle_boundary(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Line remove (tombstone as of today) is blocked while a payment is paid ahead.

        The removal tombstone lands at today (2026-03-20); with an early-settled
        payment for period[6] (starts 2026-03-27, installment due 2026-04-01) the
        boundary is that DUE date, so a tombstone at today is on/before the
        boundary and would zero the line for that settled payment.  The route must
        reject the removal.
        """
        acct = _create_mortgage(seed_user, db.session)
        line = add_escrow_line(db.session, acct.id, "Tax", Decimal("3600.00"))
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[6], amount=Decimal("1500.00"),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line.line_id}/delete",
        )
        assert resp.status_code == 400
        assert b"latest recorded payment" in resp.data
        # No tombstone appended: the line's only version is still the original.
        db.session.expire_all()
        reloaded = db.session.get(EscrowLine, line.line_id)
        assert all(not v.is_removed for v in reloaded.versions)
        lines = loan_loaders.load_escrow_lines(acct.id)
        # 3600 / 12 = 300.00, unchanged at the early-settled payment's 04-01
        # installment -- the date its split resolves escrow on.
        assert escrow_calculator.escrow_monthly_as_of(
            lines, date(2026, 4, 1),
        ) == Decimal("300.00")
