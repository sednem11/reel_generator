#!/usr/bin/env python3
"""
YouTube Audio Extractor
Extracts audio from a YouTube video and saves it with a custom filename.
"""

import os
import sys
import subprocess
import argparse
import re
from pathlib import Path


def clean_url(url):
    """
    Clean URL by removing escape characters that might be added by the shell.
    """
    # Remove backslashes before special characters
    url = url.replace('\\?', '?')
    url = url.replace('\\=', '=')
    url = url.replace('\\&', '&')
    # Remove any other escaped characters
    url = re.sub(r'\\([^\\])', r'\1', url)
    return url.strip('"\'')  # Remove quotes if present


def extract_audio(youtube_url, output_filename, audio_format="mp3", quality="best"):
    """
    Extract audio from a YouTube video and save it with a custom filename.
    
    Args:
        youtube_url: URL of the YouTube video
        output_filename: Desired output filename (without extension)
        audio_format: Audio format (mp3, m4a, opus, wav, etc.) - default: mp3
        quality: Audio quality (best, worst, or specific bitrate) - default: best
    """
    # Clean the URL to handle escaped characters
    youtube_url = clean_url(youtube_url)
    
    # Ensure output filename doesn't have extension (yt-dlp will add it)
    output_name = Path(output_filename).stem
    
    # Get the directory where the script is run from
    output_dir = os.getcwd()
    output_path = os.path.join(output_dir, output_name)
    
    print(f"🎵 Extracting audio from: {youtube_url}")
    print(f"📁 Output: {output_path}.{audio_format}")
    
    try:
        # Build yt-dlp command for audio extraction
        yt_dlp_cmd = [
            "yt-dlp",
            "-x",  # Extract audio only
            "--audio-format", audio_format,  # Set audio format
            "--audio-quality", quality,  # Set audio quality
            "-o", output_path + ".%(ext)s",  # Output filename
            "--no-warnings",
            "--quiet",  # Less verbose output
            "--progress",  # Show progress bar
            youtube_url
        ]
        
        # Run yt-dlp
        result = subprocess.run(
            yt_dlp_cmd,
            check=True,
            text=True
        )
        
        # Check if file was created
        final_path = f"{output_path}.{audio_format}"
        if os.path.exists(final_path):
            file_size = os.path.getsize(final_path) / (1024 * 1024)  # Size in MB
            print(f"\n✅ Success! Audio saved as: {final_path}")
            print(f"📊 File size: {file_size:.2f} MB")
            return final_path
        else:
            # Sometimes yt-dlp uses different extensions
            # Try to find the file
            for ext in [audio_format, "m4a", "opus", "webm"]:
                possible_path = f"{output_path}.{ext}"
                if os.path.exists(possible_path):
                    print(f"\n✅ Success! Audio saved as: {possible_path}")
                    file_size = os.path.getsize(possible_path) / (1024 * 1024)
                    print(f"📊 File size: {file_size:.2f} MB")
                    return possible_path
            
            print("\n⚠️  Warning: File created but path not found")
            return None
            
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error extracting audio: {e}")
        print("Make sure yt-dlp is installed: pip install yt-dlp")
        return None
    except KeyboardInterrupt:
        print("\n\n⚠️  Extraction cancelled by user")
        # Clean up partial file if exists
        for ext in [audio_format, "m4a", "opus", "webm", "part"]:
            possible_path = f"{output_path}.{ext}"
            if os.path.exists(possible_path):
                try:
                    os.remove(possible_path)
                except:
                    pass
        return None
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Extract audio from a YouTube video",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ" "my_audio"
  %(prog)s "https://youtu.be/dQw4w9WgXcQ" "song" --format m4a
  %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ" "audio" --quality 192K
        """
    )
    
    parser.add_argument(
        "url",
        help="YouTube video URL"
    )
    
    parser.add_argument(
        "output",
        help="Output filename (without extension)"
    )
    
    parser.add_argument(
        "--format", "-f",
        default="mp3",
        choices=["mp3", "m4a", "opus", "wav", "flac", "aac"],
        help="Audio format (default: mp3)"
    )
    
    parser.add_argument(
        "--quality", "-q",
        default="best",
        help="Audio quality: 'best', 'worst', or bitrate like '192K', '320K' (default: best)"
    )
    
    args = parser.parse_args()
    
    # Extract audio
    result = extract_audio(
        args.url,
        args.output,
        audio_format=args.format,
        quality=args.quality
    )
    
    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

