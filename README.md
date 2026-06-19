# Autonomous Data Detective

An agent that investigates vague business questions ("why did revenue dip?")
against a real database by **generating competing hypotheses, pre-registering
predictions, testing them against data, and then adversarially trying to break
its own conclusions** before reporting — instead of querying once and
confirming whatever story it finds first.

This is a working scaffold, not a toy demo: the SQL layer is genuinely
read-only and injection-resistant, every query is logged for audit, and the
hypothesis loop actually reopens and re-tests when the critique step finds a
hole in the reasoning.

## Why this exists

LLM agents that explore data have a strong bias toward **confirming the first
plausible story**, because generation and verification are the same pass with
the same blind spots. This project makes hypothesis-killing a *structural*
part of the pipeline rather than something the prompt hopes will happen:

1. Hypotheses are generated **together**, before any query runs — so the
   agent can't anchor on whatever it happens to see first.
2. Each hypothesis gets a **pre-registered prediction** — a specific pattern
   that would have to be true in the data, stated before looking. This
   prevents "I found a number that's kind of related" from counting as
   confirmation.
3. A **separate adversarial pass**, with its own system prompt, reviews the
   findings looking only for holes — sloppy verification, confounds, sample
   size issues — and can force a hypothesis to be reopened and retested.
4. The final report shows the **full hypothesis table, including what was
   refuted**, not just the winning story — so the reasoning is auditable.

## The planted mystery

`data/seed_warehouse.py` generates a synthetic e-commerce warehouse (SQLite)
with a revenue dip in March that has **two simultaneous causes**, deliberately
chosen so a naive single-query investigation gets the wrong answer:

1. **The real cause**: a single large B2B account (`Northwind Logistics`,
   customer_id 4001) churned at the end of February. It was ~15-20% of
   monthly revenue.
2. **The red herring**: on March 1, the (fictional) finance team changed how
   refunds are recorded — from *backdated to the original order's month* to
   *recorded on the date the refund actually happens*. This makes March's
   refund total spike, which looks like a quality/returns problem but is a
   pure reporting artifact — the `refunds` table has both
   `original_order_date` and `refund_recorded_date` specifically so this is
   detectable, but only if the agent checks.

A shallow investigation ("refunds are up 6x in March, must be quality
issues") gets this wrong. The adversarial critique step in this pipeline is
specifically designed to catch that exact mistake — see the trace below.

## Architecture

```
intake → orient → generate_hypotheses → test_hypotheses ⇄ adversarial_critique → report
                                              ↑________________________|
                                         (reopened hypotheses loop back)
```

| Stage | What it does |
|---|---|
| `intake` | Restates the question as falsifiable, sets a query budget |
| `orient` | Explores schema (read-only), flags potential gotchas (e.g. two date columns) |
| `generate_hypotheses` | Produces 3-5 **competing** hypotheses + pre-registered predictions, before any querying |
| `test_hypotheses` | For each hypothesis: runs a query, checks the result against the *specific* prediction (not just "found something"), marks supported/refuted/inconclusive |
| `adversarial_critique` | A skeptical-reviewer pass that tries to break the surviving hypotheses; can force a reopen + retest |
| `report` | Full hypothesis table (including refuted ones) + critique notes + conclusion |

## Project layout

```
data-detective/
├── data/
│   └── seed_warehouse.py     # generates data/warehouse.db with the planted mystery
├── detective/
│   ├── state.py               # Hypothesis dataclass + graph state schema
│   ├── sql_tool.py            # read-only, injection-guarded SQL execution + audit logging
│   ├── llm.py                 # LLM wrapper: real (Anthropic API) or mock backend
│   └── graph.py                # the LangGraph pipeline itself
├── traces/
│   └── query_log.jsonl        # every query run, with hypothesis label + result (audit trail)
└── requirements.txt
```

## Running it

### Mock mode (no API key needed — verifies the graph wiring)

```bash
cd data-detective
python3 data/seed_warehouse.py        # regenerate the warehouse if needed
DETECTIVE_LLM_MODE=mock python3 -m detective.graph
```

This replays a hand-written but realistic trajectory: the agent's first pass
at the refund hypothesis (H3) makes the naive mistake (refund $ is up, must
be a quality issue), the critique step catches that the prediction was never
actually checked against `original_order_date`, forces a retest, and the
retest correctly reclassifies it as a reporting artifact. This is the
behavior the whole architecture exists to produce — useful to see it work
without spending API tokens.

### Real mode (uses Claude via the Anthropic API)

```bash
export ANTHROPIC_API_KEY=sk-...
export DETECTIVE_LLM_MODE=real
python3 -m detective.graph
```

In real mode, each node's LLM call is driven by the actual prompt (visible
in `graph.py`) rather than a canned response — the agent writes its own SQL
queries based on the hypothesis and schema, not the fixed queries the mock
demo uses. **Note**: the `test_hypotheses` node currently runs fixed,
hand-written queries per hypothesis ID (`H1`-`H4`) even in real mode, because
the mock trajectory was written against a known hypothesis set. The natural
next step (see below) is to have the LLM **write the SQL itself** from the
hypothesis text, which is what makes this generalize to arbitrary business
questions instead of just this seeded scenario.

### Inspect the audit trail

```bash
cat traces/query_log.jsonl | python3 -m json.tool
```

Every query is logged with which hypothesis it was testing, the SQL itself,
whether it succeeded, row count, and timing — this is the artifact that
makes the investigation auditable rather than a black box.

## What's stubbed vs. real

To be upfront about what's a working implementation vs. scaffold-for-you-to-extend:

**Fully working:**
- SQL guard rails (SELECT-only, keyword blocklist, `PRAGMA query_only`, row
  limits, timeouts) — tested against injection attempts in the build process
- Schema introspection
- The full LangGraph state machine, including the conditional reopen/retest loop
- Audit logging
- The synthetic warehouse with a genuinely non-trivial, verifiable mystery

**Stubbed for the demo, real prompts written but not exercised against a live model here**
(this sandbox has no `ANTHROPIC_API_KEY` configured — these run for real once you add one):
- All LLM reasoning steps (`orient`, `generate_hypotheses`, the verifier
  judgment in `test_hypotheses`, `adversarial_critique`) have real system
  prompts in `graph.py` and will call Claude directly in `real` mode

**Worth building next** (the genuinely hard parts we flagged in design):
1. **LLM-written SQL** — right now `test_hypotheses` runs fixed queries per
   hypothesis ID. Replace with: give the LLM the hypothesis + prediction +
   schema, have it write the query itself, run it through `run_sql()`. This
   is what makes the agent generalize beyond this one seeded scenario.
2. **Stopping criteria** — currently capped by `max_iterations=2`. A real
   version needs a smarter signal: confidence reached, query budget
   exhausted, or "genuinely inconclusive, here's what we ruled out" as a
   valid terminal state.
3. **Statistical rigor in the verifier** — the verifier step is currently an
   LLM eyeballing numbers. For real use, swap in actual computed checks
   (sample size, confidence intervals, simple significance tests run in
   Python) that the LLM then *interprets* rather than estimates.
4. **Swap SQLite → Postgres** — `sql_tool.py` is written so this is a
   small, contained change (replace the `sqlite3.connect` calls and
   `PRAGMA query_only` with a read-only Postgres role + connection string).
