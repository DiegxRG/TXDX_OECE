"""Estado, etapas y ventanas de tiempo según datos oficiales del OECE/SEACE.

El portal OCDS no publica un campo único `status` de convocatoria; el estado real
vive en `tender.items[].statusDetails` (CONVOCADO / CONTRATADO / DESIERTO / NULO...).
Las fechas accionables por el postor son `tender.enquiryPeriod` (consultas) y
`tender.tenderPeriod` (registro/presentación en SEACE). Todo se evalúa en hora Perú.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

# Estados que el portal marca cuando el proceso ya NO admite participación
ESTADOS_CERRADOS = {
    "CONTRATADO", "ADJUDICADO", "DESIERTO", "NULO", "CONSENTIDO",
    "APELADO", "CULMINADO", "CONCLUIDO", "ANULADO", "DESCALIFICADO", "CANCELADO",
}

ESTADOS_ABIERTOS = {
    "CONVOCADO", "CONCURSO", "CONVOCATORIA", "REGISTRO DE PARTICIPANTES",
    "CONSULTAS", "OFERTAS", "PRESENTACION DE OFERTAS", "EN CONSULTAS",
}

PERU_TZ = timezone(timedelta(hours=-5))
MIN_PLAZO_HORAS = 48


def parse_fecha(s: Optional[str]) -> Optional[datetime]:
    """Convierte ISO (con/sin tz) a datetime UTC-aware. None si no parsea."""
    if not s:
        return None
    try:
        c = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if c.tzinfo is None:
            c = c.replace(tzinfo=PERU_TZ)
        return c
    except (ValueError, TypeError):
        return None


def es_fecha_corrupta(pub: Optional[datetime], cierre: Optional[datetime]) -> bool:
    """Cierre anterior a la publicación por más de 1 día y año anterior = dato incoherente del portal."""
    if pub is None or cierre is None:
        return False
    diff = (pub - cierre).total_seconds()
    return diff > 86400 and cierre.year < pub.year


def extraer_estado_official(release: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Estado oficial desde items[].statusDetails + estado derivado de etapas."""
    items = (release.get("tender", {}) or {}).get("items", []) or []
    detalles = [i.get("statusDetails") for i in items if i.get("statusDetails")]
    # Si hay varios, quedarse con el primer estado "no cerrado" si existe
    detalle = detalles[0] if detalles else None
    return detalle, (detalle or "CONVOCADO")


def compute_estado(
    status_details: Optional[str],
    enquiry_end: Optional[datetime],
    tender_end: Optional[datetime],
    ahora: datetime,
) -> str:
    """El cierre prevalece sobre la etiqueta de convocatoria."""
    s = (status_details or "").upper().strip()
    if s in ESTADOS_CERRADOS or (tender_end and tender_end <= ahora):
        return "CERRADO"
    if tender_end is None:
        return "POR_VERIFICAR"
    if enquiry_end and enquiry_end > ahora:
        return "CONSULTAS"
    return "CONVOCADO"


def etapa_texto(estado: str, enquiry_end: Optional[datetime], tender_end: Optional[datetime], ahora: datetime) -> str:
    if estado == "CERRADO":
        return "Proceso cerrado según portal"
    if estado == "CONSULTAS":
        return f"Consultas abiertas hasta {enquiry_end.astimezone(PERU_TZ).strftime('%d/%m/%Y %H:%M')} (hora Perú)"
    if estado == "CONVOCADO":
        if tender_end and tender_end > ahora:
            return f"Convocado / fase de registro en SEACE (Tender hasta {tender_end.strftime('%d/%m/%Y')})"
        if enquiry_end and enquiry_end > ahora:
            return "Convocado / fase de consultas"
        return "Convocado — fechas de registro por verificar en SEACE"
    return "Estado oficial por verificar (portal no publica etapa)"


def dias_restantes_accionable(
    enquiry_end: Optional[datetime],
    tender_end: Optional[datetime],
    ahora: datetime,
) -> Optional[int]:
    """Días completos hasta propuestas; consultas no sustituyen ese cierre."""
    if tender_end is None:
        return None
    return int((tender_end - ahora).total_seconds() // 86400)


def ventana_por_dias(dias: Optional[int]) -> str:
    """Clasificación de ventana según el prompt TxDx: <2 urgente, 2-5 accionable, >5 seguimiento."""
    if dias is None:
        return "FUERA_DE_VENTANA"
    if dias < 0:
        return "FUERA_DE_VENTANA"
    if dias < 2:
        return "URGENTE"
    if dias <= 5:
        return "ACCIONABLE"
    return "SEGUIMIENTO"


def cobertura_txdx(departamento: str) -> Dict[str, Any]:
    """Modalidad/cobertura: Lima y Callao cubre TxDx directamente; provincia requiere cobertura."""
    dep = (departamento or "").upper()
    if "LIMA" in dep or "CALLAO" in dep:
        return {"cobertura": "Lima/Callao", "requiere_provincia": False}
    return {"cobertura": "Provincia", "requiere_provincia": True}


def parse_dates_op(d: Dict[str, Any], ahora: Optional[datetime] = None) -> Dict[str, Any]:
    """Enriquece el dict de una oportunidad con fechas parseadas y flags."""
    pub = parse_fecha(d.get("fecha_publicacion") or d.get("fecha_exposicion"))
    tender_end = parse_fecha(d.get("fecha_cierre_propuestas"))
    consultas = parse_fecha(d.get("fecha_cierre_consultas"))
    ahora = ahora or datetime.now(timezone.utc)
    status_raw = (d.get("estado_ocds") or "").upper().strip()

    # En OECE OCDS, tenderPeriod.endDate frecuentemente se exporta con la fecha de convocatoria
    # a las 00:00:00-05:00 (mismo día de publicación +/- 1 día).
    # Esto es un metadato de inicio de convocatoria en OECE, NO el fin de presentación de propuestas.
    es_placeholder = bool(
        pub and tender_end
        and abs((pub.date() - tender_end.date()).days) <= 1
        and tender_end.hour == 0 and tender_end.minute == 0
    )

    # Determinar la fecha límite de participación según datos oficiales del portal
    cierre_efectivo = tender_end
    if es_placeholder and status_raw not in ESTADOS_CERRADOS:
        if consultas:
            # En OECE OCDS, la fecha oficial de finalización de consultas es el hito determinante
            cierre_efectivo = consultas
        elif pub and (ahora - pub).days <= 5:
            cierre_efectivo = pub + timedelta(days=7)
        else:
            cierre_efectivo = tender_end

    estado = compute_estado(d.get("estado_ocds"), consultas, cierre_efectivo, ahora)
    dias = dias_restantes_accionable(consultas, cierre_efectivo, ahora)
    
    # Incoherente si cierre es anterior a publicación (sin ser placeholder) o publicación en el futuro
    corrupta = bool(
        (pub and tender_end and not es_placeholder and tender_end < pub)
        or (pub and pub > ahora + timedelta(days=1))
    )

    horas = (cierre_efectivo - ahora).total_seconds() / 3600 if cierre_efectivo else None
    elegible = estado != "CERRADO" and not corrupta and horas is not None and horas >= MIN_PLAZO_HORAS

    if corrupta:
        motivo = "Fechas incoherentes: cierre anterior a publicación"
    elif estado == "CERRADO":
        motivo = f"Proceso cerrado o plazo de propuestas vencido ({d.get('estado_ocds') or 'CERRADO'})"
    elif horas is None:
        motivo = "Sin cierre de propuestas verificable"
    elif horas < MIN_PLAZO_HORAS:
        motivo = "Quedan menos de 48 horas para presentar propuestas"
    else:
        dias_txt = f"{dias} días restantes" if dias is not None else ""
        motivo = f"Plazo disponible de al menos 48 horas ({dias_txt})".strip()

    return {
        "es_elegible": elegible,
        "horas_restantes": horas,
        "motivo_vigencia": motivo,
        "fecha_pub": pub,
        "fecha_cierre": cierre_efectivo,
        "fecha_consultas": consultas,
        "estado": estado,
        "etapa": etapa_texto(estado, consultas, cierre_efectivo, ahora),
        "dias_restantes": dias,
        "ventana": "FUERA_DE_VENTANA" if estado == "CERRADO" or corrupta else ventana_por_dias(dias),
        "es_fecha_corrupta": corrupta,
        "cobertura": cobertura_txdx(d.get("departamento", "Lima")),
    }