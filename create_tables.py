from database import get_db, close_db

def create_tables():
    """Tüm veritabanı tablolarını ve index'leri oluşturur"""
    conn = get_db()
    cur = conn.cursor()
    
    print("📊 Veritabanı tabloları oluşturuluyor...\n")

    # ----------------------------------------
    # ŞEMA GÜNCELLEME (idempotent ALTER komutları)
    # Mevcut veritabanında eksik kolon/trigger varsa ekler.
    # ----------------------------------------
    cur.execute("""
        ALTER TABLE IF EXISTS predictions
            ADD COLUMN IF NOT EXISTS has_odds BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS odds_source VARCHAR(50);
    """)
    
    cur.execute("""
        ALTER TABLE IF EXISTS match_stats
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();
    """)
    
    cur.execute("""
        ALTER TABLE IF EXISTS telegram_logs
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();
    """)
    
    cur.execute("""
        ALTER TABLE IF EXISTS performance_summary
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();
    """)
    
    # ========================================
    # 1. ANA TAHMİN TABLOSU
    # ========================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50) UNIQUE NOT NULL,
            home_team VARCHAR(100) NOT NULL,
            away_team VARCHAR(100) NOT NULL,
            league VARCHAR(100),
            match_date TIMESTAMP NOT NULL,
            -- Odds durumu
            has_odds BOOLEAN DEFAULT FALSE,
            odds_source VARCHAR(50),
            
            -- Odds bilgileri (ZORUNLU - DeepSeek analizi için)
            home_odds DECIMAL(5,2),
            draw_odds DECIMAL(5,2),
            away_odds DECIMAL(5,2),
            over_2_5_odds DECIMAL(5,2),
            under_2_5_odds DECIMAL(5,2),
            btts_yes_odds DECIMAL(5,2),
            btts_no_odds DECIMAL(5,2),
            
            -- DeepSeek AI analiz sonuçları
            ai_prediction TEXT,
            ai_confidence DECIMAL(5,2),
            ai_reasoning TEXT,
            recommended_bet VARCHAR(100),
            risk_level VARCHAR(20) CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
            expected_value DECIMAL(6,2), -- Beklenen değer hesaplaması
            
            -- Maç sonuç bilgileri
            home_score INTEGER,
            away_score INTEGER,
            result VARCHAR(20),
            is_correct BOOLEAN,
            profit_loss DECIMAL(10,2),
            
            -- Telegram bilgileri
            telegram_sent BOOLEAN DEFAULT FALSE,
            telegram_sent_at TIMESTAMP,
            telegram_chat_id VARCHAR(50),
            
            -- Meta bilgiler
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    print("  ✅ predictions tablosu oluşturuldu")
    
    # ========================================
    # 2. İSTATİSTİK TABLOSU
    # ========================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS match_stats (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50) UNIQUE NOT NULL REFERENCES predictions(match_id) ON DELETE CASCADE,
            
            -- Takım formu
            home_form VARCHAR(20),
            away_form VARCHAR(20),
            
            -- Gol ortalamaları
            home_goals_avg DECIMAL(4,2),
            away_goals_avg DECIMAL(4,2),
            home_conceded_avg DECIMAL(4,2),
            away_conceded_avg DECIMAL(4,2),
            
            -- Kafa kafaya istatistikler
            head_to_head TEXT,
            h2h_home_wins INTEGER DEFAULT 0,
            h2h_draws INTEGER DEFAULT 0,
            h2h_away_wins INTEGER DEFAULT 0,
            
            -- Ek istatistikler
            home_win_percentage DECIMAL(5,2),
            away_win_percentage DECIMAL(5,2),
            
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    print("  ✅ match_stats tablosu oluşturuldu")
    
    # ========================================
    # 3. TELEGRAM MESAJ GEÇMİŞİ
    # ========================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_logs (
            id SERIAL PRIMARY KEY,
            match_id VARCHAR(50) REFERENCES predictions(match_id) ON DELETE CASCADE,
            
            -- Mesaj bilgileri
            message_text TEXT NOT NULL,
            message_type VARCHAR(20) DEFAULT 'prediction', -- prediction, result, error
            
            -- Gönderim bilgileri
            chat_id VARCHAR(50),
            sent_at TIMESTAMP DEFAULT NOW(),
            success BOOLEAN NOT NULL,
            
            -- Hata yönetimi
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            
            -- Meta
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    print("  ✅ telegram_logs tablosu oluşturuldu")
    
    # ========================================
    # 4. PERFORMANS TAKIP TABLOSU (Yeni!)
    # ========================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS performance_summary (
            id SERIAL PRIMARY KEY,
            
            -- Tarih aralığı
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            
            -- Genel istatistikler
            total_predictions INTEGER DEFAULT 0,
            correct_predictions INTEGER DEFAULT 0,
            accuracy_rate DECIMAL(5,2),
            
            -- Finansal performans
            total_profit_loss DECIMAL(10,2) DEFAULT 0,
            roi DECIMAL(6,2), -- Return on Investment
            
            -- Risk bazlı başarı
            low_risk_accuracy DECIMAL(5,2),
            medium_risk_accuracy DECIMAL(5,2),
            high_risk_accuracy DECIMAL(5,2),
            
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            
            UNIQUE(period_start, period_end)
        );
    """)
    print("  ✅ performance_summary tablosu oluşturuldu")
    
    # ========================================
    # 5. PERFORMANS İÇİN INDEX'LER
    # ========================================
    print("\n🚀 Performans index'leri oluşturuluyor...")
    
    # Predictions tablosu index'leri
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_match_date 
        ON predictions(match_date);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_league 
        ON predictions(league);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_telegram_sent 
        ON predictions(telegram_sent) WHERE telegram_sent = FALSE;
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_created_at 
        ON predictions(created_at DESC);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_risk_level 
        ON predictions(risk_level);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_has_odds 
        ON predictions(has_odds);
    """)
    
    # Match stats tablosu index'i
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_match_stats_match_id 
        ON match_stats(match_id);
    """)
    
    # Telegram logs index'leri
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_telegram_logs_match_id 
        ON telegram_logs(match_id);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_telegram_logs_sent_at 
        ON telegram_logs(sent_at DESC);
    """)
    
    print("  ✅ Tüm index'ler oluşturuldu")
    
    # ========================================
    # 6. KULLANIŞLI VIEW'LER
    # ========================================
    print("\n📊 Analiz view'ları oluşturuluyor...")
    
    cur.execute("""
        CREATE OR REPLACE VIEW pending_predictions AS
        SELECT 
            p.match_id,
            p.home_team,
            p.away_team,
            p.league,
            p.match_date,
            p.recommended_bet,
            p.risk_level,
            p.ai_confidence,
            p.telegram_sent,
            ms.home_form,
            ms.away_form
        FROM predictions p
        LEFT JOIN match_stats ms ON p.match_id = ms.match_id
        WHERE p.telegram_sent = FALSE
          AND p.match_date > NOW()
        ORDER BY p.match_date ASC;
    """)
    print("  ✅ pending_predictions view'ı oluşturuldu")
    
    cur.execute("""
        CREATE OR REPLACE VIEW daily_performance AS
        SELECT 
            DATE(match_date) as match_day,
            COUNT(*) as total_matches,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct_predictions,
            ROUND(
                COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / 
                NULLIF(COUNT(*), 0) * 100, 
                2
            ) as accuracy_percentage,
            SUM(COALESCE(profit_loss, 0)) as daily_profit_loss
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '30 days'
          AND result IS NOT NULL
        GROUP BY DATE(match_date)
        ORDER BY match_day DESC;
    """)
    print("  ✅ daily_performance view'ı oluşturuldu")
    
    # ========================================
    # 7. OTOMATIK GÜNCELLEME TRİGGER'I
    # ========================================
    print("\n⚡ Trigger'lar oluşturuluyor...")
    
    cur.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    cur.execute("""
        DROP TRIGGER IF EXISTS update_predictions_updated_at ON predictions;
        CREATE TRIGGER update_predictions_updated_at
        BEFORE UPDATE ON predictions
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    
    cur.execute("""
        DROP TRIGGER IF EXISTS update_match_stats_updated_at ON match_stats;
        CREATE TRIGGER update_match_stats_updated_at
        BEFORE UPDATE ON match_stats
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    
    cur.execute("""
        DROP TRIGGER IF EXISTS update_telegram_logs_updated_at ON telegram_logs;
        CREATE TRIGGER update_telegram_logs_updated_at
        BEFORE UPDATE ON telegram_logs
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    
    cur.execute("""
        DROP TRIGGER IF EXISTS update_performance_summary_updated_at ON performance_summary;
        CREATE TRIGGER update_performance_summary_updated_at
        BEFORE UPDATE ON performance_summary
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    print("  ✅ updated_at trigger'ı oluşturuldu")
    
    # ========================================
    # COMMIT VE SONUÇ
    # ========================================
    conn.commit()
    close_db(conn)
    
    print("\n" + "="*60)
    print("✅ VERİTABANI BAŞARIYLA OLUŞTURULDU!")
    print("="*60)
    print("\n📊 Oluşturulan Tablolar:")
    print("  1. predictions         - Ana tahmin tablosu")
    print("  2. match_stats         - Maç istatistikleri")
    print("  3. telegram_logs       - Telegram mesaj geçmişi")
    print("  4. performance_summary - Performans özeti")
    
    print("\n📈 Oluşturulan View'lar:")
    print("  1. pending_predictions - Gönderilmemiş tahminler")
    print("  2. daily_performance   - Günlük performans")
    
    print("\n🚀 Performans Optimizasyonları:")
    print("  ✓ 8 adet index oluşturuldu")
    print("  ✓ Foreign key constraints eklendi")
    print("  ✓ Otomatik updated_at trigger'ı aktif")
    print("  ✓ CHECK constraints eklendi")
    
    print("\n💡 Kullanım Örnekleri:")
    print("  • Bekleyen tahminler: SELECT * FROM pending_predictions;")
    print("  • Son 30 gün performans: SELECT * FROM daily_performance;")
    print("  • Bugünkü maçlar: SELECT * FROM predictions WHERE DATE(match_date) = CURRENT_DATE;")
    print("\n")

def drop_all_tables():
    """Tüm tabloları siler - DIKKATLI KULLANIN!"""
    conn = get_db()
    cur = conn.cursor()
    
    print("⚠️  TÜM TABLOLAR SİLİNİYOR...")
    
    cur.execute("DROP VIEW IF EXISTS pending_predictions CASCADE;")
    cur.execute("DROP VIEW IF EXISTS daily_performance CASCADE;")
    cur.execute("DROP TABLE IF EXISTS telegram_logs CASCADE;")
    cur.execute("DROP TABLE IF EXISTS performance_summary CASCADE;")
    cur.execute("DROP TABLE IF EXISTS match_stats CASCADE;")
    cur.execute("DROP TABLE IF EXISTS predictions CASCADE;")
    cur.execute("DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE;")
    
    conn.commit()
    close_db(conn)
    
    print("✅ Tüm tablolar silindi!")

def reset_database():
    """Veritabanını sıfırlar ve yeniden oluşturur"""
    print("\n🔄 VERİTABANI SIFIRLANIYOR...\n")
    drop_all_tables()
    create_tables()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--reset":
            reset_database()
        elif sys.argv[1] == "--drop":
            drop_all_tables()
        else:
            print("Kullanım:")
            print("  python create_tables.py          # Tabloları oluştur")
            print("  python create_tables.py --reset  # Sıfırla ve yeniden oluştur")
            print("  python create_tables.py --drop   # Tüm tabloları sil")
    else:
        create_tables()
