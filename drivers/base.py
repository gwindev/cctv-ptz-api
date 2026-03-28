from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple


class PTZDriverError(Exception):
    pass


class BasePTZDriver(ABC):
    def __init__(self, camera):
        self.camera = camera

    @abstractmethod
    def test_connection(self):
        raise NotImplementedError

    @abstractmethod
    def get_snapshot(self) -> Tuple[bytes, str]:
        raise NotImplementedError

    @abstractmethod
    def continuous_move(self, pan: float, tilt: float, zoom: float):
        raise NotImplementedError

    @abstractmethod
    def stop(self):
        raise NotImplementedError

    def goto_preset(self, preset_id: int):
        raise PTZDriverError("goto_preset is not supported by this camera driver")

    def set_preset(self, preset_id: int):
        raise PTZDriverError("set_preset is not supported by this camera driver")

    def go_home(self):
        # Default fallback attempts to go to preset 1.
        self.goto_preset(1)
