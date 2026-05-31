"""Hard held-out eval set — 12 ambiguous incidents with real headroom.

Design goals (so a 70B reading only its own slice can't ace it blind):
  * Several incidents have a RED-HERRING recent deploy that is NOT the cause —
    punishing the "always blame the latest deploy" reflex.
  * Distinct root-cause TYPES: external dependency outage, disk-full, DNS, multiple
    competing deploys (timing discriminates), DB migration lock, legit traffic
    spike (scale, don't roll back), bad feature flag, secret rotation, kafka
    consumer config, JVM heap config, redis eviction, clock skew.
  * Noisy/unrelated log lines mixed in.

Each scenario carries `ground_truth_cause` plus `ground_truth_aliases` (semantic
labels the model may legitimately use). No `mock_findings` — these are scored live.
"""
from __future__ import annotations

H = []


def _add(**kw):
    H.append(kw)


_add(
    id="multi-deploy",
    ground_truth_cause="deploy-7002",
    ground_truth_aliases=[],
    title="api: 500s — three deploys in the window",
    incident_start="2024-11-12T14:27:00Z",
    window_start="2024-11-12T12:45:00Z",
    window_end="2024-11-12T15:05:00Z",
    alert="PagerDuty: [P1] api error rate spike to 22% starting ~14:27.",
    logs="""\
2024-11-12T14:27:05Z ERROR api UpstreamSerializationError: cannot parse user payload (new field 'tier' missing)
2024-11-12T14:27:40Z ERROR api 500 /v2/users (serialization) x rising
2024-11-12T14:31:00Z WARN  api error rate 22% sustained
""",
    metrics="""\
metric window: 12:45..15:05  (deploy markers: deploy-7001@13:00, deploy-7002@14:25, deploy-7003@14:50)
error_rate(%): 13:00=0.3 13:30=0.2 14:25=0.4 14:27=21.8 14:40=22.4 14:50=22.1 15:00=22.0
p99_ms:        13:00=180 14:25=185 14:27=190 14:40=188 15:00=187
note: error rate steps up at 14:27 — i.e. right after deploy-7002 @14:25, NOT 7001 or 7003.
""",
    deploys="""\
recent changes (api), most recent first:
- deploy-7003  2024-11-12T14:50:00Z author=kim  PR#3110 "tweak logging format"
- deploy-7002  2024-11-12T14:25:00Z author=ravi PR#3108 "serialize users with new 'tier' field"
- deploy-7001  2024-11-12T13:00:00Z author=lee  PR#3105 "add /v2/health endpoint"
""",
)

_add(
    id="stripe-outage",
    ground_truth_cause="upstream-dependency-outage",
    ground_truth_aliases=["stripe", "upstream", "third-party", "gateway", "dependency", "provider"],
    title="checkout: payment failures (recent deploy is a red herring)",
    incident_start="2024-11-12T10:15:00Z",
    window_start="2024-11-12T09:55:00Z",
    window_end="2024-11-12T10:40:00Z",
    alert="PagerDuty: [P1] checkout success rate dropped to 40%. A deploy went out 10 min ago.",
    logs="""\
2024-11-12T10:05:00Z INFO  checkout deploy hook: applied deploy-6120 (copy change on receipt email)
2024-11-12T10:15:02Z ERROR checkout stripe.api call failed: HTTP 503 from api.stripe.com (request_id rq_88)
2024-11-12T10:15:30Z ERROR checkout stripe 503 (Service Unavailable) — retries exhausted
2024-11-12T10:20:00Z WARN  checkout stripe error budget burned; status.stripe.com reports 'degraded'
""",
    metrics="""\
metric window: 09:55..10:40  (deploy markers: deploy-6120@10:05)
checkout_success(%):   10:00=99 10:05=99 10:15=41 10:25=39 10:35=40
stripe_5xx_rate(%):    10:00=0  10:05=0  10:15=58 10:25=61 10:35=60
internal_cpu(%):       10:00=33 10:05=34 10:15=33 10:25=34 10:35=33
note: our CPU/latency normal; failures are all 503s FROM stripe. deploy-6120 only touched email copy.
""",
    deploys="""\
recent changes (checkout), most recent first:
- deploy-6120  2024-11-12T10:05:00Z author=mona PR#2990 "update receipt email copy" (no payment-path code)
- deploy-6118  2024-11-11T16:00:00Z author=ops  bump base image
""",
)

_add(
    id="disk-full",
    ground_truth_cause="disk-full",
    ground_truth_aliases=["disk", "storage", "enospc", "no space", "volume"],
    title="ingest: write failures",
    incident_start="2024-11-12T03:12:00Z",
    window_start="2024-11-12T02:50:00Z",
    window_end="2024-11-12T03:35:00Z",
    alert="PagerDuty: [P2] ingest-service write errors climbing overnight.",
    logs="""\
2024-11-12T03:12:01Z ERROR ingest java.io.IOException: No space left on device (/var/lib/ingest)
2024-11-12T03:13:00Z ERROR ingest failed to flush segment; disk write error
2024-11-12T03:20:00Z WARN  ingest log rotation failed: cannot create file (ENOSPC)
""",
    metrics="""\
metric window: 02:50..03:35  (deploy markers: none in window; last deploy 3 days ago)
disk_util(%):   02:50=92 03:00=96 03:10=99 03:12=100 03:20=100 03:30=100
write_err_rate: 02:50=0  03:00=0  03:10=2  03:12=40  03:20=88  03:30=90
cpu(%):         02:50=30 03:12=31 03:30=30
note: disk_util crossed 100% at 03:12 exactly when write errors began. slow climb overnight, no deploy.
""",
    deploys="""\
recent changes (ingest), most recent first:
- deploy-5500  2024-11-09T11:00:00Z author=sun  PR#2700 "add gzip to archive job"
note: nothing in the incident window.
""",
)

_add(
    id="dns-failure",
    ground_truth_cause="dns-resolution-failure",
    ground_truth_aliases=["dns", "resolve", "unknownhost", "resolution", "name resolution"],
    title="worker: UnknownHostException to internal services",
    incident_start="2024-11-12T18:40:00Z",
    window_start="2024-11-12T18:20:00Z",
    window_end="2024-11-12T19:05:00Z",
    alert="PagerDuty: [P1] worker can't reach billing/notify; jobs failing. Deploy went out 18:30.",
    logs="""\
2024-11-12T18:30:00Z INFO  worker deploy hook: applied deploy-6400 (retry tuning)
2024-11-12T18:40:03Z ERROR worker java.net.UnknownHostException: billing.svc.internal
2024-11-12T18:40:10Z ERROR worker UnknownHostException: notify.svc.internal
2024-11-12T18:45:00Z WARN  worker all *.svc.internal lookups failing; 8.8.8.8 reachable, internal resolver timing out
""",
    metrics="""\
metric window: 18:20..19:05  (deploy markers: deploy-6400@18:30)
dns_resolve_err: 18:25=0  18:30=0  18:40=120 18:50=300 19:00=295
job_fail_rate(%):18:25=0  18:30=0  18:40=70  18:50=95  19:00=94
note: failures start 18:40, 10min AFTER the deploy; ALL internal hostnames fail at once (cluster DNS), not just one service.
""",
    deploys="""\
recent changes (worker), most recent first:
- deploy-6400  2024-11-12T18:30:00Z author=tao PR#3001 "increase HTTP retry from 2 to 3"
""",
)

_add(
    id="migration-lock",
    ground_truth_cause="deploy-7110",
    ground_truth_aliases=["migration", "lock", "alter table", "schema", "ddl"],
    title="orders: query timeouts during a schema migration",
    incident_start="2024-11-12T22:05:00Z",
    window_start="2024-11-12T21:45:00Z",
    window_end="2024-11-12T22:30:00Z",
    alert="PagerDuty: [P1] orders DB query timeouts; writes blocked.",
    logs="""\
2024-11-12T22:00:00Z INFO  orders deploy hook: applied deploy-7110 (includes db migration 0042)
2024-11-12T22:05:01Z ERROR orders QueryTimeout: lock wait timeout exceeded on table `orders`
2024-11-12T22:06:00Z WARN  orders migration 0042: ALTER TABLE orders ADD COLUMN ... holding ACCESS EXCLUSIVE lock
2024-11-12T22:12:00Z ERROR orders writes queuing behind table lock
""",
    metrics="""\
metric window: 21:45..22:30  (deploy markers: deploy-7110@22:00)
write_p99_ms:   21:50=40 22:00=42 22:05=9000 22:12=9000 22:20=9000
lock_waiters:   21:50=0  22:00=0  22:05=51   22:12=80   22:20=78
db_cpu(%):      21:50=35 22:05=38 22:20=37
note: writes block on a table lock from 22:05; CPU normal. deploy-7110 ran migration 0042 at 22:00.
""",
    deploys="""\
recent changes (orders), most recent first:
- deploy-7110  2024-11-12T22:00:00Z author=ben PR#3201 "add orders.region column (migration 0042, blocking ALTER)"
- deploy-7104  2024-11-12T15:00:00Z author=ana PR#3188 "fix typo in label"
""",
)

_add(
    id="traffic-spike",
    ground_truth_cause="traffic-surge",
    ground_truth_aliases=["traffic", "load", "capacity", "surge", "scale", "demand",
                          "campaign", "marketing", "email blast", "saturation"],
    title="web: latency under load (no code change)",
    incident_start="2024-11-12T20:00:00Z",
    window_start="2024-11-12T19:40:00Z",
    window_end="2024-11-12T20:25:00Z",
    alert="PagerDuty: [P2] web p99 latency high; site slow. Marketing email went out at 20:00.",
    logs="""\
2024-11-12T20:00:30Z WARN  web request queue depth rising; workers saturated
2024-11-12T20:02:00Z INFO  web all 200s, no new error signatures; just slow
2024-11-12T20:10:00Z WARN  web autoscaler at max replicas (cap=10)
""",
    metrics="""\
metric window: 19:40..20:25  (deploy markers: none in window; last deploy yesterday)
rps:           19:45=400 19:55=420 20:00=2600 20:10=2900 20:20=2850
p99_ms:        19:45=210 19:55=215 20:00=1900 20:10=2200 20:20=2150
error_rate(%): 19:45=0.2 20:00=0.4 20:10=0.5 20:20=0.5
cpu(%):        19:45=45  20:00=96  20:10=97  20:20=96
note: rps 6x at 20:00 (campaign), CPU pegged, errors LOW. healthy app, just over capacity. no deploy.
""",
    deploys="""\
recent changes (web), most recent first:
- deploy-6800  2024-11-11T10:00:00Z author=ivy PR#2900 "footer link update"
note: nothing in the incident window.
""",
)

_add(
    id="bad-flag",
    ground_truth_cause="config-7200",
    ground_truth_aliases=["feature flag", "flag", "config", "toggle"],
    title="feed: latency after a feature-flag flip",
    incident_start="2024-11-12T11:30:00Z",
    window_start="2024-11-12T11:10:00Z",
    window_end="2024-11-12T11:55:00Z",
    alert="PagerDuty: [P1] feed p99 5x; CPU high. A deploy shipped this morning.",
    logs="""\
2024-11-12T08:00:00Z INFO  feed deploy hook: applied deploy-7150 (morning release)
2024-11-12T11:28:00Z INFO  feed flag change: feed.ranking_v3 = true (config-7200)
2024-11-12T11:30:05Z WARN  feed ranking_v3 path: O(n^2) re-score per request; p99 climbing
2024-11-12T11:40:00Z WARN  feed CPU saturated on ranking
""",
    metrics="""\
metric window: 11:10..11:55  (markers: deploy-7150@08:00, config-7200@11:28)
p99_ms:  11:15=300 11:28=305 11:30=1600 11:40=1800 11:50=1750
cpu(%):  11:15=40  11:28=41  11:30=82   11:40=90   11:50=88
note: degradation starts 11:30, right after the 11:28 FLAG flip (config-7200) — not the 08:00 deploy.
""",
    deploys="""\
recent changes (feed), most recent first:
- config-7200  2024-11-12T11:28:00Z author=ops  feature flag feed.ranking_v3 = true
- deploy-7150  2024-11-12T08:00:00Z author=raj  PR#3160 "morning release: UI polish"
""",
)

_add(
    id="secret-rotation",
    ground_truth_cause="secret-rotation",
    ground_truth_aliases=["secret", "credential", "api key", "401", "auth", "token", "rotation"],
    title="sync: 401s from partner API",
    incident_start="2024-11-12T00:00:00Z",
    window_start="2024-11-11T23:40:00Z",
    window_end="2024-11-12T00:25:00Z",
    alert="PagerDuty: [P1] sync-service getting 401 Unauthorized from partner API at midnight.",
    logs="""\
2024-11-12T00:00:05Z ERROR sync partner API returned 401 Unauthorized (invalid api key)
2024-11-12T00:00:40Z ERROR sync 401 on all partner calls; key prefix sk_live_a1.. rejected
2024-11-12T00:05:00Z WARN  sync vault shows partner_api_key rotated at 2024-11-12T00:00:00Z; pods still using cached old key
""",
    metrics="""\
metric window: 23:40..00:25  (deploy markers: none in window)
partner_401_rate(%): 23:50=0 23:59=0 00:00=100 00:10=100 00:20=100
our_5xx(%):          23:50=0 00:00=0  00:10=0   00:20=0
note: clean 0->100% 401s exactly at 00:00; no deploy. secret rotated at midnight, app holds the old key.
""",
    deploys="""\
recent changes (sync), most recent first:
- deploy-6600  2024-11-10T09:00:00Z author=ned PR#2810 "pagination fix"
note: nothing in window. (a scheduled secret rotation ran at 00:00 outside the deploy system)
""",
)

_add(
    id="kafka-lag",
    ground_truth_cause="deploy-7300",
    ground_truth_aliases=["kafka", "consumer", "lag", "partition", "concurrency", "throughput"],
    title="events: consumer lag exploding",
    incident_start="2024-11-12T13:10:00Z",
    window_start="2024-11-12T12:50:00Z",
    window_end="2024-11-12T13:40:00Z",
    alert="PagerDuty: [P2] events consumer lag growing; downstream stale.",
    logs="""\
2024-11-12T13:05:00Z INFO  events deploy hook: applied deploy-7300
2024-11-12T13:10:00Z WARN  events consumer group lag 50k and rising
2024-11-12T13:25:00Z WARN  events only 2 partitions actively consumed (was 12)
""",
    metrics="""\
metric window: 12:50..13:40  (deploy markers: deploy-7300@13:05)
consumer_lag:    12:55=200 13:05=300 13:10=52000 13:25=180000 13:35=240000
msgs_per_sec:    12:55=12000 13:05=12000 13:10=2100 13:25=2050 13:35=2000
note: throughput collapsed and lag exploded right after deploy-7300 @13:05; consumer parallelism dropped 12->2.
""",
    deploys="""\
recent changes (events), most recent first:
- deploy-7300  2024-11-12T13:05:00Z author=om PR#3250 "refactor consumer; set concurrency=2 (was 12)"
- deploy-7290  2024-11-12T09:00:00Z author=su PR#3240 "metric label fix"
""",
)

_add(
    id="heap-config",
    ground_truth_cause="config-7400",
    ground_truth_aliases=["heap", "gc", "jvm", "memory", "garbage", "xmx"],
    title="risk-engine: long GC pauses after a JVM config change",
    incident_start="2024-11-12T17:20:00Z",
    window_start="2024-11-12T17:00:00Z",
    window_end="2024-11-12T17:45:00Z",
    alert="PagerDuty: [P1] risk-engine p99 spikes + periodic freezes.",
    logs="""\
2024-11-12T17:15:00Z INFO  risk-engine config-7400 applied: -Xmx reduced 8g -> 2g (cost-saving)
2024-11-12T17:20:02Z WARN  risk-engine GC pause 3.8s (Full GC); heap 1.9g/2g
2024-11-12T17:30:00Z WARN  risk-engine repeated Full GC every ~20s; requests stalling during pauses
""",
    metrics="""\
metric window: 17:00..17:45  (markers: config-7400@17:15)
gc_pause_ms:  17:05=40 17:15=45 17:20=3800 17:30=4100 17:40=3900
p99_ms:       17:05=260 17:15=265 17:20=4200 17:30=4500 17:40=4300
heap_used(%): 17:05=35 17:15=40 17:20=96 17:30=97 17:40=96
note: Full GC storms begin 17:20 right after the 17:15 heap-shrink config-7400. no deploy in window.
""",
    deploys="""\
recent changes (risk-engine), most recent first:
- config-7400  2024-11-12T17:15:00Z author=ops set JVM -Xmx 8g -> 2g
- deploy-7380  2024-11-12T10:00:00Z author=pat PR#3300 "add audit log line"
""",
)

_add(
    id="redis-eviction",
    ground_truth_cause="redis-memory-eviction",
    ground_truth_aliases=["redis", "cache", "eviction", "maxmemory", "evict"],
    title="catalog: DB load spike from cache eviction (deploy is a red herring)",
    incident_start="2024-11-12T15:50:00Z",
    window_start="2024-11-12T15:30:00Z",
    window_end="2024-11-12T16:15:00Z",
    alert="PagerDuty: [P1] catalog DB CPU 95%; pages slow. A deploy shipped at 15:40.",
    logs="""\
2024-11-12T15:40:00Z INFO  catalog deploy hook: applied deploy-6900 (add 'new' badge to product cards)
2024-11-12T15:50:01Z WARN  catalog redis: maxmemory reached, evicting keys (policy allkeys-lru)
2024-11-12T15:50:30Z WARN  catalog cache hit-rate fell 95% -> 20%; DB read QPS surging
""",
    metrics="""\
metric window: 15:30..16:15  (deploy markers: deploy-6900@15:40)
redis_used_mem(%): 15:35=88 15:40=92 15:50=100 16:00=100 16:10=100
cache_hit(%):      15:35=95 15:40=95 15:50=21 16:00=19 16:10=20
db_cpu(%):         15:35=40 15:40=41 15:50=93 16:00=95 16:10=94
note: redis hit maxmemory at 15:50 (slow growth all week) -> evictions -> DB load. deploy-6900 only added UI badge, 10min earlier.
""",
    deploys="""\
recent changes (catalog), most recent first:
- deploy-6900  2024-11-12T15:40:00Z author=zoe PR#2980 "add 'new' badge to product cards" (frontend only)
- deploy-6890  2024-11-12T09:00:00Z author=kai PR#2975 "logging"
""",
)

_add(
    id="clock-skew",
    ground_truth_cause="clock-skew",
    ground_truth_aliases=["clock", "ntp", "time", "skew", "drift", "jwt exp"],
    title="auth: token validation failures",
    incident_start="2024-11-12T06:30:00Z",
    window_start="2024-11-12T06:10:00Z",
    window_end="2024-11-12T06:55:00Z",
    alert="PagerDuty: [P1] auth rejecting valid tokens; logins failing on some pods.",
    logs="""\
2024-11-12T06:30:02Z ERROR auth JWT validation failed: token 'exp' in the past / 'nbf' in the future (skew)
2024-11-12T06:31:00Z ERROR auth failures only on pods auth-3, auth-7
2024-11-12T06:35:00Z WARN  auth chronyd: clock on auth-3 drifted +47s vs NTP; NTP sync had been failing since 06:00
""",
    metrics="""\
metric window: 06:10..06:55  (deploy markers: none in window)
auth_fail_rate(%): 06:15=0 06:29=0 06:30=33 06:40=34 06:50=33
clock_offset_s(max):06:15=1 06:29=2 06:30=47 06:40=49 06:50=48
note: failures ~1/3 of traffic (only skewed pods); offset jumped to ~47s at 06:30. no deploy. NTP sync failing.
""",
    deploys="""\
recent changes (auth), most recent first:
- deploy-6200  2024-11-11T14:00:00Z author=ola PR#2750 "rate-limit tweak"
note: nothing in window.
""",
)

HARD_EVAL_SET = H
