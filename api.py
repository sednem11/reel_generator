"""
FastAPI server for Reel Generator backend API.
Provides REST endpoints to interact with the video processing functionality.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from urllib.parse import urlencode
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import uuid
import os
import json
import threading
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session
import asyncio

# Import functions from reel.py
from reel import (
    download_video,
    is_youtube_url,
    get_transcript,
    get_transcript_with_whisper,
    analyze_transcript_with_llm,
    analyze_transcript_for_medium_clips,
    analyze_transcript_for_short_clips,
    validate_clips,
    create_reel,
    create_medium_clip_reels,
    create_short_clip_reels,
    generate_metadata,
    generate_metadata_for_video,
    calculate_virality_score,
    VideoFileClip,
    apply_video_edits
)

# Import database and auth
from database import get_db, User, Job, JobFile, init_db
from auth import (
    hash_email, hash_password, verify_password, 
    create_access_token, get_current_user
)
from moviepy_stdout_capture import install_capture, get_progress, get_completed, get_tracker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Reel Generator API",
    description="API for generating video reels from uploaded files or direct video URLs",
    version="1.0.0"
)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    try:
        init_db()
        logger.info("Database initialized successfully")
        
        # Migrate file_type column if needed
        try:
            from sqlalchemy import text
            from database import engine
            with engine.connect() as conn:
                # Check current column size
                result = conn.execute(text("""
                    SELECT character_maximum_length 
                    FROM information_schema.columns 
                    WHERE table_name = 'job_files' 
                    AND column_name = 'file_type';
                """))
                row = result.fetchone()
                if row and row[0] < 50:
                    logger.info("Migrating file_type column from VARCHAR(20) to VARCHAR(50)...")
                    conn.execute(text("ALTER TABLE job_files ALTER COLUMN file_type TYPE VARCHAR(50);"))
                    conn.commit()
                    logger.info("✅ Successfully migrated file_type column to VARCHAR(50)")
                else:
                    logger.debug(f"file_type column is already adequate (size: {row[0] if row else 'N/A'})")
        except Exception as migrate_error:
            logger.warning(f"Could not migrate file_type column (this is OK if column is already correct): {migrate_error}")
            
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    
    # Start background cleanup task
    asyncio.create_task(periodic_cleanup_task())
    logger.info("Background cleanup task started")
    
    # Run initial cleanup to remove any orphaned files from previous runs
    # This handles files that were created before the cleanup system was implemented
    try:
        logger.info("Running initial cleanup of orphaned files...")
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, cleanup_old_files)
        logger.info("Initial cleanup completed")
    except Exception as e:
        logger.warning(f"Initial cleanup had errors (non-critical): {e}")

# Configure CORS to allow frontend connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", 
        "http://localhost:8000",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app|https://.*\.ngrok-free\.dev|https://.*\.ngrok\.io",  # Allow Vercel and ngrok domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Job storage (in-memory for now, used for progress tracking)
# Actual job data is stored in database
jobs: Dict[str, Dict[str, Any]] = {}

# Track video files currently in use (thread-safe)
# Used to prevent deletion of files that are actively being processed
video_files_in_use: set = set()
video_files_lock = threading.Lock()

# Limit concurrent video processing jobs to prevent memory exhaustion
# Each job uses significant RAM:
#   - Whisper model: ~1-2 GB (base model)
#   - Video file in memory: ~500 MB - 2 GB (depending on video size)
#   - MoviePy rendering: ~2-4 GB per job
# With 16 GB RAM, 1 concurrent job is safest to avoid OOM kills
# Can be increased if you have more RAM or use smaller Whisper models
MAX_CONCURRENT_JOBS = 1  # Reduced from os.cpu_count() // 2 to prevent OOM crashes
job_processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
logger.info(f"Job concurrency limit set to {MAX_CONCURRENT_JOBS} concurrent jobs (memory-constrained)")

# Credit costs
WATERMARK_REMOVAL_COST = 15  # Flat cost per package of reels for watermark removal (regardless of number of videos)
FREE_MINUTES_LIMIT = 50.0  # Free minutes limit (changed from 100)

def can_generate_with_watermark(user: User) -> bool:
    """
    Check if user can generate videos with watermark.
    Logic: total_minutes_downloaded < 50 OR (last_purchase exists AND last_purchase < 1 month ago)
    """
    # Check if total minutes is below the free limit
    if user.total_minutes_downloaded < FREE_MINUTES_LIMIT:
        return True
    
    # Check if user has purchased credits within the last month
    if user.last_credit_purchase:
        one_month_ago = datetime.utcnow() - timedelta(days=30)
        if user.last_credit_purchase >= one_month_ago:
            return True
    
    return False

# Job statuses
class JobStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Request/Response models
class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: str
    token: str


class UserResponse(BaseModel):
    user_id: str
    credit: int
    total_minutes_downloaded: float


class VideoProcessRequest(BaseModel):
    youtube_url: Optional[str] = None  # Optional for uploads
    min_clips: Optional[int] = None  # Auto-calculated if not provided
    quality: Optional[str] = "hd"  # Quality: "full_hd", "hd", or "normal"
    remove_watermark: Optional[bool] = False  # Remove watermark for 15 credits per video
    video_types: Optional[str] = "both"  # "short_only", "medium_only", or "both"
    font_style: Optional[str] = None  # Font style: "bold", "funny", "scary", "movie", "peptalk", "elegant", "modern" - MUST be provided by UI
    font_color: Optional[str] = None  # Font color: "white_red", "white_green", "white_blue", "white_black", "rainbow", etc. - MUST be provided by UI


class DistributedProcessRequest(BaseModel):
    video_url: str
    clips: Dict[str, Any]  # Clips structure from analysis
    quality: str
    remove_watermark: bool
    video_types: Optional[str] = "both"  # "short_only", "medium_only", or "both"
    font_style: Optional[str] = None  # Font style: "bold", "funny", "scary", "movie", "peptalk", "elegant", "modern" - MUST be provided by UI
    font_color: Optional[str] = None  # Font color: "white_red", "white_green", "white_blue", "white_black", "rainbow", "white_yellow", "white_purple", etc. - MUST be provided by UI
    client_capabilities: Dict[str, Any]  # RAM info from client
    distribution: Dict[str, Any]  # Task distribution plan


class PaymentRequest(BaseModel):
    credits: int
    amount: float
    currency: str = "EUR"


class PaymentResponse(BaseModel):
    success: bool
    message: str
    credits_added: Optional[int] = None
    new_balance: Optional[int] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    results: Optional[Dict[str, Any]] = None
    video_downloaded: Optional[str] = None  # "yes", "no", or "failed" - indicates if video was successfully downloaded


class ProgressUpdate(BaseModel):
    stage: str
    message: str
    percentage: Optional[float] = None


# Utility functions
def create_job() -> str:
    """Create a new job and return its ID"""
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": JobStatus.PENDING,
        "created_at": datetime.now().isoformat(),
        "progress": {},
        "results": None,
        "error": None,
        "completed_at": None,
        "cancelled": False,  # Cancellation flag
        "process": None  # Store subprocess handle for killing
    }
    return job_id


def update_job_status(job_id: str, status: str, **kwargs):
    """Update job status and optionally other fields"""
    if job_id not in jobs:
        logger.error(f"Job {job_id} not found")
        return
    
    jobs[job_id]["status"] = status
    if "error" in kwargs:
        jobs[job_id]["error"] = kwargs["error"]
    if "progress" in kwargs:
        jobs[job_id]["progress"].update(kwargs["progress"])
    if "results" in kwargs:
        jobs[job_id]["results"] = kwargs["results"]
    if status in [JobStatus.COMPLETED, JobStatus.FAILED]:
        jobs[job_id]["completed_at"] = datetime.now().isoformat()


def cleanup_old_files():
    """Clean up files older than 1 day from disk and database"""
    from database import SessionLocal
    import glob
    db = SessionLocal()
    
    try:
        # Calculate cutoff time (1 day ago)
        cutoff_time = datetime.utcnow() - timedelta(days=1)
        
        # Query JobFile table for files older than 1 day
        old_files = db.query(JobFile).filter(JobFile.created_at < cutoff_time).all()
        
        files_deleted = 0
        space_freed = 0
        files_skipped = 0
        
        # Clean up files from database
        for job_file in old_files:
            file_path = job_file.file_path
            
            # Check if file is a video file that might be in use
            is_video_file = file_path.endswith("_video.mp4")
            
            if is_video_file:
                # Check if file is currently in use
                with video_files_lock:
                    if file_path in video_files_in_use:
                        files_skipped += 1
                        continue
            
            # Check if file exists on disk
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    files_deleted += 1
                    space_freed += file_size
                    logger.debug(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not delete file {file_path}: {e}")
            
            # Delete database record
            try:
                db.delete(job_file)
            except Exception as e:
                logger.warning(f"Could not delete JobFile record for {file_path}: {e}")
        
        # Also clean up orphaned job_*_video.mp4 files (not in database, should be deleted immediately)
        # These are original downloaded videos that should have been deleted after processing
        orphaned_video_files = glob.glob("job_*_video.mp4")
        for video_file in orphaned_video_files:
            # Check if file is currently in use
            with video_files_lock:
                if video_file in video_files_in_use:
                    files_skipped += 1
                    continue
            
            # Delete orphaned video files immediately (they should have been deleted after processing)
            # These files are large (hundreds of MB) and not needed for downloads
            try:
                if os.path.exists(video_file):
                    file_size = os.path.getsize(video_file)
                    os.remove(video_file)
                    files_deleted += 1
                    space_freed += file_size
                    file_size_mb = file_size / (1024 * 1024)
                    logger.info(f"🗑️  Deleted orphaned original video file: {video_file} ({file_size_mb:.2f} MB)")
            except Exception as e:
                logger.warning(f"Could not delete orphaned video file {video_file}: {e}")
        
        db.commit()
        
        if files_deleted > 0 or files_skipped > 0:
            space_freed_mb = space_freed / (1024 * 1024)
            logger.info(f"Cleanup completed: {files_deleted} files deleted, {files_skipped} files skipped (in use), {space_freed_mb:.2f} MB freed")
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


async def periodic_cleanup_task():
    """Background task that runs cleanup every 2 hours"""
    while True:
        try:
            await asyncio.sleep(2 * 60 * 60)  # 2 hours
            logger.info("Starting periodic file cleanup...")
            # Run cleanup in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, cleanup_old_files)
        except Exception as e:
            logger.error(f"Error in periodic cleanup task: {e}", exc_info=True)
            # Continue even if cleanup fails
            await asyncio.sleep(60)  # Wait 1 minute before retrying


async def process_video_job_async(job_id: str, youtube_url: Optional[str] = None, min_clips_required: Optional[int] = None, user_id: str = None, quality: str = "hd", video_file_path: Optional[str] = None, remove_watermark: bool = False, video_types: str = "both", font_style: Optional[str] = None, font_color: Optional[str] = None):
    """Process video with concurrency control"""
    async with job_processing_semaphore:
        try:
            # Run the actual processing in a thread pool since it's CPU-bound
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, process_video_job, job_id, youtube_url, min_clips_required, user_id, quality, video_file_path, remove_watermark, video_types, font_style, font_color)
        except Exception as e:
            # Catch any exceptions that might still propagate
            # The job status should already be updated in process_video_job, but log here as well
            logger.error(f"Exception in process_video_job_async for job {job_id}: {e}", exc_info=True)
            # Don't re-raise - the job status is already handled


def is_job_cancelled(db, job_id: str) -> bool:
    """Check if a job has been cancelled (deleted from database or cancellation flag set)"""
    # First check in-memory cancellation flag
    if job_id in jobs and jobs[job_id].get("cancelled", False):
        return True
    # Then check if job was deleted from database
    db_job = db.query(Job).filter(Job.job_id == job_id).first()
    return db_job is None

def process_video_job(job_id: str, youtube_url: Optional[str] = None, min_clips_required: Optional[int] = None, user_id: str = None, quality: str = "hd", video_file_path: Optional[str] = None, remove_watermark: bool = False, video_types: str = "both", font_style: Optional[str] = None, font_color: Optional[str] = None):
    """Process video in background thread"""
    # Create new database session for background thread
    from database import SessionLocal
    db = SessionLocal()
    should_close_db = True
    
    try:
        if video_file_path:
            logger.info(f"Starting job {job_id} for uploaded file: {video_file_path}")
        else:
            logger.info(f"Starting job {job_id} for URL: {youtube_url}")
        
        update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "starting", "message": "Initializing...", "percentage": 5})
        
        # Get job from database
        db_job = db.query(Job).filter(Job.job_id == job_id).first()
        
        # Check if job still exists (cancelled jobs are deleted)
        if not db_job:
            logger.info(f"Job {job_id} was deleted (cancelled), stopping processing")
            return
        
        # Update job status to processing
        db_job.status = JobStatus.PROCESSING
        db.commit()
        
        # Handle video file - either download or use uploaded
        if video_file_path and os.path.exists(video_file_path):
            # Use uploaded file
            video_file = video_file_path
            update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "upload", "message": "Processing uploaded video...", "percentage": 10})
            logger.info(f"Using uploaded file: {video_file}")
        elif youtube_url:
            # Download from URL
            update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "download", "message": "Downloading video... (1-3 minutes)", "percentage": 10})
            
            # Check if cancelled before starting download
            if is_job_cancelled(db, job_id):
                logger.info(f"Job {job_id} was cancelled before download, stopping")
                return
            
            # Mark download attempt in database
            db_job = db.query(Job).filter(Job.job_id == job_id).first()
            if db_job:
                db_job.video_downloaded = "no"  # Mark as attempting download
                db.commit()
            
            try:
                # Import download_video with cancellation support
                from reel import download_video_with_cancellation
                
                # Create cancellation check function
                def check_cancelled():
                    return is_job_cancelled(db, job_id)
                
                # Store process handle in jobs dict
                def store_process(process):
                    if job_id in jobs:
                        jobs[job_id]["process"] = process
                
                # Download with cancellation support
                video_file = download_video_with_cancellation(
                    youtube_url, 
                    filename=f"job_{job_id}_video.mp4", 
                    quality=quality,
                    check_cancelled=check_cancelled,
                    process_handle_ref=store_process
                )
                
                # Check if cancelled after download
                if is_job_cancelled(db, job_id):
                    logger.info(f"Job {job_id} was cancelled during download, cleaning up")
                    if video_file and os.path.exists(video_file):
                        try:
                            os.remove(video_file)
                        except:
                            pass
                    return
                
                if not video_file:
                    # Mark download as failed
                    if db_job:
                        db_job.video_downloaded = "failed"
                        db.commit()
                    raise ValueError("Failed to download video")
                
                # Mark download as successful
                if db_job:
                    db_job.video_downloaded = "yes"
                    db.commit()
            except Exception as download_error:
                # Check if cancelled
                if is_job_cancelled(db, job_id):
                    logger.info(f"Job {job_id} was cancelled during download error handling")
                    return
                # Mark download as failed
                if db_job:
                    db_job.video_downloaded = "failed"
                    db.commit()
                raise  # Re-raise to be handled by outer exception handler
        else:
            raise ValueError("Either youtube_url or video_file_path must be provided")
        
        # Check if job still exists (cancelled jobs are deleted)
        if is_job_cancelled(db, job_id):
            logger.info(f"Job {job_id} was deleted (cancelled), stopping processing")
            with video_files_lock:
                if video_file and video_file in video_files_in_use:
                    video_files_in_use.discard(video_file)
            return
        
        # Track video file as in use
        with video_files_lock:
            video_files_in_use.add(video_file)
        
        # Get video duration
        temp_video = None
        try:
            temp_video = VideoFileClip(video_file)
            video_duration = temp_video.duration
        finally:
            if temp_video:
                temp_video.close()
        
        # Check if job still exists (cancelled jobs are deleted)
        db.refresh(db_job)
        if not db_job:
            logger.info(f"Job {job_id} was deleted (cancelled), stopping processing")
            with video_files_lock:
                video_files_in_use.discard(video_file)
            return
        
        # Get transcript
        transcript_text = None
        transcript_data = None
        
        if youtube_url:
            # For YouTube videos, try YouTube transcript API first (faster), fallback to Whisper
            update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "transcript", "message": "Fetching transcript... (~30 seconds)", "percentage": 15})
            
            # Check if cancelled before transcript
            if is_job_cancelled(db, job_id):
                logger.info(f"Job {job_id} was cancelled before transcript, stopping")
                with video_files_lock:
                    if video_file and video_file in video_files_in_use:
                        video_files_in_use.discard(video_file)
                return
            
            try:
                video_id = youtube_url.split("v=")[1].split("&")[0]
                logger.info(f"Trying YouTube transcript API first (with proxy support)...")
                transcript_text, transcript_data = get_transcript(video_id)
                
                # Check if cancelled after YouTube transcript
                if is_job_cancelled(db, job_id):
                    logger.info(f"Job {job_id} was cancelled after YouTube transcript, stopping")
                    with video_files_lock:
                        if video_file and video_file in video_files_in_use:
                            video_files_in_use.discard(video_file)
                    return
                
                if transcript_text and transcript_data:
                    logger.info("✅ Successfully retrieved transcript from YouTube API")
            except (IndexError, ValueError) as e:
                logger.info(f"Not a YouTube URL or invalid format: {e}")
            except Exception as e:
                logger.warning(f"YouTube transcript API failed: {e}")
            
            # Fallback to Whisper if YouTube API failed
            if transcript_text is None or transcript_data is None:
                # Check if cancelled before Whisper
                if is_job_cancelled(db, job_id):
                    logger.info(f"Job {job_id} was cancelled before Whisper transcript, stopping")
                    with video_files_lock:
                        if video_file and video_file in video_files_in_use:
                            video_files_in_use.discard(video_file)
                    return
                
                logger.info("YouTube transcript API unavailable, using Whisper (local transcription)...")
                update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "transcript", "message": "Transcribing video with Whisper... (2-5 minutes)", "percentage": 15})
                transcript_text, transcript_data = get_transcript_with_whisper(video_file, model_name="base")
                
                # Check if cancelled after Whisper transcript
                if is_job_cancelled(db, job_id):
                    logger.info(f"Job {job_id} was cancelled after Whisper transcript, stopping")
                    with video_files_lock:
                        if video_file and video_file in video_files_in_use:
                            video_files_in_use.discard(video_file)
                    return
        else:
            # For uploaded videos, use Whisper directly
            logger.info("Using Whisper for uploaded video transcription...")
            update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "transcript", "message": "Transcribing video with Whisper... (2-5 minutes)", "percentage": 15})
            transcript_text, transcript_data = get_transcript_with_whisper(video_file, model_name="base")
            
            # Check if cancelled after Whisper transcript
            if is_job_cancelled(db, job_id):
                logger.info(f"Job {job_id} was cancelled after Whisper transcript, stopping")
                with video_files_lock:
                    if video_file and video_file in video_files_in_use:
                        video_files_in_use.discard(video_file)
                return
        
        if transcript_text is None or transcript_data is None:
            error_msg = (
                "Failed to transcribe video. This may be due to:\n"
                "• Audio quality issues in the video\n"
                "• Video file corruption\n"
                "• Unsupported video format\n\n"
                "Please try a different video or contact support if the issue persists."
            )
            logger.error(f"Job {job_id} failed: {error_msg}")
            raise ValueError(error_msg)
        
        # Calculate min clips if not provided
        if min_clips_required is None:
            video_duration_minutes = video_duration / 60.0
            if video_duration_minutes >= 5 and video_duration_minutes < 10:
                min_clips_required = 2
            elif video_duration_minutes >= 10 and video_duration_minutes < 20:
                min_clips_required = 3
            elif video_duration_minutes >= 20:
                min_clips_required = 4
            else:
                min_clips_required = 2
        
        logger.info(f"Video duration: {video_duration / 60:.1f} minutes, requiring {min_clips_required} clips")
        
        # Check if job still exists (cancelled jobs are deleted)
        db.refresh(db_job)
        if not db_job:
            logger.info(f"Job {job_id} was deleted (cancelled), stopping processing")
            with video_files_lock:
                video_files_in_use.discard(video_file)
            return
        
        # Find clips in parallel
        update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "analyzing", "message": "Analyzing video content to find best clips... (1-2 minutes)", "percentage": 25})
        
        main_clips_result = [None]
        medium_clips_result = [None]
        short_clips_result = [None]
        main_clips_error = [None]
        medium_clips_error = [None]
        short_clips_error = [None]
        
        def find_main_clips():
            try:
                result = analyze_transcript_with_llm(transcript_text, min_clips_required=min_clips_required, less_strict=False)
                main_clips_result[0] = result
            except Exception as e:
                main_clips_error[0] = e
                logger.error(f"Error finding main clips: {e}")
        
        # Determine max limits based on user selection from UI
        # First find ALL possible clips, then select the best ones up to these limits
        if video_types == "short_only":
            medium_clips_min = 0
            medium_clips_max = 0  # Don't create medium clips
            short_clips_min = 0
            short_clips_max = 6  # Max 6 shorts as per UI selection
        elif video_types == "medium_only":
            medium_clips_min = 0
            medium_clips_max = 4  # Max 4 medium as per UI selection
            short_clips_min = 0
            short_clips_max = 0  # Don't create short clips
        else:  # both
            medium_clips_min = 0
            medium_clips_max = 2  # Max 2 medium as per UI selection
            short_clips_min = 0
            short_clips_max = 4  # Max 4 shorts as per UI selection
        
        def find_medium_clips():
            if medium_clips_max > 0:
                try:
                    # First find ALL possible medium clips (high limit to get all candidates)
                    all_clips_result = analyze_transcript_for_medium_clips(transcript_text, min_clips=0, max_clips=50)
                    if all_clips_result and all_clips_result.get("clips"):
                        # Sort clips by score (highest first) and take top N
                        clips = all_clips_result["clips"]
                        # Extract score, defaulting to 50 if not present
                        sorted_clips = sorted(clips, key=lambda x: x.get("score", 50), reverse=True)
                        # Take only the top max_clips_max clips
                        selected_clips = sorted_clips[:medium_clips_max]
                        all_clips_result["clips"] = selected_clips
                        print(f"✅ Found {len(clips)} medium clips, selected top {len(selected_clips)} by score")
                    medium_clips_result[0] = all_clips_result
                except Exception as e:
                    medium_clips_error[0] = e
                    logger.error(f"Error finding medium clips: {e}")
        
        def find_short_clips():
            if short_clips_max > 0:
                try:
                    # First find ALL possible short clips (high limit to get all candidates)
                    all_clips_result = analyze_transcript_for_short_clips(transcript_text, min_clips=0, max_clips=50)
                    if all_clips_result and all_clips_result.get("clips"):
                        # Sort clips by score (highest first) and take top N
                        clips = all_clips_result["clips"]
                        # Extract score, defaulting to 50 if not present
                        sorted_clips = sorted(clips, key=lambda x: x.get("score", 50), reverse=True)
                        # Take only the top short_clips_max clips
                        selected_clips = sorted_clips[:short_clips_max]
                        all_clips_result["clips"] = selected_clips
                        print(f"✅ Found {len(clips)} short clips, selected top {len(selected_clips)} by score")
                    short_clips_result[0] = all_clips_result
                except Exception as e:
                    short_clips_error[0] = e
                    logger.error(f"Error finding short clips: {e}")
        
        # Run in parallel threads (only start threads that are needed)
        thread1 = threading.Thread(target=find_main_clips, name="MainClipsFinder")
        thread1.start()
        
        threads = [thread1]
        if medium_clips_max > 0:
            thread2 = threading.Thread(target=find_medium_clips, name="MediumClipsFinder")
            thread2.start()
            threads.append(thread2)
        
        if short_clips_max > 0:
            thread3 = threading.Thread(target=find_short_clips, name="ShortClipsFinder")
            thread3.start()
            threads.append(thread3)
        
        # Join all threads
        for thread in threads:
            thread.join()
        
        if main_clips_error[0]:
            raise Exception(f"Error finding main clips: {main_clips_error[0]}")
        
        clips_info = main_clips_result[0]
        if not clips_info or "clips" not in clips_info:
            raise ValueError("No clips returned from analysis")
        
        # Validate clips
        update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "validating", "message": "Validating clips... (~30 seconds)", "percentage": 35})
        clips_info["clips"] = validate_clips(clips_info["clips"], video_duration=video_duration, min_clips=min_clips_required, transcript_data=transcript_data)
        
        if not clips_info["clips"]:
            raise ValueError("No valid clips found after validation")
        
        # Calculate total number of videos to create
        medium_clips_count = len(medium_clips_result[0].get("clips", [])) if medium_clips_result[0] and medium_clips_result[0].get("clips") else 0
        short_clips_count = len(short_clips_result[0].get("clips", [])) if short_clips_result[0] and short_clips_result[0].get("clips") else 0
        
        # Initialize empty results if not requested
        if medium_clips_max == 0:
            medium_clips_result[0] = None
        if short_clips_max == 0:
            short_clips_result[0] = None
        # Calculate total videos - main reel only created for "both" video type
        main_reel_count = 1 if video_types == "both" else 0
        total_videos = main_reel_count + medium_clips_count + short_clips_count
        
        # Credits are already deducted when the job is started, so we don't need to check again here
        # The actual credit deduction happens immediately in the endpoint handlers
        
        # Prepare results
        results = {
            "video_file": video_file,
            "video_duration": video_duration,
            "main_reel": None,
            "medium_reels": [],
            "short_reels": [],
            "metadata": None
        }
        
        # Create informative message about videos being prepared
        # Estimate time based on video duration and number of clips
        video_duration_minutes = video_duration / 60.0
        if video_duration_minutes < 10:
            base_time = "3-6 minutes"
        elif video_duration_minutes < 20:
            base_time = "5-10 minutes"
        else:
            base_time = "8-15 minutes"
        
        video_count_msg = f"Preparing {total_videos} video{'s' if total_videos > 1 else ''}: 1 compilation video"
        if medium_clips_count > 0:
            video_count_msg += f", {medium_clips_count} medium reel{'s' if medium_clips_count > 1 else ''}"
        if short_clips_count > 0:
            video_count_msg += f", {short_clips_count} short reel{'s' if short_clips_count > 1 else ''}"
        video_count_msg += f" ({base_time} estimated)"
        
        # Check if job still exists (cancelled jobs are deleted)
        if is_job_cancelled(db, job_id):
            logger.info(f"Job {job_id} was deleted (cancelled), stopping processing before video creation")
            with video_files_lock:
                if video_file and video_file in video_files_in_use:
                    video_files_in_use.discard(video_file)
            return
        
        # Create videos in parallel
        update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "creating", "message": video_count_msg, "percentage": 40})
        
        main_reel_error = [None]
        medium_reels_error = [None]
        short_reels_error = [None]
        main_reel_done = [False]
        medium_reels_done = [False]
        short_reels_done = [False]
        
        def create_main_reel_thread():
            # Only create main reel (compilation) when video_types is "both"
            if video_types != "both":
                logger.info(f"Skipping main reel creation (video_types={video_types}, main reel only created for 'both')")
                results["main_reel"] = None
                main_reel_done[0] = True
                return
            
            try:
                output_file = f"job_{job_id}_reel.mp4"
                logger.info(f"Starting main reel creation: {output_file}")
                # Use UI values directly - NO DEFAULTS, must use what UI sends
                # If None, log error but still pass it (UI should always send values)
                if font_style is None or font_color is None:
                    logger.warning(f"⚠️  UI did not send font_style or font_color! style={font_style}, color={font_color}. Using minimal fallback.")
                    final_font_style = font_style if font_style is not None else "bold"
                    final_font_color = font_color if font_color is not None else "white_red"
                else:
                    final_font_style = font_style
                    final_font_color = font_color
                logger.info(f"🎨 Creating main reel with font_style={final_font_style}, font_color={final_font_color} (UI sent: style={font_style}, color={font_color})")
                result_file = create_reel(video_file, clips_info, output_file=output_file, min_clips=min_clips_required, transcript_data=transcript_data, quality=quality, remove_watermark=remove_watermark, font_style=final_font_style, font_color=final_font_color)
                
                # Check if file was created (either from return value or file existence)
                if result_file and os.path.exists(result_file):
                    file_size = os.path.getsize(result_file)
                    logger.info(f"Main reel created successfully: {result_file} ({file_size} bytes)")
                    results["main_reel"] = result_file
                elif os.path.exists(output_file):
                    file_size = os.path.getsize(output_file)
                    logger.info(f"Main reel created successfully (fallback check): {output_file} ({file_size} bytes)")
                    results["main_reel"] = output_file
                else:
                    logger.error(f"Main reel file was not created: {output_file}")
                    results["main_reel"] = None
                    main_reel_error[0] = Exception("Main reel file was not created after processing")
                
                main_reel_done[0] = True
            except Exception as e:
                main_reel_error[0] = e
                logger.error(f"Error creating main reel: {e}", exc_info=True)
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                results["main_reel"] = None
                main_reel_done[0] = True
        
        def create_medium_reels_thread():
            if medium_clips_max == 0:
                medium_reels_done[0] = True
                return
            try:
                medium_clips_info = medium_clips_result[0]
                if medium_clips_info and medium_clips_info.get('clips'):
                    # Use UI values directly - NO DEFAULTS, must use what UI sends
                    if font_style is None or font_color is None:
                        logger.warning(f"⚠️  UI did not send font_style or font_color for medium reels! style={font_style}, color={font_color}. Using minimal fallback.")
                        final_font_style = font_style if font_style is not None else "bold"
                        final_font_color = font_color if font_color is not None else "white_red"
                    else:
                        final_font_style = font_style
                        final_font_color = font_color
                    logger.info(f"🎨 Creating medium reels with font_style={final_font_style}, font_color={final_font_color} (UI sent: style={font_style}, color={font_color})")
                    medium_reels = create_medium_clip_reels(video_file, medium_clips_info, transcript_data=transcript_data, quality=quality, remove_watermark=remove_watermark, font_style=final_font_style, font_color=final_font_color)
                    results["medium_reels"] = medium_reels
                medium_reels_done[0] = True
            except Exception as e:
                medium_reels_error[0] = e
                logger.error(f"Error creating medium reels: {e}")
        
        def create_short_reels_thread():
            if short_clips_max == 0:
                short_reels_done[0] = True
                return
            try:
                short_clips_info = short_clips_result[0]
                if short_clips_info and short_clips_info.get('clips'):
                    # Use UI values directly - NO DEFAULTS, must use what UI sends
                    if font_style is None or font_color is None:
                        logger.warning(f"⚠️  UI did not send font_style or font_color for short reels! style={font_style}, color={font_color}. Using minimal fallback.")
                        final_font_style = font_style if font_style is not None else "bold"
                        final_font_color = font_color if font_color is not None else "white_red"
                    else:
                        final_font_style = font_style
                        final_font_color = font_color
                    logger.info(f"🎨 Creating short reels with font_style={final_font_style}, font_color={final_font_color} (UI sent: style={font_style}, color={font_color})")
                    short_reels = create_short_clip_reels(video_file, short_clips_info, transcript_data=transcript_data, quality=quality, remove_watermark=remove_watermark, font_style=final_font_style, font_color=final_font_color)
                    results["short_reels"] = short_reels
                short_reels_done[0] = True
            except Exception as e:
                short_reels_error[0] = e
                logger.error(f"Error creating short reels: {e}")
        
        reel_thread = threading.Thread(target=create_main_reel_thread, name="CompilationVideoCreator")
        medium_thread = threading.Thread(target=create_medium_reels_thread, name="MediumReelsCreator")
        short_thread = threading.Thread(target=create_short_reels_thread, name="ShortReelsCreator")
        
        reel_thread.start()
        medium_thread.start()
        short_thread.start()
        
        # Monitor progress during video creation by checking actual video files
        start_time = datetime.now()
        import time as time_module
        import glob
        
        # Track which videos we expect to be created
        expected_main_reel = 1 if video_types == "both" else 0
        expected_medium_reels = medium_clips_count
        expected_short_reels = short_clips_count
        
        # Track completion times for better estimation
        video_completion_times = []
        completed_file_sizes = {}  # Track file sizes to detect when files are fully written
        video_start_times = {}  # Track when each video file starts being created
        video_frame_rates = {}  # Track frame processing rates for videos in progress
        last_videos_completed_count = 0  # Track when videos_completed changes
        
        # Install MoviePy stdout capture to parse progress
        tracker = get_tracker()
        tracker.reset()
        install_capture()
        
        # Calculate initial time estimate based on typical video creation
        # We'll update this as we get actual data
        parallel_workers = 2  # Videos are processed 2 at a time
        initial_estimate_seconds = None  # Will be calculated from first completed video
        last_estimate_seconds = None
        
        while not (main_reel_done[0] and medium_reels_done[0] and short_reels_done[0]):
            # Check if cancelled during video creation
            if is_job_cancelled(db, job_id):
                logger.info(f"Job {job_id} was cancelled during video creation, stopping")
                with video_files_lock:
                    if video_file and video_file in video_files_in_use:
                        video_files_in_use.discard(video_file)
                # Note: Threads will continue running, but we stop monitoring and return
                return
            
            # Count completed videos based on "✅ Created short:" messages from stdout
            # This is the definitive signal that videos are created
            videos_completed = 0
            
            # Calculate progress percentage (40% to 85% for video creation)
            base_progress = 40
            progress_range = 45  # 40% to 85%
            if total_videos > 0:
                current_progress = base_progress + int((videos_completed / total_videos) * progress_range)
            else:
                current_progress = base_progress
            
            # Track videos that are starting to be created
            # Check for new video files that are being written (size > 0 but not stable yet)
            all_video_files = []
            if expected_main_reel > 0:
                main_reel_file = f"job_{job_id}_reel.mp4"
                if os.path.exists(main_reel_file):
                    all_video_files.append(main_reel_file)
            if expected_medium_reels > 0:
                all_video_files.extend(glob.glob("medium_reel_*.mp4"))
            if expected_short_reels > 0:
                all_video_files.extend(glob.glob("short_reel_*.mp4"))
            
            # Track when videos start being created
            for f in all_video_files:
                if os.path.exists(f) and f not in video_start_times and f not in completed_file_sizes:
                    current_size = os.path.getsize(f)
                    if current_size > 0:
                        video_start_times[f] = datetime.now()
            
            # Get MoviePy progress from stdout capture
            moviepy_progress = get_progress()
            moviepy_completed = get_completed()
            
            # Update videos_completed based on "✅ Created short:" messages
            # This is the definitive signal that videos are created
            # Count should only go up, never down
            video_just_completed = False
            if moviepy_completed > videos_completed:
                videos_completed = moviepy_completed
                # Check if a new video just completed
                if videos_completed > last_videos_completed_count:
                    video_just_completed = True
                    last_videos_completed_count = videos_completed
            # Ensure count never goes down (safety check)
            elif videos_completed < last_videos_completed_count:
                videos_completed = last_videos_completed_count
            
            # Estimate time remaining using MoviePy frame data
            elapsed = (datetime.now() - start_time).total_seconds()
            remaining_videos = total_videos - videos_completed
            
            # Recalculate time estimate every time a video finishes
            if video_just_completed:
                estimated_remaining_seconds = None
                
                # Try to calculate from MoviePy progress first
                if moviepy_progress:
                    for filename, progress_info in moviepy_progress.items():
                        if progress_info.get('status') == 'processing':
                            total_frames = progress_info.get('total_frames', 0)
                            fps = progress_info.get('fps', 0)
                            
                            if total_frames > 0 and fps > 0:
                                # Calculate: (total_frames / fps) * remaining_videos / 60 = minutes
                                time_per_video_seconds = total_frames / fps
                                total_remaining_minutes = (time_per_video_seconds * remaining_videos) / 60
                                estimated_remaining_seconds = total_remaining_minutes * 60
                                last_estimate_seconds = estimated_remaining_seconds
                                break
                
                # If can't recalculate from MoviePy, subtract 3 minutes from previous estimate
                if estimated_remaining_seconds is None:
                    if last_estimate_seconds is not None:
                        estimated_remaining_seconds = max(0, last_estimate_seconds - 180)  # Subtract 3 minutes (180 seconds)
                        last_estimate_seconds = estimated_remaining_seconds
                    else:
                        # Fallback: estimate based on remaining videos
                        avg_time_per_video = 300
                        estimated_remaining_seconds = (remaining_videos / parallel_workers) * avg_time_per_video
                        last_estimate_seconds = estimated_remaining_seconds
            else:
                # No new video completed - use existing estimate or calculate from MoviePy progress
                estimated_remaining_seconds = None
                
                if moviepy_progress:
                    # Calculate from MoviePy frame data
                    for filename, progress_info in moviepy_progress.items():
                        if progress_info.get('status') == 'processing':
                            total_frames = progress_info.get('total_frames', 0)
                            fps = progress_info.get('fps', 0)
                            
                            if total_frames > 0 and fps > 0:
                                time_per_video_seconds = total_frames / fps
                                total_remaining_minutes = (time_per_video_seconds * remaining_videos) / 60
                                estimated_remaining_seconds = total_remaining_minutes * 60
                                last_estimate_seconds = estimated_remaining_seconds
                                break
                
                # Fallback to last estimate if no MoviePy progress
                if estimated_remaining_seconds is None:
                    if last_estimate_seconds is not None:
                        estimated_remaining_seconds = last_estimate_seconds
                    else:
                        avg_time_per_video = 300
                        estimated_remaining_seconds = (remaining_videos / parallel_workers) * avg_time_per_video
                        last_estimate_seconds = estimated_remaining_seconds
            
            if estimated_remaining_seconds and estimated_remaining_seconds > 0:
                # estimated_remaining_seconds is in seconds, convert to minutes for display
                estimated_remaining_minutes = estimated_remaining_seconds / 60
                if estimated_remaining_minutes < 2:
                    time_msg = f" (~{int(estimated_remaining_seconds)} sec remaining)"
                else:
                    time_msg = f" (~{int(estimated_remaining_minutes)} min remaining)"
            else:
                time_msg = ""
            
            # Display format: completed/total (e.g., 1/4, 2/4, 3/4, 4/4)
            progress_msg = f"Creating videos... {videos_completed}/{total_videos} completed{time_msg}"
            update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "creating", "message": progress_msg, "percentage": current_progress})
            
            # Wait a bit before checking again
            time_module.sleep(2)
        
        reel_thread.join()
        medium_thread.join()
        short_thread.join()
        
        # Check if cancelled after video creation threads complete
        if is_job_cancelled(db, job_id):
            logger.info(f"Job {job_id} was cancelled after video creation, stopping")
            with video_files_lock:
                if video_file and video_file in video_files_in_use:
                    video_files_in_use.discard(video_file)
            return
        
        # Check for errors and log them
        if main_reel_error[0]:
            error_msg = f"Main reel creation failed: {main_reel_error[0]}"
            logger.error(error_msg)
            # Don't fail the entire job, but log the error
            update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "creating", "message": f"Main reel failed: {str(main_reel_error[0])}", "percentage": 85})
        
        if medium_reels_error[0]:
            logger.warning(f"Medium reels creation had errors: {medium_reels_error[0]}")
        
        if short_reels_error[0]:
            logger.warning(f"Short reels creation had errors: {short_reels_error[0]}")
        
        # Generate metadata for each video individually
        update_job_status(job_id, JobStatus.PROCESSING, progress={"stage": "metadata", "message": "Generating metadata for videos... (~1 minute)", "percentage": 88})
        
        # Check if cancelled before metadata generation
        if is_job_cancelled(db, job_id):
            logger.info(f"Job {job_id} was cancelled before metadata generation, stopping")
            with video_files_lock:
                if video_file and video_file in video_files_in_use:
                    video_files_in_use.discard(video_file)
            return
        
        results["metadata"] = {}
        
        # Helper function to extract transcript segment for a clip
        def get_transcript_segment_for_clip(clip_start, clip_end, transcript_data):
            """Extract transcript text for a specific clip time range"""
            segment_texts = []
            for snippet in transcript_data:
                snippet_start = snippet['start']
                snippet_end = snippet['start'] + snippet['duration']
                if snippet_end >= clip_start and snippet_start <= clip_end:
                    segment_texts.append(snippet['text'])
            return ' '.join(segment_texts)
        
        # Generate metadata for main reel
        if results.get("main_reel") and clips_info and clips_info.get("clips"):
            print("📝 Generating metadata for main reel...")
            # Use all clips for main reel
            main_clip_texts = [clip.get('text', '') for clip in clips_info['clips']]
            main_clip_summary = " | ".join(main_clip_texts[:3])  # First 3 clips
            main_transcript_segment = transcript_text[:2000]  # First 2000 chars of full transcript
            
            main_metadata = generate_metadata_for_video(
                clip_text=main_clip_summary,
                clip_transcript_segment=main_transcript_segment,
                video_type="main_reel",
                video_index=0
            )
            # Calculate virality score for main reel
            main_virality_score = calculate_virality_score(
                clip_text=main_clip_summary,
                clip_transcript_segment=main_transcript_segment,
                video_type="main_reel",
                video_index=0
            )
            if main_metadata:
                metadata_file = f"job_{job_id}_main_reel_metadata.json"
                with open(metadata_file, 'w', encoding='utf-8') as f:
                    json.dump(main_metadata, f, indent=2, ensure_ascii=False)
                results["metadata"]["main_reel"] = {
                    "file": metadata_file,
                    "content": main_metadata,
                    "virality_score": main_virality_score
                }
        
        # Generate metadata for each medium reel
        if results.get("medium_reels") and medium_clips_result[0] and medium_clips_result[0].get("clips"):
            medium_clips_info = medium_clips_result[0]
            results["metadata"]["medium_reels"] = []
            
            # Create a mapping from clip index to clip for easy lookup
            clips_by_index = {i: clip for i, clip in enumerate(medium_clips_info["clips"])}
            
            # Process each file and match it to its corresponding clip by extracting index from filename
            # Filename format: medium_reel_{clip_index+1}_{start}s-{end}s.mp4
            import re
            for file_path in results["medium_reels"]:
                # Extract clip index from filename (e.g., "medium_reel_2_100s-150s.mp4" -> index 1)
                filename = os.path.basename(file_path)
                match = re.search(r'medium_reel_(\d+)_', filename)
                if match:
                    # Convert from 1-based filename index to 0-based clip index
                    file_index = int(match.group(1)) - 1
                    
                    if file_index in clips_by_index:
                        clip = clips_by_index[file_index]
                        clip_text = clip.get('text', '')
                        clip_start = clip.get('start', 0)
                        clip_end = clip.get('end', 0)
                        clip_transcript_segment = get_transcript_segment_for_clip(clip_start, clip_end, transcript_data)
                        
                        medium_metadata = generate_metadata_for_video(
                            clip_text=clip_text,
                            clip_transcript_segment=clip_transcript_segment,
                            video_type="medium_reel",
                            video_index=file_index
                        )
                        # Calculate virality score for medium reel
                        medium_virality_score = calculate_virality_score(
                            clip_text=clip_text,
                            clip_transcript_segment=clip_transcript_segment,
                            video_type="medium_reel",
                            video_index=file_index
                        )
                        # Ensure the list is large enough and insert at correct index
                        while len(results["metadata"]["medium_reels"]) <= file_index:
                            results["metadata"]["medium_reels"].append(None)
                        
                        if medium_metadata:
                            metadata_file = f"job_{job_id}_medium_reel_{file_index}_metadata.json"
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(medium_metadata, f, indent=2, ensure_ascii=False)
                            
                            results["metadata"]["medium_reels"][file_index] = {
                                "file": metadata_file,
                                "content": medium_metadata,
                                "virality_score": medium_virality_score
                            }
                        else:
                            logger.warning(f"Failed to generate metadata for medium_reel index {file_index}, but virality score calculated: {medium_virality_score}")
                            # Store virality score even if metadata failed
                            results["metadata"]["medium_reels"][file_index] = {
                                "file": None,
                                "content": None,
                                "virality_score": medium_virality_score
                            }
                    else:
                        logger.warning(f"Could not find clip for file {filename} (extracted index: {file_index})")
                else:
                    logger.warning(f"Could not extract clip index from filename: {filename}")
            
            # Ensure all clips have metadata entries (even if None) to maintain index alignment
            final_medium_metadata = []
            for i in range(len(medium_clips_info["clips"])):
                if i < len(results["metadata"]["medium_reels"]) and results["metadata"]["medium_reels"][i] is not None:
                    final_medium_metadata.append(results["metadata"]["medium_reels"][i])
                else:
                    # Create placeholder if metadata generation failed
                    logger.warning(f"Missing metadata for medium_reel index {i}, creating placeholder")
                    final_medium_metadata.append({
                        "file": None,
                        "content": None,
                        "virality_score": None
                    })
            results["metadata"]["medium_reels"] = final_medium_metadata
        
        # Generate metadata for each short reel
        if results.get("short_reels") and short_clips_result[0] and short_clips_result[0].get("clips"):
            short_clips_info = short_clips_result[0]
            results["metadata"]["short_reels"] = []
            
            # Create a mapping from clip index to clip for easy lookup
            clips_by_index = {i: clip for i, clip in enumerate(short_clips_info["clips"])}
            
            # Process each file and match it to its corresponding clip by extracting index from filename
            # Filename format: short_reel_{clip_index+1}_{start}s-{end}s.mp4
            import re
            for file_path in results["short_reels"]:
                # Extract clip index from filename (e.g., "short_reel_5_155s-178s.mp4" -> index 4)
                filename = os.path.basename(file_path)
                match = re.search(r'short_reel_(\d+)_', filename)
                if match:
                    # Convert from 1-based filename index to 0-based clip index
                    file_index = int(match.group(1)) - 1
                    
                    if file_index in clips_by_index:
                        clip = clips_by_index[file_index]
                        clip_text = clip.get('text', '')
                        clip_start = clip.get('start', 0)
                        clip_end = clip.get('end', 0)
                        clip_transcript_segment = get_transcript_segment_for_clip(clip_start, clip_end, transcript_data)
                        
                        short_metadata = generate_metadata_for_video(
                            clip_text=clip_text,
                            clip_transcript_segment=clip_transcript_segment,
                            video_type="short_reel",
                            video_index=file_index
                        )
                        # Calculate virality score for short reel
                        short_virality_score = calculate_virality_score(
                            clip_text=clip_text,
                            clip_transcript_segment=clip_transcript_segment,
                            video_type="short_reel",
                            video_index=file_index
                        )
                        # Ensure the list is large enough and insert at correct index
                        while len(results["metadata"]["short_reels"]) <= file_index:
                            results["metadata"]["short_reels"].append(None)
                        
                        if short_metadata:
                            metadata_file = f"job_{job_id}_short_reel_{file_index}_metadata.json"
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(short_metadata, f, indent=2, ensure_ascii=False)
                            
                            results["metadata"]["short_reels"][file_index] = {
                                "file": metadata_file,
                                "content": short_metadata,
                                "virality_score": short_virality_score
                            }
                        else:
                            logger.warning(f"Failed to generate metadata for short_reel index {file_index}, but virality score calculated: {short_virality_score}")
                            # Store virality score even if metadata failed
                            results["metadata"]["short_reels"][file_index] = {
                                "file": None,
                                "content": None,
                                "virality_score": short_virality_score
                            }
                    else:
                        logger.warning(f"Could not find clip for file {filename} (extracted index: {file_index})")
                else:
                    logger.warning(f"Could not extract clip index from filename: {filename}")
            
            # Ensure all clips have metadata entries (even if None) to maintain index alignment
            final_short_metadata = []
            for i in range(len(short_clips_info["clips"])):
                if i < len(results["metadata"]["short_reels"]) and results["metadata"]["short_reels"][i] is not None:
                    final_short_metadata.append(results["metadata"]["short_reels"][i])
                else:
                    # Create placeholder if metadata generation failed
                    logger.warning(f"Missing metadata for short_reel index {i}, creating placeholder")
                    final_short_metadata.append({
                        "file": None,
                        "content": None,
                        "virality_score": None
                    })
            results["metadata"]["short_reels"] = final_short_metadata
        
        # Save files to database
        db_job = db.query(Job).filter(Job.job_id == job_id).first()
        if db_job:
            # Update job status and duration
            db_job.status = JobStatus.COMPLETED
            db_job.completed_at = datetime.now()
            if results.get("video_duration"):
                db_job.video_duration = results["video_duration"]
            
            # Save main reel
            if results.get("main_reel") and os.path.exists(results["main_reel"]):
                file_size = os.path.getsize(results["main_reel"])
                # Get duration from video file
                try:
                    temp_video = VideoFileClip(results["main_reel"])
                    duration_minutes = temp_video.duration / 60.0
                    temp_video.close()
                except:
                    duration_minutes = None
                
                # Get virality score from metadata if available
                virality_score = None
                if results.get("metadata") and results["metadata"].get("main_reel"):
                    virality_score = results["metadata"]["main_reel"].get("virality_score")
                
                job_file = JobFile(
                    job_id=db_job.id,
                    file_type="main_reel",
                    file_path=results["main_reel"],
                    file_size=file_size,
                    duration_minutes=duration_minutes,
                    virality_score=virality_score
                )
                db.add(job_file)
            
            # Save medium reels
            for i, file_path in enumerate(results.get("medium_reels", [])):
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    try:
                        temp_video = VideoFileClip(file_path)
                        duration_minutes = temp_video.duration / 60.0
                        temp_video.close()
                    except:
                        duration_minutes = None
                    
                    # Get virality score from metadata if available
                    virality_score = None
                    if results.get("metadata") and results["metadata"].get("medium_reels") and i < len(results["metadata"]["medium_reels"]):
                        virality_score = results["metadata"]["medium_reels"][i].get("virality_score")
                    
                    job_file = JobFile(
                        job_id=db_job.id,
                        file_type="medium_reel",
                        file_path=file_path,
                        file_size=file_size,
                        duration_minutes=duration_minutes,
                        virality_score=virality_score
                    )
                    db.add(job_file)
            
            # Save short reels
            for i, file_path in enumerate(results.get("short_reels", [])):
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    try:
                        temp_video = VideoFileClip(file_path)
                        duration_minutes = temp_video.duration / 60.0
                        temp_video.close()
                    except:
                        duration_minutes = None
                    
                    # Get virality score from metadata if available
                    virality_score = None
                    if results.get("metadata") and results["metadata"].get("short_reels") and i < len(results["metadata"]["short_reels"]):
                        virality_score = results["metadata"]["short_reels"][i].get("virality_score")
                    
                    job_file = JobFile(
                        job_id=db_job.id,
                        file_type="short_reel",
                        file_path=file_path,
                        file_size=file_size,
                        duration_minutes=duration_minutes,
                        virality_score=virality_score
                    )
                    db.add(job_file)
            
            # Save metadata files for each video
            if results.get("metadata"):
                metadata_dict = results["metadata"]
                
                # Save main reel metadata
                if metadata_dict.get("main_reel"):
                    main_meta = metadata_dict["main_reel"]
                    file_path = main_meta.get("file") if isinstance(main_meta, dict) else main_meta
                    if file_path and os.path.exists(file_path):
                        file_size = os.path.getsize(file_path)
                        job_file = JobFile(
                            job_id=db_job.id,
                            file_type="metadata_main_reel",
                            file_path=file_path,
                            file_size=file_size,
                            duration_minutes=None
                        )
                        db.add(job_file)
                
                # Save medium reels metadata
                if metadata_dict.get("medium_reels"):
                    for i, medium_meta in enumerate(metadata_dict["medium_reels"]):
                        file_path = medium_meta.get("file") if isinstance(medium_meta, dict) else medium_meta
                        if file_path and os.path.exists(file_path):
                            file_size = os.path.getsize(file_path)
                            job_file = JobFile(
                                job_id=db_job.id,
                                file_type=f"metadata_medium_reel_{i}",
                                file_path=file_path,
                                file_size=file_size,
                                duration_minutes=None
                            )
                            db.add(job_file)
                
                # Save short reels metadata
                if metadata_dict.get("short_reels"):
                    logger.info(f"Saving {len(metadata_dict['short_reels'])} short reel metadata entries to database")
                    for i, short_meta in enumerate(metadata_dict["short_reels"]):
                        if short_meta is None:
                            logger.warning(f"Skipping short_reel metadata index {i} - entry is None")
                            continue
                        file_path = short_meta.get("file") if isinstance(short_meta, dict) else short_meta
                        if file_path and os.path.exists(file_path):
                            file_size = os.path.getsize(file_path)
                            job_file = JobFile(
                                job_id=db_job.id,
                                file_type=f"metadata_short_reel_{i}",
                                file_path=file_path,
                                file_size=file_size,
                                duration_minutes=None
                            )
                            db.add(job_file)
                            logger.info(f"Saved metadata_short_reel_{i} to database")
                        else:
                            logger.warning(f"Metadata file for short_reel index {i} does not exist: {file_path}")
            
            db.commit()
        
        # Mark as completed
        # Log what was created
        created_items = []
        if results.get("main_reel"):
            created_items.append("main reel")
        if results.get("medium_reels"):
            created_items.append(f"{len(results['medium_reels'])} medium reels")
        if results.get("short_reels"):
            created_items.append(f"{len(results['short_reels'])} short reels")
        
        logger.info(f"Job {job_id} completed. Created: {', '.join(created_items) if created_items else 'nothing'}")
        
        # Update user's total minutes downloaded with all generated videos
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            total_duration_minutes = 0.0
            
            # Sum duration from all generated videos
            if results.get("main_reel") and os.path.exists(results["main_reel"]):
                try:
                    temp_video = VideoFileClip(results["main_reel"])
                    total_duration_minutes += temp_video.duration / 60.0
                    temp_video.close()
                except:
                    pass
            
            for file_path in results.get("medium_reels", []):
                if os.path.exists(file_path):
                    try:
                        temp_video = VideoFileClip(file_path)
                        total_duration_minutes += temp_video.duration / 60.0
                        temp_video.close()
                    except:
                        pass
            
            for file_path in results.get("short_reels", []):
                if os.path.exists(file_path):
                    try:
                        temp_video = VideoFileClip(file_path)
                        total_duration_minutes += temp_video.duration / 60.0
                        temp_video.close()
                    except:
                        pass
            
            # Update total minutes
            if total_duration_minutes > 0:
                previous_total = user.total_minutes_downloaded
                user.total_minutes_downloaded += total_duration_minutes
                
                # Give 250 free credits every 1000 minutes (1000, 2000, 3000, etc.)
                previous_milestone = int(previous_total // 1000)
                new_milestone = int(user.total_minutes_downloaded // 1000)
                
                if new_milestone > previous_milestone:
                    credits_to_add = (new_milestone - previous_milestone) * 250
                    user.credit += credits_to_add
                    logger.info(f"User {user_id} reached {new_milestone * 1000} minutes milestone! Added {credits_to_add} free credits. New balance: {user.credit}")
                
                db.commit()
                logger.info(f"Updated user {user_id} total minutes: {previous_total:.2f} -> {user.total_minutes_downloaded:.2f} (added {total_duration_minutes:.2f} minutes from generated videos)")
        
        # Credits are already deducted when the job is started, so we don't need to deduct again here
        # If fewer videos were created than estimated, we could refund the difference, but for simplicity
        # we'll keep the upfront deduction. The actual cost was already paid.
        
        update_job_status(job_id, JobStatus.COMPLETED, results=results, progress={"stage": "completed", "message": "Processing complete!", "percentage": 100})
        
        # Remove video file from tracking set (no longer in use)
        if results.get("video_file"):
            with video_files_lock:
                video_files_in_use.discard(results["video_file"])
        
        # Delete original video file (job_*_video.mp4) immediately after processing
        # This file is NOT needed for downloads - only the generated reels are needed
        # Generated reels and metadata are kept for 1 day (cleaned up by background task)
        if results.get("video_file"):
            video_file_path = results["video_file"]
            # Double-check it's not in use (thread-safe check)
            with video_files_lock:
                if video_file_path not in video_files_in_use:
                    try:
                        if os.path.exists(video_file_path):
                            file_size = os.path.getsize(video_file_path)
                            os.remove(video_file_path)
                            file_size_mb = file_size / (1024 * 1024)
                            logger.info(f"🗑️  Deleted original video file: {video_file_path} ({file_size_mb:.2f} MB)")
                        else:
                            logger.debug(f"Original video file already deleted: {video_file_path}")
                    except Exception as e:
                        logger.warning(f"⚠️  Could not delete original video file {video_file_path}: {e}")
                        # Will be cleaned up by background task
                else:
                    logger.warning(f"⏸️  Skipped deletion of {video_file_path} (still in use - this should not happen at job completion)")
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        update_job_status(job_id, JobStatus.FAILED, error=str(e))
        
        # Remove video file from tracking set if job failed
        try:
            if 'video_file' in locals() and video_file:
                with video_files_lock:
                    video_files_in_use.discard(video_file)
        except:
            pass
        
        # Rollback any pending transaction before attempting to update job status
        try:
            db.rollback()
        except:
            pass
        
        # Update job status in database and refund credits if video download failed
        try:
            db_job = db.query(Job).filter(Job.job_id == job_id).first()
            if db_job:
                db_job.status = JobStatus.FAILED
                
                # Refund credits if video download failed (video_downloaded is "no" or "failed")
                # IMPORTANT: Only refund the credits for THIS specific job, not all credits ever spent
                # Each job tracks its own credits_deducted amount (typically 15 for watermark removal)
                if db_job.credits_deducted > 0 and db_job.video_downloaded in ["no", "failed"]:
                    # Get user and refund ONLY the credits for this specific job
                    user = db.query(User).filter(User.id == db_job.user_id).first()
                    if user:
                        # Refund only the amount deducted for this specific job
                        refund_amount = db_job.credits_deducted
                        previous_balance = user.credit
                        user.credit += refund_amount
                        db_job.credits_deducted = 0  # Reset to 0 after refund to prevent double refunding
                        logger.info(
                            f"💰 Refunded {refund_amount} credits to user {user.id} for failed job {job_id} "
                            f"(video download failed). Previous balance: {previous_balance}, "
                            f"New balance: {user.credit}. Only refunded credits for this job, not all credits."
                        )
                
                db.commit()
        except Exception as db_error:
            logger.error(f"Failed to update job status in database: {db_error}")
            try:
                db.rollback()
            except:
                pass
        
        # Don't re-raise the exception - we've already handled it by updating job status
        # This prevents the exception from propagating to the ASGI layer
        # The job status is already set to FAILED, so the client can check the status
        if should_close_db:
            db.close()
        return  # Exit gracefully instead of raising
    finally:
        if should_close_db and db:
            db.close()


# API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Reel Generator API",
        "version": "1.0.0",
        "endpoints": {
            "POST /api/auth/register": "Register new user",
            "POST /api/auth/login": "Login",
            "GET /api/auth/me": "Get current user info",
            "POST /api/process": "Start video processing (requires auth)",
            "GET /api/job/{job_id}": "Get job status",
            "GET /api/job/{job_id}/download/{file_type}": "Download generated files",
            "GET /api/jobs": "List all jobs (requires auth)"
        }
    }


@app.post("/api/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user"""
    # Validate email and password
    if not request.email or not request.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    
    if len(request.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    # Hash email and check if user exists
    email_hash = hash_email(request.email)
    existing_user = db.query(User).filter(User.email_hash == email_hash).first()
    
    if existing_user:
        # User exists - if it's an OAuth-only user (no password), add password to link accounts
        if not existing_user.password_hash:
            # OAuth user wants to add password - link the accounts
            existing_user.password_hash = hash_password(request.password)
            existing_user.auth_provider = None  # Can use both methods now
            db.commit()
            db.refresh(existing_user)
            logger.info(f"Password added to existing OAuth user: {existing_user.id}")
            token = create_access_token(str(existing_user.id))
            return AuthResponse(user_id=str(existing_user.id), token=token)
        else:
            # User already has password - email already registered
            raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    password_hash = hash_password(request.password)
    new_user = User(
        email_hash=email_hash,
        password_hash=password_hash,
        auth_provider=None,  # Regular email/password auth
        google_id=None,
        credit=0,
        total_minutes_downloaded=0.0
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create access token
    token = create_access_token(str(new_user.id))
    
    logger.info(f"New user registered: {new_user.id}")
    
    return AuthResponse(user_id=str(new_user.id), token=token)


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login user"""
    if not request.email or not request.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    
    # Hash email and find user
    email_hash = hash_email(request.email)
    user = db.query(User).filter(User.email_hash == email_hash).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Check if user has password (not OAuth-only user)
    if not user.password_hash:
        raise HTTPException(status_code=401, detail="This account uses Google login. Please use Google sign-in instead.")
    
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Create access token
    token = create_access_token(str(user.id))
    
    logger.info(f"User logged in: {user.id}")
    
    return AuthResponse(user_id=str(user.id), token=token)


@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return UserResponse(
        user_id=str(current_user.id),
        credit=current_user.credit,
        total_minutes_downloaded=current_user.total_minutes_downloaded
    )


# Google OAuth endpoints
@app.get("/api/auth/google/login")
async def google_login(request: Request):
    """Initiate Google OAuth login - redirects to Google"""
    from google_auth_oauthlib.flow import Flow
    from google.oauth2 import id_token
    from google.auth.transport import requests
    
    # Google OAuth configuration
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
    
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.")
    
    # Create OAuth flow
    # Include YouTube scopes in case user has already authorized them (prevents scope mismatch errors)
    # We only actually need userinfo scopes for login, but including YouTube scopes won't hurt
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI]
            }
        },
        scopes=[
            "openid", 
            "https://www.googleapis.com/auth/userinfo.email", 
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly"
        ]
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    
    # Generate authorization URL
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    # Redirect to Google OAuth
    return RedirectResponse(url=authorization_url)


@app.get("/api/auth/google/callback")
async def google_callback(request: Request, code: str = None, state: str = None, db: Session = Depends(get_db)):
    """Handle Google OAuth callback"""
    from google_auth_oauthlib.flow import Flow
    from google.oauth2 import id_token
    from google.auth.transport import requests
    
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    
    if not code:
        return RedirectResponse(url=f"{FRONTEND_URL}/login?error=oauth_failed")
    
    try:
        # Create OAuth flow
        # Include YouTube scopes in case user has already authorized them (prevents scope mismatch errors)
        # We only actually need userinfo scopes for login, but including YouTube scopes won't hurt
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GOOGLE_REDIRECT_URI]
                }
            },
            scopes=[
                "openid", 
                "https://www.googleapis.com/auth/userinfo.email", 
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ]
        )
        flow.redirect_uri = GOOGLE_REDIRECT_URI
        
        # Exchange authorization code for tokens
        credentials = None
        try:
            flow.fetch_token(code=code)
            credentials = flow.credentials
        except (ValueError, Exception) as e:
            error_str = str(e)
            # Ignore scope mismatch errors - we only need the ID token for authentication
            # Google may return additional scopes (like YouTube) that we didn't request,
            # but we don't need them for user login
            if "Scope has changed" in error_str or "scope" in error_str.lower():
                logger.warning(f"Scope mismatch detected, attempting direct token exchange: {e}")
                # Don't try to get credentials from flow - it will fail due to scope mismatch
                # Go directly to token exchange
                credentials = None
                
                # Fetch token directly from Google, bypassing Flow's scope validation
                try:
                    # Get token directly from Google, bypassing Flow's scope validation
                    import requests as http_requests
                    
                    token_url = "https://oauth2.googleapis.com/token"
                    token_data = {
                        "code": code,
                        "client_id": GOOGLE_CLIENT_ID,
                        "client_secret": GOOGLE_CLIENT_SECRET,
                        "redirect_uri": GOOGLE_REDIRECT_URI,
                        "grant_type": "authorization_code"
                    }
                    
                    logger.info("Attempting direct token exchange with Google (bypassing scope validation)...")
                    response = http_requests.post(token_url, data=token_data, timeout=10)
                    
                    if response.status_code == 200:
                        token_response = response.json()
                        id_token_str = token_response.get("id_token")
                        
                        if id_token_str:
                            # Create a minimal credentials object with just the ID token
                            # We only need the ID token for user authentication
                            from google.oauth2.credentials import Credentials
                            credentials = Credentials(
                                token=token_response.get("access_token"),
                                refresh_token=token_response.get("refresh_token"),
                                id_token=id_token_str,
                                token_uri=token_url,
                                client_id=GOOGLE_CLIENT_ID,
                                client_secret=GOOGLE_CLIENT_SECRET
                            )
                            logger.info("✅ Successfully obtained credentials via direct token exchange")
                        else:
                            logger.error("Token response did not include id_token")
                            logger.debug(f"Token response keys: {list(token_response.keys()) if isinstance(token_response, dict) else 'not a dict'}")
                    else:
                        error_text = response.text[:500] if response.text else "No error message"
                        logger.error(f"Token exchange failed with status {response.status_code}: {error_text}")
                except Exception as retry_error:
                    logger.error(f"Failed to get credentials via direct exchange: {retry_error}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    # Will fall through to error handling below
            else:
                # Re-raise if it's not a scope error
                raise
        
        # Get user info from ID token
        if not credentials or not credentials.id_token:
            logger.error("Failed to get credentials from OAuth flow")
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=oauth_failed")
        
        request_obj = requests.Request()
        try:
            id_info = id_token.verify_oauth2_token(credentials.id_token, request_obj, GOOGLE_CLIENT_ID)
        except ValueError as e:
            logger.error(f"Token verification failed: {e}")
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=oauth_failed")
        
        # Extract user information
        google_id = id_info.get("sub")
        email = id_info.get("email")
        
        if not email:
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=no_email")
        
        # Hash email and check if user exists
        email_hash = hash_email(email)
        user = db.query(User).filter(User.email_hash == email_hash).first()
        
        if not user:
            # Create new user for Google OAuth
            user = User(
                email_hash=email_hash,
                password_hash=None,  # No password for OAuth users
                auth_provider="google",
                google_id=google_id,
                credit=0,
                total_minutes_downloaded=0.0
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"New Google OAuth user registered: {user.id}")
        else:
            # User exists - link Google account if not already linked
            if not user.google_id:
                user.google_id = google_id
                # If user already has password, they can use both methods
                # Only set auth_provider to "google" if they don't have password
                if not user.password_hash:
                    user.auth_provider = "google"
                else:
                    # User has both methods now - keep auth_provider as None
                    user.auth_provider = None
                db.commit()
            logger.info(f"Google OAuth user logged in: {user.id} (existing user)")
        
        # Create access token
        token = create_access_token(str(user.id))
        
        # Redirect to frontend with token
        return RedirectResponse(url=f"{FRONTEND_URL}/login?token={token}&provider=google")
        
    except Exception as e:
        error_str = str(e)
        # Don't log scope errors as errors - they're handled above
        if "Scope has changed" in error_str or "scope" in error_str.lower():
            logger.warning(f"Google OAuth scope issue (handled): {e}")
        else:
            logger.error(f"Google OAuth callback error: {e}")
        return RedirectResponse(url=f"{FRONTEND_URL}/login?error=oauth_failed")


# Payment endpoints
@app.post("/api/payment/purchase", response_model=PaymentResponse)
async def purchase_credits(
    request: PaymentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Purchase credits for the current user.
    
    Note: This is a simplified payment endpoint. In production, you should:
    1. Integrate with a payment provider (Stripe, PayPal, etc.)
    2. Verify payment before adding credits
    3. Store transaction records
    4. Handle payment webhooks
    """
    # Validate payment plans
    valid_plans = {
        60: 5.99,    # 60 credits for €5.99 (entry level)
        200: 14.99,  # 200 credits for €14.99 (best value - target purchase)
        700: 49.99   # 700 credits for €49.99 (decoy - makes €14.99 look great)
    }
    
    if request.credits not in valid_plans:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid credit amount. Valid options: {list(valid_plans.keys())}"
        )
    
    expected_amount = valid_plans[request.credits]
    if abs(request.amount - expected_amount) > 0.01:  # Allow small floating point differences
        raise HTTPException(
            status_code=400,
            detail=f"Invalid amount. Expected €{expected_amount} for {request.credits} credits"
        )
    
    if request.currency.upper() != "EUR":
        raise HTTPException(
            status_code=400,
            detail="Only EUR currency is supported"
        )
    
    try:
        # Update user credits and last purchase timestamp
        current_user.credit += request.credits
        current_user.last_credit_purchase = datetime.utcnow()
        db.commit()
        
        logger.info(
            f"User {current_user.id} purchased {request.credits} credits. "
            f"New balance: {current_user.credit}. Last purchase: {current_user.last_credit_purchase}"
        )
        
        return PaymentResponse(
            success=True,
            message=f"Successfully purchased {request.credits} credits",
            credits_added=request.credits,
            new_balance=current_user.credit
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Error processing payment for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to process payment. Please try again."
        )


@app.post("/api/process")
async def process_video(
    request: VideoProcessRequest, 
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start video processing job from a direct video URL (requires authentication)"""
    if not request.youtube_url:
        raise HTTPException(status_code=400, detail="youtube_url is required")

    if is_youtube_url(request.youtube_url):
        raise HTTPException(
            status_code=400,
            detail="YouTube URLs are not supported for server-side downloads. Please upload the file or provide a direct video file URL."
        )
    
    # Refresh user to get latest data
    db.refresh(current_user)
    
    required_credits = 0
    
    # Check watermark removal requirements
    if request.remove_watermark:
        # Without watermark: always requires credits >= 15
        required_credits = WATERMARK_REMOVAL_COST  # Flat 15 credits per package
        
        # Check if user has enough credits
        if current_user.credit < required_credits:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient credits for watermark removal. Required: {required_credits} credits per package of reels. You have {current_user.credit} credits."
            )
        
        # Deduct credits immediately
        current_user.credit -= required_credits
        if current_user.credit < 0:
            current_user.credit = 0  # Prevent negative credits
        db.commit()
        logger.info(f"Deducted {required_credits} credits from user {current_user.id} for watermark removal package. Remaining credits: {current_user.credit}")
    else:
        # With watermark: check if user can generate (total < 50 mins OR paid < 1 month ago)
        if not can_generate_with_watermark(current_user):
            raise HTTPException(
                status_code=403,
                detail=f"You cannot generate videos with watermark. You need to have less than {FREE_MINUTES_LIMIT} minutes total OR have purchased credits within the last month. Please purchase credits to continue generating reels."
            )
    
    try:
        job_id = create_job()
        logger.info(f"Created job {job_id} for URL: {request.youtube_url} by user {current_user.id}")
        
        # Create job in database with credit tracking
        db_job = Job(
            user_id=current_user.id,
            job_id=job_id,
            youtube_url=request.youtube_url,
            status=JobStatus.PENDING,
            credits_deducted=required_credits if request.remove_watermark else 0,
            video_downloaded="no"  # Track if video was successfully downloaded
        )
        db.add(db_job)
        db.commit()
        
        # Start processing in background with concurrency control
        background_tasks.add_task(
            process_video_job_async, 
            job_id, 
            request.youtube_url, 
            request.min_clips, 
            str(current_user.id), 
            request.quality, 
            None, 
            request.remove_watermark,
            request.video_types or "both",
            request.font_style,  # Use UI value directly, no default
            request.font_color  # Use UI value directly, no default
        )
        
        return {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "message": "Video processing started"
        }
    except Exception as e:
        logger.error(f"Error starting job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/process/upload")
async def process_video_upload(
    video_file: UploadFile = File(...),
    quality: str = Form("hd"),
    min_clips: Optional[int] = Form(None),
    remove_watermark: Optional[bool] = Form(False),
    video_types: Optional[str] = Form("both"),
    font_style: Optional[str] = Form(None),
    font_color: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start video processing job from uploaded file (requires authentication)"""
    # Refresh user to get latest data
    db.refresh(current_user)
    
    required_credits = 0
    
    # Check watermark removal requirements
    if remove_watermark:
        # Without watermark: always requires credits >= 15
        required_credits = WATERMARK_REMOVAL_COST  # Flat 15 credits per package
        
        # Check if user has enough credits
        if current_user.credit < required_credits:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient credits for watermark removal. Required: {required_credits} credits per package of reels. You have {current_user.credit} credits."
            )
        
        # Deduct credits immediately
        current_user.credit -= required_credits
        if current_user.credit < 0:
            current_user.credit = 0  # Prevent negative credits
        db.commit()
        logger.info(f"Deducted {required_credits} credits from user {current_user.id} for watermark removal package. Remaining credits: {current_user.credit}")
    else:
        # With watermark: check if user can generate (total < 50 mins OR paid < 1 month ago)
        if not can_generate_with_watermark(current_user):
            raise HTTPException(
                status_code=403,
                detail=f"You cannot generate videos with watermark. You need to have less than {FREE_MINUTES_LIMIT} minutes total OR have purchased credits within the last month. Please purchase credits to continue generating reels."
            )
    
    try:
        # Validate file type
        if not video_file.content_type or not video_file.content_type.startswith('video/'):
            raise HTTPException(status_code=400, detail="File must be a video")
        
        job_id = create_job()
        logger.info(f"Created job {job_id} for uploaded file: {video_file.filename} by user {current_user.id}")
        
        # Save uploaded file
        filename = f"job_{job_id}_video.mp4"
        file_path = filename
        
        # Read and save file
        with open(file_path, "wb") as f:
            content = await video_file.read()
            f.write(content)
        
        logger.info(f"Saved uploaded file: {file_path} ({len(content) / 1024 / 1024:.2f} MB)")
        
        # Create job in database with credit tracking
        # For uploads, video is already "downloaded" (uploaded), so mark as "yes"
        db_job = Job(
            user_id=current_user.id,
            job_id=job_id,
            youtube_url=None,  # No URL for uploads
            status=JobStatus.PENDING,
            credits_deducted=required_credits if remove_watermark else 0,
            video_downloaded="yes"  # Uploaded files are already available
        )
        db.add(db_job)
        db.commit()
        
        # Start processing with uploaded file
        background_tasks.add_task(
            process_video_job_async, 
            job_id, 
            None,  # No URL
            min_clips, 
            str(current_user.id), 
            quality,
            file_path,  # Pass file path directly
            remove_watermark,
            video_types or "both",
            font_style,  # Use UI value directly, no default
            font_color  # Use UI value directly, no default
        )
        
        return {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "message": "Video processing started"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting upload job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/process/distributed")
async def process_distributed(
    request: DistributedProcessRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Process video with distributed server-client approach based on client RAM"""
    if not request.video_url:
        raise HTTPException(status_code=400, detail="video_url is required")

    if is_youtube_url(request.video_url):
        raise HTTPException(
            status_code=400,
            detail="YouTube URLs are not supported for server-side downloads. Please upload the file or provide a direct video file URL."
        )
    
    # Refresh user to get latest data
    db.refresh(current_user)
    
    required_credits = 0
    
    # Check watermark removal requirements
    if request.remove_watermark:
        # Without watermark: always requires credits >= 15
        required_credits = WATERMARK_REMOVAL_COST  # Flat 15 credits per package
        
        if current_user.credit < required_credits:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient credits for watermark removal. Required: {required_credits} credits per package of reels. You have {current_user.credit} credits."
            )
        
        # Deduct credits immediately
        current_user.credit -= required_credits
        if current_user.credit < 0:
            current_user.credit = 0
        db.commit()
        logger.info(f"Deducted {required_credits} credits from user {current_user.id} for watermark removal package. Remaining credits: {current_user.credit}")
    else:
        # With watermark: check if user can generate (total < 50 mins OR paid < 1 month ago)
        if not can_generate_with_watermark(current_user):
            raise HTTPException(
                status_code=403,
                detail=f"You cannot generate videos with watermark. You need to have less than {FREE_MINUTES_LIMIT} minutes total OR have purchased credits within the last month. Please purchase credits to continue generating reels."
            )
    
    # Log client capabilities
    client_ram_mb = request.client_capabilities.get('max_ram_mb', 0)
    client_max_clips = request.client_capabilities.get('max_clips', 0)
    can_process = request.client_capabilities.get('can_process', False)
    
    logger.info(
        f"Distributed processing - Client capabilities: {client_ram_mb:.2f} MB RAM, "
        f"can process {client_max_clips} clips, client processing: {can_process}"
    )
    
    try:
        job_id = create_job()
        logger.info(f"Created distributed job {job_id} for URL: {request.video_url} by user {current_user.id}")
        
        # Create job in database with credit tracking
        db_job = Job(
            user_id=current_user.id,
            job_id=job_id,
            youtube_url=request.video_url,
            status=JobStatus.PENDING,
            credits_deducted=required_credits if request.remove_watermark else 0,
            video_downloaded="no"  # Track if video was successfully downloaded
        )
        db.add(db_job)
        db.commit()
        
        # Store distribution info in job status for reference
        update_job_status(job_id, JobStatus.PENDING, progress={
            "stage": "distributed",
            "message": "Distributed processing initialized",
            "percentage": 5,
            "client_capabilities": request.client_capabilities,
            "distribution": request.distribution
        })
        
        # Start processing with distribution info
        # The server will process its assigned tasks
        background_tasks.add_task(
            process_video_job_async,
            job_id,
            request.video_url,
            None,  # min_clips
            str(current_user.id),
            request.quality,
            None,  # video_file_path
            request.remove_watermark,
            request.video_types or "both",
            request.font_style,  # Use UI value directly, no default
            request.font_color  # Use UI value directly, no default
        )
        
        return {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "message": "Distributed video processing started",
            "distribution": request.distribution,
            "client_tasks": request.distribution.get("client", {})
        }
    except Exception as e:
        logger.error(f"Error starting distributed job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/job/{job_id}/client-ready")
async def client_ready(
    job_id: str,
    request: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Endpoint for client to notify server that it's ready to process tasks"""
    # Verify job belongs to user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    client_tasks = request.get("client_tasks", [])
    ram_available_mb = request.get("ram_available_mb", 0)
    
    logger.info(
        f"Client ready for job {job_id}: {len(client_tasks)} tasks, "
        f"{ram_available_mb:.2f} MB RAM available"
    )
    
    # Update job status to indicate client is assisting
    update_job_status(job_id, JobStatus.PROCESSING, progress={
        "stage": "client_processing",
        "message": f"Client processing {len(client_tasks)} clips...",
        "percentage": 30,
        "client_ram_mb": ram_available_mb
    })
    
    return {
        "status": "acknowledged",
        "message": "Client processing acknowledged"
    }


@app.get("/api/job/{job_id}/video-chunk")
async def get_video_chunk(
    job_id: str,
    start_time: float,
    end_time: float,
    quality: str = "hd",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stream a video chunk for client-side processing"""
    # Verify job belongs to user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Find the base video file
    # Look for video file in current directory with job_id pattern
    import glob
    video_files = glob.glob(f"job_{job_id}_video.*")
    if not video_files:
        # Try alternative pattern
        video_files = glob.glob(f"*{job_id}*.mp4")
    
    if not video_files:
        raise HTTPException(status_code=404, detail="Video file not found for this job")
    
    video_file = video_files[0]
    
    try:
        # Use ffmpeg to extract chunk
        import subprocess
        import tempfile
        import io
        
        # Create temporary output file
        temp_output = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        temp_output.close()
        
        duration = end_time - start_time
        
        # Build ffmpeg command
        ffmpeg_cmd = [
            "ffmpeg",
            "-i", video_file,
            "-ss", str(start_time),
            "-t", str(duration),
            "-c", "copy",  # Copy codec (fast, no re-encoding)
            "-avoid_negative_ts", "make_zero",
            temp_output.name
        ]
        
        # Run ffmpeg
        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Read chunk file
        with open(temp_output.name, "rb") as f:
            chunk_data = f.read()
        
        # Clean up temp file
        os.unlink(temp_output.name)
        
        # Return chunk as streaming response
        return StreamingResponse(
            io.BytesIO(chunk_data),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f"attachment; filename=chunk_{start_time}_{end_time}.mp4",
                "Content-Length": str(len(chunk_data))
            }
        )
        
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error extracting chunk: {e.stderr}")
        raise HTTPException(status_code=500, detail=f"Error extracting video chunk: {e.stderr}")
    except Exception as e:
        logger.error(f"Error getting video chunk: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/job/{job_id}/upload-clip")
async def upload_clip(
    job_id: str,
    clip_file: UploadFile = File(...),
    clip_type: str = Form(...),
    clip_start: str = Form(...),
    clip_end: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Receive processed clip from client"""
    # Verify job belongs to user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    try:
        # Determine output filename based on clip type
        clip_start_float = float(clip_start)
        clip_end_float = float(clip_end)
        
        if clip_type == "medium":
            # Find next medium reel index
            import glob
            existing_medium = glob.glob("medium_reel_*.mp4")
            index = len(existing_medium)
            filename = f"medium_reel_{index}.mp4"
        elif clip_type == "short":
            # Find next short reel index
            import glob
            existing_short = glob.glob("short_reel_*.mp4")
            index = len(existing_short)
            filename = f"short_reel_{index}.mp4"
        else:
            raise HTTPException(status_code=400, detail=f"Invalid clip_type: {clip_type}")
        
        # Save uploaded file
        with open(filename, "wb") as f:
            content = await clip_file.read()
            f.write(content)
        
        logger.info(
            f"Received client-processed clip for job {job_id}: {filename} "
            f"({clip_type}, {clip_start_float}s-{clip_end_float}s, {len(content) / 1024 / 1024:.2f} MB)"
        )
        
        # Create JobFile entry
        from database import JobFile
        job_file = JobFile(
            job_id=db_job.id,
            file_path=filename,
            file_type=clip_type,
            metadata={"start": clip_start_float, "end": clip_end_float}
        )
        db.add(job_file)
        db.commit()
        
        return {
            "status": "success",
            "message": f"Clip uploaded successfully",
            "filename": filename,
            "clip_type": clip_type
        }
        
    except Exception as e:
        logger.error(f"Error uploading clip: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/job/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get job status and progress (requires authentication, only own jobs)"""
    # Check if job belongs to current user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get progress from in-memory jobs if available
    if job_id in jobs:
        job = jobs[job_id]
        return JobStatusResponse(
            job_id=job_id,
            status=job["status"],
            progress=job.get("progress"),
            error=job.get("error"),
            created_at=job["created_at"],
            completed_at=job.get("completed_at"),
            results=job.get("results"),
            video_downloaded=db_job.video_downloaded if db_job else None
        )
    else:
        # Job not in memory, return from database
        # Build results from database files
        results = {}
        job_files = db.query(JobFile).filter(JobFile.job_id == db_job.id).all()
        
        for job_file in job_files:
            if job_file.file_type == "main_reel":
                results["main_reel"] = job_file.file_path
            elif job_file.file_type == "medium_reel":
                if "medium_reels" not in results:
                    results["medium_reels"] = []
                results["medium_reels"].append(job_file.file_path)
            elif job_file.file_type == "short_reel":
                if "short_reels" not in results:
                    results["short_reels"] = []
                results["short_reels"].append(job_file.file_path)
            elif job_file.file_type == "metadata":
                results["metadata"] = {"file": job_file.file_path}
        
        return JobStatusResponse(
            job_id=job_id,
            status=db_job.status,
            progress=None,
            error=None,
            created_at=db_job.created_at.isoformat() if db_job.created_at else datetime.now().isoformat(),
            completed_at=db_job.completed_at.isoformat() if db_job.completed_at else None,
            results=results if results else None,
            video_downloaded=db_job.video_downloaded
        )


def get_original_video_file(db, db_job, video_type: str, video_index: Optional[int] = None):
    """
    Get the original video file (not edited).
    
    Args:
        db: Database session
        db_job: Job database object
        video_type: Type of video ('main_reel', 'medium_reel', 'short_reel')
        video_index: Index of the video (for medium_reel and short_reel)
    
    Returns:
        Tuple of (file_path, file_name, duration_minutes) or (None, None, None)
    """
    if video_type == "main_reel":
        job_file = db.query(JobFile).filter(
            JobFile.job_id == db_job.id,
            JobFile.file_type == "main_reel"
        ).first()
        if job_file:
            return (job_file.file_path, os.path.basename(job_file.file_path), job_file.duration_minutes)
    elif video_index is not None:
        # For indexed videos, get by index
        job_files = db.query(JobFile).filter(
            JobFile.job_id == db_job.id,
            JobFile.file_type == video_type
        ).order_by(JobFile.created_at).all()
        if 0 <= video_index < len(job_files):
            job_file = job_files[video_index]
            return (job_file.file_path, os.path.basename(job_file.file_path), job_file.duration_minutes)
    
    return (None, None, None)


def get_edited_video_file(db, db_job, video_type: str, video_index: Optional[int] = None):
    """
    Get the edited video file for a specific original video.
    
    Args:
        db: Database session
        db_job: Job database object
        video_type: Type of video ('main_reel', 'medium_reel', 'short_reel')
        video_index: Index of the video (for medium_reel and short_reel)
    
    Returns:
        Tuple of (file_path, file_name, duration_minutes) or (None, None, None)
    """
    edited_file_type = f"{video_type}_edited"
    edited_job_files = db.query(JobFile).filter(
        JobFile.job_id == db_job.id,
        JobFile.file_type == edited_file_type
    ).order_by(JobFile.created_at.desc()).all()  # Most recent first
    
    logger.debug(f"Found {len(edited_job_files)} edited files of type {edited_file_type} for job {db_job.job_id}")
    
    matching_edited_file = None
    
    if video_index is not None:
        # For indexed videos, find edited version matching the index
        for jf in edited_job_files:
            try:
                # Check if file_metadata exists
                if not jf.file_metadata:
                    logger.debug(f"Skipping file {jf.id} - no file_metadata")
                    continue
                
                # Handle both string and dict metadata
                if isinstance(jf.file_metadata, str):
                    metadata_dict = json.loads(jf.file_metadata)
                elif isinstance(jf.file_metadata, dict):
                    metadata_dict = jf.file_metadata
                else:
                    logger.debug(f"Skipping file {jf.id} - file_metadata is not string or dict: {type(jf.file_metadata)}")
                    continue
                
                orig_index = metadata_dict.get('original_video_index')
                orig_type = metadata_dict.get('original_video_type')
                # Convert to int for comparison (JSON might store as int or string)
                orig_index_int = int(orig_index) if orig_index is not None else None
                logger.debug(f"Checking edited file: orig_type={orig_type}, orig_index={orig_index} (as int: {orig_index_int}), target_index={video_index}, path={os.path.basename(jf.file_path)}")
                if orig_index_int is not None and orig_index_int == video_index and orig_type == video_type:
                    matching_edited_file = jf
                    logger.info(f"✅ Found matching edited file: {os.path.basename(jf.file_path)}")
                    break
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing file_metadata for edited file {jf.id}: {e}, file_metadata type: {type(getattr(jf, 'file_metadata', None))}")
                continue
            except Exception as e:
                logger.warning(f"Unexpected error parsing metadata for edited file {jf.id}: {e}")
                continue
        if not matching_edited_file:
            logger.warning(f"No edited file found matching video_type={video_type}, video_index={video_index} (checked {len(edited_job_files)} files)")
    else:
        # For main_reel, get the most recent edited version
        if edited_job_files:
            matching_edited_file = edited_job_files[0]
            logger.info(f"Found edited main_reel: {os.path.basename(matching_edited_file.file_path)}")
    
    if matching_edited_file:
        # Ensure we have an absolute path
        file_path = matching_edited_file.file_path
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        
        if not os.path.exists(file_path):
            logger.error(f"Edited file path does not exist: {file_path} (original: {matching_edited_file.file_path})")
            return (None, None, None)
        
        logger.info(f"✅ Returning edited file: {os.path.basename(file_path)}")
        return (
            file_path,
            os.path.basename(file_path),
            matching_edited_file.duration_minutes
        )
    
    return (None, None, None)


def get_video_file_with_edited_preference(db, db_job, video_type: str, video_index: Optional[int] = None):
    """
    Get a video file, preferring edited versions over originals.
    This ensures that whenever a video is displayed, the most recent edited version is shown.
    
    Args:
        db: Database session
        db_job: Job database object
        video_type: Type of video ('main_reel', 'medium_reel', 'short_reel')
        video_index: Index of the video (for medium_reel and short_reel)
    
    Returns:
        Tuple of (file_path, file_name, duration_minutes, is_edited) or (None, None, None, False)
    """
    # First try to get edited version
    edited_path, edited_name, edited_duration = get_edited_video_file(db, db_job, video_type, video_index)
    if edited_path:
        return (edited_path, edited_name, edited_duration, True)
    
    # Otherwise get original
    orig_path, orig_name, orig_duration = get_original_video_file(db, db_job, video_type, video_index)
    if orig_path:
        return (orig_path, orig_name, orig_duration, False)
    
    return (None, None, None, False)


@app.get("/api/job/{job_id}/download/{file_type}")
async def download_file(
    job_id: str, 
    file_type: str,
    download: bool = False,  # Query parameter: true for actual downloads, false for streaming/viewing
    original: bool = False,  # Query parameter: true to force return original (not edited) version
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Download generated files (requires authentication, only own jobs)
    
    By default, this endpoint checks for edited versions first and returns them if available.
    Set original=true to force return the original version.
    
    Args:
        download: If True, counts as an actual download and updates user's total minutes.
                  If False (default), only streams the file for viewing without counting.
        original: If True, returns the original version even if edited version exists.
    """
    # Check if job belongs to current user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if db_job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Job not completed yet")
    
    # Try to get file from database first
    file_path = None
    file_name = None
    duration_minutes = None
    
    # Determine which file to get based on file_type
    if file_type == "metadata":
        job_file = db.query(JobFile).filter(
            JobFile.job_id == db_job.id,
            JobFile.file_type == "metadata"
        ).first()
        if job_file:
            file_path = job_file.file_path
            file_name = "metadata.txt"
    elif file_type == "main_reel" or file_type == "main_reel_edited":
        if file_type == "main_reel_edited":
            # Get edited version
            file_path, file_name, duration_minutes = get_edited_video_file(db, db_job, "main_reel", None)
        elif original:
            # Get original version
            file_path, file_name, duration_minutes = get_original_video_file(db, db_job, "main_reel", None)
        else:
            # Default: prefer edited
            file_path, file_name, duration_minutes, _ = get_video_file_with_edited_preference(
                db, db_job, "main_reel", None
            )
    elif file_type.startswith("medium_reel_"):
        if file_type.startswith("medium_reel_edited_"):
            # Get edited version - parse index from end (e.g., "medium_reel_edited_0" -> 0)
            index = int(file_type.split("_")[-1])
            logger.debug(f"Requesting edited medium_reel_{index}, file_type={file_type}")
            file_path, file_name, duration_minutes = get_edited_video_file(db, db_job, "medium_reel", index)
            if not file_path:
                logger.warning(f"No edited file found for medium_reel_{index}")
        else:
            # Regular medium_reel request (e.g., "medium_reel_0")
            index = int(file_type.split("_")[-1])
            if original:
                # Get original version
                file_path, file_name, duration_minutes = get_original_video_file(db, db_job, "medium_reel", index)
            else:
                # Default: prefer edited
                file_path, file_name, duration_minutes, _ = get_video_file_with_edited_preference(
                    db, db_job, "medium_reel", index
                )
            if not file_path:
                logger.warning(f"No file found for medium_reel_{index}")
    elif file_type.startswith("short_reel_"):
        if file_type.startswith("short_reel_edited_"):
            # Get edited version - parse index from end (e.g., "short_reel_edited_0" -> 0)
            index = int(file_type.split("_")[-1])
            logger.debug(f"Requesting edited short_reel_{index}, file_type={file_type}")
            file_path, file_name, duration_minutes = get_edited_video_file(db, db_job, "short_reel", index)
            if not file_path:
                logger.warning(f"No edited file found for short_reel_{index}")
        else:
            # Regular short_reel request (e.g., "short_reel_0")
            index = int(file_type.split("_")[-1])
            if original:
                # Get original version
                file_path, file_name, duration_minutes = get_original_video_file(db, db_job, "short_reel", index)
            else:
                # Default: prefer edited
                file_path, file_name, duration_minutes, _ = get_video_file_with_edited_preference(
                    db, db_job, "short_reel", index
                )
            if not file_path:
                logger.warning(f"No file found for short_reel_{index}")
    
    # Fallback to in-memory jobs if not found in database
    if not file_path and job_id in jobs:
        job = jobs[job_id]
        results = job.get("results")
        if results:
            if file_type == "main_reel":
                file_path = results.get("main_reel") or f"job_{job_id}_reel.mp4"
                file_name = "reel.mp4"
            elif file_type == "metadata":
                metadata_info = results.get("metadata")
                if isinstance(metadata_info, dict):
                    file_path = metadata_info.get("file") or f"job_{job_id}_metadata.txt"
                else:
                    file_path = metadata_info or f"job_{job_id}_metadata.txt"
                file_name = "metadata.txt"
            elif file_type.startswith("medium_reel_"):
                index = int(file_type.split("_")[-1])
                medium_reels = results.get("medium_reels", [])
                if 0 <= index < len(medium_reels):
                    file_path = medium_reels[index]
                    file_name = os.path.basename(file_path)
            elif file_type.startswith("short_reel_"):
                index = int(file_type.split("_")[-1])
                short_reels = results.get("short_reels", [])
                if 0 <= index < len(short_reels):
                    file_path = short_reels[index]
                    file_name = os.path.basename(file_path)
    
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found in database")
    
    if not os.path.exists(file_path):
        # File was likely cleaned up by background task
        raise HTTPException(
            status_code=404, 
            detail=f"File no longer available. It may have been automatically cleaned up after 1 day. File path: {file_path}"
        )
    
    # Update user's total minutes downloaded ONLY if this is an actual download (not streaming/viewing)
    if download and duration_minutes is not None and duration_minutes > 0:
        current_user.total_minutes_downloaded += duration_minutes
        db.commit()
        logger.info(f"Updated user {current_user.id} total minutes: {current_user.total_minutes_downloaded} (added {duration_minutes:.2f} minutes from {file_type})")
    
    # Determine correct media type using mimetypes module
    import mimetypes
    mimetypes.init()
    media_type, _ = mimetypes.guess_type(file_path)
    
    # Fallback to video/mp4 for .mp4 files if mimetypes can't determine
    if not media_type and file_path.endswith(".mp4"):
        media_type = "video/mp4"
    elif not media_type and file_path.endswith(".txt"):
        media_type = "text/plain"
    elif not media_type:
        media_type = "application/octet-stream"
    
    # For video files, use StreamingResponse to allow HTML5 video player streaming
    if file_path.endswith((".mp4", ".webm", ".avi", ".mov", ".mkv")):
        file_size = os.path.getsize(file_path)
        
        def iterfile():
            with open(file_path, mode="rb") as file_like:
                yield from file_like
        
        # Set Content-Disposition based on whether it's a download or stream
        content_disposition = f'attachment; filename="{file_name}"' if download else f'inline; filename="{file_name}"'
        
        return StreamingResponse(
            iterfile(),
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Disposition": content_disposition,
                "Content-Type": media_type
            }
        )
    else:
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=file_name,
            headers={
                "Content-Type": media_type
            }
        )


@app.post("/api/job/{job_id}/edit")
async def edit_video(
    job_id: str,
    request: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Apply edits to a video (text overlays, stickers, filters)"""
    # Verify job belongs to user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if db_job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Job not completed yet")
    
    video_type = request.get('video_type')
    video_index = request.get('video_index')
    edits = request.get('edits', {})
    
    try:
        # Get the video file path
        file_path = None
        
        if video_type == "main_reel":
            job_file = db.query(JobFile).filter(
                JobFile.job_id == db_job.id,
                JobFile.file_type == "main_reel"
            ).first()
            if job_file:
                file_path = job_file.file_path
        elif video_type == "medium_reel" and video_index is not None:
            job_files = db.query(JobFile).filter(
                JobFile.job_id == db_job.id,
                JobFile.file_type == "medium_reel"
            ).order_by(JobFile.created_at).all()
            if 0 <= video_index < len(job_files):
                file_path = job_files[video_index].file_path
        elif video_type == "short_reel" and video_index is not None:
            job_files = db.query(JobFile).filter(
                JobFile.job_id == db_job.id,
                JobFile.file_type == "short_reel"
            ).order_by(JobFile.created_at).all()
            if 0 <= video_index < len(job_files):
                file_path = job_files[video_index].file_path
        
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Video file not found")
        
        # Create output filename
        base_name = os.path.splitext(file_path)[0]
        output_path = f"{base_name}_edited_{int(time.time())}.mp4"
        
        # Apply edits
        edited_path = apply_video_edits(file_path, edits, output_path)
        
        # Ensure edited_path is absolute
        if not os.path.isabs(edited_path):
            edited_path = os.path.abspath(edited_path)
        
        # Get original file's metadata (virality_score, duration, etc.) to preserve
        original_job_file = None
        if video_type == "main_reel":
            original_job_file = db.query(JobFile).filter(
                JobFile.job_id == db_job.id,
                JobFile.file_type == "main_reel"
            ).first()
        elif video_type == "medium_reel" and video_index is not None:
            job_files = db.query(JobFile).filter(
                JobFile.job_id == db_job.id,
                JobFile.file_type == "medium_reel"
            ).order_by(JobFile.created_at).all()
            if 0 <= video_index < len(job_files):
                original_job_file = job_files[video_index]
        elif video_type == "short_reel" and video_index is not None:
            job_files = db.query(JobFile).filter(
                JobFile.job_id == db_job.id,
                JobFile.file_type == "short_reel"
            ).order_by(JobFile.created_at).all()
            if 0 <= video_index < len(job_files):
                original_job_file = job_files[video_index]
        
        # Get video duration from edited file
        try:
            from moviepy.editor import VideoFileClip
            edited_video = VideoFileClip(edited_path)
            edited_duration_minutes = edited_video.duration / 60.0
            edited_video.close()
        except Exception as e:
            logger.warning(f"Could not get duration from edited video: {e}")
            edited_duration_minutes = original_job_file.duration_minutes if original_job_file else None
        
        # Create new JobFile entry for edited video
        # Store original index and preserve metadata (virality_score, etc.)
        metadata_dict = {
            'edits': edits,
            'original_video_type': video_type,
            'original_video_index': video_index if video_index is not None else None
        }
        logger.info(f"Creating edited video entry: video_type={video_type}, video_index={video_index}, original_file={os.path.basename(file_path) if file_path else 'None'}, edited_file={os.path.basename(edited_path)}, metadata={json.dumps(metadata_dict)}")
        # Preserve virality_score from original if available
        if original_job_file:
            # Try to get virality_score from original file
            original_virality_score = original_job_file.virality_score
            if original_virality_score is not None:
                metadata_dict['virality_score'] = original_virality_score
        
        edited_job_file = JobFile(
            job_id=db_job.id,
            file_path=edited_path,
            file_type=f"{video_type}_edited",
            file_size=os.path.getsize(edited_path) if os.path.exists(edited_path) else None,
            duration_minutes=edited_duration_minutes,
            virality_score=original_job_file.virality_score if original_job_file else None,
            file_metadata=json.dumps(metadata_dict)
        )
        db.add(edited_job_file)
        db.commit()
        
        logger.info(f"Created edited video JobFile: file_type={edited_job_file.file_type}, original_index={video_index}, path={edited_path}")
        
        return {
            "status": "success",
            "message": "Video edited successfully",
            "edited_file": edited_path,
            "video_type": video_type,
            "video_index": video_index,
            "file_type": f"{video_type}_edited"
        }
        
    except Exception as e:
        logger.error(f"Error editing video: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/job/{job_id}/is_edited")
async def check_if_edited(
    job_id: str,
    video_type: str,
    video_index: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if a video has been edited (requires authentication, only own jobs)"""
    # Check if job belongs to current user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if db_job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Job not completed yet")
    
    # Check for edited version
    edited_file_type = f"{video_type}_edited"
    edited_job_files = db.query(JobFile).filter(
        JobFile.job_id == db_job.id,
        JobFile.file_type == edited_file_type
    ).order_by(JobFile.created_at.desc()).all()
    
    if video_index is not None:
        # Check if there's an edited version for this specific index
        logger.debug(f"Checking if video_type={video_type}, video_index={video_index} is edited. Found {len(edited_job_files)} edited files.")
        for jf in edited_job_files:
            try:
                # Check if file_metadata exists
                if not jf.file_metadata:
                    logger.debug(f"Skipping file {jf.id} - no file_metadata")
                    continue
                    
                # Handle both string and dict metadata
                if isinstance(jf.file_metadata, str):
                    metadata_dict = json.loads(jf.file_metadata)
                elif isinstance(jf.file_metadata, dict):
                    metadata_dict = jf.file_metadata
                else:
                    logger.debug(f"Skipping file {jf.id} - file_metadata is not string or dict: {type(jf.file_metadata)}")
                    continue
                
                # Verify that this edited video corresponds to the requested original video
                orig_index = metadata_dict.get('original_video_index')
                orig_type = metadata_dict.get('original_video_type')
                
                logger.debug(f"Checking edited file {jf.id}: orig_type={orig_type}, orig_index={orig_index}, target_type={video_type}, target_index={video_index}")
                
                if orig_index is not None and orig_type == video_type:
                    orig_index_int = int(orig_index) if orig_index is not None else None
                    if orig_index_int == video_index:
                        logger.info(f"✅ Found edited version for {video_type}_{video_index}: {os.path.basename(jf.file_path)}")
                        return {"is_edited": True, "edited_at": jf.created_at.isoformat() if jf.created_at else None}
            except Exception as e:
                logger.warning(f"Error checking metadata for edited file {jf.id}: {e}")
                continue
        logger.debug(f"No edited version found for {video_type}_{video_index}")
        return {"is_edited": False}
    else:
        # For main_reel, just check if any edited version exists
        if edited_job_files:
            return {"is_edited": True, "edited_at": edited_job_files[0].created_at.isoformat() if edited_job_files[0].created_at else None}
        return {"is_edited": False}


@app.get("/api/job/{job_id}/metadata")
async def get_metadata(
    job_id: str,
    video_type: str = "main_reel",
    video_index: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get metadata content for a specific video in a job (requires authentication, only own jobs)
    
    Args:
        job_id: The job ID
        video_type: Type of video ("main_reel", "medium_reel", "short_reel")
        video_index: Index for medium_reel or short_reel (0-based), not needed for main_reel
    """
    # Check if job belongs to current user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if db_job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Job not completed yet")
    
    # Determine metadata file type
    if video_type == "main_reel":
        metadata_file_type = "metadata_main_reel"
    elif video_type == "medium_reel":
        if video_index is None:
            raise HTTPException(status_code=400, detail="video_index required for medium_reel")
        metadata_file_type = f"metadata_medium_reel_{video_index}"
    elif video_type == "short_reel":
        if video_index is None:
            raise HTTPException(status_code=400, detail="video_index required for short_reel")
        metadata_file_type = f"metadata_short_reel_{video_index}"
    else:
        raise HTTPException(status_code=400, detail="Invalid video_type. Must be 'main_reel', 'medium_reel', or 'short_reel'")
    
    # Try to get metadata from in-memory jobs first (has parsed JSON)
    if job_id in jobs:
        job = jobs[job_id]
        results = job.get("results")
        if results:
            metadata_dict = results.get("metadata", {})
            if video_type == "main_reel" and metadata_dict.get("main_reel"):
                meta_info = metadata_dict["main_reel"]
                if isinstance(meta_info, dict):
                    # Return the full metadata object including virality_score
                    return meta_info
            elif video_type == "medium_reel" and video_index is not None and metadata_dict.get("medium_reels"):
                if video_index < len(metadata_dict["medium_reels"]):
                    meta_info = metadata_dict["medium_reels"][video_index]
                    if isinstance(meta_info, dict) and meta_info.get("content") is not None:
                        # Return the full metadata object including virality_score
                        return meta_info
                    elif isinstance(meta_info, dict):
                        # Metadata exists but content is None - return what we have (virality_score might still be there)
                        return meta_info
            elif video_type == "short_reel" and video_index is not None and metadata_dict.get("short_reels"):
                if video_index < len(metadata_dict["short_reels"]):
                    meta_info = metadata_dict["short_reels"][video_index]
                    if isinstance(meta_info, dict) and meta_info.get("content") is not None:
                        # Return the full metadata object including virality_score
                        return meta_info
                    elif isinstance(meta_info, dict):
                        # Metadata exists but content is None - return what we have (virality_score might still be there)
                        return meta_info
    
    # Fallback: get metadata file from database
    job_file = db.query(JobFile).filter(
        JobFile.job_id == db_job.id,
        JobFile.file_type == metadata_file_type
    ).first()
    
    # Also try to get the video file to get virality_score from database
    video_file_type = video_type.replace("_reel", "_reel") if video_type != "main_reel" else "main_reel"
    if video_index is not None:
        if video_type == "medium_reel":
            video_file_type = "medium_reel"
        elif video_type == "short_reel":
            video_file_type = "short_reel"
    
    video_job_file = None
    if video_type == "main_reel":
        video_job_file = db.query(JobFile).filter(
            JobFile.job_id == db_job.id,
            JobFile.file_type == "main_reel"
        ).first()
    elif video_type == "medium_reel" and video_index is not None:
        # For medium_reels, we need to find the file by index
        medium_files = db.query(JobFile).filter(
            JobFile.job_id == db_job.id,
            JobFile.file_type == "medium_reel"
        ).order_by(JobFile.created_at).all()
        if video_index < len(medium_files):
            video_job_file = medium_files[video_index]
    elif video_type == "short_reel" and video_index is not None:
        # For short_reels, we need to find the file by index
        short_files = db.query(JobFile).filter(
            JobFile.job_id == db_job.id,
            JobFile.file_type == "short_reel"
        ).order_by(JobFile.created_at).all()
        if video_index < len(short_files):
            video_job_file = short_files[video_index]
    
    virality_score = None
    if video_job_file:
        virality_score = video_job_file.virality_score
    
    if job_file and os.path.exists(job_file.file_path):
        try:
            with open(job_file.file_path, 'r', encoding='utf-8') as f:
                metadata_content = json.load(f)
            # Return metadata with virality_score if available
            result = {
                "content": metadata_content,
                "virality_score": virality_score
            }
            return result
        except json.JSONDecodeError:
            # Fallback: return as text if not JSON
            try:
                with open(job_file.file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                return {
                    "text": content,
                    "virality_score": virality_score
                }
            except Exception as e:
                logger.error(f"Error reading metadata file: {e}")
        except Exception as e:
            logger.error(f"Error reading metadata file: {e}")
    
    raise HTTPException(status_code=404, detail=f"Metadata not found for {video_type}" + (f" (index {video_index})" if video_index is not None else ""))


@app.get("/api/jobs")
async def list_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all jobs for current user (requires authentication)"""
    # Get all jobs for current user from database
    db_jobs = db.query(Job).filter(Job.user_id == current_user.id).order_by(Job.created_at.desc()).all()
    
    jobs_list = []
    for db_job in db_jobs:
        # Get progress from in-memory jobs if available
        job_progress = jobs.get(db_job.job_id, {})
        jobs_list.append({
            "job_id": db_job.job_id,
            "status": db_job.status,
            "youtube_url": db_job.youtube_url,
            "video_duration": db_job.video_duration,
            "created_at": db_job.created_at.isoformat() if db_job.created_at else None,
            "completed_at": db_job.completed_at.isoformat() if db_job.completed_at else None,
            "progress": job_progress.get("progress")
        })
    
    return {"jobs": jobs_list}


@app.post("/api/job/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancel a pending or processing job and delete it (requires authentication, only own jobs)"""
    # Check if job belongs to current user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Can only cancel pending or processing jobs
    if db_job.status not in [JobStatus.PENDING, JobStatus.PROCESSING]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot cancel job with status: {db_job.status}. Only pending or processing jobs can be cancelled."
        )
    
    # Set cancellation flag in in-memory jobs dict to stop processing
    if job_id in jobs:
        jobs[job_id]["cancelled"] = True
        # Kill any running subprocess
        if jobs[job_id].get("process"):
            try:
                process = jobs[job_id]["process"]
                if process.poll() is None:  # Process is still running
                    logger.info(f"Killing subprocess for cancelled job {job_id}")
                    process.terminate()
                    try:
                        process.wait(timeout=5)  # Wait up to 5 seconds for graceful termination
                    except:
                        process.kill()  # Force kill if it doesn't terminate
                    logger.info(f"Subprocess killed for job {job_id}")
            except Exception as e:
                logger.warning(f"Error killing subprocess for job {job_id}: {e}")
    
    # Refund credits if video wasn't downloaded yet
    refunded = False
    if db_job.credits_deducted > 0 and db_job.video_downloaded in ["no", "failed"]:
        user = db.query(User).filter(User.id == db_job.user_id).first()
        if user:
            refund_amount = db_job.credits_deducted
            previous_balance = user.credit
            user.credit += refund_amount
            db_job.credits_deducted = 0
            db.commit()
            refunded = True
            logger.info(
                f"💰 Refunded {refund_amount} credits to user {user.id} for cancelled job {job_id}. "
                f"Previous balance: {previous_balance}, New balance: {user.credit}"
            )
    
    # Clean up files before deleting
    try:
        # Get all job files
        job_files = db.query(JobFile).filter(JobFile.job_id == db_job.id).all()
        for job_file in job_files:
            if job_file.file_path and os.path.exists(job_file.file_path):
                try:
                    os.remove(job_file.file_path)
                    logger.info(f"Deleted file: {job_file.file_path}")
                except Exception as e:
                    logger.warning(f"Could not delete file {job_file.file_path}: {job_file.file_path}: {e}")
        
        # Also clean up any files from in-memory job data
        if job_id in jobs:
            job_data = jobs[job_id]
            results = job_data.get("results", {})
            files_to_delete = []
            if results.get("main_reel"):
                files_to_delete.append(results["main_reel"])
            if results.get("video_file"):
                files_to_delete.append(results["video_file"])
            files_to_delete.extend(results.get("medium_reels", []))
            files_to_delete.extend(results.get("short_reels", []))
            
            for file_path in files_to_delete:
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete file {file_path}: {e}")
    except Exception as e:
        logger.warning(f"Error cleaning up files for cancelled job {job_id}: {e}")
    
    # Remove from in-memory jobs dict (this helps release semaphore tracking)
    if job_id in jobs:
        del jobs[job_id]
    
    # Delete job from database (cascade will delete job files)
    db.delete(db_job)
    db.commit()
    
    logger.info(f"Job {job_id} cancelled and deleted by user {current_user.id}")
    return {"message": "Job cancelled and deleted successfully", "refunded": refunded}


@app.delete("/api/job/{job_id}")
async def delete_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a job and its associated files (requires authentication, only own jobs)"""
    # Check if job belongs to current user
    db_job = db.query(Job).filter(Job.job_id == job_id, Job.user_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found in progress tracking")
    
    job = jobs[job_id]
    
    # Delete from database (cascade will delete files)
    db.delete(db_job)
    db.commit()
    
    # Clean up files
    try:
        results = job.get("results")
        if results:
            files_to_delete = []
            if results.get("main_reel"):
                files_to_delete.append(results["main_reel"])
            if results.get("video_file"):
                files_to_delete.append(results["video_file"])
            if results.get("metadata"):
                files_to_delete.append(results["metadata"])
            files_to_delete.extend(results.get("medium_reels", []))
            files_to_delete.extend(results.get("short_reels", []))
            
            for file_path in files_to_delete:
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete file {file_path}: {e}")
    except Exception as e:
        logger.warning(f"Error cleaning up files for job {job_id}: {e}")
    
    del jobs[job_id]
    return {"message": "Job deleted successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

