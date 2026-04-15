from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    # Uses UUID to prevent ID Enumeration attacks
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # User role: red_team, blue_team, admin
    role = Column(String, nullable=False)

    # Links the user to a specific isolated lab environment
    lab_instance_id = Column(UUID(as_uuid=True), nullable=False)


class GameSession(Base):
    """
    State of the game and Blue Team economy.
    """
    __tablename__ = "game_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    lab_instance_id = Column(UUID(as_uuid=True), nullable=False)

    # Starting budget for the Blue Team
    defense_budget = Column(Integer, default=5)

    system_prompt = Column(Text, default="You are a helpful university assistant.")
    use_reranker = Column(Boolean, default=False)
    rate_limit_enabled = Column(Boolean, default=False)

    rate_limit_rpm = Column(Integer, default=60)


class ChatHistory(Base):
    """
    Chat logs.
    NOTE: Although we define it safely here via ORM, the search endpoint
    (Sprint 2) will deliberately use raw SQL string concatenation on this
    table to create an SQL Injection vulnerability.
    """
    __tablename__ = "chat_history"

    # Here we use sequential Integer because chat messages don't need anti-enumeration protection
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    user_message = Column(Text, nullable=False)
    bot_response = Column(Text, nullable=False)
    lab_instance_id = Column(UUID(as_uuid=True), nullable=False)


class SecurityAuditLog(Base):
    """
    Forensics and SIEM Logs for the Blue Team.
    """
    __tablename__ = "security_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lab_instance_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String, nullable=False)

    # JSONB for querying structured metadata (IPs, vector distances)
    payload = Column(JSONB, nullable=False)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Set to True by heuristics if the query was a successful attack
    is_compromised = Column(Boolean, default=False)

    investigated_at = Column(DateTime(timezone=True), nullable=True)
    investigated_by = Column(UUID(as_uuid=True), nullable=True)


class CtfFlag(Base):
    """
    Dictionary of hidden secrets/flags to be stolen by the Red Team.
    """
    __tablename__ = "ctf_flags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    flag_value = Column(String, nullable=False) # e.g., hashed "Vizja_Sec..."
    reward = Column(Integer, nullable=False)
    lab_id = Column(String, nullable=False)


class CtfSubmission(Base):
    """
    Tracks successfully captured flags by the Red Team for auto-grading.
    """
    __tablename__ = "ctf_submissions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    flag_id = Column(Integer, ForeignKey("ctf_flags.id"), nullable=False)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    lab_instance_id = Column(UUID(as_uuid=True), nullable=False)