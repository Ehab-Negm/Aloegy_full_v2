from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import random
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from livekit import api as livekit_api
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, desc, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
STORAGE_DIR = BACKEND_DIR / "storage"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / "agent" / ".env")

logging.basicConfig(
    level=os.getenv("BACKEND_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("aloegy.backend")

APP_ENV = os.getenv("APP_ENV", "dev").strip().lower() or "dev"
BACKEND_DB_PATH = Path(os.getenv("BACKEND_DB_PATH", str(DATA_DIR / "app.db")))
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_TTL_MINUTES = max(10, int(os.getenv("JWT_TTL_MINUTES", "720")))
OTP_TTL_MINUTES = max(1, int(os.getenv("OTP_TTL_MINUTES", "10")))
DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS", "123456").strip()
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "mock_secret_key").strip()
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()
DEMO_SESSION_TTL_MINUTES = max(1, int(os.getenv("DEMO_SESSION_TTL_MINUTES", "15")))
DEFAULT_RESTAURANT_PUBLIC_ID = os.getenv("DEMO_RESTAURANT_ID", "demo-restaurant").strip() or "demo-restaurant"

DEFAULT_CORS_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)).split(",") if origin.strip()]
DEFAULT_CORS_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$"
CORS_ORIGIN_REGEX = (
    r"https?://.*"
    if APP_ENV != "prod"
    else (os.getenv("CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX).strip() or None)
)
ALLOW_CREDENTIALS = APP_ENV == "prod"
BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    SQLALCHEMY_DATABASE_URL = DATABASE_URL
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_size=10, max_overflow=20, pool_pre_ping=True)
    logger.info("database | using DATABASE_URL (PostgreSQL or external)")
else:
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{BACKEND_DB_PATH.as_posix()}"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    logger.info("database | using SQLite at %s", BACKEND_DB_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class OrderEventBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, set[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, restaurant_id: int) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(restaurant_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, restaurant_id: int, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(restaurant_id)
            if not subscribers:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(restaurant_id, None)

    def publish(self, restaurant_id: int, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(restaurant_id, ()))
        for subscriber in subscribers:
            subscriber.put_nowait(event)


order_event_broker = OrderEventBroker()


class Base(DeclarativeBase):
    pass


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_name: Mapped[str] = mapped_column(String(120))
    owner_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    address: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    working_hours: Mapped[str] = mapped_column(Text, default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    plan: Mapped[str] = mapped_column(String(40), default="Basic")
    status: Mapped[str] = mapped_column(String(20), default="active")
    assigned_phone: Mapped[str] = mapped_column(String(32), default="")
    agent_name: Mapped[str] = mapped_column(String(120), default="Aloegy")
    voice_style: Mapped[str] = mapped_column(String(120), default="Warm Egyptian Arabic")
    language: Mapped[str] = mapped_column(String(120), default="Egyptian Arabic")
    personality: Mapped[str] = mapped_column(Text, default="")
    pre_call_instructions: Mapped[str] = mapped_column(Text, default="")
    supplementary_info: Mapped[str] = mapped_column(Text, default="")
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    closed_reason: Mapped[str] = mapped_column(String(255), default="")
    wait_minutes: Mapped[int] = mapped_column(Integer, default=20)
    min_guests: Mapped[int] = mapped_column(Integer, default=1)
    max_guests: Mapped[int] = mapped_column(Integer, default=20)
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_minutes: Mapped[int] = mapped_column(Integer, default=45)
    delivery_fee: Mapped[float] = mapped_column(Float, default=15.0)
    min_order: Mapped[float] = mapped_column(Float, default=80.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id"), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    restaurant: Mapped[Restaurant | None] = relationship()


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SalesRequest(Base):
    __tablename__ = "sales_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    restaurant_name: Mapped[str] = mapped_column(String(120))
    owner_name: Mapped[str] = mapped_column(String(120))
    owner_phone: Mapped[str] = mapped_column(String(32))
    location: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class DemoSessionRecord(Base):
    __tablename__ = "demo_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    restaurant_name: Mapped[str] = mapped_column(String(120))
    phone_number: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ContactLead(Base):
    __tablename__ = "contact_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80), default="Meals")
    ingredients: Mapped[str] = mapped_column(Text, default="")
    small_price: Mapped[float] = mapped_column(Float, default=0.0)
    medium_price: Mapped[float] = mapped_column(Float, default=0.0)
    large_price: Mapped[float] = mapped_column(Float, default=0.0)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("public_id"), UniqueConstraint("idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    type: Mapped[str] = mapped_column(String(20), default="takeaway")
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(32), index=True)
    items_summary: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="received")
    upsell: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="dashboard")
    driver_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_zone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_landmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)

    items: Mapped[list["OrderItem"]] = relationship(cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[float] = mapped_column(Float, default=0.0)


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    customer_phone: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text)
    complaint_type: Mapped[str] = mapped_column(String(80), default="general")
    status: Mapped[str] = mapped_column(String(20), default="new")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (UniqueConstraint("public_id"), UniqueConstraint("idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_phone: Mapped[str] = mapped_column(String(32))
    reservation_time: Mapped[str] = mapped_column(String(255))
    reservation_time_iso: Mapped[str | None] = mapped_column(String(64), nullable=True)
    guests_count: Mapped[int] = mapped_column(Integer, default=1)
    branch: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    last_message: Mapped[str] = mapped_column(Text, default="")
    ai_response: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="closed")
    order_total: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class FileAsset(Base):
    __tablename__ = "file_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(80), default="file")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    stored_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class PhoneRequest(BaseModel):
    phone: str


class VerifyOtpRequest(BaseModel):
    phone: str
    otp: str


class ContactFormRequest(BaseModel):
    restaurantName: str
    phone: str
    message: str = ""


class OrderStatusUpdateRequest(BaseModel):
    status: str
    driverPhone: str | None = None


class RestaurantSettingsPayload(BaseModel):
    name: str
    address: str
    workingHours: str
    contactPhone: str


class AgentSettingsPayload(BaseModel):
    name: str
    voiceStyle: str
    language: str
    personality: str
    preCallInstructions: str
    supplementaryInfo: str


class SettingsPayload(BaseModel):
    restaurant: RestaurantSettingsPayload
    agent: AgentSettingsPayload


class MenuItemPayload(BaseModel):
    name: str = ""
    category: str = "وجبات"
    ingredients: str = ""
    smallPrice: str = "0"
    mediumPrice: str = "0"
    largePrice: str = "0"
    available: bool = True


class EmployeePayload(BaseModel):
    name: str
    phone: str


class IssueStatusPayload(BaseModel):
    status: Literal["new", "resolved"]


class AdminRestaurantCreatePayload(BaseModel):
    name: str
    ownerName: str
    ownerPhone: str
    location: str = ""
    plan: str = "أساسي"
    assignedPhone: str = ""


class SalesMemberPayload(BaseModel):
    name: str
    phone: str


class SalesRequestPayload(BaseModel):
    restaurantName: str
    ownerName: str
    ownerPhone: str
    location: str = ""


class SalesRequestStatusPayload(BaseModel):
    status: Literal["approved", "rejected"]


class DemoSessionPayload(BaseModel):
    restaurantName: str
    phoneNumber: str


class DemoLivekitSessionPayload(BaseModel):
    restaurantId: str | None = None
    participantName: str | None = None


class AgentOrderItemPayload(BaseModel):
    name: str
    qty: int = 1
    price: float = 0.0


class AgentOrderPayload(BaseModel):
    call_id: str
    type: Literal["takeaway", "delivery"]
    customer_name: str
    customer_phone: str
    order_items: list[AgentOrderItemPayload]
    special_requests: str | None = None
    delivery_address: str | None = None
    delivery_zone: str | None = None
    delivery_landmark: str | None = None
    order_time: str | None = None
    channel: str = "voice_agent"


class AgentReservationPayload(BaseModel):
    call_id: str
    customer_name: str
    customer_phone: str
    reservation_time: str
    reservation_time_iso: str | None = None
    guests_count: int
    branch: str | None = None
    notes: str | None = None
    channel: str = "voice_agent"


class AgentComplaintPayload(BaseModel):
    call_id: str
    customer_name: str
    customer_phone: str
    complaint_text: str
    complaint_type: str
    logged_at: str | None = None
    channel: str = "voice_agent"


class CurrentUser(BaseModel):
    id: int
    name: str
    phone: str
    role: str
    restaurant_id: int | None

    model_config = ConfigDict(from_attributes=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_phone(value: str) -> str:
    stripped = "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+")
    if stripped.startswith("00"):
        stripped = f"+{stripped[2:]}"
    if stripped.startswith("20") and not stripped.startswith("+20"):
        stripped = f"+{stripped}"
    if stripped.startswith("0"):
        stripped = f"+20{stripped[1:]}"
    return stripped


def validate_phone_or_400(value: str) -> str:
    phone = normalize_phone(value)
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 11:
        raise HTTPException(status_code=400, detail="invalid phone number")
    return phone


def hash_otp(phone: str, code: str) -> str:
    return hashlib.sha256(f"{normalize_phone(phone)}::{code}::{JWT_SECRET}".encode("utf-8")).hexdigest()


def create_access_token(user: User) -> str:
    expires_at = utc_now() + timedelta(minutes=JWT_TTL_MINUTES)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "restaurant_id": user.restaurant_id,
        "exp": int(expires_at.timestamp()),
        "iat": int(utc_now().timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc


def resolve_current_user_from_token(db: Session, token: str) -> CurrentUser:
    payload = decode_access_token(token)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return CurrentUser.model_validate(user)


def authenticate_current_user(
    db: Session,
    *,
    authorization: str | None = None,
    access_token: str | None = None,
) -> CurrentUser:
    if access_token and access_token.strip():
        return resolve_current_user_from_token(db, access_token.strip())
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return resolve_current_user_from_token(db, authorization.split(" ", 1)[1].strip())


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> CurrentUser:
    return authenticate_current_user(db, authorization=authorization)


def require_roles(*roles: str):
    def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    return _dependency


def verify_agent_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if x_api_key != BACKEND_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "restaurant"


def ensure_unique_public_id(db: Session, base_value: str) -> str:
    candidate = slugify(base_value)
    index = 1
    while db.scalar(select(Restaurant.id).where(Restaurant.public_id == candidate)) is not None:
        index += 1
        candidate = f"{slugify(base_value)}-{index}"
    return candidate


def currency_text(value: float) -> str:
    return f"{value:.0f} ج.م"


def file_size_text(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


def iso_date(value: datetime) -> str:
    return ensure_utc_datetime(value).date().isoformat()


def relative_time_label(value: datetime) -> str:
    delta = utc_now() - ensure_utc_datetime(value)
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "الآن"
    if minutes < 60:
        return f"من {minutes} دقيقة"
    hours = minutes // 60
    if hours < 24:
        return f"من {hours} ساعة"
    days = hours // 24
    if days == 1:
        return "أمس"
    return f"من {days} يوم"


def restaurant_or_404(db: Session, restaurant_id: int) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")
    return restaurant


def resolve_restaurant_scope(
    db: Session,
    user: CurrentUser,
    restaurant_id: int | None = None,
) -> Restaurant:
    if user.role == "admin":
        if restaurant_id is not None:
            return restaurant_or_404(db, restaurant_id)
        first_restaurant = db.scalar(select(Restaurant).order_by(Restaurant.id))
        if not first_restaurant:
            raise HTTPException(status_code=404, detail="restaurant not found")
        return first_restaurant
    if user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="user has no restaurant")
    return restaurant_or_404(db, user.restaurant_id)


def serialize_call(call: CallLog) -> dict[str, Any]:
    return {
        "id": call.id,
        "phone": call.phone,
        "lastMessage": call.last_message,
        "aiResponse": call.ai_response,
        "time": relative_time_label(call.created_at),
        "status": call.status,
        "orderTotal": currency_text(call.order_total),
    }


def serialize_order(order: Order) -> dict[str, Any]:
    return {
        "id": order.public_id,
        "phone": order.phone,
        "items": order.items_summary,
        "amount": currency_text(order.amount),
        "status": order.status,
        "date": iso_date(order.created_at),
        "upsell": order.upsell,
        "driverPhone": order.driver_phone,
    }


def format_sse_message(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def publish_order_event(restaurant: Restaurant, order: Order, action: Literal["created", "updated"]) -> None:
    order_event_broker.publish(
        restaurant.id,
        {
            "action": action,
            "restaurantId": restaurant.id,
            "order": serialize_order(order),
        },
    )


def serialize_file(file_asset: FileAsset, request: Request) -> dict[str, Any]:
    preview_url = str(request.base_url).rstrip("/") + f"/storage/{Path(file_asset.stored_path).relative_to(STORAGE_DIR).as_posix()}"
    return {
        "id": file_asset.id,
        "name": file_asset.name,
        "type": file_asset.type,
        "size": file_size_text(file_asset.size_bytes),
        "date": iso_date(file_asset.created_at),
        "url": preview_url,
    }


def serialize_menu_item(item: MenuItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "ingredients": item.ingredients,
        "smallPrice": f"{item.small_price:.0f}",
        "mediumPrice": f"{item.medium_price:.0f}",
        "largePrice": f"{item.large_price:.0f}",
        "available": item.available,
    }


def serialize_issue(issue: Issue) -> dict[str, Any]:
    return {
        "id": issue.id,
        "customerPhone": issue.customer_phone,
        "description": issue.description,
        "status": issue.status,
        "date": iso_date(issue.created_at),
        "callId": issue.call_id or f"ISS-{issue.id:04d}",
    }


def serialize_employee(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "phone": user.phone,
        "addedAt": iso_date(user.created_at),
    }


def restaurant_settings_payload(restaurant: Restaurant) -> dict[str, Any]:
    return {
        "restaurant": {
            "name": restaurant.name,
            "address": restaurant.address,
            "workingHours": restaurant.working_hours,
            "contactPhone": restaurant.contact_phone,
        },
        "agent": {
            "name": restaurant.agent_name,
            "voiceStyle": restaurant.voice_style,
            "language": restaurant.language,
            "personality": restaurant.personality,
            "preCallInstructions": restaurant.pre_call_instructions,
            "supplementaryInfo": restaurant.supplementary_info,
        },
    }


def upsert_call_log(
    db: Session,
    *,
    restaurant_id: int,
    phone: str,
    last_message: str,
    ai_response: str,
    status: str,
    order_total: float,
) -> None:
    db.add(
        CallLog(
            restaurant_id=restaurant_id,
            phone=phone,
            last_message=last_message,
            ai_response=ai_response,
            status=status,
            order_total=order_total,
        )
    )


def best_menu_price(item: MenuItem) -> float:
    for value in (item.medium_price, item.small_price, item.large_price):
        if value and value > 0:
            return float(value)
    return 0.0


DEFAULT_HOURS = {
    "saturday": {"open": "10:00", "close": "23:59"},
    "sunday": {"open": "10:00", "close": "23:59"},
    "monday": {"open": "10:00", "close": "23:59"},
    "tuesday": {"open": "10:00", "close": "23:59"},
    "wednesday": {"open": "10:00", "close": "23:59"},
    "thursday": {"open": "10:00", "close": "23:59"},
    "friday": {"open": "13:00", "close": "23:59"},
}


def _parse_working_hours(raw: str) -> dict[str, Any]:
    """Parse working_hours from DB. Supports JSON or falls back to defaults with text."""
    if not raw or not raw.strip():
        return DEFAULT_HOURS
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return DEFAULT_HOURS


def restaurant_config_payload(restaurant: Restaurant, menu_items: list[MenuItem]) -> dict[str, Any]:
    return {
        "id": restaurant.public_id,
        "name": restaurant.name,
        "phone": restaurant.contact_phone or restaurant.owner_phone,
        "address": restaurant.address,
        "branches": [{"name": restaurant.location or restaurant.name, "address": restaurant.address}],
        "hours": _parse_working_hours(restaurant.working_hours),
        "menu_items": [
            {"name": item.name, "price": best_menu_price(item), "available": item.available}
            for item in menu_items
        ],
        "upsell_rules": [
            {"trigger": menu_items[0].name, "suggestion": f"تحب تضيف {menu_items[1].name}؟"}
            for _ in [0]
            if len(menu_items) >= 2
        ],
        "is_open": restaurant.is_open,
        "closed_reason": restaurant.closed_reason,
        "wait_minutes": restaurant.wait_minutes,
        "min_guests": restaurant.min_guests,
        "max_guests": restaurant.max_guests,
        "delivery_enabled": restaurant.delivery_enabled,
        "delivery_minutes": restaurant.delivery_minutes,
        "delivery_fee": restaurant.delivery_fee,
        "min_order": restaurant.min_order,
        "delivery_zones": [restaurant.location] if restaurant.location else [],
    }


def compute_customer_profiles(db: Session, restaurant_id: int) -> list[dict[str, Any]]:
    orders = db.scalars(select(Order).where(Order.restaurant_id == restaurant_id).order_by(desc(Order.created_at))).all()
    grouped: dict[str, list[Order]] = {}
    for order in orders:
        grouped.setdefault(order.phone, []).append(order)

    profiles = []
    for phone, user_orders in grouped.items():
        total_spent = sum(order.amount for order in user_orders)
        total_orders = len(user_orders)
        avg_order = total_spent / total_orders if total_orders else 0.0
        profiles.append(
            {
                "phone": phone,
                "totalOrders": total_orders,
                "totalSpent": currency_text(total_spent),
                "lastOrder": relative_time_label(user_orders[0].created_at),
                "avgOrder": currency_text(avg_order),
            }
        )
    return profiles


def compute_dashboard_stats(db: Session, restaurant_id: int) -> dict[str, str]:
    calls_count = db.scalar(select(func.count(CallLog.id)).where(CallLog.restaurant_id == restaurant_id)) or 0
    orders_count = db.scalar(select(func.count(Order.id)).where(Order.restaurant_id == restaurant_id)) or 0
    files_count = db.scalar(select(func.count(FileAsset.id)).where(FileAsset.restaurant_id == restaurant_id)) or 0
    customers_count = db.scalar(select(func.count(func.distinct(Order.phone))).where(Order.restaurant_id == restaurant_id)) or 0
    return {
        "totalCalls": str(calls_count),
        "totalOrders": str(orders_count),
        "totalCustomers": str(customers_count),
        "totalFiles": str(files_count),
    }


def compute_analytics(db: Session, restaurant_id: int) -> dict[str, Any]:
    # Summary — single SQL aggregation instead of loading all orders
    summary_row = db.execute(
        select(
            func.coalesce(func.sum(Order.amount), 0.0).label("total_revenue"),
            func.count(Order.id).label("total_orders"),
        ).where(Order.restaurant_id == restaurant_id)
    ).one()
    total_revenue = float(summary_row.total_revenue)
    total_orders = int(summary_row.total_orders)
    avg_order = total_revenue / total_orders if total_orders else 0.0

    today = utc_now().date()
    today_count = db.scalar(
        select(func.count(Order.id)).where(
            Order.restaurant_id == restaurant_id,
            func.date(Order.created_at) == today,
        )
    ) or 0

    # Daily orders — last 14 days aggregated in SQL
    fourteen_days_ago = utc_now() - timedelta(days=14)
    daily_rows = db.execute(
        select(
            func.strftime("%w", Order.created_at).label("dow"),
            func.count(Order.id).label("cnt"),
            func.coalesce(func.sum(Order.amount), 0.0).label("rev"),
        )
        .where(Order.restaurant_id == restaurant_id, Order.created_at >= fourteen_days_ago)
        .group_by("dow")
        .order_by("dow")
    ).all()
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    daily_orders = [
        {"day": day_labels[int(row.dow)] if row.dow is not None else "?", "orders": int(row.cnt), "revenue": float(row.rev)}
        for row in daily_rows
    ]

    # Monthly revenue — aggregated in SQL
    monthly_rows = db.execute(
        select(
            func.strftime("%Y-%m", Order.created_at).label("month"),
            func.coalesce(func.sum(Order.amount), 0.0).label("rev"),
        )
        .where(Order.restaurant_id == restaurant_id)
        .group_by("month")
        .order_by("month")
    ).all()
    monthly_revenue = [{"month": row.month, "revenue": float(row.rev)} for row in monthly_rows][-6:]

    # Category breakdown — only loads order_items (lighter than full orders)
    category_rows = db.execute(
        select(
            MenuItem.category,
            func.coalesce(func.sum(OrderItem.qty), 0).label("total_qty"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .outerjoin(MenuItem, func.lower(OrderItem.name) == func.lower(MenuItem.name))
        .where(Order.restaurant_id == restaurant_id)
        .group_by(MenuItem.category)
    ).all()
    category_data = [{"name": row.category or "أصناف أخرى", "value": int(row.total_qty)} for row in category_rows]
    if not category_data:
        category_data = [{"name": "وجبات", "value": 1}]

    return {
        "summary": {
            "totalRevenue": currency_text(total_revenue),
            "avgOrder": currency_text(avg_order),
            "todayOrders": str(today_count),
            "responseTime": "1.2 ثانية",
        },
        "dailyOrders": daily_orders[-7:],
        "monthlyRevenue": monthly_revenue,
        "categoryData": category_data,
    }


def generate_public_order_id(db: Session) -> str:
    next_number = (db.scalar(select(func.count(Order.id))) or 0) + 1
    return f"ORD-{utc_now().year}-{next_number:05d}"


def generate_public_reservation_id(db: Session) -> str:
    next_number = (db.scalar(select(func.count(Reservation.id))) or 0) + 1
    return f"RES-{utc_now().year}-{next_number:05d}"


def get_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        return "Image"
    if suffix == ".pdf":
        return "PDF"
    if suffix in {".xlsx", ".xls", ".csv"}:
        return "Excel"
    if suffix in {".doc", ".docx"}:
        return "Word"
    return "File"


def save_upload_file(restaurant: Restaurant, upload: UploadFile) -> tuple[str, int]:
    restaurant_dir = STORAGE_DIR / restaurant.public_id
    restaurant_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", upload.filename or "file.bin")
    unique_name = f"{uuid.uuid4().hex[:10]}-{safe_name}"
    target = restaurant_dir / unique_name
    data = upload.file.read()
    target.write_bytes(data)
    return str(target), len(data)


def ensure_livekit_configured() -> None:
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="livekit environment variables are missing")


async def create_livekit_demo_session(data: DemoLivekitSessionPayload, db: Session) -> dict[str, Any]:
    ensure_livekit_configured()
    requested_public_id = (data.restaurantId or DEFAULT_RESTAURANT_PUBLIC_ID).strip()
    restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_public_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")

    room_name = f"web-demo-{restaurant.public_id}-{uuid.uuid4().hex[:10]}"
    participant_identity = f"web-user-{uuid.uuid4().hex[:12]}"
    participant_name = (data.participantName or "Website Demo Visitor").strip() or "Website Demo Visitor"
    room_metadata = json.dumps({"restaurant_id": restaurant.public_id, "source": "website_demo"}, ensure_ascii=False)

    try:
        async with livekit_api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET) as lk_api:
            await lk_api.room.create_room(
                livekit_api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=60,
                    max_participants=4,
                    metadata=room_metadata,
                )
            )
    except Exception as exc:
        logger.exception("livekit demo session creation failed")
        raise HTTPException(status_code=503, detail="demo session service is temporarily unavailable") from exc

    token = (
        livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(participant_identity)
        .with_name(participant_name)
        .with_metadata(json.dumps({"source": "website_demo", "restaurant_id": restaurant.public_id}, ensure_ascii=False))
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(minutes=DEMO_SESSION_TTL_MINUTES))
        .to_jwt()
    )

    return {
        "ok": True,
        "livekitUrl": LIVEKIT_URL,
        "roomName": room_name,
        "token": token,
        "participantIdentity": participant_identity,
        "participantName": participant_name,
        "restaurantId": restaurant.public_id,
        "roomMetadata": room_metadata,
        "expiresInSeconds": DEMO_SESSION_TTL_MINUTES * 60,
    }


def seed_database() -> None:
    with SessionLocal() as db:
        has_restaurants = db.scalar(select(func.count(Restaurant.id))) or 0
        if has_restaurants:
            return

        demo_restaurant = Restaurant(
            public_id=DEFAULT_RESTAURANT_PUBLIC_ID,
            name="بيتزا كينج",
            owner_name="محمد أحمد",
            owner_phone=normalize_phone("+201012345678"),
            address="12 شارع التحرير، الدقي، الجيزة",
            location="الدقي، الجيزة",
            working_hours="من 10 الصبح لـ 12 بالليل - كل يوم ماعدا الجمعة من 2 الظهر",
            contact_phone=normalize_phone("+201012345678"),
            plan="بريميوم",
            status="active",
            assigned_phone="+20221234567",
            agent_name="ألو إيچي",
            voice_style="ودود ومصري",
            language="عامية مصرية",
            personality="بيساعد العميل بسرعة وبيقترح إضافات ذكية بشكل طبيعي.",
            pre_call_instructions="اسأل عن الطلب، أكد التفاصيل، واقترح إضافة مناسبة.",
            supplementary_info="عرض الفاميلي يوم الخميس. التوصيل مجاني فوق 150 جنيه.",
        )
        second_restaurant = Restaurant(
            public_id="shawerma-elsham",
            name="شاورما الشام",
            owner_name="فاطمة حسن",
            owner_phone=normalize_phone("+201098765432"),
            address="مدينة نصر، القاهرة",
            location="مدينة نصر، القاهرة",
            working_hours="من 11 صباحا حتى 1 صباحا",
            contact_phone=normalize_phone("+201098765432"),
            plan="أساسي",
            status="active",
            assigned_phone="+20221234568",
            agent_name="ألو إيچي",
            voice_style="سريع وواضح",
            language="عامية مصرية",
            personality="يرد بسرعة ويأكد العنوان قبل إنهاء المكالمة.",
            pre_call_instructions="أكد رقم الهاتف والعنوان في طلبات التوصيل.",
            supplementary_info="فيه عرض شاورما فاميلي آخر الأسبوع.",
        )
        third_restaurant = Restaurant(
            public_id="koshary-tahrir",
            name="كشري التحرير",
            owner_name="عمرو خالد",
            owner_phone=normalize_phone("+201155544433"),
            address="وسط البلد، القاهرة",
            location="التحرير، القاهرة",
            working_hours="من 10 صباحا حتى منتصف الليل",
            contact_phone=normalize_phone("+201155544433"),
            plan="بريميوم",
            status="active",
            assigned_phone="+20221234569",
            agent_name="ألو إيچي",
            voice_style="مصري ابن بلد",
            language="عامية مصرية",
            personality="ابن بلد ويعرف يقفل الطلب بسرعة.",
            pre_call_instructions="اقترح حمصية أو بيبسي مع كل طلب كشري.",
            supplementary_info="التوصيل 45 دقيقة وحد أدنى 60 جنيه.",
        )
        db.add_all([demo_restaurant, second_restaurant, third_restaurant])
        db.flush()

        users = [
            User(name="System Admin", phone=normalize_phone("+201094321642"), role="admin"),
            User(name="Sales One", phone=normalize_phone("+201111111111"), role="sales"),
            User(name="Sales Two", phone=normalize_phone("+201222222222"), role="sales"),
            User(name=demo_restaurant.owner_name, phone=demo_restaurant.owner_phone, role="owner", restaurant_id=demo_restaurant.id),
            User(name=second_restaurant.owner_name, phone=second_restaurant.owner_phone, role="owner", restaurant_id=second_restaurant.id),
            User(name=third_restaurant.owner_name, phone=third_restaurant.owner_phone, role="owner", restaurant_id=third_restaurant.id),
            User(name="أحمد محمد", phone=normalize_phone("+201112223344"), role="employee", restaurant_id=demo_restaurant.id),
            User(name="سارة علي", phone=normalize_phone("+201155667788"), role="employee", restaurant_id=demo_restaurant.id),
        ]
        db.add_all(users)

        menu_items = [
            MenuItem(restaurant_id=demo_restaurant.id, name="بيتزا مارجريتا", category="وجبات", ingredients="جبنة موتزاريلا، صلصة طماطم، ريحان", small_price=80, medium_price=120, large_price=160),
            MenuItem(restaurant_id=demo_restaurant.id, name="شاورما فراخ", category="وجبات", ingredients="فراخ، طحينة، مخلل، بطاطس", small_price=60, medium_price=90, large_price=120),
            MenuItem(restaurant_id=demo_restaurant.id, name="كولا", category="مشروبات", ingredients="", small_price=15, medium_price=20, large_price=25),
            MenuItem(restaurant_id=demo_restaurant.id, name="كنانة بطاطس", category="إضافات", ingredients="بطاطس كرسبي", small_price=25, medium_price=35, large_price=45),
            MenuItem(restaurant_id=second_restaurant.id, name="شاورما سوري", category="وجبات", ingredients="فراخ، ثومية", small_price=70, medium_price=95, large_price=130),
            MenuItem(restaurant_id=second_restaurant.id, name="بيبسي", category="مشروبات", ingredients="", small_price=20, medium_price=25, large_price=30),
            MenuItem(restaurant_id=third_restaurant.id, name="كشري كبير", category="وجبات", ingredients="مكرونة، أرز، صلصة", small_price=35, medium_price=45, large_price=60),
            MenuItem(restaurant_id=third_restaurant.id, name="حمصية", category="إضافات", ingredients="حمص و دقة", small_price=12, medium_price=16, large_price=20),
        ]
        db.add_all(menu_items)
        db.flush()

        def add_order(restaurant: Restaurant, phone: str, customer_name: str, items: list[tuple[str, int, float]], status_value: str, created_at: datetime, upsell: bool = False, source: str = "voice_agent") -> None:
            order = Order(
                public_id=f"ORD-{created_at.year}-{uuid.uuid4().hex[:5].upper()}",
                restaurant_id=restaurant.id,
                call_id=uuid.uuid4().hex[:8],
                type="delivery" if status_value in {"out_for_delivery", "delivered"} else "takeaway",
                customer_name=customer_name,
                phone=normalize_phone(phone),
                items_summary=" + ".join(f"{qty} {name}" for name, qty, _ in items),
                amount=sum(qty * price for _, qty, price in items),
                status=status_value,
                upsell=upsell,
                source=source,
                created_at=created_at,
            )
            db.add(order)
            db.flush()
            for name, qty, price in items:
                db.add(OrderItem(order_id=order.id, name=name, qty=qty, price=price))
            upsert_call_log(
                db,
                restaurant_id=restaurant.id,
                phone=normalize_phone(phone),
                last_message=order.items_summary,
                ai_response=f"تم تسجيل الطلب {order.public_id}",
                status="active" if status_value in {"received", "preparing"} else "closed",
                order_total=order.amount,
            )

        now = utc_now()
        add_order(demo_restaurant, "+201012345678", "محمد", [("بيتزا مارجريتا", 2, 120), ("كولا", 1, 20)], "preparing", now - timedelta(minutes=15), True)
        add_order(demo_restaurant, "+201012345678", "محمد", [("شاورما فراخ", 1, 90)], "delivered", now - timedelta(days=1), False)
        add_order(demo_restaurant, "+201111222333", "سارة", [("بيتزا مارجريتا", 1, 160), ("كنانة بطاطس", 1, 35)], "out_for_delivery", now - timedelta(hours=3), True)
        add_order(second_restaurant, "+201199887766", "ليلى", [("شاورما سوري", 2, 95), ("بيبسي", 2, 25)], "received", now - timedelta(hours=2), True)
        add_order(third_restaurant, "+201155544433", "عمرو", [("كشري كبير", 3, 60), ("حمصية", 1, 16)], "delivered", now - timedelta(days=2), True)

        db.add_all(
            [
                Issue(restaurant_id=demo_restaurant.id, call_id="CALL-1001", customer_name="محمد", customer_phone=normalize_phone("+201012345678"), description="الأوردر وصل بارد", complaint_type="delivery", status="new"),
                Issue(restaurant_id=demo_restaurant.id, call_id="CALL-1002", customer_name="سارة", customer_phone=normalize_phone("+201111222333"), description="الصنف ناقص", complaint_type="order", status="resolved"),
            ]
        )

        db.add_all(
            [
                SalesRequest(sales_user_id=2, restaurant_name="فول الحارة", owner_name="حسن علي", owner_phone=normalize_phone("+201177889900"), location="الهرم، الجيزة", status="pending"),
                SalesRequest(sales_user_id=3, restaurant_name="مشويات الصعيدي", owner_name="سعيد إبراهيم", owner_phone=normalize_phone("+201188990011"), location="شبرا، القاهرة", status="pending"),
            ]
        )

        db.add_all(
            [
                DemoSessionRecord(sales_user_id=2, restaurant_name="مشويات المعلم", phone_number=normalize_phone("+201122334455"), status="completed"),
                DemoSessionRecord(sales_user_id=2, restaurant_name="بيتزا إيطاليانو", phone_number=normalize_phone("+201133445566"), status="scheduled"),
            ]
        )

        sample_file = STORAGE_DIR / DEFAULT_RESTAURANT_PUBLIC_ID / "menu-demo.txt"
        sample_file.parent.mkdir(parents=True, exist_ok=True)
        sample_file.write_text("Demo menu file", encoding="utf-8")
        db.add(
            FileAsset(
                restaurant_id=demo_restaurant.id,
                name="menu_demo.txt",
                type="File",
                size_bytes=sample_file.stat().st_size,
                stored_path=str(sample_file),
            )
        )

        db.commit()
        logger.info("seeded backend database with sample data")


def _run_migrations() -> None:
    """Add columns that were introduced after the initial schema."""
    import sqlalchemy as sa

    inspector = sa.inspect(engine)
    migrations: list[tuple[str, str, str]] = [
        ("reservations", "reservation_time_iso", "VARCHAR(64)"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            existing = [c["name"] for c in inspector.get_columns(table)]
            if column not in existing:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info("migration | added column %s.%s", table, column)
        conn.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    seed_database()
    yield


app = FastAPI(title="Aloegy Backend", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if APP_ENV != "prod" else CORS_ORIGINS,
    allow_origin_regex=None if APP_ENV != "prod" else CORS_ORIGIN_REGEX,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "env": APP_ENV, "database": str(BACKEND_DB_PATH)}


@app.post("/contact")
def submit_contact_form(payload: ContactFormRequest, db: Session = Depends(get_db)) -> bool:
    db.add(ContactLead(restaurant_name=payload.restaurantName, phone=validate_phone_or_400(payload.phone), message=payload.message))
    db.commit()
    return True


@app.post("/auth/send-otp")
def send_otp(payload: PhoneRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    phone = validate_phone_or_400(payload.phone)
    user_exists = db.scalar(select(User).where(User.phone == phone))
    if not user_exists:
        raise HTTPException(status_code=403, detail="هذا الرقم غير مسجل في النظام. تواصل مع الإدارة.")
    code = f"{random.randint(0, 999999):06d}"
    db.add(
        OtpCode(
            phone=phone,
            code_hash=hash_otp(phone, code),
            expires_at=utc_now() + timedelta(minutes=OTP_TTL_MINUTES),
        )
    )
    db.commit()
    logger.info("otp generated | phone=%s", phone)
    return {"success": True}


@app.post("/auth/verify-otp")
def verify_otp(payload: VerifyOtpRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    phone = validate_phone_or_400(payload.phone)
    otp = payload.otp.strip()

    otp_row = db.scalar(
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.consumed_at.is_(None))
        .order_by(desc(OtpCode.created_at))
    )
    otp_is_valid = False
    if otp_row and ensure_utc_datetime(otp_row.expires_at) >= utc_now() and otp_row.code_hash == hash_otp(phone, otp):
        otp_is_valid = True
        otp_row.consumed_at = utc_now()
    elif APP_ENV != "prod" and otp == DEV_OTP_BYPASS:
        otp_is_valid = True

    if not otp_is_valid:
        raise HTTPException(status_code=400, detail="invalid otp")

    user = db.scalar(select(User).where(User.phone == phone))
    if not user:
        raise HTTPException(status_code=403, detail="هذا الرقم غير مسجل في النظام. تواصل مع الإدارة.")

    db.commit()
    db.refresh(user)

    return {
        "success": True,
        "token": create_access_token(user),
        "role": user.role,
    }


@app.get("/me")
def get_me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    restaurant = db.get(Restaurant, user.restaurant_id) if user.restaurant_id else None
    return {
        "id": user.id,
        "name": user.name,
        "phone": user.phone,
        "role": user.role,
        "restaurantId": user.restaurant_id,
        "restaurantName": restaurant.name if restaurant else None,
    }


@app.post("/demo/livekit-session")
async def create_demo_session_endpoint(payload: DemoLivekitSessionPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await create_livekit_demo_session(payload, db)


@app.get("/stats")
def fetch_stats(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    return compute_dashboard_stats(db, restaurant.id)


@app.get("/calls")
def fetch_calls(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    calls = db.scalars(select(CallLog).where(CallLog.restaurant_id == restaurant.id).order_by(desc(CallLog.created_at))).all()
    return [serialize_call(call) for call in calls]


@app.get("/users")
def fetch_users(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    return compute_customer_profiles(db, restaurant.id)


@app.get("/orders")
def fetch_orders(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    orders = db.scalars(select(Order).where(Order.restaurant_id == restaurant.id).order_by(desc(Order.created_at))).all()
    return [serialize_order(order) for order in orders]


@app.get("/orders/stream")
def stream_orders(
    token: str | None = Query(default=None),
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    user = authenticate_current_user(db, access_token=token)
    if user.role not in {"admin", "owner", "employee"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    subscriber = order_event_broker.subscribe(restaurant.id)

    def event_stream():
        try:
            yield format_sse_message("ready", {"restaurantId": restaurant.id})
            while True:
                try:
                    event = subscriber.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield format_sse_message("order_update", event)
        finally:
            order_event_broker.unsubscribe(restaurant.id, subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.patch("/orders/{order_public_id}")
def update_order_status(
    order_public_id: str,
    payload: OrderStatusUpdateRequest,
    user: CurrentUser = Depends(require_roles("admin", "owner", "employee")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    order = db.scalar(select(Order).where(Order.public_id == order_public_id))
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if user.role != "admin" and order.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="forbidden")
    order.status = payload.status
    order.driver_phone = normalize_phone(payload.driverPhone) if payload.driverPhone else None
    db.commit()
    db.refresh(order)
    publish_order_event(restaurant_or_404(db, order.restaurant_id), order, "updated")
    return serialize_order(order)


@app.get("/files")
def fetch_files(
    request: Request,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    files = db.scalars(select(FileAsset).where(FileAsset.restaurant_id == restaurant.id).order_by(desc(FileAsset.created_at))).all()
    return [serialize_file(file_asset, request) for file_asset in files]


MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_UPLOAD_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv",
    ".txt", ".json", ".xml", ".zip",
}


@app.post("/files/upload")
def upload_file(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"file type '{ext}' is not allowed")
    restaurant = resolve_restaurant_scope(db, user, None)
    stored_path, size_bytes = save_upload_file(restaurant, file)
    if size_bytes > MAX_UPLOAD_SIZE_BYTES:
        Path(stored_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="file size exceeds 10 MB limit")
    asset = FileAsset(
        restaurant_id=restaurant.id,
        name=file.filename or "upload.bin",
        type=get_file_type(file.filename or ""),
        size_bytes=size_bytes,
        stored_path=stored_path,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {
        "id": asset.id,
        "name": asset.name,
        "type": asset.type,
        "size": file_size_text(asset.size_bytes),
        "date": iso_date(asset.created_at),
    }


@app.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    asset = db.get(FileAsset, file_id)
    if not asset:
        raise HTTPException(status_code=404, detail="file not found")
    if user.role != "admin" and asset.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return FileResponse(asset.stored_path, filename=asset.name)


@app.get("/files/{file_id}/preview")
def preview_file(
    file_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    asset = db.get(FileAsset, file_id)
    if not asset:
        raise HTTPException(status_code=404, detail="file not found")
    if user.role != "admin" and asset.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="forbidden")
    url = str(request.base_url).rstrip("/") + f"/storage/{Path(asset.stored_path).relative_to(STORAGE_DIR).as_posix()}"
    return {"url": url}


@app.get("/settings")
def fetch_settings(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    return restaurant_settings_payload(restaurant)


@app.put("/settings")
def save_settings(
    payload: SettingsPayload,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    restaurant.name = payload.restaurant.name
    restaurant.address = payload.restaurant.address
    restaurant.working_hours = payload.restaurant.workingHours
    restaurant.contact_phone = validate_phone_or_400(payload.restaurant.contactPhone)
    restaurant.agent_name = payload.agent.name
    restaurant.voice_style = payload.agent.voiceStyle
    restaurant.language = payload.agent.language
    restaurant.personality = payload.agent.personality
    restaurant.pre_call_instructions = payload.agent.preCallInstructions
    restaurant.supplementary_info = payload.agent.supplementaryInfo
    db.commit()
    db.refresh(restaurant)
    return restaurant_settings_payload(restaurant)


@app.get("/menu-items")
def fetch_menu_items(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    items = db.scalars(select(MenuItem).where(MenuItem.restaurant_id == restaurant.id).order_by(MenuItem.id)).all()
    return [serialize_menu_item(item) for item in items]


@app.post("/menu-items")
def create_menu_item(
    payload: MenuItemPayload,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    item = MenuItem(
        restaurant_id=restaurant.id,
        name=payload.name,
        category=payload.category,
        ingredients=payload.ingredients,
        small_price=float(payload.smallPrice or 0),
        medium_price=float(payload.mediumPrice or 0),
        large_price=float(payload.largePrice or 0),
        available=payload.available,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize_menu_item(item)


@app.put("/menu-items/{item_id}")
def update_menu_item(
    item_id: int,
    payload: MenuItemPayload,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    item = db.get(MenuItem, item_id)
    if not item or item.restaurant_id != restaurant.id:
        raise HTTPException(status_code=404, detail="menu item not found")
    item.name = payload.name
    item.category = payload.category
    item.ingredients = payload.ingredients
    item.small_price = float(payload.smallPrice or 0)
    item.medium_price = float(payload.mediumPrice or 0)
    item.large_price = float(payload.largePrice or 0)
    item.available = payload.available
    item.updated_at = utc_now()
    db.commit()
    db.refresh(item)
    return serialize_menu_item(item)


@app.delete("/menu-items/{item_id}")
def delete_menu_item(
    item_id: int,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> Response:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    item = db.get(MenuItem, item_id)
    if not item or item.restaurant_id != restaurant.id:
        raise HTTPException(status_code=404, detail="menu item not found")
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@app.get("/employees")
def fetch_employees(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    employees = db.scalars(select(User).where(User.restaurant_id == restaurant.id, User.role == "employee").order_by(User.created_at)).all()
    return [serialize_employee(employee) for employee in employees]


@app.post("/employees")
def create_employee(
    payload: EmployeePayload,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    phone = validate_phone_or_400(payload.phone)
    existing = db.scalar(select(User).where(User.phone == phone))
    if existing:
        raise HTTPException(status_code=409, detail="phone already exists")
    employee = User(name=payload.name.strip(), phone=phone, role="employee", restaurant_id=restaurant.id)
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return serialize_employee(employee)


@app.delete("/employees/{employee_id}")
def delete_employee(
    employee_id: int,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> Response:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    employee = db.get(User, employee_id)
    if not employee or employee.restaurant_id != restaurant.id or employee.role != "employee":
        raise HTTPException(status_code=404, detail="employee not found")
    db.delete(employee)
    db.commit()
    return Response(status_code=204)


@app.get("/issues")
def fetch_issues(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    issues = db.scalars(select(Issue).where(Issue.restaurant_id == restaurant.id).order_by(desc(Issue.created_at))).all()
    return [serialize_issue(issue) for issue in issues]


@app.patch("/issues/{issue_id}")
def update_issue_status(
    issue_id: int,
    payload: IssueStatusPayload,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner", "employee")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    issue = db.get(Issue, issue_id)
    if not issue or issue.restaurant_id != restaurant.id:
        raise HTTPException(status_code=404, detail="issue not found")
    issue.status = payload.status
    db.commit()
    db.refresh(issue)
    return serialize_issue(issue)


@app.get("/analytics")
def fetch_analytics(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    return compute_analytics(db, restaurant.id)


@app.get("/admin/restaurants")
def fetch_admin_restaurants(
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    # Single query with subqueries instead of N+1
    calls_sub = (
        select(CallLog.restaurant_id, func.count(CallLog.id).label("cnt"))
        .group_by(CallLog.restaurant_id)
        .subquery()
    )
    orders_sub = (
        select(Order.restaurant_id, func.count(Order.id).label("cnt"))
        .group_by(Order.restaurant_id)
        .subquery()
    )
    employees_sub = (
        select(User.restaurant_id, func.count(User.id).label("cnt"))
        .where(User.role == "employee")
        .group_by(User.restaurant_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Restaurant,
            func.coalesce(calls_sub.c.cnt, 0).label("total_calls"),
            func.coalesce(orders_sub.c.cnt, 0).label("total_orders"),
            func.coalesce(employees_sub.c.cnt, 0).label("total_employees"),
        )
        .outerjoin(calls_sub, Restaurant.id == calls_sub.c.restaurant_id)
        .outerjoin(orders_sub, Restaurant.id == orders_sub.c.restaurant_id)
        .outerjoin(employees_sub, Restaurant.id == employees_sub.c.restaurant_id)
        .order_by(Restaurant.id)
    ).all()

    return [
        {
            "id": restaurant.id,
            "restaurantId": restaurant.id,
            "publicId": restaurant.public_id,
            "name": restaurant.owner_name,
            "phone": restaurant.owner_phone,
            "restaurantName": restaurant.name,
            "plan": restaurant.plan,
            "totalCalls": int(total_calls),
            "totalOrders": int(total_orders),
            "totalEmployees": int(total_employees),
            "joinedAt": iso_date(restaurant.created_at),
            "status": restaurant.status,
            "assignedPhone": restaurant.assigned_phone,
            "location": restaurant.location,
        }
        for restaurant, total_calls, total_orders, total_employees in rows
    ]


@app.post("/admin/restaurants")
def create_admin_restaurant(
    payload: AdminRestaurantCreatePayload,
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    owner_phone = validate_phone_or_400(payload.ownerPhone)
    if db.scalar(select(Restaurant.id).where(Restaurant.owner_phone == owner_phone)):
        raise HTTPException(status_code=409, detail="restaurant owner phone already exists")
    public_id = ensure_unique_public_id(db, payload.name)
    restaurant = Restaurant(
        public_id=public_id,
        name=payload.name,
        owner_name=payload.ownerName,
        owner_phone=owner_phone,
        address=payload.location,
        location=payload.location,
        working_hours="",
        contact_phone=owner_phone,
        plan=payload.plan,
        status="active",
        assigned_phone=payload.assignedPhone,
        agent_name="ألو إيچي",
        voice_style="ودود ومصري",
        language="عامية مصرية",
        personality="يساعد العميل ويقترح إضافات مفيدة.",
        pre_call_instructions="أكد الطلب قبل الإنهاء.",
        supplementary_info="",
    )
    db.add(restaurant)
    db.flush()
    db.add(User(name=payload.ownerName, phone=owner_phone, role="owner", restaurant_id=restaurant.id))
    db.commit()
    return fetch_admin_restaurants(user, db)[-1]


@app.get("/admin/sales-team")
def fetch_admin_sales_team(
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    members = db.scalars(select(User).where(User.role == "sales").order_by(User.created_at)).all()
    return [{"id": member.id, "name": member.name, "phone": member.phone} for member in members]


@app.post("/admin/sales-team")
def create_admin_sales_member(
    payload: SalesMemberPayload,
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    phone = validate_phone_or_400(payload.phone)
    if db.scalar(select(User.id).where(User.phone == phone)):
        raise HTTPException(status_code=409, detail="phone already exists")
    member = User(name=payload.name.strip() or "Sales Member", phone=phone, role="sales")
    db.add(member)
    db.commit()
    db.refresh(member)
    return {"id": member.id, "name": member.name, "phone": member.phone}


@app.delete("/admin/sales-team/{member_id}")
def delete_admin_sales_member(
    member_id: int,
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> Response:
    member = db.get(User, member_id)
    if not member or member.role != "sales":
        raise HTTPException(status_code=404, detail="sales member not found")
    db.delete(member)
    db.commit()
    return Response(status_code=204)


@app.get("/admin/sales-requests")
def fetch_admin_sales_requests(
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    sales_users = {sales_user.id: sales_user.name for sales_user in db.scalars(select(User).where(User.role == "sales")).all()}
    requests = db.scalars(select(SalesRequest).order_by(desc(SalesRequest.created_at))).all()
    return [
        {
            "id": item.id,
            "salesPerson": sales_users.get(item.sales_user_id, "Sales Team"),
            "restaurantName": item.restaurant_name,
            "ownerName": item.owner_name,
            "ownerPhone": item.owner_phone,
            "location": item.location,
            "status": item.status,
            "date": iso_date(item.created_at),
        }
        for item in requests
    ]


@app.patch("/admin/sales-requests/{request_id}")
def update_admin_sales_request(
    request_id: int,
    payload: SalesRequestStatusPayload,
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sales_request = db.get(SalesRequest, request_id)
    if not sales_request:
        raise HTTPException(status_code=404, detail="sales request not found")
    sales_request.status = payload.status
    if payload.status == "approved" and db.scalar(select(Restaurant.id).where(Restaurant.owner_phone == sales_request.owner_phone)) is None:
        public_id = ensure_unique_public_id(db, sales_request.restaurant_name)
        restaurant = Restaurant(
            public_id=public_id,
            name=sales_request.restaurant_name,
            owner_name=sales_request.owner_name,
            owner_phone=sales_request.owner_phone,
            address=sales_request.location,
            location=sales_request.location,
            working_hours="",
            contact_phone=sales_request.owner_phone,
            plan="أساسي",
            status="active",
            assigned_phone="",
            agent_name="ألو إيچي",
            voice_style="ودود ومصري",
            language="عامية مصرية",
            personality="يساعد العميل ويقفل الطلب بسرعة.",
            pre_call_instructions="أكد البيانات الأساسية.",
            supplementary_info="",
        )
        db.add(restaurant)
        db.flush()
        if db.scalar(select(User.id).where(User.phone == sales_request.owner_phone)) is None:
            db.add(User(name=sales_request.owner_name, phone=sales_request.owner_phone, role="owner", restaurant_id=restaurant.id))
    db.commit()
    sales_user = db.get(User, sales_request.sales_user_id) if sales_request.sales_user_id else None
    return {
        "id": sales_request.id,
        "salesPerson": sales_user.name if sales_user else "Sales Team",
        "restaurantName": sales_request.restaurant_name,
        "ownerName": sales_request.owner_name,
        "ownerPhone": sales_request.owner_phone,
        "location": sales_request.location,
        "status": sales_request.status,
        "date": iso_date(sales_request.created_at),
    }


@app.get("/admin/overview")
def fetch_admin_overview(
    user: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    live_calls = db.scalars(select(CallLog).where(CallLog.status == "active").order_by(desc(CallLog.created_at)).limit(5)).all()
    recent_orders = db.scalars(select(Order).order_by(desc(Order.created_at)).limit(5)).all()
    restaurant_lookup = {restaurant.id: restaurant.name for restaurant in db.scalars(select(Restaurant)).all()}
    return {
        "liveCalls": [
            {
                "restaurant": restaurant_lookup.get(call.restaurant_id, "Restaurant"),
                "phone": call.phone,
                "duration": relative_time_label(call.created_at),
                "status": "جاري",
            }
            for call in live_calls
        ],
        "recentOrders": [
            {
                "restaurant": restaurant_lookup.get(order.restaurant_id, "Restaurant"),
                "items": order.items_summary,
                "amount": currency_text(order.amount),
                "status": order.status,
            }
            for order in recent_orders
        ],
    }


@app.get("/sales/requests")
def fetch_sales_requests(
    user: CurrentUser = Depends(require_roles("sales")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    items = db.scalars(select(SalesRequest).where(SalesRequest.sales_user_id == user.id).order_by(desc(SalesRequest.created_at))).all()
    return [
        {
            "id": item.id,
            "restaurantName": item.restaurant_name,
            "ownerName": item.owner_name,
            "ownerPhone": item.owner_phone,
            "location": item.location,
            "status": item.status,
            "date": iso_date(item.created_at),
        }
        for item in items
    ]


@app.post("/sales/requests")
def create_sales_request(
    payload: SalesRequestPayload,
    user: CurrentUser = Depends(require_roles("sales")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = SalesRequest(
        sales_user_id=user.id,
        restaurant_name=payload.restaurantName,
        owner_name=payload.ownerName,
        owner_phone=validate_phone_or_400(payload.ownerPhone),
        location=payload.location,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "restaurantName": item.restaurant_name,
        "ownerName": item.owner_name,
        "ownerPhone": item.owner_phone,
        "location": item.location,
        "status": item.status,
        "date": iso_date(item.created_at),
    }


@app.get("/sales/demo-sessions")
def fetch_sales_demo_sessions(
    user: CurrentUser = Depends(require_roles("sales")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    items = db.scalars(select(DemoSessionRecord).where(DemoSessionRecord.sales_user_id == user.id).order_by(desc(DemoSessionRecord.created_at))).all()
    return [
        {
            "id": item.id,
            "restaurantName": item.restaurant_name,
            "phoneNumber": item.phone_number,
            "status": item.status,
            "date": iso_date(item.created_at),
        }
        for item in items
    ]


@app.post("/sales/demo-sessions")
def create_sales_demo_session(
    payload: DemoSessionPayload,
    user: CurrentUser = Depends(require_roles("sales")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = DemoSessionRecord(
        sales_user_id=user.id,
        restaurant_name=payload.restaurantName,
        phone_number=validate_phone_or_400(payload.phoneNumber),
        status="scheduled",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "restaurantName": item.restaurant_name,
        "phoneNumber": item.phone_number,
        "status": item.status,
        "date": iso_date(item.created_at),
    }


@app.get("/restaurant/config")
def get_restaurant_config(
    _: None = Depends(verify_agent_key),
    restaurant_id: str | None = Query(default=None),
    x_restaurant_id: str | None = Header(default=None, alias="X-Restaurant-ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requested_id = (restaurant_id or x_restaurant_id or DEFAULT_RESTAURANT_PUBLIC_ID).strip()
    restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")
    menu_items = db.scalars(select(MenuItem).where(MenuItem.restaurant_id == restaurant.id).order_by(MenuItem.id)).all()
    return restaurant_config_payload(restaurant, menu_items)


@app.post("/orders")
def create_agent_order(
    payload: AgentOrderPayload,
    _: None = Depends(verify_agent_key),
    restaurant_id: str | None = Query(default=None),
    x_restaurant_id: str | None = Header(default=None, alias="X-Restaurant-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requested_id = (restaurant_id or x_restaurant_id or DEFAULT_RESTAURANT_PUBLIC_ID).strip()
    restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")
    if idempotency_key:
        existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing:
            return {"order_id": existing.public_id, "estimated_time": restaurant.delivery_minutes if existing.type == "delivery" else restaurant.wait_minutes}

    order = Order(
        public_id=generate_public_order_id(db),
        restaurant_id=restaurant.id,
        call_id=payload.call_id,
        type=payload.type,
        customer_name=payload.customer_name,
        phone=validate_phone_or_400(payload.customer_phone),
        items_summary=" + ".join(f"{item.qty} {item.name}" for item in payload.order_items),
        amount=sum(item.qty * item.price for item in payload.order_items),
        status="received",
        upsell=len(payload.order_items) > 1,
        source=payload.channel,
        special_requests=payload.special_requests,
        delivery_address=payload.delivery_address,
        delivery_zone=payload.delivery_zone,
        delivery_landmark=payload.delivery_landmark,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    db.flush()
    for item in payload.order_items:
        db.add(OrderItem(order_id=order.id, name=item.name, qty=item.qty, price=item.price))
    upsert_call_log(
        db,
        restaurant_id=restaurant.id,
        phone=order.phone,
        last_message=order.items_summary,
        ai_response=f"تم تسجيل الطلب {order.public_id}",
        status="active",
        order_total=order.amount,
    )
    db.commit()
    publish_order_event(restaurant, order, "created")
    return {"order_id": order.public_id, "estimated_time": restaurant.delivery_minutes if payload.type == "delivery" else restaurant.wait_minutes}


@app.post("/reservations")
def create_agent_reservation(
    payload: AgentReservationPayload,
    _: None = Depends(verify_agent_key),
    restaurant_id: str | None = Query(default=None),
    x_restaurant_id: str | None = Header(default=None, alias="X-Restaurant-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requested_id = (restaurant_id or x_restaurant_id or DEFAULT_RESTAURANT_PUBLIC_ID).strip()
    restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")
    if idempotency_key:
        existing = db.scalar(select(Reservation).where(Reservation.idempotency_key == idempotency_key))
        if existing:
            return {"reservation_id": existing.public_id}
    reservation = Reservation(
        public_id=generate_public_reservation_id(db),
        restaurant_id=restaurant.id,
        call_id=payload.call_id,
        customer_name=payload.customer_name,
        customer_phone=validate_phone_or_400(payload.customer_phone),
        reservation_time=payload.reservation_time,
        reservation_time_iso=payload.reservation_time_iso,
        guests_count=payload.guests_count,
        branch=payload.branch,
        notes=payload.notes,
        idempotency_key=idempotency_key,
    )
    db.add(reservation)
    upsert_call_log(
        db,
        restaurant_id=restaurant.id,
        phone=reservation.customer_phone,
        last_message=f"حجز {reservation.guests_count} أفراد",
        ai_response=f"تم تسجيل الحجز {reservation.public_id}",
        status="closed",
        order_total=0.0,
    )
    db.commit()
    return {"reservation_id": reservation.public_id}


@app.post("/complaints")
def create_agent_complaint(
    payload: AgentComplaintPayload,
    _: None = Depends(verify_agent_key),
    restaurant_id: str | None = Query(default=None),
    x_restaurant_id: str | None = Header(default=None, alias="X-Restaurant-ID"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requested_id = (restaurant_id or x_restaurant_id or DEFAULT_RESTAURANT_PUBLIC_ID).strip()
    restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")
    if idempotency_key:
        existing = db.scalar(select(Issue).where(Issue.idempotency_key == idempotency_key))
        if existing:
            return {"complaint_id": existing.id, "status": existing.status}
    issue = Issue(
        restaurant_id=restaurant.id,
        call_id=payload.call_id,
        customer_name=payload.customer_name,
        customer_phone=validate_phone_or_400(payload.customer_phone),
        description=payload.complaint_text,
        complaint_type=payload.complaint_type,
        status="new",
        idempotency_key=idempotency_key,
    )
    db.add(issue)
    upsert_call_log(
        db,
        restaurant_id=restaurant.id,
        phone=issue.customer_phone,
        last_message=payload.complaint_text,
        ai_response="تم تسجيل الشكوى وهيتم المتابعة",
        status="closed",
        order_total=0.0,
    )
    db.commit()
    return {"complaint_id": issue.id, "status": issue.status}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT, reload=False)
