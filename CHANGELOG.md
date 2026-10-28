# Changelog

All notable changes to this project. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-28

### Fixed
- Breaking changes that used to be reported as "no changes": fields removed or changed
  inside one branch of a `oneOf`/`anyOf`; path parameter type, format and enum changes;
  query/header/cookie enums that drop or newly restrict values; request fields newly
  restricted to an enum or no longer nullable; response fields becoming nullable; changes
  in any 2xx response other than the first; removed request and response media types;
  request unions losing a branch.
- Impact analysis no longer reports "unaffected" when it can't trace the response (passed
  to another function, stored on an object, read through an unrelated variable); those are
  now "may break". A change with no specific rule defaults to "may break".
- The GitHub Action no longer passes when the base branch can't be fetched, and fails by
  default when a consumer repository can't be cloned.
- CI's demo step no longer hides failures with `|| true`.
- Every JSON media type of a body is compared, not only the first one, and media types are
  matched by name, so listing them in another order no longer reports false removals.
- A change in one `oneOf`/`anyOf` branch is no longer hidden by another branch that still
  has the field: branches that can be paired (by `$ref` name, `title` or position) are
  compared one to one, as uncertain changes.

### Changed
- `summary.may_break` lists consumers with nothing worse, so a consumer is never in both
  `will_break` and `may_break`; the reports say "N more may break".
- The web API only allows browser calls from local dev origins unless
  `BLASTRADIUS_CORS_ORIGINS` is set (it allowed any origin).

### Added
- Scanner: ignores comments and strings; resolves URLs from variables and helper calls;
  matches generated-client calls by `operationId`; understands arrow functions and class
  methods; follows the response through variables, loops, callbacks, destructuring and one
  level of function calls in the same file; checks payloads built before the call.
- Changes inside a union are marked uncertain and capped at "may break"; JSON reports and
  the web API include `certain` for every change, and Markdown marks uncertain ones.
- Action: `fail-on-clone-error` input; fork PRs report to the job summary; the token is
  passed through a git credential helper instead of the clone URL.
- `blastradius export module:app` for FastAPI apps.
- Web playground: FastAPI backend (`blastradius.server`) and React front end (`web/`).
- Playground results view: a verdict, per-consumer stats, by-consumer and by-change views,
  filters, Markdown/JSON export, "Uncertain" badges, and the full report on the page.
- Web API limits on total request size (413), total source size, call sites and work per
  request; `docs/DEPLOYMENT.md` on local/demo use versus public hosting.
- Evaluation set grown to 5 cases and 21 labeled breaking call sites.
- MIT license, package metadata, `docs/ARCHITECTURE.md`, `docs/SCHEMA_SUPPORT.md`.

## [0.1.0] - 2026-10-28

First version: OpenAPI diff engine, consumer scanner for Python and JS/TS, impact
analysis, CLI with terminal/Markdown/JSON reports, and a GitHub Action that comments on
pull requests.

[0.2.0]: https://github.com/ramprakashyallavula/Blast-Radius-for-API-s/compare/35c4836...v0.2.0
[0.1.0]: https://github.com/ramprakashyallavula/Blast-Radius-for-API-s/commit/35c4836
