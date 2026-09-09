from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wifiguard_contracts.api import DeviceIn, DeviceOut, DevicePatch, ErrorOut
from wifiguard_db.models import Device, ResidentDevice

from ..deps import Scope, add_log, get_db, get_scope, scope_filter, scoped_get, stamp_scope
from ..errors import ApiError

router = APIRouter(prefix="/devices", tags=["devices"])
_ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}}


def issue_mqtt_topic(scope: Scope, device_id: uuid.UUID) -> str:
    """명세 backend §7.1 — wifiguard/{facility}/{device}. HOME 은 {facility} 자리에 home-{userId}."""
    tenant = str(scope.facility_id) if scope.is_facility else f"home-{scope.user_id}"
    return f"wifiguard/{tenant}/{device_id}"


def _validate_target(connection: str, mqtt_topic: str | None, serial_port: str | None) -> None:
    if connection == "SERIAL" and not serial_port:
        raise ApiError(422, "VALIDATION_ERROR", "입력값이 올바르지 않습니다.", {"serialPort": "SERIAL 연결은 포트가 필요합니다."})


@router.get("", response_model=list[DeviceOut], responses=_ERR)
def list_devices(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> list[Device]:
    return list(db.execute(select(Device).where(scope_filter(Device, scope)).order_by(Device.created_at)).scalars())


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED,
             responses={**_ERR, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def create_device(body: DeviceIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Device:
    _validate_target(body.connection, body.mqtt_topic, body.serial_port)
    device_id = uuid.uuid4()
    topic = body.mqtt_topic.strip() if body.mqtt_topic else None
    if body.connection == "MQTT" and not topic:
        topic = issue_mqtt_topic(scope, device_id)
    dev = Device(
        id=device_id, name=body.name.strip(), room=body.room.strip(), connection=body.connection,
        mqtt_topic=topic, serial_port=body.serial_port, mac=body.mac, fw=body.fw,
    )
    stamp_scope(dev, scope)
    db.add(dev)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "TOPIC_TAKEN", "이미 사용 중인 MQTT 토픽입니다.") from exc
    add_log(db, scope, "INFO", f"장치 등록: {dev.name} ({dev.room}) · {dev.connection}", device_id=dev.id)
    db.commit()
    return dev


@router.get("/{device_id}", response_model=DeviceOut, responses=_ERR)
def get_device(device_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Device:
    return scoped_get(db, Device, device_id, scope, what="장치")


@router.patch("/{device_id}", response_model=DeviceOut, responses={**_ERR, 409: {"model": ErrorOut}})
def patch_device(device_id: uuid.UUID, body: DevicePatch, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Device:
    dev = scoped_get(db, Device, device_id, scope, what="장치")
    data = body.model_dump(exclude_unset=True)
    prev_stage = dev.calibration_stage
    for key, value in data.items():
        setattr(dev, key, value.strip() if isinstance(value, str) else value)
    _validate_target(dev.connection, dev.mqtt_topic, dev.serial_port)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "TOPIC_TAKEN", "이미 사용 중인 MQTT 토픽입니다.") from exc
    if prev_stage != "DONE" and dev.calibration_stage == "DONE":
        add_log(db, scope, "INFO",
                f"[{dev.name}] 캘리브레이션 완료 · 움직임 임계값={dev.presence_mv_threshold} 재실 baseline={dev.wander_baseline}",
                device_id=dev.id)
    elif prev_stage == "IDLE" and dev.calibration_stage == "LEAVING":
        add_log(db, scope, "WARN", f"[{dev.name}] 재설정 시작 · 공간을 비워주세요 (약 61초 소요)", device_id=dev.id)
    db.commit()
    return dev


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT, responses={**_ERR, 409: {"model": ErrorOut}})
def delete_device(device_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Response:
    dev = scoped_get(db, Device, device_id, scope, what="장치")
    primary_of = list(db.execute(
        select(ResidentDevice.resident_id).where(ResidentDevice.device_id == dev.id, ResidentDevice.is_primary.is_(True))
    ).scalars())
    if primary_of:
        raise ApiError(409, "DEVICE_IS_PRIMARY", "거주자의 주 장치라 삭제할 수 없습니다. 먼저 주 장치를 바꾸세요.",
                       {"residentIds": [str(r) for r in primary_of]})
    add_log(db, scope, "WARN", f"장치 삭제: {dev.name} ({dev.room})")
    db.delete(dev)  # resident_devices 부 매핑은 CASCADE
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
