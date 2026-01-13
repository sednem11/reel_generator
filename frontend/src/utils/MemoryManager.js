/**
 * Memory Manager
 * Manages memory cleanup and monitoring for video processing
 */

import SystemResourceDetector from './SystemResourceDetector';

class MemoryManager {
  constructor() {
    this.resourceDetector = new SystemResourceDetector();
    this.blobUrls = new Set();
    this.cleanupCallbacks = [];
  }

  /**
   * Register a blob URL for cleanup
   * @param {string} url - Blob URL to track
   */
  registerBlobUrl(url) {
    if (url && url.startsWith('blob:')) {
      this.blobUrls.add(url);
    }
  }

  /**
   * Revoke a blob URL
   * @param {string} url - Blob URL to revoke
   */
  revokeBlobUrl(url) {
    if (url && this.blobUrls.has(url)) {
      try {
        URL.revokeObjectURL(url);
        this.blobUrls.delete(url);
      } catch (error) {
        console.warn('Error revoking blob URL:', error);
      }
    }
  }

  /**
   * Revoke all tracked blob URLs
   */
  revokeAllBlobUrls() {
    this.blobUrls.forEach(url => {
      try {
        URL.revokeObjectURL(url);
      } catch (error) {
        console.warn('Error revoking blob URL:', error);
      }
    });
    this.blobUrls.clear();
  }

  /**
   * Register a cleanup callback
   * @param {Function} callback - Cleanup function to call
   */
  registerCleanup(callback) {
    this.cleanupCallbacks.push(callback);
  }

  /**
   * Execute all cleanup callbacks
   */
  async executeCleanup() {
    for (const callback of this.cleanupCallbacks) {
      try {
        await callback();
      } catch (error) {
        console.warn('Cleanup callback error:', error);
      }
    }
    this.cleanupCallbacks = [];
  }

  /**
   * Force garbage collection if available
   */
  async forceGarbageCollection() {
    // Chrome/Edge only
    if (global.gc && typeof global.gc === 'function') {
      try {
        global.gc();
        console.log('Garbage collection triggered');
      } catch (error) {
        console.warn('Garbage collection failed:', error);
      }
    }
  }

  /**
   * Get current memory usage
   */
  async getMemoryUsage() {
    return await this.resourceDetector.getMemoryInfo();
  }

  /**
   * Check if memory usage is safe
   * @param {number} threshold - Threshold percentage (default: 75)
   */
  async isMemorySafe(threshold = 75) {
    const memory = await this.getMemoryUsage();
    const usagePercent = (memory.used / memory.total) * 100;
    return usagePercent < threshold;
  }

  /**
   * Full cleanup: revoke URLs, execute callbacks, force GC
   */
  async fullCleanup() {
    this.revokeAllBlobUrls();
    await this.executeCleanup();
    await this.forceGarbageCollection();
  }
}

export default MemoryManager;

