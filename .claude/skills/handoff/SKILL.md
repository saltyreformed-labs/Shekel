---
name: handoff
description: >
  A lane's stop at a boundary: commit and push what is clean, write the stop's note (only
  what git and the card cannot say), have the handoff-checker agent grade it against git and
  the card,
  post it with `quill note` on the task the next session starts, refresh the memory hook,
  then alert Josh with the line to paste, `Resume plan#T`. Invoke at every leaf boundary
  and at every forced stop.
---

# /handoff: stop clean at a boundary

Card `plan#9` (X-cx L9b); the note left its file for the card in plan#1861 (rulings plan#1921,
plan#1922). Josh clears a session at a boundary and starts a fresh one, because a filling context
degrades (his rule, 2026-09-18). The fresh session's WHOLE prompt is `Resume plan#T`, the line
`quill note` prints; nobody writes it a separate one (R-BAL282). It resumes from three things
(`/charter` item 0): the card, whose spec and tasks in order are the plan; git, which says where
the work stands and which tasks are done; and this note, which says only what neither can.
`/charter` item 4 says when to stop; this says how.

## 1. Stop at a clean boundary

A clean boundary is a piece finished (its task's file committed), or a design loop closed (every
fork ruled or refused, each ruling filed as a card and the spec edited to it). Let a task already
running finish (a suite, a review); start nothing new. A stop forced mid-piece commits what is
clean, and its note says what of the piece is not.

The note goes on the card the lane claimed, `card/N`. A lane on any other branch claims its card
first: `python -m tools.quill.quill claim plan#N --here` makes `card/N` at the work checked out.

## 2. Commit and push

Commit what is clean. A task this session finished (a piece inside its card's one pull request,
R-BAL386) has its file `plan/tasks/T` in the commit that finished it:
`python -m tools.quill.quill task-file plan#T` writes it on `card/<its card>`, and the file goes in
with the piece. Then read whether a pull request from the branch is open:

```bash
b=$(git symbolic-ref --short HEAD) || exit 1
gh pr list --head "$b" --state open; echo "exit=$?"
```

Push the branch by an explicit refspec. With nothing listed, the push is a backup, not a request to
merge (CLAUDE.md, Multi-session operation). A listed pull request is the lane's own and the push
joins it: when the push took it out of the merge queue, queue it again. The block refuses a
detached HEAD and `dev` or `main`, which only the merge flow pushes:

```bash
b=$(git symbolic-ref --short HEAD) && case "$b" in dev|main) echo "not a lane branch: $b"; exit 1;; esac \
    && git push origin "$(git rev-parse HEAD):refs/heads/$b"; echo "exit=$?"
```

The push comes before the note: `quill note` reads from `origin` which task the next session
starts. A commit made later (a fix the check asks for) is pushed the same way before the re-check.

## 3. Write the note

A file in the session's scratchpad, outside every checkout. It holds only what git and the card
cannot say, each in a line or two:

1. The tree, when `git status --porcelain` prints anything: each path and why it stays. HEAD, when
   the push failed: why it is not pushed.
2. Every worktree, branch and database clone this session made, a review subagent's worktree and
   its `worktree-agent-*` branch included, and everything else it made outside git (a file on the
   host, a scratch card on a throwaway board): removed, or listed with its state and who removes
   it. And each thing the note this session resumed from (`quill show plan#T`, T the task its
   prompt named) listed as left standing: carried forward the same way, removed or listed again,
   since `quill show` prints only the newest note and no later session reads an older one.
3. What was not measured, and, at a stop mid-piece, what of the piece in progress is not built.
4. Traps: what the next session must not do (edit a file a peer's open card changes, say), and
   what this session learned the hard way or its review found recurring.

It never says what git or the card says: where the work stands (the branch, its sha, what is
pushed), what is done or next (the card's tasks, each done once git holds its file), a list of
pieces (a piece added or split is a task: `/charter` item 4), or the next session's first act
(`/charter` item 0). A decision made at the build goes in the piece's commit message, for the
review to grade; a ruling is a card. Nothing in it asks Josh for anything: a question he must
answer is asked before the stop, by AskUserQuestion (CLAUDE.md, Multi-session operation).
`quill note` refuses a note over 2,000 characters (ruling plan#1922).

## 4. The check

Spawn the `handoff-checker` agent: a fresh agent by that name, never a fork, and with no
`isolation`: it reads the lane's own worktree and writes nothing, and the harness refuses an
isolated agent's `git -C` into any other worktree (measured 2026-10-14). Give it the note's path,
the worktree's path, the card (`plan#N`), each task this session finished (`plan#T`), and the list
of every worktree, branch and database clone this session made, taken from the session's own tool
results. Give it the card too, and the note this session resumed from: the checker runs no quill
command, so save `quill show plan#N` and, when the session's prompt was `Resume plan#T`, `quill
show plan#T` each to a file in the scratchpad and give it their paths. Its report opens with the
worktree, its toplevel and its HEAD; a report without them is VOID: run it once more, and a second
VOID is a failed check. PASS ends the check. A list: fix each item, then run it ONCE more; a second
list is a failed check, and its items go in the note's last lines, so the next session reads them.
When they would take the note past 2,000 characters, cut its other lines first, then each item to
its number and what differs.

## 5. Post the note

```bash
python -m tools.quill.quill note plan#N --body-file NOTE; echo "exit=$?"
```

Exit 0 posts it as a comment of quill's on the task card N's next session starts (the one in
progress, else the first open one in N's order that git does not say is done, else N) and prints
`the next session's prompt: Resume plan#T`. It refuses, writing nothing, a note over 2,000
characters and a card `origin` holds no claim of. A note is never edited: the next stop posts its
own, and `quill show plan#T` prints only the newest in full.

Once the card's pull request has landed, `origin` holds no claim of it, so `quill note` refuses
and no session resumes the card. Then nothing is posted: remove what the session and the note it
resumed from left standing, say in the session's last output what could not be removed and the
check's items, and the memory hook (step 6) and the alert (step 7) say the card landed.

A lane that kept a handoff file before plan#1861 moves it into the archive once its first note is
posted, so no session follows its stale section 0:
`mv ~/projects/shekel-handoffs/HANDOFF-<lane>.md ~/projects/shekel-handoffs/archive/`.

## 6. Refresh the memory hook

The lane's memory file (`~/.claude/projects/-home-josh-projects-Shekel/memory/project_*.md`) names
its card and the prompt step 5 printed, `Resume plan#T`, copied from that line, in its `hook:`
line (or that the card landed, when step 5 posted nothing), never a handoff file. Then regenerate
the index:

```bash
python3 ~/.claude/projects/-home-josh-projects-Shekel/build_memory_index.py; echo "exit=$?"
```

Exit 0. It refuses an index over its byte budget: shorten the hook first.

## 7. The alert

The line to paste, with HEAD's short sha, the session's name (`ListAgents` prints it) and the
prompt step 5 printed:

```text
<session> stopped clean at <sha>, pushed, check passed. /clear, then: Resume plan#T
```

- A forced stop mid-piece reads `stopped mid-leaf`; an unpushed branch reads `not pushed`; a
  failed check reads `CHECK FAILED`; a stop after the card's pull request landed ends
  `plan#N landed` in place of `/clear, then: Resume plan#T`. Why goes in the session's output,
  never in the line, which stays under PushNotification's 200 characters.
- Send it with PushNotification, then put the same line FIRST, in bold, in the session's last
  output, followed by why it is not pushed and the failed check's items, if any: the tool skips
  the push while Josh's terminal is active, and the output line reaches him either way.

Then stop.
