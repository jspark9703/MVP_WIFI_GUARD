from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from wifiguard_contracts.api import ErrorOut, RecipientIn, RecipientOut, RecipientPatch
from wifiguard_db.models import Recipient, Resident

from ..deps import Scope, add_log, assert_in_scope, get_db, get_scope, scope_filter, scoped_get, stamp_scope
from ..errors import ApiError

router = APIRouter(prefix="/recipients", tags=["recipients"])
_ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}, 400: {"model": ErrorOut}}


@router.get("", response_model=list[RecipientOut], responses=_ERR)
def list_recipients(
    resident_id: uuid.UUID | None = Query(default=None, alias="residentId"),
    scope: Scope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> list[Recipient]:
    where = [scope_filter(Recipient, scope)]
    if resident_id:
        where.append(Recipient.resident_id == resident_id)
    return list(db.execute(select(Recipient).where(*where).order_by(Recipient.created_at)).scalars())


@router.post("", response_model=RecipientOut, status_code=status.HTTP_201_CREATED, responses=_ERR)
def create_recipient(body: RecipientIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Recipient:
    if body.resident_id:
        assert_in_scope(db, Resident, [body.resident_id], scope, field="residentId")
    rec = Recipient(**body.model_dump())
    rec.name = rec.name.strip()
    stamp_scope(rec, scope)
    db.add(rec)
    db.flush()
    add_log(db, scope, "INFO", f"알림 수신자 등록: {rec.name}", resident_id=rec.resident_id)
    db.commit()
    return rec


@router.patch("/{recipient_id}", response_model=RecipientOut, responses=_ERR)
def patch_recipient(recipient_id: uuid.UUID, body: RecipientPatch, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Recipient:
    rec = scoped_get(db, Recipient, recipient_id, scope, what="수신자")
    data = body.model_dump(exclude_unset=True)
    data.pop("clear_resident", None)
    if "resident_id" in data and data["resident_id"] is not None:
        assert_in_scope(db, Resident, [data["resident_id"]], scope, field="residentId")
    for key, value in data.items():
        if value is None and key != "resident_id":
            continue
        setattr(rec, key, value.strip() if isinstance(value, str) else value)
    if body.clear_resident:
        rec.resident_id = None
    db.commit()
    return rec


@router.delete("/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERR)
def delete_recipient(recipient_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Response:
    rec = scoped_get(db, Recipient, recipient_id, scope, what="수신자")
    add_log(db, scope, "INFO", f"알림 수신자 삭제: {rec.name}")
    db.delete(rec)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{recipient_id}/test", responses={**_ERR, 501: {"model": ErrorOut}})
def test_recipient(recipient_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> None:
    scoped_get(db, Recipient, recipient_id, scope, what="수신자")
    raise ApiError(501, "NOT_IMPLEMENTED", "알림 발송은 다음 단계(알림 서비스)에서 제공됩니다.")
