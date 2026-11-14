"""The MATCH pane: it prices what is ticked, tags a row the bank never shows alone, applies a
proposed card, renders a ticked row whatever list is showing, and shows the dates a human
verifies by on both surfaces that render it.
"""

import re
from datetime import timedelta
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app.enums import SettledDayBasisEnum, StatusEnum
from app.models.statement_match import StatementMatch
from app.services import pay_calendar, transaction_service
from app.services.row_valuation import settled_figure
from app.services.settle_day import SettleDay
from tests.test_routes._statement_forms import controls_inside_the_trigger, reconcile_form_fields
from tests._test_helpers import payback_row_of
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import _match_url, _page, _post, _row_tokens, _open, _card_markup


class TestTheMatchPanePricesWhatIsTicked:
    """Ruling **R-FN**: a difference is a transaction the owner ACCEPTS.

    The pane runs the accept door's own reads and refusals without the
    writes, so the figure on screen and the figure the door compares against
    are one derivation.
    """

    def _a_payroll_deposit_and_its_two_rows(self, seed_user, db):
        """Stage finding **salary:N-391**'s own case.

        N-239 until `balance:X-aw` retired that row on 2026-08-30 and split
        its bank half off as N-391; the planning ledger held no N-239 row
        after it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        db.session.commit()
        return line

    def test_it_names_the_rows_the_lines_own_period_holds(
        self, auth_client, db, seed_user,
    ):
        """The list a card opens on."""
        line = self._a_payroll_deposit_and_its_two_rows(seed_user, db)

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        assert "Data Manager" in pane
        assert "Health Insurance Allowance" in pane

    def test_ticking_both_prices_the_gap_and_offers_the_consent(
        self, auth_client, db, seed_user,
    ):
        """`$2,473.38` + `$100.00` against `$2,573.42` is `$0.04`."""
        line = self._a_payroll_deposit_and_its_two_rows(seed_user, db)
        first = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)
        tokens = [
            value for name, value in reconcile_form_fields(first)
            if name == f"rows-{line.id}"
        ]
        # The reader keeps only CHECKED boxes, so the tokens come off the
        # rendered values rather than from the pane's own state.
        tokens = _row_tokens(first, line.id)

        assert len(tokens) >= 2, "the pane offered fewer than two rows"
        priced = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data=MultiDict(
                [("csrf_token", "x")]
                + [(f"rows-{line.id}", token) for token in tokens]
            ),
        ).get_data(as_text=True)

        assert "$0.04" in priced
        assert f'name="consent-{line.id}"' in priced
        assert 'value="0.04"' in priced

    def test_a_search_reaches_a_row_outside_the_lines_period(
        self, auth_client, db, seed_user,
    ):
        """The half that keeps a card payment groupable."""
        line = self._a_payroll_deposit_and_its_two_rows(seed_user, db)

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x", f"q-{line.id}": "Data"},
        ).get_data(as_text=True)

        assert "Data Manager" in pane
        assert "Health Insurance Allowance" not in pane


class TestThePaneTagsARowTheBankNeverShowsAloneOnItsOwn:
    """Plan step ``bank_import:X-gc``: the caveat, on the surface that renders it.

    A row whose figure is not its own -- a CC payback, or a container priced
    from the purchases inside it -- can never be a bank line by itself, so the
    pane tags it and says why (:data:`~app.services.statement_match._caveat
    .NOT_SHOWN_ALONE`).  The row STAYS tickable, because ruling **R-GJ** leaves
    grouping it against the line that does carry its money as a parked card
    payment's one remaining arm.

    **These two cases are here because plan step ``bank_import:X-gi-2`` deleted
    the pair that graded the render.**  They lived on the hand-build workbench
    (``test_statement_workbench.TestTheNeverShowedPanel``), which was the only
    other surface that listed candidate rows; ``tests/test_services
    /test_statement_match/test_candidates.py`` grades the DERIVATION and cannot
    see a template.  Deleting a page must not silently delete the coverage of a
    disclosure the surviving page still renders, which is exactly what an
    adversarial review of that step measured happening.

    **Asserted inside the PANE and not page-wide**, which is the discipline the
    deleted pair kept for the same reason: this page renders many badges, and a
    body-wide search would be graded by whatever else says the same words.

    **Both cases pin the row into the CANDIDATE loop by its rendered ``id``,
    and that is a correctness fix rather than thoroughness.**  The pane has TWO
    row lists -- a tier's PROPOSAL (``id="proposed-<line>-..."``) and the
    candidate list this caveat is rendered in (``id="row-<line>-..."``) -- and
    only the second carries the badge at all.  A first version of the
    discriminating case gave its row the line's own figure, so a tier proposed
    it and it rendered in the FIRST list: the case passed with the badge forced
    on unconditionally, because the block under test never ran.  Measured by
    mutation.  So the figures here are deliberately unequal.
    """

    def _a_card_payment_line(self, seed_user, db, description):
        """Record one unexplained outflow on the owner's first day.

        Args:
            seed_user: The seeded user bundle.
            db: The session.
            description: What the bank called it.

        Returns:
            The :class:`~app.models.statement_import.BankStatementLine`.
        """
        return a_bank_line(
            seed_user, an_import(seed_user), amount="-500.00",
            posted_on=seed_user["bootstrap_period"].start_date,
            description=description,
        )

    def test_the_pane_TAGS_a_payback_and_still_offers_it(
        self, auth_client, db, seed_user,
    ):
        """A CC payback is listed, tickable, AND tagged.

        All three halves matter together: X-gc's plan text said the panel
        should "stop listing rows the bank could never show", and taken
        literally that removes the paybacks -- the only thing a parked card
        payment can be grouped against.  So the row is listed, its token is
        rendered, and only the CLAIM is withdrawn.
        """
        bank_day = seed_user["bootstrap_period"].start_date
        line = self._a_card_payment_line(
            seed_user, db, "ACH DEBIT CAPITAL ONE      MOBILE PMT",
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_later_period(seed_user)
        # The payback through its own producer (plan step balance:X-bi-7c),
        # then settled on the bank's day as the owner would settle it.
        payback = payback_row_of(
            db.session, seed_user, envelope, Decimal("60.00"), bank_day,
        )
        transaction_service.settle_transaction(
            payback,
            settle_day=SettleDay(day=bank_day, basis=SettledDayBasisEnum.ENTERED),
        )
        db.session.commit()

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        assert "CC Payback: Groceries" in pane, (
            "the payback was not offered at all, so the tag assertions below "
            "would be quantified over a pane that lists nothing"
        )
        # A settled row renders as its PAYMENT (plan step
        # ``credit_card:CC-5-4a-1``, ruling **R-CC43**): the candidate's id
        # carries the covering movement's kind and id, where through
        # ``CC-5-3`` it carried ``transaction-<row id>``.
        (payment,) = payback.covering_movements
        assert f'id="row-{line.id}-settlement-{payment.id}"' in pane, (
            "the payback did not render in the CANDIDATE list, so this case "
            "is not exercising the block that carries the caveat"
        )
        assert "not a line of its own" in pane
        assert "never shows it as a line by itself" in pane

    def test_the_pane_does_NOT_tag_a_row_the_bank_would_have_shown(
        self, auth_client, db, seed_user,
    ):
        """The discriminating half, without which the pair grades nothing.

        A tag on every row would withdraw the alarm from the payments the bank
        really did fail to make, which is the half of ruling **R-FP** this
        list exists for.  The two cases have DISJOINT expectations on the same
        two strings, so a render that tagged unconditionally fails here and a
        render that tagged nothing fails above.
        """
        bank_day = seed_user["bootstrap_period"].start_date
        line = self._a_card_payment_line(
            seed_user, db, "ACH DEBIT NOTHING EXPLAINS THIS",
        )
        # **NOT the line's own `-$500.00`.**  A row that matches the figure is
        # PROPOSED, and a proposal renders in the pane's other list, which
        # carries no caveat markup at all -- so the case would pass however
        # this block behaved.  See the class docstring.
        ghost = a_transaction(
            seed_user, name="Ghost Payment", amount="123.45",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()

        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        # As its payment (ruling **R-CC43**; the case above says why).
        (payment,) = ghost.covering_movements
        assert f'id="row-{line.id}-settlement-{payment.id}"' in pane, (
            "the row was not offered in the CANDIDATE list, so the absence "
            "assertions below would pass over a block that never ran"
        )
        assert "not a line of its own" not in pane
        assert "never shows it as a line by itself" not in pane


class TestAProposedCardAppliesFromThisPageAndItsPaneLoads:
    """The near tier's own act, walked end to end on the Reconcile page.

    **Both halves were ungraded and an adversarial review measured it**: with
    the page's hidden ``residual-<line>`` (``consent-<line>`` since plan step
    ``bank_import:X-gp``) deleted the whole tracked suite
    stayed green at 4,516 passed, while the same mutation on the review
    queue's own input fails a case by name.  One of the two emission sites
    was checked and the other was not, and it is the newer one that a browser
    now depends on: the accept door exempts no shape since plan step
    ``bank_import:X-gj-1b``, so a proposal that states no figure REFUSES.
    """

    @staticmethod
    def _near_miss(seed_user, db):
        """Stage the developer's own Geico case: a bill settled 3 cents off.

        Returns:
            ``(line, txn)`` -- the bank line and the row a tier will pair it
            with, three cents apart, which is what makes the proposal a NEAR
            MISS rather than an exact match.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Geico", amount="-178.29",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=line.posted_on,
        )
        db.session.commit()
        return line, txn

    def test_the_page_emits_the_figure_the_proposal_states(
        self, auth_client, db, seed_user,
    ):
        """The control the mutation deleted, asserted by name and value.

        It is a HIDDEN input, so it is not something a case can discover by
        pressing: nothing else in this package would notice it going away.
        """
        line, _ = self._near_miss(seed_user, db)

        fields = reconcile_form_fields(_page(auth_client, seed_user))

        assert (f"consent-{line.id}", "0.03") in fields, (
            "the card stated no difference, so the door would refuse the "
            "very act this tier exists to offer"
        )

    def test_OK_on_a_proposed_card_REPRICES_the_row(
        self, auth_client, db, seed_user,
    ):
        """Scrape the page, press OK, and check the money moved.

        The loop the file's own header asks for: the page's bytes go back to
        the door, so the figure the owner agreed to and the figure the door
        wrote are the same derivation rather than two that agree by reading.
        """
        line, txn = self._near_miss(seed_user, db)
        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)

        assert (f"verb-{line.id}", "match") in fields, (
            "the card did not open on MATCH, so this grades the wrong verb"
        )
        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 1
        assert settled_figure(txn) == Decimal("178.29"), (
            "the row must book what the BANK took"
        )

    def test_the_MATCH_pane_LOADS_for_a_proposed_card(
        self, auth_client, db, seed_user,
    ):
        """The pane a proposal card asks for on its first open.

        **It answered 404 for every proposal until plan step
        ``bank_import:X-gj-1b``**, because the route resolved the line in
        ``review.unmatched`` and ``_unexplained`` takes a proposal's line out
        of that list before it exists -- 137 of the developer's 137 proposal
        cards, each a spinner that never resolved.  htmx does not swap a 4xx,
        so the owner saw *Finding the rows this could be...* for ever.
        """
        line, _ = self._near_miss(seed_user, db)

        response = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        )

        assert response.status_code == 200, (
            "a proposed card's MATCH pane must load like any other"
        )
        assert f'name="rows-{line.id}"' in response.get_data(as_text=True), (
            "the pane rendered none of the proposal's own rows"
        )

    def test_the_TRIGGER_does_not_contain_the_control_it_replaces(
        self, auth_client, db, seed_user,
    ):
        """The consent box may not sit inside the element that re-renders it.

        **The workbench carries this control and this pane shipped without
        it**, which is how the same defect arrived twice: ticking the consent
        fired the re-render, and the swap replaced it with a fresh unticked
        one, so the owner could never keep it ticked and Apply carried no
        figure -- with every server test green.  Asserted over the MARKUP,
        because the defect is a containment relation and nothing else in the
        tree can see one.

        The rows must be INSIDE, which is the other half and is what makes
        unticking a proposed row re-price at all: a change event bubbles to
        ancestors only.
        """
        line, _ = self._near_miss(seed_user, db)
        pane = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)

        inside = controls_inside_the_trigger(pane)

        assert f"rows-{line.id}" in inside, (
            "the row checkboxes are outside the element whose change "
            "re-prices them, so unticking one changes the submission and "
            "re-prices nothing"
        )
        assert f"consent-{line.id}" not in inside, (
            "the consent control is inside the element that replaces it, so "
            "ticking it discards the tick"
        )


class TestATickedRowStillOfferedIsRenderedWhateverListIsShowing:
    """Plan step ``bank_import:X-gi-1``, second clause.

    **A checkbox that is not in the document is not submitted.**  The pane
    renders one list at a time -- a pay period, or a search -- and a row the
    owner has ticked can fall outside the next one.  Without this rule,
    ticking a payback found by one search and then searching for the second
    takes the first silently out of the act, which is the very group a card
    payment needs; and it is what would make the scriptless render's wider
    list unsafe on a browser that DOES have scripting, where the first
    re-price swaps the period's rows back in.
    """

    def test_a_ticked_row_survives_a_search_that_does_not_match_it(
        self, auth_client, db, seed_user,
    ):
        """Tick one row, then search for another; the first must still be there."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        db.session.commit()

        first = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data={"csrf_token": "x"},
        ).get_data(as_text=True)
        tokens = _row_tokens(first, line.id)
        assert len(tokens) == 2, (
            f"the pane offered {len(tokens)} rows, not the two staged"
        )
        # The row the owner ticks FIRST is the one the search below excludes.
        held = _token_for(first, line.id, "Health Insurance Allowance")

        # THE CONTROL, and without it this case cannot tell the union from
        # the search: the same query with NOTHING ticked must not offer the
        # held row, or its presence below would prove nothing.
        control = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data=MultiDict([("csrf_token", "x"), (f"q-{line.id}", "Data")]),
        ).get_data(as_text=True)

        assert "Data Manager" in control, "the search returned nothing"
        assert "Health Insurance Allowance" not in control, (
            "the search matched the row this case was written to exclude"
        )

        narrowed = auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data=MultiDict([
                ("csrf_token", "x"),
                (f"rows-{line.id}", held),
                (f"q-{line.id}", "Data"),
            ]),
        ).get_data(as_text=True)

        assert re.search(
            rf'value="{re.escape(held)}"\s+checked>', narrowed,
        ), (
            "the row the owner had ticked is missing from the narrowed pane, "
            "or is rendered unticked -- either way a browser drops it from "
            "the act without saying so"
        )


class TheMatchPaneShowsTheDatesAHumanVerifiesBy:
    """Plan step ``bank_import:X-gz``, ruling **R-BI9**, finding **BI-498**.

    A base rather than a test class: the two surfaces that render the pane --
    the live fragment and the ``?open=`` page -- share the staging and the
    assertions, and each subclass names its own render.  The staged case is
    the finding's own: a `$47.61` fuel purchase made three days BEFORE the
    paycheck its envelope is budgeted in opened, settled by a balance true-up
    thirty-three days later, offered against a bank line posted the day after
    it was made.  The pane labelled that row "2026-08-18" and the developer
    stopped the first production matching session at 22 of 172 lines.
    """

    def _the_finding_s_case(self, seed_user, db):
        """Stage entry 68 against line 303, on the seeded calendar.

        Returns:
            ``(line, purchase, later)`` -- the bank line, the purchase a tier
            will propose for it, and the paycheck the purchase is budgeted in.
        """
        later = a_later_period(seed_user)
        made_on = later.start_date - timedelta(days=3)
        asserted_for = later.start_date + timedelta(days=33)
        envelope = a_transaction(
            seed_user, name="Gas", is_envelope=True, period=later,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="47.61", description="Bjs",
            purchased_on=made_on, settled_on=asserted_for,
            settle_day_basis=SettledDayBasisEnum.ASSERTED,
        )
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-47.61",
            posted_on=made_on + timedelta(days=1),
            description="POINT OF SALE DEBIT L343 BJS FUEL #9151",
        )
        db.session.commit()
        return line, purchase, later

    def _render(self, auth_client, seed_user, line, query=None):
        """Return the surface under test, with *line*'s pane in it."""
        raise NotImplementedError

    @staticmethod
    def _labelled(label, text):
        """Return the pattern one labelled date renders as."""
        return rf'{label}\s*<span class="font-mono">{re.escape(text)}</span>'

    def test_a_purchase_prints_its_day_its_paycheck_and_the_gap_and_not_the_stamp(
        self, auth_client, db, seed_user,
    ):
        """The finding, end to end through the real door."""
        line, purchase, later = self._the_finding_s_case(seed_user, db)
        paycheck = pay_calendar.calendar_for(
            seed_user["user"].id,
        ).period_by_id(later.id).label
        made = purchase.purchased_on.strftime("%m/%d")
        stamp_iso = purchase.settled_on.isoformat()
        stamp = purchase.settled_on.strftime("%m/%d")

        pane = self._render(auth_client, seed_user, line)

        assert re.search(self._labelled("purchased", made), pane), (
            f"the pane does not print 'purchased {made}' for the fuel purchase"
        )
        assert re.search(self._labelled("budgeted", paycheck), pane), (
            f"the pane does not print 'budgeted {paycheck}' for the fuel purchase"
        )
        assert "the bank posted it 1 day after the purchase" in pane
        assert stamp_iso not in pane, (
            f"the true-up stamp {stamp_iso} is still printed on the pane"
        )
        assert re.search(self._labelled(r"\w+", stamp), pane) is None, (
            f"the true-up stamp {stamp} is printed as a labelled date"
        )
        assert "not recorded" not in pane

    def test_a_bill_prints_its_paycheck_and_the_gap_and_no_purchase_day(
        self, auth_client, db, seed_user,
    ):
        """An unsettled transaction: budgeted in a paycheck, made on no day."""
        period = seed_user["bootstrap_period"]
        a_transaction(seed_user, name="Electricity", amount="180.00")
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.00",
            posted_on=period.start_date + timedelta(days=5),
        )
        db.session.commit()
        paycheck = pay_calendar.calendar_for(
            seed_user["user"].id,
        ).period_by_id(period.id).label

        pane = self._render(auth_client, seed_user, line)

        assert re.search(self._labelled("budgeted", paycheck), pane), (
            f"the pane does not print 'budgeted {paycheck}' for the bill"
        )
        assert "the bank posted it inside that pay period" in pane
        # A direct pattern: ``_labelled`` escapes its text, so a character
        # class handed to it would be matched literally and never found.
        assert re.search(r'purchased\s*<span class="font-mono">', pane) is None, (
            "a bill has no purchase day, yet the pane prints one"
        )
        assert "not recorded" not in pane


class TestTheLiveFragmentShowsTheDatesAHumanVerifiesBy(
    TheMatchPaneShowsTheDatesAHumanVerifiesBy,
):
    """The scripted surface: ``POST .../line/<id>/match``."""

    def _render(self, auth_client, seed_user, line, query=None):
        data = {"csrf_token": "x"}
        if query is not None:
            data[f"q-{line.id}"] = query
        return auth_client.post(
            _match_url(seed_user["account"].id, line.id), data=data,
        ).get_data(as_text=True)

    def test_the_CANDIDATE_list_prints_the_same_labels_as_the_proposed_one(
        self, auth_client, db, seed_user,
    ):
        """The copy a change forgets is the candidates' (the searched list).

        A second purchase no tier proposes -- a different figure -- is a
        CANDIDATE and renders through the same macro, so its dates are
        labelled too.  Asserted on the candidate row's own control id, which
        only the candidate list emits.  It is made on the bank's own day,
        because the search reaches only rows the recorded statement could
        have shown (``_reads._could_have_been_shown``) and the account holds
        one recorded line.
        """
        line, _, later = self._the_finding_s_case(seed_user, db)
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True, period=later,
        )
        other = a_purchase(
            seed_user, envelope, amount="31.07", description="Food Lion",
            purchased_on=line.posted_on,
        )
        db.session.commit()
        made = other.purchased_on.strftime("%m/%d")

        pane = self._render(auth_client, seed_user, line, query="Food")

        candidate = re.search(
            rf'id="row-{line.id}-purchase-{other.id}"(.*?)</label>', pane, re.S,
        )
        assert candidate is not None, "the search did not offer Food Lion"
        assert re.search(
            self._labelled("purchased", made), candidate.group(1),
        ), f"the candidate list does not print 'purchased {made}'"
        assert "the bank posted it on the purchase day" in candidate.group(1)


class TestTheScriptlessPageShowsTheDatesAHumanVerifiesBy(
    TheMatchPaneShowsTheDatesAHumanVerifiesBy,
):
    """The ``?open=<line_id>`` render, which puts the pane in the document."""

    def _render(self, auth_client, seed_user, line, query=None):
        return _card_markup(_open(auth_client, seed_user, line.id), line.id)


def _token_for(pane, line_id, label):
    """Return the reviewed-row token the pane rendered for one row LABEL.

    Args:
        pane: The rendered MATCH pane.
        line_id: The bank line it is about.
        label: The row's on-screen name.

    Returns:
        Its token.
    """
    block = re.search(
        rf'name="rows-{line_id}" value="([^"]+)"'
        rf'(?:(?!name="rows-).)*?<span class="rec-row-name">{re.escape(label)}<',
        pane,
        re.S,
    )
    assert block is not None, f"the pane rendered no row called {label!r}"
    return block.group(1)
