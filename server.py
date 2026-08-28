from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import re
import socket
import subprocess
import threading
import time
import uuid

import psutil
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from zeroconf import IPVersion, ServiceInfo, Zeroconf
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
WEB_DIR = BASE_DIR / "web"
STORAGE_DIR.mkdir(exist_ok=True)
started_at = time.time()
mdns = None
ftp_server = None
ftp_thread = None
connected_devices = {}
connected_lock = threading.Lock()
uploads = {}
uploads_lock = threading.Lock()


def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def register_mdns():
    global mdns
    address = local_ip()
    if address == "127.0.0.1":
        return
    try:
        mdns = Zeroconf(ip_version=IPVersion.V4Only)
        info = ServiceInfo("_http._tcp.local.", "SciNET Link._http._tcp.local.", addresses=[socket.inet_aton(address)], port=8000, server="scinet.local.", properties={"path": "/", "app": "SciNET Link"})
        mdns.async_register_service(info)
        print("SciNET Link: http://scinet.local:8000")
        print(f"SciNET Link: http://{address}:8000")
    except Exception as exc:
        print(f"SciNET mDNS unavailable: {exc}")
        if mdns:
            mdns.close()
            mdns = None


def start_ftp():
    global ftp_server, ftp_thread
    address = local_ip()
    authorizer = DummyAuthorizer()
    authorizer.add_user("scinet", "scinet", str(STORAGE_DIR), perm="elradfmwMT")
    authorizer.add_anonymous(str(STORAGE_DIR), perm="elr")

    class SciNETFTPHandler(FTPHandler):
        def on_connect(self):
            with connected_lock:
                connected_devices[f"ftp:{self.remote_ip}:{self.remote_port}"] = {"id": f"ftp:{self.remote_ip}:{self.remote_port}", "ip": self.remote_ip, "port": self.remote_port, "type": "FTP", "agent": "FTP client", "connected": time.time()}

        def on_disconnect(self):
            with connected_lock:
                connected_devices.pop(f"ftp:{self.remote_ip}:{self.remote_port}", None)

    SciNETFTPHandler.authorizer = authorizer
    SciNETFTPHandler.passive_ports = range(30000, 30010)
    SciNETFTPHandler.masquerade_address = address
    ftp_server = FTPServer((address, 2121), SciNETFTPHandler)
    ftp_server.max_cons = 20
    ftp_server.max_cons_per_ip = 5

    def serve():
        try:
            print(f"SciNET FTP: ftp://{address}:2121")
            ftp_server.serve_forever(timeout=1, blocking=True, handle_exit=False)
        except Exception as exc:
            print(f"SciNET FTP unavailable: {exc}")

    ftp_thread = threading.Thread(target=serve, name="SciNET-FTP", daemon=True)
    ftp_thread.start()


def stop_ftp():
    global ftp_server
    if ftp_server:
        ftp_server.close_all()
        ftp_server = None


def unregister_mdns():
    global mdns
    if mdns:
        mdns.close()
        mdns = None


def discover_devices():
    devices = {}
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=2, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pattern = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+(\w+)", re.MULTILINE)
        for ip, mac, entry_type in pattern.findall(result.stdout):
            if entry_type.lower() == "invalid" or ip == local_ip():
                continue
            devices[f"arp:{ip}"] = {"id": f"arp:{ip}", "ip": ip, "port": 0, "type": "Network", "agent": f"MAC {mac.upper()}", "connected": time.time(), "mac": mac.upper()}
    except (OSError, subprocess.SubprocessError):
        pass
    return devices


@asynccontextmanager
async def lifespan(app):
    register_mdns()
    start_ftp()
    yield
    stop_ftp()
    unregister_mdns()


app = FastAPI(title="SciNET Link", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def system_info():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(BASE_DIR))
    net = psutil.net_io_counters()
    discovered = discover_devices()
    with connected_lock:
        devices = {item["id"]: item for item in connected_devices.values()}
    devices.update(discovered)
    with uploads_lock:
        active_uploads = [dict(item) for item in uploads.values() if item["status"] in {"uploading", "paused"}]
    return {"hostname": socket.gethostname(), "cpu": psutil.cpu_percent(interval=None), "memory": {"percent": memory.percent, "used": memory.used, "total": memory.total}, "disk": {"percent": disk.percent, "used": disk.used, "total": disk.total}, "network": {"sent": net.bytes_sent, "received": net.bytes_recv}, "uptime": int(time.time() - started_at), "devices": list(devices.values()), "device_count": len(devices), "uploads": active_uploads}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/system")
async def api_system():
    return system_info()


@app.get("/api/files")
async def list_files():
    files = []
    for path in STORAGE_DIR.iterdir():
        if path.is_file() and not path.name.startswith(".scinet-"):
            files.append({"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime})
    files.sort(key=lambda item: item["name"].lower())
    return {"files": files}


@app.post("/api/upload/start")
async def upload_start(request: Request):
    data = await request.json()
    filename = Path(str(data.get("name") or "unnamed")).name
    total = max(0, int(data.get("size") or 0))
    upload_id = uuid.uuid4().hex
    temp = STORAGE_DIR / f".scinet-{upload_id}.part"
    temp.touch()
    with uploads_lock:
        uploads[upload_id] = {"id": upload_id, "name": filename, "size": 0, "total": total, "speed": 0, "status": "uploading", "started": time.monotonic(), "last_chunk": time.monotonic(), "temp": str(temp)}
    return {"id": upload_id}


@app.post("/api/upload/{upload_id}/chunk")
async def upload_chunk(upload_id: str, request: Request):
    with uploads_lock:
        item = uploads.get(upload_id)
        if not item:
            return JSONResponse({"error": "Upload not found"}, status_code=404)
        if item["status"] == "paused":
            return JSONResponse({"error": "Upload is paused"}, status_code=409)
        if item["status"] != "uploading":
            return JSONResponse({"error": "Upload is not active"}, status_code=409)
        temp = Path(item["temp"])
    chunk = await request.body()
    if not chunk:
        return {"received": 0}
    with temp.open("ab") as output:
        output.write(chunk)
    with uploads_lock:
        item = uploads.get(upload_id)
        if not item:
            return JSONResponse({"error": "Upload not found"}, status_code=404)
        item["size"] += len(chunk)
        now = time.monotonic()
        elapsed = max(now - item["last_chunk"], 0.001)
        item["speed"] = len(chunk) / elapsed
        item["last_chunk"] = now
        return {"received": item["size"], "total": item["total"]}


@app.post("/api/upload/{upload_id}/pause")
async def pause_upload(upload_id: str):
    with uploads_lock:
        if upload_id not in uploads:
            return JSONResponse({"error": "Upload not found"}, status_code=404)
        current = uploads[upload_id]["status"]
        if current == "uploading":
            uploads[upload_id]["status"] = "paused"
        elif current == "paused":
            uploads[upload_id]["status"] = "uploading"
        else:
            return JSONResponse({"status": current})
        return {"status": uploads[upload_id]["status"]}


@app.post("/api/upload/{upload_id}/finish")
async def finish_upload(upload_id: str):
    with uploads_lock:
        item = uploads.get(upload_id)
        if not item:
            return JSONResponse({"error": "Upload not found"}, status_code=404)
        if item["status"] != "uploading":
            return JSONResponse({"error": "Upload is paused or inactive"}, status_code=409)
        temp = Path(item["temp"])
        target = STORAGE_DIR / item["name"]
        name = item["name"]
        size = item["size"]
    if not temp.is_file() or size != item["total"]:
        return JSONResponse({"error": "Upload is incomplete"}, status_code=400)
    temp.replace(target)
    with uploads_lock:
        uploads.pop(upload_id, None)
    return {"id": upload_id, "name": name, "size": size, "status": "complete"}


@app.delete("/api/upload/{upload_id}")
async def cancel_upload(upload_id: str):
    with uploads_lock:
        item = uploads.pop(upload_id, None)
    if not item:
        return JSONResponse({"error": "Upload not found"}, status_code=404)
    Path(item["temp"]).unlink(missing_ok=True)
    return {"id": upload_id, "status": "cancelled"}


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    safe_name = Path(filename).name
    target = STORAGE_DIR / safe_name
    if not target.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(target, filename=safe_name)


@app.websocket("/ws")
async def system_socket(websocket: WebSocket):
    await websocket.accept()
    client = websocket.client
    device_id = f"web:{client.host}:{client.port}" if client else f"web:{id(websocket)}"
    user_agent = websocket.headers.get("user-agent", "Browser")
    with connected_lock:
        connected_devices[device_id] = {"id": device_id, "ip": client.host if client else "unknown", "port": client.port if client else 0, "type": "Web", "agent": user_agent[:120], "connected": time.time()}
    try:
        while True:
            await websocket.send_json(system_info())
            await asyncio.sleep(1)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with connected_lock:
            connected_devices.pop(device_id, None)
