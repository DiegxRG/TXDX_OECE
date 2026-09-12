# Radar de Contrataciones Públicas TxDx (OECE)

Plataforma inteligente de detección y monitoreo de oportunidades de contratación pública en el Perú (Portal OCDS / OECE - SEACE v3), calibrada específicamente para las capacidades comerciales y servicios de **TxDx**:
- **Ciberseguridad** (Net Hardening, RansomSafe, Pentesting, SOC/SIEM, Criptografía Post-Cuántica)
- **Networking & Resiliencia** (Net Evolution, Resiliencia, Migración, Alta Disponibilidad, NOC)
- **Inteligencia Artificial & Automatización** (AutoTasking, AI Deployment Bot, RPA, Desarrollo de Software)
- **Gestión de Datos & Analítica Avanzada** (ADA Solution, Business Intelligence, Dashboards, Power BI)

---

## 🚀 Inicio Rápido

### 1. Iniciar el Dashboard Web
El servidor backend FastAPI corre en el puerto `8000`:

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Abre tu navegador en:  
👉 **[http://localhost:8000](http://localhost:8000)**

Desde el dashboard puedes:
* Ver el resumen de oportunidades detectadas y el pipeline económico estimado (PEN).
* Filtrar por línea de servicio (*Ciberseguridad*, *Networking*, *IA*, *Datos*).
* Filtrar por ventana de cierre: procesos que cierran en ≤5 días o abiertos con ventana &gt;5 días.
* Cambiar el estado interno de postulación: `Por Evaluar` ➔ `Interesante` ➔ `En Preparación` ➔ `Postulado` ➔ `Descartado`.
* Descargar directamente las **Bases Administrativas (PDF)** oficiales de SEACE sin buscar manualmente.
* Presionar **"Escanear Convocatorias"** para buscar en vivo nuevos procesos en el portal OECE.
* **Exportar a CSV** para reuniones de comité de licitaciones.

---

### 2. Uso por Línea de Comandos (CLI)
Si prefieres correr un escaneo rápido desde la terminal:

```bash
# Escanear las últimas 25 páginas de convocatorias (default)
python scan_cli.py

# Escanear 100 páginas para capturar también procesos abiertos más atrás en el histórico
python scan_cli.py --pages 100
```

---

## ⚙️ Configuración del Motor de Afinidad (`config/txdx_services.json`)

El clasificador semántico traduce las necesidades del Estado a los productos de TxDx y cuenta con un filtro estricto anti-falsos positivos:
* **CUBSO / UNSPSC:** Prioriza familias de TI (`811122`, `811118`, `811115`, `811120`, `432226`, `432332`).
* **Keywords Técnicas:** Detecta términos como *hacking ético, pentesting, firewall, cableado estructurado, rpa, switches, business intelligence, etc.*
* **Lista Negra:** Descarta automáticamente licitaciones no vinculadas (ej. obras civiles, agua potable, vigilancia física con guardianes, redes de salud asistenciales, limpieza).

Puedes agregar nuevas palabras clave o ajustar ponderaciones editando [`config/txdx_services.json`](config/txdx_services.json).

## ⚙️ Análisis de Bases con IA (Groq)

El analizador de PDF (`engine/pdf_analyzer.py`) usa **Groq** (API gratuita, OpenAI-compatible) para leer el Capítulo III de las Bases y extraer con precisión: experiencia del postor, personal clave, certificaciones, plazo, modalidad, forma de pago y factibilidad TxDx.

Configura tu clave gratuita (`gsk_...`) en:
- Archivo `config/groq_key.txt`, o
- Variable de entorno `GROQ_API_KEY`, o
- Botón **"Configurar IA (Groq)"** en el dashboard.

Si no hay clave Groq, cae a Gemini (`config/gemini_key.txt` / `GEMINI_API_KEY`) y luego al extractor heurístico local.

Cada oportunidad muestra un badge **`tipo`** (término de búsqueda rápida) y una guía **"Búsqueda rápida en SEACE"** con los dos campos exactos para localizar el proceso manualmente: *Descripción del Objeto* y *Año de la nomenclatura*.

---

## 📁 Estructura del Proyecto

```
TXDX-OECE/
├── app.py                     # Servidor FastAPI y endpoints de API
├── scan_cli.py                # Herramienta de escaneo en consola
├── radar_txdx.db              # Base de datos SQLite (Modo WAL)
├── config/
│   └── txdx_services.json     # Diccionario de servicios TxDx y reglas de scoring
├── db/
│   └── database.py            # Capa de datos y persistencia SQLite
├── engine/
│   ├── classifier.py          # Motor de scoring y normalización semántica
│   ├── oece_client.py         # Conector HTTP hacia la API de OECE
│   └── scanner.py             # Orquestador del escaneo de releases
├── web/
│   └── index.html             # Dashboard interactivo Dark-Mode TxDx
└── README.md
```

## Regla de participación (48 horas)

El radar, las estadísticas, el reporte y el CSV muestran por defecto procesos
con cierre de propuestas conocido y al menos 48 horas restantes. Un estado
CONVOCADO o una publicación reciente no rescata un plazo vencido. Las consultas
no sustituyen el cierre de propuestas. Las fechas sin zona se interpretan en Perú.
Los días mostrados son días completos, sin redondear hacia arriba.

Los registros cerrados, sin fecha, incoherentes o con menos de 48 horas se conservan
como historial: desactiva «Disponibles con al menos 48 horas» para consultarlos.
CSV histórico: `/api/export/csv?solo_vigentes=false`.

El escaneo usa el año actual de Perú por defecto y consulta el record completo
para obtener el cronograma y las bases identificadas. La fecha de actualización
OCDS no se utiliza como fecha de convocatoria. Una búsqueda parcial se registra
como PARTIAL; el portal puede omitir fechas y no se presume disponibilidad.

Pruebas de regresión: `python -m unittest discover -s tests -v`.

### Duración y progreso del escaneo

El botón consulta 2 páginas por término (antes enviaba 25). La API acepta de 1 a 5.
Se omite la consulta de expedientes con menos de 48 horas según el resultado de
búsqueda; esos resultados se guardan como historial conservando sus enlaces.
El escáner deja de iniciar consultas al cumplir 180 segundos y termina la petición
en curso (timeout de red de 8 segundos por petición). Si no completó la búsqueda,
reporta PARTIAL. No garantiza haber revisado todos los procesos del portal.
El panel muestra avance por términos, páginas, tiempo y errores mediante
`/api/scan/status`. Recupera el seguimiento al recargar y distingue éxito, resultado
parcial, fallo y pérdida de conexión. Solo admite un escaneo por proceso del servidor.
Para usar estos cambios, reinicia el servidor anterior y recarga la página.
