#!/usr/bin/env python3
"""
Script to remove all jobs and video files from database and filesystem.
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from database import SessionLocal, Job, JobFile
from sqlalchemy import text

def cleanup_all_jobs():
    """Remove all jobs and associated files"""
    db = SessionLocal()
    
    try:
        # Get all job files to delete from disk
        all_job_files = db.query(JobFile).all()
        
        files_deleted = 0
        files_not_found = 0
        total_size = 0
        
        print(f"Found {len(all_job_files)} job files in database")
        
        # Delete files from disk
        for job_file in all_job_files:
            file_path = job_file.file_path
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    files_deleted += 1
                    total_size += file_size
                    print(f"  ✓ Deleted: {file_path}")
                except Exception as e:
                    print(f"  ✗ Error deleting {file_path}: {e}")
            else:
                files_not_found += 1
                print(f"  - File not found (already deleted): {file_path}")
        
        # Also delete any job_*.mp4 and job_*.json files in current directory
        current_dir = os.getcwd()
        for filename in os.listdir(current_dir):
            if filename.startswith("job_") and (filename.endswith(".mp4") or filename.endswith(".json")):
                filepath = os.path.join(current_dir, filename)
                try:
                    if os.path.exists(filepath):
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        files_deleted += 1
                        total_size += file_size
                        print(f"  ✓ Deleted: {filepath}")
                except Exception as e:
                    print(f"  ✗ Error deleting {filepath}: {e}")
        
        # Delete all job files from database (cascade will handle job deletion)
        job_files_count = db.query(JobFile).count()
        db.query(JobFile).delete()
        
        # Delete all jobs from database
        jobs_count = db.query(Job).count()
        db.query(Job).delete()
        
        db.commit()
        
        print("\n" + "="*60)
        print("Cleanup Summary:")
        print("="*60)
        print(f"Files deleted from disk: {files_deleted}")
        print(f"Files not found: {files_not_found}")
        print(f"Total space freed: {total_size / (1024*1024):.2f} MB")
        print(f"Job files deleted from database: {job_files_count}")
        print(f"Jobs deleted from database: {jobs_count}")
        print("="*60)
        print("✓ All jobs and videos removed successfully!")
        
    except Exception as e:
        db.rollback()
        print(f"✗ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    print("WARNING: This will delete ALL jobs and video files!")
    response = input("Are you sure you want to continue? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        cleanup_all_jobs()
    else:
        print("Cleanup cancelled.")
        sys.exit(0)

