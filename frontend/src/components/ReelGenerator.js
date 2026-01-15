import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import './ReelGenerator.css';
import axios from 'axios';
import { useAuth } from '../contexts/AuthContext';
import DistributedVideoProcessor from '../utils/DistributedVideoProcessor';
import VideoStylingModal from './VideoStylingModal';
import QuickEditModal from './QuickEditModal';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function ReelGenerator({ interactiveClothRef }) {
  const { isAuthenticated, refreshUser } = useAuth();
  const navigate = useNavigate();
  const generateButtonRef = useRef(null);
  const [inputMethod, setInputMethod] = useState('url'); // 'url' or 'upload'
  const [url, setUrl] = useState('');
  const [uploadedFile, setUploadedFile] = useState(null);
  const [quality, setQuality] = useState('hd'); // Default to HD
  const [removeWatermark, setRemoveWatermark] = useState(false); // Remove watermark option
  const [videoTypes, setVideoTypes] = useState('both'); // 'short_only', 'medium_only', 'both'
  const [fontStyle, setFontStyle] = useState('bold'); // 'bold', 'funny', 'scary', 'movie', 'peptalk', 'elegant', 'modern'
  const [fontColor, setFontColor] = useState('white_red'); // 'white_red', 'white_green', 'white_blue', 'white_black', 'rainbow', 'white_yellow', 'white_purple'
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');
  const [jobId, setJobId] = useState(null);
  const [results, setResults] = useState(null);
  const [metadata, setMetadata] = useState({}); // Store metadata per video: { main_reel: {...}, medium_reels: [{...}, ...], short_reels: [{...}, ...] }
  const [editedVideos, setEditedVideos] = useState({}); // Track which videos are edited: { medium_reel: [true, false, ...], short_reel: [false, true, ...] }
  const [videoUrls, setVideoUrls] = useState({}); // Store blob URLs for videos
  const [editedVideoUrls, setEditedVideoUrls] = useState({}); // Store blob URLs for edited videos
  const [isStylingModalOpen, setIsStylingModalOpen] = useState(false);
  const [quickEditModal, setQuickEditModal] = useState({ isOpen: false, videoUrl: null, videoType: null, videoIndex: null });
  const pollIntervalRef = useRef(null);
  const resultsSectionRef = useRef(null);
  const distributedProcessor = useRef(new DistributedVideoProcessor(API_BASE_URL));
  const abortControllerRef = useRef(null); // For cancelling ongoing requests
  const isCancellingRef = useRef(false); // Track if cancellation is in progress

  // Check which videos have been edited
  const checkEditedVideos = useCallback(async (jobIdToCheck, results) => {
    const edited = {};
    const token = localStorage.getItem('token');
    
    try {
      // Check main reel
      if (results.main_reel) {
        const response = await fetch(`${API_BASE_URL}/api/job/${jobIdToCheck}/is_edited?video_type=main_reel`, {
          headers: { 
            'Authorization': `Bearer ${token}`,
            'ngrok-skip-browser-warning': 'true'
          }
        });
        if (response.ok) {
          const data = await response.json();
          edited.main_reel = data.is_edited;
        }
      }
      
      // Check medium reels
      if (results.medium_reels && results.medium_reels.length > 0) {
        edited.medium_reel = [];
        for (let i = 0; i < results.medium_reels.length; i++) {
          const response = await fetch(`${API_BASE_URL}/api/job/${jobIdToCheck}/is_edited?video_type=medium_reel&video_index=${i}`, {
            headers: { 
              'Authorization': `Bearer ${token}`,
              'ngrok-skip-browser-warning': 'true'
            }
          });
          if (response.ok) {
            const data = await response.json();
            edited.medium_reel[i] = data.is_edited;
          } else {
            edited.medium_reel[i] = false;
          }
        }
      }
      
      // Check short reels
      if (results.short_reels && results.short_reels.length > 0) {
        edited.short_reel = [];
        for (let i = 0; i < results.short_reels.length; i++) {
          const response = await fetch(`${API_BASE_URL}/api/job/${jobIdToCheck}/is_edited?video_type=short_reel&video_index=${i}`, {
            headers: { 
              'Authorization': `Bearer ${token}`,
              'ngrok-skip-browser-warning': 'true'
            }
          });
          if (response.ok) {
            const data = await response.json();
            edited.short_reel[i] = data.is_edited;
          } else {
            edited.short_reel[i] = false;
          }
        }
      }
      
      setEditedVideos(edited);
    } catch (error) {
      console.error('Error checking edited videos:', error);
    }
  }, []);

  // Load video URLs using authenticated fetch and create blob URLs
  const loadVideoUrls = useCallback(async (jobIdToLoad, results) => {
    const urls = {};
    const editedUrls = {};
    
    // Helper function to fetch video and create blob URL
    const createVideoBlobUrl = async (fileType, index = null, useOriginal = true) => {
      try {
        let downloadPath;
        if (index !== null) {
          downloadPath = `${API_BASE_URL}/api/job/${jobIdToLoad}/download/${fileType}_${index}${useOriginal ? '?original=true' : ''}`;
        } else {
          downloadPath = `${API_BASE_URL}/api/job/${jobIdToLoad}/download/${fileType}${useOriginal ? '?original=true' : ''}`;
        }
        
        const token = localStorage.getItem('token');
        const response = await fetch(downloadPath, {
          headers: {
            'Authorization': `Bearer ${token}`,
            'ngrok-skip-browser-warning': 'true'
          }
        });
        
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const blob = await response.blob();
        return URL.createObjectURL(blob);
      } catch (err) {
        console.error(`Error loading video ${fileType}${index !== null ? `_${index}` : ''}:`, err);
        return null;
      }
    };
    
    // Helper function to fetch edited video
    const createEditedVideoBlobUrl = async (fileType, index = null) => {
      try {
        let downloadPath;
        const editedFileType = `${fileType}_edited`;
        if (index !== null) {
          downloadPath = `${API_BASE_URL}/api/job/${jobIdToLoad}/download/${editedFileType}_${index}`;
        } else {
          downloadPath = `${API_BASE_URL}/api/job/${jobIdToLoad}/download/${editedFileType}`;
        }
        
        const token = localStorage.getItem('token');
        const response = await fetch(downloadPath, {
          headers: {
            'Authorization': `Bearer ${token}`,
            'ngrok-skip-browser-warning': 'true'
          }
        });
        
        if (!response.ok) {
          return null; // No edited version exists
        }
        
        const blob = await response.blob();
        return URL.createObjectURL(blob);
      } catch (err) {
        return null; // No edited version exists
      }
    };
    
    // Load main reel if exists
    if (results.main_reel) {
      urls.main_reel = await createVideoBlobUrl('main_reel', null, true);
      editedUrls.main_reel = await createEditedVideoBlobUrl('main_reel');
    }
    
    // Load medium reels
    if (results.medium_reels && results.medium_reels.length > 0) {
      urls.medium_reels = [];
      editedUrls.medium_reels = [];
      for (let i = 0; i < results.medium_reels.length; i++) {
        urls.medium_reels[i] = await createVideoBlobUrl('medium_reel', i, true);
        // Only try to load edited version if it exists (will return null if not)
        editedUrls.medium_reels[i] = await createEditedVideoBlobUrl('medium_reel', i);
      }
    }
    
    // Load short reels
    if (results.short_reels && results.short_reels.length > 0) {
      urls.short_reels = [];
      editedUrls.short_reels = [];
      for (let i = 0; i < results.short_reels.length; i++) {
        urls.short_reels[i] = await createVideoBlobUrl('short_reel', i, true);
        editedUrls.short_reels[i] = await createEditedVideoBlobUrl('short_reel', i);
      }
    }
    
    setVideoUrls(urls);
    setEditedVideoUrls(editedUrls);
    
    // After loading video URLs, check which ones are actually edited
    // This ensures we only show edited videos that correspond to their originals
    await checkEditedVideos(jobIdToLoad, results);
  }, [checkEditedVideos]);

  // Check for active jobs on component mount (after refresh)
  useEffect(() => {
    const checkActiveJobs = async () => {
      if (!isAuthenticated) {
        return;
      }

      try {
        const token = localStorage.getItem('token');
        const response = await axios.get(`${API_BASE_URL}/api/jobs`, {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        });

        const jobs = response.data.jobs || [];
        
        // Find the most recent active job (pending, processing, or completed)
        // We check for completed too in case it finished while the page was closed
        const activeJob = jobs.find(job => 
          job.status === 'pending' || job.status === 'processing' || job.status === 'completed'
        );

        if (activeJob) {
          // Fetch full job details to check video_downloaded status
          try {
            const jobResponse = await axios.get(`${API_BASE_URL}/api/job/${activeJob.job_id}`, {
              headers: {
                'Authorization': `Bearer ${token}`
              }
            });
            const job = jobResponse.data;
            
            // Don't resume if video download failed - credits will be refunded
            if (job.video_downloaded === 'failed' || (job.status === 'failed' && job.video_downloaded !== 'yes')) {
              console.log('Job failed during video download - not resuming. Credits will be refunded.');
              setStatus('Video download failed. Credits have been refunded. Please try again.');
              setLoading(false);
              return; // Don't resume this job
            }
            
            // Restore state for active job
            setJobId(activeJob.job_id);
            
            // If job is already completed, load results immediately
            if (activeJob.status === 'completed') {
              if (job.results) {
                setLoading(false);
                setStatus('Video processing completed successfully!');
                setResults(job.results);
                fetchAllMetadata(activeJob.job_id, job.results);
                loadVideoUrls(activeJob.job_id, job.results);
              }
            } else {
              // Job is pending or processing - restore loading state
              // Only resume if video was successfully downloaded
              if (job.video_downloaded === 'yes' || job.video_downloaded === null) {
                setLoading(true);
                setStatus('Resuming video processing...');
                
                // Set progress if available
                if (activeJob.progress) {
                  if (activeJob.progress.percentage) {
                    setProgress(`${activeJob.progress.message || 'Processing...'} (${Math.round(activeJob.progress.percentage)}%)`);
                  } else {
                    setProgress(activeJob.progress.message || 'Processing...');
                  }
                } else {
                  setProgress('Processing...');
                }
              } else {
                // Video not downloaded yet or failed - don't resume
                console.log('Video not downloaded yet - waiting for download to complete');
                setStatus('Waiting for video download...');
                setLoading(true);
              }
            }
          } catch (err) {
            console.error('Error fetching job details:', err);
          }
        }
      } catch (err) {
        console.error('Error checking for active jobs:', err);
      }
    };

    checkActiveJobs();
  }, [isAuthenticated, loadVideoUrls]);

  // Poll for job status
  useEffect(() => {
    if (jobId && loading) {
      pollIntervalRef.current = setInterval(async () => {
        // Check if cancelled
        if (isCancellingRef.current) {
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          return;
        }
        
        try {
          const controller = new AbortController();
          abortControllerRef.current = controller;
          
          const response = await axios.get(`${API_BASE_URL}/api/job/${jobId}`, {
            signal: controller.signal
          });
          const job = response.data;

          // Update progress
          if (job.progress) {
            setProgress(job.progress.message || 'Processing...');
            if (job.progress.percentage) {
              setProgress(`${job.progress.message || 'Processing...'} (${Math.round(job.progress.percentage)}%)`);
            }
          }

          // Handle completion
          if (job.status === 'completed') {
            setLoading(false);
            setStatus('Video processing completed successfully!');
            setResults(job.results);
            
            // Fetch metadata for all videos
            if (job.results) {
              fetchAllMetadata(jobId, job.results);
            }
            
            // Load video URLs using authenticated requests
            loadVideoUrls(jobId, job.results);
            
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current);
            }
          }

          // Handle failure
          if (job.status === 'failed') {
            setLoading(false);
            setError(job.error || 'Processing failed');
            setStatus('');
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current);
            }
          }

          // Handle cancellation
          if (job.status === 'cancelled') {
            setLoading(false);
            setStatus('Job cancelled');
            setError('');
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current);
            }
          }
        } catch (err) {
          console.error('Error polling job status:', err);
        }
      }, 2000); // Poll every 2 seconds

      return () => {
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
        }
      };
    }
  }, [jobId, loading, loadVideoUrls]);

  const handleCancel = async () => {
    if (!jobId) return;
    
    // Set cancellation flag
    isCancellingRef.current = true;
    
    try {
      // Stop polling immediately
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      
      // Abort any ongoing requests
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      
      // Stop client-side processing if any
      if (distributedProcessor.current) {
        // Stop any ongoing client processing
        try {
          distributedProcessor.current.stopProcessing?.();
        } catch (e) {
          console.warn('Error stopping distributed processor:', e);
        }
      }
      
      // Cancel the job on the backend
      const token = localStorage.getItem('token');
      const cancelController = new AbortController();
      abortControllerRef.current = cancelController;
      
      await axios.post(
        `${API_BASE_URL}/api/job/${jobId}/cancel`,
        {},
        {
          headers: {
            'Authorization': `Bearer ${token}`
          },
          signal: cancelController.signal
        }
      );
      
      setLoading(false);
      setStatus('Job cancelled. Credits have been refunded if applicable.');
      setError('');
      setProgress('');
      setJobId(null);
      setResults(null);
      
      // Refresh user to get updated credits
      if (refreshUser) {
        refreshUser();
      }
    } catch (err) {
      if (err.name === 'AbortError' || err.code === 'ERR_CANCELED') {
        // Request was aborted, that's fine
        setLoading(false);
        setStatus('Job cancellation in progress...');
      } else {
        console.error('Error cancelling job:', err);
        setError(err.response?.data?.detail || 'Failed to cancel job');
      }
    } finally {
      isCancellingRef.current = false;
      abortControllerRef.current = null;
    }
  };

  const isYouTubeUrl = (value) => {
    try {
      const host = new URL(value).hostname.toLowerCase();
      return host === 'youtube.com' || host === 'www.youtube.com' || host === 'm.youtube.com' || host === 'music.youtube.com' || host === 'youtu.be';
    } catch (err) {
      return false;
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    // Create a wave effect from the Generate Reels button
    if (generateButtonRef.current && interactiveClothRef?.current?.createWave) {
      const buttonRect = generateButtonRef.current.getBoundingClientRect();
      const buttonCenterX = buttonRect.left + buttonRect.width / 2;
      const buttonCenterY = buttonRect.top + buttonRect.height / 2;
      
      // Create a big wave from the button center that propagates to screen edges
      interactiveClothRef.current.createWave(buttonCenterX, buttonCenterY);
    }
    
    // Check authentication
    if (!isAuthenticated) {
      navigate('/login');
      return;
    }
    
    // Cleanup old video URLs before starting new job
    Object.values(videoUrls).forEach(url => {
      if (url && typeof url === 'string' && url.startsWith('blob:')) {
        URL.revokeObjectURL(url);
      } else if (Array.isArray(url)) {
        url.forEach(u => {
          if (u && typeof u === 'string' && u.startsWith('blob:')) {
            URL.revokeObjectURL(u);
          }
        });
      }
    });
    
    // Reset cancellation flag
    isCancellingRef.current = false;
    
    // Create new AbortController for this request
    const controller = new AbortController();
    abortControllerRef.current = controller;
    
    setLoading(true);
    setError('');
    setStatus('Starting video processing...');
    setProgress('');
    setJobId(null);
    setResults(null);
    setMetadata({}); // Reset to empty object instead of null
    setVideoUrls({});

    try {
      let response;
      
      if (inputMethod === 'url') {
        setStatus('Downloading video on your device...');
        setProgress('');

        let downloadResponse;
        try {
          downloadResponse = await fetch(url, { signal: controller.signal });
        } catch (fetchError) {
          if (isYouTubeUrl(url)) {
            throw new Error('YouTube links are not supported for server-side downloads. Please upload the file or provide a direct video file URL.');
          }
          throw new Error('We could not download this link in your browser. This usually means the host blocks cross-origin downloads (CORS) or the URL is not a direct file.');
        }

        if (!downloadResponse.ok) {
          if (isYouTubeUrl(url)) {
            throw new Error('YouTube links are not supported for server-side downloads. Please upload the file or provide a direct video file URL.');
          }
          throw new Error(`We could not download this link in your browser (HTTP ${downloadResponse.status}). Make sure the URL is a direct video file and allows cross-origin downloads.`);
        }

        const blob = await downloadResponse.blob();
        const contentType = downloadResponse.headers.get('Content-Type') || blob.type || 'video/mp4';
        if (!contentType.startsWith('video/')) {
          throw new Error('This link does not look like a direct video file. Please use a direct file URL or upload the file instead.');
        }

        let fileName = 'video.mp4';
        try {
          const urlPath = new URL(url).pathname;
          const pathName = urlPath.split('/').pop();
          if (pathName) {
            fileName = pathName;
          }
        } catch (e) {
          // Keep default filename if URL parsing fails.
        }

        const downloadFile = new File([blob], fileName, { type: contentType });

        setStatus('Uploading video...');
        const formData = new FormData();
        formData.append('video_file', downloadFile);
        formData.append('quality', quality);
        formData.append('remove_watermark', removeWatermark);
        formData.append('video_types', videoTypes);
        formData.append('font_style', fontStyle);
        formData.append('font_color', fontColor);

        response = await axios.post(`${API_BASE_URL}/api/process/upload`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data'
          },
          signal: controller.signal
        });
      } else {
        // New file upload method
        if (!uploadedFile) {
          setLoading(false);
          setError('Please select a video file to upload');
          return;
        }
        
        const formData = new FormData();
        formData.append('video_file', uploadedFile);
        formData.append('quality', quality);
        formData.append('remove_watermark', removeWatermark);
        formData.append('video_types', videoTypes);
        formData.append('font_style', fontStyle);
        formData.append('font_color', fontColor);
        
        response = await axios.post(`${API_BASE_URL}/api/process/upload`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data'
          },
          signal: controller.signal
        });
      }
      
      // Check if cancelled before setting jobId
      if (isCancellingRef.current) {
        return;
      }

      setJobId(response.data.job_id);
      setStatus('Video processing started!');
      setProgress('Initializing...');
    } catch (err) {
      if (err.name === 'AbortError' || err.code === 'ERR_CANCELED' || isCancellingRef.current) {
        setLoading(false);
        setStatus('Processing cancelled');
        setError('');
        setProgress('');
      } else {
        setLoading(false);
        setError(err.response?.data?.detail || err.message || 'An error occurred');
        setStatus('');
      }
    }
  };

  const fetchAllMetadata = async (jobIdToFetch, results) => {
    const metadataMap = {};
    
    // Fetch metadata for main reel
    if (results.main_reel) {
      try {
        const response = await axios.get(`${API_BASE_URL}/api/job/${jobIdToFetch}/metadata?video_type=main_reel`);
        metadataMap.main_reel = response.data;
      } catch (err) {
        console.error('Error fetching main reel metadata:', err);
      }
    }
    
    // Fetch metadata for medium reels
    if (results.medium_reels && results.medium_reels.length > 0) {
      metadataMap.medium_reels = [];
      for (let i = 0; i < results.medium_reels.length; i++) {
        try {
          const response = await axios.get(`${API_BASE_URL}/api/job/${jobIdToFetch}/metadata?video_type=medium_reel&video_index=${i}`);
          metadataMap.medium_reels[i] = response.data;
        } catch (err) {
          console.error(`Error fetching medium reel ${i} metadata:`, err);
          metadataMap.medium_reels[i] = null;
        }
      }
    }
    
    // Fetch metadata for short reels
    if (results.short_reels && results.short_reels.length > 0) {
      metadataMap.short_reels = [];
      for (let i = 0; i < results.short_reels.length; i++) {
        try {
          const response = await axios.get(`${API_BASE_URL}/api/job/${jobIdToFetch}/metadata?video_type=short_reel&video_index=${i}`);
          metadataMap.short_reels[i] = response.data;
        } catch (err) {
          console.error(`Error fetching short reel ${i} metadata:`, err);
          metadataMap.short_reels[i] = null;
        }
      }
    }
    
    setMetadata(metadataMap);
  };

  // Cleanup blob URLs when component unmounts or job changes
  useEffect(() => {
    return () => {
      // Revoke all blob URLs to free memory
      Object.values(videoUrls).forEach(url => {
        if (url && typeof url === 'string' && url.startsWith('blob:')) {
          URL.revokeObjectURL(url);
        } else if (Array.isArray(url)) {
          url.forEach(u => {
            if (u && typeof u === 'string' && u.startsWith('blob:')) {
              URL.revokeObjectURL(u);
            }
          });
        }
      });
      // Also cleanup edited video URLs
      Object.values(editedVideoUrls).forEach(url => {
        if (url && typeof url === 'string' && url.startsWith('blob:')) {
          URL.revokeObjectURL(url);
        } else if (Array.isArray(url)) {
          url.forEach(u => {
            if (u && typeof u === 'string' && u.startsWith('blob:')) {
              URL.revokeObjectURL(u);
            }
          });
        }
      });
    };
  }, [videoUrls, editedVideoUrls]);

  // Auto-scroll to results when videos are available
  useEffect(() => {
    if (results && (results.main_reel || (results.medium_reels && results.medium_reels.length > 0) || (results.short_reels && results.short_reels.length > 0))) {
      // Wait a bit for the DOM to update and videos to load, then scroll
      setTimeout(() => {
        if (resultsSectionRef.current) {
          resultsSectionRef.current.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'start' 
          });
        }
      }, 500);
    }
  }, [results]);

  const handleDownload = async (fileType, index = null) => {
    try {
      let downloadPath;
      if (index !== null) {
        // Handle edited file types (e.g., medium_reel_edited_0)
        if (fileType.includes('_edited')) {
          downloadPath = `${API_BASE_URL}/api/job/${jobId}/download/${fileType}_${index}?download=true`;
        } else {
          downloadPath = `${API_BASE_URL}/api/job/${jobId}/download/${fileType}_${index}?download=true&original=true`;
        }
      } else {
        if (fileType.includes('_edited')) {
          downloadPath = `${API_BASE_URL}/api/job/${jobId}/download/${fileType}?download=true`;
        } else {
          downloadPath = `${API_BASE_URL}/api/job/${jobId}/download/${fileType}?download=true&original=true`;
        }
      }
      
      // Fetch file with authentication
      const token = localStorage.getItem('token');
      const response = await fetch(downloadPath, {
        headers: {
          'Authorization': `Bearer ${token}`,
          'ngrok-skip-browser-warning': 'true'
        }
      });
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      // Get filename from Content-Disposition header or use default
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = fileType + (index !== null ? `_${index}` : '') + '.mp4';
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="?(.+)"?/i);
        if (filenameMatch) {
          filename = filenameMatch[1];
        }
      }
      
      // Create blob and trigger download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      
      // Refresh user data after a short delay to update minutes/credits
      // Only refresh if it's a video file (not metadata)
      if (fileType !== 'metadata') {
        setTimeout(() => {
          refreshUser();
        }, 2000);
      }
    } catch (err) {
      setError('Failed to download file: ' + err.message);
    }
  };

  return (
    <div className="reel-generator">
      <div className="generator-card">
        <h2>Create Your Reel</h2>
        <p className="subtitle">Upload a video file or enter a YouTube URL to generate engaging video reels</p>
        
        <form onSubmit={handleSubmit} className="generator-form">
          <div className="form-group">
            <label>Input Method</label>
            <div className="input-method-toggle">
              <button
                type="button"
                className={`toggle-button ${inputMethod === 'url' ? 'active' : ''}`}
                onClick={() => {
                  setInputMethod('url');
                  setUploadedFile(null);
                }}
                disabled={loading}
              >
                YouTube URL
              </button>
              <button
                type="button"
                className={`toggle-button ${inputMethod === 'upload' ? 'active' : ''}`}
                onClick={() => {
                  setInputMethod('upload');
                  setUrl('');
                }}
                disabled={loading}
              >
                Upload Video
              </button>
            </div>
          </div>

          {inputMethod === 'url' ? (
            <div className="form-group">
              <label htmlFor="youtube-url">YouTube Video URL</label>
              <input
                type="text"
                id="youtube-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
                required={inputMethod === 'url'}
                disabled={loading}
                className="url-input"
              />
            </div>
          ) : (
            <div className="form-group">
              <label htmlFor="video-upload">Upload Video File</label>
              <input
                type="file"
                id="video-upload"
                accept="video/*"
                onChange={(e) => setUploadedFile(e.target.files[0])}
                required={inputMethod === 'upload'}
                disabled={loading}
                className="file-input"
              />
              {uploadedFile && (
                <p className="file-info">
                  Selected: {uploadedFile.name} ({(uploadedFile.size / 1024 / 1024).toFixed(2)} MB)
                </p>
              )}
            </div>
          )}

          <div className="form-group">
            <label>Video Quality</label>
            <div className="quality-buttons">
              <button
                type="button"
                className={`quality-button ${quality === 'full_hd' ? 'active' : ''}`}
                onClick={() => setQuality('full_hd')}
                disabled={loading}
                title="Full HD (1080p) - Highest quality, slower processing"
              >
                Full HD
              </button>
              <button
                type="button"
                className={`quality-button ${quality === 'hd' ? 'active' : ''}`}
                onClick={() => setQuality('hd')}
                disabled={loading}
                title="HD (720p) - Balanced quality and speed"
              >
                HD
              </button>
              <button
                type="button"
                className={`quality-button ${quality === 'normal' ? 'active' : ''}`}
                onClick={() => setQuality('normal')}
                disabled={loading}
                title="Normal (480p) - Fastest processing"
              >
                Normal
              </button>
            </div>
            <p className="quality-hint">
              {quality === 'full_hd' && 'Best quality, takes longer to process'}
              {quality === 'hd' && 'Good balance of quality and speed (recommended)'}
              {quality === 'normal' && 'Fastest processing, good for quick previews'}
            </p>
          </div>

          <div className="form-group">
            <label>Watermark</label>
            <div className="watermark-buttons">
              <button
                type="button"
                className={`watermark-button ${!removeWatermark ? 'active' : ''}`}
                onClick={() => setRemoveWatermark(false)}
                disabled={loading}
                title="Keep watermark (free)"
              >
                Keep Watermark
              </button>
              <button
                type="button"
                className={`watermark-button ${removeWatermark ? 'active' : ''}`}
                onClick={() => setRemoveWatermark(true)}
                disabled={loading}
                title="Remove watermark for 15 credits per video"
              >
                Remove Watermark
              </button>
            </div>
            <p className="watermark-hint">
              {removeWatermark 
                ? 'Watermark will be removed (15 credits per video)' 
                : 'Videos will have a watermark by default. Remove it for 15 credits per generated video.'}
            </p>
          </div>

          <div className="form-group">
            <label>Video Types</label>
            <div className="video-type-buttons">
              <button
                type="button"
                className={`video-type-button ${videoTypes === 'short_only' ? 'active' : ''}`}
                onClick={() => setVideoTypes('short_only')}
                disabled={loading}
                title="Generate up to 6 short form videos (15-25s each) + compilation"
              >
                Short Only (max 6)
              </button>
              <button
                type="button"
                className={`video-type-button ${videoTypes === 'medium_only' ? 'active' : ''}`}
                onClick={() => setVideoTypes('medium_only')}
                disabled={loading}
                title="Generate up to 4 medium form videos (45-75s each) + compilation"
              >
                Medium Only (max 4)
              </button>
              <button
                type="button"
                className={`video-type-button ${videoTypes === 'both' ? 'active' : ''}`}
                onClick={() => setVideoTypes('both')}
                disabled={loading}
                title="Generate up to 2 medium + 4 short videos + compilation"
              >
                Both (2 medium + 4 short)
              </button>
            </div>
            <p className="video-type-hint">
              {videoTypes === 'short_only' && 'Will generate up to 6 short form videos (15-25 seconds each) + compilation video'}
              {videoTypes === 'medium_only' && 'Will generate up to 4 medium form videos (45-75 seconds each) + compilation video'}
              {videoTypes === 'both' && 'Will generate up to 2 medium videos + 4 short videos + compilation video'}
            </p>
          </div>

          <div className="form-group">
            <label>Video Styling</label>
            <button
              type="button"
              className="styling-button"
              onClick={() => setIsStylingModalOpen(true)}
              disabled={loading}
            >
              <span>🎨</span> Customize Font
            </button>
            <p className="styling-hint">
              Current: {fontStyle} style, {fontColor} color
            </p>
          </div>

          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            <button
              ref={generateButtonRef}
              type="submit"
              disabled={loading || (inputMethod === 'url' && !url) || (inputMethod === 'upload' && !uploadedFile)}
              className={`submit-button ${!loading && ((inputMethod === 'url' && url) || (inputMethod === 'upload' && uploadedFile)) ? 'ready' : ''}`}
            >
              {loading ? (
                <>
                  <span className="spinner"></span>
                  Generating...
                </>
              ) : (
                'Generate Reels'
              )}
            </button>
            
            {loading && jobId && (
              <button
                type="button"
                onClick={handleCancel}
                className="cancel-button"
                style={{
                  padding: '10px 20px',
                  backgroundColor: '#dc3545',
                  color: 'white',
                  border: 'none',
                  borderRadius: '5px',
                  cursor: 'pointer',
                  fontSize: '14px',
                  fontWeight: '500',
                  transition: 'background-color 0.2s'
                }}
                onMouseEnter={(e) => e.target.style.backgroundColor = '#c82333'}
                onMouseLeave={(e) => e.target.style.backgroundColor = '#dc3545'}
              >
                Cancel
              </button>
            )}
          </div>
        </form>

        {status && (
          <div className="status-message success">
            {status}
          </div>
        )}

        {error && (
          <div className="status-message error">
            {error}
          </div>
        )}

        {progress && (
          <div className="progress-message">
            {progress}
          </div>
        )}

        {results && (
          <div className="results-section" ref={resultsSectionRef}>
            <h3>Generated Videos</h3>
            
            {results.main_reel && (
              <div className="video-card">
                {metadata && metadata.main_reel && metadata.main_reel.virality_score && (
                  <ViralityScore score={metadata.main_reel.virality_score} />
                )}
                <h4>Compilation Video</h4>
                <div className="video-container">
                  <video 
                    controls 
                    className="video-player"
                    src={videoUrls.main_reel || ''}
                    preload="metadata"
                  >
                    Your browser does not support the video tag.
                  </video>
                </div>
                <div className="video-actions">
                  <button 
                    onClick={() => handleDownload('main_reel')}
                    className="download-button"
                  >
                    📥 Download
                  </button>
                  <button 
                    onClick={() => setQuickEditModal({ 
                      isOpen: true, 
                      videoUrl: videoUrls.main_reel, 
                      videoType: 'main_reel', 
                      videoIndex: null 
                    })}
                    className="quick-edit-button"
                  >
                    ✏️ Quick Edit
                  </button>
                </div>
                {metadata && metadata.main_reel && (metadata.main_reel.options || (metadata.main_reel.content && metadata.main_reel.content.options)) && (
                  <MetadataDisplay metadata={metadata.main_reel.content || metadata.main_reel} />
                )}
              </div>
            )}

            {results.medium_reels && results.medium_reels.length > 0 && (
              <div className="result-group">
                <h4 className="section-title">Medium Reels ({results.medium_reels.length})</h4>
                <div className="video-grid">
                  {results.medium_reels.map((reel, index) => {
                    // Only show edited video if it exists AND corresponds to this original video index
                    const editedUrl = editedVideoUrls.medium_reels && editedVideoUrls.medium_reels[index];
                    const isEdited = editedVideos.medium_reel && editedVideos.medium_reel[index] === true;
                    const hasEdited = editedUrl !== null && editedUrl !== undefined && isEdited;
                    
                    // Debug logging
                    if (index === 1) { // Log for Medium Reel 2 (index 1)
                      console.log(`Medium Reel 2 (index ${index}):`, {
                        editedUrl: editedUrl ? 'exists' : 'null',
                        isEdited: isEdited,
                        hasEdited: hasEdited,
                        editedVideosState: editedVideos.medium_reel
                      });
                    }
                    
                    return (
                      <React.Fragment key={index}>
                        {/* Original Video */}
                        <div className="video-card">
                          {metadata && metadata.medium_reels && metadata.medium_reels[index] && metadata.medium_reels[index].virality_score && (
                            <ViralityScore score={metadata.medium_reels[index].virality_score} />
                          )}
                          <h5>Medium Reel {index + 1}</h5>
                          <div className="video-container">
                            <video 
                              controls 
                              className="video-player"
                              src={videoUrls.medium_reels && videoUrls.medium_reels[index] ? videoUrls.medium_reels[index] : ''}
                              preload="metadata"
                            >
                              Your browser does not support the video tag.
                            </video>
                          </div>
                          <div className="video-actions">
                            <button 
                              onClick={() => handleDownload('medium_reel', index)}
                              className="download-button"
                            >
                              📥 Download
                            </button>
                            <button 
                              onClick={() => setQuickEditModal({ 
                                isOpen: true, 
                                videoUrl: videoUrls.medium_reels && videoUrls.medium_reels[index], 
                                videoType: 'medium_reel', 
                                videoIndex: index 
                              })}
                              className="quick-edit-button"
                            >
                              ✏️ Quick Edit
                            </button>
                          </div>
                          {metadata && metadata.medium_reels && metadata.medium_reels[index] && (metadata.medium_reels[index].options || (metadata.medium_reels[index].content && metadata.medium_reels[index].content.options)) && (
                            <MetadataDisplay metadata={metadata.medium_reels[index].content || metadata.medium_reels[index]} />
                          )}
                        </div>
                        {/* Edited Video (if exists) */}
                        {hasEdited && (
                          <div className="video-card">
                            {metadata && metadata.medium_reels && metadata.medium_reels[index] && metadata.medium_reels[index].virality_score && (
                              <ViralityScore score={metadata.medium_reels[index].virality_score} />
                            )}
                            <h5>Medium Reel {index + 1} (edited)</h5>
                            <div className="video-container">
                              <video 
                                controls 
                                className="video-player"
                                src={editedVideoUrls.medium_reels[index]}
                                preload="metadata"
                              >
                                Your browser does not support the video tag.
                              </video>
                            </div>
                            <div className="video-actions">
                              <button 
                                onClick={() => handleDownload('medium_reel_edited', index)}
                                className="download-button"
                              >
                                📥 Download
                              </button>
                            </div>
                            {metadata && metadata.medium_reels && metadata.medium_reels[index] && (metadata.medium_reels[index].options || (metadata.medium_reels[index].content && metadata.medium_reels[index].content.options)) && (
                              <MetadataDisplay metadata={metadata.medium_reels[index].content || metadata.medium_reels[index]} />
                            )}
                          </div>
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>
            )}

            {results.short_reels && results.short_reels.length > 0 && (
              <div className="result-group">
                <h4 className="section-title">Short Reels ({results.short_reels.length})</h4>
                <div className="video-grid">
                  {results.short_reels.map((reel, index) => {
                    // Only show edited video if it exists AND corresponds to this original video index
                    const editedUrl = editedVideoUrls.short_reels && editedVideoUrls.short_reels[index];
                    const isEdited = editedVideos.short_reel && editedVideos.short_reel[index] === true;
                    const hasEdited = editedUrl !== null && editedUrl !== undefined && isEdited;
                    return (
                      <React.Fragment key={index}>
                        {/* Original Video */}
                        <div className="video-card">
                          {metadata && metadata.short_reels && metadata.short_reels[index] && metadata.short_reels[index].virality_score && (
                            <ViralityScore score={metadata.short_reels[index].virality_score} />
                          )}
                          <h5>Short Reel {index + 1}</h5>
                          <div className="video-container">
                            <video 
                              controls 
                              className="video-player"
                              src={videoUrls.short_reels && videoUrls.short_reels[index] ? videoUrls.short_reels[index] : ''}
                              preload="metadata"
                            >
                              Your browser does not support the video tag.
                            </video>
                          </div>
                          <div className="video-actions">
                            <button 
                              onClick={() => handleDownload('short_reel', index)}
                              className="download-button"
                            >
                              📥 Download
                            </button>
                            <button 
                              onClick={() => setQuickEditModal({ 
                                isOpen: true, 
                                videoUrl: videoUrls.short_reels && videoUrls.short_reels[index], 
                                videoType: 'short_reel', 
                                videoIndex: index 
                              })}
                              className="quick-edit-button"
                            >
                              ✏️ Quick Edit
                            </button>
                          </div>
                          {metadata && metadata.short_reels && metadata.short_reels[index] && (metadata.short_reels[index].options || (metadata.short_reels[index].content && metadata.short_reels[index].content.options)) && (
                            <MetadataDisplay metadata={metadata.short_reels[index].content || metadata.short_reels[index]} />
                          )}
                        </div>
                        {/* Edited Video (if exists) */}
                        {hasEdited && (
                          <div className="video-card">
                            {metadata && metadata.short_reels && metadata.short_reels[index] && metadata.short_reels[index].virality_score && (
                              <ViralityScore score={metadata.short_reels[index].virality_score} />
                            )}
                            <h5>Short Reel {index + 1} (edited)</h5>
                            <div className="video-container">
                              <video 
                                controls 
                                className="video-player"
                                src={editedVideoUrls.short_reels[index]}
                                preload="metadata"
                              >
                                Your browser does not support the video tag.
                              </video>
                            </div>
                            <div className="video-actions">
                              <button 
                                onClick={() => handleDownload('short_reel_edited', index)}
                                className="download-button"
                              >
                                📥 Download
                              </button>
                            </div>
                            {metadata && metadata.short_reels && metadata.short_reels[index] && (metadata.short_reels[index].options || (metadata.short_reels[index].content && metadata.short_reels[index].content.options)) && (
                              <MetadataDisplay metadata={metadata.short_reels[index].content || metadata.short_reels[index]} />
                            )}
                          </div>
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>
            )}

          </div>
        )}
      </div>

      <VideoStylingModal
        isOpen={isStylingModalOpen}
        onClose={() => setIsStylingModalOpen(false)}
        fontStyle={fontStyle}
        fontColor={fontColor}
        onFontStyleChange={setFontStyle}
        onFontColorChange={setFontColor}
        disabled={loading}
      />

      <QuickEditModal
        isOpen={quickEditModal.isOpen}
        onClose={() => setQuickEditModal({ isOpen: false, videoUrl: null, videoType: null, videoIndex: null })}
        videoUrl={quickEditModal.videoUrl}
        videoType={quickEditModal.videoType}
        videoIndex={quickEditModal.videoIndex}
        jobId={jobId}
        onEditComplete={async (result) => {
          // Wait a moment for the database to commit, then refresh the page to show the edited video
          await new Promise(resolve => setTimeout(resolve, 1000));
          window.location.reload();
        }}
      />
    </div>
  );
}

function ViralityScore({ score }) {
  if (!score || score < 1 || score > 100) {
    return null;
  }
  
  // Determine color based on score
  let colorClass = 'virality-red';
  if (score > 95) {
    colorClass = 'virality-light-blue';
  } else if (score > 85) {
    colorClass = 'virality-dark-blue';
  } else if (score > 65) {
    colorClass = 'virality-green';
  } else if (score > 50) {
    colorClass = 'virality-yellow';
  }
  
  const isHighScore = score > 90;
  
  return (
    <div className={`virality-score-container ${colorClass}`}>
      {isHighScore && <span className="virality-fire">🔥</span>}
      <div className="virality-score">
        {score}/100
      </div>
    </div>
  );
}

function MetadataDisplay({ metadata }) {
  const [selectedOption, setSelectedOption] = useState(0);
  const [isExpanded, setIsExpanded] = useState(false);
  
  if (!metadata || !metadata.options || metadata.options.length === 0) {
    return null;
  }

  const option = metadata.options[selectedOption];

  return (
    <div className="metadata-display">
      <div className="metadata-header">
        <button 
          className="metadata-toggle-button"
          onClick={() => setIsExpanded(!isExpanded)}
          aria-expanded={isExpanded}
        >
          <h6>Suggested Metadata</h6>
          <span className="metadata-toggle-icon">{isExpanded ? '▼' : '▶'}</span>
        </button>
        {metadata.options.length > 1 && isExpanded && (
          <div className="metadata-options">
            {metadata.options.map((_, index) => (
              <button
                key={index}
                className={`option-button ${selectedOption === index ? 'active' : ''}`}
                onClick={() => setSelectedOption(index)}
              >
                {index + 1}
              </button>
            ))}
          </div>
        )}
      </div>
      {isExpanded && (
        <div className="metadata-content">
          <div className="metadata-item">
            <strong>Title:</strong>
            <p>{option.title || 'N/A'}</p>
          </div>
          
          <div className="metadata-item">
            <strong>Description:</strong>
            <p>{option.description || 'N/A'}</p>
          </div>
          
          <div className="metadata-item">
            <strong>Hashtags:</strong>
            <p className="hashtags">
              {option.hashtags && option.hashtags.length > 0
                ? option.hashtags.join(' ')
                : 'N/A'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default ReelGenerator;

