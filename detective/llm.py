"""
llm.py — thin LLM wrapper with two backends:

  - "real": calls Claude via langchain_anthropic (requires ANTHROPIC_API_KEY)
  - "mock": returns deterministic, hand-written responses so the GRAPH WIRING
            can be tested without API access or burning tokens. The mock
            responses are intentionally written to simulate a *plausible*
            agent trajectory through this specific seeded mystery, including
            a wrong turn that the adversarial critic should catch — so you
            can verify the critique step actually does something.

Set DETECTIVE_LLM_MODE=mock|real as an env var. Defaults to mock if no
ANTHROPIC_API_KEY is present.
"""

import os
import json

MODE = os.environ.get(
    "DETECTIVE_LLM_MODE",
    "real" if os.environ.get("ANTHROPIC_API_KEY") else "mock",
)

MODEL_NAME = "claude-sonnet-4-6"


def _real_call(system: str, user: str, *, json_mode: bool = False) -> str:
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model=MODEL_NAME, max_tokens=2000, temperature=0.3)
    messages = [("system", system), ("human", user)]
    resp = llm.invoke(messages)
    return resp.content


# ---------------------------------------------------------------------------
# Mock backend: keyed by a short "intent" tag passed from each node, so each
# node can get a canned-but-realistic response without regex-matching prompts.
# ---------------------------------------------------------------------------
_MOCK_RESPONSES = {
    "orient": json.dumps({
        "notes": (
            "Relevant tables: orders (gross revenue by customer/date), "
            "refunds (has both original_order_date and refund_recorded_date - "
            "important, these can differ), customers (segment B2B/B2C), "
            "support_tickets (possible corroborating signal)."
        )
    }),

    "generate_hypotheses": json.dumps({
        "hypotheses": [
            {
                "id": "H1",
                "statement": "Overall order volume or customer count declined broadly across the customer base in March.",
                "prediction": "If true, the DECLINE in gross orders should be spread across many customers, not concentrated in one or two accounts."
            },
            {
                "id": "H2",
                "statement": "A small number of large accounts churned or reduced spend, driving most of the dip.",
                "prediction": "If true, removing the top 1-2 customers by historical revenue from the monthly total should make most of the dip disappear."
            },
            {
                "id": "H3",
                "statement": "Refunds increased in March, indicating a quality or fulfillment problem that is reducing net revenue.",
                "prediction": "If true, refunds recorded in March should correspond to orders PLACED in March (i.e. recent purchases are increasingly being returned), and support tickets should show a rise in 'quality' or 'shipping' complaints, not 'billing' complaints."
            },
            {
                "id": "H4",
                "statement": "Average order value (not volume) dropped in March, e.g. due to discounting or a product mix shift.",
                "prediction": "If true, AOV computed from orders alone (ignoring refunds) should be measurably lower in March vs Jan/Feb."
            },
        ]
    }),

    # Note: this mock intentionally has the agent jump to a WRONG conclusion on H3
    # first pass (sees refund $ amount is higher in March, calls it "supported"
    # without checking original_order_date vs refund_recorded_date). The
    # adversarial critic step should catch this.
    "test_H1": json.dumps({
        "status": "refuted",
        "evidence_summary": (
            "Querying orders excluding the largest single customer shows the "
            "remaining customer base's revenue is roughly flat Feb->March "
            "(~$120k vs ~$121k after correcting for one outlier customer). "
            "The decline is NOT broadly distributed across many customers."
        ),
        "verifier_note": "Prediction was 'decline spread across many customers' - evidence shows the opposite (concentrated). REFUTED as stated."
    }),

    "test_H2": json.dumps({
        "status": "supported",
        "evidence_summary": (
            "Customer 4001 (Northwind Logistics) had ~$27-28k/month in orders "
            "in Jan and Feb, then ZERO orders from March onward. This single "
            "customer's churn accounts for the majority of the gross revenue "
            "decline in March when compared against a relatively flat "
            "all-other-customers baseline."
        ),
        "verifier_note": "Prediction confirmed: removing this one customer from the historical baseline closes most of the gap. Sample size of n=1 customer is a limitation worth flagging to the critic."
    }),

    "test_H3_first_pass": json.dumps({
        "status": "supported",
        "evidence_summary": (
            "Refunds recorded in March total ~$4,615, noticeably higher than "
            "February's ~$677 and most other months. This looks like a real "
            "increase in returns starting in March."
        ),
        "verifier_note": "Refund dollar amount in March is higher, consistent with prediction direction. Marking supported -- though I have not yet checked whether these refunds correspond to orders placed in March vs earlier months."
    }),

    "test_H4": json.dumps({
        "status": "refuted",
        "evidence_summary": (
            "Average order value computed from the orders table alone is "
            "essentially flat across Jan-May (no meaningful month-to-month "
            "difference in per-order amount)."
        ),
        "verifier_note": "Prediction was a measurable AOV drop in March; not observed. REFUTED."
    }),

    # The adversarial critic catches the H3 sloppiness: never checked whether
    # the refunded orders were actually PLACED in March, vs just RECORDED in
    # March (could be backdated refunds from earlier orders under an old
    # accounting policy. This is exactly the trap we want surfaced.
    "critique": json.dumps({
        "critiques": [
            {
                "hypothesis_id": "H3",
                "issue": (
                    "H3 was marked SUPPORTED based only on the total refund dollar "
                    "amount being higher in March, but the prediction specifically "
                    "required checking whether refunded orders were PLACED in March "
                    "(implying a fresh quality/fulfillment problem) versus refunds for "
                    "OLDER orders simply being recorded/booked in March. These are very "
                    "different stories: a real quality issue vs. a reporting timing "
                    "artifact. The original_order_date field exists and was not used in "
                    "this check. Also, the support ticket category breakdown for March "
                    "was never queried, even though the prediction mentioned it as a "
                    "discriminating signal (quality/shipping tickets vs billing tickets)."
                ),
                "recommended_action": "reopen_and_retest"
            },
            {
                "hypothesis_id": "H2",
                "issue": (
                    "Supported on n=1 customer, which is a thin evidentiary base by "
                    "construction (single account), not a flaw in the analysis. Worth "
                    "stating explicitly in the final report as a concentration risk "
                    "finding rather than implying it's a generalizable trend across many "
                    "accounts."
                ),
                "recommended_action": "keep_but_annotate"
            }
        ]
    }),

    "critique_after_retest": json.dumps({
        "critiques": [
            {
                "hypothesis_id": "H2",
                "issue": (
                    "Supported on n=1 customer, which is a thin evidentiary base by "
                    "construction (single account), not a flaw in the analysis. Worth "
                    "stating explicitly in the final report as a concentration risk "
                    "finding rather than implying it's a generalizable trend across many "
                    "accounts."
                ),
                "recommended_action": "keep_but_annotate"
            },
            {
                "hypothesis_id": "H3",
                "issue": (
                    "Retest properly used original_order_date vs refund_recorded_date "
                    "and cross-checked against support ticket categories. Conclusion "
                    "(reporting/policy artifact, not a real quality issue) is now "
                    "adequately supported. No further action needed."
                ),
                "recommended_action": "keep_but_annotate"
            }
        ]
    }),

    "test_H3_retest": json.dumps({
        "status": "refuted",
        "evidence_summary": (
            "Re-querying with original_order_date vs refund_recorded_date "
            "breakdown: of the refunds recorded in March, the majority "
            "(23 of 37, ~62%) correspond to orders that were PLACED before "
            "the apparent March 1 cutover, not new March purchases. This is "
            "inconsistent with a fresh quality problem (which would show "
            "refunds concentrated on recently-placed orders) and consistent "
            "with a change in WHEN refunds get recorded relative to when "
            "they occur. Support ticket data corroborates this: March shows "
            "a rise specifically in 'billing' category tickets, not "
            "'quality' or 'shipping' tickets, suggesting customer confusion "
            "about statement timing rather than a product problem."
        ),
        "verifier_note": (
            "Original H3 prediction (decline driven by genuine quality issue) "
            "is REFUTED. Reclassifying as: refund timing/recording artifact, "
            "likely a process or policy change rather than a real revenue "
            "decline -- net cash impact across the full period is probably "
            "similar, just attributed to different months than before."
        )
    }),
}


def call_llm(system: str, user: str, *, intent: str = "", json_mode: bool = True) -> str:
    """
    Unified entrypoint used by all graph nodes.
    `intent` is only used by the mock backend to select a canned response;
    in real mode it's ignored (the prompt itself drives behavior).
    """
    if MODE == "mock":
        if intent not in _MOCK_RESPONSES:
            raise KeyError(
                f"No mock response registered for intent='{intent}'. "
                f"Available: {list(_MOCK_RESPONSES.keys())}"
            )
        return _MOCK_RESPONSES[intent]
    else:
        return _real_call(system, user, json_mode=json_mode)


def mode() -> str:
    return MODE
