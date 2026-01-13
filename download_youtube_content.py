#!/usr/bin/env python3
"""
Download YouTube video content: transcript, audio, and video files.
Usage: python download_youtube_content.py <youtube_url> [output_folder]
"""

import sys
import os
import subprocess
from pathlib import Path
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
)

def get_video_id(url):
    """Extract video ID from YouTube URL"""
    if 'youtube.com/watch?v=' in url:
        return url.split('v=')[1].split('&')[0]
    elif 'youtu.be/' in url:
        return url.split('youtu.be/')[1].split('?')[0]
    elif 'youtube.com/shorts/' in url:
        return url.split('shorts/')[1].split('?')[0]
    else:
        raise ValueError(f"Invalid YouTube URL: {url}")

def download_transcript(video_id, output_folder):
    """Download transcript in multiple languages if available"""
    print(f"📝 Downloading transcript for video ID: {video_id}")
    
    try:
        # Use the same API pattern as the existing code
        api = YouTubeTranscriptApi()
        fetched_transcript = api.fetch(video_id)
        
        # Convert to list of dicts
        transcript_data = []
        for snippet in fetched_transcript:
            transcript_data.append({
                'text': snippet.text,
                'start': snippet.start,
                'duration': snippet.duration
            })
        
        output_file = os.path.join(output_folder, f"{video_id}_transcript.txt")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for item in transcript_data:
                f.write(f"[{item['start']:.2f}s - {item['start'] + item['duration']:.2f}s] {item['text']}\n")
        
        print(f"  ✅ Transcript saved: {output_file}")
        
        # Also save as JSON
        import json
        json_file = os.path.join(output_folder, f"{video_id}_transcript.json")
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(transcript_data, f, indent=2, ensure_ascii=False)
        
        print(f"  ✅ Transcript JSON saved: {json_file}")
        return output_file
        
    except (NoTranscriptFound, TranscriptsDisabled) as e:
        print(f"  ⚠️  Transcript not available: {e}")
        print("  💡 Trying yt-dlp fallback...")
        return download_transcript_ytdlp(video_id, output_folder)
    except Exception as e:
        print(f"  ⚠️  Error with YouTube API: {e}")
        print("  💡 Trying yt-dlp fallback...")
        return download_transcript_ytdlp(video_id, output_folder)

def download_transcript_ytdlp(video_id, output_folder):
    """Fallback: Download transcript using yt-dlp"""
    print(f"  📝 Downloading transcript via yt-dlp...")
    try:
        cmd = [
            'python3', '-m', 'yt_dlp',
            '--write-subs',
            '--write-auto-subs',
            '--sub-lang', 'en',
            '--skip-download',
            '--no-warnings',
            '-o', os.path.join(output_folder, f"{video_id}_subs.%(ext)s"),
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Find downloaded subtitle files
        import glob
        subtitle_files = glob.glob(os.path.join(output_folder, f"{video_id}_subs*"))
        if subtitle_files:
            print(f"  ✅ Subtitle files downloaded: {subtitle_files}")
            return subtitle_files[0]
        else:
            print("  ❌ No subtitle files found")
            return None
    except Exception as e:
        print(f"  ❌ Error with yt-dlp: {e}")
        return None

def download_audio(video_id, url, output_folder):
    """Download audio file using yt-dlp"""
    print(f"🎵 Downloading audio for video ID: {video_id}")
    
    audio_file = os.path.join(output_folder, f"{video_id}_audio.%(ext)s")
    
    try:
        # Use Python module directly
        cmd = [
            'python3', '-m', 'yt_dlp',
            '-x',  # Extract audio only
            '--audio-format', 'm4a',  # Use m4a format
            '--audio-quality', '0',  # Best quality
            '-o', audio_file,  # Output filename
            '--no-playlist',  # Don't download playlists
            '--no-warnings',  # Suppress warnings
            url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # yt-dlp outputs the filename, try to extract it
        output_lines = result.stdout.split('\n')
        downloaded_file = None
        
        # Look for the filename in output
        for line in output_lines:
            if 'Destination:' in line or 'has already been downloaded' in line:
                # Extract filename from output
                if 'Destination:' in line:
                    downloaded_file = line.split('Destination:')[-1].strip()
                break
        
        # If not found in output, check common locations and extensions
        if not downloaded_file:
            import glob
            # Check for files with video_id and audio extensions
            for ext in ['m4a', 'mp3', 'webm', 'opus']:
                pattern = os.path.join(output_folder, f"{video_id}_audio.{ext}")
                matches = glob.glob(pattern)
                if matches:
                    downloaded_file = matches[0]
                    break
            
            # If still not found, check for any new files in the folder
            if not downloaded_file:
                # Get files before download (if we can)
                # Just check for any audio-like files
                for ext in ['m4a', 'mp3', 'webm', 'opus']:
                    all_files = glob.glob(os.path.join(output_folder, f"*.{ext}"))
                    if all_files:
                        # Get the most recently modified
                        downloaded_file = max(all_files, key=os.path.getmtime)
                        break
        
        if downloaded_file and os.path.exists(downloaded_file):
            # Rename to standard format if needed
            standard_name = os.path.join(output_folder, f"{video_id}_audio{os.path.splitext(downloaded_file)[1]}")
            if downloaded_file != standard_name:
                os.rename(downloaded_file, standard_name)
                downloaded_file = standard_name
            print(f"  ✅ Audio saved: {downloaded_file}")
            return downloaded_file
        else:
            print(f"  ❌ Audio file not found. yt-dlp output:")
            print(result.stdout)
            if result.stderr:
                print(f"  Error: {result.stderr}")
            return None
            
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error downloading audio: {e}")
        print(f"  Output: {e.stdout}")
        print(f"  Error: {e.stderr}")
        return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

def download_video(video_id, url, output_folder):
    """Download video file using yt-dlp"""
    print(f"🎬 Downloading video for video ID: {video_id}")
    
    video_file = os.path.join(output_folder, f"{video_id}_video.%(ext)s")
    
    try:
        # Use Python module directly
        cmd = [
            'python3', '-m', 'yt_dlp',
            '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',  # Best MP4 format
            '-o', video_file,  # Output filename
            '--merge-output-format', 'mp4',  # Merge to mp4
            '--no-playlist',  # Don't download playlists
            '--no-warnings',  # Suppress warnings
            url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # yt-dlp outputs the filename, try to extract it
        output_lines = result.stdout.split('\n')
        downloaded_file = None
        
        # Look for the final merged file in output
        for line in output_lines:
            if '[Merger] Merging formats into' in line:
                downloaded_file = line.split('[Merger] Merging formats into')[-1].strip().strip('"')
                break
            elif '[download] Destination:' in line and ('video' in line.lower() or 'mp4' in line.lower()):
                downloaded_file = line.split('[download] Destination:')[-1].strip()
        
        # If not found in output, check common locations and extensions
        if not downloaded_file:
            import glob
            # Check for files with video_id and video extensions
            for ext in ['mp4', 'webm', 'mkv']:
                pattern = os.path.join(output_folder, f"{video_id}_video.{ext}")
                matches = glob.glob(pattern)
                if matches:
                    downloaded_file = matches[0]
                    break
        
        # Final check - look for any video file with video_id in name
        if not downloaded_file:
            import glob
            all_files = glob.glob(os.path.join(output_folder, f"{video_id}*video*"))
            if all_files:
                # Filter by extension
                video_exts = ['.mp4', '.webm', '.mkv']
                for f in all_files:
                    if any(f.endswith(ext) for ext in video_exts):
                        downloaded_file = f
                        break
        
        if downloaded_file and os.path.exists(downloaded_file):
            print(f"  ✅ Video saved: {downloaded_file}")
            return downloaded_file
        else:
            print(f"  ❌ Video file not found. yt-dlp output:")
            print(result.stdout[-500:])  # Last 500 chars of output
            if result.stderr:
                print(f"  Error: {result.stderr[-200:]}")
            return None
            
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error downloading video: {e}")
        print(f"  Output: {e.stdout}")
        print(f"  Error: {e.stderr}")
        return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python download_youtube_content.py <youtube_url> [output_folder]")
        print("\nExample:")
        print("  python download_youtube_content.py https://www.youtube.com/shorts/fiZthsmIOWE")
        print("  python download_youtube_content.py https://www.youtube.com/watch?v=VIDEO_ID ./downloads")
        sys.exit(1)
    
    url = sys.argv[1]
    output_folder = sys.argv[2] if len(sys.argv) > 2 else f"youtube_download_{get_video_id(url)}"
    
    # Create output folder
    os.makedirs(output_folder, exist_ok=True)
    print(f"📁 Output folder: {os.path.abspath(output_folder)}")
    print()
    
    try:
        # Get video ID
        video_id = get_video_id(url)
        print(f"🎥 Video ID: {video_id}")
        print()
        
        # Download transcript
        transcript_file = download_transcript(video_id, output_folder)
        print()
        
        # Download audio
        audio_file = download_audio(video_id, url, output_folder)
        print()
        
        # Download video
        video_file = download_video(video_id, url, output_folder)
        print()
        
        # Summary
        print("=" * 60)
        print("📦 Download Summary:")
        print("=" * 60)
        if transcript_file:
            print(f"  📝 Transcript: {transcript_file}")
        if audio_file:
            print(f"  🎵 Audio: {audio_file}")
        if video_file:
            print(f"  🎬 Video: {video_file}")
        print()
        print(f"✅ All files saved to: {os.path.abspath(output_folder)}")
        
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️  Download interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

