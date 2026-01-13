# Google OAuth Setup Guide

This guide explains how to set up Google OAuth login for SIZA.

## 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Google+ API:
   - Go to "APIs & Services" > "Library"
   - Search for "Google+ API" and enable it
4. Create OAuth 2.0 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Web application"
   - Add authorized redirect URIs:
     - For development: `http://localhost:8000/api/auth/google/callback`
     - For production: `https://yourdomain.com/api/auth/google/callback`
   - Save and copy the Client ID and Client Secret

## 2. Environment Variables

Add these to your `.env` file:

```bash
GOOGLE_CLIENT_ID=your_client_id_here
GOOGLE_CLIENT_SECRET=your_client_secret_here
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback  # Update for production
FRONTEND_URL=http://localhost:3000  # Update for production
```

## 3. Database Migration

The User model has been updated to support OAuth users. You need to run a migration to add the new columns:

```sql
-- Run these SQL commands on your PostgreSQL database:

-- Make password_hash nullable for OAuth users
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- Add auth_provider column
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(50) DEFAULT NULL;

-- Add google_id column
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255) UNIQUE;
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
```

Or let SQLAlchemy handle it automatically (it will attempt to create the columns on next startup, but you may need to run the migration manually for existing databases).

## 4. Install Dependencies

Make sure you have the required packages installed:

```bash
cd /opt/reel_generator
source venv/bin/activate
pip install google-auth google-auth-oauthlib google-auth-httplib2
```

## 5. Testing

1. Start the backend server
2. Navigate to the login page
3. Click "Continue with Google"
4. You should be redirected to Google's OAuth consent screen
5. After authorization, you'll be redirected back and logged in

## Notes

- Users who register with Google OAuth will have `password_hash` set to NULL
- The `auth_provider` field will be set to "google" for OAuth users
- Users can link their Google account to an existing email/password account
- Email/password users cannot use Google login unless they link their account first

