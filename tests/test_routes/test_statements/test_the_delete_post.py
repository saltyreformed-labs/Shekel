"""The delete door, and no link on the page reaching the retiring queue."""

from datetime import date, timedelta
from decimal import Decimal
import logging
import re

from app.models.merchant import Merchant
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import auth_service, entry_service, statement_match
from app.utils.log_events import EVT_STATEMENT_IMPORT_DELETED
from tests._test_helpers import create_settled_cash_transaction
from tests.test_services.test_statement_import import _csv_builder as build
from tests.test_services.test_statement_match._builders import (
    a_rule,
    a_submission,
    an_envelope,
    an_unexplained_outflow,
    filed_by,
)

from ._helpers import (
    _placed_on,
    _payload,
    _flash_toasts,
    _delete_form,
    _delete_form_for,
    _a_loan_account,
    _upload,
    _record_in_a_new_envelope,
)


class TestTheDeletePost:
    """The UNDO door, end to end through HTTP (plan step X-f6a-4).

    The route's own subjects, none of which the service test can see: OWNERSHIP
    on a DESTRUCTIVE act, the validated id, the unit of work, and the flash
    that reports what was actually removed.
    """

    def _delete(self, client, account_id, import_id, **extra):
        """POST the delete the way the form does."""
        return client.post(
            f"/accounts/{account_id}/statements/delete",
            data={"import_id": str(import_id), **extra},
            follow_redirects=True,
        )

    def test_it_deletes_the_import_and_reports_what_went(
        self, auth_client, db, seed_user,
    ):
        """The happy path, with the counts in the flash."""
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Deleted the import of" in body
        assert "2 bank line(s)" in body
        assert db.session.query(StatementImport).count() == 0
        assert db.session.query(BankStatementLine).count() == 0

    def test_the_flash_says_how_many_SKIPS_went_with_the_lines(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-JG**.

        A skip goes with its line rather than refusing the delete, so what the
        owner loses is answers they already gave -- and re-importing the same
        span asks about those lines again.  **The service counting it is not
        enough**: ``ImportRemoval.anchors_released`` was computed, tested at
        the service tier and rendered by NO surface from 2026-08-23 until plan
        step ``bank_import:X-gi-4`` (finding **N-470**), while its own
        docstring said it was "reported rather than silent" -- exactly the
        state this case exists to keep the skip count out of, and the four
        cases below it now keep the other two figures out of as well.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()
        line = db.session.query(BankStatementLine).order_by(
            BankStatementLine.id,
        ).first()
        statement_match.skip_line(
            line.id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        # **Read out of the TOAST, not out of the page** (``_flash_toasts``).
        # The imports table renders each evidence sentence in a ``title``
        # attribute, so a bare ``in body`` has passed here against a receipt
        # that said nothing at all -- and the 2026-08-23 review measured that
        # flipping every category to ``success`` left all 32 cases in this file
        # green.  Named by adversarial test-quality review 2026-09-02.
        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "1 line(s) you had skipped went with them" in message
            for _, message in toasts
        )
        assert [category for category, _ in toasts] == ["info"]

    def test_the_flash_says_NOTHING_about_skips_when_none_went(
        self, auth_client, db, seed_user,
    ):
        """The clause is conditional, so an ordinary delete does not mention it.

        Paired with the case above deliberately: a sentence rendered
        unconditionally would read "0 line(s) you had skipped went with them"
        on every delete, which is the noise every other clause on this receipt
        is written to avoid.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert toasts, "the delete should still have flashed its receipt"
        assert not any("you had skipped" in message for _, message in toasts)

    def test_the_flash_says_how_many_PLACEMENTS_it_released(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gi-4``, finding **N-470**.

        A later import placed its stated balance on a day whose evidence
        included the lines this delete removes, so that placement is released
        with them (``release_anchors_from``) and the account holds no checked
        balance from it.  ``ImportRemoval.anchors_released`` said of itself
        that it was "reported rather than silent" while no surface read it;
        this case is the surface.  The count is of OTHER imports: the deleted
        import's own placement goes with its row and is not among them, which
        is why one import alone (the case below) says nothing.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        first = db.session.query(StatementImport).one()
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("1575.00", [
                (date(2026, 3, 5), "-10.00", "POINT OF SALE DEBIT L340 X"),
            ])),
            filename="later.csv",
        )
        later = db.session.query(StatementImport).filter(
            StatementImport.id != first.id,
        ).one()
        assert _placed_on(seed_user["account"].id, later.id) == date(2026, 3, 5)

        response = self._delete(
            auth_client, seed_user["account"].id, first.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "1 other import(s) had placed a stated balance on these lines"
            in message
            for _, message in toasts
        )
        assert [category for category, _ in toasts] == ["info"]
        db.session.expire_all()
        assert _placed_on(seed_user["account"].id, later.id) is None

    def test_the_flash_says_NOTHING_about_placements_when_none_went(
        self, auth_client, db, seed_user,
    ):
        """The clause is conditional: one import's delete releases no other.

        Paired with the case above for the reason the skip pair is: rendered
        unconditionally the receipt would read "0 other import(s) had placed"
        on every delete.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert toasts, "the delete should still have flashed its receipt"
        assert not any(
            "had placed a stated balance" in message for _, message in toasts
        )

    def test_the_flash_says_how_many_MERCHANTS_it_forgot(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gi-4``, finding **N-470**.

        A merchant no surviving line names and no standing rule answers for
        is swept with the import (plan step ``bank_import:X-gd-1``), and the
        receipt says how many.  **One of the two is ANSWERED here**, so the
        sentence's figure is the sweep's own and not a count of the merchants
        the file named: a receipt reading the latter would say 2.
        """
        _upload(auth_client, seed_user["account"].id, _payload([
            (date(2026, 3, 2), "-25.00",
             "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
            (date(2026, 3, 4), "-40.81",
             "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
        ]))
        recorded = db.session.query(StatementImport).one()
        assert db.session.query(Merchant).count() == 2
        a_rule(seed_user, "Food Lion")
        db.session.commit()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "1 merchant(s) were forgotten: nothing else named them and you "
            "had stated no rule for them" in message
            for _, message in toasts
        )
        assert [category for category, _ in toasts] == ["info"]
        assert [row.name for row in db.session.query(Merchant).all()] == [
            "Food Lion",
        ]

    def test_the_flash_says_NOTHING_about_merchants_when_none_went(
        self, auth_client, db, seed_user,
    ):
        """The clause is conditional: a file naming no merchant forgets none.

        ``_payload``'s default lines carry no parenthesised merchant, which is
        how SECU names one, so the import records no merchant row and the
        delete has none to sweep -- asserted rather than assumed, because a
        fixture that quietly did name one would make this case pass for the
        wrong reason.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()
        assert db.session.query(Merchant).count() == 0

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert toasts, "the delete should still have flashed its receipt"
        assert not any(
            "were forgotten" in message for _, message in toasts
        )

    def test_the_EVENT_carries_the_two_figures_the_receipt_gained(
        self, auth_client, db, seed_user, caplog,
    ):
        """The forensic record names both, and with the act's own values.

        **N-470** measured ``anchors_released`` and ``merchants_forgotten``
        absent from the door's ``log_event`` kwargs as well as from the flash,
        and the event is the record of the one act in this package that
        DESTROYS.  The world is the two-import one above -- one placement
        released, no merchant named -- so both values are graded, and a zero
        is graded as a zero rather than as an absence.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        first = db.session.query(StatementImport).one()
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("1575.00", [
                (date(2026, 3, 5), "-10.00", "POINT OF SALE DEBIT L340 X"),
            ])),
            filename="later.csv",
        )

        with caplog.at_level(
            logging.INFO, logger="app.routes.accounts.statements",
        ):
            self._delete(auth_client, seed_user["account"].id, first.id)

        deleted = [
            record for record in caplog.records
            if getattr(record, "event", None) == EVT_STATEMENT_IMPORT_DELETED
        ]
        assert len(deleted) == 1
        assert deleted[0].anchors_released == 1
        assert deleted[0].merchants_forgotten == 0

    def test_the_confirmation_previews_the_PLACEMENTS_the_delete_releases(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gr``, finding **BI-490**.

        The developer ruled 2026-09-11 that the confirmation previews the two
        figures the receipt reports.  Same world as the receipt case above:
        deleting the FIRST import releases the later one's placement, so the
        first's confirmation says so and the later's -- whose delete releases
        nothing -- does not.  **Then the delete runs and the receipt is
        graded against the number the confirmation promised**, which is what
        *from the one read the act counts with* means in a browser.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        first = db.session.query(StatementImport).one()
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("1575.00", [
                (date(2026, 3, 5), "-10.00", "POINT OF SALE DEBIT L340 X"),
            ])),
            filename="later.csv",
        )
        later = db.session.query(StatementImport).filter(
            StatementImport.id != first.id,
        ).one()

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        promised = (
            "It also RELEASES the checked balance 1 other import(s) had placed "
            "on these lines"
        )
        assert promised in _delete_form_for(body, first.id)
        assert "RELEASES the checked balance" not in _delete_form_for(
            body, later.id,
        )

        response = self._delete(
            auth_client, seed_user["account"].id, first.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "1 other import(s) had placed a stated balance on these lines"
            in message
            for _, message in toasts
        )

    def test_the_confirmation_previews_the_MERCHANTS_the_delete_forgets(
        self, auth_client, db, seed_user,
    ):
        """The other figure, same ruling, same world as the receipt's case.

        Two merchants named, one answered for: the confirmation says ONE, and
        the receipt afterwards says the same one.  A confirmation counting the
        merchants the file named would say two.
        """
        _upload(auth_client, seed_user["account"].id, _payload([
            (date(2026, 3, 2), "-25.00",
             "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
            (date(2026, 3, 4), "-40.81",
             "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
        ]))
        recorded = db.session.query(StatementImport).one()
        a_rule(seed_user, "Food Lion")
        db.session.commit()

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert (
            "It also forgets 1 merchant(s) nothing else names and you have "
            "stated no rule for" in _delete_form_for(body, recorded.id)
        )

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "1 merchant(s) were forgotten" in message for _, message in toasts
        )

    def test_the_confirmation_says_NOTHING_about_either_when_neither_would_go(
        self, auth_client, db, seed_user,
    ):
        """Both clauses are conditional, paired with the two cases above.

        One import naming no merchant: its delete releases no other
        placement and forgets no merchant, so neither sentence is on its
        confirmation -- rendered unconditionally each would promise a zero.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()
        assert db.session.query(Merchant).count() == 0

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        form = _delete_form_for(body, recorded.id)
        assert "This removes 2 bank line(s)" in form
        assert "RELEASES the checked balance" not in form
        assert "merchant(s) nothing else names" not in form

    def test_a_re_imports_confirmation_removes_NOTHING(
        self, auth_client, db, seed_user,
    ):
        """Two imports of one file: each confirmation says 0 lines go.

        Plan step ``bank_import:X-f6b-1``, ruling **R-BI10**: both imports
        sighted both lines, so neither holds one alone and deleting either
        removes none.  The FIRST import's ``recorded_count`` is still 2 --
        which is what its confirmation printed before this step, and what
        the delete would not have done.  Found by adversarial review
        2026-09-18.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        _upload(
            auth_client, seed_user["account"].id, _payload(),
            filename="again.csv",
        )
        first, again = (
            db.session.query(StatementImport).order_by(StatementImport.id).all()
        )

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "This removes 0 bank line(s)" in _delete_form_for(body, first.id)
        assert "This removes 0 bank line(s)" in _delete_form_for(body, again.id)
        assert "This removes 2 bank line(s)" not in body

    def test_the_page_offers_the_control_with_what_it_would_remove(
        self, auth_client, db, seed_user,
    ):
        """The confirmation names what the delete would REMOVE.

        ``recorded_count`` is how many lines the act was the first to sight
        and ``removes.lines`` is how many it alone holds today; putting the
        first on a destructive control would state a figure the delete does
        not perform -- on the one sentence the owner reads before pressing
        delete.  One import, so the two agree here; the two-import case is
        ``test_a_re_imports_confirmation_removes_NOTHING``.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        body = response.get_data(as_text=True)
        assert "/statements/delete" in body
        assert "This removes 2 bank line(s)" in body
        # **The CSRF token is asserted INSIDE THE DELETE FORM**, not merely
        # somewhere on the page.  TestConfig sets `WTF_CSRF_ENABLED = False`, so
        # a form that lost its token passes every functional test here while
        # 400ing in production -- which is why the project asserts the markup
        # (`test_loan/`, `test_security_event_banner.py`).  A first version of
        # this assertion looked for the token anywhere in the body and PASSED
        # with the delete form's token deleted, because the upload form on the
        # same page carries one; measured by planting it 2026-08-20.
        assert 'name="csrf_token"' in _delete_form(body)

    def test_it_says_how_many_matches_it_would_UNDO(
        self, auth_client, db, seed_user,
    ):
        """The one LIVE count on the confirmation, and its arm.

        A match can be released independently of its import, so this number is
        genuinely current where the line count is the act's own history.  The
        `{% if row.matches_affected %}` arm had no test at all until adversarial
        review said so -- it is the sentence that warns an owner they are about
        to undo work they accepted.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        line = db.session.query(BankStatementLine).first()
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_user["bootstrap_period"],
            Decimal("25.00"), settled_on=line.posted_on, name="Coffee",
        )
        db.session.flush()
        scope = statement_match.ReviewScope.build(
            seed_user["user"].id, seed_user["account"].id,
        )
        statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]),
            scope,
        )
        db.session.commit()

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "undoes 1 accepted match(es)" in body

    def test_it_DESTROYS_what_was_created_from_those_lines_and_says_so(
        self, auth_client, db, seed_user,
    ):
        """Plan step **bank_import:X-f6f**, ruling **R-GG**, amending R-GB.

        A row created from one of these lines exists only because that line
        did, so destroying the line while keeping it leaves a movement in
        the books that nothing accounts for.  It goes with the line -- which
        means this act MOVES MONEY where R-GB said it could not, and both
        the confirmation and the receipt have to say so.

        **The wording stopped saying *the review* created it at plan step
        ``bank_import:X-ge``**, and that is a correction rather than a
        rewording: a row destroyed here may have been filed by a standing
        RULE at import, which nobody reviewed.  What the sentence must be
        right about is the count and the figure, and both are unchanged.

        Driven through the two real POSTs: import, record the `-$25.00` coffee
        line as a purchase in a new envelope, then delete the import.
        """
        # **Dated INSIDE the owner's own calendar**, unlike the module's
        # default fixture: recording a line as a purchase resolves the pay
        # period it is BUDGETED in, so a line outside every saved period is
        # never offered as creatable and this case would silently test nothing.
        inside = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        _upload(auth_client, seed_user["account"].id, _payload(entries=[
            (inside, "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
            (inside + timedelta(days=1), "1500.00",
             "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
        ]))
        line = db.session.query(BankStatementLine).filter(
            BankStatementLine.amount < 0,
        ).one()
        _record_in_a_new_envelope(
            auth_client, seed_user["account"].id, line,
            seed_user["categories"]["Groceries"].id,
        )
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 1, (
            "the recording must really have happened, or the delete below "
            "proves nothing"
        )

        # The CONFIRMATION says it first, on the page that offers the button.
        listing = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)
        assert "DESTROYS 2 row(s) created from those lines" in listing
        assert "-$25.00" in listing

        recorded = db.session.query(StatementImport).one()
        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        body = response.get_data(as_text=True)
        assert "2 row(s) created from those " in body
        assert "-25.00" in body
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 0
        assert db.session.query(Transaction).filter(
            Transaction.name == "Coffee",
        ).count() == 0

    def test_a_BLOCKED_delete_says_so_instead_of_naming_a_destruction(
        self, auth_client, db, seed_user,
    ):
        """Found by adversarial security review 2026-08-24, in one edit.

        One refusing release takes the WHOLE import delete down
        (``_release_matches``), so an import holding a created row the owner
        has since edited cannot be deleted at all.  A first version of this
        page went on printing *"DESTROYS 2 row(s) ... worth -$25.00"* over a
        press that destroys nothing and cannot succeed -- a money figure
        attached to a no-op, and an import the owner cannot delete until they
        find the row by hand, which is finding **N-302**'s shape with a
        confirmation that lies on top of it.
        """
        inside = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        _upload(auth_client, seed_user["account"].id, _payload(entries=[
            (inside, "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
        ]))
        line = db.session.query(BankStatementLine).one()
        _record_in_a_new_envelope(
            auth_client, seed_user["account"].id, line,
            seed_user["categories"]["Groceries"].id,
        )
        db.session.expire_all()
        entry_service.update_entry(
            db.session.query(TransactionEntry).one().id,
            seed_user["user"].id, description="Coffee -- the good beans",
        )
        db.session.commit()

        listing = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "This delete is REFUSED right now" in listing
        assert "you have edited that row since" in listing
        assert "DESTROYS" not in listing
        # ...and the press really is refused, so the page was not being
        # pessimistic about an act that would have worked.
        recorded = db.session.query(StatementImport).one()
        body = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        ).get_data(as_text=True)
        assert "you have edited that row since" in body
        db.session.expire_all()
        assert db.session.query(StatementImport).count() == 1

    def test_an_import_whose_review_created_NOTHING_says_nothing(
        self, auth_client, db, seed_user,
    ):
        """The control: no warning where the delete destroys no record.

        A dialog that always threatened destruction would train the owner to
        click through the one that means it.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        listing = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)
        recorded = db.session.query(StatementImport).one()
        body = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        ).get_data(as_text=True)

        assert "DESTROYS" not in listing
        assert "the review had created" not in body

    def test_the_flash_names_the_matches_and_the_pairing_it_removed(
        self, auth_client, db, seed_user,
    ):
        """Both conditional arms of the report, which had no test.

        A destructive act whose report is one word leaves the owner unable to
        tell a no-op from a much larger removal -- and this one reaches past
        the import itself, releasing matches and clearing the bank pairing.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        body = response.get_data(as_text=True)
        assert "This was the last import for this account from that source" in body
        assert "the app no longer records which bank account it is" in body

    def test_the_KIND_gate_answers_404_for_a_loan(
        self, auth_client, db, seed_user,
    ):
        """A loan has no bank statement, so it has no import to delete.

        The gate is `load_cash_account_or_404`, and this project has already
        paid twice for a sibling route being written without it -- the GET and
        the import POST both assert it, and the door that DESTROYS was added
        without joining them.  Adversarial security review 2026-08-20.
        """
        loan = _a_loan_account(db, seed_user)

        response = auth_client.post(
            f"/accounts/{loan.id}/statements/delete",
            data={"import_id": "1"},
        )

        assert response.status_code == 404

    def test_ANOTHER_users_import_answers_404(
        self, auth_client, db, seed_user,
    ):
        """FIRING CONTROL against an IDOR on a destructive act.

        The delete is decorated independently of the read and the import, so
        neither of those being right proves anything about this one -- and
        this is the door that DESTROYS.
        """
        stranger = User(
            email="stranger2@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger Two",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        from app.models.account import Account

        theirs = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking Two",
        )
        db.session.add(theirs)
        db.session.flush()
        _upload(auth_client, seed_user["account"].id, _payload())
        mine = db.session.query(StatementImport).one()

        response = auth_client.post(
            f"/accounts/{theirs.id}/statements/delete",
            data={"import_id": str(mine.id)},
        )

        assert response.status_code == 404
        assert db.session.query(BankStatementLine).count() == 2

    def test_an_import_on_ANOTHER_of_the_owners_accounts_is_refused(
        self, auth_client, db, seed_user,
    ):
        """Ownership of the ACCOUNT is not ownership of the IMPORT.

        The decorator proves the caller owns the account in the URL; only the
        service can say the import belongs to THAT account.  Without that arm a
        single owner could delete one account's statements through another
        account's door.
        """
        from app.models.ref import AccountType
        from app.services import account_service
        from decimal import Decimal

        checking_type = (
            db.session.query(AccountType).filter_by(name="Checking").one()
        )
        second = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Second Checking",
                anchor_balance=Decimal("0.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
        )
        db.session.flush()
        _upload(auth_client, seed_user["account"].id, _payload())
        mine = db.session.query(StatementImport).one()

        response = self._delete(auth_client, second.id, mine.id)

        assert "no longer there" in response.get_data(as_text=True)
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_missing_id_is_refused_by_the_schema(
        self, auth_client, db, seed_user,
    ):
        """The validated-input discipline, not a bare ``request.form.get``."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}/statements/delete",
            data={},
            follow_redirects=True,
        )

        assert "Which import do you want to delete?" in response.get_data(
            as_text=True,
        )
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_NON_ROW_id_is_refused_rather_than_coerced(
        self, auth_client, db, seed_user,
    ):
        """``RowId``, not ``fields.Integer`` (finding N-141).

        ``Integer`` reads ``' 12 '``, ``'+12'``, ``'007'`` and ``'0'`` as ids,
        two of which name no row at all -- on a door that destroys.  The
        padded id is the REAL one, so this case cannot pass by naming a row
        that does not exist.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        recorded = db.session.query(StatementImport).one()
        response = self._delete(
            auth_client, seed_user["account"].id, f" {recorded.id} ",
        )

        assert db.session.query(BankStatementLine).count() == 2
        assert "Deleted the import of" not in response.get_data(as_text=True)

    def test_a_refused_delete_leaves_the_pairing_alone(
        self, auth_client, db, seed_user,
    ):
        """The unit of work is the request, asserted through a real one."""
        from app.models.statement_import import AccountExternalIdentity

        _upload(auth_client, seed_user["account"].id, _payload())

        self._delete(auth_client, seed_user["account"].id, 999999)

        assert db.session.query(AccountExternalIdentity).count() == 1
        assert db.session.query(BankStatementLine).count() == 2


class TestNoLinkHereReachesTheRetiringQueue:
    """Plan step ``bank_import:X-gi-1``.

    Three links on this page opened ``accounts.review_statements``, and plan
    step ``bank_import:X-gi-2`` deletes that endpoint.  **The URL is what is
    asserted and not the link TEXT**, because a repoint that left one
    ``url_for`` behind would raise ``BuildError`` at render time on the day
    that step lands -- a 500 on the import screen, found by a person rather
    than by this suite.

    **Asserted over a page with acts staged on it**, because two of the three
    links are inside sections that render only where the account has something
    to show: an absence assertion over a bare page is satisfied by rendering
    neither the link nor the section it lives in, which is why the first case
    here grades the staging.
    """

    def _a_page_with_the_filed_section_rendered(
        self, auth_client, db, seed_user,
    ):
        """Return the statements page with its rule-filed section populated.

        Returns:
            The rendered page, as text.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user, merchant="Amazon")
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=True)
        db.session.commit()

        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_the_rule_filed_section_really_rendered(
        self, auth_client, db, seed_user,
    ):
        """The staging the absence assertions below are quantified over."""
        page = self._a_page_with_the_filed_section_rendered(
            auth_client, db, seed_user,
        )

        assert "Undoing one removes the" in page, (
            "the rule-filed footer did not render, so this class's absence "
            "assertions would be quantified over a page without it"
        )

    def test_no_url_on_the_page_names_the_review_queue(
        self, auth_client, db, seed_user,
    ):
        """The repoint, read off the URLs the page actually emits."""
        page = self._a_page_with_the_filed_section_rendered(
            auth_client, db, seed_user,
        )
        queue = f"/accounts/{seed_user['account'].id}/statements/review"

        assert f'href="{queue}"' not in page, (
            "a link on the statements page still opens the review queue, "
            "which bank_import:X-gi-2 deletes"
        )

    def test_the_three_of_them_lead_to_the_reconcile_page(
        self, auth_client, db, seed_user,
    ):
        """Where they go now, and how many there are.

        The header's *Review matches* button was DELETED rather than
        repointed: the queue's job is this page's inbox tab, which the primary
        button beside it already opened, so a repoint would have put two
        controls on one header leading to one URL.  The banner's *waits on ...
        for you to accept it* and the rule-filed footer's *puts the bank line
        back among the unexplained on ...* are the two that were repointed.
        **A COUNT and not three presence checks**, because a presence check
        passes just as well against the duplicate button the deletion exists
        to prevent.
        """
        page = self._a_page_with_the_filed_section_rendered(
            auth_client, db, seed_user,
        )
        reconcile = f"/accounts/{seed_user['account'].id}/statements/reconcile"
        bare = page.count(f'href="{reconcile}"')

        assert bare == 3, (
            f"expected the Reconcile button and the two repointed sentences "
            f"pointing at the untabbed page; found {bare}"
        )
        assert re.search(
            r"Reconcile\s+page</a> for you to accept it", page,
        ), "the import banner's sentence does not lead to Reconcile"
        assert re.search(
            r"Reconcile\s+page</a>; the Undo itself leaves you here", page,
        ), "the rule-filed footer's sentence does not lead to Reconcile"
