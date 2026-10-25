> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Claude Code prompt: Audit Phase 0 priors review

This is the closing session of Phase 0. It does not add content. Its job is to read the full priors
document end-to-end, tighten prose that drifted across sessions, and verify structural integrity
before Phase 1 begins.

The audit plan target for the priors document is 200-500 lines. The cumulative output from P0-a
through P0-d is typically 300-600 lines. The review brings the file to within target by tightening,
not by deleting content.

This session is structurally different from the previous Phase 0 sessions because it modifies
existing content rather than appending. The biggest risk is the agent "improving" things that must
stay verbatim. The prompt below draws a hard line between content that can be edited and content
that is protected.

## Before you paste the prompt

1. **P0-a through P0-d must all be complete.** Every section of the
   priors document should be filled in (no `_To be filled in by..._`
   placeholders). The only acceptable placeholders are explicit
   `_None found._`, `_No patch files in working tree._`, and similar
   "no items in this category" markers from the section structures.
2. **All adjudications you intend to do should be done first.** If
   you have unaddressed P0-b candidate expectations or P0-c
   contradiction questions in `09_open_questions.md`, decide on them
   before this review session. The review session does not adjudicate.
3. **Take a backup of the priors file** before pasting. The review
   session edits in place; a backup gives you a clean revert if
   anything goes wrong:

   ```bash
   cp docs/audit/financial_calculations/00_priors.md \
      docs/audit/financial_calculations/00_priors.pre-review.md
   ```

   You can delete the backup once the reviewed file looks correct.
4. **Launch Claude Code in plan mode with a named session:**

   ```bash
   claude --permission-mode plan --session-name audit-p0-review
   ```

5. **Confirm `plan` mode is active.** Plan mode permits writing to
   plan files (the priors document is one); it blocks edits to
   source. If the agent reports it cannot edit the priors file,
   the most likely cause is that the file path is being read
   through `--add-dir` or a non-default directory. Restart from
   the project root.
6. **Use a fresh session.** Run `/clear` if you stayed in P0-d's
   session, or start a new one.

## The prompt

Paste everything between the lines, exactly, into the Claude Code session.

---

```text
<role>
You are conducting the priors-review session of the Shekel
financial-calculation audit. The full plan is at
@docs/financial_calculation_audit_plan.md. Read sections 0
("Purpose and ground rules"), the "Phase 0 deliverable" subsection
near the end of Phase 0, and section 10.1a (the priors-review
session description) before doing anything else.

Sessions P0-a through P0-d have populated the priors file at
@docs/audit/financial_calculations/00_priors.md. Your job is to
read the entire file end-to-end, tighten prose that drifted across
the four content sessions, and verify structural integrity. You
do not add content. You do not adjudicate open questions. You do
not start Phase 1.
</role>

<rules>
This session is part of a read-only audit running in Claude Code's
`plan` permission mode. The only file you may modify is the priors
document itself. Read the relevant section fully before drawing
conclusions about its content. Verify factual claims about the
priors file by running `grep`, `wc`, or another mechanical check;
do not recall from memory. Reference files with `@` so they are
actually read. When something needs adjudication or new content,
note it for the developer and stop on that item; do not invent
content. Source files, tests, migrations, and the audit plan
itself remain untouched.
</rules>

<scope>
This session has one job: tighten the priors document so its total
length lands within 200-500 lines, while preserving every piece of
content that later phases will cite or verify against.

In scope:
- Reading the entire priors document end-to-end.
- Editing prose paragraphs that explain context or summarize
  counts in a wordy way.
- Removing duplicate framing sentences that appear in multiple
  sections.
- Compressing multi-sentence cells in tables down to one sentence
  where the extra sentences add no information.
- Fixing any markdown formatting inconsistencies that crept in
  across sessions (table column alignment, heading levels, list
  punctuation).
- Verifying structural integrity: every required section is
  present, every ID series is contiguous, all citations resolve.

Out of scope:
- Adding any new finding, expectation, watchlist entry, prior
  audit, patch, or commit. The priors document is content-frozen
  before this session starts.
- Reclassifying anything in the triage table.
- Adjudicating open questions, P0-b candidates, or P0-c
  contradictions.
- Editing any verbatim block (the Transfer Invariants from
  CLAUDE.md, the four developer-stated expectations from the
  audit plan).
- Modifying any ID, citation, or table column header.
- Editing any file other than `00_priors.md`.
- Reading source code under `app/`, plans, prior audits, patches,
  or git log. Phase 0's reading is done.

If you find yourself opening any file other than the audit plan
and the priors document, you have drifted out of scope. Stop and
report.
</scope>

<task>
Execute the steps below in order.

## Step 1: Read context and snapshot the current state

Read these in full:

- @docs/financial_calculation_audit_plan.md, sections 0 and the
  Phase 0 deliverable; section 10.1a's priors-review entry.
- @docs/audit/financial_calculations/00_priors.md, the entire
  current state.

Capture the following counts for use in step 5's verification.
Run each command and record the result:

  wc -l docs/audit/financial_calculations/00_priors.md
  grep -cE "^E-[0-9]+:" docs/audit/financial_calculations/00_priors.md
  grep -cE "^\| W-" docs/audit/financial_calculations/00_priors.md
  grep -cE "^\| PA-" docs/audit/financial_calculations/00_priors.md
  grep -cE "^\| PT-" docs/audit/financial_calculations/00_priors.md
  grep -cE "^\| CM-" docs/audit/financial_calculations/00_priors.md
  grep -cE "^C-[0-9]+:" docs/audit/financial_calculations/00_priors.md
  grep -c "^| " docs/audit/financial_calculations/00_priors.md

These are the BEFORE values. Step 5's AFTER values must match for
every count except the wc -l line count, which should drop or stay
the same.

## Step 2: Identify the protected blocks

Before editing anything, locate and mentally tag the protected
blocks. None of these are touched in this session.

Protected content:
- Every line that starts with an ID prefix and a colon or
  pipe-table cell:
    `E-NN:`, `| W-NNN |`, `| PA-NN |`, `| PT-NN |`, `| CM-NN |`,
    `C-NN:`
  These rows' content is the deliverable for later phases.
- The Transfer Invariants verbatim block from CLAUDE.md in
  section 0.2. Identify it by running:
    grep -n "Transfer Invariants\|CRITICAL INVARIANTS" \
      docs/audit/financial_calculations/00_priors.md
  and noting the surrounding fenced block.
- The four developer-stated expectation paragraphs in section
  0.3. Identify them by their E-01, E-02, E-03, E-04 IDs and
  their copied-verbatim paragraphs.
- Every line beginning `Source:` or `Citation:` in any section.
  Citations stay exactly as the writer recorded them.
- The triage table in section 0.1. Classifications and reasons
  may be tightened only if they are clearly verbose and the
  tightened reason still says the same thing; otherwise, leave
  the triage table alone. The Filename and Classification columns
  are never modified.
- Every column header in every table. Schemas are stable across
  phases.
- All `_None found._`, `_No matching commits..._`, and similar
  null-marker lines.

If you cannot tell whether a piece of content is protected, treat
it as protected.

## Step 3: Identify mutable content

What you may edit:

- Section intro paragraphs that exceed three sentences, where the
  later sentences repeat the audit plan's framing. Compress to
  one or two sentences that add information specific to the
  priors document.
- "Per-document counts:" or "Per-plan counts:" bulleted lists
  that immediately follow a table whose contents already make the
  counts visible. Either remove the redundant list or compress to
  a single sentence ("counts: <plan-A> 12, <plan-B> 8, ...").
- "Why in scope" cells in section 0.6 longer than one short
  phrase. Compress to a phrase.
- "Why still in scope" or other reason cells that contain
  multiple sentences. Reduce to one sentence.
- Markdown table column padding inconsistencies (extra spaces,
  unaligned pipes) that make the file harder to read.
- Heading levels that are inconsistent across sections (e.g. one
  section uses `###` for sub-sections and another uses `####`
  for the same depth).
- Trailing whitespace, double blank lines, or stray formatting
  artifacts.

What you do not edit:
- Tone, voice, or word choice in protected blocks (see step 2).
- Anything that would change the meaning of a row, even if the
  row reads awkwardly. Awkward but accurate stays.
- Anything that would lose a citation or break an ID sequence.

## Step 4: Apply edits in passes

Edit the priors file in three passes. Run `wc -l` after each pass.

### Pass A: Section intros and framing duplicates

Read each section header and the prose paragraph(s) immediately
after it. Compress only where the prose:
- Repeats what the audit plan already says (the priors document
  doesn't need to re-explain the audit's purpose).
- Repeats framing already stated in the section above.
- Has more than three sentences when one would suffice.

Do not remove any sentence that asserts something specific to
this priors file (counts, decisions made, version recorded, etc.).

### Pass B: Cell tightening

Walk every table. For each cell containing multiple sentences,
ask whether the additional sentences carry information. If they
just elaborate on the first sentence, remove them. If they carry
information needed for verification, keep them.

Pay particular attention to:
- 0.6's "Why in scope" column.
- 0.7's "Intent" column for patches.
- The "Reason" column in the 0.1 triage table.

### Pass C: Format cleanup

Fix markdown formatting issues:
- Heading-level inconsistency.
- Table column padding.
- Trailing whitespace.
- Stray double-blank lines.

This pass changes line counts only modestly; it is mostly about
visual polish.

## Step 5: Verify integrity and length

Run the same commands from step 1 again. Record AFTER values.

Compare BEFORE and AFTER:

- Every ID-series count (E, W, PA, PT, CM, C) must be EXACTLY
  the same. If any count is lower, an entry was deleted; revert
  the change.
- The total table-row count (`grep -c "^| "`) must be the same
  or higher (higher is possible only if previously-malformed
  rows were repaired). Lower is a problem.
- The line count (`wc -l`) should drop by the amount of prose
  removed. The target final value is 200-500.

Additional verification:

1. Run:
     grep -n "_To be filled in by session" \
       docs/audit/financial_calculations/00_priors.md
   You should see 0 matches.
2. Run:
     grep -n "Transfer Invariants\|CRITICAL INVARIANTS" \
       docs/audit/financial_calculations/00_priors.md
   The verbatim block must still be present and untouched.
3. Run:
     grep -n "Transfer to a debt account splits two ways" \
       docs/audit/financial_calculations/00_priors.md
   The first developer-stated expectation must still be present
   verbatim. (Same check applies to the other three; sample at
   least one to confirm.)
4. Sample three random Source: lines. For each, confirm the
   citation still resolves to a real file and line in the
   project.

## Step 6: Report and stop

The priors document is now content-frozen and ready for Phase 1.
Do not begin Phase 1.

If at any point during step 5 a verification check failed and you
could not resolve it without violating step 2, revert the entire
file from the backup the developer made (named
`00_priors.pre-review.md`) and report the failed check. The
developer will rerun the review session with a tighter prompt or
investigate manually.
</task>

<verification>
The verification work is integrated into step 5 above. Beyond
those checks, before reporting done:

1. Visually skim the priors file end-to-end. Confirm every
   section header is still present:
     grep -n "^## 0\." docs/audit/financial_calculations/00_priors.md
   You should see headers for 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7
   plus their sub-sections (table-of-contents-style listing).
2. Confirm the file's last non-empty line is from section 0.7,
   not from earlier. Run:
     tail -20 docs/audit/financial_calculations/00_priors.md
   The last content should be section 0.7's commit table or its
   placeholder marker.
3. If `09_open_questions.md` exists, confirm it was not modified.
   Run:
     ls -la docs/audit/financial_calculations/09_open_questions.md
   The modification time should be unchanged from before this
   session.
</verification>

<stop>
After verification passes, stop. Do not begin Phase 1. Do not
start any work in any subsequent phase.

In your final message, report:

- The full path to the reviewed priors file.
- BEFORE and AFTER line counts (`wc -l` outputs).
- BEFORE and AFTER counts for each ID series (E, W, PA, PT, CM,
  C). These should match exactly.
- The number of edits applied in each pass:
    Pass A (intros): <n> edits.
    Pass B (cells): <n> edits.
    Pass C (format): <n> edits.
- Whether the final line count is within the 200-500 target.
  If yes, declare Phase 0 complete.
  If no, report the final count and which pass would need
  another round to reach target.
- Anything you noticed during the review that the developer
  should know but that was out of scope to fix (e.g., a triage
  classification that looks wrong, a watchlist entry that
  duplicates another, an open question that may already have an
  answer recorded elsewhere). Flag these as one-line notes; do
  not edit them.

Do not summarize the priors document in chat. The file is the
deliverable.
</stop>
```

---

## After the agent finishes

1. **Diff the reviewed file against your backup.** This is the
   most efficient way to see what the review session changed:

   ```bash
   diff docs/audit/financial_calculations/00_priors.pre-review.md \
        docs/audit/financial_calculations/00_priors.md \
        | less
   ```

   Look for any change that touched a protected block. Common
   warning signs:
   - Lines starting with `E-`, `| W-`, `| PA-`, `| PT-`, `| CM-`,
     or `C-` appearing in either the `<` or `>` half of the diff.
     IDs and their content should be identical before and after.
   - Lines starting with `Source:` appearing in the diff.
     Citations are protected.
   - Any change inside the Transfer Invariants verbatim block.
   If any of these appear, restore the backup file and rerun the review session with the offending
   behavior named explicitly in the `<scope>` block.
2. **Read the reviewed file end-to-end.** This is the last full
   pass on the priors document before Phase 1 begins. The file
   you have now is what every subsequent phase will cite. Catch
   inconsistencies now, not later.
3. **Address the agent's flagged notes.** The agent's report
   should list anything noticed during review that was out of
   scope to fix. Decide which (if any) to address now (with a
   small fix-up session) versus leave for the relevant later
   phase to handle.
4. **Delete the backup** once you are confident in the reviewed
   file:

   ```bash
   rm docs/audit/financial_calculations/00_priors.pre-review.md
   ```

5. **Phase 0 is complete.** The priors document is ready to feed
   Phase 1.
6. **Start Phase 1.** Phase 1 is the inventory phase. The audit
   plan recommends one session per layer (services, routes,
   models, templates, JS), each using the Explore subagent. The
   suggested first session is the services layer, since it
   contains the canonical calculation paths:

   ```bash
   claude --permission-mode plan --session-name audit-p1-services
   ```

   Ask for the Phase 1 services prompt when ready.

## If the agent goes off script

Common ways the priors-review session can drift:

- **Editing a verbatim block.** The Transfer Invariants quote and
  the four developer-stated expectations are sacred. If the
  diff shows changes inside either, restore from backup and
  rerun with step 2's protected-content list reinforced.
- **Adding new content.** The session does not add findings,
  expectations, watchlist entries, or anything else. If the diff
  shows new ID-tagged entries, the agent invented content;
  restore from backup.
- **"Improving" awkward but accurate cells.** Step 3 is explicit:
  awkward but accurate stays. If the agent rewrites a one-
  sentence finding into smoother prose that subtly shifts the
  meaning, restore from backup.
- **Chasing under-200 lines.** If the file was already tight at
  300-400 lines, the review may make minor cosmetic
  improvements but will not approach 200. The 200 figure is the
  lower bound of the acceptable range, not the target. Stop
  chasing it.
- **Reading other files.** The session reads the audit plan and
  the priors file. Nothing else. If the agent opens an
  implementation plan to "verify a watchlist entry", that is
  Phase 3's job; restore and rerun with the scope reinforced.

If the agent's first attempt drifts in a way that requires a restore, run `/clear`, restore from
backup, launch a fresh session with `--session-name audit-p0-review-2`, and paste this prompt with
one extra sentence in the `<scope>` block describing the specific drift you observed.

If the agent's first attempt does not produce a file within the 200-500 line target, decide whether
to:

- Run a second review session with stricter pruning instructions.
- Accept the file at its current length. The 200-500 range is a
  guideline, not a hard rule; a 510-line priors file does not
  invalidate the audit. The audit plan target reflects what
  experience suggests is sustainable across the rest of the
  audit, not a strict acceptance criterion.
