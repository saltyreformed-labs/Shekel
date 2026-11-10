"""
Shekel Budget App -- Statement Match: which of your rows a bank line IS.

**The app's own records of when money moved are guesses, and X-f6a-1 recorded
the answer without yet applying it.**  This package applies it.  Measured on
the developer's own 2026-08-16 SECU export against a production clone: of 58
bank lines an exact-amount predicate pairs uniquely with an app row, only 23
carry the day the app had recorded -- so 35 of 58 are corrections waiting to
happen, by as much as 8 days (finding **N-173**).

Plan step ``bank_import:X-f6a-2``, rulings **R-FS**, **R-FP** and **R-FV**.

The public surface, and what each piece is for:

* :class:`ReviewScope` -- ONE derivation of what a pass over one account may
  act on, built once by the route and threaded through everything below it.
  Plan step ``bank_import:X-f6a-3c-2`` exists because it was not: every act
  derived the account again for itself, at 3.593 s a time over the 215 acts the
  developer's own statement offers -- 12.88 minutes of derivation to work one
  statement, against 5.80 s for the whole pass now.
* :func:`review_set` -- everything the Reconcile page's INBOX shows for one
  account: what the app proposes, what it could not explain, and what is out
  of reach.  **It is the pass and nothing else** since plan step
  ``bank_import:X-gf-2`` (ruling **bank_import:R-GX**) took the settled acts
  off it.  *It said "the review screen" and "the EXCEPTION QUEUE" until plan
  step ``bank_import:X-gi-3``: ``bank_import:X-gi-2`` deleted that screen and
  this step deleted its MODEL.  It has FOUR readers, counted rather than
  assumed (adversarial review 2026-09-06 measured a first draft of this
  sentence claiming one): :func:`reconcile_page`, :func:`file_new_swipes` --
  the import door, published in this same list -- and the Reconcile route's
  Apply and MATCH doors.*
* :func:`accepted_register` -- what has already been DECIDED: the acts
  accepted, with the undo, and every act that no longer holds first.  **It
  needs no** :class:`ReviewScope`, which is the point of the split as much as
  the page weight was: it was folded into the review screen's own derivation,
  so rendering it valued all 221 of the developer's accepted acts and re-asked
  29 answers he was not looking at -- 442,109 bytes of a 578,523-byte page.
  *The ANSWERS half was ``_section.answered_merchants``, assembled beside this
  one into ``_register.register_set`` for the register screen.  Plan step
  ``bank_import:X-gi-3`` deleted the assembly AND that half with the screen's
  model: :func:`merchant_directory` is the durable home for an answer
  (**R-IC**), and the Reconcile page reads the acts on the tab that needs
  them.*
* :func:`merchant_directory` -- every merchant this account has ever seen and
  what the owner said about each, which is the DURABLE home for that answer
  (plan step ``bank_import:X-gk``, ruling **bank_import:R-IC**).  The three
  surfaces above it are each PARTIAL -- the INBOX asks only where a line is
  waiting AND nobody has answered, the SETTLED TABS show only the answered, and
  the receipt offers only what a pass just filed -- and measured on a clone of
  the developer's own database 2026-08-31 that left **32 of his 62 merchants on
  no surface at all**.  It needs no :class:`ReviewScope`, for the reason
  :func:`accepted_register` needs none.
* :func:`apply_reviewed` -- the batch door, and the one the screen posts to.
  **It MOVES MONEY.**  It applies every act the owner ticked, each in its own
  SAVEPOINT so a refused item leaves nothing behind and the rest still land,
  and reports what each one did (:class:`BatchOutcome`).  It is not "accept
  everything" (**R-FP**): nothing is applied that was not ticked.
* :func:`accept_match` -- one correspondence between things that already exist.
  Every member row takes the bank's posted day, which settles a still-Projected
  row and corrects a wrongly-dated settled one.
* :func:`create_purchase_from_line` -- ruling **R-FS**'s THIRD shape (plan step
  ``bank_import:X-f6a-3b``): a bank line the app has no row for BECOMES a
  purchase against a budget line the owner picks, or against one this door
  creates for it.  **It MOVES MONEY** in the other direction from the first --
  it records a movement the app did not have at all, where a match re-dates one
  it did.  It records the correspondence through the same function
  :func:`accept_match` does, so there is still exactly one place a match is
  written.
* :func:`record_income_from_line` -- ruling **bank_import:R-GW** (plan step
  ``bank_import:X-gf-1``): a bank line of money COMING IN that no app row
  explains BECOMES an uncategorized income row, matched to itself.  **It MOVES
  MONEY**, and it is the mirror of the door above on the direction that had
  none: a purchase had to be an expense, so that one refused EVERY inflow, and
  a match refuses an empty side -- between them they left eight of the
  developer's own deposits, `$58.87`, with no act on the review screen at all.
  (The purchase door refuses only the inflows no container answer claims since
  ruling **bank_import:R-II**, which took part of this door's own case with it:
  measured 2026-09-01, all 5 of the developer's card refunds under `$39.54`
  carry a container answer and are the purchase door's, leaving this one the 8
  dividends no rule claims.  :mod:`._income` carries the figures.)  The row it
  writes is the one a matched group's residual already used
  (``_uncategorized.mint_uncategorized``), so there is one writer of *the row
  bank evidence requires and the books do not hold*.
* :class:`MerchantAnswers` -- what the owner has SAID about this account's
  merchants, read once: the stated rules and the bars together, because one is
  built from the other and every write door wants both (plan step
  ``bank_import:X-gj-2b-2``).
* :class:`CreationBars` -- which of an account's merchants may NOT become
  purchases at all, and why (ruling **R-GJ**, plan step ``bank_import:X-ga``).
  Two bars: the owner answered *never a purchase*, or a SOURCE files the
  merchant as a payment to a credit card and they have not answered for it at
  all.  A barred line is not offered a create control and is refused at the
  door; it is listed as a :class:`BarredLine` beside the hand-build form, where
  the group match this ruling leaves open is made.  Until this step *never a
  purchase* only withheld a sweep value, and one YTD pass recorded
  **`$7,412.94`** of card payments the app already held as ``CC Payback`` rows
  through the select printed beneath the warning.
* :func:`file_new_swipes` -- the door an IMPORT opens, and the only one in the
  app that MOVES MONEY without a press (ruling **R-GH**, plan step
  ``bank_import:X-ge``).  Consent splits by ACT CLASS: a standing merchant rule
  is the owner saying once where that merchant's money goes, so a NEW swipe
  line the rule answers for becomes a purchase at import; every act that
  MODIFIES a row they made by hand keeps its tick, which is
  :func:`accept_match`'s door.  It files a SUBSET of what :func:`review_set`
  offers a create control for and can never widen it, and
  :func:`rule_filed_acts` is the receipt -- derived from what was stored
  (``applied_by_rule``, **R-GT**) rather than flashed, so every filed line
  still carries :func:`release_match`'s one-click undo after a reload.
* :func:`destinations_for` -- the budget lines that door may write into, which
  is the SAME set the screen offers, and :func:`current_destinations`, which
  narrows it to the rows that still take a purchase as a pass has left them;
  :func:`matched_subjects` is the one statement of what an accepted match has
  already claimed.  **What this exports is what something
  outside the package imports**: ``AppliedItem``, ``RefusedItem``,
  ``MatchedSubjects`` and ``unmatched_rows`` were exported for symmetry and had
  no importer at all, which is a surface nobody asked for.
* :func:`reconcile_page` and :class:`Tab` -- everything the RECONCILE screen
  displays, for one of its five tabs (plan step ``bank_import:X-gj-1a``,
  rulings **R-HP** and **R-HW**): one card per bank line carrying the verb it
  ends on, which of the four verbs have a door, the sentence as spans, the
  hero, the holding chips and the tab counts.  **Only these two are exported**,
  because only the route imports anything: the card values are what the
  template reads off the page it is handed, and a surface nobody imports is
  one nobody asked for.
* :func:`preview_hand_build` -- what the hand-build form's two sides come to
  RIGHT NOW, so ruling **R-FN**'s *a difference is a transaction the user
  accepts* has a figure to accept.  It runs the accept door's own reads and
  refusals without the writes, so the screen and the door cannot state
  different totals (plan step ``bank_import:X-f6d-4``).
* :func:`skip_line` and :func:`unskip_line` -- the FOURTH verb's two doors
  (plan step ``bank_import:X-gj-4a``, ruling **R-JG**): the owner decides a
  bank line is explained by nothing they budget for, and can decide otherwise
  again.  **They MOVE NO MONEY and can move none** -- the only table either
  writes is ``budget.statement_line_skips``, which holds no figure -- so the
  bank's record, the books and the difference between them are all exactly
  what they were; what changes is that :func:`review_set` stops asking.  A
  line carries at most ONE answer, so :func:`skip_line` refuses a line an
  accepted match already explains and ABSORBS a repeat of the same decision.
  **Where a recorded skip is FOUND and undone is the Reconcile page's Skipped
  tab** (plan step ``bank_import:X-gj-4c-2``), which reads that store through
  :func:`reconcile_page` -- so the reader is not exported either, for the
  reason nothing else on that page is: only the route imports, and it imports
  the page.
* :func:`awaiting_review_count` -- how many bank lines the review is still
  asking the owner about, which is the figure the GRID's bank control renders.
  Since plan step ``bank_import:X-gm`` it is ``len`` over
  :func:`~._undisposed.inbox_partition`, the SAME walk the Reconcile page draws
  its inbox cards from -- so a badge and the screen it links to cannot answer
  different questions.  It applied four predicates of its own until then, two
  of them SQL restatements of the pass's Python and one the pass did not have,
  and it read **27** where the page read **18**.
* :func:`release_match` -- the undo.  It restores the QUESTION rather than
  the days, and removes what the act CREATED: the purchase a bank line became,
  a group's recorded difference, and the budget line minted to hold a purchase
  where nothing is left in it (plan step ``bank_import:X-f6f``, ruling
  **R-GG**).  :class:`PlannedRemoval` is what the screen prints beside the
  Undo button, from the door's own derivation, so the control names the money
  it is about to destroy.
* The value types :class:`MatchProposal`, :class:`MatchSubmission`,
  :class:`ReviewedRow`, :class:`ReviewedLine`, :class:`ReviewedDifference`,
  :class:`CandidateRow`,
  :class:`BankLine`,
  :class:`RowKind`,
  :class:`AcceptedMatch`, :class:`AcceptedGroup`, :class:`AcceptedRow`,
  :class:`ReviewedBatch`, :class:`BatchOutcome`, :class:`ReviewSet`, and
  ruling **bank_import:R-GW**'s :class:`IncomeCreation`, :class:`RecordedIncome` and
  :class:`RecordableInflow`.

**Three rules this package is built on, each of them the developer's ruling of
2026-08-17 rather than an implementation choice:**

1. **A match is stored as IDENTITY and nothing else** (**R-FV**).  It does not
   write ``reconciled_by_id``: that column names a balance the owner asserted
   by hand, a bank line is not one, and what it records is derivable from the
   match once a statement carries the line.  The settle doors RELEASE it as
   they move a day, which is right -- the bank has just contradicted the day
   that link was recorded against.
2. **An unbalanced group is REFUSED and the difference is NAMED**, never
   apportioned and never absorbed by a tolerance.  6 of 16 payroll deposits on
   the developer's own statement sit `$0.05`-`$0.06` from the app's rows
   (finding **N-239**), and a tolerance would silence the instrument that
   found it.
3. **A match may SETTLE a still-Projected row**, because a statement is
   evidence that money moved.  11 rows inside the developer's own statement
   span had never been marked as having happened.

**Every proposal is a question** (**R-FP**): nothing here applies a match the
owner has not accepted, and :mod:`._propose` cannot write at all.
"""

from ._accept import AcceptedMatch, accept_match
from ._bars import (
    BarredLine,
    CreationBar,
    CreationBars,
    MerchantAnswers,
)
from ._release import (
    PlannedRemoval,
    PlannedRemovals,
    ReleasedMatch,
    release_match,
    removals_by_match,
)
from ._batch import Consent, ReviewedBatch, apply_reviewed
from ._container import MintedEnvelopes
from ._candidates import (
    candidates_for,
    matched_subjects,
)
from ._create import create_purchase_from_line
from ._creations import (
    NEW_ENVELOPE,
    RECORD_AS_INCOME,
    CreatedPurchase,
    IncomeCreation,
    NewEnvelope,
    PlaceIn,
    PurchaseCreation,
    PurchaseDestination,
    RecordedIncome,
    parse_place_token,
    place_token,
)
from ._destinations import current_destinations, destinations_for
from ._income import record_income_from_line
from ._leftovers import CreatableLine, RecordableInflow
from ._offers import (
    BankLine,
    MatchDays,
    MatchProposal,
    corrected_purchase_day,
    merchant_label,
)
from ._offered_rules import (
    OfferedAnswer,
    RuleDoorAccepts,
    RuleOffer,
    rules_worth_offering,
)
from ._outcome import BatchOutcome
from ._subjects import CandidateRow, Candidates, RowKind
from ._submission import (
    MatchSubmission,
    ReviewedDifference,
    ReviewedLine,
    ReviewedRow,
    as_reviewed,
    as_reviewed_line,
)
from ._near import NEAR_MISS_BOUND
from ._pairing import DAY_WINDOW
from ._propose import ProposedMatches, propose
from ._accepted_view import (
    REGISTER_LIMIT,
    AcceptedGroup,
    AcceptedRegister,
    AcceptedRow,
    accepted_register,
)
from ._placement import Placement, PlacementKind
from ._preview import DifferenceOption, HandTotals, preview_hand_build
from ._rules import (
    CONTAINER_ANSWERS,
    LinePipeline,
    StandingRule,
    RuleAnswer,
    RuleView,
    account_merchants,
    pipeline_for,
)
from ._stating import (
    RuleSubmission,
    StatedRules,
    rule_creating,
    rule_naming,
    state_rules,
)
from ._gaps import BooksBound, ReviewBounds
from ._reads import (
    ReviewSet,
    review_set,
)
from ._verdict import RuleVerdict
from ._section import (
    MerchantSection,
    MerchantSummary,
    WaitingMerchant,
)
from ._directory import (
    DIRECTORY_LIMIT,
    DirectoryAsk,
    MerchantDirectory,
    MerchantWanted,
    merchant_directory,
)
from ._scope import ReviewScope
from ._skipping import (
    SkippedLine,
    SkipRequest,
    skip_line,
    unskip_line,
)
from ._undisposed import awaiting_review_count
from ._panel import AddAct, AddTab, MatchCandidates, VerbPanel
from ._opened import (
    MatchAsk,
    MatchReach,
    OpenedAsk,
    OpenedMatch,
    opened_match,
    proposed_submission,
    refused_match,
)
from ._verbs import Verb, VerbOffer
from ._reconcile import Tab, reconcile_page
from ._filing import (
    RECEIPT_LIMIT,
    RuleFiling,
    WithheldLine,
    file_new_swipes,
    rule_filed_acts,
)

__all__ = [
    "NEW_ENVELOPE",
    "RECEIPT_LIMIT",
    "RECORD_AS_INCOME",
    "DIRECTORY_LIMIT",
    "REGISTER_LIMIT",
    "AcceptedGroup",
    "AcceptedRegister",
    "AcceptedMatch",
    "AcceptedRow",
    "BankLine",
    "BatchOutcome",
    "CandidateRow",
    "Candidates",
    "CreatableLine",
    "RecordableInflow",
    "CreatedPurchase",
    "IncomeCreation",
    "BarredLine",
    "CreationBar",
    "CreationBars",
    "MerchantAnswers",
    "Consent",
    "DifferenceOption",
    "HandTotals",
    "DAY_WINDOW",
    "NEAR_MISS_BOUND",
    "MatchDays",
    "StandingRule",
    "MerchantSection",
    "MintedEnvelopes",
    "MerchantSummary",
    "WaitingMerchant",
    "MatchProposal",
    "MatchSubmission",
    "NewEnvelope",
    "PlaceIn",
    "Placement",
    "PlacementKind",
    "PlannedRemoval",
    "PlannedRemovals",
    "CONTAINER_ANSWERS",
    "LinePipeline",
    "RuleAnswer",
    "pipeline_for",
    "RuleFiling",
    "RuleVerdict",
    "RuleSubmission",
    "RuleView",
    "ProposedMatches",
    "PurchaseCreation",
    "RecordedIncome",
    "PurchaseDestination",
    "ReleasedMatch",
    "BooksBound",
    "ReviewBounds",
    "ReviewScope",
    "ReviewSet",
    "ReviewedBatch",
    "ReviewedDifference",
    "ReviewedLine",
    "ReviewedRow",
    "SkippedLine",
    "DirectoryAsk",
    "MerchantDirectory",
    "MerchantWanted",
    "Tab",
    "AddAct",
    "AddTab",
    "MatchCandidates",
    "MatchAsk",
    "MatchReach",
    "OpenedAsk",
    "OpenedMatch",
    "VerbPanel",
    "Verb",
    "VerbOffer",
    "RowKind",
    "OfferedAnswer",
    "RuleDoorAccepts",
    "RuleOffer",
    "StatedRules",
    "WithheldLine",
    "accept_match",
    "account_merchants",
    "accepted_register",
    "apply_reviewed",
    "as_reviewed",
    "as_reviewed_line",
    "awaiting_review_count",
    "candidates_for",
    "corrected_purchase_day",
    "rules_worth_offering",
    "create_purchase_from_line",
    "record_income_from_line",
    "current_destinations",
    "destinations_for",
    "parse_place_token",
    "place_token",
    "file_new_swipes",
    "matched_subjects",
    "merchant_directory",
    "merchant_label",
    "opened_match",
    "preview_hand_build",
    "proposed_submission",
    "refused_match",
    "propose",
    "reconcile_page",
    "release_match",
    "removals_by_match",
    "review_set",
    "SkipRequest",
    "skip_line",
    "unskip_line",
    "rule_filed_acts",
    "rule_creating",
    "rule_naming",
    "state_rules",
]
