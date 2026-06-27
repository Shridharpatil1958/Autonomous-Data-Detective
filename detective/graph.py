"""
graph.py — the investigation pipeline, built as a LangGraph StateGraph.

Flow (matches the architecture diagram from the design discussion):

  intake -> orient -> generate_hypotheses -> test_hypotheses (loop per H)
         -> adversarial_critique -> [reopen failed H's -> test again]* -> report

Real-mode prompts are written in full below even though we're demoing in
mock mode here (no API key in this sandbox) -- they are what actually
drives the agent once you set ANTHROPIC_API_KEY and DETECTIVE_LLM_MODE=real.
"""

import json
from langgraph.graph import StateGraph, END

from detective.state import InvestigationState, Hypothesis
from detective.sql_tool import run_sql, get_schema_summary
from detective.llm import call_llm, mode


def log(state: InvestigationState, msg: str):
    state.setdefault("log", []).append(msg)
    print(msg)


# ---------------------------------------------------------------------------
# Node: intake — restate the question as falsifiable
# ---------------------------------------------------------------------------
def node_intake(state: InvestigationState) -> InvestigationState:
    log(state, f"\n{'='*70}\n[INTAKE] Question: {state['question']}\n{'='*70}")
    # In real mode this would be an LLM call to restate the question precisely.
    # Kept deterministic here since it's not load-bearing for the demo.
    state["falsifiable_question"] = (
        f"{state['question']} -- Answered when we can attribute the dip to "
        f"specific, evidenced cause(s) with magnitude estimates, having "
        f"actively tried to rule out competing explanations."
    )
    state["max_iterations"] = 2
    state["iteration"] = 0
    state["query_budget_remaining"] = 25
    return state


# ---------------------------------------------------------------------------
# Node: orient — schema exploration
# ---------------------------------------------------------------------------
def node_orient(state: InvestigationState) -> InvestigationState:
    schema = get_schema_summary()
    state["schema_summary"] = schema
    log(state, f"\n[ORIENT] Schema discovered:\n{schema}")

    system = (
        "You are a data analyst orienting yourself in a new warehouse before "
        "investigating a business question. Identify which tables/columns are "
        "relevant and flag anything that looks like it could be a gotcha "
        "(e.g. two date columns that could differ, ambiguous joins)."
    )
    user = f"Question: {state['question']}\n\nSchema:\n{schema}"
    raw = call_llm(system, user, intent="orient")
    notes = json.loads(raw)["notes"]
    log(state, f"[ORIENT] Notes: {notes}")
    return state


# ---------------------------------------------------------------------------
# Node: generate_hypotheses — competing hypotheses, generated together
# ---------------------------------------------------------------------------
def node_generate_hypotheses(state: InvestigationState) -> InvestigationState:
    system = (
        "You are investigating a business question against a data warehouse. "
        "Generate 3-5 COMPETING hypotheses that could explain the observation. "
        "Do this BEFORE running any queries, to avoid anchoring on whatever you "
        "happen to find first. For each hypothesis, state a PREDICTION: a "
        "specific, falsifiable pattern that would have to be true in the data "
        "if (and ideally only if) the hypothesis is correct. Respond as JSON: "
        '{"hypotheses": [{"id": "H1", "statement": "...", "prediction": "..."}]}'
    )
    user = (
        f"Question: {state['falsifiable_question']}\n\n"
        f"Schema:\n{state['schema_summary']}"
    )
    raw = call_llm(system, user, intent="generate_hypotheses")
    parsed = json.loads(raw)["hypotheses"]
    hyps = [Hypothesis(id=h["id"], statement=h["statement"], prediction=h["prediction"])
            for h in parsed]
    state["hypotheses"] = hyps

    log(state, f"\n[HYPOTHESES] Generated {len(hyps)} competing hypotheses:")
    for h in hyps:
        log(state, f"  {h.id}: {h.statement}\n      predicts: {h.prediction}")
    return state


# ---------------------------------------------------------------------------
# Node: test_hypotheses — predict-then-test for each hypothesis
# ---------------------------------------------------------------------------
def node_test_hypotheses(state: InvestigationState) -> InvestigationState:
    log(state, f"\n{'='*70}\n[TEST] Iteration {state['iteration']+1}: testing hypotheses\n{'='*70}")

    for h in state["hypotheses"]:
        if h.status not in ("untested",) and not h.reopened:
            continue  # already resolved and not flagged for retest

        intent_key = f"test_{h.id}"
        if h.id == "H3" and not h.reopened:
            intent_key = "test_H3_first_pass"
        if h.reopened:
            intent_key = f"test_{h.id}_retest"

        log(state, f"\n[TEST {h.id}] {h.statement}")
        log(state, f"  Prediction: {h.prediction}")

        # --- This is where real-mode would have the LLM WRITE the SQL query
        # itself based on the hypothesis + schema, then we'd call run_sql().
        # For the mock demo we run a representative real query per hypothesis
        # so the SQL tool execution + trace logging is exercised for real,
        # then feed the (canned) interpretation through the LLM mock. ---

        if h.id == "H1":
            r = run_sql(
                "SELECT strftime('%Y-%m', order_date) AS m, SUM(amount) "
                "FROM orders WHERE customer_id NOT IN "
                "(SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1) "
                "GROUP BY 1 ORDER BY 1",
                label=h.id, node="test_hypotheses",
            )
            h.queries_run.append(r.sql)
        elif h.id == "H2":
            r = run_sql(
                "SELECT customer_id, strftime('%Y-%m', order_date) AS m, SUM(amount) "
                "FROM orders WHERE customer_id = 4001 GROUP BY 1,2 ORDER BY 2",
                label=h.id, node="test_hypotheses",
            )
            h.queries_run.append(r.sql)
        elif h.id == "H3":
            if not h.reopened:
                r = run_sql(
                    "SELECT strftime('%Y-%m', refund_recorded_date) AS m, "
                    "COUNT(*), SUM(amount) FROM refunds GROUP BY 1 ORDER BY 1",
                    label=h.id, node="test_hypotheses",
                )
            else:
                r = run_sql(
                    "SELECT strftime('%Y-%m', refund_recorded_date) AS recorded_m, "
                    "CASE WHEN original_order_date < '2025-03-01' THEN 'pre' ELSE 'post' END AS order_bucket, "
                    "COUNT(*), SUM(amount) FROM refunds "
                    "WHERE strftime('%Y-%m', refund_recorded_date) = '2025-03' "
                    "GROUP BY 1,2",
                    label=f"{h.id}_retest", node="test_hypotheses",
                )
                r2 = run_sql(
                    "SELECT category, COUNT(*) FROM support_tickets "
                    "WHERE strftime('%Y-%m', created_date) = '2025-03' "
                    "GROUP BY 1 ORDER BY 2 DESC",
                    label=f"{h.id}_retest_tickets", node="test_hypotheses",
                )
                h.queries_run.append(r2.sql)
            h.queries_run.append(r.sql)
        elif h.id == "H4":
            r = run_sql(
                "SELECT strftime('%Y-%m', order_date) AS m, AVG(amount) "
                "FROM orders GROUP BY 1 ORDER BY 1",
                label=h.id, node="test_hypotheses",
            )
            h.queries_run.append(r.sql)
        else:
            r = None

        if r and r.ok:
            log(state, f"  Query OK ({r.row_count} rows, {r.elapsed_ms}ms): {r.sql}")
        elif r:
            log(state, f"  Query FAILED: {r.error}")

        # --- Interpret + verify against the pre-registered prediction ---
        system = (
            "You ran a query to test a hypothesis. Compare the query result "
            "against the PRE-REGISTERED PREDICTION (stated before you saw any "
            "data). Be honest about whether the evidence actually matches the "
            "specific prediction, not just 'something interesting happened'. "
            'Respond as JSON: {"status": "supported|refuted|inconclusive", '
            '"evidence_summary": "...", "verifier_note": "..."}'
        )
        user = (
            f"Hypothesis: {h.statement}\nPrediction: {h.prediction}\n"
            f"Query result: {r.rows if r and r.ok else r.error if r else 'n/a'}"
        )
        raw = call_llm(system, user, intent=intent_key)
        parsed = json.loads(raw)

        valid_statuses = ("supported", "refuted", "inconclusive")
        if parsed["status"] not in valid_statuses:
            log(state, f"  [WARN] LLM returned unrecognized status "
                       f"'{parsed['status']}' - coercing to 'inconclusive'. "
                       f"This is itself worth auditing.")
        h.status = parsed["status"] if parsed["status"] in valid_statuses else "inconclusive"
        h.evidence_summary = parsed["evidence_summary"]
        h.verifier_note = parsed["verifier_note"]
        h.reopened = False

        log(state, f"  -> STATUS: {h.status.upper()}")
        log(state, f"  -> Evidence: {h.evidence_summary}")
        log(state, f"  -> Verifier: {h.verifier_note}")

    return state


# ---------------------------------------------------------------------------
# Node: adversarial_critique — separate pass, tries to break surviving H's
# ---------------------------------------------------------------------------
def node_adversarial_critique(state: InvestigationState) -> InvestigationState:
    log(state, f"\n{'='*70}\n[CRITIQUE] Adversarial pass over current findings\n{'='*70}")

    system = (
        "You are a SKEPTICAL senior analyst reviewing a junior analyst's "
        "hypothesis testing. Your ONLY job is to find holes: sloppy "
        "verification, predictions that weren't actually checked precisely, "
        "confounds, small sample sizes, alternative explanations not "
        "considered. Do not be agreeable. For each issue found, recommend "
        '"reopen_and_retest" or "keep_but_annotate". Respond as JSON: '
        '{"critiques": [{"hypothesis_id": "...", "issue": "...", '
        '"recommended_action": "..."}]}'
    )
    summary = "\n".join(
        f"{h.id} [{h.status}]: {h.statement}\n  prediction: {h.prediction}\n"
        f"  evidence: {h.evidence_summary}\n  verifier said: {h.verifier_note}"
        for h in state["hypotheses"]
    )
    critique_intent = "critique" if state["iteration"] == 0 else "critique_after_retest"
    raw = call_llm(system, f"Findings so far:\n{summary}", intent=critique_intent)
    critiques = json.loads(raw)["critiques"]

    by_id = {h.id: h for h in state["hypotheses"]}
    any_reopened = False
    for c in critiques:
        h = by_id.get(c["hypothesis_id"])
        if not h:
            continue
        h.critique_note = c["issue"]
        log(state, f"\n[CRITIQUE -> {h.id}] {c['issue']}")
        log(state, f"  Recommended action: {c['recommended_action']}")
        if c["recommended_action"] == "reopen_and_retest":
            h.reopened = True
            h.status = "untested"
            any_reopened = True

    state["iteration"] += 1
    if any_reopened:
        log(state, "\n[CRITIQUE] One or more hypotheses reopened -> looping back to testing.")
    else:
        log(state, "\n[CRITIQUE] No reopenings needed -> proceeding to report.")
    return state


def route_after_critique(state: InvestigationState) -> str:
    any_reopened = any(h.reopened for h in state["hypotheses"])
    if any_reopened and state["iteration"] < state["max_iterations"]:
        return "test_hypotheses"
    return "report"


# ---------------------------------------------------------------------------
# Node: report — full hypothesis table, not just the winning story
# ---------------------------------------------------------------------------
def node_report(state: InvestigationState) -> InvestigationState:
    log(state, f"\n{'='*70}\n[REPORT] Compiling final report\n{'='*70}")

    lines = [
        f"# Investigation: {state['question']}\n",
        f"**Falsifiable framing:** {state['falsifiable_question']}\n",
        "## Hypothesis Table\n",
        "| ID | Hypothesis | Status | Evidence |",
        "|---|---|---|---|",
    ]
    for h in state["hypotheses"]:
        status_marker = {
            "supported": "✅ SUPPORTED",
            "refuted": "❌ REFUTED",
            "inconclusive": "❓ INCONCLUSIVE",
        }.get(h.status, h.status)
        ev = h.evidence_summary.replace("\n", " ")
        lines.append(f"| {h.id} | {h.statement} | {status_marker} | {ev} |")

    lines.append("\n## Adversarial Critique Notes\n")
    for h in state["hypotheses"]:
        if h.critique_note:
            lines.append(f"- **{h.id}**: {h.critique_note}")

    lines.append("\n## Conclusion\n")
    supported = [h for h in state["hypotheses"] if h.status == "supported"]
    if supported:
        for h in supported:
            lines.append(f"- **{h.statement}** — {h.evidence_summary}")
    else:
        lines.append("- No hypothesis was clearly supported; investigation is inconclusive.")

    lines.append(
        "\n*Full query + reasoning trace logged to traces/query_log.jsonl "
        "for audit.*"
    )

    state["final_report"] = "\n".join(lines)
    return state


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(InvestigationState)
    g.add_node("intake", node_intake)
    g.add_node("orient", node_orient)
    g.add_node("generate_hypotheses", node_generate_hypotheses)
    g.add_node("test_hypotheses", node_test_hypotheses)
    g.add_node("adversarial_critique", node_adversarial_critique)
    g.add_node("report", node_report)

    g.set_entry_point("intake")
    g.add_edge("intake", "orient")
    g.add_edge("orient", "generate_hypotheses")
    g.add_edge("generate_hypotheses", "test_hypotheses")
    g.add_edge("test_hypotheses", "adversarial_critique")
    g.add_conditional_edges(
        "adversarial_critique",
        route_after_critique,
        {"test_hypotheses": "test_hypotheses", "report": "report"},
    )
    g.add_edge("report", END)

    return g.compile()


if __name__ == "__main__":
    print(f"LLM mode: {mode()}\n")
    app = build_graph()
    result = app.invoke({"question": "Why did revenue dip in March 2025?"})
    print("\n\n" + "#" * 70)
    print(result["final_report"])
