"""The skipped tab: where a skip is found and undone, its bound wired to the page, and the SKIP
verb pressed from the rendered page.
"""

import re

from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.statement_line_skip import StatementLineSkip
from app.services.statement_match import REGISTER_LIMIT, skip_line
from tests.test_routes._statement_forms import reconcile_form_fields
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import (
    _url,
    _page,
    _choosing,
    _post,
    _unskip_url,
    _a_skipped_line,
    _a_swipe_a_rule_files,
)


def _unskip_control(page):
    """Return the first skip Undo's ``(action, skip_id)``, as the page emits it.

    Scraped rather than composed, which is :func:`_undo_control`'s own rule
    one act over: the action carries the tab in its query string, and a case
    that rebuilt that URL would grade the route while the card pointed
    somewhere else.

    Args:
        page: The rendered page.

    Returns:
        The pair, or ``None`` when the page renders no skip Undo at all.
    """
    found = re.search(
        r'<form method="post" action="([^"]*unskip[^"]*)"'
        r'(?:.|\n)*?name="skip_id" value="(\d+)"',
        page,
    )
    return None if found is None else (
        found.group(1).replace("&amp;", "&"), found.group(2),
    )


def _skipped_tab_count(page):
    """Return the figure the tab bar prints beside "Skipped".

    **Read past the whitespace**, because the alternative couples a behaviour
    assertion to the template's indentation: a reindent of the tab bar would
    fail a case about counts with no count having changed.  The bar renders
    each tab as its label followed by a mono count span, so this finds the
    Skipped label and takes the next one.

    Args:
        page: The rendered page.

    Returns:
        The count as an ``int``.

    Raises:
        AssertionError: When the bar carries no Skipped tab at all, which is a
            different failure from the count being wrong and should not be
            reported as a wrong number.
    """
    found = re.search(
        r'Skipped\s*<span class="rec-tab-count font-mono">(\d+)</span>', page,
    )
    assert found is not None, "the tab bar rendered no Skipped count"
    return int(found.group(1))


class TestTheSkippedTabIsWhereASkipIsFoundAndUndone:
    """Plan step ``bank_import:X-gj-4c-2``; rulings **R-JG**, **R-JH**,
    **R-GY**.

    The locked direction gives this tab in three words -- *the same card with
    Undo* -- and the whole of what it has to be true about is:  the card
    carries the LINE's facts, the Undo actually undoes, the line comes back to
    the inbox, and the press CONFIRMS because it destroys a record.

    **Every case that presses a control reads the page's own bytes and posts
    them back.**  A hand-composed payload is what let a primary act ship DEAD
    in a browser on 31 of 248 cards (plan step ``X-gj-1b``); the control is
    scraped here for that reason.  The one exception is
    :meth:`test_a_skip_that_is_NOT_YOURS_is_refused_by_the_door`, which MUST
    compose by hand: the request it grades is one this page will never render,
    which is the whole point of it.
    """

    def test_the_card_shows_the_BANK_S_own_facts_and_the_past_tense_sentence(
        self, auth_client, db, seed_user,
    ):
        """What a reader scanning this tab sees.

        The merchant, the day, the bank's raw words, the amount and one
        sentence whose first word is the verb -- the same grid the other four
        tabs draw, which is what makes the five one list.
        """
        line, _recorded = _a_skipped_line(seed_user, db)

        page = _page(auth_client, seed_user, "skipped")

        assert "Target" in page
        assert str(line.posted_on) in page
        assert line.description in page
        assert "$9.99" in page
        # **The SENTENCE'S OWN SPANS, not the bare words.**  ``Skipped`` is
        # also the tab bar's label for this tab and ``explained by nothing``
        # is also the page legend's gloss on the SKIP verb, and both are
        # rendered unconditionally on every tab -- so asserting the bare
        # strings graded nothing at all.  Named by adversarial review; the
        # empty-tab case below is the negative control that keeps it honest.
        assert '<span class="rec-ink-verb">Skipped</span>' in page
        assert '<span class="rec-ink-strong">explained by nothing</span>' in (
            page
        )

    def test_pressing_the_rendered_undo_really_deletes_the_skip(
        self, auth_client, db, seed_user,
    ):
        """The round trip: read the page, post ITS bytes, read the database.

        **Ruling R-JG's own shape**: undoing DELETES the row rather than
        answering it, so the assertion is on the table and not on a flag.
        """
        _line, recorded = _a_skipped_line(seed_user, db)
        assert db.session.query(StatementLineSkip).count() == 1

        page = _page(auth_client, seed_user, "skipped")
        action, skip_id = _unskip_control(page)
        response = auth_client.post(
            action, data={"csrf_token": "x", "skip_id": skip_id},
        )
        db.session.expire_all()

        assert int(skip_id) == recorded.skip_id
        assert response.status_code == 302
        assert db.session.query(StatementLineSkip).count() == 0

    def test_the_line_comes_BACK_to_the_inbox_and_leaves_this_tab(
        self, auth_client, db, seed_user,
    ):
        """What the undo is FOR: the question is restored.

        **Both halves, because either alone is satisfiable by a bug.**  A door
        that deleted the row but left the pass stale would empty this tab and
        never re-ask; one that re-asked without deleting would show the line
        twice.  The counts are read off the rendered tab bar, which is what the
        owner actually sees.
        """
        line, _recorded = _a_skipped_line(seed_user, db)
        an_envelope(seed_user)
        db.session.commit()
        before = _page(auth_client, seed_user, "skipped")
        assert _skipped_tab_count(before) == 1

        action, skip_id = _unskip_control(before)
        auth_client.post(
            action, data={"csrf_token": "x", "skip_id": skip_id},
        )
        after = _page(auth_client, seed_user, "skipped")
        inbox = _page(auth_client, seed_user, "to_explain")

        assert _unskip_control(after) is None
        assert "Nothing on this tab." in after
        assert line.description in inbox
        assert _skipped_tab_count(after) == 0

    def test_the_undo_comes_back_to_the_SKIPPED_tab(
        self, auth_client, db, seed_user,
    ):
        """The owner is returned to the page they pressed the control on.

        Redirecting to the bare URL would drop them onto the inbox, which is
        the defect that made the settled tabs' own door take a target at all.
        """
        _a_skipped_line(seed_user, db)

        page = _page(auth_client, seed_user, "skipped")
        action, skip_id = _unskip_control(page)
        response = auth_client.post(
            action, data={"csrf_token": "x", "skip_id": skip_id},
        )

        assert action == _unskip_url(seed_user["account"].id, tab="skipped")
        assert response.headers["Location"] == _url(
            seed_user["account"].id, "skipped",
        )

    def test_every_press_CONFIRMS_and_the_dialog_names_the_destruction(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GY**: a press that destroys a RECORD confirms.

        Undoing a skip moves no money, and that ruling is exactly about
        refusing to treat *moves no money* as *needs no dialog*: what it
        destroys is the owner's own decision, rebuildable only by making it
        again.  **The wording is asserted and not merely the attribute's
        presence**, because a dialog that named the wrong destruction is the
        failure this ruling is made of -- and because the sentence carries the
        one fact about a skip that is easy to misread.
        """
        _a_skipped_line(seed_user, db)

        page = _page(auth_client, seed_user, "skipped")
        dialog = re.search(r'data-confirm="([^"]*)"', page)

        assert dialog is not None, "the skip Undo shipped with no dialog"
        said = dialog.group(1)
        assert "explained by nothing" in said
        assert "back among the ones to explain" in said
        assert "No money moves" in said

    def test_the_RECEIPT_says_what_happened_and_names_no_row_id(
        self, auth_client, db, seed_user,
    ):
        """The flash the door sets, which nothing graded until now.

        **Its sibling's receipt is asserted in three places and this one in
        none**, which adversarial review found: round one removed an
        interpolated ``line_id`` from this sentence on the ground that no bank
        line id is visible anywhere on this screen, and re-adding one would
        have shipped in silence.

        The negative half is the point.  ``budget.bank_statement_lines.id``
        names nothing the owner can see, so the receipt must not quote it --
        and the assertion is that no bare number survives in the flash at all,
        rather than that one particular id is absent, because the id that
        would appear is exactly the one this test knows.
        """
        line, _recorded = _a_skipped_line(seed_user, db)

        page = _page(auth_client, seed_user, "skipped")
        action, skip_id = _unskip_control(page)
        landed = auth_client.post(
            action, data={"csrf_token": "x", "skip_id": skip_id},
            follow_redirects=True,
        ).get_data(as_text=True)
        said = re.search(
            r"Skip undone\.[^<]*", landed,
        )

        assert said is not None, "the undo set no receipt at all"
        assert "waiting to be explained again" in said.group(0)
        assert "closed no difference" in said.group(0)
        assert str(line.id) not in said.group(0)
        assert not re.search(r"\d", said.group(0)), said.group(0)

    def test_it_offers_NO_apply_form_and_no_OK(
        self, auth_client, db, seed_user,
    ):
        """A tab with nothing to Apply renders no band that says otherwise.

        **And it is structural rather than tidiness**: Undo is a form, a form
        cannot nest in a form, so a skip card rendered inside the Apply form
        would have shipped a control the browser drops.  Ruling **R-HW**'s
        rule one surface over -- a control whose submission can never succeed
        is a defect.
        """
        _a_skipped_line(seed_user, db)

        page = _page(auth_client, seed_user, "skipped")

        # **A POSITIVE control first**, so the negatives below are asserted
        # over a tab that really drew a card rather than over an empty one.
        assert "reconcile/unskip" in page
        # **The Apply FORM and its submit**, which are the two things only the
        # bank-line arm emits.  *Three earlier assertions here could not fail
        # and were replaced* (adversarial review): ``apply_statement_reconcile``
        # is an ENDPOINT NAME that ``url_for`` never renders -- the bytes carry
        # the path, which is this page's own GET URL -- ``data-rec-sweep`` is
        # already withheld for every tab but the inbox by ``reconcile_page``,
        # and ``name="ok"`` is unreachable because rendering ``line_card`` over
        # a ``SkipCard`` raises on the undefined ``card.line`` and ``_page``'s
        # own status assertion fires first.
        assert "data-rec-form" not in page
        assert "data-rec-ok-count" not in page

    def test_an_EMPTY_tab_renders_no_card_and_no_undo_prose(
        self, auth_client, db, seed_user,
    ):
        """The empty state, and no paragraph about a control nothing draws.

        A sentence explaining what Undo does, over a tab with no Undo, is the
        *nothing to see here* panel this rebuild removed.
        """
        an_unexplained_outflow(seed_user, merchant="Target")
        db.session.commit()

        page = _page(auth_client, seed_user, "skipped")

        assert "Nothing on this tab." in page
        assert _unskip_control(page) is None
        assert "Undoing a skip" not in page
        # **The NEGATIVE CONTROL for the sentence assertions above.**  The bare
        # words ``Skipped`` and ``explained by nothing`` ARE on this page -- the
        # tab bar's label and the legend -- so a sibling case asserting them
        # would pass here, with no card at all.  The spans are what distinguish
        # a rendered sentence from the furniture, and they are absent.
        assert "Skipped" in page and "explained by nothing" in page
        assert '<span class="rec-ink-verb">Skipped</span>' not in page
        assert '<span class="rec-ink-strong">explained by nothing</span>' not in (
            page
        )

    def test_the_tab_shows_no_other_owner_s_skips(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """The reader's ownership narrowing, asked through the ROUTE.

        The service test grades the query; this grades that the route hands it
        the account it proved, over a second owner who really has a skip.
        """
        theirs = a_bank_line(
            seed_second_user, an_import(seed_second_user),
            description="SOMEONE ELSES SWIPE",
        )
        db.session.commit()
        skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        db.session.commit()

        page = _page(auth_client, seed_user, "skipped")

        assert "SOMEONE ELSES SWIPE" not in page
        assert _unskip_control(page) is None

    def test_a_skip_that_is_NOT_YOURS_is_refused_by_the_door(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """The door's own refusal, past the page that would never offer it.

        The tab cannot render another owner's skip, so the only way to submit
        one is by hand -- which is exactly the request this must refuse.  A
        redirect with a flash and the row STILL THERE is the answer;  a 500
        from an ``IntegrityError`` would not be.
        """
        theirs = a_bank_line(seed_second_user, an_import(seed_second_user))
        db.session.commit()
        recorded = skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        db.session.commit()

        response = auth_client.post(
            _unskip_url(seed_user["account"].id, tab="skipped"),
            data={"csrf_token": "x", "skip_id": recorded.skip_id},
        )
        db.session.expire_all()

        assert response.status_code == 302
        assert db.session.get(StatementLineSkip, recorded.skip_id) is not None


class TestTheSkippedBoundIsWiredToThePage:
    """Ruling **bank_import:R-GX**'s bound, on the tab that got it last
    (**R-JW**).

    ``test_skipping`` grades the reader's arithmetic; this grades that the
    PAGE threads the parameter, that the link past the bound is rendered and
    routes, and that an undo pressed while the bound is lifted comes back to
    the lifted view rather than collapsing the record under the reader.
    """

    def _many_skips(self, seed_user, db, how_many):
        """Record *how_many* skips on the seeded account.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            how_many: How many lines to stage and skip.

        Returns:
            Nothing; the rows are recorded and committed.
        """
        owner_id, account_id = (
            seed_user["user"].id, seed_user["account"].id,
        )
        for index in range(how_many):
            line = an_unexplained_outflow(
                seed_user, merchant=f"Shop {index}", amount="-1.00",
                sequence=index,
            )
            db.session.flush()
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

    def test_it_cuts_says_so_and_the_link_shows_the_rest(
        self, auth_client, db, seed_user,
    ):
        """One skip past the bound: the cut, the sentence, and the way past.

        **The caption must NOT move**, which is the half a bound is easiest to
        get wrong: the tab bar states what the account holds and the list
        states what it drew, and lifting the bound changes only the second.
        """
        self._many_skips(seed_user, db, REGISTER_LIMIT + 1)

        bounded = _page(auth_client, seed_user, "skipped")
        everything = auth_client.get(
            _url(seed_user["account"].id, "skipped") + "&all=1"
        ).get_data(as_text=True)

        assert bounded.count('name="skip_id"') == REGISTER_LIMIT
        # **Whitespace-NORMALISED**, which is the idiom the settled tabs' own
        # bound test already uses.  This read the raw bytes with their exact
        # indentation until the *show the other N* paragraph became a shared
        # macro, at which point a behaviour test failed over a reindent with
        # no behaviour changed -- the coupling an adversarial review had
        # already named on this file's tab-count assertion.
        assert "the other 1 skip(s)" in " ".join(bounded.split())
        assert everything.count('name="skip_id"') == REGISTER_LIMIT + 1
        assert "Every skip on this tab is listed." in everything
        assert "skip(s)</a>" not in everything
        for page in (bounded, everything):
            assert _skipped_tab_count(page) == REGISTER_LIMIT + 1

    def test_the_undo_KEEPS_the_lifted_view(self, auth_client, db, seed_user):
        """An undo pressed while showing everything answers with everything.

        Without this the record collapses to the bounded list under a reader
        mid-scroll, which is the defect that made ``release_and_return`` take
        a view at all -- and the reason the docstring claiming this door
        "could not honour ``all``" was wrong.
        """
        self._many_skips(seed_user, db, REGISTER_LIMIT + 1)

        everything = auth_client.get(
            _url(seed_user["account"].id, "skipped") + "&all=1"
        ).get_data(as_text=True)
        action, skip_id = _unskip_control(everything)
        response = auth_client.post(
            action, data={"csrf_token": "x", "skip_id": skip_id},
        )

        assert action == _unskip_url(
            seed_user["account"].id, tab="skipped", show_all=True,
        )
        assert response.headers["Location"] == (
            _url(seed_user["account"].id, "skipped") + "&all=1"
        )

    def test_an_UNBOUNDED_tab_offers_no_link_at_all(
        self, auth_client, db, seed_user,
    ):
        """Below the bound there is nothing to show, so nothing is offered.

        A *show the other 0* link is the affordance-that-cannot-succeed shape
        ruling **R-HW** bounds, and a bound that always announced itself would
        be one on every account this owner actually has.
        """
        self._many_skips(seed_user, db, 2)

        page = _page(auth_client, seed_user, "skipped")

        assert page.count('name="skip_id"') == 2
        assert "skip(s)</a>" not in page
        assert "Every skip on this tab is listed." not in page


class TestTheSKIPVerbIsPressableFromTheRenderedPage:
    """Plan step ``bank_import:X-gj-4b``, rulings **R-HW** and **R-JG**.

    The loop the service tests cannot close: a card's SKIP radio, the schema's
    ``skips`` list and ``skip_line``'s door have no compile-time relationship,
    so this scrapes the page and posts exactly what a browser would.

    **The verb radio is the act**, which is what makes the scrape meaningful
    here: the four tabs are one radio group per card, so leaving a card on
    SKIP and ticking OK is the whole submission -- there is no destination and
    no row list to pick, because a skip takes no argument.
    """

    def test_the_page_RENDERS_a_selectable_skip_radio(
        self, auth_client, db, seed_user,
    ):
        """FIRING CONTROL for the two cases below.

        Both post ``verb-<line>=skip`` through :func:`_choosing`, which is what
        clicking the SKIP tab does; if the template stopped rendering that
        radio they would be hand-picked payloads no browser could produce --
        the defect ``_post`` exists to refuse, and the one that left a primary
        control dead on 31 of 248 cards.

        **It asserts on the DOCUMENT and not on the scrape**, and the
        distinction is the whole reason this control is written down: a
        browser submits only the CHECKED member of a radio group, so ``skip``
        is legitimately absent from a scrape of a card that opens on ADD.  A
        first draft asserted the pair was in the scraped fields and failed
        against a page that renders the tab perfectly well.
        """
        _, line = _a_swipe_a_rule_files(seed_user, db)

        page = _page(auth_client, seed_user)

        assert f'id="verb-{line.id}-skip"' in page, (
            "the card renders no SKIP tab, so nothing below posts what a "
            "browser would"
        )
        assert (f"verb-{line.id}", "add") in reconcile_form_fields(page), (
            "the card does not open on ADD, so switching to SKIP is not what "
            "the two cases below are simulating"
        )

    def test_OK_ING_a_card_on_SKIP_records_the_decision(
        self, auth_client, db, seed_user,
    ):
        """The press, end to end, and it writes exactly one table."""
        _, line = _a_swipe_a_rule_files(seed_user, db)
        transactions_before = db.session.query(Transaction).count()
        page = _page(auth_client, seed_user)
        fields = _choosing(
            reconcile_form_fields(page), f"verb-{line.id}", "skip",
        )

        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        skip = db.session.query(StatementLineSkip).one()
        assert skip.bank_statement_line_id == line.id
        # **It moved no money**, which is the whole of what makes this act
        # safe: no purchase, no match, no row of any kind.
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(Transaction).count() == transactions_before

    def test_the_line_LEAVES_the_inbox_and_arrives_on_the_Skipped_tab(
        self, auth_client, db, seed_user,
    ):
        """The consequence the owner sees, asserted on both surfaces.

        A skip nothing reads is a line that comes back on the next visit,
        which is what the store was built to stop.
        """
        _, line = _a_swipe_a_rule_files(seed_user, db)
        page = _page(auth_client, seed_user)
        fields = _choosing(
            reconcile_form_fields(page), f"verb-{line.id}", "skip",
        )
        _post(auth_client, seed_user, fields + [("ok", str(line.id))], page)

        inbox = _page(auth_client, seed_user)
        assert f"verb-{line.id}" not in inbox, (
            "the skipped line is still being asked about on the inbox"
        )
        assert _skipped_tab_count(inbox) == 1
