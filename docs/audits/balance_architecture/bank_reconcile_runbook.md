# Runbook: the bank reconcile (Checking against SECU)

**STATUS: NOT YET PERFORMED.** Written 2026-10-31 by task plan#2371 of plan step
**balance:X-bk-2** (plan#1192); its task plan#2373 rehearses it on a same-day copy, has the
developer perform it on production, and re-measures. Its blocks were run end to end on
2026-10-31 against copies of production's dump of 2026-10-30 15:24 (How this was rehearsed,
below).

## What this is

Checking's books (account 1) disagree with SECU's own record of it on some days of the books
span, and plan step X-bk-2 reconciles them through the app's own doors, as the owner does
(ruling **R-BAL196**). **It MOVES MONEY**: it cancels rows and re-dates others. The acts are
derived, not chosen: `tests/manual/reconcile_act_sheet.py` (task plan#2372) classes every
movement the disagreement list leaves unanswered, on rulings

* **plan#2343**: a CARD day (a Capital One payment line beside the per-purchase CC Payback rows
  the app holds on other days) is named and left for step **plan#2348**, after the card
  cutover;
* **plan#2359**: a Checking row on neither SECU's nor Capital One's export never moved, so it is
  cancelled, or deleted where it is a line inside an envelope;
* **plan#2426**: a row a Capital One charge could be is kept as card spending, and only a row
  neither export could be is cancelled;
* **plan#2433**: SECU's 2026-04-22 Van Loan line is held back, because matching it moves the
  payment onto the day Van Loan's books open, which no door can move; step **plan#2436**
  performs it;
* **plan#2439**: a line under a Paid envelope comes off by reopening the envelope, pressing the
  line's X, closing it again, and typing back the day it closed on.

**Where the figures are (ruling R-BAL249).** This file names every act by its ROLE. Every figure
to check, every row's name and every place on screen is on the PERFORMANCE SHEET the rehearsal
writes into the handoff folder, derived from the same-day copy and the two exports. **Perform
from the sheet of the same day's rehearsal, and from nowhere else.** Each act on it is headed
`## Act N:` and says where it is (`Where`, the page, and from act 2 on `FIND`, how to reach it on
screen), what to type (`TYPE`), tick (`TICK`) or press (`PRESS`), and what the rehearsal saw land
(`LANDED`).

**Done when** (plan#1192's done test, as rulings plan#2343 and plan#2433 amended it): after the
acts, on the account's disagreement list, every books day still apart is apart by exactly the
card items and the held-back act on it, and every other books day agrees. The check below grades
it on production's copy.

---

## The acts, by role, in the order the rehearsal performs them

| # | act | where | what it answers |
|---|---|---|---|
| 1 | **Import** SECU's export | Checking's Import statements page | records the lines only the export holds, so an import covers the books' last day; the owner's standing rules file the swipe lines they answer for |
| 2.. | **Match** each TIMING pair | the Reconcile page, the line's card, MATCH | a row the app dated otherwise than SECU posted it; the match moves it onto SECU's day |
| .. | **Match** each TRANSFER line | the same | a Projected transfer SECU paid; the match settles it on SECU's day |
| .. | **Cancel** each never-moved row | the row's card on Checking's grid | a row neither export holds (plan#2359) |
| ..23 | **Delete** each never-moved envelope line | the envelope's card on Checking's grid, under Purchases | a purchase line neither export holds; under a Paid envelope, by plan#2439's round trip |

On the dump of 2026-10-30 that is 23 acts: the import, 6 matches (5 TIMING, 1 TRANSFER), 9
cancels and 7 deletes, with 1 act held back and 60 card items named and left. **A same-day sheet
can differ**, because the owner keeps using the app: the sheet tool classes every movement by
the rulings above and refuses, writing no sheet, one it cannot class. Read the sheet's acts
before act 1 either way.

**Not performed, and graded unmoved**: the card items (plan#2348) and the held-back match
(plan#2436). The sheet names them in its last two sections, and the rehearsal and the check
both fail if any act moved one.

---

## Before you start

**1. Get both exports the same day, before the dump.** SECU's Checking history as CSV (the
running-balance column included) and Capital One's transaction history as CSV, both saved into
the day's handoff folder. The sheet is derived from both, and act 1 imports **that very
file** of SECU's: the rehearsal and the check know the import by the file's SHA-256, and a
download taken again can differ in its bytes.

**2. Open the grid once, then change nothing in the app until the after-dump.** The first grid
or dashboard page loaded after a payday adds a paycheck and fills it from the recurring items
(`pay_period_rolling.top_up_rolling_window`); opening it before the dump puts those rows in the
dump, where the rehearsal sees them too. After the dump, **record nothing, import nothing and
true up no balance** until the after-dump is taken: a balance recorded moves the books span,
and the check then refuses the copy as one no act sheet describes.

**3. Work in ONE browser tab.** Log out everywhere else first. A second tab holds pages rendered
before an act, and a save from one posts what it rendered.

**4. Run every block from the checkout holding `card/1192`**, outside `~/projects/Shekel` (a
checkout inside it loads that folder's `.env`). The harness files are that checkout's. The app
the rehearsal and the check run is PRODUCTION's: the CODE block exports the deployed revision's
`app/` into the handoff folder, and every block runs it from there. The act sheet and the
disagreement list run that checkout's own `app/`, which the sheet needs: it reads each
movement's id from the day drill-down, which carries them since task plan#2372. **That
checkout's models must read production's schema**: measured 2026-10-31, `migrations/` differs
from the deployed revision's only in comments. A checkout carrying a migration production lacks
fails the act sheet at its first read, and is not run.

First the SETUP block. It only defines names and runs nothing, so it can be pasted again at any
time; fill in the four values in capitals.

```bash
D=~/projects/shekel-handoffs/FOLDER    # the day's handoff folder, outside every git checkout
SECU=SECU_CSV                          # SECU's Checking export in $D, downloaded today (a name, not a path)
CARD=CAPITAL_ONE_CSV                   # Capital One's export in $D, downloaded today
H=CHECKOUT                             # the checkout holding card/1192, outside ~/projects/Shekel
A=1                                    # the account reconciled: Checking
B=shekel_xbk2_before                   # the copy the act sheet is derived from; no act touches it
R=shekel_xbk2_rehearsal                # the copy the rehearsal performs on
P=shekel_xbk2_performed                # the copy of production after the performance
U=$(docker exec shekel-dev-db sh -c 'echo "postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:5432"')
psql_dev() { docker exec shekel-dev-db sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1 -c '$1'"; }
dump_prod() { docker exec shekel-prod-db sh -c 'PGPASSWORD="$(cat /run/secrets/postgres_password)" pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$1" &&
  [ -s "$1" ] || { echo "STOPPED: the dump $1 failed or is empty"; return 1; }; }
restore() { { psql_dev "DROP DATABASE IF EXISTS $1;" && psql_dev "CREATE DATABASE $1 OWNER shekel_user;" &&
  docker exec -i shekel-dev-db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d '"$1" < "$2"; } ||
  { echo "STOPPED: restoring $1 failed"; return 1; }; }
# Run a harness file on copy $1 with PRODUCTION's app/ ($D/app_$T), or with this checkout's
# (here); $2 holds only the program's output, and its errors go to $2.err.
on() { app=$1 db=$2 out=$3; shift 3
  SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))') LC_ALL=C.UTF-8 LOG_LEVEL=ERROR \
    PYTHONPATH=$app DATABASE_URL="$U/$db" "$H/.venv/bin/python" "$H/tests/manual/$@" > "$out" 2> "$out.err" && rc=0 || rc=$?
  cat "$out"; [ $rc -eq 0 ] || tail -n 20 "$out.err"
  echo "exit $rc  ($*)"; return $rc; }
prod() { on "$D/app_$T" "$@"; }
here() { on "$H" "$@"; }
```

Then the CODE block: it names the run, records the deployed revision, exports that revision's
`app/`, and takes the dump. **The dump is also the performance's backup**, and the REHEARSAL
block restoring it is what proves it restores.

```bash
T=$(date +%F_%H%M%S)                   # names every file this run writes
( set -e -o pipefail
  [ -d "$D" ] || { echo "STOPPED: no folder $D"; exit 1; }
  for f in "$SECU" "$CARD"; do [ -f "$D/$f" ] || { echo "STOPPED: no file $D/$f"; exit 1; }; done
  [ "$(git -C "$H" rev-parse --abbrev-ref HEAD)" = card/1192 ] || { echo "STOPPED: $H is not on card/1192"; exit 1; }
  REV=$(docker inspect shekel-prod-app --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  [ -n "$REV" ] || { echo "STOPPED: the deployed image names no revision"; exit 1; }
  printf '%s\n%s\n' "$REV" "$(git -C "$H" rev-parse HEAD)" > "$D/revision_$T.txt"   # production's, then the harness's
  mkdir "$D/app_$T" && git -C "$H" archive "$REV" app | tar -x -C "$D/app_$T"
  [ -f "$D/app_$T/app/__init__.py" ] || { echo "STOPPED: $REV's app/ was not exported"; exit 1; }
  dump_prod "$D/prod_$T.dump"
  echo "CODE AND DUMP TAKEN: production runs $REV"
)
```

Then the REHEARSAL block. Its steps run inside `( set -e ... )`, so the FIRST failure stops
them, says why, and nothing after it runs. It drops and rebuilds `$B` and `$R`, and nothing else.

```bash
( set -e
  restore $B "$D/prod_$T.dump"; restore $R "$D/prod_$T.dump"
  here $B "$D/act_sheet_$T.out" reconcile_act_sheet.py --clone $B --account $A \
        --secu "$D/$SECU" --card "$D/$CARD" --out "$D/act_sheet_$T.json"
  here $B "$D/disagreements_before_$T.out" measure_bank_disagreements.py --out "$D/disagreements_before_$T.txt"
  prod $R "$D/rehearsal_$T.out" reconcile_rehearsal.py --clone $R --account $A --acts "$D/act_sheet_$T.json" \
        --secu "$D/$SECU" --sheet "$D/sheet_$T.md" --snapshot "$D/snapshot_$T.json"
  prod $R "$D/check_rehearsed_$T.out" reconcile_rehearsal.py --check --clone $R --account $A \
        --acts "$D/act_sheet_$T.json" --secu "$D/$SECU" --snapshot "$D/snapshot_$T.json"
  prod $R "$D/baseline_rehearsed_$T.out" verify_balance_baseline.py "$D/baseline_rehearsed_$T.json"
  here $R "$D/disagreements_rehearsed_$T.out" measure_bank_disagreements.py --out "$D/disagreements_rehearsed_$T.txt"
  echo "REHEARSED: perform from $D/sheet_$T.md"
)
```

**The block must end `REHEARSED`, or the performance does not start.** The act sheet refuses,
writing nothing, a movement no class takes, a day whose unanswered movements do not add up to
its residue, an export the import would refuse, and a books day the comparison cannot take. The
rehearsal refuses BEFORE any write a copy named `shekel` or one on which any act's pre-state has
moved (the export already imported, a row already matched or no longer Paid, a line gone), and
an act sheet that holds back a transfer other than the one ruling plan#2433 names, or matches a
settled transfer no ruling has rehearsed: **once plan#2436 has performed the Van Loan match,
`HELD_BACK` in `tests/manual/reconcile_post_state.py` changes with it**. After each act it reads
the database to see the act LANDED, because the Reconcile page answers a refused card with a
200; after the last it grades the post-state and the done test. The check is that grading again
on a READ-ONLY connection, and must end `check complete; every connection was read-only`.

**Read the sheet's header line**: `app` must be `$D/app_$T/app`. A different folder means the
rehearsal ran some other `app/` than production's.

---

## The stop rules

**Perform every act in ONE sitting, in the sheet's order, and finish each act before the next.**

* **Inside a cancel: a save, then a press.** Between them the row is Projected: its cell shows a
  check-mark button on hover, and its reopened card a green **Paid** beside the red **Cancel**.
  **Press neither the check mark nor Paid**: each marks the row Paid again, dated today. Click
  the row's amount to reopen the card, and press **Cancel**.
* **Inside a delete under a Paid envelope: four presses.** Between the third (Paid) and the
  fourth (the day typed back) the envelope stands settled on TODAY for its whole figure; between
  the first and the third it is Projected, with the same check-mark button not to press, and its
  card's red **Cancel** not to press either: it cancels the whole envelope.
* **One Reconcile card per Apply.** The rehearsal applied each card alone, and the receipt then
  speaks for that one card.
* **Do not explain anything else on the Reconcile page**, including the lines the sheet leaves
  for you (below), until the check has passed.

**A refused save wrote nothing, and every save before it stands. Stop and read it.** It shows as:

* on the import page, a RED flash;
* on the Reconcile page, the receipt at the top reading `0 applied, 1 refused` on an amber
  border. A good Apply reads `1 applied` on a green border;
* on a grid card, NO flash: the card closes as after a good save, and the CELL redraws in red
  with a small octagon icon, its reason in the cell's tooltip. **After every save on a grid card,
  look at the cell**;
* on a purchase's X, the purchase still in the Purchases list after the confirm.

**If the refused save is the day typed back on a reopened envelope** (a delete's fourth press),
that envelope stands settled on TODAY: perform no other act, because that remedy has not been
rehearsed.

---

## Finding things on screen

* **The grid opens on the account Settings > General > Default Grid Account names**, which is
  Checking on the copies measured. Every cancel and delete is on Checking's grid.
* **A grid column is a pay period, headed by its first day.** The arrow left of the window
  ("Show earlier periods") steps back one paycheck at a time; the books span reaches back to
  March 2026. **Find a row by its column, not by the day on the sheet's heading**: the heading
  gives the day its money moved, and a row's column is the paycheck it was budgeted in, which
  can be another. The sheet's `FIND` line names the column ("the paycheck starting ...") and the
  row.
* **Clicking a row's amount opens its card**, the full edit. On a Paid row it shows **Status**
  and **Money moved on**. On a Projected row it shows a green **Paid** and a red **Cancel** on its
  top row, and Status only under **More options**, which no act here opens: the acts press those
  two buttons instead. An envelope's card also lists its purchases under **Purchases**, each with
  its day bought (month/day), its description and its figure (unsigned); under a settled
  envelope the list renders no X and says to set Status to Projected first.
* **The Reconcile page** is the **Reconcile** button on Checking's Import statements page
  (Accounts, Checking, **Import statements**). Its tabs are To explain, Explained, Filed by
  rules, Transfers and Skipped; the sheet's `FIND` line names the card's tab. A card is SECU's
  line: its day, description and figure, as the act's heading gives them. A card's own tabs
  name what the line is (MATCH among them); MATCH lists the rows the line could be, each with a
  checkbox.

---

## The acts

Each act is headed as the sheet heads it.

### The import (act 1)

Accounts, Checking, **Import statements**. **Export kind**: *SECU checking -- CSV export*.
**Statement file**: SECU's export from the handoff folder, the file the sheet names. **Import**.

*Success:* a green flash beginning "Recorded", whose counts are the ones the sheet's act 1
records the rehearsal's flash saying: the new lines, the lines already known, and what your
standing rules filed. *Why:* the export holds lines no earlier import recorded, among them the
days after the last statement, and the done test is read after an import covering the books'
last day (ruling plan#2343). The rules filing new swipe lines is the import's own act (ruling
R-GH), and the sheet lists each one.

### A match (TIMING, then TRANSFER)

The Reconcile page, on the tab the sheet's `FIND` line names; find the card of SECU's line by
the act's heading, and open its **MATCH**. Then:

* where the sheet says **the page proposes the row, ticked**, check that the ticked row is the
  one the sheet names and that NO other row is ticked;
* where it says **TICK the row**, tick that row, and no other.

Tick the card's **OK**, and press **Apply** ("Apply ... the cards you OK'd"). *Success:* the
receipt at the top reads `1 applied`. If the page then asks "Should any of
these stand?", press nothing there: it states a standing rule for the future and moves no money.

*Why:* a TIMING pair is a row the app dated otherwise than SECU posted it, and the match moves
the row onto SECU's day; a TRANSFER line is a Projected transfer SECU paid, and the match settles
it on SECU's day.

### A cancel

`FIND` it on Checking's grid. Click its amount, set **Status** to **Projected**, **Save**. The
card closes and the cell shows a check-mark button: **do NOT press it**. Click the amount again,
and press the red **Cancel** on the card's top row, **not** the green **Paid** beside it.

*Success:* the grid reloads, and the row's cell is gone from its column (the grid draws no
Cancelled row). *Why:* neither export holds it, so its money never moved (ruling plan#2359); the
Cancel door takes only a Projected row, so a Paid row goes back to Projected first.

### A delete

`FIND` the envelope on Checking's grid and click its amount.

* **Under a Paid envelope** (the sheet's act says TYPE Status: Projected first): set **Status**
  to **Projected**, **Save**; click the amount again. Under **Purchases**, press the **X** of the
  purchase the sheet names, and confirm "Delete this entry?". Click the amount again and press
  the green **Paid** on the card's top row, **not** the red **Cancel** beside it; the cell
  redraws as settled. Click the amount again, set **Money moved on** to the day the sheet gives,
  **Save**.
* **Under an envelope still Projected**: press the X of the purchase the sheet names, and
  confirm.

*Success:* the purchase is gone from the list, and a reopened envelope reads Paid with Money
moved on the sheet's day. *Why:* the line is a purchase neither export holds (ruling plan#2359),
and under a Paid envelope it comes off by ruling plan#2439's round trip: measured 2026-10-31,
with the day typed back it ends exactly where a delete of the line alone does, and moves no other
line.

---

## What the sheet leaves for you

**Lines past the books' last day.** The import records SECU's lines up to the export's last day,
and the books end at the latest balance's day, so some new lines are no act's. The sheet lists
them under "Left for you on the Reconcile page" (4 on the dump of 2026-10-30). Explain them as
any reconcile is explained, **after** the check has passed.

---

## After: what to check

**On the rehearsal (before anyone clicks)**, the REHEARSAL block has written, beside the sheet:
the check of the rehearsed copy, the disagreement list before (`disagreements_before_$T.txt`)
and after (`disagreements_rehearsed_$T.txt`), and every balance figure after
(`baseline_rehearsed_$T.json`). The list after shows the books span apart only on the days the
sheet's last two sections name.

**On production, after the last act (the same day as the rehearsal).** Paste the SETUP block if
this terminal does not still hold it, set `T` to the stamp in the rehearsal's sheet name, then
paste the POST block. It compares against FILES the rehearsal wrote, never a live copy, and
rebuilds `$P` only.

```bash
T=STAMP                                # from the sheet name of the rehearsal: sheet_STAMP.md
( set -e
  [ "${T%%_*}" = "$(date +%F)" ] || { echo "STOPPED: T=$T is not a rehearsal of today's"; exit 1; }
  for f in revision_$T.txt act_sheet_$T.json sheet_$T.md snapshot_$T.json check_rehearsed_$T.out \
           baseline_rehearsed_$T.json disagreements_rehearsed_$T.txt; do
    [ -f "$D/$f" ] || { echo "STOPPED: no $f in $D"; exit 1; }; done
  [ "$(tail -n 1 "$D/check_rehearsed_$T.out")" = "check complete; every connection was read-only" ] ||
    { echo "STOPPED: the rehearsed copy's check did not complete"; exit 1; }
  REV=$(docker inspect shekel-prod-app --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  [ "$REV" = "$(head -n 1 "$D/revision_$T.txt")" ] ||
    { echo "STOPPED: production now runs $REV, not the revision rehearsed"; exit 1; }
  echo "SAME CODE: production still runs $REV"
  dump_prod "$D/prod_after_$T.dump"
  restore $P "$D/prod_after_$T.dump"
  prod $P "$D/check_performed_$T.out" reconcile_rehearsal.py --check --clone $P --account $A \
        --acts "$D/act_sheet_$T.json" --secu "$D/$SECU" --snapshot "$D/snapshot_$T.json" || true
  prod $P "$D/baseline_performed_$T.out" verify_balance_baseline.py "$D/baseline_performed_$T.json"
  here $P "$D/disagreements_performed_$T.out" measure_bank_disagreements.py --out "$D/disagreements_performed_$T.txt"
  if diff "$D/check_rehearsed_$T.out" "$D/check_performed_$T.out"; then echo "CHECK AS REHEARSED"
  else echo "CHECK DIFFERS (above)"; fi
  if cmp "$D/baseline_rehearsed_$T.json" "$D/baseline_performed_$T.json"; then echo "EVERY BALANCE AS REHEARSED"
  else echo "BALANCES DIFFER: compare the two JSON files"; fi
  if diff <(tail -n +4 "$D/disagreements_rehearsed_$T.txt") <(tail -n +4 "$D/disagreements_performed_$T.txt")
  then echo "DISAGREEMENTS AS REHEARSED"; else echo "DISAGREEMENTS DIFFER (above)"; fi
)
```

**All three "AS REHEARSED" lines should print.** The check runs before the two measurements
because those boot the app's development build, which seeds the reference tables at boot; the
check opens the copy read-only first.

* **The CHECK** grades the post-state on production's copy: every act's own arm again (the
  import by its digest, each match, each Cancelled row, each envelope as it stood with only its
  line gone), every card item and the held-back act unmoved since the snapshot, and the done
  test. A failing arm prints a `FAILED:` line naming its act and the row, line or envelope;
  `CHECK DIFFERS` with no `FAILED:` line is a count that moved (the act markers' count), read
  beside the other two lines. Its passing output names no database, time or id, so two copies
  of the same acts print it identically.
* **The BALANCES** compare every figure the balance seam answers, for every account, with the
  rehearsed copy's: two copies on which the same acts were performed hold the same balances
  (measured, below). A difference names the account where money departed from the rehearsal: a
  check mark pressed, a skipped act, or a change made in the app after the dump. **It cannot
  see a day typed back wrong on a reopened envelope**: that day moves no money, because the
  envelope's purchases carry their own days, so only the CHECK names it (measured, below).
* **The DISAGREEMENTS** compare the list after, below its three header lines (the title, the
  database and the time written), with the rehearsed copy's. It sees only the days the list
  compares, which end at the export's last day; a row settled TODAY by a pressed check mark is
  past them (measured, below).

A failed line is read before anything else is done. **No remedy is rehearsed**: report what
failed, with the three outputs, before performing anything more.

---

## What this does NOT do

* **The card days stay apart** until step plan#2348 reconciles them through the card account,
  after the card cutover (ruling plan#2343); its done test is `SpanAgreement.reconciles` over the
  whole span.
* **2026-04-22 and 2026-04-23 stay apart by the Van Loan payment** until step plan#2436 lets the
  loan's books open earlier and performs that match (ruling plan#2433).
* **This card's own code is not what the owner performs on.** The rehearsal and the check run
  production's `app/`, as deployed; the Reconcile page's refusal of Done while records run past
  the last statement (ruling plan#2346, finding BAL-651) ships with plan#1192's pull request.
* **The lines past the books' last day** are left for the owner, above.

---

## How this was rehearsed

Through production's own doors (`fc9dcfba7`, the deployed revision), on restores of production's
dump of 2026-10-30 15:24, by `tests/manual/reconcile_rehearsal.py` (task plan#2414: each act
fetches the page the owner opens, changes only what the owner types, and submits the rest as
rendered; the post-state and the check, task plan#2416). Its door-driving half is
`tests/manual/door_rehearsal.py`, from the account-10 repair's retired rehearsal.

* **2026-10-31**, the whole act sheet: the import and all 22 non-card acts landed, the post-state
  held, and the done test held, with 30 books days still apart, each by exactly the card items
  and the held-back act on it. The read-only check of that copy completed.
* **2026-10-31 08:52, this file's own blocks**, extracted verbatim and run under zsh from
  `card/1192`, with two stand-ins for production's dumps: the CODE block's dump was the dump of
  2026-10-30, and the POST block's after-dump was a dump of a copy the rehearsal had performed
  every act on earlier that day, standing in for the owner's performance. Every step exited 0;
  the block printed `REHEARSED`, `SAME CODE`, and all three "AS REHEARSED" lines, so two copies
  rehearsed apart hold the same check output, the same balances byte for byte, and the same
  list. The sheet's acts were the earlier run's, act for act. **Two controls, each through the
  owner's own doors on a restore of that after-dump, then the POST block's three comparisons**:
  an envelope's day typed back one day early failed the check on that envelope alone, and moved
  neither the balances nor the list; and a cancelled row set back to Projected with its check
  mark pressed (Paid, today) failed the check on that cancel and the balances on Checking, and
  did not move the list.
* **2026-10-31, the buttons a Projected row's card shows** (task plan#2451): the card folds
  Status under More options and shows a green Paid and a red Cancel instead, so each cancel's
  second press and each Paid envelope's re-close are made by those buttons. On a fresh restore of
  the same dump, the whole act sheet so performed printed the earlier rehearsal's check output
  exactly, and every balance figure was byte-identical to its baseline.
* **Two measured traps the harness answers**: the rate limiter answered the rehearsal's 31st
  full-edit open inside a minute with a 429 (a person's pace never reaches it, and the rehearsal
  turns it off: `door_rehearsal.connect`), and the Reconcile page answers a refused card with a
  200 (the rehearsal reads the database after every act).
