"""
Database models and connection setup for Reel Generator.
Uses SQLAlchemy ORM with PostgreSQL.
"""

from sqlalchemy import create_engine, Column, String, Integer, Float, ForeignKey, DateTime, Text, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
import uuid
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

# Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/reel_generator")

# Create engine
engine = create_engine(DATABASE_URL, echo=False)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


class User(Base):
    """User model"""
    __tablename__ = "users"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_hash = Column(String(64), unique=True, nullable=False, index=True)  # SHA256 hash
    password_hash = Column(String(255), nullable=True)  # bcrypt hash (nullable for OAuth users)
    auth_provider = Column(String(50), nullable=True, default=None)  # 'google' or None for email/password
    google_id = Column(String(255), nullable=True, unique=True, index=True)  # Google user ID for OAuth users
    credit = Column(Integer, default=0, nullable=False)
    total_minutes_downloaded = Column(Float, default=0.0, nullable=False)
    last_credit_purchase = Column(DateTime, nullable=True)  # Last time user purchased credits
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    jobs = relationship("Job", back_populates="user", cascade="all, delete-orphan")


class Job(Base):
    """Job model - represents a video processing job"""
    __tablename__ = "jobs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(String(36), unique=True, nullable=False, index=True)  # API job ID (UUID string)
    youtube_url = Column(Text, nullable=True)  # Nullable for uploaded videos
    status = Column(String(20), nullable=False, default="pending")  # pending, processing, completed, failed
    video_duration = Column(Float, nullable=True)  # Original video duration in minutes
    credits_deducted = Column(Integer, default=0, nullable=False)  # Credits deducted for this job (for refunds)
    video_downloaded = Column(String(10), default="no", nullable=False)  # "yes", "no", "failed" - track if video was successfully downloaded
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="jobs")
    files = relationship("JobFile", back_populates="job", cascade="all, delete-orphan")


class JobFile(Base):
    """Job file model - stores information about generated files"""
    __tablename__ = "job_files"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(PG_UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, index=True)
    file_type = Column(String(50), nullable=False)  # main_reel, medium_reel, short_reel, metadata_main_reel, metadata_medium_reel_0, etc.
    file_path = Column(Text, nullable=False)
    file_size = Column(BigInteger, nullable=True)  # Size in bytes
    duration_minutes = Column(Float, nullable=True)  # Duration in minutes (NULL for metadata files)
    virality_score = Column(Integer, nullable=True)  # Virality score from 1-100 (NULL for metadata files)
    file_metadata = Column(Text, nullable=True)  # JSON string storing additional metadata (e.g., edit information)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    job = relationship("Job", back_populates="files")


def init_db():
    """Initialize database - create all tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

