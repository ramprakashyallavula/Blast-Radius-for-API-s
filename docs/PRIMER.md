# BlastRadius Primer: what to know before you build

This document covers the concepts, the competition, the architecture and the plan.
Read it once before you write code, and come back to it before interviews: most
of what an interviewer will ask you about this project is answered here.

---

## 1. The problem in one paragraph

In a company with many services, one team owns an API (the **producer**) and
other teams call it (the **consumers**). When the producer changes the API (it
removes a field, renames an endpoint, or adds a required parameter), some
consumers break. Today this is usually discovered **in production**, days later,
by the consumer team. BlastRadius answers one question **at pull-request time**:

> "If this API change merges, exactly which services break, and on which lines?"

---

## 2. Vocabulary you must be fluent in

| Term | Meaning |
|---|---|
| **API contract** | The agreed shape of requests and responses. Here: an OpenAPI document. |
| **OpenAPI** | The standard YAML/JSON format describing REST APIs (paths, methods, parameters, schemas). Versions 3.0 and 3.1 are the ones you will see. |
| **JSON Schema** | The language OpenAPI uses to describe the shape of data (`type`, `properties`, `required`, `enum`, `items`, `$ref`, `allOf`). |
| **`$ref`** | A pointer to a reusable definition, e.g. `#/components/schemas/Order`. You must resolve these to compare specs. |
| **Producer / consumer** | The service that owns the API / the services that call it. |
| **Backward compatible** | Old consumers keep working against the new API without changing their code. |
| **Breaking change** | A change that is not backward compatible. |
| **Blast radius** | The set of consumers (and code locations) affected by a change. |
| **Semantic versioning** | MAJOR.MINOR.PATCH. Breaking change = MAJOR bump. |
| **Expand / contract** | The safe migration pattern: add the new thing, migrate all consumers, then remove the old thing. BlastRadius tells you when "contract" is safe. |
| **Contract testing** | Consumers publish what they expect and producers verify against it (the tool *Pact* does this). Powerful, but every team must write contracts first. |

---

## 3. The single most important idea: request vs. response asymmetry

This is the core rule of the diff engine. Know it well, because it's a great
interview answer.

- **Requests flow consumer → producer.** The producer must keep accepting
  everything it accepted before. So the API may **loosen** requests but never
  **tighten** them.
- **Responses flow producer → consumer.** Consumers rely on what they received
  before. So the API may **add** to responses but never **take away**.

(In type-theory terms, request types are *contravariant* and response types are
*covariant*.)

| Change | Request side | Response side |
|---|---|---|
| Add an optional field | Safe | Safe |
| Add a **required** field | **Breaking**: old clients don't send it | Safe |
| Remove a field | Usually safe (warning) | **Breaking**: clients read it |
| Change a field's type | **Breaking** | **Breaking** |
| Optional → required | **Breaking** | Safe |
| Required → optional | Safe | **Warning**: value may now be missing |
| Add an enum value | Safe | **Warning**: exhaustive `switch` statements fail |
| Remove an enum value | **Breaking** | Safe |
| Remove an endpoint | **Breaking** | n/a |
| Rename a path parameter (`{id}` → `{orderId}`) | Safe: the URL is the same | n/a |

---

## 4. What already exists (be honest about this in interviews)

| Tool | What it does | What it does *not* do |
|---|---|---|
| **oasdiff**, **openapi-diff**, **Optic** | Diff two OpenAPI specs and flag breaking changes | Don't know who the consumers are |
| **buf breaking** | Same idea for protobuf/gRPC | Same gap |
| **GraphQL Inspector** | Same for GraphQL schemas | Same gap |
| **Apollo GraphOS schema checks** | Checks GraphQL changes against real client operations | GraphQL only, and needs the Apollo platform |
| **Pact** | Consumer-driven contract tests | Every consumer team must write and maintain contracts |

**Our angle:** "Is this change breaking?" is a solved problem. The open question
is **"who will it actually break?"** for plain **REST** APIs, *without* asking
consumer teams to write anything. BlastRadius finds the consumers on its own by
scanning their code (and, later, traffic logs). It then turns a theoretical
breaking change into a concrete list: *billing-service, `invoices.py:12`, reads
`discount_code`*.

That also gives you the most useful kind of result: **"this change is
technically breaking, but no consumer uses that field, so it's safe to ship."**
Spec-only tools can't say that.

---

## 5. Architecture

```
  base spec ─┐                          consumer repos
             ├─► Spec loader            ──► Scanner (find HTTP call sites)
  head spec ─┘   ($ref, allOf)               │  URL template + method + code context
                    │                         │
                    ▼                         ▼
               Diff engine            Matcher (call site → API operation)
          (classified changes:              │
           breaking / warning / safe)       │
                    └──────────┬────────────┘
                               ▼
                       Impact analysis
            (per change × call site: will break / may break / unaffected)
                               │
                               ▼
              Reports: terminal · Markdown (PR comment) · JSON (dashboard/CI)
```

| Module | Responsibility |
|---|---|
| `spec.py` | Load YAML/JSON, resolve `$ref`, merge `allOf`, list operations, flatten schemas into field paths like `items[].sku` |
| `diff.py` | Compare two specs and emit `Change` objects using the rules in section 3 |
| `scanner.py` | Walk consumer repos and find HTTP calls (`requests`, `httpx`, `fetch`, `axios`) with their URL templates |
| `impact.py` | Match call sites to operations, then decide per change whether each call site breaks |
| `report.py` | Render the results for humans and machines |
| `cli.py` | `diff`, `scan`, `check` commands; the exit code drives CI pass/fail |

### Key design decisions (and why)

1. **Deterministic core, AI on the edges.** The diff engine and impact rules are
   plain code: fast, testable and explainable. An LLM can't be the thing that
   decides whether CI fails. AI comes in later for *explaining* impact and
   *drafting migration PRs*, where an occasional mistake is cheap.
2. **Three impact levels, not two.** Static analysis can't see everything.
   `will_break` = we saw the code use the changed thing. `may_break` = we
   couldn't trace it (for example, the response is returned to other code).
   `unaffected` = the consumer calls the endpoint but never touches the changed
   field. Being honest about uncertainty is what makes engineers trust a tool.
3. **Read vs. write usage.** `order["total"]` *reads* a field; `{"total": 5}`
   *sends* it. Response changes check reads and request changes check writes.
   Without this split you get false positives.
4. **Path matching by suffix with known base segments.** Consumers call
   `f"{BASE}/v1/orders/{id}"`. We normalize that to `/{}/v1/orders/{}` and
   match it against `/orders/{orderId}`, allowing only base-URL-like prefixes
   (`{}`, `api`, `v1`, or the spec's `servers` path). When several operations
   match, the most specific wins (literal segments beat parameters).
5. **The CI exit code is the product.** `--fail-on impacted` (the default) fails
   only when a real consumer will break. That's how you avoid a tool that
   teams switch off because it cries wolf.

### Known limitations of the MVP (good "what would you do next" answers)

- Regex-based scanning. Next step: real parsers (Python `ast`, then
  **tree-sitter** for JS/TS/Go/Java) with data-flow tracking through variables
  and functions.
- Field matching uses the field's leaf name (`sku`, not `items[].sku`).
- `oneOf`/`anyOf`, external `$ref` files, and non-JSON bodies aren't modelled yet.
- Generated API clients (openapi-generator) need a dedicated scanner that maps
  method names like `getOrder()` to operations.

---

## 6. Roadmap (about 4 weeks)

| Week | Goal | Done when |
|---|---|---|
| **1. Core engine** ✅ *(built in this repo)* | Spec loader, diff engine, scanner, impact analysis, CLI, demo, tests | `blastradius check ...` prints the demo report and tests pass |
| **2. Real parsers + accuracy** | Python `ast` scanner, tree-sitter for TS; generated-client support; an **evaluation set** of 50+ labeled changes; precision/recall numbers | You can say "precision X%, recall Y% on N cases" |
| **3. CI + multi-repo** | GitHub Action posting the Markdown report as a PR comment; a `blastradius.yaml` config listing consumer repos; cloning repos via the GitHub API | A PR in a demo org gets an automatic comment |
| **4. Service, dashboard, AI** | FastAPI service + PostgreSQL storing scans; React dependency-graph dashboard; LLM that drafts migration PRs for consumers; deploy + 2-minute demo video | Live URL, video, README with architecture diagram and numbers |

Optional "wow" extensions:
- **Traffic-based discovery**: read API-gateway or OpenTelemetry logs to see
  which services call which endpoints and fields in production.
- **gRPC/GraphQL** support through the same `Change` model.
- **Deprecation tracker**: mark a field deprecated and watch the consumer count
  drop to zero before it's removed.

---

## 7. How you'll prove it works

Collect real breaking changes from open-source projects' git history (spec
before/after), plus synthetic ones in the demo services. Label each one by hand
(which call sites truly break), then measure:

- **Precision**: of the call sites we flagged `will_break`, how many truly break?
- **Recall**: of the call sites that truly break, how many did we flag?
- **Latency**: time to analyze a spec pair against N repos.

Put those numbers in your README and resume. They are what separate this from a
toy project.

---

## 8. Interview talking points

- Why request/response asymmetry exists, with an example.
- Why the core is deterministic and AI is only used for drafting and explaining.
- How you handled uncertainty (three impact levels) and why precision matters
  more than recall for a CI gate.
- How path matching works and its edge cases (`/orders/search` vs `/orders/{id}`).
- What you'd change at 1,000 repos: incremental scanning, caching call-site
  indexes per commit, a job queue, storing the index in Postgres.

## 9. Skills to brush up on as you go

- OpenAPI 3 structure and JSON Schema (read the spec's "Schema Object" section)
- Python: dataclasses, `re`, `pathlib`, `ast`, packaging with `pyproject.toml`, `pytest`
- Git and GitHub: Actions workflows, GitHub Apps, PR comments through the REST API
- Later: FastAPI, PostgreSQL, Docker, a little React
