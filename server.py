from pathlib import Path
import socket
import time

import psutil
from fastapi import FastAPI, File, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
WEB_DIR = BASE_DIR / "web"
STORAGE_DIR.mkdir(exist_ok=True)

app = FastAPI(title="SciNET Link", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

started_at = time.time()


def system_info():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(BASE_DIR))
    net = psutil.net_io_counters()
    return {
        "hostname": socket.gethostname(),
        "cpu": psutil.cpu_percent(interval=None),
        "memory": {
            "percent": memory.percent,
            "used": memory.used,
            "total": memory.total,
        },
        "disk": {
            "percent": disk.percent,
            "used": disk.used,
            "total": disk.total,
        },
        "network": {
            "sent": net.bytes_sent,
            "received": net.bytes_recv,
        },
        "uptime": int(time.time() - started_at),
    }


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
        if path.is_file():
            files.append({
                "name": path.name,
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
            })
    files.sort(key=lambda item: item["name"].lower())
    return {"files": files}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = Path(file.filename or "unnamed").name
    target = STORAGE_DIR / filename

    with target.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)

    return {"name": filename, "size": target.stat().st_size}


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    safe_name = Path(filename).name
    target = STORAGE_DIR / safe_name
    return FileResponse(target, filename=safe_name)


@app.websocket("/ws")
async def system_socket(websocket: WebSocket):
    await websocket.accept()
    while True:
        await websocket.send_json(system_info())
        await __import__("asyncio").sleep(1)
