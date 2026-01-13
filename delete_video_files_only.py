#!/usr/bin/env python3
"""
Script to delete ONLY video files from storage folder.
This will NOT delete anything from the database.
It will only remove video files, metadata files, and temp files from the filesystem.
"""
import os
import sys
import glob
from dotenv import load_dotenv

load_dotenv()

def delete_video_files_only():
    """Delete only video files from filesystem, keep database intact"""
    
    try:
        print("="*60)
        print("DELETING VIDEO FILES FROM STORAGE")
        print("="*60)
        print("Note: Database records will be kept intact\n")
        
        current_dir = os.getcwd()
        deleted = 0
        total_size = 0
        
        # Delete all video files
        print("1. Deleting video files...")
        video_patterns = [
            "job_*.mp4",
            "job_*.mp4.part*",
            "job_*.ytdl",
            "medium_reel_*.mp4",
            "short_reel_*.mp4",
            "reel_*.mp4",
            "base_video.mp4",
            "reel.mp4",
        ]
        
        for pattern in video_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                # Skip files in protected directories
                if any(skip in filepath for skip in ['node_modules', 'venv', '.git', '__pycache__', 'frontend']):
                    continue
                try:
                    if os.path.isfile(filepath):
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        deleted += 1
                        total_size += file_size
                        print(f"   ✓ Deleted: {os.path.basename(filepath)} ({file_size / (1024*1024):.2f} MB)")
                except Exception as e:
                    print(f"   ✗ Error deleting {os.path.basename(filepath)}: {e}")
        
        # Delete metadata JSON files
        print("\n2. Deleting metadata files...")
        json_patterns = [
            "job_*_metadata.json",
            "*_metadata.json",
        ]
        
        json_deleted = 0
        for pattern in json_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                if any(skip in filepath for skip in ['node_modules', 'venv', '.git', 'frontend']):
                    continue
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        json_deleted += 1
                        print(f"   ✓ Deleted: {os.path.basename(filepath)}")
                except Exception as e:
                    print(f"   ✗ Error deleting {os.path.basename(filepath)}: {e}")
        
        # Delete temporary files
        print("\n3. Deleting temporary files...")
        temp_patterns = [
            "temp-audio*.m4a",
            "reelTEMP_*.mp3",
            "reel_*_intro_*.mp3",
        ]
        
        temp_deleted = 0
        for pattern in temp_patterns:
            for filepath in glob.glob(os.path.join(current_dir, pattern)):
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        temp_deleted += 1
                except:
                    pass  # Silently skip temp file errors
        
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Video files deleted: {deleted}")
        print(f"Metadata files deleted: {json_deleted}")
        print(f"Temp files deleted: {temp_deleted}")
        print(f"Total space freed: {total_size / (1024*1024):.2f} MB")
        print("="*60)
        print("✓ Video files deleted from storage!")
        print("  (Database records are still intact)")
        print("="*60)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--yes":
        # Skip confirmation if --yes flag is provided
        delete_video_files_only()
    else:
        print("\n⚠️  This will delete ALL video files from storage folder!")
        print("   Database records will be kept intact.\n")
        response = input("Type 'yes' to continue: ")
        if response.lower() == 'yes':
            delete_video_files_only()
        else:
            print("Cancelled.")

