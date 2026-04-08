from __future__ import annotations
import json
import math
import os
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from drivers.axis_vapix import AxisVapixDriver
from drivers.hikvision_isapi import HikvisionIsapiDriver
from drivers.base import PTZDriverError

BASE_DIR = Path(__file__).resolve().parent
PRESET_FILE = BASE_DIR / "presets.json"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@dataclass
class CameraConfig:
    id: str
    name: str
    brand: str
    host: str
    username: str
    password: str
    protocol: str = "http"
    port: int = 80
    channel: int = 1
    verify_tls: bool = False
    snapshot_path: str | None = None


STREAMIN_BASE_URL = (os.getenv("STREAMIN_BASE_URL") or "http://main-api:80").rstrip("/")
STREAMIN_CCTV_URL = f"{STREAMIN_BASE_URL}/api/streamin/cctvs"


def _load_cameras_from_streamin() -> Dict[str, CameraConfig]:
    try:
        resp = requests.get(STREAMIN_CCTV_URL, timeout=8)
        if resp.status_code != 200:
            return {}

        json_data = resp.json()
        if not (json_data.get("success") and json_data.get("data")):
            return {}

        cameras = {}
        for item in json_data["data"].get("cctvs", []):
            cctv_id = str(item.get("cctv_id"))
            if not cctv_id:
                continue

            model = (item.get("model") or item.get("brand") or "").strip().lower()
            if not model:
                rtsp_url = str(item.get("rtsp_private", "")).lower()
                if "axis" in rtsp_url:
                    model = "axis"
                elif "hikvision" in rtsp_url or "isapi" in rtsp_url:
                    model = "hikvision"
                else:
                    model = "hikvision"

            if "axis" in model:
                brand = "axis"
            elif "hik" in model:
                brand = "hikvision"
            else:
                brand = model or "hikvision"

            cameras[cctv_id] = CameraConfig(
                id=cctv_id,
                name=str(item.get("cctv_name", "")),
                brand=brand,
                host=str(item.get("ip_address", "")),
                username=str(item.get("username", "") or ""),
                password=str(item.get("password", "") or ""),
                protocol="http",
                port=80,
                channel=1,
                verify_tls=False,
                snapshot_path=item.get("snapshot_path"),
            )
        return cameras
    except Exception as exc:
        print(f"Failed to fetch cameras from streamin: {exc}")
        return {}


def _load_cameras_from_file() -> Dict[str, CameraConfig]:
    if not os.path.exists(BASE_DIR / "cameras.json"):
        return {}

    try:
        raw = json.loads((BASE_DIR / "cameras.json").read_text(encoding="utf-8"))
        return {str(item["id"]): CameraConfig(**item) for item in raw}
    except Exception as exc:
        print(f"Failed to load cameras.json: {exc}")
        return {}


def load_cameras() -> Dict[str, CameraConfig]:
    # Prefer stream-service data; fallback to local cache if stream is unavailable.
    cameras = _load_cameras_from_streamin()
    if cameras:
        return cameras
    return _load_cameras_from_file()


def refresh_cameras() -> int:
    fresh = _load_cameras_from_streamin()
    if not fresh:
        return 0
    CAMERAS.clear()
    CAMERAS.update(fresh)
    return len(CAMERAS)


def get_camera_or_refresh(camera_id: str) -> CameraConfig | None:
    camera = CAMERAS.get(camera_id)
    if camera:
        return camera
    refresh_cameras()
    return CAMERAS.get(camera_id)


def save_cameras(cameras: Dict[str, CameraConfig]) -> None:
    # no-op or optional persistence depending on policy
    try:
        (BASE_DIR / "cameras.json").write_text(
            json.dumps([asdict(c) for c in cameras.values()], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


CAMERAS = load_cameras()
stop_timers: Dict[str, threading.Timer] = {}
autopan_threads: Dict[str, tuple[threading.Thread, threading.Event]] = {}


def load_presets() -> Dict[str, List[dict]]:
    if not PRESET_FILE.exists():
        return {}
    raw = json.loads(PRESET_FILE.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw
    return {}


def save_presets(presets: Dict[str, List[dict]]) -> None:
    PRESET_FILE.write_text(
        json.dumps(presets, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


CAMERA_PRESETS = load_presets()


def get_driver(camera: CameraConfig):
    if camera.brand.lower() == "axis":
        return AxisVapixDriver(camera)
    if camera.brand.lower() in {"hik", "hikvision"}:
        return HikvisionIsapiDriver(camera)
    raise PTZDriverError(f"Unsupported brand: {camera.brand}")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse("<html><body><h1>PTZ API</h1><p>Use /api/cameras endpoints.</p></body></html>")


@app.get("/api/cameras")
def list_cameras():
    if not CAMERAS:
        refresh_cameras()
    return JSONResponse(content=[camera_public_dict(c) for c in CAMERAS.values()])


@app.post("/api/cameras/sync")
async def sync_cameras():
    count = refresh_cameras()
    if count == 0:
        return JSONResponse(content={"ok": False, "error": "Unable to sync cameras from stream-service"}, status_code=502)
    return JSONResponse(content={"ok": True, "total": count})


@app.post("/api/cameras")
async def create_camera(request: Request):
    data = await request.json()
    required = ["id", "name", "brand", "host", "username", "password"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        return JSONResponse(content={"ok": False, "error": f"Missing fields: {', '.join(missing)}"}, status_code=400)

    camera = CameraConfig(
        id=str(data["id"]),
        name=str(data["name"]),
        brand=str(data["brand"]),
        host=str(data["host"]),
        username=str(data["username"]),
        password=str(data["password"]),
        protocol=str(data.get("protocol", "http")),
        port=int(data.get("port", 80)),
        channel=int(data.get("channel", 1)),
        verify_tls=bool(data.get("verify_tls", False)),
        snapshot_path=data.get("snapshot_path") or None,
    )
    CAMERAS[camera.id] = camera
    save_cameras(CAMERAS)
    return JSONResponse(content={"ok": True, "camera": camera_public_dict(camera)})


@app.put("/api/cameras/{camera_id}")
async def update_camera(camera_id: str, request: Request):
    if camera_id not in CAMERAS:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    data = await request.json()
    camera = CAMERAS[camera_id]
    for field in [
        "name",
        "brand",
        "host",
        "username",
        "password",
        "protocol",
        "snapshot_path",
    ]:
        if field in data:
            setattr(camera, field, data[field])
    for field in ["port", "channel"]:
        if field in data:
            setattr(camera, field, int(data[field]))
    if "verify_tls" in data:
        camera.verify_tls = bool(data["verify_tls"])
    save_cameras(CAMERAS)
    return JSONResponse(content={"ok": True, "camera": camera_public_dict(camera)})


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    stop_autopan(camera_id)
    CAMERAS.pop(camera_id)
    save_cameras(CAMERAS)
    if camera_id in CAMERA_PRESETS:
        CAMERA_PRESETS.pop(camera_id, None)
        save_presets(CAMERA_PRESETS)
    return JSONResponse(content={"ok": True})


@app.get("/api/cameras/{camera_id}/snapshot")
def snapshot(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    try:
        image_bytes, content_type = get_driver(camera).get_snapshot()
        return Response(content=image_bytes, media_type=content_type)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/cameras/{camera_id}/test")
def test_camera(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    try:
        info = get_driver(camera).test_connection()
        return JSONResponse(content={"ok": True, "info": info})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/cameras/{camera_id}/ptz/move")
async def ptz_move(camera_id: str, request: Request):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    data = await request.json()
    pan = clamp(float(data.get("pan", 0.0)), -1.0, 1.0)
    tilt = clamp(float(data.get("tilt", 0.0)), -1.0, 1.0)
    zoom = clamp(float(data.get("zoom", 0.0)), -1.0, 1.0)
    duration_ms = int(data.get("duration_ms", 0) or 0)

    try:
        stop_autopan(camera_id)
        driver = get_driver(camera)
        driver.continuous_move(pan=pan, tilt=tilt, zoom=zoom)
        if duration_ms > 0:
            schedule_stop(camera_id, duration_ms)
        return JSONResponse(content={"ok": True})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/cameras/{camera_id}/ptz/stop")
def ptz_stop(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    try:
        cancel_timer(camera_id)
        stop_autopan(camera_id)
        get_driver(camera).stop()
        return JSONResponse(content={"ok": True})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/cameras/{camera_id}/ptz/click-center")
async def ptz_click_center(camera_id: str, request: Request):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    data = await request.json()
    x = clamp(float(data.get("x", 0.5)), 0.0, 1.0)
    y = clamp(float(data.get("y", 0.5)), 0.0, 1.0)
    sensitivity = clamp(float(data.get("sensitivity", 1.0)), 0.2, 2.0)

    dx = x - 0.5
    dy = y - 0.5

    # dead zone รอบกลางภาพ
    if abs(dx) < 0.03 and abs(dy) < 0.03:
        return JSONResponse(content={"ok": True, "message": "within dead zone"})

    pan = clamp(dx * 2.0 * sensitivity, -1.0, 1.0)
    tilt = clamp(-dy * 2.0 * sensitivity, -1.0, 1.0)

    distance = math.sqrt(dx * dx + dy * dy)
    duration_ms = int(clamp(180 + distance * 650, 180, 900))

    try:
        driver = get_driver(camera)
        driver.continuous_move(pan=pan, tilt=tilt, zoom=0)
        schedule_stop(camera_id, duration_ms)
        return JSONResponse(content={
            "ok": True,
            "pan": pan,
            "tilt": tilt,
            "duration_ms": duration_ms,
        })
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/cameras/{camera_id}/ptz/zoom-wheel")
async def zoom_wheel(camera_id: str, request: Request):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    data = await request.json()
    delta = float(data.get("delta", 0.0))
    zoom = clamp(delta, -1.0, 1.0)
    duration_ms = int(data.get("duration_ms", 180))

    try:
        stop_autopan(camera_id)
        driver = get_driver(camera)
        driver.continuous_move(pan=0, tilt=0, zoom=zoom)
        schedule_stop(camera_id, duration_ms)
        return JSONResponse(content={"ok": True})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/cameras/{camera_id}/ptz/home")
def ptz_home(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    try:
        stop_autopan(camera_id)
        get_driver(camera).go_home()
        return JSONResponse(content={"ok": True})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/cameras/{camera_id}/ptz/autopan")
async def ptz_autopan(camera_id: str, request: Request):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    data = await request.json()
    enable = bool(data.get("enable", False))
    speed = clamp(float(data.get("speed", 0.4)), 0.1, 1.0)
    interval_ms = int(clamp(float(data.get("interval_ms", 1500)), 300, 10000))

    try:
        if enable:
            start_autopan(camera_id, speed=speed, interval_ms=interval_ms)
        else:
            stop_autopan(camera_id)
        return JSONResponse(content={"ok": True, "enable": enable, "speed": speed, "interval_ms": interval_ms})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/cameras/{camera_id}/ptz/presets")
def list_presets(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    presets = CAMERA_PRESETS.get(camera_id, [])
    return JSONResponse(content={"ok": True, "presets": presets})


@app.post("/api/cameras/{camera_id}/ptz/presets")
async def save_preset(camera_id: str, request: Request):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    data = await request.json()
    preset_id = int(data.get("preset_id", 0))
    preset_name = str(data.get("preset_name") or f"Preset {preset_id}")
    if preset_id <= 0:
        return JSONResponse(content={"ok": False, "error": "preset_id must be > 0"}, status_code=400)

    try:
        get_driver(camera).set_preset(preset_id)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)

    presets = CAMERA_PRESETS.setdefault(camera_id, [])
    existing = next((p for p in presets if int(p.get("id", 0)) == preset_id), None)
    if existing:
        existing["name"] = preset_name
    else:
        presets.append({"id": preset_id, "name": preset_name})
        presets.sort(key=lambda p: int(p.get("id", 0)))

    save_presets(CAMERA_PRESETS)
    return JSONResponse(content={"ok": True, "presets": presets})


@app.post("/api/cameras/{camera_id}/ptz/presets/{preset_id}/goto")
async def goto_preset(camera_id: str, preset_id: int):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)

    if preset_id <= 0:
        return JSONResponse(content={"ok": False, "error": "preset_id must be > 0"}, status_code=400)

    try:
        stop_autopan(camera_id)
        get_driver(camera).goto_preset(preset_id)
        return JSONResponse(content={"ok": True, "preset_id": preset_id})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


def _lookup_camera_by_ip(ip: str) -> CameraConfig | None:
    """Find a camera whose host matches the given IP address."""
    for cam in CAMERAS.values():
        if cam.host == ip:
            return cam
    return None


@app.get("/api/cameras/{camera_id}/ptz/position")
def ptz_get_position(camera_id: str):
    camera = get_camera_or_refresh(camera_id)
    if not camera:
        return JSONResponse(content={"ok": False, "error": "Camera not found"}, status_code=404)
    try:
        pan, tilt, zoom = get_driver(camera).get_position()
        return JSONResponse(content={
            "ok": True,
            "name": camera.name,
            "pan": pan,
            "tilt": tilt,
            "zoom": zoom,
        })
    except PTZDriverError as exc:
        return JSONResponse(content={"ok": False, "error": "ไม่สามารถเข้าถึงบริการ PTZ บนกล้องตัวนี้ได้", "detail": str(exc)}, status_code=502)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/getposition")
async def getposition(request: Request):
    data = await request.json() or {}
    cctv_id_or_ip = str(data.get("cctv_id_or_ip", "")).strip()
    if not cctv_id_or_ip:
        return JSONResponse(content={"ok": False, "error": "cctv_id_or_ip is required"}, status_code=400)

    if "." in cctv_id_or_ip:
        camera = _lookup_camera_by_ip(cctv_id_or_ip)
        if not camera:
            refresh_cameras()
            camera = _lookup_camera_by_ip(cctv_id_or_ip)
    else:
        camera = get_camera_or_refresh(cctv_id_or_ip)

    if not camera:
        return JSONResponse(content={"ok": False, "error": "ไม่พบข้อมูลกล้องที่ต้องการ"}, status_code=404)

    try:
        pan, tilt, zoom = get_driver(camera).get_position()
        return JSONResponse(content={
            "ok": True,
            "message": "ดึงข้อมูลตำแหน่ง PTZ สำเร็จ",
            "data": {
                "name": camera.name,
                "pan": pan,
                "tilt": tilt,
                "zoom": zoom,
            },
        })
    except PTZDriverError as exc:
        return JSONResponse(content={
            "ok": False,
            "error": "ไม่สามารถเข้าถึงบริการ PTZ บนกล้องตัวนี้ได้",
            "detail": str(exc),
        }, status_code=502)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": f"Error: {str(exc)}"}, status_code=500)


def camera_public_dict(camera: CameraConfig) -> dict:
    data = asdict(camera)
    data.pop("password", None)
    return data


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def cancel_timer(camera_id: str) -> None:
    timer = stop_timers.pop(camera_id, None)
    if timer:
        timer.cancel()


def schedule_stop(camera_id: str, duration_ms: int) -> None:
    cancel_timer(camera_id)

    def do_stop():
        camera = CAMERAS.get(camera_id)
        if not camera:
            return
        try:
            get_driver(camera).stop()
        finally:
            stop_timers.pop(camera_id, None)

    timer = threading.Timer(duration_ms / 1000.0, do_stop)
    stop_timers[camera_id] = timer
    timer.daemon = True
    timer.start()


def start_autopan(camera_id: str, speed: float, interval_ms: int) -> None:
    stop_autopan(camera_id)
    stop_event = threading.Event()

    def runner():
        while not stop_event.is_set():
            camera = CAMERAS.get(camera_id)
            if not camera:
                break
            try:
                driver = get_driver(camera)
                driver.continuous_move(pan=speed, tilt=0, zoom=0)
                if stop_event.wait(interval_ms / 1000.0):
                    break
                driver.continuous_move(pan=-speed, tilt=0, zoom=0)
                if stop_event.wait(interval_ms / 1000.0):
                    break
            except Exception:
                break
        camera = CAMERAS.get(camera_id)
        if camera:
            try:
                get_driver(camera).stop()
            except Exception:
                pass
        autopan_threads.pop(camera_id, None)

    thread = threading.Thread(target=runner, daemon=True)
    autopan_threads[camera_id] = (thread, stop_event)
    thread.start()


def stop_autopan(camera_id: str) -> None:
    thread_info = autopan_threads.pop(camera_id, None)
    if not thread_info:
        return
    _, stop_event = thread_info
    stop_event.set()
