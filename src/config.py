"""
config.py
=========
Carga y valida la configuración del proyecto desde variables de entorno.

En local: lee el archivo .env (vía python-dotenv).
En GitHub Actions: las variables vienen inyectadas como Secrets/Env.

Si falta una variable obligatoria, se lanza un error claro y el programa
se detiene antes de intentar conectarse a Jira.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# Carga el .env si existe (no falla si no está, p. ej. en CI).
load_dotenv()


class ConfigError(RuntimeError):
    """Error de configuración (variable faltante o inválida)."""


def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"Falta la variable de entorno obligatoria: {name}. "
            f"Revisa tu archivo .env o los Secrets del repositorio."
        )
    return value


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"La variable {name} debe ser un número entero, recibido: {raw!r}")


@dataclass(frozen=True)
class Config:
    """Configuración inmutable del proyecto."""

    base_url: str
    email: str
    api_token: str
    project_keys: List[str] = field(default_factory=list)
    lookback_days: int = 180
    page_size: int = 100
    duedate_field: str = "duedate"
    dashboard_title: str = "Dashboard Ejecutivo de Gestión - Jira"
    timezone: str = "America/Bogota"
    # Nombres visibles de campos personalizados (se resuelven a customfield_XXXXX).
    field_activity: str = "Actividad"
    field_start: str = "Fecha inicio"
    field_l4: str = "Fecha estimada L4"
    # Criterio de "vencido": "duedate" (Fecha de vencimiento) o "l4" (Fecha estimada L4).
    overdue_basis: str = "duedate"

    @property
    def auth(self) -> tuple[str, str]:
        """Tupla (email, token) para HTTP Basic Auth de Jira Cloud."""
        return (self.email, self.api_token)


def load_config() -> Config:
    """Construye y valida el objeto Config a partir del entorno."""
    base_url = _get_required("JIRA_BASE_URL").rstrip("/")
    email = _get_required("JIRA_EMAIL")
    api_token = _get_required("JIRA_API_TOKEN")

    raw_keys = _get_required("JIRA_PROJECT_KEYS")
    project_keys = [k.strip().upper() for k in raw_keys.split(",") if k.strip()]
    if not project_keys:
        raise ConfigError("JIRA_PROJECT_KEYS no contiene ninguna clave válida.")

    page_size = _get_int("JIRA_PAGE_SIZE", 100)
    page_size = max(1, min(page_size, 100))  # Jira Cloud limita a 100.

    return Config(
        base_url=base_url,
        email=email,
        api_token=api_token,
        project_keys=project_keys,
        lookback_days=_get_int("JIRA_LOOKBACK_DAYS", 180),
        page_size=page_size,
        duedate_field=os.getenv("JIRA_DUEDATE_FIELD", "duedate").strip() or "duedate",
        dashboard_title=os.getenv("DASHBOARD_TITLE", "Dashboard Ejecutivo de Gestión - Jira").strip(),
        timezone=os.getenv("DASHBOARD_TIMEZONE", "America/Bogota").strip() or "America/Bogota",
        field_activity=os.getenv("JIRA_FIELD_ACTIVITY", "Actividad").strip(),
        field_start=os.getenv("JIRA_FIELD_START", "Fecha inicio").strip(),
        field_l4=os.getenv("JIRA_FIELD_L4", "Fecha estimada L4").strip(),
        overdue_basis=(os.getenv("JIRA_OVERDUE_BASIS", "duedate").strip().lower() or "duedate"),
    )
