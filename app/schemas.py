import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import GiftStatus, Rarity, Role


class UserOut(BaseModel):
    id: uuid.UUID
    nickname: str
    role: Role


class CardOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    rarity: Rarity
    quantity: int
    question: str
    interpretation: str
    display_order: int


class DrawOut(BaseModel):
    draw_id: uuid.UUID
    card: CardOut
    draws_remaining_today: int


class GiftCreateIn(BaseModel):
    card_type_id: uuid.UUID


class GiftOut(BaseModel):
    gift_id: uuid.UUID
    share_url: str
    expires_at: datetime
    status: GiftStatus
    card: CardOut


class GiftPreviewOut(BaseModel):
    sender_nickname: str
    card: CardOut
    status: GiftStatus
    expires_at: datetime


class ClaimOut(BaseModel):
    gift_id: uuid.UUID
    claimed_at: datetime
    card: CardOut


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorOut(BaseModel):
    error: ErrorDetail


IdempotencyKey = Field(min_length=8, max_length=100)

