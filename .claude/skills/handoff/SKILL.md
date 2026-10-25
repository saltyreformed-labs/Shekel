---
name: handoff
description: >
  A lane's stop at a boundary: commit and push what is clean, rewrite the handoff's
  section 0 as the next session's whole prompt, refresh the memory hook, have the
  handoff-checker agent grade it against git, then alert Josh with the line to paste.
  Invoke at every leaf boundary and at every forced stop.
---

# /handoff: stop clean at a boundary

Card `plan#9` (X-cx L9b). Josh clears a session at a boundary and starts a fresh one, because a
filling context degrades (his rule, 2026-09-18). The handoff's section 0 is the fresh session's
WHOLE prompt; nobody writes it a separate one (R-BAL282). `/charter` item 4 says when to stop;
this says how.

## 1. Stop at a clean boundary

A clean boundary is a leaf committed and reviewed, or a design loop closed (every fork ruled or
refused, each ruling written in the handoff). Let a task already running finish (a suite, a
review); start nothing new. A stop forced mid-leaf commits what is clean and says in s.0 what is
not.

## 2. Commit and push

Commit what is clean. A task this session finished (a piece inside its card's one pull request,
R-BAL386) has its file `plan/tasks/T` in the commit that finished it:
`python -m tools.quill.quill task-file plan#T` writes it on `card/<its card>`, and the file goes in
with the piece. Then read whether a pull request from the branch is open; a push would join
it, so an open one means do NOT push, and s.0 says so:

```bash
b=$(git symbolic-ref --short HEAD) || exit 1
gh pr list --head "$b" --state open; echo "exit=$?"
```

Nothing listed: push the branch by an explicit refspec (a pushed branch is a backup, not a request
to merge: CLAUDE.md, Multi-session operation). The block refuses a detached HEAD and `dev` or
`main`, which only the merge flow pushes:

```bash
b=$(git symbolic-ref --short HEAD) && case "$b" in dev|main) echo "not a lane branch: $b"; exit 1;; esac \
    && git push origin "$(git rev-parse HEAD):refs/heads/$b"; echo "exit=$?"
```

A commit made later (a fix the check asks for) is pushed the same way, and s.0's items 1 and 4
re-read from git, before the re-check.

## 3. Rewrite s.0

The file is `~/projects/shekel-handoffs/HANDOFF-<lane>.md`. s.0 is its FIRST section, whose
heading starts `## 0`: write the new one there, headed with the time `date` prints and the
session's name. A superseded s.0 is never deleted (the folder is not in git); it stays below as
history. s.0 holds:

1. The worktree, the branch, HEAD's sha and origin's state (equal, or why not), each read from
   git (`git rev-parse`), never typed from memory. A lane working card N names it as `plan#N`, and
   its branch is `card/N`, the one `quill claim` made.
2. The tree: clean, or each dirty path and why it stays.
3. Every worktree, branch and database clone this session made, a review subagent's worktree and
   its `worktree-agent-*` branch included: removed, or listed with its state and who removes it.
4. What is done, each commit by its sha, and what is not; each task this session finished, as
   `plan#T`.
5. The next session's FIRST act: when `pwd` is not item 1's worktree, `EnterWorktree` into it once
   (Josh cannot relaunch a terminal from the web); then `pwd` and `git rev-parse --show-toplevel`
   print item 1's worktree, `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD` print its
   branch and sha, and `git status --porcelain` prints only item 2's paths; any difference is a STOP
   to report. Then the next leaf and the first command it runs.
6. What the next session must not do (push while a pull request is open, say), and the traps
   learned or review findings that recurred.

The checker grades items 1, 2, 3 and 5 against git, and every commit and task file item 4 cites;
items 4 and 6 are otherwise the lane's judgment. Nothing in s.0 asks Josh for anything: a question he must answer is asked
before the stop, by AskUserQuestion (CLAUDE.md, Multi-session operation).

## 4. Refresh the memory hook

The lane's memory file (`~/.claude/projects/-home-josh-projects-Shekel/memory/project_*.md`) names
this handoff and its s.0 in its `hook:` line. Then regenerate the index:

```bash
python3 ~/.claude/projects/-home-josh-projects-Shekel/build_memory_index.py; echo "exit=$?"
```

Exit 0. It refuses an index over its byte budget: shorten the hook first.

## 5. The check

Spawn the `handoff-checker` agent: a fresh agent by that name, never a fork, and with no
`isolation`: it reads the lane's own worktree and writes nothing, and the harness refuses an
isolated agent's `git -C` into any other worktree (measured 2026-10-14). Give it the handoff's path,
the worktree's path, the memory file's path, and the list of every worktree, branch and database
clone this session made, taken from the session's own tool results. Its report opens with the
worktree, its toplevel and its HEAD; a report without them is VOID: run it once more, and a second
VOID is a failed check. PASS ends the check. A list: fix each item, then run it ONCE more; a second
list is a failed check.

## 6. The alert

The line to paste, with HEAD's short sha and the session's name (`ListAgents` prints it):

```text
<session> stopped clean at <sha>, pushed, check passed. /clear, then: Read and follow ~/projects/shekel-handoffs/HANDOFF-<lane>.md s.0
```

- A forced stop mid-leaf reads `stopped mid-leaf`; an unpushed branch reads `not pushed`; a
  failed check reads `CHECK FAILED`. Why goes in the session's output, never in the line, which
  stays under PushNotification's 200 characters.
- Send it with PushNotification, then put the same line FIRST, in bold, in the session's last
  output, followed by why it is not pushed and the failed check's items, if any: the tool skips
  the push while Josh's terminal is active, and the output line reaches him either way.
- With a coordinator session active, SendMessage it `handoff written` and the sha.

Then stop.
