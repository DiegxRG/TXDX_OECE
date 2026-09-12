import sys
sys.path.insert(0, '.')
from db.database import list_oportunidades, get_stats

stats = get_stats()
print("Stats total_oportunidades:", stats["total_oportunidades"])
print("Stats por_linea:", stats["por_linea"])
print("Stats por_prioridad:", stats["por_prioridad"])
print("Stats pipeline PEN:", f"S/ {stats['pipeline_monto_pen']:,.2f}")

ops = list_oportunidades(solo_vigentes=True)
print(f"\nTotal oportunidades vigentes (>= 48h): {len(ops)}")
for o in ops[:10]:
    print(f"  - [{o['linea_servicio']}] {o['titulo']} | Ventana: {o['ventana']} | {o['etapa']} | Días restantes: {o['dias_restantes']}")
