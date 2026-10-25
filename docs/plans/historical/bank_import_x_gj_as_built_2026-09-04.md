> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The `bank_import:X-gj` span, as built

The `bank_import:X-gj` span as built: the Reconcile rebuild the developer locked at Loop A round 4
on 2026-08-29, from the view model to the fourth verb. Archived 2026-09-04 under
`docs/plans/conventions.md` rule 5, when `X-gj-4b` shipped and made the whole family complete.
Cite it for how a decision came to be, never for what is true now; the code as committed is the
source of truth for what the app does.

The locked direction and its four rounds are `docs/design/bank_import_audit.md`. Every ruling this
span was built on is `docs/plans/rulings.md`, keyed `(bank_import, id)`. What the span did NOT
close is a live `ledger.md` row, and what it binds on a step still open was moved to that step's
own entry rather than left here -- rule 5's "no live sentence may depend on an archived one".

| step | commit | what it did |
|---|---|---|
| X-gj | `f119ec0a` | container; the whole rebuild, ticked with `X-gj-4b` |
| X-gj-1 | `a43e8e2f` | container; the page, split three ways on the services boundary. Detail: `bank_import_x_gj_1_as_built_2026-09-02.md` |
| X-gj-1a | `bc851df9` | the pass becomes CARDS (**R-HP**, **R-HQ**, **R-HW**) |
| X-gj-1b | `cfcfcac9` | the page and the three bank-line tabs (**R-HR**, **R-IA**, **R-IB**); minted `X-gk` |
| X-gj-1c | `a43e8e2f` | the two settled tabs; the register RETIRED (**R-HU**). Shut **N-389**, **N-403**; opened **N-404**, **N-405** |
| X-gj-2 | `a23315dc` | container; **R-HT(a)**'s inflow answer. The deposit half REPORTS a merchant credit, the refund half FILES one |
| X-gj-2a | `751eba5d` | a standing rule answers a DEPOSIT: the fifth `RuleAnswer`, auto-applied at import (**R-HT(a)**, **R-HX**) |
| X-gj-2b | `a23315dc` | container; the refund filing. Ruled **R-IK**, **R-IL**, **R-IM**; opened **N-420**, **N-435** |
| X-gj-2b-1 | `9920bed7` | the entry positivity check becomes `amount <> 0` (**R-II**) |
| X-gj-2b-2 | `1bfeff07` | a rule FILES a refund; a PARTITION correction, not a new arm |
| X-gj-2b-3 | `a23315dc` | the reader census by NAME then by SHAPE; a purchase's sign is PICKED, not derived |
| X-gj-3 | `e42dcd6b` | container; **R-HT(b)**'s group answer, ticked with its FIRST leaf because its second was WITHDRAWN (**R-JJ**). The withdrawal record is `bank_import_x_gj_3b_withdrawn_2026-09-02.md` |
| X-gj-3a | `e42dcd6b` | a group's difference may land on a member the OWNER names (**R-IU**). `bank_cash_for` becomes `DifferenceLanding`; no member pre-selected (**R-HX**) |
| X-gj-4 | `f119ec0a` | container; the SKIP verb, split at its gate. **R-JG** took the act ROW over the nullable column and the append-only log |
| X-gj-4a | `758bbe55` | the STORE and its two doors (**R-JG**): `budget.statement_line_skips`, one row per line, deleted to undo. Carried `balance:R-IR`'s split, `_reads.py` 989 -> 838. Opened **N-470**, **N-471** |
| X-gj-4b | `f119ec0a` | the VERB lit (**R-HW**, **R-JI**): `offers_for` stops shutting SKIP, a `skips` list on the batch schema, `reconcile_payload` reads `verb=skip`, `apply_reviewed` grows a fourth arm, the SKIP pane renders. Shut for a line a source files as paying an account the owner holds, on the pass's own merchant set so a PROPOSED line is shut too |
| X-gj-4c | `56f97b98` | container; the SKIPPED TAB (**R-JH**). Its ORDER argument is spent and lives on in `_reconcile.py` |
| X-gj-4c-1 | `456d6bd2` | a *never a purchase* answer is not a disposition (**R-JH**): those lines leave `parked` for `ReviewSet.answered_never` and render in the INBOX; `ParkedLine` is `BarredLine` |
| X-gj-4c-2 | `56f97b98` | the TAB: the reader, the card, the Undo through `unskip_line`, and `Tab.holds_settled_acts` deleted for a `CardKind` the building arm states (**R-JW**, **R-JX**) |

**Three sentences left this span for a live step rather than being archived with it**, because each
binds work that has not shipped: `X-gi` must take the review QUEUE with the register; `_resolve
.load_lines`'s keyword-only `for_write` contract with no default; and `parked` being
account-payments ONLY, so its chip's magnitude is theirs alone. The first is on `X-gi`'s entry
in `implementation_plan_bank_import.md`; the other two are on `X-gl-1` and `X-gl-5`, because
**R-JY**'s container was minted in the same commit that archived this span.

*One sentence is retired rather than moved: `X-gj-4c-2`'s note that `_verbs.SKIP_WAITS` "is now
false in both clauses". `X-gj-4b` DELETED that constant; what replaced it is
`SKIP_SHUT_PAYS_AN_ACCOUNT`, which is about one class of line rather than about the verb.*
