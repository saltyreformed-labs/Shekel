"""The settled tabs: where an act a person ticked or a standing rule filed is found and undone, and
the settled bound wired to the page.
"""

import re
from datetime import timedelta

import pytest

from app.models.statement_match import StatementMatch
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.services.statement_match import REGISTER_LIMIT
from tests.test_services.test_statement_match._builders import (
    an_envelope,
    an_unexplained_outflow,
    filed_acts,
    filed_by,
)

from ._helpers import _url, _release_url, _page, _a_swipe_a_rule_files


def _undo_control(page):
    """Return the first Undo form's ``(action, match_id)``, as the page emits it.

    **Scraped rather than composed**, which is this module's own rule: the
    action carries the tab and the bound in its query string, and a case that
    rebuilt that URL would grade the route while the card pointed somewhere
    else.

    Args:
        page: The rendered page.

    Returns:
        The pair, or ``None`` when the page renders no Undo at all.
    """
    found = re.search(
        r'<form method="post" action="([^"]*release[^"]*)"'
        r'(?:.|\n)*?name="match_id" value="(\d+)"',
        page,
    )
    return None if found is None else (
        found.group(1).replace("&amp;", "&"), found.group(2),
    )


class TestTheSettledTabsAreWhereAnActIsFoundAndUndone:
    """Plan step ``bank_import:X-gj-1c``; rulings **R-HU** and **R-GY**.

    The register's whole job, on two tabs: the acts a person ticked, the acts
    a standing rule filed, each with the Undo that removes what it created.

    **The cases press what the PAGE emits.**  A hand-written undo payload is
    written by the same person as the template, and this arc has already
    shipped a primary control that was dead in a browser because a test
    appended a value no rendered form could have sent.  So the ``match_id``
    and the action URL below are scraped out of the rendered card.
    """

    def _an_act(self, seed_user, db, merchant, envelope, *, by_rule, seq=0):
        """File one bank line as a purchase, by hand or by a standing rule.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            merchant: The merchant whose line to file.
            envelope: The budget line to file it into.
            by_rule: Whether a STANDING RULE performed it (**R-GT**), which is
                the only fact deciding which of the two tabs holds it.
            seq: Which sequence the staged line takes, so two lines on one day
                are distinguishable.

        Returns:
            The :class:`~app.services.statement_match._creations
            .CreatedPurchase` the door reports, whose ``entry_id`` is the row
            an undo would take back.
        """
        line = an_unexplained_outflow(
            seed_user, merchant=merchant, amount="-12.34", sequence=seq,
        )
        db.session.commit()
        return filed_by(seed_user, line, envelope, by_rule=by_rule)

    def test_each_tab_renders_its_OWN_half_with_an_undo_on_every_card(
        self, auth_client, db, seed_user,
    ):
        """The partition is real on both sides, through HTTP.

        On an account with no acts every assertion here is satisfied by zero,
        which is why both are staged.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False, seq=1)
        db.session.commit()

        by_hand = _page(auth_client, seed_user, "explained")
        by_rule = _page(auth_client, seed_user, "filed_by_rules")

        for page in (by_hand, by_rule):
            assert page.count('name="match_id"') == 1, "one card, one Undo"
            assert page.count('class="rec-card rec-act"') == 1
        assert "Amazon" in by_rule and "Amazon" not in by_hand
        assert "Walmart" in by_hand and "Walmart" not in by_rule

    def test_pressing_the_rendered_undo_really_releases_the_act(
        self, auth_client, db, seed_user,
    ):
        """The round trip: read the page, post ITS bytes, read the database.

        **The purchase must come back too.**  Releasing removes the rows the
        act created (**R-GG**), so a case asserting only that the match row
        went would pass over an undo that left `-$12.34` of spending behind
        with nothing explaining it.
        """
        envelope = an_envelope(seed_user)
        purchase = self._an_act(
            seed_user, db, "Walmart", envelope, by_rule=False,
        )
        db.session.commit()
        purchase_id = purchase.entry_id

        page = _page(auth_client, seed_user, "explained")
        action, match_id = _undo_control(page)
        response = auth_client.post(
            action, data={"csrf_token": "x", "match_id": match_id},
        )
        db.session.expire_all()

        assert response.status_code == 302
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.get(TransactionEntry, purchase_id) is None

    def test_the_undo_comes_back_to_the_tab_it_was_pressed_on(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-HU**'s tab is part of the page the control was on.

        Redirecting to the bare URL would drop the reader onto the inbox --
        which is the defect that made ``release_and_return`` take a target at
        all, one surface earlier.  Asserted on FILED BY RULES rather than on
        Explained, because Explained's own value would be indistinguishable
        from a hard-coded first settled tab.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        db.session.commit()

        page = _page(auth_client, seed_user, "filed_by_rules")
        action, match_id = _undo_control(page)
        response = auth_client.post(
            action, data={"csrf_token": "x", "match_id": match_id},
        )

        assert action == _release_url(
            seed_user["account"].id, tab="filed_by_rules",
        )
        assert response.headers["Location"] == _url(
            seed_user["account"].id, "filed_by_rules",
        )

    def test_an_act_that_no_longer_holds_is_FIRST_and_says_so(
        self, auth_client, db, seed_user,
    ):
        """The one thing on a settled tab a reader must act on.

        A cascade elsewhere can take a row a match names and leave the act
        standing, explaining less than it claims -- so such an act sorts above
        every agreeing one whatever its age, and the card says why.  Staged by
        moving a member's day, which is what a later hand edit produces.

        **The OLDER act is the one drifted, and that is the whole case.**
        ``acts_of`` orders ``created_at DESC, id DESC``, so drifting the newer
        one leaves it first for a reason that has nothing to do with agreement
        -- and deleting the sort in ``accepted_register`` left an earlier
        version of this case green.  Measured 2026-08-31 by adversarial
        test-quality review, which is also where the fix came from.
        """
        envelope = an_envelope(seed_user)
        drifted = self._an_act(
            seed_user, db, "Amazon", envelope, by_rule=False,
        )
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False, seq=1)
        db.session.commit()
        # THE ROW ITSELF, reached by the id the door reports: a hand edit that
        # moves a member's day off the one the act asserted is exactly what
        # `AcceptedGroup.agrees` reports, and it is an ordinary thing an owner
        # can do on the grid.
        row = db.session.get(TransactionEntry, drifted.entry_id)
        row.settled_on = row.settled_on + timedelta(days=3)
        db.session.commit()

        page = _page(auth_client, seed_user, "explained")

        assert page.index("Amazon") < page.index("Walmart"), (
            "the act that stopped holding must sort above a NEWER one that "
            "still holds, which is the only thing the sort does"
        )
        assert "This no longer holds -- re-review it." in page
        assert "not the bank's day" in page

    def test_an_undo_the_door_would_REFUSE_says_so_and_offers_no_dialog(
        self, auth_client, db, seed_user,
    ):
        """The macro's whole argument, on the surface that replaces the register.

        A control may not promise what the button will not do, so an act whose
        undo the door would refuse renders the refusal and NO confirmation --
        a dialog before a refusal is the dialog-for-nothing ruling **R-GY**'s
        argument is actually about.  Both come from
        :func:`~app.services.statement_match._release.planned_removals`, the
        door's own derivation.

        **Staged the way the register's own case stages it**: a created
        purchase the owner has EDITED since is their record, so removing it
        would throw that away -- which is one of the three things that refuse.
        Nothing rendered this arm on a settled TAB until now, though the macro
        it shares was covered one surface over.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()
        entry = db.session.query(TransactionEntry).one()
        entry_service.update_entry(
            entry.id, seed_user["user"].id, description="Walmart -- hose",
        )
        db.session.commit()

        page = _page(auth_client, seed_user, "explained")

        assert "Undo is refused:" in page
        assert "you have edited that row since" in page
        assert "Undo removes" not in page
        assert "data-confirm=" not in page
        # The BUTTON is still there: the refusal is a disclosure the owner can
        # act on by un-editing the row, not a control withdrawn from them.
        assert 'name="match_id"' in page

    def test_the_tab_shows_no_other_owner_s_acts(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """The ownership 404 covers the DOOR; this covers the CONTENT.

        A route can be perfectly gated and still fold another owner's rows
        into the page it renders for the caller, which no 404 case can see --
        so this stages an act on each account and asserts the tab holds one.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()

        page = _page(auth_client, seed_user, "explained")

        assert page.count('name="match_id"') == 1
        assert db.session.query(StatementMatch).count() == 1, (
            "only one act exists at all, so this case cannot yet tell a "
            "scoped fold from an unscoped one"
        )

    def test_a_settled_tab_offers_NO_apply_and_no_OK(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-HW**: no control that cannot succeed.

        There is nothing on a settled tab to Apply -- no OK checkbox, no
        sweep, no batch door -- so rendering the footer band would be a button
        whose press does nothing.  The inbox is asserted beside it, because
        "absent everywhere" would satisfy the first three lines alone.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        _a_swipe_a_rule_files(seed_user, db, merchant="Lowe's")
        db.session.commit()

        settled = _page(auth_client, seed_user, "explained")
        inbox = _page(auth_client, seed_user)

        assert 'name="ok"' not in settled
        assert "rec-footer" not in settled
        assert "apply_statement_reconcile" not in settled
        assert 'name="ok"' in inbox
        assert "rec-footer" in inbox

    def test_an_empty_settled_tab_renders_no_card_and_no_undo_prose(
        self, auth_client, db, seed_user,
    ):
        """An empty section is ABSENT rather than rendered empty.

        And the paragraph explaining what Undo does goes with the list: over
        no cards it is a page describing a control it does not render.
        """
        # STAGED AND COMMITTED FIRST, then both tabs read.  A render begins
        # its own snapshot and refuses one that has already written, so a page
        # fetched between a flush and a commit is not a state any request can
        # reach.
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()

        # FILED BY RULES is empty -- that act was ticked by a person -- and
        # EXPLAINED holds it, so one account shows both arms.
        empty = _page(auth_client, seed_user, "filed_by_rules")
        populated = _page(auth_client, seed_user, "explained")

        assert "Nothing on this tab." in empty
        assert 'name="match_id"' not in empty
        assert "Undoing an act puts its statement line(s)" not in empty
        # THE POSITIVE TWIN.  A bare `not in` is satisfied by deleting the
        # paragraph outright, which would take the page's only statement of
        # what Undo destroys with it.
        assert "Nothing on this tab." not in populated
        assert "Undoing an act puts its statement line(s)" in populated


class TestTheSettledBoundIsWiredToThePage:
    """Ruling **R-GX**'s bound and the link past it, through the real route.

    The arithmetic is the service tier's
    (``test_reconcile.test_the_cards_and_skips.TestTheSettledBoundIsLIFTABLE``) at a
    parameterised limit.  What only the route can show is that the page passes
    the SHIPPED :data:`~app.services.statement_match.REGISTER_LIMIT`, says what it
    withheld, offers a link that lifts it, and CARRIES that view through an
    Undo -- so this stages one act past the real boundary and drives it.

    **Retiring the register makes this load-bearing rather than a nicety**
    (**R-HU**): on the developer's own account the bound withholds 171 of 221
    acts, and without the link those 171 would be out of reach.
    """

    @pytest.mark.parametrize(
        ("by_rule", "tab"),
        [(False, "explained"), (True, "filed_by_rules")],
    )
    def test_it_cuts_at_the_limit_offers_the_rest_and_the_undo_keeps_it(
        self, auth_client, db, seed_user, by_rule, tab,
    ):
        """One act past the bound: the cut, the count, the link, the press.

        **Both halves, because the page threads the bound to each arm
        separately.**  An earlier version staged ``by_rule=False`` only, and
        adversarial test-quality review measured what that hid: the
        FILED_BY_RULES arm could ignore ``limit`` entirely -- passing the
        shipped constant instead of the parameter -- with 796 tests green,
        which is a permanently dead *show the rest* link on that tab.
        """
        filed_acts(seed_user, REGISTER_LIMIT + 1, by_rule=by_rule)

        bounded = _page(auth_client, seed_user, tab)
        # **FOLLOWED, not reconstructed.**  A case that builds the unbounded
        # URL by hand grades the route and never the LINK -- measured by
        # adversarial test-quality review 2026-08-31, which renamed the
        # anchor's query argument to one nothing reads and ran 4,712 tests
        # green.  This is the only path to 171 of the developer's 221 acts.
        more = re.search(
            r'<p class="rec-more small">\s*<a href="([^"]+)"', bounded,
        )
        assert more is not None, "the bounded tab offered no way past the bound"
        everything = auth_client.get(
            more.group(1).replace("&amp;", "&"),
        ).get_data(as_text=True)

        assert bounded.count('name="match_id"') == REGISTER_LIMIT
        assert "the other 1 act(s)" in " ".join(bounded.split())
        assert "Every act on this tab is listed." not in bounded
        assert everything.count('name="match_id"') == REGISTER_LIMIT + 1
        assert "Every act on this tab is listed." in everything
        # The view rides the Undo, so a press while showing everything does
        # not collapse the record under the owner mid-read.
        action, match_id = _undo_control(everything)
        assert action == _release_url(
            seed_user["account"].id, tab=tab, show_all=True,
        )
        assert auth_client.post(
            action, data={"csrf_token": "x", "match_id": match_id},
        ).headers["Location"] == (
            f"{_url(seed_user['account'].id, tab)}&all=1"
        )
