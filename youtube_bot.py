"""
YouTube Upload Bot
Standalone bot for automatically uploading videos to YouTube.

Usage:
    python youtube_bot.py upload --video path/to/video.mp4 --title "My Video" --description "Description"
    python youtube_bot.py authenticate  # First time setup
"""

import os
import sys
import argparse
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv
import json

load_dotenv()

# YouTube API scopes
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly'
]

# OAuth credentials file
CREDENTIALS_FILE = 'youtube_credentials.json'
TOKEN_FILE = 'youtube_token.json'


class YouTubeUploader:
    """YouTube upload bot class"""
    
    def __init__(self):
        self.youtube = None
        self.credentials = None
    
    def authenticate(self):
        """
        Authenticate with YouTube API.
        On first run, this will open a browser for OAuth consent.
        """
        creds = None
        
        # Load existing token if available
        if os.path.exists(TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            except Exception as e:
                print(f"⚠️  Error loading token: {e}")
                creds = None
        
        # If no valid credentials, get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("🔄 Refreshing expired token...")
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"⚠️  Error refreshing token: {e}")
                    creds = None
            
            if not creds:
                # Check if credentials file exists
                if not os.path.exists(CREDENTIALS_FILE):
                    print("❌ Error: youtube_credentials.json not found!")
                    print("\n📋 To set up YouTube upload bot:")
                    print("1. Go to https://console.cloud.google.com/")
                    print("2. Create a project or select existing one")
                    print("3. Enable 'YouTube Data API v3'")
                    print("4. Go to 'Credentials' > 'Create Credentials' > 'OAuth client ID'")
                    print("5. Choose 'Desktop app' (or 'Other')")
                    print("6. Download the JSON file and save it as 'youtube_credentials.json' in this directory")
                    return False
                
                print("🔐 Starting OAuth flow...")
                print(" browser will open for authentication...")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            # Save credentials for next time
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
            print("✅ Authentication successful! Token saved.")
        
        self.credentials = creds
        
        # Build YouTube API service
        try:
            self.youtube = build('youtube', 'v3', credentials=creds)
            print("✅ YouTube API service initialized")
            return True
        except Exception as e:
            print(f"❌ Error building YouTube service: {e}")
            return False
    
    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: list = None,
        category_id: str = "22",  # People & Blogs
        privacy_status: str = "private",  # private, unlisted, public
        thumbnail_path: str = None,
        default_language: str = "en"
    ):
        """
        Upload a video to YouTube.
        
        Args:
            video_path: Path to video file
            title: Video title
            description: Video description
            tags: List of tags
            category_id: YouTube category ID (default: 22 for People & Blogs)
            privacy_status: 'private', 'unlisted', or 'public'
            thumbnail_path: Optional path to thumbnail image
            default_language: Default language code
        
        Returns:
            Video ID if successful, None otherwise
        """
        if not self.youtube:
            print("❌ Not authenticated. Run 'authenticate' first.")
            return None
        
        if not os.path.exists(video_path):
            print(f"❌ Video file not found: {video_path}")
            return None
        
        print(f"📤 Uploading video: {title}")
        print(f"   File: {video_path}")
        print(f"   Privacy: {privacy_status}")
        
        try:
            # Prepare video metadata
            body = {
                'snippet': {
                    'title': title,
                    'description': description,
                    'tags': tags or [],
                    'categoryId': category_id,
                    'defaultLanguage': default_language
                },
                'status': {
                    'privacyStatus': privacy_status,
                    'selfDeclaredMadeForKids': False
                }
            }
            
            # Create media upload object
            media = MediaFileUpload(
                video_path,
                chunksize=-1,
                resumable=True,
                mimetype='video/*'
            )
            
            # Insert video
            insert_request = self.youtube.videos().insert(
                part=','.join(body.keys()),
                body=body,
                media_body=media
            )
            
            # Upload with progress
            response = self._resumable_upload(insert_request)
            
            if response:
                video_id = response['id']
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                print(f"✅ Video uploaded successfully!")
                print(f"   Video ID: {video_id}")
                print(f"   URL: {video_url}")
                
                # Upload thumbnail if provided
                if thumbnail_path and os.path.exists(thumbnail_path):
                    try:
                        self.youtube.thumbnails().set(
                            videoId=video_id,
                            media_body=MediaFileUpload(thumbnail_path)
                        ).execute()
                        print(f"✅ Thumbnail uploaded successfully!")
                    except Exception as e:
                        print(f"⚠️  Failed to upload thumbnail: {e}")
                
                return video_id
            else:
                print("❌ Upload failed")
                return None
                
        except HttpError as e:
            print(f"❌ HTTP Error: {e}")
            if e.resp.status == 403:
                print("   This might be due to:")
                print("   - Quota exceeded (YouTube API has daily limits)")
                print("   - OAuth token doesn't have upload permissions")
                print("   - Account doesn't have YouTube channel enabled")
            return None
        except Exception as e:
            print(f"❌ Error uploading video: {e}")
            return None
    
    def _resumable_upload(self, insert_request):
        """Handle resumable upload with progress"""
        response = None
        error = None
        retry = 0
        
        while response is None:
            try:
                print("   Uploading...", end='', flush=True)
                status, response = insert_request.next_chunk()
                if response is not None:
                    if 'id' in response:
                        print(" ✅")
                        return response
                    else:
                        print(" ❌")
                        raise Exception(f"Upload failed: {response}")
                else:
                    if status:
                        progress = int(status.progress() * 100)
                        print(f"\r   Upload progress: {progress}%", end='', flush=True)
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    error = f"Retriable HTTP error {e.resp.status}: {e}"
                else:
                    raise
            except Exception as e:
                error = f"Retriable error: {e}"
            
            if error is not None:
                print(f"\n   ⚠️  {error}")
                retry += 1
                if retry > 3:
                    print("   ❌ Max retries exceeded")
                    raise Exception("Upload failed after retries")
                print(f"   🔄 Retrying... ({retry}/3)")
                error = None
        
        return response
    
    def get_channel_info(self):
        """Get information about the authenticated channel"""
        if not self.youtube:
            print("❌ Not authenticated. Run 'authenticate' first.")
            return None
        
        try:
            request = self.youtube.channels().list(
                part='snippet,contentDetails,statistics',
                mine=True
            )
            response = request.execute()
            
            if response['items']:
                channel = response['items'][0]
                print("📺 Channel Information:")
                print(f"   Name: {channel['snippet']['title']}")
                print(f"   ID: {channel['id']}")
                print(f"   Subscribers: {channel['statistics'].get('subscriberCount', 'N/A')}")
                print(f"   Videos: {channel['statistics'].get('videoCount', 'N/A')}")
                return channel
            else:
                print("❌ No channel found. Make sure you have a YouTube channel.")
                return None
        except Exception as e:
            print(f"❌ Error getting channel info: {e}")
            return None


def main():
    parser = argparse.ArgumentParser(description='YouTube Upload Bot')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Authenticate command
    auth_parser = subparsers.add_parser('authenticate', help='Authenticate with YouTube')
    
    # Upload command
    upload_parser = subparsers.add_parser('upload', help='Upload a video')
    upload_parser.add_argument('--video', required=True, help='Path to video file')
    upload_parser.add_argument('--title', required=True, help='Video title')
    upload_parser.add_argument('--description', default='', help='Video description')
    upload_parser.add_argument('--tags', help='Comma-separated tags')
    upload_parser.add_argument('--privacy', choices=['private', 'unlisted', 'public'], 
                              default='private', help='Privacy status')
    upload_parser.add_argument('--category', default='22', help='Category ID (default: 22)')
    upload_parser.add_argument('--thumbnail', help='Path to thumbnail image')
    
    # Channel info command
    info_parser = subparsers.add_parser('info', help='Get channel information')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    uploader = YouTubeUploader()
    
    if args.command == 'authenticate':
        uploader.authenticate()
    elif args.command == 'upload':
        if not uploader.authenticate():
            sys.exit(1)
        
        tags = args.tags.split(',') if args.tags else None
        if tags:
            tags = [tag.strip() for tag in tags]
        
        video_id = uploader.upload_video(
            video_path=args.video,
            title=args.title,
            description=args.description,
            tags=tags,
            privacy_status=args.privacy,
            category_id=args.category,
            thumbnail_path=args.thumbnail
        )
        
        if video_id:
            print(f"\n✅ Success! Video uploaded: https://www.youtube.com/watch?v={video_id}")
        else:
            print("\n❌ Upload failed")
            sys.exit(1)
    elif args.command == 'info':
        if not uploader.authenticate():
            sys.exit(1)
        uploader.get_channel_info()


if __name__ == '__main__':
    main()

