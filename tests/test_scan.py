import unittest
from unittest.mock import patch
from fastapi import BackgroundTasks
from pydantic import ValidationError
import app as api
from engine.scanner import RadarScanner


class ScanTests(unittest.TestCase):
    def setUp(self):
        api.is_scanning = False
        api.last_scan_result = None
        api.scan_progress = {}

    def tearDown(self):
        api.is_scanning = False
        api.last_scan_result = None
        api.scan_progress = {}

    def test_duplicate_reserved_before_worker_starts(self):
        tasks = BackgroundTasks()
        self.assertEqual(api.api_trigger_scan(api.ScanRequest(), tasks)["status"], "started")
        self.assertTrue(api.api_scan_status()["is_scanning"])
        self.assertEqual(api.api_trigger_scan(api.ScanRequest(), tasks)["status"], "already_running")
        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(tasks.tasks[0].kwargs["pages"], 2)
        with self.assertRaises(ValidationError):
            api.ScanRequest(pages=25)

    def test_worker_exception_clears_running(self):
        api.is_scanning = True
        with patch("app.RadarScanner", side_effect=RuntimeError("offline")):
            api._background_scan_worker()
        self.assertFalse(api.api_scan_status()["is_scanning"])
        self.assertEqual(api.last_scan_result["status"], "ERROR")

    def test_deadline_finishes_partial_without_queries(self):
        scanner = RadarScanner()
        with patch.object(scanner.client, "search_processes") as search, patch("engine.scanner.record_scan_history"):
            result = scanner.run_smart_scan(query="firewall", max_seconds=0)
        search.assert_not_called()
        self.assertTrue(result["limite_tiempo"])
        self.assertEqual(result["status"], "PARTIAL")

    def test_expired_skips_record_and_stops_at_last_page(self):
        scanner = RadarScanner()
        data = {"results": [{"compiledRelease": {"ocid": "old", "tender": {
            "description": "firewall", "tenderPeriod": {"endDate": "2015-01-01"}}}}], "next": None}
        states = []
        with patch.object(scanner.client, "search_processes", return_value=data) as search, \
             patch.object(scanner.client, "fetch_record_by_ocid") as record, \
             patch("engine.scanner.upsert_oportunidad", return_value=False), \
             patch("engine.scanner.record_scan_history"):
            result = scanner.run_smart_scan(query="firewall", progress=states.append)
        record.assert_not_called()
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result["expedientes_omitidos"], 1)
        self.assertEqual(states[-1]["queries_done"], 1)

    def test_failed_search_never_success(self):
        scanner = RadarScanner()
        with patch.object(scanner.client, "search_processes", return_value=None), patch("engine.scanner.record_scan_history"):
            result = scanner.run_smart_scan(query="firewall")
        self.assertNotEqual(result["status"], "SUCCESS")
