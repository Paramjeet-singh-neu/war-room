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
    "ground_truth_cause": "deploy-4471",
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
    "ground_truth_cause": "deploy-4480",
    "title": "orders-service: latency + DB errors (Logs vs Deploys disagree)",
    "incident_start": "2024-11-12T14:32:00Z",
    "window_start": "2024-11-12T14:20:00Z",
    "window_end": "2024-11-12T14:45:00Z",
    "alert": (
        "PagerDuty: [P1] orders-service p99 latency > 4s and 'could not get connection' "
        "errors. On-call paged 14:36."
    ),
    "logs": """\
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

# ─── Additional held-out incidents (used by the Weave Evaluation) ────────────
# Varied root causes so the Commander can't win by "always blame the latest deploy":
# a memory-leak deploy, a config change, and a NON-deploy cause (expired TLS cert).

MEMORY_LEAK = {
    "id": "memory-leak",
    "ground_truth_cause": "deploy-5012",
    "title": "search-service: OOM kills + rising latency",
    "incident_start": "2024-11-12T09:48:00Z",
    "window_start": "2024-11-12T09:20:00Z",
    "window_end": "2024-11-12T10:10:00Z",
    "alert": "PagerDuty: [P2] search-service pods OOMKilled & restarting; p99 climbing. Paged 09:52.",
    "logs": """\
2024-11-12T09:31:10Z INFO  search-service deploy hook: applying release deploy-5012 (rolling restart)
2024-11-12T09:48:02Z ERROR search-service pod search-5f7 OOMKilled (memory limit 1Gi exceeded)
2024-11-12T09:52:40Z ERROR search-service pod search-5f7 OOMKilled again; restart loop
2024-11-12T10:01:11Z WARN  search-service heap dump shows QueryCache retaining 780MB (unbounded)
""",
    "metrics": """\
metric window: 09:20 .. 10:10   (deploy markers: deploy-5012 @ 09:31)
mem_util(%):     09:25=44  09:31=47  09:38=66  09:44=88  09:48=99  09:55=99
p99_latency(ms): 09:25=120 09:31=124 09:38=180 09:44=260 09:48=900 09:55=1400
restarts:        09:25=0   09:31=1   09:38=0   09:44=0   09:48=2   09:55=5
note: memory climbs monotonically from the 09:31 deploy marker until OOM at 09:48. classic leak.
""",
    "deploys": """\
recent changes (search-service), most recent first:
- deploy-5012  2024-11-12T09:31:00Z  author=rkhan PR #2301 "add in-memory QueryCache for autocomplete"
      diff: introduces a process-wide cache with no eviction / max-size bound
- deploy-5009  2024-11-11T18:00:00Z  author=ops   bump base image (security patch)
""",
    "mock_findings": [
        Finding(source="logs", finding="Repeated OOMKilled restarts from 09:48; heap dump shows an unbounded QueryCache retaining 780MB. First OOM ~17min after deploy-5012 hook.", timestamp="2024-11-12T09:48:02Z", severity="high", confidence=0.85, points_to="deploy-5012"),
        Finding(source="metrics", finding="mem_util climbs monotonically 44%->99% from the 09:31 deploy marker to OOM at 09:48; latency follows. Classic memory leak, not a load spike.", timestamp="2024-11-12T09:48:00Z", severity="high", confidence=0.82, points_to="deploy-5012"),
        Finding(source="deploys", finding="deploy-5012 (PR #2301) added a process-wide QueryCache with no eviction bound — matches the unbounded-retention heap dump and the steady climb.", timestamp="2024-11-12T09:31:00Z", severity="high", confidence=0.9, points_to="deploy-5012"),
    ],
}

CACHE_MISCONFIG = {
    "id": "cache-misconfig",
    "ground_truth_cause": "config-1042",
    "title": "catalog-service: DB CPU spike after config flip",
    "incident_start": "2024-11-12T16:05:00Z",
    "window_start": "2024-11-12T15:45:00Z",
    "window_end": "2024-11-12T16:25:00Z",
    "alert": "PagerDuty: [P1] catalog DB CPU 95%+, query queue growing, catalog pages slow. Paged 16:08.",
    "logs": """\
2024-11-12T16:03:30Z INFO  catalog-service config reload: applied config-1042
2024-11-12T16:05:12Z WARN  catalog-service cache MISS rate jumped to 98% (was ~6%)
2024-11-12T16:06:40Z WARN  catalog-service db query p99 1800ms; read replicas saturating
""",
    "metrics": """\
metric window: 15:45 .. 16:25   (config markers: config-1042 @ 16:03)
cache_hit_rate(%): 15:50=94  15:58=93  16:03=92  16:05=2    16:10=2    16:20=3
db_cpu(%):         15:50=38  15:58=40  16:03=41  16:05=88   16:10=95   16:20=96
qps_to_db:         15:50=210 15:58=220 16:03=225 16:05=3100 16:10=3300 16:20=3250
note: cache hit-rate collapses 92%->2% exactly at the 16:03 config marker; DB CPU follows.
""",
    "deploys": """\
recent changes (catalog-service), most recent first:
- config-1042  2024-11-12T16:03:00Z  author=ops  set catalog.cache.ttl_seconds = 0 (intended: 600)
- deploy-4990  2024-11-12T12:00:00Z  author=tlin PR #2280 "tweak product sort"
""",
    "mock_findings": [
        Finding(source="logs", finding="Cache MISS rate jumped to 98% at 16:05 right after a config-1042 reload at 16:03; DB query p99 spiked and replicas saturated.", timestamp="2024-11-12T16:05:12Z", severity="high", confidence=0.8, points_to="config-1042"),
        Finding(source="metrics", finding="cache_hit_rate collapsed 92%->2% exactly at the 16:03 config marker; qps_to_db 225->3100 and db_cpu 41%->95%. Symptom is cache bypass, not organic load.", timestamp="2024-11-12T16:05:00Z", severity="critical", confidence=0.83, points_to="config-1042"),
        Finding(source="deploys", finding="config-1042 set catalog.cache.ttl_seconds = 0 (intended 600), disabling caching — directly explains the hit-rate collapse and DB CPU spike.", timestamp="2024-11-12T16:03:00Z", severity="high", confidence=0.9, points_to="config-1042"),
    ],
}

CERT_EXPIRY = {
    "id": "cert-expiry",
    "ground_truth_cause": "cert-expiry",
    # The cause is an expired TLS cert; models legitimately label it differently
    # (e.g. "tls-certificate-expiration"). Accept any label naming TLS/cert.
    "ground_truth_aliases": ["cert", "tls", "certificate"],
    "title": "gateway: upstream 503s with no recent deploy",
    "incident_start": "2024-11-12T00:00:30Z",
    "window_start": "2024-11-11T23:40:00Z",
    "window_end": "2024-11-12T00:20:00Z",
    "alert": "PagerDuty: [P1] api-gateway 503s to billing upstream; TLS handshake failures. Paged 00:03.",
    "logs": """\
2024-11-12T00:00:31Z ERROR api-gateway upstream=billing TLS handshake failed: certificate has expired (notAfter=2024-11-12T00:00:00Z)
2024-11-12T00:01:10Z ERROR api-gateway upstream=billing 503 (no healthy upstream, TLS error)
2024-11-12T00:05:00Z WARN  api-gateway 503 rate to billing = 100%
""",
    "metrics": """\
metric window: 23:40 .. 00:20   (deploy markers: NONE in window; last deploy 19:00 prior day)
upstream_503_rate(%): 23:45=0  23:55=0  00:00=4  00:01=72  00:05=100  00:15=100
tls_handshake_err:    23:45=0  23:55=0  00:00=12 00:01=240 00:05=520  00:15=510
billing_cpu(%):       23:45=22 23:55=23 00:00=23 00:01=22  00:05=22   00:15=22
note: 503s begin exactly at 00:00; upstream CPU normal. no deploy in the window.
""",
    "deploys": """\
recent changes (api-gateway / billing), most recent first:
- deploy-4955  2024-11-11T19:00:00Z  author=ops  routine config bump (5h before onset)
note: nothing deployed inside the incident window.
""",
    "mock_findings": [
        Finding(source="logs", finding="TLS handshake to billing upstream fails with 'certificate has expired (notAfter=2024-11-12T00:00:00Z)', causing 503s from 00:00:31.", timestamp="2024-11-12T00:00:31Z", severity="critical", confidence=0.92, points_to="cert-expiry"),
        Finding(source="metrics", finding="upstream_503_rate 0->100% and tls_handshake_err spike begin exactly at 00:00; billing CPU normal (22%). Not load, not the app — a TLS boundary failure.", timestamp="2024-11-12T00:00:30Z", severity="critical", confidence=0.8, points_to="cert-expiry"),
        Finding(source="deploys", finding="No deploy or config change inside the incident window (last change 19:00, 5h earlier). Rules deploys OUT as the cause.", timestamp="2024-11-11T19:00:00Z", severity="low", confidence=0.6, points_to="no-recent-change"),
    ],
}

SCENARIOS = {s["id"]: s for s in (PAYMENTS, DB_VS_DEPLOY)}
DEFAULT_SCENARIO = "payments"

# Held-out evaluation set (all five, with ground-truth root causes).
EVAL_SET = [PAYMENTS, DB_VS_DEPLOY, MEMORY_LEAK, CACHE_MISCONFIG, CERT_EXPIRY]
