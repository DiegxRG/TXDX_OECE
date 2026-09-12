from db.database import list_oportunidades
from engine.pdf_analyzer import BasesAnalyzer

ops = list_oportunidades(limit=5)
target = None
for op in ops:
    if op.get("url_bases"):
        target = op
        break

if target:
    print(f"Analizando: {target['titulo']} - Entidad: {target['entidad']}")
    print(f"URL Bases: {target['url_bases']}")
    analyzer = BasesAnalyzer()
    res = analyzer.analyze_document(target["ocid"], target["url_bases"], meta=target)
    print("\n--- RESULTADO DE EXTRACCIÓN REAL ---")
    print("Success:", res.get("success"))
    print("Total Páginas:", res.get("total_paginas"))
    print("Experiencia del Postor:", res.get("experiencia_postor"))
    print("Personal Clave:", res.get("personal_clave"))
    print("Certificaciones Detectadas:", res.get("certificaciones_requeridas"))
    print("Plazo:", res.get("plazo_ejecucion"))
    print("Modalidad:", res.get("modalidad"))
    print("Factibilidad TxDx:", res.get("factibilidad_txdx"))
else:
    print("No se encontró oportunidad con link de bases en DB.")
