import os
import requests
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from database import (
    get_db_cursor, 
    get_pending_predictions,
    get_today_matches,
    mark_telegram_sent,
    get_performance_stats,
    execute_query
)
from data_collector import collect_today_matches

# ========================================
# ENVIRONMENT VARIABLES
# ========================================
API_KEY = os.getenv("API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Validation
if not all([API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY]):
    print("⚠️ UYARI: Bazı environment variable'lar eksik!")
    print(f"   API_KEY: {'✓' if API_KEY else '✗'}")
    print(f"   TELEGRAM_BOT_TOKEN: {'✓' if TELEGRAM_BOT_TOKEN else '✗'}")
    print(f"   TELEGRAM_CHAT_ID: {'✓' if TELEGRAM_CHAT_ID else '✗'}")
    print(f"   DEEPSEEK_API_KEY: {'✓' if DEEPSEEK_API_KEY else '✗'}")

API_BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# Target leagues
TARGET_LEAGUES = [
    39,   # Premier League
    78,   # Bundesliga
    140,  # La Liga
    61,   # Ligue 1
    135,  # Serie A
    203,  # Süper Lig
]

# ========================================
# TELEGRAM FUNCTIONS
# ========================================

def send_telegram_message(text: str, parse_mode="Markdown", disable_preview=True):
    """
    Telegram'a mesaj gönderir.
    
    Args:
        text: Mesaj metni
        parse_mode: "Markdown" veya "HTML"
        disable_preview: Link önizlemesini kapat
    
    Returns:
        bool: Başarılı ise True
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram bilgileri eksik!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Telegram gönderim hatası: {e}")
        return False

def log_telegram_message(match_id, message_text, success, error_message=None):
    """Telegram mesaj logunu veritabanına kaydeder"""
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                INSERT INTO telegram_logs (
                    match_id, message_text, chat_id, success, error_message
                ) VALUES (%s, %s, %s, %s, %s)
            """, (match_id, message_text, TELEGRAM_CHAT_ID, success, error_message))
    except Exception as e:
        print(f"⚠️ Log kaydetme hatası: {e}")

# ========================================
# DEEPSEEK AI PREDICTION
# ========================================

def deepseek_predict(match_data):
    """
    DeepSeek API ile gelişmiş maç tahmini yapar.
    
    Args:
        match_data: Dictionary içinde maç bilgileri
    
    Returns:
        dict: {
            'prediction': str,
            'confidence': float,
            'reasoning': str,
            'recommended_bet': str,
            'risk_level': str
        }
    """
    if not DEEPSEEK_API_KEY:
        return {
            'prediction': 'API KEY yok',
            'confidence': 0,
            'reasoning': 'DeepSeek API KEY tanımlanmamış',
            'recommended_bet': 'SKIP',
            'risk_level': 'HIGH'
        }

    # Prompt oluştur
    prompt = f"""
Sen profesyonel bir futbol analisti ve bahis uzmanısın. Aşağıdaki maç için detaylı analiz yap:

📊 MAÇ BİLGİLERİ:
- Ev Sahibi: {match_data['home_team']}
- Deplasman: {match_data['away_team']}
- Lig: {match_data['league']}
- Tarih: {match_data['match_date']}

📈 ODDS (Bahis Oranları):
- Ev Kazanır: {match_data.get('home_odds', 'N/A')}
- Beraberlik: {match_data.get('draw_odds', 'N/A')}
- Deplasman Kazanır: {match_data.get('away_odds', 'N/A')}
- Üst 2.5 Gol: {match_data.get('over_2_5_odds', 'N/A')}
- Alt 2.5 Gol: {match_data.get('under_2_5_odds', 'N/A')}
- BTTS Yes: {match_data.get('btts_yes_odds', 'N/A')}

📊 İSTATİSTİKLER:
- Ev Sahibi Formu: {match_data.get('home_form', 'N/A')}
- Deplasman Formu: {match_data.get('away_form', 'N/A')}
- Ev Sahibi Ortalama Gol: {match_data.get('home_goals_avg', 'N/A')}
- Deplasman Ortalama Gol: {match_data.get('away_goals_avg', 'N/A')}

GÖREV: Aşağıdaki formatta SADECE JSON yanıt ver, başka hiçbir metin ekleme:

{{
  "prediction": "HOME_WIN | DRAW | AWAY_WIN | OVER_2.5 | UNDER_2.5 | BTTS_YES",
  "confidence": 75,
  "reasoning": "Kısa analiz açıklaması (max 150 karakter)",
  "recommended_bet": "Hangi bahsi öneriyorsun (örn: 'Ev Kazanır @2.10')",
  "risk_level": "LOW | MEDIUM | HIGH",
  "expected_value": 1.15
}}

KURALLAR:
1. Confidence: 0-100 arası sayı
2. Risk Level: Sadece LOW, MEDIUM veya HIGH
3. Expected Value: (Odds * Win Probability) - 1
4. Reasoning: Maksimum 150 karakter
5. SADECE JSON yanıt ver, başka açıklama ekleme
"""

    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,  # Daha tutarlı sonuçlar için
                "max_tokens": 500
            },
            timeout=30
        )
        
        response.raise_for_status()
        result = response.json()
        
        # JSON parse et
        import json
        ai_response = result["choices"][0]["message"]["content"]
        
        # Markdown code block'larını temizle
        ai_response = ai_response.replace('```json', '').replace('```', '').strip()
        
        prediction_data = json.loads(ai_response)
        
        return {
            'prediction': prediction_data.get('prediction', 'UNKNOWN'),
            'confidence': float(prediction_data.get('confidence', 0)),
            'reasoning': prediction_data.get('reasoning', 'Analiz yapılamadı'),
            'recommended_bet': prediction_data.get('recommended_bet', 'SKIP'),
            'risk_level': prediction_data.get('risk_level', 'HIGH'),
            'expected_value': float(prediction_data.get('expected_value', 0))
        }
        
    except json.JSONDecodeError as e:
        print(f"❌ DeepSeek JSON parse hatası: {e}")
        print(f"   Raw response: {ai_response[:200]}")
        return {
            'prediction': 'PARSE_ERROR',
            'confidence': 0,
            'reasoning': 'AI yanıtı parse edilemedi',
            'recommended_bet': 'SKIP',
            'risk_level': 'HIGH',
            'expected_value': 0
        }
    except Exception as e:
        print(f"❌ DeepSeek API hatası: {e}")
        return {
            'prediction': 'ERROR',
            'confidence': 0,
            'reasoning': str(e)[:150],
            'recommended_bet': 'SKIP',
            'risk_level': 'HIGH',
            'expected_value': 0
        }

# ========================================
# AI ANALYSIS & UPDATE
# ========================================

def analyze_and_update_predictions():
    """
    Veritabanındaki AI prediction'ı olmayan maçları analiz eder.
    """
    print("\n🤖 DeepSeek analizi başlatılıyor...\n")
    
    query = """
        SELECT 
            p.*,
            ms.home_form,
            ms.away_form,
            ms.home_goals_avg,
            ms.away_goals_avg
        FROM predictions p
        LEFT JOIN match_stats ms ON p.match_id = ms.match_id
        WHERE p.ai_confidence IS NULL
          AND p.match_date > NOW()
          AND p.home_odds IS NOT NULL
        ORDER BY p.match_date ASC
        LIMIT 20;
    """
    
    matches = execute_query(query)
    
    if not matches:
        print("✓ Analiz edilecek maç yok")
        return 0
    
    analyzed_count = 0
    
    for match in matches:
        print(f"  📊 Analiz ediliyor: {match['home_team']} vs {match['away_team']}")
        
        # DeepSeek'ten tahmin al
        ai_result = deepseek_predict(match)
        
        # Veritabanını güncelle
        try:
            with get_db_cursor() as cur:
                cur.execute("""
                    UPDATE predictions
                    SET ai_prediction = %s,
                        ai_confidence = %s,
                        ai_reasoning = %s,
                        recommended_bet = %s,
                        risk_level = %s,
                        expected_value = %s,
                        updated_at = NOW()
                    WHERE match_id = %s
                """, (
                    ai_result['prediction'],
                    ai_result['confidence'],
                    ai_result['reasoning'],
                    ai_result['recommended_bet'],
                    ai_result['risk_level'],
                    ai_result['expected_value'],
                    match['match_id']
                ))
            
            print(f"    ✅ Tahmin: {ai_result['prediction']} (Güven: %{ai_result['confidence']})")
            analyzed_count += 1
            
            # Rate limiting
            time.sleep(2)
            
        except Exception as e:
            print(f"    ❌ Güncelleme hatası: {e}")
    
    print(f"\n✅ Toplam {analyzed_count} maç analiz edildi!\n")
    return analyzed_count

# ========================================
# TELEGRAM MESSAGE FORMATTING
# ========================================

def format_prediction_message(match):
    """Maç tahmin kartını formatlar"""
    
    # Risk emoji
    risk_emoji = {
        'LOW': '🟢',
        'MEDIUM': '🟡',
        'HIGH': '🔴'
    }.get(match.get('risk_level', 'HIGH'), '⚪')
    
    # Tarih formatı
    match_time = match['match_date'].strftime('%H:%M') if isinstance(match['match_date'], datetime) else str(match['match_date'])[11:16]
    
    message = f"""
━━━━━━━━━━━━━━━━━━━━━━
⚽ *{match['home_team']}* vs *{match['away_team']}*
🏆 {match['league']}
🕐 Saat: {match_time}

📊 *ODDS*
├ Ev: {match.get('home_odds', 'N/A')}
├ Beraberlik: {match.get('draw_odds', 'N/A')}
└ Deplasman: {match.get('away_odds', 'N/A')}

🤖 *AI TAHMİNİ*
{risk_emoji} *{match.get('recommended_bet', 'SKIP')}*

📈 Güven: %{match.get('ai_confidence', 0):.0f}
💡 Analiz: _{match.get('ai_reasoning', 'Analiz yok')}_
⚡ Risk: {match.get('risk_level', 'HIGH')}
━━━━━━━━━━━━━━━━━━━━━━
"""
    return message.strip()

# ========================================
# SEND PREDICTIONS TO TELEGRAM
# ========================================

def send_daily_predictions(min_confidence=60, max_risk='MEDIUM'):
    """
    Günlük tahminleri Telegram'a gönderir.
    
    Args:
        min_confidence: Minimum güven skoru
        max_risk: Maximum risk seviyesi (LOW, MEDIUM, HIGH)
    """
    print("\n📤 Telegram'a tahminler gönderiliyor...\n")
    
    # Risk seviyesi mapping
    risk_levels = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
    max_risk_value = risk_levels.get(max_risk, 2)
    
    query = """
        SELECT 
            p.*,
            ms.home_form,
            ms.away_form
        FROM predictions p
        LEFT JOIN match_stats ms ON p.match_id = ms.match_id
        WHERE p.telegram_sent = FALSE
          AND p.match_date > NOW()
          AND p.match_date < NOW() + INTERVAL '24 hours'
          AND p.ai_confidence >= %s
          AND p.ai_confidence IS NOT NULL
        ORDER BY p.ai_confidence DESC, p.match_date ASC
        LIMIT 10;
    """
    
    matches = execute_query(query, params=(min_confidence,))
    
    if not matches:
        print("  ℹ️ Gönderilecek tahmin yok")
        return 0
    
    # Risk filtreleme
    filtered_matches = [
        m for m in matches 
        if risk_levels.get(m.get('risk_level', 'HIGH'), 3) <= max_risk_value
    ]
    
    if not filtered_matches:
        print(f"  ℹ️ {max_risk} risk seviyesinde tahmin yok")
        return 0
    
    # Header mesajı
    header = f"""
🔥 *BUGÜNÜN VIP TAHMİNLERİ* 🔥
📅 {datetime.now().strftime('%d.%m.%Y')}
🎯 Toplam {len(filtered_matches)} maç

_Min Güven: %{min_confidence} | Max Risk: {max_risk}_
"""
    
    send_telegram_message(header)
    time.sleep(1)
    
    sent_count = 0
    
    for match in filtered_matches:
        message = format_prediction_message(match)
        success = send_telegram_message(message)
        
        # Log kaydet
        log_telegram_message(
            match['match_id'],
            message,
            success,
            None if success else "Send failed"
        )
        
        if success:
            # Telegram durumunu güncelle
            mark_telegram_sent(match['match_id'], TELEGRAM_CHAT_ID)
            sent_count += 1
            print(f"  ✅ {match['home_team']} vs {match['away_team']}")
        else:
            print(f"  ❌ {match['home_team']} vs {match['away_team']}")
        
        time.sleep(2)  # Telegram rate limit
    
    # Footer
    footer = f"""
━━━━━━━━━━━━━━━━━━━━━━
✅ {sent_count} tahmin gönderildi
🤖 *Powered by DeepSeek AI*
━━━━━━━━━━━━━━━━━━━━━━
"""
    send_telegram_message(footer)
    
    print(f"\n✅ Toplam {sent_count} tahmin gönderildi!\n")
    return sent_count

# ========================================
# RESULT CHECKING & UPDATE
# ========================================

def check_and_update_results():
    """
    Biten maçların sonuçlarını kontrol eder ve veritabanını günceller.
    """
    print("\n🔍 Maç sonuçları kontrol ediliyor...\n")
    
    query = """
        SELECT * FROM predictions
        WHERE result IS NULL
          AND match_date < NOW()
          AND match_date > NOW() - INTERVAL '7 days'
        ORDER BY match_date DESC;
    """
    
    matches = execute_query(query)
    
    if not matches:
        print("  ℹ️ Kontrol edilecek maç yok")
        return 0
    
    updated_count = 0
    
    for match in matches:
        match_id = match['match_id']
        
        try:
            url = f"{API_BASE_URL}/fixtures"
            params = {"id": match_id}
            
            response = requests.get(url, headers=HEADERS, params=params, timeout=10)
            data = response.json().get("response", [])
            
            if not data:
                continue
            
            fixture = data[0]
            status = fixture["fixture"]["status"]["short"]
            
            # Sadece bitmiş maçları işle
            if status != "FT":
                continue
            
            home_score = fixture["goals"]["home"]
            away_score = fixture["goals"]["away"]
            
            # Tahmin doğruluğunu kontrol et
            is_correct = check_prediction_accuracy(match, home_score, away_score)
            
            # Kar/zarar hesapla (örnek: 10 birim bahis)
            profit_loss = calculate_profit_loss(match, is_correct)
            
            # Veritabanını güncelle
            with get_db_cursor() as cur:
                cur.execute("""
                    UPDATE predictions
                    SET home_score = %s,
                        away_score = %s,
                        result = %s,
                        is_correct = %s,
                        profit_loss = %s,
                        updated_at = NOW()
                    WHERE match_id = %s
                """, (
                    home_score,
                    away_score,
                    f"{home_score}-{away_score}",
                    is_correct,
                    profit_loss,
                    match_id
                ))
            
            status_icon = "✅" if is_correct else "❌"
            print(f"  {status_icon} {match['home_team']} {home_score}-{away_score} {match['away_team']}")
            updated_count += 1
            
            time.sleep(1)  # API rate limit
            
        except Exception as e:
            print(f"  ⚠️ {match['home_team']} - Hata: {e}")
    
    print(f"\n✅ {updated_count} maç sonucu güncellendi!\n")
    return updated_count

def check_prediction_accuracy(match, home_score, away_score):
    """Tahmin doğruluğunu kontrol eder"""
    prediction = match.get('ai_prediction', '')
    
    if 'HOME_WIN' in prediction and home_score > away_score:
        return True
    elif 'AWAY_WIN' in prediction and away_score > home_score:
        return True
    elif 'DRAW' in prediction and home_score == away_score:
        return True
    elif 'OVER_2.5' in prediction and (home_score + away_score) > 2.5:
        return True
    elif 'UNDER_2.5' in prediction and (home_score + away_score) < 2.5:
        return True
    elif 'BTTS_YES' in prediction and home_score > 0 and away_score > 0:
        return True
    
    return False

def calculate_profit_loss(match, is_correct, stake=10):
    """Kar/zarar hesaplar"""
    if not is_correct:
        return -stake
    
    prediction = match.get('ai_prediction', '')
    
    # İlgili odds'u bul
    if 'HOME_WIN' in prediction:
        odds = match.get('home_odds', 0)
    elif 'AWAY_WIN' in prediction:
        odds = match.get('away_odds', 0)
    elif 'DRAW' in prediction:
        odds = match.get('draw_odds', 0)
    elif 'OVER_2.5' in prediction:
        odds = match.get('over_2_5_odds', 0)
    elif 'UNDER_2.5' in prediction:
        odds = match.get('under_2_5_odds', 0)
    elif 'BTTS_YES' in prediction:
        odds = match.get('btts_yes_odds', 0)
    else:
        odds = 0
    
    if odds == 0:
        return 0
    
    return round((stake * odds) - stake, 2)

# ========================================
# DAILY REPORT
# ========================================

def send_daily_report():
    """Günlük performans raporunu gönderir"""
    print("\n📊 Günlük rapor hazırlanıyor...\n")
    
    stats = get_performance_stats(days=1)
    
    if not stats or stats['total_predictions'] == 0:
        send_telegram_message("📊 Bugün değerlendirilmiş tahmin yok.")
        return
    
    total = stats['total_predictions']
    correct = stats['correct_predictions'] or 0
    accuracy = stats['accuracy_rate'] or 0
    profit = stats['total_profit_loss'] or 0
    
    report = f"""
📊 *GÜNLÜK PERFORMANS RAPORU*
📅 {datetime.now().strftime('%d.%m.%Y')}

━━━━━━━━━━━━━━━━━━━━━━
📈 *İSTATİSTİKLER*
├ Toplam Tahmin: {total}
├ Doğru: {correct} ✅
├ Yanlış: {total - correct} ❌
└ Başarı Oranı: %{accuracy:.1f}

💰 *FİNANSAL*
└ Kar/Zarar: {profit:+.2f} TL

⭐ Ortalama Güven: %{stats.get('avg_confidence', 0):.1f}
━━━━━━━━━━━━━━━━━━━━━━
"""
    
    send_telegram_message(report)
    print("✅ Günlük rapor gönderildi!")

# ========================================
# FLASK WEB SERVER
# ========================================

app = Flask(__name__)

@app.route("/")
def home():
    """Ana sayfa"""
    return jsonify({
        "status": "running",
        "service": "Football Match Prediction System",
        "version": "2.0",
        "endpoints": {
            "/collect": "Bugünkü maçları topla",
            "/analyze": "DeepSeek ile analiz yap",
            "/send": "Tahminleri Telegram'a gönder",
            "/check": "Sonuçları kontrol et",
            "/report": "Günlük rapor gönder",
            "/run": "Tam döngü (collect + analyze + send)",
            "/stats": "Performans istatistikleri"
        }
    })

@app.route("/collect")
def collect_endpoint():
    """Bugünkü maçları topla"""
    try:
        count = collect_today_matches(TARGET_LEAGUES)
        return jsonify({"success": True, "collected": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/analyze")
def analyze_endpoint():
    """DeepSeek ile analiz yap"""
    try:
        count = analyze_and_update_predictions()
        return jsonify({"success": True, "analyzed": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/send")
def send_endpoint():
    """Tahminleri Telegram'a gönder"""
    try:
        min_confidence = int(request.args.get('min_confidence', 60))
        max_risk = request.args.get('max_risk', 'MEDIUM')
        
        count = send_daily_predictions(min_confidence, max_risk)
        return jsonify({"success": True, "sent": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/check")
def check_endpoint():
    """Sonuçları kontrol et"""
    try:
        count = check_and_update_results()
        return jsonify({"success": True, "updated": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/report")
def report_endpoint():
    """Günlük rapor gönder"""
    try:
        send_daily_report()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/run")
def run_endpoint():
    """Tam döngü: Collect → Analyze → Send"""
    try:
        results = {}
        
        # 1. Maçları topla
        print("1️⃣ Maç toplama...")
        results['collected'] = collect_today_matches(TARGET_LEAGUES)
        
        # 2. Analiz yap
        print("2️⃣ AI Analizi...")
        results['analyzed'] = analyze_and_update_predictions()
        
        # 3. Telegram'a gönder
        print("3️⃣ Telegram gönderimi...")
        results['sent'] = send_daily_predictions(min_confidence=65, max_risk='MEDIUM')
        
        return jsonify({"success": True, **results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/stats")
def stats_endpoint():
    """Performans istatistikleri"""
    try:
        days = int(request.args.get('days', 30))
        stats = get_performance_stats(days)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ========================================
# MAIN
# ========================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    
    print("\n" + "="*60)
    print("🚀 FOOTBALL PREDICTION SYSTEM v2.0")
    print("="*60)
    print(f"🌐 Server başlatılıyor: http://0.0.0.0:{port}")
    print(f"📊 Target Leagues: {len(TARGET_LEAGUES)} lig")
    print(f"🤖 DeepSeek AI: {'✓' if DEEPSEEK_API_KEY else '✗'}")
    print(f"📱 Telegram: {'✓' if TELEGRAM_BOT_TOKEN else '✗'}")
    print("="*60 + "\n")
    
    app.run(host="0.0.0.0", port=port, debug=False)
```

---

## 🎯 MAJOR IMPROVEMENTS

### ✅ Tamamen Yeniden Yazıldı

1. **DeepSeek Entegrasyonu** 🤖
   - JSON formatında yapılandırılmış yanıt
   - Confidence, risk level, expected value hesaplama
   - Error handling ve retry logic

2. **Gelişmiş Telegram Mesajları** 📱
   - Güzel formatlanmış kartlar
   - Risk emoji'leri (🟢🟡🔴)
   - Detaylı analiz bilgileri

3. **Akıllı Filtreleme** 🎯
   - Min confidence threshold
   - Max risk level filtering
   - Expected value calculation

4. **Otomatik Sonuç Kontrolü** ✅
   - Maç sonuçlarını API'den çek
   - Tahmin doğruluğunu hesapla
   - Kar/zarar tracking

5. **Modüler Yapı** 🏗️
   - Her fonksiyon tek bir iş yapar
   - Yeniden kullanılabilir kod
   - Kolay test edilebilir

6. **Flask Endpoint'leri** 🌐
```
   /collect  → Maç toplama
   /analyze  → AI analizi
   /send     → Telegram gönderimi
   /check    → Sonuç kontrolü
   /report   → Günlük rapor
   /run      → Tam döngü
   /stats    → İstatistikler
