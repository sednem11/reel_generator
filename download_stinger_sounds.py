#!/usr/bin/env python3
"""
Script to help download or create placeholder stinger sounds.
This script provides instructions and can create placeholder files if needed.
"""

import os
import sys

SOUNDS_DIR = 'frontend/public/sounds'
REQUIRED_SOUNDS = {
    'pop': 'Pop sound for emoji/text appearance (whoosh, pop, swoosh)',
    'cling': 'Cling/approval sound (success, ding, notification bell)',
    'disappointment': 'Disappointment/wasted sound (GTA wasted, fail, wah wah)',
    'shock': 'Shock/surprise sound (record scratch, dramatic hit)',
    'confirmation': 'Soft confirmation sound (soft ding, gentle bell)',
}

def create_placeholder_message():
    """Create a message file with instructions"""
    message = """# Stinger Sounds Setup

To use real stinger sounds, please add the following audio files to:
frontend/public/sounds/

Required files:
"""
    for sound, description in REQUIRED_SOUNDS.items():
        message += f"\n- {sound}.mp3 (or .wav/.m4a) - {description}\n"
    
    message += """
## Quick Setup

1. Download stinger sounds from free sources:
   - Zapsplat (https://www.zapsplat.com) - free with account
   - Freesound.org (https://freesound.org) - Creative Commons
   - YouTube Audio Library - royalty-free
   - Pixabay (https://pixabay.com/music/sound-effects/) - free sounds

2. Name them exactly as listed above and place in frontend/public/sounds/

3. Supported formats: .mp3, .wav, .m4a

## Popular Stinger Sounds to Search For:

- **Pop**: "whoosh", "pop", "swoosh", "swish"
- **Cling**: "success", "ding", "notification", "checkmark", "bell"
- **Disappointment**: "wasted", "fail", "error", "wah wah", "sad trombone"
- **Shock**: "record scratch", "dramatic", "shock", "hit"
- **Confirmation**: "soft ding", "gentle bell", "confirmation"

The application will automatically detect and use these files if they exist.
If a file is missing, it will fall back to synthetic sound generation.
"""
    
    readme_path = os.path.join(SOUNDS_DIR, 'README.md')
    with open(readme_path, 'w') as f:
        f.write(message)
    
    print("✅ Created README.md with instructions")
    print(f"📁 Location: {readme_path}")

def check_existing_sounds():
    """Check which sound files already exist"""
    print("\n📊 Checking existing sound files...")
    print("=" * 60)
    
    existing = []
    missing = []
    
    for sound in REQUIRED_SOUNDS.keys():
        found = False
        for ext in ['.mp3', '.wav', '.m4a']:
            path = os.path.join(SOUNDS_DIR, f"{sound}{ext}")
            if os.path.exists(path):
                size = os.path.getsize(path)
                print(f"✅ {sound}{ext} ({size:,} bytes)")
                existing.append(sound)
                found = True
                break
        
        if not found:
            print(f"❌ {sound}.mp3 (missing)")
            missing.append(sound)
    
    print("=" * 60)
    print(f"\n✅ Found: {len(existing)}/{len(REQUIRED_SOUNDS)} sounds")
    print(f"❌ Missing: {len(missing)}/{len(REQUIRED_SOUNDS)} sounds")
    
    if missing:
        print(f"\n⚠️  Please add the missing sound files to: {SOUNDS_DIR}/")
    
    return existing, missing

def main():
    print("🎵 Stinger Sounds Setup")
    print("=" * 60)
    
    # Create directory if it doesn't exist
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    print(f"📁 Directory: {SOUNDS_DIR}")
    
    # Create README
    create_placeholder_message()
    
    # Check existing sounds
    existing, missing = check_existing_sounds()
    
    if missing:
        print("\n💡 Tips:")
        print("   - You can download free sounds from Zapsplat, Freesound, or YouTube Audio Library")
        print("   - Look for short sound effects (0.1-1 second)")
        print("   - Use MP3 format for smaller file sizes")
        print("\n📖 See README.md in the sounds directory for more information")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

