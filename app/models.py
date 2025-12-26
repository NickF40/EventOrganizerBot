import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class UserStatus(str, enum.Enum):
    NONE = "NONE"
    PROCESSING = "PROCESSING"
    ATTENDEE = "ATTENDEE"
    WAITLIST = "WAITLIST"


class AdminStateType(str, enum.Enum):
    WELCOME = "WELCOME"
    SCHEDULE = "SCHEDULE"
    BROADCAST_ALL = "BROADCAST_ALL"
    BROADCAST_ATTENDEE = "BROADCAST_ATTENDEE"
    UPLOAD_DB = "UPLOAD_DB"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    job = Column(String(255), nullable=True)
    career_path = Column(String(255), nullable=True)
    status = Column(Enum(UserStatus), default=UserStatus.NONE, nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    feedback = relationship("Feedback", back_populates="user", cascade="all, delete-orphan")


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    feedback_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="feedback")


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    name = Column(String(255), primary_key=True)
    admin_chat_id = Column(BigInteger, nullable=False)
    message_id = Column(Integer, nullable=False)


class AdminState(Base):
    __tablename__ = "admin_state"

    id = Column(Integer, primary_key=True)
    admin_id = Column(BigInteger, nullable=False, index=True)
    waiting_for = Column(Enum(AdminStateType), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ttl_seconds = Column(Integer, default=300, nullable=False)


class EventState(Base):
    __tablename__ = "event_state"

    id = Column(Integer, primary_key=True)
    event_started = Column(Boolean, default=False, nullable=False)
    current_event_id = Column(String(255), nullable=True)


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)
