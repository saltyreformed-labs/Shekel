"""The statements page and its import door: the page reads for its owner, another user's account is
refused, and the import POST.
"""

from datetime import date
from io import BytesIO

import pytest

from app.models.statement_import import BankStatementLine, StatementImport
from app.models.statement_match import StatementMatch as statement_match_model
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_services.test_statement_import import _csv_builder as build
from tests.test_services.test_statement_match._builders import a_rule, a_transaction

from ._helpers import _ENTRIES, _payload, _flash_toasts, _upload


class TestThePageReadsForItsOwner:
    """The GET."""

    def test_it_renders_for_the_owner(self, auth_client, seed_user):
        """The ordinary case."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert response.status_code == 200
        assert b"Import a statement" in response.data

    def test_it_says_which_of_the_two_things_it_does(
        self, auth_client, seed_user,
    ):
        """The page's own claim about itself, asserted rather than assumed.

        An import screen that looked like it reconciled would be READ as having
        reconciled, so the sentence saying so has always been part of the
        deliverable.  **Plan step ``bank_import:X-ge`` made HALF of it false**:
        recording still moves no figure, and a standing rule now files a new
        swipe as a purchase in the same request (ruling **R-GH**), so a page
        claiming only *it changes no balance* would be claiming the opposite of
        what happened to the owner's money.

        **Both halves are asserted, and the second is why this case was
        rewritten rather than left alone.**  The old assertion was
        ``b"changes no balance" in response.data``, and the replacement copy
        still contains that phrase -- about the RECORDING half -- so the case
        went on passing while the sentence it was written to grade had gone.
        A test that survives the change it exists to catch is not a test.
        """
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )
        body = response.data

        # The recording half, unchanged.
        assert b"That part changes no balance" in body
        # ...and the half X-ge added, which is what MOVES money.
        assert b"files what you have already\n    decided" in body
        assert b"standing" in body and b"filed as a purchase" in body
        # ...and the protective half R-GH keeps: nothing touches a row the
        # owner made without their tick.
        assert b"is never\n    done for you" in body

    def test_an_account_with_nothing_recorded_says_so(
        self, auth_client, seed_user,
    ):
        """The empty state is an answer, not a blank."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"Nothing recorded yet" in response.data

    def test_it_offers_the_adapters_that_have_a_parser(
        self, auth_client, seed_user,
    ):
        """The form's options come from the registry, so an unusable source
        cannot be chosen."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"secu_checking_csv" in response.data

    def test_recorded_lines_are_rendered(self, auth_client, seed_user):
        """What the bank said, back on the page that recorded it."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"POINT OF SALE DEBIT L340 COFFEE" in response.data
        assert b"lines recorded" in response.data


class TestItRefusesAnotherUsersAccount:
    """Firing controls against an IDOR on a record of real bank activity."""

    @pytest.fixture()
    def other_users_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user."""
        stranger = User(
            email="stranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        from app.models.account import Account

        account = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking",
        )
        db.session.add(account)
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this fixture only flushed is
        # one the request cannot see.  The 404 these tests assert must be the
        # OWNERSHIP gate refusing a real account of someone else's rather than
        # a missing row.
        db.session.commit()
        return account.id

    def test_the_page_answers_404(self, auth_client, other_users_account):
        """404 for both "not found" and "not yours" -- the security rule.

        A 403 would confirm the account exists, which is the disclosure the
        rule exists to prevent.
        """
        response = auth_client.get(
            f"/accounts/{other_users_account}/statements"
        )

        assert response.status_code == 404

    def test_the_import_answers_404(
        self, auth_client, db, other_users_account,
    ):
        """The write door is decorated independently of the read."""
        response = auth_client.post(
            f"/accounts/{other_users_account}/statements",
            data={
                "source": "secu_checking_csv",
                "statement_file": (BytesIO(_payload()), "s.csv"),
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 404
        assert db.session.query(BankStatementLine).count() == 0


class TestTheImportPost:
    """The write door, end to end through HTTP."""

    def test_it_records_the_file_and_says_what_it_did(
        self, auth_client, db, seed_user,
    ):
        """The happy path, with the count in the flash."""
        response = _upload(auth_client, seed_user["account"].id, _payload())

        assert response.status_code == 200
        assert b"Recorded 2 new line" in response.data
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_second_identical_upload_reports_nothing_new(
        self, auth_client, db, seed_user,
    ):
        """Idempotency, as the user experiences it."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(auth_client, seed_user["account"].id, _payload())

        assert b"Nothing new" in response.data
        assert db.session.query(BankStatementLine).count() == 2

    def test_the_import_is_COMMITTED_not_merely_staged(
        self, auth_client, db, seed_user,
    ):
        """The route owns the unit of work, so the rows survive the request."""
        _upload(auth_client, seed_user["account"].id, _payload())

        db.session.expire_all()
        assert db.session.query(StatementImport).count() == 1

    def test_a_rule_covered_swipe_is_FILED_by_the_import_itself(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GH** through the real door, and it MOVES MONEY.

        The whole point of the step: the owner stated once where Food Lion
        goes, uploads an export, and the swipe is a Groceries purchase before
        they have pressed anything else.  The receipt says so, names the
        figure, and the page lists the act with its undo.

        **The second line is a payroll DEPOSIT the same file carries**, so this
        cannot pass on a door that filed everything it saw: money coming in can
        never be a purchase.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            envelope_id = envelope.id
            db.session.commit()

        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
                (date(2024, 1, 9), "1500.00",
                 "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
            ]),
        )

        assert response.status_code == 200
        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "filed 1 as purchases, -25.00 in total" in message
            for _, message in toasts
        ), toasts
        assert db.session.query(TransactionEntry).filter(
            TransactionEntry.transaction_id == envelope_id,
        ).count() == 1
        # ...and the act is on the page, with the control that takes it back.
        assert b"Filed by your rules" in response.data
        assert b"match_id" in response.data

    def test_the_receipt_SENDS_the_owner_where_the_acts_actually_are(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gf-2`` moved them; this sentence follows.

        The receipt is bounded at ``RECEIPT_LIMIT``, so it names the place
        every act IS listed -- and that used to be the review screen, which
        after the split lists none of them.  A sentence sending an owner to a
        page that cannot answer is the *chooser whose submission can never
        succeed* shape, on prose.  The three surfaces changed together and none
        of them was graded: the sentence, the button in the header, and the
        anchor that explains what a standing rule does.

        Found by adversarial test-quality review of this step, 2026-08-27.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            db.session.commit()
        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
            ]),
        )
        page = response.get_data(as_text=True)
        toasts = _flash_toasts(page)

        assert any(
            "every one of them is on the Filed by rules tab of the "
            "Reconcile screen" in message
            for _, message in toasts
        ), toasts
        joined = " ".join(message for _, message in toasts)
        assert "on the review screen" not in joined
        # AND NOT THE OTHER SETTLED TAB, which is the half that holds none of
        # these: `Explained` is the acts a PERSON ticked (**R-GT**), and every
        # act this receipt is about is one nobody pressed.
        assert "already decided" not in joined
        # The header button and the standing-rule anchor, which are how an
        # owner reaches those places without reading a flash.
        #
        # THREE LINKS, THREE DESTINATIONS, and each move was this case's own
        # reasoning applied once further.
        #
        # The ANCHOR is about the standing RULE and led to the register until
        # ``bank_import:X-gk`` (ruling **R-IC**, 2026-08-31): that page renders
        # only the merchants already answered for, and measured on a clone of
        # the developer's own database 32 of his 62 merchants were on no
        # surface at all -- so a sentence explaining what a standing rule does
        # pointed at a page that could not show, or answer for, half the
        # merchants it was explaining.
        #
        # The BUTTON is about the acts, and the CAPTION is about the acts a
        # RULE filed.  Both led to the register until ``bank_import:X-gj-1c``
        # retired it (**R-HU**), and they now lead to DIFFERENT TABS, because
        # the Reconcile screen splits accepted acts by who agreed to them
        # (**R-GT**).  The button is general and opens Explained with the whole
        # bar one click away; the caption is specifically about acts nobody
        # pressed, so it names the tab that holds all of them and none of the
        # others.  Pointing the caption at Explained would send its reader to
        # the half holding none of what it receipts, which is the same
        # page-that-cannot-answer shape this case was written for.
        account_id = seed_user["account"].id
        reconcile = f"/accounts/{account_id}/statements/reconcile"
        merchants = f"/accounts/{account_id}/statements/merchants"
        assert f'href="{reconcile}?tab=explained"' in page
        assert f'href="{reconcile}?tab=filed_by_rules"' in page
        assert f'href="{merchants}#merchant-rules"' in page
        assert f'href="/accounts/{account_id}/statements/register"' not in page

    def test_undoing_a_filed_act_LEAVES_THE_OWNER_HERE(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gf-2``: a door returns you where you were.

        This receipt lists up to twenty acts a standing rule performed, and an
        owner checking it is reading a list.  The undo posted to the review
        screen's own release door until this step and redirected THERE --
        a different page, mid-receipt, and after the split a page that lists no
        accepted match at all, so the redirect landed nowhere near the act.

        What is graded is the whole round trip through the real control: the
        receipt renders this page's own action, the press works, and the
        answer is this page carrying the undo's receipt.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            db.session.commit()
        page = _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
            ]),
        ).get_data(as_text=True)

        # The RENDERED action, so this cannot pass against a receipt still
        # posting to the review screen's door.
        assert f"/accounts/{seed_user['account'].id}/statements/release" in (
            page
        )
        match_id = db.session.query(statement_match_model.id).scalar()

        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}/statements/release",
            data={"match_id": match_id},
            follow_redirects=True,
        )

        assert b"Match undone" in response.data
        # ...and it is THIS page that came back, not the review screen.
        assert b"Import a statement" in response.data
        assert b"What the bank has told us" in response.data
        db.session.expire_all()
        assert db.session.query(statement_match_model).count() == 0

    def test_an_import_whose_rules_file_NOTHING_says_nothing_about_them(
        self, auth_client, db, seed_user,
    ):
        """The control that stops the receipt sentence appearing always.

        An ordinary import for an owner who has stated no rule files nothing,
        and a receipt claiming a filing pass on every upload would be noise on
        the one screen whose sentences have to be read.
        """
        response = _upload(auth_client, seed_user["account"].id, _payload())

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert toasts, "the import receipt itself is missing"
        assert not any(
            "your standing rules" in message.lower()
            for _, message in toasts
        ), toasts
        # The receipt CARD, which is a different surface from the banner's
        # explanation of what a rule would do.
        assert b'<i class="bi bi-signpost-split"></i> Filed by your rules' \
            not in response.data
        assert db.session.query(TransactionEntry).count() == 0

    def test_the_filing_is_COMMITTED_with_the_import(
        self, auth_client, db, seed_user,
    ):
        """ONE unit of work: the lines and the purchases land together.

        The route commits once, so an import whose filing died outside a
        designed refusal would leave neither -- which is what makes the receipt
        an account of what happened rather than a hope.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            db.session.commit()

        _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
            ]),
        )

        db.session.expire_all()
        assert db.session.query(StatementImport).count() == 1
        assert db.session.query(TransactionEntry).count() == 1
        assert db.session.query(statement_match_model).filter(
            statement_match_model.applied_by_rule.is_(True),
        ).count() == 1

    def test_a_BUDGET_the_app_cannot_derive_still_records_the_bank_s_lines(
        self, auth_client, db, seed_user,
    ):
        """Found by adversarial security review 2026-08-26.

        `ReviewScope.build` raises for two setup states -- `PayCalendarError`
        when the owner's paydays cannot define a calendar, and
        `BaselineMissingError` when no scenario can price a row.  **Nothing
        registers a handler for the first**, so before this arm existed a
        broken calendar reached the browser as a bare 500 AND rolled the whole
        unit of work back, losing the import.

        Recording what the bank said has no dependency on either.  The page's
        own GET proves it: `statements()` builds no scope at all and renders
        perfectly well.  So the lines land, the rules report that they could
        not run, and the owner is told what to fix.

        **The baseline arm is the one this drives**, because it is the one
        ordinary data can reach: `uq_pay_periods_user_start` makes two paydays
        on one day UNWRITABLE, so the calendar's own tie arm cannot be built
        through the schema.  Both are caught by the same `except`, on the same
        line, so the case that reaches one grades the arm for both.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            # What `BaselineMissingError`'s own message calls "the data was
            # changed outside the app": the owner has scenarios and none of
            # them is the baseline.
            seed_user["scenario"].is_baseline = False
            db.session.commit()

        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
            ]),
        )

        assert response.status_code == 200
        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "did not run" in message for _, message in toasts
        ), toasts
        db.session.expire_all()
        # The BANK's lines are recorded...
        assert db.session.query(BankStatementLine).count() == 1
        assert db.session.query(StatementImport).count() == 1
        # ...and nothing was filed into the budget.
        assert db.session.query(TransactionEntry).count() == 0

    def test_a_missing_file_is_refused_without_a_500(
        self, auth_client, db, seed_user,
    ):
        """Submitting the form with no file chosen."""
        response = _upload(auth_client, seed_user["account"].id, None)

        assert response.status_code == 200
        assert b"Choose a file" in response.data
        assert db.session.query(StatementImport).count() == 0

    def test_an_unknown_source_is_refused(self, auth_client, db, seed_user):
        """A submitted source with no adapter, e.g. a tampered form."""
        response = _upload(
            auth_client, seed_user["account"].id, _payload(),
            source="not_a_real_source",
        )

        assert response.status_code == 200
        assert b"Must be one of" in response.data
        assert db.session.query(StatementImport).count() == 0

    def test_a_file_that_is_not_a_statement_shows_the_refusal(
        self, auth_client, db, seed_user,
    ):
        """The service's own sentence reaches the user, not a generic error."""
        response = _upload(
            auth_client, seed_user["account"].id, b"not,a,statement\r\n1,2,3\r\n",
        )

        assert response.status_code == 200
        assert b"not a SECU transaction export" in response.data
        assert db.session.query(StatementImport).count() == 0

    def test_a_file_that_contradicts_itself_shows_the_refusal(
        self, auth_client, db, seed_user,
    ):
        """The running-balance chain, refused through the real request path.

        Written by breaking the chain rather than by mocking the checker, so
        this test would fail if the route stopped calling it.
        """
        rows = build.chained("100.00", _ENTRIES)
        rows[0][10] = "9999.00"
        payload = build.build(rows)

        response = _upload(auth_client, seed_user["account"].id, payload)

        assert b"does not add up" in response.data
        assert db.session.query(BankStatementLine).count() == 0

    def test_a_refusal_after_a_good_import_leaves_the_good_one_intact(
        self, auth_client, db, seed_user,
    ):
        """The rollback must not take the previous request's work with it."""
        _upload(auth_client, seed_user["account"].id, _payload())

        _upload(
            auth_client, seed_user["account"].id,
            _payload(account_number="******9999"), filename="wrong.csv",
        )

        db.session.expire_all()
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(StatementImport).count() == 1

    def test_a_wrong_account_file_shows_the_refusal(
        self, auth_client, db, seed_user,
    ):
        """R-FP's mapping check, through HTTP."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(account_number="******9999"), filename="wrong.csv",
        )

        assert b"has been imported from" in response.data
