"""
Shekel Budget App -- What the Reconcile page's REQUEST asked for

Three readers of the query string, one per argument the screen carries --
which TAB is open, whether the bound on the settled tabs is lifted, and which
card's MATCH pane renders in the document (plan step ``bank_import:X-gi-1``)
-- and, since plan step ``bank_import:X-gi-2a``, the reader of what the FORM
holds for one card's MATCH tab, and the ask the page is handed for the card it
opens.

**They are here because the page module is FULL**, and that is the honest
reason rather than a discovered cohesion: ``statement_reconcile`` stood at 993
of pylint's 1000-line ``max-module-lines`` before ``X-gi-1``, so the
correctness fix below could not be written where it belonged, and
``X-gi-2a``'s two readers pushed a first draft of it past the ceiling, which
is why they landed here instead.  The cohesion is real all the same -- every
function here answers *what did this request ask for*, before the door, and
every one of them is called by a ROUTE.

**THAT LAST WORD IS THE CORRECTNESS FIX** (adversarial review 2026-09-05).
:func:`asked_to_open` was read inside the page's context builder, which
:func:`~._statement_doors.run_statement_fragment_door` calls AFTER
``db.session.commit()`` -- so a POST carrying a malformed ``open`` applied a
money pass, committed it, and then answered a bare 404.  A reader of the
request runs before its door; putting all three in one module is what makes
that visible rather than remembered.

Services boundary: these are HTTP-shaped concerns.  What they import from the
service package is only the VALUES they hand back --
:class:`~app.services.statement_match.Tab`,
:class:`~app.services.statement_match.OpenedAsk`,
:class:`~app.services.statement_match.MatchSubmission` -- and the one schema
the form reader grades with.
"""

from flask import abort, request

from app.routes.accounts._statement_doors import (
    refusal_sentence,
    submitted_match,
)
from app.schemas.validation.statement_reconcile import reconcile_match_payload
from app.schemas.validation.statements import StatementMatchSchema
from app.services.statement_match import MatchSubmission, OpenedAsk, Tab
from app.utils.digit_strings import parse_row_id

#: The schema that grades ONE card's MATCH tab, constructed at import like
#: every sibling's.  It is the same schema
#: :class:`~app.schemas.validation.statements.StatementBatchSchema` nests, so
#: a card priced by the live fragment, the same card priced by the page on
#: Apply, and the same card the pass applies are graded by one set of rules.
_match_schema = StatementMatchSchema()


def requested_tab() -> Tab:
    """Return which tab the request is about.

    **ONE reader for both methods**, over ``request.values``: the GET carries
    the tab as a query argument and the POST as a hidden field, and two
    readers would be two places for the answer to differ -- which is a page
    that applies a pass and answers with another tab.

    **Every tab the service builds is served, as of plan step
    ``bank_import:X-gj-1c``.**  This route carried a ``_TABS_SERVED`` tuple and
    404'd a tab outside it, because ``X-gj-1b`` shipped the three whose cards
    are bank lines and the two whose cards are ACTS were not built yet --
    offering one would have been a control that cannot succeed (**R-HW**).
    Both are built now, so the tuple guarded nothing and is DELETED rather than
    widened to hold every member of the enum: a subset constant equal to the
    whole set is a fence a reader has to check against the enum to trust.

    Returns:
        The :class:`~app.services.statement_match.Tab`, defaulting to the
        inbox.

    Raises:
        werkzeug.exceptions.NotFound: When the value names no tab at all.
            **A 404 rather than a rendered apology**, which is the answer
            :func:`~.bank_agreement._requested_day` already gives for the same
            shape: nothing composes this URL by hand, so a value that does not
            resolve is a tampered or stale request rather than a person
            mid-edit.
    """
    asked = request.values.get("tab")
    if asked is None:
        return Tab.TO_EXPLAIN
    try:
        return Tab(asked)
    except ValueError:
        return abort(404)


def asked_for_everything() -> bool:
    """Return whether the request asked for the whole settled record.

    Plan steps ``bank_import:X-gj-1c`` and ``X-gj-4c-2``.  **The bound the
    register offered to lift, carried onto the tabs that replace it**
    (**R-HU**, **R-GX**): three tabs now render
    :data:`~app.services.statement_match.REGISTER_LIMIT` rows and say how many
    they withheld -- the two settled ones and the Skipped tab -- and this is
    what each of their *show the other N* links asks.  On the developer's own
    account it reaches 171 of 221 acts, so retiring the register without it
    would put them out of reach.

    A PRESENCE test and not a value one, exactly as the register's own reader
    is: the link either carries the flag or it does not, so there is no
    spelling of it to parse and no value to refuse.  What a crafted request
    can ask for is the page it would get by following the link the page
    already renders.

    **Over ``request.args`` and not ``request.values``**, which is the register's
    own reader and is the narrower of the two.  Nothing submits this in a form
    BODY: the *show the other N* link carries it in a query string, and the
    Undo form carries it in its own ACTION's query string -- which is
    ``request.args`` on a POST as much as on a GET, and is why that form needs
    no hidden field at all.  Reading ``values`` would let a body flip the bound
    on a door, which is a widening nothing here asks for.  (:func:`requested_tab`
    does read ``values``, and must: the Apply form carries ``tab`` as a real
    hidden field.)

    Returns:
        Whether the bound is lifted for this render.
    """
    return "all" in request.args


def asked_to_open() -> "int | None":
    """Return which card's MATCH pane renders IN the document, or ``None``.

    Plan step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**.  The pane is
    a fragment htmx fetches when the tab is first shown, so with scripting off
    it never arrives at all and the card's ``<noscript>`` is what sends the
    owner here: the same page, the same tab, and ``?open=<line_id>``.

    **Over ``request.args``**, for the reason :func:`asked_for_everything`
    argues two functions above.  The Apply form carries it in its ACTION's
    query string, so a REFUSED press answers with the card's rows rather than
    the spinner -- and, since plan step ``bank_import:X-gi-2a``, with the
    owner's own ticks: the route reads what the body holds for this card
    through :func:`read_match` and hands the page the pair as one
    :func:`opened_ask`.  *Until then the page priced its pane from what the
    pass OFFERS and never from the body*, which was finding
    **bank_import:BI-478**: parity with the workbench, which lost a refused
    press's ticks the same way, until ``bank_import:X-gi-2`` deleted that
    page and left this the only hand-build door there is.

    **CALLED BY EACH ROUTE, BEFORE ITS DOOR**, exactly as :func:`requested_tab`
    is.  It was read inside :func:`~.statement_reconcile._reconcile_context`
    until adversarial review 2026-09-05 -- and
    :func:`~._statement_doors.run_statement_fragment_door`
    calls that builder AFTER ``db.session.commit()``, so a POST whose ``open``
    was not an integer APPLIED THE PASS, COMMITTED IT, then answered a bare 404
    with no receipt and no way to know whether the money had moved.  *An
    earlier draft of this docstring cited ``_requested_tab`` as precedent for
    the opposite placement; that function is read per route.*

    Returns:
        The bank line id, or ``None`` where the request asked for no card.
        A line this pass renders no card for is NOT refused here: that is what
        a stale ``?open=`` is, and :func:`~app.services.statement_match
        .reconcile_page` answers it with no pane.

    **Read through** :func:`~app.utils.digit_strings.parse_row_id`, **the one
    spelling a row id has** (plan step ``bank_import:X-gi-2a``).  It was a
    bare ``int()`` until then -- which reads ``0``, ``-5``, ``007`` and
    ``+5`` as line ids, none of which names a row -- and that laxness became
    load-bearing the moment :func:`read_match` graded the same value as a
    ``line_ids`` member through :class:`~app.schemas.validation._helpers
    .RowId` (until plan step ``bank_import:X-f6b-2``, since when the key only
    LOCATES the card's controls and the line itself arrives as the card's
    own reviewed-line token): ``?open=0`` on Apply would have refused the WHOLE PASS at 400 in
    a sentence naming a field the body never carried, where the query string
    is this module's to answer and its answer is the 404 below.  Two readers
    of one value that disagree on ``"0"`` are the shape
    :mod:`app.utils.digit_strings` exists to delete, and
    :func:`~.statement_merchants._asked` already reads its own ``open`` this
    way.

    Raises:
        werkzeug.exceptions.NotFound: When the value does not name a row at
            all, which is the answer :func:`requested_tab` already gives for
            the same shape -- nothing composes this URL by hand, so a value
            that cannot even be a line id is tampered rather than stale.
    """
    asked = request.args.get("open")
    if asked is None:
        return None
    line_id = parse_row_id(asked)
    if line_id is None:
        return abort(404)
    return line_id


def read_match(
    form, line_id: int,
) -> "tuple[MatchSubmission | None, str | None]":
    """Return what *form* holds for one card's MATCH tab, graded.

    **ONE reading for the two renders that price a card from a body** (plan
    step ``bank_import:X-gi-2a``): the live fragment reads the card it is
    re-pricing, and Apply reads the card ``?open=`` names so a refused press
    answers with the owner's ticks rather than the tier's proposal.  It is the
    fragment's own four lines, moved here when the page became the second
    caller -- the payload through
    :func:`~app.schemas.validation.statement_reconcile.reconcile_match_payload`,
    graded by :class:`~app.schemas.validation.statements.StatementMatchSchema`,
    built by :func:`~._statement_doors.submitted_match` -- so the figure the
    pane shows and the figure the door compares against stay one derivation.

    **It reads the card whether or not the card was OK'd**, exactly as the
    fragment never reads ``ok``: an owner who ticked rows on one card and
    pressed Apply for another has still ticked them, and the page comes back
    with that card as they left it.

    Args:
        form: The request's ``MultiDict``.
        line_id: The bank line whose card to read.

    Returns:
        ``(submission, refusal)``: the
        :class:`~app.services.statement_match.MatchSubmission` and ``None``,
        or ``None`` and the schema's own sentence where the body is not one
        this page could have rendered.
    """
    payload = reconcile_match_payload(form, str(line_id))
    errors = _match_schema.validate(payload)
    if errors:
        return None, refusal_sentence(errors)
    return submitted_match(_match_schema.load(payload)), None


def opened_ask(line_id: "int | None", submitted=None) -> "OpenedAsk | None":
    """Return what the page is asked to open, or ``None`` for nothing.

    Args:
        line_id: What :func:`asked_to_open` read.
        submitted: What this request's form holds for that card
            (:func:`read_match`), or ``None`` where the request carries no
            form holding it -- **the route's fact to state**, for the reason
            :class:`~app.services.statement_match.OpenedAsk` gives: a reader
            inferring it from a missing field would read an owner who
            unticked every row as one who never touched the card.

    Returns:
        The :class:`~app.services.statement_match.OpenedAsk`, or ``None``.
    """
    if line_id is None:
        return None
    return OpenedAsk(line_id=line_id, submitted=submitted)
