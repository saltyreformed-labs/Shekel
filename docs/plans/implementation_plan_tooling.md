# The tooling arc: what builds, ships, tracks and guards the app

**The arc for the work that is not one of the money arcs' arguments.** That work was filed under
`balance`, which made `balance` the catch-all, until ruling plan#2179 gave it an arc of its own on
2026-10-29 (plan#2227 moves its cards here), in the developer's words: "I also think there needs to
be an arc for security and tooling that isn't balance and move those type of cards to the new arc. I
don't want balance to be the catch-all." Each step, finding and ruling is a card in the tracker,
`saltyreformed-labs/shekel-plan` (`CLAUDE.md`, Development Status), whose board holds the order, and
what "done" means is `verification.md`.

**This document states the arc's scope and nothing else.** The repository is public, so no open
finding's detail is written here: that lives on its card in the private tracker, and a security
audit goes to a private repository of its own (ruling plan#2178).

## What belongs here

A card belongs to `tooling` when its work serves the project as a whole, or every arc alike, rather
than one arc's argument:

- **The tracker and its tools**: quill (`tools/quill/`), the tracker's setup, the commit hook, the
  landing check, the lane tool, and the timers that run `quill sync` and `quill watch`.
- **CI and the gates**: the workflows, the pylint floor and the custom checkers' machinery, the
  pre-commit and Claude Code hooks, and the skills, agents and rules sessions work by.
- **The test infrastructure**: `scripts/test.sh`, the test database image and its bake, and the
  harness a test runs in, as distinct from what a test asserts about the app.
- **Deploy and the host**: the images, compose files, nginx, cloudflared, backups, the deploy
  scripts and the release tool.
- **Dependencies**: `requirements*.txt`, the Python version, and the base images.
- **The development environment**: worktrees, virtualenvs and the dev server.
- **Security work that no other arc's argument covers**: the host, the image, the network in front
  of the app, sign-in and sessions.
- **App-wide code that holds no one arc's rule** (ruling plan#2292): what every arc's code stands
  on, such as how a submitted form or query string is parsed, how a template renders, the reference
  cache and its seeds, how a fresh database is built, package privacy, the integrity sweep, who an
  audit row names as its author, the shared value types, and the honesty of the test suite as a
  whole.

## What stays in another arc

A card goes where its work's reason lives. A test, a checker or a fence built to hold one arc's rule
serves that arc's argument and stays in it, even though it is code outside `app/`: a gate that
guards the balance seam is `balance`'s. Security work inside an arc's own subject, such as how the
bank importer reads a statement, stays in that arc too.

## The `security` label

**Security is a label** (ruling plan#2179), **not an arc, because it cuts across arcs**: it marks
security work wherever its card sits, a `bank_import` card as readily as a `tooling` one. A person
adds or removes it with `quill label plan#N --add security`, and files a step carrying it with
`quill file step --label security` (ruling plan#2221). A card moves between arcs with
`quill arc plan#N --to ARC`.
