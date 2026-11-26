/* AP Weather Intelligence - static dashboard. Reads data/*.json written by the pipeline. */
(function () {
  'use strict';
  const S = { latest: null, topo: null, idx: {}, alerts: null, reports: null, content: null, sources: null, events: null,
              hourly: {}, history: {}, sel: null, done: {}, mapOpts: {} };
  const LEVELS = ['NONE', 'INFORMATIONAL', 'MODERATE', 'HIGH', 'CRITICAL'];
  const RANK = Object.fromEntries(LEVELS.map((l, i) => [l, i]));
  const RISK_COLOR = { CRITICAL: '#b42318', HIGH: '#e8590c', MODERATE: '#f5b400', INFORMATIONAL: '#3b6fb6', NONE: '#9ed4b5' };
  const TZ = 'America/Chicago';
  const ATLAS = 'https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-10m.json';
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s === undefined || s === null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const badge = (lv) => `<span class="badge lv-${lv || 'NONE'}">${esc(lv || 'NONE')}</span>`;
  const num = (v) => v !== null && v !== undefined && !Number.isNaN(Number(v));
  const fmt = (v, unit = '', nd = 0) => (num(v) ? `${Number(v).toFixed(nd)}${unit}` : '-');
  const dtf = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
  const dshort = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', hour: 'numeric' });
  const fmtTime = (iso) => { if (!iso) return '-'; const d = new Date(iso); return isNaN(d) ? esc(iso) : dtf.format(d); };
  const ago = (iso) => { if (!iso) return 'never'; const h = (Date.now() - new Date(iso)) / 3.6e6; if (h < 1) return `${Math.max(0, Math.round(h * 60))} min ago`; if (h < 48) return `${h.toFixed(1)} h ago`; return `${Math.round(h / 24)} d ago`; };
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } } };
  const getJSON = async (p) => { const r = await fetch(`${p}?v=${Date.now()}`); if (!r.ok) throw new Error(`${p}: HTTP ${r.status}`); return r.json(); };
  const crop = (id) => (S.latest && S.latest.crops[id]) || id;
  const hazard = (id) => (S.latest && S.latest.hazard_labels[id]) || id;
  const thr = (domain, range) => d3.scaleThreshold().domain(domain).range(range);
  const RAIN = thr([0.01, 0.1, 0.25, 0.5, 1, 1.5, 2, 3], ['#f4f6f8', '#d9f0d3', '#a6dba0', '#5aae61', '#1b7837', '#4393c3', '#2166ac', '#762a83', '#40004b']);
  const cpcColor = (v, kind) => {
    if (!v) return '#f2f4f7';
    const [cat, p] = v;
    const i = p >= 70 ? 4 : p >= 60 ? 3 : p >= 50 ? 2 : p >= 40 ? 1 : 0;
    if (cat === 'A') return (kind === 'p' ? ['#c7e9c0', '#a1d99b', '#74c476', '#31a354', '#006d2c'] : ['#fcbba1', '#fc9272', '#fb6a4a', '#de2d26', '#a50f15'])[i];
    if (cat === 'B') return (kind === 'p' ? ['#f6e8c3', '#dfc27d', '#bf812d', '#8c510a', '#543005'] : ['#c6dbef', '#9ecae1', '#6baed6', '#3182bd', '#08519c'])[i];
    if (cat === 'N') return '#d9d9d9';
    return '#f7f7f7';
  };
  const LAYERS = {
    risk: { label: 'Risk level (highest in county)', get: (c) => c.lv || 'NONE', color: (v) => RISK_COLOR[v] || RISK_COLOR.NONE,
      legend: () => LEVELS.slice().reverse().map((l) => [RISK_COLOR[l], l === 'NONE' ? 'Monitored - no risk' : l]), show: (v) => v },
    tmin7d: { label: 'Coldest forecast low, next 7 days (F)', get: (c) => c.fc.tmin7d, unit: ' F',
      scale: thr([0, 10, 20, 24, 28, 32, 36, 40, 50, 60], ['#3f007d', '#54278f', '#6a51a3', '#2171b5', '#4292c6', '#9ecae1', '#c6dbef', '#fee391', '#fec44f', '#fe9929', '#d95f0e']) },
    tmin72: { label: 'Coldest forecast low, next 72 h (F)', get: (c) => c.fc.tmin72, unit: ' F',
      scale: thr([0, 10, 20, 24, 28, 32, 36, 40, 50, 60], ['#3f007d', '#54278f', '#6a51a3', '#2171b5', '#4292c6', '#9ecae1', '#c6dbef', '#fee391', '#fec44f', '#fe9929', '#d95f0e']) },
    tmax7d: { label: 'Warmest forecast high, next 7 days (F)', get: (c) => c.fc.tmax7d, unit: ' F',
      scale: thr([40, 50, 60, 70, 80, 90, 95, 100], ['#2166ac', '#67a9cf', '#d1e5f0', '#f7f7f7', '#fddbc7', '#f4a582', '#d6604d', '#b2182b', '#67001f']) },
    qpf72: { label: 'NWS forecast rain, next 72 h (in)', get: (c) => c.fc.qpf72, unit: ' in', nd: 2, scale: RAIN },
    qpf7d: { label: 'NWS forecast rain, next 7 days (in)', get: (c) => c.fc.qpf7d, unit: ' in', nd: 2, scale: RAIN },
    obs7: { label: 'Observed precipitation, last 7 days (in, PRISM via RCC-ACIS)', get: (c) => (c.obs ? c.obs.pcpn7 : null), unit: ' in', nd: 2, scale: RAIN },
    snow72: { label: 'Forecast snowfall, next 72 h (in)', get: (c) => c.fc.snow72, unit: ' in', nd: 1,
      scale: thr([0.1, 1, 2, 4, 6, 8, 12], ['#f4f6f8', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#3182bd', '#08519c', '#08306b']) },
    gust72: { label: 'Highest forecast wind gust, next 72 h (mph)', get: (c) => c.fc.gust72, unit: ' mph',
      scale: thr([20, 30, 40, 50, 58, 70], ['#f4f6f8', '#fde0dd', '#fcc5c0', '#fa9fb5', '#f768a1', '#c51b8a', '#7a0177']) },
    dm: { label: 'U.S. Drought Monitor: % of county in D1 or worse', get: (c) => (c.dm ? c.dm.d1 : null), unit: '%',
      scale: thr([1, 10, 25, 50, 75, 90], ['#f4f6f8', '#ffffd4', '#fee391', '#fec44f', '#fe9929', '#d95f0e', '#993404']) },
    spc: { label: 'SPC convective outlook (max of Days 1-3)', get: (c) => Math.max(c.spc.d1_cat || 0, c.spc.d2_cat || 0, c.spc.d3_cat || 0),
      cat: { 0: ['#f2f4f7', 'None'], 1: ['#c1e9c1', 'General thunder'], 2: ['#66a366', 'Marginal'], 3: ['#f6f67b', 'Slight'], 4: ['#e6c27a', 'Enhanced'], 5: ['#e67e7e', 'Moderate'], 6: ['#ff7fff', 'High'] } },
    ero: { label: 'WPC Excessive Rainfall Outlook (max of Days 1-3)', get: (c) => Math.max(c.wpc.ero1 || 0, c.wpc.ero2 || 0, c.wpc.ero3 || 0),
      cat: { 0: ['#f2f4f7', 'None'], 1: ['#7fd67f', 'Marginal'], 2: ['#ffd24d', 'Slight'], 3: ['#ff5f5f', 'Moderate'], 4: ['#ff4dff', 'High'] } },
    t814: { label: 'CPC 8-14 day temperature outlook', get: (c) => c.cpc.t814, color: (v) => cpcColor(v, 't'), show: (v) => (v ? `${v[0] === 'EC' ? 'Equal chances' : v[1] + '% ' + ({ A: 'above', B: 'below', N: 'near' })[v[0]]}` : '-'),
      legend: () => [['#a50f15', 'Above (70%+)'], ['#fc9272', 'Above (40-50%)'], ['#d9d9d9', 'Near normal'], ['#9ecae1', 'Below (40-50%)'], ['#08519c', 'Below (70%+)'], ['#f7f7f7', 'Equal chances']] },
    p814: { label: 'CPC 8-14 day precipitation outlook', get: (c) => c.cpc.p814, color: (v) => cpcColor(v, 'p'), show: (v) => (v ? `${v[0] === 'EC' ? 'Equal chances' : v[1] + '% ' + ({ A: 'above', B: 'below', N: 'near' })[v[0]]}` : '-'),
      legend: () => [['#006d2c', 'Above (70%+)'], ['#a1d99b', 'Above (40-50%)'], ['#d9d9d9', 'Near normal'], ['#dfc27d', 'Below (40-50%)'], ['#543005', 'Below (70%+)'], ['#f7f7f7', 'Equal chances']] },
  };
  function layerColor(layer, v) {
    const L = LAYERS[layer];
    if (L.cat) return (L.cat[v] || L.cat[0])[0];
    if (L.color) return L.color(v);
    return num(v) ? L.scale(v) : '#eceff3';
  }
  function layerText(layer, v) {
    const L = LAYERS[layer];
    if (L.cat) return (L.cat[v] || L.cat[0])[1];
    if (L.show) return L.show(v);
    return fmt(v, L.unit || '', L.nd || 0);
  }
  function legendHTML(layer) {
    const L = LAYERS[layer];
    let items = [];
    if (L.legend) items = L.legend();
    else if (L.cat) items = Object.values(L.cat);
    else {
      const d = L.scale.domain(); const r = L.scale.range();
      items = r.map((c, i) => [c, i === 0 ? `< ${d[0]}` : i === r.length - 1 ? `>= ${d[d.length - 1]}` : `${d[i - 1]} - ${d[i]}`]);
    }
    return `<b style="margin-right:6px">${esc(L.label)}</b>` + items.map(([c, t]) => `<span><i style="background:${c}"></i>${esc(t)}</span>`).join('') +
      '<span><i style="background:#e3e8ee"></i>Not monitored</span>';
  }

  // ---------------------------------------------------------------- header
  function renderStatus() {
    const L = S.latest;
    const el = $('#status');
    const parts = [];
    if (L.simulation) parts.push('<span class="pill sim">SIMULATION DATA</span>');
    const age = L.cycle.utc ? (Date.now() - new Date(L.cycle.utc)) / 3.6e6 : 99;
    const cls = age < 4 ? '' : age < 8 ? 'warn' : 'bad';
    parts.push(`<span class="pill ${cls}"><span class="dot"></span>Forecast cycle ${L.cycle.utc ? ago(L.cycle.utc) : 'not yet run'}</span>`);
    parts.push(`<span class="pill">Updated ${esc(fmtTime(L.generated))}</span>`);
    if (L.next_cycle) parts.push(`<span class="pill">Next cycle ~${esc(fmtTime(L.next_cycle))}</span>`);
    el.innerHTML = parts.join('');
  }

  // ---------------------------------------------------------------- map
  async function atlas() {
    if (!S.topo) S.topo = await d3.json(ATLAS);
    return S.topo;
  }
  function stateFipsSet() { return new Set(S.latest.states.map((s) => s.fips)); }

  async function drawMap(el, opts) {
    const host = typeof el === 'string' ? $(el) : el;
    let t;
    try { t = await atlas(); } catch (e) { host.innerHTML = `<div class="empty">Map geometry could not be loaded (${esc(e.message)}).</div>`; return; }
    const states = topojson.feature(t, t.objects.states).features;
    const counties = topojson.feature(t, t.objects.counties).features;
    const mon = stateFipsSet();
    const focus = opts.state ? S.latest.states.find((s) => s.abbr === opts.state) : null;
    const region = states.filter((f) => (focus ? f.id === focus.fips : mon.has(f.id)));
    const W = 960; const H = opts.height || 620;
    const proj = d3.geoAlbersUsa().fitExtent([[8, 8], [W - 8, H - 8]], { type: 'FeatureCollection', features: region });
    const path = d3.geoPath(proj);
    const svg = d3.select(host).html('').append('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', 'Map of monitored counties');
    const g = svg.append('g');
    g.append('g').selectAll('path').data(states.filter((f) => !mon.has(f.id))).join('path').attr('d', path).style('fill', 'var(--chip)').style('stroke', 'var(--line)');
    const cs = counties.filter((f) => mon.has(String(f.id).slice(0, 2)));
    const layer = opts.layer || 'risk';
    g.append('g').selectAll('path').data(cs).join('path')
      .attr('class', (d) => 'county' + (S.idx[d.id] ? '' : ' unmon') + (S.sel === d.id ? ' sel' : ''))
      .attr('d', path)
      .style('fill', (d) => fillFor(d.id, layer, opts))
      .on('mousemove', (ev, d) => tip(ev, d.id, layer))
      .on('mouseleave', () => { $('#tooltip').style.display = 'none'; })
      .on('click', (ev, d) => { if (S.idx[d.id]) selectCounty(d.id, true); });
    g.append('g').selectAll('path').data(states).join('path').attr('class', (d) => 'statebd' + (mon.has(d.id) ? ' mon' : '')).attr('d', path);
    if (opts.legend) $(opts.legend).innerHTML = legendHTML(layer);
  }
  function fillFor(fips, layer, opts) {
    const c = S.idx[fips];
    if (!c) return '#e3e8ee';
    if (opts.crop && !(c.c || []).includes(opts.crop)) return '#e3e8ee';
    if (opts.hazard && !(c.hz || []).some((h) => h[0] === opts.hazard)) return '#e3e8ee';
    return layerColor(layer, LAYERS[layer].get(c));
  }
  function tip(ev, fips, layer) {
    const c = S.idx[fips];
    const el = $('#tooltip');
    if (!c) { el.style.display = 'none'; return; }
    const hz = (c.hz || []).slice(0, 3).map((h) => `${badge(h[1])} ${esc(hazard(h[0]))} (${esc(crop(h[2]))})`).join('<br>');
    el.innerHTML = `<b>${esc(c.n)}, ${esc(c.s)}</b><br><span class="muted">${esc((c.c || []).map(crop).join(', '))}</span><br>` +
      `${esc(LAYERS[layer].label)}: <b>${esc(layerText(layer, LAYERS[layer].get(c)))}</b>` + (hz ? `<br>${hz}` : '') +
      (c.al && c.al.length ? `<br><b>NWS:</b> ${esc(c.al.join(', '))}` : '');
    el.style.display = 'block';
    const pad = 14;
    let x = ev.pageX + pad; let y = ev.pageY + pad;
    if (x + 290 > window.scrollX + window.innerWidth) x = ev.pageX - 290;
    el.style.left = `${x}px`; el.style.top = `${y}px`;
  }

  // ---------------------------------------------------------------- overview
  function renderOverview() {
    const L = S.latest;
    const ev = L.events || []; const al = L.alerts || [];
    const hi = ev.filter((e) => RANK[e.level] >= 3).length;
    const mod = ev.filter((e) => e.level === 'MODERATE').length;
    const alHi = al.filter((a) => RANK[a.level] >= 3).length;
    const k = [
      ['High / critical events', hi, 'forecast-based crop risk'],
      ['Moderate events', mod, 'developing conditions'],
      ['Official NWS alerts', al.length, `${alHi} high / critical`],
      ['Monitored counties', L.counties.length, `${new Set(L.counties.map((c) => c.s)).size} states, ${new Set(L.counties.flatMap((c) => c.c || [])).size} crops`],
      ['Forecast cycle', L.cycle.utc ? new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(new Date(L.cycle.utc)) : '-', `${L.cycle.ok} ok / ${L.cycle.failed} failed counties`],
    ];
    $('#kpis').innerHTML = k.map(([a, b, c]) => `<div class="kpi"><div class="label">${esc(a)}</div><div class="value">${esc(b)}</div><div class="sub">${esc(c)}</div></div>`).join('');
    const dev = [];
    al.filter((a) => RANK[a.level] >= 3).slice(0, 4).forEach((a) => dev.push(`${badge(a.level)} <b>${esc(a.event)}</b> - ${esc(a.states)} (${a.fips.length} ag counties) until ${esc(fmtTime(a.ends))}`));
    ev.slice(0, 6).forEach((e) => dev.push(`${badge(e.level)} <b>${esc(hazard(e.hazard))}</b> - ${esc(e.state)}: ${esc(e.crop_names.join(', '))}, ${e.n_counties} counties`));
    $('#top-dev').innerHTML = dev.length ? `<ul class="list">${dev.map((d) => `<li>${d}</li>`).join('')}</ul>` : '<div class="empty">No high-priority developments right now.</div>';
    $('#events-overview').innerHTML = ev.length ? ev.slice(0, 8).map(eventCard).join('') : '<div class="empty">No active forecast-based risk events.</div>';
    $('#statecards').innerHTML = L.states.map((s) => {
      const a = s.agg || {};
      const crops = Object.entries(s.stages || {}).map(([k, v]) => `${crop(k)}`).join(', ');
      const color = RISK_COLOR[s.level] || RISK_COLOR.NONE;
      return `<div class="statecard" data-state="${s.abbr}" style="border-left-color:${color}"><div class="t"><span>${esc(s.name)}</span>${badge(s.level)}</div>` +
        `<div class="m">${esc(s.tier || '')} &middot; ${s.n_counties} counties &middot; ${esc(crops)}</div>` +
        `<div class="m">Low 7d ${fmt(a.tmin7d, ' F')} &middot; High 7d ${fmt(a.tmax7d, ' F')} &middot; Rain 7d avg ${fmt(a.qpf7d_mean, ' in', 2)}</div></div>`;
    }).join('');
    document.querySelectorAll('.statecard').forEach((el) => el.addEventListener('click', () => { $('#state').value = el.dataset.state; show('map'); refreshMap(); }));
    drawMap('#map-overview', { layer: 'risk', legend: '#legend-overview', height: 560 });
  }
  function eventCard(e) {
    const counties = (e.counties || []).slice(0, 8).map((c) => c.name.replace(' County', '').replace(' Parish', '')).join(', ');
    const stats = Object.entries(e.stats || {}).filter(([k, v]) => v !== null && !['worst_fips', 'worst_county'].includes(k)).slice(0, 3)
      .map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(typeof v === 'number' ? +v.toFixed(2) : v)}</span>`).join('');
    return `<div class="evcard" style="border-left-color:${RISK_COLOR[e.level]}"><h4>${badge(e.level)} ${esc(hazard(e.hazard))} - ${esc(e.state)}</h4>` +
      `<div class="small">${esc(e.crop_names.join(', '))} &middot; ${e.n_counties} counties: ${esc(counties)}${e.n_counties > 8 ? '...' : ''}</div>` +
      `<div class="small muted">Window from ${esc(fmtTime(e.window_start))} &middot; first detected ${esc(fmtTime(e.first_detected))}</div><div>${stats}</div></div>`;
  }
  // ---------------------------------------------------------------- map tab
  function initMapControls() {
    const L = S.latest;
    $('#layer').innerHTML = Object.entries(LAYERS).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join('');
    $('#crop').innerHTML += Object.entries(L.crops).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('#state').innerHTML += L.states.map((s) => `<option value="${s.abbr}">${esc(s.name)}</option>`).join('');
    $('#hazard').innerHTML += Object.entries(L.hazard_labels).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('#countylist').innerHTML = L.counties.map((c) => `<option value="${esc(c.n)}, ${c.s}">`).join('');
    const saved = store.get('apwx-layer');
    if (saved && LAYERS[saved]) $('#layer').value = saved;
    ['#layer', '#crop', '#state', '#hazard'].forEach((s) => $(s).addEventListener('change', () => { store.set('apwx-layer', $('#layer').value); refreshMap(); }));
    $('#find').addEventListener('change', () => {
      const v = $('#find').value.toLowerCase();
      const c = L.counties.find((x) => `${x.n}, ${x.s}`.toLowerCase() === v) || L.counties.find((x) => x.n.toLowerCase().startsWith(v));
      if (c) { $('#state').value = c.s; refreshMap(); selectCounty(c.f, false); }
    });
  }
  function refreshMap() {
    drawMap('#map-main', { layer: $('#layer').value, crop: $('#crop').value, state: $('#state').value, hazard: $('#hazard').value, legend: '#legend-main', height: 620 });
  }

  async function selectCounty(fips, fromMap) {
    S.sel = fips;
    if (fromMap && !$('#view-map').classList.contains('active')) { $('#state').value = S.idx[fips].s; show('map'); refreshMap(); }
    document.querySelectorAll('path.county.sel').forEach((p) => p.classList.remove('sel'));
    d3.selectAll('#map-main path.county').filter((d) => d.id === fips).classed('sel', true).raise();
    const c = S.idx[fips];
    const fc = c.fc || {}; const now = fc.now || {};
    const obs = (S.latest.observations || []).find((o) => o.station === c.st);
    const crops = (c.c || []).map((k) => `<span class="chip">${esc(crop(k))}: ${Math.round((c.a[k] || 0) / 1000)}k ac (${fmt((c.sh || {})[k], '% of state', 1)})</span>`).join('');
    const stg = (S.latest.states.find((s) => s.abbr === c.s) || {}).stages || {};
    const stageRows = (c.c || []).map((k) => stg[k] ? `<li><b>${esc(crop(k))}:</b> ${esc(stg[k].detail || '')}</li>` : '').join('');
    const hz = (c.hz || []).map((h) => `<li>${badge(h[1])} ${esc(hazard(h[0]))} - ${esc(crop(h[2]))}</li>`).join('');
    const al = (S.latest.alerts || []).filter((a) => a.fips.includes(fips));
    const daily = (fc.daily || []).map((d) => `<tr><td class="nowrap">${esc(d.d.slice(5))}</td><td>${fmt(d.hi, ' F')}</td><td>${fmt(d.lo, ' F')}</td><td>${fmt(d.qpf, ' in', 2)}</td><td>${fmt(d.snow, ' in', 1)}</td><td>${fmt(d.pop, '%')}</td><td>${fmt(d.gust, ' mph')}</td></tr>`).join('');
    const spc = c.spc || {}; const wpc = c.wpc || {}; const cpc = c.cpc || {}; const dm = c.dm || null; const ob = c.obs || null;
    const cpcT = (v) => (v ? (v[0] === 'EC' ? 'equal chances' : `${v[1]}% ${({ A: 'above', B: 'below', N: 'near' })[v[0]]}`) : '-');
    $('#county-panel').innerHTML = `
      <h2>${esc(c.n)}, ${esc(c.s)} ${badge(c.lv)}<span class="hint">${esc(c.r || '')}</span></h2>
      <div>${crops}</div>
      ${hz ? `<h3>Crop risk</h3><ul class="list">${hz}</ul>` : '<p class="small muted">No crop-weather risk detected for this county in the latest cycle.</p>'}
      ${al.length ? `<h3>Official NWS alerts</h3><ul class="list">${al.map((a) => `<li>${badge(a.level)} <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.event)}</a> until ${esc(fmtTime(a.ends))}</li>`).join('')}</ul>` : ''}
      <h3>Current conditions</h3>
      <div class="kv"><div>Forecast, this hour</div><div>${fmt(now.t, ' F')} &middot; dew pt ${fmt(now.td, ' F')} &middot; wind ${fmt(now.wind, ' mph')} (gust ${fmt(now.gust, ' mph')}) &middot; sky ${fmt(now.sky, '%')} &middot; PoP ${fmt(now.pop, '%')}</div>
      <div>Observed${obs ? ` (${esc(obs.station)})` : ''}</div><div>${obs ? `${fmt(obs.temp_f, ' F')} &middot; ${esc(obs.text || '')} &middot; wind ${fmt(obs.wind_mph, ' mph')} &middot; ${esc(fmtTime(obs.time))}` : '<span class="muted">no station observation stored for this county</span>'}</div>
      <div>Observed precip</div><div>${ob ? `${fmt(ob.pcpn7, ' in', 2)} last 7 d &middot; ${fmt(ob.pcpn14, ' in', 2)} last 14 d (through ${esc(ob.through)})` : '-'}</div>
      <div>Forecast issued</div><div>${esc(fmtTime(fc.upd))}${fc.stale ? ' <b>(previous cycle reused)</b>' : ''}</div></div>
      <h3>Next 72 hours (hourly)</h3><div class="chart" id="hourly-chart"><div class="empty">Loading...</div></div>
      <h3>7-day forecast (NWS)</h3>
      <table class="data"><tr><th>Day</th><th>High</th><th>Low (night)</th><th>Rain</th><th>Snow</th><th>PoP</th><th>Gust</th></tr>${daily || '<tr><td colspan="7" class="muted">No forecast available</td></tr>'}</table>
      <h3>Outlooks and drought</h3>
      <div class="kv"><div>SPC Day 1/2/3</div><div>${esc(LAYERS.spc.cat[spc.d1_cat || 0][1])} / ${esc(LAYERS.spc.cat[spc.d2_cat || 0][1])} / ${esc(LAYERS.spc.cat[spc.d3_cat || 0][1])}${spc.d1_hail ? ` &middot; D1 hail ${spc.d1_hail}%` : ''}${spc.d1_wind ? ` &middot; D1 wind ${spc.d1_wind}%` : ''}${spc.d1_torn ? ` &middot; D1 tornado ${spc.d1_torn}%` : ''}</div>
      <div>WPC rain / ERO</div><div>7-day QPF band &ge; ${fmt(wpc.qpf7d, ' in', 2)} &middot; ERO D1-3: ${esc(LAYERS.ero.cat[Math.max(wpc.ero1 || 0, wpc.ero2 || 0, wpc.ero3 || 0)][1])}</div>
      <div>CPC 6-10 day</div><div>temp ${esc(cpcT(cpc.t610))} &middot; precip ${esc(cpcT(cpc.p610))}</div>
      <div>CPC 8-14 day</div><div>temp ${esc(cpcT(cpc.t814))} &middot; precip ${esc(cpcT(cpc.p814))}</div>
      <div>Drought Monitor</div><div>${dm ? `D0+ ${fmt(dm.d0, '%')} &middot; D1+ ${fmt(dm.d1, '%')} &middot; D2+ ${fmt(dm.d2, '%')} &middot; D3+ ${fmt(dm.d3, '%')} (map ${esc(dm.date)})` : '-'}</div></div>
      ${stageRows ? `<h3>Crop stage (state level)</h3><ul class="list small">${stageRows}</ul>` : ''}
      <h3>Forecast history (this county)</h3><div class="chart" id="county-history"><div class="empty">Loading...</div></div>
      <p class="small"><a href="https://forecast.weather.gov/MapClick.php?lat=${c.lat}&lon=${c.lon}" target="_blank" rel="noopener">NWS point forecast for this county</a></p>`;
    try {
      if (!S.hourly[c.s]) S.hourly[c.s] = await getJSON(`data/hourly/${c.s}.json`);
      hourlyChart('#hourly-chart', S.hourly[c.s][fips]);
    } catch (e) { $('#hourly-chart').innerHTML = '<div class="empty">Hourly data unavailable.</div>'; }
    try {
      if (!S.history[c.s]) S.history[c.s] = await getJSON(`data/history/${c.s}.json`);
      const h = S.history[c.s]; const row = h.counties[fips];
      if (row) lineChart('#county-history', h.t, [{ name: 'Coldest low next 7 d (F)', values: row.tmin7d, color: '#3b6fb6' }, { name: 'Warmest high next 7 d (F)', values: row.tmax7d, color: '#e8590c' }], { unit: ' F' });
      else $('#county-history').innerHTML = '<div class="empty">No history yet.</div>';
    } catch (e) { $('#county-history').innerHTML = '<div class="empty">History unavailable.</div>'; }
  }

  // ---------------------------------------------------------------- charts
  function hourlyChart(sel, h) {
    const host = $(sel);
    if (!h || !h.t) { host.innerHTML = '<div class="empty">No hourly data.</div>'; return; }
    const t0 = new Date(h.t0);
    const pts = h.t.map((v, i) => ({ d: new Date(t0.getTime() + i * 3.6e6), t: v, p: (h.pop || [])[i], g: (h.gust || [])[i] }));
    const W = 560; const H = 200; const m = { l: 34, r: 34, t: 10, b: 24 };
    const x = d3.scaleTime().domain(d3.extent(pts, (d) => d.d)).range([m.l, W - m.r]);
    const tv = pts.map((d) => d.t).filter(num);
    const y = d3.scaleLinear().domain([Math.min(d3.min(tv) - 3, 30), Math.max(d3.max(tv) + 3, 34)]).nice().range([H - m.b, m.t]);
    const yp = d3.scaleLinear().domain([0, 100]).range([H - m.b, m.t]);
    const svg = d3.select(host).html('').append('svg').attr('viewBox', `0 0 ${W} ${H}`);
    svg.append('g').selectAll('rect').data(pts.filter((d) => num(d.p))).join('rect').attr('x', (d) => x(d.d) - 2).attr('width', 4)
      .attr('y', (d) => yp(d.p)).attr('height', (d) => H - m.b - yp(d.p)).attr('fill', '#4393c3').attr('opacity', 0.35);
    if (y.domain()[0] <= 32 && y.domain()[1] >= 32) svg.append('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y(32)).attr('y2', y(32)).attr('stroke', '#3b6fb6').attr('stroke-dasharray', '4 3');
    svg.append('path').datum(pts.filter((d) => num(d.t))).attr('fill', 'none').attr('stroke', '#e8590c').attr('stroke-width', 2).attr('d', d3.line().x((d) => x(d.d)).y((d) => y(d.t)));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6).tickFormat((d) => dshort.format(d)));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((d) => `${d}F`));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(${W - m.r},0)`).call(d3.axisRight(yp).ticks(4).tickFormat((d) => `${d}%`));
    d3.select(host).append('div').attr('class', 'legend').html('<span><i style="background:#e8590c"></i>Temperature (F)</span><span><i style="background:#4393c3;opacity:.5"></i>Probability of precipitation</span><span><i style="background:#3b6fb6"></i>32 F line</span>');
  }
  function lineChart(sel, times, series, opts = {}) {
    const host = $(sel);
    const dates = times.map((t) => new Date(t));
    if (!dates.length) { host.innerHTML = '<div class="empty">No data yet.</div>'; return; }
    const W = 620; const H = 240; const m = { l: 44, r: 12, t: 10, b: 26 };
    const all = series.flatMap((s) => s.values.filter(num));
    if (!all.length) { host.innerHTML = '<div class="empty">No data yet.</div>'; return; }
    const x = d3.scaleTime().domain(d3.extent(dates)).range([m.l, W - m.r]);
    const pad = (d3.max(all) - d3.min(all)) * 0.1 || 1;
    const y = d3.scaleLinear().domain([d3.min(all) - pad, d3.max(all) + pad]).nice().range([H - m.b, m.t]);
    const svg = d3.select(host).html('').append('svg').attr('viewBox', `0 0 ${W} ${H}`);
    svg.append('g').attr('class', 'grid').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - m.l - m.r)).tickFormat('')).call((g) => g.select('.domain').remove());
    series.forEach((s) => {
      const pts = dates.map((d, i) => [d, s.values[i]]).filter((p) => num(p[1]));
      svg.append('path').datum(pts).attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', 2).attr('d', d3.line().x((p) => x(p[0])).y((p) => y(p[1])));
      svg.append('g').selectAll('circle').data(pts).join('circle').attr('cx', (p) => x(p[0])).attr('cy', (p) => y(p[1])).attr('r', 2.5).attr('fill', s.color)
        .append('title').text((p) => `${s.name}: ${(+p[1]).toFixed(2)} (${dtf.format(p[0])})`);
    });
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6).tickFormat((d) => dshort.format(d)));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5));
    d3.select(host).append('div').attr('class', 'legend').html(series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join(''));
  }
  function barChart(sel, rows, opts = {}) {
    const host = $(sel);
    const valid = rows.filter((r) => num(r.value));
    if (!valid.length) { host.innerHTML = '<div class="empty">No data for this metric yet.</div>'; return; }
    const W = 760; const rowH = 26; const H = valid.length * rowH + 30; const m = { l: 130, r: 70, t: 6, b: 22 };
    const ext = d3.extent(valid, (r) => r.value);
    const lo = Math.min(0, ext[0]); const hi = Math.max(0, ext[1]) || 1;
    const x = d3.scaleLinear().domain([lo, hi]).nice().range([m.l, W - m.r]);
    const svg = d3.select(host).html('').append('svg').attr('viewBox', `0 0 ${W} ${H}`);
    const g = svg.selectAll('g.r').data(valid).join('g').attr('transform', (r, i) => `translate(0,${m.t + i * rowH})`);
    g.append('text').attr('x', m.l - 8).attr('y', rowH / 2 + 4).attr('text-anchor', 'end').attr('font-size', 12).attr('fill', 'currentColor').text((r) => r.label);
    g.append('rect').attr('x', (r) => x(Math.min(0, r.value))).attr('y', 4).attr('height', rowH - 8).attr('width', (r) => Math.abs(x(r.value) - x(0))).attr('fill', (r) => r.color || '#1b395f').attr('rx', 2);
    g.append('text').attr('x', (r) => x(Math.max(0, r.value)) + 6).attr('y', rowH / 2 + 4).attr('font-size', 11).attr('fill', 'currentColor').text((r) => `${(+r.value).toFixed(opts.nd || 0)}${opts.unit || ''}${r.note ? '  ' + r.note : ''}`);
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6));
  }

  // ---------------------------------------------------------------- crops
  function renderCrops() {
    const L = S.latest;
    const crops = Object.keys(L.crops);
    const cell = {};
    (L.matrix || []).forEach((m) => { cell[`${m.state}|${m.crop}`] = m; });
    const grown = (st, cr) => L.counties.some((c) => c.s === st && (c.c || []).includes(cr));
    let html = '<table class="data matrix"><tr><th>State</th>' + crops.map((c) => `<th>${esc(L.crops[c])}</th>`).join('') + '</tr>';
    L.states.forEach((s) => {
      html += `<tr><td>${esc(s.name)}</td>` + crops.map((cr) => {
        if (!grown(s.abbr, cr)) return '<td class="na">not monitored</td>';
        const m = cell[`${s.abbr}|${cr}`];
        if (!m) return `<td class="cell" data-k="${s.abbr}|${cr}">${badge('NONE')}</td>`;
        const hz = Object.entries(m.hazards).sort((a, b) => RANK[b[1]] - RANK[a[1]]).slice(0, 2).map((h) => hazard(h[0])).join(', ');
        return `<td class="cell" data-k="${s.abbr}|${cr}">${badge(m.level)}<div class="small muted">${esc(hz)}</div></td>`;
      }).join('') + '</tr>';
    });
    $('#matrix').innerHTML = html + '</table>';
    document.querySelectorAll('#matrix td.cell').forEach((td) => td.addEventListener('click', () => {
      const [st, cr] = td.dataset.k.split('|');
      const m = cell[td.dataset.k];
      const stg = ((L.states.find((s) => s.abbr === st) || {}).stages || {})[cr];
      $('#matrix-detail').innerHTML = `<div class="evcard" style="border-left-color:${RISK_COLOR[m ? m.level : 'NONE']}"><h4>${esc(st)} ${esc(L.crops[cr])} ${badge(m ? m.level : 'NONE')}</h4>` +
        (stg ? `<div class="small"><b>Stage:</b> ${esc(stg.detail)} <span class="muted">(basis: ${esc(stg.basis)}, confidence ${esc(stg.confidence)})</span></div>` : '') +
        (m ? `<div class="small"><b>Most affected county:</b> ${esc(m.county || '-')}</div><ul class="list small">${m.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : '<p class="small muted">No hazard currently detected for this crop in this state.</p>') + '</div>';
    }));
    let st = '<table class="data"><tr><th>State</th><th>Crop</th><th>Status</th><th>Basis</th><th>As of</th></tr>';
    L.states.forEach((s) => Object.entries(s.stages || {}).forEach(([cr, v]) => {
      st += `<tr><td>${esc(s.abbr)}</td><td>${esc(L.crops[cr])}</td><td>${esc(v.detail)}</td><td>${esc(v.basis === 'nass' ? 'USDA NASS Crop Progress' : 'Typical calendar')}</td><td class="nowrap">${esc(v.as_of || '')}</td></tr>`;
    }));
    $('#stages').innerHTML = st + '</table>';
  }

  // ---------------------------------------------------------------- compare
  const CMP = {
    tmin7d: ['Coldest forecast low, 7 days (F)', (a) => a.tmin7d, ' F', 0, 'tmin7d_fips'],
    tmin72: ['Coldest forecast low, 72 h (F)', (a) => a.tmin72, ' F', 0, 'tmin72_fips'],
    tmax7d: ['Warmest forecast high, 7 days (F)', (a) => a.tmax7d, ' F', 0, 'tmax7d_fips'],
    qpf72: ['Max forecast rain, 72 h (in)', (a) => a.qpf72, ' in', 2, 'qpf72_fips'],
    qpf7d_mean: ['Average forecast rain, 7 days (in)', (a) => a.qpf7d_mean, ' in', 2, null],
    obs7: ['Average observed rain, last 7 days (in)', (a) => a.obs_pcpn7_mean, ' in', 2, null],
    snow72: ['Max forecast snow, 72 h (in)', (a) => a.snow72, ' in', 1, 'snow72_fips'],
    gust72: ['Max forecast gust, 72 h (mph)', (a) => a.gust72, ' mph', 0, 'gust72_fips'],
    dm1: ['Average % area in drought (D1+)', (a) => a.dm_d1_mean, '%', 0, null],
    dsci: ['Average Drought Severity & Coverage Index', (a) => a.dm_dsci_mean, '', 0, null],
  };
  function renderCompare() {
    const sel = $('#cmp-metric');
    if (!sel.options.length) {
      sel.innerHTML = Object.entries(CMP).map(([k, v]) => `<option value="${k}">${esc(v[0])}</option>`).join('');
      sel.addEventListener('change', renderCompare);
    }
    const [label, get, unit, nd, fk] = CMP[sel.value];
    const rows = S.latest.states.map((s) => {
      const a = s.agg || {}; const f = fk ? a[fk] : null; const cn = f && S.idx[f] ? S.idx[f].n.replace(' County', '').replace(' Parish', '') : '';
      return { label: s.name, value: get(a), color: RISK_COLOR[s.level] === RISK_COLOR.NONE ? '#1b395f' : RISK_COLOR[s.level], note: cn ? `(${cn})` : '' };
    }).sort((a, b) => (num(b.value) ? b.value : -1e9) - (num(a.value) ? a.value : -1e9));
    barChart('#cmp-chart', rows, { unit, nd });
    $('#cmp-table').innerHTML = '<table class="data"><tr><th>State</th><th>Risk</th><th>Low 72h</th><th>Low 7d</th><th>High 7d</th><th>Rain 72h max</th><th>Rain 7d avg</th><th>Obs rain 7d avg</th><th>Drought D1+ avg</th></tr>' +
      S.latest.states.map((s) => { const a = s.agg || {}; return `<tr><td>${esc(s.name)}</td><td>${badge(s.level)}</td><td>${fmt(a.tmin72, ' F')}</td><td>${fmt(a.tmin7d, ' F')}</td><td>${fmt(a.tmax7d, ' F')}</td><td>${fmt(a.qpf72, ' in', 2)}</td><td>${fmt(a.qpf7d_mean, ' in', 2)}</td><td>${fmt(a.obs_pcpn7_mean, ' in', 2)}</td><td>${fmt(a.dm_d1_mean, '%')}</td></tr>`; }).join('') + '</table>';
  }

  // ---------------------------------------------------------------- forecast changes
  const CHG = { tmin7d: 'Coldest low next 7 days (F)', tmin72: 'Coldest low next 72 h (F)', tmax7d: 'Warmest high next 7 days (F)', qpf72: 'Rain next 72 h (in; state = county average)', qpf7d: 'Rain next 7 days (in; state = county average)', snow72: 'Snow next 72 h (in)' };
  async function renderChanges() {
    const L = S.latest;
    if (!$('#chg-state').options.length) {
      $('#chg-state').innerHTML = L.states.map((s) => `<option value="${s.abbr}">${esc(s.name)}</option>`).join('');
      $('#chg-metric').innerHTML = Object.entries(CHG).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
      $('#chg-state').addEventListener('change', () => { $('#chg-county').innerHTML = '<option value="">State aggregate</option>'; renderChanges(); });
      $('#chg-metric').addEventListener('change', renderChanges);
      $('#chg-county').addEventListener('change', renderChanges);
    }
    const st = $('#chg-state').value; const metric = $('#chg-metric').value;
    try {
      if (!S.history[st]) S.history[st] = await getJSON(`data/history/${st}.json`);
    } catch (e) { $('#chg-chart').innerHTML = '<div class="empty">History not available yet.</div>'; return; }
    const h = S.history[st];
    const cs = $('#chg-county');
    if (cs.options.length <= 1) cs.innerHTML += Object.keys(h.counties).map((f) => `<option value="${f}">${esc(S.idx[f] ? S.idx[f].n : f)}</option>`).join('');
    const f = cs.value;
    const values = f ? (h.counties[f] || {})[metric] || [] : (h.state || {})[metric] || [];
    lineChart('#chg-chart', h.t, [{ name: `${f ? S.idx[f].n : st + ' (all monitored counties)'} - ${CHG[metric]}`, values, color: metric.startsWith('qpf') ? '#2166ac' : metric === 'tmax7d' ? '#e8590c' : '#3b6fb6' }]);
    if (!S.events) { try { S.events = await getJSON('data/events.json'); } catch (e) { S.events = { active: L.events, recent_ended: [] }; } }
    const evs = S.events.active || [];
    $('#event-history').innerHTML = evs.length ? evs.map((e) => `<div class="evcard" style="border-left-color:${RISK_COLOR[e.level]}"><h4>${badge(e.level)} ${esc(hazard(e.hazard))} - ${esc(e.state)}</h4>` +
      `<div class="small">${(e.history || []).slice(-12).map((x) => `<span class="chip">${esc(dshort.format(new Date(x.t)))}: ${esc(x.level)} (${x.n_counties})</span>`).join('')}</div></div>`).join('') +
      ((S.events.recent_ended || []).length ? `<h3>Recently ended</h3><ul class="list small">${S.events.recent_ended.map((e) => `<li>${badge(e.level)} ${esc(e.title)} (${esc(fmtTime(e.first))} - ${esc(fmtTime(e.last))})</li>`).join('')}</ul>` : '')
      : '<div class="empty">No active events.</div>';
  }

  // ---------------------------------------------------------------- alerts
  async function renderAlerts() {
    if (!S.alerts) { try { S.alerts = await getJSON('data/alerts.json'); } catch (e) { S.alerts = { active: S.latest.alerts, history: [], notifications: [], storm_reports: [] }; } }
    const A = S.alerts;
    const stSel = $('#al-state');
    if (stSel.options.length <= 1) {
      stSel.innerHTML += S.latest.states.map((s) => `<option value="${s.abbr}">${esc(s.name)}</option>`).join('');
      ['#al-level', '#al-state'].forEach((s) => $(s).addEventListener('change', renderAlerts));
    }
    const lv = $('#al-level').value; const st = stSel.value;
    const act = (A.active || []).filter((a) => (!lv || a.level === lv) && (!st || (a.states || '').split(',').includes(st)));
    $('#alerts-active').innerHTML = act.length ? '<table class="data"><tr><th>Level</th><th>Event</th><th>States</th><th>Ag counties</th><th>From</th><th>Until</th><th>Why this level</th></tr>' +
      act.map((a) => `<tr><td>${badge(a.level)}</td><td><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.event)}</a><div class="small muted">${esc(a.headline || '')}</div></td><td>${esc(a.states)}</td><td>${a.fips.length}</td><td class="nowrap">${esc(fmtTime(a.onset))}</td><td class="nowrap">${esc(fmtTime(a.ends))}</td><td class="small">${esc((a.reasons || []).join(' '))}</td></tr>`).join('') + '</table>'
      : '<div class="empty">No matching official alerts in monitored agricultural counties.</div>';
    const N = A.notifications || [];
    $('#notif').innerHTML = N.length ? '<table class="data"><tr><th>Created</th><th>Type</th><th>Subject</th><th>Status</th></tr>' +
      N.map((n) => `<tr><td class="nowrap">${esc(fmtTime(n.created_utc))}</td><td>${esc(n.kind)}</td><td>${esc(n.subject)}</td><td>${esc(n.status)}${n.error ? `<div class="small muted">${esc(n.error)}</div>` : ''}</td></tr>`).join('') + '</table>'
      : '<div class="empty">No emails generated yet.</div>';
    const H = A.history || [];
    $('#alerts-hist').innerHTML = H.length ? '<table class="data"><tr><th>First seen</th><th>Level</th><th>Event</th><th>States</th><th>Counties</th><th>Status</th></tr>' +
      H.map((a) => `<tr><td class="nowrap">${esc(fmtTime(a.first_seen))}</td><td>${badge(a.level)}</td><td><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.event)}</a></td><td>${esc(a.states)}</td><td>${a.n}</td><td>${a.active ? 'active' : 'expired'}</td></tr>`).join('') + '</table>'
      : '<div class="empty">No alerts recorded in the last 30 days.</div>';
    const R = (A.storm_reports || []).filter((r) => r.fips);
    $('#storm').innerHTML = R.length ? '<table class="data"><tr><th>Type</th><th>Mag.</th><th>Location</th><th>Time (UTC)</th></tr>' +
      R.map((r) => `<tr><td>${esc(r.kind)}</td><td>${esc(r.magnitude)}</td><td>${esc(r.location)} (${esc(r.county)}, ${esc(r.state)})</td><td>${esc(r.time_hhmm)}Z ${esc(r.day)}</td></tr>`).join('') + '</table>'
      : '<div class="empty">No preliminary storm reports in monitored counties.</div>';
  }

  // ---------------------------------------------------------------- reports / content / sources
  async function renderReports() {
    if (!S.reports) { try { S.reports = await getJSON('data/reports.json'); } catch (e) { S.reports = []; } }
    const R = S.reports;
    $('#report-list').innerHTML = R.length ? '<table class="data"><tr><th>Type</th><th>Period</th><th>Subject</th></tr>' +
      R.map((r, i) => `<tr class="click" data-i="${i}"><td>${esc(r.kind)}</td><td class="nowrap">${esc(r.period)}</td><td>${esc(r.subject)}</td></tr>`).join('') + '</table>'
      : '<div class="empty">No reports generated yet. The daily report is produced once a day and the weekly outlook once a week.</div>';
    document.querySelectorAll('#report-list tr.click').forEach((tr) => tr.addEventListener('click', () => { $('#report-frame').src = R[+tr.dataset.i].file; }));
    if (R.length && !$('#report-frame').getAttribute('src')) $('#report-frame').src = R[0].file;
  }
  async function renderContent() {
    if (!S.content) { try { S.content = await getJSON('data/content.json'); } catch (e) { S.content = []; } }
    const I = S.content;
    $('#ideas').innerHTML = I.length ? I.map((i) => `<div class="idea"><h4>${esc(i.headline)} ${badge(i.level)}</h4>` +
      `<div class="kv"><div>Series</div><div>${esc(i.series)}</div><div>Crop</div><div>${esc(i.crop)}</div><div>Region</div><div>${esc(i.region)}</div><div>Format</div><div>${esc(i.format)} - ${esc(i.format_reason)}</div><div>Created</div><div>${esc(fmtTime(i.created_utc))}</div></div>` +
      `<p><b>Why it matters:</b> ${esc(i.why)}</p><p><b>Verified facts:</b></p><ul class="list small">${(i.facts || []).map((f) => `<li>${esc(f.text)} - <i>${esc(f.source)}</i></li>`).join('')}</ul>` +
      `<p><b>Suggested caption</b></p><div class="cap">${esc(i.caption)}</div><p class="small"><b>Visual:</b> ${esc(i.visual)}</p>` +
      `<p class="small"><b>Hashtags:</b> ${esc((i.hashtags || []).join(' '))}${i.hashtags_verified ? '' : ' <span class="muted">(state tag unverified)</span>'}</p></div>`).join('')
      : '<div class="empty">No content opportunities yet. Ideas are generated with the daily report when MODERATE or higher events are active.</div>';
  }
  async function renderSources() {
    if (!S.sources) { try { S.sources = await getJSON('data/sources.json'); } catch (e) { S.sources = { sources: [], runs: [] }; } }
    const X = S.sources;
    $('#src-table').innerHTML = '<table class="data"><tr><th>Source</th><th>Agency</th><th>Type</th><th>Status</th><th>Last success</th><th>Product time</th><th>Latency</th><th>Last error</th></tr>' +
      X.sources.map((s) => `<tr><td>${s.info ? `<a href="${esc(s.info)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)}<div class="small muted">${esc(s.cadence || '')}</div></td><td>${esc(s.agency || '')}</td><td>${esc(s.kind || '')}</td><td>${s.healthy ? '<span class="badge lv-NONE">OK</span>' : '<span class="badge lv-CRITICAL">ERROR</span>'}</td><td class="nowrap">${esc(ago(s.last_success_utc))}</td><td class="nowrap">${esc(s.product_time || '-')}</td><td>${fmt(s.last_latency_ms, ' ms')}</td><td class="small">${esc(s.last_error || '')}${s.last_error_utc ? ` <span class="muted">(${esc(ago(s.last_error_utc))})</span>` : ''}</td></tr>`).join('') + '</table>';
    $('#runs').innerHTML = '<table class="data"><tr><th>Started</th><th>Task</th><th>Status</th><th>Notes</th></tr>' +
      (X.runs || []).map((r) => `<tr><td class="nowrap">${esc(fmtTime(r.started_utc))}</td><td>${esc(r.task)}</td><td>${esc(r.status)}</td><td class="small">${esc((r.error || '').slice(0, 200))}</td></tr>`).join('') + '</table>';
    const E = S.latest.email || {};
    $('#delivery').innerHTML = `<div class="kv"><div>Channel</div><div>Email only</div><div>Provider</div><div>${esc(E.provider || 'not configured')}</div><div>Recipient</div><div>${esc(E.recipient)} (read from the ALERT_RECIPIENT secret)</div><div>Configured</div><div>${E.configured ? 'yes' : 'no - missing ' + esc((E.missing || []).join(', '))}</div><div>Dry run</div><div>${E.dry_run ? 'yes' : 'no'}</div><div>Last scheduler tick</div><div>${esc(fmtTime(S.latest.last_tick))}</div><div>Schedule</div><div>alerts every tick; forecast cycle every 3 h; daily report ${esc(S.latest.schedule.daily_ct)} CT; weekly ${esc(S.latest.schedule.weekly)}</div></div>`;
  }

  // ---------------------------------------------------------------- navigation
  const RENDER = { overview: renderOverview, map: () => { refreshMap(); }, crops: renderCrops, compare: renderCompare, changes: renderChanges, alerts: renderAlerts, reports: renderReports, content: renderContent, sources: renderSources };
  function show(view) {
    document.querySelectorAll('nav.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    document.querySelectorAll('section.view').forEach((s) => s.classList.toggle('active', s.id === `view-${view}`));
    store.set('apwx-view', view);
    if (!S.done[view] || view === 'map') { S.done[view] = true; Promise.resolve(RENDER[view]()).catch((e) => console.error(e)); }
  }
  function initTheme() {
    const saved = store.get('apwx-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
    $('#theme').addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next); store.set('apwx-theme', next);
    });
  }
  async function init() {
    initTheme();
    try { S.latest = await getJSON('data/latest.json'); } catch (e) {
      $('#status').innerHTML = '<span class="pill bad"><span class="dot"></span>No data yet</span>';
      $('#kpis').innerHTML = `<div class="empty" style="grid-column:1/-1">Dashboard data has not been generated yet (${esc(e.message)}). Run the pipeline once.</div>`;
      return;
    }
    S.latest.counties.forEach((c) => { S.idx[c.f] = c; });
    renderStatus();
    initMapControls();
    document.querySelectorAll('nav.tabs button').forEach((b) => b.addEventListener('click', () => show(b.dataset.view)));
    const v = store.get('apwx-view');
    show(v && RENDER[v] ? v : 'overview');
  }
  init();
})();