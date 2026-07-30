import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Role(str, enum.Enum):
    USER = "USER"
    MASTER = "MASTER"
    INHERITOR = "INHERITOR"


class Rarity(str, enum.Enum):
    NORMAL = "NORMAL"
    RARE = "RARE"


class GiftStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    CLAIMED = "CLAIMED"


class QuotaKind(str, enum.Enum):
    DRAW = "DRAW"
    GIFT_NORMAL = "GIFT_NORMAL"
    GIFT_RARE = "GIFT_RARE"
    RECEIVE_NORMAL = "RECEIVE_NORMAL"
    RECEIVE_RARE = "RECEIVE_RARE"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nickname: Mapped[str] = mapped_column(String(50), unique=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"))
    phone: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)


class CardType(Base):
    __tablename__ = "card_types"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    rarity: Mapped[Rarity] = mapped_column(Enum(Rarity, name="rarity"))
    draw_weight: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    interpretation: Mapped[str] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, unique=True)

    __table_args__ = (CheckConstraint("draw_weight > 0", name="ck_card_weight_positive"),)


class CardBalance(Base):
    __tablename__ = "card_balances"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    card_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_types.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
    card_type: Mapped[CardType] = relationship()

    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_card_quantity_nonnegative"),)


class CardTransaction(Base):
    __tablename__ = "card_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    card_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_types.id"))
    delta: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_type", "source_id", "card_type_id", name="uq_card_tx_source"
        ),
    )


class QuotaUsage(Base):
    __tablename__ = "quota_usage"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    kind: Mapped[QuotaKind] = mapped_column(Enum(QuotaKind, name="quota_kind"), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )

    __table_args__ = (CheckConstraint("used >= 0", name="ck_quota_used_nonnegative"),)


class GiftLink(Base):
    __tablename__ = "gift_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    card_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_types.id"))
    status: Mapped[GiftStatus] = mapped_column(
        Enum(GiftStatus, name="gift_status"), default=GiftStatus.AVAILABLE
    )
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sender: Mapped[User] = relationship(foreign_keys=[sender_id])
    card_type: Mapped[CardType] = relationship()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    http_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)

    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_scope"),
    )

