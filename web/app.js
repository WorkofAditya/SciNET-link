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
  $('networkLine').setAttribute('points', trafficHistory.map((value, index) => {
    const x = trafficHistory.length === 1 ? 0 : index / (trafficHistory.length - 1) * 600;
    return `${x.toFixed(1)},${(140 - (value / max) * 120).toFixed(1)}`;
  }).join(' '));
}

function updateSystem(data) {
  $('hostname').textContent = data.hostname.toUpperCase();
  $('cpu').textContent = Math.round(data.cpu); $('cpuLoad').textContent = `${Math.round(data.cpu)}%`; $('cpuBar').style.width = `${Math.min(data.cpu, 100)}%`;
  $('ram').textContent = Math.round(data.memory.percent); $('ramLoad').textContent = `${Math.round(data.memory.percent)}%`; $('ramBar').style.width = `${Math.min(data.memory.percent, 100)}%`; $('ramDetail').textContent = `${formatBytes(data.memory.used)} / ${formatBytes(data.memory.total)}`;
  $('disk').textContent = Math.round(data.disk.percent); $('diskLoad').textContent = `${Math.round(data.disk.percent)}%`; $('diskBar').style.width = `${Math.min(data.disk.percent, 100)}%`; $('diskDetail').textContent = `${formatBytes(data.disk.used)} / ${formatBytes(data.disk.total)}`;
  $('uptime').textContent = formatUptime(data.uptime); $('deviceCount').textContent = data.device_count;
  updateTraffic(data.network); renderDevices(data.devices || []); renderServerUploads(data.uploads || []);
}

function deviceName(device) {
  if (device.type === 'FTP') return 'FTP client'; if (device.type === 'Network') return 'Network device';
  const agent = device.agent || '';
  if (/Android/i.test(agent)) return 'Android device'; if (/iPhone|iPad/i.test(agent)) return 'Apple device'; if (/Windows/i.test(agent)) return 'Windows device'; return 'Web browser';
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

function uploadWithProgress(file, row) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const uploadId = crypto.randomUUID ? crypto.randomUUID().replaceAll('-', '') : `${Date.now()}${Math.random()}`;
    let lastLoaded = 0; let lastTime = performance.now(); let paused = false; let cancelled = false;
    activeUploads.set(uploadId, { xhr, file, row });
    const bar = row.querySelector('.upload-track i'); const percent = row.querySelector('.upload-percent');
    const status = row.querySelector('.upload-status'); const speed = row.querySelector('.upload-speed'); const size = row.querySelector('.upload-size');
    const pauseButton = row.querySelector('.upload-pause'); const cancelButton = row.querySelector('.upload-cancel');
    xhr.upload.onprogress = event => {
      if (!event.lengthComputable) return;
      const now = performance.now(); const elapsed = Math.max((now - lastTime) / 1000, 0.05); const currentSpeed = (event.loaded - lastLoaded) / elapsed;
      lastLoaded = event.loaded; lastTime = now;
      const value = event.loaded / event.total * 100;
      bar.style.width = `${value}%`; percent.textContent = `${Math.round(value)}%`; speed.textContent = formatRate(currentSpeed); size.textContent = `${formatBytes(event.loaded)} / ${formatBytes(event.total)}`;
    };
    xhr.onload = async () => {
      activeUploads.delete(uploadId);
      if (cancelled) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        bar.style.width = '100%'; percent.textContent = '100%'; status.textContent = 'Complete'; speed.textContent = 'Done'; pauseButton.remove(); cancelButton.remove();
        await loadFiles(); resolve();
      } else { status.textContent = 'Failed'; reject(new Error('Upload failed')); }
    };
    xhr.onerror = () => { activeUploads.delete(uploadId); if (!cancelled) { status.textContent = 'Failed'; reject(new Error('Upload failed')); } };
    xhr.onabort = () => { activeUploads.delete(uploadId); if (cancelled) { status.textContent = 'Cancelled'; } else { status.textContent = 'Paused'; } };
    pauseButton.onclick = async () => {
      if (!paused) {
        paused = true; pauseButton.textContent = 'RESUME'; status.textContent = 'Pausing...';
        try { await fetch(`/api/upload/${uploadId}/pause`, { method: 'POST' }); } catch {}
        xhr.abort();
      } else {
        paused = false; pauseButton.textContent = 'PAUSE'; status.textContent = 'Resuming...';
        uploadWithProgress(file, row).catch(() => {});
      }
    };
    cancelButton.onclick = async () => {
      cancelled = true; status.textContent = 'Cancelling...'; pauseButton.disabled = true; cancelButton.disabled = true;
      try { await fetch(`/api/upload/${uploadId}/cancel`, { method: 'POST' }); } catch {}
      xhr.abort();
    };
    xhr.open('POST', '/api/upload'); xhr.send((() => { const form = new FormData(); form.append('file', file); return form; })());
  });
}

async function uploadFiles(files) {
  for (const file of files) {
    const row = createUploadRow(file);
    uploadWithProgress(file, row).catch(() => {});
  }
}

function renderServerUploads(uploads) {
  for (const item of uploads) {
    const local = [...activeUploads.values()].find(entry => entry.file.name === item.name);
    if (!local) continue;
    const row = local.row; const total = item.total || local.file.size; const value = total ? item.size / total * 100 : 0;
    row.querySelector('.upload-track i').style.width = `${value}%`; row.querySelector('.upload-percent').textContent = `${Math.round(value)}%`;
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
  socket.onopen = () => { $('connectionText').textContent = 'ONLINE'; };
  socket.onmessage = event => updateSystem(JSON.parse(event.data));
  socket.onclose = () => { $('connectionText').textContent = 'RECONNECTING'; setTimeout(connectSocket, 1500); };
}
loadFiles().catch(() => { $('fileList').innerHTML = '<div class="empty">Storage unavailable.</div>'; });
connectSocket();
