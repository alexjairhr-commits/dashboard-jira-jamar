# 📊 Dashboard Ejecutivo de Jira — 100 % Automático

Solución sin servidor que **consulta Jira Cloud por API REST**, calcula indicadores de gestión y publica un **dashboard HTML profesional** en **GitHub Pages**. Se actualiza solo todos los días vía GitHub Actions. **Cero costo**: solo tecnologías gratuitas y open source.

---

## ✨ Qué hace

- Se conecta a Jira Cloud (uno o varios proyectos) con API token.
- Descarga tickets, incidencias, historias, tareas y bugs con sus estados, prioridades, responsables y fechas.
- Calcula KPIs: total, abiertos, cerrados, en progreso, vencidos, % cumplimiento, tiempo promedio de resolución, distribución por responsable / prioridad / estado / tipo, y tendencias semanal y mensual.
- Genera un dashboard responsive con tarjetas KPI, gráficos interactivos (Chart.js), tabla dinámica y filtros por proyecto, responsable y fecha, con indicadores tipo **semáforo**.
- Publica automáticamente la nueva versión cada día.

---

## 🗂️ Estructura del proyecto

```
jira-dashboard/
├── .github/workflows/deploy.yml   # CI/CD: corre diario, genera y publica en Pages
├── src/
│   ├── __init__.py
│   ├── config.py                  # Carga y valida variables de entorno
│   ├── jira_client.py             # Cliente API REST con paginación y reintentos
│   ├── metrics.py                 # Cálculo de todos los indicadores
│   ├── report_generator.py        # Renderiza el HTML desde la plantilla Jinja2
│   └── main.py                    # Orquestador (punto de entrada)
├── templates/dashboard.html.j2    # Plantilla HTML5 + CSS3 + JS + Chart.js
├── public/                        # Aquí se genera index.html (lo sirve Pages)
├── logs/                          # Logs de ejecución (logs/run.log)
├── .env.example                   # Plantilla de variables de entorno
├── .gitignore
├── requirements.txt
└── README.md
```

### Qué hace cada archivo

| Archivo | Responsabilidad |
|---|---|
| `config.py` | Lee el `.env` (o los Secrets en CI), valida que existan las variables obligatorias y expone un objeto `Config` inmutable. Si falta algo, detiene el programa con un mensaje claro. |
| `jira_client.py` | Encapsula la API REST de Jira (`/rest/api/3/search`). Construye el JQL, pagina los resultados, reintenta ante errores 429/5xx con *backoff* exponencial y normaliza cada issue a un diccionario plano. |
| `metrics.py` | Lógica pura (sin red). Recibe la lista de issues y calcula todos los KPIs, distribuciones y tendencias. Devuelve un único diccionario listo para serializar. |
| `report_generator.py` | Toma las métricas, las inyecta como JSON en la plantilla Jinja2 y escribe `public/index.html`. Además **incrusta Chart.js** desde `vendor/chart.umd.min.js`, de modo que el dashboard funciona sin internet ni CDN (si falta el vendor, usa el CDN como respaldo). |
| `vendor/chart.umd.min.js` | Copia local de Chart.js v4.4.4 (MIT) que se incrusta en el HTML. Hace el dashboard 100 % autónomo. |
| `main.py` | Orquesta el flujo completo, configura el logging (consola + `logs/run.log`) y devuelve códigos de salida para que CI marque fallos. |
| `templates/dashboard.html.j2` | El dashboard. Recalcula KPIs y gráficos en el navegador según los filtros, así que es totalmente interactivo sin backend. |
| `deploy.yml` | GitHub Actions: agenda diaria, instala dependencias, corre el script y despliega `public/` en Pages. |

---

## 🚀 Instalación local

> Requisitos: **Python 3.10+** y **git**.

```bash
# 1. Clonar / copiar el proyecto y entrar a la carpeta
cd jira-dashboard

# 2. (Recomendado) crear entorno virtual
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar credenciales
cp .env.example .env        # Windows: copy .env.example .env
#   …edita .env con tus datos reales…

# 5. Ejecutar
python -m src.main
```

El dashboard se genera en `public/index.html`. Ábrelo en el navegador para verlo.

---

## 🔑 Configuración de Jira (paso a paso)

1. **Crea tu API token**
   - Entra a <https://id.atlassian.com/manage-profile/security/api-tokens>.
   - Clic en **Create API token**, ponle un nombre (p. ej. `dashboard`) y **copia el valor** (solo se muestra una vez).

2. **Identifica las claves de tus proyectos**
   - Es el prefijo de los tickets. Si tus issues son `OPS-101`, la clave es `OPS`.
   - Puedes poner varias separadas por coma: `OPS,SOPORTE,DEV`.

3. **Completa el archivo `.env`**
   ```bash
   JIRA_BASE_URL=https://tu-empresa.atlassian.net
   JIRA_EMAIL=tu-correo@tu-empresa.com
   JIRA_API_TOKEN=el_token_que_copiaste
   JIRA_PROJECT_KEYS=OPS,SOPORTE
   ```

4. **Verifica el campo de vencimiento (opcional)**
   - Por defecto se usa `duedate` (Fecha de vencimiento de Jira). Si usas un campo SLA personalizado, ajusta `JIRA_DUEDATE_FIELD`.

5. **Campos personalizados (Actividad, Fecha inicio, Fecha estimada L4)**
   - El dashboard incluye estas columnas que tienes en tu Jira. Los campos personalizados tienen un ID interno (`customfield_XXXXX`) distinto en cada instancia, así que el cliente **lo resuelve solo** a partir del nombre visible.
   - Solo asegúrate de que el nombre coincida exactamente con el de tu Jira:
     ```bash
     JIRA_FIELD_ACTIVITY=Actividad
     JIRA_FIELD_START=Fecha inicio
     JIRA_FIELD_L4=Fecha estimada L4
     ```
   - El KPI principal de "vencidos" usa por defecto la Fecha de vencimiento. Para basarlo en la Fecha estimada L4, pon `JIRA_OVERDUE_BASIS=l4`. El dashboard muestra ambos vencimientos de todos modos.

> 🔒 El token da acceso a Jira con tus permisos. Nunca lo subas al repo: el `.env` ya está en `.gitignore` y en GitHub se guarda como *Secret* cifrado.

---

## ☁️ Despliegue en GitHub Pages (paso a paso)

1. **Crea el repositorio** en GitHub y sube el proyecto:
   ```bash
   git init
   git add .
   git commit -m "Dashboard de Jira inicial"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/jira-dashboard.git
   git push -u origin main
   ```

2. **Activa GitHub Pages**
   - Repo → **Settings** → **Pages**.
   - En *Build and deployment* → *Source*, elige **GitHub Actions**.

3. **Carga los Secrets** (Settings → Secrets and variables → **Actions** → pestaña *Secrets* → *New repository secret*):

   | Secret | Valor |
   |---|---|
   | `JIRA_BASE_URL` | `https://tu-empresa.atlassian.net` |
   | `JIRA_EMAIL` | tu correo Atlassian |
   | `JIRA_API_TOKEN` | tu API token |
   | `JIRA_PROJECT_KEYS` | `OPS,SOPORTE` |

4. **(Opcional) Carga las Variables** (misma pantalla, pestaña *Variables*):

   | Variable | Ejemplo |
   |---|---|
   | `JIRA_LOOKBACK_DAYS` | `180` |
   | `DASHBOARD_TITLE` | `Dashboard Ejecutivo - Operaciones` |
   | `DASHBOARD_TIMEZONE` | `America/Bogota` |

5. **Lanza el primer despliegue**
   - Pestaña **Actions** → *Generar y publicar Dashboard de Jira* → **Run workflow**.
   - Al terminar, la URL aparece en el job `deploy` y en Settings → Pages:
     `https://TU_USUARIO.github.io/jira-dashboard/`

A partir de aquí se regenera **solo, todos los días a las 06:00 UTC**.

---

## ⏰ Cambiar la frecuencia de actualización

Edita el `cron` en `.github/workflows/deploy.yml`:

```yaml
schedule:
  - cron: "0 6 * * *"     # diario 06:00 UTC
  # - cron: "0 */6 * * *" # cada 6 horas
  # - cron: "0 12 * * 1"  # lunes 12:00 UTC
```

---

## 🔐 Seguridad y robustez

- **Credenciales** solo en `.env` (local) o *Secrets* cifrados (CI). Nunca en el código ni en el HTML.
- **Sin backend**: el dashboard es estático; el token jamás llega al navegador.
- **Reintentos automáticos** con *backoff* exponencial ante fallos de red o