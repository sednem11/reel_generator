"""
Parser for MoviePy progress output to track video creation progress.
Parses lines like: t:   0%|...| 2/1544 [00:00<02:39,  9.64it/s, now=None]
"""
import re
import sys
from io import StringIO
from typing import Dict, Optional, Tuple

class MoviePyProgressParser:
    """Parses MoviePy stdout/stderr to extract video creation progress"""
    
    def __init__(self):
        self.active_videos: Dict[str, Dict] = {}  # filename -> {current_frame, total_frames, fps}
        self.completed_videos: set = set()
    
    def parse_line(self, line: str) -> Optional[Dict]:
        """
        Parse a MoviePy progress line.
        Returns dict with video info if found, None otherwise.
        
        Format: t:   X%|...| current/total [elapsed/remaining, fps, now=None]
        Example: t:   0%|...| 2/1544 [00:00<02:39,  9.64it/s, now=None]
        """
        # Pattern: t:   X%|...| current/total [elapsed/remaining, fps, now=None]
        # Also matches: Moviepy - Writing video filename.mp4
        pattern = r'Moviepy - Writing video (.+\.mp4)'
        match = re.search(pattern, line)
        if match:
            filename = match.group(1)
            if filename not in self.active_videos:
                self.active_videos[filename] = {
                    'current_frame': 0,
                    'total_frames': 0,
                    'fps': 0,
                    'status': 'starting'
                }
            return {'filename': filename, 'action': 'started'}
        
        # Pattern for progress: t:   X%|...| current/total [elapsed/remaining, fps, now=None]
        progress_pattern = r't:\s*(\d+)%\s*\|[^\|]+\|\s*(\d+)/(\d+)\s*\[[^\]]+,\s*([\d.]+)it/s'
        match = re.search(progress_pattern, line)
        if match:
            percent = int(match.group(1))
            current_frame = int(match.group(2))
            total_frames = int(match.group(3))
            fps = float(match.group(4))
            
            # Try to find which video this is for by checking active videos
            # We'll match by checking if current_frame is close to what we expect
            for filename, video_info in self.active_videos.items():
                if video_info['status'] == 'starting' or abs(current_frame - video_info.get('current_frame', 0)) < 100:
                    video_info['current_frame'] = current_frame
                    video_info['total_frames'] = total_frames
                    video_info['fps'] = fps
                    video_info['status'] = 'processing'
                    video_info['percent'] = percent
                    return {
                        'filename': filename,
                        'action': 'progress',
                        'current_frame': current_frame,
                        'total_frames': total_frames,
                        'fps': fps,
                        'percent': percent
                    }
        
        # Pattern for completion: t: 100%|...| total/total [elapsed/remaining, fps, now=None]
        complete_pattern = r't:\s*100%\s*\|[^\|]+\|\s*(\d+)/(\d+)\s*\[[^\]]+\]'
        match = re.search(complete_pattern, line)
        if match:
            total_frames = int(match.group(2))
            # Find the video that matches
            for filename, video_info in list(self.active_videos.items()):
                if video_info.get('total_frames') == total_frames or video_info.get('status') == 'processing':
                    video_info['status'] = 'completed'
                    video_info['current_frame'] = total_frames
                    video_info['percent'] = 100
                    self.completed_videos.add(filename)
                    return {
                        'filename': filename,
                        'action': 'completed',
                        'total_frames': total_frames
                    }
        
        return None
    
    def get_active_video_progress(self) -> Dict[str, Dict]:
        """Get current progress for all active videos"""
        return {
            filename: info.copy()
            for filename, info in self.active_videos.items()
            if info.get('status') in ['starting', 'processing']
        }
    
    def get_completed_count(self) -> int:
        """Get count of completed videos"""
        return len(self.completed_videos)
    
    def reset(self):
        """Reset parser state"""
        self.active_videos.clear()
        self.completed_videos.clear()

# Global parser instance
_global_parser = MoviePyProgressParser()

def parse_moviepy_output(line: str) -> Optional[Dict]:
    """Parse a single line of MoviePy output"""
    return _global_parser.parse_line(line)

def get_video_progress() -> Dict[str, Dict]:
    """Get current progress for all active videos"""
    return _global_parser.get_active_video_progress()

def get_completed_video_count() -> int:
    """Get count of completed videos"""
    return _global_parser.get_completed_count()

def reset_parser():
    """Reset the global parser"""
    _global_parser.reset()

