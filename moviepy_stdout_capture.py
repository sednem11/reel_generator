"""
Capture and parse MoviePy stdout to track video creation progress.
Uses a thread-safe stdout wrapper.
"""
import re
import sys
from typing import Dict, Optional
from threading import Lock

class MoviePyProgressTracker:
    """Tracks MoviePy video creation progress by parsing output lines"""
    
    def __init__(self):
        self.lock = Lock()
        self.active_videos: Dict[str, Dict] = {}  # filename -> progress info
        self.completed_videos: set = set()
        
    def add_output_line(self, line: str):
        """Add an output line to be parsed"""
        if line and line.strip():
            self._parse_line(line)
    
    def _parse_line(self, line: str):
        """Parse a line of output for MoviePy progress"""
        with self.lock:
            # Pattern: ✅ Created short: filename.mp4
            # Pattern: ✅ Successfully created reel: filename.mp4
            # These are the definitive signals that videos are created
            created_patterns = [
                r'✅ Created short:\s*(.+\.mp4)',
                r'✅ Successfully created reel:\s*(.+\.mp4)'
            ]
            for pattern in created_patterns:
                match = re.search(pattern, line)
                if match:
                    filename = match.group(1)
                    # Mark this video as completed
                    if filename not in self.completed_videos:
                        self.completed_videos.add(filename)
                        # Also mark in active_videos if it exists
                        if filename in self.active_videos:
                            self.active_videos[filename]['status'] = 'completed'
                    return
            
            # Pattern: Moviepy - Writing video filename.mp4
            start_pattern = r'Moviepy - Writing video (.+\.mp4)'
            match = re.search(start_pattern, line)
            if match:
                filename = match.group(1)
                if filename not in self.active_videos:
                    self.active_videos[filename] = {
                        'current_frame': 0,
                        'total_frames': 0,
                        'fps': 0,
                        'status': 'starting'
                    }
                return
            
            # Pattern: t:   X%|...| current/total [elapsed/remaining, fps, now=None]
            # Example: t:   0%|...| 2/1544 [00:00<02:39,  9.64it/s, now=None]
            progress_pattern = r't:\s*(\d+)%\s*\|[^\|]+\|\s*(\d+)/(\d+)\s*\[[^\]]+,\s*([\d.]+)it/s'
            match = re.search(progress_pattern, line)
            if match:
                percent = int(match.group(1))
                current_frame = int(match.group(2))
                total_frames = int(match.group(3))
                fps = float(match.group(4))
                
                # Find matching video - prefer one that's starting, or closest frame count
                best_match = None
                best_diff = float('inf')
                
                for filename, video_info in self.active_videos.items():
                    if video_info['status'] == 'starting':
                        # Perfect match - video just started
                        best_match = filename
                        break
                    elif video_info['status'] == 'processing':
                        # Check if frame count is close (within 100 frames)
                        existing_frame = video_info.get('current_frame', 0)
                        diff = abs(current_frame - existing_frame)
                        if diff < best_diff and diff < 100:
                            best_diff = diff
                            best_match = filename
                
                if best_match:
                    video_info = self.active_videos[best_match]
                    video_info['current_frame'] = current_frame
                    video_info['total_frames'] = total_frames
                    video_info['fps'] = fps
                    video_info['status'] = 'processing'
                    video_info['percent'] = percent
                else:
                    # No match found - might be a new video, use most recent starting one
                    starting_videos = [f for f, v in self.active_videos.items() if v.get('status') == 'starting']
                    if starting_videos:
                        best_match = starting_videos[-1]  # Most recent
                        video_info = self.active_videos[best_match]
                        video_info['current_frame'] = current_frame
                        video_info['total_frames'] = total_frames
                        video_info['fps'] = fps
                        video_info['status'] = 'processing'
                        video_info['percent'] = percent
                return
            
            # Pattern: t: 100%|...| total/total [...] - video completed
            # Example: t: 100%|█████████████████████████▉| 1537/1544 [07:21<00:01,  4.63it/s, now=None]
            complete_pattern = r't:\s*100%\s*\|[^\|]+\|\s*(\d+)/(\d+)\s*\[[^\]]+\]'
            match = re.search(complete_pattern, line)
            if match:
                total_frames = int(match.group(2))
                # Find video that matches by total_frames
                for filename, video_info in list(self.active_videos.items()):
                    if video_info.get('status') == 'processing':
                        # Match by total_frames (exact or close)
                        if abs(video_info.get('total_frames', 0) - total_frames) < 10:
                            video_info['status'] = 'completed'
                            video_info['current_frame'] = total_frames
                            video_info['percent'] = 100
                            self.completed_videos.add(filename)
                            break
    
    def get_active_progress(self) -> Dict[str, Dict]:
        """Get progress for all active videos"""
        with self.lock:
            return {
                filename: info.copy()
                for filename, info in self.active_videos.items()
                if info.get('status') in ['starting', 'processing']
            }
    
    def get_completed_count(self) -> int:
        """Get count of completed videos"""
        with self.lock:
            return len(self.completed_videos)
    
    def reset(self):
        """Reset tracker state"""
        with self.lock:
            self.active_videos.clear()
            self.completed_videos.clear()

# Global tracker instance
_tracker_instance = None
_original_stdout = sys.stdout

class TeeStdout:
    """Tee stdout to both original stdout and progress tracker"""
    def __init__(self, original_stdout, tracker):
        self.original_stdout = original_stdout
        self.tracker = tracker
        self.buffer = ""
    
    def write(self, text):
        """Write to both original stdout and parse for MoviePy progress"""
        self.original_stdout.write(text)
        self.original_stdout.flush()
        
        # Buffer text until we have a complete line
        self.buffer += text
        if '\n' in self.buffer:
            lines = self.buffer.split('\n')
            self.buffer = lines[-1]  # Keep incomplete line in buffer
            for line in lines[:-1]:
                if line.strip():
                    self.tracker.add_output_line(line)
    
    def flush(self):
        """Flush original stdout"""
        self.original_stdout.flush()
        # Parse any remaining buffer
        if self.buffer.strip():
            self.tracker.add_output_line(self.buffer)
            self.buffer = ""

def get_tracker():
    """Get or create global tracker instance"""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = MoviePyProgressTracker()
    return _tracker_instance

def install_capture():
    """Install stdout capture for MoviePy progress"""
    global _original_stdout
    tracker = get_tracker()
    if not isinstance(sys.stdout, TeeStdout):
        _original_stdout = sys.stdout
        sys.stdout = TeeStdout(_original_stdout, tracker)
    return tracker

def uninstall_capture():
    """Restore original stdout"""
    global _original_stdout
    if isinstance(sys.stdout, TeeStdout):
        sys.stdout = _original_stdout

def get_progress() -> Dict[str, Dict]:
    """Get current video progress"""
    tracker = get_tracker()
    return tracker.get_active_progress()

def get_completed() -> int:
    """Get completed video count"""
    tracker = get_tracker()
    return tracker.get_completed_count()

def reset_parser():
    """Reset the parser state"""
    tracker = get_tracker()
    tracker.reset()

