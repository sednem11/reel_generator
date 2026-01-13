import SystemResourceDetector from './SystemResourceDetector';

/**
 * Smart Task Distributor
 * Distributes video processing tasks between server and client based on RAM
 */
class TaskDistributor {
  constructor(serverUrl) {
    this.serverUrl = serverUrl;
    this.resourceDetector = new SystemResourceDetector();
  }

  /**
   * Distribute tasks based on available client RAM
   */
  async distributeTasks(videoInfo, clips) {
    // Get client RAM capabilities
    const ramInfo = await this.resourceDetector.getSafeRAMAllocation();
    const averageDuration = this.getAverageClipDuration(clips);
    const clipCapacity = await this.resourceDetector.estimateProcessableClips(
      averageDuration,
      videoInfo.quality
    );
    
    console.log(`💻 Client RAM Info:
      Total: ${(ramInfo.totalRAM / (1024**3)).toFixed(2)} GB
      Free: ${(ramInfo.freeRAM / (1024**3)).toFixed(2)} GB
      Safe Allocation (75%): ${ramInfo.safeAllocationGB.toFixed(2)} GB
      Can process: ${clipCapacity.maxClips} clips
    `);
    
    const totalMediumClips = clips.medium?.length || 0;
    const totalShortClips = clips.short?.length || 0;
    const totalClips = totalMediumClips + totalShortClips;
    
    // Calculate how many clips client can handle
    const clientMaxClips = Math.min(clipCapacity.maxClips, totalClips);
    
    // Distribute: Client takes what it can handle, server takes the rest
    // Medium clips: 40% of client capacity
    // Short clips: 60% of client capacity
    const clientMediumCount = Math.floor(clientMaxClips * 0.4);
    const clientShortCount = Math.floor(clientMaxClips * 0.6);
    
    const distribution = {
      client: {
        mediumReels: clips.medium?.slice(0, Math.min(clientMediumCount, totalMediumClips)) || [],
        shortReels: clips.short?.slice(0, Math.min(clientShortCount, totalShortClips)) || [],
        ramAllocation: ramInfo.safeAllocation,
        maxClips: clientMaxClips
      },
      server: {
        mainReel: true, // Server always does main reel
        mediumReels: clips.medium?.slice(Math.min(clientMediumCount, totalMediumClips)) || [],
        shortReels: clips.short?.slice(Math.min(clientShortCount, totalShortClips)) || [],
        reason: clientMaxClips < totalClips ? 'client_ram_limit' : 'balanced'
      }
    };
    
    return {
      distribution,
      ramInfo,
      clipCapacity
    };
  }

  getAverageClipDuration(clips) {
    const allClips = [
      ...(clips.medium || []),
      ...(clips.short || [])
    ];
    
    if (allClips.length === 0) return 60;
    
    const totalDuration = allClips.reduce((sum, clip) => {
      return sum + (clip.end - clip.start);
    }, 0);
    
    return totalDuration / allClips.length;
  }
}

export default TaskDistributor;

