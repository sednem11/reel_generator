"""
Migration script to add last_credit_purchase column to the users table.
This tracks when a user last purchased credits to enable watermark-free generation
for users who have purchased within the last month.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/reel_generator")

def migrate():
    """Add last_credit_purchase column to users table"""
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
        
        # Check if column already exists
        cursor.execute("""
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'last_credit_purchase'
        """)
        
        result = cursor.fetchone()
        if result:
            print("✅ Column 'last_credit_purchase' already exists in 'users' table")
            print(f"   Type: {result[1]}, Nullable: {result[2]}")
            return True
        
        print("📝 Column 'last_credit_purchase' not found in 'users' table")
        print("🔄 Adding column...")
        
        # Add column (nullable, since existing users won't have a purchase date)
        cursor.execute("""
            ALTER TABLE users 
            ADD COLUMN last_credit_purchase TIMESTAMP NULL
        """)
        
        print("✅ Successfully added last_credit_purchase column")
        
        # Verify the change
        cursor.execute("""
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'last_credit_purchase'
        """)
        result = cursor.fetchone()
        if result:
            print("✅ Verification: last_credit_purchase column exists")
            print(f"   Type: {result[1]}, Nullable: {result[2]}")
            return True
        else:
            print("❌ Verification failed: last_credit_purchase column not found")
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
    print("Migration: Add last_credit_purchase column to users table")
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
