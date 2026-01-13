/**
 * Client Clip Processor
 * Processes video clips on the client side using ffmpeg.wasm
 */

import { FFmpeg } from '@ffmpeg/ffmpeg';
import { toBlobURL } from '@ffmpeg/util';
import SubtitleRenderer from './SubtitleRenderer';
import WatermarkRemover from './WatermarkRemover';

class ClientClipProcessor {
  constructor() {
    this.ffmpeg = null;
    this.isLoaded = false;
    this.subtitleRenderer = new SubtitleRenderer();
    this.watermarkRemover = new WatermarkRemover();
    this.loadPromise = null;
  }

  /**
   * Load ffmpeg.wasm
   */
  async load() {
    if (this.isLoaded && this.ffmpeg) {
      return this.ffmpeg;
    }

    if (this.loadPromise) {
      return this.loadPromise;
    }

    this.loadPromise = (async () => {
      this.ffmpeg = new FFmpeg();
      
      try {
        const baseURL = 'https://unpkg.com/@ffmpeg/core@0.12.6/dist/esm';
        await this.ffmpeg.load({
          coreURL: await toBlobURL(`${baseURL}/ffmpeg-core.js`, 'text/javascript'),
          wasmURL: await toBlobURL(`${baseURL}/ffmpeg-core.wasm`, 'application/wasm'),
        });
        
        this.isLoaded = true;
        return this.ffmpeg;
      } catch (error) {
        this.loadPromise = null;
        throw error;
      }
    })();

    return this.loadPromise;
  }

  /**
   * Process a single clip
   * @param {Blob} videoChunk - Video chunk blob
   * @param {Object} clipInfo - Clip information {start, end, text, ...}
   * @param {string} quality - Video quality
   * @param {boolean} removeWatermark - Whether to remove watermark
   * @param {Array} transcriptData - Transcript data for subtitles
   * @param {Function} onProgress - Progress callback
   * @returns {Promise<Blob>} Processed clip as blob
   */
  async processClip(videoChunk, clipInfo, quality = 'hd', removeWatermark = false, transcriptData = null, onProgress = null) {
    if (!this.isLoaded) {
      await this.load();
    }

    try {
      // Write input video to virtual filesystem
      await this.ffmpeg.writeFile('input.mp4', videoChunk);

      // Build video filter
      const outputWidth = 720;
      const outputHeight = 1280;
      
      let filterComplex = `scale=${outputWidth}:${outputHeight}:force_original_aspect_ratio=decrease,pad=${outputWidth}:${outputHeight}:(ow-iw)/2:(oh-ih)/2:color=black`;
      
      // Add watermark removal if needed
      if (removeWatermark) {
        // Use boxblur to remove watermark
        filterComplex += ',boxblur=10:5';
      }

      // Build ffmpeg arguments
      const crf = quality === 'full_hd' ? '20' : quality === 'hd' ? '23' : '26';
      
      const args = [
        '-i', 'input.mp4',
        '-vf', filterComplex,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', crf,
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart',
        'output.mp4'
      ];

      if (onProgress) {
        this.ffmpeg.on('progress', ({ progress, time }) => {
          onProgress({
            type: 'processing',
            progress: progress * 100,
            time: time
          });
        });
      }

      // Execute ffmpeg
      await this.ffmpeg.exec(args);

      // Read output
      const data = await this.ffmpeg.readFile('output.mp4');

      // Clean up
      await this.ffmpeg.deleteFile('input.mp4');
      await this.ffmpeg.deleteFile('output.mp4');

      // Convert to blob
      const blob = new Blob([data.buffer], { type: 'video/mp4' });

      return blob;
    } catch (error) {
      console.error('Error processing clip:', error);
      throw error;
    }
  }

  /**
   * Process multiple clips
   * @param {Array} clips - Array of {clip, chunkBlob} objects
   * @param {string} quality - Video quality
   * @param {boolean} removeWatermark - Whether to remove watermark
   * @param {Array} transcriptData - Transcript data
   * @param {Function} onProgress - Progress callback
   * @returns {Promise<Array>} Processed clips
   */
  async processClips(clips, quality = 'hd', removeWatermark = false, transcriptData = null, onProgress = null) {
    if (!this.isLoaded) {
      await this.load();
    }

    const results = [];

    for (let i = 0; i < clips.length; i++) {
      const { clip, chunkBlob } = clips[i];

      if (!chunkBlob) {
        console.warn(`Skipping clip ${i + 1} - no chunk data`);
        results.push({ clip, processedBlob: null, error: 'No chunk data' });
        continue;
      }

      if (onProgress) {
        onProgress({
          type: 'clip_start',
          current: i + 1,
          total: clips.length,
          clip: clip
        });
      }

      try {
        const processedBlob = await this.processClip(
          chunkBlob,
          clip,
          quality,
          removeWatermark,
          transcriptData,
          (progress) => {
            if (onProgress) {
              onProgress({
                type: 'clip_progress',
                clipIndex: i,
                ...progress
              });
            }
          }
        );

        results.push({ clip, processedBlob, error: null });
      } catch (error) {
        console.error(`Error processing clip ${i + 1}:`, error);
        results.push({ clip, processedBlob: null, error: error.message });
      }
    }

    return results;
  }

  /**
   * Clean up resources
   */
  async cleanup() {
    if (this.ffmpeg) {
      try {
        // Clean up virtual filesystem
        const files = await this.ffmpeg.listDir('/');
        for (const file of files) {
          if (file.isFile) {
            try {
              await this.ffmpeg.deleteFile(file.name);
            } catch (e) {
              // Ignore cleanup errors
            }
          }
        }
      } catch (e) {
        // Ignore cleanup errors
      }
    }

    this.subtitleRenderer.cleanup();
    this.watermarkRemover.cleanup();
  }
}

export default ClientClipProcessor;

