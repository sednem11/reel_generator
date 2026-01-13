#!/usr/bin/env python3
"""
Script to remove all jobs and video files.
This will delete:
- All jobs from the database (cascades to job files)
- All video files (*.mp4) from the current directory
- All metadata JSON files (job_*_metadata.json)
"""
import os
import sys
import glob
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from database import SessionLocal, Job, JobFile
from sqlalchemy import text

def clear_jobs_and_videos():
    """Remove all jobs from database and all video files from filesystem"""
    db = SessionLocal()
    
    try:
        print("="*60)
        print("CLEARING ALL JOBS AND VIDEOS")
        print("="*60)
        
        # Step 1: Count jobs before deletion
        job_count = db.query(Job).count()
        job_file_count = db.query(JobFile).count()
        print(f"\nFound {job_count} jobs and {job_file_count} job files in database")
        
        # Step 2: Delete all jobs (cascades to job files)
        print("\n1. Deleting all jobs from database...")
        deleted_jobs = db.query(Job).delete(synchronize_session=False)
        db.commit()
        print(f"  ✓ Deleted {deleted_jobs} jobs from database")
        
        # Step 3: Delete all video files from disk
        print("\n2. Deleting video files from disk...")
        video_patterns = [
            "job_*.mp4",
            "medium_reel_*.mp4",
            "short_reel_*.mp4",
            "reel_*.mp4",
            "base_video.mp4",
            "reel.mp4",
        ]
        
        files_deleted = 0
        total_size = 0
        
        current_dir = os.getcwd()
        for pattern in video_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                # Skip files in node_modules and venv
                if 'node_modules' in filepath or 'venv' in filepath or '.git' in filepath:
                    continue
                    
                try:
                    if os.path.isfile(filepath):
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        files_deleted += 1
                        total_size += file_size
                        print(f"  ✓ Deleted: {os.path.basename(filepath)} ({file_size / (1024*1024):.2f} MB)")
                except Exception as e:
                    print(f"  ✗ Error deleting {os.path.basename(filepath)}: {e}")
        
        # Step 4: Delete all metadata JSON files
        print("\n3. Deleting metadata JSON files...")
        json_patterns = [
            "job_*_metadata.json",
            "*_metadata.json",
        ]
        
        json_files_deleted = 0
        for pattern in json_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                if 'node_modules' in filepath or 'venv' in filepath or '.git' in filepath:
                    continue
                    
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        json_files_deleted += 1
                        print(f"  ✓ Deleted: {os.path.basename(filepath)}")
                except Exception as e:
                    print(f"  ✗ Error deleting {os.path.basename(filepath)}: {e}")
        
        # Step 5: Delete temporary audio files
        print("\n4. Deleting temporary audio files...")
        audio_patterns = [
            "reel_*_intro_*.mp3",
            "temp-audio*.m4a",
            "reelTEMP_MPY_*.mp3",
        ]
        
        audio_files_deleted = 0
        for pattern in audio_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                if 'node_modules' in filepath or 'venv' in filepath or '.git' in filepath:
                    continue
                    
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        audio_files_deleted += 1
                except Exception as e:
                    pass  # Silently skip errors for temp files
        
        # Summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Jobs deleted: {deleted_jobs}")
        print(f"Video files deleted: {files_deleted}")
        print(f"Metadata files deleted: {json_files_deleted}")
        print(f"Audio files deleted: {audio_files_deleted}")
        print(f"Total space freed: {total_size / (1024*1024):.2f} MB")
        print("="*60)
        print("✓ All jobs and videos have been removed!")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    # Ask for confirmation
    print("\n⚠️  WARNING: This will delete ALL jobs and video files!")
    print("   This action cannot be undone.\n")
    
    response = input("Are you sure you want to continue? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        clear_jobs_and_videos()
    else:
        print("Operation cancelled.")

