"""What the page routes and posts: each ownership 404 beside a URL that still routes, every tab
served, an untouched page writing nothing, OK then Apply, a verb with no door, the panel's one
footer, and a malformed open parameter read before the door.
"""

import re
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.models.account import Account
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import auth_service
from app.services.statement_match import Tab
from tests.test_routes._statement_forms import reconcile_form_fields
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_acts,
    filed_by,
)

from ._helpers import (
    _CARD_PAYMENT,
    _url,
    _release_url,
    _match_url,
    _page,
    _post,
    _unskip_url,
    _a_skipped_line,
    _a_swipe_a_rule_files,
)


def _merchants_url(account_id):
    """Return the standing-rule offer's door for *account_id*."""
    return f"/accounts/{account_id}/statements/reconcile/merchants"


class TestTheOwnershipRefusalIsPairedWithTheURLStillRouting:
    """A 404 from the URL MAP and a 404 from the gate look identical.

    So each refusal below stands beside a case proving the same URL answers
    200 for the owner -- without which moving or renaming this route would
    leave every IDOR case here green and guarding nothing.
    """

    @pytest.fixture()
    def _someone_elses_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user."""
        stranger = User(
            email="reconcilestranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        account = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking",
        )
        db.session.add(account)
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this fixture only flushed
        # is one the request cannot see -- and the 404 asserted below must be
        # the OWNERSHIP gate refusing a real account rather than a missing
        # row, which is the whole point of the pairing.
        db.session.commit()
        return account.id

    def test_the_page_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The security response rule: 404 for both refusals."""
        assert auth_client.get(
            _url(_someone_elses_account)
        ).status_code == 404

    def test_the_page_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the refusal above is the GATE's, not the map's."""
        assert auth_client.get(
            _url(seed_user["account"].id)
        ).status_code == 200

    def test_apply_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The money door's own gate."""
        assert auth_client.post(
            _url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_apply_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing for the door that writes."""
        assert auth_client.post(
            _url(seed_user["account"].id), data={"csrf_token": "x"},
        ).status_code == 200

    def test_the_match_pane_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The read endpoint is gated exactly as the doors are."""
        assert auth_client.post(
            _match_url(_someone_elses_account, 1), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_match_pane_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing, on a line this account really holds."""
        _, line = _a_swipe_a_rule_files(seed_user, db)

        assert auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).status_code == 200

    def test_the_rules_door_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The standing-rule door is gated exactly as the money doors are.

        Added with the door itself (ruling **bank_import:R-IB**), because this
        class's own reason for existing is that a 404 from the URL MAP and a
        404 from the gate look identical -- so a door landing without its pair
        is a door whose ownership control nothing holds in place.
        """
        assert auth_client.post(
            _merchants_url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_rules_door_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the same URL answers 200 for the account's owner."""
        assert auth_client.post(
            _merchants_url(seed_user["account"].id),
            data={"csrf_token": "x"},
        ).status_code == 200

    def test_the_release_door_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The UNDO is gated exactly as the money doors are.

        Added with the door itself (plan step ``bank_import:X-gj-1c``).  It
        DESTROYS rows an act created, which is the shape a moved route leaves
        passing and guarding nothing -- so it stands beside its pair below.
        """
        assert auth_client.post(
            _release_url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_release_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the same URL answers for the account's owner.

        A body naming no match is refused by the schema and redirected with a
        flash, which is a 302 -- what this asserts is that the URL RESOLVES,
        which is the only thing the refusal above could otherwise be.
        """
        assert auth_client.post(
            _release_url(seed_user["account"].id), data={"csrf_token": "x"},
        ).status_code == 302

    def test_the_unskip_door_is_404_for_an_account_that_is_not_yours(
        self, auth_client, db, seed_user, _someone_elses_account,
    ):
        """The SKIP undo is gated exactly as the other doors are.

        Added with the door itself (plan step ``bank_import:X-gj-4c-2``).  It
        destroys the owner's own decision, and a moved or renamed route would
        leave this case green while guarding nothing -- so it stands beside
        its pair below.
        """
        assert auth_client.post(
            _unskip_url(_someone_elses_account), data={"csrf_token": "x"},
        ).status_code == 404

    def test_the_unskip_URL_still_routes_for_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The pairing: the same URL answers for the account's owner.

        A body naming no skip is refused by the schema and redirected with a
        flash, which is a 302 -- what this asserts is that the URL RESOLVES,
        which is the only thing the refusal above could otherwise be.
        """
        assert auth_client.post(
            _unskip_url(seed_user["account"].id), data={"csrf_token": "x"},
        ).status_code == 302

    def test_the_match_pane_refuses_a_line_this_pass_never_offered(
        self, auth_client, db, seed_user,
    ):
        """The membership test IS the authorisation.

        A line belonging to someone else, one another match already claims,
        and one that exists nowhere are all refused by the same intersection
        against the pass's own unexplained set -- rather than by a second
        ownership check written here that could drift from it.
        """
        _a_swipe_a_rule_files(seed_user, db)

        assert auth_client.post(
            _match_url(seed_user["account"].id, 999999),
            data={"csrf_token": "x"},
        ).status_code == 404


class TestEveryTabTheServiceBuildsIsServed:
    """Ruling **R-HW**, applied to the bar rather than to a verb.

    **This class graded the OPPOSITE until plan step ``X-gj-1c``**, and both
    readings are the same rule: a tab bar may not offer what the build cannot
    render.  ``X-gj-1b`` shipped the three tabs whose cards are bank lines and
    404'd the two whose cards are ACTS, because they did not exist; both exist
    now, so the route's ``_TABS_SERVED`` tuple was equal to the whole enum and
    was DELETED rather than widened.  What holds now is the stronger claim --
    every member of :class:`~app.services.statement_match.Tab` answers 200 and
    the bar names every one of them -- which is why the cases are driven from
    the enum rather than from a list written here that could fall behind it.
    """

    def test_every_tab_renders(self, auth_client, db, seed_user):
        """Driven from the enum, so a sixth tab is a failure and not a gap."""
        for tab in Tab:
            assert auth_client.get(
                _url(seed_user["account"].id, tab.value)
            ).status_code == 200, tab

    def test_the_release_door_404s_a_tab_that_names_nothing(
        self, auth_client, db, seed_user,
    ):
        """The door reads the tab through the page's own reader, so it refuses
        the same way.

        A crafted body naming no tab must not redirect somewhere the reader
        cannot follow; ``_reconcile_query.requested_tab`` is one function
        and this holds the door to it.
        """
        assert auth_client.post(
            f"{_release_url(seed_user['account'].id)}?tab=nonsense",
            data={"csrf_token": "x"},
        ).status_code == 404

    def test_a_value_naming_no_tab_at_all_is_404(
        self, auth_client, db, seed_user,
    ):
        """A tampered or stale request: not a rendered apology.

        **The one refusal left**, and it is the only one there ever should have
        been: nothing composes this URL by hand, so a value that resolves to no
        tab is a crafted or stale request rather than a person mid-edit.
        """
        assert auth_client.get(
            _url(seed_user["account"].id, "nonsense")
        ).status_code == 404

    def test_every_tab_RENDERS_THE_CONTROL_ITS_KIND_CARRIES(
        self, auth_client, db, seed_user,
    ):
        """Plan step ``bank_import:X-gj-4c-2``: three kinds, three arms.

        **This is what holds the body's three `if` arms complete.**  They are
        rendered one per :class:`~app.services.statement_match._cards.CardKind`
        with NO ``else``, so a kind whose arm was never written draws a blank
        tab and raises nothing -- the state a 200 alone cannot tell from a
        correct one.  Every tab is given a real card and asked for the control
        its kind carries, driven from the enum so a sixth tab fails here.

        The three markers are the ones the kinds do not share: a bank line's
        Apply form, an act's release door, a skip's unskip door.
        """
        envelope = an_envelope(seed_user)
        matched = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72", sequence=1,
        )
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            sequence=2, source_category=_CARD_PAYMENT,
        )
        db.session.commit()
        filed_by(seed_user, matched, envelope, by_rule=False)
        filed_acts(seed_user, 1, by_rule=True)
        _a_skipped_line(seed_user, db)

        #: What only ONE card kind puts in the document -- the Apply form a
        #: bank line's OK submits, the release door an act's Undo posts to,
        #: and the unskip door a skip's Undo posts to.
        markers = ("data-rec-form", "reconcile/release", "reconcile/unskip")
        for tab in Tab:
            page = _page(auth_client, seed_user, tab.value)
            present = [said for said in markers if said in page]

            assert "rec-card" in page, f"{tab} rendered no card at all"
            # **EXACTLY ONE**, which is the template's three-arm partition
            # asserted without restating WHICH kind each tab holds.  That
            # mapping has one home -- ``_tab_sections``, graded by
            # ``TestWhichKindOfCARDAPageHoldsIsSTATEDByWhatBuiltThem`` in
            # ``test_reconcile/test_the_cards_and_skips.py``
            # -- and a copy of it here would be the second home this step
            # exists to delete.  Zero markers is the blank tab a missing arm
            # draws; two is an arm rendering over another's cards.
            assert len(present) == 1, (tab, present)
        # The tab this step BUILT, named once, so the sweep above cannot go
        # green on a page that renders the wrong control everywhere equally.
        assert "reconcile/unskip" in _page(auth_client, seed_user, "skipped")

    def test_the_bar_names_every_tab_and_each_link_answers(
        self, auth_client, db, seed_user,
    ):
        """The bar is the whole of ``page.counts``, and each href routes.

        Asserting the bar NAMES a tab is not the same claim as the tab
        answering, and a bar drawn from a narrowed set would satisfy the first
        while the enum grew past it -- so this follows each link it finds.
        """
        page = _page(auth_client, seed_user)

        for tab in Tab:
            assert f"tab={tab.value}" in page, tab
            assert auth_client.get(
                _url(seed_user["account"].id, tab.value)
            ).status_code == 200, tab


class TestAnUntouchedPageWritesNothing:
    """Rulings **R-FP** and **R-HS**, and the collision they would otherwise
    have.

    R-HS pre-fills a justified suggestion -- the destination a standing rule
    names arrives SELECTED -- and then says *an untouched card is not
    submitted*.  On the review queue's form those two cannot both hold,
    because there the select IS the tick.  Here the OK checkbox is the tick,
    so this posts the page's own bytes with nothing pressed and asserts the
    books did not move.
    """

    def test_the_page_pre_fills_the_rule_s_destination(
        self, auth_client, db, seed_user,
    ):
        """The premise: without the pre-fill this case grades nothing."""
        envelope, _ = _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        assert f'value="{envelope.id}" selected' in page

    def test_posting_it_back_untouched_records_nothing(
        self, auth_client, db, seed_user,
    ):
        """Every control the page renders, at the value it renders."""
        _a_swipe_a_rule_files(seed_user, db)
        before = db.session.query(Transaction).count()
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert not any(name == "ok" for name, _ in fields), (
            "a card arrived OK'd, so this case cannot grade the default"
        )
        response = _post(auth_client, seed_user, fields, page)

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(Transaction).count() == before


class TestOKThenApplyIsWhatMovesMoney:
    """What the card and the footer emit is what the door accepts.

    The loop nothing else closes: a Jinja field name and a Marshmallow field
    name have no compile-time relationship, so this scrapes the page and posts
    exactly what it found.
    """

    def test_an_OK_D_card_becomes_a_purchase_in_the_rule_s_envelope(
        self, auth_client, db, seed_user,
    ):
        """The one-click case ruling **R-HS** exists for."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert any(
            name == f"destination-{line.id}" for name, _ in fields
        ), "the page rendered no destination for this line"
        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 1
        # The act is a PURCHASE against that envelope, which is an entry row
        # rather than anything on the envelope itself.
        entry = db.session.query(TransactionEntry).one()
        assert entry.transaction_id == envelope.id
        assert entry.amount == Decimal("35.72")

    def test_a_deposit_OK_D_on_ADD_becomes_an_income_row(
        self, auth_client, db, seed_user,
    ):
        """Ruling **bank_import:R-GW**'s door, reached through this screen.

        The arm is STATED by the form (``destination-<line>=income``) rather
        than inferred from a missing destination, which is the defect plan
        step X-f6a-3c-2 corrected on the purchase side.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        db.session.commit()
        before = db.session.query(Transaction).count()
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert (f"destination-{line.id}", "income") in fields, (
            "the deposit's ADD tab named no arm, so this grades nothing"
        )
        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        assert db.session.query(Transaction).count() == before + 1
        assert db.session.query(StatementMatch).count() == 1

    def test_a_card_OK_D_with_nothing_chosen_is_REPORTED(
        self, auth_client, db, seed_user,
    ):
        """A press may not go unanswered, and may not cost the other cards.

        It is reachable from a browser -- pressing the ADD tab's own button
        with the select still on *choose where this goes* -- so it may not be
        a pass-level refusal (**R-FZ(a)**) and may not be a silent drop.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Public Library", amount="-5.99",
        )
        db.session.commit()
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 0
        assert "without choosing what to do with them" in body
        assert str(line.id) in body


class TestAVerbWithNoDoorRendersNoControl:
    """Ruling **R-HW**, read off the markup a browser would receive.

    All four verbs render on every card whatever this build can act on, and a
    verb whose door does not exist renders its explanation and NO submitting
    control.
    """

    def test_all_four_tabs_render_on_a_card(
        self, auth_client, db, seed_user,
    ):
        """The vocabulary is taught by the panel itself."""
        _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        for word in ("MATCH", "ADD", "TRANSFER", "SKIP"):
            assert word in page

    def test_the_shut_verb_says_what_it_waits_for(
        self, auth_client, db, seed_user,
    ):
        """A disabled tab carrying its reason is a disclosure.

        **And it must TEACH THE WORD, not only report the wait** (**R-HW**:
        the panel is where the vocabulary is taught).

        *This graded SKIP as a second shut verb until plan step
        ``bank_import:X-gj-4b``*, which lit it: the sentence it asserted --
        *explains nothing you budget for*, plus *not recorded yet* -- was
        deleted with the constant that held it, because a lit verb saying it
        is not recorded yet would be false. TRANSFER is the one verb left with
        no door, and what the OPEN SKIP tab teaches is asserted below.
        """
        _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        assert "pair a bank line with another of your own accounts" in page
        # **EXACTLY ONE shut tab on this card, and it is TRANSFER.**  A
        # `"not recorded yet" not in page` assertion stood here and could not
        # fail: that string died with `SKIP_WAITS` and exists nowhere in
        # `app/`, so it graded nothing this step could regress (adversarial
        # review 2026-09-04).  Counting the shut class is falsifiable in the
        # direction that matters -- shut SKIP again and this reads 2.
        #
        # **It also pins EXACTLY ONE CARD, and that is load-bearing rather than
        # incidental**: `rec-verb-shut` is emitted once per shut verb, and
        # TRANSFER is shut on every card (`offers_for` passes TRANSFER_WAITS as
        # a literal), so a second card would make this 2 on its own.  The
        # assertion naming "pair a bank line with another of your own
        # accounts" above proves the one shut verb is TRANSFER, since
        # `waiting_for` renders only on the shut arm.
        assert page.count("rec-verb-shut") == 1, (
            "exactly one verb should render shut on this card (TRANSFER); "
            "SKIP has had a door since X-gj-4b"
        )

    def test_the_OPEN_skip_tab_teaches_the_word(
        self, auth_client, db, seed_user,
    ):
        """**R-HW**'s other half: an open verb still teaches its vocabulary.

        A shut tab taught SKIP by explaining what it waits for; a lit one has
        to teach the same word by saying what the act does -- and what it does
        NOT do, which is the half this act is misread without.
        """
        _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)

        assert "explained by nothing" in page, (
            "the SKIP tab does not say what skipping MEANS"
        )
        assert "closes no difference between your books and your bank" in page, (
            "the SKIP tab does not say what skipping does NOT do"
        )

    def test_a_parked_card_offers_no_ONE_CLICK_OK(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-HQ**: a holding state is not inbox work.

        Its sentence opens on TRANSFER, a verb with no door, so a control
        keyed on the SENTENCE would have put a working-looking OK button on
        every parked card payment.

        **It is the BUTTON this is about, not the consent**, and the two were
        one control until plan step ``bank_import:X-gj-1b``.  A parked line's
        ADD is shut by ruling **R-GJ**'s bar and its TRANSFER and SKIP have no
        door, but its MATCH tab is open like any other card's -- the pass's
        unexplained rows are a fact about the PASS -- so the owner may still
        group-match a card payment against the paybacks it covers, and that
        act needs the ``ok`` checkbox in the document.  Gating the checkbox on
        the summary button is what made that act dead in a browser
        (:attr:`LineCard.takes_ok`).
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(auth_client, seed_user, "transfers")

        assert "Capital One Credit Card" in page
        assert f'for="ok-{line.id}">OK<' not in page, (
            "a parked card must not offer the one-click OK its sentence "
            "would otherwise justify"
        )
        # ...and the consent it CAN give, through the tab that has a door.
        assert f'name="ok" value="{line.id}"' in page, (
            "a parked payment must still be group-matchable"
        )


class TestThePanelHasONEFooterAndItCloses:
    """The panel's footer: what this writes, Close, and one verb-named button.

    **ONE band per opened card, and that is what changed.**  The primary
    button was rendered inside every OPEN pane, so a card whose MATCH and ADD
    are both open emitted two footers and two copies of the sentence --
    measured on a restored production clone 2026-08-30: 341 bands over 239
    cards, 102 of them doubled.  The developer ruled the collapse the same
    day.  Which button shows is CSS over the verb radios; that pairing is
    graded structurally in
    ``tests/test_arch/test_a_control_rendered_invisible_has_a_rule_that_shows_it``.
    """

    def test_a_card_with_TWO_open_verbs_still_has_ONE_footer(
        self, auth_client, db, seed_user,
    ):
        """The duplication this collapse removed, asserted over the axis it lived on.

        **The two-open-verb card is staged and then CHECKED for**, because a
        page of one-verb cards satisfies every count here while proving
        nothing: the defect only existed where a card had two.
        """
        _a_swipe_a_rule_files(seed_user, db)

        page = _page(auth_client, seed_user)

        cards = page.count("data-rec-card")
        assert cards, "no card rendered"
        buttons = re.findall(r'class="btn btn-sm btn-primary rec-cta"', page)
        assert len(buttons) > cards, (
            "no card offered two verbs, so this case is not exercising the "
            "shape the collapse was about"
        )
        assert page.count('class="rec-panel-foot"') == cards, (
            f"{page.count('class=\"rec-panel-foot\"')} footers over {cards} "
            f"cards; the footer is per PANE again"
        )
        assert page.count("Nothing is written until you press Apply.") == cards, (
            "the sentence is rendered more than once per card"
        )

    def test_a_card_whose_every_verb_is_SHUT_still_closes(
        self, auth_client, db, seed_user,
    ):
        """The state that tells the panel's footer from a pane's.

        A parked card payment on an account with no unexplained row has no
        open verb at all (:attr:`LineCard.takes_ok` is exactly that question),
        so it offers no button and no sentence -- and its whole footer is
        therefore scripted-only.  A Close living in a pane's footer would be
        missing from the one card whose panel is nothing BUT disclosure.
        """
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(auth_client, seed_user, "transfers")

        assert "Capital One Credit Card" in page
        assert "rec-cta" not in page, (
            "this card was expected to have no open verb; the case no longer "
            "stages the state it is about"
        )
        assert "Nothing is written until you press Apply." not in page, (
            "a card with no act promises that pressing Apply would write it"
        )
        assert page.count("data-rec-close") == 1, (
            "a card with no act renders no Close, so the control lives in a "
            "pane's footer rather than in the panel's"
        )
        assert '<div class="rec-panel-foot" data-rec-scripted hidden>' in page, (
            "the whole band is scripted-only on this card and is not marked"
        )

    def test_every_scripted_only_control_is_rendered_hidden(
        self, auth_client, db, seed_user,
    ):
        """A details element is closed by its own summary with nothing running.

        So Close is a convenience over a control that already works, and it is
        revealed by ``statement_reconcile.js`` -- the same rule the page
        footer's keyboard hints are rendered under.  A Close printed with
        scripting off could not close anything, which is the
        control-that-cannot-succeed shape ruling **R-HW** bounds.

        Asserted over EVERY marked control rather than over this one, and the
        set is checked non-empty first: an assertion quantified over nothing
        is satisfied by a page that renders no control at all.
        """
        _a_swipe_a_rule_files(seed_user, db)

        page = _page(auth_client, seed_user)

        marked = re.findall(r'<[^>]*\bdata-rec-scripted\b[^>]*>', page)
        assert len(marked) >= 2, (
            f"expected the keyboard hints and at least one Close; found "
            f"{len(marked)}"
        )
        assert all("hidden" in tag for tag in marked), (
            f"a scripted-only control is not hidden: "
            f"{[tag for tag in marked if 'hidden' not in tag][:2]}"
        )


class TestAMalformedOpenNeverOutLIVESTheDoor:
    """A reader of the request runs BEFORE the door, or it grades a write.

    Found by adversarial review 2026-09-05.  ``?open=`` was read inside the
    page's context builder, and
    :func:`~app.routes.accounts._statement_doors.run_statement_fragment_door`
    calls that builder AFTER ``db.session.commit()`` -- so a POST carrying a
    malformed ``open`` APPLIED the pass, COMMITTED it, and answered a bare 404
    with no receipt and no way for the owner to know their money had moved.
    """

    def _a_pass_that_really_writes(self, auth_client, seed_user, db):
        """Return the body a browser would submit for one applying card.

        **A faithful scrape**, for the reason :func:`_post` refuses anything
        else: a hand-picked payload is written by the same person as the
        template, so the two agree about a mistake as readily as the truth.

        Returns:
            ``(fields, line)``.
        """
        _envelope, line = _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page) + [("ok", str(line.id))]
        assert any(
            name == f"destination-{line.id}" for name, _ in fields
        ), "the page rendered no destination, so this body writes nothing"
        return fields, line

    def test_a_bad_open_on_the_APPLY_door_writes_nothing(
        self, auth_client, db, seed_user,
    ):
        """The refusal must precede the write, not follow it."""
        fields, _line = self._a_pass_that_really_writes(
            auth_client, seed_user, db,
        )
        before = db.session.query(TransactionEntry).count()

        refused = auth_client.post(
            _url(seed_user["account"].id) + "?open=nonsense",
            data=MultiDict(fields),
        )

        assert refused.status_code == 404
        assert db.session.query(TransactionEntry).count() == before, (
            "a request answered 404 committed a money pass anyway -- the "
            "owner is told nothing and their records moved"
        )

    @pytest.mark.parametrize("spelling", ["0", "-5", "007", "+5"])
    def test_an_open_that_names_no_row_is_the_same_404_before_the_door(
        self, auth_client, db, seed_user, spelling,
    ):
        """``int()`` reads these as ids; nothing names a row with them.

        Plan step ``bank_import:X-gi-2a`` made the reader load-bearing: the
        Apply door grades the open card's fields as a match item, whose
        ``line_ids`` member :class:`~app.schemas.validation._helpers.RowId`
        refuses at ``0`` and ``-5`` -- so a lax ``asked_to_open`` answered
        ``?open=0`` with a 400 over the WHOLE PASS naming a field the body
        never carried, where the query string is this reader's to answer.
        One spelling per id, read through ``parse_row_id``, and the answer
        is the 404 the malformed case above already gets.
        """
        fields, _line = self._a_pass_that_really_writes(
            auth_client, seed_user, db,
        )
        before = db.session.query(TransactionEntry).count()

        refused = auth_client.post(
            _url(seed_user["account"].id) + f"?open={spelling}",
            data=MultiDict(fields),
        )

        assert refused.status_code == 404
        assert db.session.query(TransactionEntry).count() == before

    def test_the_same_body_WITHOUT_the_bad_open_really_writes(
        self, auth_client, db, seed_user,
    ):
        """The control, without which the case above grades a dead payload.

        A 404 over a body that could never have written anything grades the
        404 and not the ORDER, which is the whole finding.
        """
        fields, _line = self._a_pass_that_really_writes(
            auth_client, seed_user, db,
        )
        before = db.session.query(TransactionEntry).count()

        applied = auth_client.post(
            _url(seed_user["account"].id), data=MultiDict(fields),
        )

        assert applied.status_code == 200
        assert db.session.query(TransactionEntry).count() == before + 1, (
            "this payload records nothing even on the happy path, so the "
            "ordering case beside it is quantified over a dead body"
        )
