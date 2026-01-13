#!/usr/bin/env python3
"""
Extract audio samples and video frames for specific sound events
to analyze the exact sounds and their visual representations
"""

import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path

def extract_audio_sample(audio_file, start_time, duration, output_file):
    """Extract a sample of audio around a sound event"""
    try:
        cmd = [
            'ffmpeg', '-y',
            '-i', audio_file,
            '-ss', str(start_time - 0.1),  # Start slightly before
            '-t', str(duration + 0.2),  # Include some after
            '-acodec', 'copy',
            output_file
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except Exception as e:
        print(f"  ⚠️  Error extracting audio sample: {e}")
        return False

def extract_video_frames(video_file, time, output_folder, video_id, sound_type, sound_index):
    """Extract video frames around a sound event"""
    try:
        # Extract frame at sound time, 50ms before, and 100ms after
        frames = []
        for offset in [-0.05, 0.0, 0.05, 0.1]:
            frame_time = time + offset
            if frame_time < 0:
                continue
            
            frame_file = os.path.join(output_folder, f"{video_id}_{sound_type}_{sound_index}_{offset:+.2f}s.png")
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(frame_time),
                '-i', video_file,
                '-vframes', '1',
                '-q:v', '2',
                frame_file
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=5)
            frames.append(frame_file)
        
        return frames
    except Exception as e:
        print(f"  ⚠️  Error extracting frames: {e}")
        return []

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_sound_samples.py <download_folder>")
        sys.exit(1)
    
    download_folder = sys.argv[1]
    analysis_file = os.path.join(download_folder, 'sound_analysis.json')
    
    if not os.path.exists(analysis_file):
        print(f"❌ Analysis file not found: {analysis_file}")
        print("   Run analyze_sounds.py first")
        sys.exit(1)
    
    # Load analysis
    with open(analysis_file) as f:
        analysis = json.load(f)
    
    # Find audio and video files
    audio_file = None
    video_file = None
    video_id = None
    
    for file in os.listdir(download_folder):
        if file.endswith(('.m4a', '.mp3', '.webm', '.opus')) and 'audio' in file.lower():
            audio_file = os.path.join(download_folder, file)
            video_id = file.split('_')[0]
        elif file.endswith(('.mp4', '.webm', '.mkv')) and 'video' in file.lower():
            video_file = os.path.join(download_folder, file)
    
    if not audio_file or not video_file:
        print(f"❌ Audio or video file not found")
        sys.exit(1)
    
    # Create samples folder
    samples_folder = os.path.join(download_folder, 'sound_samples')
    os.makedirs(samples_folder, exist_ok=True)
    
    # Group sounds by type
    sound_groups = {}
    for event in analysis['sound_events']:
        sound_type = event['type']
        if sound_type not in sound_groups:
            sound_groups[sound_type] = []
        sound_groups[sound_type].append(event)
    
    print(f"📦 Extracting sound samples and frames...")
    print(f"   Audio: {audio_file}")
    print(f"   Video: {video_file}")
    print(f"   Output: {samples_folder}")
    print()
    
    # Extract samples for each sound type (first 3 of each type)
    extracted = {}
    
    for sound_type, events in sound_groups.items():
        print(f"🔊 {sound_type.upper()}: Extracting {min(3, len(events))} samples...")
        extracted[sound_type] = []
        
        for i, event in enumerate(events[:3]):  # First 3 of each type
            time = event['time']
            sound_index = i + 1
            
            # Extract audio sample
            audio_sample = os.path.join(samples_folder, f"{sound_type}_{sound_index}.m4a")
            if extract_audio_sample(audio_file, time, 0.5, audio_sample):
                print(f"  ✅ {sound_type}_{sound_index} audio: {time:.2f}s")
                extracted[sound_type].append({
                    'time': time,
                    'audio_file': audio_sample,
                    'frames': []
                })
            
            # Extract video frames
            frames = extract_video_frames(video_file, time, samples_folder, video_id, sound_type, sound_index)
            if frames:
                print(f"  ✅ {sound_type}_{sound_index} frames: {len(frames)} frames")
                if extracted[sound_type]:
                    extracted[sound_type][-1]['frames'] = frames
    
    print()
    print("=" * 60)
    print("📋 EXTRACTION SUMMARY")
    print("=" * 60)
    for sound_type, samples in extracted.items():
        print(f"\n{sound_type.upper()}: {len(samples)} samples")
        for sample in samples:
            print(f"  - {sample['time']:.2f}s: {os.path.basename(sample['audio_file'])}")
            if sample['frames']:
                print(f"    Frames: {len(sample['frames'])}")
    
    print(f"\n✅ Samples saved to: {samples_folder}")
    print(f"\n💡 You can now:")
    print(f"   1. Listen to the audio samples to understand the sounds")
    print(f"   2. View the video frames to see visual effects")
    print(f"   3. Use this data to recreate similar sounds in the editor")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Extraction interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

