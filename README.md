# BlastRadius

**Predict which services an API change will break, before it ships.**

BlastRadius compares two versions of an OpenAPI spec, finds the services that call
the API, and reports exactly which consumers (and which lines of code) a change
will break.

> Status: in development. See [docs/PRIMER.md](docs/PRIMER.md) for the design.

Spec-diff tools tell you a change is *technically* breaking. BlastRadius tells you
*who* it breaks, and when a "breaking" change is actually safe because no consumer
uses what changed.

## Try the demo

```bash
blastradius check examples/orders-service/openapi.v1.yaml \
                  examples/orders-service/openapi.v2.yaml \
                  --consumers-dir examples/consumers
```

```
╭─ BlastRadius ──────────────────────────────────────────────────────────────╮
│ 💥 3 consumers will break: billing-service, shipping-service, web-frontend │
│ 11 breaking changes in the spec · 3 will break · 1 may break · 7 call sites │
╰────────────────────────────────────────────────────────────────────────────╯

BREAKING POST /orders
  New required request field currency
    ● will break  web-frontend  src/api/orders.ts:23
      does not send currency, which is now required

BREAKING GET /orders/{orderId}
  Response field discount_code was removed
    ● will break  billing-service  billing/invoices.py:11
      reads discount_code, which was removed
    ● unaffected  shipping-service  shipping/orders_client.py:17
      calls this endpoint but never reads discount_code
...
✓ 8 more breaking/warning changes with no affected consumers
```

## Use it on pull requests (GitHub Action)

Add a workflow to the repository that owns the API. Every pull request that changes
the spec gets a comment listing the consumers it breaks, and the check fails if
one will.

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
      - uses: actions/checkout@v4
      - uses: OWNER/blastradius@main
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
| `fail-on` | `impacted` | `impacted`, `breaking`, or `never` |
| `comment` | `true` | Post the report as a PR comment (updated on each push, not duplicated) |

This repository uses its own Action: see
[`.github/workflows/blastradius.yml`](.github/workflows/blastradius.yml), which runs
when [`examples/orders-service/openapi.yaml`](examples/orders-service/openapi.yaml) changes.

## Commands

| Command | What it does |
|---|---|
| `blastradius check BASE HEAD -C DIR` | Who will break. The main command. |
| `blastradius diff BASE HEAD [--all]` | What changed in the spec, classified breaking / warning / safe |
| `blastradius scan -C DIR` | The HTTP calls found in consumer code |

Useful options for `check`:

| Option | Meaning |
|---|---|
| `-c PATH` / `--consumer PATH` | One consumer's source folder (repeatable) |
| `-C DIR` / `--consumers-dir DIR` | Every subfolder of DIR is a consumer |
| `-f table\|markdown\|json` | Terminal output, a PR comment, or machine-readable output |
| `-o FILE` | Write the Markdown/JSON report to a file |
| `--fail-on impacted\|breaking\|never` | When to exit with code 1 (default: only if a consumer will break) |

Exit codes: `0` OK, `1` the `--fail-on` condition was met, `2` bad input.

## How it works

1. **Spec loader**: reads OpenAPI 3.x, resolves `$ref`, merges `allOf`, and flattens schemas into field paths like `items[].sku`.
2. **Diff engine**: classifies every change. Requests may loosen but not tighten; responses may grow but not shrink.
3. **Scanner**: finds HTTP calls (`requests`, `httpx`, `fetch`, `axios`, ...) in Python and JS/TS and turns their URLs into templates.
4. **Matcher**: links each call to the most specific endpoint it hits.
5. **Impact analysis**: checks whether each caller reads (or sends) what changed, giving *will break*, *may break* or *unaffected*.
6. **Reports**: terminal, Markdown for PR comments, and JSON.

## Accuracy

Measured against hand-labeled cases in [`eval/cases`](eval/cases), including hard cases
added on purpose to expose the scanner's limits (`python eval/run_eval.py`):

| | Precision | Recall |
|---|---|---|
| Strict (only *will break* counts) | 83% | 56% |
| Lenient (*will break* or *may break* counts) | 88% | 78% |

Known misses, all on the roadmap: URLs built by helper functions, JS destructuring
(`const { field } = ...`), and fields read in a different function from the call.
The evaluation set is small (9 labeled breaking call sites), so treat these as
early numbers.

## Roadmap

- **Better scanning**: Python `ast` and tree-sitter parsers with data-flow tracking, to fix
  the known misses above; support for generated API clients.
- **Traffic-based discovery**: find consumers from API-gateway or OpenTelemetry logs.
- **Dashboard**: a service and web UI showing which services depend on which endpoints and
  fields, and how that changes over time.
- **AI-drafted migrations**: open a pull request in each affected consumer with a proposed fix.
- **More protocols**: GraphQL and gRPC through the same change model.

## Development setup

```bash
# The .nosync suffix stops iCloud Drive from syncing the virtualenv (and marking
# its files hidden, which breaks imports on Python 3.13+). .venv is a shortcut to it.
python3 -m venv .venv.nosync && ln -s .venv.nosync .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # unit tests
ruff check . && ruff format --check .
python eval/run_eval.py     # accuracy
```
