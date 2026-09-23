import os
import io
import re
import json
import time
import random
import threading
import zipfile
import urllib.request
import urllib.error
import ssl
from typing import Dict, Any, List, Optional
from pypdf import PdfReader
from db.database import record_usage
try:
    import pymupdf as fitz
    import pytesseract
except ImportError:
    fitz = None
    pytesseract = None

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "bases")
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "gemini_key.txt")
GROQ_KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "groq_key.txt")
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSDATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "ocr", "tessdata")
os.makedirs(STORAGE_DIR, exist_ok=True)

# Semaforo global: las cuentas gratuitas limitan concurrencia (1-2 peticiones simultaneas).
_LLM_SEMAPHORE = threading.Semaphore(2)

_DEF_EXPERIENCIA = "Acreditar experiencia en servicios similares a través de contratos, órdenes de servicio o comprobantes de pago."
_DEF_PERSONAL = "Profesional titulado en Ingeniería de Sistemas, Informática o afines con experiencia en soporte y redes."
_DEF_PLAZO = "Según cronograma contractual de las Bases"
_DEF_PLAZO_V2 = "Según cronograma del Capítulo III"
_DEF_MODALIDAD = "Presencial / Sede institucional"
_DEF_PAGO = "Pago contra entrega de informes mensuales o entregables, previa conformidad del área usuaria."
_DEF_GARANTIA = "No especificada (verificar en Capítulo III de las Bases)"

FIELD_REFINE = {
    "experiencia_postor": {
        "keys": ["experiencia_postor"],
        "instruction": ("Busca el requisito de EXPERIENCIA DEL POSTOR: monto de facturación acumulada "
                        "mínima exigida en soles, número de veces el valor estimado y años de antigüedad "
                        "válidos. Responde con el texto exacto o un resumen fiel.")
    },
    "personal_clave": {
        "keys": ["personal_clave"],
        "instruction": ("Busca el PERSONAL CLAVE exigido (cargos como jefe de proyecto, coordinador del "
                        "servicio, especialistas, ingenieros): profesión, años de experiencia acreditada y "
                        "funciones. Responde una lista de perfiles.")
    },
    "certificaciones_requeridas": {
        "keys": ["certificaciones_requeridas"],
        "instruction": ("Busca CERTIFICACIONES TÉCNICAS exigidas al personal o al postor (ej. CCNA, CCNP, "
                        "Fortinet NSE, ISO 27001, ITIL, PMP, Security+, colegiatura y habilitación). "
                        "Responde una lista con el nombre exacto de cada certificación encontrada.")
    },
    "garantia": {
        "keys": ["garantia"],
        "instruction": ("Busca la GARANTÍA exigida al postor: carta fianza de fiel cumplimiento, monto "
                        "exacto en soles y porcentaje si se indica. Si el monto aparece en cualquier "
                        "página o anexo, repórtalo. Si no aparece, responde null.")
    },
    "forma_pago": {
        "keys": ["forma_pago"],
        "instruction": ("Busca la FORMA Y CONDICIONES DE PAGO: pago único contra entregables, pagos "
                        "mensuales con conformidad, armadas, porcentajes, plazos. Responde un resumen fiel.")
    },
    "plazo_modalidad": {
        "keys": ["plazo_ejecucion", "modalidad"],
        "instruction": ("Busca el PLAZO DE EJECUCIÓN (días calendario o meses) y la MODALIDAD de trabajo "
                        "(presencial, remota, híbrida). Responde ambas claves.")
    },
}


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
        self._usage_total = 0
        self._usage_ocid: Optional[str] = None
        self._usage_modo: Optional[str] = None

    def download_pdf(self, ocid: str, url: str, progress=None) -> Optional[str]:
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

        # Bases ya extraídas de un documento Office: reutilizar texto.
        txt_cached = file_path[:-4] + ".txt"
        if os.path.exists(txt_cached) and os.path.getsize(txt_cached) > 200:
            return txt_cached

        try:
            if progress:
                progress("Descargando bases", "Recibiendo documento oficial de SEACE")
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=60, context=self.ctx) as resp:
                size = int(resp.headers.get("Content-Length") or 0)
                if size and size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("El documento supera el límite seguro de 80 MB")
                parts, received = [], 0
                while True:
                    part = resp.read(1024 * 1024)
                    if not part:
                        break
                    received += len(part)
                    if received > MAX_DOWNLOAD_BYTES:
                        raise ValueError("El documento supera el límite seguro de 80 MB")
                    parts.append(part)
                content = b"".join(parts)

                # Caso ZIP: puede contener un PDF real o un documento Office (docx/xlsx).
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

                            # Sin PDF dentro: intentar extraer texto de docx/xlsx (bases en Office).
                            office_text = None
                            if "word/document.xml" in zf.namelist():
                                office_text = re.sub(
                                    r"<[^>]+>", " ",
                                    zf.read("word/document.xml").decode("utf-8", "ignore"))
                                office_text = re.sub(r"\s+", " ", office_text).strip()
                            elif any(n.startswith("xl/worksheets/") and n.endswith(".xml") for n in zf.namelist()):
                                office_text = self._extract_xlsx_text(zf)
                            if office_text and len(office_text) > 200:
                                txt_path = os.path.join(STORAGE_DIR, re.sub(r"[^a-zA-Z0-9_\-]", "_", ocid) + ".txt")
                                with open(txt_path, "w", encoding="utf-8") as f:
                                    f.write(office_text)
                                if os.path.exists(file_path):
                                    os.remove(file_path)
                                return txt_path
                    except Exception as ze:
                        print(f"[BasesAnalyzer] Error al descomprimir ZIP de SEACE: {ze}")

                # Caso PDF directo: solo aceptar contenido con cabecera %PDF; archivos
                # devueltos como HTML/correo erróneo no deben contaminar la caché.
                if content.startswith(b"%PDF"):
                    with open(file_path, "wb") as f:
                        f.write(content)
                    return file_path
        except Exception as e:
            print(f"[BasesAnalyzer] Error descargando bases para {ocid} desde {url}: {e}")
            return None
        return None

    def _extract_xlsx_text(self, zf: "zipfile.ZipFile") -> str:
        shared = {}
        if "xl/sharedStrings.xml" in zf.namelist():
            sh_raw = zf.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            shared = {i: t for i, t in enumerate(re.findall(r"<t[^>]*>(.*?)</t>", sh_raw, re.S))}
        cells = []
        for name in sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")):
            sheet_raw = zf.read(name).decode("utf-8", "ignore")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet_raw, re.S):
                row_text = []
                for cell in re.findall(r"<c[^>]*>(.*?)</c>", row, re.S):
                    t_ref = re.search(r'<t[^>]*>(.*?)</t>', cell, re.S)
                    v_ref = re.search(r'<v>(.*?)</v>', cell, re.S)
                    s_ref = re.search(r'<is><t[^>]*>(.*?)</t>', cell, re.S)
                    is_inline = re.search(r't="s"', cell)
                    raw = None
                    if s_ref and v_ref and is_inline:
                        raw = shared.get(int(v_ref.group(1)), "")
                    elif t_ref:
                        raw = re.sub(r"<[^>]+>", "", t_ref.group(1))
                    elif v_ref:
                        raw = v_ref.group(1)
                    if raw is not None and str(raw).strip():
                        row_text.append(str(raw).strip())
                if row_text:
                    cells.append(" | ".join(row_text))
        return "\n".join(cells)

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

    def _toc_anchor_page(self, pdf_path: str) -> Optional[int]:
        """Busca en los marcadores del PDF (TOC) la página del capítulo técnico.
        Devuelve página física 0-based o None si no hay marcadores útiles."""
        if not fitz:
            return None
        try:
            doc = fitz.open(pdf_path)
            toc = doc.get_toc()
            doc.close()
        except Exception as e:
            print(f"[BasesAnalyzer] Error leyendo TOC: {e}")
            return None
        toc_markers = ("capítulo iii", "capitulo iii", "capítulo 3", "capitulo 3",
                       "terminos de referencia", "términos de referencia",
                       "requerimiento técnico", "requerimiento tecnico",
                       "requisitos de calificación", "requisitos de calificacion")
        for title, page_no, _ in toc:
            t = (title or "").lower()
            if any(m in t for m in toc_markers):
                return max(0, page_no - 1)
        return None

    def extract_relevant_text(self, pdf_path: str, progress=None, max_pages: int = 240):
        """Busca el capítulo técnico antes de enviar texto al modelo y conserva evidencia."""
        if not pdf_path or not os.path.exists(pdf_path):
            return "", 0, []
        page_texts = []
        try:
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            for i in range(min(total_pages, max_pages)):
                try:
                    text = reader.pages[i].extract_text() or ""
                    if text.strip():
                        page_texts.append((i + 1, text))
                except Exception:
                    continue
        except Exception as e:
            print(f"[BasesAnalyzer] Error extrayendo texto del PDF {pdf_path}: {e}")
            return "", 0, []
        if not page_texts:
            return "", total_pages, []
        if progress:
            progress("Ubicando requisitos", f"Leyendo {len(page_texts)} de {total_pages} páginas")
        markers = ("capítulo iii", "capitulo iii", "términos de referencia", "terminos de referencia",
                   "requerimiento técnico", "requerimiento tecnico", "requisitos de calificación")
        toc_anchor = self._toc_anchor_page(pdf_path)
        anchor = None
        if toc_anchor is not None:
            anchor = next((i for i, (page, _) in enumerate(page_texts) if page == toc_anchor + 1), None)
        if anchor is None:
            anchor = next((i for i, (_, text) in enumerate(page_texts) if any(m in text.lower() for m in markers)), None)
        if anchor is not None:
            selected = list(page_texts[anchor:anchor + 45])
            # Cola comercial: garantías/fianza/forma de pago/penalidades suelen vivir en
            # Capítulos IV-VI o anexos, FUERA de la ventana fija de 45 páginas. Se extiende
            # (extracción nativa = barata) hasta cubrir esos cuatro campos o agotar el PDF.
            tail_markers = ("garant", "fianza", "forma de pago", "penalida", "mora",
                            "carta fianza", "fiel cumplimiento", "dieta por mora")
            covered = " ".join(c.lower() for _, c in selected)
            tail = [(p, c) for p, c in page_texts[anchor + 45:] if any(m in c.lower() for m in tail_markers)]
            final, seen = [], set()
            for p, c in sorted(selected + tail, key=lambda x: x[0]):
                if p not in seen:
                    seen.add(p)
                    final.append((p, c))
            selected = final
        else:
            selected = (page_texts[:20] + page_texts[max(20, len(page_texts) // 2):max(20, len(page_texts) // 2) + 20])
        selected_pages = [page for page, _ in selected]
        text = "\n".join(f"--- PAGINA {page} ---\n{content}" for page, content in selected)
        return text, total_pages, selected_pages

    def _ocr_cache_path(self, pdf_path: str) -> str:
        return pdf_path + ".ocr.json"

    def extract_ocr_relevant_text(self, pdf_path: str, progress=None, limit_pages: Optional[int] = None,
                                  probe_pages: int = 30, block_pages: int = 45, tail_cap: int = 120):
        """OCR local: índice primero, luego solo el capítulo técnico. Cache sidecar por PDF."""
        if not fitz or not pytesseract or not os.path.exists(TESSERACT_CMD):
            return "", 0, []
        try:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
            os.environ["TESSDATA_PREFIX"] = os.path.abspath(TESSDATA_DIR) + os.sep
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            if progress:
                progress("OCR local", "El PDF no tiene texto. Buscando índice y Capítulo III sin usar Groq")
            config = "--psm 6"
            markers = ("capitulo iii", "capítulo iii", "terminos de referencia", "términos de referencia",
                       "requerimiento tecnico", "requerimiento técnico", "requisitos de calificacion")

            cache = {}
            cache_path = self._ocr_cache_path(pdf_path)
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cache = json.load(f)
                except Exception:
                    cache = {}

            def save_cache():
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False)
                except Exception as e:
                    print(f"[BasesAnalyzer] Error guardando cache OCR: {e}")

            def ocr_page(page_no, scale=1.3):
                key = str(page_no)
                if key in cache and cache[key]:
                    return cache[key]
                page = doc.load_page(page_no)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                from PIL import Image
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                text = pytesseract.image_to_string(image, lang="spa", config=config)
                cache[key] = text
                return text

            def ocr_page_adaptive(page_no):
                text = ocr_page(page_no, 1.3)
                if len(text.strip()) < 40:
                    text = ocr_page(page_no, 1.7)
                return text

            # Modo rápido: solo patrullar las primeras páginas, sin buscar capítulo.
            if limit_pages:
                selected = []
                for page_no in range(min(limit_pages, total_pages)):
                    text = ocr_page(page_no, 1.3)
                    if text.strip():
                        selected.append((page_no + 1, text))
                save_cache()
                selected_pages = [page for page, _ in selected]
                text = "\n".join(f"--- PAGINA {page} ---\n{content}" for page, content in selected)
                doc.close()
                return text, total_pages, selected_pages

            # Ancla por marcadores TOC si existen (gratis, evita la pasada de índice).
            toc_anchor = self._toc_anchor_page(pdf_path)
            selected = []
            if toc_anchor is not None:
                anchor = toc_anchor
            else:
                probe = []
                for page_no in range(min(probe_pages, total_pages)):
                    text = ocr_page(page_no, 1.1)
                    probe.append((page_no, text))
                anchor = next((page - 1 for page, text in probe if any(m in text.lower() for m in markers)), None)
                if anchor is None:
                    selected = [(p + 1, t) for p, t in probe if t.strip()]

            if anchor is not None:
                selected = []
                for page_no in range(anchor, min(total_pages, anchor + block_pages)):
                    if progress and page_no % 5 == 0:
                        progress("OCR local", f"Leyendo página {page_no + 1} de {total_pages}; sin consumo de Groq")
                    selected.append((page_no + 1, ocr_page(page_no, 1.7)))

                # Barrido continuo de la cola con early-stop: cubre garantías/pagos/penalidades
                # en Capítulos IV-VI o anexos, FUERA del bloque técnico, sin paginar todo el PDF.
                tail_markers = ("garant", "fianza", "fiel cumplimiento", "forma de pago", "pago unico",
                                "pago único", "penalida", "por mora", "carta fianza")
                combined_tail = " ".join(c.lower() for _, c in selected)
                tail_missing = [m for m in tail_markers if m not in combined_tail]
                selected_pages_set = set(p for p, _ in selected)
                for page_no in range(anchor + block_pages, min(total_pages, anchor + tail_cap + block_pages)):
                    if not tail_missing:
                        break
                    if (page_no + 1) in selected_pages_set:
                        continue
                    if progress and page_no % 5 == 0:
                        progress("OCR local", f"Buscando garantías/pagos/penalidades, página {page_no + 1} de {total_pages}; sin Groq")
                    text_p = ocr_page_adaptive(page_no)
                    selected.append((page_no + 1, text_p))
                    selected_pages_set.add(page_no + 1)
                    combined_tail += text_p.lower()
                    tail_missing = [m for m in tail_missing if m not in combined_tail]
                selected = sorted(selected, key=lambda item: item[0])

            save_cache()
            selected_pages = [p for p, c in selected if c.strip()]
            text = "\n".join(f"--- PAGINA {page} ---\n{content}" for page, content in selected if content.strip())
            doc.close()
            return text, total_pages, selected_pages
        except Exception as exc:
            print(f"[BasesAnalyzer] Error OCR local: {exc}")
            return "", 0, []

    def extract_quick_text(self, pdf_path: str, progress=None, max_pages: int = 14) -> tuple:
        """Triaje rápido: solo las primeras páginas nativas (o patrulla OCR corta)."""
        if not pdf_path or not os.path.exists(pdf_path):
            return "", 0, []
        method = "texto nativo (triaje rápido)"
        total_pages = 0
        selected = []
        try:
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            for i in range(min(total_pages, max_pages)):
                try:
                    t = reader.pages[i].extract_text() or ""
                except Exception:
                    t = ""
                if t.strip():
                    selected.append((i + 1, t))
        except Exception as e:
            print(f"[BasesAnalyzer] Error extracción rápida {pdf_path}: {e}")
            total_pages = 0
        text = "\n".join(f"--- PAGINA {page} ---\n{content}" for page, content in selected)
        if len(text) < 200:
            if progress:
                progress("OCR local", "Triaje: PDF sin texto nativo, leyendo primeras páginas")
            text2, total_pages2, selected2 = self.extract_ocr_relevant_text(pdf_path, progress, limit_pages=8)
            if text2 and len(text2) >= 200:
                text, total_pages, selected = text2, total_pages2, selected2
                method = "OCR local (triaje rápido)"
        selected_pages = [page for page, _ in selected]
        return text, total_pages, selected_pages, method

    def _build_audit_prompt(self, text: str, meta: Dict[str, Any]) -> str:
        """Construye el prompt de auditoría de Bases (Capítulo III) para motores IA."""
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

Reglas de extracción: busca los requisitos en todos los fragmentos y conserva cifras, unidades y condiciones exactas. No respondas “No especificado” si el dato aparece en otra página; si realmente no aparece, indícalo y marca “requiere verificación”. No inventes perfiles, certificaciones ni modalidad.

TEXTO DE LAS BASES:
{doc_sample}
"""

    @staticmethod
    def _compact_relevant_text(text: str, max_chars: int = 28000) -> str:
        """Selecciona páginas asegurando cobertura por campo (experiencia, personal, garantías,
        pagos, plazos, penalidades). Corta solo en límites de página, nunca a mitad."""
        max_chars = max(max_chars, 14000)
        pages = re.split(r"(?=--- PAGINA \d+ ---)", text)
        parsed = []
        for raw in pages:
            if not raw.strip():
                continue
            m = re.match(r"--- PAGINA (\d+) ---", raw.strip())
            idx = int(m.group(1)) if m else len(parsed) + 1
            parsed.append({"idx": idx, "text": raw})
        if not parsed:
            return text[:max_chars]

        field_terms = {
            "experiencia": ("experiencia del postor", "facturaci", "veces el valor", "acumulada"),
            "personal": ("personal clave", "jefe de proyecto", "gestor del servicio", "especialista en",
                         "coordinador", "ingeniero de", "tecnico en", "técnico en"),
            "certificaciones": ("certificaci", "pmp", "itil", "iso 27001", "scrum", "ccna", "ccnp", "security+", "nse"),
            "garantia": ("garant", "fianza", "fiel cumplimiento"),
            "pago": ("forma de pago", "pago unico", "pago único", "pagos mensuales", "conformidad", "armadas", "entregable"),
            "plazo": ("plazo de ejecuci", "modalidad", "calendario", "cronograma", "en el plazo"),
            "penalidad": ("penalida", "mora", "multa")
        }

        for p in parsed:
            low = p["text"].lower()
            p["fields"] = [name for name, terms in field_terms.items() if any(t in low for t in terms)]
            p["score"] = sum(low.count(t) for terms in field_terms.values() for t in terms)

        picked = []
        # 1) Cobertura mínima: la página más densa de cada campo, aunque tenga poca puntuación global.
        for name, terms in field_terms.items():
            best = max((p for p in parsed if name in p["fields"]),
                       key=lambda p: (p["score"], -p["idx"]), default=None)
            if best and best not in picked:
                picked.append(best)
        # 2) Relleno con el resto, mayor densidad primero.
        rest = [p for p in parsed if p not in picked]
        rest.sort(key=lambda p: (p["score"], -p["idx"]), reverse=True)
        picked.extend(rest)

        chosen, used = [], 0
        for p in picked:
            remaining = max_chars - used
            if remaining <= 250:
                break
            if len(p["text"]) <= remaining:
                chosen.append(p)
                used += len(p["text"])
            elif not chosen:
                # Solo si ninguna página cabe entera, permitir corte de la primera.
                chosen.append({"idx": p["idx"], "text": p["text"][:max_chars]})
                used = max_chars
        chosen.sort(key=lambda c: c["idx"])
        return "\n".join(p["text"] for p in chosen)

    def _build_field_prompt(self, campo: str, text: str, meta: Dict[str, Any]) -> str:
        spec = FIELD_REFINE[campo]
        sample = self._compact_relevant_text(text, 10000)
        return f"""Eres un auditor experto de contrataciones públicas del Perú (SEACE/OECE) para la empresa TxDx.
Título/Proceso: {meta.get('titulo', '')}
Entidad: {meta.get('entidad', '')}

Tarea: {spec['instruction']}

Reglas: responde EXCLUSIVAMENTE con un JSON válido. Usa como máximo estas claves exactas: {spec['keys']}.
Si el dato realmente no aparece en el texto, usa null para esa clave. No inventes valores.

TEXTO DE LAS BASES:
{sample}
"""

    def _groq_json_call(self, api_key: str, prompt: str, model_name: str, max_tokens: int = 1024) -> Optional[Dict[str, Any]]:
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "Eres un auditor experto de contrataciones públicas del Perú (SEACE/OECE). Solo respondes con JSON válido, sin texto adicional."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}
        }
        for attempt in range(3):
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    endpoint,
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }
                )
                with _LLM_SEMAPHORE:
                    with urllib.request.urlopen(req, timeout=90, context=self.ctx) as resp:
                        if resp.status == 200:
                            res_json = json.loads(resp.read().decode("utf-8"))
                            usage_o = (res_json.get("usage") or {})
                            p_tok = int(usage_o.get("prompt_tokens") or 0)
                            c_tok = int(usage_o.get("completion_tokens") or 0)
                            total_tok = int(usage_o.get("total_tokens") or (p_tok + c_tok)) or 0
                            self._usage_total += total_tok
                            record_usage("groq", model_name, p_tok, c_tok,
                                         self._usage_ocid, self._usage_modo)
                            choices = res_json.get("choices", [])
                            if choices:
                                raw_reply = choices[0].get("message", {}).get("content", "")
                                clean_reply = re.sub(r'^```json\s*', '', raw_reply.strip())
                                clean_reply = re.sub(r'\s*```$', '', clean_reply)
                                return json.loads(clean_reply)
                            return None
            except urllib.error.HTTPError as he:
                if he.code == 429:
                    time.sleep(5 + attempt * 5 + random.uniform(0, 3))
                    continue
                print(f"[BasesAnalyzer] Groq {model_name} falló: {he}")
                return None
            except Exception as e:
                print(f"[BasesAnalyzer] Groq {model_name} falló: {e}")
                return None
        return None

    def _gemini_json_call(self, api_key: str, prompt: str, model_name: str = "gemini-2.5-flash", max_tokens: int = 1024) -> Optional[Dict[str, Any]]:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "maxOutputTokens": max_tokens
            }
        }
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            with _LLM_SEMAPHORE:
                with urllib.request.urlopen(req, timeout=30, context=self.ctx) as resp:
                    if resp.status == 200:
                        res_json = json.loads(resp.read().decode("utf-8"))
                        usage_o = (res_json.get("usageMetadata") or {})
                        p_tok = int(usage_o.get("promptTokenCount") or 0)
                        c_tok = int(usage_o.get("candidatesTokenCount") or 0)
                        total_tok = int(usage_o.get("totalTokenCount") or (p_tok + c_tok)) or 0
                        self._usage_total += total_tok
                        record_usage("gemini", model_name, p_tok, c_tok,
                                     self._usage_ocid, self._usage_modo)
                        candidates = res_json.get("candidates", [])
                        if candidates:
                            raw_reply = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            clean_reply = re.sub(r'^```json\s*', '', raw_reply.strip())
                            clean_reply = re.sub(r'\s*```$', '', clean_reply)
                            return json.loads(clean_reply)
        except Exception as e:
            print(f"[BasesAnalyzer] Gemini {model_name} falló: {e}")
        return None

    def analyze_with_ai(self, text: str, meta: Dict[str, Any], api_key: str, max_sample: int = 28000) -> Optional[Dict[str, Any]]:
        """Usa OpenAI-compatible API (Groq) para analizar los requisitos con precisión IA."""
        models_to_try = [
            "qwen/qwen3.8-27b"
        ]
        # max_output de qwen = 16384; muestras > 20k chars provocan 413 en JSON mode.
        model_caps = {"qwen/qwen3.8-27b": 20000}
        mid = max(14000, int(max_sample * 0.7))
        for model_name in models_to_try:
            cap = min(max_sample, model_caps.get(model_name, max_sample))
            rungs = tuple(sorted(set((cap, min(mid, cap), min(14000, cap))), reverse=True))
            for sample_len in rungs:
                prompt = self._build_audit_prompt(self._compact_relevant_text(text, sample_len), meta)
                parsed = self._groq_json_call(api_key, prompt, model_name, max_tokens=4096)
                if parsed:
                    parsed["motor"] = f"Groq IA ({model_name})"
                    return parsed
        return None

    def analyze_with_gemini(self, text: str, meta: Dict[str, Any], api_key: str) -> Optional[Dict[str, Any]]:
        """Usa Google Gemini Flash para analizar el requerimiento."""
        text = self._compact_relevant_text(text)
        prompt = self._build_audit_prompt(text, meta)
        models_to_try = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash"
        ]
        for model_name in models_to_try:
            parsed = self._gemini_json_call(api_key, prompt, model_name, max_tokens=4096)
            if parsed:
                parsed["motor"] = f"Gemini IA ({model_name})"
                return parsed
        return None

    def _local_analysis(self, text: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        experiencia = self._extract_experiencia_postor(text)
        personal = self._extract_personal_clave(text)
        certs = self._extract_certificaciones(text)
        plazo_modalidad = self._extract_plazo_modalidad(text)
        pago = self._extract_forma_pago(text)
        garantia = self._extract_garantia(text)

        exp_conf = bool(experiencia) and not experiencia.startswith("Acreditar experiencia en servicios similares")
        pers_conf = bool(personal) and personal != [_DEF_PERSONAL]
        cert_conf = bool(certs)
        plazo = plazo_modalidad.get("plazo", _DEF_PLAZO)
        plazo_conf = plazo not in (_DEF_PLAZO, _DEF_PLAZO_V2)
        modalidad = plazo_modalidad.get("modalidad", _DEF_MODALIDAD)
        modalidad_conf = modalidad != _DEF_MODALIDAD
        pago_conf = bool(pago) and not pago.startswith(_DEF_PAGO) and "conformidad del área usuaria" not in pago.lower()
        gar_conf = bool(garantia) and "verificar" not in garantia.lower() and garantia != _DEF_GARANTIA

        confianza = {
            "experiencia_postor": exp_conf,
            "personal_clave": pers_conf,
            "certificaciones_requeridas": cert_conf,
            "plazo_ejecucion": plazo_conf,
            "modalidad": modalidad_conf,
            "forma_pago": pago_conf,
            "garantia": gar_conf,
        }
        factibilidad = self._evaluate_factibilidad(
            meta.get("linea_servicio", ""), certs, experiencia, personal, garantia)

        return {
            "experiencia_postor": experiencia,
            "personal_clave": personal,
            "certificaciones_requeridas": certs,
            "plazo_ejecucion": plazo,
            "modalidad": modalidad,
            "forma_pago": pago,
            "garantia": garantia,
            "factibilidad_txdx": factibilidad,
            "_confianza": confianza,
            "completa": all(confianza.values()),
        }

    @staticmethod
    def _field_is_empty(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, list):
            return len(val) == 0
        if isinstance(val, str):
            v = val.strip().lower()
            if not v:
                return True
            empty_phrases = ("no especificado", "no especificada", "por verificar", "requiere verificación",
                             "no se especific", "no identificado", "no identificada")
            return any(p in v for p in empty_phrases)
        return False

    def _merge_ai_with_local(self, ai_res: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
        keys = ["experiencia_postor", "personal_clave", "certificaciones_requeridas",
                "plazo_ejecucion", "modalidad", "forma_pago", "garantia"]
        fuente = dict(ai_res.get("fuente_campos") or {})
        conf = local["_confianza"]
        for k in keys:
            ai_val = ai_res.get(k)
            lv = local.get(k)
            if self._field_is_empty(ai_val) and conf.get(k):
                ai_res[k] = lv
                fuente[k] = "extractor local"
            elif self._field_is_empty(ai_val):
                if not self._field_is_empty(lv):
                    ai_res[k] = lv
                    fuente[k] = "extractor local"
            else:
                fuente.setdefault(k, "IA")
                if isinstance(ai_val, str) and "no especific" in ai_val.lower() and conf.get(k):
                    ai_res[k] = lv
                    fuente[k] = "extractor local"
        fact = ai_res.get("factibilidad_txdx")
        if not isinstance(fact, dict) or not fact.get("nivel"):
            ai_res["factibilidad_txdx"] = local.get("factibilidad_txdx")
            fuente["factibilidad_txdx"] = "extractor local"
        elif not fact.get("color") or not fact.get("mensaje"):
            lf = local.get("factibilidad_txdx") or {}
            fact.setdefault("color", lf.get("color"))
            fact.setdefault("mensaje", lf.get("mensaje"))
            fact.setdefault("alertas", lf.get("alertas") or [])
        ai_res["fuente_campos"] = fuente
        return ai_res

    def _refine_missing_fields(self, ai_res: Dict[str, Any], raw_text: str, meta: Dict[str, Any],
                               groq_key: Optional[str], gemini_key: Optional[str]) -> Dict[str, Any]:
        missing = []
        for campo, spec in FIELD_REFINE.items():
            if any(self._field_is_empty(ai_res.get(k)) for k in spec["keys"]):
                missing.append(campo)
        if not missing:
            return ai_res
        fuente = dict(ai_res.get("fuente_campos") or {})
        for campo in missing:
            prompt = self._build_field_prompt(campo, raw_text, meta)
            parsed = None
            if groq_key:
                parsed = self._groq_json_call(groq_key, prompt, "qwen/qwen3.8-27b", max_tokens=700)
            if parsed is None and gemini_key:
                parsed = self._gemini_json_call(gemini_key, prompt, "gemini-2.5-flash", max_tokens=700)
            if isinstance(parsed, dict):
                for k in FIELD_REFINE[campo]["keys"]:
                    v = parsed.get(k)
                    if not self._field_is_empty(v):
                        ai_res[k] = v
                        fuente[k] = "IA (refinamiento por campo)"
        ai_res["fuente_campos"] = fuente
        if "motor" in ai_res and missing:
            ai_res["motor"] += " + refinado por campo"
        return ai_res

    def analyze_document(self, ocid: str, url_bases: str, meta: Optional[Dict[str, Any]] = None,
                         progress=None, modo: str = "completo") -> Dict[str, Any]:
        """Orquesta la descarga y el análisis. modo='rapido' hace triaje barato; 'completo' es exhaustivo."""
        modo = modo if modo in ("rapido", "completo") else "completo"
        meta = meta or {}
        self._usage_total = 0
        self._usage_ocid = ocid
        self._usage_modo = modo
        pdf_path = self.download_pdf(ocid, url_bases, progress)

        if not pdf_path:
            return {
                "success": False,
                "error": "No se pudo descargar el archivo de Bases oficial desde SEACE (documento no accesible o formato no legible).",
                "ocid": ocid
            }

        # Bases entregadas como documento Office (docx/xlsx): texto ya extraído al descargar.
        if pdf_path.lower().endswith(".txt"):
            with open(pdf_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            total_pages, selected_pages = 0, [1]
            extraction_method = "documento Office (texto extraído del archivo original)"
        elif modo == "rapido":
            raw_text, total_pages, selected_pages, extraction_method = self.extract_quick_text(pdf_path, progress)
        else:
            raw_text, total_pages, selected_pages = self.extract_relevant_text(pdf_path, progress)
            extraction_method = "texto nativo"
            if not raw_text or len(raw_text) < 200:
                raw_text, total_pages, selected_pages = self.extract_ocr_relevant_text(pdf_path, progress)
                extraction_method = "OCR local" if raw_text else extraction_method

        if len(raw_text) < 200:
            raw_text, total_pages, selected_pages, extraction_method = self.extract_quick_text(pdf_path, progress)
            if len(raw_text) < 200:
                return {
                    "success": False,
                    "error": "El documento no contiene texto utilizable y el OCR local no pudo extraerlo. Abre el PDF oficial para revisarlo.",
                    "ocid": ocid,
                    "pdf_local": pdf_path
                }

        evidence = {
            "paginas_revisadas": selected_pages,
            "metodo_extraccion": extraction_method,
            "nota": "Estas páginas se seleccionaron por contener el capítulo técnico o requisitos de calificación. Verifica el PDF oficial antes de postular."
        }
        if progress:
            progress("Analizando requisitos", f"Enviando {len(selected_pages)} páginas relevantes al motor IA")

        groq_key = get_groq_key()
        gemini_key = get_gemini_key()
        local = self._local_analysis(raw_text, meta)
        result = None

        # Triaje rápido: si el extractor local cubre todo, no gastar ninguna llamada IA.
        if modo == "rapido" and local["completa"]:
            result = self._build_local_result(ocid, pdf_path, total_pages, local, evidence,
                                              "Extractor Local (triaje rápido, sin IA)")
            result["sugerencia"] = "El triaje cubrió todos los campos. Ejecuta 'Análisis completo' si necesitas precisión IA. No especificado: confirma montos en el PDF."

        if result is None:
            ai_res = None
            if groq_key:
                try:
                    ai_res = self.analyze_with_ai(raw_text, meta, groq_key, max_sample=12000 if modo == "rapido" else 28000)
                except Exception as ge:
                    print(f"[BasesAnalyzer] Error con Groq AI: {ge}")
            if ai_res is None and gemini_key:
                try:
                    ai_res = self.analyze_with_gemini(raw_text, meta, gemini_key)
                except Exception as ge:
                    print(f"[BasesAnalyzer] Error con Gemini AI: {ge}")

            if ai_res:
                ai_res = self._merge_ai_with_local(ai_res, local)
                ai_res = self._refine_missing_fields(ai_res, raw_text, meta, groq_key, gemini_key)
                ai_res = self._merge_ai_with_local(ai_res, local)
                ai_res.setdefault("garantia", "Por verificar en Bases")
                ai_res["success"] = True
                ai_res["ocid"] = ocid
                ai_res["pdf_local"] = pdf_path
                ai_res["total_paginas"] = total_pages
                ai_res["evidencia"] = evidence
                if modo == "rapido":
                    ai_res["sugerencia"] = "Triaje rápido. Ejecuta 'Análisis completo' para revisión exhaustiva de garantías y anexos."
                result = ai_res

        if result is None:
            result = self._build_local_result(ocid, pdf_path, total_pages, local, evidence,
                                              "Extractor Local (Configura tu Groq API Key gratuita para precisión IA)")

        result["modo"] = modo
        result["usage_tokens_total"] = int(self._usage_total)
        return result

    def _build_local_result(self, ocid, pdf_path, total_pages, local, evidence, motor_label) -> Dict[str, Any]:
        fact = dict(local.get("factibilidad_txdx") or {})
        fact.setdefault("nivel", self._evaluate_factibilidad("", [], "", []).get("nivel", "EVALUAR CON CUIDADO"))
        return {
            "success": True,
            "ocid": ocid,
            "pdf_local": pdf_path,
            "motor": motor_label,
            "experiencia_postor": local.get("experiencia_postor", _DEF_EXPERIENCIA),
            "personal_clave": local.get("personal_clave", [_DEF_PERSONAL]),
            "certificaciones_requeridas": local.get("certificaciones_requeridas", []),
            "plazo_ejecucion": local.get("plazo_ejecucion", _DEF_PLAZO_V2),
            "modalidad": local.get("modalidad", _DEF_MODALIDAD),
            "forma_pago": local.get("forma_pago", _DEF_PAGO),
            "garantia": local.get("garantia", _DEF_GARANTIA),
            "factibilidad_txdx": fact,
            "total_paginas": total_pages,
            "evidencia": evidence,
            "fuente_campos": {"todos": "extractor local"},
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