from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    VideoUnavailable,
    YouTubeRequestFailed,
    IpBlocked,
    RequestBlocked
)
import time
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, TextClip
import openai
import json
import subprocess
from urllib.parse import urlparse
import requests
import os
import re
import tempfile
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from pilmoji import Pilmoji
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
from dotenv import load_dotenv
import whisper
import threading

# Load environment variables from .env file
load_dotenv()

# Whisper model cache to avoid reloading models (saves memory and time)
# Models are thread-safe and can be shared across jobs
_whisper_model_cache = {}
_whisper_model_lock = threading.Lock()

# Fix for Pillow 10+ compatibility: Image.ANTIALIAS is deprecated
# Patch it before moviepy uses it
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

def add_watermark(video_clip, watermark_path=None, position='bottom-right', opacity=0.35):
    """
    Add watermark to video clip.
    
    Args:
        video_clip: MoviePy VideoClip object
        watermark_path: Path to watermark image (default: tesoura.png from frontend/public)
        position: 'bottom-right', 'bottom-left', 'top-right', 'top-left'
        opacity: Watermark opacity (0.0 to 1.0)
    
    Returns:
        VideoClip with watermark added
    """
    try:
        # Default watermark path
        if watermark_path is None:
            # Try to find the watermark in frontend/public
            possible_paths = [
                'frontend/public/tesoura.png',
                '/opt/reel_generator/frontend/public/tesoura.png',
                'tesoura.png'
            ]
            watermark_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    watermark_path = path
                    break
            
            if watermark_path is None:
                print("⚠️  Watermark image not found, skipping watermark")
                return video_clip
        
        # Load watermark image
        watermark_img = Image.open(watermark_path)
        
        # Resize watermark to be 15% of video height (or max 150px) - bigger watermark
        video_height = video_clip.h
        watermark_size = min(int(video_height * 0.15), 150)
        watermark_img = watermark_img.resize((watermark_size, watermark_size), Image.Resampling.LANCZOS)
        
        # Apply opacity
        if opacity < 1.0:
            watermark_img = watermark_img.convert("RGBA")
            alpha = watermark_img.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            watermark_img.putalpha(alpha)
        
        # Save watermark to temp file
        import tempfile
        temp_watermark = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        watermark_img.save(temp_watermark.name)
        temp_watermark.close()
        
        # Create ImageClip from watermark
        watermark_clip = ImageClip(temp_watermark.name)
        watermark_clip = watermark_clip.set_duration(video_clip.duration)
        watermark_clip = watermark_clip.set_fps(video_clip.fps)
        
        # Position watermark
        video_width = video_clip.w
        video_height = video_clip.h
        watermark_width = watermark_clip.w
        watermark_height = watermark_clip.h
        
        margin = 20  # Margin from edges
        bottom_margin = 50  # Larger bottom margin to position watermark higher
        
        if position == 'bottom-right':
            x_pos = video_width - watermark_width - margin
            y_pos = video_height - watermark_height - bottom_margin
        elif position == 'bottom-left':
            x_pos = margin
            y_pos = video_height - watermark_height - bottom_margin
        elif position == 'top-right':
            x_pos = video_width - watermark_width - margin
            y_pos = margin
        elif position == 'top-left':
            x_pos = margin
            y_pos = margin
        else:
            # Default to bottom-right
            x_pos = video_width - watermark_width - margin
            y_pos = video_height - watermark_height - bottom_margin
        
        watermark_clip = watermark_clip.set_position((x_pos, y_pos))
        
        # Composite watermark with video
        final_clip = CompositeVideoClip([video_clip, watermark_clip])
        
        # Preserve audio
        if video_clip.audio is not None:
            final_clip = final_clip.set_audio(video_clip.audio)
        
        # Clean up temp file after a delay (let MoviePy finish using it)
        def cleanup_temp():
            import time
            time.sleep(2)  # Wait a bit for MoviePy to finish
            try:
                if os.path.exists(temp_watermark.name):
                    os.remove(temp_watermark.name)
            except:
                pass
        
        import threading
        cleanup_thread = threading.Thread(target=cleanup_temp, daemon=True)
        cleanup_thread.start()
        
        return final_clip
        
    except Exception as e:
        print(f"⚠️  Error adding watermark: {e}")
        import traceback
        traceback.print_exc()
        # Return original clip if watermark fails
        return video_clip

def get_quality_params(quality="hd"):
    """
    Get ffmpeg parameters based on quality setting.
    
    Args:
        quality: "full_hd" (1080p), "hd" (720p), or "normal" (480p)
    
    Returns:
        Dictionary with resolution, crf, and preset settings
    """
    quality_settings = {
        "full_hd": {
            "resolution": (1080, 1920),  # 1080p vertical (9:16)
            "crf": "20",  # Higher quality
            "preset": "slow",  # Better compression
            "bitrate": "5000k"
        },
        "hd": {
            "resolution": (720, 1280),  # 720p vertical (9:16)
            "crf": "23",  # Balanced quality
            "preset": "medium",  # Balanced speed
            "bitrate": "3000k"
        },
        "normal": {
            "resolution": (480, 854),  # 480p vertical (9:16)
            "crf": "26",  # Lower quality, faster
            "preset": "fast",  # Faster encoding
            "bitrate": "1500k"
        }
    }
    return quality_settings.get(quality.lower(), quality_settings["hd"])


def parse_srt_file(srt_path):
    """
    Parse an SRT subtitle file and convert it to transcript format.
    
    Args:
        srt_path: Path to the SRT file
    
    Returns:
        List of dicts with 'text', 'start', and 'duration' keys
    """
    transcript = []
    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by double newlines to get individual subtitle blocks
        blocks = content.strip().split('\n\n')
        
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            
            # Skip the sequence number (first line)
            # Parse timestamp (second line) - format: "00:00:00,000 --> 00:00:02,500"
            time_line = lines[1]
            if '-->' not in time_line:
                continue
            
            start_str, end_str = time_line.split('-->')
            start_str = start_str.strip()
            end_str = end_str.strip()
            
            # Convert SRT timestamp to seconds
            def srt_to_seconds(srt_time):
                # Format: "00:00:00,000" or "00:00:00.000"
                srt_time = srt_time.replace(',', '.')
                parts = srt_time.split(':')
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            
            start_time = srt_to_seconds(start_str)
            end_time = srt_to_seconds(end_str)
            duration = end_time - start_time
            
            # Get text (all remaining lines)
            text = ' '.join(lines[2:]).strip()
            
            # Remove HTML tags if present
            text = re.sub(r'<[^>]+>', '', text)
            
            if text:
                transcript.append({
                    'text': text,
                    'start': start_time,
                    'duration': duration
                })
        
        return transcript
    except Exception as e:
        print(f"⚠️  Error parsing SRT file: {e}")
        return None


def get_transcript(video_id, max_retries=3, retry_delay=5):
    """
    Get transcript from YouTube video.
    Primary method: YouTube Transcript API (no proxies)
    Fallback method: yt-dlp to download subtitles
    
    Args:
        video_id: YouTube video ID
        max_retries: Maximum number of retry attempts for API
        retry_delay: Initial delay between retries in seconds (exponential backoff)
    
    Returns:
        Tuple of (transcript_text, transcript_data) or (None, None) on failure
    """
    # Try YouTube Transcript API first (no proxies - running on localhost)
    api = YouTubeTranscriptApi()
    
    for attempt in range(max_retries):
        try:
            fetched_transcript = api.fetch(video_id)
            
            # Convert FetchedTranscript to the old format (list of dicts)
            transcript = []
            for snippet in fetched_transcript:
                transcript.append({
                    'text': snippet.text,
                    'start': snippet.start,
                    'duration': snippet.duration
                })
            
            text_blocks = [entry['text'] for entry in transcript]
            print("✅ Successfully retrieved transcript from YouTube API")
            return "\n".join(text_blocks), transcript
            
        except TranscriptsDisabled:
            print("⚠️  Subtitles disabled for this video. Trying yt-dlp fallback...")
            break  # Exit retry loop and try yt-dlp
            
        except (IpBlocked, RequestBlocked, CouldNotRetrieveTranscript, YouTubeRequestFailed) as e:
            error_str = str(e)
            # If IP blocking or request blocking, try yt-dlp immediately
            if "IP" in error_str or "blocked" in error_str.lower() or "cloud provider" in error_str.lower():
                print("⚠️  YouTube API blocked. Trying yt-dlp fallback...")
                break  # Exit retry loop and try yt-dlp
            else:
                # Other errors - retry
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"⚠️  Request failed. Retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    print("⚠️  YouTube API failed after retries. Trying yt-dlp fallback...")
                    break  # Exit retry loop and try yt-dlp
                
        except (NoTranscriptFound, VideoUnavailable) as e:
            print("⚠️  Transcript not found via API. Trying yt-dlp fallback...")
            break  # Exit retry loop and try yt-dlp
            
        except Exception as e:
            error_str = str(e)
            if "IP" in error_str or "blocked" in error_str.lower() or "cloud provider" in error_str.lower():
                print("⚠️  YouTube API blocked. Trying yt-dlp fallback...")
                break  # Exit retry loop and try yt-dlp
            else:
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"⚠️  Unexpected error. Retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    print("⚠️  YouTube API failed. Trying yt-dlp fallback...")
                    break  # Exit retry loop and try yt-dlp
    
    # Fallback: Use yt-dlp to download subtitles
    print("📥 Attempting to download subtitles with yt-dlp...")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    
    try:
        # Create temporary directory for subtitle file
        with tempfile.TemporaryDirectory() as temp_dir:
            subtitle_path = os.path.join(temp_dir, f"{video_id}.%(ext)s")
            
            # Try to download subtitles with yt-dlp
            # First try auto-generated subtitles (more likely to exist)
            yt_dlp_cmd = [
                "yt-dlp",
                "--skip-download",  # Only download subtitles, not video
                "--write-auto-sub",  # Download auto-generated subtitles
                "--write-sub",  # Also try manual subtitles
                "--sub-lang", "en,en-US,en-GB",  # Prefer English
                "--sub-format", "srt",  # Request SRT format
                "-o", subtitle_path,
                "--no-warnings",
                video_url
            ]
            
            result = subprocess.run(
                yt_dlp_cmd,
                capture_output=True,
                text=True,
                timeout=60  # 60 second timeout
            )
            
            if result.returncode == 0:
                # Find the downloaded subtitle file
                subtitle_files = []
                for ext in ['.srt', '.vtt']:
                    # yt-dlp might add language code, try different patterns
                    patterns = [
                        os.path.join(temp_dir, f"{video_id}.en{ext}"),
                        os.path.join(temp_dir, f"{video_id}.en-US{ext}"),
                        os.path.join(temp_dir, f"{video_id}.en-GB{ext}"),
                        os.path.join(temp_dir, f"{video_id}{ext}"),
                    ]
                    for pattern in patterns:
                        if os.path.exists(pattern):
                            subtitle_files.append(pattern)
                            break
                
                # Also search for any .srt or .vtt files in temp_dir
                if not subtitle_files:
                    for file in os.listdir(temp_dir):
                        if file.endswith(('.srt', '.vtt')):
                            subtitle_files.append(os.path.join(temp_dir, file))
                
                if subtitle_files:
                    # Use the first found subtitle file
                    subtitle_file = subtitle_files[0]
                    print(f"✅ Found subtitle file: {os.path.basename(subtitle_file)}")
                    
                    # Parse SRT file
                    if subtitle_file.endswith('.srt'):
                        transcript = parse_srt_file(subtitle_file)
                    else:
                        # For VTT, we'd need a different parser, but for now try to parse as SRT
                        # (VTT format is similar)
                        transcript = parse_srt_file(subtitle_file)
                    
                    if transcript:
                        text_blocks = [entry['text'] for entry in transcript]
                        print("✅ Successfully retrieved transcript using yt-dlp")
                        return "\n".join(text_blocks), transcript
                    else:
                        print("⚠️  Failed to parse subtitle file")
                else:
                    print("⚠️  Subtitle file not found after download")
            else:
                error_output = result.stderr or result.stdout
                print(f"⚠️  yt-dlp failed: {error_output[:200] if error_output else 'Unknown error'}")
    
    except subprocess.TimeoutExpired:
        print("⚠️  yt-dlp timeout while downloading subtitles")
    except FileNotFoundError:
        print("⚠️  yt-dlp not found. Please install it: pip install yt-dlp")
    except Exception as e:
        print(f"⚠️  Error using yt-dlp fallback: {e}")
    
    # If all methods failed
    print("❌ Failed to retrieve transcript using both YouTube API and yt-dlp")
    return None, None


def get_transcript_with_whisper(video_file, model_name="base", language=None):
    """
    Get transcript from video file using Whisper (local transcription).
    This works for any video platform and doesn't require YouTube API.
    
    Uses model caching to avoid reloading the same model multiple times,
    which saves memory and improves performance.
    
    Args:
        video_file: Path to video file
        model_name: Whisper model to use ("tiny", "base", "small", "medium", "large", "turbo")
                   Default: "base" (good balance of speed and accuracy)
        language: Language code (e.g., "en", "es", "fr"). If None, auto-detects.
    
    Returns:
        Tuple of (transcript_text, transcript_data) or (None, None) on failure
        transcript_data format matches YouTube transcript API: [{'text': str, 'start': float, 'duration': float}]
    """
    try:
        print(f"🎤 Transcribing video with Whisper (model: {model_name})...")
        
        # Load Whisper model with caching (thread-safe)
        # This prevents loading the same model multiple times, saving memory
        with _whisper_model_lock:
            if model_name not in _whisper_model_cache:
                print(f"📦 Loading Whisper model '{model_name}' (first use, will be cached)...")
                _whisper_model_cache[model_name] = whisper.load_model(model_name)
                print(f"✅ Whisper model '{model_name}' loaded and cached")
            else:
                print(f"♻️  Reusing cached Whisper model '{model_name}'")
            model = _whisper_model_cache[model_name]
        
        # Transcribe video
        # whisper.transcribe returns a dict with 'text' and 'segments'
        result = model.transcribe(
            video_file,
            language=language,
            verbose=False  # Suppress progress output
        )
        
        # Convert Whisper output to YouTube transcript API format
        transcript_text = result["text"]
        transcript_data = []
        
        for segment in result["segments"]:
            transcript_data.append({
                'text': segment['text'].strip(),
                'start': segment['start'],
                'duration': segment['end'] - segment['start']
            })
        
        print(f"✅ Transcription complete! ({len(transcript_data)} segments)")
        return transcript_text, transcript_data
        
    except Exception as e:
        print(f"❌ Error transcribing with Whisper: {e}")
        import traceback
        traceback.print_exc()
        return None, None


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def is_youtube_url(url: str) -> bool:
    """Return True if the URL points to YouTube."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return host in YOUTUBE_HOSTS
    except Exception:
        return False


def is_direct_video_url(url: str) -> bool:
    """Best-effort check for direct video file URLs."""
    try:
        path = urlparse(url).path
        ext = os.path.splitext(path)[1].lower()
        return ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
    except Exception:
        return False


def download_direct_file(url: str, filename: str, check_cancelled=None) -> str:
    """
    Download a direct video file URL using streaming HTTP.
    Only supports direct file URLs; no site scraping.
    """
    http_proxy = os.getenv("YOUTUBE_PROXY_HTTP")
    https_proxy = os.getenv("YOUTUBE_PROXY_HTTPS")
    proxy_url = https_proxy or http_proxy
    proxies = None
    if proxy_url:
        proxies = {"http": http_proxy or proxy_url, "https": https_proxy or proxy_url}

    response = requests.get(url, stream=True, timeout=(10, 30), proxies=proxies)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if not is_direct_video_url(url):
        if not content_type.startswith("video/") and content_type not in {"application/octet-stream"}:
            raise ValueError("URL must point to a direct video file.")

    with open(filename, "wb") as output_file:
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if check_cancelled and check_cancelled():
                raise InterruptedError("Download cancelled by user")
            if chunk:
                output_file.write(chunk)

    return filename


def download_video(url, filename="base_video.mp4", max_retries=3, retry_delay=5, quality="hd"):
    """
    Download a direct video file URL.
    YouTube URLs are not supported for server-side downloads.
    """
    if is_youtube_url(url):
        raise ValueError("YouTube URLs are not supported for server-side downloads. Please upload the file or provide a direct video file URL.")

    for attempt in range(max_retries):
        try:
            print("📥 Downloading direct video file...")
            return download_direct_file(url, filename)
        except InterruptedError:
            raise
        except Exception as e:
            error_msg = str(e)
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                print(f"⚠️  Download failed. Retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            raise Exception(f"Direct download failed after {max_retries} attempts: {error_msg}")


def download_video_with_cancellation(url, filename="base_video.mp4", max_retries=3, retry_delay=5, quality="hd", check_cancelled=None, process_handle_ref=None):
    """
    Download a direct video file URL with cancellation support.
    Same as download_video but supports cancellation checks.
    """
    import logging
    logger = logging.getLogger(__name__)
    if is_youtube_url(url):
        raise ValueError("YouTube URLs are not supported for server-side downloads. Please upload the file or provide a direct video file URL.")

    for attempt in range(max_retries):
        if check_cancelled and check_cancelled():
            logger.info("Download cancelled before starting")
            raise InterruptedError("Download cancelled by user")

        try:
            print("📥 Downloading direct video file...")
            return download_direct_file(url, filename, check_cancelled=check_cancelled)
        except InterruptedError:
            raise
        except Exception as e:
            if check_cancelled and check_cancelled():
                raise InterruptedError("Download cancelled by user")
            error_msg = str(e)
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                print(f"⚠️  Download failed. Retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            raise Exception(f"Direct download failed after {max_retries} attempts: {error_msg}")


def get_word_timestamps_with_llm(text, snippet_start, snippet_end):
    """
    Use LLM to estimate exact word timestamps based on speech patterns.
    Returns a list of dicts with 'word', 'start', 'end' keys.
    """
    words = re.findall(r'\S+', text)
    if not words or len(words) == 0:
        return []
    
    snippet_duration = snippet_end - snippet_start
    
    # Prepare words list for LLM (preserve exact order and punctuation)
    words_list_str = ", ".join([f'"{w}"' for w in words])
    
    # Prepare prompt for LLM
    prompt = f"""You are an expert at estimating speech timing. Given a text snippet and its total duration, estimate when each word starts and ends.

Text: "{text}"
Total duration: {snippet_duration:.3f} seconds
Words (in exact order): [{words_list_str}]
Number of words: {len(words)}

IMPORTANT: 
- You MUST include ALL words in the exact order shown above
- Each word must have timestamps that don't overlap
- Consider natural speech patterns: longer words take slightly longer to say
- Punctuation indicates brief pauses (add small gaps)
- Common speech rate is approximately 2-3 words per second
- Some words may be spoken faster or slower depending on emphasis

Return ONLY valid JSON in this exact format:
{{
  "word_timestamps": [
    {{"word": "{words[0]}", "start": 0.000, "end": 0.XXX}},
    {{"word": "{words[1] if len(words) > 1 else ''}", "start": 0.XXX, "end": 0.XXX}},
    ...
  ]
}}

CRITICAL REQUIREMENTS:
- The first word must start at exactly 0.000
- The last word must end at exactly {snippet_duration:.3f}
- All words must be included in the EXACT ORDER shown above - word 1 comes before word 2, word 2 before word 3, etc.
- Each word's 'start' time MUST be GREATER THAN OR EQUAL TO the previous word's 'end' time
- Words MUST appear in sequence: word[i].start >= word[i-1].end (no backward timestamps)
- Each word's 'start' should equal the previous word's 'end' (no gaps unless punctuation suggests a pause)
- NEVER assign a timestamp to a later word that is earlier than a previous word's timestamp
- Use 3 decimal places for precision
"""
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3  # Lower temperature for more consistent timing
        )
        
        json_output = response.choices[0].message.content.strip()
        # Remove markdown code blocks if present
        if json_output.startswith("```json"):
            json_output = json_output[7:]
        if json_output.startswith("```"):
            json_output = json_output[3:]
        if json_output.endswith("```"):
            json_output = json_output[:-3]
        json_output = json_output.strip()
        
        result = json.loads(json_output)
        llm_timestamps = result.get("word_timestamps", [])
        
        # Validate that we got all words and match them in order
        if len(llm_timestamps) != len(words):
            raise ValueError(f"LLM returned {len(llm_timestamps)} words but expected {len(words)}")
        
        # Ensure words match in order
        validated_timestamps = []
        for i, word in enumerate(words):
            if i < len(llm_timestamps):
                llm_word = llm_timestamps[i].get('word', '')
                # Use word from LLM but validate order
                validated_timestamps.append({
                    'word': word,  # Use original word to preserve exact punctuation/spacing
                    'start': llm_timestamps[i].get('start', 0),
                    'end': llm_timestamps[i].get('end', 0)
                })
        
        return validated_timestamps
    except Exception as e:
        print(f"⚠️  LLM timestamp estimation failed: {e}, falling back to uniform distribution")
        # Fallback to uniform distribution
        time_per_word = snippet_duration / len(words)
        word_timestamps = []
        for i, word in enumerate(words):
            word_timestamps.append({
                    'word': word,
                'start': i * time_per_word,
                'end': (i + 1) * time_per_word
            })
        return word_timestamps


def get_word_timestamps_from_audio(video_file, clip_start, clip_end):
    """
    Extract word-level timestamps from audio using OpenAI Whisper API.
    Returns list of dicts with 'word', 'start', 'end' keys.
    Timestamps are relative to clip_start (0-based).
    """
    try:
        import tempfile
        
        print(f"🎤 Extracting word timestamps from audio using Whisper...")
        
        # Load video and extract audio segment
        video_clip = VideoFileClip(video_file)
        audio_clip = video_clip.subclip(clip_start, clip_end).audio
        
        # Save audio to temporary file
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_audio:
            tmp_audio_path = tmp_audio.name
        
        try:
            # Write audio to temp file (suppress moviepy output)
            audio_clip.write_audiofile(tmp_audio_path, logger=None, verbose=False)
            
            # Use OpenAI Whisper API with word timestamps
            with open(tmp_audio_path, 'rb') as audio_file:
                transcript = openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["word"]  # Get word-level timestamps
                )
            
            # Extract word timestamps
            words_with_timestamps = []
            
            # Check different possible response formats
            if hasattr(transcript, 'words') and transcript.words:
                # Direct words attribute
                for word_info in transcript.words:
                    word_text = word_info.word if isinstance(word_info.word, str) else getattr(word_info, 'word', '')
                    words_with_timestamps.append({
                        'word': word_text.strip(),
                        'start': float(word_info.start),
                        'end': float(word_info.end),
                        'original_order': len(words_with_timestamps)
                    })
            elif hasattr(transcript, 'segments') and transcript.segments:
                # Extract from segments
                for segment in transcript.segments:
                    if hasattr(segment, 'words') and segment.words:
                        for word_info in segment.words:
                            word_text = word_info.word if isinstance(word_info.word, str) else getattr(word_info, 'word', '')
                            words_with_timestamps.append({
                                'word': word_text.strip(),
                                'start': float(word_info.start),
                                'end': float(word_info.end),
                                'original_order': len(words_with_timestamps)
                            })
            elif isinstance(transcript, dict):
                # Dictionary format
                if 'words' in transcript and transcript['words']:
                    for word_info in transcript['words']:
                        words_with_timestamps.append({
                            'word': str(word_info.get('word', '')).strip(),
                            'start': float(word_info.get('start', 0)),
                            'end': float(word_info.get('end', 0)),
                            'original_order': len(words_with_timestamps)
                        })
                elif 'segments' in transcript:
                    for segment in transcript['segments']:
                        if 'words' in segment:
                            for word_info in segment['words']:
                                words_with_timestamps.append({
                                    'word': str(word_info.get('word', '')).strip(),
                                    'start': float(word_info.get('start', 0)),
                                    'end': float(word_info.get('end', 0)),
                                    'original_order': len(words_with_timestamps)
                                })
            
            if not words_with_timestamps:
                print("⚠️  Whisper API did not return word-level timestamps, falling back to LLM")
                return []
            
            print(f"✅ Extracted {len(words_with_timestamps)} words from audio")
            return words_with_timestamps
            
        finally:
            # Clean up temp file
            if os.path.exists(tmp_audio_path):
                os.unlink(tmp_audio_path)
            audio_clip.close()
            video_clip.close()
            
    except Exception as e:
        print(f"⚠️  Error extracting word timestamps from audio: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_word_timestamps_for_clip(clip_start, clip_end, transcript_data, video_file=None):
    """
    Extract word-level timestamps for a clip from audio using Whisper (preferred) or LLM fallback.
    
    Args:
        clip_start: Start time of clip in video (seconds)
        clip_end: End time of clip in video (seconds)
        transcript_data: Original transcript data (for LLM fallback)
        video_file: Path to video file (for audio extraction with Whisper)
    
    Returns:
        List of dicts with 'word', 'start', 'end' keys.
        Timestamps are relative to clip_start (0-based), ensuring subtitles start empty.
    """
    # Try to use Whisper from audio first (most accurate)
    if video_file and os.path.exists(video_file):
        audio_words = get_word_timestamps_from_audio(video_file, clip_start, clip_end)
        if audio_words:
            # Timestamps from Whisper are already relative to the audio clip (0-based)
            # Add small delay to ensure subtitles start empty
            min_start_delay = 0.2
            if audio_words and audio_words[0]['start'] < min_start_delay:
                delay = min_start_delay - audio_words[0]['start']
                for word_data in audio_words:
                    word_data['start'] += delay
                    word_data['end'] += delay
            
            return audio_words
        else:
            print("⚠️  Falling back to LLM estimation for word timestamps")
    
    # Fallback to LLM estimation if Whisper fails or no video file
    return get_word_timestamps_for_clip_llm(clip_start, clip_end, transcript_data)


def get_word_timestamps_for_clip_llm(clip_start, clip_end, transcript_data):
    """
    Extract word-level timestamps for a clip from transcript data using LLM.
    Uses LLM to estimate exact word timestamps based on speech patterns.
    This is a fallback method when audio extraction is not available.
    
    Returns a list of dicts with 'word', 'start', 'end' keys.
    Timestamps are relative to clip_start (0-based), ensuring subtitles start empty.
    """
    words_with_timestamps = []
    
    # Find all transcript snippets that overlap with the clip
    for snippet in transcript_data:
        snippet_start = snippet['start']
        snippet_end = snippet['start'] + snippet['duration']
        
        # Check if snippet overlaps with clip
        if snippet_end < clip_start or snippet_start > clip_end:
            continue
        
        # Get the text and split into words
        text = snippet['text'].strip()
        if not text:
            continue
        
        # Use LLM to get exact word timestamps
        word_timestamps = get_word_timestamps_with_llm(text, snippet_start, snippet_end)
        
        # Convert absolute timestamps to clip-relative timestamps
        for word_data in word_timestamps:
            word = word_data['word']
            # Convert from snippet-relative to absolute timestamps
            absolute_start = snippet_start + word_data['start']
            absolute_end = snippet_start + word_data['end']
            
            # Only include words that START within the clip (exclude words that started before clip_start)
            # This ensures we only process text that actually belongs to this clip
            if absolute_start >= clip_start and absolute_start < clip_end:
                # Adjust timestamps to be relative to clip start (0-based)
                # This ensures subtitles start empty at time 0
                adjusted_start = absolute_start - clip_start
                adjusted_end = min(clip_end - clip_start, absolute_end - clip_start)
                
                # Only add if there's a valid duration
                if adjusted_end > adjusted_start:
                    words_with_timestamps.append({
                            'word': word,
                            'start': adjusted_start,
                            'end': adjusted_end,
                            'original_order': len(words_with_timestamps)  # Track original script order
                        })
    
    # Remove duplicates: same word at same or very close timestamp (within 0.1s)
    # This handles cases where overlapping transcript snippets give us duplicate words
    unique_words = []
    seen_word_positions = set()
    for word_data in words_with_timestamps:
        word = word_data['word']
        start = word_data['start']
        # Check if we've seen this word at approximately this position
        # Round to 0.1s precision to catch near-duplicates
        position_key = (word, round(start, 1))
        if position_key not in seen_word_positions:
            seen_word_positions.add(position_key)
            unique_words.append(word_data)
    words_with_timestamps = unique_words
    
    # CRITICAL: Keep words in script order (original_order), DO NOT sort by timestamp
    # This ensures words always appear in the correct sequence from the transcript
    words_with_timestamps.sort(key=lambda x: x['original_order'])
    
    # Now fix timestamps to ensure they respect script order
    # Each word must start after the previous word ends (preserve relative timing when possible)
    if words_with_timestamps:
        min_start_delay = 0.2  # Minimum 0.2 seconds before first word appears
        min_word_duration = 0.1  # Minimum duration per word
        
        fixed_words = []
        for i, word_data in enumerate(words_with_timestamps):
            word = word_data['word']
            original_start = word_data['start']
            original_end = word_data['end']
            original_duration = max(original_end - original_start, min_word_duration)
            
            # First word: ensure minimum delay
            if i == 0:
                adjusted_start = max(original_start, min_start_delay)
                adjusted_end = adjusted_start + original_duration
            else:
                # All other words: must start after previous word ends
                prev_end = fixed_words[i-1]['end']
                
                # If original timestamp is later than prev_end, use it (preserve relative timing)
                # Otherwise, place it right after previous word
                if original_start >= prev_end:
                    adjusted_start = original_start
                    adjusted_end = original_end
                    # Ensure minimum duration
                    if adjusted_end - adjusted_start < min_word_duration:
                        adjusted_end = adjusted_start + min_word_duration
                else:
                    # This word's timestamp violates script order - fix it
                    adjusted_start = prev_end + 0.05  # Small gap between words
                    adjusted_end = adjusted_start + original_duration
            
            fixed_words.append({
                    'word': word,
                    'start': adjusted_start,
                    'end': adjusted_end
                })
        
        words_with_timestamps = fixed_words
    
    return words_with_timestamps


def create_word_by_word_subtitles(words_with_timestamps, clip_duration, size, max_words=5, time_offset=0.0, font_style=None, font_color=None):
    """
    Create word-by-word subtitle clips using a two-line system:
    - Words go to line 1 first (max 5 words)
    - When line 1 is full, words go to line 2 (max 5 words)
    - When line 2 is full, clear both lines and start fresh with line 1
    - When words are added to a line, previous words in that line are erased first
    - Only ONE place creates subtitles to avoid overlapping
    
    Args:
        words_with_timestamps: List of word dicts with 'word', 'start', 'end' keys
        clip_duration: Total duration of the clip
        size: Tuple of (width, height) for the video
        max_words: Maximum words per line (default 5)
        time_offset: Time offset to add to all subtitle timestamps (e.g., intro duration)
        font_style: Font style ("bold", "funny", "scary", "movie", "peptalk", "elegant", "modern") - MUST be provided by UI
        font_color: Font color scheme ("white_red", "white_green", "white_blue", "white_black", "rainbow", "white_yellow", etc.) - MUST be provided by UI
    
    Returns a list of ImageClip objects to be composited.
    """
    # Validate that UI values were provided - no defaults allowed
    if font_style is None or font_color is None:
        print(f"⚠️  ERROR: font_style or font_color is None! style={font_style}, color={font_color}. This should never happen - UI must send values.")
        # Use minimal fallback only as last resort
        font_style = font_style if font_style is not None else "bold"
        font_color = font_color if font_color is not None else "white_red"
    
    print(f"🎨 create_word_by_word_subtitles called with font_style='{font_style}', font_color='{font_color}'")
    
    subtitle_clips = []
    
    if not words_with_timestamps:
        return subtitle_clips
    
    # Apply time offset to all word timestamps
    if time_offset > 0:
        adjusted_words = []
        for word_data in words_with_timestamps:
            adjusted_words.append({
                'word': word_data['word'],
                'start': word_data['start'] + time_offset,
                'end': word_data['end'] + time_offset
            })
        words_with_timestamps = adjusted_words
    
    # DEBUG: Print words being processed
    print(f"DEBUG: Processing {len(words_with_timestamps)} words for subtitles")
    all_words_text = ' '.join([w['word'] for w in words_with_timestamps])
    print(f"All words to process: {all_words_text}")
    print(f"{'='*60}\n")
    
    # Track line 1 (top line) and line 2 (bottom line)
    line1_start = 0  # Index of first word in line 1
    line1_count = 0  # Number of words in line 1
    line2_start = -1  # Index of first word in line 2 (-1 means no line 2)
    line2_count = 0  # Number of words in line 2
    
    # Track previous state to detect when lines change
    prev_line1_start = -1
    prev_line1_count = 0
    prev_line2_start = -1
    prev_line2_count = 0
    
    # Track when the current subtitle clip started
    current_clip_start = words_with_timestamps[0]['start']
    
    # Track emojis for each line (cached to avoid regenerating for same line)
    line1_emoji = None
    line2_emoji = None
    prev_line1_text_for_emoji = ""
    prev_line2_text_for_emoji = ""
    
    for i in range(len(words_with_timestamps)):
        word_data = words_with_timestamps[i]
        word_start = word_data['start']
        word_text = word_data['word']
        
        # DEBUG: Show which word is being processed
        print(f"DEBUG: Processing word {i+1}/{len(words_with_timestamps)}: '{word_text}' at {word_start:.3f}s")
        
        # Store previous state before updating
        prev_line1_start = line1_start
        prev_line1_count = line1_count
        prev_line2_start = line2_start
        prev_line2_count = line2_count
        
        # Determine which line this word should go to
        if line1_count < max_words:
            # Line 1 is not full, add to line 1
            if line1_count == 0:
                line1_start = i  # First word in line 1
                line1_emoji = None  # Reset emoji for new line
            line1_count += 1
        elif line2_count < max_words:
            # Line 1 is full, add to line 2
            if line2_start == -1:
                line2_start = i  # First word in line 2
                line2_emoji = None  # Reset emoji for new line
            line2_count += 1
        else:
            # Both lines are full, clear both and start fresh with line 1
            # First, clear the old content
            if prev_line1_count > 0 or prev_line2_count > 0:
                prev_line1_words = words_with_timestamps[prev_line1_start:prev_line1_start + prev_line1_count]
                prev_line1_text = ' '.join([w['word'] for w in prev_line1_words])
                prev_full_text = prev_line1_text
            if prev_line2_start != -1 and prev_line2_count > 0:
                prev_line2_words = words_with_timestamps[prev_line2_start:prev_line2_start + prev_line2_count]
                prev_line2_text = ' '.join([w['word'] for w in prev_line2_words])
                prev_full_text = f"{prev_line1_text}\n{prev_line2_text}"
                
                # Clear old content before new word appears
                clear_time = max(current_clip_start, word_start - 0.1)
                if clear_time > current_clip_start:
                    text_img = create_text_image_two_line(prev_full_text, size, fontsize=44, color='white', bg_color='transparent')
                    txt_clip = ImageClip(text_img, ismask=False).set_start(current_clip_start).set_duration(clear_time - current_clip_start).set_position(('center', 'top'))
                    subtitle_clips.append(txt_clip)
                    current_clip_start = word_start
            
            # Start fresh with line 1
            line1_start = i
            line1_count = 1
            line2_start = -1
            line2_count = 0
            prev_line1_start = -1
            prev_line1_count = 0
            prev_line2_start = -1
            prev_line2_count = 0
            line1_emoji = None  # Reset emoji for new line
            line2_emoji = None
        
        # Check if line was REPLACED (different start index), not just words added
        # But don't clear if the content moved to the other line (still visible)
        line1_moved_to_line2 = (prev_line1_start == line2_start and prev_line1_count == line2_count and prev_line1_start != -1)
        line1_replaced = (prev_line1_start != line1_start and prev_line1_start != -1 and prev_line1_count > 0 and not line1_moved_to_line2)
        
        line2_moved_to_line1 = (prev_line2_start == line1_start and prev_line2_count == line1_count and prev_line2_start != -1)
        line2_replaced = (prev_line2_start != line2_start and prev_line2_start != -1 and prev_line2_count > 0 and not line2_moved_to_line1)
        
        # If a line was replaced (not moved), clear the old line first before showing new content
        if line1_replaced or line2_replaced:
            # Build previous content to clear (only the replaced line, not the moved one)
            prev_line1_text = ""
            prev_line2_text = ""
            if line1_replaced:
                prev_line1_words = words_with_timestamps[prev_line1_start:prev_line1_start + prev_line1_count]
                prev_line1_text = ' '.join([w['word'] for w in prev_line1_words])
            if line2_replaced:
                prev_line2_words = words_with_timestamps[prev_line2_start:prev_line2_start + prev_line2_count]
                prev_line2_text = ' '.join([w['word'] for w in prev_line2_words])
            
            # Only build full text if we have content to clear
            if prev_line1_text or prev_line2_text:
                if prev_line2_text and prev_line1_text:
                    prev_full_text = f"{prev_line1_text}\n{prev_line2_text}"
                elif prev_line2_text:
                    prev_full_text = prev_line2_text
                else:
                    prev_full_text = prev_line1_text
                
                # Clear old content slightly before new word appears
                clear_time = max(current_clip_start, word_start - 0.1)
                if clear_time > current_clip_start:
                    text_img = create_text_image_two_line(prev_full_text, size, fontsize=44, color='white', bg_color='transparent')
                    txt_clip = ImageClip(text_img, ismask=False).set_start(current_clip_start).set_duration(clear_time - current_clip_start).set_position(('center', 'top'))
                    subtitle_clips.append(txt_clip)
                    current_clip_start = word_start
        
        # Determine when this clip should end (when next word starts, or end of clip)
        if i < len(words_with_timestamps) - 1:
            clip_end_time = words_with_timestamps[i + 1]['start']
        else:
            clip_end_time = min(clip_duration, word_data['end'] + 0.5)
        
        # Build current text for display (ONLY ONE PLACE THAT CREATES SUBTITLES)
        line1_words = words_with_timestamps[line1_start:line1_start + line1_count]
        line1_text = ' '.join([w['word'] for w in line1_words])
        
        line2_text = ""
        if line2_start != -1 and line2_count > 0:
            line2_words = words_with_timestamps[line2_start:line2_start + line2_count]
            line2_text = ' '.join([w['word'] for w in line2_words])
            full_text = f"{line1_text}\n{line2_text}"  # Line 1 (top) first, then line 2 (bottom)
        else:
            full_text = line1_text
        
        # Generate emoji for the current line(s) only when:
        # 1. A new line starts (line1_count == 1 or line2_count == 1)
        # 2. A line completes (reaches max_words)
        # Use emoji from line 2 if it exists, otherwise line 1
        current_emoji = None
        
        # Check if we should generate a new emoji for line 1
        line1_needs_emoji = False
        if line1_count == 1 or line1_count == max_words:
            # New line 1 started OR line 1 just completed (reached max_words)
            if line1_text != prev_line1_text_for_emoji:
                line1_needs_emoji = True
        
        # Check if we should generate a new emoji for line 2
        line2_needs_emoji = False
        if line2_text and (line2_count == 1 or line2_count == max_words):
            # New line 2 started OR line 2 just completed (reached max_words)
            if line2_text != prev_line2_text_for_emoji:
                line2_needs_emoji = True
        
        # Generate emoji for line 2 if needed (priority, since it's displayed below)
        if line2_needs_emoji:
            line2_emoji = get_emoji_for_text(line2_text)
            prev_line2_text_for_emoji = line2_text
            current_emoji = line2_emoji
        elif line1_needs_emoji and not line2_text:
            # New line 1 (and no line 2), generate emoji for it
            line1_emoji = get_emoji_for_text(line1_text)
            prev_line1_text_for_emoji = line1_text
            current_emoji = line1_emoji
        elif line2_text and line2_emoji:
            # Use existing line 2 emoji
            current_emoji = line2_emoji
        elif line1_emoji:
            # Use existing line 1 emoji
            current_emoji = line1_emoji
        
        # Use actual word start time, ensuring order
        actual_clip_start = max(current_clip_start, word_start)
        actual_clip_end = clip_end_time
        clip_duration = actual_clip_end - actual_clip_start
        
        # Create subtitle clip with ClipGoat style: last word is red and bigger, previous words are white
        # Use UI-selected font_style and font_color - these are the ONLY values that should be used
        text_img = create_text_image_two_line_with_highlight(full_text, size, fontsize=44, highlight_last_word=True, emoji=current_emoji, font_style=font_style, font_color=font_color)
        txt_clip = ImageClip(text_img, ismask=False).set_start(actual_clip_start).set_duration(clip_duration).set_position(('center', 'top'))
        subtitle_clips.append(txt_clip)
        
        # Update for next iteration
        current_clip_start = actual_clip_end
    
    # DEBUG: Summary
    print(f"\nDEBUG: Created {len(subtitle_clips)} subtitle clips total")
    print(f"{'='*60}\n")
    
    return subtitle_clips


def get_emoji_for_text(text):
    """
    Use LLM to generate a single relevant emoji for the given text.
    Returns a string with a single emoji, or a default emoji if LLM fails.
    """
    try:
        prompt = f"""Given this subtitle text, return ONLY a single relevant emoji that represents the main emotion, topic, or meaning.

Text: "{text}"

Return ONLY the emoji character itself, nothing else. No explanation, no quotes, just the emoji.

Examples:
- Text about money/finance: 💰
- Text about love/heart: ❤️
- Text about laughter/funny: 😂
- Text about shock/surprise: 😱
- Text about anger: 😠
- Text about happiness: 😊
- Text about food: 🍕
- Text about success/victory: 🎉
"""
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=10
        )
        
        emoji = response.choices[0].message.content.strip()
        # Remove quotes if present
        emoji = emoji.strip('"\'`')
        # Take only the first character (emoji)
        if emoji:
            # Handle multi-character emojis (some emojis are multiple characters)
            # Try to extract the first emoji
            import re
            emoji_match = re.search(r'[\U0001F300-\U0001F9FF]|[\U0001FA00-\U0001FAFF]|[\U00002600-\U000026FF]|[\U00002700-\U000027BF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F1E0-\U0001F1FF]|[\U0001F900-\U0001F9FF]', emoji)
            if emoji_match:
                return emoji_match.group(0)
            # Fallback: return first character
            return emoji[0] if emoji else "💬"
        return "💬"
    except Exception as e:
        print(f"⚠️  Failed to generate emoji: {e}, using default")
        return "💬"


def create_text_image_two_line_with_highlight(text, size, fontsize=44, highlight_last_word=True, emoji=None, font_style=None, font_color=None):
    """
    Create a text image where the last word is red and slightly bigger,
    and all previous words are white and normal size.
    This creates the "ClipGoat" style effect where new words pop in red and larger.
    
    Args:
        font_style: Font style - MUST be provided by UI, no defaults
        font_color: Font color - MUST be provided by UI, no defaults
    """
    # Validate that UI values were provided - no defaults allowed
    if font_style is None or font_color is None:
        print(f"⚠️  ERROR: font_style or font_color is None in create_text_image_two_line_with_highlight! style={font_style}, color={font_color}. This should never happen.")
        # Use minimal fallback only as last resort
        font_style = font_style if font_style is not None else "bold"
        font_color = font_color if font_color is not None else "white_red"
    
    print(f"🎨 create_text_image_two_line_with_highlight called with font_style='{font_style}', font_color='{font_color}'")
    
    width, height = size
    
    # Create image with transparent background (RGBA)
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Map font_style to font paths
    font_style_paths = {
        "bold": [
            "/usr/share/fonts/truetype/liberation/Liberation-Sans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ],
        "funny": [
            "/usr/share/fonts/truetype/liberation/Liberation-Sans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
            "C:/Windows/Fonts/comic.ttf",
        ],
        "scary": [
            "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "C:/Windows/Fonts/courbd.ttf",
        ],
        "movie": [
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "C:/Windows/Fonts/timesbd.ttf",
        ],
        "peptalk": [
            "/usr/share/fonts/truetype/liberation/Liberation-Sans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ],
        "elegant": [
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "C:/Windows/Fonts/timesbd.ttf",
        ],
        "modern": [
            "/usr/share/fonts/truetype/liberation/Liberation-Sans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ],
    }
    
    # Get font paths for the selected style, default to bold
    font_paths = font_style_paths.get(font_style, font_style_paths["bold"])
    
    # Try to use a more catchy, bold font
    font = None
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, fontsize)
                break
        except:
            continue
    
    if font is None:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", fontsize)
            except:
                font = ImageFont.load_default()
    
    # Create larger font for highlighted word (1.2x size)
    highlight_font = font  # Default to normal font
    if highlight_last_word:
        try:
            # Try to create a larger version of the font
            for font_path in font_paths:
                try:
                    if os.path.exists(font_path):
                        highlight_font = ImageFont.truetype(font_path, int(fontsize * 1.2))
                        break
                except:
                    continue
        except:
            highlight_font = font  # Fallback to normal font if can't create larger
    
    def draw_text_with_shadow(draw, x, y, text, font, color, shadow_offset=5, shadow_blur=2):
        """Draw text with shadow effect."""
        shadow_color = (0, 0, 0, 255)
        shadow_directions = [
            (-shadow_offset, -shadow_offset), (-shadow_offset, shadow_offset),
            (shadow_offset, -shadow_offset), (shadow_offset, shadow_offset),
            (0, -shadow_offset), (0, shadow_offset), (-shadow_offset, 0), (shadow_offset, 0),
        ]
        
        # Draw blur layers
        for blur in range(shadow_blur):
            blur_offset = shadow_offset + blur + 1
            for adj in [(-blur_offset, -blur_offset), (-blur_offset, blur_offset),
                       (blur_offset, -blur_offset), (blur_offset, blur_offset),
                       (0, -blur_offset), (0, blur_offset), (-blur_offset, 0), (blur_offset, 0)]:
                draw.text((x + adj[0], y + adj[1]), text, font=font, fill=(0, 0, 0, 180))
        
        # Draw main shadow
        for adj in shadow_directions:
            draw.text((x + adj[0], y + adj[1]), text, font=font, fill=shadow_color)
        
        # Draw main text
        draw.text((x, y), text, font=font, fill=color)
    
    # Split text into lines
    lines = text.split('\n')
    line_height = int(fontsize * 1.4)
    start_y = 60
    
    for line_idx, line in enumerate(lines):
        if not line.strip():
            continue
        
        words = line.split()
        if not words:
            continue
        
        # Determine which word to highlight (last word in the text)
        if highlight_last_word and line_idx == len(lines) - 1:
            # Last line - highlight the last word
            highlight_word_idx = len(words) - 1
        else:
            highlight_word_idx = -1  # No highlight for this line
        
        # Calculate positions for all words (using normal font for width calculation)
        word_widths = []
        for word in words:
            bbox = draw.textbbox((0, 0), word, font=font)
            word_widths.append(bbox[2] - bbox[0])
        
        space_bbox = draw.textbbox((0, 0), " ", font=font)
        space_width = space_bbox[2] - space_bbox[0]
        
        # Calculate total width (accounting for larger font on highlighted word)
        total_width = sum(word_widths) + (len(words) - 1) * space_width
        if highlight_word_idx >= 0:
            # Adjust for larger highlighted word
            highlight_bbox = draw.textbbox((0, 0), words[highlight_word_idx], font=highlight_font)
            highlight_width = highlight_bbox[2] - highlight_bbox[0]
            total_width += highlight_width - word_widths[highlight_word_idx]
        
        x = (width - total_width) // 2
        y = start_y + (line_idx * line_height)
        
        # Map font_color to color schemes
        def get_color_scheme(color_scheme):
            """Get colors for normal and highlighted words based on color scheme."""
            color_map = {
                "white_red": {
                    "normal": (255, 255, 255, 255),  # White
                    "highlight": (255, 0, 0, 255),  # Red
                },
                "white_green": {
                    "normal": (255, 255, 255, 255),  # White
                    "highlight": (0, 255, 0, 255),  # Green
                },
                "white_blue": {
                    "normal": (255, 255, 255, 255),  # White
                    "highlight": (0, 150, 255, 255),  # Blue
                },
                "white_black": {
                    "normal": (255, 255, 255, 255),  # White
                    "highlight": (0, 0, 0, 255),  # Black (with white outline)
                },
                "white_yellow": {
                    "normal": (255, 255, 255, 255),  # White
                    "highlight": (255, 255, 0, 255),  # Yellow
                },
                "white_purple": {
                    "normal": (255, 255, 255, 255),  # White
                    "highlight": (200, 0, 255, 255),  # Purple
                },
                "rainbow": {
                    "normal": (255, 255, 255, 255),  # White (will be overridden per word)
                    "highlight": (255, 0, 0, 255),  # Red (will be overridden per word)
                },
            }
            # Use UI-selected color scheme - if not found, log warning but still use it
            if color_scheme not in color_map:
                print(f"⚠️  Warning: Unknown font_color '{color_scheme}', using white_red as fallback")
                return color_map["white_red"]
            return color_map[color_scheme]  # Use UI value directly, no default override
        
        # Use UI-selected font_color - log it to verify it's being received
        print(f"🎨 Using font_color='{font_color}' for subtitles (UI selection)")
        colors = get_color_scheme(font_color)
        print(f"🎨 Color scheme result: normal={colors['normal']}, highlight={colors['highlight']}")
        rainbow_colors = [
            (255, 0, 0, 255),      # Red
            (255, 128, 0, 255),    # Orange
            (255, 255, 0, 255),    # Yellow
            (0, 255, 0, 255),      # Green
            (0, 128, 255, 255),   # Blue
            (75, 0, 130, 255),     # Indigo
            (139, 0, 255, 255),    # Violet
        ]
        
        # Draw words
        current_x = x
        for word_idx, word in enumerate(words):
            if word_idx == highlight_word_idx:
                # Highlighted word: use highlight color and larger font
                if font_color == "rainbow":
                    # Use rainbow color based on word index
                    word_color = rainbow_colors[word_idx % len(rainbow_colors)]
                else:
                    word_color = colors["highlight"]
                word_font = highlight_font
            else:
                # Normal word: use normal color
                if font_color == "rainbow":
                    # Use rainbow color based on word index
                    word_color = rainbow_colors[word_idx % len(rainbow_colors)]
                else:
                    word_color = colors["normal"]
                word_font = font
            
            # For white_black, add white outline to black text
            if font_color == "white_black" and word_idx == highlight_word_idx:
                # Draw white outline first
                outline_offset = 2
                for dx in [-outline_offset, 0, outline_offset]:
                    for dy in [-outline_offset, 0, outline_offset]:
                        if dx != 0 or dy != 0:
                            draw.text((current_x + dx, y + dy), word, font=word_font, fill=(255, 255, 255, 255))
                # Then draw the black text on top
                draw.text((current_x, y), word, font=word_font, fill=word_color)
            else:
                draw_text_with_shadow(draw, current_x, y, word, word_font, word_color)
            
            # Move to next word position
            if word_idx == highlight_word_idx:
                bbox = draw.textbbox((0, 0), word, font=word_font)
                word_width = bbox[2] - bbox[0]
            else:
                word_width = word_widths[word_idx]
            current_x += word_width + space_width
    
    # Draw emoji below the subtitle text if provided (using Pilmoji for colorful emojis)
    if emoji:
        try:
            # Find the bottom of the last line
            if lines:
                last_line_idx = len([l for l in lines if l.strip()]) - 1
                emoji_y = start_y + ((last_line_idx + 1) * line_height) + 15  # 15px spacing below last line
            else:
                emoji_y = start_y + line_height + 15
            
            # Calculate emoji size (same or larger than font size for better visibility)
            emoji_size = int(fontsize * 1.2)  # Make emojis larger (20% bigger than text)
            
            # Create a dummy font at emoji_size to control emoji scaling
            # Pilmoji uses font size to determine emoji scale
            emoji_font = ImageFont.load_default()
            try:
                # Try to load a font at the desired size for better control
                emoji_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", emoji_size)
            except:
                pass  # Use default font if loading fails
            
            # Use Pilmoji to render colorful emojis (Twemoji style)
            with Pilmoji(img) as pilmoji:
                # Estimate emoji width for centering (emojis are roughly square, Twemoji default is ~72px)
                # Scale factor helps adjust size relative to font
                emoji_scale = emoji_size / 72.0  # Twemoji default size is ~72px
                emoji_width = int(72 * emoji_scale)  # Approximate width
                emoji_x = (width - emoji_width) // 2
                
                # Draw emoji shadow for better visibility (simple offset approach)
                shadow_offset = 2
                shadow_temp = Image.new('RGBA', (emoji_size + shadow_offset * 2 + 4, emoji_size + shadow_offset * 2 + 4), (0, 0, 0, 0))
                with Pilmoji(shadow_temp) as shadow_pilmoji:
                    shadow_pilmoji.text((shadow_offset + 2, shadow_offset + 2), emoji, font=emoji_font, emoji_scale_factor=emoji_scale)
                    # Create shadow by converting to grayscale with reduced alpha
                    shadow_array = np.array(shadow_temp)
                    # Extract alpha channel and create shadow (black with alpha)
                    shadow_alpha = shadow_array[:, :, 3]
                    # Create shadow RGBA (black color with reduced opacity)
                    shadow_rgba = np.zeros((shadow_temp.height, shadow_temp.width, 4), dtype=np.uint8)
                    shadow_rgba[:, :, 3] = (shadow_alpha * 0.6).astype(np.uint8)  # 60% opacity shadow
                    shadow_layer = Image.fromarray(shadow_rgba, 'RGBA')
                    # Paste shadow with small offset for depth effect
                    img.paste(shadow_layer, (emoji_x - shadow_offset - 2, emoji_y - shadow_offset - 2), shadow_layer)
                
                # Draw main colorful emoji using Pilmoji (Twemoji images)
                pilmoji.text((emoji_x, emoji_y), emoji, font=emoji_font, emoji_scale_factor=emoji_scale)
        except Exception as e:
            # Fallback: if Pilmoji fails (network issue, etc.), skip emoji rendering
            print(f"⚠️  Failed to render emoji '{emoji}' with Pilmoji: {e}")
    
    return np.array(img)


def create_text_image_two_line(text, size, fontsize=44, color='white', bg_color='transparent'):
    """
    Create a text image with support for two lines (separated by newline).
    Top line appears higher, bottom line appears lower.
    Used for word-by-word subtitles with enhanced styling: better fonts, shadows, and colors.
    """
    width, height = size
    
    # Create image with transparent background (RGBA)
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Try to use a more catchy, bold font (prefer Impact-style fonts)
    font = None
    font_paths = [
        # Try Impact-style fonts first (most catchy)
        "/usr/share/fonts/truetype/liberation/Liberation-Sans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",  # macOS
        "C:/Windows/Fonts/impact.ttf",  # Windows
        "C:/Windows/Fonts/arialbd.ttf",  # Windows Arial Bold
    ]
    
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, fontsize)
                break
        except:
            continue
    
    # Fallback to default bold font
    if font is None:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", fontsize)
            except:
                font = ImageFont.load_default()
    
    # Define color palette for alternating word colors: red, white, and dark blue
    color_palette = [
        (255, 255, 255, 255),  # White
        (255, 0, 0, 255),      # Red
        (0, 0, 139, 255),      # Dark Blue
    ]
    
    def draw_text_with_shadow_and_color(draw, x, y, text, font, base_color, shadow_offset=5, shadow_blur=2):
        """
        Draw text with enhanced shadow effect and color.
        Creates a prominent shadow behind text for better visibility.
        """
        # Main shadow layer (solid black for strong definition)
        shadow_color = (0, 0, 0, 255)  # Solid black shadow
        
        # Draw main shadow in multiple directions for thickness
        shadow_directions = [
            (-shadow_offset, -shadow_offset),
            (-shadow_offset, shadow_offset),
            (shadow_offset, -shadow_offset),
            (shadow_offset, shadow_offset),
            (0, -shadow_offset),
            (0, shadow_offset),
            (-shadow_offset, 0),
            (shadow_offset, 0),
        ]
        
        # Draw additional shadow layers for blur effect
        for blur in range(shadow_blur):
            blur_offset = shadow_offset + blur + 1
            for adj in [
                (-blur_offset, -blur_offset),
                (-blur_offset, blur_offset),
                (blur_offset, -blur_offset),
                (blur_offset, blur_offset),
                (0, -blur_offset),
                (0, blur_offset),
                (-blur_offset, 0),
                (blur_offset, 0),
            ]:
                draw.text((x + adj[0], y + adj[1]), text, font=font, fill=(0, 0, 0, 180))
        
        # Draw main shadow
        for adj in shadow_directions:
            draw.text((x + adj[0], y + adj[1]), text, font=font, fill=shadow_color)
        
        # Draw main text with color on top
        draw.text((x, y), text, font=font, fill=base_color)
    
    # Split text into lines
    lines = text.split('\n')
    if len(lines) == 1:
        # Single line - show at top with colored words
        line = lines[0]
        words = line.split()
        
        # Calculate total width to center
        total_width = sum(draw.textbbox((0, 0), word + " ", font=font)[2] - draw.textbbox((0, 0), word, font=font)[0] for word in words)
        total_width -= draw.textbbox((0, 0), " ", font=font)[2] - draw.textbbox((0, 0), "", font=font)[0]  # Remove last space width
        
        x = (width - total_width) // 2
        y = 60  # Top margin
        
        # Draw each word with alternating colors
        current_x = x
        for word_idx, word in enumerate(words):
            word_color = color_palette[word_idx % len(color_palette)]
            draw_text_with_shadow_and_color(draw, current_x, y, word, font, word_color)
            
            # Move to next word position
            word_width = draw.textbbox((0, 0), word, font=font)[2] - draw.textbbox((0, 0), "", font=font)[0]
            space_width = draw.textbbox((0, 0), " ", font=font)[2] - draw.textbbox((0, 0), "", font=font)[0]
            current_x += word_width + space_width
    else:
        # Two lines - first line (line 1) at top, second line (line 2) below it
        line_height = int(fontsize * 1.4)
        start_y = 60  # Top margin
        
        for line_idx, line in enumerate(lines):
            if not line.strip():
                continue
            
            words = line.split()
            
            # Calculate total width to center
            total_width = sum(draw.textbbox((0, 0), word + " ", font=font)[2] - draw.textbbox((0, 0), word, font=font)[0] for word in words)
            total_width -= draw.textbbox((0, 0), " ", font=font)[2] - draw.textbbox((0, 0), "", font=font)[0]
            
            x = (width - total_width) // 2
            y = start_y + (line_idx * line_height)
            
            # Draw each word with alternating colors
            current_x = x
            color_offset = line_idx * 3  # Different color pattern for each line
            for word_idx, word in enumerate(words):
                word_color = color_palette[(word_idx + color_offset) % len(color_palette)]
                draw_text_with_shadow_and_color(draw, current_x, y, word, font, word_color)
                
                # Move to next word position
                word_width = draw.textbbox((0, 0), word, font=font)[2] - draw.textbbox((0, 0), "", font=font)[0]
                space_width = draw.textbbox((0, 0), " ", font=font)[2] - draw.textbbox((0, 0), "", font=font)[0]
                current_x += word_width + space_width
    
    # Convert PIL image to numpy array
    return np.array(img)


def create_text_image(text, size, fontsize=24, color='white', bg_color='black', position='bottom'):
    """
    Create a text image using PIL/Pillow (no ImageMagick required).
    Returns a numpy array that can be used with ImageClip.
    Creates a semi-transparent background bar at the bottom with text.
    
    Args:
        text: Text to display
        size: (width, height) tuple
        fontsize: Font size in pixels
        color: Text color
        bg_color: Background color ('black', 'transparent', etc.)
        position: 'bottom' or 'top' for subtitle positioning
    """
    width, height = size
    
    # Create image with transparent background (RGBA)
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Try to use a nice font, fallback to default if not available
    try:
        # Try DejaVu Sans which is commonly available on Linux
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
    except:
        try:
            # Try Liberation Sans
            font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", fontsize)
        except:
            # Fallback to default font
            font = ImageFont.load_default()
    
    # Calculate text position (centered horizontally, at bottom or top)
    # Wrap text if needed
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        test_line = ' '.join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        text_width = bbox[2] - bbox[0]
        
        if text_width <= width - 40:  # 20px padding on each side
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
    
    if current_line:
        lines.append(' '.join(current_line))
    
    # Draw text lines
    line_height = int(fontsize * 1.2)
    total_text_height = len(lines) * line_height
    padding = 20
    
    if position == 'top':
        y_start = 0
        bar_height = total_text_height + (padding * 2)
    else:  # bottom
        bar_height = total_text_height + (padding * 2)
        y_start = height - bar_height
    
    # Draw background bar if not transparent
    if bg_color != 'transparent':
        bg_rgba = (0, 0, 0, 180) if bg_color == 'black' else (255, 255, 255, 180)  # Black or white with ~70% opacity
        draw.rectangle([(0, y_start), (width, y_start + bar_height)], fill=bg_rgba)
    
    # Draw text lines
    text_y_start = y_start + padding
    
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2  # Center horizontally
        y = text_y_start + i * line_height
        
        # Draw text with slight outline for better visibility
        outline_color = (0, 0, 0, 255)  # Black outline
        text_color = (255, 255, 255, 255)  # White text
        for adj in [(-1, -1), (-1, 1), (1, -1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0)]:
            draw.text((x + adj[0], y + adj[1]), line, font=font, fill=outline_color)
        draw.text((x, y), line, font=font, fill=text_color)
    
    # Convert PIL image to numpy array
    return np.array(img)


def apply_blur_to_frame(frame, blur_radius=30):
    """
    Apply Gaussian blur to a video frame.
    
    Args:
        frame: numpy array representing the frame (RGB)
        blur_radius: Blur intensity (higher = more blur)
    
    Returns:
        Blurred frame as numpy array
    """
    try:
        from PIL import Image, ImageFilter
        import numpy as np_local
        
        # Convert frame to PIL Image
        pil_img = Image.fromarray(frame.astype('uint8'))
        
        # Apply Gaussian blur
        blurred_img = pil_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        
        # Convert back to numpy array
        return np_local.array(blurred_img)
    except Exception as e:
        # Fallback: return original frame if blur fails
        print(f"⚠️  Could not apply blur: {e}, using original frame")
        return frame


def create_red_fog_background(final_width, final_height, video_x, video_y, video_width, video_height):
    """
    Create a red fog gradient background using NumPy for better performance.
    Fades from dark red at edges to near-black near the video content.
    Creates a fog/shade effect around the video.
    """
    import numpy as np
    from PIL import Image
    
    # Create coordinate grids
    y_coords, x_coords = np.ogrid[0:final_height, 0:final_width]
    
    # Calculate video bounds
    video_x2 = video_x + video_width
    video_y2 = video_y + video_height
    
    # Calculate distance from video edges for each pixel
    dist_x = np.maximum(
        np.maximum(video_x - x_coords, x_coords - video_x2),
        0
    )
    dist_y = np.maximum(
        np.maximum(video_y - y_coords, y_coords - video_y2),
        0
    )
    
    # Use minimum distance to nearest edge
    dist = np.minimum(dist_x, dist_y)
    
    # Create red fog effect - stronger red further from video
    max_dist = max(final_width, final_height) * 0.3  # Fade distance (30% of largest dimension)
    intensity = np.clip(dist / max_dist, 0, 1.0)
    
    # Red component: stronger at edges (40-120 range for red fog)
    red = (40 + (80 * intensity)).astype(np.uint8)
    # Slight green/blue for depth and warmth
    green = (5 * intensity).astype(np.uint8)
    blue = (5 * intensity).astype(np.uint8)
    
    # Combine into RGB image
    img_array = np.stack([red, green, blue], axis=-1)
    return Image.fromarray(img_array)


def create_single_clip_short(base_video_file, clip, clip_index, transcript_data=None, output_prefix="short_clip", quality="hd", remove_watermark=False, font_style="bold", font_color="white_red"):
    """
    Create a single-clip short video from one clip.
    
    Args:
        base_video_file: Path to the base video file
        clip: Dictionary with 'start', 'end', 'text' keys
        clip_index: Index of the clip (for filename)
        transcript_data: Transcript data for subtitles
        output_prefix: Prefix for output filename (default: "short_clip")
    
    Returns:
        Path to the created short video file, or None on error
    """
    try:
        start = clip["start"]
        end = clip["end"]
        text = clip.get("text", "")
        duration = end - start
        
        print(f"\n🎬 Creating short for clip {clip_index + 1} ({duration:.1f}s): {text[:50]}...")
        
        base_video = VideoFileClip(base_video_file)
        video_duration = base_video.duration
        
        # Validate clip timestamps
        if start >= video_duration:
            print(f"⚠️  Skipping clip starting at {start}s (beyond video duration {video_duration:.2f}s)")
            base_video.close()
            return None
        
        start = max(0, min(start, video_duration))
        end = max(start, min(end, video_duration))
        
        if end <= start:
            print(f"⚠️  Skipping invalid clip ({start}s - {end}s)")
            base_video.close()
            return None
        
        video_clip = base_video.subclip(start, end)
        
        # Validate that video_clip has a valid reader
        if video_clip.reader is None:
            print(f"⚠️  Video clip reader is None, cannot process clip")
            base_video.close()
            video_clip.close()
            return None
        
        # Note: Cannot close base_video yet - video_clip subclip still needs its reader
        # We'll close it in the finally block after processing is complete
        
        # Get transcript text for intro generation
        transcript_text = None
        if transcript_data:
            clip_transcript_texts = []
            for snippet in transcript_data:
                snippet_start = snippet['start']
                snippet_end = snippet['start'] + snippet['duration']
                if snippet_end >= start and snippet_start <= end:
                    clip_transcript_texts.append(snippet['text'])
            transcript_text = ' '.join(clip_transcript_texts)
        
        # Scale to fit vertical format (9:16) for Shorts/TikTok - preserve all content with letterboxing
        original_width, original_height = video_clip.size
        target_aspect_ratio = 9 / 16  # Vertical format (720x1280)
        original_aspect_ratio = original_width / original_height
        
        # Final output size (reduced to 720p to save memory: 720x1280 instead of 1080x1920)
        final_width, final_height = 720, 1280
        
        # Calculate how to scale video to fit within 9:16 without cropping
        if original_aspect_ratio > target_aspect_ratio:
            # Video is wider than 9:16 - scale to fit width, will have letterboxing on top/bottom
            # Scale so width matches final_width
            scale_factor = final_width / original_width
            scaled_width = final_width
            scaled_height = int(original_height * scale_factor)
        else:
            # Video is taller than 9:16 - scale to fit height, will have pillarboxing on sides
            # Scale so height matches final_height
            scale_factor = final_height / original_height
            scaled_width = int(original_width * scale_factor)
            scaled_height = final_height
        
        # Validate reader is still available before processing
        if video_clip.reader is None or (hasattr(base_video, 'reader') and base_video.reader is None):
            print(f"⚠️  Video reader is None before resize, cannot process clip")
            base_video.close()
            video_clip.close()
            return None
        
        # Resize video to scaled size (preserves all content) with letterboxing/pillarboxing
        try:
            import numpy as np_local  # Import locally to ensure it's captured in closure
            from PIL import Image as PILImage
            if hasattr(PILImage, 'Resampling'):
                def resize_frame(frame):
                    # Frame might be None if reader was closed
                    if frame is None:
                        raise ValueError("Frame is None - reader may have been closed")
                    pil_img = PILImage.fromarray(frame.astype('uint8'))
                    resized = pil_img.resize((scaled_width, scaled_height), PILImage.Resampling.LANCZOS)
                    # Create red fog background and center the resized video
                    x_offset = (final_width - scaled_width) // 2
                    y_offset = (final_height - scaled_height) // 2
                    final_img = create_red_fog_background(
                        final_width, final_height,
                        x_offset, y_offset, scaled_width, scaled_height
                    )
                    final_img.paste(resized, (x_offset, y_offset))
                    return np_local.array(final_img)
                # Test if we can get a frame before applying transformation
                test_frame = video_clip.get_frame(0)
                if test_frame is None:
                    raise ValueError("Cannot get test frame - reader may be closed")
                video_clip = video_clip.fl_image(resize_frame)
            else:
                # Fallback for older Pillow versions
                def resize_frame(frame):
                    if frame is None:
                        raise ValueError("Frame is None - reader may have been closed")
                    pil_img = PILImage.fromarray(frame.astype('uint8'))
                    resized = pil_img.resize((scaled_width, scaled_height), PILImage.LANCZOS)
                    x_offset = (final_width - scaled_width) // 2
                    y_offset = (final_height - scaled_height) // 2
                    final_img = create_red_fog_background(
                        final_width, final_height,
                        x_offset, y_offset, scaled_width, scaled_height
                    )
                    final_img.paste(resized, (x_offset, y_offset))
                    return np_local.array(final_img)
                # Test if we can get a frame before applying transformation
                test_frame = video_clip.get_frame(0)
                if test_frame is None:
                    raise ValueError("Cannot get test frame - reader may be closed")
                video_clip = video_clip.fl_image(resize_frame)
        except (AttributeError, ImportError, ValueError) as e:
            print(f"⚠️  Error during resize (PIL method): {e}, trying MoviePy resize fallback")
            # Fallback if PIL fails - use MoviePy resize (will crop though)
            try:
                # Ensure reader is still valid
                if video_clip.reader is None:
                    raise ValueError("Video reader is None")
                video_clip = video_clip.resize((scaled_width, scaled_height))
            except Exception as resize_error:
                print(f"❌ Error with MoviePy resize fallback: {resize_error}")
                base_video.close()
                video_clip.close()
                return None
        
        # Generate and add intro hook at the beginning
        intro_audio_file = None
        intro_audio_clip = None
        intro_duration = 0.0  # Track intro duration for subtitle offset
        intro_text = None  # Initialize intro_text
        
        try:
            print(f"🎤 Generating introduction hook...")
            intro_text = generate_video_introduction(text, transcript_text, clip_type="reel")
            print(f"   Hook: \"{intro_text}\"")
            
            intro_audio_file = create_intro_audio(intro_text)
            if intro_audio_file and os.path.exists(intro_audio_file):
                from moviepy.editor import AudioFileClip
                intro_audio_clip = AudioFileClip(intro_audio_file)
                if intro_audio_clip is None or intro_audio_clip.reader is None:
                    print(f"⚠️  Intro audio clip has no reader, skipping intro")
                    intro_audio_clip.close()
                    intro_audio_clip = None
                else:
                    intro_duration = intro_audio_clip.duration
                    
                    # Ensure main video clip has fps set
                    if not hasattr(video_clip, 'fps') or video_clip.fps is None:
                        video_clip = video_clip.set_fps(24)
                    main_clip_fps = video_clip.fps if hasattr(video_clip, 'fps') and video_clip.fps else 24
                    
                    # Use final_width and final_height for intro clip (matches the resized video output size)
                    intro_clip_size = (final_width, final_height)
                    
                    # Get first frame of video and apply blur effect for intro
                    try:
                        first_frame = video_clip.get_frame(0)
                        blurred_frame = apply_blur_to_frame(first_frame, blur_radius=30)
                        
                        # Create intro clip with blurred first frame (NO text overlay since we have subtitles)
                        blurred_img_clip = ImageClip(blurred_frame, duration=intro_duration).set_fps(main_clip_fps)
                        
                        # Just use the blurred video without text overlay
                        intro_clip = blurred_img_clip.set_audio(intro_audio_clip)
                    except Exception as blur_err:
                        # Fallback to black background if blur fails
                        print(f"⚠️  Could not create blurred intro: {blur_err}, using black background")
                        # Create a simple black background without text
                        black_img = Image.new('RGB', intro_clip_size, (0, 0, 0))
                        intro_clip = ImageClip(np.array(black_img), duration=intro_duration).set_fps(main_clip_fps)
                        intro_clip = intro_clip.set_audio(intro_audio_clip)
                    
                    # Ensure both clips have same size and fps before concatenation
                    # Note: video_clip frames are already final_width x final_height after fl_image transformation
                    intro_clip = intro_clip.resize(intro_clip_size).set_fps(main_clip_fps)
                    video_clip = video_clip.set_fps(main_clip_fps)
                    
                    # Ensure video clip has audio before concatenation
                    if video_clip.audio is None:
                        # Create silent audio for video clip
                        from moviepy.audio.AudioClip import AudioClip
                        import numpy as np
                        video_clip = video_clip.set_audio(AudioClip(lambda t: np.zeros((1, 2), dtype=np.float32), duration=video_clip.duration, fps=22050))
                    
                    # Concatenate intro with main video clip (sequential, not compose)
                    concatenated = concatenate_videoclips([intro_clip, video_clip], method="compose")
                    if concatenated is not None:
                        # Validate concatenated audio before using it
                        if concatenated.audio is not None:
                            # Check if audio reader is valid
                            if hasattr(concatenated.audio, 'reader') and concatenated.audio.reader is None:
                                print(f"⚠️  Concatenated clip has audio with None reader, removing audio")
                                concatenated = concatenated.without_audio()
                            else:
                                # Test if audio can be accessed (catch any errors)
                                try:
                                    test_frame = concatenated.audio.get_frame(0)
                                    video_clip = concatenated
                                    print(f"✅ Added {intro_duration:.2f}s introduction hook")
                                except (AttributeError, TypeError) as audio_err:
                                    print(f"⚠️  Audio frame access failed: {audio_err}, removing audio")
                                    concatenated = concatenated.without_audio()
                                    video_clip = concatenated
                                    print(f"✅ Added {intro_duration:.2f}s introduction hook (no audio)")
                            if video_clip != concatenated:
                                concatenated.close()
                        else:
                            video_clip = concatenated
                            print(f"✅ Added {intro_duration:.2f}s introduction hook (no audio)")
                    else:
                        print(f"⚠️  Failed to concatenate intro with video clip, skipping intro")
                        if concatenated is not None:
                            concatenated.close()
                        intro_clip.close()
                        intro_duration = 0.0
        except Exception as e:
            print(f"⚠️  Could not add introduction hook: {e}")
            import traceback
            traceback.print_exc()
            # Continue without intro if there's an error
            intro_duration = 0.0
        
        # Create word-by-word subtitles if transcript data is available
        clip_layers = [video_clip]
        
        # Add subtitles for intro audio if intro exists
        if intro_duration > 0 and intro_text:
            try:
                # Create word timestamps for intro text using LLM estimation
                intro_words_with_timestamps = get_word_timestamps_with_llm(intro_text, 0, intro_duration)
                if intro_words_with_timestamps:
                    # Create word-by-word subtitle clips for intro (no time offset needed, starts at 0)
                    intro_subtitle_clips = create_word_by_word_subtitles(
                        intro_words_with_timestamps,
                        intro_duration,
                        (final_width, final_height),
                        max_words=5,
                        time_offset=0.0,  # Intro subtitles start at the beginning
                        font_style=font_style,
                        font_color=font_color
                    )
                    clip_layers.extend(intro_subtitle_clips)
                    print(f"✅ Added subtitles for intro audio")
            except Exception as e:
                print(f"⚠️  Could not create subtitles for intro: {e}")
        
        if transcript_data:
            # Get word timestamps for this clip
            words_with_timestamps = get_word_timestamps_for_clip(start, end, transcript_data, video_file=base_video_file)
            
            if words_with_timestamps:
                # Create word-by-word subtitle clips
                # Pass intro_duration as time_offset so subtitles start after intro
                subtitle_clips = create_word_by_word_subtitles(
                    words_with_timestamps, 
                    video_clip.duration, 
                    (final_width, final_height),
                    max_words=5,
                    time_offset=intro_duration,  # Subtitles start after intro
                    font_style=font_style,
                    font_color=font_color
                )
                clip_layers.extend(subtitle_clips)
        
        # Create composite clip - preserve audio from video_clip
        final_clip = CompositeVideoClip(clip_layers)
        # Ensure audio is preserved from the original video clip
        if video_clip.audio is not None:
            # Validate audio reader before setting
            if hasattr(video_clip.audio, 'reader') and video_clip.audio.reader is None:
                print(f"⚠️  Video clip has audio with None reader, creating composite without audio")
                final_clip = final_clip.without_audio()
            else:
                final_clip = final_clip.set_audio(video_clip.audio)
        
        # Generate output filename using prefix
        output_file = f"{output_prefix}_{clip_index + 1}_{int(start)}s-{int(end)}s.mp4"
        
        # Use thread-safe unique temp file name
        import threading
        thread_id = threading.get_ident()
        temp_audio_file = f'temp-audio_{thread_id}.m4a'
        
        try:
            # Get quality settings
            quality_params = get_quality_params(quality)
            
            # Resize video to target resolution
            final_clip = final_clip.resize(quality_params["resolution"])
            
            # Add watermark unless user paid to remove it
            if not remove_watermark:
                final_clip = add_watermark(final_clip, position='bottom-right', opacity=0.35)
            
            # Write video file with optimized ffmpeg threading and quality settings
            final_clip.write_videofile(
                output_file,
                codec='libx264',
                audio_codec='aac',
                temp_audiofile=temp_audio_file,
                remove_temp=True,
                fps=24,
                ffmpeg_params=[
                    '-threads', '0',  # 0 = use all available CPU cores
                    '-preset', quality_params["preset"],
                    '-crf', quality_params["crf"],
                    '-b:v', quality_params["bitrate"],
                ]
            )
        finally:
            # Clean up temp audio file even on error
            if os.path.exists(temp_audio_file):
                try:
                    os.remove(temp_audio_file)
                except:
                    pass
            
            # Clean up clips
            final_clip.close()
            video_clip.close()
            if base_video is not None:
                base_video.close()
            
            # Force garbage collection to free memory immediately
            import gc
            gc.collect()
            
            # Clean up intro audio files
            if intro_audio_clip:
                intro_audio_clip.close()
            if intro_audio_file and os.path.exists(intro_audio_file):
                try:
                    os.remove(intro_audio_file)
                    print(f"🗑️  Removed intro audio file: {intro_audio_file}")
                except Exception as e:
                    print(f"⚠️  Could not remove intro audio file {intro_audio_file}: {e}")
        
        print(f"✅ Created short: {output_file}")
        return output_file
        
    except Exception as e:
        print(f"❌ Error creating short for clip {clip_index + 1}: {e}")
        import traceback
        traceback.print_exc()
        # Ensure base_video is closed on error
        if 'base_video' in locals() and base_video is not None:
            try:
                base_video.close()
            except:
                pass
        # Force garbage collection on error
        import gc
        gc.collect()
        return None


def generate_video_introduction(clip_text, full_transcript=None, clip_type="reel"):
    """
    Generate an engaging hook-style introduction for a video clip using LLM.
    
    Args:
        clip_text: Text description of the clip (from LLM analysis)
        full_transcript: The ACTUAL transcript text from THIS SPECIFIC CLIP (filtered by timing)
        clip_type: "reel" for short clips (3-5s), "medium" for longer clips (5-10s)
    
    Returns:
        String with engaging hook/introduction text
    """
    target_length = "3-5 seconds" if clip_type == "reel" else "5-10 seconds"
    
    # Use the actual transcript from this specific clip as the main content
    # This transcript has already been filtered to match the clip's timing (start/end)
    transcript_content = full_transcript if full_transcript else clip_text
    transcript_length = len(transcript_content) if transcript_content else 0
    
    prompt = f"""
You are a social media content creator. Generate an engaging, hook-style introduction for a YouTube Short/Reel.

CRITICAL: The introduction should NOT quote or repeat what is said in the transcript. Instead, create an engaging hook that describes what's happening or what the person did, similar to:
- "Look what this person just did!"
- "Speed just gave away this $1000 watch!"
- "You won't believe what happened next!"
- "This person did something INSANE!"

Your introduction should:
- Be a compelling hook that grabs attention immediately
- Be {target_length} when spoken (approximately 8-15 words for short, 15-25 words for medium)
- Create curiosity and make viewers want to watch
- Describe what's happening or what the person did, NOT quote the transcript
- Be engaging and exciting, like a teaser or announcement
- Use an engaging, conversational tone with ENERGY and EXCITEMENT
- Start with a hook that creates intrigue (e.g., "Look what happened!", "This person just...", "You need to see this!")
- Use exclamation marks and energetic language to convey excitement
- Sound enthusiastic and dynamic when spoken aloud
- Mention the action or event that's about to happen, not the actual words spoken

TRANSCRIPT CONTENT FROM THIS CLIP (use this to understand what's happening, but DON'T quote it):
{transcript_content[:1500] if transcript_content else "No transcript available"}

Clip description (for additional context): {clip_text[:200]}

Generate ONLY an engaging hook/introduction that describes what's happening or what the person did, similar to the examples above. Do NOT quote the transcript. Make it exciting and energetic! Do not include any additional commentary, explanations, or formatting. Just return the hook text that will be spoken.
"""
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        
        intro_text = response.choices[0].message.content.strip()
        # Remove any quotes or formatting if present
        intro_text = intro_text.strip('"').strip("'").strip()
        return intro_text
    except Exception as e:
        print(f"⚠️  Error generating introduction: {e}")
        # Fallback to simple intro
        return f"Watch this: {clip_text[:50]}"


def create_intro_audio(intro_text, output_path=None):
    """
    Convert introduction text to speech using OpenAI TTS API.
    
    Args:
        intro_text: Text to convert to speech
        output_path: Optional path to save audio file. If None, creates temporary file.
    
    Returns:
        Path to the generated audio file
    """
    if output_path is None:
        # Use thread-safe unique temp file name
        import threading
        thread_id = threading.get_ident()
        temp_file = tempfile.NamedTemporaryFile(suffix=f'_intro_{thread_id}.mp3', delete=False, prefix='reel_')
        output_path = temp_file.name
        temp_file.close()
    
    try:
        response = openai_client.audio.speech.create(
            model="tts-1",  # Use OpenAI's TTS-1 model (faster, cheaper)
            voice="onyx",  # Male voice - Options: alloy (neutral), echo (male), fable (male), onyx (male), nova (female), shimmer (female)
            input=intro_text
        )
        
        # Save the audio response to file
        with open(output_path, 'wb') as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
        
        return output_path
    except Exception as e:
        print(f"⚠️  Error creating TTS audio: {e}")
        # If TTS fails, return None - video can continue without intro
        return None


def create_single_clip_shorts(base_video_file, clips_info, transcript_data=None, min_duration=45.0, quality="hd", remove_watermark=False, font_style="bold", font_color="white_red"):
    """
    Create individual short videos for clips longer than min_duration seconds.
    
    Args:
        base_video_file: Path to the base video file
        clips_info: Dictionary with 'clips' list
        transcript_data: Transcript data for subtitles
        min_duration: Minimum duration in seconds to create a short (default: 45.0)
        quality: Video quality setting
        remove_watermark: Whether to remove watermark
        font_style: Font style for subtitles
        font_color: Font color for subtitles
    
    Returns:
        List of paths to created short video files
    """
    created_shorts = []
    
    if not clips_info or "clips" not in clips_info:
        return created_shorts
    
    print(f"\n{'='*60}")
    print(f"Creating single-clip shorts for clips longer than {min_duration}s...")
    print(f"{'='*60}")
    
    for i, clip in enumerate(clips_info["clips"]):
        start = clip.get("start", 0)
        end = clip.get("end", 0)
        duration = end - start
        
        if duration > min_duration:
            short_file = create_single_clip_short(base_video_file, clip, i, transcript_data, quality=quality, remove_watermark=remove_watermark, font_style=font_style, font_color=font_color)
            if short_file:
                created_shorts.append(short_file)
        else:
            print(f"⏭️  Skipping clip {i + 1} ({duration:.1f}s) - shorter than {min_duration}s")
    
    if created_shorts:
        print(f"\n✅ Created {len(created_shorts)} single-clip shorts:")
        for short_file in created_shorts:
            print(f"   - {short_file}")
    else:
        print(f"\nℹ️  No clips longer than {min_duration}s found")
    
    return created_shorts


def create_reel(base_video_file, clips_info, output_file="reel.mp4", min_clips=3, transcript_data=None, quality="hd", remove_watermark=False, font_style="bold", font_color="white_red"):
    """
    Create main reel from multiple clips.
    Uses thread-safe temporary file names to avoid conflicts.
    """
    final_clips = []
    base_video = VideoFileClip(base_video_file)
    video_duration = base_video.duration  # Get actual video duration
    
    # Track intro audio files for cleanup
    intro_audio_files_to_cleanup = []
    
    try:
        for i, clip in enumerate(clips_info["clips"]):
            start = clip["start"]
            end = clip["end"]
            text = clip["text"]
            
            print(f"📋 Processing clip {i+1}/{len(clips_info['clips'])}: {start:.1f}s - {end:.1f}s")
            
            # Validate and adjust clip timestamps to be within video bounds
            # Instead of skipping, try to adjust the clip to fit
            if start >= video_duration:
                print(f"⚠️  Clip {i+1} starts at {start}s (beyond video duration {video_duration:.2f}s), skipping")
                continue
            
            # Clamp timestamps to video duration
            original_start = start
            original_end = end
            start = max(0, min(start, video_duration))
            end = max(start, min(end, video_duration))
            
            # If clip was significantly adjusted, warn but still use it
            if abs(original_start - start) > 0.1:
                print(f"⚠️  Adjusted clip {i+1} start from {original_start:.2f}s to {start:.2f}s to fit video duration")
            if abs(original_end - end) > 1.0:
                print(f"⚠️  Adjusted clip {i+1} end from {original_end:.2f}s to {end:.2f}s to fit video duration")
            
            # Skip if clip has no duration after clamping (minimum 1 second)
            if end <= start or (end - start) < 1.0:
                print(f"⚠️  Skipping clip {i+1} (duration too short after adjustment: {end - start:.2f}s)")
                continue
            
            video_clip = base_video.subclip(start, end)
            
            # Validate that video_clip has a valid reader
            if video_clip.reader is None:
                print(f"⚠️  Video clip {i+1} reader is None, skipping clip")
                video_clip.close()
                continue
            
            # Note: base_video stays open for the loop, but subclips are closed individually
            
            # Get transcript text for intro generation
            transcript_text = None
            if transcript_data:
                clip_transcript_texts = []
                for snippet in transcript_data:
                    snippet_start = snippet['start']
                    snippet_end = snippet['start'] + snippet['duration']
                    if snippet_end >= start and snippet_start <= end:
                        clip_transcript_texts.append(snippet['text'])
                transcript_text = ' '.join(clip_transcript_texts)
            
            # Scale to fit vertical format (9:16) for Shorts/TikTok - preserve all content with letterboxing
            original_width, original_height = video_clip.size
            target_aspect_ratio = 9 / 16  # Vertical format
            original_aspect_ratio = original_width / original_height
            
            # Final output size (reduced to 720p to save memory: 720x1280 instead of 1080x1920)
            final_width, final_height = 720, 1280
            
            # Calculate how to scale video to fit within 9:16 without cropping
            if original_aspect_ratio > target_aspect_ratio:
                # Video is wider than 9:16 - scale to fit width, will have letterboxing on top/bottom
                # Scale so width matches final_width
                scale_factor = final_width / original_width
                scaled_width = final_width
                scaled_height = int(original_height * scale_factor)
            else:
                # Video is taller than 9:16 - scale to fit height, will have pillarboxing on sides
                # Scale so height matches final_height
                scale_factor = final_height / original_height
                scaled_width = int(original_width * scale_factor)
                scaled_height = final_height
            
            # Validate reader is still available before processing
            if video_clip.reader is None or (hasattr(base_video, 'reader') and base_video.reader is None):
                print(f"⚠️  Video clip {i+1} reader is None before resize, skipping clip")
                video_clip.close()
                continue
            
            # Resize video to scaled size (preserves all content) with letterboxing/pillarboxing
            try:
                from PIL import Image as PILImage
                if hasattr(PILImage, 'Resampling'):
                    def resize_frame(frame):
                        # Frame might be None if reader was closed
                        if frame is None:
                            raise ValueError("Frame is None - reader may have been closed")
                        pil_img = PILImage.fromarray(frame.astype('uint8'))
                        resized = pil_img.resize((scaled_width, scaled_height), PILImage.Resampling.LANCZOS)
                        # Create red fog background and center the resized video
                        x_offset = (final_width - scaled_width) // 2
                        y_offset = (final_height - scaled_height) // 2
                        final_img = create_red_fog_background(
                            final_width, final_height,
                            x_offset, y_offset, scaled_width, scaled_height
                        )
                        final_img.paste(resized, (x_offset, y_offset))
                        return np.array(final_img)
                    # Test if we can get a frame before applying transformation
                    test_frame = video_clip.get_frame(0)
                    if test_frame is None:
                        raise ValueError("Cannot get test frame - reader may be closed")
                    video_clip = video_clip.fl_image(resize_frame)
                else:
                    # Fallback for older Pillow versions
                    def resize_frame(frame):
                        if frame is None:
                            raise ValueError("Frame is None - reader may have been closed")
                        pil_img = PILImage.fromarray(frame.astype('uint8'))
                        resized = pil_img.resize((scaled_width, scaled_height), PILImage.LANCZOS)
                        x_offset = (final_width - scaled_width) // 2
                        y_offset = (final_height - scaled_height) // 2
                        final_img = create_red_fog_background(
                            final_width, final_height,
                            x_offset, y_offset, scaled_width, scaled_height
                        )
                        final_img.paste(resized, (x_offset, y_offset))
                        return np.array(final_img)
                    # Test if we can get a frame before applying transformation
                    test_frame = video_clip.get_frame(0)
                    if test_frame is None:
                        raise ValueError("Cannot get test frame - reader may be closed")
                    video_clip = video_clip.fl_image(resize_frame)
            except (AttributeError, ImportError, ValueError) as e:
                print(f"⚠️  Error during resize for clip {i+1} (PIL method): {e}, trying MoviePy resize fallback")
                # Fallback to MoviePy's resize if custom method fails
                try:
                    # Ensure reader is still valid
                    if video_clip.reader is None:
                        raise ValueError("Video reader is None")
                    video_clip = video_clip.resize((final_width, final_height))
                except Exception as resize_error:
                    print(f"❌ Error with MoviePy resize fallback for clip {i+1}: {resize_error}, skipping clip")
                    video_clip.close()
                    continue
            
            # Generate and add intro hook at the beginning of this clip
            intro_audio_file = None
            intro_audio_clip = None
            intro_duration = 0.0  # Track intro duration for subtitle offset
            intro_text = None  # Initialize intro_text
            
            try:
                print(f"🎤 Generating introduction hook for clip {i+1}...")
                intro_text = generate_video_introduction(text, transcript_text, clip_type="reel")
                print(f"   Hook: \"{intro_text}\"")
                
                intro_audio_file = create_intro_audio(intro_text)
                if intro_audio_file and os.path.exists(intro_audio_file):
                    from moviepy.editor import AudioFileClip
                    intro_audio_clip = AudioFileClip(intro_audio_file)
                    if intro_audio_clip is None or intro_audio_clip.reader is None:
                        print(f"⚠️  Intro audio clip has no reader, skipping intro")
                        intro_audio_clip.close()
                        intro_audio_clip = None
                    else:
                        intro_duration = intro_audio_clip.duration
                        
                        # Ensure main video clip has fps set
                        if not hasattr(video_clip, 'fps') or video_clip.fps is None:
                            video_clip = video_clip.set_fps(24)
                        main_clip_fps = video_clip.fps if hasattr(video_clip, 'fps') and video_clip.fps else 24
                        
                        # Use final_width and final_height for intro clip (matches the resized video output size)
                        intro_clip_size = (final_width, final_height)
                        
                        # Get first frame of video and apply blur effect for intro
                        try:
                            first_frame = video_clip.get_frame(0)
                            blurred_frame = apply_blur_to_frame(first_frame, blur_radius=30)
                            
                            # Create intro clip with blurred first frame (NO text overlay since we have subtitles)
                            blurred_img_clip = ImageClip(blurred_frame, duration=intro_duration).set_fps(main_clip_fps)
                            
                            # Just use the blurred video without text overlay
                            intro_clip = blurred_img_clip.set_audio(intro_audio_clip)
                        except Exception as blur_err:
                            # Fallback to black background if blur fails
                            print(f"⚠️  Could not create blurred intro: {blur_err}, using black background")
                            # Create a simple black background without text
                            black_img = Image.new('RGB', intro_clip_size, (0, 0, 0))
                            intro_clip = ImageClip(np.array(black_img), duration=intro_duration).set_fps(main_clip_fps)
                            intro_clip = intro_clip.set_audio(intro_audio_clip)
                        
                        # Ensure both clips have same size and fps before concatenation
                        # Note: video_clip frames are already final_width x final_height after fl_image transformation
                        intro_clip = intro_clip.resize(intro_clip_size).set_fps(main_clip_fps)
                        video_clip = video_clip.set_fps(main_clip_fps)
                        
                        # Ensure video clip has audio before concatenation
                        if video_clip.audio is None:
                            # Create silent audio for video clip
                            from moviepy.audio.AudioClip import AudioClip
                            import numpy as np_audio
                            video_clip = video_clip.set_audio(AudioClip(lambda t: np_audio.zeros((1, 2), dtype=np_audio.float32), duration=video_clip.duration, fps=22050))
                        
                        # Concatenate intro with main video clip (sequential, not compose)
                        concatenated = concatenate_videoclips([intro_clip, video_clip], method="compose")
                        if concatenated is not None:
                            # Validate concatenated audio before using it
                            audio_valid = True
                            if concatenated.audio is not None:
                                # Check if audio reader is valid
                                if hasattr(concatenated.audio, 'reader') and concatenated.audio.reader is None:
                                    print(f"⚠️  Concatenated clip has audio with None reader, removing audio")
                                    audio_valid = False
                                else:
                                    # Test if audio can be accessed (catch any errors)
                                    try:
                                        test_frame = concatenated.audio.get_frame(0)
                                        # If it's a CompositeAudioClip, check all sub-clips
                                        if hasattr(concatenated.audio, 'clips'):
                                            for sub_audio in concatenated.audio.clips:
                                                if hasattr(sub_audio, 'reader') and sub_audio.reader is None:
                                                    print(f"⚠️  Concatenated clip has sub-audio with None reader, removing audio")
                                                    audio_valid = False
                                                    break
                                                try:
                                                    sub_audio.get_frame(0)
                                                except (AttributeError, TypeError, Exception) as sub_err:
                                                    print(f"⚠️  Concatenated clip sub-audio can't be accessed: {sub_err}, removing audio")
                                                    audio_valid = False
                                                    break
                                    except (AttributeError, TypeError, Exception) as audio_err:
                                        print(f"⚠️  Audio frame access failed: {audio_err}, removing audio")
                                        audio_valid = False
                            
                            if audio_valid and concatenated.audio is not None:
                                video_clip = concatenated
                                print(f"✅ Added {intro_duration:.2f}s introduction hook")
                            else:
                                concatenated = concatenated.without_audio()
                                video_clip = concatenated
                                print(f"✅ Added {intro_duration:.2f}s introduction hook (no audio)")
                        else:
                            print(f"⚠️  Failed to concatenate intro with video clip, skipping intro")
                            if concatenated is not None:
                                concatenated.close()
                            intro_clip.close()
                            intro_duration = 0.0
            except Exception as e:
                print(f"⚠️  Could not add introduction hook for clip {i+1}: {e}")
                import traceback
                traceback.print_exc()
                # Continue without intro if there's an error
                intro_duration = 0.0
            
            # Create word-by-word subtitles if transcript data is available
            clip_layers = [video_clip]
            
            # Add subtitles for intro audio if intro exists
            if intro_duration > 0 and intro_text:
                try:
                    # Create word timestamps for intro text using LLM estimation
                    intro_words_with_timestamps = get_word_timestamps_with_llm(intro_text, 0, intro_duration)
                    if intro_words_with_timestamps:
                        # Create word-by-word subtitle clips for intro (no time offset needed, starts at 0)
                        intro_subtitle_clips = create_word_by_word_subtitles(
                            intro_words_with_timestamps,
                            intro_duration,
                            (final_width, final_height),
                            max_words=5,
                            time_offset=0.0,  # Intro subtitles start at the beginning
                            font_style=font_style,
                            font_color=font_color
                        )
                        clip_layers.extend(intro_subtitle_clips)
                        print(f"✅ Added subtitles for intro audio")
                except Exception as e:
                    print(f"⚠️  Could not create subtitles for intro: {e}")
            
            if transcript_data:
                # DEBUG: Print full transcript for this clip
                print(f"\n{'='*60}")
                print(f"DEBUG: Clip {start:.1f}s - {end:.1f}s")
                print(f"{'='*60}")
                
                # Get all transcript snippets for this clip
                clip_transcript_texts = []
                for snippet in transcript_data:
                    snippet_start = snippet['start']
                    snippet_end = snippet['start'] + snippet['duration']
                    if snippet_end >= start and snippet_start <= end:
                        clip_transcript_texts.append(snippet['text'])
                
                full_clip_transcript = ' '.join(clip_transcript_texts)
                print(f"Full transcript text: {full_clip_transcript}")
                print(f"{'='*60}\n")
                
                # Get word timestamps for this clip (using audio with Whisper if available)
                words_with_timestamps = get_word_timestamps_for_clip(start, end, transcript_data, video_file=base_video_file)
                
                # DEBUG: Print all words found
                print(f"DEBUG: Found {len(words_with_timestamps)} words with timestamps:")
                for idx, word_data in enumerate(words_with_timestamps):
                    print(f"  {idx+1}. '{word_data['word']}' - start: {word_data['start']:.3f}s, end: {word_data['end']:.3f}s")
                print(f"{'='*60}\n")
                
                if words_with_timestamps:
                    # Create word-by-word subtitle clips
                    # Pass intro_duration as time_offset so subtitles start after intro
                    subtitle_clips = create_word_by_word_subtitles(
                        words_with_timestamps, 
                        video_clip.duration, 
                        (final_width, final_height),
                        max_words=5,
                        time_offset=intro_duration,  # Subtitles start after intro
                        font_style=font_style,
                        font_color=font_color
                    )
                    clip_layers.extend(subtitle_clips)
            
            # Ensure the main video clip is the base layer (not intro)
            # The video_clip should contain both intro + main video after concatenation
            try:
                composite_clip = CompositeVideoClip(clip_layers)
                # Validate composite_clip was created successfully
                if composite_clip is None:
                    print(f"⚠️  CompositeVideoClip returned None for clip {i+1}, skipping")
                    video_clip.close()
                    continue
                # Test that we can get a frame from the composite clip
                try:
                    test_frame = composite_clip.get_frame(0)
                    if test_frame is None:
                        print(f"⚠️  Composite clip {i+1} returned None frame, skipping")
                        composite_clip.close()
                        video_clip.close()
                        continue
                except Exception as frame_test_err:
                    print(f"⚠️  Cannot get frame from composite clip {i+1}: {frame_test_err}, skipping")
                    composite_clip.close()
                    video_clip.close()
                    continue
            except Exception as composite_err:
                print(f"⚠️  Error creating CompositeVideoClip for clip {i+1}: {composite_err}, skipping")
                import traceback
                traceback.print_exc()
                if video_clip:
                    video_clip.close()
                continue
            
            # Ensure audio is properly set from the concatenated video
            if video_clip.audio is not None:
                audio_valid = True
                # Validate audio reader before setting
                if hasattr(video_clip.audio, 'reader') and video_clip.audio.reader is None:
                    print(f"⚠️  Video clip has audio with None reader, creating composite without audio")
                    audio_valid = False
                else:
                    # Test actual frame access to catch issues early
                    try:
                        test_frame = video_clip.audio.get_frame(0)
                        # If it's a CompositeAudioClip, check sub-clips
                        if hasattr(video_clip.audio, 'clips'):
                            for sub_audio in video_clip.audio.clips:
                                if hasattr(sub_audio, 'reader') and sub_audio.reader is None:
                                    print(f"⚠️  Video clip has sub-audio with None reader, removing audio")
                                    audio_valid = False
                                    break
                                try:
                                    sub_audio.get_frame(0)
                                except (AttributeError, TypeError, Exception):
                                    print(f"⚠️  Video clip sub-audio can't be accessed, removing audio")
                                    audio_valid = False
                                    break
                    except (AttributeError, TypeError, Exception) as audio_err:
                        print(f"⚠️  Video clip audio access failed: {audio_err}, removing audio")
                        audio_valid = False
                
                if audio_valid:
                    composite_clip = composite_clip.set_audio(video_clip.audio)
                else:
                    composite_clip = composite_clip.without_audio()
            # Only append if composite_clip is valid (not None)
            if composite_clip is not None:
                # Validate composite_clip can get frames BEFORE closing video_clip
                try:
                    test_frame = composite_clip.get_frame(0)
                    if test_frame is None:
                        print(f"⚠️  Composite clip {i+1} returned None frame, skipping")
                        composite_clip.close()
                        if video_clip:
                            video_clip.close()
                        continue
                except Exception as frame_test_err:
                    print(f"⚠️  Cannot get frame from composite clip {i+1}: {frame_test_err}, skipping")
                    composite_clip.close()
                    if video_clip:
                        video_clip.close()
                    continue
                
                final_clips.append(composite_clip)
                print(f"✅ Added clip to final clips list (duration: {composite_clip.duration:.2f}s)")
            else:
                print(f"⚠️  Skipping clip {i+1} - composite_clip is None")
                if video_clip:
                    video_clip.close()
            
            # DO NOT close video_clip here - composite_clip still references it
            # Closing it prematurely causes NoneType errors when validating clips later during concatenation
            # The clips will be properly cleaned up in the finally block after concatenation
            # video_clip.close()  # REMOVED - causes NoneType errors during validation
            video_clip = None  # Set to None to help with garbage collection, but don't close yet
            
            # Store intro audio file for cleanup later (don't close clip - it's still referenced)
            if intro_audio_file and os.path.exists(intro_audio_file):
                intro_audio_files_to_cleanup.append(intro_audio_file)
            
            # Force garbage collection after each clip to free memory
            import gc
            gc.collect()
        
        print(f"\n📊 Summary: {len(final_clips)} valid clip(s) out of {len(clips_info['clips'])} total clips")
        
        if not final_clips:
            print("❌ No valid clips found within video duration")
            return None
        
        if len(final_clips) < min_clips:
            print(f"⚠️  Only found {len(final_clips)} clips (wanted {min_clips}), but proceeding with available clips")
            # Continue anyway - don't return, proceed with what we have
        
        if len(final_clips) == 1:
            print(f"ℹ️  Only 1 clip - no concatenation needed")
            final_video = final_clips[0]
        else:
            print(f"🔗 Concatenating {len(final_clips)} clips together...")
            # Ensure all clips have fps set and matching dimensions
            # Also validate audio clips before concatenation
            validated_clips = []
            for i, clip in enumerate(final_clips):
                # First, ensure clip is not None
                if clip is None:
                    print(f"⚠️  Clip {i+1} in final_clips is None, skipping")
                    continue
                # Validate that clip can get a frame
                try:
                    test_frame = clip.get_frame(0)
                    if test_frame is None:
                        print(f"⚠️  Clip {i+1} returned None frame, skipping")
                        clip.close()
                        continue
                except Exception as frame_test_err:
                    print(f"⚠️  Cannot get frame from clip {i+1}: {frame_test_err}, skipping")
                    clip.close()
                    continue
                if not hasattr(clip, 'fps') or clip.fps is None:
                    clip = clip.set_fps(24)
                # Validate audio - if audio exists, ensure it can actually be accessed
                if clip.audio is not None:
                    audio_valid = True
                    # Check if audio has a valid reader
                    if hasattr(clip.audio, 'reader') and clip.audio.reader is None:
                        print(f"⚠️  Clip {i+1} has audio with None reader, removing audio")
                        audio_valid = False
                    else:
                        # Actually test if audio can be accessed by trying to get a frame
                        try:
                            test_frame = clip.audio.get_frame(0)
                            # If it's a CompositeAudioClip, also check its sub-clips
                            if hasattr(clip.audio, 'clips'):
                                for sub_audio in clip.audio.clips:
                                    if hasattr(sub_audio, 'reader') and sub_audio.reader is None:
                                        print(f"⚠️  Clip {i+1} has sub-audio with None reader, removing audio")
                                        audio_valid = False
                                        break
                                    # Test sub-audio frame access too
                                    try:
                                        sub_audio.get_frame(0)
                                    except (AttributeError, TypeError, Exception):
                                        print(f"⚠️  Clip {i+1} has sub-audio that can't be accessed, removing audio")
                                        audio_valid = False
                                        break
                        except (AttributeError, TypeError, Exception) as audio_err:
                            print(f"⚠️  Clip {i+1} audio access failed: {audio_err}, removing audio")
                            audio_valid = False
                    
                    if not audio_valid:
                        clip = clip.without_audio()
                validated_clips.append(clip)
            final_clips = validated_clips
            
            # Check if we have any valid clips left after validation
            if not final_clips:
                print("❌ No valid clips remaining after validation - cannot create reel")
                # Close all remaining clips before returning
                for clip in final_clips:
                    try:
                        clip.close()
                    except:
                        pass
                return None
            
            final_video = concatenate_videoclips(final_clips)
            if final_video is None:
                print("❌ Failed to concatenate clips")
                return
            
            # Validate final video audio - test actual frame access
            if final_video.audio is not None:
                audio_valid = True
                try:
                    # Check if it's a CompositeAudioClip first
                    # Ensure audio exists and has clips attribute before checking
                    if not hasattr(final_video.audio, 'clips'):
                        # Not a CompositeAudioClip, treat as regular AudioClip
                        is_composite = False
                    else:
                        # Check if clips list exists and is not empty
                        try:
                            clips_list = final_video.audio.clips
                            is_composite = clips_list is not None and len(clips_list) > 0
                        except (AttributeError, TypeError):
                            is_composite = False
                    
                    if is_composite:
                        # For CompositeAudioClip, check all sub-clips
                        for sub_audio in final_video.audio.clips:
                            if sub_audio is None:
                                print(f"⚠️  Final video has None sub-audio, removing audio")
                                audio_valid = False
                                break
                            if hasattr(sub_audio, 'reader') and sub_audio.reader is None:
                                print(f"⚠️  Final video has sub-audio with None reader, removing audio")
                                audio_valid = False
                                break
                            # Test sub-audio frame access
                            try:
                                test_frame = sub_audio.get_frame(0)
                                if test_frame is None:
                                    print(f"⚠️  Final video sub-audio returned None frame, removing audio")
                                    audio_valid = False
                                    break
                            except (AttributeError, TypeError, Exception) as sub_err:
                                print(f"⚠️  Final video has sub-audio that can't be accessed: {sub_err}, removing audio")
                                audio_valid = False
                                break
                    else:
                        # For regular AudioClip, check reader first
                        if hasattr(final_video.audio, 'reader') and final_video.audio.reader is None:
                            print(f"⚠️  Final video audio has None reader, removing audio")
                            audio_valid = False
                        else:
                            # Only try get_frame for non-CompositeAudioClip
                            try:
                                test_frame = final_video.audio.get_frame(0)
                                if test_frame is None:
                                    print(f"⚠️  Final video audio returned None frame, removing audio")
                                    audio_valid = False
                            except (AttributeError, TypeError, Exception) as frame_err:
                                print(f"⚠️  Final video audio frame access failed: {frame_err}, removing audio")
                                audio_valid = False
                except (AttributeError, TypeError, Exception) as audio_err:
                    print(f"⚠️  Final video audio validation failed: {audio_err}, removing audio")
                    audio_valid = False
                
                if not audio_valid:
                    final_video = final_video.without_audio()
        if final_video is None:
            print("❌ Final video is None, cannot write file")
            return None
        
        print(f"✅ Final video duration: {final_video.duration:.2f}s")
        
        # Final validation: ensure video and audio are valid before writing
        try:
            # Test that we can get at least one video frame
            test_video_frame = final_video.get_frame(0)
            if test_video_frame is None:
                print("❌ Cannot get video frame, skipping write")
                return None
        except Exception as frame_err:
            print(f"❌ Error accessing video frame: {frame_err}")
            import traceback
            traceback.print_exc()
            return None
        
        # Use thread-safe unique temp file name
        import threading
        thread_id = threading.get_ident()
        temp_audio_file = f'reelTEMP_MPY_wvf_snd_{thread_id}.mp3'
        
        try:
            # Get quality settings
            quality_params = get_quality_params(quality)
            
            # Resize final video to target resolution
            final_video = final_video.resize(quality_params["resolution"])
            
            # Add watermark unless user paid to remove it
            remove_watermark = locals().get('remove_watermark', False)
            if not remove_watermark:
                final_video = add_watermark(final_video, position='bottom-right', opacity=0.35)
            
            final_video.write_videofile(
                output_file, 
                codec="libx264", 
                fps=24, 
                temp_audiofile=temp_audio_file, 
                remove_temp=True,
                ffmpeg_params=[
                    '-threads', '0',  # 0 = use all available CPU cores
                    '-preset', quality_params["preset"],
                    '-crf', quality_params["crf"],
                    '-b:v', quality_params["bitrate"],
                ]
            )
            print(f"✅ Successfully created reel: {output_file}")
            # Return the output file path on success
            return output_file
        except Exception as e:
            print(f"❌ Error writing video file: {e}")
            import traceback
            traceback.print_exc()
            # Clean up temp audio file even on error
            if os.path.exists(temp_audio_file):
                try:
                    os.remove(temp_audio_file)
                except:
                    pass
            return None
        finally:
            # Clean up temp audio file if it still exists (backup cleanup)
            if os.path.exists(temp_audio_file):
                try:
                    os.remove(temp_audio_file)
                    print(f"🗑️  Removed temporary audio file: {temp_audio_file}")
                except Exception as e:
                    print(f"⚠️  Could not remove {temp_audio_file}: {e}")
        
        # Clean up
        final_video.close()
        for clip in final_clips:
            try:
                clip.close()
            except:
                pass
        base_video.close()
        
        # Force garbage collection after cleanup
        import gc
        gc.collect()
    finally:
        # Clean up base video
        try:
            base_video.close()
        except:
            pass
        
        # Clean up intro audio files
        for intro_file in intro_audio_files_to_cleanup:
            if os.path.exists(intro_file):
                try:
                    os.remove(intro_file)
                    print(f"🗑️  Removed intro audio file: {intro_file}")
                except Exception as e:
                    print(f"⚠️  Could not remove intro audio file {intro_file}: {e}")
        
        # Clean up any remaining temp files (thread-safe cleanup)
        import threading
        import glob
        thread_id = threading.get_ident()
        
        # Clean up all temp files matching this thread's pattern
        # Use glob patterns to catch all variations
        temp_patterns = [
            f'reelTEMP_MPY_wvf_snd_*.mp3',  # All thread-specific temp files
            f'temp-audio_*.m4a',  # All thread-specific temp audio files
            f'reel_*_intro_*.mp3',  # All intro audio files
            'temp-audio.m4a',  # Legacy temp file
            'reelTEMP_MPY_wvf_snd.mp3'  # Legacy temp file
        ]
        
        for pattern in temp_patterns:
            # Use glob to find all matching files
            for temp_file in glob.glob(pattern):
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        print(f"🗑️  Removed temp file: {temp_file}")
                except Exception as e:
                    print(f"⚠️  Could not remove temp file {temp_file}: {e}")


# Initialize OpenAI client
# API key is loaded from environment variable OPENAI_API_KEY
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError(
        "OPENAI_API_KEY environment variable is not set. "
        "Please create a .env file with your OpenAI API key or set it as an environment variable."
    )
openai_client = openai.OpenAI(api_key=openai_api_key)

# Note: Emoji rendering now uses Pilmoji (colorful Twemoji images)
# No longer using font-based emoji rendering

def find_sentence_boundaries(transcript_data, target_time, direction='nearest'):
    """
    Find the nearest sentence boundary (start or end) to a given timestamp.
    
    Args:
        transcript_data: List of transcript snippets with 'start', 'duration', 'text'
        target_time: The timestamp to find boundaries near
        direction: 'nearest', 'before', or 'after' - which boundary to find
    
    Returns:
        Adjusted timestamp at sentence boundary, or original if no boundary found
    """
    if not transcript_data:
        return target_time
    
    # Find the transcript snippet containing or nearest to target_time
    best_boundary = target_time
    min_distance = float('inf')
    
    for i, snippet in enumerate(transcript_data):
        snippet_start = snippet['start']
        snippet_end = snippet['start'] + snippet['duration']
        snippet_text = snippet['text'].strip()
        
        # Check if this snippet's start is a sentence boundary (starts with capital or after punctuation)
        if i > 0:
            prev_text = transcript_data[i-1]['text'].strip()
            # If previous snippet ends with sentence-ending punctuation, this is a sentence start
            if prev_text and prev_text[-1] in '.!?':
                distance = abs(snippet_start - target_time)
                if direction == 'nearest' and distance < min_distance:
                    min_distance = distance
                    best_boundary = snippet_start
                elif direction == 'before' and snippet_start <= target_time and (target_time - snippet_start) < min_distance:
                    min_distance = target_time - snippet_start
                    best_boundary = snippet_start
                elif direction == 'after' and snippet_start >= target_time and (snippet_start - target_time) < min_distance:
                    min_distance = snippet_start - target_time
                    best_boundary = snippet_start
        
        # Check if this snippet's end is a sentence boundary
        if snippet_text and snippet_text[-1] in '.!?':
            distance = abs(snippet_end - target_time)
            if direction == 'nearest' and distance < min_distance:
                min_distance = distance
                best_boundary = snippet_end
            elif direction == 'before' and snippet_end <= target_time and (target_time - snippet_end) < min_distance:
                min_distance = target_time - snippet_end
                best_boundary = snippet_end
            elif direction == 'after' and snippet_end >= target_time and (snippet_end - target_time) < min_distance:
                min_distance = snippet_end - target_time
                best_boundary = snippet_end
    
    # Only adjust if we found a boundary within 2 seconds (reasonable adjustment range)
    if min_distance <= 2.0:
        return best_boundary
    return target_time


def validate_clips(clips, max_clips=5, max_total_duration=50.0, min_gap=2.0, video_duration=None, min_clips=3, transcript_data=None, be_lenient=False):
    """
    Validates and selects video clips ensuring they meet quality and timing requirements.
    
    This function performs comprehensive validation including:
    - Filtering clips beyond video duration
    - Ensuring clips start/end at sentence boundaries (if transcript provided)
    - Prioritizing high-score clips
    - Maintaining proper spacing between clips
    - Ensuring minimum clip count
    
    Args:
        clips: List of dicts with 'start', 'end', 'text', 'score' keys
        max_clips: Maximum number of clips to select (default: 5)
        max_total_duration: Maximum combined duration in seconds (default: 50.0)
        min_gap: Minimum gap between clips in seconds (default: 2.0)
        video_duration: Total video duration in seconds (optional, for bounds checking)
        min_clips: Minimum number of clips required (default: 3)
        transcript_data: List of transcript snippets for sentence boundary validation (optional)
    
    Returns:
        List of validated and adjusted clips, sorted by start time
    """
    # ============================================================================
    # STEP 1: Filter clips that are outside video duration bounds
    # ============================================================================
    if video_duration:
        valid_clips = []
        for clip in clips:
            # Reject clips that start after video ends
            if clip['start'] >= video_duration:
                continue
            # Clamp end time to not exceed video duration
            if clip['end'] > video_duration:
                clip['end'] = video_duration
            # Reject clips with invalid or zero duration
            if clip['end'] <= clip['start']:
                continue
            valid_clips.append(clip)
        clips = valid_clips
    
    # ============================================================================
    # STEP 2: Adjust clip boundaries to sentence boundaries (if transcript available)
    # ============================================================================
    if transcript_data:
        for clip in clips:
            original_start = clip['start']
            original_end = clip['end']
            
            # Adjust start to nearest sentence boundary (prefer before, then after)
            adjusted_start = find_sentence_boundaries(transcript_data, original_start, 'before')
            if abs(adjusted_start - original_start) > 2.0:  # If too far, try after
                adjusted_start = find_sentence_boundaries(transcript_data, original_start, 'after')
            if abs(adjusted_start - original_start) <= 2.0:  # Only adjust if reasonable
                clip['start'] = adjusted_start
            
            # Adjust end to nearest sentence boundary (prefer after, then before)
            adjusted_end = find_sentence_boundaries(transcript_data, original_end, 'after')
            if abs(adjusted_end - original_end) > 2.0:  # If too far, try before
                adjusted_end = find_sentence_boundaries(transcript_data, original_end, 'before')
            if abs(adjusted_end - original_end) <= 2.0:  # Only adjust if reasonable
                clip['end'] = adjusted_end
            
            # Ensure end is still after start after adjustments
            if clip['end'] <= clip['start']:
                clip['end'] = clip['start'] + 1.0  # Minimum 1 second clip
    
    # ============================================================================
    # STEP 2.5: Deduplicate clips - remove clips with same or very similar timestamps
    # ============================================================================
    # Remove duplicates based on start/end times (within 1.0 second tolerance for both)
    unique_clips = []
    seen_timestamps = []
    for clip in clips:
        start = clip['start']
        end = clip['end']
        
        # Check if we've seen a similar clip (both start and end within 1.0s)
        is_duplicate = False
        for seen_start, seen_end in seen_timestamps:
            if abs(start - seen_start) < 1.0 and abs(end - seen_end) < 1.0:
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique_clips.append(clip)
            seen_timestamps.append((start, end))
    
    clips = unique_clips
    
    # ============================================================================
    # STEP 3: Sort clips by score (highest first) to prioritize best content
    # ============================================================================
    clips = sorted(clips, key=lambda c: c.get('score', 0), reverse=True)
    
    validated = []
    total_duration = 0.0
    last_end = 0.0
    
    # ============================================================================
    # STEP 4: Select clips in score order, maintaining spacing and duration limits
    # ============================================================================
    for clip in clips:
        # Stop if we've reached maximum clip count
        if len(validated) >= max_clips:
            break
        
        original_duration = clip['end'] - clip['start']
        clip_duration = original_duration
        
        # Ensure minimum gap between consecutive clips (reduce gap if being lenient)
        effective_min_gap = min_gap * 0.5 if be_lenient else min_gap
        if validated and clip['start'] < last_end + effective_min_gap:
            clip['start'] = last_end + effective_min_gap
            clip['end'] = clip['start'] + clip_duration
        
        # Check if adding this clip would exceed total duration limit (increase limit if being lenient)
        effective_max_duration = max_total_duration * 1.5 if be_lenient else max_total_duration
        if total_duration + clip_duration > effective_max_duration:
            excess = (total_duration + clip_duration) - effective_max_duration
            # For high-value clips, allow small excess (up to 5 seconds, or more if lenient)
            max_excess = 10.0 if be_lenient else 5.0
            if excess > max_excess:
                continue  # Skip this clip, it's too long
            else:
                # Fit it by reducing duration slightly
                remaining = effective_max_duration - total_duration
                min_clip_duration = 2.0 if be_lenient else 3.0  # Lower minimum if lenient
                if remaining >= min_clip_duration:
                    clip['end'] = clip['start'] + remaining
                    clip_duration = remaining
                else:
                    continue  # Not enough time left
        
        validated.append(clip)
        total_duration += clip_duration
        last_end = clip['end']
    
    # ============================================================================
    # STEP 5: Sort selected clips chronologically by start time
    # ============================================================================
    validated = sorted(validated, key=lambda c: c['start'])
    
    # ============================================================================
    # STEP 6: Ensure minimum clip count requirement is met
    # ============================================================================
    if len(validated) < min_clips:
        # Add more clips from remaining pool to meet minimum requirement
        remaining_clips = [c for c in clips if c not in validated]
        for clip in remaining_clips:
            if len(validated) >= min_clips:
                break
            
            original_duration = clip['end'] - clip['start']
            clip_duration = original_duration
            
            # Maintain minimum gap between clips (reduce gap if being lenient)
            effective_min_gap = min_gap * 0.5 if be_lenient else min_gap
            if validated and clip['start'] < last_end + effective_min_gap:
                clip['start'] = last_end + effective_min_gap
                clip['end'] = clip['start'] + clip_duration
            
            # Check duration limits (increase limit if being lenient)
            effective_max_duration = max_total_duration * 1.5 if be_lenient else max_total_duration
            if total_duration + clip_duration > effective_max_duration:
                excess = (total_duration + clip_duration) - effective_max_duration
                max_excess = 10.0 if be_lenient else 5.0
                if excess > max_excess:
                    continue
                else:
                    remaining = effective_max_duration - total_duration
                    min_clip_duration = 2.0 if be_lenient else 3.0
                    if remaining >= min_clip_duration:
                        clip['end'] = clip['start'] + remaining
                        clip_duration = remaining
                    else:
                        continue
            
            validated.append(clip)
            total_duration += clip_duration
            last_end = clip['end']
        
        # Re-sort after adding additional clips
        validated = sorted(validated, key=lambda c: c['start'])
    
    # ============================================================================
    # STEP 7: Final deduplication pass - remove any duplicates created during validation
    # ============================================================================
    final_validated = []
    seen_final = []
    for clip in validated:
        start = clip['start']
        end = clip['end']
        
        # Check if we've seen a similar clip (both start and end within 1.0s)
        is_duplicate = False
        for seen_start, seen_end in seen_final:
            if abs(start - seen_start) < 1.0 and abs(end - seen_end) < 1.0:
                is_duplicate = True
                break
        
        if not is_duplicate:
            final_validated.append(clip)
            seen_final.append((start, end))
    
    return final_validated


def truncate_transcript(transcript_text, max_chars=25000):
    """
    Truncate transcript to fit within token limits while preserving sentence boundaries.
    GPT-3.5-turbo has ~16k token limit, so we keep transcript under ~25k chars to leave room for prompt.
    """
    if len(transcript_text) <= max_chars:
        return transcript_text
    
    # Truncate at sentence boundary (look for last period, exclamation, or question mark)
    truncated = transcript_text[:max_chars]
    
    # Find the last sentence boundary
    last_period = truncated.rfind('.')
    last_exclamation = truncated.rfind('!')
    last_question = truncated.rfind('?')
    
    # Find the latest sentence boundary
    last_boundary = max(last_period, last_exclamation, last_question)
    
    if last_boundary > max_chars * 0.8:  # If we found a boundary in the last 20% of truncation
        truncated = truncated[:last_boundary + 1]
    else:
        # If no good boundary found, just truncate at word boundary
        last_space = truncated.rfind(' ')
        if last_space > max_chars * 0.8:
            truncated = truncated[:last_space]
    
    return truncated + "\n\n[... transcript truncated for length ...]"


def analyze_transcript_with_llm(transcript_text, min_clips_required=3, less_strict=False, existing_clips=None):
    # Truncate transcript if too long to fit in token limit
    truncated_transcript = truncate_transcript(transcript_text)
    
    if len(transcript_text) > len(truncated_transcript):
        print(f"⚠️  Transcript is very long ({len(transcript_text)} chars), truncating to {len(truncated_transcript)} chars for LLM analysis")
    
    prompt = f"""
You are an expert video editor. Your task is to extract the best clips from a YouTube video transcript to create an engaging reel.

## CLIP SELECTION RULES

### 1. QUANTITY (CRITICAL REQUIREMENT)
{f"- You MUST return AT LEAST {min_clips_required + 2} clips (we need more clips, be less selective)" if less_strict else f"- You MUST return AT LEAST {min_clips_required} clips (this is a hard requirement)"}
- Maximum 5 clips total
- If the transcript has enough good moments, return {min_clips_required} to 5 clips
- DO NOT return fewer than {min_clips_required} clips - this will cause the script to fail
{f"- Be LESS selective - include clips even if they're not perfect, we need quantity" if less_strict else "- Prioritize finding {min_clips_required} good clips even if some are slightly less perfect"}
{f"- Try to find clips in different parts of the video that haven't been used yet" if less_strict and existing_clips else ""}

### 2. SENTENCE BOUNDARIES (CRITICAL)
- Each clip MUST start at the beginning of a complete sentence
- Each clip MUST end at the end of a complete sentence
- NEVER cut mid-sentence or mid-thought
- Look for natural punctuation marks (periods, exclamation marks, question marks) to determine boundaries

### 3. CLIP DURATION
{f"- Target duration: 3-25 seconds per clip (be flexible)" if less_strict else "- Target duration: 5-18 seconds per clip"}
- Can exceed 18 seconds ONLY if needed to complete a full thought/sentence
- Avoid clips shorter than 3 seconds (too abrupt)
- Avoid clips longer than 25 seconds (too long for reels)
{f"- Accept shorter or longer clips if they're complete sentences" if less_strict else ""}

### 4. CONTENT SELECTION (VIRAL-FOCUSED)
{f"- Choose ANY interesting or important moments (be less selective)" if less_strict else "- Choose the MOST VIRAL-WORTHY and ENGAGING moments"}
- Prioritize clips with HIGH VIRAL POTENTIAL:
  * Shocking revelations, surprising facts, or unexpected twists
  * Controversial statements or strong opinions that spark discussion
  * Emotional peaks: dramatic moments, reactions, or intense feelings
  * Quick wins: actionable tips, hacks, or valuable insights delivered fast
  * Relatable moments that make viewers say "this is so me"
  * Memorable quotes, one-liners, or punchy statements
  * Conflict, debate, or tension that creates engagement
  * Before/after reveals or transformations
  * "You won't believe this" moments
- Think like a TikTok/Instagram algorithm: what would make people stop scrolling?
- Each clip should be a "hook" that grabs attention in the first 3 seconds
- Avoid: filler words, pauses, repetitive content, boring explanations, generic advice
{f"- Include clips even if they're not the absolute best - we need more clips" if less_strict else "- Only select clips that have genuine viral potential (score 60+)"}

### 5. SPACING & FLOW
- Distribute clips evenly throughout the video timeline
{f"- Clips can be closer together if needed (minimum 5 seconds gap is acceptable)" if less_strict else "- Avoid clustering clips too close together"}
- Ensure at least 10-20 seconds gap between clip start times (when possible)
- Create a natural narrative flow when clips are combined

### 6. TOTAL DURATION
- Combined duration of all clips: 30-50 seconds
- Aim for 35-45 seconds for optimal reel length

## OUTPUT FORMAT

Return ONLY valid JSON in this exact structure:
{{
  "clips": [
    {{
      "start": <number in seconds>,
      "end": <number in seconds>,
      "text": "<brief caption, max 15 words>",
      "score": <number 0-100>
    }}
  ]
}}

CRITICAL: You MUST return at least {min_clips_required} clips in the "clips" array. Returning fewer than {min_clips_required} clips will cause the script to fail.

## SCORING GUIDELINES (VIRAL POTENTIAL)
- score 90-100: EXTREMELY VIRAL - shocking, controversial, or highly emotional moments that will definitely go viral
- score 80-89: VERY VIRAL - strong hooks, surprising facts, or highly engaging content with high share potential
- score 70-79: HIGH VIRAL POTENTIAL - interesting, relatable, or valuable content that people will want to share
- score 60-69: MODERATE VIRAL POTENTIAL - good content but may need better hook or more emotional impact
- score 50-59: LOW VIRAL POTENTIAL - decent content but lacks the "wow" factor
- score 0-49: MINIMAL VIRAL POTENTIAL - avoid unless absolutely needed to reach minimum clip count

IMPORTANT: Prioritize clips with scores 70+ for maximum viral potential. Only include lower scores if you cannot find enough high-scoring clips.

## EXAMPLE
If transcript has: "So today I'm going to show you how to make pasta. First, you need flour. Then add eggs. Mix it well. Finally, cook it for 10 minutes."

Good clip: start=0, end=8 (complete first sentence)
Bad clip: start=5, end=12 (cuts mid-sentence)

{f"## EXISTING CLIPS (avoid overlapping with these):" if less_strict and existing_clips else ""}
{chr(10).join([f"- {clip.get('start', 0):.1f}s to {clip.get('end', 0):.1f}s: {clip.get('text', '')[:50]}" for clip in existing_clips[:5]]) if less_strict and existing_clips else ""}

## TRANSCRIPT TO ANALYZE:
    {truncated_transcript}

Remember: Only return valid JSON. Each clip must be a complete sentence from start to finish.
"""
    
    try:
        response = openai_client.chat.completions.create(
            # model="gpt-4",  # Using GPT-4 for better clip selection quality
            model="gpt-3.5-turbo",  # Alternative: cheaper option (~10x less cost)
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    except openai.RateLimitError as e:
        print("❌ OpenAI API Error: Rate limit exceeded or quota exceeded.")
        print("   Please check your OpenAI plan and billing details.")
        print("   For more info: https://platform.openai.com/docs/guides/error-codes/api-errors")
        return None
    except openai.AuthenticationError as e:
        print("❌ OpenAI API Error: Authentication failed.")
        print("   Please check your API key.")
        return None
    except openai.APIError as e:
        print(f"❌ OpenAI API Error: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error calling OpenAI API: {e}")
        return None
    
    # Parse the JSON from LLM output
    try:
        json_output = response.choices[0].message.content
        clips = json.loads(json_output)
        return clips
    except Exception as e:
        print("Error parsing JSON:", e)
        return None
    

def analyze_transcript_for_short_clips(transcript_text, min_clips=3, max_clips=10):
    """
    Use LLM to find short clips (15-25 seconds) from the transcript.
    These clips should be the most catchy, headline-worthy parts of the video.
    
    Args:
        transcript_text: Full transcript text
        min_clips: Minimum number of clips to find
        max_clips: Maximum number of clips to find
    
    Returns:
        Dictionary with 'clips' list, or None on error
    """
    # Truncate transcript if too long
    truncated_transcript = truncate_transcript(transcript_text)
    
    if len(transcript_text) > len(truncated_transcript):
        print(f"⚠️  Transcript is very long ({len(transcript_text)} chars), truncating to {len(truncated_transcript)} chars for LLM analysis")
    
    prompt = f"""
You are an expert video editor. Your task is to extract short, catchy clips from a YouTube video transcript.
These clips will be used as individual reel videos (standalone content) and should be the MOST HEADLINE-WORTHY and CATCHY parts.

## CLIP SELECTION RULES

### 1. DURATION (CRITICAL REQUIREMENT)
- Each clip MUST be between 15 seconds and 25 seconds in duration
- This is a hard requirement - clips outside this range will be rejected
- Aim for 18-22 seconds for optimal length
- The clip must be a complete, self-contained segment

### 2. SENTENCE BOUNDARIES (CRITICAL)
- Each clip MUST start at the beginning of a complete sentence
- Each clip MUST end at the end of a complete sentence
- NEVER cut mid-sentence or mid-thought
- Look for natural punctuation marks (periods, exclamation marks, question marks) to determine boundaries

### 3. QUANTITY
- Find as many valid clips as possible (up to {max_clips} clips)
- The system will select the best ones later, so find ALL good candidates
- Return all clips that meet the quality and duration requirements
- Don't limit yourself - find as many high-quality clips as you can (up to {max_clips})

### 4. CONTENT SELECTION (MOST IMPORTANT FOR SHORT CLIPS - VIRAL FOCUS)
- Choose the MOST VIRAL-WORTHY, HEADLINE-WORTHY, and HOOK-ABLE segments
- These should be the "clickbait" moments that grab attention and make people stop scrolling
- Prioritize clips with MAXIMUM VIRAL POTENTIAL:
  * Shocking revelations, surprising facts, or "wait, what?!" moments
  * Controversial statements that spark debate and comments
  * Strong opinions that divide audiences (creates engagement)
  * Emotional peaks: dramatic reactions, intense feelings, or powerful moments
  * Quick wins: actionable tips, hacks, or valuable insights delivered in 15-25 seconds
  * Relatable moments that make viewers say "this is so me" or "I needed this"
  * Memorable quotes, one-liners, or punchy statements that stick
  * Transformations, reveals, or "before/after" moments
  * Conflict, tension, or unexpected twists
  * "You won't believe this" or "This changed everything" moments
- Each clip should work as a PERFECT HOOK - something that makes people want to watch, share, and comment
- Think like TikTok/Instagram algorithm: what would make people stop scrolling and engage?
- Think of these as "trailer moments" - the most exciting, shareable parts
- Avoid: filler, transitions, context-heavy segments, boring explanations, generic advice
- Score each clip 70-100 for viral potential (lower scores only if no better options exist)

### 5. SPACING & DISTRIBUTION
- Distribute clips evenly throughout the video timeline
- Ensure clips don't overlap significantly
- Try to find clips from different parts of the video
- Each clip should be unique and offer different value

## OUTPUT FORMAT

Return ONLY valid JSON in this exact structure:
{{
  "clips": [
    {{
      "start": <number in seconds>,
      "end": <number in seconds>,
      "text": "<brief description of the clip, max 30 words>",
      "score": <number 0-100 indicating how catchy/headline-worthy this clip is>
    }},
    ...
  ]
}}

The "score" field should indicate VIRAL POTENTIAL (0-100):
- 90-100: EXTREMELY VIRAL - shocking, controversial, highly emotional, guaranteed to go viral
- 80-89: VERY VIRAL - strong hooks, surprising facts, high share potential
- 70-79: HIGH VIRAL POTENTIAL - interesting, relatable, valuable content
- 60-69: MODERATE VIRAL POTENTIAL - decent but needs better hook
- Below 60: LOW VIRAL POTENTIAL - avoid unless no better options

Prioritize clips with scores 70+ for maximum viral potential.
The "text" field should briefly describe what makes this clip viral-worthy.

## TRANSCRIPT TO ANALYZE:
{truncated_transcript}

Remember: 
- Each clip MUST be 15-25 seconds long (this is critical!)
- Each clip must start and end at complete sentence boundaries
- Find as many good clips as possible (up to {max_clips}) - the system will select the best ones
- Focus on the MOST CATCHY and HEADLINE-WORTHY moments
- Include a "score" field (0-100) for each clip indicating viral potential
- Only return valid JSON, no other text
"""
    
    try:
        print(f"🎬 Analyzing transcript to find short catchy clips (15-25s)...")
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        
        json_output = response.choices[0].message.content.strip()
        
        # Fix common JSON issues (remove markdown code blocks, fix missing commas, etc.)
        json_output = json_output.strip()
        if json_output.startswith("```json"):
            json_output = json_output[7:]
        if json_output.startswith("```"):
            json_output = json_output[3:]
        if json_output.endswith("```"):
            json_output = json_output[:-3]
        json_output = json_output.strip()
        
        # Fix missing commas between fields (common GPT error)
        json_output = re.sub(r'("\s*")([a-zA-Z_])', r'\1, \2', json_output)
        json_output = re.sub(r'(")\s*("clips")', r'\1, \2', json_output)
        
        # Try to parse JSON
        try:
            clips_info = json.loads(json_output)
        except json.JSONDecodeError as e:
            print(f"❌ Could not parse JSON: {e}")
            print("Raw response (first 500 chars):", json_output[:500])
            return None
        
        # Validate structure
        if 'clips' not in clips_info or not isinstance(clips_info['clips'], list):
            print("❌ Invalid JSON structure: missing 'clips' array")
            return None
        
        # Filter clips to ensure they meet duration requirements
        valid_clips = []
        for clip in clips_info['clips']:
            duration = clip.get('end', 0) - clip.get('start', 0)
            if 15.0 <= duration <= 25.0:
                valid_clips.append(clip)
            else:
                print(f"⚠️  Filtered out clip ({clip.get('start', 0):.1f}s - {clip.get('end', 0):.1f}s, duration: {duration:.1f}s) - outside 15-25s range")
        
        clips_info['clips'] = valid_clips
        
        if len(valid_clips) < min_clips:
            print(f"⚠️  Only found {len(valid_clips)} valid short clips (wanted at least {min_clips})")
        else:
            print(f"✅ Found {len(valid_clips)} short catchy clips (15-25s)")
        
        return clips_info
            
    except Exception as e:
        print(f"❌ Error finding short clips: {e}")
        import traceback
        traceback.print_exc()
        return None


def analyze_transcript_for_medium_clips(transcript_text, min_clips=3, max_clips=10):
    """
    Use LLM to find medium-length clips (30-60 seconds) from the transcript.
    These clips will be processed as individual reel videos.
    
    Args:
        transcript_text: Full transcript text
        min_clips: Minimum number of clips to find
        max_clips: Maximum number of clips to find
    
    Returns:
        Dictionary with 'clips' list, or None on error
    """
    # Truncate transcript if too long
    truncated_transcript = truncate_transcript(transcript_text)
    
    if len(transcript_text) > len(truncated_transcript):
        print(f"⚠️  Transcript is very long ({len(transcript_text)} chars), truncating to {len(truncated_transcript)} chars for LLM analysis")
    
    prompt = f"""
You are an expert video editor. Your task is to extract medium-length clips from a YouTube video transcript.
These clips will be used as individual reel videos (standalone content).

## CLIP SELECTION RULES

### 1. DURATION (CRITICAL REQUIREMENT)
- Each clip MUST be between 30 seconds and 60 seconds (1 minute) in duration
- This is a hard requirement - clips outside this range will be rejected
- Aim for 35-55 seconds for optimal length
- The clip must be a complete, self-contained segment

### 2. SENTENCE BOUNDARIES (CRITICAL)
- Each clip MUST start at the beginning of a complete sentence
- Each clip MUST end at the end of a complete sentence
- NEVER cut mid-sentence or mid-thought
- Look for natural punctuation marks (periods, exclamation marks, question marks) to determine boundaries

### 3. QUANTITY
- Find as many valid clips as possible (up to {max_clips} clips)
- The system will select the best ones later, so find ALL good candidates
- Return all clips that meet the quality and duration requirements
- Don't limit yourself - find as many high-quality clips as you can (up to {max_clips})

### 4. CONTENT SELECTION (VIRAL-FOCUSED)
- Choose the MOST VIRAL-WORTHY, ENGAGING, and VALUABLE segments
- Prioritize clips with HIGH VIRAL POTENTIAL:
  * Shocking revelations, surprising facts, or unexpected insights
  * Controversial statements or strong opinions that spark discussion
  * Emotional peaks: dramatic moments, reactions, or intense feelings
  * Complete valuable thoughts: key insights, actionable advice, or important takeaways
  * Engaging storytelling: compelling narratives, transformations, or reveals
  * Relatable moments that make viewers connect emotionally
  * "You need to know this" moments with high share value
- Each clip should work as standalone content (understandable without context) AND have viral potential
- Think like a social media algorithm: what would make people stop, watch, share, and comment?
- Avoid: filler words, pauses, repetitive content, boring explanations, generic advice
- Score each clip 70-100 for viral potential (lower scores only if no better options exist)

### 5. SPACING & DISTRIBUTION
- Distribute clips evenly throughout the video timeline
- Ensure clips don't overlap significantly
- Try to find clips from different parts of the video

## OUTPUT FORMAT

Return ONLY valid JSON in this exact structure:
{{
  "clips": [
    {{
      "start": <number in seconds>,
      "end": <number in seconds>,
      "text": "<brief description of the clip, max 30 words>",
      "score": <number 0-100 indicating quality>
    }},
    ...
  ]
}}

The "score" field should indicate VIRAL POTENTIAL (0-100):
- 90-100: EXTREMELY VIRAL - shocking, controversial, highly emotional, guaranteed to go viral
- 80-89: VERY VIRAL - strong hooks, surprising facts, high share potential
- 70-79: HIGH VIRAL POTENTIAL - interesting, relatable, valuable content
- 60-69: MODERATE VIRAL POTENTIAL - decent but needs better hook
- Below 60: LOW VIRAL POTENTIAL - avoid unless no better options

Prioritize clips with scores 70+ for maximum viral potential.
The "text" field should briefly describe what makes this clip viral-worthy.

## TRANSCRIPT TO ANALYZE:
{truncated_transcript}

Remember: 
- Each clip MUST be 30-60 seconds long (this is critical!)
- Each clip must start and end at complete sentence boundaries
- Find as many good clips as possible (up to {max_clips}) - the system will select the best ones
- Include a "score" field (0-100) for each clip indicating viral potential
- Only return valid JSON, no other text
"""
    
    try:
        print(f"🎬 Analyzing transcript to find medium-length clips (30-60s)...")
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        
        json_output = response.choices[0].message.content.strip()
        
        # Fix common JSON issues (remove markdown code blocks, fix missing commas, etc.)
        json_output = json_output.strip()
        if json_output.startswith("```json"):
            json_output = json_output[7:]
        if json_output.startswith("```"):
            json_output = json_output[3:]
        if json_output.endswith("```"):
            json_output = json_output[:-3]
        json_output = json_output.strip()
        
        # Fix missing commas between fields (common GPT error)
        json_output = re.sub(r'("\s*")([a-zA-Z_])', r'\1, \2', json_output)
        json_output = re.sub(r'(")\s*("clips")', r'\1, \2', json_output)
        
        # Try to parse JSON
        try:
            clips_info = json.loads(json_output)
        except json.JSONDecodeError as e:
            print(f"❌ Could not parse JSON: {e}")
            print("Raw response (first 500 chars):", json_output[:500])
            return None
        
        # Validate structure
        if 'clips' not in clips_info or not isinstance(clips_info['clips'], list):
            print("❌ Invalid JSON structure: missing 'clips' array")
            return None
        
        # Filter clips to ensure they meet duration requirements
        valid_clips = []
        for clip in clips_info['clips']:
            duration = clip.get('end', 0) - clip.get('start', 0)
            if 30.0 <= duration <= 60.0:
                valid_clips.append(clip)
            else:
                print(f"⚠️  Filtered out clip ({clip.get('start', 0):.1f}s - {clip.get('end', 0):.1f}s, duration: {duration:.1f}s) - outside 30-60s range")
        
        clips_info['clips'] = valid_clips
        
        if len(valid_clips) < min_clips:
            print(f"⚠️  Only found {len(valid_clips)} valid medium clips (wanted at least {min_clips})")
        else:
            print(f"✅ Found {len(valid_clips)} medium-length clips (30-60s)")
        
        return clips_info
        
    except Exception as e:
        print(f"❌ Error analyzing transcript for medium clips: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_short_clip_reels(base_video_file, clips_info, transcript_data=None, quality="hd", remove_watermark=False, font_style="bold", font_color="white_red"):
    """
    Create individual reel videos for short clips (15-25 seconds).
    These are the most catchy, headline-worthy parts of the video.
    
    Args:
        base_video_file: Path to the base video file
        clips_info: Dictionary with 'clips' list
        transcript_data: Transcript data for subtitles
    
    Returns:
        List of created video file paths
    """
    created_reels = []
    
    if not clips_info or not clips_info.get('clips'):
        print("ℹ️  No short clips to process")
        return created_reels
    
    # Clean up old short reel files before creating new ones
    old_short_reels = glob.glob("short_reel_*.mp4")
    for old_file in old_short_reels:
        try:
            os.remove(old_file)
            print(f"🗑️  Removed old file: {old_file}")
        except Exception as e:
            print(f"⚠️  Could not remove {old_file}: {e}")
    
    print(f"\n{'='*60}")
    print(f"Creating short catchy reel clips (15-25s) in parallel...")
    print(f"{'='*60}")
    
    # Filter valid clips first - don't hardcode limit, use all valid clips found
    valid_clips = []
    for i, clip in enumerate(clips_info["clips"]):
        start = clip.get("start", 0)
        end = clip.get("end", 0)
        duration = end - start
        
        if 15.0 <= duration <= 25.0:
            valid_clips.append((i, clip))
        else:
            print(f"⏭️  Skipping clip {i + 1} ({duration:.1f}s) - outside 15-25s range")
    
    if not valid_clips:
        print("ℹ️  No valid short clips to process")
        return created_reels
    
    print(f"ℹ️  Processing {len(valid_clips)} short clips found")
    
    # Process clips in parallel using ThreadPoolExecutor
    def process_clip(index_clip_pair):
        i, clip = index_clip_pair
        try:
            reel_file = create_single_clip_short(base_video_file, clip, i, transcript_data, output_prefix="short_reel", quality=quality, remove_watermark=remove_watermark, font_style=font_style, font_color=font_color)
            return reel_file
        except Exception as e:
            print(f"❌ Error processing short clip {i + 1}: {e}")
            return None
    
    # Use reduced number of workers to save memory
    # Each clip rendering can use 2-4 GB RAM, so limit to 2 workers max
    # With job-level concurrency at 1, this is safe for 16 GB RAM servers
    max_workers = min(2, len(valid_clips))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_clip = {executor.submit(process_clip, index_clip): index_clip for index_clip in valid_clips}
        
        # Collect results as they complete
        for future in as_completed(future_to_clip):
            reel_file = future.result()
            if reel_file:
                created_reels.append(reel_file)
            # Force garbage collection after each clip is processed
            import gc
            gc.collect()
    
    if created_reels:
        print(f"\n✅ Created {len(created_reels)} short catchy reel clips:")
        for reel_file in created_reels:
            print(f"   - {reel_file}")
    else:
        print(f"\nℹ️  No short clips were successfully created")
    
    return created_reels


def create_medium_clip_reels(base_video_file, clips_info, transcript_data=None, quality="hd", remove_watermark=False, font_style="bold", font_color="white_red"):
    """
    Create individual reel videos for medium-length clips (30-60 seconds).
    Uses the same processing as main reel clips (word-by-word subtitles, styling, etc.)
    
    Args:
        base_video_file: Path to the base video file
        clips_info: Dictionary with 'clips' list
        transcript_data: Transcript data for subtitles
    
    Returns:
        List of created video file paths
    """
    created_reels = []
    
    if not clips_info or not clips_info.get('clips'):
        print("ℹ️  No medium clips to process")
        return created_reels
    
    # Clean up old medium reel files before creating new ones
    old_medium_reels = glob.glob("medium_reel_*.mp4")
    for old_file in old_medium_reels:
        try:
            os.remove(old_file)
            print(f"🗑️  Removed old file: {old_file}")
        except Exception as e:
            print(f"⚠️  Could not remove {old_file}: {e}")
    
    print(f"\n{'='*60}")
    print(f"Creating medium-length reel clips (30-60s) in parallel...")
    print(f"{'='*60}")
    
    # Filter valid clips first - don't hardcode limit, use all valid clips found
    valid_clips = []
    for i, clip in enumerate(clips_info["clips"]):
        start = clip.get("start", 0)
        end = clip.get("end", 0)
        duration = end - start
        
        if 30.0 <= duration <= 60.0:
            valid_clips.append((i, clip))
        else:
            print(f"⏭️  Skipping clip {i + 1} ({duration:.1f}s) - outside 30-60s range")
    
    if not valid_clips:
        print("ℹ️  No valid medium clips to process")
        return created_reels
    
    print(f"ℹ️  Processing {len(valid_clips)} medium clips found")
    
    # Process clips in parallel using ThreadPoolExecutor
    def process_clip(index_clip_pair):
        i, clip = index_clip_pair
        try:
            reel_file = create_single_clip_short(base_video_file, clip, i, transcript_data, output_prefix="medium_reel", quality=quality, remove_watermark=remove_watermark, font_style=font_style, font_color=font_color)
            return reel_file
        except Exception as e:
            print(f"❌ Error processing medium clip {i + 1}: {e}")
            return None
    
    # Use reduced number of workers to save memory
    # Each clip rendering can use 2-4 GB RAM, so limit to 2 workers max
    # With job-level concurrency at 1, this is safe for 16 GB RAM servers
    max_workers = min(2, len(valid_clips))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_clip = {executor.submit(process_clip, index_clip): index_clip for index_clip in valid_clips}
        
        # Collect results as they complete
        for future in as_completed(future_to_clip):
            reel_file = future.result()
            if reel_file:
                created_reels.append(reel_file)
            # Force garbage collection after each clip is processed
            import gc
            gc.collect()
    
    if created_reels:
        print(f"\n✅ Created {len(created_reels)} medium-length reel clips:")
        for reel_file in created_reels:
            print(f"   - {reel_file}")
    else:
        print(f"\nℹ️  No medium clips were successfully created")
    
    return created_reels


def generate_metadata(transcript_text, clips_info, output_file="reel_metadata.txt"):
    """
    Generate 5 titles, descriptions, and hashtags for the reel using GPT-3.5.
    Writes the results to a file.
    
    Args:
        transcript_text: The full transcript text
        clips_info: Dictionary with clip information
        output_file: File to write metadata to (default: reel_metadata.txt)
    """
    # Extract clip texts for context
    clip_texts = [clip.get('text', '') for clip in clips_info.get('clips', [])]
    clips_summary = "\n".join([f"Clip {i+1}: {text}" for i, text in enumerate(clip_texts)])
    
    prompt = f"""
You are a social media content creator. Generate 5 different title, description, and hashtag combinations for a YouTube/TikTok reel.

Based on this video content:
Transcript: {transcript_text[:2000]}

Key clips:
{clips_summary}

Generate 5 unique combinations, each with:
- Title: Catchy, engaging title (max 60 characters)
- Description: Compelling description (2-3 sentences, max 200 characters)
- Hashtags: 5-10 relevant hashtags (mix of popular and niche)

CRITICAL: Return ONLY valid JSON. Every field must be followed by a comma except the last field in each object. No text before or after the JSON.

Return valid JSON in this EXACT format:
{{
  "options": [
    {{
      "title": "Title here",
      "description": "Description here",
      "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3"]
    }},
    {{
      "title": "Title here",
      "description": "Description here",
      "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3"]
    }}
  ]
}}

Remember: Every field needs a comma after it, except the last field in each object. Make sure all 5 options are included.
"""
    
    def fix_json_common_issues(json_str):
        """Try to fix common JSON formatting issues."""
        # Remove markdown code blocks if present
        json_str = json_str.strip()
        if json_str.startswith("```json"):
            json_str = json_str[7:]
        if json_str.startswith("```"):
            json_str = json_str[3:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()
        
        # Fix missing commas after string values (most common issue)
        # Pattern: "description": "text with quotes" "hashtags" -> add comma
        # This handles: "description": "text" "hashtags" or "description": "text"\n      "hashtags"
        json_str = re.sub(r'(")\s*\n\s*("hashtags")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'(")\s*("hashtags")', r'\1, \2', json_str)
        json_str = re.sub(r'(")\s*\n\s*("title")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'(")\s*("title")', r'\1, \2', json_str)
        json_str = re.sub(r'(")\s*\n\s*("description")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'(")\s*("description")', r'\1, \2', json_str)
        
        # Fix missing commas between object fields (general case)
        # Pattern: "field": "value" "next_field" -> "field": "value", "next_field"
        json_str = re.sub(r'(")\s*\n\s*(")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'("\s*")([a-zA-Z_])', r'\1, \2', json_str)
        
        return json_str
    
    try:
        print("📝 Generating titles, descriptions, and hashtags...")
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",  # Using GPT-3.5 for cost efficiency
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8  # Higher temperature for more creative variations
        )
        
        json_output = response.choices[0].message.content
        
        # Try to parse JSON, with repair attempts
        try:
            metadata = json.loads(json_output)
        except json.JSONDecodeError:
            # Try to fix common issues
            print("⚠️  Attempting to fix JSON formatting issues...")
            fixed_json = fix_json_common_issues(json_output)
            try:
                metadata = json.loads(fixed_json)
            except json.JSONDecodeError as e:
                print(f"❌ Could not parse JSON even after repair: {e}")
                print("Raw response (first 1000 chars):", json_output[:1000])
                return None
        
        # Validate structure
        if 'options' not in metadata or not isinstance(metadata['options'], list):
            print("❌ Invalid JSON structure: missing 'options' array")
            return None
        
        if len(metadata['options']) < 5:
            print(f"⚠️  Only got {len(metadata['options'])} options instead of 5")
        
        # Add #SizaClipps hashtag to all options
        for option in metadata.get('options', []):
            hashtags = option.get('hashtags', [])
            if '#SizaClipps' not in hashtags:
                hashtags.insert(0, '#SizaClipps')  # Add at the beginning
            option['hashtags'] = hashtags
        
        # Write to file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("REEL METADATA - 5 OPTIONS FOR PUBLISHING\n")
            f.write("=" * 80 + "\n\n")
            
            for i, option in enumerate(metadata.get('options', []), 1):
                f.write(f"OPTION {i}:\n")
                f.write("-" * 80 + "\n")
                f.write(f"Title: {option.get('title', 'N/A')}\n")
                f.write(f"\nDescription:\n{option.get('description', 'N/A')}\n")
                f.write(f"\nHashtags:\n{' '.join(option.get('hashtags', []))}\n")
                f.write("\n" + "=" * 80 + "\n\n")
            
            # Also write a summary section
            f.write("\n" + "=" * 80 + "\n")
            f.write("QUICK COPY-PASTE FORMAT\n")
            f.write("=" * 80 + "\n\n")
            
            for i, option in enumerate(metadata.get('options', []), 1):
                f.write(f"--- OPTION {i} ---\n")
                f.write(f"{option.get('title', 'N/A')}\n\n")
                f.write(f"{option.get('description', 'N/A')}\n\n")
                f.write(f"{' '.join(option.get('hashtags', []))}\n\n")
        
        print(f"✅ Metadata saved to: {output_file}")
        return metadata
        
    except Exception as e:
        print(f"❌ Error generating metadata: {e}")
        return None


def generate_metadata_for_video(clip_text, clip_transcript_segment, video_type="reel", video_index=0):
    """
    Generate 3 titles, descriptions, and hashtags for a specific video using GPT-3.5.
    
    Args:
        clip_text: The clip text/description (what the clip is about)
        clip_transcript_segment: The transcript text for this specific clip/video
        video_type: Type of video ("main_reel", "medium_reel", "short_reel")
        video_index: Index of the video (for medium/short reels)
    
    Returns:
        Dictionary with "options" list containing 3 metadata options, or None on error
    """
    
    prompt = f"""
You are an expert viral content creator for TikTok, Instagram Reels, and YouTube Shorts. Your job is to create MAXIMUM ENGAGEMENT content that stops the scroll and gets shared.

This video is about:
{clip_text}

Transcript content for this specific video:
{clip_transcript_segment[:2000] if clip_transcript_segment else "N/A"}

## VIRAL CONTENT STRATEGY:
1. **Hook-First Titles**: Start with shocking questions, numbers, or bold statements that create curiosity
   - Examples: "This Changed Everything", "You Won't Believe What Happened", "I Tried This For 30 Days..."
2. **Emotional Triggers**: Use words that evoke strong emotions (shocked, amazed, angry, inspired, scared)
3. **Controversy/Debate**: Frame content to spark discussion and comments
4. **Relatability**: Make it about the viewer ("You need to see this", "This will change your life")
5. **Trending Topics**: Reference current trends, memes, or popular culture when relevant
6. **Value Proposition**: Promise transformation, revelation, or entertainment

Generate 3 unique combinations, each with:
- Title: VIRAL HOOK title (max 60 characters) - must stop scrolling immediately
- Description: Compelling description (2-3 sentences, max 200 characters) that creates urgency and engagement
- Hashtags: 5-10 relevant hashtags (mix of trending/popular and niche-specific)

CRITICAL: Return ONLY valid JSON. Every field must be followed by a comma except the last field in each object. No text before or after the JSON.

Return valid JSON in this EXACT format:
{{
  "options": [
    {{
      "title": "Title here",
      "description": "Description here",
      "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3"]
    }},
    {{
      "title": "Title here",
      "description": "Description here",
      "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3"]
    }},
    {{
      "title": "Title here",
      "description": "Description here",
      "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3"]
    }}
  ]
}}

Remember: Every field needs a comma after it, except the last field in each object. Make sure all 3 options are included.
"""
    
    def fix_json_common_issues(json_str):
        """Try to fix common JSON formatting issues."""
        # Remove markdown code blocks if present
        json_str = json_str.strip()
        if json_str.startswith("```json"):
            json_str = json_str[7:]
        if json_str.startswith("```"):
            json_str = json_str[3:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()
        
        # Fix missing commas after string values (most common issue)
        # Pattern: "description": "text with quotes" "hashtags" -> add comma
        # This handles: "description": "text" "hashtags" or "description": "text"\n      "hashtags"
        json_str = re.sub(r'(")\s*\n\s*("hashtags")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'(")\s*("hashtags")', r'\1, \2', json_str)
        json_str = re.sub(r'(")\s*\n\s*("title")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'(")\s*("title")', r'\1, \2', json_str)
        json_str = re.sub(r'(")\s*\n\s*("description")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'(")\s*("description")', r'\1, \2', json_str)
        
        # Fix missing commas between object fields (general case)
        # Pattern: "field": "value" "next_field" -> "field": "value", "next_field"
        json_str = re.sub(r'(")\s*\n\s*(")', r'\1,\n      \2', json_str)
        json_str = re.sub(r'("\s*")([a-zA-Z_])', r'\1, \2', json_str)
        
        return json_str
    
    try:
        print(f"📝 Generating metadata for {video_type} (video {video_index + 1})...")
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        
        json_output = response.choices[0].message.content
        
        # Try to parse JSON, with repair attempts
        try:
            metadata = json.loads(json_output)
        except json.JSONDecodeError:
            # Try to fix common issues
            print("⚠️  Attempting to fix JSON formatting issues...")
            fixed_json = fix_json_common_issues(json_output)
            try:
                metadata = json.loads(fixed_json)
            except json.JSONDecodeError as e:
                print(f"❌ Could not parse JSON even after repair: {e}")
                print("Raw response (first 1000 chars):", json_output[:1000])
                return None
        
        # Validate structure
        if 'options' not in metadata or not isinstance(metadata['options'], list):
            print("❌ Invalid JSON structure: missing 'options' array")
            return None
        
        if len(metadata['options']) < 3:
            print(f"⚠️  Only got {len(metadata['options'])} options instead of 3")
        
        # Add #SizaClipps hashtag to all options
        for option in metadata.get('options', []):
            hashtags = option.get('hashtags', [])
            if '#SizaClipps' not in hashtags:
                hashtags.insert(0, '#SizaClipps')  # Add at the beginning
            option['hashtags'] = hashtags
        
        print(f"✅ Generated {len(metadata['options'])} metadata options for {video_type}")
        return metadata
        
    except Exception as e:
        print(f"❌ Error generating metadata for {video_type}: {e}")
        import traceback
        traceback.print_exc()
        return None


def calculate_virality_score(clip_text, clip_transcript_segment, video_type="reel", video_index=None):
    """
    Calculate virality score (1-100) for a video using LLM.
    
    Args:
        clip_text: The clip text/description (what the clip is about)
        clip_transcript_segment: The transcript text for this specific clip/video
        video_type: Type of video ("main_reel", "medium_reel", "short_reel")
        video_index: Optional index to help ensure unique scoring
    
    Returns:
        Integer score from 1-100, or None on error
    """
    
    # Add variation to ensure each video gets a unique score
    import random
    import hashlib
    variation_seed = hashlib.md5(f"{clip_text}{clip_transcript_segment}{video_index}".encode()).hexdigest()[:8]
    
    prompt = f"""
You are a social media analytics expert with deep knowledge of what makes content go viral on TikTok, Instagram Reels, and YouTube Shorts. Analyze this video content and assign a PRECISE, UNIQUE virality score from 1 to 100.

CRITICAL: Each video must receive a DIFFERENT score. Even if videos seem similar, find distinguishing factors (hook strength, emotional peaks, shareability nuances, timing, etc.) and assign distinct scores. Do NOT give the same score to multiple videos.

IMPORTANT: Be GENEROUS with scores. Most engaging content should score 70-90. Only truly boring or irrelevant content should score below 50. Look for viral potential even in seemingly ordinary content.

Video content:
{clip_text}

Transcript:
{clip_transcript_segment[:2000] if clip_transcript_segment else "N/A"}

## VIRALITY SCORING CRITERIA (Total: 100 points)

Evaluate each factor independently and assign precise points. Look for POSITIVE aspects and viral potential:

1. **Hook Quality** (0-25 points): 
   - 20-25: Opens with shocking statement, question, or visual that stops scrolling immediately, OR has a compelling narrative hook
   - 15-19: Strong hook that creates curiosity or intrigue, OR interesting opening moment
   - 10-14: Decent hook but could be stronger, OR has some engaging element
   - 5-9: Weak hook, takes time to get interesting
   - 0-4: No hook, boring opening

2. **Emotional Impact** (0-20 points):
   - 17-20: Evokes intense emotions (shock, anger, joy, fear, surprise, inspiration) that create strong reactions
   - 13-16: Strong emotional response, makes viewers feel something (excitement, curiosity, relatability)
   - 9-12: Moderate emotional impact, some connection with viewers
   - 5-8: Mild emotional response
   - 0-4: No emotional impact, flat content

3. **Shareability** (0-20 points):
   - 17-20: Highly shareable - people will definitely share this (relatable, controversial, educational, or "you need to see this")
   - 13-16: Very shareable - strong share potential (interesting, useful, or entertaining)
   - 9-12: Moderately shareable - some people might share (decent content)
   - 5-8: Low shareability - unlikely to be shared
   - 0-4: Not shareable at all

4. **Controversy/Debate Potential** (0-15 points):
   - 13-15: Highly controversial or divisive - will spark intense debate and comments
   - 10-12: Somewhat controversial - will generate discussion, OR thought-provoking content
   - 7-9: Mildly controversial or thought-provoking, OR has discussion-worthy elements
   - 4-6: Neutral, won't create debate, but not negative
   - 0-3: Completely uncontroversial, no discussion value

5. **Value/Entertainment** (0-10 points):
   - 9-10: Exceptional value or highly entertaining (educational, funny, inspiring, or very engaging)
   - 7-8: Good value or entertaining (interesting, informative, or enjoyable)
   - 5-6: Moderate value or entertainment (decent content)
   - 3-4: Low value or minimal entertainment
   - 0-2: No value or boring

6. **Trend Relevance & Timing** (0-10 points):
   - 9-10: Perfectly aligned with current trends, topics, or cultural moments
   - 7-8: Relevant to current trends, OR evergreen content that's always relevant
   - 5-6: Somewhat relevant, OR has timeless appeal
   - 3-4: Not very relevant to current trends
   - 0-2: Completely irrelevant or outdated

## SCORING INSTRUCTIONS

1. Evaluate each criterion independently and assign precise points (not ranges)
2. Sum all points to get the final score (1-100)
3. Use the FULL range of 1-100 - don't cluster scores in the middle
4. Be GENEROUS: Look for viral potential. Most good content should score 65-85
5. Be precise: a score of 73 is different from 74 or 75
6. Consider that even "ordinary" content can go viral if it's relatable, entertaining, or has a good hook

## SCORE DISTRIBUTION GUIDELINES

- **95-100**: EXTREMELY VIRAL - This will definitely go viral. Shocking, controversial, or highly emotional content that creates massive engagement
- **85-94**: VERY VIRAL - High viral potential, strong hooks, surprising content
- **75-84**: HIGH VIRAL POTENTIAL - Good content with viral characteristics
- **65-74**: MODERATE VIRAL POTENTIAL - Decent content but needs stronger elements
- **55-64**: LOW-MODERATE POTENTIAL - Some viral elements but missing key factors
- **45-54**: LOW POTENTIAL - Lacks most viral characteristics
- **35-44**: VERY LOW POTENTIAL - Minimal viral appeal
- **25-34**: MINIMAL POTENTIAL - Almost no viral characteristics
- **15-24**: EXTREMELY LOW - Boring, generic content
- **1-14**: NO VIRAL POTENTIAL - Completely unengaging

Return ONLY a JSON object with this exact format:
{{
  "score": <precise integer from 1 to 100>,
  "reasoning": "<brief explanation in 1-2 sentences explaining the score>"
}}

CRITICAL: 
- Use the FULL range 1-100, be precise (e.g., 73, 84, 92, not just 70, 80, 90)
- Don't round to multiples of 5 or 10 - use all numbers
- Higher scores (80+) should be reserved for truly exceptional viral content
- Lower scores (below 50) should reflect genuinely poor viral potential

Remember: Return ONLY valid JSON, no other text.
"""
    
    try:
        print(f"🔥 Calculating virality score for {video_type}...")
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7  # Higher temperature to ensure unique scores for each video
        )
        
        json_output = response.choices[0].message.content.strip()
        
        # Remove markdown code blocks if present
        if json_output.startswith("```json"):
            json_output = json_output[7:]
        if json_output.startswith("```"):
            json_output = json_output[3:]
        if json_output.endswith("```"):
            json_output = json_output[:-3]
        json_output = json_output.strip()
        
        try:
            result = json.loads(json_output)
            score = result.get('score')
            
            if score is None:
                print("⚠️  No score in LLM response")
                return None
            
            # Ensure score is between 1-100
            score = max(1, min(100, int(score)))
            
            reasoning = result.get('reasoning', '')
            print(f"✅ Virality score: {score}/100 - {reasoning}")
            
            return score
            
        except json.JSONDecodeError as e:
            print(f"❌ Could not parse virality score JSON: {e}")
            print(f"Raw response: {json_output[:200]}")
            return None
            
    except Exception as e:
        print(f"❌ Error calculating virality score: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    import signal
    import sys
    import atexit
    
    def emergency_exit():
        """Emergency exit handler"""
        print("\n⚠️  Emergency exit triggered")
        sys.stdout.flush()
        sys.stderr.flush()
    
    atexit.register(emergency_exit)
    
    # Handle SIGTERM and SIGINT gracefully (in case system kills the process)
    def signal_handler(signum, frame):
        signal_name = signal.Signals(signum).name if hasattr(signal.Signals, '__getitem__') else str(signum)
        print(f"\n\n⚠️  Received termination signal ({signal_name}). Process is being killed by system.")
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Try to write to log file directly before exit
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.critical(f"Process killed by signal: {signal_name}")
        except:
            pass
        
        emergency_exit()
        raise SystemExit(1)
    
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        # SIGQUIT - usually sent by system on resource exhaustion
        if hasattr(signal, 'SIGQUIT'):
            signal.signal(signal.SIGQUIT, signal_handler)
    except (ValueError, OSError) as e:
        # Some signals might not be available on all platforms
        print(f"⚠️  Could not set signal handlers: {e}")
    
    # Force flush stdout/stderr regularly
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except:
            pass
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(line_buffering=True)
        except:
            pass
    
    # 1️⃣ Ask for YouTube URL
    try:
        try:
            logger = logging.getLogger(__name__)
            logger.info("Prompting user for YouTube URL...")
        except:
            print("Prompting user for YouTube URL...")
        
        url = input("Enter YouTube video URL: ")
        
        try:
            logger = logging.getLogger(__name__)
            logger.info(f"User provided URL (length: {len(url)} chars)")
        except:
            pass
    except (EOFError, KeyboardInterrupt) as e:
        try:
            logger = logging.getLogger(__name__)
            logger.error(f"Input error: {e}")
        except:
            pass
        print(f"\n⚠️  Input cancelled: {e}")
        return
    except Exception as e:
        try:
            logger = logging.getLogger(__name__)
            logger.error(f"Unexpected error getting input: {e}", exc_info=True)
        except:
            import traceback
            traceback.print_exc()
        print(f"\n❌ Error getting input: {e}")
        return
    
    # Extract video ID from URL (simple split for now)
    try:
        video_id = url.split("v=")[1].split("&")[0]
    except IndexError:
        print("Invalid YouTube URL")
        return

    # 2️⃣ Get transcript
    transcript_text, transcript_data = get_transcript(video_id)
    
    # Check if transcript retrieval failed
    if transcript_text is None or transcript_data is None:
        print("❌ Cannot proceed without transcript. Exiting to avoid unnecessary API calls.")
        return
    
    # 3️⃣ Download video first to get duration
    video_file = None
    temp_video = None
    try:
        video_file = download_video(url)
        if not video_file:
            print("❌ Failed to download video")
            return
        
        # Get video duration for validation
        try:
            temp_video = VideoFileClip(video_file)
            video_duration = temp_video.duration
        finally:
            if temp_video is not None:
                try:
                    temp_video.close()
                except:
                    pass
        
        # Calculate minimum clips based on video duration
        video_duration_minutes = video_duration / 60.0
        if video_duration_minutes >= 5 and video_duration_minutes < 10:
            min_clips_required = 2
        elif video_duration_minutes >= 10 and video_duration_minutes < 20:
            min_clips_required = 3
        elif video_duration_minutes >= 20:
            min_clips_required = 4
        else:
            min_clips_required = 2  # Default for videos under 5 minutes
        
        print(f"📊 Video duration: {video_duration_minutes:.1f} minutes - requiring at least {min_clips_required} clips")
        
        # 4️⃣ Find clips in parallel using threading
        print(f"\n{'='*60}")
        print("Finding clips (main reel + medium clips) in parallel...")
        print(f"{'='*60}")
        
        # Thread 1: Find main clips
        main_clips_result = [None]
        main_clips_error = [None]
        
        def find_main_clips():
            try:
                result = analyze_transcript_with_llm(transcript_text, min_clips_required=min_clips_required, less_strict=False)
                main_clips_result[0] = result
            except Exception as e:
                main_clips_error[0] = e
        
        # Thread 2: Find medium clips (30-60s) - limit to max 2 clips
        medium_clips_result = [None]
        medium_clips_error = [None]
        
        def find_medium_clips():
            try:
                result = analyze_transcript_for_medium_clips(transcript_text, min_clips=1, max_clips=2)
                medium_clips_result[0] = result
            except Exception as e:
                medium_clips_error[0] = e
        
        # Thread 3: Find short clips (15-25s) - limit to max 4 clips
        short_clips_result = [None]
        short_clips_error = [None]
        
        def find_short_clips():
            try:
                result = analyze_transcript_for_short_clips(transcript_text, min_clips=1, max_clips=4)
                short_clips_result[0] = result
            except Exception as e:
                short_clips_error[0] = e
        
        # Start all three threads
        thread1 = threading.Thread(target=find_main_clips, name="MainClipsFinder")
        thread2 = threading.Thread(target=find_medium_clips, name="MediumClipsFinder")
        thread3 = threading.Thread(target=find_short_clips, name="ShortClipsFinder")
        
        thread1.start()
        thread2.start()
        thread3.start()
        
        # Wait for all threads to complete
        thread1.join()
        thread2.join()
        thread3.join()
        
        # Check for errors
        if main_clips_error[0]:
            print(f"❌ Error finding main clips: {main_clips_error[0]}")
            return
        
        if medium_clips_error[0]:
            print(f"⚠️  Error finding medium clips: {medium_clips_error[0]}")
        
        if short_clips_error[0]:
            print(f"⚠️  Error finding short clips: {short_clips_error[0]}")
        
        # Use the results
        clips_info = main_clips_result[0]
        if not clips_info or "clips" not in clips_info:
            print("No clips returned from LLM")
            return
        
        # Re-validate clips with video duration to filter out invalid timestamps
        # First try with normal strictness
        clips_info["clips"] = validate_clips(clips_info["clips"], video_duration=video_duration, min_clips=min_clips_required, transcript_data=transcript_data)
        
        if not clips_info["clips"]:
            print("❌ No valid clips found after validation")
            return
        
        # If we still don't have enough clips after validation, try one more time with less strict requirements
        if len(clips_info["clips"]) < min_clips_required:
            # Try re-validating with lenient mode first
            lenient_clips = validate_clips(clips_info["clips"], video_duration=video_duration, min_clips=min_clips_required, transcript_data=transcript_data, be_lenient=True)
            if len(lenient_clips) > len(clips_info["clips"]):
                print(f"   Re-validating with lenient mode: found {len(lenient_clips)} clips (was {len(clips_info['clips'])})")
                clips_info["clips"] = lenient_clips
            
            # If still not enough, try re-analyzing
        if len(clips_info["clips"]) < min_clips_required:
            print(f"⚠️  Only found {len(clips_info['clips'])} clips after validation, need {min_clips_required}.")
            print("   Re-analyzing with less strict requirements to find more clips...")
            
            # Keep existing valid clips
            existing_valid_clips = clips_info["clips"].copy()
            print(f"   Keeping {len(existing_valid_clips)} existing valid clips...")
            
            # Re-analyze with less strict requirements (ask for more clips, be less selective)
            new_clips_info = analyze_transcript_with_llm(
                transcript_text, 
                min_clips_required=min_clips_required + 2,  # Ask for more than needed
                less_strict=True,
                existing_clips=existing_valid_clips
            )
            if not new_clips_info or "clips" not in new_clips_info:
                print("❌ Failed to get clips on retry")
                return
            
            # Combine existing clips with new ones
            all_clips = existing_valid_clips + new_clips_info["clips"]
            print(f"   Found {len(new_clips_info['clips'])} new clips, combining with {len(existing_valid_clips)} existing clips...")
            print(f"   Total clips before validation: {len(all_clips)}")
            
            # Re-validate all clips together (be lenient since we're retrying)
            clips_info["clips"] = validate_clips(all_clips, video_duration=video_duration, min_clips=min_clips_required, transcript_data=transcript_data, be_lenient=True)
            
            print(f"   Total clips after validation: {len(clips_info['clips'])}")
            
            if len(clips_info["clips"]) < min_clips_required:
                if len(clips_info["clips"]) == 0:
                    print(f"❌ No valid clips found after retry")
                    return
                print(f"⚠️  Only found {len(clips_info['clips'])} distinct clips (wanted {min_clips_required}), but proceeding with available clips")
                # Debug: show what clips we have
                if clips_info["clips"]:
                    print("   Clips found:")
                    for i, clip in enumerate(clips_info["clips"], 1):
                        print(f"     {i}. {clip.get('start', 0):.1f}s - {clip.get('end', 0):.1f}s: {clip.get('text', '')[:50]}")
                # Continue anyway - don't return, proceed with what we have
        
        # 5️⃣ Create reel and medium clips in parallel
        medium_clips_info = medium_clips_result[0]
        short_clips_info = short_clips_result[0]
        
        # Limit medium clips to max 4 if more were found
        if medium_clips_info and medium_clips_info.get('clips'):
            if len(medium_clips_info['clips']) > 4:
                print(f"⚠️  Limiting medium clips to 4 (found {len(medium_clips_info['clips'])})")
                medium_clips_info['clips'] = medium_clips_info['clips'][:4]
        
        # Limit short clips to max 5 if more were found
        if short_clips_info and short_clips_info.get('clips'):
            if len(short_clips_info['clips']) > 5:
                print(f"⚠️  Limiting short clips to 5 (found {len(short_clips_info['clips'])})")
                short_clips_info['clips'] = short_clips_info['clips'][:5]
        
        # Thread for main reel creation
        main_reel_error = [None]
        main_reel_done = [False]
        
        def create_main_reel_thread():
            try:
                create_reel(video_file, clips_info, min_clips=min_clips_required, transcript_data=transcript_data)
                print("🎬 Reel created successfully: reel.mp4")
                main_reel_done[0] = True
            except Exception as e:
                main_reel_error[0] = e
                import traceback
                print(f"❌ Error creating main reel: {e}")
                print("Full traceback:")
                traceback.print_exc()
        
        # Thread for medium clips creation
        medium_reels_error = [None]
        medium_reels_done = [False]
        
        def create_medium_reels_thread():
            try:
                if medium_clips_info and medium_clips_info.get('clips'):
                    print(f"✅ Found {len(medium_clips_info['clips'])} medium clips")
                    create_medium_clip_reels(video_file, medium_clips_info, transcript_data=transcript_data)
                else:
                    print("ℹ️  No medium-length clips found")
                medium_reels_done[0] = True
            except Exception as e:
                medium_reels_error[0] = e
                print(f"❌ Error creating medium reels: {e}")
        
        # Thread for short clips creation
        short_reels_error = [None]
        short_reels_done = [False]
        
        def create_short_reels_thread():
            try:
                if short_clips_info and short_clips_info.get('clips'):
                    print(f"✅ Found {len(short_clips_info['clips'])} short clips")
                    create_short_clip_reels(video_file, short_clips_info, transcript_data=transcript_data)
                else:
                    print("ℹ️  No short clips found")
                short_reels_done[0] = True
            except Exception as e:
                short_reels_error[0] = e
                print(f"❌ Error creating short reels: {e}")
        
        # Start all three threads in parallel
        reel_thread = threading.Thread(target=create_main_reel_thread, name="MainReelCreator")
        medium_thread = threading.Thread(target=create_medium_reels_thread, name="MediumReelsCreator")
        short_thread = threading.Thread(target=create_short_reels_thread, name="ShortReelsCreator")
        
        reel_thread.start()
        medium_thread.start()
        short_thread.start()
        
        # Wait for all threads to complete
        reel_thread.join()
        medium_thread.join()
        short_thread.join()
        
        # Check for errors
        if main_reel_error[0]:
            print(f"❌ Failed to create main reel")
        
        if medium_reels_error[0]:
            print(f"❌ Failed to create medium reels")
        
        if short_reels_error[0]:
            print(f"❌ Failed to create short reels")
        
        # 6️⃣ Generate metadata (titles, descriptions, hashtags)
        generate_metadata(transcript_text, clips_info)
        
    except Exception as e:
        print(f"\n❌ Error in main processing: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 6️⃣ Clean up - delete base video file (always runs, even on error)
        # Ensure temp_video is closed if still open
        if 'temp_video' in locals() and temp_video is not None:
            try:
                temp_video.close()
            except:
                pass
        
        if video_file:
            try:
                if os.path.exists(video_file):
                    os.remove(video_file)
                    print(f"🗑️  Deleted temporary video file: {video_file}")
            except Exception as e:
                print(f"⚠️  Could not delete {video_file}: {e}")
        
        # Final cleanup: ensure all temp audio files are deleted
        import glob
        import gc
        
        # Force garbage collection to free memory
        try:
            gc.collect()
        except:
            pass
        
        temp_patterns = [
            'reelTEMP_MPY_wvf_snd*.mp3',  # All temp audio files
            'temp-audio*.m4a',  # All temp audio files
            'reel_*_intro_*.mp3'  # All intro audio files
        ]
        for pattern in temp_patterns:
            for temp_file in glob.glob(pattern):
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        print(f"🗑️  Removed temporary file: {temp_file}")
                except Exception as e:
                    print(f"⚠️  Could not remove {temp_file}: {e}")

if __name__ == "__main__":
    import sys
    import traceback
    import atexit
    import logging
    from datetime import datetime
    
    # Setup logging to file for debugging
    # Use INFO level to avoid excessive DEBUG logs that consume memory
    log_file = f"reel_generator_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,  # Changed from DEBUG to INFO to reduce memory usage
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger(__name__)
    
    # Suppress verbose DEBUG logs from external libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    
    def emergency_cleanup():
        """Emergency cleanup function that runs on exit"""
        try:
            import gc
            gc.collect()
            logger.info("Emergency cleanup completed")
        except:
            pass
    
    # Register emergency cleanup
    atexit.register(emergency_cleanup)
    
    logger.info("=" * 60)
    logger.info("Starting reel generator...")
    logger.info("=" * 60)
    
    try:
        logger.info("Calling main() function...")
        logger.info("Main function entry point reached")
        main()
        logger.info("=" * 60)
        logger.info("Script completed successfully")
        logger.info("=" * 60)
    except KeyboardInterrupt:
        logger.warning("\n\n⚠️  Process interrupted by user (Ctrl+C)")
        print("\n\n⚠️  Process interrupted by user (Ctrl+C)")
        print("Cleaning up resources...")
        logger.info("Cleaning up resources...")
        sys.stdout.flush()
    except MemoryError as e:
        error_msg = "\n\n❌ FATAL ERROR: Out of memory!"
        logger.critical(error_msg, exc_info=True)
        print(error_msg)
        print("The video processing requires too much RAM.")
        print("Try:")
        print("  1. Close other applications to free memory")
        print("  2. Process shorter videos")
        print("  3. Reduce parallel processing (fewer workers)")
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
    except SystemExit:
        logger.info("SystemExit raised - normal exit")
        raise
    except Exception as e:
        error_msg = "\n\n❌ FATAL ERROR: Unexpected error occurred!"
        logger.critical(error_msg, exc_info=True)
        print(error_msg)
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")


def apply_video_edits(video_path, edits, output_path=None):
    """
    Apply edits to a video (text overlays with time-based positioning, filters).
    
    Args:
        video_path: Path to input video file
        edits: Dictionary with edit options:
            - overlays: List of overlay objects, each with:
                - text: Text/emoji content
                - start_time: Start time in seconds (float)
                - duration: Duration in seconds (float)
                - position: 'top', 'center', or 'bottom'
                - style: 'bold', 'outline', or 'shadow'
                - size: Font size (int)
                - color: Text color (hex string)
            - filter: Filter name ('none', 'vintage', 'blackwhite', 'warm', 'cool', 'vibrant')
        output_path: Path for output video (if None, creates temp file)
    
    Returns:
        Path to edited video file
    """
    try:
        from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        from pilmoji import Pilmoji
        
        if output_path is None:
            output_path = video_path.replace('.mp4', '_edited.mp4')
        
        print(f"🎬 Applying edits to video: {video_path}")
        
        # Load video
        video = VideoFileClip(video_path)
        clips_to_composite = [video]
        audio_clips = []  # Collect audio clips for engaging sounds
        
        # Apply overlays (time-based text/emoji overlays with custom x,y positioning)
        overlays = edits.get('overlays', [])
        if overlays:
            print(f"📝 Applying {len(overlays)} text overlay(s)")
            for overlay in overlays:
                text = overlay.get('text', '')
                if not text:  # Skip overlays without text
                    continue
                start_time = float(overlay.get('start_time', 0))
                duration = float(overlay.get('duration', 1.0))
                x_percent = float(overlay.get('x', 50))  # Percentage from left (0-100)
                y_percent = float(overlay.get('y', 80))   # Percentage from top (0-100)
                style = overlay.get('style', 'bold')
                size = overlay.get('size', 40)
                color = overlay.get('color', '#FFFFFF')
                
                # Ensure start_time and duration are within video bounds
                if start_time < 0:
                    start_time = 0
                if start_time + duration > video.duration:
                    duration = video.duration - start_time
                if duration <= 0:
                    print(f"⚠️  Skipping overlay: start_time {start_time} + duration {duration} exceeds video duration {video.duration}")
                    continue
                
                # Convert percentage to pixel position
                # x_percent: 0 = left, 50 = center, 100 = right
                # y_percent: 0 = top, 50 = center, 100 = bottom
                x_pos = (x_percent / 100.0) * video.w
                y_pos = (y_percent / 100.0) * video.h
                
                print(f"  - Overlay: '{text}' at {start_time}s for {duration}s at ({x_percent}%, {y_percent}%)")
                
                # Create text clip using PIL (no ImageMagick required)
                try:
                    # Convert hex color to RGB
                    color_rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) if color.startswith('#') else (255, 255, 255)
                    
                    # Try to load a font, fallback to default if not available
                    try:
                        # Try different font paths
                        font_paths = [
                            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
                            '/System/Library/Fonts/Helvetica.ttc',
                            'arial.ttf'
                        ]
                        font_obj = None
                        for font_path in font_paths:
                            try:
                                font_obj = ImageFont.truetype(font_path, size)
                                break
                            except:
                                continue
                        if font_obj is None:
                            font_obj = ImageFont.load_default()
                    except:
                        font_obj = ImageFont.load_default()
                    
                    # Calculate text size
                    temp_img = Image.new('RGBA', (1, 1))
                    temp_draw = ImageDraw.Draw(temp_img)
                    bbox = temp_draw.textbbox((0, 0), text, font=font_obj)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    
                    # Add padding for outline/shadow
                    padding = 10 if style in ['outline', 'shadow'] else 5
                    img_width = text_width + padding * 2
                    img_height = text_height + padding * 2
                    
                    # Create image with transparent background
                    img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
                    
                    text_x = padding
                    text_y = padding
                    
                    # Check if text contains emojis (Unicode emoji ranges)
                    import re
                    # Pattern to detect emojis including ✕ (U+2716), ✅ (U+2705), and 🎯 (U+1F3AF)
                    # The range \U00002700-\U000027BF covers dingbats including ✅ and ✕
                    # The range \U0001F300-\U0001F5FF covers symbols & pictographs including 🎯
                    emoji_pattern = re.compile(
                        "["
                        "\U0001F600-\U0001F64F"  # emoticons
                        "\U0001F300-\U0001F5FF"  # symbols & pictographs (includes 🎯 U+1F3AF)
                        "\U0001F680-\U0001F6FF"  # transport & map symbols
                        "\U0001F1E0-\U0001F1FF"  # flags
                        "\U00002700-\U000027BF"  # dingbats (includes ✅ U+2705 and ✕ U+2716)
                        "\U000024C2-\U0001F251"  # enclosed characters
                        "\U0001F900-\U0001F9FF"  # supplemental symbols
                        "\U0001FA00-\U0001FAFF"  # chess symbols
                        "\U00002600-\U000026FF"  # miscellaneous symbols
                        "]+",
                        flags=re.UNICODE
                    )
                    has_emoji = bool(emoji_pattern.search(text))
                    
                    # Use Pilmoji for emoji rendering, regular draw for text-only
                    if has_emoji:
                        # Use Pilmoji to render text with emojis properly
                        with Pilmoji(img) as pilmoji:
                            # For emoji text, we need to handle styles differently
                            if style == 'outline':
                                # Draw outline (black background) - simple offset approach
                                outline_color = (0, 0, 0, 255)
                                for adj in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                                    pilmoji.text((text_x + adj[0], text_y + adj[1]), text, font=font_obj, fill=outline_color)
                                # Draw main text
                                pilmoji.text((text_x, text_y), text, font=font_obj, fill=color_rgb + (255,))
                            elif style == 'shadow':
                                # Draw shadow
                                shadow_offset = 3
                                pilmoji.text((text_x + shadow_offset, text_y + shadow_offset), text, font=font_obj, fill=(0, 0, 0, 153))
                                # Draw main text
                                pilmoji.text((text_x, text_y), text, font=font_obj, fill=color_rgb + (255,))
                            else:  # bold or normal
                                pilmoji.text((text_x, text_y), text, font=font_obj, fill=color_rgb + (255,))
                    else:
                        # Regular text rendering (no emojis)
                        draw = ImageDraw.Draw(img)
                    if style == 'outline':
                        # Draw outline (black background)
                        outline_color = (0, 0, 0, 255)
                        for adj in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                            draw.text((text_x + adj[0], text_y + adj[1]), text, font=font_obj, fill=outline_color)
                        # Draw main text
                        draw.text((text_x, text_y), text, font=font_obj, fill=color_rgb + (255,))
                    elif style == 'shadow':
                        # Draw shadow
                        shadow_offset = 3
                        draw.text((text_x + shadow_offset, text_y + shadow_offset), text, font=font_obj, fill=(0, 0, 0, 153))
                        # Draw main text
                        draw.text((text_x, text_y), text, font=font_obj, fill=color_rgb + (255,))
                    else:  # bold or normal
                        draw.text((text_x, text_y), text, font=font_obj, fill=color_rgb + (255,))
                    
                    # Convert PIL image to numpy array
                    img_array = np.array(img)
                    
                    # Create ImageClip from numpy array
                    txt_clip = ImageClip(img_array).set_duration(duration).set_start(start_time)
                    
                    # Position the clip (adjust for padding)
                    txt_clip = txt_clip.set_position((x_pos - img_width/2, y_pos - img_height/2))
                    
                    clips_to_composite.append(txt_clip)
                    
                except Exception as e:
                    print(f"⚠️  Error creating text clip for '{text}': {e}")
                    import traceback
                    traceback.print_exc()
                    continue
        
        # Handle sound events (separate from text overlays)
        sound_events = edits.get('soundEvents', [])
        if sound_events:
            print(f"🔊 Applying {len(sound_events)} sound event(s)")
            try:
                from moviepy.editor import AudioFileClip, CompositeAudioClip
                
                for sound_event in sound_events:
                    sound_name = sound_event.get('sound')
                    sound_time = float(sound_event.get('time', 0))
                    
                    if not sound_name or sound_time < 0 or sound_time > video.duration:
                        continue
                    
                    # Map sound names
                    sound_file_map = {
                        'pop': 'pop',
                        'bell': 'bell',
                        'ding': 'ding',
                        'flash': 'flash',
                        'night': 'night',
                        'whoosh': 'whoosh',
                        'Wasted': 'Wasted',
                        'error': 'error'
                    }
                    
                    sound_file_name = sound_file_map.get(sound_name, sound_name)
                    
                    # Try to find sound file
                    possible_paths = [
                        f'frontend/public/sounds/{sound_file_name}.mp3',
                        f'frontend/public/sounds/{sound_file_name}.wav',
                        f'frontend/public/sounds/{sound_file_name}.m4a',
                        f'/opt/reel_generator/frontend/public/sounds/{sound_file_name}.mp3',
                        f'/opt/reel_generator/frontend/public/sounds/{sound_file_name}.wav',
                        f'/opt/reel_generator/frontend/public/sounds/{sound_file_name}.m4a',
                    ]
                    
                    sound_file_found = None
                    for path in possible_paths:
                        if os.path.exists(path):
                            sound_file_found = path
                            break
                    
                    if sound_file_found:
                        print(f"  🔊 Adding sound '{sound_name}' from {sound_file_found} at {sound_time}s")
                        audio_clip = AudioFileClip(sound_file_found).set_start(sound_time)
                        audio_clips.append(audio_clip)
                    else:
                        print(f"  ⚠️  Sound file not found for '{sound_name}' at {sound_time}s")
            except Exception as e:
                print(f"⚠️  Error processing sound events: {e}")
                import traceback
                traceback.print_exc()
        
        # Handle color hits
        color_hits = edits.get('colorHits', [])
        if color_hits:
            print(f"🎨 Applying {len(color_hits)} color hit(s)")
            for color_hit in color_hits:
                hit_time = float(color_hit.get('time', 0))
                hit_duration = float(color_hit.get('duration', 0.2))
                hit_color = color_hit.get('color', '#FFFFFF')
                hit_intensity = float(color_hit.get('intensity', 0.5))
                
                if hit_time < 0 or hit_time + hit_duration > video.duration:
                    continue
                
                # Convert hex color to RGB
                hex_color = hit_color.replace('#', '')
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                
                # Create color overlay with intensity
                color_img = Image.new('RGB', (video.w, video.h), (r, g, b))
                color_array = np.array(color_img)
                color_clip = ImageClip(color_array).set_duration(hit_duration).set_start(hit_time)
                color_clip = color_clip.set_opacity(hit_intensity * 0.3)  # Max 30% opacity
                clips_to_composite.append(color_clip)
                
                print(f"  🎨 Color hit: {hit_color} at {hit_time}s for {hit_duration}s (intensity: {hit_intensity})")
        
        
        # Composite all video clips
        if len(clips_to_composite) > 1:
            final_video = CompositeVideoClip(clips_to_composite, size=video.size)
        else:
            final_video = video
        
        # Add engaging sound audio clips if any
        if audio_clips and hasattr(video, 'audio') and video.audio is not None:
            try:
                from moviepy.editor import CompositeAudioClip
                base_audio = video.audio
                all_audio = [base_audio] + audio_clips
                final_audio = CompositeAudioClip(all_audio)
                final_video = final_video.set_audio(final_audio)
                print(f"🔊 Added {len(audio_clips)} engaging sound(s) to audio track")
            except Exception as e:
                print(f"⚠️  Error adding audio clips: {e}")
                # Continue without audio effects
        elif audio_clips:
            # Video has no audio, just add the sound clips
            try:
                from moviepy.editor import CompositeAudioClip
                final_audio = CompositeAudioClip(audio_clips)
                final_video = final_video.set_audio(final_audio)
                print(f"🔊 Added {len(audio_clips)} engaging sound(s) as audio track")
            except Exception as e:
                print(f"⚠️  Error creating audio track: {e}")
        
        # Write output
        print(f"💾 Writing edited video to: {output_path}")
        final_video.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            temp_audiofile='temp-audio.m4a',
            remove_temp=True,
            fps=video.fps,
            preset='medium',
            threads=4
        )
        
        # Cleanup temporary sound files
        for audio_clip in audio_clips:
            try:
                if hasattr(audio_clip, 'filename') and audio_clip.filename and os.path.exists(audio_clip.filename):
                    os.remove(audio_clip.filename)
            except:
                pass
        
        # Cleanup
        video.close()
        final_video.close()
        
        print(f"✅ Edited video saved to: {output_path}")
        return output_path
        
    except Exception as e:
        print(f"❌ Error applying video edits: {e}")
        import traceback
        traceback.print_exc()
        raise
        print(f"\nCheck log file for details: {log_file}")
        print("\nFull traceback:")
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Try to clean up resources even on crash
        try:
            import gc
            gc.collect()
            
            import glob
            temp_patterns = [
                'reelTEMP_MPY_wvf_snd*.mp3',
                'temp-audio*.m4a',
                'reel_*_intro_*.mp3'
            ]
            for pattern in temp_patterns:
                for temp_file in glob.glob(pattern):
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                            print(f"🗑️  Cleaned up: {temp_file}")
                    except:
                        pass
        except Exception as cleanup_err:
            print(f"⚠️  Cleanup error: {cleanup_err}")
            import traceback
            traceback.print_exc()
