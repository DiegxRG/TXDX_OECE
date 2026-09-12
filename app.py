import os
import csv
import io
import threading
import time
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db.database import (
    list_oportunidades,
    update_estado_oportunidad,
    get_stats,
    get_connection,
    save_analisis_bases,
    get_analisis_bases
)
from engine.estado import PERU_TZ
from engine.scanner import RadarScanner
from engine.pdf_analyzer import BasesAnalyzer, get_gemini_key, save_gemini_key, get_groq_key, save_groq_key
import re

app = FastAPI(
    title="TxDx Radar OECE API",
    description="API para el Radar de Contrataciones Públicas TxDx",
    version="1.0.0"
)

# Estado de scan en background
scanner_lock = threading.Lock()
is_scanning = False
last_scan_result = None
scan_progress = {}
scan_started = None

class UpdateEstadoRequest(BaseModel):
    estado_interno: str
    notas: Optional[str] = None

class ScanRequest(BaseModel):
    pages: int = Field(default=2, ge=1, le=5)
    start_page: int = Field(default=1, ge=1, le=1)
    year: str = Field(default_factory=lambda: str(datetime.now(PERU_TZ).year))
    query: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class GeminiKeyRequest(BaseModel):
    api_key: str

class GroqKeyRequest(BaseModel):
    api_key: str

def _background_scan_worker(pages: int = 2, start_page: int = 1, year: Optional[str] = None, query: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
    global is_scanning, last_scan_result
    def publish(state):
        global scan_progress
        with scanner_lock:
            scan_progress = state
    result = {"status": "ERROR", "error": "Escaneo interrumpido"}
    try:
        scanner = RadarScanner()
        result = scanner.run_smart_scan(
            year=year, query=query, start_date=start_date, end_date=end_date,
            max_pages_per_query=pages, progress=publish, max_seconds=180)
    except Exception as e:
        result = {"status": "ERROR", "error": str(e)}
    finally:
        with scanner_lock:
            last_scan_result = result
            is_scanning = False

@app.get("/api/scan/status")
def api_scan_status():
    with scanner_lock:
        state = dict(scan_progress)
        if is_scanning and scan_started is not None:
            state["elapsed_seconds"] = round(time.monotonic() - scan_started, 1)
        return {"is_scanning": is_scanning, "progress": state,
                "last_scan_result": last_scan_result}

@app.get("/api/config/gemini-key")
def api_get_gemini_key_status():
    key = get_gemini_key()
    return {
        "configured": bool(key),
        "preview": f"...{key[-4:]}" if key and len(key) >= 4 else None
    }

@app.post("/api/config/gemini-key")
def api_set_gemini_key(req: GeminiKeyRequest):
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="Clave no puede estar vacía")
    ok = save_gemini_key(req.api_key.strip())
    return {"status": "saved" if ok else "error"}

@app.get("/api/config/groq-key")
def api_get_groq_key_status():
    key = get_groq_key()
    return {
        "configured": bool(key),
        "preview": f"...{key[-4:]}" if key and len(key) >= 4 else None
    }

@app.post("/api/config/groq-key")
def api_set_groq_key(req: GroqKeyRequest):
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="Clave no puede estar vacía")
    ok = save_groq_key(req.api_key.strip())
    return {"status": "saved" if ok else "error"}

@app.get("/api/stats")
def api_get_stats():
    stats = get_stats()
    stats["is_scanning"] = is_scanning
    stats["last_scan_result"] = last_scan_result
    key = get_gemini_key()
    stats["gemini_configured"] = bool(key)
    groq = get_groq_key()
    stats["groq_configured"] = bool(groq)
    return stats

@app.get("/api/oportunidades")
def api_get_oportunidades(
    linea_servicio: Optional[str] = Query(None),
    estado_interno: Optional[str] = Query(None),
    min_score: int = Query(0),
    search: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
    solo_vigentes: bool = Query(True),
    max_monto: Optional[float] = Query(None)
):
    ops = list_oportunidades(
        linea_servicio=linea_servicio,
        estado_interno=estado_interno,
        min_score=min_score,
        search=search,
        limit=limit,
        offset=offset,
        solo_vigentes=solo_vigentes,
        max_monto=max_monto
    )
    for op in ops:
        titulo = op.get("titulo") or ""
        y_m = re.search(r'-(\d{4})-', titulo)
        year = y_m.group(1) if y_m else ((op.get("fecha_publicacion") or "")[:4] or str(datetime.now(PERU_TZ).year))
        n_m = re.search(r'-(\d+)-\d{4}-', titulo)
        num = n_m.group(1) if n_m else ""
        
        desc = op.get("descripcion") or ""
        clean_desc = re.sub(r'^(?:contrataci[oó]n|servicio|adquisici[oó]n|ejecuci[oó]n)\s+(?:del\s+|de\s+|para\s+la\s+|para\s+el\s+)*', '', desc, flags=re.I).strip()
        
        objeto = (op.get("tipo") or "").strip() or clean_desc or desc or "TI"

        seace_guide = {
            "nomenclatura": titulo,
            "ano": year,
            "numero": num,
            "objeto_sugerido": objeto,
            "descripcion_completa": desc or titulo,
            "entidad": op.get("entidad") or "",
            "url_buscador": "https://prod1.seace.gob.pe/",
            "pasos": [
                f"Campo 'Descripción del Objeto': {objeto}",
                f"Campo 'Año de la nomenclatura': {year}",
                "Dejar todo lo demás en blanco o en [Seleccione]",
                "Pulsar [Buscar]"
            ]
        }
        op["seace_guide"] = seace_guide
    return ops

@app.post("/api/oportunidades/{ocid}/estado")
def api_update_estado(ocid: str, req: UpdateEstadoRequest):
    estados_validos = ["POR_EVALUAR", "INTERESANTE", "EN_PREPARACION", "POSTULADO", "DESCARTADO"]
    if req.estado_interno not in estados_validos:
        raise HTTPException(status_code=400, detail=f"Estado inválido. Válidos: {estados_validos}")
        
    ok = update_estado_oportunidad(ocid, req.estado_interno, req.notas)
    if not ok:
        raise HTTPException(status_code=404, detail="Oportunidad no encontrada")
    return {"status": "ok", "ocid": ocid, "nuevo_estado": req.estado_interno}

@app.get("/api/oportunidades/{ocid}/analisis")
def api_get_analisis(ocid: str):
    analisis = get_analisis_bases(ocid)
    if not analisis:
        raise HTTPException(status_code=404, detail="Análisis no encontrado para esta oportunidad")
    return analisis

@app.post("/api/oportunidades/{ocid}/analizar")
def api_analizar_oportunidad(ocid: str):
    # 1. Verificar si ya fue analizado antes
    cached = get_analisis_bases(ocid)
    if cached and cached.get("success"):
        return {"status": "cached", "data": cached}
        
    # 2. Buscar datos de la oportunidad
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM oportunidades WHERE ocid = ?;", (ocid,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Oportunidad no encontrada en el sistema")
        op = dict(row)

    url_bases = op.get("url_bases")
    if not url_bases:
        raise HTTPException(status_code=400, detail="Esta oportunidad no cuenta con URL directa de Bases disponible")
        
    # 3. Analizar PDF
    analyzer = BasesAnalyzer()
    resultado = analyzer.analyze_document(ocid=ocid, url_bases=url_bases, meta=op)
    
    if resultado.get("success"):
        save_analisis_bases(ocid, resultado)
        
    return {"status": "analyzed", "data": resultado}

@app.post("/api/scan")
def api_trigger_scan(req: ScanRequest, background_tasks: BackgroundTasks):
    global is_scanning, last_scan_result, scan_progress, scan_started
    with scanner_lock:
        if is_scanning:
            return {"status": "already_running", "message": "Un escaneo ya está en ejecución"}
        is_scanning = True
        last_scan_result = None
        scan_progress = {"stage": "Iniciando", "queries_done": 0, "queries_total": 0}
        scan_started = time.monotonic()
        
    background_tasks.add_task(
        _background_scan_worker,
        pages=req.pages,
        start_page=req.start_page,
        year=req.year,
        query=req.query,
        start_date=req.start_date,
        end_date=req.end_date
    )
    return {
        "status": "started",
        "year": req.year,
        "query": req.query,
        "start_date": req.start_date,
        "end_date": req.end_date
    }

@app.get("/api/export/csv")
def api_export_csv(solo_vigentes: bool = Query(True)):
    # CSV disponible por defecto; historial mediante solo_vigentes=false
    oportunidades = list_oportunidades(limit=None, solo_vigentes=solo_vigentes, max_monto=None)
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Encabezados
    writer.writerow([
        "OCID", "Tipo (Objeto SEACE)", "Línea de Servicio", "Score", "Estado Interno", "Título",
        "Entidad", "Departamento", "Monto PEN", "Fecha Publicación",
        "Cierre Consultas", "Cierre Propuestas", "Keywords Coincidentes",
        "Productos TxDx", "Link Bases", "Link OECE", "Notas"
    ])
    
    for op in oportunidades:
        writer.writerow([
            op["ocid"],
            op.get("tipo", ""),
            op["linea_servicio"],
            op["score"],
            op["estado_interno"],
            op["titulo"],
            op["entidad"],
            op["departamento"],
            op["monto_referencial"],
            op["fecha_publicacion"] or "",
            op["fecha_cierre_consultas"] or "",
            op["fecha_cierre_propuestas"] or "",
            ", ".join(op.get("matched_keywords", [])),
            ", ".join(op.get("matched_products", [])),
            op["url_bases"] or "",
            op["url_oece"] or "",
            op["notas"] or ""
        ])
        
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=radar_txdx_oportunidades.csv"}
    )

# Servir Frontend
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
os.makedirs(WEB_DIR, exist_ok=True)

CSS_DIR = os.path.join(WEB_DIR, "css")
JS_DIR = os.path.join(WEB_DIR, "js")
if os.path.isdir(CSS_DIR):
    app.mount("/css", StaticFiles(directory=CSS_DIR), name="css")
if os.path.isdir(JS_DIR):
    app.mount("/js", StaticFiles(directory=JS_DIR), name="js")

@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Radar TxDx Backend Activo</h1><p>Coloca index.html en la carpeta web/</p>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
