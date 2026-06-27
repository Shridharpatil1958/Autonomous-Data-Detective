"""
sql_tool.py — a constrained, read-only SQL execution layer.

Design goals (mirrors what you'd want against a real warehouse):
  - SELECT-only. No INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA etc.
  - Row limit on results returned to the agent (don't blow up context).
  - Wall-clock timeout per query.
  - Every query + result is logged to a trace file for auditability —
    this log IS the investigation's audit trail.

Swapping to Postgres later: replace the sqlite3 connection with psycopg2/
SQLAlchemy and keep the same validate_readonly() + execute() interface.
"""

import sqlite3
import re
import time
import json
import os
from dataclasses import dataclass, asdict
from typing import Any

DB_PATH = "/home/claude/data-detective/data/warehouse.db"
TRACE_PATH = "/home/claude/data-detective/traces/query_log.jsonl"
MAX_ROWS = 200
TIMEOUT_SECONDS = 5

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|"
    r"REPLACE|TRUNCATE|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


@dataclass
class QueryResult:
    ok: bool
    sql: str
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    row_count: int | None = None
    truncated: bool = False
    error: str | None = None
    elapsed_ms: float | None = None


def validate_readonly(sql: str) -> str | None:
    """Returns an error string if the query is not a safe read-only SELECT."""
    stripped = sql.strip().rstrip(";")
    if not re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return "Only SELECT (or WITH ... SELECT) statements are permitted."
    if FORBIDDEN_KEYWORDS.search(stripped):
        return "Query contains a forbidden keyword (only read-only SELECTs are allowed)."
    if ";" in stripped:
        return "Multiple statements are not permitted (remove semicolons)."
    return None


def _log_trace(entry: dict):
    os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
    with open(TRACE_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run_sql(sql: str, *, label: str = "", node: str = "") -> QueryResult:
    """
    Execute a read-only SQL query against the warehouse.
    `label` and `node` are metadata for the trace log (e.g. which hypothesis
    and which graph node issued this query) — not used by SQLite itself.
    """
    err = validate_readonly(sql)
    if err:
        result = QueryResult(ok=False, sql=sql, error=err)
        _log_trace({"node": node, "label": label, **asdict(result)})
        return result

    conn = sqlite3.connect(DB_PATH, timeout=TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA query_only = ON;")  # belt-and-suspenders: DB-level enforcement
    cur = conn.cursor()

    start = time.time()
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        elapsed = (time.time() - start) * 1000
        result = QueryResult(
            ok=True,
            sql=sql,
            columns=cols,
            rows=[list(r) for r in rows],
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round(elapsed, 1),
        )
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        result = QueryResult(ok=False, sql=sql, error=str(e), elapsed_ms=round(elapsed, 1))
    finally:
        conn.close()

    _log_trace({"node": node, "label": label, **asdict(result)})
    return result


def get_schema_summary() -> str:
    """Lightweight schema introspection the agent can call during orientation."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA query_only = ON;")
    cur = conn.cursor()
    tables = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    lines = []
    for (tbl,) in tables:
        cols = cur.execute(f"PRAGMA table_info({tbl})").fetchall()
        col_desc = ", ".join(f"{c[1]} {c[2]}" for c in cols)
        count = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        date_cols = [c[1] for c in cols if "date" in c[1].lower()]
        date_range = ""
        if date_cols:
            dc = date_cols[0]
            lo, hi = cur.execute(f"SELECT MIN({dc}), MAX({dc}) FROM {tbl}").fetchone()
            date_range = f" | date range ({dc}): {lo} to {hi}"
        lines.append(f"- {tbl}({col_desc}) | {count} rows{date_range}")

    conn.close()
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_schema_summary())
    print()
    print(run_sql("SELECT 1; DROP TABLE customers", label="should_fail"))
    print(run_sql("SELECT strftime('%Y-%m', order_date) AS m, SUM(amount) FROM orders GROUP BY 1",
                   label="sanity_check"))
