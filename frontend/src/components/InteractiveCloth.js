import React, { useEffect, useRef, useImperativeHandle, forwardRef } from 'react';
import './InteractiveCloth.css';

const InteractiveCloth = forwardRef((props, ref) => {
  const canvasRef = useRef(null);
  const mouseRef = useRef({ x: 0, y: 0 });
  const lastMouseRef = useRef({ x: 0, y: 0 });
  const ripplesRef = useRef([]);
  const animationFrameRef = useRef(null);
  const lastRippleTimeRef = useRef(0);
  const mouseSpeedRef = useRef(0); // Track mouse speed
  const lastMouseMoveTimeRef = useRef(0);
  const cardBoundsRef = useRef([]); // Cache card bounds
  const lastCardBoundsUpdateRef = useRef(0);
  const gridSizeRef = useRef(6); // Store grid size for external access
  const colsRef = useRef(0);
  const rowsRef = useRef(0);

  // Function to create a wave at a specific position (exposed via ref)
  const createWaveAtPosition = (x, y, moveDirX = 0, moveDirY = 0) => {
    // Use ripplesRef directly since it's accessible
    if (!ripplesRef.current) return;
    
    const currentTime = Date.now();
    const gridSize = gridSizeRef.current;
    const cols = colsRef.current;
    const rows = rowsRef.current;
    
    if (cols === 0 || rows === 0) return; // Not initialized yet
    
    const gridX = Math.floor(x / gridSize);
    const gridY = Math.floor(y / gridSize);
    
    // Check bounds
    if (gridX >= 0 && gridX < cols && gridY >= 0 && gridY < rows) {
      // Calculate distance from button center to screen edges in grid squares
      // Calculate distances to each edge in grid squares
      const distToLeft = gridX;
      const distToRight = cols - gridX;
      const distToTop = gridY;
      const distToBottom = rows - gridY;
      
      // Find maximum distance to edge in grid squares
      const maxDistToEdge = Math.max(distToLeft, distToRight, distToTop, distToBottom);
      
      // Create ripples in all directions from the button center
      // Use 32 directions (every ~11 degrees) to cover 360 degrees
      const directions = 32;
      
      for (let i = 0; i < directions; i++) {
        const angle = (i / directions) * Math.PI * 2;
        const dirX = Math.cos(angle);
        const dirY = Math.sin(angle);
        
        // Create a ripple at the button center that will propagate outward
        ripplesRef.current.push({
          gridX: gridX,
          gridY: gridY,
          distance: 0, // Start at center
          intensity: 1.0,
          startTime: currentTime,
          moveDirX: dirX,
          moveDirY: dirY,
          maxDistance: maxDistToEdge * 1.2 // Travel 20% beyond edge to ensure full coverage
        });
      }
      
      // Also create additional ripples in a small circle around the button for a stronger effect
      const innerCircleRipples = 16;
      const innerRadius = 3; // Small radius around button
      
      for (let i = 0; i < innerCircleRipples; i++) {
        const angle = (i / innerCircleRipples) * Math.PI * 2;
        const dirX = Math.cos(angle);
        const dirY = Math.sin(angle);
        const offsetX = Math.cos(angle) * innerRadius;
        const offsetY = Math.sin(angle) * innerRadius;
        
        const finalGridX = gridX + Math.round(offsetX);
        const finalGridY = gridY + Math.round(offsetY);
        
        if (finalGridX >= 0 && finalGridX < cols && 
            finalGridY >= 0 && finalGridY < rows) {
          ripplesRef.current.push({
            gridX: finalGridX,
            gridY: finalGridY,
            distance: 0,
            intensity: 1.0,
            startTime: currentTime,
            moveDirX: dirX,
            moveDirY: dirY,
            maxDistance: maxDistToEdge * 1.2
          });
        }
      }
    }
  };

  // Expose function via ref (must be at component level, not inside useEffect)
  useImperativeHandle(ref, () => ({
    createWave: createWaveAtPosition
  }));

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const width = window.innerWidth;
    const height = window.innerHeight;
    
    canvas.width = width;
    canvas.height = height;

    // Grid settings
    const gridSize = 6; // Size of each square (5x smaller: 30/5 = 6)
    const cols = Math.ceil(width / gridSize) + 1;
    const rows = Math.ceil(height / gridSize) + 1;
    
    // Store in refs for external access
    gridSizeRef.current = gridSize;
    colsRef.current = cols;
    rowsRef.current = rows;
    
    // Grid state - stores intensity, Z-height, and age for each cell
    const grid = [];
    for (let y = 0; y < rows; y++) {
      grid[y] = [];
      for (let x = 0; x < cols; x++) {
        grid[y][x] = { intensity: 0, zHeight: 0, age: 0, rippleTime: 0 }; // Object with intensity, Z-height, age, and ripple timestamp
      }
    }

    // Ripple settings
    const rippleDistance = 18.75; // Number of squares ripples travel (0.75x of 25)
    // const rippleSpacing = 5; // Minimum distance between new ripples (increased to reduce ripple count) - unused
    const rippleSpeed = 0.4; // How fast ripples propagate (faster: 0.15 -> 0.4)
    const maxRipples = 60; // Maximum number of active ripples (reduced for performance)

    // Get bounding boxes of floating cards for collision detection
    const getCardBounds = () => {
      const cards = [];
      const generatorCard = document.querySelector('.generator-card');
      const loginCard = document.querySelector('.login-card');
      
      if (generatorCard) {
        const rect = generatorCard.getBoundingClientRect();
        cards.push({
          x: rect.left,
          y: rect.top,
          width: rect.width,
          height: rect.height
        });
      }
      
      if (loginCard) {
        const rect = loginCard.getBoundingClientRect();
        cards.push({
          x: rect.left,
          y: rect.top,
          width: rect.width,
          height: rect.height
        });
      }
      
      return cards;
    };

    // Check if a point is inside any card and get distance to nearest edge
    const getCardCollisionInfo = (x, y, cards) => {
      for (const card of cards) {
        const isInside = x >= card.x && x <= card.x + card.width &&
                         y >= card.y && y <= card.y + card.height;
        
        if (isInside) {
          // Calculate distance to nearest edge
          const distToLeft = x - card.x;
          const distToRight = card.x + card.width - x;
          const distToTop = y - card.y;
          const distToBottom = card.y + card.height - y;
          
          const minDist = Math.min(distToLeft, distToRight, distToTop, distToBottom);
          
          // Determine which edge is closest
          let edgeX = 0, edgeY = 0;
          if (minDist === distToLeft) {
            edgeX = card.x;
            edgeY = y;
          } else if (minDist === distToRight) {
            edgeX = card.x + card.width;
            edgeY = y;
          } else if (minDist === distToTop) {
            edgeX = x;
            edgeY = card.y;
          } else {
            edgeX = x;
            edgeY = card.y + card.height;
          }
          
          return {
            isInside: true,
            distanceToEdge: minDist,
            edgeX: edgeX,
            edgeY: edgeY,
            card: card
          };
        }
      }
      return { isInside: false };
    };
    
    // Check if a point is near a card edge (for wave buildup effect)
    const getDistanceToCardEdge = (x, y, cards) => {
      let minDistance = Infinity;
      for (const card of cards) {
        // Check distance to each edge
        const distToLeft = Math.abs(x - card.x);
        const distToRight = Math.abs(x - (card.x + card.width));
        const distToTop = Math.abs(y - card.y);
        const distToBottom = Math.abs(y - (card.y + card.height));
        
        // If inside card, distance is 0
        if (x >= card.x && x <= card.x + card.width &&
            y >= card.y && y <= card.y + card.height) {
          minDistance = 0;
          continue;
        }
        
        // Check horizontal edges
        if (x >= card.x && x <= card.x + card.width) {
          minDistance = Math.min(minDistance, distToTop, distToBottom);
        }
        // Check vertical edges
        if (y >= card.y && y <= card.y + card.height) {
          minDistance = Math.min(minDistance, distToLeft, distToRight);
        }
        // Check corners
        const cornerDist = Math.min(
          Math.sqrt((x - card.x) ** 2 + (y - card.y) ** 2),
          Math.sqrt((x - (card.x + card.width)) ** 2 + (y - card.y) ** 2),
          Math.sqrt((x - card.x) ** 2 + (y - (card.y + card.height)) ** 2),
          Math.sqrt((x - (card.x + card.width)) ** 2 + (y - (card.y + card.height)) ** 2)
        );
        minDistance = Math.min(minDistance, cornerDist);
      }
      return minDistance;
    };

    // Bresenham's line algorithm to interpolate mouse path (prevents skipping on fast movement)
    const bresenhamLine = (x0, y0, x1, y1, callback) => {
      const dx = Math.abs(x1 - x0);
      const dy = Math.abs(y1 - y0);
      const sx = x0 < x1 ? 1 : -1;
      const sy = y0 < y1 ? 1 : -1;
      let err = dx - dy;
      let x = x0;
      let y = y0;
      
      while (true) {
        callback(x, y);
        if (x === x1 && y === y1) break;
        const e2 = 2 * err;
        if (e2 > -dy) {
          err -= dy;
          x += sx;
        }
        if (e2 < dx) {
          err += dx;
          y += sy;
        }
      }
    };

    // Mouse tracking
    const handleMouseMove = (e) => {
      const currentTime = Date.now();
      const newX = e.clientX;
      const newY = e.clientY;
      
      // Calculate distance from last mouse position
      const dx = newX - lastMouseRef.current.x;
      const dy = newY - lastMouseRef.current.y;
      const distance = Math.sqrt(dx * dx + dy * dy);
      
      // Calculate mouse speed (pixels per millisecond)
      const timeDelta = currentTime - lastMouseMoveTimeRef.current;
      if (timeDelta > 0) {
        mouseSpeedRef.current = distance / timeDelta; // pixels per ms
      }
      
      // Get current grid position
      const gridX = Math.floor(newX / gridSize);
      const gridY = Math.floor(newY / gridSize);
      const lastGridX = Math.floor(lastMouseRef.current.x / gridSize);
      const lastGridY = Math.floor(lastMouseRef.current.y / gridSize);
      
      // Use Bresenham's line to interpolate path and prevent skipping
      if (distance > 0.1) {
        const dynamicMaxRipples = mouseSpeedRef.current > 2.0 ? Math.floor(maxRipples * 0.5) : maxRipples; // Reduce ripples during fast movement
        
        // Calculate movement direction (normalized)
        const moveDirLength = Math.sqrt(dx * dx + dy * dy);
        const moveDirX = moveDirLength > 0 ? dx / moveDirLength : 0;
        const moveDirY = moveDirLength > 0 ? dy / moveDirLength : 0;
        
        // Remove oldest ripples if we're at the limit (instead of preventing new ones)
        if (ripplesRef.current.length >= dynamicMaxRipples) {
          // Sort by age (oldest first) and remove the oldest ones
          ripplesRef.current.sort((a, b) => a.startTime - b.startTime);
          const toRemove = ripplesRef.current.length - dynamicMaxRipples + 5; // Remove enough to make room + buffer
          ripplesRef.current = ripplesRef.current.slice(toRemove); // Keep the newest ones
        }
        
        // Interpolate path to catch all grid cells
        bresenhamLine(lastGridX, lastGridY, gridX, gridY, (x, y) => {
          // Always create ripple if within bounds (no limit check - we remove old ones instead)
          if (x >= 0 && x < cols && y >= 0 && y < rows) {
            // Check spacing to avoid too many ripples
            const spacing = Math.max(3, Math.floor(mouseSpeedRef.current * 2)); // Increase spacing during fast movement
            const lastRipple = ripplesRef.current[ripplesRef.current.length - 1];
            const shouldCreate = !lastRipple || 
              Math.abs(x - lastRipple.gridX) >= spacing || 
              Math.abs(y - lastRipple.gridY) >= spacing;
            
            if (shouldCreate) {
              ripplesRef.current.push({
                gridX: x,
                gridY: y,
                distance: 0, // How many squares it has traveled
                intensity: 1.0,
                startTime: currentTime,
                moveDirX: moveDirX, // Store movement direction
                moveDirY: moveDirY
              });
              
              // If we exceed the limit after adding, remove oldest ones immediately
              if (ripplesRef.current.length > dynamicMaxRipples) {
                ripplesRef.current.sort((a, b) => a.startTime - b.startTime);
                ripplesRef.current = ripplesRef.current.slice(-dynamicMaxRipples); // Keep newest ones
              }
            }
          }
        });
        
        lastMouseRef.current = { x: newX, y: newY };
        lastRippleTimeRef.current = currentTime;
      }
      
      mouseRef.current = { x: newX, y: newY };
      lastMouseMoveTimeRef.current = currentTime;
    };

    window.addEventListener('mousemove', handleMouseMove);

    // Animation loop
    const animate = () => {
      // Calculate fade rate based on mouse speed (2x faster than before)
      // Fast movement (> 2 pixels/ms) = faster fade, slow movement = normal fade
      const mouseSpeed = mouseSpeedRef.current;
      const fastThreshold = 2.0; // pixels per millisecond
      let fadeRate;
      let oldWaveFadeRate; // Extra fade for old waves when moving fast
      
      if (mouseSpeed > fastThreshold) {
        // Fast movement: fade MUCH faster to prevent overload
        // Scale fade rate based on speed (more speed = faster fade)
        const speedFactor = Math.min(4, mouseSpeed / fastThreshold); // Cap at 4x
        fadeRate = 0.65 - (speedFactor - 1) * 0.15; // Much faster fade: 0.65 -> 0.20 or lower
        // Old waves fade even faster when moving fast
        oldWaveFadeRate = 0.50 - (speedFactor - 1) * 0.15; // Very fast for old waves: 0.50 -> 0.05
      } else {
        // Normal/slow movement: faster fade for performance
        fadeRate = 0.78; // Faster fade: was 0.84, now 0.78
        oldWaveFadeRate = 0.78; // Same for old waves when moving slow
      }
      
      const currentTime = Date.now();
      const oldWaveThreshold = 1000; // Waves older than 1 second are considered "old"
      
      // Clear and fade grid (speed-based fade, optimized)
      // Only process cells that have intensity > threshold for performance
      const fadeThreshold = 0.03; // Increased threshold even more to remove cells earlier
      
      // During fast movement, skip some grid cells to reduce processing
      const skipCells = mouseSpeed > fastThreshold ? 2 : 1; // Skip every other cell during fast movement
      
      for (let y = 0; y < rows; y += skipCells) {
        for (let x = 0; x < cols; x += skipCells) {
          if (grid[y][x].intensity > fadeThreshold) {
            // Check if this cell is from an old wave (we'll track this in the grid)
            const cellAge = grid[y][x].age || 0;
            const isOldWave = cellAge > oldWaveThreshold;
            
            // Apply appropriate fade rate (more aggressive for fast movement)
            const cellFadeRate = (isOldWave && mouseSpeed > fastThreshold) ? oldWaveFadeRate : fadeRate;
            grid[y][x].intensity *= cellFadeRate;
            grid[y][x].zHeight *= cellFadeRate;
            
            // Age the cell
            grid[y][x].age = (grid[y][x].age || 0) + 16; // Approximate frame time
            
            // Remove very low values to free memory (higher threshold)
            if (grid[y][x].intensity < fadeThreshold) {
              grid[y][x].intensity = 0;
              grid[y][x].zHeight = 0;
              grid[y][x].age = 0;
            }
          }
        }
      }
      
      // Also fade skipped cells if not skipping
      if (skipCells === 1) {
        // Process remaining cells normally
      } else {
        // Process skipped cells with even faster fade
        for (let y = 0; y < rows; y++) {
          for (let x = 0; x < cols; x++) {
            if ((x % skipCells !== 0 || y % skipCells !== 0) && grid[y][x].intensity > fadeThreshold) {
              grid[y][x].intensity *= oldWaveFadeRate; // Very fast fade for skipped cells
              grid[y][x].zHeight *= oldWaveFadeRate;
              if (grid[y][x].intensity < fadeThreshold) {
                grid[y][x].intensity = 0;
                grid[y][x].zHeight = 0;
                grid[y][x].age = 0;
              }
            }
          }
        }
      }
      
      // Also decay mouse speed over time
      mouseSpeedRef.current *= 0.95;
      
      // First pass: update ripple distances and aggressively remove old ripples during fast movement
      const dynamicMaxRipples = mouseSpeed > fastThreshold ? Math.floor(maxRipples * 0.4) : maxRipples;
      
      ripplesRef.current = ripplesRef.current.filter(ripple => {
        ripple.distance += rippleSpeed;
        
        // Use custom maxDistance for button waves, or default rippleDistance for mouse waves
        const maxDist = ripple.maxDistance || rippleDistance;
        
        // Calculate current intensity with faster fade
        const progress = ripple.distance / maxDist;
        ripple.intensity = Math.max(0, 1 - progress);
        
        // During fast movement, remove ripples more aggressively
        const age = currentTime - ripple.startTime;
        const shouldRemove = ripple.intensity <= 0.01 || 
                            ripple.distance >= maxDist ||
                            (mouseSpeed > fastThreshold && age > 500); // Remove ripples older than 500ms during fast movement
        
        if (shouldRemove) {
          return false; // Remove this ripple
        }
        return true;
      });
      
      // If still too many ripples, remove oldest ones
      if (ripplesRef.current.length > dynamicMaxRipples) {
        ripplesRef.current.sort((a, b) => a.startTime - b.startTime); // Sort by age
        ripplesRef.current = ripplesRef.current.slice(-dynamicMaxRipples); // Keep newest ones
      }
      
      // Get card bounds for collision detection (update every 500ms for performance)
      const now = Date.now();
      if (now - lastCardBoundsUpdateRef.current > 500) {
        cardBoundsRef.current = getCardBounds();
        lastCardBoundsUpdateRef.current = now;
      }
      const cards = cardBoundsRef.current;
      
      // Second pass: update grid with ripples, checking for collisions at cell level
      ripplesRef.current.forEach(ripple => {
        // Calculate which squares are affected by this ripple (optimized: only check nearby cells)
        const currentRadius = ripple.distance;
        const waveWidth = 1.5; // Width of the wave rim
        const maxRadius = currentRadius + waveWidth;
        const minRadius = Math.max(0, currentRadius - waveWidth);
        
        // Only check cells within the ripple's maximum radius (performance optimization)
        const checkRadius = Math.ceil(maxRadius) + 1;
        const minX = Math.max(0, ripple.gridX - checkRadius);
        const maxX = Math.min(cols - 1, ripple.gridX + checkRadius);
        const minY = Math.max(0, ripple.gridY - checkRadius);
        const maxY = Math.min(rows - 1, ripple.gridY + checkRadius);
        
        // Pre-calculate squared radii for faster comparison
        const minRadiusSq = minRadius * minRadius;
        const maxRadiusSq = maxRadius * maxRadius;
        
        // Update grid cells within ripple radius (wave pattern: brightest at rim)
        for (let y = minY; y <= maxY; y++) {
          for (let x = minX; x <= maxX; x++) {
            const dx = x - ripple.gridX;
            const dy = y - ripple.gridY;
            // Use squared distance to avoid sqrt (faster)
            const distSq = dx * dx + dy * dy;
            
            // Check card collision for wave reflection effect
            const pixelX = x * gridSize;
            const pixelY = y * gridSize;
            const cardCollision = getCardCollisionInfo(pixelX, pixelY, cards);
            const distanceToCardEdge = getDistanceToCardEdge(pixelX, pixelY, cards);
            const waveApproachDistance = 30; // Distance at which wave starts building up
            
            if (distSq >= minRadiusSq && distSq <= maxRadiusSq) {
              const dist = Math.sqrt(distSq);
              
              // Calculate direction from ripple center to this cell
              const cellDirX = dist > 0 ? dx / dist : 0;
              const cellDirY = dist > 0 ? dy / dist : 0;
              
              // Calculate smooth radius multiplier based on angle and mouse speed
              const moveDirX = ripple.moveDirX || 0;
              const moveDirY = ripple.moveDirY || 0;
              const fastThreshold = 2.0; // Same threshold as used elsewhere
              const mouseSpeed = mouseSpeedRef.current;
              
              let radiusMultiplier = 1.0; // Default: normal propagation
              
              // Only apply asymmetric effect when mouse moves fast
              if (mouseSpeed > fastThreshold && (moveDirX !== 0 || moveDirY !== 0)) {
                // Calculate dot product (cosine of angle between movement and cell direction)
                // dotProduct = 1.0 when same direction, 0.0 when perpendicular, -1.0 when opposite
                const dotProduct = cellDirX * moveDirX + cellDirY * moveDirY;
                
                // Smooth transition: map dot product from [-1, 1] to radius multiplier [0.6, 1.0]
                // Use smoothstep-like function for gradual transition
                // When dotProduct = -1 (opposite): radiusMultiplier = 0.6
                // When dotProduct = 0 (perpendicular): radiusMultiplier = 0.8
                // When dotProduct = 1 (same direction): radiusMultiplier = 1.0
                const normalizedAngle = (dotProduct + 1) / 2; // Map from [-1, 1] to [0, 1]
                const smoothAngle = normalizedAngle * normalizedAngle * (3 - 2 * normalizedAngle); // Smoothstep
                radiusMultiplier = 0.6 + smoothAngle * 0.4; // Interpolate from 0.6 to 1.0
              }
              
              const effectiveRadius = currentRadius * radiusMultiplier;
              const effectiveMinRadius = Math.max(0, effectiveRadius - waveWidth);
              const effectiveMaxRadius = effectiveRadius + waveWidth;
              
              // Re-check if cell is within effective radius
              if (dist >= effectiveMinRadius && dist <= effectiveMaxRadius) {
                // Calculate distance from wave rim (effectiveRadius is the rim)
                const distFromRim = Math.abs(dist - effectiveRadius);
                const normalizedDistFromRim = distFromRim / waveWidth;
                
                // Create wave pattern with Z-axis values
                let waveIntensity;
                let zHeight; // Z-axis value (-2 to 4)
                
                if (dist < effectiveRadius) {
                  // Inside the rim - pattern: -2, 0, 2, 3, 4 (from center to rim)
                  const inwardDist = effectiveRadius - dist;
                  const inwardProgress = inwardDist / effectiveRadius; // 0 at center, 1 at rim
                  
                  // Z-axis pattern: center is -2, rim is 4
                  // Map: 0 (center) -> -2, 1 (rim) -> 4
                  zHeight = -2 + inwardProgress * 6; // Goes from -2 to 4
                  
                  // Intensity pattern: center is low, rim is high
                  waveIntensity = 0.1 + inwardProgress * 0.9; // Fade from 0.1 to 1.0
                } else {
                  // At or just outside rim - peak intensity and Z
                  zHeight = 4 - normalizedDistFromRim * 2; // Fade from 4 to 2
                  waveIntensity = 1.0 - normalizedDistFromRim * 0.3; // Slight fade outward
                }
                
                // Apply overall ripple intensity and wave pattern
                let cellIntensity = ripple.intensity * Math.max(0, Math.min(1, waveIntensity));
                
                // Apply wave reflection effect when hitting cards (like waves on a beach)
                if (cardCollision.isInside) {
                  // Inside card: create reflected wave effect
                  // Calculate distance from edge (how deep into the card)
                  const depthInCard = cardCollision.distanceToEdge;
                  const maxDepth = Math.min(cardCollision.card.width, cardCollision.card.height) / 2;
                  const depthRatio = Math.min(1, depthInCard / maxDepth);
                  
                  // Wave slides in and reflects back
                  // Near edge: high intensity (wave building up)
                  // Deep inside: lower intensity (wave receding)
                  const reflectionIntensity = 0.3 + (1 - depthRatio) * 0.7; // 0.3 to 1.0
                  cellIntensity *= reflectionIntensity;
                  
                  // Create reflected Z-height (wave going backwards)
                  zHeight = -zHeight * 0.8; // Invert and reduce for reflection
                } else if (distanceToCardEdge < waveApproachDistance) {
                  // Approaching card: wave builds up (like waves approaching beach)
                  const approachRatio = 1 - (distanceToCardEdge / waveApproachDistance);
                  const buildupMultiplier = 1 + approachRatio * 0.5; // Increase intensity by up to 50%
                  cellIntensity *= buildupMultiplier;
                  zHeight *= (1 + approachRatio * 0.3); // Slightly increase Z-height
                }
                
                // Check for collisions with other ripples at this cell
                // Age-based collision fading: similar age = complete fade, different age = partial fade
                let collisionFade = 1.0; // No collision by default
                const existingRippleTime = grid[y][x].rippleTime || 0;
                
                // Check collisions if intensity is significant and there's an existing ripple
                if (cellIntensity > 0.1 && existingRippleTime > 0) {
                const invDist = 1 / dist; // Pre-calculate inverse for performance
                const vec1x = dx * invDist;
                const vec1y = dy * invDist;
                
                // Check all other ripples for collisions
                for (const otherRipple of ripplesRef.current) {
                  if (otherRipple === ripple) continue;
                  
                  const otherDx = x - otherRipple.gridX;
                  const otherDy = y - otherRipple.gridY;
                  const otherDistSq = otherDx * otherDx + otherDy * otherDy;
                  
                  // Quick distance check - skip if too far (performance optimization)
                  if (otherDistSq > 144) continue; // Skip if more than 12 squares away (12^2 = 144)
                  
                  const otherRadius = otherRipple.distance;
                  const otherWaveWidth = 1.5;
                  const otherMinRadius = Math.max(0, otherRadius - otherWaveWidth);
                  const otherMaxRadius = otherRadius + otherWaveWidth;
                  const otherMinRadiusSq = otherMinRadius * otherMinRadius;
                  const otherMaxRadiusSq = otherMaxRadius * otherMaxRadius;
                  
                  // Check if other ripple also affects this cell (using squared distance)
                  if (otherDistSq >= otherMinRadiusSq && otherDistSq <= otherMaxRadiusSq) {
                    const otherDist = Math.sqrt(otherDistSq);
                    const invOtherDist = 1 / otherDist;
                    const vec2x = otherDx * invOtherDist;
                    const vec2y = otherDy * invOtherDist;
                    
                    // If vectors point in opposite directions (dot product < -0.5), they're colliding
                    const dotProduct = vec1x * vec2x + vec1y * vec2y;
                    
                    if (dotProduct < -0.5) {
                      // Ripples are colliding - calculate age-based fade
                      const ageDifference = Math.abs(ripple.startTime - existingRippleTime);
                      const similarAgeThreshold = 200; // 200ms = similar age
                      
                      if (ageDifference < similarAgeThreshold) {
                        // Similar age - fade completely
                        collisionFade = 0.0;
                      } else {
                        // Different age - fade based on age difference
                        // Older wave fades newer one less
                        const isThisNewer = ripple.startTime > existingRippleTime;
                        const ageRatio = Math.min(1, ageDifference / 2000); // Normalize to 0-1 over 2 seconds
                        
                        if (isThisNewer) {
                          // This ripple is newer - existing (older) wave fades it less
                          // Very old waves can't fade new ones much
                          collisionFade = 0.3 + (ageRatio * 0.5); // Fade from 30% to 80% based on age
                        } else {
                          // This ripple is older - it gets faded more by the newer one
                          collisionFade = 0.1 + (ageRatio * 0.3); // Fade from 10% to 40% based on age
                        }
                      }
                      break; // Only check first collision
                    }
                  }
                }
                }
                
                // Apply intensity with collision fading
                const finalIntensity = cellIntensity * collisionFade;
                
                // Store Z-height with the grid cell (we'll use this for rendering)
                // We need to track both intensity and Z-height
                if (!grid[y][x] || typeof grid[y][x] === 'number') {
                  grid[y][x] = { intensity: 0, zHeight: 0 };
                }
                
                // Update if this ripple is newer or has higher intensity
                if (finalIntensity > 0 && (ripple.startTime > existingRippleTime || finalIntensity > grid[y][x].intensity)) {
                  grid[y][x].intensity = finalIntensity;
                  grid[y][x].zHeight = zHeight;
                  grid[y][x].rippleTime = ripple.startTime; // Track which ripple created this
                  grid[y][x].age = 0; // Reset age when updated
                }
              }
            }
          }
        }
      });

      // Draw grid with Z-axis effect (rims appear elevated, center depressed)
      ctx.clearRect(0, 0, width, height);
      
      // Draw grid squares with intensity and Z-axis depth (optimized)
      const drawThreshold = 0.02; // Increased threshold to skip drawing low-intensity cells
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const cell = grid[y][x];
          const intensity = cell.intensity;
          const zHeight = cell.zHeight; // Z-axis value: -2 (lowest) to 4 (highest)
          
          if (intensity > drawThreshold) {
            const pixelX = x * gridSize;
            const pixelY = y * gridSize;
            const baseAlpha = intensity * 0.4;
            
            // Calculate Z-axis elevation (much more drastic for contrast)
            // Z-height range: -2 to 4, so elevation range: -0.6 to 1.2
            const elevation = (zHeight / 6) * 2.5; // Increased from 1.8 to 2.5 for more contrast
            const pixelOffset = elevation * 3; // Increased from 2 to 3 for more pronounced offset
            
            // Draw shadow first (for elevated effect, much more drastic)
            if (zHeight > 1) {
              // Elevated cells get a much stronger shadow
              const shadowIntensity = Math.min(1, (zHeight - 1) / 3); // Shadow strength based on height
              ctx.fillStyle = `rgba(0, 0, 0, ${baseAlpha * 0.6 * shadowIntensity})`; // Increased from 0.3 to 0.6
              ctx.fillRect(pixelX + 3, pixelY + 3, gridSize, gridSize); // Increased offset from 2 to 3
            } else if (zHeight < -1) {
              // Depressed cells get a lighter shadow above them (inverse effect)
              const depressionIntensity = Math.min(1, Math.abs(zHeight + 1) / 1);
              ctx.fillStyle = `rgba(0, 0, 0, ${baseAlpha * 0.2 * depressionIntensity})`;
              ctx.fillRect(pixelX - 1, pixelY - 1, gridSize, gridSize);
            }
            
            // Draw main square with Z-axis gradient and offset
            const drawX = pixelX - pixelOffset * 0.4; // Increased from 0.3 to 0.4 for more horizontal offset
            const drawY = pixelY - pixelOffset; // Vertical offset (elevated cells move up)
            
            if (zHeight > 0) {
              // Elevated cells (rim): keep normal brightness, just slight gradient
              const gradient = ctx.createLinearGradient(
                drawX, drawY,
                drawX, drawY + gridSize
              );
              // Reduced brightness - keep it subtle
              const topAlpha = baseAlpha * (1 + elevation * 0.2); // Reduced from 0.8 to 0.2
              const bottomAlpha = baseAlpha * (1 - elevation * 0.1); // Reduced from 0.4 to 0.1
              // Ocean colors for elevated areas (teal/cyan like shallow water)
              gradient.addColorStop(0, `rgba(61, 154, 176, ${Math.min(1, topAlpha)})`); // Teal-cyan
              gradient.addColorStop(0.5, `rgba(45, 122, 138, ${baseAlpha})`); // Medium teal
              gradient.addColorStop(1, `rgba(26, 77, 107, ${bottomAlpha})`); // Deep teal
              ctx.fillStyle = gradient;
            } else {
              // Depressed cells (center): deep ocean blue-green, appears below surface
              const depressionAlpha = baseAlpha * (1 + zHeight * 0.8); // Increased from 0.5 to 0.8 for deeper color
              // Deep ocean bottom colors (very dark blue-green)
              ctx.fillStyle = `rgba(10, 37, 64, ${Math.max(0.05, depressionAlpha)})`; // Deep ocean blue
            }
            ctx.fillRect(drawX, drawY, gridSize, gridSize);
            
            // Draw highlight on top edge for elevated rim cells (ocean surface shimmer)
            if (zHeight > 2) {
              const highlightIntensity = (zHeight - 2) / 2; // Highlight strength
              ctx.strokeStyle = `rgba(115, 200, 230, ${baseAlpha * highlightIntensity * 1.5})`; // Cyan shimmer
              ctx.lineWidth = 2; // Increased from 1.5 to 2
              ctx.beginPath();
              ctx.moveTo(drawX, drawY);
              ctx.lineTo(drawX + gridSize, drawY);
              ctx.stroke();
            }
            
            // Draw border with intensity (more contrast based on Z-height)
            if (intensity > 0.1) {
              const borderAlpha = zHeight > 0 
                ? baseAlpha * 0.8  // Brighter border for elevated
                : baseAlpha * 0.3;  // Darker border for depressed
              const borderColor = zHeight > 0 
                ? `rgba(61, 154, 176, ${borderAlpha})`  // Teal for elevated (like shallow water)
                : `rgba(13, 58, 82, ${borderAlpha})`;    // Deep ocean for depressed
              ctx.strokeStyle = borderColor;
              ctx.lineWidth = zHeight > 2 ? 0.8 : 0.5; // Thicker border for very elevated
              ctx.strokeRect(drawX, drawY, gridSize, gridSize);
            }
          }
        }
      }

      animationFrameRef.current = requestAnimationFrame(animate);
    };

    animate();

    // Handle window resize
    const handleResize = () => {
      const newWidth = window.innerWidth;
      const newHeight = window.innerHeight;
      canvas.width = newWidth;
      canvas.height = newHeight;
      
      // Recalculate grid size
      const newCols = Math.ceil(newWidth / gridSize) + 1;
      const newRows = Math.ceil(newHeight / gridSize) + 1;
      
      // Resize grid array
      while (grid.length < newRows) {
        grid.push(new Array(newCols).fill(0));
      }
      for (let y = 0; y < grid.length; y++) {
        while (grid[y].length < newCols) {
          grid[y].push(0);
        }
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('resize', handleResize);
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="interactive-cloth-layer"
    />
  );
});

InteractiveCloth.displayName = 'InteractiveCloth';

export default InteractiveCloth;

