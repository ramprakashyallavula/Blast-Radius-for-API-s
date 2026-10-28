# Running the web playground

The playground (`blastradius.server` + `web/`) is built for **local use and demos**.
There is no official hosted deployment of it. This page covers what the server protects
against on its own, and what you'd have to add before putting it on the public internet.

## Local use (the supported setup)

```bash
pip install ".[server]"
uvicorn blastradius.server:app --port 8787      # API docs: http://127.0.0.1:8787/docs
cd web && npm install && npm run dev            # http://localhost:5173
```

By default the API only accepts browser calls from the Vite dev server and `vite preview`
on this machine (`localhost`/`127.0.0.1`, ports 5173 and 4173). To use another port or
host, set `BLASTRADIUS_CORS_ORIGINS` to a comma-separated list of origins:

```bash
BLASTRADIUS_CORS_ORIGINS=http://localhost:5174 uvicorn blastradius.server:app --port 8787
```

The frontend calls `http://127.0.0.1:8787` unless it's built with `VITE_API_URL` set.

## What the server does on its own

| Concern | What's built in |
|---|---|
| Storage | None. Specs and code arrive in the request, are analyzed in memory, and are dropped after the response. Nothing is logged by the app. |
| Request size | Bodies over 6 MB get `413` before they're parsed, checked by `Content-Length` and while streaming. |
| Input limits | Each spec ≤ 500,000 characters; ≤ 20 consumers, ≤ 200 files, each file ≤ 200,000 characters, all source ≤ 500,000 characters in total. |
| CPU per request | ≤ 2,000 HTTP calls found in the source, and ≤ 50,000 checks (breaking/warning changes × calls to them). On a laptop the slowest requests within these limits take about 2–10 seconds. Over a limit, the request gets `422` with the reason. |
| YAML bombs | YAML aliases (`*name`) are rejected, which blocks "billion laughs" payloads. Real OpenAPI specs use `$ref` instead. |
| Deeply nested specs | Turned into a `422` instead of crashing the worker. |
| CORS | Local dev origins only, unless `BLASTRADIUS_CORS_ORIGINS` says otherwise. The API uses no cookies or credentials. |

## What's missing for public hosting

None of these are built in. Add them before exposing the API to the internet:

- **Rate limiting.** The limits above cap the cost of *one* request, not how many arrive.
  Put a rate limit per client IP in front of the API: a reverse proxy (nginx
  `limit_req`, Caddy, Traefik), your host's edge (Cloudflare and similar), or middleware
  such as `slowapi`.
- **Timeouts.** The analysis runs in a worker thread that can't be interrupted, so set a
  request timeout at the proxy (around 30 seconds) and run several workers
  (`uvicorn --workers N` or gunicorn with uvicorn workers), so one slow request doesn't
  block the others.
- **Authentication.** There's none. Anyone who can reach the API can use it. If it
  shouldn't be open, put it behind your platform's access controls or an API gateway.
- **Memory and process limits.** Run it in a container with a memory limit, so the
  platform restarts it instead of the host running out of memory.
- **CORS.** Set `BLASTRADIUS_CORS_ORIGINS` to your frontend's exact origin. Avoid `*`.
- **HTTPS, monitoring and logs.** Terminate TLS at the proxy or platform. Keep request
  bodies out of logs: they contain other people's source code.
- **The frontend.** Build it with `VITE_API_URL=https://your-api.example.com npm run build`
  and serve `web/dist/` as static files.

The Python CLI and the GitHub Action don't use the server, and none of this applies to
them.
