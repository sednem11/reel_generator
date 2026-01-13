#!/usr/bin/env python3
"""
Audio Splitter
Extracts specific segments from an audio file and saves them with custom names.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def get_ffmpeg_path():
    """Get the path to ffmpeg binary."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        # Try system ffmpeg
        return "ffmpeg"


def extract_audio_segment(input_file, output_file, start_time, duration=None, end_time=None):
    """
    Extract a segment from an audio file using ffmpeg.
    
    Args:
        input_file: Input audio file path
        output_file: Output audio file path
        start_time: Start time in seconds (can be float like 1.5)
        duration: Duration in seconds (if provided, end_time is ignored)
        end_time: End time in seconds (if duration not provided)
    """
    ffmpeg_path = get_ffmpeg_path()
    
    # Calculate duration if end_time is provided
    if duration is None and end_time is not None:
        duration = end_time - start_time
    
    if duration is None:
        raise ValueError("Either duration or end_time must be provided")
    
    # Format time for ffmpeg (supports decimal seconds)
    start_str = str(start_time)
    duration_str = str(duration)
    
    print(f"  📦 Extracting: {start_str}s - {start_time + duration}s ({duration_str}s)")
    
    # Build ffmpeg command - use re-encoding for better compatibility
    # For short segments, re-encoding ensures proper extraction
    cmd = [
        ffmpeg_path,
        "-i", input_file,
        "-ss", start_str,  # Start time
        "-t", duration_str,  # Duration
        "-acodec", "libmp3lame",  # MP3 encoder (re-encode for reliability)
        "-q:a", "2",  # High quality (0-9, 2 is high quality)
        "-ar", "44100",  # Sample rate
        "-ac", "2",  # Stereo
        "-y",  # Overwrite output file if exists
        output_file
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error: {e.stderr}")
        return False


def split_audio(input_file, output_dir=None):
    """
    Split audio file into segments:
    - flash: 0-1.5 seconds
    - wasted_simple: 2-4 seconds
    - Original file is kept
    """
    input_path = Path(input_file)
    
    if not input_path.exists():
        print(f"❌ Error: File not found: {input_file}")
        return False
    
    # Determine output directory
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = input_path.parent
    
    # Get base name without extension
    base_name = input_path.stem
    
    # Define segments
    segments = [
        {
            "name": "flash",
            "start": 0.0,
            "duration": 2.0,  # 2 seconds (0-2.0s, added 0.5s)
            "output": output_dir / f"{base_name}_flash.mp3"
        },
        {
            "name": "wasted_simple",
            "start": 2.5,  # Start at 2.5s (took 0.5s from start)
            "duration": 2.0,  # 2 seconds (from 2.5 to 4.5s - added 0.5s to end)
            "output": output_dir / f"{base_name}_wasted_simple.mp3"
        }
    ]
    
    print(f"🎵 Splitting audio: {input_file}")
    print(f"📁 Output directory: {output_dir}")
    print()
    
    success_count = 0
    
    for segment in segments:
        print(f"✂️  Extracting '{segment['name']}':")
        if extract_audio_segment(
            str(input_path),
            str(segment['output']),
            segment['start'],
            duration=segment['duration']
        ):
            file_size = segment['output'].stat().st_size / 1024  # Size in KB
            print(f"  ✅ Saved: {segment['output']} ({file_size:.2f} KB)")
            success_count += 1
        else:
            print(f"  ❌ Failed to extract: {segment['name']}")
        print()
    
    print(f"📊 Summary:")
    print(f"  ✅ Successfully extracted: {success_count}/{len(segments)} segments")
    print(f"  📄 Original file kept: {input_path}")
    
    return success_count == len(segments)


def main():
    parser = argparse.ArgumentParser(
        description="Split audio file into segments (flash: 0-2.0s, wasted_simple: 2.5-4.5s)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s Wasted.mp3
  %(prog)s audio.mp3 --output-dir ./sounds
  %(prog)s /path/to/audio.wav
        """
    )
    
    parser.add_argument(
        "input_file",
        help="Input audio file"
    )
    
    parser.add_argument(
        "--output-dir", "-o",
        help="Output directory (default: same as input file)"
    )
    
    args = parser.parse_args()
    
    # Split audio
    success = split_audio(args.input_file, args.output_dir)
    
    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

