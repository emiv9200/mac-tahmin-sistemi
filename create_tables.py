from database import get_db, close_db

def create_tables():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50),
            home_team VARCHAR(100),
            away_team VARCHAR(100),
            league VARCHAR(100),
            match_date TIMESTAMP,
            ai_prediction TEXT,
            result TEXT,
            is_correct BOOLEAN,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    conn.commit()
    close_db(conn)
    print("✅ Tablolar oluşturuldu.")

if __name__ == "__main__":
    create_tables()
