import React, { useState, useEffect } from 'react';
import './VideoStylingModal.css';

const FONT_STYLES = [
  { value: 'bold', label: 'Bold', description: 'Default' },
  { value: 'funny', label: 'Funny', description: 'Playful' },
  { value: 'scary', label: 'Scary', description: 'Horror' },
  { value: 'movie', label: 'Movie', description: 'Cinematic' },
  { value: 'peptalk', label: 'Peptalk', description: 'Motivational' },
  { value: 'elegant', label: 'Elegant', description: 'Classy' },
  { value: 'modern', label: 'Modern', description: 'Tech' },
];

const FONT_COLORS = [
  { value: 'white_red', label: 'White/Red', gradient: 'linear-gradient(135deg, white 50%, red 50%)' },
  { value: 'white_green', label: 'White/Green', gradient: 'linear-gradient(135deg, white 50%, green 50%)' },
  { value: 'white_blue', label: 'White/Blue', gradient: 'linear-gradient(135deg, white 50%, blue 50%)' },
  { value: 'white_black', label: 'White/Black', gradient: 'linear-gradient(135deg, white 50%, black 50%)' },
  { value: 'rainbow', label: 'Rainbow', gradient: 'linear-gradient(90deg, red, orange, yellow, green, blue, indigo, violet)' },
  { value: 'white_yellow', label: 'White/Yellow', gradient: 'linear-gradient(135deg, white 50%, yellow 50%)' },
  { value: 'white_purple', label: 'White/Purple', gradient: 'linear-gradient(135deg, white 50%, purple 50%)' },
];

function VideoStylingModal({ isOpen, onClose, fontStyle, fontColor, onFontStyleChange, onFontColorChange, disabled }) {
  // Animation state for word-by-word subtitle display
  const [currentWords, setCurrentWords] = useState([]);
  
  // All words in sequence (matching video behavior: max 5 words per line, then next line, then clear)
  const allWords = ['This', 'is', 'how', 'your', 'subtitles', 'will', 'look', 'in', 'videos'];
  const maxWordsPerLine = 5;

  // Animation effect - word-by-word display (looping like a GIF, matching video behavior)
  useEffect(() => {
    if (!isOpen) {
      setCurrentWords([]);
      return;
    }

    let wordIndex = 0;
    let line1Words = [];
    let line2Words = [];
    let timeoutId;
    
    const showNextWord = () => {
      if (wordIndex < allWords.length) {
        const word = allWords[wordIndex];
        
        // Determine which line this word goes to
        if (line1Words.length < maxWordsPerLine) {
          // Add to line 1
          line1Words.push(word);
        } else if (line2Words.length < maxWordsPerLine) {
          // Add to line 2
          line2Words.push(word);
        } else {
          // Both lines full, clear and restart
          line1Words = [word];
          line2Words = [];
        }
        
        // Build current words array
        const words = [];
        
        // Add line 1 words
        line1Words.forEach((w, idx) => {
          words.push({
            text: w,
            line: 0,
            index: idx,
            isLast: idx === line1Words.length - 1 && line2Words.length === 0 // Last word if it's the last on line 1 and no line 2
          });
        });
        
        // Add line 2 words
        line2Words.forEach((w, idx) => {
          words.push({
            text: w,
            line: 1,
            index: idx,
            isLast: idx === line2Words.length - 1 // Last word if it's the last on line 2
          });
        });
        
        setCurrentWords([...words]);
        wordIndex++;
        timeoutId = setTimeout(showNextWord, 600); // Speed of word appearance
      } else {
        // Reset animation - clear and restart
        line1Words = [];
        line2Words = [];
        setCurrentWords([]);
        wordIndex = 0;
        timeoutId = setTimeout(showNextWord, 1000);
      }
    };

    // Start animation
    timeoutId = setTimeout(showNextWord, 500);

    return () => {
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [isOpen, fontStyle, fontColor, allWords]); // Restart animation when style/color changes

  // Early return after hooks
  if (!isOpen) return null;

  const currentFontStyle = FONT_STYLES.find(f => f.value === fontStyle) || FONT_STYLES[0];
  const currentFontColor = FONT_COLORS.find(f => f.value === fontColor) || FONT_COLORS[0];

  // Get preview text style based on font style
  const getPreviewFontStyle = () => {
    const styles = {
      bold: { fontWeight: '700', fontFamily: 'Arial Black, Impact, "Liberation Sans Bold", sans-serif' },
      funny: { fontWeight: '600', fontFamily: 'Comic Sans MS, "Comic Sans", cursive', fontStyle: 'italic' },
      scary: { fontWeight: '700', fontFamily: 'Impact, "Arial Black", "Liberation Sans Bold", sans-serif', letterSpacing: '2px' },
      movie: { fontWeight: '400', fontFamily: 'Georgia, "Times New Roman", serif', letterSpacing: '1px', fontStyle: 'italic' },
      peptalk: { fontWeight: '700', fontFamily: 'Arial, "Helvetica Neue", sans-serif', textTransform: 'uppercase', letterSpacing: '1px' },
      elegant: { fontWeight: '400', fontFamily: '"Times New Roman", Times, serif', fontStyle: 'italic', letterSpacing: '0.5px' },
      modern: { fontWeight: '600', fontFamily: '"Courier New", Courier, monospace', letterSpacing: '1.5px' },
    };
    return styles[fontStyle] || styles.bold;
  };

  // Get preview text color based on font color (for non-highlighted words)
  const getPreviewTextColor = (wordIndex = 0) => {
    if (fontColor === 'rainbow') {
      // Rainbow colors: red, orange, yellow, green, blue, indigo, violet
      const rainbowColors = ['#FF0000', '#FF8000', '#FFFF00', '#00FF00', '#0080FF', '#4B0082', '#8B00FF'];
      const colorIndex = wordIndex % rainbowColors.length;
      return { color: rainbowColors[colorIndex] };
    }
    
    const colorMap = {
      'white_red': { color: '#FFFFFF' },
      'white_green': { color: '#FFFFFF' },
      'white_blue': { color: '#FFFFFF' },
      'white_black': { color: '#FFFFFF' },
      'white_yellow': { color: '#FFFFFF' },
      'white_purple': { color: '#FFFFFF' },
    };
    return colorMap[fontColor] || { color: '#FFFFFF' };
  };

  // Get highlighted word color based on font color selection
  const getHighlightedWordColor = (wordIndex = 0) => {
    if (fontColor === 'rainbow') {
      // Rainbow colors: red, orange, yellow, green, blue, indigo, violet
      const rainbowColors = ['#FF0000', '#FF8000', '#FFFF00', '#00FF00', '#0080FF', '#4B0082', '#8B00FF'];
      const colorIndex = wordIndex % rainbowColors.length;
      return rainbowColors[colorIndex];
    }
    
    const colorMap = {
      'white_red': '#FF0000',
      'white_green': '#00FF00',
      'white_blue': '#0080FF',
      'white_black': '#000000',
      'white_yellow': '#FFFF00',
      'white_purple': '#8000FF',
    };
    return colorMap[fontColor] || '#FF0000';
  };

  // Get highlighted word style
  const getHighlightedWordStyle = (wordIndex = 0) => {
    const color = getHighlightedWordColor(wordIndex);
    const style = { color };
    
    // Add white outline for black text
    if (fontColor === 'white_black' && color === '#000000') {
      style.WebkitTextStroke = '2px white';
      style.textStroke = '2px white';
    }
    
    return style;
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="video-styling-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Video Styling</h2>
          <button className="close-button" onClick={onClose}>&times;</button>
        </div>

        <div className="modal-content">
          {/* Font Preview Board */}
          <div className="preview-section">
            <h3>Subtitle Preview</h3>
            <div className="preview-board">
              <div className="preview-video-player">
                <div className="preview-video-frame">
                  {/* Video-like background with some visual elements */}
                  <div className="preview-video-content">
                    <div className="preview-video-pattern"></div>
                    <div className="preview-video-overlay"></div>
                    
                    {/* Subtitles overlaid on video - animated word-by-word (matching video behavior) */}
                    <div className="preview-subtitle-container">
                      {/* Line 1 (top) */}
                      <div className="preview-subtitle-line">
                        {currentWords
                          .filter(w => w.line === 0)
                          .map((wordData, idx) => {
                            const isLast = wordData.isLast;
                            // Calculate global word index for rainbow color cycling
                            const globalWordIndex = wordData.index;
                            const wordColor = getPreviewTextColor(globalWordIndex);
                            
                            return (
                              <span
                                key={`0-${wordData.index}`}
                                className={`preview-word preview-word-animate ${
                                  isLast ? 'preview-word-highlighted' : ''
                                }`}
                                style={{
                                  ...getPreviewFontStyle(),
                                  ...(isLast ? getHighlightedWordStyle(globalWordIndex) : wordColor),
                                  fontSize: isLast ? '38px' : '32px',
                                }}
                              >
                                {wordData.text}
                              </span>
                            );
                          })}
                      </div>
                      
                      {/* Line 2 (bottom) */}
                      {currentWords.some(w => w.line === 1) && (
                        <div className="preview-subtitle-line">
                          {currentWords
                            .filter(w => w.line === 1)
                            .map((wordData, idx) => {
                              const isLast = wordData.isLast;
                              // Calculate global word index for rainbow color cycling (continues from line 1)
                              const line1WordCount = currentWords.filter(w => w.line === 0).length;
                              const globalWordIndex = line1WordCount + wordData.index;
                              const wordColor = getPreviewTextColor(globalWordIndex);
                              
                              return (
                                <span
                                  key={`1-${wordData.index}`}
                                  className={`preview-word preview-word-animate ${
                                    isLast ? 'preview-word-highlighted' : ''
                                  }`}
                                  style={{
                                    ...getPreviewFontStyle(),
                                    ...(isLast ? getHighlightedWordStyle(globalWordIndex) : wordColor),
                                    fontSize: isLast ? '38px' : '32px',
                                  }}
                                >
                                  {wordData.text}
                                </span>
                              );
                            })}
                        </div>
                      )}
                    </div>
                  </div>
                  
                  {/* Video player controls overlay */}
                  <div className="preview-video-controls">
                    <div className="preview-play-button">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="white" opacity="0.8">
                        <path d="M8 5v14l11-7z"/>
                      </svg>
                    </div>
                  </div>
                  
                  {/* Video time indicator */}
                  <div className="preview-video-time">
                    0:15
                  </div>
                </div>
              </div>
              <div className="preview-info">
                <span className="preview-label">Style:</span> {currentFontStyle.label} ({currentFontStyle.description})
              </div>
              <div className="preview-info">
                <span className="preview-label">Color:</span> {currentFontColor.label} (last word highlighted in {fontColor === 'white_red' ? 'red' : fontColor === 'white_green' ? 'green' : fontColor === 'white_blue' ? 'blue' : fontColor === 'white_yellow' ? 'yellow' : fontColor === 'white_purple' ? 'purple' : fontColor === 'white_black' ? 'black' : fontColor === 'rainbow' ? 'rainbow' : 'red'})
              </div>
            </div>
          </div>

          {/* Font Style Selection */}
          <div className="styling-section">
            <h3>Font Style</h3>
            <div className="font-style-grid">
              {FONT_STYLES.map((style) => (
                <button
                  key={style.value}
                  type="button"
                  className={`font-style-box ${fontStyle === style.value ? 'active' : ''}`}
                  onClick={() => onFontStyleChange(style.value)}
                  disabled={disabled}
                >
                  <div className="font-style-label">{style.label}</div>
                  <div className="font-style-description">{style.description}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Font Color Selection */}
          <div className="styling-section">
            <h3>Font Color</h3>
            <div className="font-color-grid">
              {FONT_COLORS.map((color) => (
                <button
                  key={color.value}
                  type="button"
                  className={`font-color-box ${fontColor === color.value ? 'active' : ''}`}
                  onClick={() => onFontColorChange(color.value)}
                  disabled={disabled}
                  style={{ background: color.gradient }}
                  title={color.label}
                >
                  <span className="font-color-label">{color.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="modal-footer">
            <button className="done-button" onClick={onClose}>
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default VideoStylingModal;

