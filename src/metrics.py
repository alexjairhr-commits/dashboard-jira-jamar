"""
metrics.py
==========
Calculo de todos los indicadores de gestion a partir de la lista de
issues normalizados que entrega jira_client.

No depende de Jira ni de la red: es logica pura y por tanto facil de
testear. Devuelve un unico diccionario `metrics` listo para inyectar
en la plantilla HTML como JSON.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse(dt: Optional[str]) -> Optional[datetime]:
    """Parsea una fecha ISO de Jira (con o sin zona) a datetime aware UTC."""
    if not dt:
        return None
    try:
        d = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        try:
            d = datetime.strptime(dt[:10], "%Y-%m-%d")
            return d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_done(issue: Dict[str, Any]) -> bool:
    return issue.get("status_category") == "done"


def _is_in_progress(issue: Dict[str, Any]) -> bool:
    return issue.get("status_category") == "indeterminate"


def _is_open(issue: Dict[str, Any]) -> bool:
    return not _is_done(issue)


def _is_overdue_on(issue: Dict[str, Any], field: str) -> bool:
    """Vencido respecto a un campo de fecha dado y solo si no esta terminado."""
    if _is_done(issue):
        return False
    d = _parse(issue.get(field))
    return bool(d and d < _now())


def compute_metrics(
    issues: List[Dict[str, Any]],
    title: str,
    timezone_name: str,
    overdue_basis: str = "duedate",
) -> Dict[str, Any]:
    """Calcula el set completo de indicadores."""
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

    compliance = round((n_done / total) * 100, 1) if total else 0.0

    resolution_days: List[float] = []
    for i in done:
        created = _parse(i.get("created"))
        resolved = _parse(i.get("resolved"))
        if created and resolved and resolved >= created:
            resolution_days.append((resolved - created).total_seconds() / 86400.0)
    avg_resolution = round(sum(resolution_days) / len(resolution_days), 1) if resolution_days else 0.0

    by_assignee = _counter_to_sorted([i["assignee"] for i in issues])
    by_reporter = _counter_to_sorted([i.get("reporter", "Sin informador") for i in issues])
    by_priority = _counter_to_sorted([i["priority"] for i in issues])
    by_status = _counter_to_sorted([i["status"] for i in issues])
    by_type = _counter_to_sorted([i["type"] for i in issues])
    by_activity = _counter_to_sorted([i.get("activity", "Sin actividad") for i in issues])
    by_resolution = _counter_to_sorted([i.get("resolution", "Sin resolver") for i in issues])
    by_project = _counter_to_sorted([i["project"] for i in issues])

    weekly = _trend(issues, "%Y-S%W", weeks=12)
    monthly = _trend(issues, "%Y-%m", weeks=None, months=12)

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
        "Metricas: total=%d abiertos=%d cerrados=%d en_progreso=%d vencidos=%d cumplimiento=%.1f%%",
        total, n_open, n_done, n_in_progress, n_overdue, compliance,
    )
    return metrics


def _counter_to_sorted(values: List[str]) -> Dict[str, List]:
    counter = Counter(values)
    items = counter.most_common()
    return {
        "labels": [k for k, _ in items],
        "data": [v for _, v in items],
    }


def _trend(issues, fmt: str, weeks: Optional[int] = 12, months: Optional[int] = None) -> Dict[str, List]:
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
    window = months if months else weeks
    if window:
        periods = periods[-window:]

    return {
        "labels": periods,
        "created": [created_counter.get(p, 0) for p in periods],
        "closed": [closed_counter.get(p, 0) for p in periods],
    }


def _build_table(issues: List[Dict[str, Any]], limit: int = 5000) -> List[Dict[str, Any]]:
    rows = []
    for i in issues[:limit]:
        rows.append({
            "key": i["key"],
            "summary": i["summary"][:120],
            "type": i["type"],
            "activity": i.get("activity", "Sin actividad"),
            "status": i["status"],
            "status_category": i["status_category"],
            "resolution": i.get("resolution", "Sin resolver"),
            "priority": i["priority"],
            "assignee": i["assignee"],
            "reporter": i.get("reporter", "Sin informador"),
            "project": i["project"],
            "created": (i.get("created") or "")[:10],
            "updated": (i.get("updated") or "")[:10],
            "start": (i.get("start") or "")[:10],
            "resolved": (i.get("resolved") or "")[:10],
            "duedate": (i.get("duedate") or "")[:10],
            "l4": (i.get("l4") or "")[:10],
            "overdue": _is_overdue_on(i, "duedate"),
            "overdue_l4": _is_overdue_on(i, "l4"),
        })
    return rows
