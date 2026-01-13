# YouTube Upload Bot Setup Guide

This guide explains how to set up and use the YouTube upload bot for automatically posting videos to YouTube.

## Prerequisites

1. A Google account with YouTube channel enabled
2. Google Cloud Console access
3. Python environment with dependencies installed

## Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

This will install `google-api-python-client` and other required packages.

## Step 2: Set Up Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **YouTube Data API v3**:
   - Go to "APIs & Services" > "Library"
   - Search for "YouTube Data API v3"
   - Click "Enable"

## Step 3: Create OAuth 2.0 Credentials

1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth client ID"
3. If prompted, configure the OAuth consent screen:
   - Choose "External" (unless you have a Google Workspace)
   - Fill in required fields (App name, User support email, etc.)
   - Add your email to test users
   - Save and continue
4. Create OAuth client ID:
   - Application type: **"Desktop app"** (or "Other")
   - Name: "YouTube Upload Bot" (or any name)
   - Click "Create"
5. Download the JSON file:
   - Click the download icon next to your OAuth client
   - Save it as `youtube_credentials.json` in the project root directory

## Step 4: Authenticate

Run the authentication command:

```bash
python youtube_bot.py authenticate
```

This will:
- Open a browser window
- Ask you to sign in with your Google account
- Request permission to upload videos to YouTube
- Save the authentication token to `youtube_token.json`

**Note:** The token file allows the bot to upload videos without re-authenticating each time (until it expires).

## Step 5: Test Channel Info

Verify authentication works:

```bash
python youtube_bot.py info
```

This will display your channel information.

## Step 6: Upload a Video

```bash
python youtube_bot.py upload \
  --video path/to/your/video.mp4 \
  --title "My Awesome Video" \
  --description "This is a test upload" \
  --tags "test,automation,youtube" \
  --privacy private
```

### Upload Options

- `--video` (required): Path to video file
- `--title` (required): Video title
- `--description`: Video description (default: empty)
- `--tags`: Comma-separated tags (e.g., "tag1,tag2,tag3")
- `--privacy`: `private`, `unlisted`, or `public` (default: `private`)
- `--category`: YouTube category ID (default: 22 for "People & Blogs")
- `--thumbnail`: Path to thumbnail image (optional)

### Example: Upload Public Video

```bash
python youtube_bot.py upload \
  --video output/reel.mp4 \
  --title "Amazing Reel - Auto Generated" \
  --description "Check out this amazing reel!\n\n#shorts #viral" \
  --tags "shorts,viral,reel,automation" \
  --privacy public \
  --thumbnail thumbnail.jpg
```

## YouTube Category IDs

Common category IDs:
- 1: Film & Animation
- 2: Autos & Vehicles
- 10: Music
- 15: Pets & Animals
- 17: Sports
- 19: Travel & Events
- 20: Gaming
- 22: People & Blogs (default)
- 23: Comedy
- 24: Entertainment
- 25: News & Politics
- 26: Howto & Style
- 27: Education
- 28: Science & Technology

## Troubleshooting

### "Quota exceeded" Error

YouTube Data API has daily quotas:
- Default: 10,000 units per day
- Each upload uses ~1,600 units
- You can request quota increase in Google Cloud Console

### "Channel not found" Error

Make sure your Google account has a YouTube channel:
1. Go to https://www.youtube.com
2. If you don't have a channel, create one
3. Verify your channel is active

### "Authentication failed" Error

1. Delete `youtube_token.json` and re-authenticate
2. Make sure `youtube_credentials.json` is in the project root
3. Check that YouTube Data API v3 is enabled in Google Cloud Console

### Token Expired

If the token expires, just run `authenticate` again:
```bash
python youtube_bot.py authenticate
```

## Future Integration

This bot is designed to be integrated into the main backend later. When ready:
1. Move authentication to user-specific tokens
2. Store credentials in database
3. Add API endpoints for scheduled uploads
4. Link YouTube accounts to user accounts

## Security Notes

- **Never commit** `youtube_credentials.json` or `youtube_token.json` to git
- Add them to `.gitignore`
- Keep credentials secure and private
- Each token allows full upload access to the authenticated account

