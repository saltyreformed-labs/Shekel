---
name: release
description: >
  Ship dev to production on Josh's word: pin the cut, judge it with tools/release, compare
  every stored row and run each money mover's kit on a same-day production dump, ask Josh to
  approve, merge into main, deploy after his "Logged out, deploy now", verify, record and clean
  up. Invoke only when Josh says to release. Not `quill release`, which deletes a card's claim.
---

# /release: ship dev to production

Card `plan#9` (X-cx L9a). The rules are Josh's: he approves each release ONCE, by the approval
question of step 5 (R-BAL217, R-BAL279); a deploy waits for his answer at step 10 (R-BAL279); a
refused release names the rule and offers the newest earlier cut that passes (R-BAL280); a money
mover is rehearsed by its card's kit or refused, and every other change to what the image ships is
NAMED unrehearsed in the approval question until the general kit exists (R-BAL281, the interim as
he confirmed it 2026-10-13). A migration needs no kit: every release runs production's deploy step
and the cut's on the same-day dump and compares every stored row, which rehearses each migration
the cut adds (rulings plan#2873, plan#3200). `tools/release` is this skill's tested half;
everything with a person or a judgment in it is here.

## 0. Before anything

- Run only when Josh says to release.
- Run as a fresh session's whole work in the release worktree, `~/projects/shekel-release`: the one
  worktree that holds `dev`, used for nothing else (plan#1829, plan#1838). Josh starts it with this
  line in a new terminal, which brings its `dev` up to GitHub's BEFORE the session starts, because a
  session reads its hooks from its own worktree when it starts (measured 2026-10-26):

  ```bash
  cd ~/projects/shekel-release && git fetch --prune origin && git merge --ff-only origin/dev && { [ ! -L .venv ] || rm .venv; } && uv sync --locked && claude "/release"
  ```

  It runs this skill to the end and stops. A session building a card does not run it: it gives Josh
  that line and does not start the release. The worktree is made once, by `git worktree add
  ~/projects/shekel-release dev` (git refuses while another worktree holds `dev`), and that line
  keeps its venv its own, on `dev`'s lock (ruling plan#2828): it removes a link an earlier release
  left, as `uv sync` writes through one into the main checkout's venv. Never in `~/projects/Shekel`,
  which stays clean on `main` because production's deploy script is a symlink into it (trap u).
- **The auto-mode classifier can refuse an act outright, even one Josh's word already covers**
  (10-20: inside the approved release, a copy into `/opt/docker/shekel` as 'Production Deploy';
  after it, a repository settings change as 'Permission Grant'; and under his standing merge
  grant, an enqueue into `dev`'s queue as 'Merge Without Review', where the release's own enqueue
  passed). Each refusal is a question to Josh for that one act, its alert first; never route
  around it.
- **Each Bash call is a fresh shell**: no variable or `source` carries to the next call, and the
  working directory may not. So the run keeps its values in ONE file,
  `~/projects/shekel-handoffs/releases/current.sh`; every block below starts by sourcing it and
  moving to the worktree, and each value a step learns is appended to it (a later line wins). Python
  is the venv's by path, `.venv/bin/python`: a bare `python` lacks the app's packages and crashes
  (trap gg).
- **A block refuses a run value it reads but no step wrote.** Its first line names `W` as
  `cd "${W:?}"`, and its second line names every other value it reads, as `: "${R:?}" "${PR:?}"`;
  a block you compose from this skill's prose opens with the same two lines. An empty or missing
  value stops the whole call at that line, naming it (`PR: parameter not set`, exit 1), so nothing
  after it runs. Name values on that line only: inside `$(...)` or a redirection's file name the
  same expansion fails just that one command, and the call runs on. A value is missing when the
  step that appends it has not run, or ran in the same call AFTER the source line (10-14: a `PR=`
  append and step 6's watch block run as one call read an empty `PR`, and `gh pr checks ""` fell
  back to the current branch's pull request): run the appending step in a call of its own first.
  It is never an earlier run's value: every run ends by moving `current.sh` into its record (step
  13), and the start block refuses while one exists (a run that never got there). Two blocks are
  exempt from the first-line rule: the start block and step 13's move, which source no `W`.
- Read every exit status UNPIPED: `cmd > "$R/file.txt" 2>&1; echo "exit=$?"`. A pipe reports its
  last command's status, not yours.
- The long waits (the pull request's checks and main's CI, whose test shards took 8-12 minutes on
  10-13) outrun one Bash call's 10-minute limit: run each in the background and read its file when
  it ends.
- **A watch's exit is not its run's verdict** (finding plan#2950: on 11-05 `gh run watch
  --exit-status` exited 1 on a GitHub 504 while its run went on to pass). Each block that watches
  a run reads the run itself after the watch, as `<status> <conclusion>`: `completed success` is
  green, `completed` with any other conclusion is the run's failure, and anything else (a status
  still running, or no line, the read itself failing) means the watch ended early, so run the
  block again. A pull request's checks are read the same way by `gh pr checks` without
  `--watch`: exit 0 every check passed; 8 one is still pending, the watch having ended early; 1
  one failed, as its file shows, or, with no failing check in it, the read itself failed. On 8
  or a failed read, run the block again.
- Production figures (counts, balances, day counts, dates measured in production) stay out of
  commit messages and rulings (R-BAL132); the record lives outside the repository.
- Each question to Josh is an AskUserQuestion that stands alone (the text before it may never
  render), plain words before ids, and its alert goes first (CLAUDE.md, Multi-session operation).
- A run that ends early after a kit ran (a STOP, or "Not now" at step 5) still runs step 12's
  census (without `--release-branch` when no branch was pushed) and writes step 13's record. A
  kit that FAILED is left out of that census (no `--kit` for it), so its clones and worktrees list
  as `other` and stay for its lane to read; the record names them. A run that stopped after
  opening its pull request names the PR and the release branch in the record and leaves both
  open. Every run, however it ends, ends with step 13's last act: `current.sh` moves into `$R`.

Start the run from the session's worktree (a resumed run, step 1's case, copies its record's
`current.sh` back instead):

```bash
[ ! -e ~/projects/shekel-handoffs/releases/current.sh ] \
    || { echo "an unfinished run: $(head -n 1 ~/projects/shekel-handoffs/releases/current.sh)"; exit 1; }
[ "$(git symbolic-ref --short HEAD)" = dev ] && [ "$(git rev-parse --show-toplevel)" != ~/projects/Shekel ] \
    || { echo "not a dev worktree"; exit 1; }
R=~/projects/shekel-handoffs/releases/$(date +%F)    # -2, -3 for a later release the same day
mkdir "$R" && printf 'R="%s"\nW="%s"\n' "$R" "$(git rev-parse --show-toplevel)" \
    > ~/projects/shekel-handoffs/releases/current.sh
```

## 1. Check production, then pin the cut

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
git fetch --prune origin || exit 1
C=$(sed -n 's/^CONTAINER_NAME="\${SHEKEL_CONTAINER_NAME:-\(.*\)}"$/\1/p' ~/projects/Shekel/deploy/shekel-deploy.sh)
P=$(docker inspect "$C" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
echo "container=$C production=$P main=$(git rev-parse origin/main)"
[ -n "$C" ] && [ -n "$P" ] || { echo "no container or no label"; exit 1; }
if [ "$P" != "$(git rev-parse origin/main)" ]; then
    git merge-base --is-ancestor "$P" origin/main || { echo "production is not in main"; exit 1; }
    git diff --no-renames --name-status "$P" origin/main > "$R/main_beyond_production.txt" || exit 1
    awk -F'\t' 'NF != 2 || $1 != "A" || $2 !~ /^plan\/ships\/[0-9]+$/ {bad=1} END {exit bad}' \
        "$R/main_beyond_production.txt" || { echo "main holds more than added done files"; exit 1; }
    git merge-base --is-ancestor origin/main origin/dev || { echo "dev lacks main"; exit 1; }
fi
printf 'C="%s"\nBASE="%s"\nCUT="%s"\n' "$C" "$P" "$(git rev-parse origin/dev)" \
    >> ~/projects/shekel-handoffs/releases/current.sh; echo "pinned=$?"
```

Production's image names the commit it was built from, `P`. Only when it equals `origin/main`, or
`main` beyond it is a records-only merge that `dev` holds, are the container, the base (always
`P`, what production runs) and the cut appended (`pinned=0`). A records-only merge is R-BAL363's
door as `tools/quill/landing.py` defines it: every entry of `P..origin/main` adds a card's done
file, `plan/ships/<N>`, and nothing else. Josh's answer of 2026-11-04 is to judge such a release
from production (finding plan#2899): `main_beyond_production.txt` is the proof, kept in the
record, and `plan` then reads the cards it records as carried, their work already live. A release
merged into `main` is never one, since each card writes its tasks' files and a release moving
versions alone changes the lock. Otherwise never judge a new release, since `plan` would not see
what `main` holds beyond production:

- An empty `container=` or `production=` (no such container, no label, or a deploy script whose
  `CONTAINER_NAME` line changed): STOP and tell Josh.
- `production is not in main`: STOP and tell Josh.
- `main holds more than added done files`: `main` holds a change production lacks. The cases
  below say which; none matching is a STOP.
- `dev lacks main`: a records-only merge into `main` whose resync into `dev` (CLAUDE.md, Git
  Workflow) has not landed, so step 6 would refuse the cut after the approval. STOP and tell
  Josh: the resync lands first.
- A release whose record ends at "Not now" at step 10: resume it at step 9 by copying its record's
  `current.sh` back.
- The start block refused with `an unfinished run`: a run that never reached step 13 (a lost
  session, say) still holds `current.sh`. Read its record folder and Josh's last answer, then
  resume it at its next step, or end it by step 13. Never delete the file.
- A release whose deploy rolled back or refused (step 11): STOP and tell Josh; it is never re-run
  from here.

The cut is `dev`'s tip, or a cut `plan` offered. Never a feature branch's tip: `plan` offers
earlier cuts from the cut's first-parent line, and only on `dev` is each of those a whole pull
request.

## 2. Judge the cut

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${BASE:?}" "${CUT:?}"
.venv/bin/python -m tools.release.release plan --base "$BASE" --cut "$CUT" \
    > "$R/plan.txt" 2>&1; echo "exit=$?"
```

Its last line is the verdict: `clean (exit 0)`, `refused (exit 1)`, or `approval must name the
above (exit 3)`. Exit 2 judged nothing (the file says why). An exit with NO `verdict:` line is a
crash, which also exits 1: STOP and report it; it is never a refusal and never clean.

Then, line by line:

- `plan` reads the cards through quill's App, so a failed tracker read exits 2. What it carries is
  the change the release makes to the ship markers (`plan/ships/<N>`, R-BAL359), and whether a
  card moves money is its moves-money label (R-BAL287). A card reading `UNDONE` is one whose
  marker the release deletes, judged as a ship is (R-BAL288): a money mover's undo needs its kit
  too. A group's way out is reverting the merge that landed each member it names, never deleting a
  marker by hand (that leaves the code in production, unseen).
- For each card reading `moves money: yes`, find the rehearsal kit its lane wrote as its "Release
  kit" task: card N's is `~/projects/shekel-handoffs/kits/plan-N/` (ruling plan#1980); a kit
  written before that ruling is named by its lane's handoff file,
  `~/projects/shekel-handoffs/HANDOFF-<lane>.md`, or, once the lane posted its first note, the
  same file under `~/projects/shekel-handoffs/archive/` (`/handoff` moves it there). Append each
  directory found to `current.sh` as `KITS="<dir> <dir> ..."`. For each with no kit, re-run with
  `--unrehearsed plan#N`; `plan` then refuses the release for it.
- An `ADDED` migration needs no kit: step 4's row comparison rehearses every one (ruling
  plan#2873). `plan` takes no revision as unrehearsed: given one, it judges nothing (exit 2).
- A refusal naming a card the tracker does not hold, a card that is no work, or an entry under
  `plan/ships/` that is no card's marker (R-BAL287, R-BAL289) is no judgment for this run to make:
  STOP and report it to Josh.
- Once production's tree holds the weekly update's workflow (`.github/workflows/updates.yml`),
  `plan` reads its runs on GitHub, with no credential, and prints `monday update: lock refreshed
  <date> UTC, published by <run>`; a week with nothing to move still counts. None in the 8 days
  before the run refuses the release whatever the cut (ruling plan#2351), so no earlier cut
  answers it. Before `main` holds the workflow there is no such line and no such refusal: GitHub
  runs it only from `main`.

Re-run with every flag at once until nothing more changes; the last run is `plan.txt`.

## 3. A refusal, or two migrations: ask first

The answer moves the cut, so this comes before any rehearsal.

- **The Monday update stopped** (`REFUSED -- no Monday update published ...`): STOP before
  anything else, whatever else `plan` printed. Tell Josh in plain words that the weekly package
  update has not finished a run in 8 days, with the page the line names. The release resumes at
  step 2 once a run has published: fixing what stopped it is a card's work, and then
  `gh workflow run updates.yml --ref main` starts a run.
- **Refused (exit 1):** name the rule and the cards in plain words, then offer the line
  `newest earlier cut that passes`: "Release up to <its subject> (<its cards>); <the rest> goes in
  the next release", or "Stop". When that line says `none`, say no earlier cut passes and the one
  option is to stop. On the offered cut, append `CUT="<its sha>"` to `current.sh` and go back to
  step 2.
- **Two or more `ADDED` migrations:** list each, its revision and in plain words what it changes,
  and ask "Both together", "Cut before the second" (the `newest cut adding at most one migration`
  line; absent or `none`, leave the option out) or "Stop".

## 4. Rehearse: the row comparison, and each money mover's kit

Every release takes the same-day dump and runs the row comparison on it once (ruling plan#3200); a
release carrying a money mover runs that card's kit on the same dump too.

**The same-day dump**, taken by production's own deploy script and nowhere else (never a hand
`docker exec ... pg_dump`):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
shekel-deploy --dump-only > "$R/dump.txt" 2> "$R/dump_err.txt"; rc=$?; echo "exit=$rc"
[ "$rc" = 0 ] && echo "DUMP=\"$(tail -n 1 "$R/dump.txt")\"" >> ~/projects/shekel-handoffs/releases/current.sh
```

On exit 0 its last stdout line is the bare path, appended as `DUMP`. Exit 1 refused (no dump, no
path line); exit 2 bad arguments, which is also what a deploy script older than `--dump-only`
prints. Either is a STOP. A dump taken before Josh's first page load of the day fails a kit's settle
grade by design, as the pay-period window tops up; the kit's first page load grade covers it (trap
i).

**The row comparison**, from production's commit to the cut, on `$DUMP` (its module docstring,
`tools/rehearsal/rows.py`, says what it compares and what it does not):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${DUMP:?}" "${BASE:?}" "${CUT:?}"
.venv/bin/python -m tools.rehearsal.rows --dump "$DUMP" --base "$BASE" --cut "$CUT" \
    --report "$R/rows_report.txt" > "$R/rows.txt" 2>&1; echo "exit=$?"
```

It restores the dump in a cluster of its own, runs base's deploy step on one copy and the cut's on
another, as production's entrypoint runs it, and compares every stored row. It tears all of it down
itself, so it makes nothing step 12 removes. Read `rows.txt`:

- **Exit 2** measured nothing, and `rows.txt` names why (the docstring's exit status lists every
  cause, a teardown that left something among them): STOP and report it.
- **Exit 0 or 1** is a measured result only when `rows.txt` holds the `controls:` line and the
  bench's `teardown:` timing, which prints only once a run returns. An exit without both is a
  crash, whatever its number (a Python traceback exits 1): STOP and report it.
- `cut's deploy refused the copy base's deployed:` (exit 1): production's deploy of the cut would
  refuse too. A line opening `ROLLBACK FAILS` (exit 1): production's rollback from the cut would
  fail. Either is a STOP: tell Josh.
- The rollback's other line: `rollback: base's image knows ...` holds; `... rolling back past this
  release is a restore` goes in the approval question, since undoing the release is then a
  restore of the pre-deploy dump, never a re-pin.
- **Each changed table and column** (`base against cut: <s> s, <n> rows differ`, then a line per
  table and an indented line per column; each row is in `rows_report.txt`) is traced to the change
  in the cut that writes it: an `ADDED` migration in `plan.txt` (its `upgrade()`, `git show
  "$CUT":<its path>`), or a pull request in `production.txt` that changes what the deploy step
  writes (`scripts/init_database.py`: the reference rows `app/ref_seeds.py` seeds, and its three
  deploy hooks with the `app/services` code they call). `public.alembic_version` differs by the
  stamp moving from production's revision to the cut's, exactly when the cut adds a migration. A
  `system.audit_log` row only on cut is the trigger's record of a write the deploy made, traced
  with the table and `row_id` it names. In the report a column one side lacks reads `null` on
  that side, so `7 -> null` can be a column the cut drops and `null -> 6` one it adds and fills
  (its migration says which), and `null -> null` is always one it adds empty or drops empty. A
  table the cut renames reads as rows only on base under the old name and only on cut under the
  new. A day column that `rows.txt`'s `compared, though the server's clock fills them` line
  names, differing because the deploys ran either side of midnight, is that line's false
  difference. A difference no change in the cut explains is a STOP: tell Josh what differs.
- An `ADDED` migration that no line traces to moved no row (a new table, a column on an empty
  table, a constraint): the comparison compares no table shape, so it saw nothing of it, and the
  approval question says so.

The trace, each table and column with the change that explains it, goes in the record's README
(step 13).

**Each money mover's kit**, by the command its README names, on `$DUMP`, its exit read unpiped and
appended:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${DUMP:?}"
echo "kit <its directory>: <the kit's command> $DUMP" >> "$R/rehearsal.txt"
<the kit's command> "$DUMP" >> "$R/rehearsal.txt" 2>&1; echo "KIT_EXIT=$?" >> "$R/rehearsal.txt"
```

One such block per kit, each headed by its directory and command. A rehearsal of
a driver or library change needs each side's own venv; this worktree's holds `dev`'s lock alone
(trap k). A kit that fails is a STOP: the release is not put to Josh as rehearsed; report the
failing grade to the lane.

**What a lane writing a kit is told:**

- A kit is written only for a card that moves money, as its "Release kit" task, which `quill
  claim` files and whose text sets the kit's limits (rulings plan#2860, plan#3201). A migration
  needs none (ruling plan#2873).
- The kit is the directory `~/projects/shekel-handoffs/kits/plan-N/`, N its card (ruling
  plan#1980), which no note or file need name. Its README names what it grades and its
  command, which takes the dump's path and exits 0 only when every grade passes.
- Its FIRST act creates `release-manifest.txt` in that directory (`touch`), and it only ever
  appends to it (`>>`), never truncates (`>`). One line per thing it makes, ending in a newline:
  `worktree <absolute path>`, `branch <name>` (a local branch) or `database <name>` (a clone on
  the dev database server).
- For each thing: check it does not exist yet, APPEND its line, then make it. A thing that already
  exists stops the kit; a make that fails fails the kit. A kit that dies mid-run so leaves a line
  naming something absent, never a thing no line names.
- Names are the kit's own and the release's (e.g. `shekel_rel_<MMDD>_<n>`): the census marks for
  removal whatever a line names EXACTLY, so a line naming a peer's clone would delete it.
- A worktree with a branch checked out is named with that branch, and a branch checked out in a
  worktree with that worktree. A manifest that names the main worktree, the worktree the census
  runs in, `main`, `dev`, the dev database itself or `postgres`, or holds a line that is not one
  of the three shapes (a torn last line included), is refused whole, and its kit's cleanup falls
  to a person.

## 5. Ask Josh to approve

First list what the release changes of what production runs: the cut's tree against the base's,
which is what step 6 releases, read as CI's write rule reads it (`tools/quill/_production.py`: the
files the image holds, by Docker's own reading of `.dockerignore`, and the files outside it that
build or run production), with each pull request's own part of it, apart each file no pull request's
own change made, and each version it moves in the files that pin versions, those outside production
under a heading of their own:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${BASE:?}" "${CUT:?}"
.venv/bin/python -m tools.release.release production --base "$BASE" --cut "$CUT" \
    > "$R/production.txt" 2>&1; echo "exit=$?"
```

Exit 0 ends with its `verdict:` line. Exit 2 listed nothing (the file says why), and an exit with
no `verdict:` line is a crash: either is a STOP.

**Steps before the merge.** A subsection of `docs/runbook.md` 2.6 for a card the release carries may
mark steps as the developer's and as coming before the merge into `main`. Ask Josh to do them, in
plain words with its links, then run its read-back, the block opening with the source line, its
output into `$R/before_merge_<card>.txt`. A read-back that does not hold is a STOP: the question
below is not asked, and nothing merges.

Header "Release ok". The question lists, in plain words with each id after its words:

- each card the release carries, and which moves money;
- each migration it adds, edits or deletes, and what it changes;
- every `APPROVAL MUST NAME` line of `plan.txt`;
- every OTHER change to what production runs, named UNREHEARSED (R-BAL281's interim, read as
  every file production runs: the image's and those outside it that build or run it). Name each
  pull request `production.txt` lists whose change no money mover's kit rehearsed, in plain words
  with how many files it changes (the file, kept in the record, holds each path): the row
  comparison reads what a migration does to stored rows, never the code beside it. Name each file
  under its `changed by no pull request's own change` heading too, saying in plain words what
  `git diff "$BASE" "$CUT" -- <file>` shows the release does to it: a merge's resolution kept or
  dropped a side there, and a hotfix a resync dropped leaves production. This skill keeps no list
  of production's files: its last one missed `docker-compose.yml`;
- every version the release moves, each line under both of `production.txt`'s version headings, in
  plain words: what moves (a package, an image, an action) from which version to which, `none` on a
  side being one added or removed there, and a digest or commit cut to its first 12 characters; past
  what the question holds (the lock's first release adds every package), say how many of each file
  and that `production.txt` lists each. A change moving pinned versions alone lands with no card
  (ruling plan#2351), so no card above names it. Keep the two lists apart and say what each covers
  (neither heading prints when it has none):
  - `versions moved`: production's files, which the app runs or is built from;
  - `versions moved outside production`: the other files `tools/quill/_versions.py` reads for pins
    (ruling plan#2876). A move here changes no file the app runs. A line of a workflow or of this
    repository's own action is what `main`'s own workflows run from the merge;
    `docker-compose.dev.yml`'s is what a development checkout pulls.

  A file under both headings crossed production's line in this release: say that it left or entered
  what production runs, never that its version was removed.

  A release whose verdict reads `0 of production's files change` still comes to this question,
  whether or not it moves a version. Say in plain words that no file of this repository's that the
  app runs changes, and that the run goes on as any other: the merge builds a new image and step 10
  asks him to log out for its deploy, as step 1 refuses the next release until production's image
  names `main`'s commit. Say too that the new image is not the old one byte for byte: each build
  installs Debian's newest system packages (the `Dockerfile`'s `apt-get` lines: OpenSSL, the
  database client and its library) and compiles the database driver against them;
- each card the release carries that `docs/runbook.md` 2.6 holds host steps for, in plain words:
  what they change on the host, and that `shekel-deploy`'s own rollback cannot undo them; and each
  host copy in 2.5's table that `production.txt` lists and no such step copies, which the deploy
  leaves as it is on the host (finding plan#2567);
- the row comparison's verdict, whatever the release carries: how many stored rows change, in
  which tables and columns, each with the change step 4 traced it to, or that no row changes;
  each `ADDED` migration that moved no row, in plain words, with what it changes that the
  comparison cannot see; and, where its rollback line says so, that undoing the release is a
  restore of the pre-deploy dump;
- each money mover's kit verdict, or that no card the release carries moves money.

Options: "Approve this release" or "Not now". "Not now" merges nothing (step 0's early end).

## 6. Merge into main

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${CUT:?}"
echo "B=\"release/$(date +%F)\"" >> ~/projects/shekel-handoffs/releases/current.sh  # -2, -3 later
git merge-base --is-ancestor origin/main "$CUT"; echo "exit=$?"
```

Each command below is a block of its own, opening with the source line and
`: "${CUT:?}" "${B:?}"`. Exit 0: push the cut as the release branch, bracing each variable (trap r):
`git push origin "${CUT}:refs/heads/${B}"`. Exit 1, `main` holds a merge `dev` lacks (trap j):
`git switch -c "$B" "$CUT" && git merge --no-ff origin/main -m "Merge origin/main into $B"`, then
`git diff --quiet "$CUT" HEAD; echo "exit=$?"` must print `exit=0` (the tree is the cut's; any
other exit is a STOP), then `git push origin "$B" && git switch dev`. Branch names are shared
across worktrees: chain with `&&` (trap hh).

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${B:?}"
gh pr create --base main --head "$B" --title "release: $(date +%F)" --body "<plan.txt's carried cards and migrations>"
echo "PR=\"<the number it printed>\"" >> ~/projects/shekel-handoffs/releases/current.sh
```

Then, in the background (a long wait):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${PR:?}"
gh pr checks "$PR" --watch > /dev/null 2>&1; gh pr checks "$PR" > "$R/checks_$PR.txt" 2>&1; echo "exit=$?"
```

Read every check, by the exit step 0 says (8: run the block again). A shard that fails with
nothing in the diff near it (trap l): read the failed
job's log BEFORE any rerun, rerun only the failed jobs (`gh run rerun <run> --failed`), and record
the first attempt. A failure near the diff is a STOP. Then put the pull request in `main`'s merge
queue, the only way into `main`: ruleset `main-queue`, whose bypass list is empty, so it binds
everyone (R-BAL322); `--admin` is refused. The queue runs the checks again on the exact merge
commit it will land, and lands it as a MERGE COMMIT, never a squash or rebase (commits are cited by
sha), one pull request at a time. `--match-head-commit "$H"` makes gh refuse any head but `$H`,
the cut Josh approved, on an enqueue as on a merge (measured 10-20 on a pull request into `dev`: a
wrong head exits 1, `expected head oid does not match the current head oid`, and queues nothing):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${PR:?}" "${B:?}"
H=$(git rev-parse --verify "origin/${B}^{commit}") || exit 1
gh pr merge "$PR" --merge --match-head-commit "$H"; echo "exit=$?"
```

Exit 0 means queued, not merged; a non-zero exit is a STOP, and nothing reached `main`. The queue's
run is a `merge_group` run of `ci.yml` on the branch `gh-readonly-queue/main/pr-<PR>-<sha>`. List
it (it is listed a few seconds after the enqueue: run again until it prints; a pull request queued
a second time lists its earlier run too, so take the newest):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${PR:?}"
gh run list --workflow ci.yml --event merge_group --limit 10 --json databaseId,headBranch,createdAt \
    --jq ".[] | select(.headBranch | startswith(\"gh-readonly-queue/main/pr-${PR}-\")) | \"\(.databaseId) \(.createdAt)\""; echo "exit=$?"
```

Then, `<run>` being that id, in the background (a long wait):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
gh run watch <run> --exit-status > "$R/queue_<run>.txt" 2>&1; echo "watch=$?"
gh run view <run> --json status,conclusion --jq '"\(.status) \(.conclusion)"'
```

Its verdict is the last line, read as step 0 says, never the watch's exit. A failed queue run
takes the pull request out of the queue, and nothing reached `main`: read it as
trap l says above, and queue the pull request again by the merge block (a new run) only when
nothing in the diff is near the failure; otherwise STOP. A green run lands the merge; append its
sha as `M`:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${PR:?}"
[ "$(gh pr view "$PR" --json state -q .state)" = MERGED ] \
    && echo "M=\"$(gh pr view "$PR" --json mergeCommit -q .mergeCommit.oid)\"" \
    >> ~/projects/shekel-handoffs/releases/current.sh; echo "appended=$?"
```

`appended=1` means the pull request is not merged yet: nothing is appended; read again.

## 7. The image build

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${M:?}"
gh run list --workflow docker-publish.yml --branch main --limit 3 --json databaseId,headSha \
    --jq ".[] | select(.headSha == \"$M\") | .databaseId"; echo "exit=$?"
```

It prints the build's run id, the run whose `headSha` is `$M`. Nothing printed means the run is not
listed yet (on 10-14 it was listed about 20 seconds after the merge): run the block again until it
prints one, never taking another run. Then, `<run>` being that id:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
gh run watch <run> --exit-status > "$R/build_<run>.txt" 2>&1; echo "watch=$?"
gh run view <run> --json status,conclusion --jq '"\(.status) \(.conclusion)"'
```

Its verdict is the last line, read as step 0 says. Once it reads `completed success`:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
gh run view <run> --log > "$R/build_log_<run>.txt" 2>&1; echo "exit=$?"
echo "DIGEST=\"$(sed -n 's/.*Image digest: \(sha256:[0-9a-f]\{64\}\)$/\1/p' "$R/build_log_<run>.txt")\"" \
    >> ~/projects/shekel-handoffs/releases/current.sh
```

The digest is the build log's own `Image digest:` line, never the first `sha256:` in the log (on
10-13 that was a Buildx image's pull). Read `current.sh`'s new `DIGEST` line back: an empty one is a
STOP. A failed build is a STOP but for the one case below: production is untouched, but a build
that failed at signing has already pushed an unsigned `:latest`, which only `shekel-deploy`'s
cosign check keeps out of production; say so in the report.

The case: a build that failed before it built anything, on an outside fault (11-05: `Install
Cosign`'s download answered 504, and Josh's answer was to re-run). Read its steps and the failed
step's log first, `<n>` its attempt:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
gh run view <run> --attempt <n> --json jobs \
    --jq '.jobs[] | "\(.name): \(.conclusion)", (.steps[] | "  \(.name)=\(.conclusion)")' \
    > "$R/build_steps_<run>_attempt<n>.txt" 2>&1; echo "exit=$?"
gh run view <run> --attempt <n> --log-failed \
    > "$R/build_failed_<run>_attempt<n>.txt" 2>&1; echo "exit=$?"
```

Re-run its failed jobs once, `gh run rerun <run> --failed`, only when all three hold: `Build and
push image=skipped` (it never ran, so nothing was pushed); the failed step is neither the
`tax-law` job's nor `Is it still before the tax-law refusal?`, which refuse by design; and the
failed step's log shows a server answering a download with a 5xx error (11-05: `curl: (22) The
requested URL returned error: 504`). Then watch it again by the first block; the record keeps the
failed attempt's two files. Anything else, or a second failure, is a STOP.

## 8. Bring production's checkout and dev to main

Production's checkout first: the dry run reads it, and nothing waits on `dev`.

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
[ -z "$(git -C ~/projects/Shekel status --porcelain)" ] \
    && [ "$(git -C ~/projects/Shekel symbolic-ref --short HEAD)" = main ] \
    || { echo "~/projects/Shekel is not clean on main"; exit 1; }
git -C ~/projects/Shekel fetch origin && git -C ~/projects/Shekel merge --ff-only origin/main; echo "exit=$?"
```

`~/projects/Shekel` must be clean on `main` before its fast-forward. A change to `deploy/`, or to
`scripts/_container_lib.sh`, which the deploy script sources from beside it (R-BAL254), is live from
this moment: `shekel-deploy` runs that checkout's copy (trap u).

Then that checkout's venv, at every release: the edit hooks in every worktree (ruling plan#1848) and
the tracker's sync service run it, and each lane has its own for the rest (ruling plan#2828).

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
uv sync --locked --directory ~/projects/Shekel; echo "exit=$?"
```

Where a card this release carries names another sync in `docs/runbook.md` 2.6, that one runs in its
place. Production reads nothing in this venv, so a failure holds nothing of the deploy: name it in
the record (step 13) with uv's own words.

Then `dev`. No one can push to `dev` (ruleset `dev-queue`, whose bypass list is empty, R-BAL322),
so `main` reaches it as a pull request from `main` through `dev`'s merge queue, as CLAUDE.md's Git
Workflow resyncs `dev` (measured on a throwaway repository 10-18: it lands, and `main` is not
deleted):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
gh pr create --base dev --head main --title "Merge main into dev after the $(date +%F) release" \
    --body "The resync of dev after the release (/release step 8)."
echo "RS=\"<the number it printed>\"" >> ~/projects/shekel-handoffs/releases/current.sh
```

Then as step 6 does for `$PR`: watch its checks in the background into `$R/checks_$RS.txt`, queue it
with `gh pr merge "$RS" --merge` (no `--match-head-commit`: whatever `main` holds when it lands is
what `dev` should get), find its queue run on the branch `gh-readonly-queue/dev/pr-<RS>-<sha>` and watch it into
`$R/queue_<run>.txt`. A failed run is read as step 6 reads one: nothing reached `dev`; queue it
again or STOP. Once it is merged, append `RM="<its merge sha>"` as step 6 appends `M`. Neither
wait holds the deploy: go on to step 9, and read both before step 13. A resync that conflicts over
a file the tracker switch deleted is settled by CLAUDE.md's resync rule (Git Workflow): it takes
`dev`'s delete.

## 9. The dry run

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}"
shekel-deploy --dry-run > "$R/dryrun.txt" 2>&1; echo "exit=$?"
```

Read it, not its exit alone:

- `current pin` is production now; `target pin` must be `$DIGEST`, the image step 7 built.
- The stamp line, `database is stamped at: <revision>`, is a real check. With `ADDED` migrations:
  `plan.txt` lists them in PATH order, not chain order, so take the one whose `down_revision`
  (`git show "$CUT":<its path>`) is not itself an `ADDED` revision; the stamp must be that
  `down_revision`. The lines under `MIGRATION-BEARING release` are FILE NAMES: they must be exactly
  the file names of `plan.txt`'s `ADDED` paths. With none, it must read `the target adds no
  migrations`. Any mismatch is a STOP.

## 10. Ask Josh to log out

Header "Deploy". "The release is merged and its image is built. Log out of Shekel in every tab,
then pick 'Logged out, deploy now'; I will tell you when to log back in." Options: "Logged out,
deploy now" or "Not now". On "Not now" nothing deploys and `main` is ahead of production: the
record says so, and the next `/release` stops at step 1 until this one resumes here at step 9.

## 11. Deploy, verify, read main's CI

**Host steps first.** `shekel-deploy` re-pins the image and nothing else, and its rollback restores
the image alone. For each card the release carries that `docs/runbook.md` 2.6 holds host steps for,
run them as 2.6 orders them around the block below, each block a call of its own opening with the
source line, its output into `$R/host_<card>_<step>.txt`, and append the snapshot path a step prints
for its rollback to `current.sh` as `HS`. A step, deploy or verify that fails after them is never
answered by running `shekel-deploy` again: run their rollback only where 2.6 says it applies, then
STOP and tell Josh, as for any rolled-back release; where 2.6 says it does not apply, STOP and ask
Josh, with the ways out 2.6 names, before anything else.

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${C:?}"
date -Iseconds > "$R/deploy_since.txt"
shekel-deploy > "$R/deploy.txt" 2>&1; echo "exit=$?"
~/projects/Shekel/deploy/verify_prod_deploy.sh "$(cat "$R/deploy_since.txt")" > "$R/verify.txt" 2>&1; echo "exit=$?"
docker inspect "$C" --format '{{.Config.Image}} {{.State.Health.Status}} {{.RestartCount}}'
```

- A non-zero deploy rolled back or refused and named its dump: read `deploy.txt` whole, do not
  re-run, and tell Josh.
- Verify exits 0 with `VERIFY OK`; its first line names the container, `$C`. After an in-place
  RESTART (not a fresh deploy) it reads VERIFY FAILED on gunicorn's `Control server error: [Errno
  13]` line (BAL-628): read the ERROR line before treating a failure as real.
- The image reference ends in `$DIGEST`; health `healthy`; restarts 0.
- Then tell Josh to reload and log back in, in one plain sentence of what changed.
- Main's CI (trap v): `gh run list --workflow ci.yml --branch main --json
  databaseId,headSha,attempt`; watch the run for `$M` into `$R/main_ci_<run>.txt` as step 6
  watches its queue run, its verdict the run's own line, read as step 0 says. `gh run list` hides
  a failed first attempt: an `attempt` above 1 is read with `gh run view <run> --attempt 1`.

## 12. Clean up

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${B:?}"
.venv/bin/python -m tools.release.release census --release-branch "$B" --kit <each of $KITS> \
    > "$R/cleanup.txt" 2>&1; echo "exit=$?"
```

With no release branch pushed (step 0's early end), leave out `--release-branch` and `"${B:?}"`.
With kits, the guard names `"${KITS:?}"` too, and each directory gets a `--kit` of its own.

- **Exit 0 or 4:** remove each thing marked `MADE`, and nothing else: a worktree with `git worktree
  remove <path>`; a local branch with `git branch -D <name>`; the release branch on origin with
  `git push origin --delete "$B"` (GitHub usually deleted it at the merge; then `git fetch
  --prune`); a database with `docker exec <container> dropdb -U <user> <name>`, the container and
  user as `docker-compose.dev.yml`'s `db` service names them. Append each command and its exit to
  `cleanup.txt`. A `GONE` line needs nothing.
- **Exit 4** also means the cleanup is NOT proven: a refused kit marks nothing, and nothing it names
  is MADE on another kit's word or on `--release-branch`'s. Send its `named` lines to the kit's lane
  and say so in the record.
- **Exit 2** listed nothing; **any other exit, or no `verdict:` line**, is a crash. Remove
  nothing and report it.
- An `other` line is its owner's, never this session's (trap bb). The dump stays in
  `~/shekel-backups/`, named in the record.

## 13. The record

`$R/README.md`: the base (production's commit), the cut, the release branch, the PR, the merge
sha, the resync PR and its merge sha (`RS`, `RM`), the image digest old -> new, the dry run's
stamp, the dump's path, the host steps run and their snapshot (`HS`), step 4's trace of each changed
table and column, and each verdict with its exit (plan, the row comparison, each kit, the checks,
each queue run, the build, each host step, the dry run, the deploy, verify, main's CI, the census);
a run that ended early says where and why, and names what it left open. Beside it:
`main_beyond_production.txt` (when step 1 wrote it), `plan.txt`, `production.txt`, `dump.txt`,
`dump_err.txt`, `rows.txt`, `rows_report.txt`, `rehearsal.txt` (when a kit ran), each
`host_<card>_<step>.txt`,
`checks_<PR>.txt`, `checks_<RS>.txt`, each
`queue_<run>.txt`, `build_<run>.txt`, `build_log_<run>.txt`, each `build_steps_<run>_attempt<n>.txt`
and `build_failed_<run>_attempt<n>.txt` (a build re-run), `dryrun.txt`, `deploy_since.txt`,
`deploy.txt`, `verify.txt`, `main_ci_<run>.txt`, `cleanup.txt`.

The last act of every run, however it ended, moves `current.sh` into the record, so no later block
reads this run's values: each sources a file that is gone and stops (`exit 1`). A resumed run
(step 1) copies it back.

```bash
. ~/projects/shekel-handoffs/releases/current.sh || exit 1
: "${R:?}"
mv ~/projects/shekel-handoffs/releases/current.sh "$R/current.sh"; echo "exit=$?"
```
