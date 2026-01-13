import axios from 'axios';
import TaskDistributor from './TaskDistributor';
import SystemResourceDetector from './SystemResourceDetector';
import RAMMonitor from './RAMMonitor';
import VideoChunkDownloader from './VideoChunkDownloader';
import ClientClipProcessor from './ClientClipProcessor';
import RAMAwareBatchProcessor from './RAMAwareBatchProcessor';
import MemoryManager from './MemoryManager';

/**
 * Distributed Video Processor
 * Coordinates video processing between server and client based on RAM
 */
class DistributedVideoProcessor {
  constructor(apiUrl) {
    this.apiUrl = apiUrl;
    this.taskDistributor = new TaskDistributor(apiUrl);
    this.resourceDetector = new SystemResourceDetector();
    this.ramMonitor = null;
    this.chunkDownloader = new VideoChunkDownloader(apiUrl);
    this.clipProcessor = new ClientClipProcessor();
    this.batchProcessor = new RAMAwareBatchProcessor();
    this.memoryManager = new MemoryManager();
  }

  /**
   * Process video package with distributed approach
   */
  async processVideoPackage(videoUrl, clips, quality, removeWatermark, videoTypes, fontStyle, fontColor, onProgress) {
    try {
      // Step 1: Check client RAM capabilities
      const ramInfo = await this.resourceDetector.getSafeRAMAllocation();
      
      // Estimate clip capacity even if clips aren't known yet
      const averageDuration = clips.medium?.length > 0 || clips.short?.length > 0 
        ? this.taskDistributor.getAverageClipDuration(clips)
        : 60; // Default estimate
      
      const clipCapacity = await this.resourceDetector.estimateProcessableClips(
        averageDuration,
        quality
      );
      
      if (ramInfo.safeAllocationGB < 0.5) {
        console.warn('⚠️ Insufficient RAM for client processing. Using server-only mode.');
        return this.fallbackToServerOnly(videoUrl, clips, quality, removeWatermark, videoTypes, fontStyle, fontColor);
      }
      
      // Step 2: Distribute tasks based on RAM (if clips are available)
      let taskDistribution = null;
      let hasClientTasks = false;
      
      if (clips.medium?.length > 0 || clips.short?.length > 0) {
        taskDistribution = await this.taskDistributor.distributeTasks(
          { quality, removeWatermark },
          clips
        );
        const clientTasks = taskDistribution.distribution.client;
        hasClientTasks = (clientTasks.mediumReels.length > 0 || clientTasks.shortReels.length > 0);
      }
      
      // Step 3: Start RAM monitoring
      this.ramMonitor = new RAMMonitor(ramInfo.safeAllocation);
      if (onProgress) {
        this.ramMonitor.startMonitoring((ramStatus) => {
          onProgress({
            type: 'ram_status',
            ...ramStatus
          });
        });
      }
      
      // Step 4: Send job request to server with RAM capabilities
      // Server will analyze video and use RAM info for task distribution
      const token = localStorage.getItem('token');
      const response = await axios.post(
        `${this.apiUrl}/api/process/distributed`,
        {
          video_url: videoUrl,
          clips: clips, // May be empty, server will analyze
          quality: quality,
          remove_watermark: removeWatermark,
          video_types: videoTypes || "both",
          font_style: fontStyle,  // Use UI value directly, no default
          font_color: fontColor,  // Use UI value directly, no default
          client_capabilities: {
            max_ram_mb: ramInfo.safeAllocationMB,
            max_clips: clipCapacity.maxClips,
            available_ram_mb: ramInfo.freeRAM / (1024 * 1024),
            ram_percentage: ramInfo.percentage,
            can_process: hasClientTasks,
            safe_allocation_gb: ramInfo.safeAllocationGB
          },
          distribution: taskDistribution?.distribution || {
            client: { mediumReels: [], shortReels: [], maxClips: clipCapacity.maxClips },
            server: { mainReel: true, mediumReels: [], shortReels: [] }
          }
        },
        {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          }
        }
      );
      
      const jobInfo = response.data;
      
      // Step 5: Process client tasks if any
      // Note: We need to wait for server to analyze and get clips first
      // For now, we'll notify server and let it coordinate
      // In a full implementation, server would send clips back after analysis
      if (hasClientTasks && taskDistribution) {
        // Notify server that client is ready
        const token = localStorage.getItem('token');
        try {
          await axios.post(
            `${this.apiUrl}/api/job/${jobInfo.job_id}/client-ready`,
            {
              client_tasks: [
                ...taskDistribution.distribution.client.mediumReels,
                ...taskDistribution.distribution.client.shortReels
              ],
              ram_available_mb: ramInfo.safeAllocationMB,
              max_clips: taskDistribution.clipCapacity.maxClips
            },
            {
              headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
              }
            }
          );

          // Wait a bit for server to analyze and send clips back
          // In production, this would use WebSocket or polling
          // For now, client processing will be triggered when server sends clips
        } catch (error) {
          console.error('Failed to notify server of client readiness:', error);
          // Continue - server will process everything
        }
      }
      
      return jobInfo;
      
    } catch (error) {
      console.error('Distributed processing error:', error);
      // Fallback to server-only processing
      return this.fallbackToServerOnly(videoUrl, clips, quality, removeWatermark, videoTypes, fontStyle, fontColor);
    } finally {
      if (this.ramMonitor) {
        this.ramMonitor.stopMonitoring();
      }
    }
  }

  /**
   * Process tasks on client side
   */
  async processClientTasks(jobId, clientTasks, videoUrl, quality, removeWatermark, transcriptData, onProgress) {
    const allClientClips = [
      ...clientTasks.mediumReels.map(c => ({ ...c, type: 'medium' })),
      ...clientTasks.shortReels.map(c => ({ ...c, type: 'short' }))
    ];
    
    if (allClientClips.length === 0) {
      return;
    }
    
    console.log(`🖥️ Processing ${allClientClips.length} clips on client...`);
    
    if (onProgress) {
      onProgress({
        type: 'client_processing_start',
        totalClips: allClientClips.length
      });
    }

    try {
      // Initialize batch processor
      await this.batchProcessor.initialize();

      // Step 1: Download video chunks for all clips
      if (onProgress) {
        onProgress({
          type: 'downloading_chunks',
          message: 'Downloading video chunks...'
        });
      }

      const chunksWithClips = await this.chunkDownloader.downloadChunksForClips(
        jobId,
        allClientClips,
        quality,
        (progress) => {
          if (onProgress) {
            onProgress({
              type: 'chunk_download_progress',
              ...progress
            });
          }
        }
      );

      // Filter out failed downloads
      const validChunks = chunksWithClips.filter(item => item.chunkBlob !== null);
      
      if (validChunks.length === 0) {
        throw new Error('Failed to download any video chunks');
      }

      // Step 2: Process clips in RAM-safe batches
      if (onProgress) {
        onProgress({
          type: 'processing_clips',
          message: 'Processing clips on client...'
        });
      }

      const processedClips = await this.batchProcessor.processInBatches(
        validChunks,
        async (chunkItem) => {
          const { clip, chunkBlob } = chunkItem;
          
          // Process clip using ClientClipProcessor
          const processedBlob = await this.clipProcessor.processClip(
            chunkBlob,
            clip,
            quality,
            removeWatermark,
            transcriptData,
            (progress) => {
              if (onProgress) {
                onProgress({
                  type: 'clip_processing_progress',
                  clip: clip,
                  ...progress
                });
              }
            }
          );

          return {
            clip: clip,
            processedBlob: processedBlob
          };
        },
        quality,
        (progress) => {
          if (onProgress) {
            onProgress({
              type: 'batch_progress',
              ...progress
            });
          }
        }
      );

      // Step 3: Upload processed clips to server
      if (onProgress) {
        onProgress({
          type: 'uploading_clips',
          message: 'Uploading processed clips...'
        });
      }

      const uploadPromises = processedClips
        .filter(item => item.processedBlob && !item.error)
        .map(async (item) => {
          const formData = new FormData();
          formData.append('clip_file', item.processedBlob, `clip_${item.clip.type}_${item.clip.start}s.mp4`);
          formData.append('clip_type', item.clip.type);
          formData.append('clip_start', item.clip.start.toString());
          formData.append('clip_end', item.clip.end.toString());

          const token = localStorage.getItem('token');
          await axios.post(
            `${this.apiUrl}/api/job/${jobId}/upload-clip`,
            formData,
            {
              headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'multipart/form-data'
              }
            }
          );

          // Register blob URL for cleanup
          this.memoryManager.registerBlobUrl(URL.createObjectURL(item.processedBlob));
        });

      await Promise.all(uploadPromises);

      // Clean up
      await this.clipProcessor.cleanup();
      await this.memoryManager.fullCleanup();

      if (onProgress) {
        onProgress({
          type: 'client_processing_complete',
          clipsProcessed: processedClips.filter(p => !p.error).length,
          totalClips: allClientClips.length
        });
      }

      console.log(`✅ Successfully processed ${processedClips.filter(p => !p.error).length}/${allClientClips.length} clips on client`);
    } catch (error) {
      console.error('Client processing error:', error);
      
      // Clean up on error
      await this.clipProcessor.cleanup();
      await this.memoryManager.fullCleanup();
      
      throw error;
    }
  }

  /**
   * Fallback to server-only processing
   */
  async fallbackToServerOnly(videoUrl, clips, quality, removeWatermark, videoTypes, fontStyle, fontColor) {
    const token = localStorage.getItem('token');
    
    // Use regular processing endpoint
    const response = await axios.post(
      `${this.apiUrl}/api/process`,
      {
        youtube_url: videoUrl,
        quality: quality,
        remove_watermark: removeWatermark,
        video_types: videoTypes || "both",
        font_style: fontStyle,  // Use UI value directly, no default
        font_color: fontColor,  // Use UI value directly, no default
        min_clips: null
      },
      {
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      }
    );
    
    return response.data;
  }
}

export default DistributedVideoProcessor;

