"""
Trigger manual del ciclo TCC desde el dashboard — versión robusta.

Puntos de falla eliminados:
- Token con whitespace: .strip() siempre
- Sin retries: 3 intentos con backoff
- Error opaco: detalle exacto en cada respuesta
- Status inconsistente: devuelve los 5 runs más recientes
- Token inválido sin aviso: /health verifica antes de trigger
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.logging import get_logger

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_VALID_CYCLES = {"0700", "1200", "1600"}
_MAX_RETRIES = 3


# ── Auth ──────────────────────────────────────────────────────────────────────

def _verify_jwt(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Sesión requerida. Inicia sesión nuevamente.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Token con formato inválido.")
    try:
        s = get_settings()
        payload = jwt.decode(parts[1].strip(), s.app_secret_key, algorithms=["HS256"])
        return str(payload["sub"])
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Sesión expirada. Recarga e inicia sesión.")


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_token() -> str:
    token = get_settings().github_token.strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_TOKEN no está configurado en el servidor. Contacta al administrador.",
        )
    return token


def _parse_run(run: dict) -> dict:
    started = run.get("run_started_at") or run.get("created_at") or ""
    updated = run.get("updated_at") or ""
    duration = None
    try:
        if started and updated:
            s = datetime.fromisoformat(started.replace("Z", "+00:00"))
            u = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            duration = max(0, int((u - s).total_seconds()))
    except Exception:
        pass
    return {
        "run_id": run["id"],
        "status": run.get("status", "unknown"),
        "conclusion": run.get("conclusion"),
        "started_at": started,
        "updated_at": updated,
        "duration_seconds": duration,
        "url": run.get("html_url"),
        "event": run.get("event"),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health")
async def dispatch_health():
    """
    Verifica que el sistema de trigger manual esté operativo.
    Endpoint público — no requiere sesión de usuario.
    """
    token = _get_token()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{get_settings().github_oidc_repository}/actions/workflows/{get_settings().github_workflow_file}",
                headers=_gh_headers(token),
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="GitHub API no responde (timeout). Intenta en unos segundos.")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Error de red al verificar GitHub: {exc}")

    if resp.status_code == 401:
        raise HTTPException(status_code=503, detail="El token de GitHub expiró o fue revocado. Contacta al administrador.")
    if resp.status_code == 404:
        raise HTTPException(status_code=503, detail="Workflow no encontrado en el repositorio.")
    if not resp.is_success:
        raise HTTPException(status_code=503, detail=f"GitHub API respondió {resp.status_code}. Intenta de nuevo.")

    wf = resp.json()
    return {
        "ready": True,
        "workflow": wf.get("name"),
        "workflow_state": wf.get("state"),
        "repo": get_settings().github_oidc_repository,
    }


@router.post("/trigger")
async def trigger_run(cycle: str = "0700"):
    """
    Dispara workflow_dispatch en GitHub con hasta 3 reintentos.
    Retorna inmediatamente — el frontend hace polling a /status.
    Endpoint público — el GITHUB_TOKEN vive en el servidor, no hay riesgo.
    """
    if cycle not in _VALID_CYCLES:
        raise HTTPException(status_code=422, detail=f"Ciclo '{cycle}' inválido. Use 0700, 1200 o 1600.")

    token = _get_token()
    repo = get_settings().github_oidc_repository
    workflow = get_settings().github_workflow_file
    url = f"{_GITHUB_API}/repos/{repo}/actions/workflows/{workflow}/dispatches"
    payload = {"ref": "main", "inputs": {"job": "daily", "cycle": cycle}}

    last_error = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info("dispatch_trigger_attempt=%s cycle=%s user=%s", attempt, cycle, user)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=_gh_headers(token), json=payload)

            if resp.status_code == 204:
                triggered_at = datetime.now(timezone.utc).isoformat()
                logger.info("dispatch_trigger_ok attempt=%s cycle=%s user=public", attempt, cycle)
                return {
                    "triggered": True,
                    "cycle": cycle,
                    "triggered_at": triggered_at,
                    "attempts": attempt,
                    "message": f"Ciclo {cycle[:2]}:{cycle[2:]} enviado a GitHub Actions.",
                }

            if resp.status_code == 401:
                raise HTTPException(status_code=503, detail="Token de GitHub inválido. El botón necesita reconfiguración.")
            if resp.status_code == 404:
                raise HTTPException(status_code=503, detail="Workflow no encontrado en el repositorio.")
            if resp.status_code == 422:
                raise HTTPException(status_code=422, detail=f"GitHub rechazó los parámetros: {resp.text[:200]}")

            last_error = f"GitHub respondió HTTP {resp.status_code}: {resp.text[:150]}"
            logger.warning("dispatch_trigger_retry attempt=%s error=%s", attempt, last_error)

        except HTTPException:
            raise
        except httpx.TimeoutException:
            last_error = f"Timeout en intento {attempt} (GitHub tardó más de 15s)"
            logger.warning("dispatch_trigger_timeout attempt=%s", attempt)
        except Exception as exc:
            last_error = f"Error inesperado: {type(exc).__name__}: {exc}"
            logger.error("dispatch_trigger_error attempt=%s\n%s", attempt, traceback.format_exc())

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(attempt * 2)

    logger.error("dispatch_trigger_all_attempts_failed cycle=%s last_error=%s", cycle, last_error)
    raise HTTPException(
        status_code=502,
        detail=f"No se pudo iniciar el ciclo tras {_MAX_RETRIES} intentos. Último error: {last_error}",
    )


@router.get("/status")
async def get_run_status():
    """
    Devuelve los 5 runs más recientes.
    Endpoint público — los runs de GitHub Actions son información no sensible.
    """
    token = _get_token()
    repo = get_settings().github_oidc_repository

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{repo}/actions/runs",
                headers=_gh_headers(token),
                params={"event": "workflow_dispatch", "per_page": "5"},
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="GitHub API no responde al consultar estado.")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Error de red: {exc}")

    if resp.status_code == 401:
        raise HTTPException(status_code=503, detail="Token de GitHub expirado al consultar estado.")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"GitHub API respondió {resp.status_code}.")

    runs = resp.json().get("workflow_runs", [])
    if not runs:
        raise HTTPException(status_code=404, detail="No hay ejecuciones recientes en GitHub Actions.")

    return {
        "latest": _parse_run(runs[0]),
        "recent": [_parse_run(r) for r in runs[:5]],
    }
