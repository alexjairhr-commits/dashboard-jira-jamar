"""
jira_client.py
==============
Cliente para la API REST de Jira Cloud (v3).

Características:
- Autenticación HTTP Basic (email + API token).
- Resolución AUTOMÁTICA de campos personalizados por su nombre visible
  (p. ej. "Fecha estimada L4" -> "customfield_10042"), consultando
  /rest/api/3/field. Así el proyecto funciona en cualquier Jira sin que
  tengas que averiguar los IDs internos.
- Búsqueda por JQL con paginación automática.
- Reintentos automáticos con backoff exponencial (tenacity) ante fallos
  de red o respuestas 429/5xx.
- Normalización de cada issue a un diccionario plano y predecible.
- Logging detallado de cada paso.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import Config

logger = logging.getLogger(__name__)

# Campos estándar que pedimos siempre.
STANDARD_FIELDS = [
    "summary",
    "issuetype",
    "status",
    "priority",
    "assignee",
    "reporter",
    "resolution",
    "created",
    "updated",
    "resolutiondate",
    "duedate",
    "project",
]

# Excepciones de red que justifican un reintento.
_RETRYABLE = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class JiraApiError(RuntimeError):
    """Error no recuperable al hablar con la API de Jira."""


class JiraClient:
    """Cliente ligero y robusto para Jira Cloud."""

    def __init__(self, config: Config, timeout: int = 30) -> None:
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = config.auth
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)

        # Mapa nombre_personalizado -> customfield_id (se llena en resolve_custom_fields)
        self.custom_ids: Dict[str, Optional[str]] = {
            "activity": None,
            "start": None,
            "l4": None,
        }

    # ------------------------------------------------------------------ #
    # Peticiones de bajo nivel con reintentos
    # ------------------------------------------------------------------ #
    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
    )
    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)

        if resp.status_code == 429 or resp.status_code >= 500:
            logger.warning("Respuesta %s de Jira; se reintentará.", resp.status_code)
            raise requests.exceptions.ConnectionError(f"Jira devolvió {resp.status_code}")
        if resp.status_code == 401:
            raise JiraApiError("Autenticación fallida (401). Verifica JIRA_EMAIL y JIRA_API_TOKEN.")
        if resp.status_code == 403:
            raise JiraApiError("Acceso denegado (403). La cuenta no tiene permiso sobre el proyecto.")
        if resp.status_code >= 400:
            raise JiraApiError(f"Error {resp.status_code} de Jira: {resp.text[:500]}")
        return resp.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params or {})

    # ------------------------------------------------------------------ #
    # Resolución de campos personalizados por nombre
    # ------------------------------------------------------------------ #
    def resolve_custom_fields(self) -> None:
        """Consulta /rest/api/3/field y mapea los nombres configurados a sus IDs."""
        try:
            fields = self._get("/rest/api/3/field")  # lista de todos los campos
        except JiraApiError as e:
            logger.warning("No se pudieron listar los campos de Jira: %s", e)
            return

        # name (en minúsculas) -> id
        name_to_id = {}
        for f in fields:
            nm = (f.get("name") or "").strip().lower()
            if nm and nm not in name_to_id:
                name_to_id[nm] = f.get("id")

        wanted = {
            "activity": self.config.field_activity,
            "start": self.config.field_start,
            "l4": self.config.field_l4,
        }
        for slot, display_name in wanted.items():
            if not display_name:
                continue
            fid = name_to_id.get(display_name.strip().lower())
            self.custom_ids[slot] = fid
            if fid:
                logger.info("Campo '%s' resuelto a %s", display_name, fid)
            else:
                logger.warning("Campo personalizado '%s' no encontrado en Jira.", display_name)

    def _request_fields(self) -> List[str]:
        ids = list(STANDARD_FIELDS)
        for fid in self.custom_ids.values():
            if fid:
                ids.append(fid)
        return ids

    # ------------------------------------------------------------------ #
    # Búsqueda JQL con paginación
    # ------------------------------------------------------------------ #
    def _build_jql(self) -> str:
        keys = ", ".join(f'"{k}"' for k in self.config.project_keys)
        jql = (
            f"project in ({keys}) "
            f"AND (created >= -{self.config.lookback_days}d "
            f"OR updated >= -{self.config.lookback_days}d) "
            f"ORDER BY created DESC"
        )
        logger.info("JQL construido: %s", jql)
        return jql

    def iter_issues(self) -> Iterator[Dict[str, Any]]:
        jql = self._build_jql()
        request_fields = self._request_fields()
        start_at = 0
        total = None
        fetched = 0

        while True:
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": self.config.page_size,
                "fields": request_fields,
            }
            data = self._post("/rest/api/3/search", payload)

            if total is None:
                total = data.get("total", 0)
                logger.info("Jira reporta %d issues coincidentes.", total)

            issues = data.get("issues", [])
            if not issues:
                break

            for issue in issues:
                yield self._normalize(issue)

            fetched += len(issues)
            start_at += len(issues)
            logger.info("Descargados %d/%s issues.", fetched, total)

            if fetched >= (total or 0):
                break

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Resuelve campos personalizados y devuelve todos los issues normalizados."""
        self.resolve_custom_fields()
        issues = list(self.iter_issues())
        logger.info("Total de issues normalizados: %d", len(issues))
        return issues

    # ------------------------------------------------------------------ #
    # Normalización
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_value(raw: Any) -> Optional[str]:
        """Extrae un valor legible de un campo que puede ser string, dict o lista."""
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            # campos tipo select/option: {"value": "..."} o {"name": "..."}
            return raw.get("value") or raw.get("name") or raw.get("displayName")
        if isinstance(raw, list) and raw:
            return ", ".join(filter(None, (JiraClient._extract_value(x) for x in raw)))
        return str(raw)

    def _normalize(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        f = issue.get("fields", {}) or {}
        assignee = f.get("assignee") or {}
        reporter = f.get("reporter") or {}
        priority = f.get("priority") or {}
        status = f.get("status") or {}
        status_category = (status.get("statusCategory") or {}) if isinstance(status, dict) else {}
        issuetype = f.get("issuetype") or {}
        project = f.get("project") or {}
        resolution = f.get("resolution") or {}

        def custom(slot: str) -> Optional[str]:
            fid = self.custom_ids.get(slot)
            return self._extract_value(f.get(fid)) if fid else None

        return {
            "key": issue.get("key", ""),
            "summary": f.get("summary", "") or "",
            "type": issuetype.get("name", "Sin tipo"),
            "activity": custom("activity") or "Sin actividad",
            "status": status.get("name", "Sin estado"),
            "status_category": status_category.get("key", "new"),
            "resolution": (resolution.get("name") if isinstance(resolution, dict) else None) or "Sin resolver",
            "priority": priority.get("name", "Sin prioridad"),
            "assignee": assignee.get("displayName", "Sin asignar"),
            "reporter": reporter.get("displayName", "Sin informador"),
            "project": project.get("key", ""),
            "created": f.get("created"),
            "updated": f.get("updated"),
            "resolved": f.get("resolutiondate"),
            "duedate": f.get("duedate"),
            "start": custom("start"),
            "l4": custom("l4"),
        }
