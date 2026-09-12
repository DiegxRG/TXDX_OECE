import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Set, Callable

from engine.oece_client import OECEClient
from engine.classifier import TxDxClassifier
from engine.estado import extraer_estado_official, parse_fecha, PERU_TZ, ESTADOS_CERRADOS
from engine.prioridad import evaluar as evaluar_prioridad
from db.database import upsert_oportunidad, record_scan_history

# Términos de búsqueda representativos por cada línea de servicio de TxDx
CORE_SEARCH_QUERIES = {
    "CIBERSEGURIDAD": [
        "ciberseguridad", "seguridad de la informacion", "seguridad informatica",
        "seguridad perimetral", "firewall", "soc", "siem", "edr", "hacking etico",
        "pentesting", "vulnerabilidades", "fortinet", "palo alto", "checkpoint",
        "backup legacy", "antivirus", "waf"
    ],
    "NETWORKING": [
        "networking", "conmutador", "switch", "enlace de datos", "datacenter",
        "centro de datos", "redes de datos", "cableado estructurado", "sd-wan",
        "enlace de internet", "router", "telecomunicaciones"
    ],
    "IA_AUTOMATIZACION": [
        "inteligencia artificial", "automatizacion", "rpa", "chatbot",
        "machine learning", "asistente virtual", "transformacion digital"
    ],
    "GESTION_DATOS": [
        "gestion de datos", "business intelligence", "analitica", "base de datos",
        "data warehouse", "etl", "big data", "power bi", "gobierno de datos"
    ]
}

def pick_tipo(matched_keywords: List[str]) -> str:
    """Elige el término de búsqueda rápida (objeto SEACE) a partir de las keywords coincidentes."""
    if not matched_keywords:
        return ""
    words = [k for k in matched_keywords if not k.startswith("CUBSO-")]
    if not words:
        return ""
    picked = next((k for k in words if len(k.split()) >= 2), words[0])
    return picked.upper()

class RadarScanner:
    def __init__(self):
        self.client = OECEClient(timeout=8, max_retries=1)
        self.classifier = TxDxClassifier()

    def run_smart_scan(
        self,
        year: Optional[str] = None,
        query: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_pages_per_query: int = 2,
        has_tender: bool = True,
        progress: Optional[Callable] = None,
        max_seconds: float = 180
    ) -> Dict[str, Any]:
        """
        Escáner Inteligente basado en el motor de búsqueda oficial del portal OECE:
        /api/v1/search?format=json con filtros de año, fechas y palabras clave TxDx.
        """
        year = year or str(datetime.now(PERU_TZ).year)
        start_time = time.monotonic()
        timed_out = False
        records_checked = 0
        records_skipped = 0
        record_errors = 0
        queries_done = 0
        errores = 0
        paginas_consultadas = 0
        total_evaluados = 0
        nuevas_oportunidades = 0
        oportunidades_actualizadas = 0
        processed_ocids: Set[str] = set()
        por_prioridad: Dict[str, int] = {"ALTA": 0, "MEDIA": 0, "BAJA": 0, "": 0}

        # Determinar queries a ejecutar
        if query and query.strip():
            queries_to_run = [query.strip()]
            print(f"[RadarScanner] Modo búsqueda puntual para: '{query}' (Año: {year})...")
        else:
            # Lista completa de términos clave de las 4 líneas de servicio TxDx
            queries_to_run = []
            for line_queries in CORE_SEARCH_QUERIES.values():
                queries_to_run.extend(line_queries)
            print(f"[RadarScanner] Iniciando escaneo dirigido: {len(queries_to_run)} términos clave para año {year}...")

        total_queries = len(queries_to_run)

        def report(stage, query_text="", page=0):
            state = dict(stage=stage, query=query_text, page=page,
                         queries_done=queries_done, queries_total=total_queries,
                         pages_done=paginas_consultadas, records_checked=records_checked,
                         records_skipped=records_skipped, errors=errores + record_errors,
                         evaluated=total_evaluados,
                         elapsed_seconds=round(time.monotonic() - start_time, 1))
            if progress:
                progress(state)

        for idx, q in enumerate(queries_to_run, 1):
            if time.monotonic() - start_time >= max_seconds:
                timed_out = True
                break
            for page in range(1, max_pages_per_query + 1):
                if time.monotonic() - start_time >= max_seconds:
                    timed_out = True
                    break
                report("Buscando", q, page)
                print(f"[RadarScanner] {idx}/{total_queries}: {q}, página {page}", flush=True)
                try:
                    paginas_consultadas += 1
                    search_res = self.client.search_processes(
                        query=q,
                        year=year,
                        has_tender=has_tender,
                        update_start=start_date,
                        update_end=end_date,
                        page=page,
                        page_size=20
                    )
                except Exception as e:
                    errores += 1
                    print(f"[RadarScanner] Error consultando query '{q}' pág {page}: {e}")
                    break

                if not search_res or "results" not in search_res:
                    errores += 1
                    break

                results = search_res.get("results", [])
                if not results:
                    break

                for item in results:
                    if time.monotonic() - start_time >= max_seconds:
                        timed_out = True
                        break
                    total_evaluados += 1
                    compiled = item.get("compiledRelease") or item.get("tender") or {}
                    ocid = compiled.get("ocid") or item.get("ocid")

                    if not ocid or ocid in processed_ocids:
                        continue
                    processed_ocids.add(ocid)

                    # Evaluar pertinencia técnica con el clasificador TxDx
                    eval_res = self.classifier.evaluate_release(compiled)
                    if not eval_res.get("matched"):
                        continue

                    # El record completo aporta cronograma y estado, además de documentos.
                    snapshot = compiled.get("tender") or {}
                    end = parse_fecha((snapshot.get("tenderPeriod") or {}).get("endDate"))
                    status, _ = extraer_estado_official(compiled)
                    
                    # Considerar fuera de plazo preliminarmente si el estado oficial ya es cerrado
                    # o si la fecha de convocatoria es manifiestamente antigua (> 45 días)
                    ahora_utc = datetime.now(timezone.utc)
                    es_antiguo = end is not None and (ahora_utc - end).total_seconds() > 45 * 86400
                    outside = (status or "").upper().strip() in ESTADOS_CERRADOS or es_antiguo
                    record_data = None
                    if outside:
                        records_skipped += 1
                    else:
                        report("Revisando expediente", q, page)
                        record_data = self.client.fetch_record_by_ocid(ocid)
                        records_checked += 1
                        if not record_data or not record_data.get("compiledRelease"):
                            record_errors += 1
                    if record_data and record_data.get("compiledRelease"):
                        compiled = record_data["compiledRelease"]
                        eval_res = self.classifier.evaluate_release(compiled)
                        if not eval_res.get("matched"):
                            continue
                    tender = compiled.get("tender") or {}
                    buyer = compiled.get("buyer") or tender.get("procuringEntity") or {}
                    url_bases = ""
                    docs = tender.get("documents") or []
                    # Preferir bases integradas cuando el portal las publica.
                    docs = sorted(docs, key=lambda d: "integrada" in (d.get("title") or "").lower(), reverse=True)
                    for doc in docs:
                        doc_type = (doc.get("documentType") or "").lower()
                        title = (doc.get("title") or "").lower()
                        if doc.get("url") and ("base" in title or "biddingdocuments" in doc_type or "terminos" in title or "términos" in title):
                            url_bases = doc.get("url", "")
                            break

                    # Ubicación / Departamento
                    departamento = "Lima"
                    address = buyer.get("address") or {}
                    if address.get("region"):
                        departamento = address.get("region")

                    # Monto referencial
                    monto = 0.0
                    val = tender.get("value") or {}
                    if val.get("amount"):
                        try:
                            monto = float(val.get("amount"))
                        except (ValueError, TypeError):
                            monto = 0.0

                    status_details, _ = extraer_estado_official(compiled)
                    fecha_pub = tender.get("datePublished")

                    op_title = tender.get("title") or ocid
                    op_desc = tender.get("description", "") or op_title

                    tender_period = tender.get("tenderPeriod") or {}
                    raw_cierre = tender_period.get("endDate")
                    enquiry_end = (tender.get("enquiryPeriod") or {}).get("endDate")

                    cierre_dt = parse_fecha(raw_cierre)
                    pub_dt = parse_fecha(fecha_pub)
                    enquiry_dt = parse_fecha(enquiry_end)

                    # En OECE OCDS, tenderPeriod.endDate suele exportarse con la fecha de convocatoria
                    # a las 00:00:00 (mismo día de publicación +/- 1 día).
                    # Si el proceso sigue abierto, calcular el cierre de propuestas efectivo:
                    es_ph = bool(
                        pub_dt and cierre_dt and abs((pub_dt.date() - cierre_dt.date()).days) <= 1
                        and cierre_dt.hour == 0 and cierre_dt.minute == 0
                    )
                    if es_ph and (status_details or "").upper().strip() not in ESTADOS_CERRADOS:
                        if enquiry_dt and enquiry_dt > datetime.now(timezone.utc):
                            fecha_cierre_prop = (enquiry_dt + timedelta(days=5)).isoformat()
                        elif pub_dt:
                            fecha_cierre_prop = (pub_dt + timedelta(days=25)).isoformat()
                        else:
                            fecha_cierre_prop = raw_cierre
                    else:
                        fecha_cierre_prop = raw_cierre

                    opportunity_payload = {
                        "ocid": ocid,
                        "tender_id": tender.get("id") or ocid,
                        "titulo": op_title,
                        "descripcion": op_desc,
                        "entidad": buyer.get("name") or "Entidad Pública",
                        "entidad_ruc": buyer.get("id", ""),
                        "departamento": departamento,
                        "monto_referencial": monto,
                        "moneda": val.get("currency", "PEN"),
                        "linea_servicio": eval_res["linea_servicio"],
                        "score": eval_res["score"],
                        "matched_keywords": eval_res["matched_keywords"],
                        "matched_products": eval_res["matched_products"],
                        "fecha_publicacion": fecha_pub,
                        "fecha_cierre_propuestas": fecha_cierre_prop,
                        "fecha_cierre_consultas": enquiry_end,
                        "tipo_proceso": tender.get("procurementMethodDetails") or tender.get("mainProcurementCategory", "Proceso General"),
                        "metodo": tender.get("procurementMethod", ""),
                        "url_bases": url_bases,
                        "url_oece": f"https://contratacionesabiertas.oece.gob.pe/proceso/{ocid}",
                        "tipo": pick_tipo(eval_res["matched_keywords"]),
                        "estado_ocds": status_details,
                    }

                    # Priorización metodológica TxDx
                    prio = evaluar_prioridad(opportunity_payload)
                    opportunity_payload["prioridad"] = prio["prioridad"]
                    opportunity_payload["nivel_encaje"] = prio["nivel_encaje"]
                    opportunity_payload["ventana"] = prio["ventana"]
                    opportunity_payload["etapa"] = prio["etapa"]
                    por_prioridad[prio["prioridad"]] = por_prioridad.get(prio["prioridad"], 0) + 1

                    is_new = upsert_oportunidad(opportunity_payload)
                    if is_new:
                        nuevas_oportunidades += 1
                        print(f" [NUEVA] [{prio['prioridad']}] {opportunity_payload['linea_servicio']} ({opportunity_payload['score']}%) | {opportunity_payload['titulo']} | S/{opportunity_payload['monto_referencial']}")
                    else:
                        oportunidades_actualizadas += 1

                # Pausa ligera de cortesía con el servidor OECE
                report("Página revisada", q, page)
                if timed_out or ("next" in search_res and not search_res["next"]):
                    break
                time.sleep(0.2)
            if timed_out:
                break
            queries_done += 1
            report("Término revisado", q)

        duracion = round(time.monotonic() - start_time, 2)
        status = "PARTIAL" if errores or record_errors or timed_out else "SUCCESS"
        report("Finalizado")
        summary = {
            "paginas_escaneadas": paginas_consultadas,
            "errores_busqueda": errores,
            "status": status,
            "limite_tiempo": timed_out,
            "terminos_completados": queries_done,
            "terminos_total": total_queries,
            "expedientes_omitidos": records_skipped,
            "errores_expedientes": record_errors,
            "total_releases_evaluados": total_evaluados,
            "nuevas_oportunidades": nuevas_oportunidades,
            "oportunidades_actualizadas": oportunidades_actualizadas,
            "por_prioridad": por_prioridad,
            "duracion_segundos": duracion,
            "timestamp": datetime.now().isoformat()
        }

        record_scan_history(
            paginas=paginas_consultadas,
            total_evaluados=total_evaluados,
            nuevas_oportunidades=nuevas_oportunidades,
            duracion=duracion,
            status=status,
            detalle=(f"Nuevas: {nuevas_oportunidades}, Actualizadas: {oportunidades_actualizadas}, "
                     f"Por prioridad: {por_prioridad}")
        )

        print(f"[RadarScanner] Finalizado en {duracion}s. Evaluados: {total_evaluados}. Nuevas: {nuevas_oportunidades}.")
        return summary

    def run_scan(self, max_pages: int = 25, start_page: int = 1, year: Optional[str] = None) -> Dict[str, Any]:
        """
        Ejecuta el escáner inteligente por defecto aprovechando los filtros y motor de búsqueda de OECE.
        """
        return self.run_smart_scan(year=year, max_pages_per_query=max(1, max_pages // 15))
