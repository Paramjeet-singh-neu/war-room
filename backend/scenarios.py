"""Realistic incident scenarios — the messy input the demo pastes in live.

Each scenario bundles three raw slices (logs / metrics / deploys) plus the
incident metadata. The root cause is *discoverable only by correlating the
three sources*: a symptom (errors + latency) that begins seconds after a
specific change (a deploy).

Scenario 1 (`payments`): the clean converging case — all three point at
deploy-4471.
Scenario 2 (`db-vs-deploy`): the disagreement case — Logs blames the database,
Deploys blames the timing of deploy-4480; the Commander adjudicates.

Mock findings are included so the FULL pipeline + graph run with no API key
(deterministic demo). With OPENAI_API_KEY set, the agents derive findings live
from the raw slices below instead.
"""
from __future__ import annotations

from backend.schema import Finding

PAYMENTS = {
    "id": "payments",
    "title": "payments-service: checkout 500s",
    "incident_start": "2024-11-12T14:32:05Z",
    "window_start": "2024-11-12T14:20:00Z",
    "window_end": "2024-11-12T14:45:00Z",
    "alert": (
        "PagerDuty: [P1] payments-service checkout error rate > 25% for 3m. "
        "Customers report failed payments at checkout. Paged on-call at 14:35."
    ),
    "logs": """\
2024-11-12T14:29:58Z INFO  payments-service deploy hook: applying release deploy-4471 (rolling restart)
2024-11-12T14:30:11Z INFO  payments-service pod payments-7c9d started (release=deploy-4471)
2024-11-12T14:31:50Z INFO  payments-service handled checkout id=ck_8841 status=200
2024-11-12T14:32:05Z ERROR payments-service checkout id=ck_8842 -> 500
    java.lang.NullPointerException: Cannot invoke "PaymentToken.getExpiry()" because "token" is null
        at com.acme.payments.ChargeProcessor.charge(ChargeProcessor.java:147)
        at com.acme.payments.CheckoutHandler.handle(CheckoutHandler.java:88)
2024-11-12T14:32:06Z ERROR payments-service checkout id=ck_8843 -> 500 NullPointerException ChargeProcessor.java:147
2024-11-12T14:32:09Z ERROR payments-service checkout id=ck_8845 -> 500 NullPointerException ChargeProcessor.java:147
2024-11-12T14:33:20Z WARN  payments-service NPE count last 60s = 214 (was 0 before 14:32)
2024-11-12T14:34:01Z ERROR payments-service checkout id=ck_8901 -> 500 NullPointerException ChargeProcessor.java:147
""",
    "metrics": """\
metric window: 2024-11-12T14:20:00Z .. 14:45:00Z   (deploy markers: deploy-4471 @ 14:30:00Z)
checkout_error_rate(%):  14:25=0.2  14:28=0.1  14:30=0.3  14:32=27.4  14:34=31.9  14:40=33.1
p99_latency(ms):         14:25=240  14:28=255  14:30=261  14:32=268   14:34=271   14:40=265
cpu_util(%):             14:25=41   14:28=44   14:30=63   14:32=46    14:34=45    14:40=44
mem_util(%):             14:25=58   14:28=59   14:30=60   14:32=61    14:34=61    14:40=62
note: error_rate steps from ~0% to ~27% at 14:32, ~2 min after the 14:30 deploy marker. latency flat -> not saturation.
""",
    "deploys": """\
recent changes (payments-service), most recent first:
- deploy-4471  2024-11-12T14:30:00Z  author=jdoe  PR #2231 "ChargeProcessor: skip token refresh when cached"
      diff touches ChargeProcessor.charge(): removed null-guard on token before getExpiry()
- config-993   2024-11-12T11:02:00Z  author=ops    feature flag payments.retry_v2 = true
- deploy-4469  2024-11-12T09:14:00Z  author=asmith PR #2225 "bump datadog agent"
""",
    "mock_findings": [
        Finding(
            source="logs",
            finding="NullPointerException flood in ChargeProcessor.charge (token null) — 0 before 14:32, 214/min after. First error at 14:32:05, ~2 min after a deploy-4471 hook at 14:29:58.",
            timestamp="2024-11-12T14:32:05Z",
            severity="critical",
            confidence=0.9,
            points_to="deploy-4471",
        ),
        Finding(
            source="metrics",
            finding="checkout_error_rate steps 0.3% -> 27.4% at 14:32 and holds ~33%. p99 latency flat (~265ms) and CPU/mem nominal, so this is a code fault, not saturation. Steps right after the 14:30 deploy marker.",
            timestamp="2024-11-12T14:32:00Z",
            severity="critical",
            confidence=0.85,
            points_to="deploy-4471",
        ),
        Finding(
            source="deploys",
            finding="deploy-4471 to payments-service at 14:30 (PR #2231) removed the null-guard on token before getExpiry() in ChargeProcessor.charge — directly matches the NPE site and the 14:32 onset.",
            timestamp="2024-11-12T14:30:00Z",
            severity="high",
            confidence=0.92,
            points_to="deploy-4471",
        ),
    ],
}

DB_VS_DEPLOY = {
    "id": "db-vs-deploy",
    "title": "orders-service: latency + DB errors (Logs vs Deploys disagree)",
    "incident_start": "2024-11-12T14:32:00Z",
    "window_start": "2024-11-12T14:20:00Z",
    "window_end": "2024-11-12T14:45:00Z",
    "alert": (
        "PagerDuty: [P1] orders-service p99 latency > 4s and 'could not get connection' "
        "errors. On-call paged 14:36."
    ),
    "logs": """\
2024-11-12T14:30:02Z INFO  orders-service deploy hook: applying release deploy-4480 (rolling restart)
2024-11-12T14:32:01Z ERROR orders-service org.hibernate.exception: could not get JDBC Connection
    Caused by: HikariPool-1 - Connection is not available, request timed out after 5000ms (pool size 5)
2024-11-12T14:32:44Z ERROR orders-service HikariPool-1 timeout; active=5 idle=0 waiting=37
2024-11-12T14:34:10Z ERROR orders-service could not get JDBC Connection (pool exhausted)
2024-11-12T14:40:00Z WARN  orders-service DB connection wait p99 = 5001ms
""",
    "metrics": """\
metric window: 2024-11-12T14:20:00Z .. 14:45:00Z   (deploy markers: deploy-4480 @ 14:30:00Z)
p99_latency(ms):       14:25=310  14:28=320  14:30=340  14:32=4800 14:34=5200 14:40=5100
db_active_conns:       14:25=3    14:28=3    14:30=4    14:32=5    14:34=5    14:40=5   (max pool=5)
db_conn_wait_ms:       14:25=2    14:28=2    14:30=3    14:32=5000 14:34=5000 14:40=5001
error_rate(%):         14:25=0.1  14:28=0.1  14:30=0.2  14:32=18.0 14:34=22.0 14:40=21.0
note: DB pool pinned at max=5 with a long wait queue from 14:32. db host CPU/mem normal.
""",
    "deploys": """\
recent changes (orders-service), most recent first:
- deploy-4480  2024-11-12T14:30:00Z  author=mlee  PR #2240 "tune Hikari pool + add order-enrich call"
      diff: HikariCP maximumPoolSize 20 -> 5; adds a synchronous enrichment query per order
- config-1010  2024-11-12T08:00:00Z  author=ops   nightly index rebuild (completed 08:40)
""",
    "mock_findings": [
        Finding(
            source="logs",
            finding="HikariPool-1 connection timeouts ('could not get JDBC Connection') starting 14:32:01, 37 requests queued. Looks like the database / connection pool is the bottleneck.",
            timestamp="2024-11-12T14:32:01Z",
            severity="critical",
            confidence=0.8,
            points_to="db-connection-pool",
        ),
        Finding(
            source="metrics",
            finding="p99 latency 340ms -> 4800ms at 14:32; db_active_conns pinned at pool max (5) with 5000ms waits. DB host CPU/mem normal — the pool ceiling is the constraint, and it stepped right after the 14:30 deploy marker.",
            timestamp="2024-11-12T14:32:00Z",
            severity="critical",
            confidence=0.78,
            points_to="deploy-4480",
        ),
        Finding(
            source="deploys",
            finding="deploy-4480 to orders-service at 14:30 (PR #2240) cut HikariCP maximumPoolSize 20 -> 5 AND added a synchronous per-order enrichment query. Onset at 14:32 matches; the 'DB problem' is downstream of this config change, not organic DB load.",
            timestamp="2024-11-12T14:30:00Z",
            severity="high",
            confidence=0.9,
            points_to="deploy-4480",
        ),
    ],
}

SCENARIOS = {s["id"]: s for s in (PAYMENTS, DB_VS_DEPLOY)}
DEFAULT_SCENARIO = "payments"
