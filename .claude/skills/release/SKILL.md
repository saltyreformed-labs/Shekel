---
name: release
description: >
  Ship dev to production on Josh's word: pin the cut, judge it with tools/release, rehearse
  money movers and migrations on a same-day production dump, ask Josh to approve, merge into
  main, deploy after his "Logged out, deploy now", verify, record and clean up. Invoke only
  when Josh says to release. Not `quill release`, which deletes a card's claim.
---

# /release: ship dev to production

Card `plan#9` (X-cx L9a). The rules are Josh's: he approves each release ONCE, by the approval
question of step 5 (R-BAL217, R-BAL279); a deploy waits for his answer at step 10 (R-BAL279); a
refused release names the rule and offers the newest earlier cut that passes (R-BAL280); a money
mover or an added migration is rehearsed by its lane's kit or refused, and every other change to
what the image ships is NAMED unrehearsed in the approval question until the general kit exists
(R-BAL281, the interim as he confirmed it 2026-10-13). `tools/release` is this skill's tested half;
everything with a person or a judgment in it is here.

## 0. Before anything

- Run only when Josh says to release.
- Run from a session LAUNCHED with `SHEKEL_PR_COORDINATOR=1`; without it every `gh pr create` and
  `gh pr merge` is a permission prompt Josh cannot see from the web: STOP and say so. Work in that
  session's own worktree with `dev` checked out, never in `~/projects/Shekel`, which stays clean on
  `main` because production's deploy script is a symlink into it (trap u).
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
git fetch --prune origin || exit 1
C=$(sed -n 's/^CONTAINER_NAME="\${SHEKEL_CONTAINER_NAME:-\(.*\)}"$/\1/p' ~/projects/Shekel/deploy/shekel-deploy.sh)
P=$(docker inspect "$C" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
echo "container=$C production=$P main=$(git rev-parse origin/main)"
[ -n "$C" ] && [ "$P" = "$(git rev-parse origin/main)" ] \
    && printf 'C="%s"\nBASE="%s"\nCUT="%s"\n' "$C" "$P" "$(git rev-parse origin/dev)" \
    >> ~/projects/shekel-handoffs/releases/current.sh; echo "pinned=$?"
```

Production's image names the commit it was built from, and it must equal `origin/main`; only then
are the container, the base and the cut appended (`pinned=0`). Otherwise never judge a new
release, since `plan` would not see what `main` holds beyond production:

- An empty `container=` or `production=` (no such container, no label, or a deploy script whose
  `CONTAINER_NAME` line changed): STOP and tell Josh.
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

- A step reading `moves money: UNREADABLE` was written already ticked. Read its flag from the
  lane's commit or handoff and re-run with `--declare <arc>:<id>=money@<source>` or
  `--declare <arc>:<id>=none@<source>`, the source being the commit sha or handoff path you read.
  Before the cutover only: after it, every step is a card whose flag is its label (R-BAL287).
- For each step reading `moves money: yes` and each `ADDED` migration, find the rehearsal kit its
  lane wrote (the lane's handoff names the kit's directory), and append each directory found to
  `current.sh` as `KITS="<dir> <dir> ..."`. For each with no kit, re-run with
  `--unrehearsed <arc>:<id>` (a card after the cutover: `--unrehearsed plan#N`) or
  `--unrehearsed <revision>`; `plan` then refuses the release for it.
- After the cutover (the cut holds no `steps.md`), `plan` reads the cards through quill's App, so
  a failed tracker read exits 2. What it carries is the change the release makes to the ship
  markers (`plan/ships/<N>`, R-BAL359). A card reading `UNDONE` is one whose marker the release
  deletes, judged as a ship is (R-BAL288): a money mover's undo needs its kit too. A group's way
  out is reverting the merge that landed each member it names, never deleting a marker by hand
  (that leaves the code in production, unseen). A refusal
  naming an old-form `<arc>:<id>` trailer, a card the tracker does not hold, a card that is no
  work, or an entry under `plan/ships/` that is no card's marker (R-BAL287, R-BAL289) is no
  judgment for this run to make: STOP and report it to Josh.

Re-run with every flag at once until nothing more changes; the last run is `plan.txt`.

## 3. A refusal, or two migrations: ask first

The answer moves the cut, so this comes before any rehearsal.

- **Refused (exit 1):** name the rule and the steps in plain words, then offer the line
  `newest earlier cut that passes`: "Release up to <its subject> (<its steps>); <the rest> goes in
  the next release", or "Stop". When that line says `none`, say no earlier cut passes and the one
  option is to stop. On the offered cut, append `CUT="<its sha>"` to `current.sh` and go back to
  step 2.
- **Two or more `ADDED` migrations:** list each, its revision and in plain words what it changes,
  and ask "Both together", "Cut before the second" (the `newest cut adding at most one migration`
  line; absent or `none`, leave the option out) or "Stop".

## 4. Rehearse (a money mover or an `ADDED` migration)

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

**Each kit**, by the command its README names, on `$DUMP`, its exit read unpiped and appended:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${DUMP:?}"
echo "kit <its directory>: <the kit's command> $DUMP" >> "$R/rehearsal.txt"
<the kit's command> "$DUMP" >> "$R/rehearsal.txt" 2>&1; echo "KIT_EXIT=$?" >> "$R/rehearsal.txt"
```

One such block per kit, each headed by its directory and command. A rehearsal of
a driver or library change needs each side's own venv; the shared venv tracks the dev tip (trap
k). A kit that fails is a STOP: the release is not put to Josh as rehearsed; report the failing
grade to the lane.

**What a lane writing a kit is told:**

- The kit is a directory in the lane's handoff folder. Its README names what it grades and its
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

Header "Release ok". The question lists, in plain words with each id after its words:

- each step the release carries, and which moves money;
- each migration it adds, edits or deletes, and what it changes;
- every `APPROVAL MUST NAME` line of `plan.txt`;
- every declared step, the value declared and its source;
- every OTHER change to what the image ships, named UNREHEARSED (R-BAL281's interim). Find them:
  `git log --first-parent --format='%h %s' "$BASE".."$CUT"` gives one merge per pull request;
  name each whose own diff (`git diff --stat <merge>^1 <merge> -- <paths>`) touches the paths
  `.dockerignore`'s header lists as living in the image, or `Dockerfile`, and that no kit
  rehearsed. Name `deploy/` and `.github/workflows/` changes too: the first change production's
  deploy path, the second the image build that runs on `main`;
- each kit's verdict, or that nothing needed rehearsal.

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
gh pr create --base main --head "$B" --title "release: $(date +%F)" --body "<plan.txt's carried steps and migrations>"
echo "PR=\"<the number it printed>\"" >> ~/projects/shekel-handoffs/releases/current.sh
```

Then, in the background (a long wait):

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${R:?}" "${PR:?}"
gh pr checks "$PR" --watch > /dev/null 2>&1; gh pr checks "$PR" > "$R/checks_$PR.txt" 2>&1; echo "exit=$?"
```

Read every check. A shard that fails with nothing in the diff near it (trap l): read the failed
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
gh run watch <run> --exit-status > "$R/queue_<run>.txt" 2>&1; echo "exit=$?"
```

A failed queue run takes the pull request out of the queue, and nothing reached `main`: read it as
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
gh run watch <run> --exit-status > "$R/build_<run>.txt" 2>&1; echo "exit=$?"
gh run view <run> --log > "$R/build_log_<run>.txt" 2>&1; echo "exit=$?"
echo "DIGEST=\"$(sed -n 's/.*Image digest: \(sha256:[0-9a-f]\{64\}\)$/\1/p' "$R/build_log_<run>.txt")\"" \
    >> ~/projects/shekel-handoffs/releases/current.sh
```

The digest is the build log's own `Image digest:` line, never the first `sha256:` in the log (on
10-13 that was a Buildx image's pull). Read `current.sh`'s new `DIGEST` line back: an empty one is a
STOP. A failed build is a STOP: production is untouched, but a build that failed at signing has
already pushed an unsigned `:latest`, which only `shekel-deploy`'s cosign check keeps out of
production; say so in the report.

## 8. Bring production's checkout and dev to main

Production's checkout first: the dry run reads it, and nothing waits on `dev`.

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
[ -z "$(git -C ~/projects/Shekel status --porcelain)" ] \
    && [ "$(git -C ~/projects/Shekel symbolic-ref --short HEAD)" = main ] \
    || { echo "~/projects/Shekel is not clean on main"; exit 1; }
git -C ~/projects/Shekel fetch origin && git -C ~/projects/Shekel merge --ff-only origin/main; echo "exit=$?"
```

`~/projects/Shekel` must be clean on `main` before its fast-forward. A `deploy/` change is live from
this moment: `shekel-deploy` runs that checkout's copy (trap u).

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
wait holds the deploy: go on to step 9, and read both before step 13.

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
  databaseId,headSha,attempt`; watch the run for `$M` into `$R/main_ci_<run>.txt` with
  `--exit-status`, exit read. `gh run list` hides a failed first attempt: an `attempt` above 1 is
  read with `gh run view <run> --attempt 1`.

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
stamp, the dump's path, and each verdict with its exit (plan, each kit, the checks, each queue run,
the build, the dry run, the deploy, verify, main's CI, the census); a run that ended early says
where and why, and names what it left open. Beside it: `plan.txt`, `dump.txt`, `dump_err.txt` and
`rehearsal.txt` (when rehearsed), `checks_<PR>.txt`, `checks_<RS>.txt`, each `queue_<run>.txt`,
`build_<run>.txt`, `build_log_<run>.txt`, `dryrun.txt`, `deploy_since.txt`, `deploy.txt`,
`verify.txt`, `main_ci_<run>.txt`, `cleanup.txt`.

The last act of every run, however it ended, moves `current.sh` into the record, so no later block
reads this run's values: each sources a file that is gone and stops (`exit 1`). A resumed run
(step 1) copies it back.

```bash
. ~/projects/shekel-handoffs/releases/current.sh || exit 1
: "${R:?}"
mv ~/projects/shekel-handoffs/releases/current.sh "$R/current.sh"; echo "exit=$?"
```
