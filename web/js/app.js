/**
 * TxDx RADAR OECE — CLIENT LOGIC & UI ENGINE
 * Soporta: Vista Dual (Tarjetas / Tabla Densa), Copiado Robusto con Fallback,
 * Prioridad/Ventana/Etapa TxDx, Ordenamiento Dinámico y Atajos de Teclado.
 */

let currentLine = 'ALL';
let searchTimeout = null;
let currentOps = [];
let filteredOps = [];
let currentView = 'table'; // Bandeja de decisión por defecto; tarjetas solo para exploración.
let currentAnalisisData = null;
let currentAnalisisOcid = null;
let scanPollTimer = null;
let scanPollBusy = false;
let scanWasActive = false;
let lastScanPages = -1;

// ==========================================================================
// INICIALIZACIÓN & EVENTOS GLOBALES
// ==========================================================================
window.addEventListener('DOMContentLoaded', () => {
  initKeyboardShortcuts();
  pollScanStatus();
  fetchStats();
  loadOpportunities();
});

function initKeyboardShortcuts() {
  window.addEventListener('keydown', (e) => {
    if ((e.key === '/' || (e.ctrlKey && e.key === 'k')) && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      const searchInput = document.getElementById('searchInput');
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
    }
    if (e.key === 'Escape') {
      closeAnalisisDrawer();
      closeAIConfigModal();
    }
  });
}

// ==========================================================================
// TOAST NOTIFICATION HUB
// ==========================================================================
function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  const iconSvg = type === 'success'
    ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>`
    : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ff6600" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;

  toast.innerHTML = `${iconSvg} <span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 250);
  }, 2600);
}

// ==========================================================================
// COPIADO ROBUSTO AL PORTAPAPELES (CON FALLBACK UNIVERSAL)
// ==========================================================================
function copyToClipboardFallback(text) {
  return new Promise((resolve, reject) => {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(resolve).catch(() => {
        fallbackWithTextarea(text, resolve, reject);
      });
    } else {
      fallbackWithTextarea(text, resolve, reject);
    }
  });
}

function fallbackWithTextarea(text, resolve, reject) {
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    textArea.setAttribute('readonly', '');
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);
    if (successful) resolve();
    else reject(new Error('execCommand copy fallo'));
  } catch (err) {
    reject(err);
  }
}

function copiarTextoDirecto(btn, encodedText, labelSuccess = '¡Copiado!') {
  const text = decodeURIComponent(encodedText);
  copyToClipboardFallback(text).then(() => {
    if (btn) {
      const origHtml = btn.innerHTML;
      btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg> ${labelSuccess}`;
      btn.style.backgroundColor = '#ecfdf5';
      btn.style.color = '#059669';
      btn.style.borderColor = '#a7f3d0';
      setTimeout(() => {
        btn.innerHTML = origHtml;
        btn.style.backgroundColor = '';
        btn.style.color = '';
        btn.style.borderColor = '';
      }, 2000);
    }
    showToast(`Copiado: "${text.substring(0, 35)}${text.length > 35 ? '...' : ''}"`, 'success');
  }).catch(() => {
    showToast('No se pudo copiar automáticamente al portapapeles', 'info');
  });
}

// ==========================================================================
// MÉTRICAS Y STATS
// ==========================================================================
async function fetchStats() {
  try {
    const res = await fetch('/api/stats');
    const data = await res.json();

    const statTotal = document.getElementById('statTotal');
    const statPipeline = document.getElementById('statPipeline');
    const statTech = document.getElementById('statTech');
    const statData = document.getElementById('statData');

    if (statTotal) statTotal.innerText = data.total_oportunidades || 0;
    if (statPipeline) {
      statPipeline.innerText = `S/ ${(data.pipeline_monto_pen || 0).toLocaleString('es-PE', { minimumFractionDigits: 2 })}`;
    }

    const ciber = (data.por_linea?.CIBERSEGURIDAD || 0);
    const net = (data.por_linea?.NETWORKING || 0);
    if (statTech) statTech.innerText = `${ciber + net} proc.`;

    const ia = (data.por_linea?.IA_AUTOMATIZACION || 0);
    const datos = (data.por_linea?.GESTION_DATOS || 0);
    if (statData) statData.innerText = `${ia + datos} proc.`;

    const am = data.analisis_metrics || {};
    const metricsEl = document.getElementById('analisisMetricsText');
    if (metricsEl) {
      const pm = am.por_modo || {};
      const conc = am.concordancia_triaje || {};
      const hasConc = conc.total ? `· Concordancia triaje↔completo: ${conc.porcentaje ?? '—'}% (${conc.coinciden}/${conc.total})` : '· Sin comparación triaje↔completo aún';
      const tokens = am.total_tokens ? `${(am.total_tokens / 1000).toFixed(1)}k tokens` : '0 tokens';

      const u = data.usage || {};
      const uTotal = u.total ? (u.total.total || 0) : 0;
      const uHoy = u.hoy ? (u.hoy.total || 0) : 0;
      const uCalls = u.total ? (u.total.llamadas || 0) : 0;
      const usageTxt = uTotal
        ? `· Tokens LLM (global): <strong>${(uTotal / 1000).toFixed(1)}k</strong> (${uCalls} llamadas) · hoy <strong>${(uHoy / 1000).toFixed(1)}k</strong> (límite free ≈200k/día)`
        : '· Sin consumo de tokens aún';

      metricsEl.innerHTML = `Análisis de bases: <strong>${tokens}</strong> usados · ${pm.rapido || 0} triaje / ${pm.completo || 0} completo ${hasConc} ${usageTxt}`;
    }

  } catch (e) {
    console.error('Error al obtener estadísticas:', e);
  }
}

// ==========================================================================
// CARGA DE OPORTUNIDADES Y FILTRADO
// ==========================================================================
async function loadOpportunities() {
  const searchInput = document.getElementById('searchInput');
  const statusFilter = document.getElementById('statusFilter');
  const scoreFilter = document.getElementById('scoreFilter');
  const soloVigentes = document.getElementById('soloVigentes');

  const search = searchInput ? searchInput.value.trim() : '';
  const estado = statusFilter ? statusFilter.value : 'ALL';
  const minScore = scoreFilter ? scoreFilter.value : '0';
  const isVigentes = soloVigentes ? soloVigentes.checked : true;

  const params = new URLSearchParams();
  if (currentLine !== 'ALL') params.append('linea_servicio', currentLine);
  if (estado !== 'ALL') params.append('estado_interno', estado);
  if (Number(minScore) > 0) params.append('min_score', minScore);
  if (search) params.append('search', search);

  if (isVigentes) {
    params.append('solo_vigentes', 'true');
  } else {
    params.append('solo_vigentes', 'false');
  }

  try {
    const res = await fetch(`/api/oportunidades?${params.toString()}`);
    const ops = await res.json();
    currentOps = Array.isArray(ops) ? ops : [];
    updateLinePillsCounts(currentOps);
    applyWindowFilter();
  } catch (e) {
    console.error('Error cargando oportunidades:', e);
  }
}

function updateLinePillsCounts(ops) {
  const counts = {
    ALL: ops.length,
    CIBERSEGURIDAD: 0,
    NETWORKING: 0,
    IA_AUTOMATIZACION: 0,
    GESTION_DATOS: 0
  };

  ops.forEach(op => {
    if (counts[op.linea_servicio] !== undefined) {
      counts[op.linea_servicio]++;
    }
  });

  Object.keys(counts).forEach(key => {
    const el = document.getElementById(`count-${key}`);
    if (el) el.innerText = counts[key];
  });
}

function applyWindowFilter() {
  const mode = document.getElementById('windowFilter')?.value || 'ALL';
  let filtered = currentOps;

  if (mode === 'SOON') {
    filtered = currentOps.filter(o => o.dias_restantes !== null && o.es_elegible && o.dias_restantes <= 5);
  } else if (mode === 'OPEN') {
    filtered = currentOps.filter(o => o.dias_restantes !== null && o.es_elegible && o.dias_restantes > 5);
  }

  filteredOps = filtered;
  applySorting();
}

function applySorting() {
  const sortMode = document.getElementById('sortFilter')?.value || 'MATCH_DESC';
  let sorted = [...filteredOps];

  if (sortMode === 'MATCH_DESC') {
    sorted.sort((a, b) => (b.score || 0) - (a.score || 0));
  } else if (sortMode === 'DAYS_ASC') {
    sorted.sort((a, b) => {
      const da = a.dias_restantes !== null ? a.dias_restantes : 9999;
      const db = b.dias_restantes !== null ? b.dias_restantes : 9999;
      return da - db;
    });
  } else if (sortMode === 'AMOUNT_DESC') {
    sorted.sort((a, b) => (Number(b.monto_referencial) || 0) - (Number(a.monto_referencial) || 0));
  } else if (sortMode === 'PUB_DESC') {
    sorted.sort((a, b) => new Date(b.fecha_publicacion || 0) - new Date(a.fecha_publicacion || 0));
  }

  renderOpportunities(sorted);
}

// ==========================================================================
// SELECTOR DE VISTAS (TARJETAS VS TABLA DENSA)
// ==========================================================================
function setViewMode(mode) {
  currentView = mode;
  const btnGrid = document.getElementById('btnViewGrid');
  const btnTable = document.getElementById('btnViewTable');

  if (mode === 'grid') {
    btnGrid?.classList.add('active');
    btnTable?.classList.remove('active');
  } else {
    btnTable?.classList.add('active');
    btnGrid?.classList.remove('active');
  }

  applySorting();
}

// ==========================================================================
// RENDERIZADO PRINCIPAL
// ==========================================================================
function renderOpportunities(ops) {
  const container = document.getElementById('opportunitiesContainer');
  const countEl = document.getElementById('visibleCount');
  if (countEl) countEl.innerText = ops.length;

  if (!ops || ops.length === 0) {
    const soloVig = document.getElementById('soloVigentes')?.checked;
    container.innerHTML = `
      <div class="empty-results-box" style="grid-column: 1 / -1;">
        <div class="empty-icon-circle">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </div>
        <h3>No se encontraron convocatorias coincidentes</h3>
        <p>${soloVig ? 'Prueba desactivando la casilla "Disponibles con al menos 48 horas" para explorar convocatorias cerradas o presiona "Escanear Portal".' : 'Ajusta los filtros de búsqueda o presiona "Escanear Portal" para sincronizar convocatorias recientes de OECE.'}</p>
      </div>
    `;
    return;
  }

  if (currentView === 'table') {
    renderDenseTable(ops, container);
  } else {
    renderCardGrid(ops, container);
  }
}

// Helper para dar formato legible a títulos en mayúsculas sin romper acrónimos clave
function formatCardTitle(str) {
  if (!str) return '';
  const acronyms = new Set([
    'SUNAT', 'ESSALUD', 'RPA', 'TI', 'AEC', 'CUBSO', 'BM', 'UNSA', 
    'OSCE', 'OECE', 'CP', 'AS', 'LP', 'PE', 'N°', 'DTI', 'MINSA', 
    'MINEDU', 'MEF', 'PBI', 'ISO', 'SOC', 'SIEM', 'EDR', 'XDR', 
    'WAF', 'VPN', 'DDoS', 'FORTINET', 'CISCO', 'PALO ALTO', 'AWS', 
    'AZURE', 'GCP', 'IA', 'API', 'SQL', 'SAN', 'NAS', 'IP'
  ]);
  const words = str.split(/\s+/);
  return words.map((word, idx) => {
    const clean = word.replace(/^[(\[{"'«]+|[)\]}"'»,.:;]+$/g, '').toUpperCase();
    if (acronyms.has(clean)) return word.toUpperCase();
    if (word.includes('-') && /\d/.test(word)) return word.toUpperCase();
    const lower = word.toLowerCase();
    if (idx === 0) return lower.charAt(0).toUpperCase() + lower.slice(1);
    const prev = words[idx - 1];
    if (prev && (prev.endsWith(':') || prev.endsWith('.') || prev === '-' || prev === '–')) {
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    }
    return lower;
  }).join(' ');
}

// --- Vista 1: Cuadrícula de Tarjetas Ricas (Rediseño Limpio y Ejecutivo) ---
function renderCardGrid(ops, container) {
  container.className = 'opportunities-display-grid';
  container.innerHTML = ops.map(op => {
    const monto = Number(op.monto_referencial || 0);
    const montoStr = monto > 0
      ? `S/ ${monto.toLocaleString('es-PE', { minimumFractionDigits: 2 })}`
      : 'Por cotizar';

    const pubDate = op.fecha_publicacion ? new Date(op.fecha_publicacion).toLocaleDateString('es-PE') : 'Reciente';
    const fechaConsultas = op.fecha_cierre_consultas ? new Date(op.fecha_cierre_consultas).toLocaleDateString('es-PE') : null;
    const fechaPropuestas = op.fecha_cierre_propuestas ? new Date(op.fecha_cierre_propuestas).toLocaleDateString('es-PE') : null;
    const cierreDate = fechaConsultas || fechaPropuestas || 'Ver portal';
    const labelCierre = fechaConsultas ? 'Fin Consultas' : 'Cierre';

    let diasBadge = '';
    if (!op.es_elegible) {
      if (op.dias_restantes !== null && op.dias_restantes < 0) {
        diasBadge = `<span class="dias-badge vencido">Consultas cerradas</span>`;
      } else {
        diasBadge = `<span class="dias-badge vencido">${op.motivo_vigencia || 'Plazo cerrado'}</span>`;
      }
    } else if (op.dias_restantes !== null && op.dias_restantes !== undefined) {
      if (op.dias_restantes <= 3) {
        diasBadge = `<span class="dias-badge urgente">⏳ Quedan ${op.dias_restantes}d</span>`;
      } else if (op.dias_restantes <= 7) {
        diasBadge = `<span class="dias-badge proximo">⏳ Quedan ${op.dias_restantes}d</span>`;
      } else {
        diasBadge = `<span class="dias-badge abierto">Abierto ${op.dias_restantes}d</span>`;
      }
    }

    // Título descriptivo real y formato legible
    const rawTitle = op.descripcion || op.titulo;
    const formattedTitle = formatCardTitle(rawTitle);
    const nomenclaturaCode = op.titulo || op.ocid;

    // Término de búsqueda para SEACE (sin cortes en el copiado)
    const objetoBusqueda = op.seace_guide?.objeto_sugerido || op.descripcion || op.titulo;
    const encodedBusqueda = encodeURIComponent(objetoBusqueda);

    // Prioridad y Línea
    const prioHtml = (op.prioridad && op.prioridad !== 'BAJA')
      ? `<span class="prio-tag ${op.prioridad.toLowerCase()}">${op.prioridad === 'ALTA' ? 'Alta' : 'Media'}</span>`
      : '';
    const lineaName = (op.linea_servicio || 'GENERAL').replace(/_/g, ' ');

    // Producto destacado (máximo 1 chip sutil si aplica)
    const topProduct = (op.matched_products && op.matched_products.length > 0)
      ? `<span class="chip-top-product" title="Producto TxDx compatible">${op.matched_products[0]}</span>`
      : '';

    return `
      <article class="op-card" data-ocid="${op.ocid}">
        <!-- Fila 1: Meta y Monto -->
        <div class="op-card-header">
          <div class="op-meta-left">
            <span class="score-badge ${op.score >= 70 ? 'high' : ''}">${op.score}% MATCH</span>
            <span class="line-badge ${op.linea_servicio}">${lineaName}</span>
            ${topProduct}
            ${prioHtml}
          </div>
          <div class="op-amount ${monto === 0 ? 'unpriced' : ''}" title="Monto Referencial">
            ${montoStr}
          </div>
        </div>

        <!-- Fila 2: Título y Entidad -->
        <div class="op-card-body">
          <h3 class="op-title" title="${rawTitle}">
            ${formattedTitle}
          </h3>
          <div class="op-meta-sub">
            <span class="entity-name" title="${op.entidad}">${op.entidad}</span>
            <span class="meta-dot">&bull;</span>
            <span class="nomenclatura-code" title="${nomenclaturaCode}">${nomenclaturaCode}</span>
            ${op.departamento ? `<span class="meta-dot">&bull;</span><span class="entity-dept">${op.departamento}</span>` : ''}
          </div>
        </div>

        <!-- Fila 3: Señales de Tiempo y Búsqueda SEACE Rápida -->
        <div class="op-signals-bar">
          <div class="op-deadline-info">
            ${diasBadge}
            <span class="deadline-date">${labelCierre}: <strong>${cierreDate}</strong></span>
          </div>
          <div class="op-seace-actions">
            <button class="btn-seace-quick" onclick="copiarTextoDirecto(this, '${encodedBusqueda}')" title="Copiar término exacto para buscar en SEACE">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
              <span>Copiar para SEACE</span>
            </button>
            <a href="${op.seace_guide?.url_buscador || 'https://prod1.seace.gob.pe/'}" target="_blank" class="btn-seace-link" title="Abrir portal SEACE">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
            </a>
          </div>
        </div>

        <!-- Fila 4: Footer de Acciones y Pipeline -->
        <div class="op-card-footer">
          <select class="status-select-pill ${op.estado_interno}" onchange="changeStatus('${op.ocid}', this.value, this)">
            <option value="POR_EVALUAR" ${op.estado_interno === 'POR_EVALUAR' ? 'selected' : ''}>Por Evaluar</option>
            <option value="INTERESANTE" ${op.estado_interno === 'INTERESANTE' ? 'selected' : ''}>Interesante</option>
            <option value="EN_PREPARACION" ${op.estado_interno === 'EN_PREPARACION' ? 'selected' : ''}>En Preparación</option>
            <option value="POSTULADO" ${op.estado_interno === 'POSTULADO' ? 'selected' : ''}>Postulado</option>
            <option value="DESCARTADO" ${op.estado_interno === 'DESCARTADO' ? 'selected' : ''}>Descartado</option>
          </select>

          <div class="op-footer-actions">
            ${op.url_bases ? `
              <button class="btn-action-analisis ${op.analisis_bases ? 'analyzed' : ''}" onclick="openAnalisisDrawer('${op.ocid}', '${encodeURIComponent(rawTitle)}', '${encodeURIComponent(op.entidad)}', '${op.linea_servicio}', '${op.url_bases}', ${op.score})" title="Analizar pliegos y bases con IA">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
                ${op.analisis_bases ? 'Ver Ficha' : 'Analizar'}
              </button>
              <a href="${op.url_bases}" target="_blank" class="btn-action-bases" title="Descargar PDF de Bases Oficiales">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                Bases
              </a>
            ` : ''}
            <a href="${op.url_oece || '#'}" target="_blank" class="btn-action-oece" title="Ficha en portal oficial OECE">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
            </a>
          </div>
        </div>
      </article>
    `;
  }).join('');
}

// --- Vista 2: Tabla Densa de Operador ---
function renderDenseTable(ops, container) {
  container.className = 'table-dense-container';
  container.innerHTML = `
    <div class="table-dense-scroll">
      <table class="dense-table">
        <thead>
          <tr>
            <th>Match</th>
            <th>Línea</th>
            <th>Convocatoria & Entidad</th>
            <th>Prioridad</th>
            <th>Monto Referencial</th>
            <th>Fin Consultas / Cierre</th>
            <th>Estado Interno</th>
            <th style="text-align: right;">Acciones</th>
          </tr>
        </thead>
        <tbody>
          ${ops.map(op => {
            const monto = Number(op.monto_referencial || 0);
            const montoStr = monto > 0
              ? `S/ ${monto.toLocaleString('es-PE', { minimumFractionDigits: 2 })}`
              : 'Por cotizar';

            const displayTitle = op.descripcion || op.titulo;
            const fechaConsultas = op.fecha_cierre_consultas ? new Date(op.fecha_cierre_consultas).toLocaleDateString('es-PE') : null;
            const cierreDate = fechaConsultas || (op.fecha_cierre_propuestas ? new Date(op.fecha_cierre_propuestas).toLocaleDateString('es-PE') : '-');

            let diasLabel = op.es_elegible ? '' : `<span class="dias-badge vencido">${op.motivo_vigencia || 'Plazo por verificar'}</span>`;
            if (op.dias_restantes !== null && op.dias_restantes !== undefined) {
              if (!op.es_elegible) diasLabel = `<span style="color: var(--text-dim);">${op.motivo_vigencia || 'No disponible'}</span>`;
              else if (op.dias_restantes <= 5) diasLabel = `<span style="color: #d97706; font-weight: 700;">${op.dias_restantes}d rest.</span>`;
              else diasLabel = `<span style="color: #059669;">${op.dias_restantes}d</span>`;
            }

            const prioHtml = op.prioridad ? `<span class="prio-badge ${op.prioridad}">${op.prioridad}</span>` : '-';
            const encodedBusqueda = encodeURIComponent(op.seace_guide?.objeto_sugerido || op.descripcion || op.titulo);

            return `
              <tr>
                <td>
                  <span class="score-badge ${op.score >= 70 ? 'high' : ''}">${op.score}%</span>
                </td>
                <td>
                  <span class="line-badge ${op.linea_servicio}">${op.linea_servicio.replace('_', ' ')}</span>
                </td>
                <td class="table-cell-title">
                  <div class="table-title-text" title="${displayTitle}">${displayTitle}</div>
                  <div class="table-entity-meta">
                    <strong>${op.entidad}</strong>
                    <span>&bull; ${op.departamento || 'Nacional'}</span>
                    <span class="table-id">${op.titulo}</span>
                  </div>
                </td>
                <td>
                  ${prioHtml}
                </td>
                <td>
                  <div class="table-amount">${montoStr}</div>
                  <div style="font-size: 10px; color: ${monto < 1000000 ? '#059669' : '#dc2626'};">
                    ${monto < 1000000 ? 'En regla TxDx' : 'Excede regla'}
                  </div>
                </td>
                <td>
                  <div class="table-date">${cierreDate}</div>
                  <div style="font-size: 11px;">${diasLabel}</div>
                </td>
                <td>
                  <select class="status-select-pill ${op.estado_interno}" onchange="changeStatus('${op.ocid}', this.value, this)">
                    <option value="POR_EVALUAR" ${op.estado_interno === 'POR_EVALUAR' ? 'selected' : ''}>Por Evaluar</option>
                    <option value="INTERESANTE" ${op.estado_interno === 'INTERESANTE' ? 'selected' : ''}>Interesante</option>
                    <option value="EN_PREPARACION" ${op.estado_interno === 'EN_PREPARACION' ? 'selected' : ''}>En Prep.</option>
                    <option value="POSTULADO" ${op.estado_interno === 'POSTULADO' ? 'selected' : ''}>Postulado</option>
                    <option value="DESCARTADO" ${op.estado_interno === 'DESCARTADO' ? 'selected' : ''}>Descartado</option>
                  </select>
                </td>
                <td style="text-align: right;">
                  <div class="table-actions-cluster" style="justify-content: flex-end;">
                    <a href="${op.seace_guide?.url_buscador || 'https://prod1.seace.gob.pe/'}" target="_blank" class="btn-mini-copy" title="Abrir SEACE para verificar el proceso">SEACE</a>
                    <button class="btn-mini-copy ${op.seace_confirmado ? 'confirmed' : ''}" onclick="confirmarSEACE('${op.ocid}', ${!op.seace_confirmado}, this)" title="Registra una verificación manual del expediente en SEACE">
                      ${op.seace_confirmado ? 'Confirmado' : 'Confirmar'}
                    </button>
                    ${op.url_bases ? `
                      <button class="btn-action-analisis ${op.analisis_bases ? 'analyzed' : ''}" onclick="openAnalisisDrawer('${op.ocid}', '${encodeURIComponent(displayTitle)}', '${encodeURIComponent(op.entidad)}', '${op.linea_servicio}', '${op.url_bases}', ${op.score})" title="Analizar Bases con IA">
                        ${op.analisis_bases ? 'Ficha' : 'Analizar'}
                      </button>
                      <a href="${op.url_bases}" target="_blank" class="btn-action-bases" title="Descargar PDF">PDF</a>
                    ` : ''}
                    <a href="${op.url_oece || '#'}" target="_blank" class="btn-action-oece" title="Abrir en Portal OECE">OECE</a>
                  </div>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;
}

// ==========================================================================
// FILTROS Y CONTROLES INTERACTIVOS
// ==========================================================================
function setLineFilter(line) {
  currentLine = line;
  document.querySelectorAll('.line-pill').forEach(p => {
    if (p.getAttribute('data-line') === line) {
      p.classList.add('active');
    } else {
      p.classList.remove('active');
    }
  });
  loadOpportunities();
}

function handleSearch() {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(loadOpportunities, 250);
}

function toggleVigentes() {
  const soloVig = document.getElementById('soloVigentes')?.checked;
  if (soloVig) {
    const windowFilter = document.getElementById('windowFilter');
    if (windowFilter) windowFilter.value = 'ALL';
  }
  loadOpportunities();
}

async function changeStatus(ocid, nuevoEstado, selectElem) {
  try {
    const res = await fetch(`/api/oportunidades/${encodeURIComponent(ocid)}/estado`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ estado_interno: nuevoEstado })
    });
    if (res.ok) {
      selectElem.className = `status-select-pill ${nuevoEstado}`;
      showToast(`Estado actualizado: ${nuevoEstado.replace('_', ' ')}`, 'success');
      fetchStats();
    }
  } catch (e) {
    console.error('Error al actualizar estado:', e);
    showToast('Error al actualizar estado', 'info');
  }
}

// ==========================================================================
// ESCANEO EN VIVO (BACKGROUND SYNC)
// ==========================================================================
async function pollScanStatus() {
  if (scanPollBusy) return;
  scanPollBusy = true;
  clearTimeout(scanPollTimer);
  const banner = document.getElementById('bannerScan');
  const title = document.getElementById('bannerText');
  const detail = document.getElementById('scanDetail');
  const bar = document.getElementById('scanProgress');
  const btn = document.getElementById('btnScan');
  let active = false;
  try {
    const res = await fetch('/api/scan/status', { signal: AbortSignal.timeout(10000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    active = data.is_scanning;
    const p = data.progress || {};
    const result = data.last_scan_result;
    if (btn) btn.disabled = active;
    if (banner) banner.style.display = active || result ? 'block' : 'none';
    if (active) {
      if (p.pages_done !== undefined && p.pages_done !== lastScanPages) {
        lastScanPages = p.pages_done;
        fetchStats();
        loadOpportunities();
      }
      if (title) title.textContent = `${p.stage || 'Escaneando'} · ${Math.floor((p.elapsed_seconds || 0) / 60)} min ${Math.floor((p.elapsed_seconds || 0) % 60)} s`;
      if (detail) detail.textContent = `${p.queries_done || 0}/${p.queries_total || '?'} términos completados · ${p.pages_done || 0} páginas · ${p.records_skipped || 0} expedientes fuera de plazo omitidos · ${p.errors || 0} errores${p.query ? ' · ' + p.query : ''}`;
      if (bar) bar.style.width = `${p.queries_total ? 100 * p.queries_done / p.queries_total : 0}%`;
    } else if (result) {
      const ok = result.status === 'SUCCESS';
      if (title) title.textContent = ok ? 'Escaneo finalizado' : result.status === 'ERROR' ? 'El escaneo falló' : 'Escaneo parcial';
      if (detail) detail.textContent = result.error || `${result.terminos_completados || 0}/${result.terminos_total || 0} términos completados · ${result.total_releases_evaluados || 0} resultados revisados · ${result.duracion_segundos || 0} s. ${result.limite_tiempo ? 'Se alcanzó el límite de tiempo; se guardó lo revisado.' : ok ? 'Resultados actualizados.' : 'Algunas consultas fallaron; los resultados están incompletos.'}`;
      if (bar) bar.style.width = `${ok ? 100 : (p.queries_total ? 100 * p.queries_done / p.queries_total : 0)}%`;
    }
    if (scanWasActive && !active) {
      fetchStats();
      loadOpportunities();
      if (!result) showToast('El escaneo se interrumpió o el servidor se reinició.', 'info');
    }
    scanWasActive = active;
  } catch (e) {
    active = true;
    if (banner) banner.style.display = 'block';
    if (title) title.textContent = 'Sin conexión con el estado del escaneo';
    if (detail) detail.textContent = 'No se puede confirmar el avance. Reintentando conexión…';
    if (bar) bar.style.width = '0%';
    if (btn) btn.disabled = true;
  } finally {
    scanPollBusy = false;
    scanPollTimer = active ? setTimeout(pollScanStatus, 3000) : null;
  }
}

async function triggerScan() {
  const btn = document.getElementById('btnScan');
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pages: 2, seconds: 600 }), signal: AbortSignal.timeout(10000)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    scanWasActive = true;
    showToast(data.status === 'already_running' ? 'Ya hay un escaneo en ejecución.' : 'Escaneo iniciado: hasta 2 páginas por término, límite 10 minutos.', 'info');
    pollScanStatus();
  } catch (e) {
    showToast('No se pudo confirmar el inicio. Comprobando el estado del servidor.', 'info');
    pollScanStatus();
  }
}

// ==========================================================================
// DRAWER DE ANÁLISIS DE BASES
// ==========================================================================
function modeBarHtml(activeModo, doneModo) {
  const hint = doneModo === 'rapido'
    ? '<span class="mode-hint">▶ Resultado actual: triaje rápido. Revisa garantía y anexos con el análisis completo.</span>'
    : (doneModo === 'completo' ? '<span class="mode-hint">✓ Análisis completo.</span>' : '');
  return `
    <div class="analisis-mode-bar">
      <button class="mode-btn ${activeModo === 'rapido' ? 'on' : ''}" onclick="startAnalisis('${currentAnalisisOcid}', 'rapido')">⚡ Triaje rápido</button>
      <button class="mode-btn ${activeModo === 'completo' ? 'on' : ''}" onclick="startAnalisis('${currentAnalisisOcid}', 'completo')">🔍 Análisis completo</button>
      ${hint}
    </div>
  `;
}

function renderAnalisisDrawer() {
  const bodyEl = document.getElementById('drawerBody');
  if (!bodyEl || !currentAnalisisData) return;
  const d = currentAnalisisData;
  bodyEl.innerHTML = modeBarHtml(d.modo || 'completo', d.modo || 'completo') + renderAnalisisContent(d);
}

async function startAnalisis(ocid, modo, metaOver = {}) {
  const loadingEl = document.getElementById('drawerLoading');
  const bodyEl = document.getElementById('drawerBody');
  if (bodyEl) bodyEl.innerHTML = modeBarHtml(modo, null) + '<div class="analisis-card"><p style="color: var(--text-dim); font-size: 13px;">Procesando… consulta el estado más abajo.</p></div>';
  if (loadingEl) loadingEl.style.display = 'flex';
  try {
    const start = await fetch(`/api/oportunidades/${encodeURIComponent(ocid)}/analizar?modo=${modo}`, { method: 'POST' });
    let json = await start.json();
    while (json.status === 'queued' || json.status === 'running') {
      if (loadingEl && loadingEl.querySelector('p')) loadingEl.querySelector('p').textContent = json.detail || json.stage || 'Procesando bases…';
      await new Promise(resolve => setTimeout(resolve, 3000));
      const poll = await fetch(`/api/oportunidades/${encodeURIComponent(ocid)}/analizar/estado`);
      json = await poll.json();
    }
    if (loadingEl) loadingEl.style.display = 'none';
    if (bodyEl) {
      if (json.data && json.data.success) {
        currentAnalisisData = { ...json.data, ...metaOver };
        renderAnalisisDrawer();
        loadOpportunities();
      } else {
        bodyEl.innerHTML = modeBarHtml(modo, null) + `
          <div class="analisis-card" style="border-color: #fca5a5; background: #fef2f2;">
            <h4 style="color: #dc2626; margin-bottom: 6px;">⚠️ No se pudo procesar el PDF oficial</h4>
            <p>${json.data?.error || json.detail || 'Ocurrió un inconveniente al descargar o leer las bases de contratación de SEACE.'}</p>
            <p style="margin-top: 10px; font-size: 12px; color: var(--text-muted);">Puedes abrir directamente el documento original con el botón "Descargar PDF Oficial".</p>
          </div>
        `;
      }
    }
  } catch (e) {
    if (loadingEl) loadingEl.style.display = 'none';
    if (bodyEl) {
      bodyEl.innerHTML = modeBarHtml(modo, null) + `
        <div class="analisis-card" style="border-color: #fca5a5; background: #fef2f2;">
          <h4 style="color: #dc2626; margin-bottom: 6px;">Error de Conexión</h4>
          <p>No se pudo conectar con el motor de análisis: ${e.message}</p>
        </div>
      `;
    }
  }
}

async function openAnalisisDrawer(ocid, encodedTitulo, encodedEntidad, lineaServicio, urlBases, score = 0) {
  const titulo = decodeURIComponent(encodedTitulo);
  const entidad = decodeURIComponent(encodedEntidad);

  const modal = document.getElementById('analisisModal');
  const titleEl = document.getElementById('drawerTitle');
  const subEl = document.getElementById('drawerSub');
  const badgeEl = document.getElementById('drawerBadge');
  const scoreBadgeEl = document.getElementById('drawerScoreBadge');
  const loadingEl = document.getElementById('drawerLoading');
  const bodyEl = document.getElementById('drawerBody');
  const btnBases = document.getElementById('drawerBtnBases');

  if (titleEl) titleEl.innerText = titulo;
  if (subEl) subEl.innerText = `Entidad: ${entidad}`;
  if (badgeEl) {
    badgeEl.className = `line-badge ${lineaServicio}`;
    badgeEl.innerText = lineaServicio.replace('_', ' ');
  }
  if (scoreBadgeEl) {
    scoreBadgeEl.innerText = `MATCH ${score}%`;
    scoreBadgeEl.className = `score-badge ${score >= 70 ? 'high' : ''}`;
  }
  if (btnBases) {
    btnBases.href = urlBases || '#';
    btnBases.style.display = urlBases ? 'inline-flex' : 'none';
  }
  currentAnalisisOcid = ocid;
  if (bodyEl) bodyEl.innerHTML = modeBarHtml('rapido', null);
  if (loadingEl) loadingEl.style.display = 'flex';
  if (modal) modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  try {
    const estRes = await fetch(`/api/oportunidades/${encodeURIComponent(ocid)}/analizar/estado`);
    const est = await estRes.json();
    if (est.status === 'completed' && est.data && est.data.success) {
      if (loadingEl) loadingEl.style.display = 'none';
      currentAnalisisData = { ...est.data, titulo, entidad };
      renderAnalisisDrawer();
    } else {
      await startAnalisis(ocid, 'rapido', { titulo, entidad });
    }
  } catch (e) {
    if (loadingEl) loadingEl.style.display = 'none';
    if (bodyEl) {
      bodyEl.innerHTML = `
        <div class="analisis-card" style="border-color: #fca5a5; background: #fef2f2;">
          <h4 style="color: #dc2626; margin-bottom: 6px;">Error de Conexión</h4>
          <p>No se pudo conectar con el motor de análisis: ${e.message}</p>
        </div>
      `;
    }
  }
}

function renderAnalisisContent(data) {
  const fact = data.factibilidad_txdx || {};

  const certsHtml = (data.certificaciones_requeridas || []).length > 0
    ? data.certificaciones_requeridas.map(c => `<span class="cert-pill">${c}</span>`).join('')
    : '<p style="color: var(--text-dim); font-size: 12px;">No se identificaron certificaciones obligatorias excluyentes en el texto extraído.</p>';

  const personalHtml = (data.personal_clave || []).length > 0
    ? (data.personal_clave || []).map(p => `<li style="margin-bottom: 6px;">${p}</li>`).join('')
    : '<li style="color: var(--text-dim); list-style: none;">No se especificó un perfil detallado de personal clave.</li>';

  const garantiaAplicable = data.garantia && !/No especificada|no especificad/i.test(data.garantia);
  const garantiaMonto = garantiaAplicable
    ? (parseFloat(String(data.garantia).replace(/[^0-9.]/g, '')) || 0)
    : 0;
  const garantiaFuera = garantiaMonto >= 1000000;
  const modo = data.modo || 'completo';
  const conc = data.concordancia_triaje || null;
  const concHtml = conc ? `
    <div class="analisis-card" style="border-color: ${conc.match_nivel ? '#a7f3d0' : '#fca5a5'}; background: ${conc.match_nivel ? '#ecfdf5' : '#fef2f2'};">
      <div class="analisis-card-title" style="color: ${conc.match_nivel ? '#059669' : '#dc2626'};">Concordancia triaje ↔ completo</div>
      <p style="font-size: 13px;">
        ${conc.match_nivel ? 'El triaje rápido coincidió con el análisis completo.' : 'El triaje se desvió del análisis completo (nivel distinto).'}
        Score previo ${conc.score_previo} → ${conc.score_completo} (delta ${conc.score_delta >= 0 ? '+' : ''}${conc.score_delta}).
      </p>
    </div>
  ` : '';

  return `
    <!-- Modo de análisis -->
    <div class="analisis-mode-pill mode-${modo}">${modo === 'rapido' ? '⚡ TRIAGE RÁPIDO' : '🔍 ANÁLISIS COMPLETO'}</div>

    <!-- Veredicto de Factibilidad TxDx -->
    <div class="factibilidad-banner" style="background: ${fact.color === '#059669' ? '#ecfdf5' : '#fff7ed'}; border: 1px solid ${fact.color === '#059669' ? '#a7f3d0' : '#fed7aa'};">
      <div class="factibilidad-banner-header" style="color: ${fact.color || 'var(--txdx-orange)'};">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
        <span>Veredicto TxDx: ${fact.nivel || 'EVALUADO'} (${fact.score || 0}/100)</span>
      </div>
      <div class="factibilidad-banner-desc">${fact.mensaje || 'Análisis completado.'}</div>
      ${fact.alertas && fact.alertas.length > 0 ? `
        <div style="margin-top: 8px; font-size: 12px; color: #b45309; line-height: 1.5;">
          ${fact.alertas.map(a => `<div>⚠️ ${a}</div>`).join('')}
        </div>
      ` : ''}
    </div>

    <!-- Experiencia del Postor (Capítulo III) -->
    <div class="analisis-card">
      <div class="analisis-card-title" style="color: var(--txdx-orange);">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
        <span>Experiencia del Postor Requerida (Capítulo III)</span>
      </div>
      <p><strong>${data.experiencia_postor || 'No especificada en extracto'}</strong></p>
    </div>

    <!-- Personal Clave Solicitado -->
    <div class="analisis-card">
      <div class="analisis-card-title" style="color: #0284c7;">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
        <span>Personal Clave y Profesionales</span>
      </div>
      <ul>${personalHtml}</ul>
    </div>

    <!-- Certificaciones Técnicas -->
    <div class="analisis-card">
      <div class="analisis-card-title" style="color: var(--ia-color);">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>
        <span>Certificaciones Técnicas Identificadas</span>
      </div>
      <div>${certsHtml}</div>
    </div>

    <!-- Garantía y Plazos en Rejilla -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
      <div class="analisis-card" style="${garantiaFuera ? 'border-color: #fca5a5; background: #fef2f2;' : ''}">
        <div class="analisis-card-title" style="color: ${garantiaFuera ? '#dc2626' : '#d97706'};">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
          <span>Garantía / Fianza</span>
        </div>
        <p style="font-weight: 700; color: ${garantiaFuera ? '#dc2626' : 'var(--text-main)'};">${data.garantia || 'Por verificar en bases'}</p>
        ${garantiaFuera ? '<small style="color: #dc2626; font-size: 11px;">⚠️ Fianza ≥ S/1M</small>' : ''}
      </div>

      <div class="analisis-card">
        <div class="analisis-card-title" style="color: #059669;">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
          <span>Plazo & Modalidad</span>
        </div>
        <p><strong>${data.plazo_ejecucion || 'Por verificar'}</strong></p>
        <small style="color: var(--text-muted); font-size: 11px;">${data.modalidad || ''}</small>
      </div>
    </div>

${concHtml}

    <div style="font-size: 11px; color: var(--text-dim); text-align: right; margin-top: 4px; line-height: 1.6;">
      Documento: ${data.total_paginas || 0} páginas. ${data.evidencia?.paginas_revisadas?.length ? `Requisitos revisados en págs. ${data.evidencia.paginas_revisadas.join(', ')}.` : 'Revisa el PDF oficial antes de postular.'}
      <br/>${data.motor || ''} · ${data.usage_tokens_total != null ? data.usage_tokens_total + ' tokens' : 'motor local'}. ${data.sugerencia || ''}
    </div>
  `;
}

function closeAnalisisDrawer() {
  const modal = document.getElementById('analisisModal');
  if (modal) modal.style.display = 'none';
  document.body.style.overflow = 'auto';
}

function handleOverlayClick(e) {
  if (e.target.id === 'analisisModal') {
    closeAnalisisDrawer();
  }
}

function copyAnalisisToClipboard() {
  if (!currentAnalisisData) return;
  const d = currentAnalisisData;
  const text = `
*FICHA DE CONTRATACIÓN TXDX — ${d.titulo}*
Entidad: ${d.entidad}
Veredicto de Factibilidad: ${d.factibilidad_txdx?.nivel} (${d.factibilidad_txdx?.score}/100)

1. EXPERIENCIA DEL POSTOR:
${d.experiencia_postor || 'Verificar bases'}

2. PERSONAL CLAVE:
${(d.personal_clave || []).map(p => `• ${p}`).join('\n') || 'No especificado'}

3. CERTIFICACIONES:
${(d.certificaciones_requeridas || []).join(', ') || 'Sin certificaciones excluyentes'}

4. GARANTÍA / CARTA FIANZA:
${d.garantia || 'Por verificar'}

5. PLAZO Y MODALIDAD:
Plazo: ${d.plazo_ejecucion || '-'} | Modalidad: ${d.modalidad || '-'}
  `.trim();

  copyToClipboardFallback(text).then(() => {
    showToast('Ficha de análisis copiada al portapapeles', 'success');
  }).catch(() => {
    showToast('No se pudo copiar al portapapeles', 'info');
  });
}

// ==========================================================================
// MODAL CONFIGURACIÓN IA (GROQ)
// ==========================================================================
async function openAIConfigModal() {
  const modal = document.getElementById('confModal');
  const statusEl = document.getElementById('confStatus');
  const spinner = document.getElementById('confSpinner');
  const inputEl = document.getElementById('groqKeyInput');

  if (modal) modal.style.display = 'flex';
  if (statusEl) statusEl.innerText = 'Verificando estado del motor IA...';
  if (spinner) spinner.style.display = 'inline-block';
  if (inputEl) inputEl.value = '';

  try {
    const res = await fetch('/api/config/groq-key');
    const data = await res.json();
    if (spinner) spinner.style.display = 'none';
    if (statusEl) {
      statusEl.innerText = data.configured
        ? `✅ Motor activo: Clave configurada (${data.preview})`
        : '⚠️ Sin clave configurada. Se usará el extractor local heurístico.';
    }
  } catch (e) {
    if (spinner) spinner.style.display = 'none';
    if (statusEl) statusEl.innerText = 'No se pudo conectar con el servidor.';
  }
}

function closeAIConfigModal() {
  const modal = document.getElementById('confModal');
  if (modal) modal.style.display = 'none';
}

function handleModalBackdropClick(e) {
  if (e.target.id === 'confModal') {
    closeAIConfigModal();
  }
}

function togglePasswordVisibility(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    btn.style.color = 'var(--txdx-orange)';
  } else {
    input.type = 'password';
    btn.style.color = 'var(--text-dim)';
  }
}

async function saveGroqKey() {
  const input = document.getElementById('groqKeyInput');
  const key = input ? input.value.trim() : '';
  if (!key) {
    showToast('Ingresa una clave válida antes de guardar', 'info');
    return;
  }

  try {
    const res = await fetch('/api/config/groq-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key })
    });
    const json = await res.json();
    if (json.status === 'saved') {
      showToast('Clave de Groq guardada exitosamente', 'success');
      document.getElementById('confStatus').innerText = '✅ Clave guardada. Análisis con IA activo.';
      input.value = '';
      setTimeout(closeAIConfigModal, 1200);
    } else {
      showToast('Error al guardar la clave', 'info');
    }
  } catch (e) {
    showToast('Error de conexión al guardar clave', 'info');
  }
}

async function confirmarSEACE(ocid, confirmado, button) {
  try {
    const res = await fetch(`/api/oportunidades/${encodeURIComponent(ocid)}/seace-confirmacion`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmado })
    });
    if (!res.ok) throw new Error('No se pudo guardar la verificación');
    if (button) {
      button.textContent = confirmado ? 'Confirmado en SEACE' : 'Confirmar en SEACE';
      button.classList.toggle('confirmed', confirmado);
      button.onclick = () => confirmarSEACE(ocid, !confirmado, button);
    }
    showToast(confirmado ? 'Proceso confirmado manualmente en SEACE' : 'Confirmación SEACE retirada', 'success');
  } catch (e) { showToast('No se pudo guardar la verificación SEACE', 'info'); }
}
