/**
 * Video Chunk Downloader
 * Downloads specific video chunks from server for client-side processing
 */

import axios from 'axios';

class VideoChunkDownloader {
  constructor(apiUrl) {
    this.apiUrl = apiUrl;
  }

  /**
   * Download a video chunk for a specific time range
   * @param {string} jobId - Job ID
   * @param {number} startTime - Start time in seconds
   * @param {number} endTime - End time in seconds
   * @param {string} quality - Video quality
   * @param {Function} onProgress - Progress callback
   * @returns {Promise<Blob>} Video chunk as blob
   */
  async downloadChunk(jobId, startTime, endTime, quality = 'hd', onProgress = null) {
    const token = localStorage.getItem('token');
    
    try {
      const response = await axios.get(
        `${this.apiUrl}/api/job/${jobId}/video-chunk`,
        {
          params: {
            start_time: startTime,
            end_time: endTime,
            quality: quality
          },
          responseType: 'blob',
          headers: {
            'Authorization': `Bearer ${token}`
          },
          onDownloadProgress: (progressEvent) => {
            if (onProgress && progressEvent.total) {
              const percent = (progressEvent.loaded / progressEvent.total) * 100;
              onProgress({
                loaded: progressEvent.loaded,
                total: progressEvent.total,
                percent: percent
              });
            }
          }
        }
      );

      return response.data;
    } catch (error) {
      console.error(`Error downloading chunk ${startTime}-${endTime}:`, error);
      throw error;
    }
  }

  /**
   * Download multiple chunks for clips
   * @param {string} jobId - Job ID
   * @param {Array} clips - Array of clip objects with {start, end, ...}
   * @param {string} quality - Video quality
   * @param {Function} onProgress - Progress callback
   * @returns {Promise<Array>} Array of {clip, chunkBlob} objects
   */
  async downloadChunksForClips(jobId, clips, quality = 'hd', onProgress = null) {
    const results = [];
    
    for (let i = 0; i < clips.length; i++) {
      const clip = clips[i];
      const startTime = clip.start;
      const endTime = clip.end;
      
      if (onProgress) {
        onProgress({
          type: 'chunk_download',
          current: i + 1,
          total: clips.length,
          clip: clip
        });
      }

      try {
        const chunkBlob = await this.downloadChunk(
          jobId,
          startTime,
          endTime,
          quality,
          (progress) => {
            if (onProgress) {
              onProgress({
                type: 'chunk_progress',
                clipIndex: i,
                ...progress
              });
            }
          }
        );

        results.push({
          clip: clip,
          chunkBlob: chunkBlob
        });
      } catch (error) {
        console.error(`Failed to download chunk for clip ${i + 1}:`, error);
        // Continue with other clips even if one fails
        results.push({
          clip: clip,
          chunkBlob: null,
          error: error.message
        });
      }
    }

    return results;
  }
}

export default VideoChunkDownloader;

