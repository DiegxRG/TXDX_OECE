"""
Script CLI para ejecutar el escáner del Radar TxDx
Uso:
  python scan_cli.py --year 2026
  python scan_cli.py --query ciberseguridad --year 2026
  python scan_cli.py --start-date 2026-01-01 --end-date 2026-09-11
"""
import argparse
import sys
from engine.scanner import RadarScanner
from db.database import list_oportunidades, get_stats

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="Radar TxDx - Escáner Inteligente de Contrataciones OECE")
    parser.add_argument("--year", type=str, default=None, help="Año de convocatoria OECE (default: año actual de Perú)")
    parser.add_argument("--query", type=str, default=None, help="Término o palabra clave puntual (ej: ciberseguridad, firewall, soc)")
    parser.add_argument("--start-date", type=str, default=None, help="Límite inicial de fecha de actualización (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="Límite final de fecha de actualización (YYYY-MM-DD)")
    parser.add_argument("--pages", type=int, default=2, help="Páginas a consultar por término (default: 2)")
    args = parser.parse_args()

    print("=" * 75)
    print("[+] RADAR DE CONTRATACIONES PÚBLICAS TXDX (OECE / SEACE v3)")
    print(f" * Año de búsqueda: {args.year}")
    if args.query:
        print(f" * Consulta puntual: '{args.query}'")
    if args.start_date or args.end_date:
        print(f" * Límites de fecha: {args.start_date or 'inicio'} hasta {args.end_date or 'actual'}")
    print("=" * 75)

    scanner = RadarScanner()
    summary = scanner.run_smart_scan(
        year=args.year,
        query=args.query,
        start_date=args.start_date,
        end_date=args.end_date,
        max_pages_per_query=args.pages
    )

    stats = get_stats()
    print("\n" + "=" * 75)
    print("[*] ESTADÍSTICAS GLOBALES DEL RADAR:")
    print(f" * Total de oportunidades en cartera: {stats['total_oportunidades']}")
    print(f" * Por línea de servicio: {stats['por_linea']}")
    print(f" * Pipeline económico estimado: S/ {stats['pipeline_monto_pen']:,.2f}")
    print("=" * 75)

    oportunidades = list_oportunidades(limit=15)
    print(f"\n[>] ÚLTIMAS OPORTUNIDADES DETECTADAS ({len(oportunidades)}):")
    for op in oportunidades:
        bases_info = "[Bases PDF ✓]" if op.get("url_bases") else "[Sin link bases]"
        print(f"\n[{op['linea_servicio']}] {op['descripcion'] or op['titulo']} (Match: {op['score']}%)")
        print(f"   Código: {op['titulo']} | Prioridad: {op.get('prioridad', 'N/A')}")
        print(f"   Entidad: {op['entidad']} ({op['departamento']})")
        print(f"   Monto: S/ {op['monto_referencial']:,.2f} | {bases_info}")
        print(f"   Keywords: {', '.join(op.get('matched_keywords', []))}")
        print(f"   Productos TxDx recomendados: {', '.join(op.get('matched_products', [])[:2])}")
        if op.get("url_bases"):
            print(f"   Link Bases: {op['url_bases']}")

if __name__ == "__main__":
    main()
