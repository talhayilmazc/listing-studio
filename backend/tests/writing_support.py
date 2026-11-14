"""Generate in tests: the request queues the writing job (fix/perf-and-memory), the test runs it.

``generate(client, batch, body)`` posts ``/generate`` like the page, runs ``write_batch_listings`` as the worker
would (with the app's own overrides: session, Redis, storage, queue) and answers with the counts the request
used to return (generated, failed, skipped, failures, skipped_groups, paused), so a test reads as before.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from httpx import AsyncClient, Response

from app.api import deps


def job_ctx(client: AsyncClient) -> dict[str, Any]:
    """The worker's ctx from the app's test overrides."""
    app = client._transport.app  # type: ignore[attr-defined]
    over = app.dependency_overrides
    make_session = over[deps.get_session]

    @asynccontextmanager
    async def sessionmaker():  # noqa: ANN202
        agen = make_session()
        session = await agen.__anext__()
        try:
            yield session
        finally:
            await agen.aclose()

    ctx: dict[str, Any] = {"redis": over[deps.get_redis](), "sessionmaker": sessionmaker}
    if deps.get_storage in over:
        ctx["storage"] = over[deps.get_storage]()
    if deps.get_enqueuer in over:
        ctx["enqueuer"] = over[deps.get_enqueuer]()
    return ctx


def result_of(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated": state.get("written", 0), "failed": state.get("failed", 0), "skipped": state.get("skipped", 0),
        "failures": [{"original_filename": f["file"], "error": f["error"]} for f in state.get("failures", [])],
        "skipped_groups": list(state.get("skipped_groups", [])),
        "paused": state.get("message") if state.get("state") == "paused" else None,
        "state": state.get("state"),
    }


async def run_writing(client: AsyncClient, batch: Any) -> dict[str, Any]:
    """Run the batch's queued writing job as the worker would; its outcome, as ``result_of``."""
    from app.workers import writing

    ctx = job_ctx(client)
    await writing.write_batch_listings(ctx, str(batch))
    return result_of(await writing.read(ctx["redis"], batch) or {})


async def generate_response(client: AsyncClient, batch: Any, body: dict[str, Any]) -> tuple[Response, dict[str, Any] | None]:
    resp = await client.post(f"/api/batches/{batch}/generate", json=body)
    if resp.status_code != 200:
        return resp, None
    out = resp.json()
    if out["state"] in ("queued", "running"):
        return resp, await run_writing(client, batch)
    return resp, result_of(out)


async def generate(client: AsyncClient, batch: Any, body: dict[str, Any]) -> dict[str, Any]:
    resp, result = await generate_response(client, batch, body)
    assert resp.status_code == 200, resp.text
    assert result is not None
    return result
