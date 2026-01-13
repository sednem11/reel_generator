#!/usr/bin/env python3
"""
Analyze YouTube video audio and video to extract sound effects and their visual representations.
Identifies: sound type, timing, frequency characteristics, visual effects (colors, emojis)
"""

import os
import sys
import json
import numpy as np
from scipy import signal
from scipy.io import wavfile
import subprocess
import tempfile

def convert_audio_to_wav(audio_file):
    """Convert audio file to WAV format for analysis"""
    temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    temp_wav_path = temp_wav.name
    temp_wav.close()
    
    try:
        # Use ffmpeg to convert to WAV
        cmd = ['ffmpeg', '-y', '-i', audio_file, '-ar', '44100', '-ac', '1', temp_wav_path]
        subprocess.run(cmd, capture_output=True, check=True)
        return temp_wav_path
    except Exception as e:
        print(f"⚠️  Error converting audio: {e}")
        return None

def detect_sound_events(audio_data, sample_rate, window_size=2048, hop_size=512):
    """
    Detect sound events (pops, clings, etc.) in audio
    Returns: list of events with timing, type, and characteristics
    """
    events = []
    
    # Calculate energy (RMS) over time
    frame_length = window_size
    hop_length = hop_size
    
    energy = []
    for i in range(0, len(audio_data) - frame_length, hop_length):
        frame = audio_data[i:i + frame_length]
        rms = np.sqrt(np.mean(frame**2))
        energy.append(rms)
    
    energy = np.array(energy)
    times = np.arange(len(energy)) * (hop_length / sample_rate)
    
    # Detect peaks (sudden increases in energy)
    peaks, properties = signal.find_peaks(
        energy,
        height=np.percentile(energy, 75),  # Above 75th percentile
        distance=int(0.1 * sample_rate / hop_length),  # At least 0.1s apart
        prominence=np.std(energy) * 0.5
    )
    
    # Analyze each peak
    for peak_idx in peaks:
        peak_time = times[peak_idx]
        peak_energy = energy[peak_idx]
        
        # Get audio segment around peak
        start_sample = int((peak_time - 0.05) * sample_rate)
        end_sample = int((peak_time + 0.3) * sample_rate)
        start_sample = max(0, start_sample)
        end_sample = min(len(audio_data), end_sample)
        segment = audio_data[start_sample:end_sample]
        
        if len(segment) < 100:
            continue
        
        # Analyze frequency content
        fft = np.fft.rfft(segment)
        freqs = np.fft.rfftfreq(len(segment), 1/sample_rate)
        magnitude = np.abs(fft)
        
        # Find dominant frequencies
        peak_freq_idx = np.argmax(magnitude[1:]) + 1  # Skip DC
        dominant_freq = freqs[peak_freq_idx]
        
        # Analyze spectral characteristics
        # High frequency content (>1000 Hz) = bright sounds (cling, pop)
        # Low frequency content (<500 Hz) = bass sounds (disappointment)
        high_freq_energy = np.sum(magnitude[freqs > 1000])
        low_freq_energy = np.sum(magnitude[freqs < 500])
        mid_freq_energy = np.sum(magnitude[(freqs >= 500) & (freqs <= 1000)])
        
        total_energy = np.sum(magnitude[1:])  # Skip DC
        
        # Calculate attack time (how fast it reaches peak)
        segment_energy = []
        chunk_size = len(segment) // 20
        for i in range(0, len(segment), chunk_size):
            chunk = segment[i:i+chunk_size]
            if len(chunk) > 0:
                segment_energy.append(np.sqrt(np.mean(chunk**2)))
        
        if len(segment_energy) > 1:
            attack_time = np.argmax(segment_energy) * (chunk_size / sample_rate)
        else:
            attack_time = 0
        
        # Determine sound type based on characteristics
        # Use scoring system to determine best match
        scores = {
            'pop': 0,
            'cling': 0,
            'typing': 0,
            'disappointment': 0
        }
        
        # Pop scoring: Very short attack, high frequency, sharp peak
        if attack_time < 0.02:
            scores['pop'] += 3
        if high_freq_energy > total_energy * 0.3:
            scores['pop'] += 2
        if peak_energy > np.mean(energy) * 1.5:
            scores['pop'] += 2
        if 500 < dominant_freq < 1500:
            scores['pop'] += 1
        
        # Cling scoring: High frequency, metallic tone, sustained
        if high_freq_energy > total_energy * 0.4:
            scores['cling'] += 3
        if 800 < dominant_freq < 2500:
            scores['cling'] += 2
        if attack_time < 0.06:
            scores['cling'] += 1
        
        # Typing scoring: Quick repeated clicks
        if len(events) > 0:
            last_event = events[-1]
            time_since_last = peak_time - last_event['time']
            if time_since_last < 0.3:
                scores['typing'] += 3
                # If previous was also typing-like, increase score
                if last_event.get('type') == 'typing' or (attack_time < 0.02 and high_freq_energy > total_energy * 0.3):
                    scores['typing'] += 2
        
        # Disappointment scoring: Low frequency, descending, longer
        if low_freq_energy > total_energy * 0.3:
            scores['disappointment'] += 2
        if dominant_freq < 600:
            scores['disappointment'] += 2
        if len(segment_energy) > 5:
            early_energy = np.mean(segment_energy[:3])
            late_energy = np.mean(segment_energy[-3:])
            if late_energy < early_energy * 0.8:  # Descending
                scores['disappointment'] += 3
        
        # Get best match
        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]
        
        if best_score > 0:
            sound_type = best_type
            confidence = min(0.95, best_score / 5.0)  # Normalize to 0-0.95
        
        if sound_type != "unknown":
            events.append({
                'time': peak_time,
                'type': sound_type,
                'confidence': confidence,
                'dominant_freq': float(dominant_freq),
                'energy': float(peak_energy),
                'attack_time': float(attack_time),
                'high_freq_ratio': float(high_freq_energy / total_energy) if total_energy > 0 else 0,
                'low_freq_ratio': float(low_freq_energy / total_energy) if total_energy > 0 else 0,
            })
    
    return events

def analyze_video_frame_by_frame(video_file, sound_events, output_folder):
    """
    Analyze video frames around sound events to identify visual effects
    (colors, emojis, transitions)
    """
    print(f"🎬 Analyzing video frames for visual effects...")
    
    visual_effects = []
    
    try:
        # Use ffprobe to get frame rate
        cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', 
               '-show_entries', 'stream=r_frame_rate', '-of', 'json', video_file]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(result.stdout)
        frame_rate_str = probe_data['streams'][0]['r_frame_rate']
        num, den = map(int, frame_rate_str.split('/'))
        fps = num / den
        
        print(f"  📹 Video FPS: {fps}")
        
        # For each sound event, extract frames around it
        for event in sound_events:
            event_time = event['time']
            
            # Extract frame at sound time and frames before/after
            frames_to_extract = [
                event_time - 0.05,  # 50ms before
                event_time,         # At sound
                event_time + 0.05,  # 50ms after
                event_time + 0.1,   # 100ms after
            ]
            
            frames_data = []
            
            for frame_time in frames_to_extract:
                if frame_time < 0:
                    continue
                
                # Extract single frame using ffmpeg
                temp_frame = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                temp_frame_path = temp_frame.name
                temp_frame.close()
                
                try:
                    cmd = [
                        'ffmpeg', '-y', '-ss', str(frame_time),
                        '-i', video_file,
                        '-vframes', '1',
                        '-q:v', '2',  # High quality
                        temp_frame_path
                    ]
                    subprocess.run(cmd, capture_output=True, check=True, timeout=5)
                    
                    # Analyze frame colors
                    from PIL import Image
                    img = Image.open(temp_frame_path)
                    img_array = np.array(img)
                    
                    # Get dominant colors
                    if img_array.ndim == 3:
                        # Reshape to 2D array of pixels
                        pixels = img_array.reshape(-1, img_array.shape[-1])
                        
                        # Calculate average color
                        avg_color = np.mean(pixels, axis=0)
                        
                        # Detect bright flashes (green, white, etc.)
                        brightness = np.mean(avg_color)
                        is_flash = brightness > 200  # Very bright
                        
                        # Detect green screen effect
                        if len(avg_color) >= 3:
                            green_ratio = avg_color[1] / np.sum(avg_color) if np.sum(avg_color) > 0 else 0
                            is_green = green_ratio > 0.4 and avg_color[1] > 150
                        else:
                            green_ratio = 0
                            is_green = False
                        
                        frames_data.append({
                            'time': frame_time,
                            'brightness': float(brightness),
                            'avg_color': avg_color.tolist() if isinstance(avg_color, np.ndarray) else [float(c) for c in avg_color],
                            'is_flash': bool(is_flash),
                            'is_green': bool(is_green),
                            'green_ratio': float(green_ratio)
                        })
                    
                    # Clean up
                    os.unlink(temp_frame_path)
                    
                except Exception as e:
                    if os.path.exists(temp_frame_path):
                        os.unlink(temp_frame_path)
                    continue
            
            # Determine visual effect type
            visual_effect = None
            if frames_data:
                at_sound_frame = next((f for f in frames_data if abs(f['time'] - event_time) < 0.01), None)
                after_sound_frame = next((f for f in frames_data if f['time'] > event_time), None)
                
                if at_sound_frame and after_sound_frame:
                    if at_sound_frame['is_green'] or after_sound_frame['is_green']:
                        visual_effect = "green_flash"
                    elif at_sound_frame['is_flash'] or after_sound_frame['is_flash']:
                        visual_effect = "white_flash"
                    elif after_sound_frame['brightness'] > at_sound_frame['brightness'] * 1.2:
                        visual_effect = "brightness_increase"
            
            visual_effects.append({
                'sound_event': event,
                'frames_analysis': frames_data,
                'visual_effect': visual_effect
            })
        
        return visual_effects
        
    except Exception as e:
        print(f"  ⚠️  Error analyzing video: {e}")
        import traceback
        traceback.print_exc()
        return []

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_sounds.py <download_folder>")
        print("\nExample:")
        print("  python analyze_sounds.py youtube_download_fiZthsmIOWE")
        sys.exit(1)
    
    download_folder = sys.argv[1]
    
    if not os.path.exists(download_folder):
        print(f"❌ Folder not found: {download_folder}")
        sys.exit(1)
    
    # Find audio and video files
    audio_file = None
    video_file = None
    
    for file in os.listdir(download_folder):
        if file.endswith(('.m4a', '.mp3', '.webm', '.opus')) and 'audio' in file.lower():
            audio_file = os.path.join(download_folder, file)
        elif file.endswith(('.mp4', '.webm', '.mkv')) and 'video' in file.lower():
            video_file = os.path.join(download_folder, file)
    
    if not audio_file:
        print(f"❌ Audio file not found in {download_folder}")
        sys.exit(1)
    
    if not video_file:
        print(f"❌ Video file not found in {download_folder}")
        sys.exit(1)
    
    print(f"🎵 Analyzing audio: {audio_file}")
    print(f"🎬 Analyzing video: {video_file}")
    print()
    
    # Convert audio to WAV if needed
    wav_file = None
    if audio_file.endswith('.wav'):
        wav_file = audio_file
    else:
        print("📝 Converting audio to WAV for analysis...")
        wav_file = convert_audio_to_wav(audio_file)
        if not wav_file:
            print("❌ Failed to convert audio")
            sys.exit(1)
    
    try:
        # Load audio
        print("📊 Loading audio data...")
        sample_rate, audio_data = wavfile.read(wav_file)
        
        # Convert to mono if stereo
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)
        
        # Normalize to float32
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32768.0
        elif audio_data.dtype == np.int32:
            audio_data = audio_data.astype(np.float32) / 2147483648.0
        
        print(f"  ✅ Loaded {len(audio_data)/sample_rate:.2f}s of audio at {sample_rate}Hz")
        print()
        
        # Detect sound events
        print("🔍 Detecting sound events...")
        sound_events = detect_sound_events(audio_data, sample_rate)
        print(f"  ✅ Found {len(sound_events)} sound event(s)")
        print()
        
        # Print detected sounds
        for i, event in enumerate(sound_events, 1):
            print(f"  {i}. {event['type'].upper()} at {event['time']:.2f}s")
            print(f"     Confidence: {event['confidence']:.2f}")
            print(f"     Dominant freq: {event['dominant_freq']:.0f} Hz")
            print(f"     Attack time: {event['attack_time']*1000:.1f}ms")
            print()
        
        # Analyze video for visual effects
        visual_effects = analyze_video_frame_by_frame(video_file, sound_events, download_folder)
        
        # Combine analysis
        print("=" * 60)
        print("📋 COMPLETE ANALYSIS")
        print("=" * 60)
        
        for i, effect_data in enumerate(visual_effects, 1):
            event = effect_data['sound_event']
            print(f"\n{i}. {event['type'].upper()} at {event['time']:.2f}s")
            print(f"   Audio Characteristics:")
            print(f"     - Frequency: {event['dominant_freq']:.0f} Hz")
            print(f"     - Attack: {event['attack_time']*1000:.1f}ms")
            print(f"     - High freq ratio: {event['high_freq_ratio']:.2f}")
            print(f"     - Low freq ratio: {event['low_freq_ratio']:.2f}")
            
            if effect_data['visual_effect']:
                print(f"   Visual Effect: {effect_data['visual_effect']}")
                frames = effect_data['frames_analysis']
                if frames:
                    at_sound = next((f for f in frames if abs(f['time'] - event['time']) < 0.01), None)
                    if at_sound:
                        color = at_sound['avg_color']
                        if len(color) >= 3:
                            print(f"     - Color at sound: RGB({int(color[0])}, {int(color[1])}, {int(color[2])})")
                        if at_sound['is_green']:
                            print(f"     - ✓ GREEN SCREEN FLASH detected")
                        if at_sound['is_flash']:
                            print(f"     - ✓ BRIGHT FLASH detected")
        
        # Save analysis to JSON (convert numpy types to native Python types)
        def convert_to_serializable(obj):
            """Recursively convert numpy types to native Python types"""
            if isinstance(obj, (np.integer, np.floating)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {k: convert_to_serializable(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_to_serializable(item) for item in obj]
            elif isinstance(obj, (bool, np.bool_)):
                return bool(obj)
            return obj
        
        analysis_file = os.path.join(download_folder, 'sound_analysis.json')
        with open(analysis_file, 'w') as f:
            json.dump(convert_to_serializable({
                'sound_events': sound_events,
                'visual_effects': visual_effects
            }), f, indent=2)
        
        print(f"\n✅ Analysis saved to: {analysis_file}")
        
    finally:
        # Cleanup temp WAV file if created
        if wav_file and wav_file != audio_file and os.path.exists(wav_file):
            os.unlink(wav_file)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Analysis interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

