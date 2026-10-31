"""The app's books beside the BANK's own record, day by day.

Plan step ``bank_import:X-f6e-2``, ruling **R-GF**: the comparison is an
INSTRUMENT and never a gate.  A refusal here deadlocks -- the import is what
fixes a disagreement, and a first import has matched nothing -- so this module
reports and refuses nothing.

**Why it compares MOVEMENT and not only level.**  A balance difference alone
cannot see a defect the owner's own true-up has already absorbed, and that is
measured rather than argued: on the developer's Checking account, over the 145
days his statement and his records both cover, **35 days carry a real
disagreement between his rows and the bank's lines, and 11 of them read as
EXACT agreement in the balance difference** because a same-day balance
assertion cancels the error to the cent.  Among the eleven are the ``$943.41``
of card paybacks finding **N-337** names, ``$2,090.47`` on 2026-07-31, and both
``$0.05`` payroll residues finding **N-391** measures -- **N-239**'s until
plan step balance:X-aw split that row, the residue being a standing per-paycheck
disagreement between the app's figures and the employer's rather than the
horizon defect X-aw closed.  So each day carries
three facts side by side rather than one:

* what the BANK's lines moved that day -- an observation;
* what the app's OWN settled rows moved that day -- the app's claim;
* what a balance ASSERTION moved that day -- the owner correcting the app.

The first two differ by the RESIDUE, which is the whole instrument: money one
side records and the other does not.  The third is why the running balances can
agree while the residue does not, and naming it is what stops the report
calling a true-up an agreement.

**And the level is still reported**, because the residue cannot say how far
apart the two records are TODAY and cannot say which side rests on an
assumption.  A first import seats its anchor by ``assumed_last_day`` (finding
**N-342**), and the signature of a wrong one is a CONSTANT offset across the
whole span rather than a difference that varies by day -- so the summary says
whether the offset is constant, which is the one shape that convicts the
anchor instead of the books.

**Each side shows only what it KNOWS, and a comparison exists only where
both sides have a figure** (plan step ``balance:X-f3c-2b-2c``; rulings
**R-BAL286** for the balance, **R-BAL291** for the movement, re-hearing
**R-BAL285**).  A figure a side does not know is ``None`` on the
:class:`AgreementDay`, never a ``0.00`` standing in for it:

* **The books** open at the CLOSE of their ``opened_on`` (ruling **R-HG**),
  so they state a closing BALANCE for that day and every later one
  (:func:`~app.utils.books_boundary.books_state_balance`) and hold MOVEMENT
  only from the day after (:func:`~app.utils.books_boundary.books_hold`).
  Whatever the bank moved on or before the opening day is already inside the
  opening figure -- the developer's statement reaches back months before his
  books open, which is finding **N-314**'s shape and not a disagreement this
  report can act on.
* **The bank** covers a day's MOVEMENT when the day lies inside the window an
  import declares (:attr:`BankAgreement.runs`), and its BALANCE where a run's
  anchor reaches the day.

So a day's MOVEMENT is compared only when BOTH records cover it, and its
BALANCE only when both state one.  A day only one side covers is still shown,
because hiding a day either record describes would be the report deciding
what the owner may see, and it is counted in no total, because a figure only
one side holds is not an agreement or a disagreement.  *Until this step the
movement half had two other boundaries.*  The books' was the account's first
CASH FACT (finding **BAL-616**): an assertion dated on the opening day put
that day's lines into the totals although the opening had absorbed them, and
a books day before the first cash fact was never compared -- its bank line
was shown and never counted, and a quiet such day was compared only once
something (a row or a back-dated balance) was recorded on or before it.  The
bank's was none at all (finding **BAL-650**): a drawn day between two
imports read ``0.00`` for the bank and was compared against it, so the
counts and totals took a figure the bank never stated, and the reconcile
verdict came out right only through a separate count of imported days.

**The drawn days run to whichever record reaches LATER** (plan step
``balance:X-bk-2``, plan#1192; finding **BAL-651**, rulings plan#2346,
plan#2399 and plan#2400): the bank's last recorded line, or the books' last
RECORD -- a settled row or a true-up
(:func:`~app.services.balance_at.cash_last_record_on`) -- and never past the
reader's NOW.  *They stopped at the last statement line until then*, so a
record made after every statement was never drawn, the Reconcile page's hero
counted none of those days unpriced, and the page read Done over days no
statement had priced.  The days past the bank's record are shown with the
bank's side unknown and are counted in :attr:`BankAgreement.unpriced_days`;
the LEVEL summary (:attr:`BankAgreement.standing_gap`,
:attr:`BankAgreement.constant_offset`) still reads through the bank's last
drawn day (:attr:`ComparedSpan.bank_through`), as it did before (ruling
plan#2399).

**Two halves, and this module is the first** (plan step ``balance:X-bk-2``,
plan#1192, which split the 988-line module before adding to it): the
agreement FOLD here prices every drawn day, and the one-day DRILL-DOWN behind
the agreement page's day view -- every line and every row on one day, each
with its match state -- is :mod:`._day_detail`, re-exported so a caller reads
both off this package.

Services-boundary discipline: no Flask import, no clock read -- the reader's
NOW arrives on the :class:`~app.services.balance_at.BalanceContext`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.services import balance_at, statement_import
from app.utils.books_boundary import books_hold, books_state_balance
from app.utils.dates import days_in_range

from ._day_detail import DayDetail, DayLine, DayRow, day_detail

__all__ = [
    "AgreementDay",
    "BankAgreement",
    "ComparedSpan",
    "DayDetail",
    "DayLine",
    "DayRow",
    "bank_agreement",
    "covered_pieces",
    "day_detail",
]

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)

#: The most days this report will COMPARE, counted back from the bank's last
#: drawn day (:attr:`ComparedSpan.bank_through`); the days the books' records
#: run past it are drawn too, and bounded by the same count apart, so the tail
#: never pushes compared days off the start (ruling plan#2399).
#:
#: **A bound rather than a preference, because the span is user-supplied
#: and nothing else bounds it.** ``bank_statement_lines.posted_on`` carries
#: no CHECK and the SECU adapter parses any four-digit year, so ONE line
#: dated 1900 -- a bank placeholder row, a mis-parsed column, a typo --
#: makes the recorded span 46,000 days wide. Measured by adversarial
#: review 2026-08-24: two lines, one dated 1900-01-01 and one dated today,
#: rendered **26,402,650 bytes over 46,257 rows**, and every later load of
#: that page allocates it again with no way to get the page back short of
#: deleting the import. Two years covers any statement history this app
#: has seen -- the developer's own is 232 days -- and the page SAYS when
#: it has drawn fewer days than are recorded, which is the same discipline
#: :attr:`BankAgreement.recorded_through` applies at the other end.
_MAX_COMPARED_DAYS = 731

#: How many days a CONSTANT offset needs before it means anything.  One day
#: satisfies "every gap is equal" vacuously.
_MIN_DAYS_FOR_A_CONSTANT = 2


@dataclass(frozen=True)
class AgreementDay:
    """One day of the two records, side by side.

    **Every figure is ``None`` where its side does not know it** (plan step
    ``balance:X-f3c-2b-2c``, see the module docstring), and that absence is
    the whole of what decides which days are compared: a day is compared when
    its :attr:`residue` exists, and its balances are compared when its
    :attr:`gap` does.  There is no flag beside the figures saying which days
    count -- *an ``in_records`` boolean did that until this step, a second
    statement of the books boundary that the figures had to be kept in step
    with, and the bank side had no such flag at all (finding* **BAL-650** *)*.

    Attributes:
        day: The civil day.
        bank_lines: What the bank's recorded lines moved -- signed, positive
            money IN, the convention
            :class:`~app.models.statement_import.BankStatementLine` states once
            and the app's own cash leg already shares.  ``0.00`` on a quiet
            day inside an imported window, and ``None`` on a day inside NO
            import's window: nothing the bank said reaches it, which is an
            absence rather than the bank saying nothing moved.
        recorded: What the app's own SETTLED rows moved that day, or ``None``
            on and before the day the books opened
            (:func:`~app.utils.books_boundary.books_hold`): that day's money is
            inside the opening equity (ruling **R-HG**), so the books hold no
            movement for it.
        asserted: What a balance assertion moved that day, EVERY assertion
            included, or ``None`` exactly when :attr:`recorded` is -- an
            assertion's correction is a movement of the books like a row's.
            What an account's books OPENED with contributes nothing here -- it
            is the fold's seed, a stored ``budget.account_openings`` fact
            establishing the level rather than moving money on any day.  *The
            account's first ASSERTION was held back too until plan step
            X-f3c-2a, because the fold had swallowed its whole correction into
            that seed (ruling R-I); it books an ordinary correction now,
            ``$0.00`` wherever the declaration agrees with the books.*
        app_balance: What the app says the account held at the day's end, or
            ``None`` BEFORE the day the books opened
            (:func:`~app.utils.books_boundary.books_state_balance`, ruling
            **R-BAL286**): the books state a balance from the close of their
            opening day, and on an earlier day they did not exist, so no
            figure the fold carries there is a balance the books held.
        bank_balance: What the bank's own record says it held, or ``None``
            when no anchor reaches the day: it lies in a run no statement
            prices, or between runs -- bar the day before an anchored run's
            first line, which that run's lines start from and its anchor
            reaches.
    """

    day: date
    bank_lines: "Decimal | None"
    recorded: "Decimal | None"
    asserted: "Decimal | None"
    app_balance: "Decimal | None"
    bank_balance: "Decimal | None"

    @property
    def residue(self) -> "Decimal | None":
        """Return what the app records that the bank does not, signed, or ``None``.

        **The instrument.**  Positive means the app's own rows moved MORE into
        the account than the bank's lines did that day; negative means the
        bank moved more.  Either way it is money exactly one of the two
        records has, which is the question a reconciliation asks.

        ``None`` unless BOTH movements are known (ruling **R-BAL291**): a day
        only one record covers has nothing to subtract from, and reading the
        missing side as ``0.00`` is the defect finding **BAL-650** names.
        """
        if self.recorded is None or self.bank_lines is None:
            return None
        return self.recorded - self.bank_lines

    @property
    def compared(self) -> bool:
        """Return whether both records cover this day's movement.

        **The one statement of which days the comparison COUNTS**: every
        MOVEMENT total and count on :class:`BankAgreement`, and the verdicts
        :mod:`app.services.outstanding_difference` folds out of it, are taken
        over the days this holds for (the LEVEL readers read the balances
        instead).  It is :attr:`residue` existing, and nothing else.
        """
        return self.residue is not None

    @property
    def gap(self) -> "Decimal | None":
        """Return how far the two running balances stand apart, or ``None``.

        ``None`` unless BOTH balances are known (ruling **R-BAL286**) -- there
        is no second figure to stand apart from.  So the opening day itself
        has a gap -- the books' close that day, which is the opening equity
        as moved by any assertion dated on or before it (the routine
        back-dated assertion finding **N-400** names), against the bank's --
        and a day before it has none.
        """
        if self.app_balance is None or self.bank_balance is None:
            return None
        return self.app_balance - self.bank_balance

    @property
    def agrees(self) -> bool:
        """Return whether this day is compared and its two records say the same thing.

        **Tested on the RESIDUE, never on the gap.**  A gap of zero over a
        non-zero residue is the exact shape this report exists to stop reading
        as agreement: the owner's own true-up cancelled the error that day, and
        eleven of the developer's thirty-five real disagreements look like
        that.  **False on a day that is not** :attr:`compared`, where there is
        no agreement to report -- so a reader counting disagreements takes
        them over compared days, as :attr:`BankAgreement.disagreeing` does.
        """
        return self.residue == _ZERO_MONEY


@dataclass(frozen=True)
class ComparedSpan:
    """Which days were DRAWN, against which days are RECORDED.

    **One value rather than four dates on the report**, because the pair of
    pairs is one subject: a reader has to know both what was compared and what
    exists, or a report of PART of the record reads as a report of the record.

    Attributes:
        first_day: The earliest day DRAWN.
        last_day: The latest day drawn: the later of the bank's last
            recorded line and the books' last record, never past the
            reader's NOW, so no still-Projected row can land inside the range
            (ruling R-G clamps one to ``as_of + 1``).  **It precedes
            :attr:`first_day` exactly when the whole statement postdates that
            NOW**: nothing was comparable, which is a different fact from
            holding no statement.
        recorded_from: The FIRST day the account has a recorded line for.
        recorded_through: The last day it has one for.
    """

    first_day: date
    last_day: date
    recorded_from: date
    recorded_through: date

    @property
    def bank_through(self) -> date:
        """Return the last drawn day the bank's recorded lines reach.

        :attr:`recorded_through` or, where lines run past the reader's NOW,
        that NOW; :attr:`last_day` is the later of this day and the books'
        last record, so the minimum of the two stored ends recovers it.  It was
        :attr:`last_day` itself until the drawn days ran on past the bank's
        record to the books' last one (finding **BAL-651**); the LEVEL
        summary reads through this day (ruling plan#2399), and the two-year
        bound counts back from it, so the books' records running past the
        last line move no compared day.  It precedes :attr:`first_day` where
        :attr:`last_day` does.
        """
        return min(self.recorded_through, self.last_day)

    @property
    def starts_late(self) -> bool:
        """Return whether recorded days fall BEFORE the drawn range.

        True when the recorded span reaches further back than this report
        draws (:data:`_MAX_COMPARED_DAYS`).  On real data that means a line is
        mis-dated, and the page SAYS so rather than rendering the span: two
        lines, one dated 1900-01-01 and one dated today, rendered 46,257 rows
        and 26 MB before the bound existed.
        """
        return self.recorded_from < self.first_day

    @property
    def ends_early(self) -> bool:
        """Return whether recorded days fall AFTER the drawn range.

        True when lines exist past the reader's NOW.  **Reported so the
        truncation is never silent**: understating a comparison is the one
        failure this page cannot afford.
        """
        return self.recorded_through > self.last_day


@dataclass(frozen=True)
class BankAgreement:
    """One account's books beside its bank's record, over the recorded span.

    Attributes:
        account_id: The account compared.
        span: The :class:`ComparedSpan` -- what was drawn, against what exists.
        books_opened_on: The day the account's books opened -- the governing
            opening's ``opened_on``, read off the same walk that priced
            :attr:`days`.  On it and before it the books' movement is ``None``
            on every :class:`AgreementDay`, and before it their balance is
            too.  Never ``None``: an account's fold cannot be built without
            its opening.
        last_record_on: The latest day on or before the reader's NOW the
            books hold a record -- a settled row or a true-up
            (:func:`~app.services.balance_at.cash_last_record_on`) -- or
            ``None`` where they hold none.  The drawn days run to it when it
            is later than the bank's record (finding **BAL-651**), and the
            Reconcile page's hero says how far the records run from it
            (ruling plan#2346).
        runs: The account's imported spans merged into contiguous runs, each
            with what prices it: a
            :class:`~app.services.statement_import.RecordedRun` carrying the
            anchor every bank balance in that run was walked from (or
            ``None``, when no statement in the run places a figure -- the
            LEVEL there is unknown and only the movement half of this report
            answers for its days) and the checkpoints the walk was checked
            against (plan step ``balance:X-bj-1b``, rulings **R-BAL63** and
            **R-BAL65**).  Ascending and disjoint, so two runs are separated
            by at least one day nobody has imported.  **A run is not the
            same question as :attr:`ComparedSpan.recorded_from` and
            :attr:`~ComparedSpan.recorded_through`**: two imports either side
            of a gap reach both of those ends while nobody has read what lies
            between them, and a quiet day inside a gap is indistinguishable
            from a quiet day inside a run by any count over :attr:`days`.
            **Carried on the VALUE and loaded once** (plan step
            **balance:X-f3c-3**), because one reader is
            :mod:`app.services.outstanding_difference`, which folds this report
            down to a verdict for one span and must not issue a query of its
            own: an answer that depended on when it was asked rather than on
            what this report was built from is the shape a memoized read pass
            exists to remove.  It is the ONE home of the run spans: the fold
            that priced :attr:`days` produced it, so no second list of spans
            rides beside it to be kept in step.
        days: One :class:`AgreementDay` per day of the span, ascending.
    """

    account_id: int
    span: ComparedSpan
    books_opened_on: date
    last_record_on: "date | None"
    runs: "list[statement_import.RecordedRun]"
    days: "list[AgreementDay]"

    @property
    def anchored(self) -> bool:
        """Return whether any statement places a figure on a day of any run.

        False is the state BOTH of the developer's pre-X-f6e-1 imports were
        in: the bank column is empty throughout and the page says so once,
        per run, rather than also counting every day as unpriced.
        """
        return any(run.anchor is not None for run in self.runs)

    @property
    def compared(self) -> "list[AgreementDay]":
        """Return only the days BOTH records cover (:attr:`AgreementDay.compared`).

        The days every movement total below is taken over.  A day on or before
        the books opened is not a disagreement -- its bank lines are inside the
        opening equity -- so counting one would report finding **N-314** as
        this arc's defect once per such day; and a day inside no import's
        window is not an agreement, since nothing the bank said reaches it
        (finding **BAL-650**).
        """
        return [day for day in self.days if day.compared]

    @property
    def disagreeing(self) -> "list[AgreementDay]":
        """Return the compared days whose two records differ."""
        return [day for day in self.compared if not day.agrees]

    @property
    def app_ahead(self) -> Decimal:
        """Return the total by which the app's own rows ran AHEAD of the bank.

        **It says which balance ends higher, never whose record holds the
        missing item**, and the distinction is the correction of the obvious
        wrong caption.  A positive residue has TWO causes -- income the app
        recorded and the bank did not, and SPENDING the bank recorded and the
        app did not -- and they sit on opposite sides of the books.  Naming
        this "money you show and your bank does not" would be false of the
        second, which is the commoner one: a bank debit nobody entered.

        Signed positive, matching :attr:`AgreementDay.residue`'s own
        convention, so this and :attr:`bank_ahead` sum to the net.
        """
        return sum(
            (day.residue for day in self.disagreeing if day.residue > 0),
            _ZERO_MONEY,
        )

    @property
    def bank_ahead(self) -> Decimal:
        """Return the total by which the BANK's lines ran ahead of the app's rows.

        The mirror of :attr:`app_ahead`, and with the same two causes reversed:
        a deposit the bank posted that the app never recorded, and spending the
        app recorded that the bank never posted.

        **A MAGNITUDE, positive**, where the residues behind it are negative.
        It was signed so the two totals summed to the net, and that put
        ``-$15,028.03`` on screen under the words "ran ahead" -- a magnitude
        caption over a signed figure, found by two independent adversarial
        reviews 2026-08-24.  The net is :attr:`app_ahead` MINUS this.
        """
        return -sum(
            (day.residue for day in self.disagreeing if day.residue < 0),
            _ZERO_MONEY,
        )

    @property
    def asserted_total(self) -> Decimal:
        """Return what balance assertions moved across the compared days.

        Reported beside the residues because it is the app correcting itself,
        never a movement the bank should show -- and because its size against
        the residues says how much work the true-ups are doing to keep a
        drifting book level.

        **Signed, and a signed sum UNDERSTATES that work**, which is worth
        stating rather than hiding: two ``$5,000`` corrections in opposite
        directions read as ``$0``.  :attr:`asserted_gross` is the same days
        summed by magnitude, and the two together are the honest pair.  An
        earlier draft quoted a figure here that no window produced -- it was the
        sum taken BEFORE the opening assertion was excluded, ``$798.03`` stale,
        found by adversarial review 2026-08-24.  Measurements now live in the
        step's own plan entry, where they are re-derived rather than remembered.

        **Over the compared days only**, like every movement total here, so a
        correction dated on a day no import covers is LEFT OUT (finding
        **BAL-650**, plan step ``balance:X-f3c-2b-2c``): the bank says nothing
        about that day, and the size of a true-up is reported against the
        days the bank's lines can be set beside.  The day-by-day table still
        shows it on its own row.
        """
        return sum((day.asserted for day in self.compared), _ZERO_MONEY)

    @property
    def asserted_gross(self) -> Decimal:
        """Return what balance assertions moved, summed by MAGNITUDE.

        The companion :attr:`asserted_total` needs: corrections that cancel are
        still corrections, and the gross is what says how often the book had to
        be pulled back into line.
        """
        return sum(
            (abs(day.asserted) for day in self.compared), _ZERO_MONEY,
        )

    @property
    def unpriced_days(self) -> int:
        """Return how many drawn days the bank's own record cannot price.

        A day is unpriced when no run's anchor REACHES it: it lies in a gap
        between imports, or in a run that holds no standing bank level
        (ruling **R-BAL64**).  Reported because the page would otherwise say
        *"walked from $X on D"* over a column of dashes, which is a
        derivation claimed and not performed.

        Reproduced by adversarial review 2026-08-24: a ``file_chain`` anchor in
        a January run left every August day unpriced -- including the effective
        day of August's OWN anchored import, whose crossing is empty -- while
        the page named the January figure.  Since plan step
        ``balance:X-bj-1b`` each run walks from its own anchor, so that shape
        counts only the gap between the two runs.

        Returns:
            The count, or ``0`` when no anchor places a balance at all (every
            run's block says THAT instead, and one absence should not be
            reported as two).  Beside an anchored run, an unpriced run's days
            ARE counted (ruling **R-BAL64**): its block names its own
            absence, and the count says how much of the drawn span the bank
            column cannot fill.
        """
        if not self.anchored:
            return 0
        return sum(1 for day in self.days if day.bank_balance is None)

    @property
    def headline(self) -> "AgreementDay | None":
        """Return the latest day BOTH records state a balance for.

        **The Reconcile screen's hero day** (plan step ``bank_import:X-gj-1a``,
        the direction locked in ``docs/design/bank_import_audit.md``): the
        three figures that answer *am I done* -- what the bank says the account
        held, what the books say, and the difference -- have to be about ONE
        day, and it must be a day both records can speak for.

        **It is not** :attr:`standing_gap`\'s **day, and the difference is not
        cosmetic.**  That property answers *how far apart are the two running
        totals at the END of the bank's record*, so it reads
        :attr:`ComparedSpan.bank_through` whether or not an anchor reaches it
        and returns ``None`` when it does not.  A hero built on it would print a
        dash for an account whose statements simply stop before the span does
        -- which is :attr:`unpriced_days`, a state this report already
        measures and already expects.  This walks BACK to the last day that
        carries a figure, so the hero states a real comparison or says there is
        none at all.

        **A LEVEL reader, so it reads the balances and no movement**: the day
        is one whose :attr:`AgreementDay.gap` exists, and the books' balance
        exists from the CLOSE of their opening day (ruling **R-BAL286**).  So
        the opening day itself qualifies, where the books' close that day
        stands beside the bank's -- and a day before it never does, because
        the books state no balance there.  *Until plan step
        balance:X-f3c-2b-2c this walked the days on and after the account's
        first cash fact, a row or an assertion (finding* **BAL-616** *), so
        books restated back before that fact could not put the hero on their
        opening day or a quiet day after it.*

        Returns:
            The :class:`AgreementDay`, whose :attr:`~AgreementDay.gap` is
            guaranteed a figure rather than an absence; or ``None`` when no
            drawn day carries both balances -- no anchor places a bank
            balance, or every day one places falls before the books open.
        """
        for day in reversed(self.days):
            if day.gap is not None:
                return day
        return None

    @property
    def standing_gap(self) -> "Decimal | None":
        """Return how far the two running balances stand apart at the END.

        The LEVEL, as one number: what the app says the account held on the
        bank's last drawn day (:attr:`ComparedSpan.bank_through`, the day the
        page captions it with), less what the bank's own record says.
        Reported because the residue cannot say it -- a residue is per day,
        and the question "how far apart are my books and my bank right now"
        is about the running totals.  **Not the last drawn day**, which it was
        until the drawn days ran past the bank's record to the books' last
        one (finding **BAL-651**): the bank prices none of those later days,
        so this would read ``None`` wherever the records run past the
        statements, and ruling plan#2399 kept the summary as it stood.

        Returns:
            That day's :attr:`~AgreementDay.gap`, or ``None`` when it was not
            drawn, when no anchor places a bank balance on it, or when it falls
            before the books open (ruling **R-BAL286**: the books state a
            balance from their opening day's close, the opening day included).
        """
        through = self.span.bank_through
        return next((day.gap for day in self.days if day.day == through), None)

    @property
    def constant_offset(self) -> "Decimal | None":
        """Return the offset shared on EVERY day the books state a balance for, or ``None``.

        **A constant offset convicts a STARTING FIGURE, not the movements**
        (ruling **R-GF**, finding **N-342**): a level wrong by ``K`` shifts
        every derived balance by ``K`` while leaving every day's movement
        right, and a difference that VARIES by day cannot be that.

        **It does NOT say WHICH starting figure**, and an earlier draft of this
        claimed the anchor -- refuted by adversarial review 2026-08-24.  There
        are two levels in play: the bank's anchor, which a first import seats by
        assumption (**N-342**), and the app's own opening ASSERTION.  A constant
        gap is exactly as consistent with one being wrong as the other.

        **The days are those the books state a balance for** -- the opening
        day and every later drawn day (ruling **R-BAL286**) through the bank's
        last drawn day (:attr:`ComparedSpan.bank_through`), which is where the
        drawn days ended until they ran on to the books' last record (finding
        **BAL-651**, ruling plan#2399 keeping this summary as it stood) -- and
        **a day among them the bank cannot price makes the answer** ``None``:
        the offset was not seen to hold there, so it is not called constant.
        A day before the books open is not among them, since the books state
        no level there to be offset.  *Until plan step balance:X-f3c-2b-2c the
        days were those on and after the account's first cash fact (finding*
        **BAL-616** *), so books restated back before that fact left out
        their opening day and the quiet days after it.*

        Returns:
            The shared offset, or ``None``.  ``None`` covers three cases: no
            anchor places a bank balance on one of those days, the offset
            MOVES (which says something about the records instead), or **too
            few days carry a books balance to call anything constant**.

        **"Constant" needs something to be constant ACROSS.** One day
        satisfies "every gap is equal" vacuously, and an earlier draft reported
        a single day's offset as the signature -- on exactly the population
        N-342 describes, an owner who imports a statement the same week they
        start recording.  Reproduced by adversarial review 2026-08-24.  Two
        days are therefore required.

        **A further guard on "some money actually MOVED" was written and then
        dropped**, because the days read end on a line day: the bank's last
        drawn day carries a line in every reachable case, so the guard could
        not be constructed and would have been an untested branch standing for
        a case that does not arise.
        """
        through = self.span.bank_through
        stated = [
            day for day in self.days
            if day.day <= through and day.app_balance is not None
        ]
        if len(stated) < _MIN_DAYS_FOR_A_CONSTANT:
            return None
        gaps = {day.gap for day in stated}
        if len(gaps) != 1:
            return None
        # A single-element set of ``None`` pops ``None``, which is the answer
        # for an account with no anchor -- so no separate guard says it twice.
        return gaps.pop()


def bank_agreement(
    account: Account, ctx: balance_at.BalanceContext,
) -> "BankAgreement | None":
    """Return this account's books beside its bank's record, day by day.

    Args:
        account: The account to compare.  Must be session-attached; the caller
            owns the ownership check.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`BankAgreement`, or ``None`` when the account has no
        recorded statement line AT ALL -- there is no outside record to compare
        against, which is an absence rather than an empty comparison.
        **A statement whose every line postdates the reader's NOW is not that
        case** and answers with an empty :attr:`BankAgreement.days`. Answering
        ``None`` there made the page tell the owner this account held no bank
        lines while the statements page one click away listed them, and nothing
        in the importer refuses a future-dated line -- reproduced by adversarial
        review 2026-08-24.

    Raises:
        BaselineMissingError: When *ctx* carries no scenario.

    **The span ends at the later of the recorded lines' last day and the
    books' last record (at most two years past the lines), and never past the
    reader's NOW**, and the NOW bound
    is load-bearing rather than defensive: a still-Projected row lands at
    ``as_of + 1`` (ruling R-G), so a range stopping at ``as_of`` cannot
    contain one.  Past that day the app's balance carries PLAN, and setting a
    plan beside a bank line would report agreement where the app has merely
    predicted what the bank did -- or a disagreement about money nobody has
    spent.  When it truncates, :attr:`ComparedSpan.recorded_through` says so.
    **The books' last record moves the end** (finding **BAL-651**, rulings
    plan#2346 and plan#2399): a day the books describe after every statement
    is drawn, unpriced, rather than hidden -- which is the Reconcile page's
    reason not to read Done over it.
    """
    span = statement_import.recorded_span(account.id)
    if span.first_day is None or span.last_day is None:
        return None
    last_record_on = balance_at.cash_last_record_on(account, ctx)
    bank_end = min(span.last_day, ctx.as_of)
    # The two-year draw counts back from the BANK's end, as it did before the
    # tail existed, so the days the books run past the last line never push
    # compared days off the start (ruling plan#2399: the summary stays).  The
    # tail is bounded apart by the same constant, for the bound's own reason:
    # one line dated 1900 beside records through today would otherwise draw
    # every day between them.
    first_day = max(
        span.first_day, bank_end - (_MAX_COMPARED_DAYS - 1) * _ONE_DAY,
    )
    last_day = bank_end
    if last_record_on is not None:
        last_day = max(
            bank_end,
            min(last_record_on, bank_end + _MAX_COMPARED_DAYS * _ONE_DAY),
        )
    # BOTH ends can leave the drawn range, and an inverted one is legal: a
    # statement entirely in the future yields no comparable day and still
    # reports what it holds.
    days = days_in_range(first_day, last_day)
    series = balance_at.cash_daily_facts_series(
        account, ctx, first_day, last_day,
    )
    bank_moves = _bank_moves(account.id, first_day, last_day)
    # ONE fold prices the days AND says which runs the lines form and what
    # each walks from; the runs are read off it rather than queried again --
    # and they are also what says which days the bank's movement covers.
    folded = statement_import.fold_bank_balances(account.id, days)
    return BankAgreement(
        account_id=account.id,
        span=ComparedSpan(
            first_day=first_day,
            last_day=last_day,
            recorded_from=span.first_day,
            recorded_through=span.last_day,
        ),
        books_opened_on=series.opened_on,
        last_record_on=last_record_on,
        runs=folded.runs,
        days=[
            _agreement_day(day, series, bank_moves, folded) for day in days
        ],
    )


def _agreement_day(
    day: date,
    series: "balance_at.CashDaySeries",
    bank_moves: "dict[date, Decimal]",
    folded: "statement_import.BankBalances",
) -> AgreementDay:
    """Return one day of the two records, each figure ``None`` where its side does not know it.

    **The one place each side's knowledge is decided** (plan step
    ``balance:X-f3c-2b-2c``): the books' through the two readings of their
    boundary (:mod:`app.utils.books_boundary`), the bank's movement through
    the imported windows and its balance through the anchors -- both read off
    the fold that is already loaded, so this issues no query.

    Args:
        day: The civil day.
        series: The account's day facts, and the day its books opened.
        bank_moves: What the bank's lines moved on each drawn day carrying one.
        folded: The bank's fold, whose runs are the imported windows.

    Returns:
        The :class:`AgreementDay`.
    """
    facts = series.facts[day]
    books_moved = books_hold(series.opened_on, day)
    return AgreementDay(
        day=day,
        bank_lines=(
            bank_moves.get(day, _ZERO_MONEY)
            if _imported(folded.runs, day) else None
        ),
        recorded=facts.recorded if books_moved else None,
        asserted=facts.asserted if books_moved else None,
        app_balance=(
            facts.balance
            if books_state_balance(series.opened_on, day) else None
        ),
        bank_balance=folded.balances.get(day),
    )


def _imported(runs: "list[statement_import.RecordedRun]", day: date) -> bool:
    """Return whether *day* lies inside some import's declared window.

    Args:
        runs: The account's runs (:attr:`BankAgreement.runs`) -- the declared
            windows, merged.
        day: The civil day.

    Returns:
        True when a run spans *day*, so the bank's lines describe its
        movement: a quiet day there moved ``0.00``.  False on a day between
        or outside every run, which nothing the bank said reaches (finding
        **BAL-650**).
    """
    return bool(covered_pieces(runs, day, day))


def covered_pieces(
    runs: "list[statement_import.RecordedRun]", first_day: date, last_day: date,
) -> "list[tuple[date, date]]":
    """Return the pieces of ``[first_day, last_day]`` the imported windows cover.

    **The one statement of which days an import covers** (``CLAUDE.md`` rule
    14, plan step ``balance:X-f3c-2b-2c``), read by :func:`_imported` per
    drawn day, by ``outstanding_difference.span_agreement``'s ``imported``
    count and by its ``_cut_at_windows``.  *Before this step the last two
    spelled it for themselves -- the count and the cut's edges -- and nothing
    asked it of a day's movement, so a compared day need not have been an
    imported one (finding* **BAL-650** *: its fixture compared 16 days, 14 of
    them inside no import's window).*  Interval arithmetic, never a day list:
    a mistyped 1900 costs one piece per run, not 46,000 days.

    Args:
        runs: The merged windows (:attr:`BankAgreement.runs`), ascending and
            disjoint.
        first_day: The range's first day, inclusive.
        last_day: Its last day, inclusive.  An inverted range has no piece.

    Returns:
        ``[(first, last), ...]``, ascending and disjoint: each run clipped to
        the range, for every run that overlaps it.
    """
    return [
        (max(run.first_day, first_day), min(run.last_day, last_day))
        for run in runs
        if max(run.first_day, first_day) <= min(run.last_day, last_day)
    ]


def _bank_moves(
    account_id: int, first_day: date, last_day: date,
) -> "dict[date, Decimal]":
    """Return what the bank's recorded lines moved on each day of a range.

    Args:
        account_id: The account whose lines to read.
        first_day: Inclusive first day.
        last_day: Inclusive last day.

    Returns:
        ``{day: signed total}``, one entry per day in the range carrying at
        least one line.

    **The aggregate is the import package's**
    (:func:`~app.services.statement_import.bank_daily_movements`), narrowed to
    the range here rather than restated as a second
    ``SUM(amount) GROUP BY posted_on`` -- which is what this was, and what an
    adversarial review measured on 2026-08-24 as two queries answering one
    question.  The narrowing is in Python because an account holds a few
    hundred recorded days and the same list is what the fold prefix-sums.
    """
    return {
        day: amount
        for day, amount in statement_import.bank_daily_movements(account_id)
        if first_day <= day <= last_day
    }
