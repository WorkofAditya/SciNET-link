from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import io
import json
import re
import socket
import subprocess
import threading
import time
import uuid

import psutil
import qrcode
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
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
events = []
events_lock = threading.Lock()
clipboard = {"text": "", "updated": 0, "source": ""}
clipboard_lock = threading.Lock()


def log_event(message, kind="info"):
    entry = {"id": uuid.uuid4().hex, "time": time.time(), "message": message, "kind": kind}
    with events_lock:
        events.insert(0, entry)
        del events[100:]
    return entry


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
            device_id = f"ftp:{self.remote_ip}:{self.remote_port}"
            with connected_lock:
                connected_devices[device_id] = {"id": device_id, "ip": self.remote_ip, "port": self.remote_port, "type": "FTP", "agent": "FTP client", "connected": time.time()}
            log_event(f"FTP device connected from {self.remote_ip}", "connect")

        def on_disconnect(self):
            with connected_lock:
                connected_devices.pop(f"ftp:{self.remote_ip}:{self.remote_port}", None)
            log_event(f"FTP device disconnected from {self.remote_ip}", "disconnect")

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


def safe_path(relative=""):
    raw = str(relative or "").replace("\\", "/")
    path = (STORAGE_DIR / raw).resolve()
    root = STORAGE_DIR.resolve()
    if path != root and root not in path.parents:
        raise ValueError("Invalid path")
    return path


def file_entry(path):
    stat = path.stat()
    return {"name": path.name, "path": path.relative_to(STORAGE_DIR).as_posix(), "type": "folder" if path.is_dir() else "file", "size": stat.st_size if path.is_file() else 0, "modified": stat.st_mtime}


@asynccontextmanager
async def lifespan(app):
    register_mdns()
    start_ftp()
    log_event("SciNET Link started", "system")
    yield
    log_event("SciNET Link stopped", "system")
    stop_ftp()
    unregister_mdns()


app = FastAPI(title="SciNET Link", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def system_info():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(BASE_DIR))
    net = psutil.net_io_counters()
    with connected_lock:
        clients = list(connected_devices.values())
    host = {"id": "host", "ip": local_ip(), "port": 8000, "type": "Host", "agent": socket.gethostname(), "connected": started_at}
    devices = [host] + clients
    with uploads_lock:
        active_uploads = [dict(item) for item in uploads.values() if item["status"] in {"uploading", "paused"}]
    return {"hostname": socket.gethostname(), "cpu": psutil.cpu_percent(interval=None), "memory": {"percent": memory.percent, "used": memory.used, "total": memory.total}, "disk": {"percent": disk.percent, "used": disk.used, "total": disk.total}, "network": {"sent": net.bytes_sent, "received": net.bytes_recv}, "uptime": int(time.time() - started_at), "devices": devices, "device_count": len(devices), "uploads": active_uploads}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/system")
async def api_system():
    return system_info()


@app.get("/api/files")
async def list_files(path: str = ""):
    try:
        directory = safe_path(path)
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not directory.is_dir():
        return JSONResponse({"error": "Folder not found"}, status_code=404)
    entries = [file_entry(item) for item in directory.iterdir() if not item.name.startswith(".scinet-")]
    entries.sort(key=lambda item: (item["type"] != "folder", item["name"].lower()))
    parent = str(Path(path).parent).replace("\\", "/") if path else ""
    if parent == ".": parent = ""
    return {"path": path.replace("\\", "/").strip("/"), "parent": parent, "files": entries}


@app.post("/api/folders")
async def create_folder(request: Request):
    data = await request.json()
    name = Path(str(data.get("name") or "New Folder")).name
    try:
        folder = safe_path(data.get("path", "")) / name
        folder.mkdir(exist_ok=False)
    except FileExistsError:
        return JSONResponse({"error": "Folder already exists"}, status_code=409)
    except (ValueError, OSError):
        return JSONResponse({"error": "Could not create folder"}, status_code=400)
    log_event(f"Folder created: {name}", "file")
    return {"ok": True}


@app.delete("/api/files")
async def delete_file(path: str):
    try:
        target = safe_path(path)
        if target == STORAGE_DIR: raise ValueError()
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            return JSONResponse({"error": "Not found"}, status_code=404)
    except (ValueError, OSError):
        return JSONResponse({"error": "Could not delete"}, status_code=400)
    log_event(f"Deleted: {Path(path).name}", "file")
    return {"ok": True}


@app.post("/api/files/rename")
async def rename_file(request: Request):
    data = await request.json()
    try:
        source = safe_path(data.get("path", ""))
        name = Path(str(data.get("name") or "")).name
        target = source.parent / name
        source.rename(target)
    except (ValueError, OSError):
        return JSONResponse({"error": "Could not rename"}, status_code=400)
    log_event(f"Renamed: {source.name} → {name}", "file")
    return {"ok": True}


@app.post("/api/upload/start")
async def upload_start(request: Request):
    data = await request.json()
    filename = Path(str(data.get("name") or "unnamed")).name
    relative_path = str(data.get("path") or "").strip("/\\")
    total = max(0, int(data.get("size") or 0))
    upload_id = uuid.uuid4().hex
    try:
        directory = safe_path(relative_path)
        directory.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError):
        return JSONResponse({"error": "Invalid destination"}, status_code=400)
    temp = directory / f".scinet-{upload_id}.part"
    temp.touch()
    with uploads_lock:
        uploads[upload_id] = {"id": upload_id, "name": filename, "path": relative_path, "size": 0, "total": total, "speed": 0, "status": "uploading", "started": time.monotonic(), "last_chunk": time.monotonic(), "temp": str(temp)}
    log_event(f"Upload started: {filename}", "transfer")
    return {"id": upload_id}


@app.post("/api/upload/{upload_id}/chunk")
async def upload_chunk(upload_id: str, request: Request):
    with uploads_lock:
        item = uploads.get(upload_id)
        if not item: return JSONResponse({"error": "Upload not found"}, status_code=404)
        if item["status"] != "uploading": return JSONResponse({"error": "Upload is not active"}, status_code=409)
        temp = Path(item["temp"])
    chunk = await request.body()
    if not chunk: return {"received": 0}
    with temp.open("ab") as output: output.write(chunk)
    with uploads_lock:
        item = uploads.get(upload_id)
        if not item: return JSONResponse({"error": "Upload not found"}, status_code=404)
        item["size"] += len(chunk)
        now = time.monotonic(); elapsed = max(now - item["last_chunk"], 0.001)
        item["speed"] = len(chunk) / elapsed; item["last_chunk"] = now
        return {"received": item["size"], "total": item["total"]}


@app.post("/api/upload/{upload_id}/pause")
async def pause_upload(upload_id: str):
    with uploads_lock:
        if upload_id not in uploads: return JSONResponse({"error": "Upload not found"}, status_code=404)
        current = uploads[upload_id]["status"]
        if current == "uploading": uploads[upload_id]["status"] = "paused"
        elif current == "paused": uploads[upload_id]["status"] = "uploading"
        else: return JSONResponse({"status": current})
        status = uploads[upload_id]["status"]
    return {"status": status}


@app.post("/api/upload/{upload_id}/finish")
async def finish_upload(upload_id: str):
    with uploads_lock:
        item = uploads.get(upload_id)
        if not item: return JSONResponse({"error": "Upload not found"}, status_code=404)
        if item["status"] != "uploading": return JSONResponse({"error": "Upload is paused or inactive"}, status_code=409)
        temp = Path(item["temp"]); target = safe_path(item["path"]) / item["name"]
        if item["size"] != item["total"]: return JSONResponse({"error": "Upload is incomplete"}, status_code=400)
    temp.replace(target)
    with uploads_lock: uploads.pop(upload_id, None)
    log_event(f"Upload completed: {item['name']}", "transfer")
    return {"id": upload_id, "name": item["name"], "size": item["size"], "status": "complete"}


@app.delete("/api/upload/{upload_id}")
async def cancel_upload(upload_id: str):
    with uploads_lock: item = uploads.pop(upload_id, None)
    if not item: return JSONResponse({"error": "Upload not found"}, status_code=404)
    Path(item["temp"]).unlink(missing_ok=True)
    log_event(f"Upload cancelled: {item['name']}", "transfer")
    return {"id": upload_id, "status": "cancelled"}


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    safe_name = Path(filename).name
    target = STORAGE_DIR / safe_name
    if not target.is_file(): return JSONResponse({"error": "File not found"}, status_code=404)
    log_event(f"Download requested: {safe_name}", "transfer")
    return FileResponse(target, filename=safe_name)


@app.get("/api/download")
async def download_path(path: str):
    try: target = safe_path(path)
    except ValueError: return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not target.is_file(): return JSONResponse({"error": "File not found"}, status_code=404)
    log_event(f"Download requested: {target.name}", "transfer")
    return FileResponse(target, filename=target.name)


@app.get("/api/qr")
async def qr_code():
    url = f"http://scinet.local:8000"
    image = qrcode.make(url)
    buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


@app.get("/api/events")
async def get_events():
    with events_lock: return {"events": list(events)}


@app.delete("/api/events")
async def clear_events():
    with events_lock: events.clear()
    return {"ok": True}


@app.get("/api/clipboard")
async def get_clipboard():
    with clipboard_lock: return dict(clipboard)


@app.post("/api/clipboard")
async def set_clipboard(request: Request):
    data = await request.json()
    text = str(data.get("text") or "")
    with clipboard_lock: clipboard.update({"text": text, "updated": time.time(), "source": str(data.get("source") or "SciNET")})
    log_event("Clipboard updated", "system")
    return {"ok": True}


@app.websocket("/ws")
async def system_socket(websocket: WebSocket):
    await websocket.accept()
    client = websocket.client
    device_id = f"web:{client.host}:{client.port}" if client else f"web:{id(websocket)}"
    user_agent = websocket.headers.get("user-agent", "Browser")
    with connected_lock:
        connected_devices[device_id] = {"id": device_id, "ip": client.host if client else "unknown", "port": client.port if client else 0, "type": "Web", "agent": user_agent[:160], "connected": time.time()}
    log_event(f"Web device connected from {client.host if client else 'unknown'}", "connect")
    try:
        while True:
            await websocket.send_json(system_info())
            await asyncio.sleep(1)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with connected_lock: connected_devices.pop(device_id, None)
        log_event(f"Web device disconnected from {client.host if client else 'unknown'}", "disconnect")
