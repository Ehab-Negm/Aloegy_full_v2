import hashlib
import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import jwt
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from livekit import api as livekit_api
from pydantic import BaseModel, ConfigDict
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, cast, create_engine, desc, event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import NullPool


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
DEFAULT_STORAGE_DIR = BACKEND_DIR / "storage"

DATA_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / "agent" / ".env")

logging.basicConfig(
    level=os.getenv("BACKEND_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("aloegy.backend")


def _resolve_backend_runtime_dir() -> Path:
    configured = os.getenv("BACKEND_RUNTIME_DIR")
    candidates = [Path(configured.strip())] if configured and configured.strip() else [
        BACKEND_DIR / ".runtime",
        Path(tempfile.gettempdir()) / "aloegy-backend-runtime",
    ]
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("failed to resolve writable backend runtime directory")


def _resolve_writable_runtime_path(
    *,
    env_name: str,
    default_path: Path,
    runtime_path: Path,
) -> Path:
    configured = os.getenv(env_name)
    if configured is not None and configured.strip():
        return Path(configured.strip())
    candidate_is_writable = (
        os.access(default_path, os.W_OK)
        if default_path.exists()
        else os.access(default_path.parent, os.W_OK)
    )
    if candidate_is_writable:
        return default_path
    logger.warning("%s target is not writable | using runtime path %s", env_name, runtime_path)
    return runtime_path


BACKEND_RUNTIME_DIR = _resolve_backend_runtime_dir()
APP_ENV = os.getenv("APP_ENV", "dev").strip().lower() or "dev"
BACKEND_DB_PATH = _resolve_writable_runtime_path(
    env_name="BACKEND_DB_PATH",
    default_path=DATA_DIR / "app.db",
    runtime_path=BACKEND_RUNTIME_DIR / "app.db",
)
STORAGE_DIR = _resolve_writable_runtime_path(
    env_name="BACKEND_STORAGE_DIR",
    default_path=DEFAULT_STORAGE_DIR,
    runtime_path=BACKEND_RUNTIME_DIR / "storage",
)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_TTL_MINUTES = max(10, int(os.getenv("JWT_TTL_MINUTES", "720")))
OTP_TTL_MINUTES = max(1, int(os.getenv("OTP_TTL_MINUTES", "10")))
DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS", "").strip() or None
DEV_OTP_BYPASS_ENABLED = APP_ENV == "dev" and bool(DEV_OTP_BYPASS)
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "mock_secret_key").strip()

if APP_ENV == "prod":
    if JWT_SECRET == "change-me-in-production":
        raise RuntimeError("FATAL: JWT_SECRET must be set in production")
    if BACKEND_API_KEY == "mock_secret_key":
        raise RuntimeError("FATAL: BACKEND_API_KEY must be set in production")

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
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX).strip() or None
ALLOW_CREDENTIALS = APP_ENV == "prod"
BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
DEFAULT_COLLECTION_LIMIT = max(20, int(os.getenv("DEFAULT_COLLECTION_LIMIT", "200")))

if DEV_OTP_BYPASS_ENABLED:
    logger.warning("DEV_OTP_BYPASS is enabled for APP_ENV=dev")
elif DEV_OTP_BYPASS:
    logger.warning("DEV_OTP_BYPASS is set but ignored outside APP_ENV=dev")
MAX_COLLECTION_LIMIT = max(DEFAULT_COLLECTION_LIMIT, int(os.getenv("MAX_COLLECTION_LIMIT", "500")))
MAX_REQUEST_BODY_BYTES = max(1024 * 1024, int(os.getenv("MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024))))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLALCHEMY_DATABASE_URL = ""
SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)


def _sqlite_database_url() -> str:
    return f"sqlite:///{BACKEND_DB_PATH.as_posix()}"


def _create_database_engine(database_url: str):
    if database_url.startswith("sqlite"):
        sqlite_engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )

        @event.listens_for(sqlite_engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            for statement in (
                "PRAGMA foreign_keys=ON",
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
            ):
                with suppress(Exception):
                    cursor.execute(statement)
            cursor.close()

        return sqlite_engine
    return create_engine(database_url, pool_size=10, max_overflow=20, pool_pre_ping=True)


def _bind_database(database_url: str) -> None:
    global SQLALCHEMY_DATABASE_URL, engine

    SQLALCHEMY_DATABASE_URL = database_url
    engine = _create_database_engine(database_url)
    SessionLocal.configure(bind=engine)

    if database_url.startswith("sqlite"):
        logger.info("database | using SQLite at %s", BACKEND_DB_PATH)
        return
    logger.info("database | using DATABASE_URL (PostgreSQL or external)")


def _fallback_to_sqlite(exc: OperationalError) -> None:
    logger.warning(
        "database | failed to connect using DATABASE_URL in APP_ENV=%s; falling back to SQLite at %s | %s",
        APP_ENV,
        BACKEND_DB_PATH,
        exc.orig or exc,
    )
    _bind_database(_sqlite_database_url())


if DATABASE_URL:
    _bind_database(DATABASE_URL)
else:
    _bind_database(_sqlite_database_url())


class OrderEventSubscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)


class OrderEventBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, list[OrderEventSubscriber]] = {}

    def subscribe(self, restaurant_id: int) -> OrderEventSubscriber:
        subscriber = OrderEventSubscriber(asyncio.get_running_loop())
        with self._lock:
            self._subscribers.setdefault(restaurant_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, restaurant_id: int, subscriber: OrderEventSubscriber) -> None:
        with self._lock:
            subscribers = self._subscribers.get(restaurant_id)
            if not subscribers:
                return
            with suppress(ValueError):
                subscribers.remove(subscriber)
            if not subscribers:
                self._subscribers.pop(restaurant_id, None)

    def publish(self, restaurant_id: int, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(restaurant_id, ()))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(self._publish_nowait, subscriber.queue, event)
            except RuntimeError:
                continue

    @staticmethod
    def _publish_nowait(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        with suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(event)


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
    branches_json: Mapped[str] = mapped_column(Text, default="")
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
    attempts: Mapped[int] = mapped_column(Integer, default=0)
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
    call_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(32), index=True)
    flow: Mapped[str] = mapped_column(String(40), default="")
    transcript_excerpt: Mapped[str] = mapped_column(Text, default="")
    agent_reply_excerpt: Mapped[str] = mapped_column(Text, default="")
    last_message: Mapped[str] = mapped_column(Text, default="")
    ai_response: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="closed")
    order_total: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(40), default="unknown")
    failure_reason: Mapped[str] = mapped_column(String(120), default="")
    close_reason: Mapped[str] = mapped_column(String(80), default="")
    review_status: Mapped[str] = mapped_column(String(20), default="needs_review")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    handoff_target: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    status: Literal["pending", "received", "preparing", "ready", "out_for_delivery", "delivered", "completed", "in_progress", "cancelled"]
    driverPhone: str | None = None


class BranchPayload(BaseModel):
    name: str = ""
    address: str = ""
    deliveryZones: list[str] = []


class RestaurantSettingsPayload(BaseModel):
    name: str
    address: str
    workingHours: str
    contactPhone: str
    branches: list[BranchPayload] = []


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


def _safe_price(value: str | None) -> float:
    try:
        price = float(value or 0)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"invalid price value: {value!r}")
    if price < 0:
        raise HTTPException(status_code=400, detail="price must not be negative")
    return price


def _normalize_order_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    aliases = {
        "completed": "delivered",
        "in_progress": "preparing",
    }
    return aliases.get(normalized, normalized)


class MenuItemPayload(BaseModel):
    name: str = ""
    category: str = "وجبات"
    ingredients: str = ""
    smallPrice: str = "0"
    mediumPrice: str = "0"
    largePrice: str = "0"
    available: bool = True


class BulkMenuItemsPayload(BaseModel):
    items: list[MenuItemPayload]


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
    upsell_accepted: bool = False
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


class AgentCallLogPayload(BaseModel):
    call_id: str
    customer_name: str = ""
    customer_phone: str = ""
    flow: str = ""
    transcript_excerpt: str = ""
    agent_reply_excerpt: str = ""
    last_message: str = ""
    ai_response: str = ""
    status: Literal["active", "closed", "pending"] = "closed"
    order_total: float = 0.0
    outcome: str = "unknown"
    failure_reason: str = ""
    close_reason: str = ""
    review_status: Literal["needs_review", "reviewed", "ignored"] = "needs_review"
    review_notes: str = ""
    handoff_target: str | None = None
    duration_seconds: int = 0
    started_at: str | None = None
    ended_at: str | None = None


class CallReviewPayload(BaseModel):
    review_status: Literal["needs_review", "reviewed", "ignored"]
    failure_reason: str = ""
    review_notes: str = ""
    outcome: str | None = None
    logged_at: str | None = None
    channel: str = "voice_agent"


for _model in (
    PhoneRequest,
    VerifyOtpRequest,
    ContactFormRequest,
    OrderStatusUpdateRequest,
    RestaurantSettingsPayload,
    AgentSettingsPayload,
    SettingsPayload,
    MenuItemPayload,
    EmployeePayload,
    IssueStatusPayload,
    AdminRestaurantCreatePayload,
    SalesMemberPayload,
    SalesRequestPayload,
    SalesRequestStatusPayload,
    DemoSessionPayload,
    DemoLivekitSessionPayload,
    AgentOrderItemPayload,
    AgentOrderPayload,
    AgentReservationPayload,
    AgentComplaintPayload,
):
    _model.model_rebuild()


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
        if not SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
            try:
                db.execute(text("SELECT 1"))
            except OperationalError as exc:
                db.close()
                if APP_ENV == "prod" or not DATABASE_URL:
                    raise
                _fallback_to_sqlite(exc)
                db = SessionLocal()
        try:
            yield db
        except OperationalError as exc:
            db.rollback()
            if APP_ENV != "prod":
                raise HTTPException(status_code=500, detail=f"OperationalError: {exc}") from exc
            raise
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


def extract_access_token(
    *,
    authorization: str | None = None,
    access_token: str | None = None,
) -> str | None:
    if access_token and access_token.strip():
        return access_token.strip()
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


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


def duration_label(value: int) -> str:
    seconds = max(0, int(value))
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{remaining_minutes:02d}:{remaining_seconds:02d}"
    return f"{remaining_minutes:02d}:{remaining_seconds:02d}"


def parse_optional_datetime(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return ensure_utc_datetime(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid datetime value: {value!r}") from exc


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
        "callId": call.call_id or f"CALL-{call.id:04d}",
        "customerName": call.customer_name,
        "phone": call.phone,
        "flow": call.flow,
        "transcriptExcerpt": call.transcript_excerpt,
        "agentReplyExcerpt": call.agent_reply_excerpt,
        "lastMessage": call.last_message or call.transcript_excerpt,
        "aiResponse": call.ai_response or call.agent_reply_excerpt,
        "time": relative_time_label(call.created_at),
        "status": call.status,
        "orderTotal": currency_text(call.order_total),
        "outcome": call.outcome,
        "failureReason": call.failure_reason,
        "closeReason": call.close_reason,
        "reviewStatus": call.review_status,
        "reviewNotes": call.review_notes,
        "handoffTarget": call.handoff_target,
        "durationSeconds": call.duration_seconds,
        "duration": duration_label(call.duration_seconds),
        "startedAt": call.started_at.isoformat() if call.started_at else None,
        "endedAt": call.ended_at.isoformat() if call.ended_at else None,
        "createdAt": call.created_at.isoformat(),
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


def serialize_file(file_asset: FileAsset, request: Request, *, access_token: str | None = None) -> dict[str, Any]:
    preview_url = str(request.url_for("download_file", file_id=str(file_asset.id)))
    if access_token:
        preview_url = str(request.url_for("download_file", file_id=str(file_asset.id)).include_query_params(token=access_token, inline="1"))
    else:
        preview_url = str(request.url_for("download_file", file_id=str(file_asset.id)).include_query_params(inline="1"))
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


def _default_branch_record(restaurant: Restaurant) -> dict[str, Any]:
    default_name = (restaurant.location or restaurant.name or "الفرع الرئيسي").strip()
    default_address = (restaurant.address or "").strip()
    delivery_zones = [restaurant.location.strip()] if restaurant.location.strip() else []
    return {
        "name": default_name,
        "address": default_address,
        "deliveryZones": delivery_zones,
    }


def _normalize_branch_records(branches: list[dict[str, Any]], restaurant: Restaurant) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in branches:
        name = str(raw.get("name", "")).strip()
        address = str(raw.get("address", "")).strip()
        delivery_zones = [
            str(zone).strip()
            for zone in raw.get("deliveryZones", [])
            if str(zone).strip()
        ]
        if not name and not address and not delivery_zones:
            continue
        if not name:
            name = address or restaurant.location or restaurant.name or "الفرع الرئيسي"
        normalized.append(
            {
                "name": name,
                "address": address,
                "deliveryZones": delivery_zones,
            }
        )
    return normalized or [_default_branch_record(restaurant)]


def _parse_branches_config(restaurant: Restaurant) -> list[dict[str, Any]]:
    raw = (restaurant.branches_json or "").strip()
    if not raw:
        return [_default_branch_record(restaurant)]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [_default_branch_record(restaurant)]
    if not isinstance(parsed, list):
        return [_default_branch_record(restaurant)]
    branch_records = [item for item in parsed if isinstance(item, dict)]
    return _normalize_branch_records(branch_records, restaurant)


def _aggregate_delivery_zones(branches: list[dict[str, Any]], restaurant: Restaurant) -> list[str]:
    zones: list[str] = []
    seen: set[str] = set()
    for branch in branches:
        for zone in branch.get("deliveryZones", []):
            normalized = str(zone).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                zones.append(normalized)
    if zones:
        return zones
    fallback_zone = (restaurant.location or "").strip()
    return [fallback_zone] if fallback_zone else []


def _payload_branches_to_records(branches: list[BranchPayload], restaurant: Restaurant) -> list[dict[str, Any]]:
    return _normalize_branch_records(
        [
            {
                "name": branch.name,
                "address": branch.address,
                "deliveryZones": branch.deliveryZones,
            }
            for branch in branches
        ],
        restaurant,
    )


def restaurant_settings_payload(restaurant: Restaurant) -> dict[str, Any]:
    branches = _parse_branches_config(restaurant)
    return {
        "restaurant": {
            "name": restaurant.name,
            "address": restaurant.address,
            "workingHours": restaurant.working_hours,
            "contactPhone": restaurant.contact_phone,
            "branches": branches,
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
    call_id: str | None = None,
    customer_name: str = "",
    phone: str,
    flow: str = "",
    transcript_excerpt: str = "",
    agent_reply_excerpt: str = "",
    last_message: str,
    ai_response: str,
    status: str,
    order_total: float,
    outcome: str = "unknown",
    failure_reason: str = "",
    close_reason: str = "",
    review_status: str = "needs_review",
    review_notes: str = "",
    handoff_target: str | None = None,
    duration_seconds: int = 0,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> CallLog:
    existing: CallLog | None = None
    if call_id:
        existing = db.scalar(
            select(CallLog)
            .where(CallLog.restaurant_id == restaurant_id, CallLog.call_id == call_id)
            .order_by(desc(CallLog.created_at))
        )

    if existing is None:
        existing = CallLog(
            restaurant_id=restaurant_id,
            call_id=call_id,
            customer_name=customer_name,
            phone=phone,
            flow=flow,
            transcript_excerpt=transcript_excerpt,
            agent_reply_excerpt=agent_reply_excerpt,
            last_message=last_message,
            ai_response=ai_response,
            status=status,
            order_total=order_total,
            outcome=outcome,
            failure_reason=failure_reason,
            close_reason=close_reason,
            review_status=review_status,
            review_notes=review_notes,
            handoff_target=handoff_target,
            duration_seconds=max(0, int(duration_seconds)),
            started_at=started_at,
            ended_at=ended_at,
        )
        db.add(existing)
        return existing

    existing.customer_name = customer_name or existing.customer_name
    existing.phone = phone or existing.phone
    existing.flow = flow or existing.flow
    existing.transcript_excerpt = transcript_excerpt or existing.transcript_excerpt
    existing.agent_reply_excerpt = agent_reply_excerpt or existing.agent_reply_excerpt
    if last_message and not existing.last_message:
        existing.last_message = last_message
    if ai_response and not existing.ai_response:
        existing.ai_response = ai_response
    existing.status = status or existing.status
    existing.order_total = max(float(existing.order_total or 0.0), float(order_total or 0.0))
    existing.outcome = outcome or existing.outcome
    existing.failure_reason = failure_reason or existing.failure_reason
    existing.close_reason = close_reason or existing.close_reason
    existing.review_status = review_status or existing.review_status
    existing.review_notes = review_notes or existing.review_notes
    existing.handoff_target = handoff_target or existing.handoff_target
    existing.duration_seconds = max(int(existing.duration_seconds or 0), int(duration_seconds or 0))
    existing.started_at = started_at or existing.started_at
    existing.ended_at = ended_at or existing.ended_at
    return existing


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


UPSELL_CATEGORIES = {"مشروبات", "إضافات", "حلويات", "drinks", "extras", "desserts", "sides", "سلطات"}

def _build_upsell_rules(menu_items: list[MenuItem]) -> list[dict]:
    available = [i for i in menu_items if i.available]
    main_items = [i for i in available if (i.category or "").strip().lower() not in UPSELL_CATEGORIES]
    side_items = [i for i in available if (i.category or "").strip().lower() in UPSELL_CATEGORIES]
    if not main_items or not side_items:
        return []
    rules = []
    for side in side_items[:5]:
        rules.append({
            "category": (side.category or "إضافات").strip(),
            "item": side.name,
            "price": best_menu_price(side),
            "suggestion": f"تحب تضيف {side.name}؟",
        })
    return rules


def restaurant_config_payload(restaurant: Restaurant, menu_items: list[MenuItem]) -> dict[str, Any]:
    branches = _parse_branches_config(restaurant)
    return {
        "id": restaurant.public_id,
        "name": restaurant.name,
        "phone": restaurant.contact_phone or restaurant.owner_phone,
        "address": restaurant.address,
        "branches": [
            {
                "name": branch.get("name", ""),
                "address": branch.get("address", ""),
            }
            for branch in branches
        ],
        "hours": _parse_working_hours(restaurant.working_hours),
        "menu_items": [
            {"name": item.name, "price": best_menu_price(item), "available": item.available}
            for item in menu_items
        ],
        "upsell_rules": _build_upsell_rules(menu_items),
        "is_open": restaurant.is_open,
        "closed_reason": restaurant.closed_reason,
        "wait_minutes": restaurant.wait_minutes,
        "min_guests": restaurant.min_guests,
        "max_guests": restaurant.max_guests,
        "delivery_enabled": restaurant.delivery_enabled,
        "delivery_minutes": restaurant.delivery_minutes,
        "delivery_fee": restaurant.delivery_fee,
        "min_order": restaurant.min_order,
        "delivery_zones": _aggregate_delivery_zones(branches, restaurant),
    }


def _effective_collection_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_COLLECTION_LIMIT
    return max(1, min(limit, MAX_COLLECTION_LIMIT))


def _effective_collection_skip(skip: int | None) -> int:
    if skip is None:
        return 0
    return max(0, skip)


def compute_customer_profiles(
    db: Session,
    restaurant_id: int,
    *,
    skip: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    effective_limit = _effective_collection_limit(limit)
    effective_skip = _effective_collection_skip(skip)
    rows = db.execute(
        select(
            Order.phone.label("phone"),
            func.count(Order.id).label("total_orders"),
            func.coalesce(func.sum(Order.amount), 0.0).label("total_spent"),
            func.max(Order.created_at).label("last_order_at"),
        )
        .where(Order.restaurant_id == restaurant_id)
        .group_by(Order.phone)
        .order_by(desc("last_order_at"))
        .offset(effective_skip)
        .limit(effective_limit)
    ).all()

    profiles = []
    for row in rows:
        total_spent = float(row.total_spent)
        total_orders = int(row.total_orders)
        avg_order = total_spent / total_orders if total_orders else 0.0
        profiles.append(
            {
                "phone": row.phone,
                "totalOrders": total_orders,
                "totalSpent": currency_text(total_spent),
                "lastOrder": relative_time_label(row.last_order_at),
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


SUCCESSFUL_CALL_OUTCOMES = {"order_confirmed", "reservation_confirmed", "complaint_logged", "handoff"}
PROCESSED_REVIEW_STATUSES = {"reviewed", "ignored"}


def _percent_text(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0%"
    return f"{round((numerator / denominator) * 100):d}%"


def compute_quality_analytics(db: Session, restaurant_id: int) -> dict[str, Any]:
    total_calls = int(
        db.scalar(select(func.count(CallLog.id)).where(CallLog.restaurant_id == restaurant_id)) or 0
    )
    successful_calls = int(
        db.scalar(
            select(func.count(CallLog.id)).where(
                CallLog.restaurant_id == restaurant_id,
                CallLog.outcome.in_(SUCCESSFUL_CALL_OUTCOMES),
            )
        ) or 0
    )
    reviewed_calls = int(
        db.scalar(
            select(func.count(CallLog.id)).where(
                CallLog.restaurant_id == restaurant_id,
                CallLog.review_status.in_(PROCESSED_REVIEW_STATUSES),
            )
        ) or 0
    )
    needs_review = int(
        db.scalar(
            select(func.count(CallLog.id)).where(
                CallLog.restaurant_id == restaurant_id,
                CallLog.review_status == "needs_review",
            )
        ) or 0
    )

    outcome_rows = db.execute(
        select(
            CallLog.outcome.label("name"),
            func.count(CallLog.id).label("value"),
        )
        .where(CallLog.restaurant_id == restaurant_id)
        .group_by(CallLog.outcome)
        .order_by(desc("value"), "name")
    ).all()
    outcomes = [
        {"name": str(row.name or "unknown"), "value": int(row.value)}
        for row in outcome_rows
        if int(row.value or 0) > 0
    ]

    failure_rows = db.execute(
        select(
            CallLog.failure_reason.label("reason"),
            func.count(CallLog.id).label("count"),
        )
        .where(
            CallLog.restaurant_id == restaurant_id,
            CallLog.failure_reason != "",
        )
        .group_by(CallLog.failure_reason)
        .order_by(desc("count"), "reason")
    ).all()
    failures = [
        {"name": str(row.reason), "value": int(row.count)}
        for row in failure_rows
        if row.reason
    ]

    review_rows = db.execute(
        select(
            CallLog.review_status.label("name"),
            func.count(CallLog.id).label("value"),
        )
        .where(CallLog.restaurant_id == restaurant_id)
        .group_by(CallLog.review_status)
        .order_by(desc("value"), "name")
    ).all()
    review_statuses = [
        {"name": str(row.name or "needs_review"), "value": int(row.value)}
        for row in review_rows
        if int(row.value or 0) > 0
    ]

    return {
        "summary": {
            "totalCalls": total_calls,
            "successfulCalls": successful_calls,
            "reviewedCalls": reviewed_calls,
            "needsReview": needs_review,
            "successRate": _percent_text(successful_calls, total_calls),
            "reviewCoverage": _percent_text(reviewed_calls, total_calls),
        },
        "outcomes": outcomes,
        "failures": failures,
        "reviewStatuses": review_statuses,
        "topBlockers": [
            {"reason": item["name"], "count": item["value"]}
            for item in failures[:5]
        ],
    }


def compute_analytics(db: Session, restaurant_id: int) -> dict[str, Any]:
    # Summary: single SQL aggregation instead of loading all orders.
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

    fourteen_days_ago = utc_now() - timedelta(days=14)
    dialect_name = db.get_bind().dialect.name
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    if dialect_name == "sqlite":
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
        daily_orders = [
            {"day": day_labels[int(row.dow)] if row.dow is not None else "?", "orders": int(row.cnt), "revenue": float(row.rev)}
            for row in daily_rows
        ]

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
    else:
        daily_rows = db.execute(
            select(
                cast(func.extract("dow", Order.created_at), Integer).label("dow"),
                func.count(Order.id).label("cnt"),
                func.coalesce(func.sum(Order.amount), 0.0).label("rev"),
            )
            .where(Order.restaurant_id == restaurant_id, Order.created_at >= fourteen_days_ago)
            .group_by("dow")
            .order_by("dow")
        ).all()
        daily_orders = [
            {"day": day_labels[int(row.dow)] if row.dow is not None else "?", "orders": int(row.cnt), "revenue": float(row.rev)}
            for row in daily_rows
        ]

        monthly_rows = db.execute(
            select(
                cast(func.extract("year", Order.created_at), Integer).label("year"),
                cast(func.extract("month", Order.created_at), Integer).label("month_num"),
                func.coalesce(func.sum(Order.amount), 0.0).label("rev"),
            )
            .where(Order.restaurant_id == restaurant_id)
            .group_by("year", "month_num")
            .order_by("year", "month_num")
        ).all()
        monthly_revenue = [
            {"month": f"{int(row.year):04d}-{int(row.month_num):02d}", "revenue": float(row.rev)}
            for row in monthly_rows
        ][-6:]

    # Category breakdown: only loads order_items (lighter than full orders).
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
        "quality": compute_quality_analytics(db, restaurant_id),
    }
def _provisional_public_id(prefix: str) -> str:
    return f"TMP-{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _format_public_id(prefix: str, numeric_id: int, created_at: datetime | None = None) -> str:
    year = ensure_utc_datetime(created_at or utc_now()).year
    return f"{prefix}-{year}-{numeric_id:05d}"


def _assign_order_public_id(order: Order) -> None:
    if order.id is None:
        raise ValueError("order id must exist before assigning public_id")
    order.public_id = _format_public_id("ORD", order.id, order.created_at)


def _assign_reservation_public_id(reservation: Reservation) -> None:
    if reservation.id is None:
        raise ValueError("reservation id must exist before assigning public_id")
    reservation.public_id = _format_public_id("RES", reservation.id, reservation.created_at)


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
    MAX_READ = MAX_UPLOAD_SIZE_BYTES + 1
    data = upload.file.read(MAX_READ)
    if len(data) >= MAX_READ:
        raise HTTPException(status_code=413, detail="File too large")
    target.write_bytes(data)
    return str(target), len(data)


def _resolve_asset_path(asset: FileAsset) -> Path:
    try:
        resolved = Path(asset.stored_path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc

    storage_root = STORAGE_DIR.resolve()
    if resolved != storage_root and storage_root not in resolved.parents:
        raise HTTPException(status_code=400, detail="invalid file path")
    return resolved


def _build_file_access_url(
    request: Request,
    *,
    file_id: int,
    token: str | None,
    inline: bool = True,
) -> str:
    url = request.url_for("download_file", file_id=str(file_id))
    query_params: dict[str, str] = {}
    if inline:
        query_params["inline"] = "1"
    if token:
        query_params["token"] = token
    if query_params:
        url = url.include_query_params(**query_params)
    return str(url)


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
            call_id = uuid.uuid4().hex[:8]
            order = Order(
                public_id=f"ORD-{created_at.year}-{uuid.uuid4().hex[:5].upper()}",
                restaurant_id=restaurant.id,
                call_id=call_id,
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
                call_id=call_id,
                customer_name=customer_name,
                phone=normalize_phone(phone),
                flow=order.type,
                transcript_excerpt=order.items_summary,
                agent_reply_excerpt=f"تم تسجيل الطلب {order.public_id}",
                last_message=order.items_summary,
                ai_response=f"تم تسجيل الطلب {order.public_id}",
                status="active" if status_value in {"received", "preparing"} else "closed",
                order_total=order.amount,
                outcome="order_confirmed",
                review_status="reviewed",
                duration_seconds=75,
                started_at=created_at,
                ended_at=created_at + timedelta(seconds=75),
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

    migrations: list[tuple[str, str, str]] = [
        ("reservations", "reservation_time_iso", "VARCHAR(64)"),
        ("otp_codes", "attempts", "INTEGER DEFAULT 0"),
        ("restaurants", "branches_json", "TEXT DEFAULT ''"),
        ("call_logs", "call_id", "VARCHAR(40)"),
        ("call_logs", "customer_name", "VARCHAR(120) DEFAULT ''"),
        ("call_logs", "flow", "VARCHAR(40) DEFAULT ''"),
        ("call_logs", "transcript_excerpt", "TEXT DEFAULT ''"),
        ("call_logs", "agent_reply_excerpt", "TEXT DEFAULT ''"),
        ("call_logs", "outcome", "VARCHAR(40) DEFAULT 'unknown'"),
        ("call_logs", "failure_reason", "VARCHAR(120) DEFAULT ''"),
        ("call_logs", "close_reason", "VARCHAR(80) DEFAULT ''"),
        ("call_logs", "review_status", "VARCHAR(20) DEFAULT 'needs_review'"),
        ("call_logs", "review_notes", "TEXT DEFAULT ''"),
        ("call_logs", "handoff_target", "VARCHAR(80)"),
        ("call_logs", "duration_seconds", "INTEGER DEFAULT 0"),
        ("call_logs", "started_at", "DATETIME"),
        ("call_logs", "ended_at", "DATETIME"),
    ]
    existing_by_table: dict[str, set[str]] = {}
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            if table not in existing_by_table:
                inspector = sa.inspect(conn)
                existing_by_table[table] = {c["name"] for c in inspector.get_columns(table)}
            existing = existing_by_table[table]
            if column not in existing:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                existing.add(column)
                logger.info("migration | added column %s.%s", table, column)
        conn.commit()


class LimitBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_body_bytes: int) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = None
            if size is not None and size > self.max_body_bytes:
                return Response(
                    status_code=413,
                    content=json.dumps({"detail": "request body too large"}),
                    media_type="application/json",
                )
        return await call_next(request)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        _run_migrations()
    except OperationalError as exc:
        if APP_ENV == "prod" or not DATABASE_URL:
            raise
        _fallback_to_sqlite(exc)
        Base.metadata.create_all(bind=engine)
        _run_migrations()
    seed_database()
    yield


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Aloegy Backend", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda req, exc: Response(
    status_code=429,
    content=json.dumps({"detail": "Too many requests. Try again later."}),
    media_type="application/json",
))
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LimitBodySizeMiddleware, max_body_bytes=MAX_REQUEST_BODY_BYTES)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "env": APP_ENV}


@app.post("/contact")
@limiter.limit("5/minute")
def submit_contact_form(
    request: Request,
    payload: ContactFormRequest = Body(...),
    db: Session = Depends(get_db),
) -> bool:
    db.add(ContactLead(restaurant_name=payload.restaurantName, phone=validate_phone_or_400(payload.phone), message=payload.message))
    db.commit()
    return True


@app.post("/auth/send-otp")
@limiter.limit("10/minute")
def send_otp(
    request: Request,
    payload: PhoneRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    phone = validate_phone_or_400(payload.phone)
    user_exists = db.scalar(select(User).where(User.phone == phone))
    if not user_exists:
        raise HTTPException(status_code=403, detail="هذا الرقم غير مسجل في النظام. تواصل مع الإدارة.")
    code = f"{secrets.randbelow(1000000):06d}"
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
@limiter.limit("10/minute")
def verify_otp(
    request: Request,
    payload: VerifyOtpRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    phone = validate_phone_or_400(payload.phone)
    otp = payload.otp.strip()

    otp_row = db.scalar(
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.consumed_at.is_(None))
        .order_by(desc(OtpCode.created_at))
    )
    otp_is_valid = False

    # Check attempt count — max 5 failed attempts per OTP
    if otp_row and getattr(otp_row, "attempts", 0) >= 5:
        otp_row.consumed_at = utc_now()
        db.commit()
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new OTP.")

    if otp_row and ensure_utc_datetime(otp_row.expires_at) >= utc_now() and otp_row.code_hash == hash_otp(phone, otp):
        otp_is_valid = True
        otp_row.consumed_at = utc_now()
    elif DEV_OTP_BYPASS_ENABLED and otp == DEV_OTP_BYPASS:
        otp_is_valid = True

    if not otp_is_valid:
        if otp_row:
            otp_row.attempts = getattr(otp_row, "attempts", 0) + 1
            db.commit()
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
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_COLLECTION_LIMIT, ge=1, le=MAX_COLLECTION_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    calls = db.scalars(
        select(CallLog)
        .where(CallLog.restaurant_id == restaurant.id)
        .order_by(desc(CallLog.created_at))
        .offset(_effective_collection_skip(skip))
        .limit(_effective_collection_limit(limit))
    ).all()
    return [serialize_call(call) for call in calls]


@app.patch("/calls/{call_log_id}/review")
def update_call_review(
    call_log_id: int,
    payload: CallReviewPayload,
    user: CurrentUser = Depends(require_roles("admin", "owner", "employee")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    call = db.get(CallLog, call_log_id)
    if not call:
        raise HTTPException(status_code=404, detail="call not found")
    if user.role != "admin" and call.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="forbidden")
    call.review_status = payload.review_status
    call.failure_reason = payload.failure_reason.strip()
    call.review_notes = payload.review_notes.strip()
    if payload.outcome is not None and payload.outcome.strip():
        call.outcome = payload.outcome.strip()
    db.commit()
    db.refresh(call)
    return serialize_call(call)


@app.get("/users")
def fetch_users(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_COLLECTION_LIMIT, ge=1, le=MAX_COLLECTION_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    return compute_customer_profiles(db, restaurant.id, skip=skip, limit=limit)


@app.get("/orders")
def fetch_orders(
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_COLLECTION_LIMIT, ge=1, le=MAX_COLLECTION_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    orders = db.scalars(
        select(Order)
        .where(Order.restaurant_id == restaurant.id)
        .order_by(desc(Order.created_at))
        .offset(_effective_collection_skip(skip))
        .limit(_effective_collection_limit(limit))
    ).all()
    return [serialize_order(order) for order in orders]


@app.get("/orders/stream")
async def stream_orders(
    token: str | None = Query(default=None),
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    user = authenticate_current_user(db, access_token=token)
    if user.role not in {"admin", "owner", "employee"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    subscriber = order_event_broker.subscribe(restaurant.id)

    async def event_stream():
        try:
            yield format_sse_message("ready", {"restaurantId": restaurant.id})
            while True:
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=15)
                except asyncio.TimeoutError:
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
    order.status = _normalize_order_status(payload.status)
    order.driver_phone = normalize_phone(payload.driverPhone) if payload.driverPhone else None
    db.commit()
    db.refresh(order)
    publish_order_event(restaurant_or_404(db, order.restaurant_id), order, "updated")
    return serialize_order(order)


@app.get("/files")
def fetch_files(
    request: Request,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_COLLECTION_LIMIT, ge=1, le=MAX_COLLECTION_LIMIT),
    authorization: str | None = Header(default=None, alias="Authorization"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    access_token = extract_access_token(authorization=authorization)
    files = db.scalars(
        select(FileAsset)
        .where(FileAsset.restaurant_id == restaurant.id)
        .order_by(desc(FileAsset.created_at))
        .offset(_effective_collection_skip(skip))
        .limit(_effective_collection_limit(limit))
    ).all()
    return [serialize_file(file_asset, request, access_token=access_token) for file_asset in files]


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
    inline: bool = Query(default=False),
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> FileResponse:
    user = authenticate_current_user(db, authorization=authorization, access_token=token)
    asset = db.get(FileAsset, file_id)
    if not asset:
        raise HTTPException(status_code=404, detail="file not found")
    if user.role != "admin" and asset.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="forbidden")
    file_path = _resolve_asset_path(asset)
    response = FileResponse(file_path, filename=asset.name)
    if inline:
        safe_name = asset.name.replace('"', "")
        response.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
    return response


@app.get("/files/{file_id}/preview")
def preview_file(
    file_id: int,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = authenticate_current_user(db, authorization=authorization, access_token=token)
    asset = db.get(FileAsset, file_id)
    if not asset:
        raise HTTPException(status_code=404, detail="file not found")
    if user.role != "admin" and asset.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="forbidden")
    _resolve_asset_path(asset)
    access_token = extract_access_token(authorization=authorization, access_token=token)
    return {"url": _build_file_access_url(request, file_id=file_id, token=access_token, inline=True)}


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
    branch_records = _payload_branches_to_records(payload.restaurant.branches, restaurant)
    if not payload.restaurant.branches:
        branch_records = [
            {
                "name": (restaurant.location or payload.restaurant.name or "الفرع الرئيسي").strip(),
                "address": payload.restaurant.address.strip(),
                "deliveryZones": [restaurant.location.strip()] if restaurant.location.strip() else [],
            }
        ]
    primary_branch = branch_records[0]
    restaurant.name = payload.restaurant.name
    restaurant.address = primary_branch.get("address", "").strip() or payload.restaurant.address
    restaurant.location = primary_branch.get("name", "").strip() or restaurant.location
    restaurant.branches_json = json.dumps(branch_records, ensure_ascii=False)
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
        small_price=_safe_price(payload.smallPrice),
        medium_price=_safe_price(payload.mediumPrice),
        large_price=_safe_price(payload.largePrice),
        available=payload.available,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize_menu_item(item)


@app.post("/menu-items/bulk")
def create_menu_items_bulk(
    payload: BulkMenuItemsPayload,
    restaurant_id: int | None = Query(default=None, alias="restaurantId"),
    user: CurrentUser = Depends(require_roles("admin", "owner")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    items_payload = payload.items[:100]
    if not items_payload:
        raise HTTPException(status_code=400, detail="at least one menu item is required")

    created: list[MenuItem] = []
    for raw_item in items_payload:
        item = MenuItem(
            restaurant_id=restaurant.id,
            name=raw_item.name.strip(),
            category=raw_item.category.strip() or "وجبات",
            ingredients=raw_item.ingredients.strip(),
            small_price=_safe_price(raw_item.smallPrice),
            medium_price=_safe_price(raw_item.mediumPrice),
            large_price=_safe_price(raw_item.largePrice),
            available=raw_item.available,
        )
        db.add(item)
        created.append(item)

    db.commit()
    for item in created:
        db.refresh(item)
    return [serialize_menu_item(item) for item in created]


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
    item.small_price = _safe_price(payload.smallPrice)
    item.medium_price = _safe_price(payload.mediumPrice)
    item.large_price = _safe_price(payload.largePrice)
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
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_COLLECTION_LIMIT, ge=1, le=MAX_COLLECTION_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    restaurant = resolve_restaurant_scope(db, user, restaurant_id)
    issues = db.scalars(
        select(Issue)
        .where(Issue.restaurant_id == restaurant.id)
        .order_by(desc(Issue.created_at))
        .offset(_effective_collection_skip(skip))
        .limit(_effective_collection_limit(limit))
    ).all()
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
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_COLLECTION_LIMIT, ge=1, le=MAX_COLLECTION_LIMIT),
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
        .offset(_effective_collection_skip(skip))
        .limit(_effective_collection_limit(limit))
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
    return fetch_admin_restaurants(skip=0, limit=DEFAULT_COLLECTION_LIMIT, user=user, db=db)[-1]


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


@app.post("/calls/upsert")
def upsert_agent_call_log(
    payload: AgentCallLogPayload,
    _: None = Depends(verify_agent_key),
    restaurant_id: str | None = Query(default=None),
    x_restaurant_id: str | None = Header(default=None, alias="X-Restaurant-ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requested_id = (restaurant_id or x_restaurant_id or DEFAULT_RESTAURANT_PUBLIC_ID).strip()
    restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="restaurant not found")
    started_at = parse_optional_datetime(payload.started_at)
    ended_at = parse_optional_datetime(payload.ended_at)
    call = upsert_call_log(
        db,
        restaurant_id=restaurant.id,
        call_id=payload.call_id,
        customer_name=payload.customer_name.strip(),
        phone=normalize_phone(payload.customer_phone) if payload.customer_phone.strip() else "",
        flow=payload.flow.strip(),
        transcript_excerpt=payload.transcript_excerpt.strip(),
        agent_reply_excerpt=payload.agent_reply_excerpt.strip(),
        last_message=payload.last_message.strip(),
        ai_response=payload.ai_response.strip(),
        status=payload.status,
        order_total=max(0.0, float(payload.order_total or 0.0)),
        outcome=payload.outcome.strip() or "unknown",
        failure_reason=payload.failure_reason.strip(),
        close_reason=payload.close_reason.strip(),
        review_status=payload.review_status,
        review_notes=payload.review_notes.strip(),
        handoff_target=(payload.handoff_target or "").strip() or None,
        duration_seconds=max(0, int(payload.duration_seconds)),
        started_at=started_at,
        ended_at=ended_at,
    )
    db.commit()
    db.refresh(call)
    return {
        "id": call.id,
        "call_id": call.call_id or payload.call_id,
        "status": call.status,
        "outcome": call.outcome,
    }


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
    try:
        restaurant = db.scalar(select(Restaurant).where(Restaurant.public_id == requested_id))
        if not restaurant:
            raise HTTPException(status_code=404, detail="restaurant not found")
        if idempotency_key:
            existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
            if existing:
                return {"order_id": existing.public_id, "estimated_time": restaurant.delivery_minutes if existing.type == "delivery" else restaurant.wait_minutes}

        order = Order(
            public_id=_provisional_public_id("ORD"),
            restaurant_id=restaurant.id,
            call_id=payload.call_id,
            type=payload.type,
            customer_name=payload.customer_name,
            phone=validate_phone_or_400(payload.customer_phone),
            items_summary=" + ".join(f"{item.qty} {item.name}" for item in payload.order_items),
            amount=sum(item.qty * item.price for item in payload.order_items),
            status="received",
            upsell=payload.upsell_accepted,
            source=payload.channel,
            special_requests=payload.special_requests,
            delivery_address=payload.delivery_address,
            delivery_zone=payload.delivery_zone,
            delivery_landmark=payload.delivery_landmark,
            idempotency_key=idempotency_key,
        )
        db.add(order)
        db.flush()
        _assign_order_public_id(order)
        for item in payload.order_items:
            db.add(OrderItem(order_id=order.id, name=item.name, qty=item.qty, price=item.price))
        upsert_call_log(
            db,
            restaurant_id=restaurant.id,
            call_id=payload.call_id,
            customer_name=payload.customer_name,
            phone=order.phone,
            flow=payload.type,
            last_message=order.items_summary,
            ai_response=f"تم تسجيل الطلب {order.public_id}",
            status="active",
            order_total=order.amount,
            outcome="order_confirmed",
            review_status="reviewed",
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if idempotency_key:
                existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
                if existing:
                    return {"order_id": existing.public_id, "estimated_time": restaurant.delivery_minutes if existing.type == "delivery" else restaurant.wait_minutes}
            raise HTTPException(status_code=409, detail="order conflict, please retry") from exc
        publish_order_event(restaurant, order, "created")
        return {"order_id": order.public_id, "estimated_time": restaurant.delivery_minutes if payload.type == "delivery" else restaurant.wait_minutes}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent order create failed | restaurant=%s | call_id=%s", requested_id, payload.call_id)
        if APP_ENV != "prod":
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        raise


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
        public_id=_provisional_public_id("RES"),
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
    db.flush()
    _assign_reservation_public_id(reservation)
    upsert_call_log(
        db,
        restaurant_id=restaurant.id,
        call_id=payload.call_id,
        customer_name=payload.customer_name,
        phone=reservation.customer_phone,
        flow="reservation",
        last_message=f"حجز {reservation.guests_count} أفراد",
        ai_response=f"تم تسجيل الحجز {reservation.public_id}",
        status="active",
        order_total=0.0,
        outcome="reservation_confirmed",
        review_status="reviewed",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(select(Reservation).where(Reservation.idempotency_key == idempotency_key))
            if existing:
                return {"reservation_id": existing.public_id}
        raise HTTPException(status_code=409, detail="reservation conflict, please retry") from exc
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
        call_id=payload.call_id,
        customer_name=payload.customer_name,
        phone=issue.customer_phone,
        flow="complaint",
        last_message=payload.complaint_text,
        ai_response="تم تسجيل الشكوى وهيتم المتابعة",
        status="active",
        order_total=0.0,
        outcome="complaint_logged",
        review_status="reviewed",
    )
    db.commit()
    return {"complaint_id": issue.id, "status": issue.status}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT, reload=False)
