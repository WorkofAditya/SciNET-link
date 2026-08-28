const $ = id => document.getElementById(id);
const trafficHistory = [];
let previousNetwork = null;
let previousTime = null;
const activeUploads = new Map();

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}
function formatRate(bytes) { return `${formatBytes(bytes)}/s`; }
function formatUptime(seconds) {
  const d = Math.floor(seconds / 86400); seconds %= 86400;
  const h = Math.floor(seconds / 3600); seconds %= 3600;
  const m = Math.floor(seconds / 60); const s = seconds % 60;
  return `${d ? `${String(d).padStart(2, '0')}:` : ''}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
function updateTraffic(network) {
  const now = performance.now(); let down = 0; let up = 0;
  if (previousNetwork && previousTime) {
    const elapsed = Math.max((now - previousTime) / 1000, 0.1);
    down = Math.max(0, network.received - previousNetwork.received) / elapsed;
    up = Math.max(0, network.sent - previousNetwork.sent) / elapsed;
  }
  previousNetwork = network; previousTime = now;
  $('downloadRate').textContent = formatRate(down); $('uploadRate').textContent = formatRate(up);
  $('receivedTotal').textContent = formatBytes(network.received); $('sentTotal').textContent = formatBytes(network.sent);
  trafficHistory.push(down + up); if (trafficHistory.length > 40) trafficHistory.shift();
  const max = Math.max(...trafficHistory, 1);
  $('networkLine').setAttribute('points', trafficHistory.map((value, index) => `${(trafficHistory.length === 1 ? 0 : index / (trafficHistory.length - 1) * 600).toFixed(1)},${(140 - (value / max) * 120).toFixed(1)}`).join(' '));
}
function updateSystem(data) {
  $('hostname').textContent = data.hostname.toUpperCase();
  $('cpu').textContent = Math.round(data.cpu); $('cpuLoad').textContent = `${Math.round(data.cpu)}%`; $('cpuBar').style.width = `${Math.min(data.cpu, 100)}%`;
  $('ram').textContent = Math.round(data.memory.percent); $('ramLoad').textContent = `${Math.round(data.memory.percent)}%`; $('ramBar').style.width = `${Math.min(data.memory.percent, 100)}%`; $('ramDetail').textContent = `${formatBytes(data.memory.used)} / ${formatBytes(data.memory.total)}`;
  $('disk').textContent = Math.round(data.disk.percent); $('diskLoad').textContent = `${Math.round(data.disk.percent)}%`; $('diskBar').style.width = `${Math.min(data.disk.percent, 100)}%`; $('diskDetail').textContent = `${formatBytes(data.disk.used)} / ${formatBytes(data.disk.total)}`;
  $('uptime').textContent = formatUptime(data.uptime); $('deviceCount').textContent = data.device_count; updateTraffic(data.network); renderDevices(data.devices || []); renderServerUploads(data.uploads || []);
}
function deviceName(device) {
  if (device.type === 'FTP') return 'FTP client'; if (device.type === 'Network') return 'Network device';
  const agent = device.agent || ''; if (/Android/i.test(agent)) return 'Android device'; if (/iPhone|iPad/i.test(agent)) return 'Apple device'; if (/Windows/i.test(agent)) return 'Windows device'; return 'Web browser';
}
function deviceIcon(device) {
  if (device.type === 'FTP') return '↕'; if (device.type === 'Network') return '⌁';
  const agent = device.agent || ''; if (/Android/i.test(agent)) return '▣'; if (/iPhone|iPad/i.test(agent)) return '▯'; return '⌘';
}
function renderDevices(devices) {
  const list = $('deviceList'); if (!devices.length) { list.innerHTML = '<div class="empty">No devices detected.</div>'; return; }
  list.innerHTML = devices.map(device => `<div class="device"><div class="device-icon">${deviceIcon(device)}</div><div class="device-info"><div class="device-name">${escapeHtml(deviceName(device))}</div><div class="device-meta">${escapeHtml(device.ip)}${device.mac ? ` · ${escapeHtml(device.mac)}` : device.port ? `:${device.port}` : ''}</div></div><span class="device-type">${escapeHtml(device.type)}</span></div>`).join('');
}
async function loadFiles() {
  const response = await fetch('/api/files'); const data = await response.json(); const list = $('fileList');
  if (!data.files.length) { list.innerHTML = '<div class="empty">No files in storage.</div>'; return; }
  list.innerHTML = data.files.map(file => `<div class="file-row"><div><div class="file-name">${escapeHtml(file.name)}</div><div class="file-size">${formatBytes(file.size)}</div></div><a class="download" href="/api/download/${encodeURIComponent(file.name)}">DOWNLOAD ↓</a></div>`).join('');
}
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char])); }

function createUploadRow(file) {
  const row = document.createElement('div'); row.className = 'upload-item upload-progress';
  row.innerHTML = `<div class="upload-top"><span class="upload-name">${escapeHtml(file.name)}</span><span class="upload-percent">0%</span></div><div class="upload-track"><i></i></div><div class="upload-bottom"><span class="upload-status">Starting...</span><span class="upload-speed">0 B/s</span><span class="upload-size">0 / ${formatBytes(file.size)}</span></div><div class="upload-actions"><button class="button upload-pause">PAUSE</button><button class="button upload-cancel">CANCEL</button></div>`;
  $('uploadList').prepend(row);
  return row;
}

async function uploadWithProgress(file, row) {
  const start = await fetch('/api/upload/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: file.name, size: file.size }) });
  if (!start.ok) throw new Error('Could not start upload');
  const { id } = await start.json();
  const state = { id, file, row, paused: false, cancelled: false, controller: null, uploaded: 0, started: performance.now(), speed: 0 };
  activeUploads.set(id, state);
  const bar = row.querySelector('.upload-track i'); const percent = row.querySelector('.upload-percent'); const status = row.querySelector('.upload-status'); const speed = row.querySelector('.upload-speed'); const size = row.querySelector('.upload-size'); const pause = row.querySelector('.upload-pause'); const cancel = row.querySelector('.upload-cancel');
  const update = () => { const value = file.size ? state.uploaded / file.size * 100 : 100; bar.style.width = `${value}%`; percent.textContent = `${Math.floor(value)}%`; size.textContent = `${formatBytes(state.uploaded)} / ${formatBytes(file.size)}`; speed.textContent = formatRate(state.speed); };
  pause.onclick = async () => {
    if (state.cancelled) return;
    state.paused = !state.paused;
    pause.textContent = state.paused ? 'RESUME' : 'PAUSE';
    status.textContent = state.paused ? 'Paused' : 'Uploading';
    if (state.paused) state.controller?.abort();
    else run();
  };
  cancel.onclick = async () => {
    if (state.cancelled) return;
    state.cancelled = true; state.controller?.abort(); pause.disabled = true; cancel.disabled = true; status.textContent = 'Cancelling...';
    try { await fetch(`/api/upload/${id}`, { method: 'DELETE' }); } catch {}
    activeUploads.delete(id); status.textContent = 'Cancelled'; speed.textContent = 'Stopped';
  };
  async function run() {
    if (state.cancelled || state.paused || state.uploaded >= file.size) return;
    status.textContent = 'Uploading';
    while (!state.cancelled && !state.paused && state.uploaded < file.size) {
      const end = Math.min(state.uploaded + 1024 * 1024, file.size);
      const chunk = file.slice(state.uploaded, end);
      state.controller = new AbortController();
      const chunkStarted = performance.now();
      try {
        const response = await fetch(`/api/upload/${id}/chunk`, { method: 'POST', body: chunk, signal: state.controller.signal, headers: { 'Content-Type': 'application/octet-stream' } });
        if (!response.ok) throw new Error('Chunk upload failed');
        const result = await response.json();
        state.uploaded = result.received;
        const elapsed = Math.max((performance.now() - chunkStarted) / 1000, 0.001);
        state.speed = chunk.size / elapsed;
        update();
      } catch (error) {
        if (state.paused || state.cancelled || error.name === 'AbortError') return;
        status.textContent = 'Failed'; throw error;
      }
    }
    if (state.cancelled || state.paused) return;
    const finish = await fetch(`/api/upload/${id}/finish`, { method: 'POST' });
    if (!finish.ok) throw new Error('Could not finish upload');
    state.uploaded = file.size; state.speed = 0; bar.style.width = '100%'; percent.textContent = '100%'; status.textContent = 'Complete'; speed.textContent = 'Done'; size.textContent = formatBytes(file.size); pause.remove(); cancel.remove(); activeUploads.delete(id); await loadFiles();
  }
  await run();
}

function renderServerUploads(uploads) {
  for (const item of uploads) {
    const local = activeUploads.get(item.id);
    if (!local) continue;
    local.uploaded = item.size; local.speed = item.speed || local.speed;
    const total = item.total || local.file.size; const value = total ? item.size / total * 100 : 0;
    local.row.querySelector('.upload-track i').style.width = `${value}%`; local.row.querySelector('.upload-percent').textContent = `${Math.floor(value)}%`; local.row.querySelector('.upload-size').textContent = `${formatBytes(item.size)} / ${formatBytes(total)}`; local.row.querySelector('.upload-speed').textContent = formatRate(item.speed || 0);
  }
}
async function uploadFiles(files) {
  for (const file of files) {
    const row = createUploadRow(file);
    uploadWithProgress(file, row).catch(() => { row.querySelector('.upload-status').textContent = 'Failed'; });
  }
}

const fileInput = $('fileInput');
fileInput.addEventListener('change', () => { uploadFiles([...fileInput.files]); fileInput.value = ''; });
const dropzone = $('dropzone');
for (const event of ['dragenter', 'dragover']) dropzone.addEventListener(event, e => { e.preventDefault(); dropzone.classList.add('drag'); });
for (const event of ['dragleave', 'drop']) dropzone.addEventListener(event, e => { e.preventDefault(); dropzone.classList.remove('drag'); });
dropzone.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files]));
$('refresh').addEventListener('click', loadFiles);
function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'; const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onopen = () => { $('connectionText').textContent = 'ONLINE'; }; socket.onmessage = event => updateSystem(JSON.parse(event.data)); socket.onclose = () => { $('connectionText').textContent = 'RECONNECTING'; setTimeout(connectSocket, 1500); };
}
loadFiles().catch(() => { $('fileList').innerHTML = '<div class="empty">Storage unavailable.</div>'; }); connectSocket();
