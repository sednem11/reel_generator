/**
 * WebCodecs Processor
 * Uses WebCodecs API for hardware-accelerated video encoding when available
 * Falls back to ffmpeg.wasm if WebCodecs is unavailable
 */

class WebCodecsProcessor {
  constructor() {
    this.supportsWebCodecs = false;
    this.supportsGPU = false;
    this.checkSupport();
  }

  checkSupport() {
    // Check for WebCodecs API
    this.supportsWebCodecs = typeof VideoEncoder !== 'undefined' && 
                             typeof VideoDecoder !== 'undefined';
    
    // Check for WebGPU (indicates GPU support)
    this.supportsGPU = typeof navigator !== 'undefined' && 
                       'gpu' in navigator;
    
    console.log(`WebCodecs support: ${this.supportsWebCodecs}, GPU support: ${this.supportsGPU}`);
  }

  /**
   * Check if WebCodecs is available and can be used
   */
  isAvailable() {
    return this.supportsWebCodecs;
  }

  /**
   * Check if GPU acceleration is available
   */
  hasGPUAcceleration() {
    return this.supportsGPU && this.supportsWebCodecs;
  }

  /**
   * Process video clip using WebCodecs (hardware-accelerated)
   * This is a placeholder for future implementation
   * Currently falls back to ffmpeg.wasm
   */
  async processClip(videoChunk, clipInfo, quality, removeWatermark) {
    if (!this.isAvailable()) {
      throw new Error('WebCodecs not available, use ffmpeg.wasm fallback');
    }

    // TODO: Implement WebCodecs processing
    // This would use VideoDecoder/VideoEncoder APIs for hardware acceleration
    // For now, this is a placeholder that indicates WebCodecs should be used
    
    throw new Error('WebCodecs processing not yet implemented, use ffmpeg.wasm');
  }

  /**
   * Get recommended processor (WebCodecs if available, otherwise ffmpeg)
   */
  getRecommendedProcessor() {
    if (this.isAvailable() && this.hasGPUAcceleration()) {
      return 'webcodecs-gpu';
    } else if (this.isAvailable()) {
      return 'webcodecs';
    } else {
      return 'ffmpeg';
    }
  }
}

export default WebCodecsProcessor;

