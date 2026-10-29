"""The context hook tells a session, once each, when it passes 33%, 45% and 65%.

``scripts/hooks/context_meter.py`` is the coordinator's meter moved into the
session it measures (ruling plan#1834, card plan#1823).  Every case here runs
the script the way the harness does -- executed by its path with the hook
input on stdin -- against a transcript built from the record shapes measured
in a live one on 2026-10-27 (Claude Code 2.1.310): the assistant record's
``message.usage``, and the ``hook_additional_context`` attachment the harness
files for a hook's answer.

**Why "once" is graded by replaying the harness.**  The hook keeps no state:
it learns what it already said from the attachment the harness wrote after its
last answer.  So the once-per-line cases append exactly that record, holding
the hook's own words, and run it again.  A hook that matched its words
anywhere in the raw text would pass those cases, so
:class:`TestOnlyTheHarnessRecordCountsAsAnnounced` holds the same words in
records the harness did not write for this hook, and expects the line again.

**Every run's exit code is asserted 0** (:func:`_run`), because a PostToolBatch
hook that exits 2 stops the session's agentic loop: "never blocks" is checked
on every path, not in one case.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "context_meter.py"
SETTINGS = ROOT / ".claude" / "settings.json"


def _assistant(tokens: int, *, sidechain: bool = False) -> dict:
    """An assistant record whose request sent *tokens* of context.

    The split is the measured shape: two input tokens, a cache write, the rest
    cache read.  ``output_tokens`` is not context and is set large so that a
    hook adding it in would cross a line it should not.
    """
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "id": "msg_probe",
            "role": "assistant",
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 1_000,
                "cache_read_input_tokens": tokens - 1_002,
                "output_tokens": 50_000,
            },
        },
    }


def _announcement_record(text: str, *, event: str = "PostToolBatch",
                         sidechain: bool = False) -> dict:
    """The attachment the harness files for a hook's ``additionalContext``."""
    return {
        "type": "attachment",
        "isSidechain": sidechain,
        "attachment": {
            "type": "hook_additional_context",
            "content": [text],
            "hookName": event,
            "hookEvent": event,
        },
    }


def _write(path: pathlib.Path, *records: dict) -> pathlib.Path:
    """Write *records* as a JSONL transcript at *path* and return the path."""
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def _append(path: pathlib.Path, *records: dict) -> None:
    """Append *records* to the transcript at *path*, as the harness does."""
    with path.open("a") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _run(payload: dict | str) -> str:
    """Run the hook as the harness does and return its stdout.

    Asserts the exit code is 0 and stderr is empty on EVERY run: exit 2 from
    a PostToolBatch hook stops the agentic loop, and any other failure shows
    the session a hook error after every batch.
    """
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    done = subprocess.run((str(HOOK),), input=stdin, capture_output=True,
                          text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    return done.stdout


def _context(path: pathlib.Path, **extra: str) -> str:
    """The hook's added context for the transcript at *path* ("" when silent)."""
    out = _run({"hook_event_name": "PostToolBatch", "transcript_path": str(path),
                **extra})
    if not out:
        return ""
    answer = json.loads(out)
    assert answer == {"hookSpecificOutput": {
        "hookEventName": "PostToolBatch",
        "additionalContext": answer["hookSpecificOutput"]["additionalContext"],
    }}, "the answer carries the added context and nothing that could block"
    return answer["hookSpecificOutput"]["additionalContext"]


#: The record the harness writes when it compacts a session (its shape read
#: from this host's transcripts, 2026-10-27; ``compactMetadata`` trimmed).
_COMPACTION = {"type": "system", "subtype": "compact_boundary", "isSidechain": False,
               "content": "Conversation compacted",
               "compactMetadata": {"trigger": "auto", "preTokens": 341_575}}

THIRD = "context passed the 33% line"
MIDDLE = "context passed the 45% line"
STOP = "context passed the 65% line"


class TestEachLineIsAnnouncedOnce:
    """The three lines, their boundaries, and "the first time" only."""

    def test_nothing_below_a_third(self, tmp_path):
        """One token short of 330,000 (33% of 1,000,000) says nothing."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(329_999))
        assert _context(transcript) == ""

    def test_the_third_at_its_first_token(self, tmp_path):
        """330,000 is past the line, and the rule is to finish then hand off."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(330_000))
        said = _context(transcript)
        assert THIRD in said
        assert "330,000 of 1,000,000 tokens" in said
        assert "finishes the work in flight, then runs /handoff" in said
        assert MIDDLE not in said and STOP not in said

    def test_input_and_both_cache_fields_all_count(self, tmp_path):
        """Context is input + cache creation + cache read, all three.

        110,000 each sums to the line exactly; a hook dropping any one field
        reads 220,000 and stays silent.
        """
        record = _assistant(330_000)
        record["message"]["usage"].update(
            input_tokens=110_000, cache_creation_input_tokens=110_000,
            cache_read_input_tokens=110_000)
        assert THIRD in _context(_write(tmp_path / "s.jsonl", record))

    def test_a_line_is_said_once_per_session(self, tmp_path):
        """The harness files the answer; the next batch past it says nothing."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000))
        first = _context(transcript)
        assert THIRD in first
        _append(transcript, _announcement_record(first), _assistant(360_000))
        assert _context(transcript) == ""

    def test_each_later_line_is_said_on_its_own(self, tmp_path):
        """Past 45% after the third was said: only the 45% line, then 65%."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000))
        _append(transcript, _announcement_record(_context(transcript)),
                _assistant(450_000))
        middle = _context(transcript)
        assert MIDDLE in middle and "starts no new build loop" in middle
        assert THIRD not in middle and STOP not in middle

        _append(transcript, _announcement_record(middle), _assistant(650_000))
        stop = _context(transcript)
        assert STOP in stop and "commits what is clean and stops" in stop
        assert THIRD not in stop and MIDDLE not in stop

        _append(transcript, _announcement_record(stop), _assistant(900_000))
        assert _context(transcript) == ""

    def test_a_compaction_rearms_every_line(self, tmp_path):
        """After a compaction the earlier words are out of the context.

        The harness keeps the file and marks the restart with a
        ``compact_boundary`` record, so a line passed again is said again.
        """
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000))
        _append(transcript, _announcement_record(_context(transcript)),
                _COMPACTION, _assistant(20_000), _assistant(340_000))
        assert THIRD in _context(transcript)

    @pytest.mark.parametrize("record", [
        pytest.param({"type": "system", "subtype": "local_command",
                      "content": "compact_boundary"}, id="another-system-record"),
        pytest.param({"type": "user", "message": {
            "role": "user", "content": '{"subtype": "compact_boundary"}'}},
            id="quoted-in-a-message"),
    ])
    def test_a_mention_of_a_compaction_is_not_one(self, tmp_path, record):
        """Only the harness's own boundary record re-arms a line."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000))
        _append(transcript, _announcement_record(_context(transcript)), record,
                _assistant(360_000))
        assert _context(transcript) == ""

    def test_a_compaction_before_the_words_changes_nothing(self, tmp_path):
        """Only a compaction AFTER an announcement re-arms its line."""
        transcript = _write(tmp_path / "s.jsonl", _COMPACTION, _assistant(340_000))
        _append(transcript, _announcement_record(_context(transcript)),
                _assistant(360_000))
        assert _context(transcript) == ""

    def test_a_jump_past_every_line_says_all_three_in_order(self, tmp_path):
        """No line is skipped because the context passed it between batches."""
        said = _context(_write(tmp_path / "s.jsonl", _assistant(700_000)))
        assert said.index(THIRD) < said.index(MIDDLE) < said.index(STOP)
        assert len(said.splitlines()) == 3


class TestItReadsOnlyThisSessionsTranscript:
    """The figure is this session's main thread, from the file it was handed."""

    def test_the_newest_main_thread_record_is_the_figure(self, tmp_path):
        """An older, larger request does not count once a newer one exists.

        After a compaction the context shrinks; the newest request is the
        session's context now.
        """
        transcript = _write(tmp_path / "s.jsonl", _assistant(700_000),
                            _assistant(100_000))
        assert _context(transcript) == ""

    def test_a_sidechain_record_is_not_the_session(self, tmp_path):
        """A sidechain's request after the main thread's is not the figure."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(100_000),
                            _assistant(700_000, sidechain=True))
        assert _context(transcript) == ""

    def test_a_peers_newer_transcript_beside_it_is_never_read(self, tmp_path):
        """The old meter read a directory's newest file; this reads its own."""
        own = _write(tmp_path / "own.jsonl", _assistant(100_000))
        peer = _write(tmp_path / "peer.jsonl", _assistant(700_000))
        later = own.stat().st_mtime + 60
        os.utime(peer, (later, later))
        assert _context(own) == ""

    def test_a_subagent_batch_gets_no_answer(self, tmp_path):
        """Measured: a subagent's hook input names the PARENT's transcript.

        Its figure would be the parent's and its words would reach only the
        subagent, so the input's ``agent_id`` silences it.
        """
        transcript = _write(tmp_path / "s.jsonl", _assistant(700_000))
        assert _context(transcript, agent_id="a5ac9037e4b037bf6",
                        agent_type="general-purpose") == ""

    def test_a_main_session_with_an_agent_type_is_answered(self, tmp_path):
        """``agent_type`` alone is a main session started with ``--agent``.

        The hooks reference: ``agent_type`` is present when "the session uses
        ``--agent``"; only ``agent_id`` marks a subagent or teammate.
        """
        transcript = _write(tmp_path / "s.jsonl", _assistant(700_000))
        said = _context(transcript, agent_type="code-reviewer")
        assert THIRD in said and MIDDLE in said and STOP in said


class TestOnlyTheHarnessRecordCountsAsAnnounced:
    """The hook's words elsewhere in the transcript are not an announcement."""

    @pytest.mark.parametrize("record", [
        pytest.param(_announcement_record(THIRD, event="PostToolUse"),
                     id="another-events-hook"),
        pytest.param(_announcement_record(THIRD, sidechain=True), id="sidechain"),
        pytest.param({"type": "user", "isSidechain": False, "message": {
            "role": "user", "content": f"a subagent quoted: {THIRD} ..."}},
            id="quoted-in-a-message"),
        pytest.param({"type": "attachment", "isSidechain": False, "attachment": {
            "type": "hook_success", "hookEvent": "PostToolBatch",
            "content": [THIRD], "stdout": "hook_additional_context"}},
            id="another-attachment-type"),
    ])
    def test_the_line_is_said_despite_its_words_elsewhere(self, tmp_path, record):
        """Measured: a subagent's hand-back carried the hook's words verbatim.

        Each record but the quoted message also names the attachment type the
        hook looks for, so it passes the text prefilter and only the record's
        structure refuses it.
        """
        transcript = _write(tmp_path / "s.jsonl", record, _assistant(340_000))
        assert THIRD in _context(transcript)


class TestItFailsSoft:
    """No usable input or transcript: no answer, exit 0, nothing on stderr."""

    @pytest.mark.parametrize("stdin", [
        pytest.param("not json", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param("{}", id="no-transcript-path"),
        pytest.param('{"transcript_path": 7}', id="path-not-a-string"),
        pytest.param('{"transcript_path": ""}', id="empty-path"),
        pytest.param('{"transcript_path": "a\\u0000b"}', id="path-with-a-nul"),
        pytest.param("[" * 100_000, id="nested-past-the-recursion-limit"),
    ])
    def test_an_input_it_cannot_use(self, stdin):
        """Every malformed input is silent (each run asserts exit 0)."""
        assert _run(stdin) == ""

    def test_a_missing_transcript(self, tmp_path):
        """The harness names a file it has not written yet."""
        assert _context(tmp_path / "absent.jsonl") == ""

    def test_a_directory_for_a_transcript(self, tmp_path):
        """An unreadable path is an OSError, and silent."""
        assert _context(tmp_path) == ""

    def test_an_empty_transcript(self, tmp_path):
        """A session's first batch can find nothing written yet."""
        assert _context(_write(tmp_path / "s.jsonl")) == ""

    def test_a_torn_last_line_is_skipped_not_fatal(self, tmp_path):
        """The harness may be mid-write: the last whole record still counts."""
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000))
        with transcript.open("a") as handle:
            handle.write(json.dumps(_assistant(100_000))[:40])
        assert THIRD in _context(transcript)

    def test_a_usage_it_cannot_sum_is_passed_over(self, tmp_path):
        """A newest record with a non-integer count yields to the one before."""
        bad = _assistant(100_000)
        bad["message"]["usage"]["cache_read_input_tokens"] = "lots"
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000), bad)
        assert THIRD in _context(transcript)

    @pytest.mark.parametrize("record", [
        pytest.param(["assistant", "hook_additional_context"], id="not-an-object"),
        pytest.param({"type": "assistant", "message": "assistant"},
                     id="message-not-an-object"),
        pytest.param({"type": "assistant", "message": {"role": "assistant"}},
                     id="no-usage"),
        pytest.param({"type": "attachment", "attachment": "hook_additional_context"},
                     id="attachment-not-an-object"),
        pytest.param({"type": "attachment", "attachment": {
            "type": "hook_additional_context", "hookEvent": "PostToolBatch",
            "content": THIRD}}, id="content-not-a-list"),
        pytest.param({"type": "attachment", "attachment": {
            "type": "hook_additional_context", "hookEvent": "PostToolBatch",
            "content": [7]}}, id="content-item-not-text"),
    ])
    def test_a_record_of_another_shape_is_passed_over(self, tmp_path, record):
        """Newer than the last good request, it neither crashes nor counts.

        Each one holds the text the hook prefilters on, so its shape is what
        is checked.  None counts as an announcement either: a ``content`` that
        is not the measured list is not one the harness wrote.
        """
        transcript = _write(tmp_path / "s.jsonl", _assistant(340_000), record)
        assert THIRD in _context(transcript)


class TestTheWiring:
    """The settings entry the harness reads at session start runs this file."""

    def test_settings_run_it_after_every_batch_and_never_block(self):
        """One PostToolBatch command hook: this script, bounded, never blocking."""
        settings = json.loads(SETTINGS.read_text())
        assert settings["hooks"]["PostToolBatch"] == [{"hooks": [{
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/scripts/hooks/context_meter.py",
            "timeout": 10,
            "onFailure": "continue",
        }]}]

    def test_the_script_is_executable(self):
        """The harness executes the command path itself, with no interpreter."""
        assert os.access(HOOK, os.X_OK)
