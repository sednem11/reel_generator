import React, { useState, useRef, useEffect, useCallback } from 'react';
import './QuickEditModal.css';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const TEXT_STYLES = [
  { value: 'bold', label: 'Bold', font: 'bold' },
  { value: 'outline', label: 'Outline', font: 'normal' },
  { value: 'shadow', label: 'Shadow', font: 'normal' },
];

function QuickEditModal({ isOpen, onClose, videoUrl, videoType, videoIndex, jobId, onEditComplete }) {
  const [overlays, setOverlays] = useState([]); // Text overlays only
  const [soundEvents, setSoundEvents] = useState([]); // Sound events (pins on timeline)
  const [colorHits, setColorHits] = useState([]); // Color hit effects
  const [selectedOverlayId, setSelectedOverlayId] = useState(null);
  const [selectedSoundId, setSelectedSoundId] = useState(null);
  const [selectedColorHitId, setSelectedColorHitId] = useState(null);
  // Track raw input values for time inputs to allow free typing
  const [timeInputValues, setTimeInputValues] = useState({});
  const [processing, setProcessing] = useState(false);
  const [videoDuration, setVideoDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [timelineFrames, setTimelineFrames] = useState([]);
  const [isGeneratingFrames, setIsGeneratingFrames] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [dragType, setDragType] = useState(null); // 'move', 'resize-left', 'resize-right', 'position'
  const [dragStartX, setDragStartX] = useState(0);
  const [dragStartY, setDragStartY] = useState(0);
  const [dragStartTime, setDragStartTime] = useState(0);
  const [dragStartDuration, setDragStartDuration] = useState(0);
  const [dragStartPos, setDragStartPos] = useState({ x: 0, y: 0 });
  const [framesGenerated, setFramesGenerated] = useState(false);
  const [videoPausedOnDrag, setVideoPausedOnDrag] = useState(false);
  
  const videoRef = useRef(null);
  const timelineRef = useRef(null);
  const frameCanvasRef = useRef(null);
  const videoContainerRef = useRef(null);
  const greenFlashRef = useRef(null);

  // Generate thumbnail frames for timeline
  const generateTimelineFrames = useCallback(async () => {
    if (!videoRef.current || !frameCanvasRef.current || videoDuration === 0 || videoDuration < 0.1) {
      setTimelineFrames([]);
      return;
    }

    setIsGeneratingFrames(true);
    const canvas = frameCanvasRef.current;
    const video = videoRef.current;
    const frames = [];
    const frameCount = Math.min(30, Math.max(10, Math.floor(videoDuration))); // Between 10-30 frames
    const frameInterval = videoDuration / frameCount;
    const originalTime = video.currentTime;

    canvas.width = 80;
    canvas.height = 45;

    try {
      for (let i = 0; i < frameCount; i++) {
        const time = i * frameInterval;
        video.currentTime = time;
        
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => {
            video.removeEventListener('seeked', seeked);
            reject(new Error('Timeout'));
          }, 2000);

          const seeked = () => {
            clearTimeout(timeout);
            video.removeEventListener('seeked', seeked);
            try {
              const ctx = canvas.getContext('2d');
              ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
              const frameData = canvas.toDataURL('image/jpeg', 0.6);
              frames.push({ time, image: frameData });
              resolve();
            } catch (err) {
              reject(err);
            }
          };
          video.addEventListener('seeked', seeked);
        });
      }

      setTimelineFrames(frames);
    } catch (error) {
      console.warn('Error generating timeline frames:', error);
      // Continue without frames - timeline will still work
      setTimelineFrames([]);
    } finally {
      setIsGeneratingFrames(false);
      // Reset video to original time or start
      try {
        video.currentTime = originalTime || 0;
      } catch (e) {
        // Ignore errors
      }
    }
  }, [videoDuration]);

  // Generate frames only once when video metadata loads
  useEffect(() => {
    if (videoRef.current && !framesGenerated) {
      const video = videoRef.current; // Capture ref value at effect start
      const handleLoadedMetadata = () => {
        if (video && video.duration) {
          const duration = video.duration;
          setVideoDuration(duration);
          // Generate frames only once after a short delay to ensure video is ready
          if (!framesGenerated) {
            setTimeout(() => {
              generateTimelineFrames();
              setFramesGenerated(true);
            }, 500);
          }
        }
      };

      video.addEventListener('loadedmetadata', handleLoadedMetadata);
      
      // Also check if metadata is already loaded
      if (video.readyState >= 1 && video.duration) {
        handleLoadedMetadata();
      }
      
      return () => {
        if (video) {
          video.removeEventListener('loadedmetadata', handleLoadedMetadata);
        }
      };
    }
  }, [videoUrl, framesGenerated, generateTimelineFrames]);

  // Handle video time updates and play/pause states
  useEffect(() => {
    if (videoRef.current) {
      const handleTimeUpdate = () => {
        if (videoRef.current && !isDragging) {
          setCurrentTime(videoRef.current.currentTime || 0);
        }
      };

      const video = videoRef.current;
      video.addEventListener('timeupdate', handleTimeUpdate);
      
      return () => {
        if (video) {
          video.removeEventListener('timeupdate', handleTimeUpdate);
        }
      };
    }
  }, [isDragging]);

  // Convert pixel position to time
  const pixelToTime = useCallback((pixel) => {
    if (!timelineRef.current || videoDuration === 0) return 0;
    const width = timelineRef.current.offsetWidth;
    return Math.max(0, Math.min(videoDuration, (pixel / width) * videoDuration));
  }, [videoDuration]);

  // Handle timeline click to seek video
  const handleTimelineClick = (e) => {
    if (!timelineRef.current || videoDuration === 0) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const time = pixelToTime(x);
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      setCurrentTime(time);
    }
  };

  // Handle overlay drag start on timeline
  const handleOverlayMouseDown = (e, overlayId, type = 'move') => {
    e.stopPropagation();
    
    // Pause video and prevent it from playing during drag
    if (videoRef.current) {
      const wasPlaying = !videoRef.current.paused;
      if (wasPlaying) {
        videoRef.current.pause();
        setVideoPausedOnDrag(true);
      } else {
        setVideoPausedOnDrag(false);
      }
    }
    
    setSelectedOverlayId(overlayId);
    setIsDragging(true);
    setDragType(type);
    setDragStartX(e.clientX);
    
    const overlay = overlays.find(ov => ov.id === overlayId);
    if (overlay) {
      setDragStartTime(overlay.start_time);
      setDragStartDuration(overlay.duration);
    }
  };

  // Handle overlay position drag start on video
  const handleOverlayPositionMouseDown = (e, overlayId) => {
    e.stopPropagation();
    
    // Pause video and prevent it from playing during drag
    if (videoRef.current) {
      const wasPlaying = !videoRef.current.paused;
      if (wasPlaying) {
        videoRef.current.pause();
        setVideoPausedOnDrag(true);
      } else {
        setVideoPausedOnDrag(false);
      }
    }
    
    setSelectedOverlayId(overlayId);
    setIsDragging(true);
    setDragType('position');
    setDragStartX(e.clientX);
    setDragStartY(e.clientY);
    
    const overlay = overlays.find(ov => ov.id === overlayId);
    if (overlay && videoRef.current) {
      setDragStartPos({
        x: overlay.x || 50,
        y: overlay.y || 50
      });
    }
  };

  // Handle drag
  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e) => {
      // Handle text overlay drags
      if (selectedOverlayId && (dragType === 'position' || dragType === 'move' || dragType === 'resize-left' || dragType === 'resize-right')) {
      const overlay = overlays.find(ov => ov.id === selectedOverlayId);
      if (!overlay) return;

      if (dragType === 'position') {
        // Dragging overlay position on video
        if (!videoContainerRef.current || !videoRef.current) return;
        const videoRect = videoRef.current.getBoundingClientRect();
        const deltaX = e.clientX - dragStartX;
        const deltaY = e.clientY - dragStartY;
        
        // Convert pixel deltas to percentage based on video dimensions
        const deltaXPercent = (deltaX / videoRect.width) * 100;
        const deltaYPercent = (deltaY / videoRect.height) * 100;
        
        const newX = Math.max(0, Math.min(100, dragStartPos.x + deltaXPercent));
        const newY = Math.max(0, Math.min(100, dragStartPos.y + deltaYPercent));
        
        setOverlays(overlays.map(ov => 
          ov.id === selectedOverlayId 
            ? { ...ov, x: newX, y: newY }
            : ov
        ));
      } else if (dragType === 'move' || dragType === 'resize-left' || dragType === 'resize-right') {
          // Dragging text overlay on timeline
        if (!timelineRef.current || videoDuration === 0) return;
        
        // Get timeline bounding rect to convert clientX to relative position
        const rect = timelineRef.current.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const startX = dragStartX - rect.left;
        
        // Convert pixel positions to time
        const currentTime = pixelToTime(currentX);
        const startTimeFromPixel = pixelToTime(startX);
        const deltaTime = currentTime - startTimeFromPixel;
        
        let newStartTime = overlay.start_time;
        let newDuration = overlay.duration;

        if (dragType === 'move') {
          newStartTime = Math.max(0, Math.min(videoDuration - overlay.duration, dragStartTime + deltaTime));
        } else if (dragType === 'resize-left') {
          const newStart = Math.max(0, dragStartTime + deltaTime);
          const maxStart = dragStartTime + dragStartDuration - 0.1; // Min duration 0.1s
          newStartTime = Math.min(newStart, maxStart);
          newDuration = dragStartDuration - (newStartTime - dragStartTime);
        } else if (dragType === 'resize-right') {
          newDuration = Math.max(0.1, dragStartDuration + deltaTime);
          const maxDuration = videoDuration - dragStartTime;
          newDuration = Math.min(newDuration, maxDuration);
        }

        setOverlays(overlays.map(ov => 
          ov.id === selectedOverlayId 
            ? { ...ov, start_time: newStartTime, duration: newDuration }
            : ov
        ));
          return;
        }
      }
      
      // Handle sound pin drags
      if (selectedSoundId && dragType === 'move-sound') {
        // Dragging sound pin on timeline
        if (!timelineRef.current || videoDuration === 0) return;
        
        const rect = timelineRef.current.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const startX = dragStartX - rect.left;
        
        const currentTime = pixelToTime(currentX);
        const startTimeFromPixel = pixelToTime(startX);
        const deltaTime = currentTime - startTimeFromPixel;
        
        const newTime = Math.max(0, Math.min(videoDuration, dragStartTime + deltaTime));
        
        setSoundEvents(soundEvents.map(se => 
          se.id === selectedSoundId 
            ? { ...se, time: newTime }
            : se
        ));
        return;
      }
      
      // Handle color hit drags
      if (selectedColorHitId && (dragType === 'move-color' || dragType === 'resize-color-left' || dragType === 'resize-color-right')) {
        // Dragging color hit on timeline
        if (!timelineRef.current || videoDuration === 0) return;
        
        const rect = timelineRef.current.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const startX = dragStartX - rect.left;
        
        const currentTime = pixelToTime(currentX);
        const startTimeFromPixel = pixelToTime(startX);
        const deltaTime = currentTime - startTimeFromPixel;
        
        const colorHit = colorHits.find(ch => ch.id === selectedColorHitId);
        if (!colorHit) return;
        
        let newTime = colorHit.time;
        let newDuration = colorHit.duration;

        if (dragType === 'move-color') {
          newTime = Math.max(0, Math.min(videoDuration - colorHit.duration, dragStartTime + deltaTime));
        } else if (dragType === 'resize-color-left') {
          const newStart = Math.max(0, dragStartTime + deltaTime);
          const maxStart = dragStartTime + dragStartDuration - 0.1;
          newTime = Math.min(newStart, maxStart);
          newDuration = dragStartDuration - (newTime - dragStartTime);
        } else if (dragType === 'resize-color-right') {
          newDuration = Math.max(0.1, dragStartDuration + deltaTime);
          const maxDuration = videoDuration - dragStartTime;
          newDuration = Math.min(newDuration, maxDuration);
        }

        setColorHits(colorHits.map(ch => 
          ch.id === selectedColorHitId 
            ? { ...ch, time: newTime, duration: newDuration }
            : ch
        ));
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setDragType(null);
      
      // Resume video playback if it was playing before drag
      if (videoRef.current && videoPausedOnDrag) {
        // Don't auto-resume - let user control playback
        setVideoPausedOnDrag(false);
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragType, selectedOverlayId, selectedSoundId, selectedColorHitId, dragStartX, dragStartY, dragStartTime, dragStartDuration, dragStartPos, overlays, soundEvents, colorHits, videoDuration, pixelToTime, videoPausedOnDrag]);

  // Add new overlay (text overlay)
  const handleAddOverlay = () => {
    // Get current video time (pause if playing to get exact frame)
    let overlayTime = currentTime;
    if (videoRef.current && !videoRef.current.paused) {
      videoRef.current.pause();
      overlayTime = videoRef.current.currentTime;
    }
    
    const newOverlay = {
      id: Date.now(),
      text: 'New Overlay',
      start_time: overlayTime,
      duration: 1.5,
      x: 50, // Center horizontally (percentage)
      y: 80, // Near bottom (percentage)
      style: 'bold',
      size: 40,
      color: '#FFFFFF'
    };
    setOverlays([...overlays, newOverlay]);
    setSelectedOverlayId(newOverlay.id);
  };

  // Add sound event (pin on timeline)
  const handleAddSoundEvent = () => {
    // Get current video time (pause if playing to get exact frame)
    let soundTime = currentTime;
    if (videoRef.current && !videoRef.current.paused) {
      videoRef.current.pause();
      soundTime = videoRef.current.currentTime;
    }
    
    const newSoundEvent = {
      id: Date.now(),
      time: soundTime,
      sound: 'pop' // Default sound
    };
    setSoundEvents([...soundEvents, newSoundEvent]);
    setSelectedSoundId(newSoundEvent.id);
    setSelectedOverlayId(null);
    setSelectedColorHitId(null);
  };

  // Add color hit
  const handleAddColorHit = () => {
    // Get current video time (pause if playing to get exact frame)
    let hitTime = currentTime;
    if (videoRef.current && !videoRef.current.paused) {
      videoRef.current.pause();
      hitTime = videoRef.current.currentTime;
    }
    
    const newColorHit = {
      id: Date.now(),
      time: hitTime,
      duration: 0.2, // Default 200ms
      color: '#FFFFFF', // Default white
      intensity: 0.5 // 0-1, how strong the color effect is
    };
    setColorHits([...colorHits, newColorHit]);
    setSelectedColorHitId(newColorHit.id);
    setSelectedOverlayId(null);
    setSelectedSoundId(null);
  };

  // Update overlay properties
  const updateOverlay = (id, updates) => {
    setOverlays(overlays.map(ov => 
      ov.id === id ? { ...ov, ...updates } : ov
    ));
  };

  // Remove overlay
  const handleRemoveOverlay = (id) => {
    setOverlays(overlays.filter(ov => ov.id !== id));
    if (selectedOverlayId === id) {
      setSelectedOverlayId(null);
    }
  };

  // Remove sound event
  const handleRemoveSoundEvent = (id) => {
    setSoundEvents(soundEvents.filter(se => se.id !== id));
    if (selectedSoundId === id) {
      setSelectedSoundId(null);
    }
  };

  // Remove color hit
  const handleRemoveColorHit = (id) => {
    setColorHits(colorHits.filter(ch => ch.id !== id));
    if (selectedColorHitId === id) {
      setSelectedColorHitId(null);
    }
  };

  // Apply edits
  const handleApply = async () => {
    if (overlays.length === 0 && soundEvents.length === 0 && colorHits.length === 0) {
      alert('Please add at least one overlay, sound, or color hit');
      return;
    }

    setProcessing(true);
    try {
      const editData = {
        overlays: overlays.map(ov => ({
          text: ov.text,
          start_time: parseFloat(ov.start_time),
          duration: parseFloat(ov.duration),
          x: parseFloat(ov.x || 50),
          y: parseFloat(ov.y || 80),
          style: ov.style,
          size: parseInt(ov.size),
          color: ov.color
        })),
        soundEvents: soundEvents.map(se => ({
          time: parseFloat(se.time),
          sound: se.sound
        })),
        colorHits: colorHits.map(ch => ({
          time: parseFloat(ch.time),
          duration: parseFloat(ch.duration),
          color: ch.color,
          intensity: parseFloat(ch.intensity)
        }))
      };

      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE_URL}/api/job/${jobId}/edit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          video_type: videoType,
          video_index: videoIndex,
          edits: editData
        })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Failed to apply edits' }));
        throw new Error(errorData.detail || 'Failed to apply edits');
      }

      const result = await response.json();
      
      if (onEditComplete) {
        onEditComplete(result);
      }
      
      onClose();
    } catch (error) {
      console.error('Error applying edits:', error);
      alert(`Failed to apply edits: ${error.message}`);
    } finally {
      setProcessing(false);
    }
  };


  // Play stinger sounds from audio files only
  const playStingerSound = useCallback((soundName) => {
    const audioExts = ['.mp3', '.wav', '.m4a'];
    
    // Function to try playing with a specific extension index
    const tryExtension = (index) => {
      if (index >= audioExts.length) {
        console.warn(`⚠ Could not play sound: ${soundName} (tried all extensions)`);
        return;
      }
      
      const ext = audioExts[index];
      const audioPath = `/sounds/${soundName}${ext}`;
      const audio = new Audio(audioPath);
      audio.volume = 0.7;
      
      // Set up error handler
      audio.addEventListener('error', (e) => {
        console.log(`✗ Failed to load ${audioPath}, trying next extension...`);
        // Try next extension
        tryExtension(index + 1);
      }, { once: true });
      
      // Try to play immediately
      const playPromise = audio.play();
      if (playPromise !== undefined) {
        playPromise
        .then(() => {
            console.log(`✓ Successfully playing sound: ${audioPath}`);
            // Success! Don't try next extension
            return;
          })
          .catch((err) => {
            console.log(`✗ Failed to play ${audioPath}: ${err.message || 'Unknown error'}`);
            // Try next extension if this one failed
            tryExtension(index + 1);
          });
      } else {
        // If play() returned undefined, try next extension
        tryExtension(index + 1);
      }
    };
    
    // Start trying from first extension
    tryExtension(0);
  }, []);
  
  
  // Sound functions - use audio files only
  const generatePopSound = useCallback(() => playStingerSound('pop'), [playStingerSound]);
  const generateBellSound = useCallback(() => playStingerSound('bell'), [playStingerSound]);
  const generateDingSound = useCallback(() => playStingerSound('ding'), [playStingerSound]);
  const generateFlashSound = useCallback(() => playStingerSound('flash'), [playStingerSound]);
  const generateNightSound = useCallback(() => playStingerSound('night'), [playStingerSound]);
  const generateWhooshSound = useCallback(() => playStingerSound('whoosh'), [playStingerSound]);
  const generateWastedSound = useCallback(() => playStingerSound('Wasted'), [playStingerSound]);
  const generateErrorSound = useCallback(() => playStingerSound('error'), [playStingerSound]);
  
  // Legacy - map to new sounds
  const generateClingSound = () => playStingerSound('bell');
  // Removed unused functions: generateDisappointmentSound, generateShockSound, generateConfirmationSound, generateTypingSound, generateChingSound, triggerGreenFlash

  // Play sound events when video reaches their time
  useEffect(() => {
    if (!videoRef.current || !isOpen) return;
    
    const video = videoRef.current;
    const handledSounds = new Set();
    
    const checkSoundEvents = () => {
      if (!video) return;
      
        const currentTime = video.currentTime;
        
      soundEvents.forEach(soundEvent => {
        const soundTime = parseFloat(soundEvent.time);
        if (isNaN(soundTime)) return;
        
        const soundKey = `${soundEvent.id}-${Math.floor(soundTime * 10)}`;
        
        // Check if we're at the sound time (with small tolerance)
        // Use a slightly larger tolerance to catch the sound
        if (currentTime >= soundTime - 0.05 && currentTime <= soundTime + 0.2 && 
            !handledSounds.has(soundKey)) {
          
          // Map sound names to actual sound file names
          const soundMap = {
            'pop': 'pop',
            'bell': 'bell',
            'ding': 'ding',
            'flash': 'flash',
            'night': 'night',
            'whoosh': 'whoosh',
            'Wasted': 'Wasted',
            'error': 'error'
          };
          
          const soundFileName = soundMap[soundEvent.sound];
          if (soundFileName) {
            console.log(`🎵 Triggering sound: ${soundFileName} at ${soundTime.toFixed(2)}s (current: ${currentTime.toFixed(2)}s, diff: ${(currentTime - soundTime).toFixed(2)}s)`);
            playStingerSound(soundFileName);
            handledSounds.add(soundKey);
          } else {
            console.warn(`⚠ Unknown sound name: ${soundEvent.sound}`);
          }
        }
        
        // Reset if we've passed the sound time by more than 0.5s
        if (currentTime > soundTime + 0.5) {
          handledSounds.delete(soundKey);
        }
      });
    };
    
    const handleTimeUpdate = () => {
      checkSoundEvents();
    };
    
    video.addEventListener('timeupdate', handleTimeUpdate);
    
    return () => {
      video.removeEventListener('timeupdate', handleTimeUpdate);
    };
  }, [soundEvents, isOpen, playStingerSound]);

  // Reset frame generation when modal opens with new video
  useEffect(() => {
    if (isOpen) {
      setFramesGenerated(false);
      setOverlays([]);
      setSelectedOverlayId(null);
      setCurrentTime(0);
    }
  }, [isOpen, videoUrl]);

  const selectedOverlay = overlays.find(ov => ov.id === selectedOverlayId);

  if (!isOpen) return null;

  return (
    <div className="quick-edit-modal-overlay" onClick={onClose}>
      <div className="quick-edit-modal" onClick={(e) => e.stopPropagation()}>
        <div className="quick-edit-header">
          <h2>Video Editor</h2>
          <button className="close-button" onClick={onClose}>✕</button>
        </div>

        <div className="quick-edit-content">
          {/* Video Preview */}
          <div className="video-preview-container">
            <div 
              ref={videoContainerRef}
              className="video-wrapper"
            >
              <video
                ref={videoRef}
                src={videoUrl}
                controls
                className="preview-video"
                preload="metadata"
              >
                Your browser does not support the video tag.
              </video>
              
              {/* Color hit overlay */}
              {colorHits.map((colorHit) => {
                const isVisible = currentTime >= colorHit.time && 
                                 currentTime < colorHit.time + colorHit.duration;
                
                if (!isVisible) return null;

                // Convert hex color to rgba with intensity
                const hex = colorHit.color.replace('#', '');
                const r = parseInt(hex.substr(0, 2), 16);
                const g = parseInt(hex.substr(2, 2), 16);
                const b = parseInt(hex.substr(4, 2), 16);
                const opacity = colorHit.intensity * 0.3; // Max 30% opacity

                return (
                  <div
                    key={colorHit.id}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                      backgroundColor: `rgba(${r}, ${g}, ${b}, ${opacity})`,
                  pointerEvents: 'none',
                      zIndex: 5,
                  borderRadius: '8px',
                }}
              />
                );
              })}

              {/* Real-time overlay preview - draggable */}
              {overlays.map((overlay) => {
                const isVisible = currentTime >= overlay.start_time && 
                                 currentTime < overlay.start_time + overlay.duration;
                
                if (!isVisible) return null;

                const isSelected = selectedOverlayId === overlay.id;
                const x = overlay.x || 50;
                const y = overlay.y || 80;

                // Only show text overlays
                if (!overlay.text) return null;

                return (
                  <div
                    key={overlay.id}
                    className={`text-preview-overlay ${isSelected ? 'selected' : ''} ${isDragging && dragType === 'position' && isSelected ? 'dragging' : ''}`}
                    style={{
                      position: 'absolute',
                      left: `${x}%`,
                      top: `${y}%`,
                      transform: 'translate(-50%, -50%)',
                      fontSize: `${overlay.size}px`,
                      color: overlay.color,
                      fontWeight: overlay.style === 'bold' ? 'bold' : 'normal',
                      textShadow: overlay.style === 'shadow' 
                        ? '2px 2px 4px rgba(0,0,0,0.8)' 
                        : overlay.style === 'outline' 
                          ? '-1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000' 
                          : 'none',
                      WebkitTextStroke: overlay.style === 'outline' ? '1px #000' : 'none',
                      cursor: isSelected ? 'move' : 'pointer',
                      zIndex: 10,
                      userSelect: 'none',
                    }}
                    onMouseDown={(e) => handleOverlayPositionMouseDown(e, overlay.id)}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedOverlayId(overlay.id);
                      setSelectedSoundId(null);
                      setSelectedColorHitId(null);
                    }}
                  >
                    {overlay.text}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Timeline */}
          <div className="timeline-container">
            <div 
              ref={timelineRef}
              className="timeline-track"
              onClick={handleTimelineClick}
            >
              {/* Timeline frames */}
              <div className="timeline-frames">
                {isGeneratingFrames ? (
                  <div className="timeline-loading">Generating frames...</div>
                ) : timelineFrames.length > 0 ? (
                  timelineFrames.map((frame, idx) => (
                    <div
                      key={idx}
                      className="timeline-frame"
                style={{
                        left: `${(frame.time / videoDuration) * 100}%`,
                        width: `${100 / timelineFrames.length}%`
                      }}
                    >
                      <img src={frame.image} alt={`Frame at ${frame.time}s`} />
                    </div>
                  ))
                ) : (
                  <div className="timeline-placeholder">Timeline ready</div>
                )}
              </div>

              {/* Time indicator */}
              <div 
                className="timeline-indicator"
                style={{ left: `${(currentTime / videoDuration) * 100}%` }}
              />

              {/* Overlay segments */}
              {overlays.map((overlay) => {
                const left = (overlay.start_time / videoDuration) * 100;
                const width = (overlay.duration / videoDuration) * 100;
                const isSelected = selectedOverlayId === overlay.id;

                return (
                  <div
                    key={overlay.id}
                    className={`timeline-overlay-segment ${isSelected ? 'selected' : ''}`}
                    style={{
                      left: `${left}%`,
                      width: `${width}%`
                    }}
                    onMouseDown={(e) => handleOverlayMouseDown(e, overlay.id, 'move')}
                  >
                    <div className="overlay-segment-label">{overlay.text}</div>
                    <div 
                      className="resize-handle resize-handle-left"
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        handleOverlayMouseDown(e, overlay.id, 'resize-left');
                      }}
                    />
                    <div 
                      className="resize-handle resize-handle-right"
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        handleOverlayMouseDown(e, overlay.id, 'resize-right');
                      }}
                    />
                  </div>
                );
              })}

              {/* Sound event pins */}
              {soundEvents.map((soundEvent) => {
                const left = (soundEvent.time / videoDuration) * 100;
                const isSelected = selectedSoundId === soundEvent.id;
                const soundEmojis = {
                  'pop': '💥',
                  'bell': '🔔',
                  'ding': '✨',
                  'flash': '📷',
                  'night': '🌙',
                  'whoosh': '💨',
                  'Wasted': '😞',
                  'error': '❌'
                };

                return (
                  <div
                    key={soundEvent.id}
                    className={`timeline-overlay-segment ${isSelected ? 'selected' : ''}`}
                    style={{
                      left: `${left}%`,
                      width: '40px',
                      transform: 'translateX(-50%)',
                      top: '80px',
                      height: '40px',
                      cursor: 'default',
                      zIndex: 20
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedSoundId(soundEvent.id);
                      setSelectedOverlayId(null);
                      setSelectedColorHitId(null);
                    }}
                    title={`${soundEvent.sound} at ${soundEvent.time.toFixed(2)}s`}
                  >
                    {/* Sound event emoji */}
                    <div className="overlay-segment-label" style={{ pointerEvents: 'none' }}>
                      {soundEmojis[soundEvent.sound] || '🔊'}
            </div>
                    
                    {/* Center drag pin - extends outside the box */}
                    <div
                      style={{
                        position: 'absolute',
                        left: '50%',
                        bottom: '-20px',
                        transform: 'translateX(-50%)',
                        width: '6px',
                        height: '20px',
                        backgroundColor: isSelected ? '#fff' : 'rgba(255, 255, 255, 0.9)',
                        cursor: 'grab',
                        zIndex: 21,
                        borderRadius: '3px',
                        transition: 'all 0.2s ease'
                      }}
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        if (videoRef.current) {
                          const wasPlaying = !videoRef.current.paused;
                          if (wasPlaying) videoRef.current.pause();
                        }
                        setSelectedSoundId(soundEvent.id);
                        setSelectedOverlayId(null);
                        setSelectedColorHitId(null);
                        setIsDragging(true);
                        setDragType('move-sound');
                        setDragStartX(e.clientX);
                        setDragStartTime(soundEvent.time);
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = '#fff';
                        e.currentTarget.style.width = '8px';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = isSelected ? '#fff' : 'rgba(255, 255, 255, 0.9)';
                        e.currentTarget.style.width = '6px';
                      }}
                      title="Drag to move"
                    />
                  </div>
                );
              })}

              {/* Color hit segments */}
              {colorHits.map((colorHit) => {
                const left = (colorHit.time / videoDuration) * 100;
                const width = (colorHit.duration / videoDuration) * 100;
                const isSelected = selectedColorHitId === colorHit.id;

                // Convert hex to rgba for better opacity control
                const hexColor = colorHit.color.replace('#', '');
                const r = parseInt(hexColor.substr(0, 2), 16);
                const g = parseInt(hexColor.substr(2, 2), 16);
                const b = parseInt(hexColor.substr(4, 2), 16);
                
                return (
                  <div
                    key={colorHit.id}
                    className={isSelected ? 'selected' : ''}
                    style={{
                      left: `${left}%`,
                      width: `${width}%`,
                      position: 'absolute',
                      top: '80px',
                      height: '40px',
                      backgroundColor: `rgba(${r}, ${g}, ${b}, 0.8)`,
                      border: isSelected ? '2px solid #fff' : `2px solid rgba(${r}, ${g}, ${b}, 0.6)`,
                      borderRadius: '4px',
                      cursor: 'default',
                      zIndex: 15,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      minWidth: '40px',
                      transition: 'all 0.1s ease',
                      boxShadow: isSelected ? `0 0 12px rgba(${r}, ${g}, ${b}, 0.8)` : 'none'
                    }}
                    onMouseEnter={(e) => {
                      if (!isSelected) {
                        e.currentTarget.style.backgroundColor = `rgba(${r}, ${g}, ${b}, 1)`;
                        e.currentTarget.style.borderColor = `rgba(${r}, ${g}, ${b}, 0.8)`;
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!isSelected) {
                        e.currentTarget.style.backgroundColor = `rgba(${r}, ${g}, ${b}, 0.8)`;
                        e.currentTarget.style.borderColor = `rgba(${r}, ${g}, ${b}, 0.6)`;
                      }
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedColorHitId(colorHit.id);
                      setSelectedOverlayId(null);
                      setSelectedSoundId(null);
                    }}
                    title={`Color hit: ${colorHit.color} at ${colorHit.time.toFixed(2)}s`}
                  >
                    {/* Left resize handle */}
                    <div 
                      className="resize-handle resize-handle-left"
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        if (videoRef.current) {
                          const wasPlaying = !videoRef.current.paused;
                          if (wasPlaying) videoRef.current.pause();
                        }
                        setSelectedColorHitId(colorHit.id);
                        setIsDragging(true);
                        setDragType('resize-color-left');
                        setDragStartX(e.clientX);
                        setDragStartTime(colorHit.time);
                        setDragStartDuration(colorHit.duration);
                      }}
                      title="Drag to resize start"
                    />
                    
                    {/* Center drag pin - extends outside the box */}
                    <div
                      style={{
                        position: 'absolute',
                        left: '50%',
                        bottom: '-25px',
                        transform: 'translateX(-50%)',
                        width: '8px',
                        height: '25px',
                        backgroundColor: isSelected ? '#fff' : 'rgba(255, 255, 255, 0.9)',
                        cursor: 'grab',
                        zIndex: 25,
                        borderRadius: '4px',
                        transition: 'all 0.2s ease',
                        pointerEvents: 'auto'
                      }}
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        if (videoRef.current) {
                          const wasPlaying = !videoRef.current.paused;
                          if (wasPlaying) {
                            videoRef.current.pause();
                            setVideoPausedOnDrag(true);
                          } else {
                            setVideoPausedOnDrag(false);
                          }
                        }
                        setSelectedColorHitId(colorHit.id);
                        setSelectedOverlayId(null);
                        setSelectedSoundId(null);
                        setIsDragging(true);
                        setDragType('move-color');
                        setDragStartX(e.clientX);
                        setDragStartTime(colorHit.time);
                        setDragStartDuration(colorHit.duration);
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = '#fff';
                        e.currentTarget.style.width = '10px';
                        e.currentTarget.style.cursor = 'grabbing';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = isSelected ? '#fff' : 'rgba(255, 255, 255, 0.9)';
                        e.currentTarget.style.width = '8px';
                        e.currentTarget.style.cursor = 'grab';
                      }}
                      title="Drag to move"
                    />
                    
                    {/* Right resize handle */}
                    <div 
                      className="resize-handle resize-handle-right"
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        if (videoRef.current) {
                          const wasPlaying = !videoRef.current.paused;
                          if (wasPlaying) videoRef.current.pause();
                        }
                        setSelectedColorHitId(colorHit.id);
                        setIsDragging(true);
                        setDragType('resize-color-right');
                        setDragStartX(e.clientX);
                        setDragStartTime(colorHit.time);
                        setDragStartDuration(colorHit.duration);
                      }}
                      title="Drag to resize end"
                    />
                  </div>
                );
              })}
            </div>
          </div>

          {/* Overlay Properties Panel */}
          <div className="editor-panel">
            <div className="editor-section">
              <h3>Text Overlays</h3>
            <button
                className="add-overlay-button"
                onClick={handleAddOverlay}
                style={{ width: '100%', marginBottom: '1rem' }}
            >
                + Add Text Overlay
            </button>
              
              <div className="overlays-list">
                {overlays.map((overlay) => (
                  <div
                    key={overlay.id}
                    className={`overlay-item ${selectedOverlayId === overlay.id ? 'active' : ''}`}
                    onClick={() => {
                      setSelectedOverlayId(overlay.id);
                      setSelectedSoundId(null);
                      setSelectedColorHitId(null);
                    }}
                  >
                    <div className="overlay-info">
                      <div className="overlay-text" style={{ fontSize: '1.2rem', lineHeight: '1.4' }}>{overlay.text}</div>
                      <div className="overlay-time-inputs">
                        <div className="time-input-group">
                          <label>Start:</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={timeInputValues[`overlay-${overlay.id}-start`] !== undefined 
                              ? timeInputValues[`overlay-${overlay.id}-start`] 
                              : overlay.start_time.toFixed(2)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              const inputValue = e.target.value;
                              const inputKey = `overlay-${overlay.id}-start`;
                              
                              // Allow any input while typing (including empty, backspace, etc.)
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: inputValue
                              }));
                              
                              // Try to parse and update if valid
                              const numValue = parseFloat(inputValue);
                              if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                                const newTime = Math.max(0, Math.min(videoDuration, numValue));
                              const newDuration = Math.min(overlay.duration, videoDuration - newTime);
                              updateOverlay(overlay.id, { 
                                start_time: newTime,
                                duration: newDuration
                              });
                              }
                            }}
                            onBlur={(e) => {
                              const inputKey = `overlay-${overlay.id}-start`;
                              const inputValue = e.target.value;
                              const numValue = parseFloat(inputValue);
                              
                              // Validate and clamp on blur
                              if (inputValue === '' || isNaN(numValue)) {
                                // Revert to last valid value
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              } else {
                                // Clamp to valid range and update
                                const clampedTime = Math.max(0, Math.min(videoDuration, numValue));
                                updateOverlay(overlay.id, { start_time: clampedTime });
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              }
                            }}
                            onFocus={(e) => {
                              // Store current value when focused
                              const inputKey = `overlay-${overlay.id}-start`;
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: overlay.start_time.toFixed(2)
                              }));
                            }}
                            className="time-input"
                          />
                        </div>
                        <div className="time-input-group">
                          <label>Dur:</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={timeInputValues[`overlay-${overlay.id}-duration`] !== undefined 
                              ? timeInputValues[`overlay-${overlay.id}-duration`] 
                              : overlay.duration.toFixed(2)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              const inputValue = e.target.value;
                              const inputKey = `overlay-${overlay.id}-duration`;
                              
                              // Allow any input while typing
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: inputValue
                              }));
                              
                              // Try to parse and update if valid
                              const numValue = parseFloat(inputValue);
                              if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                              const maxDuration = videoDuration - overlay.start_time;
                                const newDuration = Math.max(0.01, Math.min(maxDuration, numValue));
                              updateOverlay(overlay.id, { duration: newDuration });
                              }
                            }}
                            onBlur={(e) => {
                              const inputKey = `overlay-${overlay.id}-duration`;
                              const inputValue = e.target.value;
                              const numValue = parseFloat(inputValue);
                              
                              // Validate and clamp on blur
                              if (inputValue === '' || isNaN(numValue)) {
                                // Revert to last valid value
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              } else {
                                // Clamp to valid range and update
                                const maxDuration = videoDuration - overlay.start_time;
                                const clampedDuration = Math.max(0.01, Math.min(maxDuration, numValue));
                                updateOverlay(overlay.id, { duration: clampedDuration });
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              }
                            }}
                            onFocus={(e) => {
                              // Store current value when focused
                              const inputKey = `overlay-${overlay.id}-duration`;
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: overlay.duration.toFixed(2)
                              }));
                            }}
                            className="time-input"
                          />
                        </div>
                      </div>
                    </div>
            <button
                      className="remove-overlay-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemoveOverlay(overlay.id);
                      }}
                    >
                      ❌
                    </button>
          </div>
                ))}
              </div>
            </div>

            {/* Sound Events List */}
            <div className="editor-section">
              <h3>Sound Events</h3>
              <button
                className="add-overlay-button"
                onClick={handleAddSoundEvent}
                style={{ width: '100%', marginBottom: '1rem' }}
              >
                + Add Sound
              </button>
              <div className="overlays-list">
                {soundEvents.map((soundEvent) => {
                  const soundEmojis = {
                    'pop': '💥',
                    'bell': '🔔',
                    'ding': '✨',
                    'flash': '📷',
                    'night': '🌙',
                    'whoosh': '💨',
                    'Wasted': '😞',
                    'error': '❌'
                  };
                  return (
                    <div
                      key={soundEvent.id}
                      className={`overlay-item ${selectedSoundId === soundEvent.id ? 'active' : ''}`}
                      onClick={() => {
                        setSelectedSoundId(soundEvent.id);
                        setSelectedOverlayId(null);
                        setSelectedColorHitId(null);
                      }}
                    >
                      <div className="overlay-info">
                        <div className="overlay-text">
                          {soundEmojis[soundEvent.sound] || '🔊'} {soundEvent.sound}
                        </div>
                        <div className="overlay-time-inputs">
                          <div className="time-input-group">
                            <label>Time:</label>
                            <input
                              type="text"
                              inputMode="decimal"
                              value={timeInputValues[`sound-${soundEvent.id}-time`] !== undefined 
                                ? timeInputValues[`sound-${soundEvent.id}-time`] 
                                : soundEvent.time.toFixed(2)}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => {
                                const inputValue = e.target.value;
                                const inputKey = `sound-${soundEvent.id}-time`;
                                
                                // Allow any input while typing
                                setTimeInputValues(prev => ({
                                  ...prev,
                                  [inputKey]: inputValue
                                }));
                                
                                // Try to parse and update if valid
                                const numValue = parseFloat(inputValue);
                                if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                                  const newTime = Math.max(0, Math.min(videoDuration, numValue));
                                  setSoundEvents(soundEvents.map(se => 
                                    se.id === soundEvent.id ? { ...se, time: newTime } : se
                                  ));
                                }
                              }}
                              onBlur={(e) => {
                                const inputKey = `sound-${soundEvent.id}-time`;
                                const inputValue = e.target.value;
                                const numValue = parseFloat(inputValue);
                                
                                // Validate and clamp on blur
                                if (inputValue === '' || isNaN(numValue)) {
                                  // Revert to last valid value
                                  setTimeInputValues(prev => {
                                    const newState = { ...prev };
                                    delete newState[inputKey];
                                    return newState;
                                  });
                                } else {
                                  // Clamp to valid range and update
                                  const clampedTime = Math.max(0, Math.min(videoDuration, numValue));
                                  setSoundEvents(soundEvents.map(se => 
                                    se.id === soundEvent.id ? { ...se, time: clampedTime } : se
                                  ));
                                  setTimeInputValues(prev => {
                                    const newState = { ...prev };
                                    delete newState[inputKey];
                                    return newState;
                                  });
                                }
                              }}
                              onFocus={(e) => {
                                // Store current value when focused
                                const inputKey = `sound-${soundEvent.id}-time`;
                                setTimeInputValues(prev => ({
                                  ...prev,
                                  [inputKey]: soundEvent.time.toFixed(2)
                                }));
                              }}
                              className="time-input"
                            />
                          </div>
                        </div>
                      </div>
                      <button
                        className="remove-overlay-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRemoveSoundEvent(soundEvent.id);
                        }}
                      >
                        ❌
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Color Hits List */}
            <div className="editor-section">
              <h3>Color Hits</h3>
              <button
                className="add-overlay-button"
                onClick={handleAddColorHit}
                style={{ width: '100%', marginBottom: '1rem' }}
              >
                + Add Color Hit
              </button>
              <div className="overlays-list">
                {colorHits.map((colorHit) => (
                  <div
                    key={colorHit.id}
                    className={`overlay-item ${selectedColorHitId === colorHit.id ? 'active' : ''}`}
                    onClick={() => {
                      setSelectedColorHitId(colorHit.id);
                      setSelectedOverlayId(null);
                      setSelectedSoundId(null);
                    }}
                  >
                    <div className="overlay-info">
                      <div className="overlay-text" style={{ color: colorHit.color }}>
                        🎨 Color Hit
                      </div>
                      <div className="overlay-time-inputs">
                        <div className="time-input-group">
                          <label>Time:</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={timeInputValues[`color-${colorHit.id}-time`] !== undefined 
                              ? timeInputValues[`color-${colorHit.id}-time`] 
                              : colorHit.time.toFixed(2)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              const inputValue = e.target.value;
                              const inputKey = `color-${colorHit.id}-time`;
                              
                              // Allow any input while typing
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: inputValue
                              }));
                              
                              // Try to parse and update if valid
                              const numValue = parseFloat(inputValue);
                              if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                                const newTime = Math.max(0, Math.min(videoDuration, numValue));
                                setColorHits(colorHits.map(ch => 
                                  ch.id === colorHit.id ? { ...ch, time: newTime } : ch
                                ));
                              }
                            }}
                            onBlur={(e) => {
                              const inputKey = `color-${colorHit.id}-time`;
                              const inputValue = e.target.value;
                              const numValue = parseFloat(inputValue);
                              
                              // Validate and clamp on blur
                              if (inputValue === '' || isNaN(numValue)) {
                                // Revert to last valid value
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              } else {
                                // Clamp to valid range and update
                                const clampedTime = Math.max(0, Math.min(videoDuration, numValue));
                                setColorHits(colorHits.map(ch => 
                                  ch.id === colorHit.id ? { ...ch, time: clampedTime } : ch
                                ));
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              }
                            }}
                            onFocus={(e) => {
                              // Store current value when focused
                              const inputKey = `color-${colorHit.id}-time`;
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: colorHit.time.toFixed(2)
                              }));
                            }}
                            className="time-input"
                          />
                        </div>
                        <div className="time-input-group">
                          <label>Dur:</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={timeInputValues[`color-${colorHit.id}-duration`] !== undefined 
                              ? timeInputValues[`color-${colorHit.id}-duration`] 
                              : colorHit.duration.toFixed(2)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              const inputValue = e.target.value;
                              const inputKey = `color-${colorHit.id}-duration`;
                              
                              // Allow any input while typing
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: inputValue
                              }));
                              
                              // Try to parse and update if valid
                              const numValue = parseFloat(inputValue);
                              if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                                const newDuration = Math.max(0.01, Math.min(2, numValue));
                                setColorHits(colorHits.map(ch => 
                                  ch.id === colorHit.id ? { ...ch, duration: newDuration } : ch
                                ));
                              }
                            }}
                            onBlur={(e) => {
                              const inputKey = `color-${colorHit.id}-duration`;
                              const inputValue = e.target.value;
                              const numValue = parseFloat(inputValue);
                              
                              // Validate and clamp on blur
                              if (inputValue === '' || isNaN(numValue)) {
                                // Revert to last valid value
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              } else {
                                // Clamp to valid range and update
                                const clampedDuration = Math.max(0.01, Math.min(2, numValue));
                                setColorHits(colorHits.map(ch => 
                                  ch.id === colorHit.id ? { ...ch, duration: clampedDuration } : ch
                                ));
                                setTimeInputValues(prev => {
                                  const newState = { ...prev };
                                  delete newState[inputKey];
                                  return newState;
                                });
                              }
                            }}
                            onFocus={(e) => {
                              // Store current value when focused
                              const inputKey = `color-${colorHit.id}-duration`;
                              setTimeInputValues(prev => ({
                                ...prev,
                                [inputKey]: colorHit.duration.toFixed(2)
                              }));
                            }}
                            className="time-input"
                          />
                        </div>
                      </div>
                    </div>
                    <button
                      className="remove-overlay-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemoveColorHit(colorHit.id);
                      }}
                    >
                      ❌
            </button>
          </div>
                ))}
              </div>
            </div>

            {/* Selected Overlay Properties */}
            {selectedOverlay && !selectedSoundId && !selectedColorHitId && (
              <div className="editor-section">
                <h3>Text Overlay Properties</h3>
                
                <div className="form-group">
                  <label>Text</label>
                  <input
                    type="text"
                    value={selectedOverlay.text || ''}
                    onChange={(e) => updateOverlay(selectedOverlayId, { text: e.target.value })}
                    maxLength={100}
                    placeholder="Enter text or emoji"
                  />
                  <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    {['😀', '😂', '🥰', '😎', '🔥', '💯', '⭐', '🎉', '🎊', '👍', '👎', '❤️', '💪', '🙌', '👏', '🎯', '✨', '💥', '🚀', '❌', '✅'].map(emoji => (
                      <button
                        key={emoji}
                        type="button"
                        onClick={() => updateOverlay(selectedOverlayId, { text: (selectedOverlay.text || '') + emoji })}
                        style={{
                          fontSize: '1.5rem',
                          padding: '0.25rem 0.5rem',
                          background: 'rgba(40, 40, 60, 0.8)',
                          border: '1px solid rgba(255, 255, 255, 0.1)',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          transition: 'all 0.2s ease'
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = 'rgba(102, 126, 234, 0.3)';
                          e.currentTarget.style.transform = 'scale(1.2)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'rgba(40, 40, 60, 0.8)';
                          e.currentTarget.style.transform = 'scale(1)';
                        }}
                        title={`Add ${emoji}`}
                      >
                        {emoji}
                      </button>
                    ))}
                  </div>
                </div>

                {selectedOverlay.text && (
                  <>
                <div className="form-group">
                  <label>Position (drag on video to move)</label>
                  <div className="position-inputs">
                    <div>
                      <label>X: {selectedOverlay.x?.toFixed(1) || 50}%</label>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={selectedOverlay.x || 50}
                        onChange={(e) => updateOverlay(selectedOverlayId, { x: parseFloat(e.target.value) })}
                      />
                    </div>
                    <div>
                      <label>Y: {selectedOverlay.y?.toFixed(1) || 80}%</label>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={selectedOverlay.y || 80}
                        onChange={(e) => updateOverlay(selectedOverlayId, { y: parseFloat(e.target.value) })}
                      />
                    </div>
                  </div>
                </div>

                <div className="form-group">
                  <label>Style</label>
                  <div className="style-buttons">
                    {TEXT_STYLES.map(style => (
                      <button
                        key={style.value}
                        type="button"
                        className={`style-button ${selectedOverlay.style === style.value ? 'active' : ''}`}
                        onClick={() => updateOverlay(selectedOverlayId, { style: style.value })}
                      >
                        {style.label}
                      </button>
                    ))}
                  </div>
                </div>
                  </>
                )}

                {selectedOverlay.text && (
                  <>
                <div className="form-group">
                  <label>Size: {selectedOverlay.size}px</label>
                  <input
                    type="range"
                    min="1"
                    max="100"
                    value={selectedOverlay.size}
                    onChange={(e) => updateOverlay(selectedOverlayId, { size: parseInt(e.target.value) })}
                  />
                </div>

                <div className="form-group">
                  <label>Color</label>
                  <div className="color-picker">
                    {['#FFFFFF', '#FF0000', '#00FF00', '#0000FF', '#FFFF00', '#FF00FF', '#00FFFF', '#FFA500'].map(color => (
                      <button
                        key={color}
                        type="button"
                        className={`color-button ${selectedOverlay.color === color ? 'active' : ''}`}
                        style={{ backgroundColor: color }}
                        onClick={() => updateOverlay(selectedOverlayId, { color })}
                      />
                    ))}
                    <input
                      type="color"
                      value={selectedOverlay.color}
                      onChange={(e) => updateOverlay(selectedOverlayId, { color: e.target.value })}
                      className="color-input"
                    />
                  </div>
                </div>
                  </>
                )}

              </div>
            )}

            {/* Selected Sound Event Properties */}
            {selectedSoundId && !selectedOverlayId && !selectedColorHitId && (() => {
              const selectedSound = soundEvents.find(se => se.id === selectedSoundId);
              if (!selectedSound) return null;
              return (
                <div className="editor-section">
                  <h3>Sound Event Properties</h3>
                <div className="form-group">
                    <label>Time</label>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={timeInputValues[`selected-sound-${selectedSoundId}-time`] !== undefined 
                        ? timeInputValues[`selected-sound-${selectedSoundId}-time`] 
                        : selectedSound.time.toFixed(2)}
                      onChange={(e) => {
                        const inputValue = e.target.value;
                        const inputKey = `selected-sound-${selectedSoundId}-time`;
                        
                        // Allow any input while typing
                        setTimeInputValues(prev => ({
                          ...prev,
                          [inputKey]: inputValue
                        }));
                        
                        // Try to parse and update if valid
                        const numValue = parseFloat(inputValue);
                        if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                          const newTime = Math.max(0, Math.min(videoDuration, numValue));
                          setSoundEvents(soundEvents.map(se => 
                            se.id === selectedSoundId ? { ...se, time: newTime } : se
                          ));
                        }
                      }}
                      onBlur={(e) => {
                        const inputKey = `selected-sound-${selectedSoundId}-time`;
                        const inputValue = e.target.value;
                        const numValue = parseFloat(inputValue);
                        
                        // Validate and clamp on blur
                        if (inputValue === '' || isNaN(numValue)) {
                          // Revert to last valid value
                          setTimeInputValues(prev => {
                            const newState = { ...prev };
                            delete newState[inputKey];
                            return newState;
                          });
                        } else {
                          // Clamp to valid range and update
                          const clampedTime = Math.max(0, Math.min(videoDuration, numValue));
                          setSoundEvents(soundEvents.map(se => 
                            se.id === selectedSoundId ? { ...se, time: clampedTime } : se
                          ));
                          setTimeInputValues(prev => {
                            const newState = { ...prev };
                            delete newState[inputKey];
                            return newState;
                          });
                        }
                      }}
                      onFocus={(e) => {
                        // Store current value when focused
                        const inputKey = `selected-sound-${selectedSoundId}-time`;
                        setTimeInputValues(prev => ({
                          ...prev,
                          [inputKey]: selectedSound.time.toFixed(2)
                        }));
                      }}
                    />
                  </div>
                  <div className="form-group">
                    <label>Sound</label>
                    <div className="engaging-sounds">
                      {['pop', 'bell', 'ding', 'flash', 'night', 'whoosh', 'Wasted', 'error'].map(sound => {
                        const soundEmojis = {
                          'pop': '💥',
                          'bell': '🔔',
                          'ding': '✨',
                          'flash': '📷',
                          'night': '🌙',
                          'whoosh': '💨',
                          'Wasted': '😞',
                          'error': '❌'
                        };
                        return (
                    <button
                            key={sound}
                      type="button"
                            className={`sound-button ${selectedSound.sound === sound ? 'active' : ''}`}
                      onClick={() => {
                              setSoundEvents(soundEvents.map(se => 
                                se.id === selectedSoundId ? { ...se, sound } : se
                              ));
                              const soundFunc = {
                                'pop': generatePopSound,
                                'bell': generateBellSound,
                                'ding': generateDingSound,
                                'flash': generateFlashSound,
                                'night': generateNightSound,
                                'whoosh': generateWhooshSound,
                                'Wasted': generateWastedSound,
                                'error': generateErrorSound
                              }[sound];
                              if (soundFunc) soundFunc();
                            }}
                            title={sound}
                          >
                            {soundEmojis[sound] || '🔊'}
                    </button>
                        );
                      })}
                    </div>
                  </div>
                    <button
                    className="remove-overlay-btn"
                    onClick={() => handleRemoveSoundEvent(selectedSoundId)}
                    style={{ width: '100%', marginTop: '1rem' }}
                  >
                    ❌ Remove Sound Event
                    </button>
                </div>
              );
            })()}

            {/* Selected Color Hit Properties */}
            {selectedColorHitId && !selectedOverlayId && !selectedSoundId && (() => {
              const selectedColorHit = colorHits.find(ch => ch.id === selectedColorHitId);
              if (!selectedColorHit) return null;
              return (
                <div className="editor-section">
                  <h3>Color Hit Properties</h3>
                  <div className="form-group">
                    <label>Time</label>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={timeInputValues[`selected-color-${selectedColorHitId}-time`] !== undefined 
                        ? timeInputValues[`selected-color-${selectedColorHitId}-time`] 
                        : selectedColorHit.time.toFixed(2)}
                      onChange={(e) => {
                        const inputValue = e.target.value;
                        const inputKey = `selected-color-${selectedColorHitId}-time`;
                        
                        // Allow any input while typing
                        setTimeInputValues(prev => ({
                          ...prev,
                          [inputKey]: inputValue
                        }));
                        
                        // Try to parse and update if valid
                        const numValue = parseFloat(inputValue);
                        if (!isNaN(numValue) && inputValue !== '' && inputValue !== '-') {
                          const newTime = Math.max(0, Math.min(videoDuration, numValue));
                          setColorHits(colorHits.map(ch => 
                            ch.id === selectedColorHitId ? { ...ch, time: newTime } : ch
                          ));
                        }
                      }}
                      onBlur={(e) => {
                        const inputKey = `selected-color-${selectedColorHitId}-time`;
                        const inputValue = e.target.value;
                        const numValue = parseFloat(inputValue);
                        
                        // Validate and clamp on blur
                        if (inputValue === '' || isNaN(numValue)) {
                          // Revert to last valid value
                          setTimeInputValues(prev => {
                            const newState = { ...prev };
                            delete newState[inputKey];
                            return newState;
                          });
                        } else {
                          // Clamp to valid range and update
                          const clampedTime = Math.max(0, Math.min(videoDuration, numValue));
                          setColorHits(colorHits.map(ch => 
                            ch.id === selectedColorHitId ? { ...ch, time: clampedTime } : ch
                          ));
                          setTimeInputValues(prev => {
                            const newState = { ...prev };
                            delete newState[inputKey];
                            return newState;
                          });
                        }
                      }}
                      onFocus={(e) => {
                        // Store current value when focused
                        const inputKey = `selected-color-${selectedColorHitId}-time`;
                        setTimeInputValues(prev => ({
                          ...prev,
                          [inputKey]: selectedColorHit.time.toFixed(2)
                        }));
                      }}
                    />
                  </div>
                  <div className="form-group">
                    <label>Duration: {selectedColorHit.duration.toFixed(2)}s</label>
                    <input
                      type="range"
                      min="0.1"
                      max="2"
                      step="0.1"
                      value={selectedColorHit.duration}
                      onChange={(e) => {
                        setColorHits(colorHits.map(ch => 
                          ch.id === selectedColorHitId ? { ...ch, duration: parseFloat(e.target.value) } : ch
                        ));
                      }}
                    />
                </div>
                  <div className="form-group">
                    <label>Color</label>
                    <input
                      type="color"
                      value={selectedColorHit.color}
                      onChange={(e) => {
                        setColorHits(colorHits.map(ch => 
                          ch.id === selectedColorHitId ? { ...ch, color: e.target.value } : ch
                        ));
                      }}
                      className="color-input"
                      style={{ width: '100%', height: '50px' }}
                    />
              </div>
                  <div className="form-group">
                    <label>Intensity: {Math.round(selectedColorHit.intensity * 100)}%</label>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={selectedColorHit.intensity}
                      onChange={(e) => {
                        setColorHits(colorHits.map(ch => 
                          ch.id === selectedColorHitId ? { ...ch, intensity: parseFloat(e.target.value) } : ch
                        ));
                      }}
                    />
                  </div>
                  <button
                    className="remove-overlay-btn"
                    onClick={() => handleRemoveColorHit(selectedColorHitId)}
                    style={{ width: '100%', marginTop: '1rem' }}
                  >
                    ❌ Remove Color Hit
                  </button>
                </div>
              );
            })()}
          </div>
        </div>

        <div className="quick-edit-footer">
          <button 
            type="button"
            className="cancel-button" 
            onClick={onClose} 
            disabled={processing}
          >
            Cancel
          </button>
          <button
            type="button"
            className="apply-button"
            onClick={handleApply}
            disabled={processing || (overlays.length === 0 && soundEvents.length === 0 && colorHits.length === 0)}
          >
            {processing ? 'Processing...' : 'Apply Edits'}
          </button>
        </div>
      </div>
      
      {/* Hidden canvas for frame extraction */}
      <canvas ref={frameCanvasRef} style={{ display: 'none' }} />
    </div>
  );
}

export default QuickEditModal;
