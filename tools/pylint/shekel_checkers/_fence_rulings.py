"""W9909's classification registry: which public names are NOT producers.

The DATA half of the balance-seam fence.  :mod:`.balance_seam` holds the
checker -- what W9909 asks and where it asks it -- and this holds the answers
it looks up.

**Why they are two modules** (plan step ``balance:X-f3c-2b-2a``).  W9909 is
FAIL-CLOSED: every public top-level function in a scoped package must be
explicitly classified, so this registry grows whenever any of those packages
gains a name, while the checker beside it changes only when the RULE changes.
Held together they put a monotonically growing table inside a file with
pylint's 1000-line ceiling over it -- and that ceiling BOUND, at exactly 1000,
the first time a step added five names.  A fail-closed gate whose registry
cannot grow refuses the next honest edit rather than the next unsafe one,
which is a fence that has stopped being structural.

Nothing else moved with it: the scope sets, the rulings and every rationale are
here verbatim, and :mod:`.balance_seam` imports the two names it reads.

**The ceiling bound a second time at plan step ``balance:X-bi-6a``** (1,007
lines when the cash-ledger leaf gained one name), and the same remedy applied
one level down: the cash-ledger entry's NAMES and their whys -- the largest
ruling in the table and the one every balance step grows -- live in
:mod:`._fence_rulings_cash_ledger`, and the entry below composes them.  The
table, its keys and every scope set are still here, so the checker and the
tests over it read one place.
"""

from __future__ import annotations

from ._fence_rulings_cash_ledger import CASH_LEDGER_NON_PRODUCERS

# ── Fail-closed completeness (W9909) ────────────────────────────────
#
# A name-keyed deny list fails OPEN: a new function added inside a covered
# module is unguarded by default until a human remembers to list it.  That is
# not a hypothetical -- it shipped twice (``investment_base_balance_map``,
# ``loan_owed_at_dates``: each called past the seam with the fence silent), and
# the same hole was re-measured four more times during Phase D (findings
# N-28 / N-31).  Two identical misses is a design defect in the FENCE, not a
# lapse in diligence, so the default is inverted here: every PUBLIC top-level
# function defined in a scoped module must be explicitly classified as either a
# producer (fenced) or a non-producer (deliberately callable), and one that is
# neither is an error AT ITS DEFINITION -- the moment it is typed, in the same
# per-edit hook run.  A producer can no longer slip through by omission;
# forgetting is now the loud path.  Since plan step E1e every scoped package's
# PRODUCER set is empty, so the only classification a new public name here can
# legitimately receive is NON-producer: if it answers balance-at-T it belongs
# inside ``balance_at`` as a private submodule instead.
#
# Scope (the D3 residue): the PUBLIC packages that hold balance INGREDIENTS
# outside W9910's protection -- the two genesis loan-ledger packages (the
# posting attribution reads and the walk they are built on), the cash ledger
# leaf, the loan resolver tier and its db-facing seeding module, and the
# account-kind classifier.  The ``balance_at`` seam package is deliberately NOT
# scoped: its public functions ARE the seam entries every consumer is supposed
# to call, so "unclassified" is meaningless there, and its private submodules
# are W9910's.  ``ledger_report_service`` is not scoped either, and that one is
# a measured GAP rather than a ruling (finding N-35: a public balance-at-T born
# there rates 10.00/10 with every gate silent) -- closing it is its own step,
# because every public name in that package must then be classified.
# Classes themselves are not scoped -- the historical misses were functions, and
# a dataclass (``AnchorPoint`` / ``CashLedgerWalk``) is data the seam folds over,
# not an answer to "what is the balance at T".  Their public METHODS ARE scoped
# (see :func:`_is_public_export_surface`), which is why ``visible_on`` and
# ``delta`` carry rulings below.
#
# The non-producer rulings are keyed BY MODULE, not pooled into one flat set.
# A pooled set would let a name ruled harmless in one module silently exempt a
# same-named function later added to another (``dated_deltas``,
# ``income_amount``, ``resolve_anchor`` are all generic enough to collide -- the
# first is in fact ruled TWICE below, once per ledger leaf) -- which is the same
# fail-open shape, one level down.  Each module owns its own ruling.
# The five SEAM-PRIVATE ENGINE rulings are GONE (plan step D3).  They existed
# only as N-31's "travel": a name-keyed fence could not see a producer born
# inside the seam, so each moved module kept a classification entry UNTIL the
# package boundary became structural.  D-gate shipped (W9910): a private
# ``balance_at`` submodule cannot be imported from outside the package in any
# spelling, so a producer born there is unreachable until someone re-exports it
# on the seam's public ``__init__`` -- the deliberate, reviewed act the
# classification existed to force.  The ``balance_at`` package itself stays
# deliberately un-scoped: its public functions ARE the seam entries every
# consumer calls.
#
# ``_context`` is the seam-private module whose ruling SURVIVES D3, because
# the engine rationale above does not reach it (D3's own adversarial review
# measured the gap): ``BalanceContext`` is already re-exported on the seam's
# public ``__init__``, so a new public METHOD on it reaches every route holder
# with NO ``__init__`` edit -- a probe ``ctx.balance_now(account)`` folding the
# memoized walk rated 10.00/10 with the ruling deleted.  W9910 sees imports,
# never attribute access on an object a consumer legitimately holds, so W9909
# is the only gate on this module's surface; a producer belongs in a private
# seam MODULE, never on the context.  Since ruling **R-BAL146** three of the
# context's methods live in ``_recurrence_memos``, a mixin ``BalanceContext``
# inherits: the same reach, from a SIBLING module that matches neither
# ``_context``'s name nor its prefix, so it is scoped here beside it.
_SEAM_PRIVATE_CONTEXT_MODULES = frozenset({
    "app.services.balance_at._context",
    "app.services.balance_at._recurrence_memos",
})
_LOAN_LEDGER_DEFINING_MODULES = frozenset({
    "app.services.loan_ledger",
    "app.services.loan_posting_service",
})
# The loan-payment LOADER module (plan step D3, from its adversarial review).
# Its PRIVILEGE is gone -- plan step E1d-b took it off the reader allowlist above
# with ``confirmed_loan_view`` -- but the scope STAYS, because privilege was never
# the only reason: this is a public module holding every ingredient of a loan
# balance (the payment feed, the escrow-netted amounts, the contractual P&I, the
# whole loaded ``LoanContext``) OUTSIDE W9910's protection, exactly like
# ``loan_ledger`` and the ``loan_resolver`` tier beside it.  The measured shape
# that put it here: a documented public wrapper ``loan_balance_for_tile()``
# returning ``confirmed_loan_balance_at(...)`` rated 10.00/10 under the full
# fail-on set (the N-28 shape).  Dropping the scope now would be a LOOSENING
# bundled with E1d-b's tightening, which is the D1b lesson.
_LOAN_PAYMENT_SEAM_MODULES = frozenset({
    "app.services.loan_payment_service",
})
# The PURE loan-resolver package (plan step D3, closing finding B-12): the
# schedule composer, the rate-period machinery, and the anchor replay -- the
# "wholly unfenced producer tier below the fence" the findings ledger named.
# Until D2a its ``resolve_loan`` returned a bundle carrying a balance-at-T with
# NO fence entry anywhere; the field is deleted, and this scope makes the
# package fail CLOSED the same way ``cash_ledger`` does: a new public
# balance-at-T born in any of its submodules errors at its definition instead
# of shipping reachable with every gate green (the N-28 shape, measured four
# times elsewhere).  A package key, prefix-matched, so a new submodule is
# scoped the day it is written.
_LOAN_RESOLVER_ENGINE_MODULES = frozenset({
    "app.services.loan_resolver",
})
# The cash LEDGER leaf (plan step D1c), scoped for the W9909 COMPLETENESS
# check: "may this module CALL a producer?" and "must a new public function
# here be CLASSIFIED?" are different questions, and D1a's own adversarial
# review proved that conflating them opens the exact hole W9909 exists to
# close.  Measured: a new
# public ``running_balance_map`` folded from ``resolve_anchor`` +
# ``period_subtotals`` + ``round_money`` (that reduction has since been deleted
# at plan step X-c2b3; ``sum_projected`` is the surviving one, and the probe
# reassembles from it unchanged) -- not one fenced name among them --
# rated 10.00/10, AND so did a route consuming it.  A real balance-at-T on a
# screen outside the seam with every gate silent, which is the third instance of
# the miss this checker's header calls "a design defect in the FENCE, not a
# lapse in diligence".
#
# This leaf is the likeliest birthplace of the next one: it holds every
# ingredient of a cash balance-at-T -- the anchor FACT, the row loader, what one
# row is WORTH, and what a set of rows SUMS TO -- and plan step X2 ("a cash
# account is an event stream") builds the cash fold directly on top of it.
#
# **It is ONE package key, and that is the point (plan step D1c).**  Until D1c
# these names lived in two flat modules (``cash_events`` / ``period_flows``)
# plus five stranded inside ``balance_calculator``, so the scope had to be a
# hand-written LIST -- which fails open exactly one level up: creating a sibling
# module is how you escape a module-keyed gate (finding N-28, and Section 8's
# "a fail-CLOSED gate is scoped by module identity").  A package is matched by
# PREFIX (:func:`_fenced_module_ruling`), so a submodule added inside it is
# scoped the day it is written, with no constant to remember.  Same shape as
# ``app.services.loan_ledger`` below, for the same reason.
_CASH_LEDGER_MODULES = frozenset({
    "app.services.cash_ledger",
})
# The account-KIND classifier (plan step D1b).  Same asymmetry as the cash-event
# sources above, reached from the other direction: it came OFF the W9906
# allowlist (it calls no producer) and STAYED scoped here.  It is not a cash
# event source, so it carries its own constant rather than joining that set --
# each scope entry names the reason it exists.
#
# **This is the canonical rationale for that entry; the registry entry below and
# the module's own docstring point HERE rather than restating it.**  D1b as
# planned deleted BOTH memberships, and dropping this one was measured unsafe:
#
#   * the module DEFINED the loan forward-projection producers through all of
#     Phase C and shed the last at ``f445aa77``, ONE DAY before D1b -- so "it
#     holds no producer" is a fact about a very recent tree, not a property; and
#   * a module's reachability surface is its PARAMETERS, not its imports.
#     ``classify_account`` takes a live ORM ``Account``, so a public
#     ``balance_on(account, target)`` folding ``account.transactions`` needs no
#     import this module lacks.  Measured with the entry dropped: that function,
#     and a route rendering it, both rated 10.00/10.
#
# What W9909 actually covers is public FUNCTIONS and public METHODS of public
# classes (see :func:`_is_public_export_surface`).  It does not see a balance
# computed in a dunder and exposed as an attribute, nor a module-level alias of a
# private function -- both rate 10.00/10 here and in every other scoped module.
# That gap is structural and pre-dates D1b; it is named so this entry is not read
# as a stronger guarantee than it is.
_KIND_CLASSIFIER_MODULES = frozenset({
    "app.services.account_projection",
})

# The producer-free half of the cash valuation (plan step X-au-c2).  It was
# defined inside ``cash_ledger`` and moved DOWN a tier because the loan stack
# needs ``settled_contribution`` and could never import that package: its
# ``_amount_source`` reached UP into ``loan_payment_service`` for amount rule
# 4's producer, so any loan-stack module naming ``cash_ledger`` closed an import
# cycle.  ``cash_ledger`` re-exports ``settled_contribution``, the only one of
# the three that was ever public, so no consumer moved.  *It was named
# ``owned_contribution`` until plan step X-bx, which deleted its fall-through
# onto the plan column and renamed it for the assertion that survived.*
#
# **THAT REACH IS GONE as of plan step X-au-g-2a**, which moved rule 4's
# producer into ``cash_ledger`` -- so the loan stack CAN name that package now,
# and this module's split is no longer forced by an import cycle.  The scope
# entry stays for the reason stated below (a fenced module's contents extracted
# into an unfenced neighbour is the N-28 shape), which never depended on the
# cycle; whether the module itself should fold back into ``cash_ledger`` is a
# separate question this step deliberately did not take, and it is not answered
# by silence here.
#
# **It is scoped here the day it is created, and that is the whole N-28
# lesson applied to its own remedy.**  This module's rationale for keying the
# cash ledger on a PACKAGE says a fail-closed gate is escaped by adding a
# sibling -- and a new TOP-LEVEL module is that escape one level further out.
# Extracting a fenced module's contents into an unfenced neighbour would have
# silently un-ruled ``settled_contribution``, which is the exact shape (a fence
# that fails open when the code moves) findings N-28 / N-31 are about.  A flat
# module rather than a package, so the key is exact rather than prefix-matched;
# if it ever becomes a package the prefix match already covers the submodules.
_ROW_VALUATION_MODULES = frozenset({
    "app.services.row_valuation",
})
# The recurring DEFINITION reader (plan step R7d-a).  It held one loan figure
# already -- the standing overpayment threaded into every payoff projection --
# and that step moved the loan-payment SETTINGS reads here off
# ``loan_payment_service``, whose 1000-line ceiling was what the move paid for.
# Two of the moved names answer a MONEY question ("what does this loan's own
# definition say one installment costs"), so the scope follows them rather than
# letting a fenced module's contents leave the fence by changing address --
# which is the loosening-bundled-with-a-move shape the D1b lesson names.
_RECURRING_DEFINITION_MODULES = frozenset({
    "app.services.recurring_transfer_query",
})

# Per-module rulings: {module: (producer set, non-producer set)}.  Every PUBLIC
# top-level function defined in one of these modules must appear in one of its
# two sets.  Adding a name to a non-producer set is a DELIBERATE ruling that it
# does not answer "what is account A's balance at time T"; if in doubt, it is a
# producer (a false negative is the dangerous mode for a fence).
_FENCED_MODULE_RULINGS = {
    # The producer-free half of the cash valuation
    # (:data:`_ROW_VALUATION_MODULES`).  The EMPTY producer set is the same D3
    # invariant its parent package carries, and it is even easier to hold here:
    # this module imports no producer and cannot -- that is the property that
    # made it a separate module.
    "app.services.row_valuation": (frozenset(), frozenset({
        # The two arms of what one row is worth that need no producer, ruled
        # on exactly the ground the ``cash_ledger._amounts`` valuation family
        # below stands on: each answers what ONE ROW is worth, and none folds,
        # dates, sums, or reads an anchor.  ``fixed_contribution`` is the
        # status / soft-delete / settlement gate every other form shares, and
        # ``settled_contribution`` composes that gate with a REFUSAL for a
        # reader whose rows have all settled.
        #
        # **There were FOUR, and two went in consecutive steps.**  Plan step
        # X-bu deleted ``owned_amount`` -- ``own_figure`` applied to a
        # transaction's own column, and the BUDGET twin of the accessor above
        # from plan step X-au-c2b: a public accessor answering "what is this
        # row's plan" from the column is a second spelling of what
        # ``cash_ledger.resolve_transaction_amount`` answers, and the two
        # parted on a row a cutover had declared DERIVED (finding **BAL-462**).
        # Plan step **X-bx** then took ``own_figure`` itself, which that
        # deletion had left with callers only inside
        # ``cash_ledger._amount_source``: it MOVED there as the private
        # ``_own_figure``, so it is not un-ruled here but out of the fence's
        # reach entirely (finding **BAL-465**).  The same step renamed
        # ``owned_contribution`` to ``settled_contribution``.
        #
        # Dropping a name here is REQUIRED rather than tidy, whether it left by
        # deletion, by a move or by a rename: the reverse-staleness arm of
        # ``test_classification_sets_match_the_real_fenced_modules`` fails on a
        # ruling for a function the module no longer defines, because such an
        # entry would silently un-fence whatever the name was reused for.
        "fixed_contribution",
        "settled_contribution",
        # The settlement RECORD's three names (plan step X-au-c3), ruled on the
        # same ground: each answers about ONE ROW, from that row's own columns
        # and children, and none folds, dates, sums or reads an anchor.
        # ``settled_figure`` is what the row RECORDED as having moved -- a
        # stored fact, not a projection of one -- ``purchases_total`` is the
        # reduction an envelope's record defers to (it takes entries, not even a
        # row), and ``settled_amounts_by_key`` is ``settled_figure`` mapped over
        # a render's rows and legs, each valued by its parent and keyed by
        # ``cell_key`` (ruling plan#1983), which is a LOOP over a non-producer
        # and not a fold toward a balance: it dates nothing and sums nothing.
        # The batch
        # is the one to look at twice, and it is ``display_amounts_by_id``'s
        # sibling one tier up -- that map needs an ``AmountBasis`` because a
        # plan may be DERIVED, and this one needs none because a record is the
        # row's own, which is exactly why it is down here.
        # ``recorded_figure`` and ``recorded_amounts_by_id`` -- the TOTAL twin
        # and its batch, ruled here on the same ground -- were DELETED at plan
        # step ``balance:X-bi-4b-1``: the one clause between the pairs ("a
        # settled row that RECORDS NOTHING answers ``None`` here and raises
        # there") named a state a settled row can no longer be in, since its
        # record is the sum of its entries and a row with none is the $0.00
        # record (rulings R-BAL80, R-BAL82); the edit doors read the counting
        # pair now.  Dropped here for the reason the paragraph above gives.
        # Four LEG twins were ruled here on the same ground until ruling
        # plan#1983 ("price the parent, draw the side") deleted them:
        # ``leg_settled_figure``, ``leg_fixed_contribution``,
        # ``leg_settled_contribution`` and ``leg_settled_amounts_by_key``, each
        # the row form arm for arm over a leg's parent transfer.  The row forms
        # take a PARENT now (a row or a transfer) and the batch is keyed by
        # ``cell_key``, so a leg is valued by its transfer through the same
        # names.  Dropped here, as was ``settled_amounts_by_id``, renamed to
        # ``settled_amounts_by_key``, for the reason the paragraph above gives.
        "purchases_total",
        "settled_amounts_by_key",
        "settled_figure",
    })),
    # The cash LEDGER leaf (plan steps D1a + D1c).  Its ruling -- the largest
    # in this table, and the one every balance step grows -- lives whole in
    # :mod:`._fence_rulings_cash_ledger` since plan step ``balance:X-bi-6a``,
    # when this registry reached 1,007 lines against the 1,000 ceiling.  The
    # EMPTY producer set is the D3 invariant stated as data; that module
    # carries the why.
    "app.services.cash_ledger": (frozenset(), CASH_LEDGER_NON_PRODUCERS),
    # The account-KIND classifier -- why it is scoped is recorded once, at
    # :data:`_KIND_CLASSIFIER_MODULES`.  Its ``find_period_containing_date``
    # went to ``loan_ledger._visible`` at D1b (chronology belongs with the rules
    # built on it), leaving two names -- and on to ``pay_calendar`` at C2-d,
    # which DELETED it: two relocations for one primitive, because "the rules
    # built on it" were themselves a copy of a question the calendar owns.
    "app.services.account_projection": (frozenset(), frozenset({
        # The canonical kind classifier and the payroll-funding predicate:
        # account metadata, not balances.
        "classify_account",
        "is_payroll_deduction_funded",
        # The revolving-credit predicate (plan step credit_card:CC-1): reads
        # ONE boolean off the account's type row and dates nothing, sums
        # nothing -- the same metadata question as the two above, on a flag
        # the seed alone sets.
        "is_revolving",
    })),
    # The loan WALK leaf (plan step B1, renamed ``_fold`` -> ``_walk`` at D-fold):
    # the event stream, the split, and the one running-balance replay over them,
    # which the posting ledger and the read seam both derive from.  Scoped WHOLE
    # for the same reason its sibling below is: a new balance-at-T reader born in any of its
    # submodules would reproduce the hole this check kills.  Its producer set is EMPTY and stays
    # that way -- D-fold moved the fold into the seam -- so every public name this leaf defines
    # is a non-producer that must say why, and one that DOES answer balance-at-T belongs inside
    # ``balance_at``.
    "app.services.loan_ledger": (
        frozenset(), frozenset({
            # The running-balance WALK: it replays the loan's events into
            # per-payment splits and per-anchor corrections -- FACTS in
            # CONTRACT-time order, not a balance-at-T.  Turning those facts into a
            # balance owed on a DATE is the FOLD (re-key by visible date,
            # prefix-sum), which moved INTO the balance seam (``balance_at._fold``)
            # at plan step D-fold.  So a consumer holding a walk cannot reach a
            # balance from a public leaf name, and the walk needs no fence -- it was
            # fenced only while the fold was one call away in the same leaf.  Both
            # sides take it: the posting writer projects it into corrections, the
            # seam's read pass folds it.
            "walk_loan_ledger",
            # A loan's CONTRACT TERMS, loaded (plan step recurrence:R25, ruling
            # R-R105): ``build_loan_calendar`` returns the loan's
            # ``LoanCalendar`` -- its origination, due day, rate periods and
            # escrow lines -- and ``LoanCalendars`` is the read pass's memo of
            # it, whose ``loan_params_of`` / ``loan_calendar_of`` methods hand
            # back the loan's ``LoanParams`` row and that same calendar.  Terms, not a
            # balance: nothing in them is a figure owed on any date, and the
            # charges built from them are ``contract_charges``' ruling below.
            "build_loan_calendar",
            "loan_calendar_of",
            "loan_params_of",
            # The CHARGE calendar (plan step X-au-g-2c-3b-1; the contract's
            # since recurrence:R16-c-2, ruling R-R100) -- the TIME half of a
            # walk, and it does not answer balance-at-T.  Its DATES
            # (``installment_dates``) were ruled here too until plan step
            # recurrence:R16-c-2 moved them to ``app.services.installment_calendar``,
            # which this registry does not scope; the ruling went with the name
            # rather than staying stale.
            #
            #   * ``contract_charges`` returns one charge per installment, and
            #     each charge carries a RATE and an escrow AMOUNT --
            #     deliberately not an interest amount.  Interest accrues on the
            #     balance standing when the charge falls, and only a WALK knows
            #     that (an anchor between two payments resets it).  So this
            #     states what a period COSTS per dollar owed, and a caller
            #     holding it still cannot reach a balance without the running
            #     walk the seam owns.  That is the same ruling
            #     ``walk_loan_ledger`` carries above: dated facts, not a
            #     balance-at-T.  Since plan step X-au-g-2c-3b-2 the charge
            #     carries the whole governing ``RatePeriod`` rather than a bare
            #     rate, which changes nothing here: a ``RatePeriod`` is the
            #     loan's CONTRACT (its rate, its level P&I, its term), a
            #     derived snapshot already public from ``rate_period_engine``,
            #     and no amount owed can be read off it.
            "contract_charges",
            # The real principal/interest/escrow split of a payment -- a
            # decomposition of CASH, not an account balance.  The whole-loan list
            # and its per-payment construction carry one ruling: cash in, four
            # parts out, no balance-at-T.
            #
            # ``apply_payment_cash`` LEFT this entry at plan step X-au-g-2c-3a and
            # its ruling is DROPPED rather than moved, because there is nowhere to
            # move it TO and nothing left for it to say.  The allocation now lives
            # in ``app.utils.money``, which is not a fenced module and cannot
            # become one: every fenced module is under ``app.services``, and that
            # leaf's defining property -- the property the step exists to create,
            # and which ``test_loan_allocation_is_one_rule`` pins -- is that it
            # reaches NOTHING in ``app.services``.  A module structurally incapable
            # of importing the seam is structurally incapable of producing a
            # balance-at-T, so the classification is unnecessary rather than
            # inconvenient.  This checker asks what a module DEFINES, not what it
            # re-exports, so ``loan_ledger`` keeping the name in its public surface
            # does not keep the ruling alive.  Same shape as `095ea62f`.
            #
            # ``split_payment_cash`` -- the month-charging composition over that
            # allocation -- is DELETED at plan step X-au-g-2c-3b-2 and its ruling
            # goes with it.  It was correct only while a loan took ONE payment per
            # accrual period, which is the assumption that step exists to remove;
            # the composition survives only as a test oracle, outside ``app/``.
            #
            # ``split_one_payment`` -- the per-field copy of a replay outcome into
            # a ``LoanPaymentSplit`` -- is DELETED at plan step recurrence:R16-c-1
            # and its ruling goes with it: the outcome IS the record now, and the
            # ten read-through properties below are how its parts are read.
            "compute_loan_payment_splits",
            # The ONE loan replay (plan step X-au-g-2c-3b-2) and the stream it
            # folds.  Chronology and cash decomposition, not balance-at-T.
            #
            #   * ``loan_event_stream`` yields the loan's FACTS mapped onto three
            #     dated events -- the same ruling its predecessor
            #     ``merge_anchor_and_payment_events`` carried.  A reset event
            #     carries an asserted ``anchor_balance``, but a user-asserted
            #     stored fact is not a balance-at-T (the ruling ``resolve_anchor``
            #     carries above), and it decides no order at all now.
            #   * ``replay_loan_events`` returns one CASH DECOMPOSITION per
            #     payment and one displaced running balance per assertion.
            #     **It DOES hand back dated running balances** -- an outcome's
            #     event carries ``on_date`` and its split carries
            #     ``balance_after`` -- and an earlier draft of this ruling
            #     claimed the opposite twice ("it answers no DATE"; "a consumer
            #     cannot reach a balance without the seam-private prefix-sum").
            #     Both were false of this very code, and an adversarial review
            #     measured it: five public calls yield ``[(date, owed)]``.  The
            #     ruling is restated on the ground that actually holds.
            #
            #     THE GROUND: this exposes nothing ``dated_deltas`` did not
            #     already expose.  That name has been public since plan step E1a
            #     and accumulating over it yields the seam's OWN answer, keyed by
            #     each event's VISIBLE date.  What the replay returns is keyed by
            #     CONTRACT time -- the installment an event belongs to, not the
            #     day it counts from -- which is strictly FURTHER from
            #     "what is owed on date D" than the public name beside it.  The
            #     re-key is the seam's clock and the prefix-sum is seam-private
            #     (``balance_at._fold``), and neither is reachable from here.  A
            #     consumer that wants a balance still has an easier public road
            #     that this ruling already covers, so the classification adds no
            #     exposure rather than resisting one.
            "loan_event_stream",
            "replay_loan_events",
            #   * ``load_loan_stream`` (plan step recurrence:R16-c-1) is
            #     ``walk_loan_ledger``'s LOAD half: the same rows mapped onto
            #     the same stream, before any replay -- ``loan_event_stream``'s
            #     ruling with the loads attached; its ``visible_by`` bound is
            #     ``confirmed_shadows_through``'s, below, applied at the load.
            "load_loan_stream",
            #   * ``replay_loan_stream`` (plan step recurrence:R16-c-1) is that
            #     same replay handed a stream and returning the walk --
            #     ``walk_loan_ledger`` without the loads -- so it carries BOTH
            #     rulings above and adds nothing to either: the seam calls it
            #     over a stream holding the loan's projections behind its facts,
            #     and what comes back is still keyed by contract time.
            #   * ``projection_boundary`` returns a ``date`` -- the day after the
            #     loan's latest recorded fact -- and cannot yield a figure.
            #   * ``with_contract_charges`` (recurrence:R16-c-2, ruling R-R100)
            #     returns a stream with its charges attached -- the stream
            #     ruling above plus ``contract_charges``' -- before any replay.
            "projection_boundary",
            "replay_loan_stream",
            "with_contract_charges",
            # ``LoanLedgerWalk``'s two views (recurrence:R16-c-1) and
            # ``PaymentOutcome``'s ten read-through properties.  Not one of them
            # derives anything: ``settled_splits`` / ``projected_splits`` filter
            # the walk's own outcome list by its ``is_projected`` flag, and each
            # property returns a field of the outcome's ``event`` or ``split``
            # under the flat name every reader spells (``outcome.principal`` for
            # ``outcome.split.principal``).  ``balance_after`` is the replay's
            # contract-time running balance, the exposure ``replay_loan_events``'s
            # ruling above already owns; ``charge_date`` / ``due_date`` /
            # ``visible_on`` are dates; ``source`` is the caller's own record
            # handed back; the other five are the cash decomposition.
            "balance_after",
            "cash",
            "charge_date",
            "due_date",
            "escrow",
            "excess",
            "interest",
            "principal",
            "projected_splits",
            "settled_splits",
            "source",
            "visible_on",
            # Chronology, not balance: each answers WHEN a fact becomes
            # countable, and the walk answers what it COST.  Each returns a
            # ``date`` and cannot yield a figure at all.  **Three names left this
            # entry at plan step C2-d** -- ``owner_pay_periods``,
            #     ``find_period_containing_date`` and
            #     ``resolve_anchor_pay_period``, the owner's calendar and the
            #     date-to-period chain the anchor writers filed against, now
            #     ``pay_calendar.PayCalendar.filing_period``.  Their ruling had
            #     to carry a caveat ("a ``PayPeriod`` is an ORM row, so money is
            #     reachable by relationship; a period is not an account's
            #     balance") that the two survivors do not need.
            "anchor_visible_on",
            "payment_visible_on",
            # The walk's events re-keyed by their visible dates (plan step
            # E1a): what ONE event contributed and when it counts -- dated
            # FACTS a consumer can already read off the public splits and
            # corrections.  Shared by the seam's fold AND the posting writer's
            # checked-projection assert precisely so neither carries its own
            # copy of the one clock; the prefix-sum that turns the list into a
            # balance-at-T stays seam-private (``balance_at._fold``).
            "dated_deltas",
            # A date-bounded loader of settled payments -- their transfers' legs
            # since plan step balance:X-bi-6-4b.  It selects records, and
            # carries no balance of any kind.
            "confirmed_shadows_through",
            # The payment feed's DATE half (plan step balance:X-bl-2a).  It
            # returns dates and nothing else, and that is structural rather than
            # incidental: ``PaymentInstallment`` has no money field to fill.  It
            # selects the loan's payments -- each a leg of its transfer since
            # plan step balance:X-bi-6-4b -- and states each one's three dates:
            # the same ruling ``confirmed_shadows_through`` carries, over the
            # same payments.  It hands back the leg, whose transfer and record
            # are ORM rows, so a figure is reachable by relationship exactly as
            # it is from that loader; what it cannot do is sum one, which is
            # the fence's subject.  (``schedule_dates``, the
            # slot assignment, is NOT here: it lives in the unfenced pure engine
            # ``amortization_engine``, which no scoped package covers.)
            "payment_installments",
        }),
    ),
    # The genesis loan-ledger package.  Scoped WHOLE, not one submodule: a new
    # balance-at-T reader born in ``_display`` would reproduce exactly the hole
    # this check exists to kill.  Its producer set is EMPTY as of plan step E1e,
    # which DELETED the two sum-of-postings balance readers that were the last
    # public producers outside the seam anywhere in ``app/`` (see the header
    # comment); this package is the general ledger -- balance sheet, statements,
    # attribution -- and never the answer to "what do I owe" (plan Section 3).
    "app.services.loan_posting_service": (
        frozenset(), frozenset({
            # Rich row detail: the payment-history table's per-payment cash /
            # principal / interest / escrow split.  Rows, not a balance-at-T.
            # (Its schedule-row sibling ``confirmed_loan_history_rows`` was
            # deleted at plan step E1d-b; the yearly tax / paid-YTD figures folded
            # off the postings onto the loan ledger at steps C3c / C6c, so this
            # package no longer exposes either.)
            "confirmed_loan_payment_history",
            # The anchor EVENT rows (the source documents behind a balance), not
            # the balance itself.
            "loan_balance_anchor_history",
            # The scenario ids a loan has a payment in: the enumeration the
            # all-scenario re-sync and the tracking-start refusal (rulings
            # R-R114, R-R115) walk.  Ids, never a balance.
            "scenarios_with_loan_payments",
            # WRITERS.  Everything below emits or reconciles postings; a writer
            # is not a balance reader, and the ledger-write path has its own
            # seams (``posting_service._emit_balanced_entry``).  The two
            # single-half syncs, the per-half reconciles and the delete-side
            # split reversal went at plan step ``balance:X-bi-6-3`` (ruling
            # **R-BAL102**): ONE reconcile over the walk, driven by the one
            # sync, and a split keyed by no row to reverse ahead of a delete.
            "reconcile_loan_corrections",
            # The two TARGET builders that reconcile consumes: each maps the
            # walk's corrections or splits onto ``{(kind, period, date):
            # {ledger: (amount, kind)}}`` -- what the ledger SHOULD hold, leg
            # by leg, never what it does hold or what a loan owes.
            "anchor_correction_targets",
            "payment_split_targets",
            "sync_loan_postings",
            "sync_loan_postings_all_scenarios",
            "sync_all_scenarios_or_duplicate",
            "backfill_all_loan_postings",
            "resync_user_loan_postings",
        }),
    ),
    # The read pass's context (:data:`_SEAM_PRIVATE_CONTEXT_MODULES`) -- the
    # seam-private ruling D3 keeps, because ``BalanceContext`` is publicly
    # re-exported and W9910 cannot see a method on an object a consumer holds.
    # A new public method here MUST be classified, and the answer is always
    # "non-producer or move it into a private seam module".
    "app.services.balance_at._context": (frozenset(), frozenset({
        # The context CONSTRUCTOR: it resolves the baseline scenario and pins
        # the as-of.  It builds the object a producer is called WITH; it
        # computes no balance.
        "build",
        # The baseline scenario's id -- an int, and the form the loaders and
        # the resolver take.  It RAISES for a pass with no baseline since plan
        # step X-v2 (ruling R-BX), which is what keeps a query from being
        # scoped to ``NULL`` and reading as an empty account; it still answers
        # nothing about a balance.
        "scenario_id",
        # The same id, nullable, for the two seam-internal rules that HAVE an
        # answer for a missing baseline (the loan resolution's payment feed and
        # the confirmed view).  Its docstring names both.  Same classification
        # and the same reason as ``scenario_id``: an id, not a balance.
        "scenario_id_or_none",
        # The read pass's ONE remaining memo handle, un-FENCED at D3 and ruled
        # a NON-producer: ``loan_walk`` hands the leaf's public FACTS
        # (``walk_loan_ledger``'s own ruling), and the fold that would turn
        # them into money is seam-private (plan step D-fold).  The RESOLUTION,
        # PLAN, and PAYOFF are pass-through data caches, not methods: their
        # derivations live in the seam modules above this one (plan steps
        # D-ctx-b / E1d-a), which is where a public balance producer would have
        # to be born to be reachable, and W9910 owns that.
        "loan_walk",
        # The read pass's PAY CALENDAR memo, and the reporting window read off
        # it (plan step C2-c).  Both NON-producers, on the same ground
        # ``loan_walk`` stands on: a calendar is the owner's paydays with the
        # two derived columns computed from them -- DATES, with no money
        # anywhere in the value -- and ``pay_calendar`` is a PUBLIC leaf below
        # this seam that any consumer may call directly for the identical
        # answer.  These hand back nothing a caller could not obtain from
        # ``pay_calendar.calendar_for`` itself; what they add is that the seam
        # and its caller cannot end up on two different calendars in one
        # render.
        "calendar",
        "reported_periods",
        # The read pass's AMOUNT-MODEL memo (plan step X-au-c2b).  A
        # NON-producer on the ground ``calendar`` stands on: it hands back an
        # ``AmountBasis``, which carries the two live DERIVATIONS a row's
        # amount is priced from and no balance-at-T of any kind -- and
        # ``cash_ledger.amount_basis`` is a public leaf BELOW this seam that any
        # consumer may call directly for the identical value.  What this adds is
        # that the seam and its caller cannot end up pricing one render's rows
        # two ways.
        "amounts",
        # Its NULLABLE sibling (plan step X-au-g-2c), classified on identical
        # ground and named separately because that is what this fence is for:
        # a method added beside a classified one inherits nothing.  It answers
        # the same ``AmountBasis`` or ``None`` -- ruling **R-BX**'s spelling for
        # the no-baseline pass, matching ``scenario_id_or_none`` beside it -- so
        # it carries no balance-at-T either, and ``None`` is the ABSENCE of a
        # derivation rather than a figure.
        "amounts_or_none",
        # The read pass's PAYCHECK PRICER (plan step salary:S3-d).  A
        # NON-producer on exactly the ground ``amounts`` stands on: it hands
        # back an ``income_service.PaycheckPricing``, which prices one
        # profile's PAYCHECKS per payday and carries no balance-at-T of any
        # kind -- a paycheck's ``net_pay`` is what a job pays on a day, not
        # what an account holds at one -- and
        # ``income_service.paycheck_pricing`` is a public leaf BELOW this seam
        # that any consumer may call directly for the identical value.  What
        # this adds is that the seam and its caller cannot end up pricing one
        # render's paychecks twice.
        "paychecks",
    })),
    # The context's RECURRENCE memos (:data:`_SEAM_PRIVATE_CONTEXT_MODULES`),
    # split out of ``_context`` as a mixin ``BalanceContext`` inherits (ruling
    # R-BAL146).  A public method here is a public method OF THE CONTEXT, so it
    # is classified by the entry above's rule; these three rulings moved here
    # verbatim with their methods.
    "app.services.balance_at._recurrence_memos": (frozenset(), frozenset({
        # The read pass's RECURRENCE memo (plan step R16-b-2, ruling R-R67's
        # one-walk consequence): what one rule MEANS against the owner's
        # calendar, resolved once per pass.  A NON-producer on the ground
        # ``calendar`` stands on -- a cadence, a first occurrence and an
        # authored bound, DATES with no money anywhere in the value -- and
        # ``recurrence.resolved_recurrence`` is a public leaf below this seam
        # that answers the identical value -- less the books floor, since
        # plan step pay_calendar:C18-a, which is one more DATE.
        "resolved_recurrence_of",
        # Its spec-and-definition form (plan step pay_calendar:C18-a, rulings
        # R-PC85, R-PC89): the same resolution with the definition's BOOKS
        # FLOOR -- the latest ``opened_on`` among the accounts it moves money
        # in -- and its envelope flag attached, which ``resolved_recurrence_of``
        # calls and the form preview's unsaved definition calls directly.  A
        # NON-producer on the same ground: a cadence, a first occurrence, a
        # bound, one more DATE and a flag, no money anywhere in the value.
        "resolved_for",
        # The read pass's OCCURRENCE-WALK memo (plan step recurrence:R7d-f-2,
        # ledger row N-513's remedy): every occurrence a resolved recurrence
        # names on the owner's calendar, walked once per pass and split by its
        # books (plan step pay_calendar:C18-a, ruling R-PC94).  A NON-producer
        # on the same ground -- occurrence DATES paired with pay periods, no
        # money anywhere in the value -- and ``recurrence.occurrence_walk`` is
        # a public leaf below this seam that answers the identical value for
        # the same inputs.
        "placements_of",
    })),
    # The loan-payment LOADER module (:data:`_LOAN_PAYMENT_SEAM_MODULES`).  It
    # was "the one reader-allowlisted module outside the defining package" until
    # plan step E1d-b took it off that allowlist with ``confirmed_loan_view``; the
    # scope stays on the surviving ground (see the constant's comment): a PUBLIC
    # module holding every ingredient of a loan balance -- the payment feed, the
    # escrow-netted amounts, the contractual P&I, the whole loaded
    # ``LoanContext`` -- outside W9910's protection, so a public balance wrapper
    # born here would still be the N-28 shape.
    "app.services.loan_payment_service": (frozenset(), frozenset({
        # The unified loan-context loader: params, payments, rate changes,
        # escrow lines -- inputs a resolution is built FROM, no balance.
        "load_loan_context",
        # Payment ROWS (the transfer-shadow feed), chronological -- records,
        # not a balance.
        "get_payment_history",
        # The contractual P&I sizing rule -- a payment amount, not a balance.
        "compute_contractual_pi",
        # PaymentRecord adaptation for the amortization engine -- shaping, no
        # figure of any kind.
        "prepare_payments_for_engine",
        # ``live_cash`` / ``derive_cash`` / ``config_by_transfer`` /
        # ``loan_pricing`` were ruled HERE until plan step X-au-g-2a moved
        # amount rule 4's producer down into ``cash_ledger`` -- and X-au-g-2c-2
        # then DELETED two of the four, so only ``derive_cash`` and
        # ``loan_pricing`` still have entries anywhere.  Their rulings
        # moved with them to the ``app.services.cash_ledger`` entry above --
        # the names did not change STANDING, only address, and the one wording
        # change is named there rather than passed off as a copy.  Said out
        # loud because a name silently leaving a fenced module's ruled set is
        # the failure mode this registry exists to prevent (findings N-28 /
        # N-31), and a reader who remembers them here needs to be told where
        # they went rather than concluding they were dropped.
    })),
    # The recurring DEFINITION reader (:data:`_RECURRING_DEFINITION_MODULES`).
    # An EMPTY producer set, and it is a strong claim rather than a weak one:
    # nothing here loads a loan, a schedule or a payment history, so no name
    # defined in this module can answer what an account is WORTH.  What it
    # answers is what a repeating definition SAYS.
    "app.services.recurring_transfer_query": (frozenset(), frozenset({
        # The query itself: which templates pay into this account, and the
        # oldest of them.  Rows, not figures.
        "active_recurring_transfer_templates",
        "active_recurring_transfer_template",
        # The other direction: which ACCOUNT a definition pays into.  A row off
        # the template's own FK column, moved here from ``loan_recurrence_sync``
        # at plan step R16-b-2 (ruling R-R70) so the balance seam's identity
        # reader can reach it; it loads no loan and answers no figure.
        "destination_account",
        # Two BOOLEAN-and-a-Decimal settings off a transfer template: does this
        # payment's cash derive from the loan, and what standing extra rides on
        # it.  Public since plan step X-au-b, whose amount resolver has to know
        # the MODE before it can price a payment; a configuration read, and the
        # furthest thing here from a balance-at-T.  It moved from
        # ``loan_payment_service`` at plan step R7d-a and keeps its ruling.
        "loan_payment_config",
        # WHICH of a loan's definitions tracks it -- the oldest whose settings
        # row says derive (plan step R7d-g-3): the track door and the payment
        # card's one-tracker rule.  A row off the list the caller holds; it
        # loads no loan and answers no figure.
        "tracking_definition",
        # ``loan_standing_extra``, ``loan_standing_extra_for_account`` and
        # ``standing_payment`` were ruled here until plan step R7d-g-3 deleted
        # them (ruling R-R83 as re-ruled there; plan ledger row D49): each
        # answered a LOAN-level question off ONE picked definition, and the
        # payoff composer that threaded the extra takes none now.  Dropped
        # rather than left, for the reason ``cash_ledger``'s entry states:
        # a ruling for a name the module no longer defines would un-fence
        # whatever the name was reused for.  ``standing_installment_cash``
        # went the same way at R16-b-2 (ruling R-R67).
    })),
    # The PURE loan-resolver tier (:data:`_LOAN_RESOLVER_ENGINE_MODULES`,
    # closing finding B-12).  Package-scoped, so a new submodule is covered the
    # day it is written; every public name is a ruled non-producer, and a new
    # balance-at-T born here errors at its definition.
    "app.services.loan_resolver": (frozenset(), frozenset({
        # The state producer: payment, rate, committed schedule, life-of-loan
        # interest.  Its balance field was DELETED at plan step D2a; the
        # schedule rows it carries are the sanctioned display class.
        "resolve_loan",
        # The scenario composer: replayed history rows + projected forward
        # slices (schedule ROWS and aggregate interest, no balance-at-T).
        "compute_payoff_scenarios",
        # Rate-period machinery: the level P&I and the annual rate of the
        # period containing a date -- contract terms, not balances.
        "compute_monthly_payment_baseline",
        "current_rate_baseline",
        "resolve_periods",
        "engine_terms",
        # The latest anchor FACT (a user-asserted row plus its date) -- the
        # same ruling ``resolve_anchor`` carries on the cash side.
        "select_latest_anchor",
    })),
}
