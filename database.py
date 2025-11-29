import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import os
import sys
from contextlib import contextmanager

# ========================================
# ENVIRONMENT VARIABLES
# ========================================
DB_URL = os.getenv("DB_URL")

if not DB_URL:
    print("❌ HATA: DB_URL environment variable tanımlanmamış!")
    print("💡 Render.com'da Internal Database URL'i kullanın")
    sys.exit(1)

# ========================================
# CONNECTION POOL (Performans İçin)
# ========================================
_connection_pool = None

def initialize_pool(minconn=1, maxconn=10):
    """
    Veritabanı connection pool'u oluşturur.
    Production'da her seferinde yeni connection açmak yerine pool kullanmak daha verimli.
    """
    global _connection_pool
    
    if _connection_pool is None:
        try:
            _connection_pool = psycopg2.pool.SimpleConnectionPool(
                minconn,
                maxconn,
                DB_URL,
                cursor_factory=RealDictCursor
            )
            print("✅ Database connection pool oluşturuldu")
            print(f"   Min: {minconn}, Max: {maxconn} bağlantı")
        except Exception as e:
            print(f"❌ Connection pool oluşturma hatası: {e}")
            _connection_pool = None
    
    return _connection_pool

def get_db():
    """
    PostgreSQL bağlantısı açar.
    Pool varsa pool'dan, yoksa direkt bağlantı döner.
    """
    try:
        # Pool varsa ondan al
        if _connection_pool and not _connection_pool.closed:
            conn = _connection_pool.getconn()
            if conn:
                return conn
        
        # Pool yoksa direkt bağlan
        conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
        return conn
        
    except psycopg2.OperationalError as e:
        print(f"❌ DB bağlantı hatası (Operational): {e}")
        print("💡 Veritabanı sunucusu çalışıyor mu kontrol edin")
        return None
    except psycopg2.DatabaseError as e:
        print(f"❌ DB hatası (Database): {e}")
        return None
    except Exception as e:
        print(f"❌ Beklenmeyen DB hatası: {e}")
        return None

def close_db(conn):
    """
    Bağlantıyı kapatır veya pool'a geri verir.
    """
    if not conn:
        return
    
    try:
        # Pool varsa bağlantıyı pool'a geri ver
        if _connection_pool and not _connection_pool.closed:
            _connection_pool.putconn(conn)
        else:
            # Pool yoksa direkt kapat
            conn.close()
    except Exception as e:
        print(f"⚠️ Bağlantı kapatma hatası: {e}")

def close_pool():
    """
    Tüm pool bağlantılarını kapatır.
    Uygulama kapanırken çağrılmalı.
    """
    global _connection_pool
    
    if _connection_pool and not _connection_pool.closed:
        _connection_pool.closeall()
        print("✅ Connection pool kapatıldı")
        _connection_pool = None

@contextmanager
def get_db_cursor(commit=True):
    """
    Context manager ile güvenli DB kullanımı.
    
    Kullanım:
        with get_db_cursor() as cur:
            cur.execute("SELECT * FROM predictions")
            results = cur.fetchall()
    
    Args:
        commit: True ise otomatik commit yapar
    """
    conn = get_db()
    if not conn:
        raise Exception("Veritabanı bağlantısı oluşturulamadı")
    
    try:
        cursor = conn.cursor()
        yield cursor
        
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"❌ Database işlem hatası: {e}")
        raise
    finally:
        cursor.close()
        close_db(conn)

def test_connection():
    """
    Veritabanı bağlantısını test eder.
    """
    print("\n🔍 Veritabanı bağlantısı test ediliyor...\n")
    
    conn = get_db()
    if not conn:
        print("❌ Bağlantı başarısız!")
        return False
    
    try:
        cur = conn.cursor()
        
        # PostgreSQL versiyonu
        cur.execute("SELECT version();")
        version = cur.fetchone()
        print(f"✅ PostgreSQL Version:")
        print(f"   {version['version'][:80]}...")
        
        # Mevcut tablolar
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables = cur.fetchall()
        
        print(f"\n✅ Mevcut Tablolar ({len(tables)}):")
        for table in tables:
            # Satır sayısı
            cur.execute(f"SELECT COUNT(*) as count FROM {table['table_name']};")
            count = cur.fetchone()['count']
            print(f"   • {table['table_name']}: {count} kayıt")
        
        # View'lar
        cur.execute("""
            SELECT table_name 
            FROM information_schema.views 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        views = cur.fetchall()
        
        if views:
            print(f"\n✅ Mevcut View'lar ({len(views)}):")
            for view in views:
                print(f"   • {view['table_name']}")
        
        cur.close()
        close_db(conn)
        
        print("\n✅ Veritabanı bağlantısı başarılı!\n")
        return True
        
    except Exception as e:
        print(f"\n❌ Test hatası: {e}\n")
        close_db(conn)
        return False

def execute_query(query, params=None, fetch=True):
    """
    Hızlı query çalıştırma fonksiyonu.
    
    Args:
        query: SQL sorgusu
        params: Parametreler (tuple veya list)
        fetch: True ise sonuçları döner
    
    Returns:
        fetch=True ise sonuçlar, False ise etkilenen satır sayısı
    """
    conn = get_db()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        
        if fetch:
            results = cur.fetchall()
            cur.close()
            close_db(conn)
            return results
        else:
            conn.commit()
            rowcount = cur.rowcount
            cur.close()
            close_db(conn)
            return rowcount
            
    except Exception as e:
        print(f"❌ Query hatası: {e}")
        if conn:
            conn.rollback()
            close_db(conn)
        return None

def get_pending_predictions():
    """
    Telegram'a gönderilmemiş tahminleri getirir.
    """
    query = """
        SELECT * FROM pending_predictions
        ORDER BY match_date ASC
        LIMIT 50;
    """
    return execute_query(query, fetch=True)

def get_today_matches():
    """
    Bugünkü maçları getirir.
    """
    query = """
        SELECT 
            p.*,
            ms.home_form,
            ms.away_form,
            ms.home_goals_avg,
            ms.away_goals_avg
        FROM predictions p
        LEFT JOIN match_stats ms ON p.match_id = ms.match_id
        WHERE DATE(p.match_date) = CURRENT_DATE
        ORDER BY p.match_date ASC;
    """
    return execute_query(query, fetch=True)

def mark_telegram_sent(match_id, chat_id=None):
    """
    Telegram gönderim durumunu günceller.
    """
    query = """
        UPDATE predictions
        SET telegram_sent = TRUE,
            telegram_sent_at = NOW(),
            telegram_chat_id = %s
        WHERE match_id = %s;
    """
    return execute_query(query, params=(chat_id, match_id), fetch=False)

def get_performance_stats(days=30):
    """
    Son N günün performans istatistiklerini getirir.
    """
    query = """
        SELECT 
            COUNT(*) as total_predictions,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct_predictions,
            ROUND(
                COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / 
                NULLIF(COUNT(*), 0) * 100, 
                2
            ) as accuracy_rate,
            SUM(COALESCE(profit_loss, 0)) as total_profit_loss,
            AVG(COALESCE(ai_confidence, 0)) as avg_confidence
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '%s days'
          AND result IS NOT NULL;
    """
    results = execute_query(query, params=(days,), fetch=True)
    return results[0] if results else None

# ========================================
# ATEXIT HANDLER - Uygulama kapanırken pool'u kapat
# ========================================
import atexit
atexit.register(close_pool)

# ========================================
# MODULE BAŞLATMA
# ========================================
if __name__ == "__main__":
    # Pool'u başlat
    initialize_pool(minconn=2, maxconn=10)
    
    # Test et
    test_connection()
    
    # Örnek kullanım
    print("\n📊 Bugünkü maçlar:")
    matches = get_today_matches()
    if matches:
        for match in matches:
            print(f"   {match['home_team']} vs {match['away_team']}")
    else:
        print("   Bugün maç yok")
    
    print("\n📈 Son 30 gün performansı:")
    stats = get_performance_stats(30)
    if stats:
        print(f"   Toplam Tahmin: {stats['total_predictions']}")
        print(f"   Doğru: {stats['correct_predictions']}")
        print(f"   Başarı Oranı: %{stats['accuracy_rate']}")
        print(f"   Kar/Zarar: {stats['total_profit_loss']}")
    
    # Pool'u kapat
    close_pool()
