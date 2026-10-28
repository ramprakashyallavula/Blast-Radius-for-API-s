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
| Real unions (`anyOf` / `oneOf` with several branches) | Compared two ways. **Merged:** all branches as one superset, which catches a field removed from every branch, type changes and a changed number of branches. **Branch by branch:** when branches can be paired (by `$ref` name or `title` if every branch has one, otherwise by position if the count is unchanged), each pair is compared too, which catches a change in one branch while another branch still has the field as before. Which branch a payload uses can't be known statically, so every union change is marked uncertain (`certain: false`) and is never more than **may break** for a consumer. See the limits below. |
| Path parameters | Matched by position, so renaming `{id}` to `{orderId}` is safe; type, format and enum changes are breaking |
| Query, header and cookie parameters | Added (required/optional), removed, became required, type/format change, enum narrowed or newly restricted |
| Media types | Every JSON media type (`application/json`, `application/problem+json`, `application/vnd.api+json`, ...) is compared with the same media type in the other version, whatever the order they're listed in; the media type is named in the message when there are several |
| Request bodies | Became required, media type removed, required/optional fields, type changes, enum narrowing, no longer nullable, fewer union branches |
| Responses | Every 2xx status is compared (named in the message when there are several); added/removed 2xx statuses; media type removed; fields removed, type changes, became optional, became nullable, new enum values, new union branches |

## Not supported yet

| Feature | What happens today | Workaround |
|---|---|---|
| External `$ref` (`common.yaml#/...`, URLs) | Clear error | Bundle first: `npx @redocly/cli bundle openapi.yaml -o bundled.yaml` |
| `discriminator` mappings | Ignored: branches are paired by `$ref` name, `title` or position, never by discriminator value | — |
| Union branches that can't be paired | When some branches have no name and the branch count changes, only the merged view applies: a change in one branch that another branch hides can be missed | Give branches a `$ref` or `title` |
| A branch renamed (a new `$ref` name for the same shape) | Reported as one branch removed and another added, not as field changes | — |
| Nested unions at the same path (a union inside a union branch) | Only the outermost union's branches are compared one to one | — |
| `readOnly` / `writeOnly` | Ignored: a read-only field is treated as part of both request and response | — |
| `additionalProperties`, `patternProperties` | Ignored | — |
| Value constraints (`minLength`, `maximum`, `pattern`, ...) | Tightening isn't reported | — |
| Non-JSON bodies (form data, XML) | Media-type removal is reported; fields inside aren't compared | — |
| Error responses (4xx/5xx) and response headers | Not compared | — |
| Callbacks, webhooks, links, security schemes | Not compared | — |
| GraphQL, gRPC | Not supported | — |
