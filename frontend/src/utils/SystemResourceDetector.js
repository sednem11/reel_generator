/**
 * System Resource Detector
 * Detects available RAM and calculates safe allocation (75% of free RAM)
 */
class SystemResourceDetector {
  /**
   * Get available system memory information
   * Returns: { total: bytes, used: bytes, free: bytes, percentage: number }
   */
  async getMemoryInfo() {
    // Check if Performance API is available (Chrome, Edge, Opera)
    if ('memory' in performance) {
      const memory = performance.memory;
      return {
        total: memory.jsHeapSizeLimit,
        used: memory.usedJSHeapSize,
        free: memory.jsHeapSizeLimit - memory.usedJSHeapSize,
        percentage: ((memory.jsHeapSizeLimit - memory.usedJSHeapSize) / memory.jsHeapSizeLimit) * 100,
        source: 'performance.memory'
      };
    }
    
    // Fallback: Use deviceMemory API (if available)
    if ('deviceMemory' in navigator) {
      const deviceMemoryGB = navigator.deviceMemory; // in GB
      const totalBytes = deviceMemoryGB * 1024 * 1024 * 1024;
      
      // Estimate used memory (rough approximation)
      const estimatedUsed = performance.memory?.usedJSHeapSize || (totalBytes * 0.3);
      const freeBytes = totalBytes - estimatedUsed;
      
      return {
        total: totalBytes,
        used: estimatedUsed,
        free: freeBytes,
        percentage: (freeBytes / totalBytes) * 100,
        source: 'navigator.deviceMemory'
      };
    }
    
    // Conservative fallback: Assume 2GB available
    const fallbackTotal = 2 * 1024 * 1024 * 1024; // 2GB
    const fallbackUsed = fallbackTotal * 0.5;
    const fallbackFree = fallbackTotal - fallbackUsed;
    
    return {
      total: fallbackTotal,
      used: fallbackUsed,
      free: fallbackFree,
      percentage: 50,
      source: 'fallback'
    };
  }

  /**
   * Get safe RAM allocation (75% of free RAM)
   */
  async getSafeRAMAllocation() {
    const memory = await this.getMemoryInfo();
    const safeAllocation = memory.free * 0.75; // 75% of free RAM
    
    return {
      totalRAM: memory.total,
      freeRAM: memory.free,
      safeAllocation: safeAllocation,
      safeAllocationMB: safeAllocation / (1024 * 1024),
      safeAllocationGB: safeAllocation / (1024 * 1024 * 1024),
      percentage: memory.percentage,
      source: memory.source
    };
  }

  /**
   * Estimate how many video clips can be processed with available RAM
   * @param {number} clipDurationSeconds - Average duration of a clip
   * @param {string} quality - Video quality (hd, full_hd, normal)
   */
  async estimateProcessableClips(clipDurationSeconds = 60, quality = 'hd') {
    const ramInfo = await this.getSafeRAMAllocation();
    
    // RAM requirements per clip (rough estimates in MB)
    const ramPerClipMB = {
      'normal': 200,   // ~200MB per 60s clip at 480p
      'hd': 400,       // ~400MB per 60s clip at 720p
      'full_hd': 800   // ~800MB per 60s clip at 1080p
    };
    
    const ramPerClip = ramPerClipMB[quality] || ramPerClipMB['hd'];
    const availableMB = ramInfo.safeAllocationMB;
    
    // Calculate max clips (with 20% buffer for overhead)
    const maxClips = Math.floor((availableMB * 0.8) / ramPerClip);
    
    return {
      maxClips: Math.max(1, maxClips), // At least 1 clip
      ramPerClipMB: ramPerClip,
      availableRAMMB: availableMB,
      estimatedRAMUsageMB: maxClips * ramPerClip,
      ramInfo: ramInfo
    };
  }

  /**
   * Get current RAM usage
   */
  async getCurrentRAMUsage() {
    if ('memory' in performance) {
      return performance.memory.usedJSHeapSize;
    }
    return 0;
  }
}

export default SystemResourceDetector;

