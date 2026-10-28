"""W9909's ruling for the cash-ledger leaf: every public name, classified.

The DATA of one registry entry, split out of :mod:`._fence_rulings` at plan
step ``balance:X-bi-6a``, for the reason that module was itself split out of
:mod:`.balance_seam` at ``balance:X-f3c-2b-2a``: a FAIL-CLOSED registry that
cannot grow refuses the next honest edit rather than the next unsafe one, and
the registry reached 1,007 lines against pylint's 1,000 ceiling the moment
this leaf gained ``planned_leg_contribution``.  The cash-ledger entry is the
largest by far -- every ingredient of a cash balance-at-T lives in that leaf,
so it carries the most rulings and the longest whys -- and it is the one that
grows with every balance step, so it is the one that leaves.

Nothing about the ruling moved: the names, their rationale and the EMPTY
producer set are verbatim, and :data:`._fence_rulings._FENCED_MODULE_RULINGS`
still keys the entry under ``"app.services.cash_ledger"`` so the checker's
prefix match (:func:`.balance_seam._fenced_module_ruling`) and every test over
the table are unchanged.  The scope set (:data:`._fence_rulings._CASH_LEDGER_MODULES`)
stays with the other scope sets, because the tests import them from one place.

The cash LEDGER leaf (plan steps D1a + D1c): the facts a cash balance is
folded from, what one row is WORTH, and what a set of rows SUMS TO.  Scoped
WHOLE for completeness but never call-allowlisted -- see
:data:`._fence_rulings._CASH_LEDGER_MODULES` for why, and why the scope is a
package rather than a list of modules.  The EMPTY producer set is the D3
invariant stated as data: a public balance producer outside the seam is not a
thing, so a new public name here is either ruled a NON-producer (with its why)
or it belongs inside ``balance_at`` -- there is no third classification.
"""

from __future__ import annotations

#: The cash-ledger leaf's NON-producer names, each with the ruling that admits
#: it.  Read by :data:`._fence_rulings._FENCED_MODULE_RULINGS`.
CASH_LEDGER_NON_PRODUCERS = frozenset({
    # ``_facts`` -- the stored anchor SoT row (a user-asserted FACT plus its
    # date), not a computed projection.  Consumers read it for the "as of"
    # caption; their balances come from the seam.
    "resolve_anchor",
    # The same stored FACT, asked as of a civil day instead of as of now
    # (ruling R-EQ, plan step X-f1c4b).  Same row, same tie-breaks, same
    # query -- the horizon is the only difference, so it carries
    # ``resolve_anchor``'s ruling verbatim.  It is emphatically NOT a
    # balance-at-T: it answers which assertion the user had made by day D,
    # never what the account held then, which is the seam's question and
    # folds settled movements this function cannot see.  Its only callers
    # are the two anchor WRITE doors, deciding whether a submission changes
    # anything.
    "governing_anchor_on",
    # The NON-RAISING twin of ``resolve_anchor``, added at plan step
    # X-f3a-1 for the reconcile panel: same row, same tie-breaks, same
    # ``_governing_row`` query, and the only difference is that "this
    # account has never had a balance declared" is an honest empty state to
    # a panel where it is a broken invariant to a reader.  It carries
    # ``resolve_anchor``'s ruling verbatim -- a stored user-asserted FACT,
    # not a computed projection.
    "governing_anchor",
    # The PLAN loader (plan step X-b), a non-producer on the same ground
    # as its settled twin ``settled_cash_facts`` below: it SELECTS rows and
    # returns them unchanged.  Its WINDOWED sibling
    # ``load_balance_transactions`` carried this same ruling until plan step
    # X-g4b deleted it with the anchor-forward producer that was its last
    # caller.  It is the weaker of the two shapes, deliberately -- the
    # settled loader returns facts
    # already VALUED and DATED, while this one cannot date anything, because
    # a projected row's effective date is ``max(attribution, as_of + 1d)``
    # (ruling R-G) and this package reads no clock.  Rows in, rows out; the
    # dating, the valuation and the prefix-sum that make them a balance are
    # all seam-private in ``balance_at._cash_fold``.
    "planned_cash_rows",
    # ``_amounts`` -- what ONE row is worth to checking.  An amount per
    # TRANSACTION is not a balance per ACCOUNT.  The cash analog of
    # ``loan_ledger``'s ``split_*`` rulings below, and carried for the same
    # reason.  The three-bucket reservation formula itself is NOT here:
    # D1c deleted its only external caller, so it went private and needs no
    # ruling -- structure retiring a fence entry, which is Phase D's point.
    #
    # **FOUR MORE ENTRIES WENT THE SAME WAY AT PLAN STEP X-au-d, and this
    # is the fence shrinking rather than being widened.**  The live-override
    # family this paragraph used to describe -- ``live_override``,
    # ``live_amounts`` and the ``income_amount`` rule that read them -- and
    # the ``display_amounts_by_id`` composition over them are all DELETED:
    # a paycheck row no longer stores a figure a live recompute has to
    # supersede, so there is no second answer for a screen to compose and
    # nothing left to classify.  What survives is ``amounts_by_key`` below,
    # which answers what a row's amount IS with no live half at all.
    # *The four names outlived the functions by one commit; CI's checker
    # step caught them, and the LOCAL suite structurally could not --
    # ``pytest.ini`` sets ``testpaths = tests``, so ``tools/pylint/tests``
    # runs nowhere but CI.*
    #
    # The VALUATION family (plan step X-au-c2) joins them on exactly that
    # ground, and it is the same question ``Transaction.effective_amount``
    # answered as a model property: ``contributed_amount`` composes a
    # resolved amount with the status, the soft delete and an entered
    # actual; ``contribution_of`` and ``contributions_by_key`` are the
    # one-parent and batch forms that resolve first.  Each answers what ONE
    # ROW or TRANSFER is worth -- none folds, dates, sums, or reads an
    # anchor, and the batch is a dict keyed by a row's id or a transfer
    # leg's ``(transfer id, account id)`` rather than anything per account.
    # ``settled_contribution`` is ruled with them,
    # under :data:`_ROW_VALUATION_MODULES` -- it is DEFINED one module
    # down and only re-exported here, and this fence keys on where a
    # function is DEFINED.  (It was ``owned_contribution`` until plan step
    # X-bx renamed it; its budget twin ``owned_amount`` was ruled there too
    # until plan step X-bu deleted the accessor.)
    # Their leg twins -- ``planned_leg_contribution`` (plan step X-bi-6a),
    # ``leg_contribution_of`` and ``leg_contributions_by_key`` (leaf
    # X-bi-6-1b) -- were ruled here until leaf ``balance:X-bi-6-5`` 4
    # deleted them (ruling plan#1983): a leg is valued as its parent
    # transfer, through the two names above.
    "contributed_amount",
    "contribution_of",
    "contributions_by_key",
    # ``_amount_source`` / ``_amount_rule`` -- WHERE one row's amount comes
    # from (plan step X-au-b, ruling R-FI).  FIVE names, one ruling, because
    # they are one question at two tiers: ``amount_basis`` resolves the live
    # producers ONCE for a row set, the two ``*amount_rule`` entries say
    # which of the four sources prices a row of each TABLE (the transfer
    # classifier arrived with rule 4 at plan step X-au-f-2, ruling R-BAL10),
    # and the two ``resolve_*_amount`` entries answer what one
    # row's AMOUNT COLUMN holds or would hold.  Non-producers on exactly the
    # ground their ``_amounts`` siblings above stand on, and one tier
    # further from a balance than those: an amount per ROW is not a balance
    # per ACCOUNT, and this tier does not even value the row against
    # checking -- it answers a figure the valuation rules then compose with
    # a status, an entered actual and an envelope's purchases.  Nothing here
    # folds, dates, sums, or reads an anchor.
    "amount_basis",
    # Its BASELINE-pinned form (plan step X-au-j): the same constructor
    # with the Phase-1 scenario pin resolved, stated once for the three
    # surfaces that make it.  Non-producer for the identical reason --
    # it resolves nothing, folds nothing, and reads no anchor.
    "baseline_amount_basis",
    # Its PASS-LESS form (plan step salary:C12, ledger row P63): the same
    # constructor over a pricer that derives the owner's calendar on the
    # first paycheck, for a producer holding no read pass.  Non-producer
    # for the identical reason; its ledger row is BAL-491 (owner
    # balance:X-i6), and each site moved onto its pass deletes one call.
    "derived_amount_basis",
    "amount_rule",
    "transfer_amount_rule",
    "resolve_transaction_amount",
    "resolve_transfer_amount",
    # The ONE branch on a plan item's PARENT table (ruling plan#1983): a
    # row's amount or a transfer's, by the two resolvers above.  A
    # non-producer on their ground: one parent's amount, nothing folded,
    # dated, summed or anchored.
    "resolve_amount",
    # The estimate's price and the classifier it dispatches on (plan step
    # R16-b-2, ruling R-R67): what a row a definition has NOT written yet
    # would resolve to, through the identical arms a written one takes.
    # A PAYMENT amount, non-producer on the same ground as the two
    # resolvers above; the template-level settings-row test beside it
    # reads one relationship and answers a bool.
    "definition_cash",
    "is_loan_payment_definition",
    # The BATCH form of the same answer (plan step X-au-c2b), on the same
    # ground as ``contributions_by_key`` above: a dict keyed by a row's id or
    # a transfer leg's ``(transfer id, account id)``, one entry per item the
    # caller loaded, each ``resolve_amount`` over its parent, and nothing per
    # account.  It differs from that sibling in the question rather than the
    # tier -- what an item's amount IS (ruling E-21's budget base) rather
    # than what it is worth -- so a reader that needs a budget stops reaching
    # for a contribution.  Its leg twin ``leg_amounts_by_key`` (leaf
    # X-bi-6-1) was ruled here until ruling plan#1983 folded the two.
    "amounts_by_key",
    # The ONE statement of "is this movement already inside the balance the
    # user declared" (ruling R-DH (a)), and since the one-partition step it
    # is a METHOD on ``ReconciledThrough`` rather than a free function, so
    # that a raw ``<=`` against the boundary is a TypeError instead of a
    # fifth answer.  The read fold, the posting walk and the entry
    # reservation all reach this one implementation -- they had three, in
    # three different units, and one of them cost production ``$4,001.42``.
    # It answers nothing about how much an account HOLDS; it answers
    # whether one event precedes one assertion.
    "covers",
    # THREE surfaces, one name, because it is one question asked of one
    # assertion, of a whole walk, or of an account: the property on
    # ``CashAnchorFact``, the property on ``CashLedgerWalk``, and the
    # module-level SQL form in ``_facts`` for callers holding no walk (one
    # indexed ``MAX(observed_on)``, no rows materialised, no anchor
    # resolution).  All three return a ``ReconciledThrough`` over a date;
    # none reads a balance.  It says WHEN the user last declared one,
    # never what it was.
    "reconciled_through",
    # (``settled_cash_leg`` -- the SETTLED per-row rule, plan step X-a, moved
    # here from ``posting_service._signed_cash_leg`` -- stood here as a
    # non-producer until plan step ``balance:X-bi-4a`` deleted it under
    # ruling **R-BAL81**: a settled row is worth what its covering movement
    # moves, ``status_seam.covered_cash_leg``, and the walk and the writer
    # read movements alone.  The two terms below outlive it for the reconcile
    # panel.)
    # One TERM of that rule -- ``Sigma(credit entry amounts)`` for one
    # row -- published at plan step X-f2-c3 so the reconcile panel can print
    # what a STATEMENT shows beside what a tick books (finding **N-226**)
    # without writing ``entry.is_credit`` a second time.  A non-producer by
    # the same reasoning one step further along: it is not even an amount
    # per transaction, it is a component of one.
    "credit_entry_sum",
    # The OTHER term ruling **R-FM** adds to that rule (plan step X-f3b) --
    # ``Sigma(posted debit purchases)`` for one row -- published for exactly
    # the reason its sibling above is, and refused for the same one: it is a
    # component of an amount per transaction, not a balance per account.
    "posted_purchase_sum",
    # The THIRD term (plan step ``credit_card:CC-5-2``, ruling **R-CC15**) --
    # ``Sigma(purchases whose account is not the row's)`` -- the account-keyed
    # arm beside the flag's, for the first door that files a purchase on
    # another account than its row's.  A component of one row's amount, as
    # its two siblings are; not a balance per account.
    "elsewhere_purchase_sum",
    # The SUM of the terms above, published at plan step
    # ``bank_import:X-f6a-2`` because three readers then asked for it -- the
    # row leg, the reconcile panel's "what a statement shows" caption, and
    # the statement matcher's corrected figure -- and two additions written
    # out is one place for them to drift; the panel is its one reader since
    # ``balance:X-bi-4a`` (finding **BAL-523**).  A non-producer for its
    # components' own reason: a component of an amount per transaction is
    # not a balance per account.
    "off_statement_sum",
    # The one sign rule for a ROW -- gross, signed by the transaction TYPE,
    # behind the contributing gate -- for a PROJECTED row the statement
    # matcher prices at what settling it would book (``settled_cash_leg``
    # with its first term supplied, through X-bi-4a's first cut).
    # Non-producing for the same reason as the function it generalised: it
    # answers what ONE ROW moves, never what an account HOLDS.
    "cash_leg_of",
    # The MOVEMENT's twin of ``cash_leg_of`` (plan step X-bi-3b,
    # ruling **R-BAL35**): what ONE purchase or covering movement moves
    # through its parent's account, its whole figure in the PARENT's
    # direction, total over the card partition and the contributing gate.
    # Six readers spelled ``-amount`` for themselves before it (the walk's
    # fact producer, the ledger writer's target, the seam's family valuation,
    # the matcher's offer, register and undo).  Non-producing for the family's
    # reason: it answers what one MOVEMENT moves, never what an account HOLDS.
    "movement_cash_leg",
    # Its inverse (the sign rule is an involution): the figure a movement
    # must STORE to move a bank line's cash, for the matcher's two writing
    # doors.  A component of one movement's figure is even further from a
    # balance per account than the leg it inverts.
    "movement_figure_for",
    # ``_flows`` -- what a SET of rows sums to: what MOVED, not what is HELD
    # at a date.  A peer reduction over the same rows a balance folds, not a
    # step toward one.  ``sum_projected`` is the shared engine BOTH cash
    # bases reduce through -- the seam's fold and the retiring anchor-forward
    # walk -- which is what keeps one entries-aware expense rule and one
    # live-override basis across them.  Its per-period ``period_subtotal`` /
    # ``period_subtotals`` siblings carried this same ruling until plan step
    # X-c2b3 deleted them: ruling R-K changed what a subtotal COUNTS, so
    # ``_cash_periods.period_view_of`` is their successor and two rulings
    # went with the names (else the reverse-staleness meta-test flags them).
    "sum_projected",
    # ``_events`` (plan step X-a) -- the cash EVENT STREAM, the exact
    # counterpart of the ``loan_ledger`` non-producer rulings below and
    # non-producers for the same reason: each answers "what happened, and
    # when", never "what is held at time T".  ``settled_civil_day`` is the
    # ONE statement of which civil day a settled source's cash moved on;
    # ``cash_anchor_facts`` and ``settled_cash_facts`` are LOADERS of stored
    # assertions and per-row signed effects.  The stream ORDERING that
    # ``merge_anchor_and_cash_events`` used to return as a third list is
    # gone: both walks now advance their own sources against
    # ``ReconciledThrough.covers``, so the order is applied where the
    # replay happens rather than published as a fact of its own.
    # ``observed_on`` and ``delta`` LEFT this set at plan step X-f3c-1 with
    # the class that carried them: ``CashAnchorCorrection`` is
    # ``balance_at._assertions``' now, because its ``balance_before`` is a
    # prefix sum and this package's producer set is empty and stays empty.
    # A ruling for a name the package no longer defines classifies nothing
    # and would un-fence whatever took the name, which is what
    # ``test_classification_sets_match_the_real_fenced_modules`` is for --
    # it caught exactly this pair.
    #
    # ``settled_civil_day`` REPLACED ``attribution_instant`` at ruling R-DH
    # (2026-07-31) and ``visible_on`` left with it: both facts carry their
    # civil day as a FIELD resolved once at construction, so no public
    # method is left for this set to rule on.  A day is not a balance.
    "cash_anchor_facts",
    "settled_cash_facts",
    # ``in_flight_movements`` (plan step ``balance:X-bi-4a``, ruling
    # **R-BAL77**) -- the UN-DATED half of the one movement stream
    # ``settled_cash_facts`` loads the dated half of: a LOADER of stored
    # purchases the bank has not been seen to take, each valued by the same
    # per-movement leg.  Where one lands and what the plan holds at time T
    # are ``balance_at._cash_fold``'s, exactly as for ``planned_cash_rows``.
    "in_flight_movements",
    # (``movements_with_parents``, ruled here from plan step
    # ``balance:X-bi-6-3`` because the posting writer's transfer-family
    # loader shared the fold's join, was folded into the fold's
    # ``_movements_of``, its last reader, at leaf ``X-bi-6-4a``: that loader
    # moved onto ``transfer_legs``' join, where a transfer movement's parent
    # is its LEG.)
    # ``account_opening_fact`` (X-f3c-2a, R-GX) -- a LOADER of the stored
    # ``account_openings`` row: returning a recorded balance is not
    # computing one.  The FOLD seeds from it.
    "account_opening_fact",
    # Its NON-RAISING twin (X-f3c-2b-2a), carrying its ruling verbatim for
    # the reason ``governing_anchor`` carries ``resolve_anchor``'s: same
    # row, same query, and only the empty-state policy differs.
    "governing_account_opening",
    # ``_books`` (X-f3c-2b, N-378; X-f3c-2b-2a; X-f3c-2b-2b, N-383) -- FIVE
    # REFUSALS stating the books boundary, each returning nothing, plus the
    # three DAYS they bound against.
    # (It read "FOUR ... two DAYS" until the counts were taken against the
    # module: it states five ``reject_*`` and three ``earliest_*``, and its
    # own docstring says so.  A count in a ruling is a claim like any
    # other.)  A
    # refusal compares ONE date against a stored row, and "may this day be
    # recorded" is the opposite direction from "what is held at T"; the
    # readers are a bare ``MIN`` over a date column, public because the
    # restatement form renders both, and a day is not a balance (the
    # ``latest_statement_day`` hatch, ruled below).
    #
    # The one COMPARISON they share, ``books_hold``, was ruled here until
    # plan step pay_calendar:C18-a moved its definition to the pure leaf
    # ``app/utils/books_boundary.py`` (the recurrence walk asks it too and
    # may not import this package).  ``_books`` imports it back, so it is no
    # longer a name this module DEFINES and a ruling here would be stale; a
    # two-date ``bool`` that reads nothing answers no balance wherever it
    # lives, and the leaf is outside every fenced module.
    "reject_books_open_after_an_assertion",
    # ``reject_books_open_on_or_after_matched_lines`` and its reader
    # (X-f3c-2b-2b) carry the same ruling as the movement pair beside
    # them, over the second row set: a ``MIN`` over ``posted_on`` on the
    # bank lines an account's matches name, and a refusal comparing one
    # date against it.  A bank line's posting day is the BANK's record of
    # when money moved, which is the same kind of fact as a settle day and
    # is no more a balance than one.
    "reject_books_open_on_or_after_matched_lines",
    "earliest_matched_line_day",
    "reject_books_open_on_or_after_movements",
    # ``reject_line_before_books_open`` (X-f3c-2b-2b) takes a LOADED
    # opening rather than an account id, which is the one shape difference
    # in this group and does not change the ruling: it is handed the fact
    # by the pass that already read it, compares one date against it and
    # returns nothing.
    "reject_line_before_books_open",
    "reject_movement_before_books_open",
    "earliest_assertion_day",
    "earliest_recorded_movement_day",
    # ``_walk`` (plan step X-a) -- the account's FACT stream and the
    # visible-day re-key of its source events.  Ruled NON-producers on
    # exactly the grounds ``loan_ledger``'s twins below are, and since plan
    # step X-f3c-1 the ruling rests on less: the walk holds no running
    # balance at all, returning settled sources ascending by settle day and
    # assertions ascending by BUSINESS date, while ``dated_deltas`` returns
    # what each source contributed and when.  Applying an assertion to a
    # running total, and the PREFIX-SUM that turns either into "what is held
    # at time T", are both seam-private.  If either moved into this package
    # these two would become producers -- which is the same thing as saying
    # they belong inside ``balance_at``, since this package's producer set
    # is empty and stays empty.
    "dated_deltas",
    "walk_cash_ledger",
    # ``_loan_pricing`` / ``_loan_installment`` (plan step X-au-g-2a) --
    # amount rule 4's producer, moved DOWN into this package from
    # ``loan_payment_service`` because the amount model is the lower tier
    # and should not reach up into a loan service to price a row (the
    # unwind :mod:`app.services.row_valuation` says ``X-au-g`` owes).
    #
    # **FOUR names were ruled here at X-au-g-2a and TWO remain**: plan step
    # X-au-g-2c-2 deleted ``live_cash`` and ``config_by_transfer`` with the
    # read-time repair they existed for, and their entries are removed here
    # in the same edit rather than left standing.  A ruling for a function
    # its module no longer defines is not inert -- it would silently
    # un-fence whatever name later takes it, which is findings N-28 / N-31's
    # shape, and it is what
    # ``test_classification_sets_match_the_real_fenced_modules`` caught.
    #
    # The derive-mode arm of a projected payment's LIVE cash (P&I + that
    # installment's escrow + standing extra) -- what a PAYMENT is worth, not
    # what an account owes.  Its wording is TIGHTENED from the
    # ``loan_payment_service`` ruling rather than copied, and saying
    # "verbatim" would be a claim licensing a reader not to diff: "current
    # escrow" became "that installment's escrow", because the derivation
    # resolves it on the shadow's own due date (ruling D5), never on a
    # current one.  ``live_cash`` was the non-derive arm beside it and is
    # gone; a shadow DECLARED ``parent_transfer`` from then and the amount
    # model answered what the override used to, until plan step
    # ``balance:X-bi-6-4d-2`` deleted the shadows.
    "derive_cash",
    # The named constructor for the derivation.  Ingredients; it resolves
    # nothing when it is called.  Word for word from the
    # ``loan_payment_service`` entry.  ``config_by_transfer`` -- the
    # scenario-wide loan-payment CONFIG map that sat beside it -- went with
    # ``live_cash``, and it was the cash-ledger package's ONLY
    # ``budget.transfers`` query.
    "loan_pricing",
    # ``_clearing`` (plan step X-f3a-1, ruling **R-FL**) -- WHICH STATEMENT
    # showed a line, which is the recorded fact that replaced ``covers``'
    # date comparison on the cash side.  Five names, one ruling, because
    # they are one question at three surfaces:
    # ``clearing_anchor_id`` names the assertion that cleared ONE line,
    # ``is_cleared`` is that answer reduced to a bool for the entry
    # reservation, ``statement_coverage`` builds the rule from an account's
    # assertion facts, ``coverage_for`` is its database twin for a caller
    # holding no walk, and ``coverage`` is the property on ``CashLedgerWalk``
    # for a caller holding one.
    #
    # Non-producers on exactly the ground ``covers`` and
    # ``reconciled_through`` stand on, and the classification did not change
    # when the fact did: each answers whether ONE event is inside ONE
    # assertion, or which assertion it is inside.  None of them reads a
    # figure -- an ``account_anchor_history`` id and a bool are not money --
    # and none folds, dates, sums or samples anything.
    #
    # ``latest_statement_day`` is the deliberate escape hatch, ruled here
    # rather than left to a call site: it returns the raw civil DAY the
    # entry list captions and the reconcile panel bounds its offer set with,
    # and it is the exact twin of ``ReconciledThrough.observed_day``, which
    # the same argument admits.  A day is not a balance.
    "clearing_anchor_id",
    "coverage",
    "coverage_for",
    "is_cleared",
    "latest_statement_day",
    "statement_coverage",
})
