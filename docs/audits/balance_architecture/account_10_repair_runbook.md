# Runbook: the account-10 repair

**STATUS: PERFORMED on production 2026-10-12** (stamp `2026-10-12_195633`), acts 1-10 as written
here, from the runbook as rehearsed that day; its record is
`../../plans/historical/balance_x_f3c_2b_2c_as_performed_2026-10-12.md`. Rewritten by plan step
`balance:X-f3c-2b-2c` (2026-10-09) for ruling **R-BAL3** as amended that day. The procedure this
file used to carry (rehearsed 2026-09-01, superseded by R-BAL3 on 2026-09-05) is in git history,
and so are its measurements; read it there as evidence, never as instructions.

## What this is

Account 10 (*Fidelity Money Market Savings*) and archived account 2 (*Fidelity Savings*) are the
same real Fidelity account (ruling **R-HK**), and the app's record of it disagrees with Fidelity's
own export: one real ACH is recorded twice (**N-382**), five dividends were never recorded, account
10's books open on the wrong day at the wrong figure (**N-379**), and the archived twin still
carries the whole balance on the balance sheet (**N-384**'s instance). Checking (account 1) is in
it too: its books open on a plug rather than on its bank's close (**N-275**), and one of its bank
lines is recorded nowhere (**BAL-468**).

**An owner performs it by clicking through the app**, never by a migration or a script writing
money rows (ruling **R-HJ**). The acts, their order and their reasons are ruled
at **R-HJ**, **R-HK**, **R-HL**, **R-HM** and **R-BAL3** (ruling cards in the tracker), amended
2026-10-09 by **R-BAL249** (this file's split), **R-BAL250** (act 9 renames a category),
**R-BAL255** (act 1 cancels rather than deletes) and **R-BAL256** (every Fidelity side is typed
its own day). The step's tick filed those four as rulings, and **BAL-622** as a
finding; before it their verbatim record was the step's handoff folder. This file
is how to carry them out.

**Where the amounts are (ruling R-BAL249).** This file names every amount by its ROLE ("Fidelity's
close for 2026-03-25"), never by its figure. Every figure to type, and every figure to expect after
each act, is on the PERFORMANCE SHEET the rehearsal writes into the handoff folder, derived at run
time from the production copy and the banks' own records. **Type amounts from the sheet of the
same-day rehearsal and from nowhere else.**

---

## Before you start

**1. Is production running the code that was rehearsed?** A rehearsal is evidence only for the
app code it ran on. From the checkout the rehearsal runs from:

```bash
REV=$(docker inspect shekel-prod-app \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
git diff --quiet "$REV" -- app migrations && [ -z "$(git status --porcelain -- app migrations)" ] \
  && echo "SAME CODE" || echo "DIFFERENT CODE"
```

It compares the deployed revision with the files on disk, uncommitted and untracked ones
included. `DIFFERENT CODE` means rehearse from a clean checkout of `$REV` instead. Plan step
`balance:X-bi-6-4d` rewrites how a transfer's sides are stored, which acts 1, 5 and 8 edit, and
that is why its release waits for this repair; if it ships first, the whole rehearsal is re-run on
that code.

**2. Take a same-day copy, and rehearse on it.** The rehearsal performs every act below through
the same doors you are about to click, on a copy of production taken the same day, and writes the
performance sheet. **The dump is also the repair's backup**: restoring it is part of the
rehearsal, which proves it restores. **If "Add earlier paychecks" is to be performed at all, do it
before taking the dump**, so the rehearsal measures the repair on top of it. Then make no other
change in the app between taking the dump and finishing act 10.

**Run every block from a checkout OUTSIDE `~/projects/Shekel`** (a sibling worktree, such as
`~/projects/shekel-f3c2b2c`): a checkout inside it loads that folder's `.env`, which configures the
app differently from every rehearsal measured here, all of which ran from outside it. First the SETUP block. It only defines names and runs
nothing, so it can be pasted again at any time; fill in the four values in capitals.

```bash
D=~/projects/shekel-handoffs/FOLDER    # the handoff folder for the day, outside every git checkout
F=FIDELITY_CSV                         # the Fidelity history export; S, the SECU daily balances:
S=SECU_CSV                             # both paths are in the handoff record of the step
RESIDUE=BAL467                         # the figure ledger row BAL-467 states, books less bank
B=shekel_f3c2b2c_before                # a copy no act touches
R=shekel_f3c2b2c_rehearsal             # the copy the rehearsal performs on
P=shekel_f3c2b2c_performed             # the copy of production after the performance
U=$(docker exec shekel-dev-db sh -c 'echo "postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:5432"')
psql_dev() { docker exec shekel-dev-db sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1 -c '$1'"; }
dump_prod() { docker exec shekel-prod-db sh -c 'PGPASSWORD="$(cat /run/secrets/postgres_password)" pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$1" &&
  [ -s "$1" ] || { echo "STOPPED: the dump $1 failed or is empty"; return 1; }; }
restore() { { psql_dev "DROP DATABASE IF EXISTS $1;" && psql_dev "CREATE DATABASE $1 OWNER shekel_user;" &&
  docker exec -i shekel-dev-db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d '"$1" < "$2"; } ||
  { echo "STOPPED: restoring $1 failed"; return 1; }; }
run() { db=$1 out=$2; shift 2    # $out holds only the output of the program; errors go to $out.err
  SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))') LC_ALL=C.UTF-8 LOG_LEVEL=ERROR \
    PYTHONPATH=$PWD DATABASE_URL="$U/$db" ~/projects/Shekel/.venv/bin/python "$@" > "$out" 2> "$out.err" && rc=0 || rc=$?
  cat "$out"; [ $rc -eq 0 ] || tail -n 20 "$out.err"
  echo "exit $rc  ($*)"; return $rc; }
```

Then the REHEARSAL block. Its steps run inside `( set -e ... )`, so the FIRST failure stops them,
says why, and nothing after it runs. It drops and rebuilds `$B` and `$R`, and nothing else.

```bash
T=$(date +%F_%H%M%S)                   # names every file this run writes; the sheet carries it
( set -e
  [ -d "$D" ] || { echo "STOPPED: no folder $D"; exit 1; }
  for f in "$F" "$S"; do [ -f "$f" ] || { echo "STOPPED: no file $f"; exit 1; }; done
  dump_prod "$D/prod_$T.dump"
  restore $B "$D/prod_$T.dump"; restore $R "$D/prod_$T.dump"
  run $B "$D/score_before_a10_$T.txt" tests/manual/measure_cutover_against_bank.py --account 10 --format fidelity --bank "$F"
  run $B "$D/score_before_a1_$T.txt"  tests/manual/measure_cutover_against_bank.py --account 1 --bank "$S"
  run $B "$D/render_before_$T.txt"    tests/manual/verify_render_surfaces.py "$D/render_before_$T.json"
  run $R "$D/rehearsal_$T.txt"        tests/manual/rehearse_account_10_repair.py --clone $R --bank "$F" \
        --residue "$RESIDUE" --sheet "$D/sheet_$T.md" --snapshot "$D/snapshot_$T.json"
  run $R "$D/score_after_a10_$T.txt"  tests/manual/measure_cutover_against_bank.py --account 10 --format fidelity --bank "$F"
  run $R "$D/score_after_a1_$T.txt"   tests/manual/measure_cutover_against_bank.py --account 1 --bank "$S"
  run $R "$D/render_after_$T.txt"     tests/manual/verify_render_surfaces.py "$D/render_after_$T.json"
  run $R "$D/baseline_rehearsed_$T.txt" tests/manual/verify_balance_baseline.py "$D/baseline_rehearsed_$T.json"
  run $R "$D/check_rehearsed_$T.txt"  tests/manual/rehearse_account_10_repair.py --check --clone $R --bank "$F" \
        --residue "$RESIDUE" --snapshot "$D/snapshot_$T.json"
  diff "$D/render_before_$T.json" "$D/render_after_$T.json" || true   # pages DO differ: read them
)
```

**The rehearsal must end `rehearsal complete` with exit 0, and its check `check complete`, or the
repair does not start.** It refuses BEFORE any write, naming every reason:

* a target it was not pointed at, or one named `shekel` (the deployed database's name AND the
  shared dev runtime's);
* a copy the repair has already touched (both openings, transfers 1 and 102, category 33);
* a stated map that does not reconcile with BOTH banks in both directions: every Fidelity day
  after the books open answered by exactly one transfer or dividend, every settled movement on
  accounts 2 and 10 answered by an act, Checking's four lines of 2026-03-26 exactly the ones the
  map answers and none matched yet, and Checking's movements before SECU's next posted day exactly
  the mapped rows;
* a payroll residue that is not the figure ledger row **BAL-467** states;
* a sheet or snapshot path inside any git checkout, or one where a file already exists.

Then it refuses AFTER the acts if any verification fails (the sheet's last section). Before its
first act it writes `snapshot_$T.json`: the id of every row and every paycheck its copy holds,
which is the dump's, which of those rows were already deleted, and when it was taken; no figure.
The check reads it, on the rehearsed copy and on production's, to tell every row the acts created
from the rows that were already there, and to name any of those a Delete removed.

**3. Work in ONE browser tab.** Log out everywhere else first. A second tab holds pages rendered
before an act, and a save from one would post what it rendered.

**4. Write down Checking's balance for the next paycheck** as the grid shows it. Act 1 is checked
against it.

---

## Already true on production, so not an act

* **Six transfers' Checking sides already sit on SECU's days.** The superseded procedure re-dated
  transfers 156, 154, 157, 346 and 409 onto the bank's days. Since SECU's statements were imported,
  each of those and transfer 155 records, on its Checking side, the day SECU's import OBSERVED,
  and that is the day Fidelity posted it. The rehearsal MEASURES this on each copy and refuses to
  start if any side is not on its day, so it is never assumed. Only their Fidelity sides are left,
  as act 8.

---

## The stop rules

**Perform acts 1 to 10 in ONE sitting.** The sheet prints the rule on its own STOP RULES line;
these are the windows it closes, each measured act by act on the sheet's AFTER lines:

* **Act 1 to act 2: the twin books interest that never happened.** Dropping transfer 1 takes its
  arrival off the twin while the twin's 2026-04-06 assertion still stands, so the gap books a
  correction, and ruling **R-FO** sends an interest-bearing account's correction to its modelled
  `interest_income` row. Act 2 clears it.
* **Inside act 2: do not stop while account 2 is unarchived**, nor between its restatement and its
  assertion. Zeroing the opening while the old assertion stands books the twin's whole asserted
  balance as the same false interest until the new assertion supersedes it, and unarchived, every
  dashboard shows it.
* **Act 3 to act 9: account 10's corrections are enlarged.** They already stand in, as modelled
  interest, for the dividends the app never recorded; restating the opening (act 3) enlarges them,
  and recording the dividends (act 9) empties them.
* **Act 9 to act 10: the last dividend is counted twice.** Until act 10 the latest assertion on
  account 10 is earlier than Fidelity's last dividend, so that dividend sits inside the open
  accrual window beside the modelled accrual it replaces (ruling **R-HM**). Act 10 moves the
  window past it.
* **Inside every row act 7 and act 9 create: finish all three saves before the next row.** Marking
  a row Paid or Received stamps TODAY, so between that save and the day correction the row counts
  in your balances on today's date for its full figure.
* **If an Add goes through twice** (two new rows where you made one), remove the second with
  **Delete this row** on its card while it is still unpaid. Never mark it Paid or Received, which
  counts its money twice (the check names it and says stop: that remedy is not rehearsed), and
  never Cancel it: a cancelled row has no cell, so no page reaches it to delete it, and the check
  then fails and the repair is not finished (ruling **R-BAL262**).

**A refused save wrote nothing, and every save before it stands. It shows one of four ways**, and
each means stop and read it:

* on the Edit, Settings and Categories pages, a RED flash at the top of the page;
* on a grid card, NO flash: the card closes as it does after a good save, and the CELL redraws in
  red with a small octagon icon. Its reason is in the cell's tooltip (hover over it). **After
  every save on a grid card, look at the cell**;
* in a balance editor (act 2 step 3, act 10), the editor stays OPEN with small red text in it;
* in the Add Transaction modal (acts 7 and 9), the modal stays OPEN with no message. A good Add
  closes it and reloads the page.

**If the refused save is a row's day correction** (the third save in act 7 or act 9), that row
stands settled on TODAY: do not go on to another act, because the remedy for it has not been
rehearsed. The order below is what keeps this from happening.

---

## The order, and where it is FORCED

**Both restatements (acts 3 and 4) precede every re-date and every new row (acts 5 to 9).** The
books boundary refuses a movement dated on or before its account's `opened_on` (ruling **R-HG**),
so nothing can be dated 2026-03-26 until the books it belongs to open 2026-03-25. Act 9 is caught
by the same rule: account 10's books open 2026-04-05 until act 3, so the 2026-03-31 dividend is
refused -- **and only at its THIRD save**, after the first two have already left a settled row
dated today. Act 7 has the same shape against act 4.

**Transfer 102's day (act 5) needs BOTH openings already restated**, not only its own side's: one
save writes both sides, and the boundary is asked per side. A per-account "restate, then re-date"
walk is one click from a refusal mid-act.

The rest is ruled rather than forced: the twin is zeroed directly after act 1 because act 1 opens
the window act 2 closes; the dividends come after the restatements anyway (ruling **R-HL**); and
act 10 follows act 9 directly (ruling **R-HM**).

---

## Finding things on screen

* **The grid opens on the account Settings > General > Default Grid Account names**, and no page
  links to it on any other account: its own earlier and later arrows keep that account. The grid
  work of acts 1 to 7 is on Checking's grid; act 8 begins by setting that to *Fidelity Money Market
  Savings* (Save Settings), and act 10 ends by setting it back. Neither moves money.
* **The grid shows a window of paychecks.** The arrow left of the window ("Show earlier periods")
  steps back one paycheck at a time; 2026's March and April columns are some way back.
* **A transfer is drawn on each account's grid under the OTHER account's name**: transfer 1 is in
  Checking's row named *Fidelity Savings*.
* **Every new row is created with the grid's "Add Transaction" button** (top of the grid), never by
  clicking an empty cell: the grid draws a row only for a category that already holds something,
  so an empty category has no cell to click. The modal asks Name, Amount, Type, Category and Pay
  Period, and books the row on the account the grid is on. The Pay Period list labels each
  paycheck by its dates, "03/26 - 04/08". After **Add** the page reloads on the same window; the
  new row shows once its column is in view.
* **A restatement is made on the account's Edit page, in the card headed "When the books
  opened"**, below the account's own form: fields **Books opened on** and **Opening equity**, and
  the card's own button **Restate opening**. The page's blue **Update** button belongs to the
  account form and restates NOTHING. **Success is a green flash beginning "Books restated: this
  account now opens on"** with the day and figure you typed; any other message, or none, means the
  opening did not move.

---

## The acts

Each act is headed as the sheet heads it, and each typed value is on the sheet under that act.

### Act 1 -- drop transfer 1: set it back, then cancel it (R-BAL3, R-BAL255)

Transfer 1 is the transfer from Checking to the archived twin, settled 2026-03-27, in the column
of the pay period starting 03/26. Click its cell to open its card, set **Status** to **Projected**
and **Save**. The card closes, and the cell now shows a check-mark button: **do NOT press it**, it
marks the transfer Paid again, dated today. **Click the cell itself to reopen the card, and press
the RED-outlined "Cancel" with the octagon icon, in the bottom row beside the green "Paid"** --
NOT the grey "Cancel" with the x beside Save, which only closes the card (the two buttons share a
word: **BAL-622**).

*Success:* the transfer's cell DISAPPEARS from Checking's grid (a cancelled transfer has no cell),
and Checking's balance for the next paycheck reads what you wrote down before act 1. **If it reads
lower by transfer 1's amount, the transfer was not cancelled**: open its card. If its Status reads
Paid (the check mark was pressed), set Status to Projected and Save first, which closes the card,
and reopen it by clicking the cell (not the check mark). Then press the red Cancel.

*Why:* one real ACH left Checking and reached Fidelity on 2026-03-26, and the app records it
twice. Transfer 102, into account 10, is the record KEPT: SECU's and Fidelity's lines for that day
pair one-for-one (R-BAL3). Cancelled says what is true of transfer 1, that the planned transfer to
the old account did not happen. No page renders a single-transfer delete, and a cancel is not a
soft delete, so the soft delete's restore exposure (**N-386**) is not incurred.

*After:* no displayed balance moves. The twin's corrections rise by transfer 1's amount and its
modelled interest falls by the same: the first window is open.

### Act 2 -- zero the archived twin (account 2) in ONE sitting

1. Accounts, the archived region, account 2: **Unarchive**.
2. Account 2, **Edit**, the "When the books opened" card: keep the day the sheet gives (its
   current one), type **0.00** as the **Opening equity**, press **Restate opening**, and see the
   green "Books restated" flash.
3. Account 2's balance editor (its cell on the Accounts page, or its details page): balance
   **0.00**, as of **2026-04-06**, Save.
4. **Archive** it again.

*Why:* the twin's history is consolidated onto account 10, which opens holding that money (R-HK
as amended by R-BAL3). The 2026-04-06 assertion supersedes the older figure for that day rather
than editing it: assertions are append-only, and the old figure stays in the account's history as
what was believed at the time. The unarchive is forced because an archived account reaches no
balance editor by clicking (**N-453**). The round trip moves no money; the rehearsal grades that
with a fingerprint of the posted ledger either side of each flip.

**Step 2's flash is this act's only visible check of the restatement.** Measured: with step 2
skipped and step 3 done, the twin still reads 0.00, but its whole asserted balance is booked as a
loss of interest. The post-performance check names it, on its lines for account 2's opening and
account 2's corrections. **Step 3's figure and day are checked there too**, on the line beginning
"act 2, step 3". Typed as of a LATER day, the old figure still governs 2026-04-06 but the 0.00
governs every day from the typed one on, so only account 2's balances from 2026-04-06 to the day
before the typed one move, and that line alone fails. Typed as of an EARLIER day, the old assertion
still governs 2026-04-06 and every day after it: the twin keeps its old figure, and three lines
fail (the twin's balance, "act 2, step 3" and account 2's corrections). A balance
difference on account 2 is read beside those lines, never alone.

*After:* the twin holds **0.00**, and its corrections and modelled interest are **0.00**: the first
window is closed. This is the act whose effect is plainly visible on a balance.

### Act 3 -- restate account 10's books to 2026-03-25 at Fidelity's close for that day

Account 10, **Edit**, "When the books opened": **Books opened on** 2026-03-25, **Opening equity**
Fidelity's close for 2026-03-25 (the sheet's figure; the export carries its 2026-03-12 close
forward, nothing moving until 2026-03-26). **Restate opening**. The green flash goes on to say the
difference shows as a correction against your later balances and that restating those balances
clears it. **Do not restate them**: here the correction stands in for the unrecorded dividends,
and act 9 is what clears it.

*After:* displayed balances unchanged; account 10's corrections grow: the third window is wider.

### Act 4 -- restate Checking's books to 2026-03-25 at SECU's close for that day

Account 1, **Edit**, "When the books opened": **Books opened on** 2026-03-25, **Opening equity**
SECU's close for 2026-03-25 (the sheet's figure, folded from the app's own import of SECU's
statement). **Restate opening**, and see the green flash.

*Why:* Checking's current opening is not a fact but the plug that absorbed 2026-03-26's
movements, the ACH among them (R-BAL3). This is the act that answers **N-275**.

**One governing opening per account** (**BAL-498**). A restatement APPENDS a row, and the newest by
id governs (`app/services/cash_ledger/_events.py`, `governing_account_opening`), so after acts 2
to 4 each of the three accounts is governed by the row its act wrote. The rows they supersede
(each of the three holds two from the migration, all with one shared `created_at`) stay as
history, and no tie-break decides anything.

*After:* nothing on the sheet's lines moves (Checking's own later assertions govern the day the
sheet values at).

### Act 5 -- type transfer 102's day into BOTH sides' boxes, each from its own bank (R-BAL3, R-BAL256)

Open transfer 102 from Checking's grid, in the column of the pay period starting 03/26 (on each
grid a transfer's row is labelled with the OTHER account, so on Checking's it reads *Fidelity
Money Market Savings*; the card's header names the transfer). Its card renders one **Money moved
on** box per side: type
**2026-03-26** into Checking's box AND into account 10's box, then ONE Save.

*Why:* both banks posted it 2026-03-26, the day both books used to open, which is why it was
absorbed on both sides (R-HG) and why acts 3 and 4 had to come first. Each side's day then comes
from its own bank's record (ruling **R-BAL142** gave each side its own day).

*After:* nothing on the sheet's lines moves.

### Act 6 -- re-date Checking's rows onto 2026-03-26, the day SECU posted them

Transactions **781**, **865** and **1069**: each one's card, **Money moved on** **2026-03-26**,
Save. The app dated them 2026-03-27.

781 and 865 together answer ONE payroll line and fall short of it by **BAL-467**'s residue, which
stays open: which row is short is the owner's knowledge, and the bank states one line where the app
states two. 1069 answers its own line exactly.

*After:* nothing on the sheet's lines moves.

### Act 7 -- record bank line 133, which no row answers (BAL-468)

On Checking's grid, **Add Transaction**: the Name the sheet gives, **the bank line's own amount**
(the sheet's figure), Type **Expense**, Category **Family: Birthday** (category 26, ruled by the
developer 2026-09-06), Pay Period the one starting **03/26**; **Add**. Then open the new row's
cell in that column, step back to it if it is out of view, open **More options**, set **Status**
to **Paid** and Save (which stamps today); reopen it, set
**Money moved on** to **2026-03-26** and Save. **Three saves; finish all three before act 8.** The
day box renders only once a row is settled, so the day cannot be typed at creation.

*After:* nothing on the sheet's lines moves; Expense rises by the line's amount (the last section).

### Act 8 -- type Fidelity's day into account 10's box on the 6 other transfers (R-BAL256)

First **Settings > General > Default Grid Account: Fidelity Money Market Savings**, **Save
Settings**; the grid is now account 10's. Then for each transfer, open its card from that grid and
type into **account 10's** box only, then Save:

| transfer | its column (pay period starting) | Fidelity posted it |
|---|---|---|
| 155 | 04/09 | 2026-04-09 |
| 156 | 04/23 | 2026-04-23 |
| 154 | 04/23 | 2026-04-29 |
| 157 | 05/07 | 2026-05-07 |
| 346 | 05/21 | 2026-05-14 |
| 409 | 07/16 | 2026-07-23 |

**Find each one by its column, not by its name**: five of the seven share one name, and the sheet
gives each transfer's amount. Transfer 346 is filed in the 05/21 column although Fidelity posted it
inside 05/07's: the day box moves the day and not the column. On account 10's grid each sits in the
row labelled *Checking*.

Leave every Checking box as rendered: each is SECU's to state, already observed by its import, and
plan step `balance:X-bk-2` grades them. A side that already holds its day as its own (not "a
guess") is skipped, and the sheet then says "nothing to type" for it.

*Why:* each side keeps its own day (R-BAL142). Left alone, a Fidelity side borrows Checking's day
and reads "a guess", and a later correction on the Checking side would move it although Fidelity
says otherwise (R-BAL256).

*After:* nothing on the sheet's lines moves; the money was already on these days.

### Act 9 -- rename the empty category, then record the dividends the app has never held (R-HL, R-BAL250)

**First the category.** Settings, Categories, category **33** (*Financial: Dividend*, created by the
owner and never used): its pencil, Group **Income**, Item Name **Interest & Dividends**, Save. The
rehearsal refuses to start unless category 33 is still the owner's, still named so, and holds
nothing: a rename relabels everything filed under it.

**Then each dividend**, in day order, on account 10's grid: **Add Transaction** with Name
**Dividend** (one name, so all five share one grid row), **the export's dividend that day** (the
sheet's figure), Type **Income**, Category **Income: Interest & Dividends**, and the Pay Period
starting on the date below; **Add**. Then open the new row's cell in that column (step back to
it if it is out of view), open **More options**, set **Status** to **Received** and Save (which
stamps today); reopen it, set **Money moved on** to the dividend's day and Save.
**Three saves per row; finish each row before the next.**

| dividend day | the pay period starting |
|---|---|
| 2026-03-31 | 03/26 |
| 2026-04-30 | 04/23 |
| 2026-05-29 | 05/21 |
| 2026-06-30 | 06/18 |
| 2026-07-31 | 07/30 |

Each is one `DIVIDEND RECEIVED` line in the export; the `REINVESTMENT` line beside it is the same
money buying the core position back and is not a second event. The export's two earlier dividends
fall on or before 2026-03-25 and are inside the opening (R-HG).

*After:* account 10's corrections and modelled interest go to **0.00** (the third window closes),
and account 10 now reads ABOVE Fidelity's last close: the fourth window is open.

### Act 10 -- assert Fidelity's last stated close on 2026-07-31 (R-HM)

Account 10's balance editor: balance **Fidelity's close for 2026-07-31** (the sheet's figure), as of
**2026-07-31**. Save. Then **Settings > General > Default Grid Account** back to the account the
sheet names (the one it was on before act 8), **Save Settings**.

*Why:* the modelled accrual window opens at the latest assertion, so asserting the bank's own close
for the export's last day moves the window past the last dividend (R-HM).

**This is the one act with no undo.** A figure can be corrected by asserting again, but the accrual
window opens at the LATEST assertion, and no door deletes one.

*After:* account 10 reads one day of modelled accrual above Fidelity's close: R-HM names that
property of ruling **R-L**, and it is not this repair's. The fourth window is closed.

---

## What you will and will not see move

**Most acts move a CORRECTION, not a displayed balance, and an operator who does not know that
will think the repair is doing nothing.** An assertion RESETS the running total on its own day, so
a movement re-dated below an account's latest assertion changes what the records EXPLAIN without
changing what the account SHOWS.

The sheet's BEFORE and AFTER lines value all three accounts at the export's last day, 2026-07-31 (a
fixed point: "today" gives a different figure every day an interest-bearing account is valued),
and print the twin's and account 10's corrections and modelled interest beside them. **Only three
acts move a displayed figure on those lines**: act 2 (the twin to 0.00), act 9 (account 10 rises,
the last dividend counted twice) and act 10 (account 10 falls back to one day of accrual above
Fidelity's close). **Checking's figure never moves at that date**, because its own later assertions
govern it, **nor does any of its figures from today forward** (measured at four forward days).

**On a daily balance screen, measured day by day over 2026-03-20 to 2026-07-31** (the rehearsal of
2026-10-09 14:34, before against after; the figures are in the handoff folder):

* **Checking moves only through 2026-03-26**, its new opening stretch. From 2026-03-27 its own first
  assertion governs, so no later day moves.
* **The twin moves on every day**, to 0.00.
* **Account 10 moves on every day through 2026-04-29, then on 2026-05-29 to 06-22, 06-30 to 07-15,
  and 07-31**: the opening, the dividends and the Fidelity days, each up to the next assertion
  (2026-05-01, 06-23 and 07-16 reset it). **On 2026-07-16 to 07-30 only the SHOWN figure moves**,
  down: act 10 strips modelled accrual the bank's own close replaces (R-HM). Every day Fidelity
  names in those spans now matches its close (the scorer, below).

**Do not use "the trial balance is zero" as a check.** It always is: a deferred database trigger
refuses any journal entry whose legs do not sum to zero, so it measures the trigger. What the acts
move is the balance BETWEEN classes, and the sheet's last section prints every class's move and
asserts the four the repair's own inputs derive: expense rises by act 7's line; the income
statement gains the dividends recorded, less the modelled interest they replace (the sheet prints
it in the ledger's own sign, where income is negative); and the Liability and Unrealized classes do
not move. Asset and Equity move by the restatements, whose figures are the repair's subject.

---

## After: what to check

### On the rehearsal (before anyone clicks)

* **Account 10:** the opening day, 2026-03-25, prints **0.00** against Fidelity on its own line.
  On the scored days (the bank days above the books plus the days the owner asserted on that the
  bank never names), **the cutover arm is exact on every one**, and it has no reset, so no day is
  exact by construction; **the cash fold is exact on every day carrying no assertion** (its
  assertion days are exact by construction, so its pooled row is the scorer's confounded one); and
  **the RENDERED figure is exact on every day but 2026-07-31**, R-HM's one day of accrual. Before
  the repair, fewer days are scored and fewer are exact. The counts are in the handoff folder.
* **Checking:** the opening day, 2026-03-25, prints **0.00** against SECU on its own line (N-275's
  opening), and the sheet's last section reads Checking's 2026-03-26 off SECU's close by BAL-467's
  residue and nothing else, which is what grades acts 5 to 7. Checking's other scored days are not
  this repair's: what Checking's own assertions disagree with SECU about is plan step
  `balance:X-bk-2`'s reconcile, which waits on this repair (**R-BAL203**).
* **The render check:** zero server errors after, and the pages whose size changed are the three
  accounts' own pages, the grid and the savings page, and no others.

**What the rehearsal also verifies, on the sheet's last section**: every class's move; the twin
holds 0.00, is archived, and its books open on their own day, 2026-03-26, holding 0.00; its 0.00
is the assertion governing 2026-04-06; the
twin's and account 10's corrections and modelled interest total 0.00; transfer 1 and its rows are
Cancelled (the grey-button trap); category 33 is named *Income: Interest & Dividends* and holds no
row the copy held before act 1; account 10 asserts Fidelity's close on 2026-07-31; both books open
2026-03-25 holding their banks' closes; all seven Fidelity sides record Fidelity's day as their
own, and the other six transfers' Checking sides still record the day SECU's import observed;
account 10 reads Fidelity's close on 2026-03-26; **every row the snapshot does not hold is
accounted for**: the six the sheet names (act 7's and the five dividends), each in the account,
category, name and paycheck the sheet gives, counted once on its day for its figure, beside any
rows the pay-period window filled into paychecks it added (below), and any other row is named, with
a remedy only where one is measured (below); every row the copy held before act 1 is still there;
and Checking reads SECU's close on 2026-03-26 apart by BAL-467's residue and nothing
else, with every Checking movement before SECU's next posted day one the map names. **Every one of
these but the class moves is also the CHECK** (below): the same arms, run read-only on any copy.

**Rows nobody typed.** The grid keeps a window of paychecks ahead of today, and on the first grid
or dashboard page loaded after a payday it adds a paycheck and fills it from the owner's recurring
items, the paycheck among them. A dump taken before that first load meets those rows after it,
in the rehearsal and in the performance alike. The check tells them apart by what made them, a
recurring item filling a paycheck the snapshot does not hold, and counts them on its line;
nothing typed is ever counted there, because an Add makes a one-off item of its own (measured
2026-10-10: on a copy one paycheck short, rows from the owner's recurring items, and a duplicate Add filed into the
added paycheck named all the same).

### On production, after act 10 (the same day as the rehearsal)

**The rehearsal cannot be re-run on production's copy**: it refuses a copy the repair has already
touched. Its verification can, as the CHECK (below). What grades the MONEY the human moved is a
comparison of that copy with the rehearsal's own: two copies on which the same acts were performed
hold the same balances.
**Measured 2026-10-09: two rehearsals of these acts on one copy, through two different create
paths, gave byte-identical dumps of every figure the balance seam answers, for every account; and
before against after differed only on accounts 1, 2 and 10.** The rehearsal block wrote its dump
of the rehearsed copy beside the sheet, and this block compares against that FILE, so nothing done
to the rehearsal copies since can change the answer. Paste the SETUP block if this terminal does
not still hold it, set `T` to the stamp in the rehearsal's sheet name (the block refuses one not
dated today), then paste:

```bash
T=STAMP                                # from the sheet name of the rehearsal: sheet_STAMP.md
( set -e                               # compares against FILES that rehearsal wrote, never a live copy
  [ "${T%%_*}" = "$(date +%F)" ] || { echo "STOPPED: T=$T is not a rehearsal of today's"; exit 1; }
  for f in sheet_$T.md snapshot_$T.json score_after_a10_$T.txt score_after_a1_$T.txt \
           baseline_rehearsed_$T.json check_rehearsed_$T.txt; do
    [ -f "$D/$f" ] || { echo "STOPPED: no $f in $D"; exit 1; }; done
  [ "$(tail -n 1 "$D/check_rehearsed_$T.txt")" = "check complete" ] ||
    { echo "STOPPED: check_rehearsed_$T.txt does not end 'check complete'"; exit 1; }
  dump_prod "$D/prod_after_$T.dump"
  restore $P "$D/prod_after_$T.dump"     # rebuilds $P only
  run $P "$D/score_performed_a10_$T.txt" tests/manual/measure_cutover_against_bank.py --account 10 --format fidelity --bank "$F"
  run $P "$D/score_performed_a1_$T.txt"  tests/manual/measure_cutover_against_bank.py --account 1 --bank "$S"
  run $P "$D/baseline_performed_$T.txt"  tests/manual/verify_balance_baseline.py "$D/baseline_performed_$T.json"
  run $P "$D/check_performed_$T.txt"     tests/manual/rehearse_account_10_repair.py --check --clone $P --bank "$F" \
        --residue "$RESIDUE" --snapshot "$D/snapshot_$T.json" || true   # a failing check still reaches the comparison below
  for a in a10 a1; do
    if diff "$D/score_after_${a}_$T.txt" "$D/score_performed_${a}_$T.txt"; then echo "SCORE $a AS REHEARSED"
    else echo "SCORE $a DIFFERS (above)"; fi
  done
  if cmp "$D/baseline_rehearsed_$T.json" "$D/baseline_performed_$T.json"; then echo "EVERY BALANCE AS REHEARSED"
  else echo "BALANCES DIFFER: compare the two JSON files"; fi
  if diff "$D/check_rehearsed_$T.txt" "$D/check_performed_$T.txt"; then echo "CHECK AS REHEARSED"
  else echo "CHECK DIFFERS (above)"; fi
)
```

**All four "AS REHEARSED" lines should print.** A difference names the account and the figure
where the money departed from the rehearsal (a mistyped amount or day, a skipped act, the grey
Cancel), or a change made in the app after the dump was taken; it is read before anything else is
done. The two balance dumps are compared on the day they are both taken, since a few of their
figures are valued at today.

**The CHECK grades what moves no balance.** Measured 2026-10-09: a twin left unarchived and
dividends filed under another income category both pass the score and balance lines. The check is
the rehearsal run with `--check`: it opens the copy READ-ONLY (it boots the app with production's
configuration, which writes nothing at boot, and every connection refuses a write), refuses a copy
no act has touched, and runs the rehearsal's own verification (the list above, all but the class
moves) on it, against the snapshot the rehearsal wrote of the same dump, reporting EVERY arm that
fails rather than the first. What only the state before the acts can grade, it PRINTS: the posted
ledger's total for each account class, and Settings > General > Default Grid Account (act 10 puts
the owner's own back). A PASSING check's output names no database, no time and no id of a row an
act created, so on two copies of the same acts it is identical, and the line above compares the
performed copy's with the rehearsed copy's; a `FAILS:` line may name such an id. Reading
`CHECK DIFFERS`:

* a `- FAILS:` line names the act that did not land: act 1 (transfer 1 not Cancelled: the grey
  Cancel); act 2 (the twin still holding a figure, not archived, its books not opening on their
  own day at 0.00, or its corrections not zero; the line beginning "act 2, step 3" is its 0.00
  typed with another figure or as of another day, act 2 above); acts 3 and 4 (an opening
  mistyped); act 3 or act 5 (account 10's books off Fidelity's close on 2026-03-26); act 5 or act 8
  (a Fidelity side still borrowing Checking's day); act 8 (a Checking side of transfers 155 to 409
  typed another day: that box is left as rendered); act 6 (a row not re-dated shows only on
  Checking's 2026-03-26 line, as a movement the map does not name there and a gap beyond the
  residue; that line also fails beside the rows line when act 7's row is not exactly the sheet's,
  or a copy of it is settled onto 2026-03-26 to 03-29); act 9 (the category not renamed, or a row
  that was already there moved into it: stop); **acts 7 and 9's rows, on the line beginning "rows
  the snapshot before act 1 does not hold"**, which names every row the acts created that differs
  from one the sheet gives in its account, category, name, paycheck, day or figure, and each of the
  six it does not find. It grades nothing else the cards offer: the Add's two checkboxes, and a
  figure typed wrong in the Add and corrected in the Actual box, were each measured to move no money
  (2026-10-10). **A remedy is named only for a row made during the acts**, after act 4's
  restatement and before act 10's assertion: if any row the line names was made outside them, the
  line is ONE stop naming those rows and no remedy, because then the snapshot is not of this copy's
  dump, the app was changed outside the acts, act 10 was skipped, an opening was restated again
  after an Add, or an Add was made out of order; delete or settle nothing on its strength. Inside
  them, an unsettled row is that act's row left UNFINISHED (finish its two saves) when the line says
  so, and otherwise a row that is not one of the six (**Delete this row** if it is a second Add you
  made, and stop if you did not make it); a
  counted one (a duplicate marked Paid or Received, or a row typed otherwise than the sheet), a
  CANCELLED one (ruling **R-BAL262**) and one marked anything else (Credit, for one) each mean
  stop, and when any row on the line means stop, the line says so and none of its remedies is
  performed; act 10 (the line beginning "act 10"); account 10's corrections (the line beginning
  "account 10's posted corrections": a dividend unfinished or mistyped in its amount, or a
  transfer's Actual box typed otherwise in act 5 or act 8); **the line beginning "rows the copy
  held before act 1 are deleted"** (a Delete pressed on a row no act made, which moves money:
  stop);
* a differing class total or Default Grid Account line, with no `FAILS:` line, is money or a
  setting the post-state cannot grade alone: read it beside the score and balance lines.

**A FAILS line is read before anything else is done, and two remedies are measured** (2026-10-11,
below): an unpaid duplicate's **Delete this row**, and the two remaining saves of a row an Add made
and left unsettled, performed when the check names it, after act 10 if that is when. A dividend's
row is on account 10's grid: put Settings > General > Default Grid Account on *Fidelity Money
Market Savings* first, as act 8 does, and back on the account the sheet names after, as act 10
does. After either, paste the POST block again: it rebuilds `$P` from a fresh dump and checks it against the same
snapshot. For any
other, stop and report it. Performing the missed act now and pasting the POST block again (it
rebuilds `$P` from a fresh dump) is the likely remedy, but it is not rehearsed, and some acts have
an order the late one would break.

**Measured 2026-10-11**, on copies of the dump of 2026-10-10 19:58, with this check: thirty-three
copies on which the acts were performed with ONE act wrong each failed the check on a line naming
that act, and none passed. Twenty-four failed that line alone: act 8 skipped; act 8 with Checking's
box on transfer 154 typed a day early; a Delete pressed on one of the owner's own rows after act 7
(the deleted-rows line); act 5's account-10 box left; the twin
left unarchived; the twin restated onto 2026-03-25; the twin's 0.00 asserted as of 2026-04-07, and
as of 2026-07-31; the grey Cancel; act 10 skipped; and, on the rows line, the dividends under
*Income: Other Income*; the 2026-05-29 dividend filed one paycheck late, named otherwise, or
settled a day early; act 7's Add, and then a dividend's, submitted twice with the copy left unpaid,
also with act 7's copy named with a curly apostrophe (left unpaid, or marked Paid) and with the
dividend's copy under *Income: Other Income* (left unpaid, or marked Received); act 7's copy marked
Paid, dated today, and dated 2026-04-02; a dividend's copy Cancelled; and, on a copy whose window
was one paycheck short, a dividend's copy filed into the paycheck the window added. Eight failed
two lines: act 2's restatement skipped (the twin's opening and its corrections); act 7's row in
another category, named with a trailing space, for another figure or one paycheck late, act 7's
copy settled onto 2026-03-27, and act 7's row marked Credit at its second save, which the rows line
names and stops on (the rows line and Checking's 2026-03-26 line); the 2026-05-29 dividend for
another figure (the rows line and account 10's corrections). One failed three: the twin's 0.00
asserted as of 2026-04-05 (act 2, above). **The rows line named no remedy for a row made outside
the acts**: a row the owner added after the snapshot and before act 1, one added after act 10, and
the clean copy checked against a snapshot one row short (as an earlier dump's would be, with its
own capture time and with an earlier one) each failed it as ONE stop naming that row; given the
pre-round snapshot format, the check refused to run. The 2026-05-29 dividend settled a day early
beside a Projected copy of it failed the rows line naming both, the copy as left unfinished, and
withholding every remedy; performing that finish anyway left account 10's corrections off by the
dividend, counted twice. Act 8 with Checking's box typed the day it already held gave the clean
rehearsal's output. Left unsettled, act 7's row failed the rows
line, which named it unfinished, and Checking's 2026-03-26 gap; a dividend's, the rows line and
account 10's corrections. Two copies whose Add went through twice and whose copy was then removed
with **Delete this row** (act 7's and a dividend's), and three whose unsettled row had its two
remaining saves made after act 10 (act 7's, the 2026-05-29 and the 2026-07-31 dividends), gave the
same check output as the clean rehearsal. So did a rehearsal on a copy whose every id sequence was
first moved on by 1000, so that every row the acts made was created under another id: a passing
check names no id an act creates. A clean rehearsal on a copy one paycheck short differed from it
only in how many rows the window filled. An unperformed copy was refused.

**Five of Fidelity's days are never compared.** 2026-01-30, 02-24, 02-26, 02-27 and 03-12 fall on
or before the books' opening and are inside the opening equity (R-HG); the scorer prints their
count and endpoints.

**The opening-day lines grade the typing of the openings.** For account 10, the rehearsal types
the export's own close and the scorer compares against the same export, so in a rehearsal that
line cannot fail. For Checking the two come from two of SECU's records, the imported statement and
the daily-balance export, so it can fail only if they disagree. On production's copy they grade
what the human typed.

---

## What this does NOT fix

* **BAL-467's residue stays.** Checking's 2026-03-26 reads SECU's close less that residue; which of
  rows 781 and 865 is short is the owner's knowledge.
* **The class behind the twin survives the instance.** An account archived while its ledger still
  holds a net is a state the app can still reach, and no surface says the balance sheet and the
  dashboards disagree about it (**N-384**), nor does an archived account reach a balance editor by
  clicking (**N-453**). Both are owned by `balance:X-f4`.
* **Checking's 2026-03-26 lines stay unmatched in the statement import.** The repair answers them
  with rows by hand and makes no match.
* **`bank_agreement`'s comparison (BAL-616) was not fixed by the repair itself;** it was fixed after
  it by the step's own code leaf, L1 `d62855733`: a day is compared only where both records cover it
  (**R-BAL291**).
* **The transfer card's two buttons reading "Cancel" (BAL-622).** Act 1 names the right one.

---

## How this was rehearsed

On copies of production taken the same day and at the current alembic head, driven through the
app's own HTTP doors. Each act fetches the page the owner opens, changes only what the owner types,
and submits the rest as rendered. The exceptions are presses of buttons that carry no form: the
archive flips post nothing, and act 1's cancel posts only the leg's account id its button carries.
Act 9's category group is also supplied, because the page sets that field by script.

* `tests/manual/rehearse_account_10_repair.py`: reconciles the map, performs the acts, verifies
  the post-state and writes the sheet. No production figure or payee is written in it (R-BAL249).
  With `--check` it writes nothing and runs the same post-state verification on a performed copy,
  against the snapshot of row and paycheck ids the rehearsal wrote before its first act.
* `tests/manual/measure_cutover_against_bank.py`: scores an account against its bank's export.
* `tests/manual/verify_render_surfaces.py`: renders every authenticated page, before and after.
* `tests/manual/verify_balance_baseline.py`: dumps every balance figure, to compare two copies.

**Seven planted defects were each refused before any write**: a boundary row dropped from the
map, a row borrowed from another day, two transfers exchanged on two different pairings, a
transfer mapped to a day the export does not name, a payroll row moved out of the census window
(refused by the stated residue), and an export carrying an extra movement. The neutral review of
2026-10-09 re-ran them, and its own mutations of each control besides.
