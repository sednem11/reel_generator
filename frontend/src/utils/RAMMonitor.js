import SystemResourceDetector from './SystemResourceDetector';

/**
 * RAM Monitor
 * Monitors RAM usage during processing to prevent overload
 */
class RAMMonitor {
  constructor(maxRAM) {
    this.maxRAM = maxRAM;
    this.monitoring = false;
    this.resourceDetector = new SystemResourceDetector();
  }

  startMonitoring(callback) {
    this.monitoring = true;
    
    const monitor = async () => {
      if (!this.monitoring) return;
      
      const memory = await this.resourceDetector.getMemoryInfo();
      const usagePercent = (memory.used / memory.total) * 100;
      const freePercent = (memory.free / memory.total) * 100;
      
      callback({
        used: memory.used,
        free: memory.free,
        total: memory.total,
        usagePercent,
        freePercent,
        isSafe: memory.free >= this.maxRAM * 0.25 // Keep 25% buffer
      });
      
      if (this.monitoring) {
        setTimeout(monitor, 1000); // Check every second
      }
    };
    
    monitor();
  }

  stopMonitoring() {
    this.monitoring = false;
  }

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

export default RAMMonitor;

