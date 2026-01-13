/**
 * Subtitle Renderer
 * Renders subtitles on video frames using Canvas API
 */

class SubtitleRenderer {
  constructor() {
    this.canvas = null;
    this.ctx = null;
  }

  /**
   * Initialize canvas for subtitle rendering
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
   * Render subtitle text on a frame
   * @param {ImageData|HTMLImageElement|HTMLVideoElement} frame - Video frame
   * @param {string} text - Subtitle text
   * @param {number} x - X position (default: center)
   * @param {number} y - Y position (default: bottom)
   * @param {Object} style - Style options
   * @returns {ImageData} Frame with subtitle rendered
   */
  renderSubtitle(frame, text, x = null, y = null, style = {}) {
    if (!this.ctx) {
      throw new Error('Canvas not initialized. Call initCanvas() first.');
    }

    const width = frame.width || frame.videoWidth || this.canvas.width;
    const height = frame.height || frame.videoHeight || this.canvas.height;

    // Set canvas size if needed
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    // Draw original frame
    if (frame instanceof ImageData) {
      this.ctx.putImageData(frame, 0, 0);
    } else {
      this.ctx.drawImage(frame, 0, 0, width, height);
    }

    // Default position (bottom center)
    const subtitleX = x !== null ? x : width / 2;
    const subtitleY = y !== null ? y : height - 80;

    // Subtitle style
    const fontSize = style.fontSize || 32;
    const fontFamily = style.fontFamily || 'Arial, sans-serif';
    const fontColor = style.color || '#FFFFFF';
    const strokeColor = style.strokeColor || '#000000';
    const strokeWidth = style.strokeWidth || 3;
    const backgroundColor = style.backgroundColor || 'rgba(0, 0, 0, 0.5)';
    const padding = style.padding || 10;

    // Configure text style
    this.ctx.font = `bold ${fontSize}px ${fontFamily}`;
    this.ctx.textAlign = 'center';
    this.ctx.textBaseline = 'middle';

    // Measure text
    const metrics = this.ctx.measureText(text);
    const textWidth = metrics.width;
    const textHeight = fontSize;

    // Draw background box
    const boxX = subtitleX - textWidth / 2 - padding;
    const boxY = subtitleY - textHeight / 2 - padding;
    const boxWidth = textWidth + padding * 2;
    const boxHeight = textHeight + padding * 2;

    this.ctx.fillStyle = backgroundColor;
    this.ctx.fillRect(boxX, boxY, boxWidth, boxHeight);

    // Draw text with stroke (outline)
    this.ctx.strokeStyle = strokeColor;
    this.ctx.lineWidth = strokeWidth;
    this.ctx.strokeText(text, subtitleX, subtitleY);

    // Draw text fill
    this.ctx.fillStyle = fontColor;
    this.ctx.fillText(text, subtitleX, subtitleY);

    // Return as ImageData
    return this.ctx.getImageData(0, 0, width, height);
  }

  /**
   * Render word-by-word subtitles with timing
   * @param {ImageData} frame - Video frame
   * @param {Array} words - Array of {text, start, end} objects
   * @param {number} currentTime - Current video time
   * @returns {ImageData} Frame with subtitles
   */
  renderWordByWord(frame, words, currentTime) {
    // Find words that should be displayed at current time
    const activeWords = words.filter(word => 
      currentTime >= word.start && currentTime <= word.end
    );

    if (activeWords.length === 0) {
      return frame;
    }

    // Combine active words into text
    const text = activeWords.map(w => w.text).join(' ');

    // Render subtitle
    return this.renderSubtitle(frame, text);
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

export default SubtitleRenderer;

