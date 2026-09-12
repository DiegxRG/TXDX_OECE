"""Memoria de oportunidades y reporte diario según metodología TxDx.

Memoria (oportunidades-seace.md): historial mínimo por OCID — detectada, revisada,
estado oficial y resultado. Solo sirve para deduplicación y continuidad, no reemplaza la DB.

Reporte (reporte_diario_YYYYMMDD.md): tabla priorizada con prioridad, encaje, ventana,
fechas accionables, garantía y estado de validación de enlaces.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from db.database import list_oportunidades, get_connection

MEMORIA_DIR = Path(__file__).resolve().parent.parent / "memoria"
MEMORIA_FILE = MEMORIA_DIR / "oportunidades-seace.md"


def _fmt(dt_str) -> str:
    d = datetime.fromisoformat(dt_str.replace("Z", "+00:00")) if dt_str else None
    return d.strftime("%Y-%m-%d") if d else "-"


def _hoy() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def generar_memoria() -> Path:
    """Regenera oportunidades-seace.md con una línea por OCID."""
    MEMORIA_DIR.mkdir(exist_ok=True)
    with get_connection() as conn:
        rows = conn.execute("""
        SELECT ocid,
               COALESCE(fecha_detectada, created_at, '') AS detectada,
               COALESCE(fecha_revisada, updated_at, '') AS revisada,
               estado_ocds, prioridad, estado_interno
        FROM oportunidades ORDER BY fecha_detectada DESC;
        """).fetchall()

    lines = [
        "# Memoria de Oportunidades TxDx (OECE/SEACE)",
        "",
        "Historial mínimo para deduplicación y continuidad. OCID =(clave), detectada, revisada, estado oficial, prioridad, resultado interno.",
        "",
        "| OCID | Detectada | Revisada | Estado oficial | Prioridad | Resultado interno |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['ocid']} | {_fmt(r['detectada'])} | {_fmt(r['revisada'])} | "
            f"{r['estado_ocds'] or '-'} | {r['prioridad'] or '-'} | {r['estado_interno'] or '-'} |"
        )
    lines.append("")
    MEMORIA_FILE.write_text("\n".join(lines), encoding="utf-8")
    return MEMORIA_FILE


def _enlace_estado(op: Dict[str, Any]) -> str:
    """Etiqueta el enlace: patrón del Portal OECE validado; API/sin probar = por verificar."""
    url = op.get("url_oece") or ""
    if "contratacionesabiertas.oece.gob.pe/proceso/" in url:
        return f"[validado]({url})"
    if url:
        return f"[por verificar]({url})"
    return "sin enlace directo"


def _escoger_garantia(op: Dict[str, Any]) -> str:
    g = (op.get("garantia") or "")
    if op.get("garantia_barrera"):
        return f"**{g}**"
    return g or "por verificar"


def generar_reporte(solo_vigentes: bool = True) -> Path:
    """Reporte diario priorizado: Alta > Media > Baja; luego menor plazo; luego mayor monto."""
    ops = list_oportunidades(limit=None, solo_vigentes=solo_vigentes, max_monto=None if not solo_vigentes else 1_000_000)
    orden = {"ALTA": 0, "MEDIA": 1, "BAJA": 2, "": 3}
    ops.sort(key=lambda o: (orden.get(o.get("prioridad"), 3), o.get("dias_restantes") if o.get("dias_restantes") is not None else 999, -(o.get("monto_referencial") or 0)))

    hoy = _hoy()
    MEMORIA_DIR.mkdir(exist_ok=True)
    out = MEMORIA_DIR / f"reporte_diario_{hoy}.md"

    nuevas = [o for o in ops if _fmt(o.get("fecha_detectada")) == hoy]
    actualizadas = [o for o in ops if _fmt(o.get("fecha_revisada")) == hoy and o not in nuevas]
    vigentes = [o for o in ops if o.get("prioridad") in ("ALTA", "MEDIA")]

    rows = [
        f"# Reporte Diario TxDx — {hoy}",
        "",
        f"**Resumen:** {len(nuevas)} nuevas · {len(actualizadas)} actualizadas · "
        f"{len([o for o in ops])} en cartera · {len(vigentes)} prioridad Alta/Media",
        "",
        "Fuentes: Portal Contrataciones Abiertas OECE (OCDS) · verificación SEACE por proceso. Hora Perú.",
        "",
    ]

    rows.append("## Prioridad Alta / Media")
    rows.append("")
    rows.append("| Prio | Encaje | Entidad / Objeto | Línea TxDx | OCID | Monto | Región·Modalidad | Estado | Cierre propuestas | Días | Garantía | Riesgo / pendiente | Enlace |")
    rows.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for o in vigentes:
        cuid = o.get("tender_id") or ""
        motivo = (o.get("motivo_prioridad") or "")[:60]
        rows.append(
            f"| **{o['prioridad']}** | {o.get('nivel_encaje', '-')} | "
            f"{o.get('entidad', '-')[:28]} · {o.get('titulo', '-')[:40]} | "
            f"{o.get('linea_servicio', '-')} | `{o.get('ocid', '')}` | "
            f"S/{o.get('monto_referencial') or 0:,.0f} | {o.get('departamento', '-')}/{(o.get('requiere_provincia') and 'provincia' or 'Lima/Callao')} | "
            f"{o.get('estado_ocds') or o.get('etapa', '-')} | {_fmt(o.get('fecha_cierre_propuestas'))} | "
            f"{o.get('dias_restantes') if o.get('dias_restantes') is not None else '-'} | {_escoger_garantia(o)} | "
            f"{motivo} | {_enlace_estado(o)} |"
        )
    rows.append("")

    urgentes = [o for o in ops if o.get("ventana") == "URGENTE"]
    if urgentes:
        rows.append("## Urgentes / fuera de ventana (cierre < 48 horas)")
        rows.append("")
        rows.append("| Prio | Entidad / Objeto | Días | Enlace |")
        rows.append("|---|---|---|---|")
        for o in urgentes:
            rows.append(f"| **{o.get('prioridad', '-')}** | {o.get('entidad', '-')[:20]} · {o.get('titulo', '-')[:45]} | {o.get('dias_restantes')} | {_enlace_estado(o)} |")
        rows.append("")

    seguimiento = [o for o in ops if o.get("ventana") == "SEGUIMIENTO" and (o.get("monto_referencial") or 0) > 0]
    if seguimiento:
        rows.append("## Seguimiento (plazo > 5 días, monto conocido)")
        rows.append("")
        rows.append("| Entidad / Objeto | Línea | Monto | Cierre propuestas | Días | Enlace |")
        rows.append("|---|---|---|---|---|---|")
        for o in seguimiento[:12]:
            rows.append(f"| {o.get('entidad', '-')[:20]} · {o.get('titulo', '-')[:40]} | {o.get('linea_servicio', '-')} | S/{o.get('monto_referencial') or 0:,.0f} | {_fmt(o.get('fecha_cierre_propuestas'))} | {o.get('dias_restantes')} | {_enlace_estado(o)} |")
        rows.append("")

    bajas = [o for o in ops if o.get("prioridad") == "BAJA" and not o.get("garantia_barrera")]
    rows.append(f"## Descartadas / baja prioridad ({len(bajas)})")
    rows.append("")
    rows.append("Motivos frecuentes: encaje ambiguo, proceso cerrado en portal, sin fecha límite visible.")
    for o in bajas[:12]:
        rows.append(f"- `{o.get('ocid', '')}` · {o.get('entidad', '-')[:25]} · {o.get('titulo', '')[:45]} — {o.get('motivo_prioridad', '')[:70]}")
    rows.append("")

    if not ops:
        rows.append("## Sin coincidencias verificables")
        rows.append("Se revisaron releases recientes del OECE (OCDS). Líneas cubiertas: CIBERSEGURIDAD, NETWORKING, IA_AUTOMATIZACION, GESTION_DATOS.")

    out.write_text("\n".join(rows), encoding="utf-8")
    return out


def generar_memoria_y_reporte() -> Dict[str, Any]:
    memo = generar_memoria()
    rep = generar_reporte(solo_vigentes=True)
    return {"memoria": str(memo), "reporte": str(rep)}