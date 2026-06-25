"""
report_generator.py
====================
Renderiza el dashboard HTML a partir del diccionario de metricas y la
plantilla Jinja2 (templates/dashboard.html.j2).

El dashboard se renderiza del lado del servidor (SSR): KPIs, graficos de
barras CSS y tabla quedan en el HTML, por lo que se visualiza sin JS.
Los datos tambien se incrustan como JSON para la capa interactiva.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)


def render_dashboard(
    metrics: Dict[str, Any],
    templates_dir: Path,
    output_path: Path,
) -> Path:
    """Renderiza la plantilla y escribe el index.html final."""
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("dashboard.html.j2")

    metrics_json = json.dumps(metrics, ensure_ascii=False)

    html = template.render(
        metrics=metrics,
        metrics_json=metrics_json,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard escrito en: %s (%d bytes)", output_path, len(html))
    return output_path
