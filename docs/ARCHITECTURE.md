# Architecture

BlastRadius is a pipeline of small, separately tested modules. Everything up to the
reports is plain, deterministic Python: no network, no database, no LLM.

```
spec.py ──► diff.py ──────────────┐
                                  ├──► impact.py ──► report.py ──► cli.py / server.py / Action
scanner.py ──► matcher.py ────────┘
```

| Module | Input → output | Key ideas |
|---|---|---|
| [`spec.py`](../src/blastradius/spec.py) | YAML/JSON → `Spec`, `Operation`s, flattened fields | Resolves local `$ref` (with cycle detection), merges `allOf`, turns `X or null` into a nullable `X`, and represents a real `oneOf`/`anyOf` as the superset of its branches with every field below it marked `in_union`; `unions()` lists each union's raw branches for one-to-one comparison. Schemas become `{"items[].sku": FieldInfo(type, required, enum, nullable, ...)}` so two versions can be compared as dictionaries. |
| [`diff.py`](../src/blastradius/diff.py) | two `Spec`s → `Change`s | Requests may loosen but not tighten; responses may grow but not shrink. Path parameters are compared by position (renames are free). Every 2xx response and every JSON media type (matched by name) is compared. Union branches that can be paired are also compared one to one. Changes inside a union are marked `certain=False`. |
| [`scanner.py`](../src/blastradius/scanner.py) | source files → `CallSite`s | Regex plus a small tokenizer, for Python and JS/TS. Comments and strings are masked out first. URLs become templates (`/{}/orders/{}`) from literals, variables or helper calls. Each call keeps the code before and after it in its function, and the code after each call to that function in the same file. Generated-client calls are recorded as `op:<method>`. |
| [`matcher.py`](../src/blastradius/matcher.py) | `CallSite`s + `Operation`s → groups by operation | Right-aligned segment matching with base-URL prefixes allowed; the most specific template wins (`/orders/search` beats `/orders/{id}`). `op:` calls match by `operationId`. |
| [`impact.py`](../src/blastradius/impact.py) | `Change` × `CallSite` → `will_break` / `may_break` / `unaffected` | Follows the variables holding the response; a field read only counts on those. Request changes check what's sent, including payloads built before the call. If anything can't be traced, the answer is `may_break`. |
| [`report.py`](../src/blastradius/report.py) | `Analysis` → terminal, Markdown, JSON | Worst first; changes nobody uses are collapsed. |
| [`cli.py`](../src/blastradius/cli.py), [`server.py`](../src/blastradius/server.py), [`action.yml`](../action.yml) | Front doors | Exit codes 0/1/2 drive CI. The API runs in memory, caps request size, input sizes and work per request, and is meant for local/demo use ([DEPLOYMENT.md](DEPLOYMENT.md)). The Action fetches the base spec, clones consumer repos, and posts or updates one comment. |

## Design decisions

1. **Deterministic core.** A CI gate has to be reproducible and explainable. An LLM is a
   good fit for *drafting* a fix (roadmap), not for deciding whether a build fails.
2. **Three answers, not two.** `may_break` is how the tool is honest about the limits of
   static analysis. It's used whenever the response escapes the code it can see, a read
   can't be tied to the response, a request body is built elsewhere, or a change sits
   inside a union branch.
3. **Precision over recall for `will_break`.** A gate that cries wolf gets switched off.
   The default `--fail-on impacted` only fails on `will_break`.
4. **Read vs. send.** Response changes matter to code that reads a field; request changes
   matter to code that sends one (or must now send one).
5. **Superset view of unions, plus paired branches.** Knowing which branch a payload
   uses needs a discriminator most specs don't have, so the tool doesn't try. Merging
   branches catches fields removed or changed everywhere; pairing branches by `$ref`
   name, `title` or position catches a change in one branch that another branch hides.
   Either way the impact is flagged as uncertain (`may_break` at most).

## Known limitations

- Pattern-based scanning: no full parser, no cross-file data flow, one level of function
  calls within a file. Unsupported clients (e.g. `ky`, `got`) and URLs from config
  dictionaries are missed. See the accuracy section of the README.
- Field matching uses names along the path (`customer.name` needs both `customer` and
  `name` on the response); it doesn't know types.
- OpenAPI features not modelled are listed in [SCHEMA_SUPPORT.md](SCHEMA_SUPPORT.md).

## Scaling notes

For hundreds of consumer repositories: index call sites per repository and commit (so a
spec change only needs the matching step), run scans in a job queue, and store the index
in a database. The GitHub Action clones on every run, which is fine for a handful of
consumers but not for an organization-wide index.
