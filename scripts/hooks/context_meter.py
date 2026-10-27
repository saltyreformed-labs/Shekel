#!/usr/bin/env python3
"""PostToolBatch: tell the session when its context passes 33%, 45% and 65%.

A building session stops at about a third of its context (the developer,
2026-10-09).  Until the coordinator session retired (plan#10), it read every
lane's transcript with a script of its own and told each lane when; no
session saw its own figure.  Now each session hears it from this hook
(ruling plan#1834, card plan#1823), once per line, with the project's rule
for that line:

* 33%: the session finishes the work in flight, then runs ``/handoff``;
* 45%: it starts no new build loop;
* 65%: it commits what is clean and stops.

**What it reads: the session's own transcript, and nothing else.**  The hook
input names it (``transcript_path``); the newest file in the transcript's
directory may be a peer's, so the directory is never listed.  The context a
request sent is its assistant record's ``input_tokens`` +
``cache_creation_input_tokens`` + ``cache_read_input_tokens``, and the newest
main-thread (not sidechain) record's sum is the session's context now, of
:data:`CONTEXT_WINDOW`.  The harness writes the transcript asynchronously,
so when a batch's hook runs, the record of the request that issued the batch
may not be written yet: measured 2026-10-27 on Claude Code 2.1.310 across two
probe sessions, it was not in most batches and was in one.  The figure read is
then the request before it.  So a line is announced at the batch its passing
request issued or the batch after; a request that ends its turn with text and
no tool call is read at the next turn's first batch.

**Once per context, and nothing stored.**  Whether a line was announced is
already in the transcript: the harness records each ``additionalContext`` as
an ``attachment`` record of type ``hook_additional_context`` under the event
that returned it (measured the same day).  So the hook reads its own earlier
words back instead of keeping a state file that could disagree with them
(CLAUDE.md rule 14).  It matches those records by their structure, never by a
search of the raw text: the same probe's subagent quoted the hook's words
back to the parent, and a session that reads this file or its tests holds
them too.  Only the records since the newest compaction count: a compaction
keeps the file but starts the context again from a summary, as ``/clear``
does by starting a new file, so a compacted or cleared session hears each
line again when it passes it again.  The answer's record must be written
before the next batch's hook runs; both probes saw it in time, and a late one
would repeat a line, never block.

**PostToolBatch, not PostToolUse.**  The ruling's words are "after each tool
call".  PostToolUse fires once per tool call, concurrently for parallel calls,
so parallel calls would each read the same transcript and announce the same
line.  PostToolBatch fires exactly once after each batch of tool calls, one
call or many, before the next request to the model (Claude Code's hooks
reference), so it runs at the same points without the race.

**Never inside a subagent.**  A subagent's tool batches fire the same hooks,
with ``agent_id`` set and the MAIN session's ``transcript_path`` (measured the
same day).  Its figure would be the parent's and its words would reach only
the subagent, so an input carrying ``agent_id`` gets no answer.

**It fails soft and never blocks.**  A missing, unreadable, empty or torn
transcript, or an input it cannot parse, gets no answer and exit 0.  It never
prints ``decision`` or ``continue`` and never exits 2: those are how a
PostToolBatch hook stops the agentic loop.  Its wiring in
``.claude/settings.json`` bounds a hang with a 10-second ``timeout`` (a run
over the largest transcript on this host, 23 MB, took 34 ms on 2026-10-27)
and states ``onFailure: continue``, so a hook that times out or crashes lets
the session go on (the hooks reference: on a failed or timed-out hook "Claude
Code still carries out the action").

The hook replaces the coordinator's meter, which read every worktree's newest
transcript from outside the repository.  It imports only the standard
library, so any ``python3`` of 3.10 or later on the session's ``PATH`` runs
it, venv or not (this host's is 3.14).
"""

import json
import sys
from collections.abc import Iterator
from typing import NamedTuple

#: The event this hook is wired to in ``.claude/settings.json``.  Named once:
#: its answer carries the name, and the transcript files earlier answers under it.
HOOK_EVENT = "PostToolBatch"

#: The sessions' context window, in tokens (card plan#1823: "of 1,000,000").
CONTEXT_WINDOW = 1_000_000

#: The three usage fields whose sum is the context one request sent.
_CONTEXT_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


class ContextLine(NamedTuple):
    """One line of context use, and the project's rule once a session passes it."""

    percent: int
    rule: str

    @property
    def tokens(self) -> int:
        """The context, in tokens, at which a session has passed this line."""
        return self.percent * CONTEXT_WINDOW // 100

    @property
    def tag(self) -> str:
        """The fixed words that name this line in an announcement.

        They are what an earlier announcement is recognised by, so every
        announcement of the line carries them exactly.
        """
        return f"context passed the {self.percent}% line"


#: The lines, lowest first (ruling plan#1834; card plan#1823's spec).
LINES = (
    ContextLine(33, "the session finishes the work in flight, then runs /handoff"),
    ContextLine(45, "the session starts no new build loop"),
    ContextLine(65, "the session commits what is clean and stops"),
)


def _parse(raw: bytes) -> dict[str, object] | None:
    """Return the JSON object on one transcript line, or None.

    A torn line (the harness may be mid-write), a line nested past Python's
    recursion limit, or any line that is not a JSON object gives None.
    """
    try:
        record = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def _records(lines: list[bytes], marker: bytes) -> Iterator[dict[str, object]]:
    """Yield each line that holds *marker* and parses as a JSON object.

    *marker* is only a prefilter, so a transcript of thousands of records is
    not parsed whole after every batch; each caller still checks the record's
    structure.

    Args:
        lines: The transcript's lines, in the order to read them.
        marker: Bytes every wanted record's line contains.

    Yields:
        Each parsed record, in the order of *lines*.
    """
    for raw in lines:
        if marker in raw:
            record = _parse(raw)
            if record is not None:
                yield record


def since_compaction(lines: list[bytes]) -> list[bytes]:
    """Return the lines after the newest compaction, or all of them if none.

    A compaction keeps the transcript file and restarts the context from a
    summary, and the harness marks it with a ``system`` record of subtype
    ``compact_boundary`` (read from this host's transcripts, 2026-10-27).

    Args:
        lines: The transcript's lines, oldest first.

    Returns:
        The lines written since the session's context last started again.
    """
    for index in range(len(lines) - 1, -1, -1):
        if b"compact_boundary" not in lines[index]:
            continue
        record = _parse(lines[index])
        if (
            record is not None
            and record.get("type") == "system"
            and record.get("subtype") == "compact_boundary"
        ):
            return lines[index + 1:]
    return lines


def newest_context(lines: list[bytes]) -> int | None:
    """Return the context the session's newest main-thread request sent.

    Args:
        lines: The transcript's lines, oldest first.

    Returns:
        The newest main-thread assistant record's input plus cache tokens, or
        None when the transcript holds no such record with a usable usage.
    """
    for record in _records(list(reversed(lines)), b"assistant"):
        if record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        message = record.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            continue
        counts = [usage.get(field, 0) for field in _CONTEXT_FIELDS]
        if all(isinstance(count, int) for count in counts):
            return sum(counts)
    return None


def announced(lines: list[bytes]) -> set[int]:
    """Return the percent of every line this hook already announced.

    Read from the main-thread ``hook_additional_context`` attachments the
    harness filed under :data:`HOOK_EVENT`, the only records it writes for a
    hook's answer; a tool result or message that merely quotes an
    announcement is not one.

    Args:
        lines: The transcript's lines, oldest first.

    Returns:
        The ``percent`` of each line in :data:`LINES` whose tag an earlier
        answer carried.
    """
    seen: set[int] = set()
    for record in _records(lines, b"hook_additional_context"):
        attachment = record.get("attachment")
        if (
            record.get("type") != "attachment"
            or record.get("isSidechain")
            or not isinstance(attachment, dict)
            or attachment.get("type") != "hook_additional_context"
            or attachment.get("hookEvent") != HOOK_EVENT
        ):
            continue
        content = attachment.get("content")
        if not isinstance(content, list):
            continue
        for text in content:
            if isinstance(text, str):
                seen.update(line.percent for line in LINES if line.tag in text)
    return seen


def announcement(tokens: int, due: list[ContextLine]) -> str:
    """Return the words that tell the session it passed each line in *due*.

    Written as statements of fact rather than commands: the hooks reference
    warns that text framed as an out-of-band system command can trip the
    model's prompt-injection defences, and then the session reports the
    words instead of acting on them.

    Args:
        tokens: The session's context now, in tokens.
        due: The lines passed and not yet announced, lowest first.

    Returns:
        One sentence per line, each carrying the line's :attr:`ContextLine.tag`.
    """
    figure = f"{tokens:,} of {CONTEXT_WINDOW:,} tokens"
    return "\n".join(
        f"Context meter (scripts/hooks/context_meter.py): this session's "
        f"{line.tag}, at {figure}. The project's rule at this line "
        f"(ruling plan#1834): {line.rule}."
        for line in due
    )


def main() -> int:
    """Announce each line the session has passed and not yet heard of.

    Returns:
        0 on every path: the hook never blocks the session.
    """
    try:
        hook_input = json.load(sys.stdin)
    except (ValueError, RecursionError):
        return 0
    if not isinstance(hook_input, dict) or hook_input.get("agent_id") is not None:
        return 0
    path = hook_input.get("transcript_path")
    if not isinstance(path, str) or not path:
        return 0
    try:
        with open(path, "rb") as transcript:
            lines = transcript.read().splitlines()
    except (OSError, ValueError):
        return 0
    tokens = newest_context(lines)
    if tokens is None:
        return 0
    done = announced(since_compaction(lines))
    due = [line for line in LINES if tokens >= line.tokens and line.percent not in done]
    if due:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "additionalContext": announcement(tokens, due),
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
