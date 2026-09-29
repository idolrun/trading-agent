# 8. Deployment Architecture

[← Data model](07-data-model.md) · [Index](README.md) · Next: [Failure & recovery →](09-failure-recovery.md)

Source: TRD §3 (hosting row), §9, §10; PRD §13, §18. **One US-East VM running Docker Compose (app + Caddy), managed HA PostgreSQL in the same region with PITR, an external uptime monitor.** No Kubernetes, Kafka, Redis or vector DB.

## 8.1 Deployment topology and network boundaries

```mermaid
flowchart LR
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef sec fill:#fffde7,stroke:#f9a825,color:#5d4037
    classDef future fill:#fafafa,stroke:#9e9e9e,color:#757575,stroke-dasharray: 5 5

    subgraph CLIENTS["Zone: user devices (untrusted network)"]
        DESK["Desktop browser"]:::human
        PHONE["Phone PWA<br/>(iOS: added to Home Screen for push)"]:::human
    end

    subgraph INTERNET["Zone: public internet — third-party SaaS"]
        MAPI["User's Message API"]:::ext
        JEV["TypeSafe Jev API"]:::ext
        ANTH["Anthropic API"]:::ext
        OAI["OpenAI API"]:::ext
        DSK["DeepSeek API or US-hosted<br/>OpenAI-compatible endpoint (OD-7)"]:::ext
        BRK["Broker API + streams<br/>(TBD, OD-3) · paper + live"]:::ext
        MDF["Market data (broker feed,<br/>SPX entitlement, OD-4)"]:::ext
        PUSH["Browser push services"]:::ext
        UPT["External uptime monitor"]:::ext
        LSM["LangSmith (optional, dev only)"]:::future
    end

    subgraph REGION["Cloud region: US-East"]
        subgraph VM["App VM (single host) — Docker Compose"]
            direction TB
            CADDY["Caddy container<br/>TLS termination · :443 only public port<br/>rate limiting (logins)"]:::sec
            subgraph APPC["App container — ONE Python 3.12 asyncio process"]
                direction LR
                WEBM["web: FastAPI / SSE / Web Push<br/>/ingest · /health · dashboard"]:::code
                CORE["ingest · inbox · dispatcher · graph<br/>interpret · validate · decide · ledger<br/>cards · execution · timers · reconcile"]:::code
                CACHE["in-process quote dict<br/>+ chain TTL cache"]:::code
            end
            ENV["root-only env file<br/>TypeSafe, Anthropic, OpenAI, DeepSeek,<br/>AWS (if Bedrock), broker keys, VAPID, HMAC secret<br/>redacted from logs"]:::sec
            SPOOL["local append-only spool<br/>ONLY IF Message API has no<br/>redelivery and no backfill (OD-1)"]:::future
        end
        subgraph PGHA["Managed PostgreSQL 16 — separate hosts (OD-9)"]
            PRIM[("Primary")]:::store
            STBY[("Synchronous standby<br/>other AZ — auto failover")]:::store
            PITR[("PITR: base backups + WAL archive")]:::store
        end
    end

    OPER["Operator: server console<br/>(password recovery, runbooks)"]:::human

    DESK -->|"HTTPS 443, user session<br/>SameSite=Strict cookie"| CADDY
    PHONE -->|"HTTPS 443"| CADDY
    MAPI -->|"HTTPS POST /ingest<br/>HMAC + timestamp window"| CADDY
    UPT -->|"HTTPS GET /health"| CADDY
    CADDY -->|"HTTP (container network)"| WEBM
    WEBM --- CORE
    CORE --- CACHE
    CORE -.->|"reads secrets at start"| ENV
    CORE -->|"HTTPS: history API (backfill)"| MAPI
    CORE -->|"HTTPS"| JEV
    CORE -->|"HTTPS"| ANTH
    CORE -->|"HTTPS"| OAI
    CORE -->|"HTTPS"| DSK
    CORE <-->|"HTTPS REST + streaming"| BRK
    MDF -.->|"quote stream"| CACHE
    WEBM -.->|"Web Push (VAPID)"| PUSH
    PUSH -.-> PHONE
    PUSH -.-> DESK
    CORE ==>|"TLS, asyncpg pool<br/>private network — PROPOSED (OD-17)"| PRIM
    PRIM ==>|"sync replication (RPO ≈ 0)"| STBY
    PRIM -.->|"WAL archive (RPO ≤ 5 min)"| PITR
    UPT -.->|"page phone on failure"| PHONE
    OPER -.->|"SSH / console"| VM
    CORE -.->|"optional traces"| LSM
    SPOOL -.-> CORE
```

### What the diagram represents
Physical placement, trust zones and every network dependency. Outbound calls go to SaaS over HTTPS; the only inbound port is 443 on Caddy, carrying three kinds of traffic: the Message API webhook (HMAC-authenticated), the dashboard (email + password, allowlisted users), and the uptime probe.

### Component responsibilities
| Element | Responsibility | Notes |
|---|---|---|
| **Caddy** | TLS, reverse proxy, login rate limiting | Only public listener. |
| **App container** | Every module in one asyncio process | Simplifies ordering (per-provider workers are in-process) and removes network hops from the p95 budget. |
| **Env file** | All API keys and secrets | Root-only; never in code or logs (PRD §13). |
| **Managed Postgres** | All state; HA with automatic failover; PITR | **Must not share a host or disk with the app** (PRD §18, C93). |
| **External uptime monitor** | Probes `/health` (fails if dispatcher heartbeat > 15 s old) | Pages the phone — the only mitigation for system-held stops during an outage. |
| **Push services** | Deliver Web Push to desktop and phone | Best-effort; dashboard latency is the SLO, push is not (PRD §12). |

### Network / security boundaries
1. **Public → Caddy (443):** HMAC on `/ingest`; email + password auth for allowlisted users (no self-registration), short device-bound server-side sessions, password step-up for manual entry and flatten, append-only audit log.
2. **App → SaaS:** only provider-side text and provider context leave the system (FR-8); enforced by the Jev state builder's types and the LLM scope-guard middleware.
3. **App → Postgres:** a separate failure domain. Network path is not specified in the TRD (OD-17); private networking + TLS is the proposed default.
4. **Operator:** server console only — the sole password-recovery path (PRD §13).

### Important dependencies and their deployment implications
| Dependency | If slow/down | Mitigation |
|---|---|---|
| Message API | No new signals | Feed-health alert; backfill on reconnect; catch-up mode |
| Jev | Every message on slow path | Latency rises; nothing lost |
| LLM vendors | Slow path fails | ≥ 2 independent vendors, circuit breakers, raw review card |
| Broker | No orders / fills | Status query by client order ID; broker-held stops continue |
| Market data | No validation, no system-held stops | See [09 §9.7](09-failure-recovery.md) (OD-12) |
| Postgres | Trading halts | HA failover ≤ 5 min RTO; webhook 503 → redelivery |

---

## 8.2 Environments and go-live stages

PRD §17 defines a staged go-live. The runtime topology is the same in every stage; only `submit_order()`'s mode and the broker account change.

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef gate fill:#fff3e0,stroke:#e65100,color:#bf360c

    R["1 Replay<br/>evalkit over labelled history<br/>no live feed, no broker"]:::code
    G1{"gate: 100% normal-card field accuracy<br/>0 wrong contracts · recall ≥ 98%"}:::gate
    S["2 Shadow ≥ 20 days<br/>live feed + cards<br/>submit_order() records only"]:::code
    G2{"gate: 0 invariant violations · p95 ≤ 5 s, p99 ≤ 8 s<br/>0 undetected outages · 0 lost msgs<br/>load + fault tests passed first"}:::gate
    P["3 Paper ≥ 10 days<br/>broker paper account (OD-16)"]:::code
    G3{"gate: every order path exercised<br/>0 unexplained reconciliation mismatches"}:::gate
    L["4 Live, minimum sizes ≥ 10 days"]:::code
    G4{"gate before size increase"}:::gate

    R --> G1 --> S --> G2 --> P --> G3 --> L --> G4
```

Each `(provider, model, prompt)` in the LLM chain must pass the replay gate on its own; config validation rejects live chain entries without a passing `gate_run` and chains with fewer than two independent vendors (TRD §4.8).

---

## 8.3 Scaling path (only on measured triggers)

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef future fill:#fafafa,stroke:#9e9e9e,color:#757575,stroke-dasharray: 5 5

    NOW["Today: 1 VM, 1 process<br/>asyncio per-provider workers<br/>~0.1 msg/s avg, bursts 50 / 10 s"]:::code
    S1["Step 1 — PROPOSED if load test 3 fails:<br/>parallel interpret, serial apply (OD-19)"]:::future
    S2["Step 2 — if multi-process needed:<br/>session-level pg advisory lock per provider"]:::future
    S3["Step 3 — only on TRD 15.2 triggers:<br/>Redis for derived quote cache"]:::future
    S4["Step 4 — only on TRD 15.1 triggers:<br/>Kafka behind inbox boundary, key = provider_id"]:::future

    NOW -.-> S1 -.-> S2 -.-> S3 -.-> S4
```

The explicit prohibition in TRD §12: if only the single-provider slow-path burst test fails, apply Step 1; **do not** add Kafka or Redis for it.
