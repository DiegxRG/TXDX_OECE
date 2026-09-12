import sys
sys.path.insert(0, '.')
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from engine.estado import parse_fecha, PERU_TZ, ESTADOS_CERRADOS, parse_dates_op
from engine.prioridad import evaluar

con = sqlite3.connect('radar_txdx.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute('SELECT * FROM oportunidades').fetchall()
print(f"Total oportunidades a evaluar: {len(rows)}")

ahora = datetime.now(timezone.utc)
actualizados = 0

for r in rows:
    pub = parse_fecha(r['fecha_publicacion'])
    consultas = parse_fecha(r['fecha_cierre_consultas'])
    cierre_actual = parse_fecha(r['fecha_cierre_propuestas'])
    status = (r['estado_ocds'] or '').strip().upper()

    es_ph = bool(
        pub and cierre_actual
        and abs((pub.date() - cierre_actual.date()).days) <= 1
        and cierre_actual.hour == 0 and cierre_actual.minute == 0
    )

    nuevo_cierre_str = r['fecha_cierre_propuestas']
    if es_ph and status not in ESTADOS_CERRADOS:
        if consultas and consultas > ahora:
            nuevo_cierre_str = (consultas + timedelta(days=5)).isoformat()
        elif pub:
            nuevo_cierre_str = (pub + timedelta(days=25)).isoformat()

    row_dict = dict(r)
    row_dict['fecha_cierre_propuestas'] = nuevo_cierre_str
    row_dict['matched_keywords'] = json.loads(row_dict.get('matched_keywords') or '[]')
    row_dict['matched_products'] = json.loads(row_dict.get('matched_products') or '[]')
    
    prio_res = evaluar(row_dict)

    cur.execute('''
        UPDATE oportunidades
        SET fecha_cierre_propuestas = ?,
            etapa = ?,
            ventana = ?,
            prioridad = ?,
            nivel_encaje = ?
        WHERE ocid = ?
    ''', (
        nuevo_cierre_str,
        prio_res['etapa'],
        prio_res['ventana'],
        prio_res['prioridad'],
        prio_res['nivel_encaje'],
        r['ocid']
    ))
    actualizados += 1

con.commit()
print(f"Actualización completada: {actualizados} registros actualizados.")

# Conteo de vigentes
cur.execute("SELECT count(*) FROM oportunidades WHERE ventana IN ('ACCIONABLE', 'SEGUIMIENTO')")
vigentes = cur.fetchone()[0]
print(f"Oportunidades con ventana ACCIONABLE o SEGUIMIENTO (>= 48h): {vigentes}")
