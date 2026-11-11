"""The account page's link to the statements page, and what the statements page shows of each
import.
"""

from datetime import date
import logging
import re

from app.models.statement_import import StatementImport
from app.services.statement_import import bank_levels
from app.utils.log_events import EVT_STATEMENT_IMPORTED
from tests.test_services.test_statement_import import _csv_builder as build

from ._helpers import _ENTRIES, _placed_on, _payload, _flash_toasts, _upload


class TestTheAccountPageLinksHere:
    """A feature nothing reaches is a feature nobody has."""

    def test_a_NON_CASH_account_has_no_statements_page(
        self, auth_client, db, seed_user,
    ):
        """A loan, a property or a 401(k) has no bank statement to import.

        Ownership alone is not the gate: without the account-KIND check this
        page rendered for those types with a back link that 404s, and would
        record bank lines against an account with no cash ledger.  The project
        has already paid twice for a sibling route skipping this helper.
        """
        from app.models.ref import AccountType
        from app.services import account_service
        from decimal import Decimal

        mortgage_type = (
            db.session.query(AccountType).filter_by(name="Mortgage").one()
        )
        loan = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="House Loan",
                anchor_balance=Decimal("100000.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
        )
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this test only flushed is
        # one the request cannot see.  The 404 below must come from the KIND
        # gate refusing a real mortgage rather than from a missing row.
        db.session.commit()

        assert auth_client.get(
            f"/accounts/{loan.id}/statements"
        ).status_code == 404
        assert _upload(
            auth_client, loan.id, _payload(),
        ).status_code == 404

    def test_a_first_import_says_its_balance_is_UNCORROBORATED(
        self, auth_client, db, seed_user,
    ):
        """Nothing can check the first file's stated balance, and it says so.

        **The signal used to be the running-balance chain, and that was
        measured WRONG** (plan step ``bank_import:X-f6e-1``): SECU stopped
        exporting that column, so the old warning fired on every modern import
        while the file's ``Totals:`` row had already checked the line list.
        What is genuinely unconfirmed on a first import is the BALANCE.

        **This is the ASSUMED-day arm, and the warning it keeps is the one
        plan step ``bank_import:X-gc`` deliberately did not remove.**  A first
        import has nothing to solve its day against, so
        :func:`~app.services.statement_import.resolve_anchor` takes the file's
        last line -- ``day_is_solved`` is ``False`` -- and the developer's
        ruling **R-GN** (2026-08-25) is that a GUESSED placement earns the
        warning
        where a merely uncorroborated LEVEL no longer does.

        **A draft of this step flipped the assertion to ``success`` and it was
        wrong**, because the level cannot tell the two arms apart: an import
        that PROVES its day against an unconfirmed opening mints the same
        ``uncorroborated``.  Two adversarial reviews measured the green tick
        landing on the developer's 2026-01-02..2026-03-31 export, whose header
        names a day 145 days past its last line.
        ``test_a_SOLVED_day_on_an_UNCORROBORATED_chain_is_not_a_warning`` below
        is the other side of the pair, and it is what makes this one
        discriminate.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))

        response = _upload(auth_client, seed_user["account"].id, payload)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "Nothing has confirmed its stated balance" in message
        assert category == "warning"
        # ...and the receipt says WHY in words, not only in colour: the level
        # alone cannot distinguish this from a proven day.
        assert "taken from its last line rather than worked out" in message
        # **And it prescribes nothing.**  The sentence told the owner to
        # "Export once with your bank's running-balance option ticked", an
        # option no SECU export he can take today offers.
        assert "option ticked" not in message

    def test_a_SOLVED_day_on_an_UNCORROBORATED_chain_is_not_a_warning(
        self, auth_client, db, seed_user,
    ):
        """The half of ruling **R-GN** that REMOVED a warning.

        The level and the placement are two facts, and only one of them earns a
        warning now.  Here the chain behind the figure is unconfirmed -- the
        first file was chainless, so everything solved against it inherits
        ``uncorroborated`` by the weakest-link rule -- while the DAY is worked
        out from what the account already holds rather than guessed.  That is
        the developer's steady state, measured on his real books 2026-08-25:
        one contiguous recorded run from 2026-01-02 to 2026-08-21, so his next
        import's day is solved.

        **This is what makes the pair discriminate.**  Without it the predicate
        could warn on every ``uncorroborated`` import -- which is what it did
        before this step -- and
        ``test_a_first_import_says_its_balance_is_UNCORROBORATED`` above would
        still be green.

        The arithmetic: the first file opens at `$100.00` and its two lines
        move `-25.00` then `+1500.00`, so it closes at `$1,575.00` on 03-03.
        The second covers 03-03..03-05 with `+1500.00` and `-30.00`, so the
        balance before its first line is `1575.00 - 1500.00 = 75.00` and its
        own closing is `75.00 + 1500.00 - 30.00 = 1545.00`.
        """
        # The first file carries NO chain, so its own day is assumed (arm 3)
        # and its figure is uncorroborated -- which is what makes everything
        # solved against it inherit that level.  Its header is stated
        # explicitly because a chainless file has no implied closing for the
        # builder to take.
        _upload(
            auth_client, seed_user["account"].id,
            build.build(
                build.chained("100.00", _ENTRIES, with_running=False),
                balance_as_of="03/03/2026", stated_balance="1575.00",
            ),
        )

        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(
                build.chained(
                    "0.00",
                    [(date(2026, 3, 3), "1500.00",
                      "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
                     (date(2026, 3, 5), "-30.00",
                      "POINT OF SALE DEBIT L340 FUEL")],
                    with_running=False,
                ),
                balance_as_of="03/05/2026", stated_balance="1545.00",
            ),
        )
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        # The LEVEL is still the weakest rung...
        assert "Nothing has confirmed its stated balance" in message
        # ...and the DAY was worked out, so nothing here was guessed.
        assert "taken from its last line rather than worked out" not in message
        assert category == "success"

    def test_a_CHAINED_file_is_PROVED_by_itself_and_ticked_green(
        self, auth_client, db, seed_user,
    ):
        """A per-line running balance states the opening, so nothing is assumed.

        The firing half of the pair above: this asserts the warning's ABSENCE
        on a file that carries its own proof, so a receipt that warned
        unconditionally would fail here and one that never warned would fail
        there -- and the same for the colour.
        """
        response = _upload(auth_client, seed_user["account"].id, _payload())
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "proved by the file" in message
        assert "per-line running balance" in message
        assert "Nothing has confirmed" not in message
        assert category == "success"

    def test_a_re_import_that_adds_NO_LINES_still_reports_its_balance(
        self, auth_client, db, seed_user,
    ):
        """A line is not the only thing an import can learn.

        **Found by driving the real app rather than by a green test**: on the
        developer's dev database, re-importing his 2026-08-16 export added 0 of
        its 361 lines and yet RECORDED an anchor -- its stated `$4,747.63`
        placed at 2026-08-13 -- while the receipt said "Nothing new" and
        nothing else.  The idempotent no-op path had never carried the balance
        sentence, because until this step there was no balance fact for it to
        carry.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(auth_client, seed_user["account"].id, _payload())
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        _, message = toasts[0]
        assert "Nothing new" in message
        assert "proved by the file" in message
        assert "per-line running balance" in message

    def test_a_RE_UPLOAD_cannot_promote_an_UNCORROBORATED_balance(
        self, auth_client, db, seed_user,
    ):
        """The assumption may not check itself.

        **Reproduced in two clicks by adversarial review, 2026-08-23**: the
        first import of a chainless file was recorded as unconfirmed, and
        re-uploading the IDENTICAL BYTES made the app walk back to its own
        assumption, find that the file agreed with it, and record the result as
        corroborated -- with the receipt turning from a warning to a green
        tick.  Nothing had reconciled.  The weakest-link rule makes that
        unreachable: the chain behind the second answer still contains the
        first one's assumption.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))
        _upload(auth_client, seed_user["account"].id, payload)

        response = _upload(auth_client, seed_user["account"].id, payload)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "Nothing has confirmed its stated balance" in message
        assert "agrees with the statements already recorded" not in message
        # **The COLOUR is no longer the tell** (plan step ``bank_import:X-gc``,
        # ruling **R-GN**): an uncorroborated import reports as an
        # ordinary success now, so what this control has to prove is that the
        # second upload did not become CORROBORATED -- which is the promotion
        # the review reproduced, and which the assertion above pins directly.
        # ``info`` and not ``success`` because this upload adds no LINES: the
        # re-import path reports "Nothing new", and its non-warning colour is
        # the neutral one.
        assert category == "info"

    def test_a_SECOND_import_is_CORROBORATED_against_a_proved_anchor(
        self, auth_client, db, seed_user,
    ):
        """Two statements agreeing, rooted in one that proved itself.

        The first file carries a chain, so it is ``file_chain``; the second
        does not, and is checked against what the first left behind.  That is
        the only route to ``corroborated``, which is the point of the
        weakest-link rule and is why this fixture pays for a chained first
        file rather than reusing the chainless one above.

        The arithmetic: the first file opens at `$100.00` and its two lines
        move `-25.00` then `+1500.00`, so it closes at `$1,575.00` on 03-03.
        The second covers 03-03..03-05 with `+1500.00` and `-30.00`, so the
        balance before its first line is `1575.00 - 1500.00 = 75.00` and its
        own closing is `75.00 + 1500.00 - 30.00 = 1545.00`.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        later = build.build(
            build.chained(
                "0.00",
                [(date(2026, 3, 3), "1500.00",
                  "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
                 (date(2026, 3, 5), "-30.00",
                  "POINT OF SALE DEBIT L340 FUEL")],
                with_running=False,
            ),
            stated_balance="1545.00",
        )
        response = _upload(auth_client, seed_user["account"].id, later)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "agrees with the statements already recorded" in message
        assert category == "success"

    def test_a_DATE_RANGE_export_records_its_claim_and_no_anchor(
        self, auth_client, db, seed_user,
    ):
        """The measured shape a refusal would have rejected.

        The developer exported 2026-01-02..2026-03-31 on 2026-08-23 and its
        header states TODAY's balance -- 145 days past its last line and
        `$255.41` from what its own 139 lines imply.  Its claim is real and its
        placement is undeterminable, and the receipt says exactly that rather
        than refusing an honest file or inventing a day for it.

        **A prior anchor has to exist for this arm to be reachable at all**,
        and that is the design rather than fixture convenience: "cannot be
        placed" is a statement about a KNOWN opening the figure fails to
        reconcile with.  A first import has no opening to fail against, so the
        same file would be taken at face value -- which is exactly what
        finding **N-342** records, and it is why the first upload here carries
        a chain.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", _ENTRIES, with_running=False),
                        balance_as_of="08/23/2026", stated_balance="2459.60"),
        )
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        # The words changed at plan step ``bank_import:X-gr`` (finding
        # **BI-489**, developer ruling 2026-09-12): "which its own lines do
        # not reach" named this file's cause and was printed on files whose
        # lines reach fine while the recorded opening disagrees, so the
        # sentence now states what the solve knows and no cause.
        assert (
            "which no day it covers reconciles with what was already recorded "
            "before it" in message
        )
        assert "no balance was recorded from it" in message
        assert category == "warning"

    def test_a_file_after_a_GAP_says_the_record_did_not_reach_it(
        self, auth_client, db, seed_user,
    ):
        """Ruling **bank_import:R-BI35**, on the receipt.

        A level stands (the first file's, at 03-03: 100.00 - 25.00 +
        1,500.00 = 1,575.00, solved on its own chain) and this file opens on
        03-10 with 03-04..03-09 imported by nobody, so nothing recorded
        prices the day before it.  Until the ruling this took the ASSUMED
        arm and read as an ordinary placement at 03-10.  Now the claim is
        unsolved for a cause the other unplaced sentence does not name -- the
        record did not REACH the file, rather than reaching it and
        disagreeing -- and the receipt says which, and what closes it (the
        span that is missing).
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(
                build.chained(
                    "100.00",
                    [(date(2026, 3, 10), "-4.00", "POINT OF SALE DEBIT L340 X")],
                    with_running=False,
                ),
                balance_as_of="03/10/2026", stated_balance="1571.00",
            ),
            filename="after-a-gap.csv",
        )
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert (
            "It states 1571.00 as of 2026-03-10, but nothing already recorded "
            "for this account reaches back to the day before 2026-03-10"
            in message
        )
        assert "Importing the span that is missing" in message
        assert "no balance was recorded from it" in message
        assert "which no day it covers reconciles" not in message
        assert category == "warning"
        # ...and it placed nothing: the first file's level stands alone.
        [(level, _release)] = bank_levels(seed_user["account"].id)
        assert level.observed_on == date(2026, 3, 3)

    def test_a_file_stating_NO_balance_says_there_was_none_to_check(
        self, auth_client, db, seed_user,
    ):
        """The fourth receipt arm, which had no test at all."""
        payload = build.build(build.chained("100.00", _ENTRIES))
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )

        response = _upload(auth_client, seed_user["account"].id, without)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "It states no balance, so there was none to check." in message
        assert category == "warning"

    def test_the_receipt_names_BOTH_days_when_they_differ(
        self, auth_client, db, seed_user,
    ):
        """The gap is the number the owner has to judge.

        An ordinary export's header sits a day past its last line; the
        developer's date-range one sat **145 days** past it and `$255.41` out.
        The days are asserted rather than the sentence's prefix, because
        swapping them left the old assertion green.
        """
        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", _ENTRIES, with_running=False),
                        balance_as_of="03/09/2026"),
        )
        _, message = _flash_toasts(response.get_data(as_text=True))[0]

        # Placed at its last line (03-03); the file states it as of 03-09.
        assert "placed at 2026-03-03" in message
        assert "states it as of 2026-03-09" in message

    def test_the_receipt_says_how_many_PLACEMENTS_the_import_released(
        self, auth_client, db, seed_user, caplog,
    ):
        """Plan step ``bank_import:X-gr``, finding **BI-488**.

        A second export inserting a line into a day the first import's
        placement had already priced means that placement was worked out
        without it, so the door releases it -- and until this step the receipt
        said nothing, leaving the owner to learn that an import had taken a
        checked balance away from an earlier one from the imports table's
        badge, if they looked.  The delete receipt has said it since
        ``aa31bedf``; this is the import receipt's own sentence, with the
        event carrying the same count -- BOTH imports' events are read, so
        the first's zero is graded as a zero rather than as an absence.
        """
        with caplog.at_level(
            logging.INFO, logger="app.routes.accounts.statements",
        ):
            _upload(auth_client, seed_user["account"].id, _payload())
            first = db.session.query(StatementImport).one()
            assert _placed_on(
                seed_user["account"].id, first.id,
            ) == date(2026, 3, 3)
            response = _upload(
                auth_client, seed_user["account"].id,
                build.build(build.chained("100.00", [
                    _ENTRIES[0],
                    (date(2026, 3, 2), "-5.00", "POINT OF SALE DEBIT L340 X"),
                    _ENTRIES[1],
                ])),
                filename="inserted.csv",
            )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "1 other import(s) had placed a stated balance at or after the "
            "earliest day this file adds a line on, so those placements were "
            "released" in message
            for _, message in toasts
        )
        db.session.expire_all()
        assert _placed_on(seed_user["account"].id, first.id) is None
        imported = [
            record for record in caplog.records
            if getattr(record, "event", None) == EVT_STATEMENT_IMPORTED
        ]
        assert [record.anchors_released for record in imported] == [0, 1]

    def test_the_receipt_says_NOTHING_about_placements_when_none_went(
        self, auth_client, db, seed_user,
    ):
        """The clause is conditional: a first import undercuts nothing.

        Paired with the case above for the reason the delete receipt's pairs
        are: rendered unconditionally the receipt would read "0 other
        import(s) had placed" on every import.
        """
        response = _upload(auth_client, seed_user["account"].id, _payload())

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert toasts, "the import should still have flashed its receipt"
        assert not any(
            "had placed a stated balance" in message for _, message in toasts
        )

    def test_the_imports_table_shows_what_the_bank_said(
        self, auth_client, db, seed_user,
    ):
        """The RECORD, where the receipt is transient.

        ``_reads.ImportedBalance``'s own docstring argues this table matters
        because an anchor the import only assumed has to stay readable after
        the flash is gone -- and the whole column had no test, so forcing
        ``_imported_balance`` to return ``None`` left 233 tests passing.
        """
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", _ENTRIES, with_running=False)),
        )

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "Bank said" in body
        assert "uncorroborated" in body
        assert "as of 2026-03-03" in body

    def test_NO_surface_prescribes_an_export_option_the_bank_dropped(
        self, auth_client, db, seed_user,
    ):
        """Plan step X-gc: the screens stop telling the owner to do the impossible.

        Every surface that spoke about an unconfirmed balance told the owner to
        "export once with your bank's running-balance option ticked".  SECU
        stopped publishing that column between the developer's 2026-07-19 and
        2026-08-16 pulls: every pull from 2026-08-16 onward carries NO balance
        column, its header being ``Date, Account, Account Number, Account
        Type, Description, Check #, Category, Memo, Credit, Debit``.  So the
        instruction names an act he cannot perform with any export he can take
        today.

        **The receipt AND the imports table are both read**, because they are
        two renders of one map and a first version of this file's assertions
        passed against a receipt that said nothing at all (see
        :func:`_flash_toasts`).  The whole page body covers the table's
        ``title`` attribute; the toast covers the receipt.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))
        response = _upload(auth_client, seed_user["account"].id, payload)
        _, receipt = _flash_toasts(response.get_data(as_text=True))[0]

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        for surface in (receipt, page):
            assert "running-balance option" not in surface
            assert "option ticked" not in surface
        # It still says the figure is unconfirmed, and now says what the level
        # MEANS rather than naming an act that cannot be performed.
        assert "Nothing has confirmed its stated balance" in receipt
        # Asserted on a clause carrying NO apostrophe: the flash is rendered
        # into HTML, so `file's` reaches the page as `file&#39;s` and a raw
        # assertion naming it would fail for a reason that is not the subject.
        assert (
            "It is checked against what this account has already recorded"
            in receipt
        )

    def test_the_UNCORROBORATED_badge_is_not_coloured_as_a_warning(
        self, auth_client, db, seed_user,
    ):
        """Plan step X-gc: the permanent state stops being dressed as an alarm.

        The badge is looked up by enum member in ``EVIDENCE_COPY``, so this
        reads the rendered markup rather than the map -- the map's own
        ``badge`` value could be changed with the template still hard-coding a
        class, which is exactly the drift the map exists to prevent.

        **``text-bg-warning`` is asserted absent NEXT TO THE LABEL, not
        page-wide**: this page has other legitimate amber (a "not placed"
        badge, the delete control), so a body-wide assertion would be graded by
        whichever of them happened to render.
        """
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained(
                "100.00", _ENTRIES, with_running=False,
            )),
        )

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        badge = re.search(
            r'<span class="badge ([\w-]+)"[^>]*>uncorroborated</span>', page,
        )
        assert badge is not None, "the imports table renders no evidence badge"
        assert badge.group(1) == "text-bg-secondary"

    def test_the_RELEASED_badge_names_the_import_that_released_it(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``balance:X-bj-1``, finding **BAL-485**.

        The badge rendered one sentence for a figure never placed AND for a
        placement the app itself released, because the release was an UPDATE
        that stored nothing (``bank_import:X-gr`` wrote it for both causes,
        ruling **R-BI6**, and named the cause as this step's).  A release is
        a row now, so the badge says which import's line withdrew the
        placement, on what day, and when.  The world is the RELEASED case --
        the second import records a line beneath the first's placement -- and
        the title is read off the badge itself.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        first = db.session.query(StatementImport).one()
        assert _placed_on(seed_user["account"].id, first.id) == date(2026, 3, 3)
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", [
                _ENTRIES[0],
                (date(2026, 3, 2), "-5.00", "POINT OF SALE DEBIT L340 X"),
                _ENTRIES[1],
            ])),
            filename="inserted.csv",
        )
        db.session.expire_all()
        assert _placed_on(seed_user["account"].id, first.id) is None

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        badge = re.search(
            r'<span class="badge text-bg-warning"\s+title="([^"]*)">released</span>',
            page,
        )
        assert badge is not None, "the released placement renders no badge"
        title = badge.group(1)
        assert title.startswith(
            "This figure had been placed on 2026-03-03 and was released on ",
        )
        assert (
            'the import of &#34;inserted.csv&#34; recorded a line on 2026-03-02'
            in title
            or 'the import of &quot;inserted.csv&quot; recorded a line on '
            '2026-03-02' in title
        )
        assert "since been deleted" not in title
        assert ">not placed<" not in page

    def test_the_RELEASED_badge_says_when_the_cause_has_been_deleted(
        self, auth_client, db, seed_user,
    ):
        """The other event that leaves a placement withdrawn: the cause is gone.

        Delete the releasing import and the release keeps the day whose lines
        changed while the key's ``SET NULL`` takes the import off it; the
        badge says that rather than guessing a file name.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        first = db.session.query(StatementImport).one()
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", [
                _ENTRIES[0],
                (date(2026, 3, 2), "-5.00", "POINT OF SALE DEBIT L340 X"),
                _ENTRIES[1],
            ])),
            filename="inserted.csv",
        )
        inserted = db.session.query(StatementImport).filter(
            StatementImport.id != first.id,
        ).one()
        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}/statements/delete",
            data={"import_id": str(inserted.id)},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert db.session.get(StatementImport, inserted.id) is None

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        badge = re.search(
            r'<span class="badge text-bg-warning"\s+title="([^"]*)">released</span>',
            page,
        )
        assert badge is not None
        assert (
            "the recorded lines from 2026-03-02 changed and the import "
            "responsible has since been deleted" in badge.group(1)
        )
        assert "inserted.csv" not in badge.group(1)

    def test_the_NOT_PLACED_badge_names_both_causes(
        self, auth_client, db, seed_user,
    ):
        """A figure never placed, and the badge names the two ways.

        Once a release is told apart, TWO states reach this branch since
        ruling **bank_import:R-BI35**: the record reached the file and no day
        reconciled it (this case: the date-range export shape, whose header
        states a figure its own lines cannot reach), or nothing recorded
        reached back to the file's first day (a file after a coverage gap
        while a level stands,
        ``test_a_file_after_a_GAP_says_the_record_did_not_reach_it``).  The
        row cannot tell them apart -- the cause is known at the resolve
        alone, and the receipt says it -- so the badge names both.  *It said
        "its one cause" until the ruling, whose arm created the second.*
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        # ADJACENT to the first file's last day, so the recorded opening is
        # known (the first import's placement reaches it) and the header's
        # figure fails to reconcile on any day.
        _upload(
            auth_client, seed_user["account"].id,
            build.build(
                build.chained("1534.19", [
                    (date(2026, 3, 4), "-10.00", "POINT OF SALE DEBIT L340 X"),
                ], with_running=False),
                balance_as_of="08/16/2026", stated_balance="2501.31",
            ),
            filename="range.csv",
        )

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        badge = re.search(
            r'<span class="badge text-bg-warning"\s+title="([^"]*)">not placed</span>',
            page,
        )
        assert badge is not None, "the unplaced figure renders no badge"
        assert badge.group(1).replace("&#39;", "'") == (
            "No day holds this figure: either no day this file covers "
            "reconciled it with what was already recorded before the file's "
            "first day, or nothing already recorded reached back that far to "
            "check it. A later import can place a balance again."
        )
        assert "released" not in badge.group(1)

    def test_the_source_select_names_the_FORMAT_not_a_column_it_lacks(
        self, auth_client, seed_user,
    ):
        """Plan step X-gc: the label stops contradicting the help text under it.

        ``ref.statement_sources.display_name`` is the text of the one control
        that chooses a parser, and it read "SECU checking -- CSV with running
        balance" while the form text directly beneath it has said the column is
        optional since plan step ``bank_import:X-f6e-1``.  Migration
        ``a1f4c7e0b839`` re-labels the row an existing database carries;
        ``app.ref_seeds`` is what a new one is born with, and the test database
        is built by the migration chain, so this grades the migration.
        """
        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        select = page[page.index('id="source"'):page.index("</select>")]
        assert "SECU checking -- CSV export" in select
        assert "with running balance" not in select

    def test_the_cash_detail_page_offers_the_statements_link(
        self, auth_client, seed_user,
    ):
        """The only entry point, so its absence would strand the whole leaf."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/details"
        )

        assert response.status_code == 200
        assert (
            f"/accounts/{seed_user['account'].id}/statements".encode()
            in response.data
        )
