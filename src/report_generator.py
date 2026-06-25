"""
report_generator.py
====================
Renderiza el dashboard HTML a partir del diccionario de métricas y la
plantilla Jinja2 (templates/dashboard.html.j2).

El resultado es un único index.html autocontenido: los datos se
inyectan como un objeto JSON dentro de una etiqueta <script>, de modo
que GitHub Pages solo necesita servir estáticos (sin backend y sin
exponer credenciales).
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
    """Renderiza la plantilla y escribe el index.html final.

    El dashboard se renderiza del lado del servidor (KPIs, gráficos de barras
    CSS y tabla quedan en el HTML), por lo que se visualiza sin JavaScript.
    Los datos también se incrustan como JSON para la capa interactiva
    (filtros y búsqueda) que se activa en un navegador.
    """
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("dashboard.html.j2")

    # Los datos se serializan a JSON y se pasan tal cual a la plantilla.
    metrics_json = json.dumps(metrics, ensure_ascii=False)

    html = templa