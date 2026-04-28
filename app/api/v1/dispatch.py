"""
Ejecución manual del ciclo de tracking TCC desde el dashboard.

El usuario presiona un botón en el frontend → este router:
1. Llama a GitHub API para disparar workflow_dispatch en tcc-scheduler.yml
2. Devuelve confirmación inmediata (sin esperar que el workflow termine)
3. El frontend hace polling a /status para saber cuándo terminó

Variable de entorno requerida en Vercel:
    GITHUB_TOKEN — Personal Access Token con scope actions:write
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.logging import get_logger

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_VALID_CYCLES = {"0700", "1200", "1600"}


# ── Auth ──────────────────────────────────────────────────────────────────────

def _verify_jwt(authorization: str | None) -> str:
    """Valida el JWT del usuario y retorna el username."""
    if not authorization:
        raise HTTPException(status_code=401, detail="No autorizado")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Formato de token inválido")
    token = parts[1].strip()
    try:
        s = get_settings()
        payload = jwt.decode(token, s.app_secret_key, algorithms=["HS256"])
        return str(payload["sub"])
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_run(run: dict) -> dict:
    started = run.get("run_started_at") or run.get("created_at")
    updated = run.get("updated_at")
    duration = None
    if started and updated:
        try:
            s = datetime.fromisoformat(started.replace("Z", "+00:00"))
            u = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            duration = int((u - s).total_seconds())
        except Exception:
            pass
    return {
        "run_id": run["id"],
        "status": run["status"],
        "conclusion": run.get("conclusion"),
        "started_at": started,
        "updated_at": updated,
        "duration_seconds": duration,
        "url": run.get("html_url"),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/trigger")
async def trigger_run(
    cycle: str = "0700",
    authorization: str | None = Header(default=None),
):
    """
    Dispara el workflow tcc-scheduler.yml via workflow_dispatch en GitHub.
    Retorna inmediatamente — el frontend hace polling a /status para ver el progreso.
    """
    try:
        user = _verify_jwt(authorization)

        if cycle not in _VALID_CYCLES:
            raise HTTPException(status_code=422, detail=f"Ciclo inválido: {cycle}. Use 0700, 1200 o 1600.")

        s = get_settings()
        if not s.github_token:
            raise HTTPException(
                status_code=503,
                detail="GITHUB_TOKEN no configurado en el servidor.",
            )

        repo = s.github_oidc_repository
        workflow = s.github_workflow_file
        url = f"{_GITHUB_API}/repos/{repo}/actions/workflows/{workflow}/dispatches"
        payload = {"ref": "main", "inputs": {"job": "daily", "cycle": cycle}}

        logger.info("dispatch_trigger repo=%s workflow=%s cycle=%s user=%s", repo, workflow, cycle, user)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=_gh_headers(s.github_token), json=payload)

        logger.info("dispatch_trigger_response status=%s", resp.status_code)

        if resp.status_code == 404:
            raise HTTPException(status_code=502, detail="Workflow no encontrado en el repositorio.")
        if resp.status_code == 401:
            raise HTTPException(status_code=502, detail="Token de GitHub inválido o expirado.")
        if resp.status_code == 422:
            raise HTTPException(status_code=422, detail=f"GitHub rechazó los parámetros: {resp.text[:200]}")
        if not resp.is_success:
            raise HTTPException(status_code=502, detail=f"Error GitHub API: HTTP {resp.status_code} — {resp.text[:200]}")

        triggered_at = datetime.now(timezone.utc).isoformat()
        logger.info("dispatch_trigger_ok cycle=%s user=%s", cycle, user)

        return {
            "triggered": True,
            "cycle": cycle,
            "triggered_at": triggered_at,
            "message": f"Ciclo {cycle[:2]}:{cycle[2:]} iniciado. Resultado en ~2-4 minutos.",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("dispatch_trigger_unexpected_error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error inesperado: {type(exc).__name__}: {exc}")


@router.get("/status")
async def get_run_status(authorization: str | None = Header(default=None)):
    """
    Devuelve el estado del workflow_dispatch más reciente.
    El frontend hace polling cada 5s mientras status != completed.
    """
    try:
        _verify_jwt(authorization)

        s = get_settings()
        if not s.github_token:
            raise HTTPException(status_code=503, detail="GITHUB_TOKEN no configurado.")

        repo = s.github_oidc_repository
        url = f"{_GITHUB_API}/repos/{repo}/actions/runs"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers=_gh_headers(s.github_token),
                params={"event": "workflow_dispatch", "per_page": "5"},
            )

        if not resp.is_success:
            raise HTTPException(status_code=502, detail=f"Error GitHub API: HTTP {resp.status_code}")

        runs = resp.json().get("workflow_runs", [])
        if not runs:
            raise HTTPException(status_code=404, detail="No hay ejecuciones recientes.")

        return _parse_run(runs[0])

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("dispatch_status_unexpected_error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error inesperado: {type(exc).__name__}: {exc}")
