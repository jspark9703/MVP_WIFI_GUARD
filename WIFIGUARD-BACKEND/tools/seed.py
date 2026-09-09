"""개발용 시드 데이터 — 프론트 `src/lib/mock-store.ts` 의 시드를 실DB에 재현한다.

    uv run python tools/seed.py            # 없는 행만 추가 (idempotent)
    uv run python tools/seed.py --reset    # 전 테이블 TRUNCATE 후 재삽입

id 는 uuid5 로 결정적으로 만들어 재실행·테스트에서 같은 값을 갖는다 (예: sid("d1")).
데모 계정 3개의 비밀번호는 전부 "demo" (argon2id 해시). 시드는 API 의 비밀번호 길이 검증을 거치지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.orm import Session

from wifiguard_db import Base, get_session
from wifiguard_db.models import (
    Device,
    EventLog,
    Facility,
    FallEvent,
    Recipient,
    Resident,
    ResidentDevice,
    TenantConfig,
    User,
)

SEED_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://wifiguard.local/seed")
DEMO_PASSWORD = "demo"


def sid(key: str) -> uuid.UUID:
    """mock-store 의 문자열 id ("d1", "u-root", …) → 결정적 uuid."""
    return uuid.uuid5(SEED_NS, key)


def _exists(db: Session, model, pk) -> bool:
    return db.get(model, pk) is not None


def _add_if_missing(db: Session, obj, *, pk=None) -> bool:
    key = pk if pk is not None else obj.id
    if _exists(db, type(obj), key):
        return False
    db.add(obj)
    return True


def seed(db: Session) -> dict[str, int]:
    now = datetime.now(UTC)
    ph = PasswordHasher()
    pw = ph.hash(DEMO_PASSWORD)
    added: dict[str, int] = {}

    def count(name: str, ok: bool) -> None:
        added[name] = added.get(name, 0) + (1 if ok else 0)

    fac_id = sid("fac-1")
    u_root, u_mem, u_home = sid("u-root"), sid("u-mem1"), sid("u-home")

    # 시설 → 사용자 (facilities.root_user_id 는 DEFERRABLE 이라 이 순서로 넣어도 커밋 시점에 검사된다)
    count("facilities", _add_if_missing(db, Facility(id=fac_id, name="강남요양원", invite_code="GN-8421", root_user_id=u_root)))
    db.flush()
    for uid, email, name, service, role, fid in (
        (u_root, "root@demo.io", "강은우", "FACILITY", "ROOT", fac_id),
        (u_mem, "member@demo.io", "김민지", "FACILITY", "MEMBER", fac_id),
        (u_home, "home@demo.io", "이가정", "HOME", "USER", None),
    ):
        count("users", _add_if_missing(db, User(
            id=uid, email=email, password_hash=pw, name=name, service=service, role=role,
            facility_id=fid, onboarded=True,
        )))
    db.flush()

    # 기기 (FACILITY, MQTT). 난수였던 RF 값은 고정값으로.
    devices = [
        ("d1", "302호 화장실", "302", "csi/gn/302/bed", "AA:BB:CC:00:01:12"),
        ("d2", "305호 화장실", "305", "csi/gn/305/bed", "AA:BB:CC:00:01:08"),
        ("d3", "201호 화장실", "201", "csi/gn/201/bed", "AA:BB:CC:00:02:04"),
        ("d4", "208호 화장실", "208", "csi/gn/208/bed", "AA:BB:CC:00:02:15"),
        ("d5", "104호 화장실", "104", "csi/gn/104/bed", "AA:BB:CC:00:03:21"),
        ("d6", "204호 화장실", "204", "csi/gn/204/bed", "AA:BB:CC:00:03:09"),
        ("d6b", "204호 샤워실", "204", "csi/gn/204/shower", "AA:BB:CC:00:03:0A"),
        ("d1b", "302호 화장실", "302", "csi/gn/302/bath", "AA:BB:CC:00:01:13"),
    ]
    for key, name, room, topic, mac in devices:
        count("devices", _add_if_missing(db, Device(
            id=sid(key), name=name, room=room, connection="MQTT", mqtt_topic=topic, mac=mac, fw="v1.4.2",
            online=True, last_seen_at=now, base_rssi=-58.0, current_rssi=-57.0, agc=26.0, noise_floor=-91.0,
            calibrating=False, calibration_stage="IDLE", calibration_progress=0.0,
            presence_mv_threshold=2.0, wander_baseline=0.5, facility_id=fac_id,
        )))
    db.flush()

    # 거주자 + 기기 매핑 (주 장치 = 첫 번째)
    residents = [
        ("r1", "김순옥", "302", 82, "강은우", ["d1"]),
        ("r2", "박영수", "305", 75, "강은우", ["d2"]),
        ("r3", "이철수", "201", 79, "김민지", ["d3"]),
        ("r4", "최영희", "208", 84, "김민지", ["d4"]),
        ("r5", "정말순", "104", 88, "박지현", ["d5"]),
        ("r6", "김옥자", "204", 84, "박지현", ["d6", "d6b"]),  # 다중 매핑 예
    ]
    for key, name, room, age, caregiver, dev_keys in residents:
        rid = sid(key)
        if _add_if_missing(db, Resident(id=rid, name=name, room=room, age=age, caregiver=caregiver, facility_id=fac_id)):
            count("residents", True)
            for i, dk in enumerate(dev_keys):
                db.add(ResidentDevice(resident_id=rid, device_id=sid(dk), is_primary=(i == 0)))
        else:
            count("residents", False)
    db.flush()

    # 수신자 (역할 한글 → 코드)
    recipients = [
        ("n1", "김보호 (김순옥 아들)", "FAMILY", "010-1234-5678", True, True, True, "r1"),
        ("n2", "박정민 (박영수 딸)", "FAMILY", "010-9876-5432", True, True, False, "r2"),
        ("n3", "강은우 요양사", "CAREGIVER", "010-2222-3333", True, True, False, "r1"),
        ("n4", "김민지 요양사", "CAREGIVER", "010-3333-4444", True, True, False, "r3"),
        ("n5", "시설 당직실", "ADMIN", "010-0000-0000", True, True, True, None),  # 공용
    ]
    for key, name, role, phone, sms, push, ars, rkey in recipients:
        count("recipients", _add_if_missing(db, Recipient(
            id=sid(key), name=name, role=role, phone=phone, sms=sms, push=push, ars=ars, enabled=True,
            resident_id=sid(rkey) if rkey else None, facility_id=fac_id,
        )))

    # 낙상 이력 1건 (42분 전, PENDING)
    fall_id = sid("f-seed-fac")
    count("fall_events", _add_if_missing(db, FallEvent(
        id=fall_id, resident_id=sid("r1"), resident_name="김순옥", room="302", device_id=sid("d1"),
        occurred_at=now - timedelta(minutes=42), confidence=0.94, duration_s=1.12, source="SEED",
        response="PENDING", facility_id=fac_id,
    )))

    # 테넌트 설정 (기본값) — 시설 1 + HOME 사용자 1
    count("tenant_configs", _add_if_missing(db, TenantConfig(id=sid("cfg-fac-1"), facility_id=fac_id)))
    count("tenant_configs", _add_if_missing(db, TenantConfig(id=sid("cfg-u-home"), owner_user_id=u_home)))

    # 이벤트 로그
    if db.query(EventLog).filter(EventLog.facility_id == fac_id, EventLog.msg == "시스템 초기화 완료").first() is None:
        db.add(EventLog(ts=now - timedelta(seconds=60), level="INFO", msg="시스템 초기화 완료", facility_id=fac_id))
        count("event_logs", True)
    else:
        count("event_logs", False)

    return added


def reset(db: Session) -> None:
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    db.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="모든 테이블을 비우고 다시 넣는다")
    args = ap.parse_args(argv)
    with get_session() as db:
        if args.reset:
            reset(db)
        added = seed(db)
    print("seed:", ", ".join(f"{k}+{v}" for k, v in added.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
