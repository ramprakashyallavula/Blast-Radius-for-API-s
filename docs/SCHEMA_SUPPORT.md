# OpenAPI support

What the diff engine compares, and what it doesn't. Every "supported" row has a
regression test in `tests/test_schema_regressions.py`, `tests/test_diff.py` or
`tests/test_flatten.py`.

## Supported

| Feature | How it's handled |
|---|---|
| OpenAPI 3.0 and 3.1, YAML or JSON | Swagger 2.0 is rejected with a clear message |
| Local `$ref` (`#/components/...`), chains of refs | Resolved; cycles are detected |
| Recursive schemas (a `Category` containing `Category`) | Compared down to the first repeat |
| `allOf` | Properties and `required` lists of all parts are merged |
| Nullable fields | `nullable: true` (3.0), `type: [X, "null"]` (3.1), `anyOf: [X, {type: null}]` (FastAPI `Optional[X]`) |
| `anyOf` / `oneOf` with one non-null branch | Treated as a nullable version of that branch, so changes inside it are exact |
| Real unions (`anyOf` / `oneOf` with several branches) | Compared as a superset of all branches: a field removed from every branch, a type change, or a new/removed branch is reported. Because which branch a payload uses can't be known statically, these changes are marked uncertain and never reported as more than **may break** for a consumer. |
| Path parameters | Matched by position, so renaming `{id}` to `{orderId}` is safe; type, format and enum changes are breaking |
| Query, header and cookie parameters | Added (required/optional), removed, became required, type/format change, enum narrowed or newly restricted |
| Request bodies | Became required, media type removed, required/optional fields, type changes, enum narrowing, no longer nullable, fewer union branches |
| Responses | Every 2xx status is compared (named in the message when there are several); added/removed 2xx statuses; media type removed; fields removed, type changes, became optional, became nullable, new enum values, new union branches |

## Not supported yet

| Feature | What happens today | Workaround |
|---|---|---|
| External `$ref` (`common.yaml#/...`, URLs) | Clear error | Bundle first: `npx @redocly/cli bundle openapi.yaml -o bundled.yaml` |
| `discriminator` mappings | Ignored: unions are compared by their merged fields | — |
| `readOnly` / `writeOnly` | Ignored: a read-only field is treated as part of both request and response | — |
| `additionalProperties`, `patternProperties` | Ignored | — |
| Value constraints (`minLength`, `maximum`, `pattern`, ...) | Tightening isn't reported | — |
| Non-JSON bodies (form data, XML) | Media-type removal is reported; fields inside aren't compared | — |
| Error responses (4xx/5xx) and response headers | Not compared | — |
| Callbacks, webhooks, links, security schemes | Not compared | — |
| GraphQL, gRPC | Not supported | — |
