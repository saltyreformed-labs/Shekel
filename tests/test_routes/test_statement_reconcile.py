"""
Shekel Budget App -- The RECONCILE page, through HTTP

Plan step **bank_import:X-gj-1b**; rulings **bank_import:R-HP** through
**R-HX**.  One page on four verbs, replacing the review queue, the register
and the hand-build workbench.

**The cases that matter most read the page and post its own bytes back.**  A
hand-picked payload is written by the same person as the template, so the two
agree about a mistake as readily as about the truth -- and this arc has paid
for that twice, once shipping a destination arm that was DEAD in a browser.
:func:`~tests.test_routes._statement_forms.reconcile_form_fields` scrapes what
a browser would submit, unticked checkboxes and all.

**The ownership 404s are PAIRED with a case asserting the URL still routes.**
A 404 from the URL MAP and a 404 from the ownership gate are indistinguishable
in a response, so moving or renaming a route leaves its IDOR control passing
and guarding nothing -- measured once on a door that DESTROYS budget rows.
"""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.enums import SettledDayBasisEnum, StatusEnum
from app.models.account import Account
from app.models.category import Category
from app.models.merchant_rule import MerchantRule
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import (
    auth_service,
    entry_service,
    pay_calendar,
    status_seam,
    transaction_service,
)
from app.services.row_valuation import settled_figure
from app.services.settle_day import SettleDay
from app.models.statement_line_skip import StatementLineSkip
from app.services.statement_match import (
    REGISTER_LIMIT,
    Tab,
    place_token,
    skip_line,
)
# Pylint: ``shekel-private-module-import`` -- a route test naming the CARD
# KIND a tab holds reaches the service's own value rather than restating its
# three names here, which is the convention this module's siblings keep.
# pylint: disable=shekel-private-module-import
from app.services.statement_match._cards import CardKind
from tests.test_routes._statement_forms import (
    controls_inside_the_trigger,
    form_fields,
    reconcile_form_fields,
    reconcile_offerable,
)
from tests._test_helpers import open_owner_calendar, payback_row_of
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_one_off_envelope,
    an_account_whose_books_hide_a_line,
    a_later_period,
    a_purchase,
    a_rule,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_acts,
    filed_by,
    the_merchant_id,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _url(account_id, tab=None):
    """Return the Reconcile page's URL for *account_id*."""
    suffix = "" if tab is None else f"?tab={tab}"
    return f"/accounts/{account_id}/statements/reconcile{suffix}"


def _merchants_url(account_id):
    """Return the standing-rule offer's door for *account_id*."""
    return f"/accounts/{account_id}/statements/reconcile/merchants"


def _release_url(account_id, tab=None, show_all=False):
    """Return the UNDO door's URL for *account_id*.

    Args:
        account_id: The account.
        tab: Which tab the control was pressed on, or ``None`` for the bare
            URL the default render emits.
        show_all: Whether the render had lifted the bound on settled acts.

    Returns:
        The URL, spelled as the template spells it.
    """
    query = "&".join(
        part for part in (
            None if tab is None else f"tab={tab}",
            "all=1" if show_all else None,
        ) if part is not None
    )
    return (
        f"/accounts/{account_id}/statements/reconcile/release"
        + (f"?{query}" if query else "")
    )


def _match_url(account_id, line_id):
    """Return the MATCH pane's own read endpoint."""
    return (
        f"/accounts/{account_id}/statements/reconcile/line/{line_id}/match"
    )


def _page(auth_client, seed_user, tab=None):
    """Return the rendered Reconcile page for the seeded account."""
    response = auth_client.get(_url(seed_user["account"].id, tab))
    assert response.status_code == 200, (
        f"the page did not render: {response.status_code}"
    )
    return response.get_data(as_text=True)


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


def _choosing(fields, name, value):
    """Return *fields* with *name* set to *value*, replacing what was there.

    **Replacing rather than appending**, because a browser sends one value per
    control and ``request.form.get`` keeps the FIRST: a case that appended its
    choice beside the page's own default would post the default and grade the
    do-nothing arm while claiming to grade the act.  Found by running it.

    Args:
        fields: What the page rendered.
        name: The control to set.
        value: What the owner picked.

    Returns:
        The fields, in document order, with that one control replaced.
    """
    replaced = [pair for pair in fields if pair[0] != name]
    return replaced + [(name, value)]


def _post(auth_client, seed_user, fields, page, pane=None, opened=None):
    """Post *fields* to Apply, refusing anything the owner could not have sent.

    **The refusal is the point, and it is structural rather than a rule to
    remember.**  Every acting case here appended ``("ok", str(line.id))`` to
    what it had scraped, and that value is one a browser could not produce for
    most cards: the ``ok-<line>`` checkbox is rendered only where a card
    suggests a verb whose door exists, so 31 of the developer's 248 cards
    carried a panel button pointing at an element that was not in the
    document -- a primary control, dead in a browser, with every server test
    green.  A helper that accepts whatever a caller appends turns a faithful
    scrape back into a hand-picked payload, and the next author in a hurry
    re-abuses it; this one cannot be.

    **It is asked as a PAIR for a checkbox or radio.**  ``ok`` is one name
    shared by every card and keyed by its value, so a check that only asked
    whether the page renders ``ok`` anywhere would pass exactly the payload
    that hid the defect.

    A case that genuinely needs to post an unrendered control -- a crafted
    body, a stale page -- posts through ``auth_client`` directly and says so,
    which is the moment the question gets asked out loud.

    Args:
        auth_client: The logged-in client.
        seed_user: The seeded user bundle.
        fields: The ``(name, value)`` pairs to submit, scraped and then
            pressed.
        page: The rendered page they were scraped from.
        pane: A MATCH pane htmx has SWAPPED INTO that page, or ``None``.

            **The pane's controls are the page's once it has loaded**, and
            that is the document a browser submits: the fragment replaces the
            placeholder inside the card, so its rows, its attribution select
            and its consent box are all inside the cards form.  Without this
            the refusal above would reject the very payload a browser sends
            for the one act that needs the pane open -- a group whose
            difference the owner directed at a member (plan step
            ``bank_import:X-gj-3a``).

            It is a SECOND document rather than a flag, so the universe stays
            *what was actually rendered* rather than *what a caller says is
            allowed*: a control in neither document is still refused.
        opened: The bank line the page was opened on with ``?open=``, or
            ``None``.  **The form's own action carries it** (plan step
            ``bank_import:X-gi-1``), so a press from an opened page posts to
            that URL and not to the bare one -- and since plan step
            ``bank_import:X-gi-2a`` the difference is what the answer prices
            the pane from.  **The URL is SCRAPED from the page's Apply form
            and asserted to name that line**, rather than composed here
            (adversarial review 2026-09-11): a re-drawn page whose action had
            dropped ``open=`` would otherwise be pressed at a URL it never
            rendered, and the door reads the rows and consent from the body
            either way, so nothing else would notice.

    Returns:
        The response.

    Raises:
        AssertionError: When a field names a control neither document
            rendered, or gives a checkbox or radio a value it could not have
            sent.
    """
    offerable = reconcile_offerable(page)
    if pane is not None:
        offerable = offerable | reconcile_offerable(pane)
    for name, value in fields:
        if name == "csrf_token":
            continue
        assert (name, value) in offerable or (name, None) in offerable, (
            f"({name!r}, {value!r}) is not something this page rendered, so a "
            f"browser could never submit it -- post it through auth_client "
            f"directly if the case is about a crafted or stale body"
        )
    target = _url(seed_user["account"].id)
    if opened is not None:
        target = _apply_action(page)
        assert target.endswith(f"?open={opened}"), (
            f"the page's Apply form posts to {target!r}, which does not name "
            f"the card it was opened on"
        )
    return auth_client.post(
        target, data=MultiDict([("csrf_token", "x")] + list(fields)),
    )


def _apply_action(page):
    """Return the Apply form's ``action``, exactly as the page emits it.

    Scraped rather than composed, which is :func:`_undo_control`'s rule for
    the Undo form one door over.

    Args:
        page: The rendered page or body.

    Returns:
        The URL.

    Raises:
        AssertionError: When the page renders no Apply form at all.
    """
    found = re.search(
        r'<form method="post"\s+action="([^"]*/statements/reconcile[^"]*)"',
        page,
    )
    assert found is not None, "the page renders no Apply form"
    return found.group(1).replace("&amp;", "&")


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


def _unskip_url(account_id, tab=None, show_all=False):
    """Return the SKIP undo door's URL for *account_id*.

    Args:
        account_id: The account.
        tab: Which tab the control was pressed on, or ``None`` for the bare
            URL.
        show_all: Whether the render had lifted the bound on recorded skips.

    Returns:
        The URL, spelled as the template spells it.  **It takes ``all`` exactly
        as :func:`_release_url` does** (**R-JW**): the tab
        bounds at ``REGISTER_LIMIT`` and offers a link past it, so an undo
        pressed while showing everything has a view to come back to.
    """
    query = "&".join(
        part for part in (
            None if tab is None else f"tab={tab}",
            "all=1" if show_all else None,
        ) if part is not None
    )
    return (
        f"/accounts/{account_id}/statements/reconcile/unskip"
        + (f"?{query}" if query else "")
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


def _a_skipped_line(seed_user, db, merchant="Target", amount="-9.99"):
    """Stage one outflow and record a skip of it through the door.

    Args:
        seed_user: The seeded user bundle.
        db: The session fixture.
        merchant: What the bank names the merchant.
        amount: Signed, negative OUT of the account.

    Returns:
        ``(line, SkippedLine)``.
    """
    line = an_unexplained_outflow(
        seed_user, merchant=merchant, amount=amount,
    )
    db.session.commit()
    recorded = skip_line(
        line.id, seed_user["user"].id, seed_user["account"].id,
    )
    db.session.commit()
    return line, recorded


def _a_swipe_a_rule_files(seed_user, db, merchant="Lowe's"):
    """Stage one outflow whose merchant the owner has answered for.

    Returns:
        The envelope the rule names, so a case can assert what it recorded.
    """
    envelope = an_envelope(seed_user, name="Home Improvement")
    line = an_unexplained_outflow(
        seed_user, merchant=merchant, amount="-35.72",
    )
    db.session.commit()
    a_rule(
        seed_user, merchant,
        template_id=envelope.template_id,
    )
    db.session.commit()
    return envelope, line


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
            # ``test_reconcile.TestWhichKindOfCARDAPageHoldsIsSTATEDByWhatBuiltThem``
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


class TestARuleNamingAOneOffEnvelopePlacesItsRowFromThisPage:
    """Leaf 7b-3 of balance:X-bi-7b (ruling R-BAL24): the PLACE option, end to end.

    A standing TEMPLATE answer names a one-off envelope's DEFINITION, and the
    line falls in a paycheck holding no row of it.  The card offers ONE
    option for that -- the placement's own token -- and OK then Apply places
    the definition's row there and records the purchase into it.  Kept in
    its own class: ``bank_import:X-gz`` lands its cases in this module too.
    """

    def test_the_card_offers_the_place_option_and_apply_places_the_row(
        self, auth_client, db, seed_user,
    ):
        """Rendered selected, posted as rendered, placed by the door."""
        existing = a_one_off_envelope(seed_user, name="Amazon")
        definition_id = existing.template_id
        later = a_later_period(seed_user)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-22.10",
            posted_on=later.start_date + timedelta(days=2),
            description="POINT OF SALE DEBIT L340 (Amazon)", merchant="Amazon",
        )
        db.session.commit()
        a_rule(seed_user, "Amazon", template_id=definition_id)
        db.session.commit()

        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)
        token = place_token(definition_id)
        assert (f"destination-{line.id}", token) in fields, (
            "the card did not render the PLACE option selected for this line"
        )
        assert "placed in this paycheck" in page

        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        rows = (
            db.session.query(Transaction)
            .filter_by(template_id=definition_id, is_deleted=False)
            .order_by(Transaction.id).all()
        )
        assert [row.id for row in rows][0] == existing.id and len(rows) == 2
        placed = rows[1]
        assert placed.pay_period_id == later.id
        entry = db.session.query(TransactionEntry).one()
        assert entry.transaction_id == placed.id
        assert entry.amount == Decimal("22.10")


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


def _row_tokens(pane, line_id):
    """Return every candidate-row token the pane rendered, ticked or not.

    :func:`~tests.test_routes._statement_forms.reconcile_form_fields` keeps
    only what a browser would SUBMIT, which is the right reader for a form and
    the wrong one for "what did this pane offer" -- an unticked box submits
    nothing.  This reads the offer.

    Args:
        pane: The rendered MATCH pane.
        line_id: The bank line it is about.

    Returns:
        The reviewed-row tokens, in document order.
    """
    import re  # pylint: disable=import-outside-toplevel

    return re.findall(
        rf'name="rows-{line_id}" value="([^"]+)"', pane,
    )


def _consent_options(pane, line_id):
    """Return every consent OPTION the pane rendered, live or disabled.

    Plan step ``bank_import:X-gp``.  :func:`~tests.test_routes._statement_forms
    .reconcile_offerable` keeps what a browser could SUBMIT, which leaves out
    a disabled option -- and whether a refused landing is still SHOWN is one
    of the things this file grades.  This reads the options' values in
    document order, hidden fields excluded.

    **The scriptless PLACEHOLDER is not an option and is not returned.**  The
    pane renders it as a disabled box with ``value=""`` in lockstep, so an
    empty value is that placeholder by the template's own contract -- and a
    reader that returned it let the lone-row case pass against a pane that
    offered NO act (adversarial review 2026-09-11).  A real option carries
    the figure, so none can be empty.

    Args:
        pane: The rendered MATCH pane.
        line_id: The bank line it is about.

    Returns:
        The consent values, in document order.
    """
    return [
        value for kind, value in re.findall(
            rf'type="(radio|checkbox)"[^>]*name="consent-{line_id}"'
            rf'\s+value="([^"]*)"',
            pane,
        )
        if value
    ]


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
        The uncategorised, non-transfer transactions on the seeded account.
    """
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == seed_user["account"].id,
            Transaction.category_id.is_(None),
            Transaction.transfer_id.is_(None),
        )
        .all()
    )


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


class TestTheReceiptOffersOneStandingRulePerMerchant:
    """Ruling **bank_import:R-IB** (developer, 2026-08-30).

    The card carried an *always, for this merchant* checkbox until that
    ruling. A standing rule is ONE fact per merchant and a card is one LINE,
    so on the developer's own pass the page asked one question **86 times** --
    Amazon 26, Walmart 13, Food Lion 12. The offer is on the RECEIPT now,
    once per merchant, about what the door actually APPLIED.
    """

    @staticmethod
    def _ok_a_swipe(auth_client, seed_user, line, envelope):
        """OK one card into *envelope* and return the answering body."""
        page = _page(auth_client, seed_user)
        chosen = _choosing(
            reconcile_form_fields(page),
            f"destination-{line.id}", str(envelope.id),
        )
        response = _post(
            auth_client, seed_user, chosen + [("ok", str(line.id))], page,
        )
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_the_receipt_offers_the_rule_the_pass_actually_filed(
        self, auth_client, db, seed_user,
    ):
        """The offer names the merchant, the count and where it went."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)

        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)

        assert "Should any of these stand?" in body
        assert "Always file Lowe&#39;s in" in body
        assert f'name="rule_merchant-{the_merchant_id(seed_user, "Lowe\'s")}"' in body

    def test_pressing_it_records_the_rule(
        self, auth_client, db, seed_user,
    ):
        """The offer's own bytes go back to the shipped rule door."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)
        db.session.query(MerchantRule).delete()
        db.session.commit()

        offered = form_fields(body, "/reconcile/merchants")
        chosen = _choosing(
            offered, f"rule-{merchant_id}", f"t:{envelope.template_id}",
        )
        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile/merchants",
            data=MultiDict([("csrf_token", "x")] + chosen),
        )

        assert response.status_code == 200
        rule = db.session.query(MerchantRule).filter_by(
            merchant_id=merchant_id,
        ).one()
        assert rule.template_id == envelope.template_id

    def test_the_offer_OPENS_on_not_now_and_writes_nothing_unpressed(
        self, auth_client, db, seed_user,
    ):
        """Nothing is pre-selected: the owner has not said it yet.

        The destination they picked is a fact about THIS purchase; whether it
        should stand is a different question about the future, and a radio
        group arriving on the answer would make Save record something nobody
        chose.
        """
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)
        db.session.query(MerchantRule).delete()
        db.session.commit()

        offered = form_fields(body, "/reconcile/merchants")
        assert (f"rule-{merchant_id}", "unset") in offered, (
            "the offer arrived pre-selected, so Save would record an answer "
            "the owner never gave"
        )

        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile/merchants",
            data=MultiDict([("csrf_token", "x")] + offered),
        )

        assert response.status_code == 200
        assert db.session.query(MerchantRule).count() == 0

    def test_ONE_offer_however_many_lines_that_merchant_has(
        self, auth_client, db, seed_user,
    ):
        """The grain the ruling corrects, at the smallest size that shows it.

        Two Lowe's swipes filed into the same envelope are ONE answer, not
        two: a rule keys on ``template_id``, so every pay period's copy of an
        envelope is the same rule. On the developer's own data this is why the
        contradiction rate is zero across 10 repeated merchants.
        """
        envelope, first = _a_swipe_a_rule_files(seed_user, db)
        second = an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-12.10",
        )
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Lowe's")

        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)
        for line in (first, second):
            fields = _choosing(
                fields, f"destination-{line.id}", str(envelope.id),
            )
        response = _post(auth_client, seed_user, fields + [
            ("ok", str(first.id)), ("ok", str(second.id)),
        ], page)

        body = response.get_data(as_text=True)
        assert body.count(f'name="rule_merchant-{merchant_id}"') == 1, (
            "one merchant was asked about more than once"
        )
        assert "You filed 2 <strong>Lowe&#39;s</strong>" in body


class TestTheROUTEHandsTheOfferWhatTheDoorAPPLIED:
    """The SEAM, graded where the two derivations differ.

    ``rules_worth_offering`` takes the APPLIED items as a PARAMETER (the
    id set ``applied_line_ids`` until plan step ``bank_import:X-gx``, the
    items themselves since), so a service test can only grade what a caller
    passes -- and an adversarial review proved the gap by hand: replacing the
    route's

        frozenset(l for item in outcome.applied for l in item.line_ids)

    with the pre-ruling ``frozenset(item["line_id"] for item in creations)``
    left **11,969 tests green**, shipping the exact regression ruling
    **bank_import:R-IB** exists to make unconstructible.  The route hands
    ``outcome.applied`` now, and the equivalent regression is an item built
    from the submission rather than from the door.

    **Choosing the refusal is the whole difficulty**, because
    ``rules_worth_offering`` has more than one way to drop a line and only
    one of them is the seam. A destination the pass does not offer is dropped
    by its own arm, and since plan step ``bank_import:X-gx`` a line no applied
    item names a merchant for -- not applied, or merchant-less -- is dropped
    by the other (a line that was not ``creatable`` had an arm of its own
    until then; an answer the rule door would refuse is not dropped but
    carried as ``blocked``). The refusal used here trips none of them: a line
    past the saved pay calendar
    IS creatable, is filed to a NEW envelope under an ACTIVE category -- an
    answer the rule door takes -- and the create door refuses it by name at
    ``scope.period_holding``. So the only thing that can keep it out of the
    offer is the narrowing this class is about.
    """

    def test_a_line_the_door_REFUSED_is_not_in_the_receipts_offer(
        self, auth_client, db, seed_user,
    ):
        """One merchant lands, one is refused; only the lander is offered."""
        envelope, lands = _a_swipe_a_rule_files(seed_user, db)
        db.session.query(MerchantRule).delete()
        far = a_bank_line(
            seed_user, an_import(seed_user), amount="-40.00",
            posted_on=date(2031, 3, 4), merchant="Faraway Co",
        )
        db.session.commit()
        category = db.session.query(Category).filter(
            Category.user_id == seed_user["user"].id,
            Category.is_active.is_(True),
        ).first()

        # **Posted directly and not through ``_post``**, and deliberately: the
        # far line's ADD tab is SHUT (its ``withheld`` names the missing pay
        # period), so no card renders a destination for it and no browser
        # could send this. A stale page is how it arises -- the calendar is
        # extended, the page is drawn, the schedule is rolled back -- and the
        # per-item SAVEPOINT is what this is about either way.
        response = auth_client.post(
            _url(seed_user["account"].id),
            data=MultiDict([
                ("csrf_token", "x"),
                ("ok", str(lands.id)),
                (f"verb-{lands.id}", "add"),
                (f"destination-{lands.id}", str(envelope.id)),
                ("ok", str(far.id)),
                (f"verb-{far.id}", "add"),
                (f"destination-{far.id}", "new"),
                (f"envelope_name-{far.id}", "Faraway"),
                (f"category_id-{far.id}", str(category.id)),
            ]),
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "No pay period covers 2031-03-04" in body, (
            "the door did not refuse the far line, so this grades nothing"
        )
        assert f'name="rule_merchant-{the_merchant_id(seed_user, "Lowe\'s")}"' \
            in body, "the merchant that LANDED earned no offer"
        assert 'name="rule_merchant-' + str(
            the_merchant_id(seed_user, "Faraway Co"),
        ) + '"' not in body, (
            "a purchase the door REFUSED earned a standing-rule offer -- the "
            "next import would auto-file that merchant with no press"
        )


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
        pressing: nothing else in this file would notice it going away.
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


class TestANeverAnswerReturnsItsLineToTheInbox:
    """Ruling **bank_import:R-JH**, plan step ``bank_import:X-gj-4c-1``.

    **Read off the markup a browser would receive**, because what this step
    moved is where a line RENDERS and the service test one tier down cannot
    see a tab bar, an Apply form or a link.  A standing *never a purchase*
    answer shuts the ADD door and claims nothing about what the line is, so
    the line is inbox work rather than a disposition.

    **The staging is deliberately an ORDINARY swipe merchant.**  A merchant a
    source ALSO files as paying an account the owner holds is a transfer
    whatever they answered, stays parked, and is graded by
    :class:`TestAVerbWithNoDoorRendersNoControl`.
    """

    @staticmethod
    def _a_never_answered_swipe(seed_user, db):
        """Stage one outflow whose merchant the owner answered NEVER for.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            The staged bank line.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Foundation Donation", amount="-4.00",
        )
        a_rule(seed_user, "Foundation Donation")
        db.session.commit()
        return line

    def test_the_card_is_on_TO_EXPLAIN_under_nothing_suggested(
        self, auth_client, db, seed_user,
    ):
        """The tab bar counts it as work, and the section heading says so."""
        self._a_never_answered_swipe(seed_user, db)

        page = _page(auth_client, seed_user)

        assert "Foundation Donation" in page
        assert "Nothing suggested" in page

    def test_it_is_on_NEITHER_holding_tab(self, auth_client, db, seed_user):
        """Both halves, and the TRANSFERS half is the one that renders money.

        Leaving the line in ``parked`` and merely dropping the tab arm would
        total that list on TRANSFERS, whose holding chip carries a COUNT and a
        MAGNITUDE -- so the line would arrive under a rendered money figure the
        bank never filed as a payment.
        """
        self._a_never_answered_swipe(seed_user, db)

        transfers = _page(auth_client, seed_user, "transfers")
        skipped = _page(auth_client, seed_user, "skipped")

        assert "Foundation Donation" not in transfers
        assert "Foundation Donation" not in skipped
        assert "waiting for the account they paid" not in transfers

    def test_ADD_renders_the_owner_s_own_reason_and_NO_destination_chooser(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GJ**'s bar, one tab over from where it used to render.

        **The chooser is what this asserts the absence of**, not the sentence:
        a warning paragraph over a working select is the shape that ruling cost
        `$7,412.94` to learn, and putting these lines back in the inbox is
        exactly the change that could have reintroduced it.
        """
        line = self._a_never_answered_swipe(seed_user, db)

        page = _page(auth_client, seed_user)

        assert "You have said Foundation Donation is never a purchase" in page
        assert f'name="destination-{line.id}"' not in page, (
            "a barred line must render no destination chooser, on any tab"
        )

    def test_it_keeps_the_CONSENT_and_the_door_that_changes_the_answer(
        self, auth_client, db, seed_user,
    ):
        """**Why R-HQ is not breached by putting this line in the inbox.**

        MATCH is open, so the card has an act and the ``ok`` checkbox has to be
        in the document for it -- the same reason a parked card payment keeps
        one.  And unlike that line this one has an answer worth changing, so
        the panel names the merchants page.
        """
        line = self._a_never_answered_swipe(seed_user, db)
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        db.session.commit()

        page = _page(auth_client, seed_user)

        assert f'name="ok" value="{line.id}"' in page, (
            "a line whose only shut door is ADD must still be matchable"
        )
        assert "Change what you have said about Foundation Donation" in page


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
    (``test_reconcile.TestTheSettledBoundIsLIFTABLE``) at a parameterised
    limit.  What only the route can show is that the page passes the SHIPPED
    :data:`~app.services.statement_match.REGISTER_LIMIT`, says what it
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


class TestThePaneOffersWHEREADifferenceGoes:
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

    @staticmethod
    def _a_payroll_deposit(seed_user, db, amount="2573.43"):
        """Stage one deposit against a salary row and a flat allowance.

        Args:
            seed_user: The seeded user bundle.
            db: The session.
            amount: What the bank deposited.  The two rows always come to
                `$2,573.38`, so the default leaves the `$0.05` gap and an
                exactly-equal figure leaves none.

        Returns:
            ``(line, salary, allowance)``.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount=amount,
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL",
        )
        salary = a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Health Insurance Allowance", amount="100.00",
            income=True,
        )
        db.session.commit()
        return line, salary, allowance

    def _pane(self, auth_client, seed_user, line, rows=(), chosen=None):
        """Render the MATCH pane as it stands with *rows* ticked.

        Args:
            auth_client: The logged-in client.
            seed_user: The seeded user bundle.
            line: The bank line whose card is open.
            rows: The reviewed-row tokens the owner has ticked.
            chosen: The consent value the owner's tick submits, or ``None``.

        Returns:
            The rendered pane, as text.
        """
        body = [("csrf_token", "x")]
        body += [(f"rows-{line.id}", token) for token in rows]
        if chosen is not None:
            body.append((f"consent-{line.id}", chosen))
        return auth_client.post(
            _match_url(seed_user["account"].id, line.id),
            data=MultiDict(body),
        ).get_data(as_text=True)

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


class TestTheMatchPaneIsReachableWithNoScript:
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

    **Every other case in this file reads BYTES.**  Both adversarial reviews
    of 2026-09-05 said the same thing about that: nothing asserted the parked
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
        line, _ = TestTheMatchPaneIsReachableWithNoScript(
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
        line, _ = TestTheMatchPaneIsReachableWithNoScript(
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
        line, _ = TestTheMatchPaneIsReachableWithNoScript(
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
        pane_of = TestThePaneOffersWHEREADifferenceGoes()
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


def _open(auth_client, seed_user, line_id, tab=None):
    """Return the Reconcile page with one card's MATCH pane in the document.

    Args:
        auth_client: The logged-in client.
        seed_user: The seeded user bundle.
        line_id: The bank line to open.
        tab: Which tab, or ``None`` for the inbox.

    Returns:
        The rendered page, as text.
    """
    query = f"?open={line_id}" if tab is None else f"?tab={tab}&open={line_id}"
    response = auth_client.get(
        f"/accounts/{seed_user['account'].id}/statements/reconcile{query}"
    )
    assert response.status_code == 200, (
        f"the page did not render: {response.status_code}"
    )
    return response.get_data(as_text=True)


def _card_markup(page, line_id):
    """Return one card's own markup, from its element to the next card's.

    Args:
        page: The rendered Reconcile page.
        line_id: The bank line whose card to cut out.

    Returns:
        That card's markup, as text.
    """
    opened = page.index(f'id="rec-card-{line_id}"')
    nxt = page.find('id="rec-card-', opened + 1)
    return page[opened:] if nxt == -1 else page[opened:nxt]
