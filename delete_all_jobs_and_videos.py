#!/usr/bin/env python3
"""
Quick script to delete ALL jobs and videos.
This will:
- Delete all jobs from database
- Delete all job files from database  
- Delete all video files from filesystem
- Delete all metadata files
- Delete all temporary files
"""
import os
import sys
import glob
from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal, Job, JobFile

def delete_all():
    """Delete all jobs and videos"""
    db = SessionLocal()
    
    try:
        print("="*60)
        print("DELETING ALL JOBS AND VIDEOS")
        print("="*60)
        
        # Get counts
        job_count = db.query(Job).count()
        job_file_count = db.query(JobFile).count()
        print(f"\nFound: {job_count} jobs, {job_file_count} job files")
        
        # Delete from database
        print("\n1. Deleting from database...")
        db.query(JobFile).delete()
        db.query(Job).delete()
        db.commit()
        print(f"   ✓ Deleted {job_file_count} job files")
        print(f"   ✓ Deleted {job_count} jobs")
        
        # Delete all video files
        print("\n2. Deleting video files...")
        patterns = [
            "job_*.mp4",
            "job_*.mp4.part*",
            "job_*.ytdl",
            "medium_reel_*.mp4",
            "short_reel_*.mp4",
            "reel_*.mp4",
            "base_video.mp4",
            "reel.mp4",
            "*.mp4"
        ]
        
        deleted = 0
        size = 0
        current_dir = os.getcwd()
        
        for pattern in patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                if any(skip in filepath for skip in ['node_modules', 'venv', '.git', '__pycache__']):
                    continue
                try:
                    if os.path.isfile(filepath):
                        s = os.path.getsize(filepath)
                        os.remove(filepath)
                        deleted += 1
                        size += s
                except:
                    pass
        
        # Delete metadata files
        print("\n3. Deleting metadata files...")
        for pattern in ["job_*_metadata.json", "*_metadata.json"]:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                if any(skip in filepath for skip in ['node_modules', 'venv', '.git']):
                    continue
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        deleted += 1
                except:
                    pass
        
        # Delete temp files
        print("\n4. Deleting temporary files...")
        for pattern in ["temp-audio*.m4a", "reelTEMP_*.mp3", "reel_*_intro_*.mp3"]:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                except:
                    pass
        
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Jobs deleted: {job_count}")
        print(f"Job files deleted: {job_file_count}")
        print(f"Files deleted: {deleted}")
        print(f"Space freed: {size / (1024*1024):.2f} MB")
        print("="*60)
        print("✓ All jobs and videos deleted!")
        
    except Exception as e:
        db.rollback()
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--yes":
        # Skip confirmation if --yes flag is provided
        delete_all()
    else:
        print("\n⚠️  WARNING: This will delete ALL jobs and videos!")
        print("   This action cannot be undone.\n")
        response = input("Type 'yes' to continue: ")
        if response.lower() == 'yes':
            delete_all()
        else:
            print("Cancelled.")

