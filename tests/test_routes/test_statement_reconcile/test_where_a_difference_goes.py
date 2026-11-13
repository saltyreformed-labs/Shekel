"""Where the MATCH pane offers a group's difference to go: one control whose options are the acts,
each labelled with what it writes, and none pre-selected.
"""

import re
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import status_seam
from app.services.row_valuation import settled_figure
from tests.test_routes._statement_forms import reconcile_form_fields, reconcile_offerable
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_transaction,
    an_import,
)

from ._helpers import (
    _url,
    _page,
    _choosing,
    _post,
    _row_tokens,
    _consent_options,
    _TestThePaneOffersWHEREADifferenceGoesMembers,
)


def _option_label(pane, value):
    """Return the label text of the consent option carrying *value*.

    Args:
        pane: The rendered MATCH pane.
        value: The option's submitted value, exactly as rendered.

    Returns:
        The text between that option's ``<label>`` and ``</label>``.

    Raises:
        AssertionError: When no option carries *value*, which would make an
            assertion over its label vacuous.
    """
    after = pane.split(f'value="{value}"', 1)
    assert len(after) == 2, f"no option carries {value!r}"
    label = re.search(r"<label[^>]*>(.*?)</label>", after[1], re.DOTALL)
    assert label is not None, f"the option carrying {value!r} has no label"
    return label.group(1)


def _uncategorized_rows(db, seed_user):
    """Return every row R-FN's ordinary-row act could have minted.

    Args:
        db: The session.
        seed_user: The seeded user bundle.

    Returns:
        The uncategorised transactions on the seeded account (no
        transaction is a transfer's: a transfer is one row of its own).
    """
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == seed_user["account"].id,
            Transaction.category_id.is_(None),
        )
        .all()
    )


class TestThePaneOffersWHEREADifferenceGoes(
    _TestThePaneOffersWHEREADifferenceGoesMembers,
):
    """Plan steps **bank_import:X-gj-3a** and **X-gp**; rulings **R-GD(a)**,
    **R-FN** and **R-BI2**.

    A group's difference could only ever become an uncategorized row before
    X-gj-3a, whatever the owner knew about it.  On the developer's own data
    that is seven payroll deposits and seven `$0.04`-`$0.06` rows a year, with
    the salary row left permanently under what the employer paid -- while the
    two flat allowances beside it matched the bank exactly in all eleven of
    their occurrences, which is what makes *which member* an answerable
    question for a person and an underivable one for the app.

    **ONE control whose options ARE the acts** (X-gp).  That step drew the
    landing as a select inside the re-price wrapper and the consent as a box
    outside it whose sentence was composed from the select, and the door
    compared only the box's figure -- so *mint a `$0.05` row* and *write
    `$2,473.43` to the salary row* submitted the SAME consent.  Every act is
    one option now, labelled with what it writes, valued with the figure AND
    the member (``consent-<line>``), and the cases below read that control
    where they read the select.

    **No member is pre-selected** (developer, 2026-09-01): a group has nothing
    the app can point at to justify one, and an unjustified default on a money
    control is what ruling **R-FZ(b)** removed from this screen.
    """

    def test_a_GROUP_with_a_difference_is_offered_the_choice(
        self, auth_client, db, seed_user,
    ):
        """One option per ticked row, plus R-FN's ordinary row first.

        **Each option's VALUE carries the figure and the member together**
        (X-gp): the ordinary row's is the figure alone, and each member's is
        the figure at that member's own row token -- so no two acts share a
        value, which is the whole of ruling **R-BI2**.
        """
        line, _, _ = self._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )

        pane = self._pane(auth_client, seed_user, line, rows=tokens)

        options = _consent_options(pane, line.id)
        assert options[0] == "0.05", (
            "R-FN's ordinary row must be the FIRST option, valued with the "
            f"figure alone: {options}"
        )
        for token in tokens:
            assert f"0.05@{token}" in options, (
                "every ticked row must be nameable as the one that carries it"
            )
        assert len(options) == len(set(options)) == len(tokens) + 1, (
            f"two acts share one consent value: {options}"
        )
        assert f'name="consent-{line.id}"' in pane
        assert re.search(
            rf'type="radio"[^>]*name="consent-{line.id}"', pane,
        ), "alternatives must be a radio group, so picking one unpicks the rest"

    def test_NOTHING_is_pre_selected(self, auth_client, db, seed_user):
        """The developer's ruling of 2026-09-01, read off the rendered control.

        A browser submits a radio only when it is checked, so the control:
        the options are OFFERED and none is SUBMITTED.  It read the select's
        default option until X-gp; a pre-selected member would be a money
        decision the page made and the owner never saw, on either widget.
        """
        line, _, _ = self._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )

        pane = self._pane(auth_client, seed_user, line, rows=tokens)

        assert any(
            name == f"consent-{line.id}" and value
            for name, value in reconcile_offerable(pane)
        ), "the pane offered no consent option at all, so nothing is graded"
        assert not [
            value for name, value in reconcile_form_fields(pane)
            if name == f"consent-{line.id}"
        ], "an option is pre-selected"

    def test_a_LONE_row_is_offered_ONE_act_and_no_choice(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GD(a)**'s determinacy: there is nothing to pick.

        A match naming one row is an assertion about that row, so the one
        act is the bank's figure written to it: ONE option, a box rather than
        a radio, valued with the figure alone -- a member is named only where
        there are several to choose between.
        """
        line, _, _ = self._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        # The difference the pane must state, from the scraped token's own
        # figure against the staged line -- so the assertion below is an
        # EXACT value and not a shape the scriptless placeholder could satisfy
        # (adversarial review 2026-09-11).
        expected = str(Decimal("2573.43") - Decimal(tokens[0].split(":")[2]))

        pane = self._pane(auth_client, seed_user, line, rows=tokens[:1])

        assert _consent_options(pane, line.id) == [expected], (
            "a lone row must be offered exactly the one act, valued with the "
            "figure alone"
        )
        assert (f"consent-{line.id}", expected) in reconcile_offerable(pane), (
            "the one act is not a live control a browser could submit"
        )
        assert _option_label(pane, expected).strip().startswith("Write"), (
            "the one act must be the bank's figure written to the row"
        )
        assert re.search(
            rf'type="checkbox"[^>]*name="consent-{line.id}"'
            rf'\s+value="{re.escape(expected)}"',
            pane,
        ), "one act is a box the owner can untick, not a lone radio"

    def test_a_group_that_ADDS_UP_is_offered_NO_choice(
        self, auth_client, db, seed_user,
    ):
        """Nothing would be written wherever it landed.

        The figure still travels, as a HIDDEN ``0.00`` naming no member.
        """
        line, _, _ = self._a_payroll_deposit(seed_user, db, amount="2573.38")
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )

        pane = self._pane(auth_client, seed_user, line, rows=tokens)

        assert "These add up" in pane
        assert _consent_options(pane, line.id) == [], (
            "an agreeing group was offered an act to consent to"
        )
        assert (f"consent-{line.id}", "0.00") in reconcile_form_fields(pane)

    def test_choosing_a_member_states_THAT_ROWS_two_figures(
        self, auth_client, db, seed_user,
    ):
        """`$2,473.43` in place of `$2,473.38`, not the deposit's own total.

        **The sums would name the wrong figure by `$100.00` here.**  A lone
        row's new figure IS the bank total and its old one IS the app total,
        so a sentence written against the sums reads correctly on every match
        that already worked and names a `$2,573.43` deposit as what gets
        written to a `$2,473.38` salary row the moment a group can attribute.
        """
        line, salary, _ = self._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        salary_token = next(
            token for token in tokens if token.split(":")[1] == str(salary.id)
        )

        pane = self._pane(auth_client, seed_user, line, rows=tokens)

        # THE SALARY OPTION'S OWN SENTENCE alone.  The row list, the sums line
        # and the other options legitimately print every figure in this
        # match, so a search over the whole pane would pass whatever the
        # sentence said.  Read off the option whose VALUE names the salary
        # row, so the label graded is the one that value was drawn with.
        sentence = _option_label(pane, f"0.05@{salary_token}")
        assert "Data Manager" in sentence
        assert "$2,473.43" in sentence
        assert "$2,473.38" in sentence
        assert "$2,573.43" not in sentence, (
            "the sentence quoted the DEPOSIT's total as what gets written to "
            "the salary row, which is wrong by $100.00"
        )

    def test_what_the_PANE_emitted_is_what_APPLY_writes(
        self, auth_client, db, seed_user,
    ):
        """The whole loop, on the act this step exists for.

        The page's bytes and the pane's bytes go back to the door together,
        which is the document a browser holds once htmx has swapped the
        fragment in.  **Nothing here is hand-picked**: the row tokens, the
        attribution and the consent figure all come off what was rendered, so
        a template that emitted a field name the schema does not read would
        fail here rather than pass by agreement.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        salary_token = next(
            token for token in tokens if token.split(":")[1] == str(salary.id)
        )
        pane = self._pane(auth_client, seed_user, line, rows=tokens)
        page = _page(auth_client, seed_user)
        # No option is ticked, so the pane's own submitted set carries no
        # consent; ticking the salary option is the owner's act and its value
        # is the server's own -- the figure at the salary row -- read off
        # what the pane rendered.
        consent = next(
            pair for pair in reconcile_offerable(pane)
            if pair == (f"consent-{line.id}", f"0.05@{salary_token}")
        )
        fields = _choosing(
            reconcile_form_fields(page) + reconcile_form_fields(pane),
            f"verb-{line.id}", "match",
        ) + [consent, ("ok", str(line.id))]

        response = _post(auth_client, seed_user, fields, page, pane=pane)

        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 1
        assert settled_figure(salary) == Decimal("2473.43"), (
            "the member the owner named must carry the difference"
        )
        assert settled_figure(allowance) == Decimal("100.00"), (
            "the member they did not name must not move"
        )
        assert not _uncategorized_rows(db, seed_user), (
            "nothing may be minted when a member carries the difference"
        )

    def test_the_ORDINARY_row_option_mints_and_moves_no_member(
        self, auth_client, db, seed_user,
    ):
        """The other act, from the same pane, through its own option.

        **The control for the case above and the whole of R-BI2**: the two
        options carry DIFFERENT values, and each performs its own act.  Under
        the two-control design both bodies submitted ``residual=0.05`` and
        which act ran depended on a second field the consent did not bind.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user, db)
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        pane = self._pane(auth_client, seed_user, line, rows=tokens)
        page = _page(auth_client, seed_user)
        consent = next(
            pair for pair in reconcile_offerable(pane)
            if pair == (f"consent-{line.id}", "0.05")
        )
        fields = _choosing(
            reconcile_form_fields(page) + reconcile_form_fields(pane),
            f"verb-{line.id}", "match",
        ) + [consent, ("ok", str(line.id))]

        response = _post(auth_client, seed_user, fields, page, pane=pane)

        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 1
        assert settled_figure(salary) == Decimal("2473.38"), (
            "a member moved under the ordinary-row option"
        )
        assert settled_figure(allowance) == Decimal("100.00")
        minted = _uncategorized_rows(db, seed_user)
        assert [settled_figure(row) for row in minted] == [Decimal("0.05")], (
            f"R-FN's ordinary row was not minted at the difference: {minted}"
        )

    def test_a_member_the_door_cannot_write_to_is_DISABLED_and_says_why(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-HW**'s shape one control down (X-gp).

        The worked example ``_reject_unrepresentable_landing`` exists for: a
        `+$2,060.00` deposit against a `+$2,050.00` salary row and a `-$50.00`
        deduction row is `$60.00` short, and landing that on the DEDUCTION
        would leave an expense row worth `+$10.00`.  The option is rendered,
        disabled, with the door's own sentence -- and the other two options
        stay live, which is the control against a pane that disabled all of
        them.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2060.00",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        salary = a_transaction(
            seed_user, name="Data Manager", amount="2050.00", income=True,
        )
        deduction = a_transaction(
            seed_user, name="Union Dues", amount="50.00",
        )
        db.session.commit()
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        deduction_token = next(
            token for token in tokens
            if token.split(":")[1] == str(deduction.id)
        )
        salary_token = next(
            token for token in tokens if token.split(":")[1] == str(salary.id)
        )

        pane = self._pane(auth_client, seed_user, line, rows=tokens)

        live = {
            value for name, value in reconcile_offerable(pane)
            if name == f"consent-{line.id}"
        }
        assert f"60.00@{deduction_token}" not in live, (
            "a landing the door refuses is offered as a live option"
        )
        assert f"60.00@{deduction_token}" in _consent_options(pane, line.id), (
            "the refused landing vanished instead of saying why"
        )
        assert "moving the other way" in _option_label(
            pane, f"60.00@{deduction_token}",
        ), "the disabled option does not carry the door's own sentence"
        assert {"60.00", f"60.00@{salary_token}"} <= live, (
            "the options the door would accept are not live"
        )

    def test_UNTICKING_the_named_member_does_not_refuse_the_owner(
        self, auth_client, db, seed_user,
    ):
        """The transition an owner actually performs, found by design review.

        Tick the option naming a member, then untick that member.  The change
        bubbles to `.rec-match-picks` and fires this fragment, and the consent
        control -- outside that wrapper, so not yet re-rendered -- posts its
        now-stale value beside a row list that no longer holds it.
        ``resolve_rows`` refuses exactly that shape, correctly, so a preview
        that read the consent would answer *"This match says its difference
        belongs to a row it does not include.  Reload the page and try
        again"* -- a sentence written for a crafted body, shown for a legal
        click, on the screen whose whole job is to say what the press would
        do.  *A ``_still_ticked`` normalisation held this from X-gj-3a until
        X-gp*; the preview drops the consent whole now, since it offers every
        act rather than the one a member named.

        **Three rows, so that unticking one leaves TWO** and the honest answer
        offers R-FN's ordinary row.  With two rows the untick leaves ONE,
        where R-GD's determinacy answers the question and the pane offers the
        one act -- also right, and not the arm this case is about.
        """
        line, salary, _ = self._a_payroll_deposit(seed_user, db)
        a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        db.session.commit()
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        salary_token = next(
            token for token in tokens if token.split(":")[1] == str(salary.id)
        )
        kept = [token for token in tokens if token != salary_token]
        assert len(kept) == 2, "this case needs two rows left after the untick"

        pane = self._pane(
            auth_client, seed_user, line, rows=kept,
            chosen=f"0.05@{salary_token}",
        )

        assert "does not include" not in pane
        assert "Reload the page" not in pane
        options = _consent_options(pane, line.id)
        assert options and "@" not in options[0], (
            "the panel must offer what Apply would actually do, which is mint"
        )
        assert "row with no category" in _option_label(pane, options[0])

    def test_APPLY_refuses_an_attribution_naming_a_row_it_does_not_carry(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """The ownership question asked of the NEW field, at the MONEY door.

        **The pane is not where this is refused, and that is deliberate.**  A
        fragment that writes nothing reads no consent at all (plan step
        ``bank_import:X-gp``), because it cannot tell a crafted body from an
        owner who has just unticked a row -- and for a read, both want the
        same answer.  What must refuse is the door that WRITES, and it does,
        before it reads the offer set at all.

        The row named here is a second owner's, so it is out of reach two ways
        over: not among the rows this body submitted, and not in this pass's
        offer set.  Neither is stated about the consent's member half until
        something asks, and a field added to a money door without an
        ownership case is how the next one gets added without one.
        """
        line, salary, allowance = self._a_payroll_deposit(seed_user, db)
        foreign = a_transaction(
            seed_second_user, name="Someone else's salary", amount="2473.38",
            income=True,
        )
        db.session.commit()
        tokens = _row_tokens(
            self._pane(auth_client, seed_user, line), line.id,
        )
        pane = self._pane(auth_client, seed_user, line, rows=tokens)
        page = _page(auth_client, seed_user)
        # The pane's own figure, at a row it never offered.
        figure = _consent_options(pane, line.id)[0]
        assert "@" not in figure, "the first option must be the figure alone"
        crafted = _choosing(
            reconcile_form_fields(page) + reconcile_form_fields(pane),
            f"consent-{line.id}",
            f"{figure}@transaction:{foreign.id}:2473.38:{foreign.version_id}",
        ) + [("ok", str(line.id))]
        crafted = _choosing(crafted, f"verb-{line.id}", "match")

        # POSTED DIRECTLY, not through ``_post``: this body names a control
        # value the page could never have rendered, which is the whole point,
        # and that helper exists to refuse exactly such a payload.
        response = auth_client.post(
            _url(seed_user["account"].id),
            data=MultiDict([("csrf_token", "x")] + crafted),
        )

        assert response.status_code == 200
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 0
        assert status_seam.recorded_settlement(salary) is None
        assert status_seam.recorded_settlement(allowance) is None
        assert status_seam.recorded_settlement(foreign) is None
        # **The OWNERSHIP refusal and not the no-consent one**, or a renamed
        # field would leave this arm green while guarding nothing: a body
        # whose consent is not read at all is also refused, with "These do not
        # add up", and every assertion above holds (adversarial review
        # 2026-09-11, the moved-door lesson).
        assert "belongs to a row it does not include" in (
            response.get_data(as_text=True)
        )
