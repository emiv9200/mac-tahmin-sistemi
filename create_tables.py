from database import get_db, close_db

def create_tables():
    conn = get_db()
    cur = conn.cursor()
    
    # Ana tahmin tablosu - GELİŞTİRİLMİŞ
    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50) UNIQUE,
            home_team VARCHAR(100),
            away_team VARCHAR(100),
            league VARCHAR(100),
            match_date TIMESTAMP,
            
            -- Odds bilgileri (ÖNEMLİ!)
            home_odds DECIMAL(5,2),
            draw_odds DECIMAL(5,2),
            away_odds DECIMAL(5,2),
            over_2_5_odds DECIMAL(5,2),
            under_2_5_odds DECIMAL(5,2),
            btts_yes_odds DECIMAL(5,2),
            btts_no_odds DECIMAL(5,2),
            
            -- DeepSeek analiz sonuçları
            ai_prediction TEXT,
            ai_confidence DECIMAL(5,2),
            ai_reasoning TEXT,
            recommended_bet VARCHAR(50),
            risk_level VARCHAR(20), -- LOW, MEDIUM, HIGH
            
            -- Sonuç bilgileri
            home_score INTEGER,
            away_score INTEGER,
            result TEXT,
            is_correct BOOLEAN,
            profit_loss DECIMAL(10,2),
            
            -- Telegram bilgileri
            telegram_sent BOOLEAN DEFAULT FALSE,
            telegram_sent_at TIMESTAMP,
            
            -- Meta bilgiler
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    
    # İstatistik tablosu (opsiyonel ama faydalı)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS match_stats (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50) REFERENCES predictions(match_id),
            home_form VARCHAR(20),
            away_form VARCHAR(20),
            home_goals_avg DECIMAL(3,2),
            away_goals_avg DECIMAL(3,2),
            head_to_head TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    
    # Telegram mesaj geçmişi
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_logs (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50) REFERENCES predictions(match_id),
            message_text TEXT,
            sent_at TIMESTAMP DEFAULT NOW(),
            success BOOLEAN,
            error_message TEXT
        );
    """)
    
    # Performans takibi için index'ler
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_match_date ON predictions(match_date);
        CREATE INDEX IF NOT EXISTS idx_league ON predictions(league);
        CREATE INDEX IF NOT EXISTS idx_telegram_sent ON predictions(telegram_sent);
        CREATE INDEX IF NOT EXISTS idx_created_at ON predictions(created_at);
    """)
    
    conn.commit()
    close_db(conn)
    print("✅ Geliştirilmiş tablolar oluşturuldu!")
    print("📊 Tablolar: predictions, match_stats, telegram_logs")
    print("🚀 Index'ler eklendi - performans optimize edildi")

if __name__ == "__main__":
    create_tables()
