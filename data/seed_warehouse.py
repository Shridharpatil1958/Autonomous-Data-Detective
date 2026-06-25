"""
Seeds a SQLite 'warehouse' with synthetic e-commerce data.

THE MYSTERY (for our own reference — the agent never sees this):
  Revenue dipped ~March 2025. Two things are true simultaneously:

  1. REAL CAUSE: A single large B2B account ("Northwind Logistics", customer_id=4001)
     churned at the end of February. They represented ~9% of monthly revenue.
     This alone explains most of the dip.

  2. RED HERRING: On March 1, the finance team changed how refunds are recorded —
     previously refunds were recorded in the month the ORIGINAL order was placed
     (backdated), now they're recorded in the month the refund actually HAPPENS.
     This is a pure reporting/accounting artifact: it makes March look worse
     (refunds from old Jan/Feb orders now hit March) but is NOT a real revenue
     decline — net cash impact is identical, it's just a timing/attribution change.
     A naive agent will likely see "refunds spiked in March" and conclude
     "increased returns/quality issues" — which is FALSE.

  3. NOISE: normal seasonality, random day-to-day variance, a few other small
     customer churns/growths that are red herrings of a different kind (real but
     immaterial — should be ruled out as "not big enough to matter").

  A good investigation should:
   - notice the refund spike, investigate it, and correctly identify it as an
     artifact of a policy change (checking refund DATES vs original ORDER dates)
   - separately identify Northwind's churn as the dominant real cause
   - quantify "how much of the dip does each explain" rather than picking one
"""

import sqlite3
import random
from datetime import date, timedelta

random.seed(42)

DB_PATH = "/home/claude/data-detective/data/warehouse.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript("""
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS refunds;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS support_tickets;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT,
    segment TEXT,           -- 'B2B' or 'B2C'
    signup_date TEXT
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    order_date TEXT,
    amount REAL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE refunds (
    refund_id INTEGER PRIMARY KEY,
    order_id INTEGER,
    customer_id INTEGER,
    original_order_date TEXT,   -- when the order that's being refunded was placed
    refund_recorded_date TEXT,  -- when the refund was RECORDED in the ledger
    amount REAL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE TABLE support_tickets (
    ticket_id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    created_date TEXT,
    category TEXT     -- 'quality', 'shipping', 'billing', 'other'
);
""")

# ---------------------------------------------------------------------------
# 1. Customers
# ---------------------------------------------------------------------------
customers = []

# The big B2B account that churns
customers.append((4001, "Northwind Logistics", "B2B", "2022-03-10"))

# ~40 other B2B accounts (smaller)
for i in range(4002, 4042):
    customers.append((i, f"B2B Account {i}", "B2B", f"202{random.randint(2,4)}-{random.randint(1,12):02d}-01"))

# ~300 B2C customers
for i in range(5001, 5301):
    customers.append((i, f"Customer {i}", "B2C", f"202{random.randint(2,4)}-{random.randint(1,12):02d}-01"))

cur.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)

# ---------------------------------------------------------------------------
# 2. Orders — Jan 2025 through May 2025
# ---------------------------------------------------------------------------
orders = []
order_id = 1

start = date(2025, 1, 1)
end = date(2025, 5, 31)

NORTHWIND_MONTHLY_AVG = 32000  # Northwind's typical monthly spend, spread across several orders

day = start
while day <= end:
    month = day.month

    # --- Northwind Logistics: regular large orders through Feb, then nothing ---
    if month <= 2:
        # roughly 2 orders/week
        if day.weekday() in (1, 4) and random.random() < 0.9:
            amt = round(random.gauss(NORTHWIND_MONTHLY_AVG / 8, 400), 2)
            orders.append((order_id, 4001, day.isoformat(), max(amt, 500)))
            order_id += 1
    # March onward: Northwind churned, zero orders

    # --- other B2B accounts: fairly steady, small random growth ---
    for cust_id in range(4002, 4042):
        if random.random() < 0.04:  # sparse ordering
            amt = round(random.gauss(1200, 300), 2)
            orders.append((order_id, cust_id, day.isoformat(), max(amt, 100)))
            order_id += 1

    # --- B2C: lots of small orders, mild weekly seasonality ---
    weekend_boost = 1.3 if day.weekday() >= 5 else 1.0
    n_orders_today = int(random.gauss(35, 4) * weekend_boost)
    for _ in range(max(n_orders_today, 0)):
        cust_id = random.randint(5001, 5300)
        amt = round(random.gauss(85, 25), 2)
        orders.append((order_id, cust_id, day.isoformat(), max(amt, 10)))
        order_id += 1

    day += timedelta(days=1)

cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)

# ---------------------------------------------------------------------------
# 3. Refunds — the policy-change red herring lives here
# ---------------------------------------------------------------------------
# Policy: before 2025-03-01, refund_recorded_date was BACKDATED to original_order_date.
#         from 2025-03-01 onward, refund_recorded_date = the actual day the refund happens
#         (which is realistically anywhere from a few days to ~6 weeks after the order).
#
# Refund RATE is constant at ~3% of orders across the whole period (no real change
# in product quality / return behavior). What changes is purely which MONTH the
# refund lands in the ledger.

refunds = []
refund_id = 1
POLICY_CHANGE_DATE = date(2025, 3, 1)

# sample ~3% of B2C/small-B2B orders (not Northwind, to keep their story clean)
refundable_orders = [o for o in orders if o[1] != 4001]
n_refunds = int(len(refundable_orders) * 0.03)
sampled = random.sample(refundable_orders, n_refunds)

for o in sampled:
    o_id, cust_id, order_date_str, amount = o
    order_date = date.fromisoformat(order_date_str)
    lag_days = random.randint(3, 40)
    actual_refund_date = order_date + timedelta(days=lag_days)
    if actual_refund_date > end:
        continue

    if order_date < POLICY_CHANGE_DATE:
        # old policy was in effect WHEN THIS ORDER WAS PLACED... but the rule that
        # matters is: was the refund itself recorded before or after the policy
        # change? Pre-change: backdate to original order month regardless of when
        # it actually happened.
        if actual_refund_date < POLICY_CHANGE_DATE:
            recorded_date = order_date  # backdated, and it would've shown in original month anyway
        else:
            # order placed under old policy, but refund actually occurs after the
            # switch -> recorded_date backdates to ORIGINAL order month under old
            # policy intent, but since policy changed on the recording side,
            # it's recorded at actual refund time (this is the artifact).
            recorded_date = actual_refund_date
    else:
        recorded_date = actual_refund_date

    refund_amt = round(amount * random.uniform(0.5, 1.0), 2)  # partial or full refund
    refunds.append((refund_id, o_id, cust_id, order_date.isoformat(),
                     recorded_date.isoformat(), refund_amt))
    refund_id += 1

cur.executemany("INSERT INTO refunds VALUES (?,?,?,?,?,?)", refunds)

# ---------------------------------------------------------------------------
# 4. Support tickets — mostly noise, slight uptick in 'billing' tickets in March
#    (customers confused by refund timing on statements — another clue pointing
#    to "process change" rather than "quality problem")
# ---------------------------------------------------------------------------
tickets = []
ticket_id = 1
day = start
while day <= end:
    n_tickets = int(random.gauss(4, 1.5))
    for _ in range(max(n_tickets, 0)):
        cust_id = random.choice([c[0] for c in customers])
        if day.month == 3 and random.random() < 0.35:
            category = "billing"
        else:
            category = random.choices(
                ["quality", "shipping", "billing", "other"],
                weights=[0.2, 0.3, 0.2, 0.3]
            )[0]
        tickets.append((ticket_id, cust_id, day.isoformat(), category))
        ticket_id += 1
    day += timedelta(days=1)

cur.executemany("INSERT INTO support_tickets VALUES (?,?,?,?)", tickets)

conn.commit()

# ---------------------------------------------------------------------------
# Quick sanity summary (printed for us, not the agent)
# ---------------------------------------------------------------------------
print("=== Seed complete ===")
for tbl in ["customers", "orders", "refunds", "support_tickets"]:
    n = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"  {tbl}: {n} rows")

print("\n=== Monthly net revenue (orders - refunds recorded that month) ===")
rows = cur.execute("""
    WITH monthly_orders AS (
        SELECT strftime('%Y-%m', order_date) AS month, SUM(amount) AS gross
        FROM orders GROUP BY 1
    ),
    monthly_refunds AS (
        SELECT strftime('%Y-%m', refund_recorded_date) AS month, SUM(amount) AS refunded
        FROM refunds GROUP BY 1
    )
    SELECT mo.month, mo.gross, COALESCE(mr.refunded,0) AS refunded,
           mo.gross - COALESCE(mr.refunded,0) AS net
    FROM monthly_orders mo LEFT JOIN monthly_refunds mr ON mo.month = mr.month
    ORDER BY 1
""").fetchall()
for r in rows:
    print(f"  {r[0]}: gross={r[1]:,.0f}  refunded={r[2]:,.0f}  net={r[3]:,.0f}")

conn.close()
