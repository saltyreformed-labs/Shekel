"""The MATCH pane with no script: it is reachable, its path records the match, and a refused Apply
keeps the owner's ticks.
"""

import re
from datetime import timedelta
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services.row_valuation import settled_figure
from app.models.statement_line_skip import StatementLineSkip
from tests.test_routes._statement_forms import reconcile_form_fields
from tests._test_helpers import open_owner_calendar
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_transaction,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import (
    _CARD_PAYMENT,
    _url,
    _match_url,
    _page,
    _choosing,
    _post,
    _row_tokens,
    _consent_options,
    _TestThePaneOffersWHEREADifferenceGoesMembers,
    _open,
    _card_markup,
)


class _TestTheMatchPaneIsReachableWithNoScriptMembers:
    """The staging ``TestTheScriptlessPathRECORDSTheMatch`` and
    ``TestARefusedApplyKeepsTheOwnersTicks`` read as well."""

    def _a_card_payment_and_a_payback_another_period_holds(
        self, seed_user, db,
    ):
        """Stage the class ruling **R-KA** exists for.

        A parked card payment, one unexplained row in the line's own pay
        period and one in an EARLIER period -- which is the shape
        :mod:`~app.services.statement_match._panel` measures on the
        developer's own Checking: all 9 of his 9 card payments have payback
        rows their own period does not hold.  The two paybacks come to the
        payment exactly, which is the group a scriptless owner has to be able
        to build.

        **A SECOND recorded line is staged and it is load-bearing.**  The
        pass's ``unmatched_rows`` is already bounded to the span the recorded
        lines cover (``_reads._could_have_been_shown``), so without a line in
        the earlier period the row there is not offered by ANY surface and
        this class would be grading the bound rather than the reach.

        Returns:
            ``(line, the_earlier_row_name)``.
        """
        later = open_owner_calendar(
            seed_user["user"].id,
            seed_user["bootstrap_period"].start_date + timedelta(days=14),
            num_periods=2,
        )[1]
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-793.23",
            posted_on=later.start_date,
            description="POINT OF SALE DEBIT L340 THING (Capital One)",
            merchant="Capital One Credit Card",
            source_category=_CARD_PAYMENT,
        )
        a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 THING (Amazon)",
            merchant="Amazon", sequence_in_group=1,
        )
        a_transaction(
            seed_user, name="Payback in this period", amount="93.23",
            period=later,
        )
        a_transaction(seed_user, name="Payback an earlier period",
                      amount="700.00")
        db.session.commit()
        return line, "Payback an earlier period"


class TestTheMatchPaneIsReachableWithNoScript(
    _TestTheMatchPaneIsReachableWithNoScriptMembers,
):
    """Plan step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**.

    The MATCH pane is a fragment fetched on ``intersect once``, which never
    fires with scripting off -- so the tab was a permanent spinner, and a
    parked card payment (ADD barred by **R-GJ**, TRANSFER doorless, SKIP shut
    by **R-JI**) had no reachable act at all once
    ``bank_import:X-gi-2`` deletes the workbench its ``<noscript>`` pointed
    at.  ``?open=<line_id>`` puts that one card's rows in the document.

    **Every case here reads the rendered PAGE**, because what this step moved
    is which bytes a browser with no JavaScript receives, and no service test
    one tier down can see that.
    """

    def test_the_noscript_names_this_page_this_tab_and_this_card(
        self, auth_client, db, seed_user,
    ):
        """The link that replaced the workbench's.

        **The TAB travels with it**, and that is not decoration: a parked card
        payment is on Transfers, so a link carrying only the line would open
        the inbox -- a page its own card is not on.
        """
        line, _ = self._a_card_payment_and_a_payback_another_period_holds(
            seed_user, db,
        )

        page = _page(auth_client, seed_user, "transfers")

        account_id = seed_user["account"].id
        assert (
            f'href="/accounts/{account_id}/statements/reconcile'
            f'?tab=transfers&amp;open={line.id}#rec-card-{line.id}"'
        ) in page, "the scriptless way into the MATCH pane is not on the card"
        assert f"/accounts/{account_id}/statements/match" not in page, (
            "the card still sends a scriptless owner to the workbench, which "
            "bank_import:X-gi-2 deletes"
        )

    def test_open_puts_the_rows_in_the_document_and_no_spinner(
        self, auth_client, db, seed_user,
    ):
        """What ``?open=`` renders instead of the placeholder."""
        line, _ = self._a_card_payment_and_a_payback_another_period_holds(
            seed_user, db,
        )

        page = _open(auth_client, seed_user, line.id, tab="transfers")

        assert f'id="rec-match-{line.id}"' in page, (
            "the pane itself is not in the document"
        )
        assert "Payback in this period" in page
        # **Scoped to THIS card's own markup**, not asserted page-wide: a
        # second card on the tab renders a placeholder legitimately, and a
        # page-wide absence would fail for a reason unrelated to what this
        # grades (adversarial review 2026-09-05).
        card = _card_markup(page, line.id)
        assert "rec-match-waiting" not in card, (
            "the opened card still renders the spinner placeholder"
        )

    def test_it_reaches_a_row_the_lines_own_period_does_not_hold(
        self, auth_client, db, seed_user,
    ):
        """The developer's ruling of 2026-09-05, and the whole of it.

        The live fragment opens on the line's own pay period because the
        SEARCH widens it.  This render has no search -- nothing re-fetches
        with scripting off -- so a period-bounded list would be a bound the
        owner cannot widen, which is the cap finding **N-374** refused, and it
        would strand exactly the class **R-KA** exists to give an act back.

        **The fragment's own answer is asserted FIRST**, because without it
        this case cannot tell "the page reaches further" from "the fixture put
        both rows in one period" -- and the second reads as a pass.
        """
        line, later_row = self._a_card_payment_and_a_payback_another_period_holds(
            seed_user, db,
        )

        fragment = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        assert "Payback in this period" in fragment
        assert later_row not in fragment, (
            "the fixture did not put the second row outside the line's own "
            "pay period, so this case cannot grade the widening"
        )

        page = _open(auth_client, seed_user, line.id, tab="transfers")

        assert later_row in page, (
            "the scriptless render is bounded to the line's own pay period, "
            "which is the bound the owner has no way to widen"
        )

    def test_the_placeholder_and_the_pane_never_coexist(
        self, auth_client, db, seed_user,
    ):
        """One ``rows-<line>`` copy in the form at every moment.

        The placeholder carries a proposal's rows as HIDDEN inputs and the
        pane carries them as tickboxes.  Rendering both would submit each row
        TWICE, which the accept door reads as one group naming a row it does
        not hold -- so the two arms are exclusive rather than merely tidy.
        """
        line = self._a_proposed_card(seed_user, db)

        page = _open(auth_client, seed_user, line.id)

        tokens = re.findall(rf'name="rows-{line.id}" value="([^"]+)"', page)
        assert tokens, "the opened card offered no rows at all"
        assert len(tokens) == len(set(tokens)), (
            f"a row is in the form twice: {tokens}"
        )
        assert f'type="hidden" name="rows-{line.id}"' not in page, (
            "the placeholder's hidden rows render beside the pane's tickboxes"
        )

    def _a_proposed_card(self, seed_user, db):
        """Stage one bank line a tier proposes an exact match for.

        Returns:
            The staged bank line.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2573.42", income=True,
        )
        db.session.commit()
        return line

    def test_an_opened_proposal_still_states_the_difference_it_offers(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-IA**: every match states the figure it was reviewed at.

        The unopened card carries that figure as a hidden ``consent-<line>``;
        the pane carries it as the consent control.  Opening the card replaces
        one with the other, so a card opened this way has to be priced against
        the proposal's OWN rows -- a pane that opened empty would report
        `$0.00` for a proposal the card is offering to apply, and the door
        would then refuse the press.
        """
        line = self._a_proposed_card(seed_user, db)

        page = _open(auth_client, seed_user, line.id)

        assert f'name="consent-{line.id}"' in page, (
            "the opened card submits no reviewed difference at all"
        )
        assert "These add up. Nothing is left over." in page, (
            "an exact proposal was not priced when its card was opened"
        )
        assert page.count(f'name="consent-{line.id}"') == 1, (
            "the figure travels twice, so which one the door reads is the "
            "browser's choice"
        )

    def test_a_stale_open_renders_the_page_with_no_pane(
        self, auth_client, db, seed_user,
    ):
        """A line the pass renders no card for is not an error.

        The owner applies the card and the door answers with the query string
        it was pressed under, so ``?open=`` naming nothing is the ordinary end
        of the flow rather than a forged request.
        """
        an_unexplained_outflow(seed_user, merchant="Amazon")
        a_transaction(seed_user, name="Electricity bill", amount="57.96")
        db.session.commit()

        page = _open(auth_client, seed_user, 999_999)

        assert "rec-match-waiting" in page, (
            "the page did not fall back to the ordinary placeholder"
        )
        assert 'id="rec-match-999999"' not in page

    def test_open_naming_ANOTHER_OWNERS_line_renders_no_pane_and_leaks_nothing(
        self, auth_client, db, seed_user, second_user,
    ):
        """The ownership control, PAIRED with the URL still routing.

        ``card_subject``'s own docstring cites
        ``feedback_a_moved_door_disarms_its_ownership_control`` about exactly
        this: "not found" and "not yours" are one answer here, so nothing
        distinguishes a control that holds from a route that moved.  Both
        reviews of 2026-09-05 said the new query argument had no such case.
        """
        an_unexplained_outflow(seed_user, merchant="Amazon")
        # **Its figure matches no line**, so no tier proposes a match for it
        # and it stays in ``unmatched_rows`` -- which is what keeps the MATCH
        # verb OPEN on every card here.  At the seeded `-57.96` the Amazon
        # line's own proposal claimed it, and the pane this case is about was
        # never offered at all.
        a_transaction(seed_user, name="Electricity bill", amount="500.00")
        theirs = an_unexplained_outflow(
            second_user, merchant="Someone Else Ltd", amount="-4321.00",
        )
        db.session.commit()

        page = _open(auth_client, seed_user, theirs.id)

        assert f'id="rec-match-{theirs.id}"' not in page, (
            "another owner's line opened a pane on this account's page"
        )
        assert "Someone Else Ltd" not in page, (
            "another owner's merchant leaked through ?open="
        )
        # ...and the URL still routes for the owner's OWN line, so the two
        # absences above cannot both be a dead route.
        mine = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-12.34",
        )
        db.session.commit()
        assert f'id="rec-match-{mine.id}"' in _open(
            auth_client, seed_user, mine.id,
        ), "?open= renders no pane for the owner's own line either"

    def test_open_on_a_tab_that_holds_no_bank_lines_renders_no_pane(
        self, auth_client, db, seed_user,
    ):
        """A settled tab has no card to render a pane inside.

        Asking ``card_subject`` alone answered a live pane here -- two
        database reads and a full price for a value the template rendered
        nowhere (adversarial review 2026-09-05).
        """
        line = an_unexplained_outflow(seed_user, merchant="Amazon")
        a_transaction(seed_user, name="Electricity bill", amount="57.96")
        db.session.commit()

        page = _open(auth_client, seed_user, line.id, tab="explained")

        assert f'id="rec-match-{line.id}"' not in page
        assert "rec-match-waiting" not in page, (
            "a settled tab rendered a bank line's MATCH placeholder"
        )

    def test_an_open_that_could_not_be_a_line_id_is_404(
        self, auth_client, db, seed_user,
    ):
        """The answer ``_reconcile_query.requested_tab`` gives for the same shape."""
        an_unexplained_outflow(seed_user, merchant="Amazon")
        db.session.commit()

        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile?open=nonsense"
        )

        assert response.status_code == 404

    def test_the_apply_form_keeps_the_card_open(
        self, auth_client, db, seed_user,
    ):
        """A refused press must not collapse the pane the owner is working in.

        With scripting off, re-opening it is a second navigation -- so the
        open card travels in the ACTION's query string, which is the shape the
        settled tabs' bound already uses on their Undo forms.
        """
        line, _ = self._a_card_payment_and_a_payback_another_period_holds(
            seed_user, db,
        )

        opened = _open(auth_client, seed_user, line.id, tab="transfers")
        shut = _page(auth_client, seed_user, "transfers")

        account_id = seed_user["account"].id
        assert (
            f'action="/accounts/{account_id}/statements/reconcile'
            f'?open={line.id}"'
        ) in opened, "an Apply from the opened card forgets which card it was"
        assert f'action="/accounts/{account_id}/statements/reconcile"' in shut, (
            "the ordinary render posts a URL carrying an open card anyway"
        )


class TestTheScriptlessPathRECORDSTheMatch:
    """The ACT plan step ``bank_import:X-gi-1`` exists to restore.

    **Every other case in its file read BYTES when this one was added.**
    Both adversarial reviews of 2026-09-05 said the same thing about that:
    nothing asserted the parked
    card payment could actually be explained again, and a page that renders
    the right markup while the door records nothing is precisely the failure
    this package has shipped before -- a primary arm dead in a browser with
    the suite green.

    **The verb is the trap.**  ``parked_card`` sets ``suggested=Verb.TRANSFER``,
    so a card scraped and posted verbatim submits ``verb-<line>=transfer`` -- a
    verb with no door -- and the pass records NOTHING while answering "you
    pressed OK without choosing what to do with them".  The page now opens the
    named card on MATCH for exactly that reason, and this case asserts the
    recorded act rather than the markup.
    """

    def test_open_tick_ok_apply_records_the_group(
        self, auth_client, db, seed_user,
    ):
        """`-$793.23` explained by `-$93.23` + `-$700.00`, from the page alone."""
        line, _ = _TestTheMatchPaneIsReachableWithNoScriptMembers(
        )._a_card_payment_and_a_payback_another_period_holds(seed_user, db)

        page = _open(auth_client, seed_user, line.id, tab="transfers")
        tokens = _row_tokens(page, line.id)
        assert len(tokens) == 2, (
            f"the opened card offered {len(tokens)} rows, not the two paybacks"
        )

        # **THE PAGE'S OWN BYTES, not a hand-built body.**  Everything except
        # the two ticks and the OK is scraped, so the verb this card submits
        # is whichever one the PAGE checked -- which is the whole finding:
        # hand-writing ``verb=match`` grades the door and not the screen, and
        # a parked card's own suggestion is TRANSFER, a verb with no door.
        applied = _post(
            auth_client, seed_user,
            reconcile_form_fields(page)
            + [(f"rows-{line.id}", token) for token in tokens]
            + [("ok", str(line.id))],
            page,
        )

        assert applied.status_code == 200, (
            f"the pass was refused: {applied.status_code}"
        )
        recorded = StatementMatch.query.filter_by(
            user_id=seed_user["user"].id,
        ).all()
        assert len(recorded) == 1, (
            f"the scriptless pass recorded {len(recorded)} matches, not one; "
            f"the page says: "
            f"{'no act named' if 'without choosing what to do' in applied.get_data(as_text=True) else 'nothing'}"
        )

    def test_the_named_card_opens_on_MATCH_and_the_others_do_not(
        self, auth_client, db, seed_user,
    ):
        """The verb the card is OK'd with IS the tab CSS shows.

        A parked payment's own suggestion is TRANSFER, which has no door, so
        leaving the radio there rendered the rows and hid them -- and recorded
        nothing when the owner pressed OK.  **The second assertion is the
        control**: without it this passes against a page that opens EVERY card
        on MATCH, which would change what an untouched card submits.
        """
        line, _ = _TestTheMatchPaneIsReachableWithNoScriptMembers(
        )._a_card_payment_and_a_payback_another_period_holds(seed_user, db)

        opened = _open(auth_client, seed_user, line.id, tab="transfers")
        shut = _page(auth_client, seed_user, "transfers")

        assert re.search(
            rf'id="verb-{line.id}-match"[^>]*\schecked', opened,
        ), "the card the link named did not open on its MATCH tab"
        assert re.search(
            rf'id="verb-{line.id}-transfer"[^>]*\schecked', shut,
        ), (
            "an unopened parked card no longer opens on the verb its own "
            "suggestion names, so this case cannot tell the two apart"
        )


class TestARefusedApplyKeepsTheOwnersTicks:
    """Plan step ``bank_import:X-gi-2a``, finding **BI-478**, ruling **R-BI3**.

    With scripting off nothing re-prices as the owner ticks, so on a
    hand-built group with a difference the FIRST press is refused by design
    (**R-BI1**) -- and the page it answered with priced the pane from the
    tier's proposal, so every tick was gone and the owner started over.  The
    answer is priced from the submitted form now, and it echoes every
    control whose value is still on offer: the rows, and the consent by
    whole-value equality.

    **Every case reads the PAGE and presses its own bytes**, because what
    this step changes is what a browser with no JavaScript receives after a
    press, and the door itself is untouched.
    """

    @staticmethod
    def _a_card_payment_and_two_paybacks(seed_user, db):
        """Stage the class the scriptless pane exists for.

        The parked `-$793.23` card payment and its two paybacks, `-$93.23`
        and `-$700.00`, from the sibling class's own fixture; the earlier
        period's second line is what keeps the `-$700.00` row offered at all.

        Returns:
            The staged bank line.
        """
        line, _ = _TestTheMatchPaneIsReachableWithNoScriptMembers(
        )._a_card_payment_and_a_payback_another_period_holds(seed_user, db)
        return line

    def _opened_tokens(self, auth_client, seed_user, line):
        """Return the opened page and its two row tokens, by figure.

        Returns:
            ``(page, token_93, token_700)``.
        """
        page = _open(auth_client, seed_user, line.id, tab="transfers")
        tokens = _row_tokens(page, line.id)
        by_figure = {token.split(":")[2]: token for token in tokens}
        assert set(by_figure) == {"-93.23", "-700.00"}, (
            f"the opened card offered {tokens}, not the two paybacks"
        )
        return page, by_figure["-93.23"], by_figure["-700.00"]

    @staticmethod
    def _ticked(body, line_id):
        """Return the row tokens the re-drawn pane renders TICKED.

        :func:`~tests.test_routes._statement_forms.reconcile_form_fields`
        keeps what a browser would submit, and an unticked box submits
        nothing -- so this is exactly the echo under test.
        """
        return [
            value for name, value in reconcile_form_fields(body)
            if name == f"rows-{line_id}"
        ]

    def _refused_once(self, auth_client, seed_user, db):
        """Press Apply on ONE ticked payback and return the refused page.

        `-$93.23` alone against `-$793.23` leaves `-$700.00`, and nothing has
        consented to it, so the door refuses -- the ordinary first press of
        the scriptless flow.

        Returns:
            ``(line, token_93, token_700, body)``.
        """
        line = self._a_card_payment_and_two_paybacks(seed_user, db)
        page, token_93, token_700 = self._opened_tokens(
            auth_client, seed_user, line,
        )
        response = _post(
            auth_client, seed_user,
            reconcile_form_fields(page)
            + [(f"rows-{line.id}", token_93), ("ok", str(line.id))],
            page, opened=line.id,
        )
        assert response.status_code == 200, (
            "a per-item refusal answers with the screen at 200"
        )
        body = response.get_data(as_text=True)
        assert "These do not add up" in body, (
            "the press was not refused, so nothing here grades the re-draw"
        )
        assert StatementMatch.query.count() == 0
        return line, token_93, token_700, body

    def test_the_refused_press_comes_back_with_the_rows_ticked_and_priced(
        self, auth_client, db, seed_user,
    ):
        """The whole of BI-478, read off the page the refusal answers with.

        The `-$93.23` row is ticked, the `-$700.00` row is not, the totals
        are the submitted rows' own, and the one act a lone row can become
        is offered -- where the shipped tree came back with nothing ticked,
        nothing priced, and the disabled placeholder.
        """
        line, token_93, token_700, body = self._refused_once(
            auth_client, seed_user, db,
        )

        assert f'id="rec-match-{line.id}"' in body, "the pane is not open"
        assert self._ticked(body, line.id) == [token_93], (
            "the refused press did not come back with the owner's tick"
        )
        assert token_700 in _row_tokens(body, line.id), (
            "the unticked payback is no longer offered"
        )
        # **The sums block, and not the figures the row list prints anyway**:
        # the line's own amount and each row's figure are in the document
        # whether or not anything was priced (adversarial review 2026-09-11).
        assert re.search(
            r'class="rec-sums(?:.|\n)*?your bank shows(?:.|\n)*?-\$793\.23'
            r'(?:.|\n)*?come to(?:.|\n)*?-\$93\.23'
            r'(?:.|\n)*?difference(?:.|\n)*?-\$700\.00',
            body,
        ), "the re-drawn pane was not priced from the submitted rows"
        assert _consent_options(body, line.id) == ["-700.00"], (
            "the difference's one act was not offered on the re-draw"
        )

    def test_the_second_press_from_the_re_drawn_page_records_the_match(
        self, auth_client, db, seed_user,
    ):
        """The flow, end to end, from the refused page's own bytes.

        Tick the one act it offers and press again: the row echoed by the
        re-draw and the consent are what the door reads, and it records the
        bank's figure written to the `-$93.23` row.
        """
        line, token_93, _, body = self._refused_once(
            auth_client, seed_user, db,
        )

        applied = _post(
            auth_client, seed_user,
            reconcile_form_fields(body)
            + [(f"consent-{line.id}", "-700.00"), ("ok", str(line.id))],
            body, opened=line.id,
        )

        assert applied.status_code == 200
        assert "These do not add up" not in applied.get_data(as_text=True)
        assert StatementMatch.query.count() == 1, (
            "the second press did not record the match"
        )
        row_id = int(token_93.split(":")[1])
        db.session.expire_all()
        assert settled_figure(db.session.get(Transaction, row_id)) == Decimal(
            "793.23"
        ), "the lone-row act did not write the bank's figure to the row"

    def test_a_consent_comes_back_PICKED_when_its_act_is_still_offered(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-BI3**: the echo reaches the consent too.

        The owner ticks the one act and presses; the press is refused for a
        reason that has nothing to do with this card -- a SECOND card's item
        is malformed, which is a pass-level refusal -- so the same act is
        still on offer, and the box comes back ticked.

        **Posted directly**, because the second card is not on this tab and
        its field is one no page renders: a crafted sibling is the only way
        to refuse a pass whose opened card is in order.
        """
        line, token_93, _, body = self._refused_once(
            auth_client, seed_user, db,
        )
        another = an_unexplained_outflow(seed_user, merchant="Kroger")
        db.session.commit()

        refused = auth_client.post(
            _url(seed_user["account"].id) + f"?open={line.id}",
            data=MultiDict(
                [("csrf_token", "x")]
                + reconcile_form_fields(body)
                + [
                    (f"consent-{line.id}", "-700.00"),
                    ("ok", str(line.id)),
                    ("ok", str(another.id)),
                    (f"verb-{another.id}", "match"),
                    (f"rows-{another.id}", "not-a-token"),
                ]
            ),
        )

        assert refused.status_code == 400
        page = refused.get_data(as_text=True)
        assert "not a row this page could have shown you" in page
        assert StatementMatch.query.count() == 0
        assert self._ticked(page, line.id) == [token_93]
        assert (f"consent-{line.id}", "-700.00") in reconcile_form_fields(
            page,
        ), "the consent the owner gave to an unchanged act came back unpicked"

    def test_a_consent_to_an_act_no_longer_offered_is_NOT_picked(
        self, auth_client, db, seed_user,
    ):
        """The control on the case above: equal value, or nothing.

        The owner consents to `-$700.00` written to the `-$93.23` row, then
        changes the rows so the difference is `-$93.23` instead.  The door
        refuses the stale figure, the re-draw offers the act for the NEW
        difference, and it is not picked -- a consent is echoed to the act
        it was given for and never to whatever stands in its place.
        """
        line, _, token_700, body = self._refused_once(
            auth_client, seed_user, db,
        )

        refused = _post(
            auth_client, seed_user,
            _choosing(
                reconcile_form_fields(body), f"rows-{line.id}", token_700,
            ) + [(f"consent-{line.id}", "-700.00"), ("ok", str(line.id))],
            body, opened=line.id,
        )

        assert refused.status_code == 200
        page = refused.get_data(as_text=True)
        assert "reviewed against a difference of -700.00" in page
        assert StatementMatch.query.count() == 0
        assert self._ticked(page, line.id) == [token_700]
        assert _consent_options(page, line.id) == ["-93.23"]
        assert not [
            value for name, value in reconcile_form_fields(page)
            if name == f"consent-{line.id}"
        ], "a consent to an act no longer offered was echoed onto another"

    def test_the_scripted_pane_keeps_a_pick_across_a_search_keystroke(
        self, auth_client, db, seed_user,
    ):
        """One template serves both surfaces, so the fragment echoes too.

        Typing in the search box re-fetches the pane with the consent radio
        in the body; the rows are unchanged, so the same acts are offered and
        the pick survives -- where the shipped fragment came back with every
        option blank.  **The body carries the keystroke** (``q-<line>``), so
        the render graded is the search-narrowed one and not a plain re-price
        (adversarial review 2026-09-11).
        """
        pane_of = _TestThePaneOffersWHEREADifferenceGoesMembers()
        line, _, _ = pane_of._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            pane_of._pane(auth_client, seed_user, line), line.id,
        )
        chosen = f"0.05@{tokens[0]}"
        assert chosen in _consent_options(
            pane_of._pane(auth_client, seed_user, line, rows=tokens), line.id,
        ), "the act this case picks is not one the pane offers"

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data=MultiDict(
                [("csrf_token", "x"), (f"q-{line.id}", "Data")]
                + [(f"rows-{line.id}", token) for token in tokens]
                + [(f"consent-{line.id}", chosen)]
            ),
        ).get_data(as_text=True)

        assert f'value="Data"' in pane, "the search was not performed"
        assert (f"consent-{line.id}", chosen) in reconcile_form_fields(pane), (
            "the fragment re-drew the picked option blank"
        )
        assert [
            value for name, value in reconcile_form_fields(pane)
            if name == f"consent-{line.id}"
        ] == [chosen], "more than one option came back picked"

    def test_ticks_on_the_open_card_survive_an_apply_that_OKd_another_card(
        self, auth_client, db, seed_user,
    ):
        """The card is read from the body whether or not it was OK'd.

        The owner ticks a row on the open card, then OKs a DIFFERENT card on
        SKIP and presses.  The skip lands, the pass answers from a fresh
        read, and the open card comes back as they left it.
        """
        opened = an_unexplained_outflow(seed_user, merchant="Amazon")
        a_transaction(seed_user, name="Electricity bill", amount="500.00")
        other = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-12.34",
        )
        db.session.commit()
        page = _open(auth_client, seed_user, opened.id)
        (token,) = _row_tokens(page, opened.id)

        applied = _post(
            auth_client, seed_user,
            _choosing(
                reconcile_form_fields(page), f"verb-{other.id}", "skip",
            ) + [(f"rows-{opened.id}", token), ("ok", str(other.id))],
            page, opened=opened.id,
        )

        assert applied.status_code == 200
        assert StatementLineSkip.query.count() == 1, "the skip did not land"
        body = applied.get_data(as_text=True)
        assert self._ticked(body, opened.id) == [token], (
            "the open card lost its tick to a press that OK'd another card"
        )

    def test_a_malformed_field_for_the_open_card_refuses_the_pass_first(
        self, auth_client, db, seed_user,
    ):
        """The reader runs BEFORE the door, or it grades a write.

        The open card was not OK'd, so the batch never grades its fields;
        a crafted row token for it must still be a pass-level refusal, and
        the skip beside it must NOT land -- the case above is the control
        that shows the same press landing it when the field is in order.
        """
        opened = an_unexplained_outflow(seed_user, merchant="Amazon")
        a_transaction(seed_user, name="Electricity bill", amount="500.00")
        other = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-12.34",
        )
        db.session.commit()
        page = _open(auth_client, seed_user, opened.id)

        # POSTED DIRECTLY: the token is one no page renders.
        refused = auth_client.post(
            _url(seed_user["account"].id) + f"?open={opened.id}",
            data=MultiDict(
                [("csrf_token", "x")]
                + _choosing(
                    reconcile_form_fields(page), f"verb-{other.id}", "skip",
                )
                + [(f"rows-{opened.id}", "not-a-token"), ("ok", str(other.id))]
            ),
        )

        assert refused.status_code == 400
        body = refused.get_data(as_text=True)
        assert "not a row this page could have shown you" in body
        assert StatementLineSkip.query.count() == 0, (
            "a malformed field for the open card was read after the door"
        )
        assert f'id="rec-match-{opened.id}"' in body, (
            "the refusal did not keep the card open"
        )
