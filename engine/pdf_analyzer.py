import os
import io
import re
import json
import time
import zipfile
import urllib.request
import urllib.error
import ssl
from typing import Dict, Any, List, Optional
from pypdf import PdfReader

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "bases")
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "gemini_key.txt")
GROQ_KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "groq_key.txt")
os.makedirs(STORAGE_DIR, exist_ok=True)

def get_gemini_key() -> Optional[str]:
    # 1. Variable de entorno
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key and key.strip():
        return key.strip()
        
    # 2. Archivo local config/gemini_key.txt
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    return None

def save_gemini_key(key: str) -> bool:
    try:
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key.strip())
        return True
    except Exception as e:
        print(f"[save_gemini_key] Error: {e}")
        return False

def get_groq_key() -> Optional[str]:
    # 1. Variable de entorno
    key = os.environ.get("GROQ_API_KEY")
    if key and key.strip():
        return key.strip()

    # 2. Archivo local config/groq_key.txt
    if os.path.exists(GROQ_KEY_FILE):
        try:
            with open(GROQ_KEY_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    return None

def save_groq_key(key: str) -> bool:
    try:
        with open(GROQ_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key.strip())
        return True
    except Exception as e:
        print(f"[save_groq_key] Error: {e}")
        return False

class BasesAnalyzer:
    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/zip,application/octet-stream,*/*"
        }

    def download_pdf(self, ocid: str, url: str) -> Optional[str]:
        """Descarga el documento de las bases desde SEACE (PDF o ZIP)."""
        if not url:
            return None
            
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', ocid) + ".pdf"
        file_path = os.path.join(STORAGE_DIR, safe_name)
        
        # Si ya existe y es un PDF válido, reutilizar
        if os.path.exists(file_path) and os.path.getsize(file_path) > 5000:
            with open(file_path, "rb") as f:
                header = f.read(5)
                if header.startswith(b"%PDF"):
                    return file_path
            
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=30, context=self.ctx) as resp:
                content = resp.read()
                
                # Caso ZIP
                if content.startswith(b"PK\x03\x04"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(content)) as zf:
                            pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
                            chosen_pdf = None
                            for name in pdf_names:
                                if "base" in name.lower():
                                    chosen_pdf = name
                                    break
                            if not chosen_pdf and pdf_names:
                                chosen_pdf = pdf_names[0]
                                
                            if chosen_pdf:
                                extracted_pdf = zf.read(chosen_pdf)
                                with open(file_path, "wb") as f:
                                    f.write(extracted_pdf)
                                return file_path
                    except Exception as ze:
                        print(f"[BasesAnalyzer] Error al descomprimir ZIP de SEACE: {ze}")

                # Caso PDF directo
                if content.startswith(b"%PDF") or len(content) > 1000:
                    with open(file_path, "wb") as f:
                        f.write(content)
                    return file_path
        except Exception as e:
            print(f"[BasesAnalyzer] Error descargando bases para {ocid} desde {url}: {e}")
            return None
        return None

    def extract_text(self, pdf_path: str, max_pages: int = 100) -> str:
        if not pdf_path or not os.path.exists(pdf_path):
            return ""
            
        full_text = []
        try:
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            pages_to_read = min(total_pages, max_pages)
            
            for i in range(pages_to_read):
                try:
                    p_text = reader.pages[i].extract_text() or ""
                    if p_text.strip():
                        full_text.append(f"--- PÁGINA {i+1} ---\n{p_text}")
                except Exception:
                    continue
        except Exception as e:
            print(f"[BasesAnalyzer] Error extrayendo texto del PDF {pdf_path}: {e}")
            return ""
            
        return "\n".join(full_text)

    def _build_audit_prompt(self, text: str, meta: Dict[str, Any]) -> str:
        """Construye el prompt de auditoría de Bases (Capítulo III) para motores IA."""
        # Limitar a los primeros 60,000 caracteres del texto (más que suficiente para el Capítulo III)
        doc_sample = text[:70000]

        return f"""
Eres un auditor experto de contrataciones públicas en Perú (SEACE / OECE) para la empresa TxDx (especializada en Ciberseguridad, Networking, Inteligencia Artificial, y Gestión de Datos).

Analiza el siguiente documento de Bases Oficiales para el proceso:
Título/Proceso: {meta.get('titulo', '')}
Entidad: {meta.get('entidad', '')}
Monto Estimado: S/ {meta.get('monto_referencial', 0)}
Línea TxDx Afín: {meta.get('linea_servicio', '')}

Tu tarea es leer el Capítulo III (Requerimiento Técnico / Términos de Referencia) y responder EXCLUSIVAMENTE con un JSON válido con esta estructura exacta:
{{
  "experiencia_postor": "Monto de facturación acumulada mínima que debe acreditar el postor en soles, número de veces el valor estimado y años de antigüedad válidos.",
  "personal_clave": [
    "Cargo / Perfil 1: Requisitos de profesión (Sistemas, Redes, etc.), años de experiencia acreditada y funciones",
    "Cargo / Perfil 2..."
  ],
  "certificaciones_requeridas": [
    "Nombre de certificación técnica específica exigida (ej. CCNA, CCNP, Fortinet NSE, ISO 27001, ITIL, PMP, etc.) o 'Colegiatura y habilitación profesional'"
  ],
  "plazo_ejecucion": "Plazo exacto en días calendario o meses para cumplir el servicio",
  "modalidad": "Modalidad de trabajo (Presencial, Remota o Híbrida) y ubicación",
  "forma_pago": "Forma y condiciones de pago (ej. pago único contra entregables, pagos mensuales con conformidad)",
  "penalidades": "Resumen de penalidades específicas o estándar aplicables",
  "garantia": "Monto exacto de la carta fianza u otra garantía exigida al postor (ej. 'S/ 85,000' por fiel cumplimiento) o 'No especificada'",
  "factibilidad_txdx": {{
    "nivel": "ALTA FACTIBILIDAD" o "FACTIBILIDAD MEDIA" o "EVALUAR CON CUIDADO",
    "color": "#059669" o "#ea580c" o "#dc2626",
    "score": 85,
    "mensaje": "Veredicto claro para el gerente comercial de TxDx: por qué calza o qué se necesita para postular con éxito",
    "alertas": [
      "Alerta sobre requisitos difíciles o montos altos si los hay"
    ]
  }}
}}

TEXTO DE LAS BASES:
{doc_sample}
"""

    def analyze_with_ai(self, text: str, meta: Dict[str, Any], api_key: str) -> Optional[Dict[str, Any]]:
        """Usa OpenAI-compatible API (Groq) para analizar los requisitos con precisión IA."""
        prompt = self._build_audit_prompt(text, meta)

        # Modelos gratuitos disponibles en Groq con soporte JSON mode (2026)
        models_to_try = [
            "groq/compound",
            "groq/compound-mini",
            "qwen/qwen3.8-27b"
        ]

        for model_name in models_to_try:
            endpoint = "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "Eres un auditor experto de contrataciones públicas del Perú (SEACE/OECE). Solo respondes con JSON válido, sin texto adicional."},
                    {"role": "user", "content": ""}
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"}
            }
            for sample_len in (70000, 30000, 12000):
                prompt = self._build_audit_prompt(text[:sample_len], meta)
                payload["messages"][1]["content"] = prompt
                for attempt in range(3):
                    try:
                        req_data = json.dumps(payload).encode("utf-8")
                        req = urllib.request.Request(
                            endpoint,
                            data=req_data,
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {api_key}",
                                # Groq bloquea el User-Agent por defecto de urllib (Python-urllib)
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                            }
                        )
                        with urllib.request.urlopen(req, timeout=90, context=self.ctx) as resp:
                            if resp.status == 200:
                                res_json = json.loads(resp.read().decode("utf-8"))
                                choices = res_json.get("choices", [])
                                if choices:
                                    raw_reply = choices[0].get("message", {}).get("content", "")
                                    clean_reply = re.sub(r'^```json\s*', '', raw_reply.strip())
                                    clean_reply = re.sub(r'\s*```$', '', clean_reply)
                                    parsed = json.loads(clean_reply)
                                    parsed["motor"] = f"Groq IA ({model_name})"
                                    return parsed
                                break
                    except urllib.error.HTTPError as he:
                        if he.code == 429:
                            # Límite de tasa del tier gratuito: esperar y reintentar
                            time.sleep(5 + attempt * 5)
                            continue
                        if he.code == 413:
                            # Payload demasiado grande: reintentar con muestra más corta
                            print(f"[BasesAnalyzer] {model_name} rechaza muestras de {sample_len} chars; reintentando más corto.")
                            break
                        # Error de autenticación/modelo: probar siguiente modelo
                        print(f"[BasesAnalyzer] Intento con {model_name} falló: {he}")
                        break
                    except Exception as e:
                        print(f"[BasesAnalyzer] Intento con {model_name} falló: {e}")
                        break

        return None

    def analyze_with_gemini(self, text: str, meta: Dict[str, Any], api_key: str) -> Optional[Dict[str, Any]]:
        """Usa Google Gemini Flash para analizar el requerimiento con 100% de precisión."""
        prompt = self._build_audit_prompt(text, meta)
        # Probar modelos disponibles en Gemini
        models_to_try = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash"
        ]
        
        for model_name in models_to_try:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [{"text": prompt}]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json"
                }
            }
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    endpoint,
                    data=req_data,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=30, context=self.ctx) as resp:
                    if resp.status == 200:
                        res_json = json.loads(resp.read().decode("utf-8"))
                        candidates = res_json.get("candidates", [])
                        if candidates:
                            raw_reply = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            # Limpiar posibles bloques ```json ... ```
                            clean_reply = re.sub(r'^```json\s*', '', raw_reply.strip())
                            clean_reply = re.sub(r'\s*```$', '', clean_reply)
                            parsed = json.loads(clean_reply)
                            parsed["motor"] = f"Gemini IA ({model_name})"
                            return parsed
            except Exception as e:
                # Si el modelo no está disponible o falla, probar el siguiente
                print(f"[BasesAnalyzer] Intento con {model_name} falló: {e}")
                continue
                
        return None

    def analyze_document(self, ocid: str, url_bases: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Orquesta la descarga y el análisis (vía Gemini AI si hay key, o heurístico)."""
        meta = meta or {}
        pdf_path = self.download_pdf(ocid, url_bases)
        
        if not pdf_path:
            return {
                "success": False,
                "error": "No se pudo descargar el archivo de Bases oficial desde SEACE (Enlace temporalmente inaccesible).",
                "ocid": ocid
            }
            
        raw_text = self.extract_text(pdf_path)
        if not raw_text or len(raw_text) < 200:
            return {
                "success": False,
                "error": "El documento descargado no contiene texto indexable por máquina (posible PDF escaneado).",
                "ocid": ocid,
                "pdf_local": pdf_path
            }

        total_pages = len(re.findall(r'--- PÁGINA \d+ ---', raw_text))

        # 1. Intentar con Groq AI (OpenAI-compatible, gratis) si existe la clave
        groq_key = get_groq_key()
        if groq_key:
            try:
                ai_res = self.analyze_with_ai(raw_text, meta, groq_key)
                if ai_res:
                    ai_res.setdefault("garantia", "Por verificar en Bases")
                    ai_res["success"] = True
                    ai_res["ocid"] = ocid
                    ai_res["pdf_local"] = pdf_path
                    ai_res["total_paginas"] = total_pages
                    return ai_res
            except Exception as ge:
                print(f"[BasesAnalyzer] Error con Groq AI: {ge}")

        # 2. Intentar con Gemini AI si existe la clave
        gemini_key = get_gemini_key()
        if gemini_key:
            try:
                ai_res = self.analyze_with_gemini(raw_text, meta, gemini_key)
                if ai_res:
                    ai_res.setdefault("garantia", "Por verificar en Bases")
                    ai_res["success"] = True
                    ai_res["ocid"] = ocid
                    ai_res["pdf_local"] = pdf_path
                    ai_res["total_paginas"] = total_pages
                    return ai_res
            except Exception as ge:
                print(f"[BasesAnalyzer] Error con Gemini AI: {ge}")

        # 3. Fallback heurístico local
        experiencia_postor = self._extract_experiencia_postor(raw_text)
        personal_clave = self._extract_personal_clave(raw_text)
        certificaciones = self._extract_certificaciones(raw_text)
        plazo_modalidad = self._extract_plazo_modalidad(raw_text)
        pagos = self._extract_forma_pago(raw_text)
        garantia = self._extract_garantia(raw_text)
        factibilidad = self._evaluate_factibilidad(
            meta.get("linea_servicio", ""),
            certificaciones,
            experiencia_postor,
            personal_clave,
            garantia
        )

        return {
            "success": True,
            "ocid": ocid,
            "pdf_local": pdf_path,
            "motor": "Extractor Local (Configura tu Groq API Key gratuita para precisión IA)",
            "experiencia_postor": experiencia_postor,
            "personal_clave": personal_clave,
            "certificaciones_requeridas": certificaciones,
            "plazo_ejecucion": plazo_modalidad.get("plazo", "Según cronograma del Capítulo III"),
            "modalidad": plazo_modalidad.get("modalidad", "Presencial / Sede institucional"),
            "forma_pago": pagos,
            "garantia": garantia,
            "factibilidad_txdx": factibilidad,
            "total_paginas": total_pages
        }

    def _extract_experiencia_postor(self, text: str) -> str:
        patterns = [
            r'(?:experiencia\s+del\s+postor\s+en\s+la\s+especialidad[\s\S]{1,400}?(?:veces|monto|facturaci[oó]n|acumulado|s\/|soles)[\s\S]{1,300}?\.)',
            r'(?:facturaci[oó]n\s+acumulada[\s\S]{1,300}?(?:veces|monto|s\/|soles)[\s\S]{1,200}?\.)',
            r'(?:el\s+postor\s+debe\s+acreditar[\s\S]{1,300}?(?:soles|s\/|monto)[\s\S]{1,200}?\.)'
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                cleaned = re.sub(r'\s+', ' ', m.group(0)).strip()
                return cleaned[:450]
                
        lines = [line.strip() for line in text.splitlines() if any(k in line.lower() for k in ["experiencia del postor", "facturacion acumulada", "veces el valor"])]
        if lines:
            return " ".join(lines[:3])[:400]
            
        return "Acreditar experiencia en servicios similares a través de contratos, órdenes de servicio o comprobantes de pago."

    def _extract_personal_clave(self, text: str) -> List[str]:
        profiles = []
        roles_keywords = [
            r'jefe\s+de\s+proyecto',
            r'coordinador\s+del\s+servicio',
            r'especialista\s+en\s+redes',
            r'especialista\s+en\s+seguridad',
            r'especialista\s+en\s+ciberseguridad',
            r'especialista\s+en\s+infraestructura',
            r'especialista\s+en\s+sistemas',
            r'especialista\s+en\s+comunicaciones',
            r'especialista\s+en\s+backup',
            r'ingeniero\s+de\s+sistemas',
            r'ingeniero\s+electr[oó]nico',
            r'ingeniero\s+de\s+telecomunicaciones',
            r't[eé]cnico\s+de\s+soporte'
        ]
        for kw in roles_keywords:
            matches = re.finditer(r'([^\.\n]{0,80}' + kw + r'[^\.\n]{0,120})', text, re.IGNORECASE)
            for match in matches:
                clean_role = re.sub(r'\s+', ' ', match.group(0)).strip()
                if len(clean_role) > 15 and clean_role not in profiles:
                    profiles.append(clean_role)
                    if len(profiles) >= 4:
                        break
            if len(profiles) >= 4:
                break
                
        if not profiles:
            profiles = ["Profesional titulado en Ingeniería de Sistemas, Informática o afines con experiencia en soporte y redes."]
            
        return profiles[:4]

    def _extract_certificaciones(self, text: str) -> List[str]:
        known_certs = [
            ("CCNA", r'\bCCNA\b'),
            ("CCNP", r'\bCCNP\b'),
            ("CCIE", r'\bCCIE\b'),
            ("Fortinet NSE", r'\bNSE\s*[4-8]\b|\bFortinet\s+NSE\b'),
            ("Palo Alto Networks", r'\bPalo\s+Alto\b|\bPCNSE\b'),
            ("Check Point", r'\bCCSA\b|\bCCSE\b'),
            ("CEH (Certified Ethical Hacker)", r'\bCEH\b|Certified\s+Ethical\s+Hacker'),
            ("CISSP", r'\bCISSP\b'),
            ("ISO 27001", r'ISO\s*(?:/IEC)?\s*27001'),
            ("ISO 9001", r'ISO\s*9001'),
            ("ISO 20000", r'ISO\s*20000'),
            ("ITIL", r'\bITIL\b'),
            ("PMP", r'\bPMP\b|Project\s+Management\s+Professional'),
            ("Scrum Master", r'\bScrum\s+Master\b'),
            ("AWS Certified", r'\bAWS\s+(?:Certified|Solutions\s+Architect)\b'),
            ("Azure Certified", r'\bAzure\s+(?:Certified|Administrator)\b'),
            ("CompTIA Security+", r'\bSecurity\+\b|\bCompTIA\b'),
            ("Colegiatura y Habilitación", r'colegiado\s+y\s+habilitado|colegiatura\s+vigente')
        ]
        
        found = []
        for name, pattern in known_certs:
            if re.search(pattern, text, re.IGNORECASE):
                found.append(name)
                
        return found

    def _extract_plazo_modalidad(self, text: str) -> Dict[str, str]:
        plazo_match = re.search(r'(?:plazo\s+de\s+ejecuci[oó]n|plazo\s+del\s+servicio)[\s\S]{1,100}?(\d+\s*(?:d[ií]as\s*calendario|meses|a[nñ]os))', text, re.IGNORECASE)
        plazo = plazo_match.group(0).strip() if plazo_match else "Según cronograma contractual de las Bases"
        plazo = re.sub(r'\s+', ' ', plazo)[:120]
        
        modalidad = "Presencial / Sede institucional"
        if re.search(r'\bremoto\b|\bvirtual\b|\btrabajo\s+remoto\b', text, re.IGNORECASE):
            modalidad = "Remota o Híbrida"
        elif re.search(r'\bpresencial\b', text, re.IGNORECASE):
            modalidad = "Presencial"
            
        return {"plazo": plazo, "modalidad": modalidad}

    def _extract_forma_pago(self, text: str) -> str:
        match = re.search(r'(?:forma\s+de\s+pago|del\s+pago)[\s\S]{1,250}?(?:entregable|conformidad|mensual|armadas|pago\s+[uú]nico)[\s\S]{1,150}?\.', text, re.IGNORECASE)
        if match:
            clean = re.sub(r'\s+', ' ', match.group(0)).strip()
            return clean[:350]
        return "Pago contra entrega de informes mensuales o entregables, previa conformidad del área usuaria."

    def _extract_garantia(self, text: str) -> str:
        """Extrae el monto de la carta fianza / garantía de fiel cumplimiento."""
        for kw in ["carta fianza", "garantia de fiel cumplimiento", "garantia del postor"]:
            idx = text.lower().find(kw)
            if idx == -1:
                continue
            ventana = text[idx:idx + 250]
            m = re.search(r'[Ss]/\.?[\s]*([\d][\d.,]*)', ventana)
            if m:
                montostr = m.group(1)
                limpio = re.search(r'[\d][\d.,]*', montostr).group(0)
                try:
                    valor = self._parse_soles(limpio)
                    if valor:
                        return f"Carta fianza de fiel cumplimiento: S/ {valor:,.2f}"
                except (ValueError, TypeError):
                    pass
                return f"Carta fianza por S/ {limpio} (verificar monto exacto en Bases)"
        return "No especificada (verificar en Capítulo III de las Bases)"

    @staticmethod
    def _parse_soles(s: str) -> Optional[float]:
        """Parsea '1,500,000.00', '1.500.000,00' o '150000' a float."""
        s = s.strip()
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")  # 1.500.000,00
            else:
                s = s.replace(",", "")  # 1,500,000.00
        elif "," in s:
            partes = s.split(",")
            # 1,50 -> decimal; 1,500 -> miles
            if len(partes) == 2 and len(partes[1]) == 2:
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        return float(s)

    def _evaluate_factibilidad(self, linea: str, certs: List[str], exp: str, personal: List[str], garantia: str = "") -> Dict[str, Any]:
        puntos = 70
        alertas = []
        
        if any(c in certs for c in ["CCNA", "CCNP", "Fortinet NSE", "ISO 27001", "ITIL", "Colegiatura y Habilitación"]):
            puntos += 20
            
        if "3 veces" in exp.lower() or "4 veces" in exp.lower() or "millon" in exp.lower():
            puntos -= 20
            alertas.append("Exige alto monto de facturación acumulada previa.")
            
        if "colegiado y habilitado" in " ".join(personal).lower() or "Colegiatura y Habilitación" in certs:
            alertas.append("Requiere ingenieros colegiados y habilitados.")

        # Regla TxDx: monto referencial o carta fianza de 1 millón está FUERA del radio
        if garantia:
            try:
                nums = re.findall(r'[\d][\d.,]*', garantia)
                montos = [self._parse_soles(n) for n in nums if self._parse_soles(n) is not None]
                monto_g = max(montos) if montos else 0
                if monto_g >= 1_000_000:
                    puntos = min(puntos, 45)
                    alertas.append("Exige carta fianza de S/ 1,000,000 o más: FUERA de la regla TxDx (< S/ 1M).")
            except (ValueError, TypeError):
                pass
            
        if puntos >= 80:
            nivel = "ALTA FACTIBILIDAD"
            color = "#059669"
            mensaje = "El requerimiento calza con las fortalezas técnicas de TxDx. Altamente recomendado preparar propuesta."
        elif puntos >= 55:
            nivel = "FACTIBILIDAD MEDIA"
            color = "#ea580c"
            mensaje = "Requerimiento viable. Verificar montos de facturación previa acumulada o evaluar postulación en consorcio."
        else:
            nivel = "EVALUAR CON CUIDADO"
            color = "#dc2626"
            mensaje = "Exigencias elevadas de facturación o perfiles. Evaluar consorcio con socios estratégicos."

        return {
            "nivel": nivel,
            "color": color,
            "score": puntos,
            "mensaje": mensaje,
            "alertas": alertas
        }
