"""
Worker para GitHub Actions.

Consulta TCC desde el runner de GitHub y envia los resultados al backend.
Asi Vercel Hobby solo persiste datos y genera el reporte, sin cargar con el
tiempo de consulta de todas las guias.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import configure_logging, get_logger
from app.integrations.tcc.client import get_tcc_client

logger = get_logger(__name__)

MAX_CONCURRENT = int(os.getenv("TCC_WORKER_CONCURRENCY", "5"))
PAGE_SIZE = 200


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def _fetch_active_tracking_numbers(client: httpx.AsyncClient, backend_url: str) -> list[str]:
    page = 1
    tracking_numbers: list[str] = []

    while True:
        response = await client.get(
            f"{backend_url}/api/v1/shipments",
            params={"is_active": "true", "page": page, "page_size": PAGE_SIZE},
        )
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])
        tracking_numbers.extend(item["tracking_number"] for item in items)

        total = int(data.get("total", len(tracking_numbers)))
        if len(tracking_numbers) >= total or not items:
            break
        page += 1

    return tracking_numbers


async def _fetch_tcc_results(tracking_numbers: list[str]) -> list[dict[str, Any]]:
    provider = get_tcc_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_one(tracking_number: str) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await provider.fetch(tracking_number)
                return _jsonable(result)
            except Exception as exc:
                logger.exception("github_worker_fetch_error", tracking=tracking_number)
                return {
                    "tracking_number": tracking_number,
                    "fetch_success": False,
                    "fetch_error": str(exc),
                    "provider": getattr(provider, "provider_name", "github-actions"),
                    "events": [],
                    "payload_snapshot": {},
                }

    return await asyncio.gather(*(fetch_one(tracking_number) for tracking_number in tracking_numbers))


async def run_daily(cycle_label: str) -> None:
    backend_url = os.environ["BACKEND_URL"].rstrip("/")
    cron_token = os.environ["CRON_TOKEN"]

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as client:
        tracking_numbers = await _fetch_active_tracking_numbers(client, backend_url)
        logger.info("github_worker_shipments_loaded", count=len(tracking_numbers), cycle=cycle_label)

        results = await _fetch_tcc_results(tracking_numbers)
        payload = {
            "run_type": f"github_actions_{cycle_label}",
            "cycle_label": cycle_label,
            "results": results,
        }

        response = await client.post(
            f"{backend_url}/api/cron/ingest-tracking",
            headers={"Authorization": f"Bearer {cron_token}"},
            json=payload,
        )
        response.raise_for_status()
        logger.info("github_worker_ingest_done", response=response.json())


async def main() -> None:
    configure_logging()
    if len(sys.argv) != 3 or sys.argv[1] != "daily":
        raise SystemExit("Uso: python scripts/github_tracking_worker.py daily <0700|1200|1600>")

    cycle_label = sys.argv[2]
    if cycle_label not in {"0700", "1200", "1600"}:
        raise SystemExit("cycle_label debe ser 0700, 1200 o 1600")

    await run_daily(cycle_label)


if __name__ == "__main__":
    asyncio.run(main())
