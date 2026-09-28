# Coding Agent Engineering Standards

## Purpose

This document is the engineering contract for any coding agent working on this project.

The agent must write production-quality Python with a strong focus on correctness, maintainability, readability, type safety, testability, observability, security, explicit failure handling, and simple architecture.

Primary stack:

- Python
- `uv` for dependency/project management
- FastAPI
- Pydantic 2.x
- SQLModel
- SQLAlchemy 2.x
- PostgreSQL
- Alembic
- LangChain
- LangGraph
- pytest
- Ruff
- mypy (or the project's configured type checker)

Do not introduce additional frameworks or infrastructure unless a concrete requirement justifies it.

---

# 1. Version Policy

Before adding or upgrading a dependency, the coding agent MUST:

1. Check the official documentation/release notes.
2. Check the current stable release.
3. Check compatibility with related packages.
4. Prefer the latest stable compatible versions.
5. Never blindly copy versions from old tutorials, blog posts, or generated code.
6. Avoid unrelated dependency upgrades.
7. Run tests and relevant lint/type checks after dependency changes.

Do not treat versions written in this document as permanent pins. Resolve current compatible versions at implementation time.

At the time this document was created, official sources reported FastAPI 0.141.1, SQLModel 0.0.47, SQLAlchemy 2.0.54, and PostgreSQL 18.6 as the current supported minor release. Verify these again before use. Pydantic, LangChain, and LangGraph should likewise be resolved from their current official documentation.

Never introduce deprecated APIs when a supported API exists. In particular, avoid legacy SQLAlchemy 1.x query patterns and outdated LangChain tutorials/APIs.

---

# 2. Package Management — uv

`uv` is the required Python package/project manager.

Use commands such as:

```bash
uv init
uv add <package>
uv add --dev <package>
uv remove <package>
uv sync
uv run <command>
uv lock
uv tree
uv audit
```

Do not maintain Poetry, Pipenv, Conda, or a second dependency-management system unless explicitly required by deployment constraints.

`pyproject.toml` and `uv.lock` are the dependency source of truth. Commit both. Do not manually edit `uv.lock`.

CI and deployment should use the locked dependency set.

---

# 3. Core Design Principles

## SOLID

### Single Responsibility

A module/class/function should have one coherent reason to change. Do not put API handling, business logic, email, persistence, and external integrations into one class.

Do not interpret SRP as requiring a class for every function. Small related functions can remain together.

### Open/Closed

Prefer designs that can be extended without repeatedly modifying stable core logic. Use protocols/interfaces when they provide real value, not mechanically.

### Liskov Substitution

Implementations of an abstraction must preserve its contract. Do not create fake implementations that violate expected behavior.

### Interface Segregation

Prefer small focused interfaces over giant service interfaces.

### Dependency Inversion

High-level application/domain logic should depend on abstractions where useful rather than concrete infrastructure. Do not create abstractions merely to make code look enterprise.

## DRY — Don't Repeat Yourself

Every piece of **knowledge** should have a single authoritative representation.

Avoid duplicated business rules, validation rules, constants, serialization logic, authentication logic, SQL, and configuration.

However, do not over-apply DRY. Similar-looking code is not necessarily the same knowledge. Duplication can be cheaper than the wrong abstraction.

## KISS — Keep It Simple

Prefer straightforward code and explicit data flow over factories, registries, adapters, managers, and abstraction layers that do not solve a real problem.

Before adding an abstraction, ask:

1. What concrete problem does it solve?
2. Does that problem exist today?
3. Does it reduce complexity or move complexity elsewhere?
4. Is there a simpler implementation?

## YAGNI — You Aren't Gonna Need It

Do not implement speculative functionality. Do not add unused configuration, caches, event buses, microservices, Kafka, Redis, vector databases, or agent supervisors without a current requirement.

## GRASP

Use Information Expert, Creator, Controller, Low Coupling, High Cohesion, Polymorphism, Pure Fabrication, Indirection, and Protected Variations when assigning responsibilities.

Do not interpret GRASP as requiring an object-oriented class for every operation. Python functions and modules are often the simpler choice.

## CUPID

Prefer code that is:

- Composable
- Unix-philosophy oriented
- Predictable
- Idiomatic
- Domain-based

Prefer small functions with explicit inputs/outputs and predictable side effects.

---

# 4. Python Standards

Use modern Python syntax supported by the project's selected Python version.

Prefer precise typing with:

- built-in generic types
- `collections.abc`
- `Protocol`
- `TypedDict`
- `Literal`
- `Annotated`
- `Enum`
- dataclasses where appropriate

Avoid `Any` unless a real boundary makes precise typing unavailable. Avoid broad `# type: ignore`; if necessary, make the suppression narrow and explain why.

Use descriptive names. Prefer `provider_position_id` over `pp`.

Comments should explain **why**, not restate obvious code.

---

# 5. Asyncio Rules

Use `async`/`await` for I/O-bound asynchronous operations.

Do not block the event loop with blocking HTTP clients, `time.sleep()`, heavy synchronous filesystem work, or CPU-heavy computation.

Use async-compatible libraries where available. For CPU-heavy work, use an appropriate worker/process/executor rather than pretending `async def` makes CPU work non-blocking.

Do not share an `AsyncSession` across concurrent asyncio tasks.

---

# 6. FastAPI Standards

## Thin routes

FastAPI routes should primarily handle HTTP concerns:

- request parsing
- dependency injection
- authentication/authorization
- invoking the application/use-case layer
- mapping results to HTTP responses

Avoid large business workflows inside route functions.

Preferred flow:

```text
HTTP request
    -> FastAPI route
    -> application/use-case service
    -> domain logic
    -> repository/infrastructure
    -> response schema
```

## Pydantic request/response schemas

Prefer separate API schemas such as:

```text
CreateUserRequest
UpdateUserRequest
UserResponse
UserListResponse
```

Do not expose database/SQLModel entities directly as the public API contract unless deliberately justified.

## Dependency injection

Use FastAPI dependencies for database sessions, authenticated users, authorization, request-scoped services, and other appropriate request/application dependencies.

Avoid global mutable request state.

## HTTP semantics

Use appropriate HTTP methods, status codes, response models, validation errors, authentication, and authorization. Do not return `200` for everything.

---

# 7. Pydantic Standards

Use Pydantic for:

- external input validation
- API request/response schemas
- configuration/settings
- structured LLM output
- validation at system boundaries

Use current Pydantic 2 APIs such as `model_validate()` and `model_dump()`.

Keep structural validation separate from business validation:

```text
External input
    -> Pydantic type/shape validation
    -> domain/business validation
```

Do not move every business rule into Pydantic validators merely because it is possible.

---

# 8. SQLModel / SQLAlchemy Standards

Use SQLModel where it provides useful integration with the application, while understanding that SQLModel is built on SQLAlchemy and Pydantic.

The agent must understand:

- sessions
- transactions
- relationships
- loading strategies
- indexes
- constraints
- connection pools
- async behavior
- transaction isolation
- query performance

Use SQLAlchemy 2.x style statements. Avoid legacy `Query`-style APIs.

For async database access, use appropriate async engines/sessions and explicit loading. Avoid accidental implicit I/O through lazy relationships in async code.

---

# 9. PostgreSQL Standards

PostgreSQL is the authoritative persistent store when it owns domain state.

Use database constraints to enforce invariants where practical:

```text
PRIMARY KEY
UNIQUE
FOREIGN KEY
CHECK
NOT NULL
```

Do not rely only on application-level existence checks for uniqueness or concurrency-sensitive invariants.

## Transactions

Use transactions when multiple changes must succeed or fail together.

Do not hold a database transaction open while waiting for a slow LLM or external API call unless there is a deliberate reason.

## Migrations

Use Alembic for schema changes.

Every schema change should include the appropriate migration and consider:

- locks
- large-table behavior
- indexes
- backfills
- nullable-to-non-null transitions
- backward compatibility
- rollback strategy where practical

Never use manual production schema changes as the normal workflow.

---

# 10. Repository and Layering Rules

Use repositories when they provide a meaningful boundary between application/domain code and persistence.

Do not automatically create a repository for every table.

A reasonable architecture is:

```text
API
  -> Application / Use Cases
      -> Domain
          -> Repository / Infrastructure
              -> PostgreSQL / external services
```

The exact folder structure should follow project complexity rather than architecture fashion.

Example structure:

```text
src/app/
├── api/
│   ├── routes/
│   ├── dependencies.py
│   └── errors.py
├── domain/
│   ├── entities/
│   ├── value_objects/
│   ├── services/
│   └── rules/
├── application/
│   ├── commands/
│   ├── queries/
│   └── services/
├── infrastructure/
│   ├── db/
│   ├── repositories/
│   └── external/
├── ai/
│   ├── prompts/
│   ├── models/
│   ├── tools/
│   └── graphs/
├── config/
└── main.py
```

Do not create empty layers just for appearance.

---

# 11. LangChain Standards

Use LangChain for capabilities that genuinely benefit from its abstractions:

- model integrations
- structured output
- tool interfaces
- prompt composition
- model abstraction
- retrieval when required

Do not use LangChain for ordinary Python logic.

Prefer structured output for machine-consumed LLM results.

Conceptually:

```text
Unstructured input
    -> LLM interpretation
    -> Pydantic structured result
    -> deterministic validation
    -> application decision
```

Never treat raw LLM text as authoritative application state.

Always verify current LangChain documentation before using an API because LangChain evolves quickly and older tutorials can be obsolete.

---

# 12. LangGraph Standards

Use LangGraph when the workflow genuinely needs stateful orchestration, branching, retries, persistence/checkpointing, human-in-the-loop behavior, resumability, or durable execution.

Do not use LangGraph simply because the project contains an LLM.

Keep nodes focused. A node should generally:

1. receive explicit state/context
2. perform one coherent operation
3. return explicit state updates

Prefer:

```text
load_context
    -> interpret
    -> validate
    -> decide
    -> approval
    -> execute
```

rather than a single `run_everything()` node.

## Side effects

Treat external side effects carefully:

- placing orders
- sending emails
- charging cards
- modifying external systems

Side effects should be explicit, auditable, and idempotent where possible.

A workflow retry does not prove that the external operation did not already happen.

---

# 13. LLM Safety and Deterministic Logic

LLMs should interpret unstructured information. Deterministic application code should enforce business rules.

Preferred:

```text
LLM
  -> structured result
  -> deterministic validation
  -> business rules
  -> decision
```

Avoid:

```text
LLM
  -> direct database mutation
```

or:

```text
LLM
  -> direct irreversible side effect
```

For trading/financial workflows in particular:

- never infer missing numeric values
- never silently correct values
- never execute ambiguous instructions
- never let model confidence alone authorize an irreversible action
- validate contracts, quantities, prices, identifiers, and state transitions in code

---

# 14. State and Context

Keep these concepts separate:

### Domain state

Authoritative state stored in PostgreSQL.

### Workflow state

LangGraph execution/checkpoint state.

### LLM context

Information intentionally provided to a model for one reasoning step.

### Cache/hot state

Optional performance state such as Redis, only if actually required.

A cache must not silently become the authoritative source of critical business state.

---

# 15. Error Handling

Never silently swallow errors.

Do not use:

```python
try:
    ...
except Exception:
    pass
```

Handle errors where recovery is possible, translate them at appropriate boundaries, log useful context, and propagate them when the caller needs to decide what to do.

Use domain-specific exceptions when they provide useful semantics. Do not create dozens of exception types without a reason.

---

# 16. Logging and Observability

Logs should answer:

- what happened?
- when?
- where?
- for which request/job/signal?
- what was the outcome?
- what failed?

Prefer structured logging and correlation identifiers such as:

```text
request_id
message_id
signal_id
provider_id
order_id
workflow_id
```

Never log API keys, passwords, tokens, or secrets. Be careful with sensitive user data and raw LLM prompts.

---

# 17. Testing

Use pytest.

Include appropriate:

### Unit tests

Business rules, validators, parsers, transformations, and calculations.

### Integration tests

PostgreSQL, repositories, FastAPI dependencies, and external adapters where practical.

### API tests

Status codes, validation, response schemas, authentication, authorization, and error behavior.

### LangGraph/workflow tests

Node behavior, branching, retries, state transitions, checkpoint/resume behavior, and failure paths.

### Property-based tests

Use Hypothesis when meaningful invariants or large input spaces justify it.

Test observable behavior rather than implementation details.

Tests that depend on PostgreSQL-specific behavior should run against PostgreSQL; SQLite is not a perfect substitute for PostgreSQL transactions, constraints, locking, types, or query behavior.

---

# 18. Security

Always validate and authorize requests.

Never trust client-provided:

```text
user_id
role
permissions
account_id
provider_id
```

Derive authorization context from authenticated identity and server-side state.

Never hardcode credentials. Use typed configuration and environment/secret management.

---

# 19. Configuration

Centralize application configuration in typed settings.

Do not scatter `os.getenv()` throughout business logic.

Example:

```python
class Settings(BaseSettings):
    database_url: PostgresDsn
    environment: Literal["development", "test", "production"]
```

Configuration should be loaded at the appropriate application boundary.

---

# 20. Time and Dates

Use timezone-aware datetimes.

Prefer UTC for persisted timestamps unless the domain explicitly requires another representation.

Be explicit about market/session time zones, expiration times, and date boundaries.

Never rely on the server's local timezone.

---

# 21. Concurrency and Idempotency

Any operation that can be retried must be evaluated for duplicate execution.

Use idempotency keys and database uniqueness constraints where appropriate:

```text
external_message_id
order_client_id
event_id
workflow_id
```

Do not rely only on a read-then-insert existence check because concurrent operations can race.

Use transactions, constraints, and appropriate locking when required.

---

# 22. External Integrations

Wrap external providers behind focused adapters.

Example:

```python
class BrokerClient(Protocol):
    async def submit_order(...): ...
    async def get_order(...): ...
    async def cancel_order(...): ...
```

Application logic should not contain provider-specific HTTP details.

Handle:

- timeout
- rate limits
- authentication failure
- malformed responses
- temporary outages
- duplicate submission risk

Retries must be safe. Never blindly retry an operation that may already have succeeded remotely.

---

# 23. Performance

Measure before optimizing.

Watch for:

- N+1 queries
- unnecessary LLM calls
- excessive serialization
- unbounded concurrency
- connection-pool exhaustion
- blocking work inside async paths
- oversized prompts
- unnecessary database round trips

Add database indexes based on real query patterns. Do not add indexes everywhere.

Do not add Redis or Kafka merely because they are fast or popular.

---

# 24. Code Style

Prefer explicit, readable code over clever code.

Good:

```python
def calculate_total(items: list[Item]) -> Decimal:
    return sum((item.price for item in items), Decimal("0"))
```

Avoid magic, reflection-heavy designs, giant generic base classes, unnecessary decorators, and dynamic registries unless there is a concrete requirement.

---

# 25. Git and Change Discipline

Keep changes focused.

Do not mix a feature with unrelated formatting, dependency upgrades, and broad refactoring unless necessary.

Before modifying code:

1. inspect the existing architecture
2. understand the relevant module
3. search for existing abstractions
4. reuse established patterns
5. identify tests
6. make the smallest correct change

Do not rewrite working code merely because you would structure it differently.

---

# 26. Coding Agent Workflow

Every coding task should follow this sequence.

## Step 1 — Understand

Read the relevant PRD/TRD, source files, tests, configuration, and existing patterns before coding.

## Step 2 — Search before creating

Search for existing services, utilities, repositories, schemas, helpers, dependencies, and validation logic before adding new ones.

## Step 3 — Identify boundaries

Determine the API, application, domain, persistence, external-service, and LLM boundaries involved.

## Step 4 — Implement minimally

Make the smallest correct change that satisfies the requirement. Avoid speculative architecture.

## Step 5 — Validate

Run the project's configured checks. Typical commands are:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Do not assume every repository uses all of these commands; follow its configuration.

## Step 6 — Review the diff

Ask:

- Did I duplicate logic?
- Did I introduce unnecessary abstraction?
- Did I break existing behavior?
- Did I introduce a race condition?
- Did I introduce an N+1 query?
- Are transaction boundaries correct?
- Did I expose internal database models through the API?
- Did I add an unnecessary dependency?
- Did I add unnecessary LLM calls?
- Did I handle failure paths?
- Did I add tests for the changed behavior?

---

# 27. Definition of Done

A task is not complete merely because the code runs.

Before declaring completion:

- [ ] Requirements are satisfied.
- [ ] Existing architecture was respected.
- [ ] No unnecessary dependency was added.
- [ ] No unnecessary abstraction was introduced.
- [ ] Types are correct.
- [ ] Boundary validation exists.
- [ ] Database transaction boundaries are appropriate.
- [ ] Async code does not block the event loop.
- [ ] External calls have appropriate timeout/error handling.
- [ ] Critical operations are idempotent where necessary.
- [ ] Tests cover changed behavior.
- [ ] Lint/format/type checks pass.
- [ ] Migration exists for schema changes.
- [ ] Secrets are not exposed.
- [ ] Logs have useful correlation information.
- [ ] No deprecated API was introduced.
- [ ] The final diff is focused and understandable.

---

# 28. Architecture Decision Rule

When two designs are technically valid, prefer in this order:

1. Correctness
2. Simplicity
3. Maintainability
4. Testability
5. Observability
6. Performance based on measured requirements
7. Scalability based on actual requirements
8. Future flexibility

Do not optimize for theoretical scale at the expense of today's correctness.

---

# 29. Final Rules for the Coding Agent

The coding agent MUST:

1. Read before changing.
2. Search before creating.
3. Reuse before duplicating.
4. Prefer simple designs.
5. Avoid speculative features.
6. Keep business logic out of HTTP routes.
7. Keep infrastructure concerns out of domain logic.
8. Keep LLM interpretation separate from deterministic business decisions.
9. Treat PostgreSQL as authoritative when it owns domain state.
10. Use Pydantic for explicit validation at system boundaries.
11. Use SQLModel/SQLAlchemy according to current supported APIs.
12. Use async correctly.
13. Make external side effects explicit and safe to retry.
14. Test failure paths, not only happy paths.
15. Verify current official documentation before using unfamiliar APIs.
16. Prefer official documentation over old tutorials.
17. Never add infrastructure merely because it is popular.
18. Never silently guess when requirements are ambiguous.
19. Raise architectural concerns when a requested implementation conflicts with correctness or reliability.
20. Keep the codebase understandable to another engineer.

---

# Official References

Use these as primary references when checking current framework behavior and APIs:

- FastAPI: https://fastapi.tiangolo.com/
- FastAPI release notes: https://fastapi.tiangolo.com/release-notes/
- Pydantic: https://docs.pydantic.dev/
- SQLModel: https://sqlmodel.tiangolo.com/
- SQLModel release notes: https://sqlmodel.tiangolo.com/release-notes/
- SQLAlchemy: https://docs.sqlalchemy.org/
- LangChain Python: https://docs.langchain.com/oss/python/langchain/
- LangGraph Python: https://docs.langchain.com/oss/python/langgraph/
- PostgreSQL: https://www.postgresql.org/docs/
- PostgreSQL versioning: https://www.postgresql.org/support/versioning/
- uv: https://docs.astral.sh/uv/

When official documentation and this document disagree because a framework has changed, prefer the **current official documentation**, then update this document if the project convention should change.

This document defines engineering principles and coding behavior. It does not replace the project's PRD, TRD, security requirements, or domain-specific business rules.
