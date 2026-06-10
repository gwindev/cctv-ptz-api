from __future__ import annotations

from typing import Tuple

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

from .base import BasePTZDriver, PTZDriverError


class AxisVapixDriver(BasePTZDriver):
    def __init__(self, camera):
        super().__init__(camera)
        self.base_url = f"{camera.protocol}://{camera.host}:{camera.port}"
        self.auth = HTTPDigestAuth(camera.username, camera.password)
        self.verify = camera.verify_tls

    def _get(self, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        response = requests.get(url, auth=self.auth, verify=self.verify, timeout=8, **kwargs)
        if response.status_code == 401:
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.camera.username, self.camera.password),
                verify=self.verify,
                timeout=8,
                **kwargs,
            )
        if not response.ok:
            raise PTZDriverError(f"Axis request failed: {response.status_code} {response.text[:200]}")
        return response

    def test_connection(self):
        resp = self._get("/axis-cgi/param.cgi?action=list&group=Properties.System")
        return {"vendor": "axis", "preview": resp.text[:200]}

    def get_snapshot(self) -> Tuple[bytes, str]:
        if self.camera.snapshot_path:
            path = self.camera.snapshot_path
        else:
            path = f"/axis-cgi/jpg/image.cgi?camera={self.camera.channel}"
        resp = self._get(path)
        return resp.content, resp.headers.get("Content-Type", "image/jpeg")

    def continuous_move(self, pan: float, tilt: float, zoom: float):
        # continuouspantiltmove รองรับ -100..100 และ continuouszoommove รองรับ -100..100
        pan_speed = int(round(pan * 100))
        tilt_speed = int(round(tilt * 100))
        zoom_speed = int(round(zoom * 100))

        params = []
        if pan_speed or tilt_speed:
            params.append(f"continuouspantiltmove={pan_speed},{tilt_speed}")
        if zoom_speed:
            params.append(f"continuouszoommove={zoom_speed}")
        if not params:
            self.stop()
            return
        query = "&".join(params)
        self._get(f"/axis-cgi/com/ptz.cgi?camera={self.camera.channel}&{query}")

    def stop(self):
        self._get(f"/axis-cgi/com/ptz.cgi?camera={self.camera.channel}&continuouspantiltmove=0,0&continuouszoommove=0")

    def goto_preset(self, preset_id: int):
        self._get(f"/axis-cgi/com/ptz.cgi?camera={self.camera.channel}&gotoserverpresetno={int(preset_id)}")

    def set_preset(self, preset_id: int):
        self._get(f"/axis-cgi/com/ptz.cgi?camera={self.camera.channel}&setserverpresetno={int(preset_id)}")

    def go_home(self):
        self._get(f"/axis-cgi/com/ptz.cgi?camera={self.camera.channel}&move=home")

    def get_position(self) -> Tuple[float, float, float]:
        resp = self._get(f"/axis-cgi/com/ptz.cgi?camera={self.camera.channel}&query=position")
        values: dict[str, str] = {}
        for line in resp.text.strip().splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                values[key.strip().lower()] = val.strip()
        pan = float(values.get("pan", 0))
        tilt = float(values.get("tilt", 0))
        zoom = float(values.get("zoom", 1))
        return pan, tilt, zoom
