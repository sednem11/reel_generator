#!/usr/bin/env python3
"""
Script to completely clear the database and remove all video files.
This will delete:
- All users from the database
- All jobs from the database
- All job files from the database
- All video files (*.mp4) from the current directory
- All metadata JSON files (job_*_metadata.json)
"""
import os
import sys
import glob
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from database import SessionLocal, Job, JobFile, User
from sqlalchemy import text

def clear_all_data():
    """Remove all data from database and all video files from filesystem"""
    db = SessionLocal()
    
    try:
        print("="*60)
        print("CLEARING ALL DATA")
        print("="*60)
        
        # Step 1: Delete all video files from disk
        print("\n1. Deleting video files from disk...")
        video_patterns = [
            "job_*.mp4",
            "medium_reel_*.mp4",
            "short_reel_*.mp4",
            "reel_*.mp4",
            "base_video.mp4",
            "reel.mp4",
            "*.mp4"  # Catch any other mp4 files
        ]
        
        files_deleted = 0
        total_size = 0
        
        current_dir = os.getcwd()
        for pattern in video_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                # Skip files in node_modules and venv
                if 'node_modules' in filepath or 'venv' in filepath:
                    continue
                    
                try:
                    if os.path.isfile(filepath):
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        files_deleted += 1
                        total_size += file_size
                        print(f"  ✓ Deleted: {os.path.basename(filepath)}")
                except Exception as e:
                    print(f"  ✗ Error deleting {filepath}: {e}")
        
        # Step 2: Delete all metadata JSON files
        print("\n2. Deleting metadata JSON files...")
        json_patterns = [
            "job_*_metadata.json",
            "reel_metadata.txt"
        ]
        
        json_deleted = 0
        for pattern in json_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                try:
                    if os.path.isfile(filepath):
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        json_deleted += 1
                        total_size += file_size
                        print(f"  ✓ Deleted: {os.path.basename(filepath)}")
                except Exception as e:
                    print(f"  ✗ Error deleting {filepath}: {e}")
        
        # Step 3: Delete all job files from database
        print("\n3. Deleting job files from database...")
        job_files_count = db.query(JobFile).count()
        db.query(JobFile).delete()
        print(f"  ✓ Deleted {job_files_count} job file records")
        
        # Step 4: Delete all jobs from database
        print("\n4. Deleting jobs from database...")
        jobs_count = db.query(Job).count()
        db.query(Job).delete()
        print(f"  ✓ Deleted {jobs_count} job records")
        
        # Step 5: Delete all users from database
        print("\n5. Deleting users from database...")
        users_count = db.query(User).count()
        db.query(User).delete()
        print(f"  ✓ Deleted {users_count} user records")
        
        # Commit all database changes
        db.commit()
        
        print("\n" + "="*60)
        print("CLEANUP SUMMARY")
        print("="*60)
        print(f"Video files deleted: {files_deleted}")
        print(f"Metadata files deleted: {json_deleted}")
        print(f"Total files deleted: {files_deleted + json_deleted}")
        print(f"Total space freed: {total_size / (1024*1024):.2f} MB")
        print(f"Job files deleted from database: {job_files_count}")
        print(f"Jobs deleted from database: {jobs_count}")
        print(f"Users deleted from database: {users_count}")
        print("="*60)
        print("✓ All data cleared successfully!")
        print("="*60)
        
    except Exception as e:
        db.rollback()
        print(f"\n✗ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    print("\n" + "="*60)
    print("WARNING: This will delete ALL data!")
    print("="*60)
    print("This includes:")
    print("  - All users from the database")
    print("  - All jobs from the database")
    print("  - All video files (*.mp4)")
    print("  - All metadata files (*.json)")
    print("="*60)
    response = input("\nAre you sure you want to continue? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        clear_all_data()
    else:
        print("\nCleanup cancelled.")
        sys.exit(0)

