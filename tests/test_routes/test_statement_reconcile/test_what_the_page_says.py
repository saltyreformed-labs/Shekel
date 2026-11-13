"""What the page says: the hero's last import and how far the records run, the card's bank words, a
holding chip's link, the books bound, and the two sentences above an ADD's OK.
"""

import re
from datetime import date, datetime, timezone

from app.enums import StatementBalanceEvidenceEnum
from tests.test_services.test_outstanding_difference import _opened_on, _settle
from tests.test_services.test_statement_import.test_anchor import _seed_import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_account_whose_books_hide_a_line,
    a_rule,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_by,
)

from ._helpers import _CARD_PAYMENT, _url, _page


def _provenance(page):
    """Return the hero's provenance line as one plain sentence, or ``None``.

    **Read as a SENTENCE rather than asserted on in fragments**, because the
    whole of what this line claims is the order and the words between the
    numbers: *2 lines recorded* and *1 filed by rules* are two figures that
    only mean anything against each other, and a test matching each number in
    isolation would pass with them transposed.

    Args:
        page: The rendered page.

    Returns:
        The line's text with its tags removed, its ``&middot;`` separators
        written as ``-`` and its whitespace collapsed; or ``None`` when the
        page renders no such line at all.
    """
    found = re.search(
        r'<p class="rec-provenance[^"]*">(.*?)</p>', page, re.S,
    )
    if found is None:
        return None
    return " ".join(
        re.sub(r"<[^>]+>", " ", found.group(1))
        .replace("&middot;", "-")
        .split()
    )


class TestTheHeroSaysWhatTheLastImportDid:
    """The provenance line the locked direction prints right of the figures.

    *Last import <day> - N lines recorded - N filed by rules - receipt*, so
    that a routine session reads *import, read the receipt, work the inbox,
    see the difference reach zero*.  It answers *is what I am looking at
    current*, which none of the four hero figures does.
    """

    def test_it_reads_as_the_sentence_the_direction_names(
        self, auth_client, db, seed_user,
    ):
        """One import, two lines recorded, one of them filed by a rule."""
        statement = an_import(
            seed_user,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        by_rule = a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Amazon)",
            merchant="Amazon", sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, amount="-12.34",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Walmart)",
            merchant="Walmart", sequence_in_group=1,
        )
        db.session.commit()
        filed_by(seed_user, by_rule, envelope, by_rule=True)
        db.session.commit()

        said = _provenance(_page(auth_client, seed_user))

        assert said == (
            "Last import Aug 24, 2026 - 2 lines recorded - "
            "1 filed by rules - receipt"
        ), said

    def test_the_day_is_the_owner_s_and_not_UTC_s(
        self, auth_client, db, seed_user,
    ):
        """An evening import may not be reported as tomorrow's work.

        ``created_at`` is a stored UTC instant and the owner reads Eastern, so
        an import performed at 21:00 on 2026-08-30 is stored at 01:00 on the
        31st.  Truncating in the service would have printed the 31st; the
        conversion is ``local_datetime``'s, which is this project's standing
        rule for every ``timestamptz`` it renders.
        """
        an_import(
            seed_user,
            created_at=datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        said = _provenance(_page(auth_client, seed_user))

        assert "Aug 30, 2026" in said, said
        assert "Aug 31, 2026" not in said, said

    def test_an_account_nobody_has_imported_into_prints_NO_line(
        self, auth_client, db, seed_user,
    ):
        """The whole line or none of it.

        *Last import -- 0 lines recorded* over an account that has never been
        imported into would state an act that never happened, which is the
        fabricated-figure shape this arc keeps deleting.
        """
        page = _page(auth_client, seed_user)

        # TWO predicates, because ``_provenance`` answers ``None`` both for a
        # page that renders no such line and for a reader that has stopped
        # matching the markup.  The second is independent of the first.
        assert "rec-provenance" not in page
        assert _provenance(page) is None

    def test_the_receipt_LINK_names_an_anchor_the_statements_page_HAS(
        self, auth_client, db, seed_user,
    ):
        """The link and its target are asserted as a PAIR.

        A link to an anchor nothing declares scrolls to the top of the page
        and looks exactly like one that works -- the same indistinguishability
        that lets a moved route leave its ownership 404 guarding nothing.  So
        the case renders BOTH surfaces: the href here, and the ``id`` there.
        """
        statement = an_import(
            seed_user,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Amazon)",
            merchant="Amazon", sequence_in_group=0,
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=True)
        db.session.commit()
        account_id = seed_user["account"].id

        page = _page(auth_client, seed_user)
        statements = auth_client.get(
            f"/accounts/{account_id}/statements",
        ).get_data(as_text=True)

        assert (
            f'href="/accounts/{account_id}/statements#filed-by-rules"'
            in page
        ), "the provenance line does not link to the rule receipt"
        assert 'id="filed-by-rules"' in statements, (
            "the statements page declares no such anchor, so the link the "
            "hero renders lands at the top of the page instead of on the "
            "receipt it names"
        )


class TestTheCardCarriesTheBanksOwnWords:
    """Both of them, in ONE element: the raw description and the bank's category.

    The locked direction's card puts them together under the merchant, in mono
    muted small.  They are PROVENANCE -- nothing on this page decides on
    either, and the one decision this package makes on a bank's category is
    ``_vocabulary``'s, asked in SQL against that adapter's own vocabulary.

    **This was recorded as impossible and was not.**  The handoff for this step
    said ``BankLine`` carried no such field "so it needs a model change";
    ``bank_statement_lines.source_category`` has existed since the importer
    shipped, and it was the SERVICE value that dropped it -- one field and one
    construction site.

    **The assertions are on the ELEMENT and not on the page**, because "the
    card puts them together" is a containment claim: two ``in page`` checks
    pass just as happily with the two facts in different corners of the screen.
    """

    def _raw_line(self, page):
        """Return the text of the card's raw-facts element.

        Args:
            page: The rendered page.

        Returns:
            The inner text of the one ``rec-raw`` div, or ``None``.

        Raises:
            AssertionError: When the page renders more than one, which would
                make "the" element a fiction and every assertion below
                ambiguous.
        """
        found = re.findall(
            r'<div class="rec-raw font-mono">(.*?)</div>', page, re.S,
        )
        assert len(found) <= 1, (
            f"{len(found)} cards rendered; this case reads THE raw line and "
            f"needs exactly one"
        )
        return found[0].strip() if found else None

    def test_the_two_facts_are_in_ONE_element_in_the_direction_s_order(
        self, auth_client, db, seed_user,
    ):
        """Description first, then what the bank filed it under."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72",
            source_category="Merchandise/Home Improvement",
        )
        db.session.commit()

        raw = self._raw_line(_page(auth_client, seed_user))

        assert raw is not None, "no card rendered its raw facts at all"
        assert raw.endswith("Merchandise/Home Improvement"), (
            f"the bank's category is not on the card's raw line: {raw!r}"
        )
        assert raw.startswith("POINT OF SALE DEBIT"), (
            f"the raw description went with it; the two are one line: {raw!r}"
        )

    def test_a_source_that_files_NOTHING_leaves_the_line_at_the_description(
        self, auth_client, db, seed_user,
    ):
        """An absent fact renders as nothing, not as a separator or a gap.

        ``bank_statement_lines.source_category`` is NULLABLE and the arm is
        for an adapter that states none.  It is not today's data: the one
        adapter registered (SECU) states a category on every line it parses --
        **378 of 378 recorded lines on the developer's own account**, measured
        2026-08-30 on a restored production clone.  So this case stages the
        state rather than sampling it.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
        db.session.commit()

        raw = self._raw_line(_page(auth_client, seed_user))

        assert raw is not None
        assert raw.startswith("POINT OF SALE DEBIT")
        assert raw.endswith(")"), (
            f"the raw line ends past its own description, so something was "
            f"rendered for a category the line does not have: {raw!r}"
        )


class TestAHoldingChipLeadsToTheTabThatHoldsItsLines:
    """Ruling **R-HQ**: what is not work is a count with a way in.

    Plan step ``bank_import:X-gj-1c`` rewrote ``_chip_href`` from three arms to
    one -- every chip now names a tab this page serves, or names none -- and
    adversarial test-quality review measured that no route case followed a chip
    at all: making the builder return ``None`` for every chip left 490 route
    tests green.  So this follows the href and checks the tab it lands on holds
    what the chip counted.
    """

    def test_the_transfers_chip_leads_there_and_the_count_agrees(
        self, auth_client, db, seed_user,
    ):
        """A card payment is a holding state with a tab (**R-GJ**, **R-HQ**)."""
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-793.23",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEBIT CAPITAL ONE MOBILE PMT",
            merchant="Capital One Credit Card",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(auth_client, seed_user)
        chip = re.search(
            r'<a class="rec-chip" href="([^"]+)">\s*'
            r'<span class="rec-chip-count font-mono">(\d+)</span>\s*'
            r'([^<]+?)\s*<',
            page,
        )

        assert chip is not None, "no chip offered a way in"
        href, count, label = (
            chip.group(1).replace("&amp;", "&"), chip.group(2), chip.group(3)
        )
        assert label == "waiting for the account they paid"
        landed = auth_client.get(href)

        assert landed.status_code == 200
        body = landed.get_data(as_text=True)
        assert body.count("data-rec-card") == int(count) == 1
        assert 'aria-current="page"' in body
        assert f'tab=transfers"\n       aria-current' in body or (
            "rec-tab rec-tab-open" in body
        )


class TestTheBooksBoundIsRENDERED:
    """The owner-visible half of ``balance:X-f3c-2b-2b``, on this surface.

    **This class exists because the whole of it was invisible to the suite.**
    An adversarial test-quality review disabled the three ``{% if %}`` blocks
    that render the bound across the three surfaces and measured the FULL
    suite still green at 12,206 -- every assertion was against the service
    value, and none against a page.  The anchor especially exists ONLY in
    Jinja: a URL is the one fact the service may not build, so nothing below
    the template can grade it.
    """

    def test_the_page_states_the_bound_and_links_the_restatement_door(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL for the rendering, the figure and the href."""
        account, day = an_account_whose_books_hide_a_line(
            db, seed_user, seed_periods,
        )

        body = auth_client.get(_url(account.id)).data.decode()

        # **No apostrophes in any of these.**  Jinja escapes ``'`` to
        # ``&#39;``, so asserting the service's own sentence verbatim can
        # never match a rendered page -- a control that fails for the wrong
        # reason is not a control.  The existing pay-calendar precedent in
        # this suite avoids them for the same reason.
        assert "already inside this account" in body
        assert "opening balance of" in body
        assert "$689.16" in body
        assert day.strftime("%b %-d, %Y") in body
        assert f"/accounts/{account.id}/edit#books-opening" in body
        assert "Restate this account" in body


class TestTheADDSentenceSaysWhichWayTheMoneyWENT:
    """The words printed directly above the OK that files a purchase.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A merchant
    credit files as a NEGATIVE purchase back into the container the owner's
    rule names, and until this class the card said *"Add records this as
    spending your budget did not have"* over it -- describing a REFUND as
    spending, on the control that moves the money.

    **This is the shape ruling R-GJ measured `$7,412.94` going through**: a
    paragraph that mis-describes a working control.  Nothing graded it,
    because the two sentences live in ``_statement_reconcile_macros.html``
    rather than in :mod:`~app.services.statement_match._sentence`, and this
    package's prose cases all read that module.

    **Both directions, and each asserts the ABSENCE of the other**, because
    the template renders the pair with no ``else``: a builder that answered
    ``records_a_refund`` for every purchase would print the refund sentence
    over an ordinary swipe and a one-sided case would not see it.
    """

    _REFUND_WORDS = "refund back into a budget line"
    _CHARGE_WORDS = "spending your budget did not have"

    def _rendered(self, auth_client, seed_user, db, *, amount, merchant):
        """Return the Reconcile page with one answered line of *amount* on it."""
        envelope = an_envelope(seed_user, name="Home Improvement")
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount=amount, merchant=merchant,
            posted_on=seed_user["bootstrap_period"].start_date,
            description=f"POINT OF SALE L340 ({merchant})",
        )
        db.session.commit()
        a_rule(seed_user, merchant, template_id=envelope.template_id)
        db.session.commit()
        return _page(auth_client, seed_user)

    def test_a_REFUND_card_says_it_lowers_what_a_budget_line_has_cost(
        self, app, db, auth_client, seed_user,
    ):
        """Money ARRIVING that the owner's own rule claims."""
        with app.app_context():
            page = self._rendered(
                auth_client, seed_user, db,
                amount="28.29", merchant="Amazon",
            )

            assert self._REFUND_WORDS in page
            assert self._CHARGE_WORDS not in page

    def test_an_ORDINARY_swipe_card_still_says_it_is_spending(
        self, app, db, auth_client, seed_user,
    ):
        """The control, so the sentence is chosen and not hard-coded."""
        with app.app_context():
            page = self._rendered(
                auth_client, seed_user, db,
                amount="-35.72", merchant="Lowe's",
            )

            assert self._CHARGE_WORDS in page
            assert self._REFUND_WORDS not in page


class TestTheBooksAlreadyHoldSentenceIsONESpelling:
    """The double-count warning, on the surface that had no case for it.

    Plan step ``bank_import:X-gj-2b-3``.  This sentence was written TWICE --
    once in ``_statement_review_body.html`` and once in
    ``_statement_reconcile_macros.html`` -- and only the first was graded, by
    one case in ``test_statement_matches.py``.  Both said *N income row(s)*
    about a set filtered on ``cash_amount > 0`` over ``unmatched_rows``, which
    holds PURCHASE rows: a stored REFUND is a positive-cash row there since
    ruling **bank_import:R-II**.  The SERVICE-composed twin
    (``ArrivalsAlreadyHeld.why_it_could_double_count``) was corrected at plan
    step ``bank_import:X-gj-2b`` and these were not, which is the drift one
    composer exists to prevent.

    Both are now the shared ``books_already_hold`` macro, and this case is what
    makes that claim checkable on the second surface.
    """

    _SAID = (
        "This pay period already holds 1 row(s) totalling $2,473.38 your "
        "records say arrived and no bank line explains"
    )

    def test_the_reconcile_card_prints_the_shared_sentence(
        self, app, db, auth_client, seed_user,
    ):
        """A deposit whose period already holds a salary row nothing explains.

        The developer's own shape: three payroll deposits worth `$7,838.92`
        each sat in a period holding a `$2,473.38` salary row, and this is the
        only per-line signal any of them got.
        """
        with app.app_context():
            a_transaction(
                seed_user, name="Salary", amount="2473.38", income=True,
            )
            a_bank_line(
                seed_user, an_import(seed_user), amount="2600.00",
                posted_on=seed_user["bootstrap_period"].start_date,
                description="ACH CREDIT PAYROLL", merchant="Some Employer",
            )
            db.session.commit()

            page = " ".join(_page(auth_client, seed_user).split())

            assert self._SAID in page
            assert "Salary $2,473.38" in page, (
                "the rows are NAMED, so the owner can find them"
            )
            assert "income row(s)" not in page, (
                "the set holds a stored refund too, whose cash is positive"
            )

    def test_a_deposit_too_SMALL_to_be_them_gets_no_sentence(
        self, app, db, auth_client, seed_user,
    ):
        """The control: an alarm on every row teaches an owner to ignore them.

        A `$0.15` dividend cannot be any subset of a `$2,473.38` row, every
        member being positive -- so the macro is not reached, and without this
        case the assertion above would pass on a page that printed the
        sentence unconditionally.
        """
        with app.app_context():
            a_transaction(
                seed_user, name="Salary", amount="2473.38", income=True,
            )
            a_bank_line(
                seed_user, an_import(seed_user), amount="0.15",
                posted_on=seed_user["bootstrap_period"].start_date,
                description="DIVIDEND EARNED", merchant="Dividend Earned",
            )
            db.session.commit()

            page = " ".join(_page(auth_client, seed_user).split())

            assert "This pay period already holds" not in page


class TestTheHeroSaysHowFarTheRecordsRun:
    """Finding **BAL-651**, ruling plan#2346, through a real render.

    On the books and statement ruling **R-BAL286**'s service test reaches
    Done with (``test_reconcile.test_the_hero``'s
    ``test_the_statement_the_opening_was_TYPED_FROM_can_be_done``): an
    account opened Feb 2 at ``$500.00`` from a statement stating ``$500.00``
    that day, both its lines inside the opening, so nothing is off and
    nothing waits to be explained.  A row settled after the statement is
    then the one difference between the page that reads Done and the page
    that refuses it.
    """

    #: The page's Done line, which only a Done page renders.
    _DONE = "Done: your books and your bank agree"

    def _body(self, auth_client, db, seed_user, seed_periods, late):
        """Render the Reconcile page for the fixture, a row settled on *late*.

        Args:
            auth_client: The logged-in client.
            db: The session fixture.
            seed_user: The seeded user bundle.
            seed_periods: The seeded pay periods.
            late: The day a ``$40.00`` row settles after the statement, or
                ``None`` for no such row.

        Returns:
            The rendered page, as text.
        """
        account = _opened_on(seed_user, date(2026, 2, 2))
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 2, 2),
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
            lines=[(date(2026, 1, 30), "-25.00"),
                   (date(2026, 2, 2), "-10.00")],
        )
        if late is not None:
            _settle(seed_user, seed_periods[4], "40.00", late,
                    account=account)
        db.session.commit()
        response = auth_client.get(_url(account.id))
        assert response.status_code == 200, (
            f"the page did not render: {response.status_code}"
        )
        return response.get_data(as_text=True)

    def test_with_no_record_past_the_statement_the_page_reads_DONE(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """The control: the same books and statement, nothing after Feb 2."""
        body = self._body(auth_client, db, seed_user, seed_periods, None)

        assert self._DONE in body
        assert "This comparison is priced through" not in body

    def test_records_past_the_last_statement_refuse_DONE_and_are_named(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """The page says how far it is priced and how far the records run.

        A row settles Feb 15, so the comparison is priced through Feb 2
        while the records run through Feb 15: Feb 3 through Feb 15 is 13
        unpriced days, and the page that read Done without it does not.
        """
        body = self._body(
            auth_client, db, seed_user, seed_periods, date(2026, 2, 15),
        )

        assert self._DONE not in body, "the page read Done over Feb 3 - Feb 15"
        assert re.search(
            r"This comparison is priced through 2026-02-02,\s+and your records"
            r" run through 2026-02-15;\s+13 later day\(s\)",
            body,
        ), "the hero did not name how far the records run"
