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
  loaded: false,
};

// designator → [svg elements] — rebuilt each time the base SVG is injected
const compElems = new Map();

// ── View / pan-zoom state ─────────────────────────────────────────────────────
const view = { scale: 1, tx: 0, ty: 0, dragging: false, lastX: 0, lastY: 0 };

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
  state.loaded = true;
  state.step   = -1;
  state.side   = 'TOP';
  boardNameEl.textContent = data.board_name;
  renderBomTable();
  enableControls(true);
  updateSideButtons();
  updateNavigation();
  setStatus('Loaded ' + data.board_name + ' — ' + data.bom.length + ' BOM groups');
}

// ── Load dialog ───────────────────────────────────────────────────────────────
function showLoadOverlay() {
  loadOverlay.classList.remove('hidden');
  loadError.classList.add('hidden');
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

// ── Controls ──────────────────────────────────────────────────────────────────
function enableControls(on) {
  ['btn-prev','btn-next','btn-fit','btn-clear','btn-top','btn-bot'].forEach(id => {
    document.getElementById(id).disabled = !on;
  });
}

document.getElementById('btn-fit').addEventListener('click', fitView);
document.getElementById('btn-clear').addEventListener('click', clearSelection);
document.getElementById('btn-prev').addEventListener('click', () => {
  if (state.step > 0) selectStep(state.step - 1);
});
document.getElementById('btn-next').addEventListener('click', () => {
  if (state.step < state.bom.length - 1) selectStep(state.step + 1);
});
document.getElementById('btn-top').addEventListener('click', () => setSide('TOP'));
document.getElementById('btn-bot').addEventListener('click', () => setSide('BOTTOM'));

function clearSelection() {
  state.step = -1;
  document.querySelectorAll('#bom-body tr.selected').forEach(r => r.classList.remove('selected'));
  updateNavigation();
  refreshBoard();
}

function setSide(side) {
  if (side === state.side) return;
  state.side = side;
  updateSideButtons();
  applySideFlip();
  refreshBoard();
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
    { text: row.comment },
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
    if (c.sideDone) td.classList.add('cell-side-done');
    tr.appendChild(td);
  });

  if (row.all_done) tr.classList.add('row-all-done');
  return tr;
}

function updateBomRow(rowData) {
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

function selectStep(idx) {
  if (idx === state.step) return;
  state.step = idx;
  document.querySelectorAll('#bom-body tr').forEach((tr, i) => {
    tr.classList.toggle('selected', i === idx);
  });
  const tr = bomBody.children[idx];
  if (tr) tr.scrollIntoView({ block: 'nearest' });
  updateNavigation();
  refreshBoard();
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
  });
}

// Re-apply dim/hide classes and markers for the current step + side.
function refreshBoard() {
  applyHighlight();
  updateMarkers();
}

function applyHighlight() {
  const hidden = state.hidden[state.side] || new Set();
  const row = state.step >= 0 ? state.bom[state.step] : null;
  const selected = row ? new Set(row.designators) : null;
  compElems.forEach((elems, desig) => {
    const hide = hidden.has(desig);
    const dim  = !hide && selected !== null && !selected.has(desig);
    elems.forEach(el => {
      el.classList.toggle('comp-hidden', hide);
      el.classList.toggle('comp-dim', dim);
    });
  });
}

// Mirror the board for bottom-side view. Markers live inside the SVG, so they
// flip along with the components and stay aligned automatically.
function applySideFlip() {
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  svg.style.transformOrigin = '50% 50%';
  svg.style.transform = state.side === 'BOTTOM' ? 'scaleX(-1)' : '';
}

// ── Placed / DNP markers ──────────────────────────────────────────────────────
const SVG_NS = 'http://www.w3.org/2000/svg';

function updateMarkers() {
  const svg = boardTransform.querySelector('svg');
  if (!svg) return;
  svg.querySelectorAll('.placed-marker, .dnp-marker').forEach(el => el.remove());
  if (state.step < 0 || !state.bom[state.step]) return;
  const hidden = state.hidden[state.side] || new Set();
  state.bom[state.step].designators.forEach(desig => {
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
  rect.setAttribute('stroke', '#00cc44');
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
    ln.setAttribute('stroke', '#dd0000');
    ln.setAttribute('stroke-width', '0.25');
    ln.setAttribute('stroke-linecap', 'round');
    svg.appendChild(ln);
  });
}

// ── Double-click toggle ───────────────────────────────────────────────────────
async function onSvgDblClick(e) {
  // Walk up from click target to find data-component attribute
  let el = e.target;
  let desig = null;
  while (el && el !== boardTransform) {
    desig = el.getAttribute ? el.getAttribute('data-component') : null;
    if (desig) break;
    el = el.parentElement;
  }
  if (!desig) return;

  try {
    const resp = await fetchJson('/api/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ designator: desig, step: state.step, side: state.side }),
    });
    if (!resp.ok) {
      setStatus(`Cannot toggle ${desig}: ${resp.error}`);
      return;
    }
    if (resp.now_placed) state.placed.add(desig);
    else state.placed.delete(desig);
    if (resp.bom_row) updateBomRow(resp.bom_row);
    updateMarkers();
    setStatus((resp.now_placed ? 'Placed: ' : 'Unplaced: ') + desig);
  } catch (e) {
    setStatus('Toggle error: ' + e.message);
  }
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

boardContainer.addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  view.dragging = true;
  view.lastX = e.clientX;
  view.lastY = e.clientY;
});
document.addEventListener('mousemove', e => {
  if (!view.dragging) return;
  view.tx += e.clientX - view.lastX;
  view.ty += e.clientY - view.lastY;
  view.lastX = e.clientX;
  view.lastY = e.clientY;
  applyTransform();
});
document.addEventListener('mouseup', () => { view.dragging = false; });

// ── Keyboard navigation ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if ((e.key === 'ArrowDown' || e.key === 'ArrowRight') && !e.ctrlKey) {
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

resizer.addEventListener('mousedown', e => {
  resizing     = true;
  resizeStartY = e.clientY;
  resizeStartH = boardPanel.offsetHeight;
  document.body.style.cursor = 'row-resize';
  e.preventDefault();
});
document.addEventListener('mousemove', e => {
  if (!resizing) return;
  const newH = Math.max(80, resizeStartH + (e.clientY - resizeStartY));
  boardPanel.style.height = newH + 'px';
});
document.addEventListener('mouseup', () => {
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
