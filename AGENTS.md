# AGENTS.md — Binding architecture & working rules for `site-rag`

These rules apply to **every task** in this repository. They override any conflicting habit or default.
- **MUST** / **MUST NOT** = mandatory.
- **SHOULD** = expected unless you explain why not.

If a task spec conflicts with this file, **stop and ask**.

---

## 1. What we are building (non-negotiables)

A RAG chatbot that answers customer questions **only** from the public content of one website (first target: `https://www.fnx.co.il`, Hebrew). Every answer cites its source page, and the bot says "this is not on the site" when it cannot ground an answer.

Pipeline:
`discover → fetch → extract → enrich → chunk → index` → `retrieve → answer` → chat UI.

These four rules outrank everything else in this file:
1. **No invented content.** The bot MUST answer only from retrieved chunks of the target site. No external knowledge, no guessing.
2. **No answer without a source.** Every answer MUST cite the page or pages it came from. If there is no grounding, there is no answer: the bot gives a referral instead.
3. **Boundaries.** The bot MUST NOT give personal advice or claim to know a customer's personal policy or data. It explains what the site says and refers the customer to customer service.
4. **Polite and lawful crawling.** Respect robots.txt, send one request at a time, use an honest User-Agent, and never bypass protections.

---

## 2. Layers and the dependency rule

```
src/siterag/
├── domain/      # pure Python: entities, enums, value objects, pure functions, domain errors
├── ports/       # interfaces (typing.Protocol / ABC) + DTOs crossing boundaries
├── adapters/    # implementations of ports: sqlalchemy/, http/, extract/, embed/, vector/, lexical/, rerank/, llm/
├── ingest/      # pure processing helpers used by stages (links, normalization, chunking logic)
├── storage/     # file stores (raw/, clean/)
├── pipeline/    # runner, context, report, stages/
├── rag/         # retrieval + answering services (use ports only)
├── config/      # pydantic models + loader
├── container.py # composition root (the ONLY place that knows concrete classes)
├── cli.py       # Typer CLI (thin)
└── ui/          # Streamlit app (thin)
```

**Dependencies point inward:**

| Layer | May import |
|---|---|
| `domain/` | nothing project-specific; stdlib only (+ pydantic-free dataclasses) |
| `ports/` | `domain/` |
| `ingest/`, `storage/` | `domain/`, `ports/` |
| `pipeline/`, `rag/` | `domain/`, `ports/`, `ingest/`, `storage/`, `config/` |
| `adapters/` | `domain/`, `ports/`, `config/`, third-party libraries |
| `container.py` | everything |
| `cli.py`, `ui/` | `container.py`, `config/`, `domain/` (for display types) |

- `domain/` and `ports/` MUST NOT import SQLAlchemy, httpx, lxml, Chroma, sentence-transformers, Groq or Streamlit. An architecture test enforces this, and it MUST keep passing.
- Stages and RAG services MUST depend on ports, never on concrete adapters.
- `cli.py` and `ui/` MUST contain no business logic. They call services built by the container.

---

## 3. Composition and dependency injection

- `container.py` is the single **composition root**. It reads the configuration and builds the concrete objects.
- Dependencies are passed through **constructors**. Do not use module-level singletons, global state, or service locators.
- No DI framework.
- Stages are registered by name in the container. Their order comes from `config/settings.yaml`. Adding a stage means a new module, one registration and one config line. The runner MUST NOT change.

---

## 4. Configuration and secrets

| What | Where |
|---|---|
| Global settings (paths, stage order, stage versions, model names, thresholds) | `config/settings.yaml` |
| Everything specific to one website (domain, scope rules, classification, HTTP politeness, contact details, refusal / referral texts, customer-service details) | `sites/<site_id>.yaml` |
| Secrets (e.g. `GROQ_API_KEY`) | `.env` (gitignored) + environment variables |
| Prompts for the LLM | `prompts/*.md` files, versioned, loaded by name |

- All configuration MUST be validated with **pydantic**. Fail fast with clear messages.
- **No magic numbers** in code. Thresholds, sizes, delays, top-k values and model names belong in config.
- **No site-specific values in code.** A new site = a new YAML profile. The code MUST NOT change.
- Secrets MUST NOT be committed, logged, or written to reports.

---

## 5. Swappable components (Strategy + ports) — closed list

Interfaces exist **only** for these components:

| Port | First implementation | Why swappable |
|---|---|---|
| `Fetcher` | httpx | Playwright may be needed later |
| `Extractor` (one per content type) | HTML (lxml/trafilatura), PDF (PyMuPDF) | per content type |
| `Embedder` | BGE-M3 (local) | model comparison |
| `VectorStore` | Chroma | Qdrant / pgvector later |
| `LexicalIndex` | rank_bm25 | alternatives |
| `Reranker` | bge-reranker-v2-m3 (local) | model comparison |
| `LLMClient` | Groq | provider change |
| Repositories + `UnitOfWork` | SQLAlchemy | ORM independence |

- New interfaces MUST NOT be created outside this list without approval. **No speculative abstraction**: no base classes "for later", no generic frameworks.
- Each port MUST have a **fake implementation** under `tests/fakes/` for tests.

---

## 6. Pipeline contract

- **`state.db` is the source of truth** for pages, chunks, receipts, runs and events. Files in `raw/` and `clean/` and the indexes (vector, lexical) are **derived** and MUST be rebuildable from `state.db` + `raw/`.
- **Stage receipts.** A stage processes a page only when its input hash or its stage version changed. Changing a stage's logic or parameters ⇒ bump its version in `settings.yaml`.
- **Idempotency.** Running any stage twice MUST produce the same result and no duplicates.
- **Per-item isolation.** One failing page MUST NOT stop a run. Record the failure through the failure policy and continue.
- **Durability.** Commit per page/item through the Unit of Work. A crash mid-run MUST lose at most the current item.
- **A stage never mutates another stage's output.** `fetch` stores bytes as received. `extract` reads raw and writes clean. And so on.
- **Removal safety.** Content leaves the index only through the defined removal paths (verified 404/410, redirect, duplicate, out of scope, skipped), and stays subject to the mass-removal threshold.
- **Every run** writes a Markdown report to `data/<site_id>/reports/`.

---

## 7. Data contracts

- **Citation travels with the data.** Every retrievable unit (chunk) MUST carry or reference:
  - `url_display`, `title`, `heading_path`;
  - `page_type`, `domain`;
  - `last_fetch_ok_at`.

  A chunk without citation data is a bug.
- Text MUST be stored as Unicode NFC. Hebrew normalization (gershayim/quotes, invisible directional marks, whitespace) happens in `extract`, once.
- All timestamps are timezone-aware **UTC**.
- Hashes: SHA-256 for content, `url_id = sha1(normalized_url)[:16]`.
- **Schema changes** happen **only** through a new Alembic migration. Never edit an applied migration. Every migration MUST be tested on a database that contains data.

---

## 8. Answering-layer rules (binding for `rag/`)

1. **Retrieval first, then a gate.** Retrieval is hybrid: vector + lexical, fused with RRF, then reranked. If no chunk passes the configured relevance threshold, **the LLM is not called**: the response is the configured "not on the site" referral.
2. **Structured output.** The LLM returns JSON with `answerable` (bool), `answer` (Hebrew text) and `source_ids` (chunk IDs). Free-text parsing is not allowed.
3. **Citations are validated in code.** Every `source_id` MUST belong to the chunks that were sent to the LLM. Unknown IDs are dropped. If none remain, the response becomes the referral. URLs shown to the user come **from our data**, never from LLM text.
4. **Follow-up questions** are rewritten into standalone questions from the chat history **before** retrieval.
5. **Boundary detection** (personal policy, personal advice, account data) uses rules from the site profile and/or a classifier. The response uses the profile's referral template.
6. **Language and style:** answers in clear, short Hebrew, with an invitation to ask a follow-up.
7. Prompts live in `prompts/` and carry a version. The prompt version is logged with every answer.
8. **Every answer** is logged (question, rewritten question, retrieved chunk IDs and scores, gate decision, LLM output, final response, prompt version) for evaluation. This log MUST NOT contain secrets.

---

## 9. Errors, logging, resilience

- Domain errors are explicit exception classes in `domain/errors.py`. Bare `except:` and silently swallowed exceptions are not allowed.
- Retries, rate limiting and timeouts belong in **adapters**, never in domain code.
- Use the stdlib `logging` module with module-level loggers. Use `print` only in `cli.py` for user output.
- Never disable TLS verification (no `verify=False`). On certificate problems, use `truststore` or stop and report.

---

## 10. Testing

- **pytest.** Tests MUST NOT use the real network or real time. Use `httpx.MockTransport`, fakes for ports, and an injected clock/sleep.
- Pure domain functions get **table-driven** unit tests.
- Every stage gets tests for: new item, unchanged item, changed item, failure, and version bump.
- **Evaluation is separate from unit tests:**
  - `eval/questions.yaml` holds the questions, each with an expected source URL or "not on site";
  - `eval/run_eval.py` reports answer/refusal correctness and citation correctness.
- All existing tests MUST pass before every commit.

---

## 11. Code quality — Clean Code, SOLID, simplicity

### 11.1 Guiding principles (in priority order)
1. **Correct and readable first.** The developer must be able to read and debug every line.
2. **KISS and YAGNI.** Build only what the current task needs. No "might be useful later" code, options, parameters, layers or generic helpers.
3. **Rule of three.** Do not extract a shared abstraction until the same logic appears a **third** time. Two similar blocks are acceptable.
4. **Explicit over implicit.** No metaprogramming, no magic decorators, no dynamic attribute tricks.

### 11.2 SOLID, applied to this project
| Principle | What it means here |
|---|---|
| **S** — Single responsibility | Every module, class and function has one reason to change. A stage orchestrates; parsing, normalization, persistence and HTTP live elsewhere. |
| **O** — Open/Closed | New behaviour comes in through configuration, registration (stages, adapters) or a new module. Working code is not modified to add a feature. |
| **L** — Liskov substitution | Every implementation of a port (including test fakes) honours the same contract: inputs, outputs, raised domain errors, side effects. |
| **I** — Interface segregation | Ports stay small and focused. Do not add unrelated methods to an existing Protocol. |
| **D** — Dependency inversion | High-level code depends on ports. Concrete classes are created only in `container.py` and injected through constructors. |

### 11.3 Functions
- **One function does one thing**, at one level of abstraction. If you need "and" to describe it, split it.
- Keep functions short. **~30 lines is a soft limit.** Longer requires a reason.
- **At most 4 parameters.** If you need more, group them in a frozen dataclass or config object.
- **No boolean flag parameters that switch behaviour** (`process(page, dry=True)`). Write two functions, or inject a strategy.
- **Command–query separation.** A function either returns information or changes state, not both. The exception is repository writes that return the saved entity.
- Prefer early returns over deep nesting (max 3 levels).
- No hidden side effects: a function named `get_…` / `is_…` / `compute_…` MUST NOT write to the database, disk or network.

### 11.4 Naming
- Names say **what**, not how. Variables and attributes are nouns (`pending_pages`). Functions are verbs (`fetch_page`, `normalize_url`). Booleans start with `is_` / `has_` / `should_` / `can_`.
- **Units are part of the name:** `timeout_s`, `max_file_mb`, `delay_ms`.
- Allowed abbreviations: `url`, `id`, `http`, `html`, `pdf`, `db`, `llm`, `uow`. Nothing else. No single letters, except `i` / `j` loop indexes and short comprehensions.
- Banned vague names: `data`, `info`, `obj`, `tmp`, `result2`, `manager`, `helper`, `utils`, `misc`, `handle_stuff`.
- Constants: `UPPER_SNAKE_CASE`. Classes: `PascalCase`. Everything else: `snake_case`.
- One concept, one name, across the whole codebase. Do not mix `page` / `doc` / `item` for the same thing.

### 11.5 Python best practices
- Full type hints, with `mypy`-clean intent. Docstrings on public classes and functions: purpose, args and raised errors, kept short.
- Value objects and DTOs are `@dataclass(frozen=True)`. Fixed sets of values use `Enum`, never free strings.
- Use `pathlib.Path` for paths. Use context managers for every resource (files, sessions, HTTP clients).
- No mutable default arguments. No wildcard imports. No circular imports.
- **Exceptions:** raise specific domain errors. Catch broad `Exception` **only** at the per-item boundary of a stage, where it is logged and recorded through the failure policy, never silently.
- Logging uses lazy formatting (`log.info("fetched %s", url)`). No f-strings in log calls.
- **Comments explain why, not what.** No commented-out code. No TODO without a task reference.
- Formatting and linting with **ruff** (format + lint) and **zero warnings**.
- Dependencies are pinned with minimum versions in `pyproject.toml`. Do not add a dependency without saying why in your plan.

### 11.6 Tests without over-engineering
- **Test behaviour, not implementation.** Test through public functions and ports. Never test private helpers directly. Never assert on internal call order.
- **One behaviour per test.** Use the Arrange–Act–Assert structure, and name tests `test_<unit>_<scenario>_<expected>`.
- Prefer **`pytest.mark.parametrize` tables** over many near-identical tests.
- **Fakes, not mocks.** Use the simple in-memory fakes in `tests/fakes/` for our ports. Use mocking libraries only at true external boundaries (HTTP via `MockTransport`, time). Never mock our own classes.
- **Fixtures stay small and local.** No fixture pyramids, no test frameworks or base classes for tests, no factories unless the rule of three applies.
- **Do not test:** third-party libraries, trivial dataclasses or getters, framework wiring, or logging text.
- **Invest in tests by risk.** Focus on citation integrity, the answer gate, receipts and reprocessing, removal and safety thresholds, URL normalization, and scope rules. There is **no coverage percentage target**.
- Tests MUST be fast and deterministic: no network, no real time, no sleeping, no ordering dependence.

---

## 12. How agents work in this repo

1. **Plan first.** Before writing code, reply with: your understanding, the files you will create or change, and your questions. **Do not invent decisions.** If something is not specified here or in the task spec, ask.
2. **One branch per task** (`task-NN`). Do not merge into `master`; the developer reviews and merges.
3. Small steps. Run the tests and commit after each step.
4. **Stay in scope.** Do not implement out-of-scope parts, and do not create placeholders for them.
5. **Do not change public interfaces of earlier tasks without approval.** Propose the change, preferring backward-compatible additions.
6. **Live site:** only the smoke test the task allows (e.g. `--limit 20`). Never run a full crawl. On anything unexpected, stop and report.
7. Update `README.md` for every user-visible change (commands, config keys, flows).

## 13. Definition of Done (every task)

- [ ] The spec is implemented and nothing out of scope was added.
- [ ] All tests pass, including the architecture test, and new behaviour is tested.
- [ ] Every schema change has a migration, tested on a database with data.
- [ ] Configuration is validated, and no magic numbers or site-specific values are in code.
- [ ] Code follows §11: single-purpose functions, clear names, no speculative abstraction, ruff clean.
- [ ] Tests follow §11.6: behaviour-focused, no over-engineered fixtures or mocks of our own code.
- [ ] README is updated.
- [ ] Work is committed on the task branch, with a short summary of what was done, deviations, and open questions.
