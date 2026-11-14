"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_statement_reconcile.py``, moved verbatim by ``tools/split``.
"""

import re

from werkzeug.datastructures import MultiDict

from app.services.statement_match import skip_line
from tests.test_routes._statement_forms import reconcile_offerable
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_rule,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)


#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _url(account_id, tab=None):
    """Return the Reconcile page's URL for *account_id*."""
    suffix = "" if tab is None else f"?tab={tab}"
    return f"/accounts/{account_id}/statements/reconcile{suffix}"


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

    Scraped rather than composed, which is the rule
    ``test_the_settled_tabs.py``'s :func:`_undo_control` follows for the Undo
    form one door over.

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
    return re.findall(
        rf'name="rows-{line_id}" value="([^"]+)"', pane,
    )


def _consent_options(pane, line_id):
    """Return every consent OPTION the pane rendered, live or disabled.

    Plan step ``bank_import:X-gp``.  :func:`~tests.test_routes._statement_forms
    .reconcile_offerable` keeps what a browser could SUBMIT, which leaves out
    a disabled option -- and whether a refused landing is still SHOWN is one
    of the things ``test_where_a_difference_goes.py`` and
    ``test_the_scriptless_pane.py`` grade.  This reads the options' values in
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


class _TestThePaneOffersWHEREADifferenceGoesMembers:
    """The pane helpers ``TestARefusedApplyKeepsTheOwnersTicks`` reads as well."""

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
