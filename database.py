import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import os
import sys
import logging
from contextlib import contextmanager
import time

# ========================================
# LOGGING CONFIGURATION
# ========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========================================
# ENVIRONMENT VARIABLES
# ========================================
DB_URL = os.getenv("DB_URL")

if not DB_URL:
    logger.error("❌ HATA: DB_URL environment variable tanımlanmamış!")
    print("💡 Render.com'da Internal Database URL'i kullanın")
    sys.exit(1)

# ========================================
# CONNECTION POOL (Performans İçin)
# ========================================
_connection_pool = None
_pool_initialized = False

def initialize_pool(minconn=2, maxconn=10):
    """
    Veritabanı connection pool'u oluşturur - AUTO INITIALIZE
    Production'da her seferinde yeni connection açmak yerine pool kullanmak daha verimli.
    """
    global _connection_pool, _pool_initialized
    
    if _pool_initialized and _connection_pool and not _connection_pool.closed:
        logger.info("✅ Pool zaten aktif")
        return _connection_pool
    
    try:
        _connection_pool = psycopg2.pool.SimpleConnectionPool(
            minconn,
            maxconn,
            DB_URL,
            cursor_factory=RealDictCursor
        )
        _pool_initialized = True
        logger.info("✅ Database connection pool oluşturuldu")
        logger.info(f"   Min: {minconn}, Max: {maxconn} bağlantı")
        return _connection_pool
    except Exception as e:
        logger.error(f"❌ Connection pool oluşturma hatası: {e}")
        _connection_pool = None
        _pool_initialized = False
        return None

def ensure_pool():
    """Pool'un hazır olduğundan emin ol - AUTO INITIALIZE"""
    global _connection_pool, _pool_initialized
    
    if not _pool_initialized or not _connection_pool:
        logger.info("🔄 Pool başlatılıyor...")
        initialize_pool()
    
    return _connection_pool is not None

def get_db(retry_count=3):
    """
    PostgreSQL bağlantısı açar - WITH RETRY
    Pool varsa pool'dan, yoksa direkt bağlantı döner.
    """
    # Ensure pool is initialized
    ensure_pool()
    
    for attempt in range(retry_count):
        try:
            # Pool varsa ondan al
            if _connection_pool and not _connection_pool.closed:
                conn = _connection_pool.getconn()
                if conn:
                    # Test connection
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT 1")
                        cur.close()
                        return conn
                    except:
                        # Connection dead, try to get another one
                        _connection_pool.putconn(conn, close=True)
                        continue
            
            # Pool yoksa direkt bağlan
            conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
            return conn
            
        except psycopg2.OperationalError as e:
            logger.error(f"❌ DB bağlantı hatası (deneme {attempt + 1}/{retry_count}): {e}")
            if attempt < retry_count - 1:
                logger.info(f"🔄 {2 ** attempt} saniye sonra tekrar denenecek...")
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            logger.error("💡 Veritabanı sunucusu çalışıyor mu kontrol edin")
            return None
        except psycopg2.DatabaseError as e:
            logger.error(f"❌ DB hatası (Database): {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Beklenmeyen DB hatası: {e}")
            return None
    
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
        logger.warning(f"⚠️ Bağlantı kapatma hatası: {e}")

def close_pool():
    """
    Tüm pool bağlantılarını kapatır.
    Uygulama kapanırken çağrılmalı.
    """
    global _connection_pool, _pool_initialized
    
    if _connection_pool and not _connection_pool.closed:
        _connection_pool.closeall()
        logger.info("✅ Connection pool kapatıldı")
        _connection_pool = None
        _pool_initialized = False

def health_check():
    """
    Veritabanı bağlantı sağlığını kontrol eder.
    Returns: (is_healthy: bool, latency_ms: float)
    """
    start_time = time.time()
    
    conn = get_db()
    if not conn:
        return (False, 0)
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        close_db(conn)
        
        latency = (time.time() - start_time) * 1000  # Convert to ms
        return (True, round(latency, 2))
    except Exception as e:
        logger.error(f"❌ Health check hatası: {e}")
        close_db(conn)
        return (False, 0)

@contextmanager
def get_db_cursor(commit=True, retry_count=3):
    """
    Context manager ile güvenli DB kullanımı - WITH RETRY
    
    Kullanım:
        with get_db_cursor() as cur:
            cur.execute("SELECT * FROM predictions")
            results = cur.fetchall()
    
    Args:
        commit: True ise otomatik commit yapar
        retry_count: Bağlantı hatası durumunda kaç kez denenecek
    """
    conn = None
    cursor = None
    
    for attempt in range(retry_count):
        try:
            conn = get_db()
            if not conn:
                if attempt < retry_count - 1:
                    logger.warning(f"🔄 Bağlantı tekrar deneniyor ({attempt + 1}/{retry_count})...")
                    time.sleep(2 ** attempt)
                    continue
                raise Exception("Veritabanı bağlantısı oluşturulamadı")
            
            cursor = conn.cursor()
            yield cursor
            
            if commit:
                conn.commit()
            
            break  # Success, exit retry loop
            
        except psycopg2.OperationalError as e:
            logger.error(f"❌ Bağlantı hatası (deneme {attempt + 1}/{retry_count}): {e}")
            if conn:
                conn.rollback()
                close_db(conn)
            
            if attempt < retry_count - 1:
                time.sleep(2 ** attempt)
                continue
            raise
            
        except Exception as e:
            logger.error(f"❌ Database işlem hatası: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                close_db(conn)

def test_connection():
    """
    Veritabanı bağlantısını test eder.
    """
    print("\n" + "="*60)
    print("🔍 VERİTABANI BAĞLANTI TESTİ")
    print("="*60 + "\n")
    
    # Health check
    is_healthy, latency = health_check()
    
    if not is_healthy:
        print("❌ Bağlantı başarısız!")
        return False
    
    print(f"✅ Bağlantı başarılı! (Gecikme: {latency}ms)")
    
    conn = get_db()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # PostgreSQL versiyonu
        cur.execute("SELECT version();")
        version = cur.fetchone()
        print(f"\n📊 PostgreSQL Version:")
        print(f"   {version['version'][:80]}...")
        
        # Mevcut tablolar
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables = cur.fetchall()
        
        print(f"\n📁 Mevcut Tablolar ({len(tables)}):")
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
            print(f"\n👁️  Mevcut View'lar ({len(views)}):")
            for view in views:
                print(f"   • {view['table_name']}")
        
        # Pool durumu
        if _connection_pool:
            print(f"\n🏊 Connection Pool Durumu:")
            print(f"   • Aktif: {'✅ Evet' if not _connection_pool.closed else '❌ Hayır'}")
        
        cur.close()
        close_db(conn)
        
        print("\n" + "="*60)
        print("✅ TÜM KONTROLLER BAŞARILI!")
        print("="*60 + "\n")
        return True
        
    except Exception as e:
        logger.error(f"❌ Test hatası: {e}")
        close_db(conn)
        return False

def execute_query(query, params=None, fetch=True, retry_count=3):
    """
    Hızlı query çalıştırma fonksiyonu - WITH RETRY
    
    Args:
        query: SQL sorgusu
        params: Parametreler (tuple veya list)
        fetch: True ise sonuçları döner
        retry_count: Hata durumunda kaç kez denenecek
    
    Returns:
        fetch=True ise sonuçlar, False ise etkilenen satır sayısı
    """
    for attempt in range(retry_count):
        conn = get_db()
        if not conn:
            if attempt < retry_count - 1:
                logger.warning(f"🔄 Query tekrar deneniyor ({attempt + 1}/{retry_count})...")
                time.sleep(2 ** attempt)
                continue
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
                
        except psycopg2.OperationalError as e:
            logger.error(f"❌ Query bağlantı hatası (deneme {attempt + 1}/{retry_count}): {e}")
            if conn:
                conn.rollback()
                close_db(conn)
            
            if attempt < retry_count - 1:
                time.sleep(2 ** attempt)
                continue
            return None
            
        except Exception as e:
            logger.error(f"❌ Query hatası: {e}")
            logger.error(f"   Query: {query[:100]}...")
            if conn:
                conn.rollback()
                close_db(conn)
            return None
    
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

def get_matches_without_odds():
    """
    Odds bilgisi olmayan maçları getirir.
    """
    query = """
        SELECT match_id, home_team, away_team, league, match_date
        FROM predictions
        WHERE home_odds IS NULL
          AND match_date > NOW()
        ORDER BY match_date ASC
        LIMIT 20;
    """
    return execute_query(query, fetch=True)

def get_league_performance(days=90):
    """
    Lig bazında performans istatistikleri.
    """
    query = """
        SELECT 
            league,
            COUNT(*) as total_predictions,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct_predictions,
            ROUND(
                COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / 
                NULLIF(COUNT(*), 0) * 100, 
                2
            ) as accuracy_rate,
            SUM(COALESCE(profit_loss, 0)) as total_profit_loss
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '%s days'
          AND result IS NOT NULL
          AND league IS NOT NULL
        GROUP BY league
        ORDER BY accuracy_rate DESC;
    """
    return execute_query(query, params=(days,), fetch=True)

# ========================================
# AUTO-INITIALIZE POOL ON IMPORT
# ========================================
logger.info("🚀 Database module yükleniyor...")
initialize_pool(minconn=2, maxconn=10)

# ========================================
# ATEXIT HANDLER - Uygulama kapanırken pool'u kapat
# ========================================
import atexit
atexit.register(close_pool)

# ========================================
# MODULE BAŞLATMA TEST
# ========================================
if __name__ == "__main__":
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
        print(f"   Kar/Zarar: {stats['total_profit_loss']} TL")
    
    print("\n⚠️  Odds olmayan maçlar:")
    no_odds = get_matches_without_odds()
    if no_odds:
        for match in no_odds[:5]:
            print(f"   • {match['home_team']} vs {match['away_team']}")
    else:
        print("   Tüm maçlarda odds mevcut ✅")
    
    print("\n🏆 Lig performansları:")
    league_stats = get_league_performance(90)
    if league_stats:
        for league in league_stats[:5]:
            print(f"   • {league['league']}: %{league['accuracy_rate']} başarı ({league['total_predictions']} maç)")
    
    # Pool'u kapat
    close_pool()
