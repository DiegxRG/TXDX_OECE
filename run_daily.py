"""Ejecución programada del radar (mismo protocolo que el prompt de operación TxDx).

Paso 1: scan del OECE (OCDS) con filtros predeterminados (pages por defecto).
Paso 2: regenerar memoria de oportunidades (deduplicación histórica).
Paso 3: generar reporte diario priorizado.

Uso:
    python run_daily.py --pages 25
"""

import argparse
import sys

from engine.scanner import RadarScanner
from engine.reporte import generar_memoria_y_reporte


def main():
    parser = argparse.ArgumentParser(description="Ejecución programada del radar TxDx")
    parser.add_argument("--pages", type=int, default=25, help="Páginas de releases OECE a escanear")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--skip-scan", action="store_true", help="Solo regenerar memoria y reporte")
    args = parser.parse_args()

    if not args.skip_scan:
        scanner = RadarScanner()
        resumen = scanner.run_scan(max_pages=args.pages, start_page=args.start_page)
        print(f"[run_daily] -> {resumen['nuevas_oportunidades']} nuevas, "
              f"{resumen['oportunidades_actualizadas']} actualizadas "
              f"({resumen['duracion_segundos']}s)")
        if resumen.get("por_prioridad"):
            print(f"[run_daily] prioridades: {resumen['por_prioridad']}")

    artefactos = generar_memoria_y_reporte()
    print(f"[run_daily] Memoria : {artefactos['memoria']}")
    print(f"[run_daily] Reporte: {artefactos['reporte']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(1)