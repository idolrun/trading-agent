# 14. Jev

[← Layers](13-layers.md) · [Index](README.md)

Jev is the primary interpreter. One TypeSafe `system_one` call answers a fixed question catalog over candidates the code already found. Jev chooses among those candidates. It does not invent a number, pick a quantity, or write a link.

The model is pinned (`jev-1.13.0`). The question-set version is a hash of the catalog, stored on the audit row with the model that was requested and the model that answered.

## 14.1 Call, map, route

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    IN["ProviderContext + Candidates<br/>no user lots or balances"]:::code
    ON{"Jev enabled?"}:::code
    STATE["build_jev_state"]:::code
    SCOPE["scope violation<br/>raw review + urgent alert<br/>no further model"]:::fail
    QS["build_questions<br/>catalog fingerprint"]:::code
    CALL["system_one<br/>typesafe semaphore<br/>queue 250 ms · request 700 ms · step 1200 ms<br/>429 or 529 retried once"]:::model
    AUDIT[("model_calls")]:::store
    FAIL{"JevFailed.kind"}:::fail
    MAP["map_answers<br/>literal only from a candidate"]:::code
    ROUTE["route()"]:::code
    FEED["feed only"]:::code
    ASM["assemble"]:::code
    APPLY["decide_apply"]:::code
    GUARD["feed guard<br/>position open and the text<br/>could act on it"]:::code
    LINK["link doubt<br/>raw review, no other model"]:::human
    SEM["semantic doubt<br/>optional LLM, else review<br/>a Jev draft is shown if the LLM is off"]:::model
    MF["model failure<br/>optional LLM, else review"]:::model

    IN --> ON
    ON -->|"disabled"| MF
    ON -->|"yes"| STATE
    STATE -->|"user data in the request"| SCOPE
    SCOPE --> AUDIT
    STATE --> QS --> CALL
    CALL --> AUDIT
    CALL -->|"no usable answer"| FAIL
    FAIL -->|"scope_violation"| SCOPE
    FAIL -->|"timeout, queue timeout, rate limit, api error, connection, or invalid"| MF
    CALL -->|"JevAnswered"| MAP --> ROUTE
    ROUTE -->|"chatter, commentary, or watch at or above t_chat"| FEED
    ROUTE -->|"same types, feed guard blocks"| GUARD
    GUARD --> SEM
    ROUTE -->|"which earlier position"| LINK
    ROUTE -->|"complete and confident"| ASM
    ROUTE -->|"unsure type, several signals, missing field, or a condition"| SEM
    FEED --> APPLY
    ASM --> APPLY
```

A failure is a `JevFailed` value. It does not raise, and it does not poison the inbox event.

`map_answers` keeps a field only when Jev picked a code-found candidate. The stored value is that candidate's exact span. Two numbers claiming one role, a required field left unstated, or a confidence under `t_field` are slow reasons, not a settled signal. Doubts about which earlier position is meant are review reasons. Those never go to the LLM.

`route()` is pure. The message graph sends each kind to a different place:

| Kind | Examples | Next |
|---|---|---|
| Settled | feed only at or above `t_chat`; assemble | `decide_apply` |
| `link` | re-entry among several positions | raw review |
| `semantic` | unsure type, several signals, missing field, a worded condition, feed guard | optional LLM, else a person. If the LLM is off and Jev left a draft, the card shows that draft |
| `model_failure` | off, timeout, queue timeout, rate limit, API error, connection, invalid answer | optional LLM, else review |
| scope violation | the request contained user-portfolio data | raw review and an urgent alert. Nothing is sent |

The feed guard refuses a feed-only verdict when the lane holds a position or today's setup and the text has an action word, a leaving phrase, a provider term, a fraction, or a price. Confidence does not override that. Over-blocking costs a review card. Under-blocking can drop an exit.
