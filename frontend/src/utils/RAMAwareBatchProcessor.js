/**
 * RAM-Aware Batch Processor
 * Processes clips in batches to stay within RAM limits (75% of free RAM)
 */

import SystemResourceDetector from './SystemResourceDetector';
import MemoryManager from './MemoryManager';

class RAMAwareBatchProcessor {
  constructor() {
    this.resourceDetector = new SystemResourceDetector();
    this.memoryManager = new MemoryManager();
    this.maxRAM = null;
  }

  /**
   * Initialize with RAM limits
   */
  async initialize() {
    const ramInfo = await this.resourceDetector.getSafeRAMAllocation();
    this.maxRAM = ramInfo.safeAllocation;
    return ramInfo;
  }

  /**
   * Calculate batch size based on available RAM
   * @param {string} quality - Video quality
   * @returns {number} Number of clips per batch
   */
  calculateBatchSize(quality = 'hd') {
    if (!this.maxRAM) {
      throw new Error('Processor not initialized. Call initialize() first.');
    }

    // RAM requirements per clip (in bytes)
    const ramPerClip = {
      'normal': 200 * 1024 * 1024,   // 200MB
      'hd': 400 * 1024 * 1024,       // 400MB
      'full_hd': 800 * 1024 * 1024   // 800MB
    };

    const ramPerClipBytes = ramPerClip[quality] || ramPerClip['hd'];
    
    // Use 80% of max RAM for processing (keep 20% buffer)
    const availableRAM = this.maxRAM * 0.8;
    
    // Calculate batch size
    const batchSize = Math.max(1, Math.floor(availableRAM / ramPerClipBytes));
    
    return batchSize;
  }

  /**
   * Create batches from clips array
   * @param {Array} clips - Array of clips to process
   * @param {number} batchSize - Number of clips per batch
   * @returns {Array} Array of batches
   */
  createBatches(clips, batchSize) {
    const batches = [];
    for (let i = 0; i < clips.length; i += batchSize) {
      batches.push(clips.slice(i, i + batchSize));
    }
    return batches;
  }

  /**
   * Process clips in RAM-safe batches
   * @param {Array} clips - Clips to process
   * @param {Function} processFn - Function to process a single clip
   * @param {string} quality - Video quality
   * @param {Function} onProgress - Progress callback
   * @returns {Promise<Array>} Processed clips
   */
  async processInBatches(clips, processFn, quality = 'hd', onProgress = null) {
    if (!this.maxRAM) {
      await this.initialize();
    }

    const batchSize = this.calculateBatchSize(quality);
    const batches = this.createBatches(clips, batchSize);
    const results = [];

    console.log(`Processing ${clips.length} clips in ${batches.length} batches (${batchSize} clips per batch)`);

    for (let batchIndex = 0; batchIndex < batches.length; batchIndex++) {
      const batch = batches[batchIndex];

      if (onProgress) {
        onProgress({
          type: 'batch_start',
          batchIndex: batchIndex + 1,
          totalBatches: batches.length,
          batchSize: batch.length
        });
      }

      // Check RAM before processing batch
      const memory = await this.resourceDetector.getMemoryInfo();
      const freeRAM = memory.free;

      // Estimate RAM needed for this batch
      const ramPerClip = {
        'normal': 200 * 1024 * 1024,
        'hd': 400 * 1024 * 1024,
        'full_hd': 800 * 1024 * 1024
      };
      const estimatedRAMNeeded = batch.length * (ramPerClip[quality] || ramPerClip['hd']);

      // Wait if not enough free RAM
      if (freeRAM < estimatedRAMNeeded * 1.2) {
        console.log(`⏳ Waiting for RAM to free up... (${(freeRAM / (1024**3)).toFixed(2)} GB free, need ${(estimatedRAMNeeded * 1.2 / (1024**3)).toFixed(2)} GB)`);
        
        if (onProgress) {
          onProgress({
            type: 'waiting_ram',
            freeRAM: freeRAM,
            neededRAM: estimatedRAMNeeded * 1.2
          });
        }

        await this.waitForRAM(estimatedRAMNeeded * 1.2);
      }

      // Process batch
      const batchResults = [];
      for (let i = 0; i < batch.length; i++) {
        const clip = batch[i];
        
        try {
          if (onProgress) {
            onProgress({
              type: 'clip_processing',
              batchIndex: batchIndex + 1,
              clipIndex: i + 1,
              totalClips: batch.length,
              clip: clip
            });
          }

          const result = await processFn(clip);
          batchResults.push(result);
        } catch (error) {
          console.error(`Error processing clip in batch ${batchIndex + 1}:`, error);
          batchResults.push({ error: error.message, clip: clip });
        }
      }

      results.push(...batchResults);

      // Clean up after batch
      await this.memoryManager.forceGarbageCollection();
      
      // Small delay to allow GC
      await new Promise(resolve => setTimeout(resolve, 500));

      if (onProgress) {
        onProgress({
          type: 'batch_complete',
          batchIndex: batchIndex + 1,
          totalBatches: batches.length,
          results: batchResults.length
        });
      }
    }

    return results;
  }

  /**
   * Wait until enough RAM is available
   * @param {number} targetFreeRAM - Target free RAM in bytes
   */
  async waitForRAM(targetFreeRAM) {
    return new Promise((resolve) => {
      const checkInterval = setInterval(async () => {
        const memory = await this.resourceDetector.getMemoryInfo();
        if (memory.free >= targetFreeRAM) {
          clearInterval(checkInterval);
          resolve();
        }
      }, 1000); // Check every second
    });
  }
}

export default RAMAwareBatchProcessor;

