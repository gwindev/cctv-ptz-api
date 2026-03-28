from __future__ import annotations

from typing import Tuple

import requests
from requests.auth import HTTPDigestAuth

from .base import BasePTZDriver, PTZDriverError


class HikvisionIsapiDriver(BasePTZDriver):
    def __init__(self, camera):
        super().__init__(camera)
        self.base_url = f"{camera.protocol}://{camera.host}:{camera.port}"
        self.auth = HTTPDigestAuth(camera.username, camera.password)
        self.verify = camera.verify_tls

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        response = requests.request(method, url, auth=self.auth, verify=self.verify, timeout=8, **kwargs)
        if not response.ok:
            raise PTZDriverError(f"Hikvision request failed: {response.status_code} {response.text[:200]}")
        return response

    def test_connection(self):
        resp = self._request("GET", "/ISAPI/System/deviceInfo")
        return {"vendor": "hikvision", "preview": resp.text[:200]}

    def get_snapshot(self) -> Tuple[bytes, str]:
        if self.camera.snapshot_path:
            path = self.camera.snapshot_path
        else:
            path = f"/ISAPI/Streaming/channels/{self.camera.channel}01/picture"
        resp = self._request("GET", path)
        return resp.content, resp.headers.get("Content-Type", "image/jpeg")

    def _build_xml(self, pan: float, tilt: float, zoom: float) -> str:
        pan_val = int(round(pan * 100))
        tilt_val = int(round(tilt * 100))
        zoom_val = int(round(zoom * 100))
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<PTZData>
    <pan>{pan_val}</pan>
    <tilt>{tilt_val}</tilt>
    <zoom>{zoom_val}</zoom>
</PTZData>'''

    def continuous_move(self, pan: float, tilt: float, zoom: float):
        if not pan and not tilt and not zoom:
            self.stop()
            return
        xml = self._build_xml(pan, tilt, zoom)
        self._request(
            "PUT",
            f"/ISAPI/PTZCtrl/channels/{self.camera.channel}/continuous",
            data=xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )

    def stop(self):
        xml = self._build_xml(0, 0, 0)
        self._request(
            "PUT",
            f"/ISAPI/PTZCtrl/channels/{self.camera.channel}/continuous",
            data=xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )

    def goto_preset(self, preset_id: int):
        self._request(
            "PUT",
            f"/ISAPI/PTZCtrl/channels/{self.camera.channel}/presets/{int(preset_id)}/goto",
        )

    def set_preset(self, preset_id: int):
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<PTZPreset>
    <id>{int(preset_id)}</id>
</PTZPreset>'''
        self._request(
            "PUT",
            f"/ISAPI/PTZCtrl/channels/{self.camera.channel}/presets/{int(preset_id)}",
            data=xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )

    def go_home(self):
        self._request(
            "PUT",
            f"/ISAPI/PTZCtrl/channels/{self.camera.channel}/homeposition/goto",
        )
