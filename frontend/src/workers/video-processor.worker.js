/**
 * Video Processing Web Worker
 * Handles video processing using ffmpeg.wasm in a separate thread
 */

import { FFmpeg } from '@ffmpeg/ffmpeg';
import { fetchFile, toBlobURL } from '@ffmpeg/util';

let ffmpeg = null;
let isLoaded = false;

// Load ffmpeg.wasm
async function loadFFmpeg() {
  if (isLoaded && ffmpeg) {
    return ffmpeg;
  }

  ffmpeg = new FFmpeg();
  
  // Set up logging
  ffmpeg.on('log', ({ message }) => {
    self.postMessage({ type: 'log', message });
  });

  ffmpeg.on('progress', ({ progress, time }) => {
    self.postMessage({ type: 'progress', progress, time });
  });

  try {
    const baseURL = 'https://unpkg.com/@ffmpeg/core@0.12.6/dist/esm';
    await ffmpeg.load({
      coreURL: await toBlobURL(`${baseURL}/ffmpeg-core.js`, 'text/javascript'),
      wasmURL: await toBlobURL(`${baseURL}/ffmpeg-core.wasm`, 'application/wasm'),
    });
    
    isLoaded = true;
    self.postMessage({ type: 'ready' });
    return ffmpeg;
  } catch (error) {
    self.postMessage({ type: 'error', error: error.message });
    throw error;
  }
}

// Process video clip
async function processClip(clipData) {
  const { videoChunk, clipInfo, quality, removeWatermark } = clipData;
  
  try {
    const ffmpegInstance = await loadFFmpeg();
    
    // Write input video to virtual filesystem
    await ffmpegInstance.writeFile('input.mp4', videoChunk);
    
    // Build ffmpeg command
    const outputWidth = 720;
    const outputHeight = 1280;
    
    let filterComplex = `scale=${outputWidth}:${outputHeight}:force_original_aspect_ratio=decrease,pad=${outputWidth}:${outputHeight}:(ow-iw)/2:(oh-ih)/2:color=black`;
    
    // Add watermark removal filter if needed
    if (removeWatermark) {
      // Simple blur approach for watermark removal
      // In production, you might want more sophisticated inpainting
      filterComplex += ',boxblur=10:5';
    }
    
    const args = [
      '-i', 'input.mp4',
      '-vf', filterComplex,
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', quality === 'full_hd' ? '20' : quality === 'hd' ? '23' : '26',
      '-c:a', 'aac',
      '-b:a', '128k',
      '-movflags', '+faststart',
      'output.mp4'
    ];
    
    self.postMessage({ type: 'processing', message: 'Processing clip...' });
    
    // Execute ffmpeg
    await ffmpegInstance.exec(args);
    
    // Read output
    const data = await ffmpegInstance.readFile('output.mp4');
    
    // Clean up
    await ffmpegInstance.deleteFile('input.mp4');
    await ffmpegInstance.deleteFile('output.mp4');
    
    return data;
  } catch (error) {
    self.postMessage({ type: 'error', error: error.message });
    throw error;
  }
}

// Handle messages from main thread
self.addEventListener('message', async (event) => {
  const { type, data } = event.data;
  
  try {
    switch (type) {
      case 'process':
        const result = await processClip(data);
        self.postMessage({ type: 'complete', result });
        break;
        
      case 'load':
        await loadFFmpeg();
        break;
        
      case 'cleanup':
        if (ffmpeg) {
          // Clean up virtual filesystem
          try {
            const files = await ffmpeg.listDir('/');
            for (const file of files) {
              if (file.isFile) {
                await ffmpeg.deleteFile(file.name);
              }
            }
          } catch (e) {
            // Ignore cleanup errors
          }
        }
        self.postMessage({ type: 'cleanup_complete' });
        break;
        
      default:
        self.postMessage({ type: 'error', error: `Unknown message type: ${type}` });
    }
  } catch (error) {
    self.postMessage({ type: 'error', error: error.message, stack: error.stack });
  }
});

