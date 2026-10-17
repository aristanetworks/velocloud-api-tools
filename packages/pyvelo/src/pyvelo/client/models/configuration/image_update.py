from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_core import MISSING

from pyvelo.core import VcoModel


class ImageUpdateScheduledTime(VcoModel):
    day_of_week: int
    specified: bool
    time_of_day_mins: int
    use_edge_time_zone: bool


class FirmwareImageUpdateData(VcoModel):
    build_number: str
    profile_device_family: str
    profile_version: str
    software_package_id: int
    software_package_name: str
    version: str
    window_duration_mins: int
    windowed: bool
    devicefamily: Annotated[list[str], Field(alias="devicefamily")]


class ImageUpdateData(VcoModel):
    build_number: str
    profile_device_family: str
    profile_version: str
    scheduled_start_time: ImageUpdateScheduledTime | MISSING = MISSING
    software_package_id: int
    software_package_name: str
    version: str
    window_duration_mins: int
    windowed: bool
    devicefamily: Annotated[list[str], Field(alias="devicefamily")]
    modem_firmware: FirmwareImageUpdateData
    platform_firmware: FirmwareImageUpdateData
    factory_firmware: FirmwareImageUpdateData
