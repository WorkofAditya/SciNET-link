const $ = (id) => document.getElementById(id);

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatUptime(seconds) {
  const d = Math.floor(seconds / 86400);
  seconds %= 86400;
  const h = Math.floor(seconds / 3600);
  seconds %= 3600;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${d ? `${String(d).padStart(2, '0')}:` : ''}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function updateSystem(data) {
  $('hostname').textContent = data.hostname.toUpperCase();
  $('cpu').textContent = Math.round(data.cpu);
  $('cpuBar').style.width = `${data.cpu}%`;
  $('ram').textContent = Math.round(data.memory.percent);
  $('ramBar').style.width = `${data.memory.percent}%`;
  $('ramDetail').textContent = `${formatBytes(data.memory.used)} / ${formatBytes(data.memory.total)}`;
  $('disk').textContent = Math.round(data.disk.percent);
  $('diskBar').style.width = `${data.disk.percent}%`;
  $('diskDetail').textContent = `${formatBytes(data.disk.used)} / ${formatBytes(data.disk.total)}`;
  $('uptime').textContent = formatUptime(data.uptime);
}

async function loadFiles() {
  const response = await fetch('/api/files');
  const data = await response.json();
  const list = $('fileList');
  if (!data.files.length) {
    list.innerHTML = '<div class="empty">No files in SciNET storage.</div>';
    return;
  }
  list.innerHTML = data.files.map(file => `
    <div class="file-row">
      <div>
        <div class="file-name">${escapeHtml(file.name)}</div>
        <div class="file-size">${formatBytes(file.size)}</div>
      </div>
      <a class="download" href="/api/download/${encodeURIComponent(file.name)}">DOWNLOAD ↓</a>
    </div>
  `).join('');
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

async function uploadFiles(files) {
  for (const file of files) {
    const row = document.createElement('div');
    row.className = 'upload-item';
    row.innerHTML = `<span>${escapeHtml(file.name)}</span><span>UPLOADING...</span>`;
    $('uploadList').prepend(row);

    const form = new FormData();
    form.append('file', file);
    try {
      const response = await fetch('/api/upload', { method: 'POST', body: form });
      if (!response.ok) throw new Error('Upload failed');
      row.lastElementChild.textContent = 'COMPLETE';
      await loadFiles();
    } catch {
      row.lastElementChild.textContent = 'FAILED';
    }
  }
}

const fileInput = $('fileInput');
fileInput.addEventListener('change', () => uploadFiles(fileInput.files));

const dropzone = $('dropzone');
for (const event of ['dragenter', 'dragover']) {
  dropzone.addEventListener(event, e => { e.preventDefault(); dropzone.classList.add('drag'); });
}
for (const event of ['dragleave', 'drop']) {
  dropzone.addEventListener(event, e => { e.preventDefault(); dropzone.classList.remove('drag'); });
}
dropzone.addEventListener('drop', e => uploadFiles(e.dataTransfer.files));
$('refresh').addEventListener('click', loadFiles);

function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onopen = () => { $('connectionText').textContent = 'ONLINE'; };
  socket.onmessage = event => updateSystem(JSON.parse(event.data));
  socket.onclose = () => {
    $('connectionText').textContent = 'RECONNECTING';
    setTimeout(connectSocket, 1500);
  };
}

loadFiles().catch(() => { $('fileList').innerHTML = '<div class="empty">Storage unavailable.</div>'; });
connectSocket();
