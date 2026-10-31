"""Drive the app's own doors as its owner, for a rehearsal on a production copy.

What a rehearsal of an operator act needs before it can perform one act: an app pointed at the
copy it names and at nothing else (:func:`connect`, and :func:`connect_read_only` for a check
that writes nothing), the owner's session (:class:`Operator`), the forms and buttons the owner's
pages render and what a browser would submit from them (:func:`forms_in`, :func:`buttons_in`,
:func:`browser_payload`), a refusal told apart from an acceptance however the door answers
(:meth:`Operator.send`, :class:`RefusedError`), and the PERFORMANCE SHEET the act is performed
from (:class:`Sheet`), written where no checkout can commit it (:func:`new_file`).

**It is the account-10 repair's rehearsal's door-driving half** (plan step
``balance:X-f3c-2b-2c``), moved here as it stood when that rehearsal was retired (ruling
plan#2412, 2026-10-30): the repair was performed on production on 2026-10-12, and the
rehearsal's last version is at commit ``01ebee2b8``.  Two things changed in the move:
:class:`Operator` is handed the owner rather than reading account 10's, and the sentences that
described that rehearsal's own acts now say whose they were.  Every measurement quoted here was
taken through that rehearsal.

A script beside this one imports it by name (``from door_rehearsal import Operator``), because
Python puts a script's own folder first on ``sys.path``.

**What the reconcile rehearsal (plan#2414) added**: a submission is the form's controls IN ORDER
with every value, as a browser posts them (:func:`browser_payload`), because the Reconcile page
repeats control names; an act ticks only a box the page rendered and types only an option a
select offers (:meth:`Operator.submit`); and a press of a button outside any form is sent as
htmx sends it (:func:`buttons_in`, :meth:`Operator.press`).
"""

import json
import os
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from werkzeug.datastructures import MultiDict

from app import create_app
from app.extensions import db, login_manager
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER

#: The flash categories that mean a door REFUSED.  Read after every submission,
#: because two of the account-10 repair's doors answered a refusal with ``302``
#: and a flash rather than with a designed fragment and a 4xx --
#: ``accounts.restate_opening``, the door that repair existed for, is one.
_REFUSAL_FLASHES = frozenset({"danger", "error", "warning"})

#: The methods htmx sends a press's values with in the URL rather than the
#: body: ``methodsThatUseUrlParams`` in the vendored htmx 2.0.10.
_URL_PARAM_METHODS = frozenset({"get", "delete"})


class _Forms(HTMLParser):
    """Collect every form a response renders: method, url and its own controls.

    **The payload comes from the page, not from this file.**  An HTML form
    submits every control it renders, so a rehearsal that posts a chosen subset
    is not rehearsing the owner's click -- a hand-picked payload once shipped a
    route arm that was dead in a browser.  An act that submits a FORM fetches
    the form the owner opens (:meth:`Operator.form`,
    :meth:`Operator.form_posting_to`), changes only what they type, and submits
    the rest exactly as rendered (:meth:`Operator.submit`), including the
    version pin, the hidden ids and the selects' own current values.  A press
    that is a button rather than a form posts what its button carries, and
    nothing else, through :meth:`Operator.send`.

    **Seven divergences from a browser were measured and fixed** over two
    review rounds of the account-10 rehearsal, each of which had this parser
    posting something no browser would.  **Three of the seven could not be
    reached on the forms that rehearsal drove** -- the ``<textarea>`` and the
    two ``<select multiple>`` cases: none of its eight forms rendered either
    (censused 2026-10-09).  They are fixed anyway because the parser is the
    thing that makes "the payload comes from the page" true, and a parser
    correct only on one rehearsal's pages is a claim about those pages rather
    than about the parser.

    * a CHECKED checkbox followed by a hidden partner of the same name -- the
      shape ``grid/_transaction_full_edit.html`` uses for its flags -- came out
      ``false``, because the controls were folded into a dict and the LAST
      value won.  Werkzeug's ``MultiDict`` gives ``request.form[key]`` the
      FIRST, so the flag survives once both are posted in order, which
      :func:`browser_payload` now does rather than folding them;
    * a ``<textarea>`` posted empty whatever it held, because its content was
      never read;
    * a ``<select multiple>`` posted only its last selected option;
    * a ``<select>`` with no ``selected`` fell back to its first option even
      when that option was ``disabled``, which the reset algorithm skips;
    * a ``<select multiple>`` with NOTHING selected posted its first option,
      where a browser posts no value at all;
    * a ``<option selected disabled>`` -- the placeholder idiom
      ``analytics/_income_statement.html`` uses -- was dropped and the next
      option posted in its place, though the markup's own selectedness makes it
      the submitted one;
    * a checked checkbox with no ``value`` posted the empty string rather than
      ``on``.

    Disabled controls are dropped because a browser drops them, which is what
    makes a finalised transfer's locked amount absent from the submission
    rather than re-posted.

    **What the owner COULD change is kept beside what is submitted** (plan
    task plan#2414): every enabled checkbox and radio rendered unticked
    (``unticked``, with the radios' names in ``radios``) and every single
    select's enabled options (``options``), so an act ticks and types only
    what the page offers (:meth:`Operator.submit`).  And every ``<button>``
    carrying an htmx verb is kept apart (``buttons``), with its ``hx-vals``,
    for a press that is not a form's (:meth:`Operator.press`).
    """

    _NOT_A_VALUE = {"submit", "button", "image", "reset"}

    #: The htmx attributes a press is sent by, in the order they are read.
    _VERBS = ("patch", "post", "put", "delete", "get")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: "list[dict]" = []
        self.buttons: "list[dict]" = []
        self._form: "dict | None" = None
        self._select: "dict | None" = None
        self._textarea: "str | None" = None
        self._text: "list[str]" = []

    def handle_starttag(self, tag, attrs):
        """Open a form, record one control's submitted value, or a button."""
        attr = dict(attrs)
        if tag == "form":
            self._open_form(attr)
        elif tag == "button":
            self._button(attr)
        elif self._form is None:
            return
        elif tag == "input":
            self._input(attr)
        elif tag == "select":
            self._select = (
                None if "disabled" in attr or not attr.get("name")
                else {
                    "name": attr["name"], "multiple": "multiple" in attr,
                    "selected": [], "first": None, "offered": [],
                }
            )
        elif tag == "option" and self._select is not None:
            self._option(attr)
        elif tag == "textarea" and attr.get("name") and "disabled" not in attr:
            self._textarea = attr["name"]
            self._text = []

    def _open_form(self, attr):
        """Start a form, taking its method and action from HTMX or plain HTML.

        Args:
            attr: The tag's attributes.
        """
        self._form = {
            "method": "post", "url": attr.get("action", ""),
            "controls": [], "unticked": [], "radios": set(), "options": {},
        }
        for verb in self._VERBS:
            if f"hx-{verb}" in attr:
                self._form["method"] = verb
                self._form["url"] = attr[f"hx-{verb}"]
                break
        self.forms.append(self._form)

    def _button(self, attr):
        """Record a ``<button>`` that htmx sends a request from.

        Args:
            attr: The tag's attributes.
        """
        if "disabled" in attr:
            return
        for verb in self._VERBS:
            if f"hx-{verb}" in attr:
                self.buttons.append({
                    "method": verb, "url": attr[f"hx-{verb}"],
                    "vals": json.loads(attr.get("hx-vals") or "{}"),
                })
                return

    def _input(self, attr):
        """Record one ``<input>``'s submitted value, or drop it.

        Args:
            attr: The tag's attributes.
        """
        if "disabled" in attr or attr.get("type") in self._NOT_A_VALUE:
            return
        if not attr.get("name"):
            return
        ticked = attr.get("type") in ("checkbox", "radio")
        if attr.get("type") == "radio":
            self._form["radios"].add(attr["name"])
        if ticked and "checked" not in attr:
            self._form["unticked"].append(
                (attr["name"], attr.get("value", "on")),
            )
            return
        # A ticked control with no ``value`` submits the string ``on``; every
        # other control with no value submits the empty string.
        default = "on" if ticked else ""
        self._form["controls"].append((attr["name"], attr.get("value", default)))

    def _option(self, attr):
        """Record one ``<option>``'s value against the open select.

        Args:
            attr: The tag's attributes.
        """
        value = attr.get("value", "")
        # **A DISABLED option is still submitted when the markup marks it
        # selected**, and only the no-selection FALLBACK skips it: the reset
        # algorithm gives selectedness to the first option that is not
        # disabled, but an option that already carries ``selected`` keeps it.
        # ``analytics/_income_statement.html`` uses exactly that placeholder
        # idiom, and an earlier version dropped such an option and posted the
        # next one instead (adversarial review, 2026-09-01).
        if "disabled" not in attr:
            self._select["offered"].append(value)
            if self._select["first"] is None:
                self._select["first"] = value
        if "selected" in attr:
            self._select["selected"].append(value)

    def handle_data(self, data):
        """Accumulate an open textarea's content."""
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        """Close a form, a select or a textarea, committing its value."""
        if tag == "form":
            self._form = None
        elif tag == "select" and self._select is not None:
            self._commit_select()
        elif tag == "textarea" and self._textarea is not None:
            if self._form is not None:
                self._form["controls"].append(
                    (self._textarea, "".join(self._text)),
                )
            self._textarea = None

    def _commit_select(self):
        """Append the open select's submitted value or values."""
        if self._form is not None:
            chosen = self._select["selected"]
            # A MULTI-select with nothing selected submits NOTHING.  The
            # first-option fallback belongs to a single select only, and
            # applying it to a multi-select posted a value no browser would.
            if not self._select["multiple"]:
                self._form["options"][self._select["name"]] = (
                    self._select["offered"]
                )
                if not chosen and self._select["first"] is not None:
                    chosen = [self._select["first"]]
                chosen = chosen[:1]
            for value in chosen:
                self._form["controls"].append((self._select["name"], value))
        self._select = None


def browser_payload(form: dict) -> MultiDict:
    """Return *form*'s controls as the payload a browser would submit.

    **Every control, in document order, every value of a repeated name kept**
    -- which is what a browser sends, so the server's own ``MultiDict`` then
    gives ``request.form[key]`` the first and ``getlist`` all of them, as it
    does for a browser.  *It was a dict keeping the FIRST value of a repeated
    name until plan task plan#2414*: right for a reader of ``form[key]``,
    wrong for the Reconcile page, whose ``ok`` and ``rows-<line>`` boxes are
    read as lists.

    Args:
        form: One parsed form.

    Returns:
        The submission.
    """
    return MultiDict(form["controls"])


def forms_in(html: str) -> "list[dict]":
    """Return every form in *html*.

    Args:
        html: A rendered page or HTMX fragment.

    Returns:
        One form dict per form, in document order.
    """
    parser = _Forms()
    parser.feed(html)
    return parser.forms


def buttons_in(html: str) -> "list[dict]":
    """Return every button in *html* that htmx sends a request from.

    Args:
        html: A rendered page or HTMX fragment.

    Returns:
        ``{"method", "url", "vals"}`` per button, in document order.
    """
    parser = _Forms()
    parser.feed(html)
    return parser.buttons


class RefusedError(AssertionError):
    """A door refused a submission this rehearsal expected it to accept."""


class Operator:
    """One owner's browser session against one clone."""

    def __init__(self, app, sheet: "Sheet", user_id: int):
        """Forge the owner's session and open a client.

        Args:
            app: The Flask application, from :func:`connect`, which disables
                what refuses a forged session.
            sheet: The performance sheet every act records into.
            user_id: The owner whose session it is: whoever owns what the
                acts touch.
        """
        self.sheet = sheet
        self.client = app.test_client()
        self.user_id = user_id
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
            session["_id"] = None
        self.acts = 0
        #: What the last submission flashed, ``[(category, message), ...]``:
        #: a door that answers by flashing, such as the statement import,
        #: says what it did there, and the act records it.
        self.flashes: "list[tuple[str, str]]" = []

    def _drain_flashes(self) -> "list[tuple[str, str]]":
        """Return and clear whatever the last request flashed.

        Returns:
            ``[(category, message), ...]``, empty when nothing flashed.
        """
        with self.client.session_transaction() as session:
            return list(session.pop("_flashes", []))

    def form(self, url: str) -> dict:
        """Open *url* and return its first form.

        Args:
            url: The page or fragment the owner opens.

        Returns:
            The form dict.

        Raises:
            AssertionError: When the page does not answer, or renders no form.
        """
        response = self.client.get(url, headers={"HX-Request": "true"})
        assert response.status_code == 200, \
            f"GET {url} -> {response.status_code}"
        forms = forms_in(response.get_data(as_text=True))
        assert forms, f"GET {url} rendered no form"
        return forms[0]

    def form_posting_to(self, page: str, action: str) -> dict:
        """Return the form on *page* whose action ends with *action*.

        Args:
            page: The full page the owner opens.
            action: The tail of the door's URL.

        Returns:
            The form dict.

        Raises:
            AssertionError: When the page does not answer, or carries no such
                form -- which is the card being unreachable, not a payload
                problem.
        """
        response = self.client.get(page)
        assert response.status_code == 200, \
            f"GET {page} -> {response.status_code}"
        for form in forms_in(response.get_data(as_text=True)):
            if form["url"].endswith(action):
                return form
        raise AssertionError(f"{page} renders no form posting to {action}")

    def submit(
        self, form: dict, typed: "dict | None" = None,
        ticked: "tuple[tuple[str, str], ...]" = (),
    ):
        """Submit *form* with the owner's edits applied.

        **An act ticks and picks only what the page offers**: a box ticked
        must be one the form rendered unticked, and a value typed into a
        select must be one of its enabled options, or the act is not one the
        owner could perform and this raises before anything is sent.

        Args:
            form: A form from :meth:`form` or :meth:`form_posting_to`.
            typed: What the owner changed, or supplied where a control is
                driven by script rather than rendered carrying its value.
                Each replaces every value its name carried.
            ticked: ``(name, value)`` per box the owner ticks.  A radio's
                replaces the one its group carried; a checkbox's is added.

        Returns:
            The response.

        Raises:
            AssertionError: When a tick or a pick is not on the page.
        """
        payload = browser_payload(form)
        for name, value in ticked:
            assert (name, value) in form["unticked"], \
                f"{form['url']} renders no unticked box {name}={value}"
            if name in form["radios"]:
                payload.setlist(name, [value])
            else:
                payload.add(name, value)
        for name, value in (typed or {}).items():
            offered = form["options"].get(name)
            assert offered is None or value in offered, \
                f"{form['url']}'s {name} offers no option {value}"
            payload.setlist(name, [value])
        return self.send(form["method"], form["url"], payload)

    def button(self, page: str, method: str, action: str) -> dict:
        """Return the button on *page* that sends *method* to *action*.

        Args:
            page: The page or fragment the owner opens.
            method: The button's htmx verb, ``delete`` for one.
            action: The door's path, matched whole, its query aside.

        Returns:
            The button dict.

        Raises:
            AssertionError: When the page does not answer or renders no such
                button.
        """
        response = self.client.get(page, headers={"HX-Request": "true"})
        assert response.status_code == 200, \
            f"GET {page} -> {response.status_code}"
        for found in buttons_in(response.get_data(as_text=True)):
            if found["method"] == method and \
                    urlsplit(found["url"]).path == action:
                return found
        raise AssertionError(f"{page} renders no {method} button to {action}")

    def press(self, button: dict):
        """Press *button* as htmx sends it: its ``hx-vals`` are the payload.

        Args:
            button: A button from :meth:`button`.

        Returns:
            The response.
        """
        return self.send(button["method"], button["url"],
                         MultiDict(button["vals"]))

    def send(self, method: str, url: str, payload: MultiDict):
        """Submit *payload* and assert the door accepted it.

        **A 302 with a danger flash is a REFUSAL, and reading only the status
        missed it.**  ``accounts.restate_opening`` -- the door the account-10
        repair existed for -- answers an illegal day by flashing and
        redirecting, so a status check alone reported success on a write that
        never happened (adversarial review, 2026-09-01).  The flash is drained
        after every submission and any refusal category raises, which also
        surfaces the refusal SENTENCE -- which is what a rehearsal is for.

        **A GET's and a DELETE's payload goes in the URL**, which is where
        htmx 2 puts it (its ``methodsThatUseUrlParams``, ``["get", "delete"]``
        in the vendored 2.0.10): a DELETE door reads ``request.args``, and a
        body it never reads would be a submission no browser makes.

        Args:
            method: The form's own method.
            url: The form's own action.
            payload: What the browser would submit.

        Returns:
            The response.

        Raises:
            RefusedError: On a refusing status, or on a refusal flash.
        """
        self.acts += 1
        if method in _URL_PARAM_METHODS:
            query = urlencode(list(payload.items(multi=True)))
            if query:
                url = f"{url}{'&' if '?' in url else '?'}{query}"
            response = getattr(self.client, method)(
                url, headers={"HX-Request": "true"},
            )
        else:
            response = getattr(self.client, method)(
                url, data=payload, headers={"HX-Request": "true"},
            )
        self.flashes = self._drain_flashes()
        refused = [
            message for category, message in self.flashes
            if category in _REFUSAL_FLASHES
        ]
        # **A designed error fragment can carry a 2xx, and the status list
        # below would accept it.**  Every refusal the account-10 repair's doors
        # produced answered 4xx or flashed, audited 2026-09-01 -- but that is a
        # convention, and the app stamps a header saying so precisely because
        # the client cannot tell a handled error from a crash page otherwise.
        # Asking the header is one line and does not rest on the audit staying
        # true.
        if DESIGNED_FRAGMENT_HEADER in response.headers:
            refused.append(
                f"a designed error fragment ({DESIGNED_FRAGMENT_HEADER})"
            )
        if response.status_code not in (200, 201, 302):
            raise RefusedError(
                f"{method.upper()} {url} -> {response.status_code}\n"
                f"payload={payload}\n"
                f"{response.get_data(as_text=True)[:800]}"
            )
        if refused:
            raise RefusedError(
                f"{method.upper()} {url} -> {response.status_code} but the "
                f"app refused it: {' | '.join(refused)}"
            )
        return response


class Sheet:
    """The PERFORMANCE SHEET: every act in order, what it types, what follows.

    **The page an operator act is performed from, and the only place its
    figures are written** (ruling **R-BAL249**): the committed runbook names
    each amount by its role, and the rehearsal derives the amount and writes it
    here, beside the state the app reaches after the act.  Every line is also
    printed, so the console and the sheet cannot disagree.  The caller saves
    it even when the run stops early, so a refusal is recorded beside the acts
    that landed before it.
    """

    def __init__(self, path: "Path | None"):
        """Hold the sheet's destination.

        Args:
            path: Where to write it; outside every checkout (:func:`new_file`).
                ``None`` for a check, which prints and saves nothing.
        """
        self.path = path
        self.lines: "list[str]" = []

    def say(self, line: str = "") -> None:
        """Print *line* and keep it for the sheet.

        Args:
            line: One line of the record.
        """
        print(line)
        self.lines.append(line)

    def act(self, number: int, title: str, door: str) -> None:
        """Open one act's section.

        Args:
            number: The act's place in the order.
            title: What it does, in the runbook's words.
            door: Where the operator performs it.
        """
        self.say("")
        self.say(f"## Act {number}: {title}")
        self.say(f"Where: {door}")

    def typed(self, what: str, value: object) -> None:
        """Record one value the operator types.

        Args:
            what: The box, as the screen labels it.
            value: What goes in it.
        """
        self.say(f"- TYPE {what}: **{value}**")

    def save(self) -> None:
        """Write the sheet."""
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def new_file(raw: str, flag: str) -> Path:
    """Return where a file the rehearsal writes goes, refusing any git checkout.

    **Any checkout, not only this one**: a harness is run from worktrees, and
    a sheet written into the main checkout or a sibling worktree is as much one
    ``git add`` from a commit as one written beside this file.  The folder is
    also required to EXIST now, because the sheet is written last and a bad
    path found then would lose the record of every act already performed.
    **And the file must NOT exist**: the sheet is saved even when a run stops,
    so a re-run naming an earlier run's sheet would replace the page a repair
    is performed from with a STOPPED one (adversarial review, 2026-10-09).
    Every file a run records of a production copy is held to the same three
    rules -- the account-10 rehearsal's snapshot of row ids was the second --
    and a run never overwrites an earlier run's.

    Args:
        raw: The argument's value.
        flag: The argument that named it (``--sheet``, for one), for the
            refusals.

    Returns:
        The resolved path.

    Raises:
        SystemExit: When the path is inside a git checkout, where what a run
            records of a production copy does not belong (ruling
            **R-BAL249**), its folder does not exist, or a file is already
            there.
    """
    path = Path(raw).expanduser().resolve()
    checkouts = [
        folder for folder in (path, *path.parents) if (folder / ".git").exists()
    ]
    if checkouts:
        raise SystemExit(
            f"{flag} {path} is inside the git checkout {checkouts[0]}; what a "
            "run records of a production copy belongs in the handoff folder "
            "(ruling R-BAL249)"
        )
    if path.is_dir() or not path.parent.is_dir():
        raise SystemExit(
            f"{flag} {path} is a folder, or its folder does not exist; it "
            "names a FILE"
        )
    if path.exists():
        raise SystemExit(
            f"{flag} {path} already exists; every run writes its own, and an "
            "earlier one is never overwritten"
        )
    return path


def connect(clone: str, config_name: "str | None" = None):
    """Build an app pointed at *clone*, refusing anything it is not.

    **A money-moving harness names its own target.**  ``DATABASE_URL`` is an
    environment variable and an environment variable is inherited: a shell that
    last exported the dev runtime's URL would silently rehearse against a
    database other sessions are working in, and ``shekel`` is what BOTH the
    deployed database and that runtime are called.  So the target is a required
    ARGUMENT, checked against the connection the app actually opened, and the
    one name that is never a rehearsal clone is refused outright.

    **The name check is the weaker half**: a name can only refuse the databases
    somebody thought of.  So a harness that writes also refuses, before its
    first write, any copy its own acts have already touched, whatever it is
    called -- the account-10 rehearsal did so by that repair's markers.

    Args:
        clone: The database this rehearsal is for.
        config_name: The app configuration, ``None`` for the environment's
            default (:func:`app.create_app`).

    Returns:
        The configured Flask application.

    Raises:
        SystemExit: When *clone* is the shared name, or the connection is not
            to it.
    """
    if clone == "shekel":
        raise SystemExit(
            "'shekel' is the deployed database AND the shared dev runtime; "
            "clone one and rehearse against the copy"
        )
    app = create_app(config_name)
    # CSRF and strong session protection are both disabled for the rehearsal
    # and neither is under test: this forges a session because it has no
    # password for the database it is pointed at, exactly as
    # ``verify_render_surfaces`` does, and strong protection refuses a session
    # whose identifier was not minted inside a request.
    app.config["WTF_CSRF_ENABLED"] = False
    login_manager.session_protection = None
    with app.app_context():
        live = db.session.execute(
            db.text("select current_database()")
        ).scalar_one()
    if live != clone:
        raise SystemExit(
            f"the copy named is {clone!r} and DATABASE_URL opened {live!r}"
        )
    return app


#: What every connection of a check's run is opened with.
_READ_ONLY = "-c default_transaction_read_only=on"


def connect_read_only(clone: str):
    """Build an app whose every connection is READ-ONLY, for a check.

    **The SERVER enforces it, not this file**: libpq hands ``PGOPTIONS`` to
    the server at connect, and the server then refuses any write in any
    transaction of the session that does not itself ask for ``READ WRITE`` --
    which no app code does (``app/db_transaction.py`` only ever asks for
    ``READ ONLY``; grepped 2026-10-10) -- so a check that wrote would RAISE
    rather than write.  It is set in this process's environment before the
    app opens its engine, appended after anything the shell already set so it
    wins, and
    then READ BACK on the app's own session: a setting nobody reads back is a
    claim, and one a later ``-c`` overrode would be a false one.

    **The app is built with production's CONFIGURATION, because the
    development build WRITES at boot** -- the configuration only: it connects
    as whatever role ``DATABASE_URL`` names, and runs no entrypoint.
    ``create_app`` creates the schemas and seeds the
    reference tables for the development and testing configurations, and the
    account-10 check's first read-only run was refused there, at
    ``CREATE SCHEMA IF NOT EXISTS`` (2026-10-10).  Those writes are idempotent
    on a seeded copy, but "idempotent" is a claim about the copy and
    read-only is a property of the run; production's build leaves both to its
    deploy and writes nothing.

    Args:
        clone: The copy to grade.

    Returns:
        The configured Flask application, every connection read-only.

    Raises:
        SystemExit: From :func:`connect`, or when the session it opened is
            not read-only after all.
    """
    os.environ["PGOPTIONS"] = " ".join(
        part for part in (os.environ.get("PGOPTIONS"), _READ_ONLY) if part
    )
    app = connect(clone, "production")
    with app.app_context():
        mode = db.session.execute(
            db.text("show transaction_read_only")
        ).scalar_one()
    if mode != "on":
        raise SystemExit(
            f"a check opened {clone} with transaction_read_only {mode!r}; it "
            "grades a copy only read-only"
        )
    return app
