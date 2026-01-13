import React, { useEffect, useRef } from 'react';
import './VideoBackground.css';

const VideoBackground = React.memo(() => {
  const videoRef = useRef(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    // Set playback rate for smooth slow-motion effect (0.75x speed)
    video.playbackRate = 0.75;

    // Handle video loaded and ready to play
    const handleLoadedMetadata = () => {
      // Start playing when video is ready
      video.play().catch(err => {
        console.error('Error playing video:', err);
      });
    };

    // Handle video end - reset to start for seamless loop
    const handleEnded = () => {
      video.currentTime = 0;
      video.play().catch(err => {
        console.error('Error replaying video:', err);
      });
    };

    // Initialize
    video.addEventListener('loadedmetadata', handleLoadedMetadata);
    video.addEventListener('ended', handleEnded);
    video.load();

    return () => {
      video.removeEventListener('loadedmetadata', handleLoadedMetadata);
      video.removeEventListener('ended', handleEnded);
      if (!video.paused) {
        video.pause();
      }
    };
  }, []);

  return (
    <video
      ref={videoRef}
      className="video-background"
      src="/background_recording.mp4"
      loop={true}
      muted
      playsInline
      preload="auto"
    />
  );
});

export default VideoBackground;

