"""
main.py
=======
Punto de entrada / orquestador del pipeline:

    1. Carga y valida la configuración.
    2. Conecta a Jira y descarga todos los issues.
    3. Calcula las métricas.
    4. Renderiza el dashboard HTML en public/index.html.

Configura logging a consola y a archivo (logs/run.log). Devuelve un
código de salida distinto de 0 si algo falla, para que GitHub Actions
marque el job como fallido.

Uso:
    python -m src.main
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .jira_client import JiraApiError, JiraClient
from .metrics import compute_metrics
from .report_generator import render_dashboard

# Rutas base del proyecto (carpeta raíz = padre de src/).
ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_FILE = ROOT / "public" / "index.html"
LOG_DIR = ROOT / "logs"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
        ],
    )


def run() -> int:
    _setup_logging()
    log = logging.getLogger("main")

    try:
        log.info("== Iniciando generación del dashboard de Jira ==")

        config = load_config()
        log.info("Proyectos a consultar: %s", ", ".join(config.project_keys))

        client = JiraClient(config)
        issues = client.fetch_all()

        if not issues:
            log.warning("No se obtuvieron issues. Se generará un dashboard vacío.")

        metrics = compute_metrics(
            issues,
            config.dashboard_title,
            config.timezone,
            overdue_basis=config.overdue_basis,
        )

        render_dashboard(metrics, TEMPLATES_DIR, OUTPUT_FILE)

        log.info("== Proceso completado correctamente ==")
        return 0

    except ConfigError as e:
        log.error("Error de configuración: %s", e)
        return 2
    except JiraApiError as e:
        log.error("Error de Jira: %s", e)
        return 3
    except Exception as e:  # noqa: BLE001 - capturamos todo para loggear y salir limpio
        log.exception("Error inesperado: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(run())
