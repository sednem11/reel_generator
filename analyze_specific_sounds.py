#!/usr/bin/env python3
"""
Analyze specific sound events at exact timestamps from the video
Extract audio samples and analyze their characteristics
"""

import os
import sys
import json
import subprocess
import tempfile
import numpy as np
from scipy.io import wavfile
from scipy import signal

# Specific timestamps and descriptions from user
SOUND_EVENTS = [
    {"time": 4.0, "name": "cling_approval", "description": "Cling sound of approval - when she says 17 pro max"},
    {"time": 7.0, "name": "disappointment_wasted", "description": "Sound of disappointment (GTA wasted meme)"},
    {"time": 13.0, "name": "unknown_good", "description": "Good sound - when she said 2 terabytes"},
    {"time": 16.0, "name": "confirmation_soft", "description": "Soft confirmation sound"},
    {"time": 16.5, "name": "pop_emoji", "description": "Pop sound for emoji (1 of 3)"},
    {"time": 17.0, "name": "pop_emoji", "description": "Pop sound for emoji (2 of 3)"},
    {"time": 18.0, "name": "pop_emoji", "description": "Pop sound for emoji (3 of 3)"},
    {"time": 25.0, "name": "shock", "description": "Shock sound when speed shows broken phone"},
]

def convert_audio_to_wav(audio_file):
    """Convert audio file to WAV format for analysis"""
    temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    temp_wav_path = temp_wav.name
    temp_wav.close()
    
    try:
        cmd = ['ffmpeg', '-y', '-i', audio_file, '-ar', '44100', '-ac', '1', temp_wav_path]
        subprocess.run(cmd, capture_output=True, check=True)
        return temp_wav_path
    except Exception as e:
        print(f"⚠️  Error converting audio: {e}")
        return None

def extract_audio_segment(audio_file, start_time, duration, output_file):
    """Extract a segment of audio for analysis"""
    try:
        cmd = [
            'ffmpeg', '-y',
            '-i', audio_file,
            '-ss', str(max(0, start_time - 0.2)),  # Start slightly before
            '-t', str(duration + 0.4),  # Include some after
            '-ar', '44100',
            '-ac', '1',
            output_file
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except Exception as e:
        print(f"  ⚠️  Error extracting segment: {e}")
        return False

def analyze_sound_characteristics(audio_data, sample_rate, event_time):
    """Analyze detailed characteristics of a sound event"""
    # Find the peak around the event time
    event_sample = int(event_time * sample_rate)
    
    # Extract segment around event (200ms before to 500ms after)
    start_sample = max(0, event_sample - int(0.2 * sample_rate))
    end_sample = min(len(audio_data), event_sample + int(0.5 * sample_rate))
    segment = audio_data[start_sample:end_sample]
    
    if len(segment) < 100:
        return None
    
    # Calculate RMS energy
    rms = np.sqrt(np.mean(segment**2))
    
    # FFT analysis
    fft = np.fft.rfft(segment)
    freqs = np.fft.rfftfreq(len(segment), 1/sample_rate)
    magnitude = np.abs(fft)
    
    # Find dominant frequency
    dominant_idx = np.argmax(magnitude[1:]) + 1  # Skip DC
    dominant_freq = freqs[dominant_idx]
    
    # Frequency band analysis
    high_freq_energy = np.sum(magnitude[freqs > 1000])
    mid_freq_energy = np.sum(magnitude[(freqs >= 300) & (freqs <= 1000)])
    low_freq_energy = np.sum(magnitude[freqs < 300])
    total_energy = np.sum(magnitude[1:])
    
    # Calculate attack time (time to reach 80% of peak)
    segment_abs = np.abs(segment)
    peak_value = np.max(segment_abs)
    peak_idx = np.argmax(segment_abs)
    threshold = peak_value * 0.8
    
    # Find when it reaches threshold
    attack_sample = None
    for i in range(peak_idx, -1, -1):
        if segment_abs[i] >= threshold:
            attack_sample = i
        else:
            break
    
    if attack_sample is None:
        attack_sample = 0
    
    attack_time = attack_sample / sample_rate
    
    # Calculate decay time (time from peak to 20% of peak)
    decay_sample = None
    for i in range(peak_idx, len(segment_abs)):
        if segment_abs[i] <= peak_value * 0.2:
            decay_sample = i
            break
    
    if decay_sample:
        decay_time = (decay_sample - peak_idx) / sample_rate
    else:
        decay_time = (len(segment) - peak_idx) / sample_rate
    
    # Duration (time above 10% of peak)
    threshold_10 = peak_value * 0.1
    above_threshold = segment_abs > threshold_10
    if np.any(above_threshold):
        start_idx = np.where(above_threshold)[0][0]
        end_idx = np.where(above_threshold)[0][-1]
        duration = (end_idx - start_idx) / sample_rate
    else:
        duration = len(segment) / sample_rate
    
    # Pitch analysis (detect if frequency changes)
    # Split into small windows and track frequency
    window_size = len(segment) // 10
    if window_size < 100:
        window_size = 100
    
    pitch_trend = []
    for i in range(0, len(segment) - window_size, window_size // 2):
        window = segment[i:i+window_size]
        window_fft = np.fft.rfft(window)
        window_freqs = np.fft.rfftfreq(len(window), 1/sample_rate)
        window_mag = np.abs(window_fft)
        window_dom_idx = np.argmax(window_mag[1:]) + 1
        window_dom_freq = window_freqs[window_dom_idx]
        if window_dom_freq > 0 and window_dom_freq < 5000:
            pitch_trend.append(window_dom_freq)
    
    if len(pitch_trend) >= 3:
        pitch_direction = "descending" if pitch_trend[-1] < pitch_trend[0] * 0.8 else "ascending" if pitch_trend[-1] > pitch_trend[0] * 1.2 else "stable"
    else:
        pitch_direction = "stable"
    
    return {
        'rms': float(rms),
        'dominant_freq': float(dominant_freq),
        'high_freq_ratio': float(high_freq_energy / total_energy) if total_energy > 0 else 0,
        'mid_freq_ratio': float(mid_freq_energy / total_energy) if total_energy > 0 else 0,
        'low_freq_ratio': float(low_freq_energy / total_energy) if total_energy > 0 else 0,
        'attack_time': float(attack_time),
        'decay_time': float(decay_time),
        'duration': float(duration),
        'peak_amplitude': float(peak_value),
        'pitch_direction': pitch_direction,
        'pitch_trend': [float(f) for f in pitch_trend[:10]] if pitch_trend else [],
    }

def generate_sound_code(sound_name, characteristics):
    """Generate code to recreate the sound based on characteristics"""
    freq = characteristics['dominant_freq']
    attack = characteristics['attack_time']
    decay = characteristics['decay_time']
    duration = characteristics['duration']
    high_ratio = characteristics['high_freq_ratio']
    
    code = f"""
    # {sound_name} sound
    # Characteristics: {freq:.0f}Hz, attack:{attack*1000:.1f}ms, decay:{decay*1000:.1f}ms, duration:{duration*1000:.1f}ms
    duration_samples = int({duration:.3f} * sample_rate)
    samples = np.zeros(duration_samples, dtype=np.float32)
    
    for i in range(duration_samples):
        t = i / sample_rate
        freq = {freq:.1f}
        # Attack: {attack*1000:.1f}ms, Decay: {decay*1000:.1f}ms
        attack_env = 1.0 if t < {attack:.3f} else np.exp(-(t - {attack:.3f}) / {decay:.3f})
        tone = np.sin(2 * np.pi * freq * t) * attack_env
        samples[i] = tone * 0.7
"""
    return code

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_specific_sounds.py <download_folder>")
        sys.exit(1)
    
    download_folder = sys.argv[1]
    
    # Find audio file
    audio_file = None
    for file in os.listdir(download_folder):
        if file.endswith(('.m4a', '.mp3', '.webm', '.opus')) and 'audio' in file.lower():
            audio_file = os.path.join(download_folder, file)
            break
    
    if not audio_file:
        print(f"❌ Audio file not found in {download_folder}")
        sys.exit(1)
    
    print(f"🎵 Analyzing specific sounds from: {audio_file}")
    print(f"📋 Analyzing {len(SOUND_EVENTS)} sound events\n")
    
    # Convert to WAV for analysis
    print("📝 Converting audio to WAV...")
    wav_file = convert_audio_to_wav(audio_file)
    if not wav_file:
        print("❌ Failed to convert audio")
        sys.exit(1)
    
    # Load full audio
    print("📊 Loading audio data...")
    sample_rate, audio_data = wavfile.read(wav_file)
    
    # Convert to mono and float32
    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)
    if audio_data.dtype == np.int16:
        audio_data = audio_data.astype(np.float32) / 32768.0
    elif audio_data.dtype == np.int32:
        audio_data = audio_data.astype(np.float32) / 2147483648.0
    
    print(f"  ✅ Loaded {len(audio_data)/sample_rate:.2f}s of audio at {sample_rate}Hz\n")
    
    # Create samples folder
    samples_folder = os.path.join(download_folder, 'specific_sound_samples')
    os.makedirs(samples_folder, exist_ok=True)
    
    # Analyze each sound
    results = []
    
    for event in SOUND_EVENTS:
        time = event['time']
        name = event['name']
        description = event['description']
        
        print(f"🔊 {name} at {time:.1f}s - {description}")
        
        # Extract audio sample
        sample_file = os.path.join(samples_folder, f"{name}_{time:.1f}s.wav")
        if extract_audio_segment(wav_file, time, 0.8, sample_file):
            print(f"  ✅ Extracted sample")
        
        # Analyze characteristics
        char = analyze_sound_characteristics(audio_data, sample_rate, time)
        
        if char:
            print(f"  📊 Frequency: {char['dominant_freq']:.0f} Hz")
            print(f"  ⚡ Attack: {char['attack_time']*1000:.1f}ms, Decay: {char['decay_time']*1000:.1f}ms")
            print(f"  ⏱️  Duration: {char['duration']*1000:.1f}ms")
            print(f"  📈 Energy: High:{char['high_freq_ratio']:.2f}, Mid:{char['mid_freq_ratio']:.2f}, Low:{char['low_freq_ratio']:.2f}")
            print(f"  🎵 Pitch: {char['pitch_direction']}")
            print()
            
            results.append({
                **event,
                'characteristics': char,
                'sample_file': sample_file
            })
        else:
            print(f"  ⚠️  Could not analyze sound")
            print()
    
    # Save results
    output_file = os.path.join(download_folder, 'specific_sounds_analysis.json')
    
    def convert_to_serializable(obj):
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
    
    with open(output_file, 'w') as f:
        json.dump(convert_to_serializable(results), f, indent=2)
    
    print("=" * 70)
    print("📋 ANALYSIS SUMMARY")
    print("=" * 70)
    
    for result in results:
        char = result['characteristics']
        print(f"\n{result['name'].upper()} at {result['time']:.1f}s")
        print(f"  {result['description']}")
        print(f"  Frequency: {char['dominant_freq']:.0f} Hz")
        print(f"  Attack: {char['attack_time']*1000:.1f}ms, Decay: {char['decay_time']*1000:.1f}ms, Duration: {char['duration']*1000:.1f}ms")
        print(f"  Energy distribution: High:{char['high_freq_ratio']:.2f} Mid:{char['mid_freq_ratio']:.2f} Low:{char['low_freq_ratio']:.2f}")
        print(f"  Pitch trend: {char['pitch_direction']}")
    
    print(f"\n✅ Analysis saved to: {output_file}")
    print(f"✅ Samples saved to: {samples_folder}")
    
    # Cleanup temp WAV file
    if wav_file and wav_file != audio_file and os.path.exists(wav_file):
        os.unlink(wav_file)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Analysis interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

