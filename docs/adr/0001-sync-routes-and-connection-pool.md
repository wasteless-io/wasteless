# ADR-0001 — Sync `def` route handlers + threaded connection pool

Status: accepted

## Context

The FastAPI UI (`ui/`) does blocking psycopg2 calls in every route — there is no
`await` anywhere in the data path. Written as `async def`, each route runs on the
single asyncio event-loop thread, so one blocking DB call stalls every other
concurrent request. A load test confirmed it: latency went from ~700 ms to
2.8 s+ between concurrency 20 and 100.

Separately, opening an unpooled `psycopg2.connect()` per request exhausts
Postgres's `max_connections` under concurrent load.

## Decision

- **All route handlers are plain `def`, not `async def`.** FastAPI then runs them
  in its threadpool, so blocking I/O no longer freezes the event loop.
- **`get_db()` hands out connections from a `ThreadedConnectionPool`**
  (`maxconn=40`) in `ui/state.py`.
- **Do not** wrap the pool with a `threading.Semaphore` to turn exhaustion into
  queueing — that deadlocked all worker threads under load.

## Consequences

- New routes must stay sync `def`. Adding `async def` reintroduces the freeze.
- The pool ceiling (40) and Postgres `max_connections` must be kept in sync.
- The reasoning is duplicated as comments in `ui/state.py`; this ADR is the
  canonical record.
