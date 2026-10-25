"""A small plan for the migration's tests: registries, arc documents and an input file.

The live registries move every day and are graded by the plan gate's own suite; the
migration's tests read this corpus instead, which holds one of each shape the source,
the input file and the cards' text must handle (each named where it is written), and
:func:`complete_input` writes the input file a session would, hashing each entry's
source text exactly as :mod:`_migrate_input` grades it.  Nothing here reads a file.
"""
from __future__ import annotations

from dataclasses import replace

from tools.ci.arcs import ARCS, STEPS_HEADINGS
from tools.plan_gate.registries import LedgerRow, RulingRow, StepRow
from tools.quill._migrate_bodies import Inputs, build
from tools.quill._migrate_input import grade
from tools.quill._migrate_numbers import PLAIN, decided_from, numbers
from tools.quill._migrate_source import (
    MAPPED,
    Registries,
    Source,
    read_source,
    source_hash,
    source_text,
)

COMMIT = "c0ffee0123456789c0ffee0123456789c0ffee01"

#: balance's steps section: a prose preamble, a bare ``###`` heading, a shipped pointer,
#: a container whose leaves are indented two spaces -- the first holding a fenced sample
#: with a blank line, then a flush-left note with NO blank line before it; the second a
#: flush-left note after a blank -- and a top-level step with its own flush-left text.
BALANCE = """# Balance

## 5. The steps

Every leaf here obeys one rule.

### Phase A

- [x] **A-1** `abc1234` -- the shipped pointer.
- [ ] **A-2** the decomposed parent of two leaves.
  - [ ] **A-2a** the first leaf.
    its continuation
    ```text
    - [ ] **Z-9** a sample, not a step

    after a blank inside the sample
    ```
*A flush-left note about archived steps, with no blank line before it.
  - [ ] **A-2b** the second leaf, which moves money.

A flush-left note about another arc.

- [ ] **A-3** a plain step.

A-3's own flush-left spec paragraph.

## 6. After the steps
"""

SALARY = """# Salary

## 4. The steps

- [ ] **S-1** the salary step.
"""

STEPS = (
    StepRow("balance", "A-1", "--", "The first step.", "SHIPPED", "`abc1234`", "--"),
    StepRow("balance", "A-2", "--", "Split the work (decomposed parent).", "container", "--",
            "--"),
    StepRow("balance", "A-2a", "--", "Do the first half.", "#1", "--", "NOW"),
    StepRow("balance", "A-2b", "--", "Do the second half. **MOVES MONEY.**", "#2", "--",
            "after #1 / balance:A-2a"),
    StepRow("balance", "A-3", "--", "Do the last part.", "#3", "--",
            "after #2 / balance:A-2a (its rows) / balance:A-1 (shipped)"),
    StepRow("salary", "S-1", "--", "Price the paycheck.", "#4", "--", "NOW"),
)

FINDINGS = (
    LedgerRow("balance", "F-1 (A-2a's trace)", "--", "The first half reads a stale row.",
              "$0.04", "OPEN", "A-2a"),
    LedgerRow("balance", "F-2", "--", "The last part double counts.", "$1.00", "OPEN",
              "A-3 (the fold) / A-2a"),
    LedgerRow("balance", "Q-1", "--", "Which rows are real?", "--", "OPEN",
              "operator (is the stub's gross right to the cent?)"),
    LedgerRow("balance", "Q-2", "--", "A fork nobody wrote down.", "--", "OPEN",
              "developer-decision (2026-09-06)"),
    LedgerRow("salary", "N-391", "--", "The stub disagrees with the profile.", "$0.04", "OPEN",
              "S-1 / operator (re-read one stub: is the gross right?)"),
)

RULINGS = (
    RulingRow("balance", "R-A", "--", "2026-10-01", "Question: where? Answer: here."),
    RulingRow("salary", "R-SAL1 (S-1's)", "R-SAL0", "2026-10-02", "Question: when? Answer: now."),
)

OUTCOMES = (("O1 the first", ("balance:A-3", "balance:A-1")),)


def registries(**changes) -> Registries:
    """The corpus, with ``changes`` (any :class:`Registries` field) replacing its parts;
    ``documents`` may name some arcs only, the rest keeping theirs."""
    documents = {arc: f"# {arc}\n\n{STEPS_HEADINGS[arc]} The steps\n" for arc in ARCS}
    documents.update({"balance": BALANCE, "salary": SALARY})
    documents.update(changes.pop("documents", {}))
    return replace(Registries(STEPS, FINDINGS, RULINGS, OUTCOMES, documents, COMMIT),
                   **changes)


#: The names the corpus's cards take, by key.
NAMES = {
    "balance:A-2": "Split the work", "balance:A-2a": "First half", "balance:A-2b": "Second half",
    "balance:A-3": "Last part", "salary:S-1": "Paycheck price",
    "balance:F-1": "Stale row", "balance:F-2": "Double count", "balance:Q-1": "Stub gross",
    "balance:Q-2": "Unwritten fork", "salary:N-391": "Stub disagrees",
    "salary:N-391 question": "Re-read the stub", "balance:R-A": "Here", "salary:R-SAL1": "Now",
}


def complete_input(corpus: Registries, source: Source | None = None) -> dict:
    """The input file a session would write for ``corpus``: every name but a card's filed
    already; Q-2's question written; the flush-left notes NOTES and A-3's own paragraph
    OWN; the preamble given to A-3's card; the outcome's milestone -- each entry hashing
    its source text (:func:`_migrate_source.source_text`) -- and each bare ``#N`` a card's
    text holds decided plain, hashing the card's text as built
    (:func:`_migrate_numbers.decided_from`); the corpus as written holds none."""
    source = source or read_source(corpus)
    items = {item.key: item for item in source.items}
    paragraphs = [paragraph for spec in source.specs.values() for paragraph in spec.paragraphs]
    preamble = source.preambles[0]
    name, scope = corpus.outcomes[0]
    data = {
        "names": {key: {"name": NAMES[key], "source": source_hash(
            source_text(items[key], source.specs, corpus.documents))} for key in items
                  if key not in MAPPED},
        "questions": {"balance:Q-2": {
            "question": "Which step makes the settle-day door refuse an early day?",
            "source": source_hash(source_text(items["balance:Q-2"], {}, {}))}},
        "notes_kept": {},
        "notes": [{"arc": p.arc, "step": p.step, "first_line": p.first_line,
                   "disposition": "own" if p.step == "A-3" else "note",
                   "source": source_hash(p.text)} for p in paragraphs],
        "preambles": [{"arc": preamble.arc, "first_line": preamble.first_line,
                       "disposition": "card-body", "step": "balance:A-3",
                       "source": source_hash(preamble.text)}],
        "milestones": {name: {"title": "The first", "description": "The first outcome.",
                              "source": source_hash(f"{name}\n" + " / ".join(scope))}},
        "numbers": {},
    }
    built = build(Inputs(source, corpus, grade(data, source, corpus)[0]))[0]
    data["numbers"] = {key: {"each": [[number.text, PLAIN] for number in found],
                             "source": decided_from(card)}
                       for key, card in built.items() if (found := numbers(card))}
    return data
