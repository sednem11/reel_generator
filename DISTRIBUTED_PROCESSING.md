# Distributed Video Processing with RAM Limiting

## Overview

This system implements a hybrid server-client approach for video processing that:
- Detects user's available RAM
- Uses 75% of free RAM for client-side processing
- Distributes tasks between server and client based on RAM capacity
- Prevents system overload by monitoring RAM usage

## Architecture

### Frontend Components

1. **SystemResourceDetector** (`frontend/src/utils/SystemResourceDetector.js`)
   - Detects available RAM using Performance API
   - Calculates safe allocation (75% of free RAM)
   - Estimates how many clips can be processed

2. **TaskDistributor** (`frontend/src/utils/TaskDistributor.js`)
   - Distributes video processing tasks between server and client
   - Allocates tasks based on RAM capacity
   - Balances workload (40% medium clips, 60% short clips for client)

3. **RAMMonitor** (`frontend/src/utils/RAMMonitor.js`)
   - Monitors RAM usage during processing
   - Prevents overload by checking every second
   - Provides callbacks for RAM status updates

4. **DistributedVideoProcessor** (`frontend/src/utils/DistributedVideoProcessor.js`)
   - Main coordinator for distributed processing
   - Handles communication with server
   - Manages client-side task processing
   - Falls back to server-only if client can't handle tasks

### Backend Components

1. **Distributed Processing Endpoint** (`/api/process/distributed`)
   - Accepts client RAM capabilities
   - Creates job with distribution info
   - Coordinates server-side processing
   - Stores client capabilities for optimization

2. **Client Ready Endpoint** (`/api/job/{job_id}/client-ready`)
   - Allows client to notify server when ready
   - Updates job status with client processing info
   - Tracks RAM availability

## How It Works

### Step 1: RAM Detection
```javascript
// Client detects available RAM
const ramInfo = await resourceDetector.getSafeRAMAllocation();
// Returns: 75% of free RAM
```

### Step 2: Task Distribution
```javascript
// System calculates how many clips client can handle
const clipCapacity = await resourceDetector.estimateProcessableClips(duration, quality);
// Distributes: Client gets what it can handle, server gets the rest
```

### Step 3: Processing
- Server processes: Main reel + remaining clips
- Client processes: Up to max_clips based on RAM
- Both work in parallel
- RAM is monitored to prevent overload

### Step 4: Result Merging
- Client uploads processed clips to server
- Server merges all results
- Final package returned to user

## RAM Limits

- **Safe Allocation**: 75% of free RAM
- **Buffer**: 25% kept free to prevent overload
- **Monitoring**: Checks every 1 second
- **Fallback**: If RAM < 0.5 GB, uses server-only mode

## RAM Requirements per Clip

- **Normal (480p)**: ~200 MB per 60s clip
- **HD (720p)**: ~400 MB per 60s clip
- **Full HD (1080p)**: ~800 MB per 60s clip

## Example

User has 8 GB total RAM, 4 GB free:
- Safe allocation: 3 GB (75% of 4 GB)
- Can process: ~7-8 HD clips (3 GB / 400 MB)
- Client processes: 3 medium + 4 short clips
- Server processes: Main reel + remaining clips

## Future Enhancements

1. **Full Client-Side Processing**
   - Implement ffmpeg.wasm for actual video processing
   - Process clips directly in browser
   - Upload results to server

2. **Dynamic Load Balancing**
   - Adjust distribution based on server load
   - Real-time task reassignment
   - Queue management

3. **WebSocket Coordination**
   - Real-time progress updates
   - Task status synchronization
   - Error handling and recovery

## Current Status

✅ RAM detection implemented
✅ Task distribution logic implemented
✅ Backend endpoints created
✅ Frontend integration complete
⏳ Full client-side processing (future enhancement)

The system currently detects RAM and reports capabilities to the server. The server uses this information for optimization. Full client-side processing can be added later using ffmpeg.wasm.

