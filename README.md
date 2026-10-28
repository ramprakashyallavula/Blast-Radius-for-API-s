# BlastRadius

[![CI](https://github.com/ramprakashyallavula/Blast-Radius-for-API-s/actions/workflows/ci.yml/badge.svg)](https://github.com/ramprakashyallavula/Blast-Radius-for-API-s/actions/workflows/ci.yml)
![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Predict which services an API change will break, before it ships.**

Spec-diff tools tell you a change is *technically* breaking. BlastRadius compares two
versions of an OpenAPI spec, scans the code of the services that call the API, and
tells you **who** it breaks and on which line. It also tells you when a "breaking" change
is actually safe because no consumer uses what changed.

- **CLI** for local use and CI: terminal, Markdown and JSON reports
- **GitHub Action** that comments on pull requests and fails the check when a consumer will break
- **Web playground** (FastAPI + React) to try it without installing anything

> **Status: beta.** The diff engine is well tested; consumer scanning is pattern-based
> and has known limits (see [Accuracy](#accuracy) and [What it understands](#what-it-understands)).

## Quick start (about 5 minutes)

Requires Python 3.10+.

```bash
git clone https://github.com/ramprakashyallavula/Blast-Radius-for-API-s.git
cd Blast-Radius-for-API-s
python3 -m venv .venv && source .venv/bin/activate
pip install .
blastradius check examples/orders-service/openapi.v1.yaml \
                  examples/orders-service/openapi.v2.yaml \
                  --consumers-dir examples/consumers
```

```
╭─ BlastRadius ────────────────────────────────────────────────────────────────────╮
│ 💥 3 consumers will break: billing-service, shipping-service, web-frontend       │
│ 11 breaking changes in the spec · 3 will break · 0 more may break · 7 call sites │
│ in 3 consumers                                                                   │
╰──────────────────────────────────────────────────────────────────────────────────╯

BREAKING POST /orders
  New required request field currency
    ● will break  web-frontend  src/api/orders.ts:23
      does not send currency, which is now required

BREAKING GET /orders/{orderId}
  Response field discount_code was removed
    ● will break  billing-service  billing/invoices.py:11
      reads discount_code, which was removed
    ● unaffected  shipping-service  shipping/orders_client.py:17
      uses the response but never reads discount_code
...
✓ 8 more breaking/warning changes with no affected consumers
```

The example is a small Orders API ([v1](examples/orders-service/openapi.v1.yaml) →
[v2](examples/orders-service/openapi.v2.yaml)) and three services that call it
([Python](examples/consumers/billing-service), [Python](examples/consumers/shipping-service),
[TypeScript](examples/consumers/web-frontend)). Try your own:

```bash
blastradius check old-openapi.yaml new-openapi.yaml -c path/to/service-a -c path/to/service-b
```

## Use it on pull requests (GitHub Action)

Add a workflow to the repository that owns the API. Every pull request that changes the
spec gets a comment listing the consumers it breaks ([live example](https://github.com/ramprakashyallavula/Blast-Radius-for-API-s/pull/1)),
and the check fails if one will.

```yaml
# .github/workflows/blastradius.yml
name: BlastRadius
on:
  pull_request:
    paths: ["api/openapi.yaml"]
permissions:
  contents: read
  pull-requests: write
jobs:
  blastradius:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: ramprakashyallavula/Blast-Radius-for-API-s@v0.2.0   # a release tag or commit SHA, not @main
        with:
          spec: api/openapi.yaml
          consumers-dir: services          # consumers in this repo (monorepo)
          consumer-repos: |                # and/or consumers in other repos
            my-org/billing-service
            my-org/web-frontend
          consumer-token: ${{ secrets.CONSUMERS_READ_TOKEN }}
```

| Input | Default | Meaning |
|---|---|---|
| `spec` | (required) | Path to the OpenAPI spec |
| `consumers` | | Consumer folders in this repo, one per line |
| `consumers-dir` | | A folder whose subfolders are each a consumer |
| `consumer-repos` | | Other repositories to scan (`owner/name`), one per line |
| `consumer-token` | `github-token` | Token that can read `consumer-repos` (needed for private repos) |
| `fail-on` | `impacted` | `impacted` (a consumer will break), `breaking` (any breaking change), or `never` |
| `fail-on-clone-error` | `true` | Fail if a `consumer-repos` repository can't be cloned, rather than silently leaving it out |
| `comment` | `true` | Post the report as a PR comment (updated on each push, not duplicated) |

- **Pin a version** (`@v0.2.0` or a full commit SHA). `@main` changes under you.
- **Private consumer repos** need a token with read access (a fine-grained PAT or a GitHub
  App token) in `consumer-token`. It's passed to git through a credential helper, never in a URL.
- **Fork pull requests** get a read-only token and no secrets, so the report goes to the job
  summary instead of a comment, and private consumer repos can't be cloned.
- **Exit codes** (also the `exit-code` output): `0` OK or nothing to compare (spec unchanged,
  or new in this PR), `1` the `fail-on` condition was met or the spec was deleted, `2` the
  check couldn't run (unreadable spec, base branch not fetchable, clone failure).

This repository uses its own Action: [`.github/workflows/blastradius.yml`](.github/workflows/blastradius.yml)
runs when [`examples/orders-service/openapi.yaml`](examples/orders-service/openapi.yaml) changes.

## What it understands

**APIs:** any **OpenAPI 3.0 or 3.1** spec, whichever framework serves the API.

| Framework | How to get the spec |
|---|---|
| **FastAPI** | `blastradius export myapp.main:app -o openapi.json` |
| Django REST Framework | `drf-spectacular` |
| Spring Boot | `springdoc-openapi` |
| NestJS / Express | `@nestjs/swagger` / `swagger-jsdoc` |
| ASP.NET Core | Swashbuckle |
| Go | `swag`, `ogen`, or a hand-written spec |

The diff handles `$ref`, `allOf`, nullable fields (including FastAPI's `Optional[X]`), real
`oneOf`/`anyOf` unions (merged, and branch by branch when branches can be paired; union
changes are always flagged as uncertain), path/query/header/cookie parameters, every 2xx
response and every JSON media type. The full list, and what's not supported yet (external `$ref` files, `discriminator`,
`readOnly`/`writeOnly`, value constraints, unions whose branches can't be paired), is in [docs/SCHEMA_SUPPORT.md](docs/SCHEMA_SUPPORT.md).

**Consumers:** **Python** (`requests`, `httpx`, `aiohttp`-style sessions, client objects) and
**JavaScript/TypeScript** (`fetch`, `axios`), plus generated API clients matched by
`operationId` (`orders_api.get_order(...)`). URLs can be literals, f-strings/template
strings, variables, or helpers like `self._url("orders", id)`. The analysis follows the
response through variables, loops, callbacks, destructuring and one level of function
calls in the same file.

When it can't trace something (the response is passed elsewhere, a body is built in
another function, a read can't be tied to the response), it says **may break** rather than
claiming the code is safe.

## Accuracy

Measured on hand-labeled cases in [`eval/cases`](eval/cases): 5 scenarios, 21 call sites
that really break, plus traps for false positives (`python eval/run_eval.py`).

| | Precision | Recall |
|---|---|---|
| Strict (only *will break* counts) | 100% | 57% |
| Lenient (*will break* or *may break* counts) | 83% | 90% |

Read these with care: the set is small and was written by the author. Cases 01–02 were used
while developing the scanner; 03–05 were labeled before running the tool and haven't been
tuned against. Known misses: clients other than `requests`/`httpx`/`fetch`/`axios`
(e.g. `ky`), URLs built from config dictionaries, and responses stored on an object and read
in another method. Several "may break" results (path parameter changes, enum values sent
as variables) are correct but can't be confirmed statically.

## Web playground and API

The engine also runs as an HTTP API with a React front end (in [`web/`](web)):

```bash
pip install ".[server]"
uvicorn blastradius.server:app --port 8787      # API docs: http://127.0.0.1:8787/docs
cd web && npm install && npm run dev            # http://localhost:5173
```

| Endpoint | What it does |
|---|---|
| `POST /analyze` | Body: `base_spec`, `head_spec` (YAML/JSON text) and `consumers` (name + source files). Returns the report as JSON, plus Markdown. |
| `GET /demo` | A ready-made `/analyze` request: the Orders API demo |
| `GET /health` | Liveness check |

Everything runs in memory and nothing is stored; inputs have size limits and YAML aliases
are rejected (to block "billion laughs" payloads).

## Commands

| Command | What it does |
|---|---|
| `blastradius check BASE HEAD -C DIR` | Who will break. The main command. |
| `blastradius diff BASE HEAD [--all]` | What changed in the spec, classified breaking / warning / safe |
| `blastradius scan -C DIR` | The HTTP calls found in consumer code |
| `blastradius export MODULE:APP` | Export a FastAPI app's OpenAPI spec |

`check` options: `-c PATH` (a consumer, repeatable), `-C DIR` (each subfolder is a consumer),
`-f table|markdown|json`, `-o FILE`, `--fail-on impacted|breaking|never`.
Exit codes: `0` OK, `1` the `--fail-on` condition was met, `2` bad input.

In reports, each consumer is counted once, under its worst result: `will_break` lists
consumers with at least one call that will break, and `may_break` those with a call that
may break and none that will (the terminal and Markdown say "N more may break"). Every
change in the JSON has `certain: false` when it sits inside a `oneOf`/`anyOf` branch, so
its impact is at most `may_break`.

## How it works

```
base spec ─┐                               consumer code
           ├─► spec loader ─► diff engine        │
head spec ─┘   ($ref, allOf,   (breaking /       ▼
                unions)         warning / safe)  scanner ─► matcher ─┐
                                      │          (HTTP calls)  (call → endpoint)
                                      └───────────────┬──────────────┘
                                                      ▼
                                   impact analysis (will break / may break / unaffected)
                                                      ▼
                                   reports: terminal · Markdown · JSON · web
```

Design decisions, module by module, are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md);
the background (OpenAPI, compatibility rules, alternatives) is in [docs/PRIMER.md](docs/PRIMER.md).

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
pytest                                   # tests
ruff check . && ruff format --check .    # lint and formatting
python eval/run_eval.py                  # accuracy
cd web && npm ci && npm test             # front end
```

> If the repository is inside an iCloud-synced folder (Desktop/Documents on a Mac), name
> the virtualenv `.venv.nosync` and symlink `.venv` to it: iCloud marks files in `.venv` as
> hidden, which breaks imports on Python 3.13+.

Releases: see [docs/RELEASING.md](docs/RELEASING.md) and the [changelog](CHANGELOG.md).

## Roadmap

- **Parser-based scanning** (Python `ast`, tree-sitter) with cross-file data flow, to close
  the known misses above
- **More clients and languages**: `ky`, `got`, Java, Go, C#
- **Traffic-based discovery** from API-gateway or OpenTelemetry logs
- **AI-drafted migrations**: a proposed fix for each affected consumer
- **GraphQL and gRPC** through the same change model

## License

[MIT](LICENSE)
