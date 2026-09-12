"""Priorización de oportunidades según la metodología TxDx.

Criterios (alineados con el prompt de operación):
1. Encaje comercial: score del clasificador + productos detectados + prefijos CUBSO.
    - FUERTE    : score >= 70 y al menos 1 producto; o score >= 85 sin producto.
    - PROBABLE  : score >= 50 (o producto detectado con score >= 40).
    - AMBIGUO   : resto que haya pasado el filtro mínimo.
2. Vigencia: estado oficial CERRADO descarta automáticamente.
3. Ventana: URGENTE (<2d), ACCIONABLE (2-5d), SEGUIMIENTO (>5d), FUERA_DE_VENTANA.
4. Cobertura: Lima/Callao directo; provincia -> "requiere cobertura en provincia".
5. Garantía: si el análisis de bases reporta carta fianza >= S/1M -> BARRERA EXCLUYENTE.
   Si no hay análisis -> "garantía por verificar" (NO excluye por defecto).
"""

from typing import Dict, Any, List

from engine.estado import parse_dates_op


def _nivel_encaje(score: int, productos: List[str], keywords: List[str]) -> Dict[str, Any]:
    cusbos = [k for k in (keywords or []) if str(k).startswith("CUBSO-")]
    if score >= 70 and ((productos or cusbos) or score >= 85):
        return {"nivel": "FUERTE", "motivo": f"Encaje comercial fuerte: score {score}%", "con_cusbo": bool(cusbos)}
    if score >= 50 or (productos and score >= 40):
        return {"nivel": "PROBABLE", "motivo": f"Encaje probable: score {score}%. Requiere revisar bases", "con_cusbo": bool(cusbos)}
    return {"nivel": "AMBIGUO", "motivo": f"Coincidencia superficial (score {score}%). Verificar objeto completo", "con_cusbo": bool(cusbos)}


def _garantia_op(d: Dict[str, Any]) -> Dict[str, Any]:
    """Garantía desde el análisis de bases; sin análisis -> por verificar (no excluye)."""
    import json
    analisis = d.get("analisis_bases")
    if isinstance(analisis, str):
        try:
            analisis = json.loads(analisis) or {}
        except (ValueError, TypeError):
            analisis = {}
    analisis = analisis or {}
    garantia = (analisis or {}).get("garantia") or ""
    if not garantia or "No especificada" in garantia:
        return {"estado": "por_verificar", "texto": "garantía por verificar", "barrera": False}
    import re
    montos = [float(n.replace(".", "").replace(",", ".")) for n in re.findall(r'[\d][\d.,]*', garantia)]
    monto_max = max(montos) if montos else 0
    if monto_max >= 1_000_000:
        return {"estado": "barrera", "texto": f"Carta fianza {garantia}", "barrera": True}
    return {"estado": "ok", "texto": f"Carta fianza {garantia}", "barrera": False}


def evaluar(op: Dict[str, Any]) -> Dict[str, Any]:
    """Evalúa una oportunidad y devuelve dict completo de priorización."""
    info = parse_dates_op(op)
    encaje = _nivel_encaje(int(op.get("score") or 0), op.get("matched_products") or [], op.get("matched_keywords") or [])
    garantia = _garantia_op(op)
    cobertura = info["cobertura"]

    motivos: List[str] = [encaje["motivo"]]
    if cobertura["requiere_provincia"]:
        motivos.append("Servicio en provincia: requiere cobertura en provincia")

    cerrado = info["estado"] == "CERRADO"
    if info["es_fecha_corrupta"]:
        motivos.append("Fechas incoherentes en portal: publicación posterior al cierre")

    if info["ventana"] in ("URGENTE",):
        motivos.append(f"Vence en {info['dias_restantes']} días: urgente/fuera de ventana preferida")
    elif info["ventana"] == "ACCIONABLE":
        motivos.append(f"Plazo accionable: {info['dias_restantes']} días")
    elif info["ventana"] == "SEGUIMIENTO":
        motivos.append(f"Plazo amplio ({info['dias_restantes']} días) — seguimiento")
    else:
        motivos.append("Sin fecha límite futura visible en OCDS: fuera de ventana")

    # Peso comercial de la línea de servicio (mismo catálogo que el clasificador)
    peso_linea = {
        "NETWORKING": 3, "CIBERSEGURIDAD": 3, "IA_AUTOMATIZACION": 2, "GESTION_DATOS": 2,
    }.get(op.get("linea_servicio"), 1)

    # Regla de decisión de prioridad según metodología comercial TxDx
    if cerrado:
        prioridad = "BAJA"; motivo_prio = "Proceso cerrado según portal"
    elif not info["es_elegible"]:
        prioridad = "BAJA"; motivo_prio = info["motivo_vigencia"]
    elif garantia["barrera"]:
        prioridad = "BAJA"; motivo_prio = "Barrera excluyente: " + garantia["texto"]
    elif encaje["nivel"] == "FUERTE" and peso_linea >= 3:
        prioridad = "ALTA"; motivo_prio = "Encaje fuerte + línea estratégica TxDx"
    elif encaje["nivel"] == "FUERTE":
        prioridad = "ALTA"; motivo_prio = "Encaje fuerte con servicios TxDx"
    elif encaje["nivel"] == "PROBABLE" and info["ventana"] in ("ACCIONABLE", "SEGUIMIENTO", "URGENTE"):
        prioridad = "ALTA"; motivo_prio = "Encaje probable y plazo de participación activo"
    elif encaje["nivel"] == "PROBABLE":
        prioridad = "MEDIA"; motivo_prio = "Encaje probable; pendiente revisar bases"
    elif not cerrado and info["estado"] in ("CONVOCADO", "CONSULTAS"):
        prioridad = "MEDIA"; motivo_prio = "Convocatoria vigente en SEACE"
    else:
        prioridad = "BAJA"; motivo_prio = "Encaje ambiguo o proceso sin vigencia"

    return {
        "es_elegible": info["es_elegible"],
        "horas_restantes": info["horas_restantes"],
        "motivo_vigencia": info["motivo_vigencia"],
        "prioridad": prioridad,
        "nivel_encaje": encaje["nivel"],
        "ventana": info["ventana"],
        "dias_restantes": info["dias_restantes"],
        "estado": info["estado"],
        "etapa": info["etapa"],
        "garantia": garantia["texto"],
        "garantia_barrera": garantia["barrera"],
        "requiere_provincia": cobertura["requiere_provincia"],
        "motivos": motivos,
        "motivo_prioridad": motivo_prio,
    }