"""
state.py — shared types for the investigation graph.
"""

from typing import TypedDict, Literal, Optional
from dataclasses import dataclass, field

HypothesisStatus = Literal["untested", "supported", "refuted", "inconclusive"]


@dataclass
class Hypothesis:
    id: str                      # "H1", "H2", ...
    statement: str                # the hypothesis itself
    prediction: str = ""          # what we'd expect to see in data IF true (pre-registered)
    status: HypothesisStatus = "untested"
    queries_run: list[str] = field(default_factory=list)
    evidence_summary: str = ""    # what the data actually showed
    verifier_note: str = ""       # did evidence actually match the prediction?
    critique_note: str = ""       # adversarial pass findings, if any
    reopened: bool = False        # True if critique forced re-investigation


class InvestigationState(TypedDict, total=False):
    question: str                          # original business question
    falsifiable_question: str              # restated, with a definition of "answered"
    schema_summary: str                    # output of get_schema_summary()
    hypotheses: list[Hypothesis]
    query_budget_remaining: int
    iteration: int
    max_iterations: int
    final_report: str
    log: list[str]                         # human-readable trace of agent reasoning
