import sqlite3

DB_NAME = "data.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id INTEGER,
        league_id INTEGER,
        date TEXT,
        home_team TEXT,
        away_team TEXT,
        home_form TEXT,
        away_form TEXT,
        home_xg REAL,
        away_xg REAL,
        home_shots INTEGER,
        away_shots INTEGER,
        created_at TEXT
    );
    """)

    conn.commit()
    conn.close()
