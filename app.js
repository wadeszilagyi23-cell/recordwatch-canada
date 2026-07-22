/* RecordWatch Canada front-end. No framework required. */
const TYPE_INFO = {
  high_max: { label: 'Record High Maximum Temperature', short: 'High Max Temp', color: '#e61924', unit: '°C' },
  high_min: { label: 'Record High Minimum Temperature', short: 'High Min Temp', color: '#ff8a00', unit: '°C' },
  low_max: { label: 'Record Low Maximum Temperature', short: 'Low Max Temp', color: '#1967e8', unit: '°C' },
  low_min: { label: 'Record Low Minimum Temperature', short: 'Low Min Temp', color: '#7b22a8', unit: '°C' },
  precipitation: { label: 'Daily Precipitation Record', short: 'Precipitation', color: '#168443', unit: 'mm' },
  snowfall: { label: 'Daily Snowfall Record', short: 'Snowfall', color: '#23a7df', unit: 'cm' }
};

let currentData = null;
let activeFilter = 'all';
let map;
let markerLayer;

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function formatDate(dateString) {
  if (!dateString) return '';
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${dateString}T12:00:00Z`));
}

function formatNumber(value, unit) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const digits = unit === '°C' ? 1 : 1;
  return `${Number(value).toFixed(digits)} ${unit}`;
}

function formatDifference(record) {
  const n = Number(record.difference);
  if (!Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)} ${record.unit}`;
}

function differenceClass(record) {
  if (record.type === 'precipitation' || record.type === 'snowfall') return 'difference-wet';
  return Number(record.difference) < 0 ? 'difference-negative' : 'difference-positive';
}

function initializeMap() {
  map = L.map('recordMap', { zoomControl: true, minZoom: 2 }).setView([57.2, -96], 4);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 12,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  const legend = $('mapLegend');
  Object.entries(TYPE_INFO).forEach(([key, info]) => {
    legend.insertAdjacentHTML('beforeend', `<span class="legend-item"><i class="legend-symbol" style="background:${info.color}"></i>${escapeHtml(info.short)}</span>`);
  });
  legend.insertAdjacentHTML('beforeend', `<span class="legend-item"><i class="legend-symbol" style="background:#fff;border:2px solid #697386"></i>Tied record</span>`);
}

function markerStyle(record) {
  const info = TYPE_INFO[record.type] || TYPE_INFO.high_max;
  return {
    radius: 7,
    fillColor: record.status === 'tied' ? '#fff' : info.color,
    color: record.status === 'tied' ? info.color : '#fff',
    weight: record.status === 'tied' ? 3 : 2,
    opacity: 1,
    fillOpacity: 0.95
  };
}

function popupHtml(record) {
  const info = TYPE_INFO[record.type];
  return `<div class="record-popup"><strong>${escapeHtml(record.community)}, ${escapeHtml(record.province)}</strong><br>${escapeHtml(info.label)}<hr><b>New value:</b> ${escapeHtml(formatNumber(record.value, record.unit))}<br><b>Previous:</b> ${escapeHtml(formatNumber(record.previousValue, record.unit))} (${escapeHtml(record.previousYear)})<br><b>Difference:</b> ${escapeHtml(formatDifference(record))}<br><b>Status:</b> ${record.status === 'tied' ? 'Tied record' : 'New record'}<br><b>Period:</b> ${escapeHtml(record.recordBeginYear)}–${escapeHtml(record.date.slice(0,4))}</div>`;
}

function filteredRecords() {
  if (!currentData) return [];
  return currentData.records.filter((record) => activeFilter === 'all' || record.type === activeFilter);
}

function renderMap() {
  markerLayer.clearLayers();
  const records = filteredRecords();
  const bounds = [];
  records.forEach((record) => {
    if (!Array.isArray(record.coordinates) || record.coordinates.length !== 2) return;
    const [lon, lat] = record.coordinates;
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
    const marker = L.circleMarker([lat, lon], markerStyle(record)).bindPopup(popupHtml(record));
    marker.recordId = record.id;
    marker.addTo(markerLayer);
    bounds.push([lat, lon]);
  });
  if (bounds.length) map.fitBounds(bounds, { padding: [25, 25], maxZoom: 6 });
  else map.setView([57.2, -96], 4);
}

function renderStory() {
  const record = currentData.recordOfDay;
  if (!record) {
    $('storyHeadline').textContent = `No new records identified for ${formatDate(currentData.date)}`;
    $('storyDescription').textContent = 'The map will update when the source contains records established or tied during the selected year.';
    $('storyValue').textContent = '—';
    return;
  }
  const info = TYPE_INFO[record.type];
  $('storyHeadline').textContent = `${record.community}, ${record.province} ${record.status === 'tied' ? 'ties' : 'sets'} a ${info.short.toLowerCase()} record`;
  $('storyDescription').textContent = currentData.story?.description || `${record.community} recorded ${formatNumber(record.value, record.unit)}, compared with the previous record of ${formatNumber(record.previousValue, record.unit)} from ${record.previousYear}.`;
  $('storyValue').textContent = formatNumber(record.value, record.unit);
  $('storyBadge').textContent = record.status === 'tied' ? 'Tied record' : 'New record';
}

function renderStats() {
  const summary = currentData.summary || {};
  $('statTotal').textContent = summary.totalRecords ?? currentData.records.length;
  $('statCommunities').textContent = summary.communities ?? new Set(currentData.records.map(r => r.community)).size;
  $('statTies').textContent = summary.tiedRecords ?? currentData.records.filter(r => r.status === 'tied').length;
  $('statOldest').textContent = summary.oldestRecordAge ? `${summary.oldestRecordAge} yrs` : '—';
}

function renderHighlights() {
  const container = $('highlightGrid');
  container.innerHTML = '';
  const highlights = currentData.highlights || [];
  if (!highlights.length) {
    container.innerHTML = '<p>No regional highlights are available for this date.</p>';
    return;
  }
  highlights.slice(0,4).forEach((item) => {
    const color = TYPE_INFO[item.leadingType]?.color || '#0759d7';
    container.insertAdjacentHTML('beforeend', `<article class="highlight-item"><strong><i class="dot" style="background:${color}"></i>${escapeHtml(item.region)}</strong><p>${escapeHtml(item.text)}</p></article>`);
  });
}

function renderTable() {
  const body = $('recordRows');
  const records = filteredRecords();
  body.innerHTML = '';
  records.forEach((record) => {
    const info = TYPE_INFO[record.type];
    body.insertAdjacentHTML('beforeend', `<tr><td><i class="dot" style="background:${info.color}"></i>${escapeHtml(record.community)}</td><td>${escapeHtml(record.province)}</td><td><span class="record-type-cell"><i class="dot" style="background:${info.color}"></i>${escapeHtml(info.short)}${record.status === 'tied' ? ' (Tied)' : ''}</span></td><td>${escapeHtml(formatNumber(record.value, record.unit))}</td><td>${escapeHtml(formatNumber(record.previousValue, record.unit))} (${escapeHtml(record.previousYear)})</td><td class="${differenceClass(record)}">${escapeHtml(formatDifference(record))}</td><td>${escapeHtml(record.recordBeginYear)}–${escapeHtml(record.date.slice(0,4))} (${escapeHtml(record.periodYears)} yrs)</td></tr>`);
  });
  $('tableTitle').textContent = `Recent Records (${formatDate(currentData.date)})`;
  $('tableSubtitle').innerHTML = `Source last updated: ${escapeHtml(currentData.sourceLastUpdated || 'unknown')}${currentData.isDemo ? '<span class="demo-badge">DEMO DATA</span>' : ''}`;
  $('tableCount').textContent = `Showing ${records.length} of ${currentData.records.length} records.`;
}

function renderSearchOptions() {
  const datalist = $('communityOptions');
  datalist.innerHTML = '';
  [...new Set(currentData.records.map(r => `${r.community}, ${r.province}`))].sort().forEach((name) => datalist.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(name)}"></option>`));
}

function renderAll() {
  $('datePicker').value = currentData.date;
  $('datePicker').max = currentData.latestAvailableDate || currentData.date;
  renderStory(); renderStats(); renderHighlights(); renderMap(); renderTable(); renderSearchOptions();
  $('statusMessage').textContent = currentData.isDemo ? 'The starter currently displays demonstration records. Run the GitHub Action to replace them with the latest ECCC snapshot.' : `Displaying records for ${formatDate(currentData.date)}.`;
}

async function loadData(path = 'data/latest.json') {
  $('statusMessage').classList.remove('error');
  $('statusMessage').textContent = 'Loading record data…';
  try {
    const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}v=${Date.now()}`);
    if (!response.ok) throw new Error(`Data file not found (${response.status})`);
    currentData = await response.json();
    activeFilter = 'all';
    document.querySelectorAll('.filter-chip[data-filter]').forEach((button) => button.classList.toggle('active', button.dataset.filter === 'all'));
    renderAll();
  } catch (error) {
    $('statusMessage').classList.add('error');
    $('statusMessage').textContent = `Unable to load that record snapshot: ${error.message}`;
  }
}

async function loadArchiveIndex() {
  try {
    const response = await fetch(`data/archive-index.json?v=${Date.now()}`);
    if (!response.ok) return;
    const data = await response.json();
    const select = $('archiveSelect');
    (data.dates || []).slice().reverse().forEach((date) => select.insertAdjacentHTML('beforeend', `<option value="${date}">${formatDate(date)}</option>`));
  } catch (_) { /* Archive list is optional in the starter. */ }
}

function archivePath(date) {
  const [year, month] = date.split('-');
  return `data/archive/${year}/${month}/${date}.json`;
}

function findCommunity(query) {
  if (!currentData || !query.trim()) return;
  const value = query.trim().toLowerCase();
  const record = currentData.records.find((item) => `${item.community} ${item.province} ${TYPE_INFO[item.type].label}`.toLowerCase().includes(value));
  if (!record) {
    $('statusMessage').textContent = `No displayed record matches “${query}”.`;
    return;
  }
  const [lon, lat] = record.coordinates;
  map.setView([lat, lon], 8);
  markerLayer.eachLayer((layer) => { if (layer.recordId === record.id) layer.openPopup(); });
  $('statusMessage').textContent = `Located ${record.community}, ${record.province}.`;
}

function downloadCsv() {
  if (!currentData) return;
  const rows = [['Date','Community','Province','Record Type','Status','New Value','Unit','Previous Record','Previous Year','Difference','Record Begin Year','Latitude','Longitude']];
  filteredRecords().forEach((r) => rows.push([r.date,r.community,r.province,TYPE_INFO[r.type].label,r.status,r.value,r.unit,r.previousValue,r.previousYear,r.difference,r.recordBeginYear,r.coordinates[1],r.coordinates[0]]));
  const csv = rows.map((row) => row.map((cell) => `"${String(cell ?? '').replaceAll('"','""')}"`).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `recordwatch-${currentData.date}.csv`; link.click(); URL.revokeObjectURL(link.href);
}

document.addEventListener('DOMContentLoaded', () => {
  initializeMap(); loadData();
  $('menuButton').addEventListener('click', () => { const nav = $('mainNav'); const open = nav.classList.toggle('open'); $('menuButton').setAttribute('aria-expanded', String(open)); });
  $('parameterFilters').addEventListener('click', (event) => { const button = event.target.closest('[data-filter]'); if (!button) return; activeFilter = button.dataset.filter; document.querySelectorAll('.filter-chip[data-filter]').forEach((chip) => chip.classList.toggle('active', chip === button)); renderMap(); renderTable(); });
  $('moreFiltersButton').addEventListener('click', () => $('statusMessage').textContent = 'Province, tied-record, and date-range filters are planned for the next version.');
  $('communitySearch').addEventListener('change', (event) => findCommunity(event.target.value));
  $('communitySearch').addEventListener('keydown', (event) => { if (event.key === 'Enter') findCommunity(event.target.value); });
  $('previousRecordsButton').addEventListener('click', () => {
    const date = $('datePicker').value;
    if (!date) return;
    if (date === currentData?.latestAvailableDate) loadData();
    else loadData(archivePath(date));
  });
  $('downloadButton').addEventListener('click', downloadCsv);
});


function normalizeRecordSearch(value) {
  return String(value ?? '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function findCommunity(query) {
  if (!currentData || !query.trim()) return;

  const searchValue = normalizeRecordSearch(query);

  const exactRecord = currentData.records.find((item) => {
    const communityName = normalizeRecordSearch(
      `${item.community}, ${item.province}`
    );

    return communityName === searchValue;
  });

  const partialRecord = currentData.records.find((item) => {
    const typeInfo = TYPE_INFO[item.type] || {};

    const searchableText = normalizeRecordSearch(
      `${item.community} ${item.province} ${item.provinceName || ''} ` +
      `${typeInfo.label || ''} ${typeInfo.short || ''}`
    );

    return searchableText.includes(searchValue);
  });

  const record = exactRecord || partialRecord;

  if (!record) {
    $('statusMessage').classList.add('error');
    $('statusMessage').textContent =
      `No displayed record matches “${query}”.`;
    return;
  }

  $('statusMessage').classList.remove('error');

  // Show all parameter types if the matching record is currently filtered out.
  if (activeFilter !== 'all' && activeFilter !== record.type) {
    activeFilter = 'all';

    document
      .querySelectorAll('.filter-chip[data-filter]')
      .forEach((button) => {
        button.classList.toggle(
          'active',
          button.dataset.filter === 'all'
        );
      });

    renderMap();
    renderTable();
  }

  const [longitude, latitude] = record.coordinates;

  map.setView([latitude, longitude], 8);

  markerLayer.eachLayer((layer) => {
    if (layer.recordId === record.id) {
      layer.openPopup();
    }
  });

  $('statusMessage').textContent =
    `Located ${record.community}, ${record.province}.`;
}
