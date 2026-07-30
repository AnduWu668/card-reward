from fastapi import APIRouter, Depends, Header, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models import User
from app.schemas import (
    CardOut,
    ClaimOut,
    DrawOut,
    GiftCreateIn,
    GiftOut,
    GiftPreviewOut,
    UserOut,
)
from app.services import (
    cache_app_error,
    claim_gift,
    create_gift,
    current_user,
    draw_card,
    gift_preview,
    list_cards,
    request_digest,
    token_hash,
)

router = APIRouter(prefix="/api/v1")


def demo_user(
    x_demo_user_id: str = Header(alias="X-Demo-User-Id"),
    db: Session = Depends(get_db),
) -> User:
    return current_user(db, x_demo_user_id)


def idempotency_key(
    value: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"),
) -> str:
    return value


@router.get("/demo/users", response_model=list[UserOut])
def users(db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.role, User.nickname)).all()


@router.get("/cards", response_model=list[CardOut])
def cards(user: User = Depends(demo_user), db: Session = Depends(get_db)):
    return list_cards(db, user)


@router.post("/draws", response_model=DrawOut)
def draw(
    user: User = Depends(demo_user),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        return draw_card(db, user, key)
    except AppError as error:
        cache_app_error(db, user, "draw", key, request_digest({}), error)
        raise


@router.post("/gift-links", response_model=GiftOut, status_code=status.HTTP_201_CREATED)
def gift_create(
    payload: GiftCreateIn,
    user: User = Depends(demo_user),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        return create_gift(db, user, payload.card_type_id, key)
    except AppError as error:
        cache_app_error(
            db,
            user,
            "create_gift",
            key,
            request_digest({"card_type_id": payload.card_type_id}),
            error,
        )
        raise


@router.get("/gift-links/{token}", response_model=GiftPreviewOut)
def gift_get(token: str, db: Session = Depends(get_db)):
    return gift_preview(db, token)


@router.post("/gift-links/{token}/claims", response_model=ClaimOut)
def gift_claim(
    token: str,
    user: User = Depends(demo_user),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        return claim_gift(db, user, token, key)
    except AppError as error:
        cache_app_error(
            db,
            user,
            "claim_gift",
            key,
            request_digest({"token_hash": token_hash(token)}),
            error,
        )
        raise
