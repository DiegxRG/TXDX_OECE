import urllib.request
import urllib.parse
import json
import ssl
import time
from typing import Dict, Any, Optional, List

BASE_API_URL = "https://contratacionesabiertas.oece.gob.pe/api/v1"

class OECEClient:
    def __init__(self, timeout: int = 15, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TxDx-Radar/2.0",
            "Accept": "application/json"
        }

    def _request(self, url: str) -> Optional[Dict[str, Any]]:
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                    if resp.status == 200:
                        raw = resp.read().decode("utf-8")
                        return json.loads(raw)
            except Exception as e:
                if attempt < self.max_retries:
                    time.sleep(attempt * 1.2)
                if attempt == self.max_retries:
                    print(f"[OECEClient] Error al consultar {url}: {e}")
                    return None
        return None

    def search_processes(
        self,
        query: str = "",
        year: Optional[str] = None,
        has_tender: bool = True,
        update_start: Optional[str] = None,
        update_end: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Optional[Dict[str, Any]]:
        """
        Consulta el endpoint oficial de búsqueda del portal OECE:
        /api/v1/search?format=json con soporte para filtros de texto, año y rangos de fechas.
        """
        params = {
            "format": "json",
            "page": str(page),
            "page_size": str(page_size)
        }
        if query:
            params["search"] = query
        if year:
            params["year"] = str(year)
        if has_tender:
            params["has_tender"] = "true"
        if update_start:
            params["updateDate"] = str(update_start)
        if update_end:
            params["updateDateEndDate"] = str(update_end)

        qs = urllib.parse.urlencode(params)
        url = f"{BASE_API_URL}/search?{qs}"
        return self._request(url)

    def fetch_record_by_ocid(self, ocid: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene el Record OCDS completo para un OCID:
        /api/v1/record/{ocid}?format=json
        Incluye la lista completa de documentos oficiales (Bases Administrativas PDF).
        """
        clean_ocid = ocid.strip()
        url = f"{BASE_API_URL}/record/{clean_ocid}?format=json"
        res = self._request(url)
        if res and "records" in res and len(res["records"]) > 0:
            return res["records"][0]
        # Fallback a endpoint de proceso
        fallback_url = f"{BASE_API_URL}/process?ocid={clean_ocid}&format=json"
        fb_res = self._request(fallback_url)
        if fb_res and "result" in fb_res:
            return fb_res["result"]
        return None

    def fetch_release_by_ocid(self, ocid: str) -> Optional[Dict[str, Any]]:
        """Compatibilidad hacia atrás: delega a fetch_record_by_ocid."""
        rec = self.fetch_record_by_ocid(ocid)
        if rec and "compiledRelease" in rec:
            return rec["compiledRelease"]
        return rec

    def fetch_releases_page(self, page: int = 1) -> Optional[Dict[str, Any]]:
        """Flujo secuencial general de releases."""
        url = f"{BASE_API_URL}/releases?page={page}"
        return self._request(url)
