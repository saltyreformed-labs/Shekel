"""
Shekel Budget App -- the read pass's RECURRENCE memos, inherited by the context.

:class:`RecurrenceMemosMixin` holds the three methods that resolve and walk a
recurrence once per read pass, and
:class:`~app.services.balance_at._context.BalanceContext` inherits them, so every
caller still asks ``ctx.placements_of(...)``.  They moved here verbatim when
``_context.py`` reached the 1,000-line ceiling, which coding-standards ruling
**balance:R-IR** answers with a split, never an exemption; ruling **R-BAL146**
chose this shape.

**A mixin, not a second object**, because the methods memoize into the PASS's
own fields and derive from its calendar: a separate holder would need the pass
handed to it on every call.  **This module is W9909-scoped on its own entry**
(``tools/pylint/shekel_checkers/_fence_rulings.py``): the fence matches a
module by its name or a package prefix, and a sibling of ``_context`` is
neither, so a public method born here would otherwise reach every route holder
of the context unclassified.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from app.services.recurrence import (
    BooksWalk,
    RecurrenceSpec,
    ResolvedRecurrence,
    occurrence_walk,
    recurrence_spec,
)

from ._definition_books import definition_books, resolved_with_books
from ._definition_due_dates import definition_due_dates


class RecurrenceMemosMixin:
    """A rule's resolution and a resolved recurrence's walk, memoized per pass.

    Methods only: the state they fill is the host dataclass's --
    ``_recurrences``, ``_placements``, ``_books_opened_on`` and
    ``_card_due_dates`` -- and they
    derive from its :meth:`~app.services.balance_at._context.BalanceContext
    .calendar`.  Each field's contract is in ``BalanceContext``'s Attributes.
    """

    def resolved_recurrence_of(self, rule) -> "ResolvedRecurrence | None":
        """Return what *rule* MEANS against this owner's calendar, resolving it once.

        The memo that collapses a read pass's N resolutions of one rule to
        one.  Plan step **R16-b-2** put it here: the composed door
        (``recurring_definition.resolved_definition``) resolves a definition's
        rule to narrow it by the loan's derived stop, and that stop is the
        forward plan's zero crossing, which since R16-b-2 walks the SAME rule
        under its authored closing to sum the definition's occurrences -- so
        one page reading one loan payment resolved its rule twice on one pass
        (plan ledger row **N-511**'s shape; rule 14's ONE WALK forbids it).
        Both readers take the resolution (books attached) from here and each
        applies its own closing to the value.

        **A memo on the PASS, not a cache**, for the reason :meth:`calendar`
        gives, and filled here because ``app.services.recurrence`` is a leaf
        below the seam.

        **Keyed by what the rule SAYS, not by which row it is.**  The
        resolution is a function of the rule's authored columns, the pass's
        calendar and its definition's books (:meth:`resolved_for`'s key), so
        an entry keyed by those inputs cannot be served for a different one.
        A first cut keyed by ``rule.id``, and the merge
        of plan step R7d-c-2 -- which has GENERATION read a rule through this
        memo -- measured the proxy's cost: ``reauthor_rule`` rewrites columns
        IN PLACE, so a rule edited and regenerated on one pass regenerated on
        its pre-edit cadence (monthly, the 5th moved to the 19th: ``updated
        5, deleted 0, created 0``).  No live route reached it only because
        every edit route builds its pass AFTER its write: a gateless
        convention maintaining the invariant the id key carried, which a key
        that is the input carries for no rule (``CLAUDE.md`` rule 14).  A
        re-authored rule misses on any pass, two rules stating one spec share
        one resolution (the resolver cannot tell them apart either), and a
        TRANSIENT rule (``id`` ``None``) needs no special case.  The spec is
        read ONCE, as key and input: what ``resolved_spec`` exists for.  The
        CALENDAR half is :meth:`calendar`'s memo, under the route convention.

        **The same lens one memo over, left as found.**  :attr:`loans`,
        :attr:`plans` and :attr:`payoffs` are keyed by account id and derive
        from ROWS as well as from every paying definition's spec.  Under the
        rows a generate pass CREATES they are invariant by construction
        (ruling **R-R64**: an occurrence no row answers is priced as its row
        would be, so writing that row changes nothing), which is why a pass
        may read them BEFORE it writes; the maintain pass's UPDATE arm
        re-dates rows the PLANNED tier reads and its RETIRE arm deletes them,
        so after either -- as under any other write inside one pass -- they
        are stale, guarded by the convention above since their key is rows.

        **A foreign rule never enters the memo with a VALUE, and no check here
        is what makes that so.**  The pure resolver refuses a spec paired with
        another owner's calendar -- ``RecurrenceResolutionError``, naming the
        rule -- BEFORE the store on every miss, so a hit holding a value is
        always the owner's own spec (``user_id`` is a field of the key); all a
        foreign rule can leave is the ``None`` an EMPTY calendar answers ahead
        of the ownership check, which carries nothing.  The composed door
        relies on that refusal being the rule's own and reaching a caller
        first, so a second, earlier refusal here would change which error
        names the pairing; :func:`~._memoize._memoize_once` carries its own check because
        the derivations it stores do not refuse for themselves.

        **Its books floor is its OWNER's accounts'** (plan step
        ``pay_calendar:C18-a``): :meth:`resolved_for` over the rule's
        template; a payroll line's rule has none and gets no floor.

        Args:
            rule: The :class:`~app.models.recurrence_rule.RecurrenceRule` to
                resolve, stored or transient.

        Returns:
            The :class:`~app.services.recurrence.ResolvedRecurrence` with the
            AUTHORED closing alone and its books floor attached, or ``None``
            when the owner has no pay periods -- the two answers
            :func:`~app.services.recurrence.resolved_spec` gives.

        Raises:
            RecurrenceResolutionError: See
                :func:`~app.services.recurrence.resolved_spec`; a rule paired
                with another owner's pass is refused there, and an unmodelled
                stored cadence reading the key, as ``resolved_recurrence``.
        """
        # ``getattr``: a rule is duck-typed on this seam (fixtures build one
        # as a namespace); ``is None``, not ``or``: an ORM row's truthiness is
        # not the question asked.
        owner = getattr(rule, "transaction_template", None)
        if owner is None:
            owner = getattr(rule, "transfer_template", None)
        return self.resolved_for(recurrence_spec(rule), owner)

    def resolved_for(
        self, spec: RecurrenceSpec, definition: object | None,
    ) -> "ResolvedRecurrence | None":
        """Return what *spec* MEANS for *definition*, its books floor and due dates attached.

        **The ONE composition of a definition's resolved value with where its
        books open** (plan step ``pay_calendar:C18-a``, rulings **R-PC85**,
        **R-PC86**; the argument is :mod:`._definition_books`'), so every
        reader of its occurrences takes the floor from one call: the composed
        door and the loan estimate's walk through
        :meth:`resolved_recurrence_of`, and the form preview's unsaved
        definition (``recurring_definition.resolved_submission``).  **And
        with the card's due dates a repeat on each statement's due date
        walks** (plan step ``credit_card:CC-6``, plan#2787; the argument is
        :mod:`._definition_due_dates`'), so generation, the Recurring page,
        the preview and every other reader of such a repeat walk the dates
        its card's terms decide on this pass, each card's terms read once.

        Memoised by ``(spec, books, due_dates)``: two definitions stating
        one spec over accounts that open on one day, both envelopes or
        neither, paying one card or none, share one value (so repeated reads
        are the SAME object, which the walk memo keys by); over different
        openings, an envelope beside a bill (ruling **R-PC89**), or two cards
        whose terms differ, they mean different occurrences and walk apart.
        The key is what is attached rather than which card it came from, so
        a pass that read a card's terms before a change and one that reads
        them after cannot share a resolution even were one memo to outlive
        its pass.  The floor and the dates are read first -- a memo hit per
        account and per card -- so a foreign spec costs one opening read
        (memoised), and a foreign due-date repeat one terms read, before the
        resolver refuses it; no resolution is stored for it, and the refusal
        names the rule.  A due-date repeat whose destination is not a
        configured card is refused by the dates' read, ahead of the
        resolver: both refusals are ``RecurrenceResolutionError``, and that
        state is one no write door leaves (plan#2788).

        Args:
            spec: The authored recurrence.
            definition: What moves the money -- a transaction or transfer
                template, an unsaved definition, or ``None`` (a payroll
                line's rule, which creates no row of its own).

        Returns:
            The resolved value with ``books_opened_on``, ``is_envelope`` and
            ``due_dates`` set, or ``None`` when the owner has no pay periods.

        Raises:
            RecurrenceResolutionError: See
                :func:`~app.services.recurrence.resolved_spec`; or a repeat
                on each statement's due date whose destination is not a
                configured card (:func:`~._definition_due_dates
                .definition_due_dates`).
        """
        books = definition_books(definition, self._books_opened_on)
        due_dates = definition_due_dates(spec, definition, self._card_due_dates)
        key = (spec, books, due_dates)
        if key not in self._recurrences:
            self._recurrences[key] = resolved_with_books(
                spec, self.calendar(), books, due_dates,
            )
        return self._recurrences[key]

    def placements_of(self, resolved: ResolvedRecurrence) -> BooksWalk:
        """Return every occurrence *resolved* names, split by its books, walking once.

        The memo that collapses a read pass's N walks of one resolved
        recurrence to one, and the other half of what
        :meth:`resolved_recurrence_of` began.  Plan step **R7d-f-2** put it
        here (plan ledger row **N-513**): a ``/savings`` render reads a
        transfer from checking into a goal account through the composed door
        TWICE -- once in the emergency-fund floor's set, once in that goal's
        contribution set -- and R16-b-2's memo had already made the second
        RESOLUTION a hit while the second WALK still ran (measured on
        2026-09-12 before this step: ``resolve`` once, the walk twice).
        Rule 14's ONE WALK, read literally.

        **Keyed by the walk's INPUT, the shape :meth:`resolved_recurrence_of`
        chose** (ruling **R-R73**).  The placements are a pure function of the
        resolved value -- its cadence, its first occurrence and its COMPOSED
        closing, the destination's derived stop included -- and of this
        pass's calendar, which is :meth:`calendar`'s one memo.  So the value
        is the key: two definitions with one composed meaning share one walk
        (the walk could not tell them apart either), a re-authored rule
        resolves to a different value and misses, a definition whose loan
        moved its payoff misses with it, and an unsaved definition needs no
        special case.  A row-keyed memo would have served a pre-edit walk on
        a pass that edited and re-read, which is the defect the id key
        measured one memo over.

        **Through the saved horizon and no further**: this is
        :func:`~app.services.recurrence.occurrence_walk`, whose ``kept`` half
        is the walk the display readers and generation take and whose other
        half the closing also counts (plan step ``pay_calendar:C18-a``, ruling
        **R-PC94**: the books decide which occurrences become rows, never when
        the rule ends).
        The seam's ESTIMATED loan tier walks PAST the horizon
        (``projected_occurrence_placements``, ``through=``) and is a different
        function of different inputs; it is not memoised here.

        Args:
            resolved: The recurrence's two-axis meaning, closing composed --
                what :func:`app.services.recurring_definition
                .resolved_definition` returns.  Must have been resolved
                against THIS pass's calendar, which every producer of one
                guarantees by reading :meth:`resolved_recurrence_of` or
                :meth:`resolved_for`, never the bare ``resolved_spec``.

        Returns:
            The :class:`~app.services.recurrence.BooksWalk` through the
            calendar's horizon, each half ascending; both empty for a
            definition its composed closing admits nothing of.

        Raises:
            RecurrenceGenerationError: See
                :func:`~app.services.recurrence.occurrence_placements`; a
                raising walk is not memoised, so the refusal fires on every
                call rather than being swallowed after the first.
        """
        if resolved not in self._placements:
            self._placements[resolved] = occurrence_walk(
                resolved, self.calendar(),
            )
        return self._placements[resolved]
