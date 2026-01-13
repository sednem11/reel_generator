/**
 * Watermark Remover
 * Removes watermarks from video clips using canvas-based image processing
 */

class WatermarkRemover {
  constructor() {
    this.canvas = null;
    this.ctx = null;
  }

  /**
   * Initialize canvas for watermark removal
   * @param {number} width - Canvas width
   * @param {number} height - Canvas height
   */
  initCanvas(width, height) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = width;
    this.canvas.height = height;
    this.ctx = this.canvas.getContext('2d');
    return this.canvas;
  }

  /**
   * Remove watermark using blur/inpainting technique
   * @param {ImageData} frame - Video frame
   * @param {Object} watermarkRegion - {x, y, width, height} of watermark area
   * @returns {ImageData} Frame with watermark removed
   */
  removeWatermark(frame, watermarkRegion = null) {
    if (!this.ctx) {
      throw new Error('Canvas not initialized. Call initCanvas() first.');
    }

    const width = frame.width;
    const height = frame.height;

    // Set canvas size
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    // Draw original frame
    this.ctx.putImageData(frame, 0, 0);

    // If watermark region is specified, blur that area
    if (watermarkRegion) {
      const { x, y, width: w, height: h } = watermarkRegion;
      
      // Extract watermark region
      const watermarkData = this.ctx.getImageData(x, y, w, h);
      
      // Apply blur to watermark region
      // Simple box blur implementation
      this.applyBoxBlur(watermarkData, 10);
      
      // Put blurred region back
      this.ctx.putImageData(watermarkData, x, y);
    } else {
      // Default: blur bottom-right corner (common watermark position)
      const watermarkSize = Math.min(width, height) * 0.15;
      const x = width - watermarkSize - 20;
      const y = height - watermarkSize - 20;
      
      const watermarkData = this.ctx.getImageData(x, y, watermarkSize, watermarkSize);
      this.applyBoxBlur(watermarkData, 15);
      this.ctx.putImageData(watermarkData, x, y);
    }

    return this.ctx.getImageData(0, 0, width, height);
  }

  /**
   * Apply box blur to image data
   * @param {ImageData} imageData - Image data to blur
   * @param {number} radius - Blur radius
   */
  applyBoxBlur(imageData, radius) {
    const data = imageData.data;
    const width = imageData.width;
    const height = imageData.height;
    const newData = new Uint8ClampedArray(data);

    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        let r = 0, g = 0, b = 0, a = 0;
        let count = 0;

        // Average pixels in radius
        for (let dy = -radius; dy <= radius; dy++) {
          for (let dx = -radius; dx <= radius; dx++) {
            const nx = x + dx;
            const ny = y + dy;

            if (nx >= 0 && nx < width && ny >= 0 && ny < height) {
              const idx = (ny * width + nx) * 4;
              r += data[idx];
              g += data[idx + 1];
              b += data[idx + 2];
              a += data[idx + 3];
              count++;
            }
          }
        }

        const idx = (y * width + x) * 4;
        newData[idx] = r / count;
        newData[idx + 1] = g / count;
        newData[idx + 2] = b / count;
        newData[idx + 3] = a / count;
      }
    }

    // Copy blurred data back
    for (let i = 0; i < data.length; i++) {
      data[i] = newData[i];
    }
  }

  /**
   * Clean up canvas resources
   */
  cleanup() {
    if (this.canvas) {
      this.canvas.width = 0;
      this.canvas.height = 0;
      this.canvas = null;
      this.ctx = null;
    }
  }
}

export default WatermarkRemover;

