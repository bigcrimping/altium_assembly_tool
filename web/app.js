'use strict';

// ── App state ─────────────────────────────────────────────────────────────────
const state = {
  step: -1,          // current BOM row index (-1 = none selected)
  side: 'TOP',
  bom: [],
  bounds: {},        // { "R1": [x0,y0,x1,y1], ... }
  placed: new Set(),
  dnp: new Set(),
  hidden: { TOP: new Set(), BOTTOM: new Set() },  // hidden designators per view side
  viewbox: null,     // [x, y, w, h]
  loaded: false,
  hideFitted: false,
  autoZoom: false,
  labels: true,
  dnpView: false,   // show every DNP part board-wide instead of a single BOM row
};

// Designators currently highlighted on the board: every DNP part when the DNP
// view is on, otherwise the selected BOM row's components (or null for none).
function activeDesignators() {
  if (state.dnpView) {
    if (!state.dnp.size) return null;
    const out = [];
    state.bom.forEach(row => row.designators.forEach(d => {
      if (state.dnp.has(d)) out.push(d);
    }));
    return out;
  }
  if (state.step >= 0 && state.bom[state.step]) return state.bom[state.step].designators;
  return null;
}

// designator → [svg elements] — rebuilt each time the base SVG is injected
const compElems = new Map();

// Undo/redo stacks of toggled designators (a toggle is its own inverse)
const undoStack = [];
const redoStack = [];

// ── View / pan-zoom state ─────────────────────────────────────────────────────
const view = { scale: 1, tx: 0, ty: 0 };

// ── DOM refs ──────────────────────────────────────────────────────────────────
const boardContainer = document.getElementById('board-container');
const boardTransform = document.getElementById('board-transform');
const bomBody        = document.getElementById('bom-body');
const stepLabel      = document.getElementById('step-label');
const boardNameEl    = document.getElementById('board-name');
const statusbar      = document.getElementById('statusbar');
const loadOverlay    = document.getElementById('load-overlay');
const loadError      = document.getElementById('load-error');
const pcbPathInput   = document.getElementById('pcb-path');
const prjPathInput   = document.getElementById('prj-path');
const searchInput    = document.getElementById('bom-search');
const progressLabel  = document.getElementById('progress-label');

const SVG_NS = 'http://www.w3.org/2000/svg';

// Colour-blind-safe palette (Okabe-Ito) — mirror of the constants in pcb_model.py.
const SELECT_HIGHLIGHT_COLOR = '#56B4E9';  // sky blue — selected BOM row's parts
const PLACED_MARKER_COLOR    = '#009E73';  // bluish green — placed
const DNP_MARKER_COLOR       = '#D55E00';  // vermillion — DNP / no-fit

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  try {
    const data = await fetchJson('/api/data');
    if (data.loaded) {
      applyData(data);
      await loadSvg();
    } else {
      showLoadOverlay();
    }
  } catch (e) {
    setStatus('Server error: ' + e.message);
  }
}

function applyData(data) {
  state.bom    = data.bom;
  state.bounds = data.bounds;
  state.placed = new Set(data.placed);
  state.dnp    = new Set(data.dnp);
  state.hidden = {
    TOP:    new Set((data.hidden && data.hidden.TOP)    || []),
    BOTTOM: new Set((data.hidden && data.hidden.BOTTOM) || []),
  };
  state.viewbox = data.viewbox || null;
  state.loaded = true;
  state.step   = -1;
  state.side   = 'TOP';
  state.dnpView = false;
  undoStack.length = 0;
  redoStack.length = 0;
  boardNameEl.textContent = data.board_name;
  renderBomTable();
  enableControls(true);
  setDnpButton();
  updateSideButtons();
  updateNavigation();
  updateProgress();
  applyRowFilters();
  setStatus('Loaded ' + data.board_name + ' — ' + data.bom.length + ' BOM groups');
}

// ── Load dialog ───────────────────────────────────────────────────────────────
function showLoadOverlay() {
  loadOverlay.classList.remove('hidden');
  loadError.classList.add('hidden');
  renderRecents();
  pcbPathInput.focus();
}
function hideLoadOverlay() {
  loadOverlay.classList.add('hidden');
}

document.getElementById('btn-open').addEventListener('click', showLoadOverlay);
document.getElementById('btn-cancel-load').addEventListener('click', () => {
  if (state.loaded) hideLoadOverlay();
});
document.getElementById('btn-load-file').addEventListener('click', doLoad);
pcbPathInput.addEventListener('keydown', e => { if (e.key === 'Enter') doLoad(); });

async function doLoad() {
  const pcb = pcbPathInput.value.trim();
  if (!pcb) { showLoadError('PCB file path is required.'); return; }
  setStatus('Loading…');
  loadError.classList.add('hidden');
  document.getElementById('btn-load-file').disabled = true;
  try {
    const resp = await fetchJson('/api/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ pcb_path: pcb, prj_path: prjPathInput.value.trim() }),
    });
    if (!resp.ok) { showLoadError(resp.error); return; }
    addRecent(pcb, prjPathInput.value.trim());
    hideLoadOverlay();
    const data = await fetchJson('/api/data');
    applyData(data);
    await loadSvg();
  } catch (e) {
    showLoadError(e.message);
  } finally {
    document.getElementById('btn-load-file').disabled = false;
  }
}

function showLoadError(msg) {
  loadError.textContent = msg;
  loadError.classList.remove('hidden');
  setStatus('Load failed: ' + msg);
}

// ── Recent paths (localStorage) ───────────────────────────────────────────────
function getRecents() {
  try { return JSON.parse(localStorage.getItem('recentPcbPaths') || '[]'); }
  catch { return []; }
}

function addRecent(pcb, prj) {
  const items = getRecents().filter(it => it.pcb !== pcb);
  items.unshift({ pcb, prj: prj || '' });
  localStorage.setItem('recentPcbPaths', JSON.stringify(items.slice(0, 8)));
}

function renderRecents() {
  const box = document.getElementById('recent-list');
  const items = getRecents();
  box.innerHTML = '';
  if (!items.length) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const title = document.createElement('label');
  title.textContent = 'Recent';
  box.appendChild(title);
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'recent-item';
    div.textContent = it.pcb;
    div.title = it.pcb + (it.prj ? '\n' + it.prj : '');
    div.addEventListener('click', () => {
      pcbPathInput.value = it.pcb;
      prjPathInput.value = it.prj || '';
    });
    box.appendChild(div);
  });
}

// ── Controls ──────────────────────────────────────────────────────────────────
function enableControls(on) {
  ['btn-prev','btn-next','btn-fit','btn-fit-sel','btn-auto-zoom','btn-labels',
   'btn-clear','btn-dnp','btn-top','btn-bot','btn-hide-fitted','bom-search'].forEach(id => {
    document.getElementById(id).disabled = !on;
  });
  document.getElementById('btn-labels').classList.toggle('active-side', state.labels);
}

document.getElementById('btn-fit').addEventListener('click', fitView);
document.getElementById('btn-fit-sel').addEventListener('click', fitSelection);
document.getElementById('btn-clear').addEventListener('click', clearSelection);
document.getElementById('btn-prev').addEventListener('click', () => {
  if (state.step > 0) selectStep(state.step - 1);
});
document.getElementById('btn-next').addEventListener('click', () => {
  if (state.step < state.bom.length - 1) selectStep(state.step + 1);
});
document.getElementById('btn-top').addEventListener('click', () => setSide('TOP'));
document.getElementById('btn-bot').addEventListener('click', () => setSide('BOTTOM'));

document.getElementById('btn-auto-zoom').addEventListener('click', () => {
  state.autoZoom = !state.autoZoom;
  document.getElementById('btn-auto-zoom').classList.toggle('active-side', state.autoZoom);
  if (state.autoZoom) autoZoomNow();
});

document.getElementById('btn-labels').addEventListener('click', () => {
  state.labels = !state.labels;
  document.getElementById('btn-labels').classList.toggle('active-side', state.labels);
  updateLabels();
});

document.getElementById('btn-hide-fitted').addEventListener('click', () => {
  state.hideFitted = !state.hideFitted;
  document.getElementById('btn-hide-fitted').classList.toggle('active-side', state.hideFitted);
  applyRowFilters();
});

document.getElementById('btn-dnp').addEventListener('click', toggleDnpView);

function setDnpButton() {
  document.getElementById('btn-dnp').classList.toggle('active-side', state.dnpView);
}

function toggleDnpView() {
  state.dnpView = !state.dnpView;
  setDnpButton();
  if (state.dnpView) {
    // Whole-board DNP set drives the view — drop any single-row selection.
    state.step = -1;
    document.querySelectorAll('#bom-body tr.selected').forEach(r => r.classList.remove('selected'));
    updateNavigation();
    const n = state.dnp.size;
    setStatus(n
      ? `DNP view: ${n} part(s) marked Do Not Fit`
      : 'DNP view: no parts marked Do Not Fit — load a .PrjPcb for DNP data');
  } else {
    setStatus('DNP view off');
  }
  refreshBoard();
  if (state.autoZoom) autoZoomNow();
}

searchInput.addEventListener('input', applyRowFilters);

function clearSelection() {
  state.step = -1;
  if (state.dnpView) { state.dnpView = false; setDnpButton(); }
  document.querySelectorAll('#bom-body tr.selected').forEach(r => r.classList.remove('selected'));
  updateNavigation();
  refreshBoard();
  if (state.autoZoom) fitView();
}

function setSide(side) {
  if (side === state.side) return;
  state.side = side;
  updateSideButtons();
  applySideFlip();
  refreshBoard();
  if (state.autoZoom) autoZoomNow();
}

function autoZoomNow() {
  if (activeDesignators()) fitSelection();
  else fitView();
}

function updateSideButtons() {
  document.getElementById('btn-top').classList.toggle('active-side', state.side === 'TOP');
  document.getElementById('btn-bot').classList.toggle('active-side', state.side === 'BOTTOM');
}

function updateNavigation() {
  const n = state.bom.length, idx = state.step;
  document.getElementById('btn-prev').disabled = !state.loaded || idx <= 0;
  document.getElementById('btn-next').disabled = !state.loaded || idx >= n - 1;
  stepLabel.textContent = state.loaded
    ? (idx >= 0 ? `Step ${idx + 1} of ${n}` : `0 of ${n}`)
    : '';
}

function updateProgress() {
  if (!state.loaded) { progressLabel.textContent = ''; return; }
  let total = 0, done = 0;
  state.bom.forEach(row => {
    total += row.designators.length;
    row.designators.forEach(d => {
      if (state.placed.has(d) || state.dnp.has(d)) done++;
    });
  });
  const pct = total ? (100 * done / total) : 0;
  progressLabel.textContent = `Fitted ${done}/${total} (${pct.toFixed(1)}%)`;
}

// ── BOM table ─────────────────────────────────────────────────────────────────
function refsHtml(refs) {
  return refs.map(d => {
    if (state.placed.has(d)) return `<span class="ref-placed">${d}</span>`;
    if (state.dnp.has(d))    return `<span class="ref-dnp">${d}</span>`;
    return d;
  }).join(', ');
}

function renderBomTable() {
  bomBody.innerHTML = '';
  state.bom.forEach((row, idx) => {
    const tr = buildBomRow(row, idx);
    bomBody.appendChild(tr);
  });
}

function buildBomRow(row, idx) {
  const tr = document.createElement('tr');
  tr.dataset.idx = idx;
  tr.addEventListener('click', () => selectStep(idx));

  const cells = [
    { text: idx + 1,            center: true },
    { text: row.quantity,       center: true },
    { text: row.placed_count,   center: true, id: 'placed' },
    { text: row.to_place_count, center: true, id: 'toplace' },
    { text: row.comment,        tooltip: row.description },
    { html: refsHtml(row.top_refs), id: 'toprefs',
      sideDone: row.top_done && !row.all_done },
    { html: refsHtml(row.bot_refs), id: 'botrefs',
      sideDone: row.bot_done && !row.all_done },
  ];

  cells.forEach(c => {
    const td = document.createElement('td');
    if (c.html !== undefined) td.innerHTML = c.html;
    else td.textContent = c.text;
    if (c.center) td.style.textAlign = 'center';
    if (c.tooltip) td.title = c.tooltip;
    if (c.sideDone) td.classList.add('cell-side-done');
    tr.appendChild(td);
  });

  if (row.all_done) tr.classList.add('row-all-done');
  return tr;
}

function updateBomRow(rowData) {
  state.bom[rowData.index] = rowData;  // keep all_done etc. fresh for filters
  const tr = bomBody.children[rowData.index];
  if (!tr) return;
  tr.children[2].textContent = rowData.placed_count;
  tr.children[3].textContent = rowData.to_place_count;
  tr.children[5].innerHTML   = refsHtml(rowData.top_refs);
  tr.children[6].innerHTML   = refsHtml(rowData.bot_refs);
  tr.classList.toggle('row-all-done', rowData.all_done);
  tr.children[5].classList.toggle('cell-side-done', rowData.top_done && !rowData.all_done);
  tr.children[6].classList.toggle('cell-side-done', rowData.bot_done && !rowData.all_done);
  // Refresh row-all-done cell backgrounds (the CSS rule targets td via row class)
  if (rowData.all_done) {
    [5, 6].forEach(i => tr.children[i].classList.remove('cell-side-done'));
  }
}

function rowMatchesQuery(row, q) {
  if (row.comment.toLowerCase().includes(q)) return true;
  if ((row.description || '').toLowerCase().includes(q)) return true;
  return row.designators.some(d => d.toLowerCase().includes(q));
}

function applyRowFilters() {
  const q = searchInput.value.trim().toLowerCase();
  state.bom.forEach((row, idx) => {
    const tr = bomBody.children[idx];
    if (!tr) return;
    const hide = (state.hideFitted && row.all_done) || (q !== '' && !rowMatchesQuery(row, q));
    tr.style.display = hide ? 'none' : '';
  });
}

function selectStep(idx) {
  const wasDnp = state.dnpView;
  if (idx === state.step && !wasDnp) return;
  if (wasDnp) { state.dnpView = false; setDnpButton(); }
  state.step = idx;
  document.querySelectorAll('#bom-body tr').forEach((tr, i) => {
    tr.classList.toggle('selected', i === idx);
  });
  const tr = bomBody.children[idx];
  if (tr) tr.scrollIntoView({ block: 'nearest' });
  updateNavigation();
  refreshBoard();
  if (state.autoZoom) fitSelection();
  if (state.bom[idx]) {
    setStatus(`Step ${idx + 1} of ${state.bom.length}: ${state.bom[idx].comment}`);
  }
}

// ── SVG loading ───────────────────────────────────────────────────────────────
// The base SVG is fetched ONCE per board load. Step / side changes only toggle
// CSS classes on the already-loaded DOM — no network, no re-parse, and the
// user's zoom/pan is preserved.
async function loadSvg() {
  try {
    const resp = await fetch('/api/svg');
    if (!resp.ok) { setStatus(`SVG load failed (HTTP ${resp.status})`); return; }
    const svgText = await resp.text();
    boardTransform.innerHTML = svgText;
    buildCompIndex();
    const svg = boardTransform.querySelector('svg');
    if (svg) svg.addEventListener('dblclick', onSvgDblClick);
    applySideFlip();
    refreshBoard();
    fitView();
  } catch (e) {
    setStatus('SVG load error: ' + e.message);
  }
}

function buildCompIndex() {
  compElems.clear();
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  svg.querySelectorAll('[data-component]').forEach(el => {
    const d = el.getAttribute('data-component');
    let arr = compElems.get(d);
    if (!arr) { arr = []; compElems.set(d, arr); }
    arr.push(el);
    // Remember the original colours so a selection highlight can be reverted.
    el._origFill   = el.getAttribute('fill');
    el._origStroke = el.getAttribute('stroke');
    el._hilite     = false;
  });
}

// Recolour a component's real fill/stroke to the highlight colour, leaving
// 'none' fills and gradient references (e.g. the pin-1 stripe) untouched.
function highlightEl(el) {
  const f = el._origFill, s = el._origStroke;
  if (f && f !== 'none' && !f.startsWith('url(')) el.setAttribute('fill', SELECT_HIGHLIGHT_COLOR);
  if (s && s !== 'none' && !s.startsWith('url(')) el.setAttribute('stroke', SELECT_HIGHLIGHT_COLOR);
  el._hilite = true;
}

function restoreEl(el) {
  if (!el._hilite) return;  // only touch elements we actually recoloured
  if (el._origFill   != null) el.setAttribute('fill', el._origFill);
  if (el._origStroke != null) el.setAttribute('stroke', el._origStroke);
  el._hilite = false;
}

// Re-apply dim/hide classes, markers, and labels for the current step + side.
function refreshBoard() {
  applyHighlight();
  updateMarkers();
  updateLabels();
}

function applyHighlight() {
  const hidden = state.hidden[state.side] || new Set();
  const active = activeDesignators();
  const selected = active ? new Set(active) : null;
  compElems.forEach((elems, desig) => {
    const hide = hidden.has(desig);
    const sel  = !hide && selected !== null && selected.has(desig);
    const dim  = !hide && selected !== null && !selected.has(desig);
    elems.forEach(el => {
      el.classList.toggle('comp-hidden', hide);
      el.classList.toggle('comp-dim', dim);
      if (sel) highlightEl(el);
      else restoreEl(el);
    });
  });
}

// Mirror the board for bottom-side view. Markers and labels live inside the
// SVG, so they flip along with the components and stay aligned automatically.
function applySideFlip() {
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  svg.style.transformOrigin = '50% 50%';
  svg.style.transform = state.side === 'BOTTOM' ? 'scaleX(-1)' : '';
}

// ── Placed / DNP markers + designator labels ──────────────────────────────────
function updateMarkers() {
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  svg.querySelectorAll('.placed-marker, .dnp-marker').forEach(el => el.remove());
  const active = activeDesignators();
  if (!active) return;
  const hidden = state.hidden[state.side] || new Set();
  active.forEach(desig => {
    if (hidden.has(desig)) return;
    const b = state.bounds[desig];
    if (!b || b.length < 4) return;
    if (state.placed.has(desig))   addPlacedMarker(svg, b);
    else if (state.dnp.has(desig)) addDnpMarker(svg, b);
  });
}

function addPlacedMarker(svg, [x0, y0, x1, y1]) {
  const rect = document.createElementNS(SVG_NS, 'rect');
  rect.setAttribute('class', 'placed-marker');
  rect.setAttribute('x', x0);
  rect.setAttribute('y', y0);
  rect.setAttribute('width', Math.abs(x1 - x0));
  rect.setAttribute('height', Math.abs(y1 - y0));
  rect.setAttribute('fill', 'none');
  rect.setAttribute('stroke', PLACED_MARKER_COLOR);
  rect.setAttribute('stroke-width', '0.25');
  svg.appendChild(rect);
}

function addDnpMarker(svg, [x0, y0, x1, y1]) {
  [[x0, y0, x1, y1], [x1, y0, x0, y1]].forEach(([ax, ay, bx, by]) => {
    const ln = document.createElementNS(SVG_NS, 'line');
    ln.setAttribute('class', 'dnp-marker');
    ln.setAttribute('x1', ax);
    ln.setAttribute('y1', ay);
    ln.setAttribute('x2', bx);
    ln.setAttribute('y2', by);
    ln.setAttribute('stroke', DNP_MARKER_COLOR);
    ln.setAttribute('stroke-width', '0.25');
    ln.setAttribute('stroke-linecap', 'round');
    svg.appendChild(ln);
  });
}

function updateLabels() {
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  svg.querySelectorAll('.desig-label').forEach(el => el.remove());
  const active = state.labels ? activeDesignators() : null;
  if (!active) return;
  const hidden = state.hidden[state.side] || new Set();
  active.forEach(desig => {
    if (hidden.has(desig)) return;
    const b = state.bounds[desig];
    if (!b || b.length < 4) return;
    const [x0, y0, x1, y1] = b;
    const w = x1 - x0, h = y1 - y0;
    // Fit within the part: scale to the short side, shrink for long refs,
    // floor so tiny parts still get a legible label.
    let size = Math.min(Math.min(w, h) * 0.55, (w * 1.6) / Math.max(desig.length, 1));
    size = Math.max(size, 0.25);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('class', 'desig-label');
    t.setAttribute('x', cx);
    t.setAttribute('y', cy + size * 0.35);
    t.setAttribute('font-size', size);
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('fill', '#ffe000');
    t.setAttribute('font-family', 'sans-serif');
    if (state.side === 'BOTTOM') {
      // Counter-mirror so the outer CSS flip leaves the text readable
      t.setAttribute('transform', `translate(${2 * cx} 0) scale(-1 1)`);
    }
    t.textContent = desig;
    svg.appendChild(t);
  });
}

// ── Component lookup + identify ───────────────────────────────────────────────
function componentFromElement(el) {
  while (el && el !== boardTransform && el !== document.body) {
    const d = el.getAttribute ? el.getAttribute('data-component') : null;
    if (d) return d;
    el = el.parentElement;
  }
  return null;
}

function componentFromPoint(cx, cy) {
  return componentFromElement(document.elementFromPoint(cx, cy));
}

function identify(desig) {
  const idx = state.bom.findIndex(r => r.designators.includes(desig));
  let msg = desig;
  if (idx >= 0) {
    const row = state.bom[idx];
    msg += ' — ' + row.comment;
    if (row.description) msg += ' — ' + row.description;
    msg += `  (row ${idx + 1})`;
    const tr = bomBody.children[idx];
    if (tr && tr.style.display !== 'none') {
      tr.scrollIntoView({ block: 'nearest' });
      tr.classList.remove('flash');
      void tr.offsetWidth;  // restart the CSS animation
      tr.classList.add('flash');
    }
  }
  setStatus(msg);
}

// ── Double-click / tap toggle ─────────────────────────────────────────────────
async function onSvgDblClick(e) {
  const desig = componentFromElement(e.target) || componentFromPoint(e.clientX, e.clientY);
  if (!desig) return;
  const resp = await postToggle(desig, state.step, state.side);
  if (resp) {
    undoStack.push(desig);
    if (undoStack.length > 200) undoStack.shift();
    redoStack.length = 0;
  }
}

async function postToggle(desig, step, side) {
  try {
    const resp = await fetchJson('/api/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ designator: desig, step, side }),
    });
    if (!resp.ok) {
      setStatus(`Cannot toggle ${desig}: ${resp.error}`);
      return null;
    }
    if (resp.now_placed) state.placed.add(desig);
    else state.placed.delete(desig);
    if (resp.bom_row) updateBomRow(resp.bom_row);
    updateMarkers();
    updateProgress();
    if (state.hideFitted || searchInput.value.trim()) applyRowFilters();
    setStatus((resp.now_placed ? 'Placed: ' : 'Unplaced: ') + desig);
    return resp;
  } catch (e) {
    setStatus('Toggle error: ' + e.message);
    return null;
  }
}

// ── Undo / redo ───────────────────────────────────────────────────────────────
function sideOf(desig) {
  // hidden.TOP = hidden when viewing TOP = bottom-side parts
  return state.hidden.TOP.has(desig) ? 'BOTTOM' : 'TOP';
}

async function undoToggle() {
  if (!undoStack.length) return;
  const desig = undoStack.pop();
  redoStack.push(desig);
  // step -1 skips the current-step restriction server-side
  const resp = await postToggle(desig, -1, sideOf(desig));
  if (resp) setStatus(`Undo: ${desig} is now ${resp.now_placed ? 'placed' : 'unplaced'}`);
}

async function redoToggle() {
  if (!redoStack.length) return;
  const desig = redoStack.pop();
  undoStack.push(desig);
  const resp = await postToggle(desig, -1, sideOf(desig));
  if (resp) setStatus(`Redo: ${desig} is now ${resp.now_placed ? 'placed' : 'unplaced'}`);
}

// ── Pan / zoom ────────────────────────────────────────────────────────────────
function applyTransform() {
  boardTransform.style.transform =
    `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`;
}

function fitView() {
  const cW = boardContainer.clientWidth;
  const cH = boardContainer.clientHeight;
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  let svgW, svgH;
  const vbAttr = svg.getAttribute('viewBox');
  if (vbAttr) {
    const parts = vbAttr.trim().split(/[\s,]+/).map(Number);
    svgW = parts[2]; svgH = parts[3];
  } else {
    svgW = parseFloat(svg.getAttribute('width')  || '200');
    svgH = parseFloat(svg.getAttribute('height') || '200');
  }
  if (!svgW || !svgH) return;
  view.scale = Math.min(cW / svgW, cH / svgH) * 0.95;
  view.tx    = (cW - svgW * view.scale) / 2;
  view.ty    = (cH - svgH * view.scale) / 2;
  applyTransform();
}

function fitSelection() {
  const active = activeDesignators();
  if (!active || !state.viewbox) return;
  const [vbx, vby, vbw] = state.viewbox;
  const hidden = state.hidden[state.side] || new Set();
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  active.forEach(d => {
    if (hidden.has(d)) return;
    const b = state.bounds[d];
    if (!b || b.length < 4) return;
    x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]);
    x1 = Math.max(x1, b[2]); y1 = Math.max(y1, b[3]);
  });
  if (x0 === Infinity) return;
  // SVG user units → element-local CSS px (the element box equals the viewBox size)
  let ex0 = x0 - vbx, ex1 = x1 - vbx;
  if (state.side === 'BOTTOM') {  // the svg element is mirrored via CSS
    [ex0, ex1] = [vbw - ex1, vbw - ex0];
  }
  const ey0 = y0 - vby, ey1 = y1 - vby;
  const pad = Math.max(ex1 - ex0, ey1 - ey0) * 0.15 + 1;
  const rx0 = ex0 - pad, ry0 = ey0 - pad;
  const rw = (ex1 - ex0) + 2 * pad, rh = (ey1 - ey0) + 2 * pad;
  const cW = boardContainer.clientWidth, cH = boardContainer.clientHeight;
  const scale = Math.min(cW / rw, cH / rh);
  view.scale = Math.min(Math.max(scale, 0.02), 200);
  view.tx = (cW - rw * view.scale) / 2 - rx0 * view.scale;
  view.ty = (cH - rh * view.scale) / 2 - ry0 * view.scale;
  applyTransform();
}

boardContainer.addEventListener('wheel', e => {
  e.preventDefault();
  const rect  = boardContainer.getBoundingClientRect();
  const mx    = e.clientX - rect.left;
  const my    = e.clientY - rect.top;
  const zoom  = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  const newSc = view.scale * zoom;
  if (newSc < 0.02 || newSc > 200) return;
  view.tx    = mx - zoom * (mx - view.tx);
  view.ty    = my - zoom * (my - view.ty);
  view.scale = newSc;
  applyTransform();
}, { passive: false });

// ── Pointer-based pan / pinch / tap (mouse + touch) ───────────────────────────
const activePointers = new Map();  // pointerId → {x, y}
let pressInfo = null;              // primary pointer for click/tap detection
let lastTap = { time: 0, x: 0, y: 0 };

boardContainer.addEventListener('pointerdown', e => {
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  try { boardContainer.setPointerCapture(e.pointerId); } catch {}
  activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (activePointers.size === 1) {
    pressInfo = { id: e.pointerId, x: e.clientX, y: e.clientY,
                  moved: false, type: e.pointerType };
  } else {
    pressInfo = null;  // second finger down → pinch, not a tap
  }
});

boardContainer.addEventListener('pointermove', e => {
  const p = activePointers.get(e.pointerId);
  if (!p) return;
  if (pressInfo && e.pointerId === pressInfo.id
      && Math.abs(e.clientX - pressInfo.x) + Math.abs(e.clientY - pressInfo.y) > 4) {
    pressInfo.moved = true;
  }
  if (activePointers.size === 1) {
    view.tx += e.clientX - p.x;
    view.ty += e.clientY - p.y;
    p.x = e.clientX; p.y = e.clientY;
    applyTransform();
  } else if (activePointers.size === 2) {
    const [a, b] = [...activePointers.values()];
    const before = Math.hypot(a.x - b.x, a.y - b.y);
    p.x = e.clientX; p.y = e.clientY;
    const [a2, b2] = [...activePointers.values()];
    const after = Math.hypot(a2.x - b2.x, a2.y - b2.y);
    if (before > 1 && after > 1) {
      const rect = boardContainer.getBoundingClientRect();
      const mx = (a2.x + b2.x) / 2 - rect.left;
      const my = (a2.y + b2.y) / 2 - rect.top;
      const zoom = after / before;
      const newSc = view.scale * zoom;
      if (newSc >= 0.02 && newSc <= 200) {
        view.tx = mx - zoom * (mx - view.tx);
        view.ty = my - zoom * (my - view.ty);
        view.scale = newSc;
        applyTransform();
      }
    }
  }
});

function onPointerEnd(e) {
  if (!activePointers.has(e.pointerId)) return;
  activePointers.delete(e.pointerId);
  if (!pressInfo || e.pointerId !== pressInfo.id) return;
  const info = pressInfo;
  pressInfo = null;
  if (info.moved || e.type === 'pointercancel') return;

  if (info.type === 'touch') {
    // Manual double-tap detection: dblclick isn't synthesized with touch-action:none
    const now = Date.now();
    const isDouble = now - lastTap.time < 350
      && Math.abs(e.clientX - lastTap.x) < 20
      && Math.abs(e.clientY - lastTap.y) < 20;
    lastTap = { time: now, x: e.clientX, y: e.clientY };
    const desig = componentFromPoint(e.clientX, e.clientY);
    if (isDouble) {
      lastTap.time = 0;
      if (desig) onSvgDblClick({ target: null, clientX: e.clientX, clientY: e.clientY });
    } else if (desig) {
      identify(desig);
    }
  } else {
    // Mouse click (no drag): identify. Double-click toggling stays on dblclick.
    const desig = componentFromPoint(e.clientX, e.clientY);
    if (desig) identify(desig);
  }
}
boardContainer.addEventListener('pointerup', onPointerEnd);
boardContainer.addEventListener('pointercancel', onPointerEnd);

// ── Keyboard navigation ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  const key = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && key === 'z' && !e.shiftKey) {
    e.preventDefault();
    undoToggle();
  } else if ((e.ctrlKey || e.metaKey) && (key === 'y' || (key === 'z' && e.shiftKey))) {
    e.preventDefault();
    redoToggle();
  } else if ((e.key === 'ArrowDown' || e.key === 'ArrowRight') && !e.ctrlKey) {
    e.preventDefault();
    if (state.loaded && state.step < state.bom.length - 1) selectStep(state.step + 1);
  } else if ((e.key === 'ArrowUp' || e.key === 'ArrowLeft') && !e.ctrlKey) {
    e.preventDefault();
    if (state.loaded && state.step > 0) selectStep(state.step - 1);
  } else if (e.key === '0') {
    fitView();
  }
});

// ── Resizable split ───────────────────────────────────────────────────────────
const resizer    = document.getElementById('resizer');
const boardPanel = document.getElementById('board-panel');
let resizing = false, resizeStartY = 0, resizeStartH = 0;

resizer.addEventListener('pointerdown', e => {
  resizing     = true;
  resizeStartY = e.clientY;
  resizeStartH = boardPanel.offsetHeight;
  try { resizer.setPointerCapture(e.pointerId); } catch {}
  document.body.style.cursor = 'row-resize';
  e.preventDefault();
});
resizer.addEventListener('pointermove', e => {
  if (!resizing) return;
  const newH = Math.max(80, resizeStartH + (e.clientY - resizeStartY));
  boardPanel.style.height = newH + 'px';
});
resizer.addEventListener('pointerup', () => {
  if (resizing) { resizing = false; document.body.style.cursor = ''; }
});

// ── Utility ───────────────────────────────────────────────────────────────────
async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
  return resp.json();
}

function setStatus(msg) {
  statusbar.textContent = msg;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
init();
