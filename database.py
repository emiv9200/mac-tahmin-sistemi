import psycopg2
from psycopg2.extras import RealDictCursor
import os

DB_URL = os.getenv("DB_URL")

def get_db():
    """PostgreSQL bağlantısı açar."""
    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print("❌ DB bağlantı hatası:", e)
        return None

def close_db(conn):
    """Bağlantıyı kapatır."""
    try:
        if conn:
            conn.close()
    except:
        pass
