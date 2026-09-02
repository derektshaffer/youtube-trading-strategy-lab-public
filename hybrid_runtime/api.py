"""Optional FastAPI adapter for the loopback desktop service."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .contracts import TERMINAL_JOB_STATUSES
from .security import token_matches
from .service import HybridService
from .storage import HybridStoreError, JobNotFound


def create_app(service: HybridService, *, expected_token: str) -> Any:
    """Create the HTTP adapter without making FastAPI a Streamlit dependency."""

    try:
        from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
        from fastapi.responses import StreamingResponse
    except ImportError as exc:  # pragma: no cover - exercised in desktop environment
        raise RuntimeError(
            "FastAPI is not installed. Install requirements-desktop.txt."
        ) from exc

    app = FastAPI(
        title="Trading Intelligence Local Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not token_matches(authorization, expected_token):
            raise HTTPException(status_code=401, detail="Invalid local service token")

    @app.get("/health", dependencies=[Depends(require_token)])
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "trading-intelligence-local"}

    @app.post("/v1/route", dependencies=[Depends(require_token)])
    def preview_route(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return service.route_preview(body).as_dict()
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/jobs", dependencies=[Depends(require_token)])
    def submit_job(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            record, created = service.submit(body)
            return {"created": created, "job": record.as_dict()}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/jobs", dependencies=[Depends(require_token)])
    def list_jobs(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        return {"jobs": [job.as_dict() for job in service.list(limit=limit)]}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return service.get(job_id).as_dict()
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return service.cancel(job_id).as_dict()
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HybridStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(require_token)])
    def job_events(job_id: str, after_id: int = Query(default=0, ge=0)) -> dict[str, Any]:
        try:
            return {"events": service.events(job_id, after_id=after_id)}
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/jobs/{job_id}/events/stream", dependencies=[Depends(require_token)])
    async def stream_job_events(job_id: str, after_id: int = Query(default=0, ge=0)):
        try:
            service.get(job_id)
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def generate():
            cursor = after_id
            idle_terminal_polls = 0
            while True:
                events = service.events(job_id, after_id=cursor)
                for event in events:
                    cursor = max(cursor, int(event["id"]))
                    yield "data: " + json.dumps(event, separators=(",", ":")) + "\n\n"
                current = service.get(job_id)
                if current.status in TERMINAL_JOB_STATUSES:
                    idle_terminal_polls = idle_terminal_polls + 1 if not events else 0
                    if idle_terminal_polls >= 2:
                        break
                await asyncio.sleep(0.35)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app
