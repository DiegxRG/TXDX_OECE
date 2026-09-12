import json
import os
import re
import unicodedata
from typing import Dict, Any, List, Tuple, Optional

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "txdx_services.json"
)

def normalize_text(text: str) -> str:
    """Elimina tildes, caracteres especiales y normaliza a minúsculas."""
    if not text:
        return ""
    # Descomponer caracteres con acento
    nfkd = unicodedata.normalize('NFKD', text)
    cleaned = ''.join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = cleaned.lower()
    # Reemplazar puntuaciones no alfanuméricas por espacios
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', cleaned)
    # Espacios múltiples
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

class TxDxClassifier:
    def __init__(self, config_file: str = CONFIG_PATH):
        with open(config_file, "r", encoding="utf-8") as f:
            self.config = json.load(f)
            
        self.service_lines = self.config.get("service_lines", {})
        self.blacklist = [normalize_text(k) for k in self.config.get("blacklist_keywords", [])]
        
        # Pre-normalize positive keywords
        self.normalized_keywords = {}
        for line_key, line_data in self.service_lines.items():
            self.normalized_keywords[line_key] = [
                normalize_text(kw) for kw in line_data.get("positive_keywords", [])
            ]

    def is_blacklisted(self, text_normalized: str) -> Tuple[bool, Optional[str]]:
        for bl in self.blacklist:
            if bl and bl in text_normalized:
                return True, bl
        return False, None

    def evaluate_release(self, release: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analiza un release OCDS y evalúa si calza con las capacidades de TxDx.
        """
        tender = release.get("tender", {})
        title = tender.get("title", "") or ""
        description = tender.get("description", "") or ""
        category = tender.get("mainProcurementCategory", "") or ""
        
        # Recolectar textos y clasificaciones de ítems
        item_descriptions = []
        item_cubso_codes = []
        for it in tender.get("items", []):
            item_descriptions.append(it.get("description", "") or "")
            classification = it.get("classification") or {}
            clf_id = classification.get("id") or ""
            clf_desc = classification.get("description") or ""
            if clf_id:
                item_cubso_codes.append(clf_id)
            if clf_desc:
                item_descriptions.append(clf_desc)
                
        raw_combined = f"{title} {description} {' '.join(item_descriptions)}"
        norm_combined = normalize_text(raw_combined)
        
        # 1. Filtro estricto de Lista Negra
        is_bl, bl_match = self.is_blacklisted(norm_combined)
        if is_bl:
            return {
                "matched": False,
                "score": 0,
                "linea_servicio": "DESCARTADO",
                "matched_keywords": [],
                "matched_products": [],
                "reason": f"Coincidencia con lista negra: '{bl_match}'"
            }
            
        # 2. Evaluación por cada Línea de Servicio TxDx
        best_line = None
        best_score = 0
        best_hits = []
        best_products = []
        
        for line_key, line_data in self.service_lines.items():
            current_score = 0
            current_hits = []
            
            # Chequeo CUBSO
            cubso_prefixes = line_data.get("cubso_prefixes", [])
            for c_code in item_cubso_codes:
                for prefix in cubso_prefixes:
                    if c_code.startswith(prefix):
                        current_score += 35
                        current_hits.append(f"CUBSO-{prefix}")
                        break
                        
            # Chequeo de Keywords Positivas
            keywords = self.normalized_keywords[line_key]
            for orig_kw, norm_kw in zip(line_data.get("positive_keywords", []), keywords):
                if re.search(r'\b' + re.escape(norm_kw) + r'\b', norm_combined):
                    # Palabras técnicas fuertes tienen mayor puntuación
                    if len(norm_kw.split()) > 1 or norm_kw in ["pentesting", "ciberseguridad", "firewall", "rpa", "datacenter", "switches", "siem", "soc", "vlans", "informix", "mongodb", "websphere", "vmware", "netbackup", "ssis"]:
                        current_score += 30
                    else:
                        current_score += 15
                    current_hits.append(orig_kw)
                    
            # Ponderador de línea
            weight = line_data.get("weight", 1.0)
            final_line_score = min(int(current_score * weight), 100)
            
            if final_line_score > best_score:
                best_score = final_line_score
                best_line = line_key
                best_hits = list(set(current_hits))
                best_products = line_data.get("commercial_products", [])
                
        # Umbral mínimo para considerar oportunidad válida
        MIN_THRESHOLD = 25
        matched = best_score >= MIN_THRESHOLD
        
        return {
            "matched": matched,
            "score": best_score,
            "linea_servicio": best_line if matched else "GENERAL",
            "matched_keywords": best_hits if matched else [],
            "matched_products": best_products if matched else [],
            "reason": f"Puntaje {best_score}/100 con {len(best_hits)} coincidencias" if matched else "Sin suficiente afinidad técnica"
        }
