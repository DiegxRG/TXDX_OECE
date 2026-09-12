import sqlite3
from contextlib import contextmanager
import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from engine.prioridad import evaluar as _evaluar_prioridad
from engine.estado import parse_dates_op

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "radar_txdx.db")

@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS oportunidades (
            ocid                     TEXT PRIMARY KEY,
            tender_id                TEXT,
            titulo                   TEXT NOT NULL,
            descripcion              TEXT,
            entidad                  TEXT,
            entidad_ruc              TEXT,
            departamento             TEXT,
            monto_referencial        REAL DEFAULT 0.0,
            moneda                   TEXT DEFAULT 'PEN',
            linea_servicio           TEXT NOT NULL,
            score                    INTEGER NOT NULL,
            matched_keywords         TEXT, -- JSON list
            matched_products         TEXT, -- JSON list
            fecha_publicacion        TEXT,
            fecha_cierre_propuestas  TEXT,
            fecha_cierre_consultas   TEXT,
            tipo_proceso             TEXT,
            metodo                   TEXT,
            url_bases                TEXT,
            url_oece                 TEXT,
            estado_interno           TEXT DEFAULT 'POR_EVALUAR',
            notas                    TEXT,
            created_at               TEXT,
            updated_at               TEXT
        );
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp                  TEXT DEFAULT (datetime('now', 'localtime')),
            paginas_escaneadas         INTEGER DEFAULT 0,
            total_releases_evaluados   INTEGER DEFAULT 0,
            nuevas_oportunidades       INTEGER DEFAULT 0,
            duracion_segundos          REAL DEFAULT 0.0,
            status                     TEXT DEFAULT 'COMPLETED',
            detalle                    TEXT
        );
        """)
        
        # Indices for rapid querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oportunidades_linea ON oportunidades(linea_servicio);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oportunidades_estado ON oportunidades(estado_interno);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oportunidades_score ON oportunidades(score DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_oportunidades_fecha ON oportunidades(fecha_publicacion DESC);")
        
        # Migración de columna analisis_bases si no existe
        cursor.execute("PRAGMA table_info(oportunidades);")
        columns = [row["name"] for row in cursor.fetchall()]
        if "analisis_bases" not in columns:
            cursor.execute("ALTER TABLE oportunidades ADD COLUMN analisis_bases TEXT;")
        # Migración de columna tipo (objeto SEACE para búsqueda rápida) si no existe
        if "tipo" not in columns:
            cursor.execute("ALTER TABLE oportunidades ADD COLUMN tipo TEXT;")
        # Metadatos de priorización y estado según metodología TxDx
        for col, ddl in [
            ("estado_ocds", "TEXT"),
            ("etapa", "TEXT"),
            ("prioridad", "TEXT"),
            ("nivel_encaje", "TEXT"),
            ("ventana", "TEXT"),
            ("fecha_detectada", "TEXT"),
            ("fecha_revisada", "TEXT"),
        ]:
            if col not in columns:
                cursor.execute(f"ALTER TABLE oportunidades ADD COLUMN {col} {ddl};")
        
        conn.commit()

def upsert_oportunidad(data: Dict[str, Any]) -> bool:
    """
    Inserta o actualiza una oportunidad.
    Retorna True si fue nueva inserción, False si ya existía y solo se actualizó.
    Respeta el estado_interno y notas existentes si ya fue catalogada por el usuario.
    """
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ocid, estado_interno, notas, fecha_detectada FROM oportunidades WHERE ocid = ?", (data["ocid"],))
        existing = cursor.fetchone()
        
        estado_interno = existing["estado_interno"] if existing else data.get("estado_interno", "POR_EVALUAR")
        notas = existing["notas"] if existing else data.get("notas", "")
        
        matched_kw_str = json.dumps(data.get("matched_keywords", []), ensure_ascii=False)
        matched_prod_str = json.dumps(data.get("matched_products", []), ensure_ascii=False)
        
        fecha_detectada = data.get("fecha_detectada") or (existing["fecha_detectada"] if existing and existing["fecha_detectada"] else now)

        cursor.execute("""
        INSERT INTO oportunidades (
            ocid, tender_id, titulo, descripcion, entidad, entidad_ruc, departamento,
            monto_referencial, moneda, linea_servicio, score, matched_keywords, matched_products,
            fecha_publicacion, fecha_cierre_propuestas, fecha_cierre_consultas,
            tipo_proceso, metodo, url_bases, url_oece, estado_interno, notas,
            tipo, estado_ocds, etapa, prioridad, nivel_encaje, ventana,
            fecha_detectada, fecha_revisada, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(ocid) DO UPDATE SET
            titulo = excluded.titulo,
            descripcion = excluded.descripcion,
            entidad = excluded.entidad,
            entidad_ruc = excluded.entidad_ruc,
            departamento = excluded.departamento,
            monto_referencial = excluded.monto_referencial,
            moneda = excluded.moneda,
            linea_servicio = excluded.linea_servicio,
            score = excluded.score,
            matched_keywords = excluded.matched_keywords,
            matched_products = excluded.matched_products,
            fecha_publicacion = COALESCE(excluded.fecha_publicacion, oportunidades.fecha_publicacion),
            fecha_cierre_propuestas = excluded.fecha_cierre_propuestas,
            fecha_cierre_consultas = excluded.fecha_cierre_consultas,
            tipo_proceso = excluded.tipo_proceso,
            metodo = excluded.metodo,
            url_bases = COALESCE(NULLIF(excluded.url_bases, ''), oportunidades.url_bases),
            url_oece = excluded.url_oece,
            tipo = excluded.tipo,
            estado_ocds = COALESCE(excluded.estado_ocds, oportunidades.estado_ocds),
            etapa = excluded.etapa,
            prioridad = excluded.prioridad,
            nivel_encaje = excluded.nivel_encaje,
            ventana = excluded.ventana,
            fecha_revisada = excluded.fecha_revisada,
            updated_at = excluded.updated_at;
        """, (
            data["ocid"],
            data.get("tender_id"),
            data["titulo"],
            data.get("descripcion", ""),
            data.get("entidad", "Entidad Pública"),
            data.get("entidad_ruc", ""),
            data.get("departamento", "Nacional"),
            float(data.get("monto_referencial") or 0.0),
            data.get("moneda", "PEN"),
            data["linea_servicio"],
            int(data["score"]),
            matched_kw_str,
            matched_prod_str,
            data.get("fecha_publicacion"),
            data.get("fecha_cierre_propuestas"),
            data.get("fecha_cierre_consultas"),
            data.get("tipo_proceso", "Proceso de Selección"),
            data.get("metodo", ""),
            data.get("url_bases", ""),
            data.get("url_oece", ""),
            estado_interno,
            notas,
            data.get("tipo", ""),
            data.get("estado_ocds", ""),
            data.get("etapa", ""),
            data.get("prioridad", ""),
            data.get("nivel_encaje", ""),
            data.get("ventana", ""),
            fecha_detectada,
            now,
            now if not existing else existing["ocid"], # preserves original created_at if exists
            now
        ))
        conn.commit()
        return existing is None

def list_oportunidades(
    linea_servicio: Optional[str] = None,
    estado_interno: Optional[str] = None,
    min_score: int = 0,
    search: Optional[str] = None,
    limit: Optional[int] = 100,
    offset: int = 0,
    solo_vigentes: bool = True,
    max_monto: Optional[float] = None
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM oportunidades WHERE score >= ?"
    params: List[Any] = [min_score]
    
    if linea_servicio and linea_servicio != "ALL":
        query += " AND linea_servicio = ?"
        params.append(linea_servicio)
        
    if estado_interno and estado_interno != "ALL":
        query += " AND estado_interno = ?"
        params.append(estado_interno)
        
    if search:
        query += " AND (titulo LIKE ? OR descripcion LIKE ? OR entidad LIKE ? OR tipo LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term])
        
    query += " ORDER BY fecha_publicacion DESC, score DESC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result = []
        ahora = datetime.now(timezone.utc)
        for r in rows:
            d = dict(r)
            d["matched_keywords"] = json.loads(d["matched_keywords"] or "[]")
            d["matched_products"] = json.loads(d["matched_products"] or "[]")
            d["analisis_bases"] = json.loads(d.get("analisis_bases") or "null") if d.get("analisis_bases") else None
            # Fallback para registros antiguos sin tipo: derivar de la primera keyword coincidente
            if not d.get("tipo") and d["matched_keywords"]:
                hits = [k for k in d["matched_keywords"] if not k.startswith("CUBSO-")]
                if hits:
                    picked = next((h for h in hits if len(h.split()) >= 2), hits[0])
                    d["tipo"] = picked.upper()

            info = parse_dates_op(d, ahora)
            d["es_vencida"] = info["estado"] == "CERRADO"
            d["es_fecha_corrupta"] = info["es_fecha_corrupta"]
            d["es_elegible"] = info["es_elegible"]
            d["_monto"] = float(d.get("monto_referencial") or 0)
            result.append(d)

    # Filtrado según parámetros solicitados
    filtered = []
    for d in result:
        if solo_vigentes and not d["es_elegible"]:
            continue
        if max_monto is not None and d["_monto"] >= max_monto:
            continue
        filtered.append(d)
    result = filtered[offset:] if limit is None else filtered[offset:offset + limit]

    # Evaluación diaria viva de priorización/ventana según estado y análisis guardado
    for d in result:
        info = _evaluar_prioridad(d)
        d["estado_actual"] = info["estado"]
        d["horas_restantes"] = info["horas_restantes"]
        d["motivo_vigencia"] = info["motivo_vigencia"]
        d["prioridad"] = info["prioridad"]
        d["nivel_encaje"] = info["nivel_encaje"]
        d["ventana"] = info["ventana"]
        d["etapa"] = info["etapa"]
        d["dias_restantes"] = info["dias_restantes"]
        d["garantia_barrera"] = info["garantia_barrera"]
        d["garantia"] = info["garantia"]
        d["requiere_provincia"] = info["requiere_provincia"]
        d["motivo_prioridad"] = info["motivo_prioridad"]
        d.pop("_pub", None)
        d.pop("_cierre", None)
        d.pop("_monto", None)

    return result

def update_estado_oportunidad(ocid: str, nuevo_estado: str, notas: Optional[str] = None) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        if notas is not None:
            cursor.execute("""
            UPDATE oportunidades 
            SET estado_interno = ?, notas = ?, updated_at = ? 
            WHERE ocid = ?
            """, (nuevo_estado, notas, now, ocid))
        else:
            cursor.execute("""
            UPDATE oportunidades 
            SET estado_interno = ?, updated_at = ? 
            WHERE ocid = ?
            """, (nuevo_estado, now, ocid))
        conn.commit()
        return cursor.rowcount > 0

def get_stats() -> Dict[str, Any]:
    """Estadísticas sobre oportunidades VIGENTES según metodología TxDx."""
    with get_connection() as conn:
        cursor = conn.cursor()
        vigentes = list_oportunidades(limit=None)

        total = len(vigentes)
        by_line: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        por_prioridad: Dict[str, int] = {}
        total_monto = 0.0
        for d in vigentes:
            by_line[d["linea_servicio"]] = by_line.get(d["linea_servicio"], 0) + 1
            by_status[d["estado_interno"]] = by_status.get(d["estado_interno"], 0) + 1
            _info = _evaluar_prioridad(d)
            por_prioridad[_info["prioridad"]] = por_prioridad.get(_info["prioridad"], 0) + 1
            if d["estado_interno"] != "DESCARTADO":
                total_monto += float(d["monto_referencial"] or 0)
        
        cursor.execute("SELECT * FROM scan_history ORDER BY id DESC LIMIT 1;")
        last_scan = cursor.fetchone()
        last_scan_dict = dict(last_scan) if last_scan else None
        
        return {
            "total_oportunidades": total,
            "por_linea": by_line,
            "por_estado": by_status,
            "por_prioridad": por_prioridad,
            "pipeline_monto_pen": total_monto,
            "ultimo_scan": last_scan_dict
        }

def record_scan_history(
    paginas: int,
    total_evaluados: int,
    nuevas_oportunidades: int,
    duracion: float,
    status: str = "COMPLETED",
    detalle: str = ""
):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO scan_history (paginas_escaneadas, total_releases_evaluados, nuevas_oportunidades, duracion_segundos, status, detalle)
        VALUES (?, ?, ?, ?, ?, ?);
        """, (paginas, total_evaluados, nuevas_oportunidades, duracion, status, detalle))
        conn.commit()

def save_analisis_bases(ocid: str, data: Dict[str, Any]) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        json_str = json.dumps(data, ensure_ascii=False)
        cursor.execute("""
        UPDATE oportunidades
        SET analisis_bases = ?, updated_at = ?
        WHERE ocid = ?;
        """, (json_str, now, ocid))
        conn.commit()
        return cursor.rowcount > 0

def get_analisis_bases(ocid: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT analisis_bases FROM oportunidades WHERE ocid = ?;", (ocid,))
        row = cursor.fetchone()
        if row and row["analisis_bases"]:
            return json.loads(row["analisis_bases"])
        return None

# Auto-initialize DB on import
init_db()
