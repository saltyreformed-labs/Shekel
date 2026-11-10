"""The books-opening restatement door, at its two entrances (plan step X-f3c-2b-2a).

**One door, two entrances** (developer ruling, 2026-08-31).  The FORM lives on
the shared account edit page, which is the surface every account kind reaches
(the cockpit card's kebab -> Edit); the balance-history card on the cash detail
page LINKS to it.  The opening is DISPLAYED only on that card, and that page
serves three of the developer's nine accounts -- while four of the other six
carry a ``migration_derived`` opening the balance fold READS, so a door
reachable only from the card would leave the accounts most likely to hold a
wrong figure with no way to correct one.

**Both entrances existed and an ARCHIVED account could click NEITHER** (finding
**N-430**, closed by plan step X-f3c-2b-2d).  The cockpit replaces an archived
account's cell with a drawer card, and that card emitted no ``href`` naming its
account at all -- so the surface "every account kind reaches" was reachable only
by typing its URL, and the account-10 repair runbook had to unarchive the twin
and re-archive it to restate books the door would have accepted either way.  The
drawer card carries the same plain *Edit* link the live cell's kebab does now,
so the FORM entrance is reached from both states of an account.  The second
entrance is not, and deliberately is not chased here: it sits on a cash detail
page an archived account links to from nowhere, which is **N-453**'s question
and ``balance:X-f4``'s to answer.

**What is graded here and what is graded one layer down.**  The service suite
(``tests/test_services/test_opening_restatement.py``) owns the money: the
append, ruling **R-EQ**'s did-this-change decision, both day bounds, and the
posted ledger following.  This file owns what a browser can reach -- that both
entrances exist, that the form POSTS what the door reads, that an owner cannot
touch another owner's books, and that a refusal is rendered rather than
swallowed.

**The form-posts-what-the-door-reads case is not ceremony.**  A hand-picked
payload can green-light a route whose real form emits different field names --
this project has shipped a primary arm that was DEAD in a browser exactly that
way -- so the field names asserted below are read out of the RENDERED page.
"""

from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, TxnTypeEnum
from app.extensions import db
from app.services import (
    account_service,
    cash_ledger,
    planned_rows_books,
    recurrence_engine,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence import RecurrenceResolutionError
from app.models.account import Account
from app.models.account_opening import AccountOpening
from app.models.transaction_template import TransactionTemplate
from app.utils.dates import display_today
from tests._test_helpers import (
    account_never_asserted,
    match_two_lines,
    create_account_of_type,
    create_settled_cash_transaction,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

_ONE_DAY = timedelta(days=1)


def _restate(client, account_id, opened_on, equity):
    """POST a restatement the way the rendered form does."""
    return client.post(
        f"/accounts/{account_id}/opening",
        data={
            "opened_on": opened_on.isoformat(),
            "opening_equity": str(equity),
        },
        follow_redirects=True,
    )


def _account_with_a_planned_bill(seed_user, seed_periods):
    """Return an account whose only record is an every-paycheck bill, and its rows.

    Its books open 30 days before the schedule and it asserts nothing, so the
    bill's still-Projected rows are the planned-row term (ruling R-PC88) and
    nothing else bounds the opening below today.  Committed, so a request
    reads it.
    """
    account = account_never_asserted(
        seed_user, db.session, name="Ceiling Planned",
    )
    db.session.flush()
    db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=seed_periods[0].start_date - timedelta(days=30),
        opening_equity=Decimal("10.00"),
        source_id=ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
    ))
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account.id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Planned bill",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("25.00"))
    make_cadence_rule(
        template, EVERY_PERIOD, starts_on=seed_periods[0].start_date,
    )
    db.session.refresh(template)
    rows = recurrence_engine.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(seed_user["user"].id),
            {period.id for period in seed_periods},
        ),
        seed_user["scenario"].id,
    )
    db.session.commit()
    return account, rows


class TestTheCardIsRendered:
    """The form's entrance, and the kinds it is withheld from."""

    def test_the_edit_page_carries_the_books_opening_card(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """GET /accounts/<id>/edit renders the card, its anchor and its POST target."""
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{account_id}/edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "When the books opened" in html
            # The anchor the balance-history card's link targets.
            assert 'id="books-opening"' in html
            assert f'/accounts/{account_id}/opening"' in html
            # The two field names the door reads, taken off the page rather
            # than from this file's own opinion of them.
            assert 'name="opened_on"' in html
            assert 'name="opening_equity"' in html

    def test_the_create_form_carries_no_books_opening_card(
        self, app, auth_client, seed_user,
    ):  # pylint: disable=unused-argument
        """GET /accounts/new offers no restatement, and that is not an oversight.

        A brand-new account holds no records for an assertion to contain, so
        the opening balance the create form already asks for IS its opening
        equity.  A second pair of boxes for the same fact is the
        two-doors-one-fact shape plan step X-f1e deleted from this very form.
        """
        with app.app_context():
            resp = auth_client.get("/accounts/new")

            assert resp.status_code == 200
            assert "When the books opened" not in resp.data.decode()

    def test_a_LOAN_edit_page_carries_no_card(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A loan's opening is its original principal, so the card is ABSENT.

        Absent rather than disabled: an affordance whose submission is
        guaranteed to be refused is the dead-end this package's own
        ``anchor.anchor_form`` docstring rules against.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, db.session, "Mortgage", "Route Loan",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            resp = auth_client.get(f"/accounts/{loan.id}/edit")

            assert resp.status_code == 200
            assert "When the books opened" not in resp.data.decode()

    def test_the_balance_history_card_links_to_the_door(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The SECOND entrance: the opening row on the cash detail page.

        The card is a RECORD and not a write door -- its own module says so --
        so it points at the form rather than editing in place.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(
                f"/accounts/{account_id}/balance-history",
            )

            assert resp.status_code == 200
            html = resp.data.decode()
            assert f"/accounts/{account_id}/edit#books-opening" in html
            assert "Restate" in html

    def test_an_ARCHIVED_accounts_drawer_card_REACHES_the_form(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Finding **N-430**: the entrance an archived account could not click.

        **The destination is FETCHED, not merely asserted.**  A card that
        emitted an ``href`` to a page carrying no form would satisfy the letter
        of "the card links to the edit page" and leave N-430 exactly where it
        was, and a bare href assertion cannot tell the two apart.  The GET below
        rebuilds the URL from ``account_id`` rather than parsing it back out of
        the page, which is equivalent only because the assertion above pins the
        rendered ``href`` to that exact string -- said rather than implied,
        because "follows the link" would be a shade stronger than the code.

        **The href is matched WHOLE, closing quote included, so the fragment
        cannot creep back in.**  ``#books-opening`` names an anchor a loan's
        edit page does not render; a link carrying it would have to be withheld
        from an archived loan, which is the gate this step's design exists
        without.  A substring match on the path would pass either way.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Closed Savings",
            )
            account.is_active = False
            db.session.commit()
            account_id = account.id

            cockpit = auth_client.get("/savings")
            # Asserted rather than assumed: without it a 500 reaches the split
            # as an ``IndexError``, which fails honestly but names the wrong
            # thing.  The split literal occurs exactly ONCE in the template
            # tree, and the drawer is skipped whole when nothing is archived --
            # so ``[1]`` cannot pick another region and cannot be silently
            # empty.
            assert cockpit.status_code == 200
            drawer = cockpit.data.decode().split('id="archivedAccounts"')[1]

            assert "Closed Savings" in drawer
            assert f'href="/accounts/{account_id}/edit"' in drawer

            landed = auth_client.get(f"/accounts/{account_id}/edit")
            assert landed.status_code == 200
            assert "When the books opened" in landed.data.decode()


class TestTheDoor:
    """What the POST does, refuses, and says."""

    def test_a_restatement_is_recorded_and_acknowledged(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The happy path: the row moves and the owner is told what did not.

        The acknowledgement names the LIMITATION as well as the change,
        because the account's later assertions still say what they said -- so
        the difference shows up as a correction against them rather than being
        absorbed, and an owner not told that reads the unchanged balance as
        the door having failed.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            standing = cash_ledger.account_opening_fact(account_id)
            new_day = standing.opened_on - _ONE_DAY

            resp = _restate(
                auth_client, account_id, new_day, Decimal("4321.00"),
            )

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Books restated" in html
            assert "correction" in html
            governing = cash_ledger.account_opening_fact(account_id)
            assert governing.opened_on == new_day
            assert governing.opening_equity == Decimal("4321.00")
            assert governing.opening_id != standing.opening_id

    def test_restating_what_already_stands_says_so_without_writing(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling **R-EQ** at the surface: reported as success, and honestly.

        "Nothing was changed" rather than "recorded", which would be false --
        the service rolled the transaction back.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            standing = cash_ledger.account_opening_fact(account_id)

            resp = _restate(
                auth_client, account_id,
                standing.opened_on, standing.opening_equity,
            )

            assert resp.status_code == 200
            assert "Nothing was changed" in resp.data.decode()
            assert cash_ledger.account_opening_fact(account_id).opening_id == (
                standing.opening_id
            )

    def test_a_FUTURE_day_is_refused_and_the_reason_is_rendered(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The service's sentence reaches the page rather than a 500 or silence.

        A refusal that renders nothing is the defect this package's own
        ``_anchor_editor_error`` exists to prevent one door over; here the
        shape is the flash-and-redirect every other write on this page uses.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            standing = cash_ledger.account_opening_fact(account_id)

            resp = _restate(
                auth_client, account_id,
                display_today() + _ONE_DAY, Decimal("1.00"),
            )

            assert resp.status_code == 200
            assert "not happened yet" in resp.data.decode()
            assert cash_ledger.account_opening_fact(account_id).opening_id == (
                standing.opening_id
            )

    def test_a_MISSING_field_is_refused_and_nothing_is_written(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both fields are required, and a bad payload writes no row.

        A restatement means *the figure on file is wrong and here is the right
        one*; there is no day or figure it defaults to, because those are
        exactly the two things being corrected.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            standing = cash_ledger.account_opening_fact(account_id)

            resp = auth_client.post(
                f"/accounts/{account_id}/opening",
                data={"opened_on": standing.opened_on.isoformat()},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert cash_ledger.account_opening_fact(account_id).opening_id == (
                standing.opening_id
            )

    def test_a_LOAN_is_refused_at_the_door_as_well_as_in_the_card(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The card being absent is not the gate, because a KIND is editable.

        An account re-typed to a mortgage in a second tab can still have this
        form submitted against it, and the service's own refusal is a
        ``ValueError`` that would reach the owner as a 500.  So the route asks
        too -- the same race ``anchor._true_up_request_gates`` documents.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, db.session, "Mortgage", "Raced Loan",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            standing = cash_ledger.account_opening_fact(loan.id)

            resp = _restate(
                auth_client, loan.id, display_today(), Decimal("-900.00"),
            )

            assert resp.status_code == 200
            assert "original principal" in resp.data.decode()
            assert cash_ledger.account_opening_fact(loan.id).opening_id == (
                standing.opening_id
            )

    def test_an_ARCHIVED_account_can_COMPLETE_a_restatement(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A REGRESSION GUARD on the capability this step makes clickable.

        **It would pass on the pre-fix tree, and saying so is the point.**  The
        door already accepted an archived account's restatement when reached by
        a typed URL -- finding **N-430** records exactly that, "both doors
        ACCEPT the write when reached directly" -- so this case grades nothing
        X-f3c-2b-2d changed and must not be read as its evidence.  What it
        guards is the future: the step points an owner at this write from the
        cockpit, and the account-10 runbook's stop rules turn on the twin being
        restatable while still archived, so an ``is_active`` filter reaching
        this path silently would now break a prescribed procedure.

        **What it does NOT keep**, said because an earlier draft of this
        docstring claimed it did: the worked-out read walks every account the
        owner holds, archived ones included (``ledger_lines.worked_out_lines``),
        and this case does not grade that decision.  *Until plan task plan#3133
        the decision was the stored ledger's enumerator,
        ``account_posting_service``'s ``_non_loan_accounts_id_query``, which
        the door never reached: it re-synced its one account by id.*

        No other case anywhere in ``tests/`` archives an account and restates
        it: ``test_opening_restatement.py`` mentions neither ``is_active`` nor
        archiving in any of its 27 cases, and every route case in this file
        restates an ACTIVE account.  A door that rendered fine and then refused,
        or silently no-opped, on an archived one would pass all of them.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Closed Writable",
            )
            account.is_active = False
            db.session.commit()
            account_id = account.id
            before = cash_ledger.account_opening_fact(account_id)
            new_day = before.opened_on - _ONE_DAY

            resp = _restate(
                auth_client, account_id, new_day, Decimal("123.45"),
            )

            assert resp.status_code == 200
            after = cash_ledger.account_opening_fact(account_id)
            # A NEW row governs -- not the old one re-read, which is what a
            # refused or no-op write would leave behind.
            assert after.opening_id != before.opening_id
            assert after.opened_on == new_day
            assert after.opening_equity == Decimal("123.45")
            # And the account is still archived: the door moved a figure, not
            # the state that made the reach a finding.
            assert db.session.get(Account, account_id).is_active is False


class TestWhatTheCardSAYS:
    """The two facts the card renders that nothing else in the app does."""

    def test_a_DERIVED_opening_is_badged_and_a_declared_one_is_not(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The badge is the card's whole reason for existing, and it was ungraded.

        Adversarial review, 2026-08-31: inverting the ``declared`` comparison
        in ``books_opening_context`` failed nothing, which would present the
        seven ``migration_derived`` production figures as stated observations
        and label a corrected one a guess -- the exact N-275 / N-379
        presentation defect this card exists to fix.

        Both directions in one case, because a control that only ever sees one
        state cannot tell a working flag from a constant.
        """
        with app.app_context():
            account = seed_user["account"]
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=cash_ledger.account_opening_fact(account.id).opened_on,
                opening_equity=Decimal("777.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.MIGRATION_DERIVED,
                ),
            ))
            db.session.commit()

            derived_page = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()
            assert "derived, not stated" in derived_page
            assert "Nobody stated this figure" in derived_page
            # The standing FIGURE is rendered too -- a card pre-filling the
            # wrong one would ship green on the badge alone.
            assert "777.00" in derived_page

            _restate(
                auth_client, account.id,
                cash_ledger.account_opening_fact(account.id).opened_on, Decimal("888.00"),
            )

            declared_page = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()
            assert "derived, not stated" not in declared_page
            assert "Nobody stated this figure" not in declared_page
            assert "888.00" in declared_page

    def test_the_ceiling_STOPS_at_the_earliest_asserted_balance(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The term that binds on an ordinary account, and the one added last.

        **Both ceiling cases here asserted the WRONG bound until 2026-08-31**:
        one expected the movement day minus one and the other expected today,
        which is what the code review reproduced as a money defect -- an
        account with no settled movement could be restated past every balance
        the owner had recorded, fabricating ``$22,809.02`` of return on the
        developer's own Roth IRA.  They are rewritten rather than deleted
        because the behaviour they graded was measured wrong, and each of the
        three now isolates ONE term of ``min(today, movement - 1, assertion)``
        so a dropped term fails exactly one case.

        This one: an ordinary account with assertions and no movements.
        """
        with app.app_context():
            account = seed_user["account"]
            first = cash_ledger.earliest_assertion_day(account.id)
            assert cash_ledger.earliest_recorded_movement_day(account.id) is None, (
                "this case isolates the ASSERTION term; the fixture has a "
                "movement, so the ceiling could be the movement's"
            )
            assert first < display_today(), (
                "this case isolates the ASSERTION term; it must be strictly "
                "below today or the ceiling could be today's"
            )

            html = auth_client.get(f"/accounts/{account.id}/edit").data.decode()

            # NOT minus a day: an opening EQUAL to the earliest assertion is
            # what every account is created holding.
            assert f'max="{first.isoformat()}"' in html
            assert "already recorded a balance" in html

    def test_the_ceiling_STOPS_before_the_earliest_movement(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The MOVEMENT term, isolated by an account that asserts nothing.

        A movement is refused ON the opening day as well as after it, so the
        last legal day is the day BEFORE it -- which is what separates this
        term from the assertion term beside it, where equality is legal.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Movement Ceiling",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.flush()
            txn = create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("25.00"),
                account=account, name="Bounding movement",
            )
            db.session.commit()
            assert cash_ledger.earliest_assertion_day(account.id) is None, (
                "this case isolates the MOVEMENT term"
            )

            html = auth_client.get(f"/accounts/{account.id}/edit").data.decode()

            assert f'max="{(txn.settled_on - _ONE_DAY).isoformat()}"' in html
            assert txn.settled_on.strftime("%b %-d, %Y") in html

    def test_the_ceiling_is_TODAY_when_the_account_records_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The clock term, and the only state in which it is the smallest.

        An account with neither an assertion nor a movement is bounded only by
        the clock, and the help text says so rather than naming a bound that
        does not exist.

        **The sentence CHANGED at plan step balance:X-f3c-2b-2b and the
        assertion changed with it.**  It read "This account records nothing
        yet, so any past day will do" -- a claim about the account's RECORDS,
        which was safe only while a Jinja ``{% else %}`` kept it to the state
        where there were none.  Composing the ceiling in the route made the
        clock the first of four candidates, and ``min`` keeps the first
        minimum, so it began winning every TIE -- and a brand-new account ties
        it against its own origination assertion at today.  The sentence now
        says why TODAY bounds the box and asserts nothing about the records,
        which is true in both states.  ``test_a_BRAND_NEW_account_is_not_told_
        it_records_nothing`` is the case that would fail if it went back.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Unbounded",
            )
            db.session.flush()
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=seed_periods[0].start_date,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()

            html = auth_client.get(f"/accounts/{account.id}/edit").data.decode()

            assert f'max="{display_today().isoformat()}"' in html
            assert "has not happened yet" in html

    def test_an_account_with_NO_opening_row_still_renders_its_edit_page(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A broken invariant costs the CARD, never the page.

        Adversarial review, 2026-08-31: the context builder read through
        ``account_opening_fact``, which RAISES for an account carrying no
        opening row -- so the one page offering rename, re-type and hard-delete
        would 500 for exactly the account most needing repair.  (It said
        "archive" until X-f3c-2b-2d's review measured that false: no template
        but the live cell's kebab references ``accounts.archive_account``, and
        ``accounts/form.html`` renders no ``is_active`` control.  The twin of
        this sentence in ``routes/accounts/opening.py`` is still wrong and is
        reported rather than fixed, being outside that step's scope -- finding
        N-454.)  It reads the non-raising twin now and returns ``None``.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="No Opening Row",
            )
            db.session.commit()

            resp = auth_client.get(f"/accounts/{account.id}/edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "When the books opened" not in html
            # The page's OTHER doors still work, which is the whole point.
            assert "Danger Zone" in html


class TestOwnership:
    """404 for both "not found" and "not yours", and nothing written either way."""

    def test_an_account_that_does_not_exist_is_404(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A missing id answers 404, not a 500 out of the service."""
        with app.app_context():
            resp = auth_client.post(
                "/accounts/999999/opening",
                data={
                    "opened_on": display_today().isoformat(),
                    "opening_equity": "1.00",
                },
            )
            assert resp.status_code == 404

    def test_another_owners_account_is_404_and_UNTOUCHED(
        self, app, auth_client, seed_user, seed_second_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The IDOR case, with the write checked as well as the status.

        A 404 from the URL MAP and a 404 from the ownership gate look
        identical, so the second assertion is what makes this case about the
        GATE: the victim's opening must still be the one it was.
        """
        with app.app_context():
            victim = seed_second_user["account"]
            standing = cash_ledger.account_opening_fact(victim.id)

            resp = auth_client.post(
                f"/accounts/{victim.id}/opening",
                data={
                    "opened_on": (standing.opened_on - _ONE_DAY).isoformat(),
                    "opening_equity": "99999.00",
                },
            )

            assert resp.status_code == 404
            after = cash_ledger.account_opening_fact(victim.id)
            assert after.opening_id == standing.opening_id
            assert after.opening_equity == standing.opening_equity

    def test_the_route_still_resolves_for_its_own_owner(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The URL MAP half, paired with the IDOR case above.

        Moving a route leaves its ownership control passing and guarding
        nothing, because a 404 from the map and a 404 from the gate are
        indistinguishable.  This is the case that fails if the URL ever moves.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            standing = cash_ledger.account_opening_fact(account_id)
            resp = auth_client.post(
                f"/accounts/{account_id}/opening",
                data={
                    "opened_on": standing.opened_on.isoformat(),
                    "opening_equity": str(standing.opening_equity),
                },
            )
            assert resp.status_code == 302


class TestTheCeilingNamesTheBoundThatBinds:
    """The date box's ``max`` and the sentence under it are ONE decision.

    Plan step **balance:X-f3c-2b-2b**.  The card chose which bound to name
    itself, in Jinja, in the order movement-then-assertion -- which is not the
    order the ``min`` behind ``max`` resolves them in.  So an account could be
    shown a true sentence about a bound that was not the one stopping it, and
    adding the fourth term would have made a two-way disagreement three-way.
    The route composes both now, and these cases grade that they agree.
    """

    def test_the_MATCHED_LINE_term_bounds_the_box(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fourth term, isolated by an account that records nothing else.

        FIRING CONTROL: without it the ceiling reads TODAY on an account whose
        books may not pass a bank line it has already matched -- the same
        shape the assertion term was added for, one row set over.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Ceiling Matched",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )
            assert cash_ledger.earliest_assertion_day(account.id) is None
            assert cash_ledger.earliest_recorded_movement_day(
                account.id,
            ) is None, "this case isolates the MATCHED-LINE term"

            html = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()

            assert f'max="{(early - _ONE_DAY).isoformat()}"' in html
            assert "matched a bank line" in html
            assert early.strftime("%b %-d, %Y") in html

    def test_the_MATCHED_LINE_term_WINS_over_a_LATER_movement(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The PRODUCTION shape, which no other case renders.

        Every other matched-line case isolates the term on an account that
        records no movement at all, so the route could have computed the
        matched entry off ``earliest_recorded_movement_day`` by copy-paste and
        every one of them would still pass.  Here BOTH terms exist and the
        matched line is strictly EARLIER -- which is the arrangement the whole
        arm exists for, because a match settles its members on the LATEST of
        its bank days -- so only a ceiling that really read the matched row
        set names the right day and the right sentence.

        Found by adversarial test review 2026-08-31.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Ceiling Both Terms",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )
            # A settled movement well AFTER the matched line, so the movement
            # bound is the looser of the two and must not win.
            create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("25.00"),
                settled_on=early + timedelta(days=40),
                name="later-than-the-matched-line", account=account,
            )
            db.session.commit()

            movement = cash_ledger.earliest_recorded_movement_day(account.id)
            matched = cash_ledger.earliest_matched_line_day(account.id)
            assert movement is not None and matched is not None, (
                "this case needs BOTH terms present to mean anything"
            )
            assert matched < movement, (
                "the matched line must be the tighter bound or the case "
                "grades nothing the movement term does not already grade"
            )

            html = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()

            assert f'max="{(matched - _ONE_DAY).isoformat()}"' in html
            assert f'max="{(movement - _ONE_DAY).isoformat()}"' not in html
            assert "matched a bank line" in html
            assert "already records money moving" not in html

    def test_the_sentence_names_the_bound_the_MAX_came_from(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL for the defect this refactor fixed.

        An account asserted early and first settled LATER: the ceiling comes
        from the assertion, and the card used to print the movement's sentence
        because its ``{% if %}`` asked about movements first.  Both facts are
        read out of the rendered page, so a sentence naming the other bound
        fails here rather than being noticed by a reader.
        """
        with app.app_context():
            account = seed_user["account"]
            first_assertion = cash_ledger.earliest_assertion_day(account.id)
            create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("25.00"),
                settled_on=first_assertion + timedelta(days=60),
                name="later-than-the-assertion",
            )
            db.session.commit()
            movement = cash_ledger.earliest_recorded_movement_day(account.id)
            assert movement > first_assertion, (
                "the whole point of this case is a movement LATER than the "
                "assertion, so the two bounds disagree about which binds"
            )

            html = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()

            # The NEGATIVE is derived from the fixture's own arithmetic
            # rather than from a reader, which is what makes this case able to
            # fail: the positive below reads the same producer the route does,
            # so on its own it would pass even if that producer were wrong.
            # The defect actually guarded here is the route picking the
            # MOVEMENT bound, and this is the assertion that sees it.
            movement_ceiling = movement - timedelta(days=1)
            assert f'max="{movement_ceiling.isoformat()}"' not in html
            assert f'max="{first_assertion.isoformat()}"' in html
            assert "already recorded a balance" in html
            assert "already records money moving" not in html

    def test_the_PLANNED_ROW_term_bounds_the_box_where_the_door_does(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fifth term (ruling R-PC88; plan step pay_calendar:C18-a, review M2).

        An account whose only record is an every-paycheck recurring bill,
        still Projected: the box stops the day before its first row's due
        day, the sentence names that row -- and the door AGREES at the
        boundary, accepting the box's own ``max`` and refusing the day after.
        FIRING CONTROL: without the term the box offered TODAY, a day the
        door refuses over every unpaid row since the first payday.
        """
        with app.app_context():
            account, rows = _account_with_a_planned_bill(seed_user, seed_periods)
            first_due = min(row.due_date for row in rows)
            assert first_due < display_today(), "the term must bind below today"
            assert cash_ledger.earliest_recorded_movement_day(account.id) is None
            assert cash_ledger.earliest_assertion_day(account.id) is None

            html = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()

            ceiling = first_due - _ONE_DAY
            assert f'max="{ceiling.isoformat()}"' in html
            assert "Planned bill" in html
            refused = _restate(
                auth_client, account.id, ceiling + _ONE_DAY, "10.00",
            ).data.decode()
            assert "still projected and due" in refused
            accepted = _restate(
                auth_client, account.id, ceiling, "10.00",
            ).data.decode()
            assert "Books restated" in accepted

    def test_a_BRAND_NEW_account_is_not_told_it_records_nothing(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL for the TIE, which is the ordinary case and not an edge.

        ``account_service.create_account`` writes the origination opening and
        its assertion on ONE day, so a fresh account ties the clock bound
        against the assertion bound at today -- and ``min`` keeps the FIRST
        minimum, which is the clock's.  A first draft of this refactor gave
        the clock entry the template's old ``{% else %}`` sentence, "This
        account records nothing yet, so any past day will do", which only ever
        ran when no other bound existed.  Here it won the tie and told the
        owner of an account holding an asserted balance that it recorded
        nothing.  Found by adversarial design review 2026-08-31.
        """
        with app.app_context():
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Brand New",
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.commit()
            assert cash_ledger.earliest_assertion_day(
                account.id,
            ) == display_today(), (
                "this case is about the TIE; the assertion has to land on "
                "today for the clock bound to be tied with it"
            )

            html = auth_client.get(
                f"/accounts/{account.id}/edit",
            ).data.decode()

            assert f'max="{display_today().isoformat()}"' in html
            assert "records nothing yet" not in html
            assert "has not happened yet" in html


class TestAnUnreadableScheduleIsRefusedNotA500:
    """The step's review, L7: the POST raised on a rule no walk can read."""

    def test_the_POST_flashes_the_refusal_and_writes_nothing(
        self, app, auth_client, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """The service fails closed, and the page it returns to still renders.

        The day is the box's own ``max``, which the walkable schedule ACCEPTS
        (``test_the_PLANNED_ROW_term_bounds_the_box_where_the_door_does``), so
        the refusal is the unread schedule's.  FIRING CONTROL: before L7 the
        recurrence error escaped the route's ``except ValidationError``.
        """
        with app.app_context():
            account, rows = _account_with_a_planned_bill(seed_user, seed_periods)
            day = min(row.due_date for row in rows) - _ONE_DAY
            before = db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).count()

            def refusing(*_args, **_kwargs):
                raise RecurrenceResolutionError("a rule no door writes")

            monkeypatch.setattr(planned_rows_books, "resolved_with_books", refusing)

            resp = _restate(auth_client, account.id, day, "10.00")

            assert resp.status_code == 200
            assert (
                "has a schedule that cannot be read: repair its schedule first."
            ) in resp.data.decode()
            db.session.expire_all()
            assert db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).count() == before
