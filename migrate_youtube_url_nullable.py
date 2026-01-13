"""
Migration script to make youtube_url nullable in the jobs table.
This allows uploaded videos (which don't have a YouTube URL) to be stored.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/reel_generator")

def migrate():
    """Make youtube_url column nullable in jobs table"""
    try:
        # Parse connection string
        # Format: postgresql://user:password@host:port/database
        conn_str = DATABASE_URL.replace("postgresql://", "")
        if "@" in conn_str:
            auth, rest = conn_str.split("@", 1)
            user, password = auth.split(":", 1)
            if ":" in rest:
                host_port, database = rest.rsplit("/", 1)
                if ":" in host_port:
                    host, port = host_port.split(":", 1)
                else:
                    host = host_port
                    port = "5432"
            else:
                host = rest.split("/")[0]
                port = "5432"
                database = rest.split("/", 1)[1] if "/" in rest else "reel_generator"
        else:
            raise ValueError("Invalid DATABASE_URL format")
        
        print(f"Connecting to database: {host}:{port}/{database}")
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if column exists and is currently NOT NULL
        cursor.execute("""
            SELECT column_name, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'jobs' AND column_name = 'youtube_url'
        """)
        
        result = cursor.fetchone()
        if not result:
            print("❌ Column 'youtube_url' not found in 'jobs' table")
            return False
        
        column_name, is_nullable = result
        if is_nullable == 'YES':
            print("✅ Column 'youtube_url' is already nullable. No migration needed.")
            return True
        
        print(f"📝 Current state: youtube_url is NOT NULL")
        print("🔄 Altering column to allow NULL values...")
        
        # Alter column to allow NULL
        cursor.execute("ALTER TABLE jobs ALTER COLUMN youtube_url DROP NOT NULL")
        
        print("✅ Successfully made youtube_url nullable")
        
        # Verify the change
        cursor.execute("""
            SELECT is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'jobs' AND column_name = 'youtube_url'
        """)
        result = cursor.fetchone()
        if result and result[0] == 'YES':
            print("✅ Verification: youtube_url is now nullable")
            return True
        else:
            print("❌ Verification failed: youtube_url is still NOT NULL")
            return False
        
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Make youtube_url nullable in jobs table")
    print("=" * 60)
    print()
    
    success = migrate()
    
    print()
    if success:
        print("✅ Migration completed successfully!")
        sys.exit(0)
    else:
        print("❌ Migration failed!")
        sys.exit(1)

