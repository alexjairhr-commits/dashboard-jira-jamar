"""
metrics.py
==========
Cálculo de todos los indicadores de gestión a partir de la lista de
issues normalizados que entrega jira_client.

No depende de Jira ni de la red: es lógica pura y por tanto fácil de
testear. Devuelve un único diccionario `metrics` listo para inyectar
en la plantilla HTML como JSON.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Utilidades de fechas
# --------------------------------------------------------------------- #
def _parse(dt: Optional[str]) -> Optional[datetime]:
    """Parsea una fecha ISO de Jira (con o sin zona) a datetime aware UTC."""
    if not dt:
        return None
    try:
        # Jira usa formato tipo 2026-06-25T10:30:00.000-0500
        d = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        # Algunos campos (duedate) llegan como 'YYYY-MM-DD'
        try:
            d = datetime.strptime(dt[:10], "%Y-%m-%d")
            return d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------- #
# Clasificación de estado
# --------------------------------------------------------------------- #
def _is_done(issue: Dict[str, Any]) -> bool:
    return issue.get("status_category") == "done"


def _is_in_progress(issue: Dict[str, Any]) -> bool:
    return issue.get("status_category") == "indeterminate"


def _is_open(issue: Dict[str, Any]) -> bool:
    # "Abierto" = nuevo o en progreso (todo lo que no está terminado).
    return not _is_done(issue)


def _is_overdue_on(issue: Dict[str, Any], field: str) -> bool:
    """Vencido respecto a un campo de fecha dado y solo si no está terminado."""
    if _is_done(issue):
        return False
    d = _parse(issue.get(field))
    return bool(d and d < _now())


# --------------------------------------------------------------------- #
# Cálculo principal
# --------------------------------------------------------------------- #
def compute_metrics(
    issues: List[Dict[str, Any]],
    title: str,
    timezone_name: str,
    overdue_basis: str = "duedate",
) -> Dict[str, Any]:
    """Calcula el set completo de indicadores.

    overdue_basis: campo usado para el KPI principal de "vencidos".
        "duedate" -> Fecha de vencimiento;  "l4" -> Fecha estimada L4.
    """
    basis_field = "l4" if overdue_basis == "l4" else "duedate"

    total = len(issues)
    done = [i for i in issues if _is_done(i)]
    in_progress = [i for i in issues if _is_in_progress(i)]
    open_issues = [i for i in issues if _is_open(i)]
    overdue = [i for i in issues if _is_overdue_on(i, basis_field)]
    overdue_l4 = [i for i in issues if _is_overdue_on(i, "l4")]

    n_done = len(done)
    n_in_progress = len(in_progress)
    n_open = len(open_issues)
    n_overdue = len(overdue)
    n_overdue_l4 = len(overdue_l4)

    # Cumplimiento (%) = cerrados / total.
    compliance = round((n_done / total) * 100, 1) if total else 0.0

    # Tiempo promedio de resolución (días) sobre los issues cerrados.
    resolution_days: List[float] = []
    for i in done:
        created = _parse(i.get("created"))
        resolved = _parse(i.get("resolved"))
        if created and resolved and resolved >= created:
            resolution_days.append((resolved - created).total_seconds() / 86400.0)
    avg_resolution = round(sum(resolution_days) / len(resolution_days), 1) if resolution_days else 0.0

    # Distribuciones (orden descendente por cantidad).
    by_assignee = _counter_to_sorted([i["assignee"] for i in issues])
    by_reporter = _counter_to_sorted([i.get("reporter", "Sin informador") for i in issues])
    by_priority = _counter_to_sorted([i["priority"] for i in issues])
    by_status = _counter_to_sorted([i["status"] for i in issues])
    by_type = _counter_to_sorted([i["type"] for i in issues])
    by_activity = _counter_to_sorted([i.get("activity", "Sin actividad") for i in issues])
    by_resolution = _counter_to_sorted([i.get("resolution", "Sin resolver") for i in issues])
    by_project = _counter_to_sorted([i["project"] for i in issues])

    # Tendencias semanal y mensual (creados vs cerrados).
    weekly = _trend(issues, "%Y-S%W", weeks=12)
    monthly = _trend(issues, "%Y-%m", weeks=None, months=12)

    # Tabla detallada (limitada para mantener el HTML ligero).
    table = _build_table(issues)

    metrics = {
        "meta": {
            "title": title,
            "timezone": timezone_name,
            "generated_at": _now().isoformat(),
            "generated_at_human": _now().strftime("%Y-%m-%d %H:%M UTC"),
        },
        "kpis": {
            "total": total,
            "open": n_open,
            "closed": n_done,
            "in_progress": n_in_progress,
            "overdue": n_overdue,
            "overdue_l4": n_overdue_l4,
            "overdue_basis": basis_field,
            "compliance": compliance,
            "avg_resolution_days": avg_resolution,
        },
        "by_assignee": by_assignee,
        "by_reporter": by_reporter,
        "by_priority": by_priority,
        "by_status": by_status,
        "by_type": by_type,
        "by_activity": by_activity,
        "by_resolution": by_resolution,
        "by_project": by_project,
        "trend_weekly": weekly,
        "trend_monthly": monthly,
        "table": table,
        "filters": {
            "projects": sorted({i["project"] for i in issues if i["project"]}),
            "assignees": sorted({i["assignee"] for i in issues if i["assignee"]}),
            "reporters": sorted({i.get("reporter", "") for i in issues if i.get("reporter")}),
            "activities": sorted({i.get("activity", "") for i in issues if i.get("activity")}),
        },
    }
    logger.info(
        "Métricas: total=%d abiertos=%d cerrados=%d en_progreso=%d vencidos=%d cumplimiento=%.1f%%",
        total, n_open, n_done, n_in_progress, n_overdue, compliance,
    )
    return metrics


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #
def _counter_to_sorted(values: List[str]) -> Dict[str, List]:
    """Devuelve {'labels': [...], 'data': [...]} ordenado desc por cantidad."""
    counter = Counter(values)
    items = counter.most_common()
    return {
        "labels": [k for k, _ in items],
        "data": [v for _, v in items],
    }


def _trend(issues, fmt: str, weeks: Optional[int] = 12, months: Optional[int] = None) -> Dict[str, List]:
    """Serie temporal de creados vs cerrados agrupada por periodo."""
    created_counter: Dict[str, int] = defaultdict(int)
    closed_counter: Dict[str, int] = defaultdict(int)

    for i in issues:
        c = _parse(i.get("created"))
        if c:
            created_counter[c.strftime(fmt)] += 1
        r = _parse(i.get("resolved"))
        if r:
            closed_counter[r.strftime(fmt)] += 1

    periods = sorted(set(created_counter) | set(closed_counter))
    window = 