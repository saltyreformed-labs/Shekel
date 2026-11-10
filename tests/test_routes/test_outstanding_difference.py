"""The Outstanding difference card, at its two mounts (plan step X-f3c-3).

What an account's own books cannot explain, beside whether an imported bank
statement has checked the days it accumulated over.  Money-neutral: nothing on
this card writes, and the act that ACCEPTS the figure is plan step X-f3c-4.

**What is graded here and what is graded one layer down.**  The service suites
own the money -- ``tests/test_services/test_outstanding_difference.py`` owns
the subtraction and its scope, and
``tests/test_services/test_bank_agreement.py`` owns the span verdict and its
four firing controls.  This file owns what a browser can reach: that the card
renders on the page AND through its refresh fragment, that both mounts publish
the same figures, that an account the question does not apply to gets no card
rather than an empty one, and that neither another owner's account nor a kind
this page does not serve can reach it.

**The kind gate is not ceremony.**  ``GET /accounts/<loan id>/balance-history``
once rendered a cash card for an amortizing account, captioned with copy that
is false for a loan, because a fragment added in a module the page imports
guarded on ownership alone (``app.routes.accounts._cash_page``).  This
fragment is that same shape, so the same case is asserted for it.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.services import account_service, outstanding_difference
from app.services.balance_at import BalanceContext
from app.enums import StatementBalanceEvidenceEnum
from tests._test_helpers import (
    append_balance_assertion,
    create_account_of_type,
    create_hysa_account,
    create_settled_cash_transaction,
    settle_instant_on,
)

#: The day this file settles a row on and declares a balance for -- inside
#: ``seed_periods``' fourth period (2026-02-27..2026-03-12) and safely in the
#: past, which is the same fixed-date idiom the sibling route suites use with
#: that fixture.
_ON = date(2026, 3, 3)


def _build_a_difference(seed_user, seed_periods):
    """Spend $150.00 and still declare $1,000.00, so the books are $150 short.

    Books on 2026-03-03: ``1000.00 (opening) - 150.00 = 850.00``.  Declared:
    ``1000.00``.  Outstanding difference: ``$150.00``.
    """
    create_settled_cash_transaction(
        seed_user, seed_periods[4], Decimal("150.00"),
        settled_on=_ON, name="Groceries",
    )
    append_balance_assertion(
        db.session, seed_user["account"],
        Decimal("1000.00"), settle_instant_on(_ON),
    )
    db.session.commit()

from tests.test_services.test_statement_import.test_anchor import _seed_import


def _reconciled_account(seed_user, seed_periods):
    """Build an account whose every span day is imported, compared and agreeing.

    Books open 2026-03-01 at ``$400.00``; the bank posts ``-$50.00`` on the
    2nd; the app records exactly that; the owner declares ``$350.00`` for the
    2nd.  Books produce ``400 - 50 = 350``, so the difference is ``$0.00`` and
    the one-day span reconciles.
    """
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Reconciled Checking",
            anchor_balance=Decimal("400.00"),
            observed_on=date(2026, 3, 1),
        ),
    )
    _seed_import(
        db, account, stated="350.00",
        effective_on=date(2026, 3, 2),
        evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
        lines=[(date(2026, 3, 2), "-50.00")],
    )
    create_settled_cash_transaction(
        seed_user, seed_periods[4], Decimal("50.00"),
        settled_on=date(2026, 3, 2), name="Groceries", account=account,
    )
    append_balance_assertion(
        db.session, account,
        Decimal("350.00"), settle_instant_on(date(2026, 3, 2)),
    )
    return account


class TestTheCardRendersOnThePage:
    """The cash detail page's mount, and the region that refreshes it."""

    def test_the_page_and_the_FRAGMENT_render_the_same_card(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The page's card is byte-identical to the fragment's.

        **A bare substring assertion on the page body would measure nothing
        here, and an adversarial review caught the first version doing exactly
        that.**  The balance-history card sits on this same page and publishes
        every money string this card does -- ``$1,000.00`` in its Recorded cell
        and its opening row, ``$850.00`` as the 2026-03-03 assertion's Ledger
        (what the records held just before it) and ``$150.00`` as that row's
        Correction.  So ``assert "$850.00" in body`` passes with this card's
        whole figure block deleted, and passes with ``books`` and ``amount``
        swapped.

        Comparing the two RENDERS instead is the assertion that cannot be
        vacuous: it is the one thing the page mount and the fragment mount can
        disagree about, and it is what the two-mounts-one-builder design
        claims.  What each of them SAYS is asserted one class down, where the
        fragment body carries nothing else.
        """
        with app.app_context():
            _build_a_difference(seed_user, seed_periods)
            account_id = seed_user["account"].id

            page = auth_client.get(
                f"/accounts/{account_id}/details",
            ).get_data(as_text=True)
            fragment = auth_client.get(
                f"/accounts/{account_id}/outstanding-difference",
            ).get_data(as_text=True)

            assert fragment.strip()
            assert fragment.strip() in page

    def test_the_region_refreshes_on_balanceChanged(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Both sides of the figure move on that event, so the card listens.

        A true-up appends the assertion the difference is measured against, and
        the reconcile panel's own POST records the settle days that decide what
        the books produce -- so a card that did not re-fetch would state a
        difference against a balance the page above it has already replaced.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            body = auth_client.get(
                f"/accounts/{account_id}/details",
            ).get_data(as_text=True)

            region = f'id="outstanding-difference-{account_id}"'
            assert region in body
            fragment = f"/accounts/{account_id}/outstanding-difference"
            assert fragment in body
            # The region's own trigger, read out of the rendered page rather
            # than assumed: a card wired to no event is a card that goes stale
            # silently.
            marker = body.index(region)
            assert 'hx-trigger="balanceChanged from:body"' in body[
                marker:marker + 400
            ]


class TestTheFragmentSaysTheSameThing:
    """One builder, two mounts -- so a refreshed card cannot drift."""

    def test_the_fragment_carries_the_same_figures(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """GET the refresh target directly and read the same three figures."""
        with app.app_context():
            _build_a_difference(seed_user, seed_periods)
            account_id = seed_user["account"].id

            resp = auth_client.get(
                f"/accounts/{account_id}/outstanding-difference",
            )
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Outstanding difference" in body
            assert "$1,000.00" in body
            assert "$850.00" in body
            assert "$150.00" in body
            # Positive means the account holds money nothing recorded put
            # there, which is the verdict the shared classifier decides.  The
            # SENTENCE is this card's own: the true-up preview renders on the
            # same page off a different subtraction, and the two publishing one
            # vocabulary is the defect this arc has measured three times.
            assert "Your records do not account for what you say" in body

    def test_it_names_what_has_checked_those_days(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With no statement imported, the card SAYS so rather than staying mute.

        Ruling **R-GY** offers the acceptance act only over a span an imported
        statement reconciles, so a card publishing the figure without the state
        of that evidence would be half the instrument.
        """
        with app.app_context():
            _build_a_difference(seed_user, seed_periods)
            account_id = seed_user["account"].id

            body = auth_client.get(
                f"/accounts/{account_id}/outstanding-difference",
            ).get_data(as_text=True)

            assert "No bank statement has been imported" in body
            assert f"/accounts/{account_id}/statements" in body


class TestTheCardIsWithheldWhereTheQuestionDoesNotApply:
    """Two absences, each a statement rather than a gap."""

    def test_a_MODELLED_account_gets_NO_card_at_all(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An HYSA's assertion is mark-to-market, not a check against cash.

        The cash detail page serves it, so the region is there and empty --
        rendering the card with a modelled account's growth under the words
        "your books cannot explain" would caption a return as untracked spend
        (ruling **R-FO**, finding **N-213**).
        """
        with app.app_context():
            # Through the INTEREST factory, which writes the
            # ``budget.interest_params`` row the page refuses to render an
            # interest-bearing account without -- the account, not the card, is
            # what this case needs built correctly.
            hysa = create_hysa_account(
                seed_user, db.session, seed_periods[4], Decimal("5000.00"),
                name="Savings",
            )
            db.session.commit()

            page = auth_client.get(
                f"/accounts/{hysa.id}/details",
            ).get_data(as_text=True)
            fragment = auth_client.get(
                f"/accounts/{hysa.id}/outstanding-difference",
            )

            assert f'id="outstanding-difference-{hysa.id}"' in page
            assert "Outstanding difference" not in page
            assert fragment.status_code == 200
            assert fragment.get_data(as_text=True).strip() == ""

    def test_an_AMORTIZING_account_404s_on_the_fragment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The kind gate, on the fragment and not only on the page.

        A loan's balance is not a cash anchor (ruling D4 / plan step A1,
        finding **B-15**), its detail page 404s, and a fragment written without
        that gate is what rendered cash copy for an amortizing account once
        before.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, db.session, "Mortgage", "House",
                anchor_balance=Decimal("-200000.00"),
            )
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{loan.id}/outstanding-difference",
            )

            assert resp.status_code == 404


class TestAnotherOwnerCannotReachIt:
    """404 for both "not found" and "not yours"."""

    def test_a_second_owner_gets_404_on_the_fragment(
        self, app, second_auth_client, seed_user, seed_second_user,
        seed_periods,
    ):  # pylint: disable=unused-argument
        """The security response rule, and the URL still ROUTES.

        Asserted together on purpose: a 404 from the URL MAP and a 404 from the
        ownership gate look identical, so a moved route would leave this
        control passing and guarding nothing.  The owner's own request in the
        classes above is what proves the URL resolves.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            resp = second_auth_client.get(
                f"/accounts/{account_id}/outstanding-difference",
            )

            assert resp.status_code == 404


class TestTheFooterSaysWhatHasCheckedTheDays:
    """The three branches a browser reaches once a statement exists.

    Only the no-statement branch rendered in any test until adversarial review
    2026-09-01 -- so the sentences an owner who has actually imported something
    sees, and both links out of them, were unguarded end to end.
    """

    def test_a_RECONCILING_span_says_so_and_links_to_the_comparison(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """Every day imported, compared and agreeing, and the difference ZERO.

        Books open 2026-03-01 at $400.00; the bank posts -$50.00 on the 2nd and
        the app records exactly that; the owner declares $350.00 for the 2nd.
        The books produce ``400 - 50 = 350``, so nothing is outstanding AND the
        span reconciles -- the state this whole instrument is aiming at, and
        the one X-f3c-4 will offer nothing on because there is nothing to
        accept.
        """
        with app.app_context():
            account = _reconciled_account(seed_user, seed_periods)
            db.session.commit()

            body = auth_client.get(
                f"/accounts/{account.id}/outstanding-difference",
            ).get_data(as_text=True)

            assert "$0.00" in body
            assert "Everything you have recorded adds up to" in body
            assert "come to the same total" in body
            assert f"/accounts/{account.id}/statements/agreement" in body

    def test_a_DISAGREEING_span_counts_the_days_rather_than_claiming_none(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The one span day carries a row the bank never posted.

        The same account plus $10.00 of spend on 2026-03-02 -- the day the
        span ENDS on, since the declaration is dated there -- with no line
        against it.  A first version dated the row 2026-03-03, which is OUTSIDE
        the span the difference accumulated over, so the footer went on saying
        the span reconciled and the case graded nothing.
        """
        with app.app_context():
            account = _reconciled_account(seed_user, seed_periods)
            create_settled_cash_transaction(
                seed_user, seed_periods[4], Decimal("10.00"),
                settled_on=date(2026, 3, 2), name="Cash out", account=account,
            )
            db.session.commit()

            body = auth_client.get(
                f"/accounts/{account.id}/outstanding-difference",
            ).get_data(as_text=True)

            assert "do not come to the same" in body
            assert "that were compared" in body

    def test_a_GAP_between_two_imports_is_said_ONCE(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The two uncompared counts share no day, so the card says each once.

        Books open 2026-02-28.  One statement's lines run Mar 1 - Mar 2 and
        the next's Mar 5 - Mar 6, every line with its row, and $395.00 is
        declared for Mar 6.  Of the span's 6 days, Mar 3 and Mar 4 lie inside
        no statement, so they are the 2 unimported days -- and every imported
        day is compared, so no day is left for the sentence about imported
        days the comparison did not draw.  Saying "2 of them fall inside an
        imported statement" too would count the same two days twice.
        """
        with app.app_context():
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Gap Checking",
                    anchor_balance=Decimal("500.00"),
                    observed_on=date(2026, 2, 28),
                ),
            )
            _seed_import(
                db, account, stated="1000.00", effective_on=date(2026, 3, 2),
                evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
                lines=[(date(2026, 3, 1), "-50.00"),
                       (date(2026, 3, 2), "-20.00")],
                file_name="first.csv",
            )
            _seed_import(
                db, account,
                lines=[(date(2026, 3, 5), "-25.00"),
                       (date(2026, 3, 6), "-10.00")],
                file_name="second.csv",
            )
            for day, amount in (
                (date(2026, 3, 1), "50.00"), (date(2026, 3, 2), "20.00"),
                (date(2026, 3, 5), "25.00"), (date(2026, 3, 6), "10.00"),
            ):
                create_settled_cash_transaction(
                    seed_user, seed_periods[4], Decimal(amount),
                    settled_on=day, name=f"row {day}", account=account,
                )
            append_balance_assertion(
                db.session, account,
                Decimal("395.00"), settle_instant_on(date(2026, 3, 6)),
            )
            db.session.commit()

            said = " ".join(auth_client.get(
                f"/accounts/{account.id}/outstanding-difference",
            ).get_data(as_text=True).split())

            assert "These 6 days run from Mar 1, 2026 to Mar 6, 2026." in said
            assert (
                "2 of them fall outside the lines of every statement you"
                " have imported" in said
            )
            assert "fall inside an imported statement" not in said

    def test_BOTH_uncompared_counts_are_said_and_ADD_UP(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """Days no statement covers, AND imported days the comparison never drew.

        The seeded account's books open 2024-01-04 and $925.00 is declared
        for 2026-03-03, so the span is 2024-01-05..2026-03-03: ``362 + 365 +
        62 = 789`` days.  One statement's lines run 2024-01-05..2024-01-10,
        six days wholly before 2024-03-03, where the comparison's two-year
        draw begins; the other's are 2026-03-02 and 2026-03-03, each with its
        row.  So 2 days are compared, ``789 - 2 = 787`` are not, and those
        787 are ``789 - 8 = 781`` days no statement covers plus the ``6``
        imported days never drawn -- each said once, by value.
        """
        with app.app_context():
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("925.00"), settle_instant_on(date(2026, 3, 3)),
            )
            _seed_import(
                db, seed_user["account"],
                lines=[(date(2024, 1, 5), "-5.00"),
                       (date(2024, 1, 10), "-5.00")],
                file_name="old.csv",
            )
            _seed_import(
                db, seed_user["account"],
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 3), "-25.00")],
                file_name="new.csv",
            )
            for day, amount in ((date(2026, 3, 2), "50.00"),
                                (date(2026, 3, 3), "25.00")):
                create_settled_cash_transaction(
                    seed_user, seed_periods[4], Decimal(amount),
                    settled_on=day, name=f"row {day}",
                )
            db.session.commit()

            said = " ".join(auth_client.get(
                f"/accounts/{seed_user['account'].id}/outstanding-difference",
            ).get_data(as_text=True).split())
            over = outstanding_difference.outstanding_difference(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
            ).reconciliation

            assert "These 789 days run from Jan 5, 2024 to Mar 3, 2026." in said
            assert (
                "781 of them fall outside the lines of every statement you"
                " have imported" in said
            )
            assert (
                "6 of them fall inside an imported statement but could not be"
                " compared" in said
            )
            # The two sentences are the two parts of the uncompared days.
            assert (over.compared, over.unchecked) == (2, 787)
            assert 781 + 6 == over.unchecked

    def test_an_EMPTY_span_says_the_books_open_after_the_declaration(
        self, app, auth_client, seed_user, seed_periods, db,
    ):  # pylint: disable=unused-argument
        """A brand-new account: the books open on the day it declares.

        The footer must not say a statement agrees or disagrees -- there is no
        day between the two for one to speak about.
        """
        with app.app_context():
            fresh = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Fresh Checking",
                    anchor_balance=Decimal("250.00"),
                    observed_on=seed_periods[4].start_date,
                ),
            )
            db.session.commit()

            body = auth_client.get(
                f"/accounts/{fresh.id}/outstanding-difference",
            ).get_data(as_text=True)

            assert "there is nothing between them for" in body
            assert "come to the same total" not in body
