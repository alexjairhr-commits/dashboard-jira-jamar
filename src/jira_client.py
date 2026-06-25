"""
jira_client.py
==============
Cliente para la API REST de Jira Cloud (v3), usando el endpoint nuevo
/rest/api/3/search/jql (el anterior /rest/api/3/search fue eliminado).

Caracteristicas:
- Autenticacion HTTP Basic (email + API token).
- Resolucion automatica de campos personalizados por su nombre visible.
- Paginacion con nextPageToken (modelo nuevo de la API).
- Reintentos automaticos con backoff exponencial ante fallos de red o 429/5xx.
- Normalizacion de cada issue a un diccionario plano.
"""

from __future__ import annotations

import logging
import unicodedata
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


def _norm_name(s):
    """Normaliza un nombre: minusculas, sin tildes, espacios colapsados."""
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return " ".join(s.split())

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

_RETRYABLE = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class JiraApiError(RuntimeError):
    """Error no recuperable al hablar con la API de Jira."""


class JiraClient:
    """Cliente ligero y robusto para Jira Cloud (endpoint search/jql)."""

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
        self.custom_ids: Dict[str, Optional[str]] = {
            "activity": None,
            "start": None,
            "l4": None,
        }

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
    )
    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.config.base_url}{path}"
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)

        if resp.status_code == 429 or resp.status_code >= 500:
            logger.warning("Respuesta %s de Jira; se reintentara.", resp.status_code)
            raise requests.exceptions.ConnectionError(f"Jira devolvio {resp.status_code}")
        if resp.status_code == 401:
            raise JiraApiError("Autenticacion fallida (401). Verifica JIRA_EMAIL y JIRA_API_TOKEN.")
        if resp.status_code == 403:
            raise JiraApiError("Acceso denegado (403). La cuenta no tiene permiso sobre el proyecto.")
        if resp.status_code >= 400:
            raise JiraApiError(f"Error {resp.status_code} de Jira: {resp.text[:500]}")
        return resp.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params or {})

    def resolve_custom_fields(self) -> None:
        """Consulta /rest/api/3/field y mapea los nombres configurados a sus IDs.

        El emparejamiento es tolerante a tildes, mayusculas y espacios, e incluye
        un respaldo por coincidencia parcial. Tambien registra los campos
        candidatos para diagnostico.
        """
        try:
            fields = self._get("/rest/api/3/field")
        except JiraApiError as e:
            logger.warning("No se pudieron listar los campos de Jira: %s", e)
            return

        norm_to_id = {}
        for f in fields:
            nm = f.get("name") or ""
            k = _norm_name(nm)
            if k and k not in norm_to_id:
                norm_to_id[k] = f.get("id")
            # Diagnostico: muestra candidatos relevantes
            if any(t in k for t in ("activ", "inicio", "start", "l4", "estimada")):
                logger.info("Campo disponible en Jira: '%s' -> %s", nm, f.get("id"))

        wanted = {
            "activity": self.config.field_activity,
            "start": self.config.field_start,
            "l4": self.config.field_l4,
        }
        for slot, display_name in wanted.items():
            if not display_name:
                continue
            key = _norm_name(display_name)
            fid = norm_to_id.get(key)
            if not fid:
                for k, v in norm_to_id.items():
                    if k and (k.startswith(key) or key.startswith(k) or key in k):
                        fid = v
                        break
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
        """Itera todos los issues usando el endpoint nuevo /search/jql con nextPageToken."""
        jql = self._build_jql()
        request_fields = self._request_fields()
        next_page_token: Optional[str] = None
        fetched = 0

        while True:
            payload: Dict[str, Any] = {
                "jql": jql,
                "maxResults": self.config.page_size,
                "fields": request_fields,
            }
            if next_page_token:
                payload["nextPageToken"] = next_page_token

            data = self._post("/rest/api/3/search/jql", payload)
            issues = data.get("issues", []) or []
            for issue in issues:
                yield self._normalize(issue)

            fetched += len(issues)
            logger.info("Descargados %d issues.", fetched)

            next_page_token = data.get("nextPageToken")
            if data.get("isLast") or not next_page_token or not issues:
                break

    def fetch_all(self) -> List[Dict[str, Any]]:
        """Resuelve campos personalizados y devuelve todos los issues normalizados."""
        self.resolve_custom_fields()
        issues = list(self.iter_issues())
        logger.info("Total de issues normalizados: %d", len(issues))
        return issues

    @staticmethod
    def _extract_value(raw: Any) -> Optional[str]:
        """Extrae un valor legible de un campo que puede ser string, dict o lista."""
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
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
