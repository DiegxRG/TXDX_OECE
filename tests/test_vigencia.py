import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from engine.estado import parse_dates_op, parse_fecha
from engine.prioridad import evaluar
from db import database as db

NOW = datetime(2026, 9, 12, 15, tzinfo=timezone.utc)


def op(hours=72, **extra):
    data = dict(ocid="test", titulo="Firewall", linea_servicio="CIBERSEGURIDAD",
                score=90, matched_products=["Firewall"], estado_ocds="CONVOCADO",
                fecha_publicacion=(NOW - timedelta(days=10)).isoformat(),
                fecha_cierre_propuestas=(NOW + timedelta(hours=hours)).isoformat())
    data.update(extra)
    return data


class VigenciaTests(unittest.TestCase):
    def test_exact_48_hours(self):
        for hours, expected in [(-1, False), (0, False), (25, False),
                                (47.999, False), (48, True), (72, True)]:
            with self.subTest(hours=hours):
                self.assertEqual(parse_dates_op(op(hours), NOW)["es_elegible"], expected)

    def test_consultas_do_not_replace_proposals(self):
        near = (NOW + timedelta(hours=1)).isoformat()
        future = (NOW + timedelta(days=10)).isoformat()
        self.assertTrue(parse_dates_op(op(72, fecha_cierre_consultas=near), NOW)["es_elegible"])
        self.assertFalse(parse_dates_op(op(-1, fecha_cierre_consultas=future), NOW)["es_elegible"])
        self.assertFalse(parse_dates_op(op(fecha_cierre_propuestas=None,
                                           fecha_cierre_consultas=future), NOW)["es_elegible"])

    def test_invalid_closed_and_incoherent(self):
        for extra in [dict(estado_ocds="CONTRATADO"), dict(fecha_cierre_propuestas="invalid"),
                      dict(fecha_publicacion=(NOW + timedelta(days=20)).isoformat())]:
            self.assertFalse(parse_dates_op(op(**extra), NOW)["es_elegible"])

    def test_naive_dates_are_peru_and_days_not_rounded_up(self):
        self.assertEqual(parse_fecha("2026-09-12T10:00:00"), NOW)
        self.assertEqual(parse_dates_op(op(25), NOW)["dias_restantes"], 1)

    def test_expired_strong_match_never_high_priority(self):
        self.assertEqual(evaluar(op(fecha_cierre_propuestas="2015-01-01"))["prioridad"], "BAJA")
        self.assertEqual(evaluar(op(fecha_cierre_propuestas=None))["prioridad"], "BAJA")


class DatabaseTests(unittest.TestCase):
    def test_filter_before_pagination_and_stats_agree(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db, "DB_PATH", str(Path(folder) / "test.db")):
            db.init_db()
            now = datetime.now(timezone.utc)
            db.upsert_oportunidad(op(ocid="expired", fecha_publicacion=now.isoformat(),
                fecha_cierre_propuestas=(now - timedelta(days=1)).isoformat()))
            for index in range(3):
                db.upsert_oportunidad(op(ocid=str(index),
                    fecha_publicacion=(now - timedelta(days=10)).isoformat(),
                    fecha_cierre_propuestas=(now + timedelta(days=3)).isoformat()))
            self.assertEqual(len(db.list_oportunidades(limit=1)), 1)
            self.assertEqual(len(db.list_oportunidades(limit=1, offset=2)), 1)
            self.assertEqual(len(db.list_oportunidades(limit=None)), 3)
            self.assertEqual(db.get_stats()["total_oportunidades"], 3)
            self.assertEqual(len(db.list_oportunidades(solo_vigentes=False)), 4)


if __name__ == "__main__":
    unittest.main()


class ScannerTests(unittest.TestCase):
    def test_full_record_updates_dates_and_prefers_integrated_bases(self):
        from engine.scanner import RadarScanner
        scanner = RadarScanner()
        search_release = {"ocid": "test", "date": "2026-09-12T00:00:00Z",
                          "tender": {"title": "Firewall", "description": "firewall"}}
        full_release = {"ocid": "test", "tender": {
            "title": "Firewall", "description": "firewall",
            "datePublished": "2026-01-01T00:00:00-05:00",
            "tenderPeriod": {"endDate": "2026-01-10T00:00:00-05:00"},
            "items": [{"statusDetails": "CONTRATADO"}],
            "documents": [
                {"title": "Bases Administrativas", "url": "https://example.org/base.pdf"},
                {"title": "Bases Integradas", "url": "https://example.org/integrada.pdf"}]}}
        with patch.object(scanner.client, "search_processes", return_value={"results": [{"compiledRelease": search_release}]}), \
             patch.object(scanner.client, "fetch_record_by_ocid", return_value={"compiledRelease": full_release}), \
             patch("engine.scanner.upsert_oportunidad", return_value=True) as save, \
             patch("engine.scanner.record_scan_history"), patch("engine.scanner.time.sleep"):
            scanner.run_smart_scan(query="firewall", max_pages_per_query=1)
        data = save.call_args.args[0]
        self.assertEqual(data["fecha_publicacion"], "2026-01-01T00:00:00-05:00")
        self.assertEqual(data["estado_ocds"], "CONTRATADO")
        self.assertEqual(data["prioridad"], "BAJA")
        self.assertEqual(data["url_bases"], "https://example.org/integrada.pdf")
