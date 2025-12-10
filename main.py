import os
import requests
import time
import json
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from database import (
    get_db_cursor, 
    get_pending_predictions,
    get_today_matches,
    mark_telegram_sent,
    get_performance_stats,
    execute_query
)
from data_collector import collect_today_matches

# Environment Variables
API_KEY = os.getenv("API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not all([API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY]):
    print("⚠️  WARNING: Some environment variables are missing!")
    print(f"   API_KEY: {'✅ OK' if API_KEY else '❌ MISSING'}")
    print(f"   TELEGRAM_BOT_TOKEN: {'✅ OK' if TELEGRAM_BOT_TOKEN else '❌ MISSING'}")
    print(f"   TELEGRAM_CHAT_ID: {'✅ OK' if TELEGRAM_CHAT_ID else '❌ MISSING'}")
    print(f"   DEEPSEEK_API_KEY: {'✅ OK' if DEEPSEEK_API_KEY else '❌ MISSING'}")

API_BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# EXPANDED TARGET LEAGUES
TARGET_LEAGUES = [
    39, 140, 135, 78, 61, 203,
    40, 141, 136, 94, 88, 144, 71, 128
]

# FIXED STAKE AMOUNT
STAKE_AMOUNT = 50  # 50 TL fixed stake
MIN_SEND_ODDS = 1.6  # minimum odds threshold for sending to Telegram
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "1") == "1"

def send_telegram_message(text: str, parse_mode="Markdown", disable_preview=True):
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram credentials missing!")
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
        print(f"❌ Telegram error: {e}")
        return False

def log_telegram_message(match_id, message_text, success, error_message=None):
    """Log Telegram message to database"""
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                INSERT INTO telegram_logs (
                    match_id, message_text, chat_id, success, error_message
                ) VALUES (%s, %s, %s, %s, %s)
            """, (match_id, message_text, TELEGRAM_CHAT_ID, success, error_message))
    except Exception as e:
        print(f"⚠️  Log error: {e}")

def validate_deepseek_response(data):
    """Validate DeepSeek response structure"""
    required_fields = ['prediction', 'confidence', 'reasoning', 'recommended_bet', 'risk_level', 'expected_value']
    
    if not isinstance(data, dict):
        return False
    
    for field in required_fields:
        if field not in data:
            return False
    
    # Validate prediction type
    valid_predictions = ['HOME_WIN', 'DRAW', 'AWAY_WIN', 'OVER_2.5', 'UNDER_2.5', 'BTTS_YES']
    if data['prediction'] not in valid_predictions:
        return False
    
    # Validate confidence range
    try:
        confidence = float(data['confidence'])
        if not (0 <= confidence <= 100):
            return False
    except (ValueError, TypeError):
        return False
    
    # Validate risk level
    if data['risk_level'] not in ['LOW', 'MEDIUM', 'HIGH']:
        return False
    
    return True

def deepseek_predict(match_data, max_retries=3):
    """Get AI prediction from DeepSeek with retry logic"""
    if not DEEPSEEK_API_KEY:
        return {
            'prediction': 'NO_API_KEY',
            'confidence': 0,
            'reasoning': 'DeepSeek API key not configured',
            'recommended_bet': 'SKIP',
            'risk_level': 'HIGH',
            'expected_value': 0
        }

    prompt = f"""You are a professional football analyst. Analyze this match and return ONLY valid JSON (no markdown, no extra text):

MATCH INFO:
Home: {match_data['home_team']}
Away: {match_data['away_team']}
League: {match_data['league']}

ODDS:
Home Win: {match_data.get('home_odds', 'N/A')}
Draw: {match_data.get('draw_odds', 'N/A')}
Away Win: {match_data.get('away_odds', 'N/A')}
Over 2.5: {match_data.get('over_2_5_odds', 'N/A')}
Under 2.5: {match_data.get('under_2_5_odds', 'N/A')}

STATS:
Home Form: {match_data.get('home_form', 'N/A')}
Away Form: {match_data.get('away_form', 'N/A')}
Home Avg Goals: {match_data.get('home_goals_avg', 'N/A')}
Away Avg Goals: {match_data.get('away_goals_avg', 'N/A')}

Return ONLY this exact JSON structure:
{
  "prediction": "HOME_WIN or DRAW or AWAY_WIN or OVER_2.5 or UNDER_2.5 or BTTS_YES",
  "confidence": 75,
  "reasoning": "Brief analysis max 150 chars",
  "recommended_bet": "Recommended bet with odds",
  "risk_level": "LOW or MEDIUM or HIGH",
  "expected_value": 1.15
}"""

    for attempt in range(max_retries):
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
                    "temperature": 0.3,
                    "max_tokens": 500
                },
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            ai_response = result["choices"][0]["message"]["content"]
            
            # Clean up response - remove markdown code blocks
            ai_response = ai_response.strip()
            # Remove markdown fences if any
            if ai_response.startswith('```'):
                ai_response = ai_response.lstrip('`').strip()
            ai_response = ai_response.replace('', '').replace('```', '').strip()
            
            # Extract JSON substring safely
            json_slice = ai_response
            if '{' in ai_response and '}' in ai_response:
                json_slice = ai_response[ai_response.find('{'):ai_response.rfind('}') + 1]
            
            prediction_data = json.loads(json_slice)
            
            # Validate response structure
            if not validate_deepseek_response(prediction_data):
                raise ValueError("Invalid response structure")
            
            return {
                'prediction': prediction_data.get('prediction', 'UNKNOWN'),
                'confidence': float(prediction_data.get('confidence', 0)),
                'reasoning': prediction_data.get('reasoning', 'No analysis available')[:150],
                'recommended_bet': prediction_data.get('recommended_bet', 'SKIP'),
                'risk_level': prediction_data.get('risk_level', 'HIGH'),
                'expected_value': float(prediction_data.get('expected_value', 0))
            }
            
        except json.JSONDecodeError as e:
            print(f"⚠️  JSON parse error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            return {
                'prediction': 'PARSE_ERROR',
                'confidence': 0,
                'reasoning': f'Failed to parse AI response after {max_retries} attempts',
                'recommended_bet': 'SKIP',
                'risk_level': 'HIGH',
                'expected_value': 0
            }
        except Exception as e:
            print(f"⚠️  DeepSeek API error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {
                'prediction': 'ERROR',
                'confidence': 0,
                'reasoning': str(e)[:150],
                'recommended_bet': 'SKIP',
                'risk_level': 'HIGH',
                'expected_value': 0
            }

def analyze_and_update_predictions():
    """Analyze matches with DeepSeek AI"""
    print("\n" + "="*60)
    print("🤖 DEEPSEEK AI ANALİZ BAŞLIYOR")
    print("="*60 + "\n")
    
    # Check API key
    if not DEEPSEEK_API_KEY:
        print("❌ HATA: DEEPSEEK_API_KEY bulunamadı!")
        return 0
    
    print(f"✅ DeepSeek API Key: {DEEPSEEK_API_KEY[:8]}...{DEEPSEEK_API_KEY[-4:]}\n")
    
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
          AND p.has_odds = TRUE
          AND p.home_odds IS NOT NULL
        ORDER BY p.match_date ASC
        LIMIT 20;
    """
    
    matches = execute_query(query)
    
    print(f"📊 Analiz edilecek maç sayısı: {len(matches) if matches else 0}\n")
    
    if not matches:
        print("💡 DURUM:")
        print("   • Tüm maçlar zaten analiz edilmiş VEYA")
        print("   • Bugün maç yok VEYA")
        print("   • Odds bilgisi eksik")
        
        # Check if there are any matches in DB without odds
        check_query = "SELECT COUNT(*) as count FROM predictions WHERE home_odds IS NULL AND match_date > NOW()"
        no_odds = execute_query(check_query)
        if no_odds and no_odds[0]['count'] > 0:
            print(f"\n⚠️  DİKKAT: {no_odds[0]['count']} maç odds bilgisi olmadan kaydedilmiş!")
            print("   Bu maçlar analiz edilemez. Veri toplama sürecini kontrol edin.\n")
        else:
            print("\n✅ Veritabanında eksik veri yok.\n")
        
        return 0
    
    analyzed_count = 0
    error_count = 0
    
    for idx, match in enumerate(matches, 1):
        print(f"[{idx}/{len(matches)}] 🔍 {match['home_team']} vs {match['away_team']}")
        
        ai_result = deepseek_predict(match)
        
        # Check if prediction was successful
        if ai_result['prediction'] in ['ERROR', 'PARSE_ERROR', 'NO_API_KEY']:
            print(f"    ❌ HATA: {ai_result['reasoning']}")
            error_count += 1
            continue
        
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
            
            risk_emoji = {'LOW': '🟢', 'MEDIUM': '🟡', 'HIGH': '🔴'}.get(ai_result['risk_level'], '⚪')
            print(f"    ✅ {ai_result['prediction']} | {risk_emoji} {ai_result['risk_level']} | Güven: {ai_result['confidence']:.0f}%")
            analyzed_count += 1
            time.sleep(2)
            
        except Exception as e:
            print(f"    ❌ DB Hatası: {e}")
            error_count += 1
    
    print("\n" + "="*60)
    print(f"✅ Analiz Tamamlandı: {analyzed_count} başarılı, {error_count} hatalı")
    print("="*60 + "\n")
    
    return analyzed_count

def format_prediction_message(match):
    """Format prediction message for Telegram with profit potential"""
    risk_emoji = {
        'LOW': '🟢',
        'MEDIUM': '🟡',
        'HIGH': '🔴'
    }.get(match.get('risk_level', 'HIGH'), '⚪')
    
    match_time = match['match_date'].strftime('%H:%M') if isinstance(match['match_date'], datetime) else str(match['match_date'])[11:16]
    
    # Calculate profit potential
    stake = STAKE_AMOUNT
    recommended_bet = match.get('recommended_bet', 'SKIP')
    
    # Find the odds for the recommended bet
    odds = 0
    if 'Home Win' in recommended_bet or 'HOME_WIN' in match.get('ai_prediction', ''):
        odds = match.get('home_odds', 0)
    elif 'Away Win' in recommended_bet or 'AWAY_WIN' in match.get('ai_prediction', ''):
        odds = match.get('away_odds', 0)
    elif 'Draw' in recommended_bet or 'DRAW' in match.get('ai_prediction', ''):
        odds = match.get('draw_odds', 0)
    elif 'Over' in recommended_bet or 'OVER_2.5' in match.get('ai_prediction', ''):
        odds = match.get('over_2_5_odds', 0)
    elif 'Under' in recommended_bet or 'UNDER_2.5' in match.get('ai_prediction', ''):
        odds = match.get('under_2_5_odds', 0)
    elif 'BTTS' in recommended_bet or 'BTTS_YES' in match.get('ai_prediction', ''):
        odds = match.get('btts_yes_odds', 0)
    
    potential_win = round(stake * odds, 2) if odds > 0 else 0
    potential_profit = round(potential_win - stake, 2) if potential_win > 0 else 0
    
    message = f"""
{'='*40}
{match['home_team']} vs {match['away_team']}
{match['league']}
⏰ Saat: {match_time}

📊 ORANLAR
Ev Sahibi: {match.get('home_odds', 'N/A')}
Beraberlik: {match.get('draw_odds', 'N/A')}
Deplasman: {match.get('away_odds', 'N/A')}

🎯 AI TAHMİNİ
{risk_emoji} {match.get('recommended_bet', 'SKIP')}

Güven: {match.get('ai_confidence', 0):.0f}%
Analiz: {match.get('ai_reasoning', 'Analiz yok')}
Risk: {match.get('risk_level', 'HIGH')}

💰 POTANSİYEL ({stake} TL)
Kazanç: {potential_win:.2f} TL
Kâr: +{potential_profit:.2f} TL
{'='*40}
"""
    return message.strip()

def send_daily_predictions(min_confidence=70, max_risk='LOW'):
    """Send predictions to Telegram - ONLY LOW RISK"""
    print("\n" + "="*60)
    print("📱 TELEGRAM BİLDİRİMLERİ GÖNDERİLİYOR")
    print("="*60 + "\n")
    
    risk_levels = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
    max_risk_value = risk_levels.get(max_risk, 1)
    
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
          AND p.has_odds = TRUE
          AND p.ai_confidence >= %s
          AND p.ai_confidence IS NOT NULL
        ORDER BY p.ai_confidence DESC, p.match_date ASC
        LIMIT 10;
    """
    
    matches = execute_query(query, params=(min_confidence,))
    
    if not matches:
        print("ℹ️  Gönderilecek tahmin yok\n")
        return 0
    
    # Filter by risk level + EV + odds threshold
    filtered_matches = []
    for m in matches:
        risk_ok = risk_levels.get(m.get('risk_level', 'HIGH'), 3) <= max_risk_value
        ev_ok = (m.get('expected_value') or 0) > 0
        rec = m.get('recommended_bet', '') or ''
        
        odds = 0
        if 'Home Win' in rec or 'HOME_WIN' in m.get('ai_prediction', ''):
            odds = m.get('home_odds', 0)
        elif 'Away Win' in rec or 'AWAY_WIN' in m.get('ai_prediction', ''):
            odds = m.get('away_odds', 0)
        elif 'Draw' in rec or 'DRAW' in m.get('ai_prediction', ''):
            odds = m.get('draw_odds', 0)
        elif 'Over' in rec or 'OVER_2.5' in m.get('ai_prediction', ''):
            odds = m.get('over_2_5_odds', 0)
        elif 'Under' in rec or 'UNDER_2.5' in m.get('ai_prediction', ''):
            odds = m.get('under_2_5_odds', 0)
        elif 'BTTS' in rec:
            odds = m.get('btts_yes_odds', 0)
        
        odds_ok = odds and odds >= MIN_SEND_ODDS
        
        if risk_ok and ev_ok and odds_ok:
            filtered_matches.append(m)
    
    if not filtered_matches:
        print(f"ℹ️  {max_risk} risk seviyesinde tahmin yok\n")
        return 0
    
    # Calculate total potential profit
    total_potential = 0
    for match in filtered_matches:
        recommended_bet = match.get('recommended_bet', 'SKIP')
        odds = 0
        
        if 'Home Win' in recommended_bet or 'HOME_WIN' in match.get('ai_prediction', ''):
            odds = match.get('home_odds', 0)
        elif 'Away Win' in recommended_bet or 'AWAY_WIN' in match.get('ai_prediction', ''):
            odds = match.get('away_odds', 0)
        elif 'Draw' in recommended_bet or 'DRAW' in match.get('ai_prediction', ''):
            odds = match.get('draw_odds', 0)
        elif 'Over' in recommended_bet or 'OVER_2.5' in match.get('ai_prediction', ''):
            odds = match.get('over_2_5_odds', 0)
        elif 'Under' in recommended_bet or 'UNDER_2.5' in match.get('ai_prediction', ''):
            odds = match.get('under_2_5_odds', 0)
        elif 'BTTS' in recommended_bet:
            odds = match.get('btts_yes_odds', 0)
        
        if odds > 0:
            profit = (STAKE_AMOUNT * odds) - STAKE_AMOUNT
            total_potential += profit
    
    header = f"""
🔥 GÜNLÜK VIP TAHMİNLER 🔥
📅 {datetime.now().strftime('%d.%m.%Y')}
🎯 Toplam: {len(filtered_matches)} maç

💰 TOPLAM POTANSİYEL KÂR
{total_potential:.2f} TL (hepsi tutarsa)

📊 Filtre: Min %{min_confidence} güven | Max Risk: {max_risk}
"""
    
    send_telegram_message(header)
    time.sleep(1)
    
    sent_count = 0
    
    for match in filtered_matches:
        message = format_prediction_message(match)
        success = send_telegram_message(message)
        
        log_telegram_message(
            match['match_id'],
            message,
            success,
            None if success else "Send failed"
        )
        
        if success:
            mark_telegram_sent(match['match_id'], TELEGRAM_CHAT_ID)
            sent_count += 1
            print(f"  ✅ {match['home_team']} vs {match['away_team']}")
        else:
            print(f"  ❌ {match['home_team']} vs {match['away_team']}")
        
        time.sleep(2)
    
    footer = f"""
{'='*40}
✅ {sent_count} tahmin gönderildi
🤖 DeepSeek AI ile güçlendirildi
💰 Bahis: {STAKE_AMOUNT} TL/maç
{'='*40}
"""
    send_telegram_message(footer)
    
    print(f"\n✅ {sent_count} tahmin başarıyla gönderildi!\n")
    return sent_count

def check_and_update_results():
    """Check match results and update database"""
    print("\n" + "="*60)
    print("🔍 MAÇ SONUÇLARI KONTROL EDİLİYOR")
    print("="*60 + "\n")
    
    query = """
        SELECT * FROM predictions
        WHERE result IS NULL
          AND match_date < NOW()
          AND match_date > NOW() - INTERVAL '7 days'
        ORDER BY match_date DESC;
    """
    
    matches = execute_query(query)
    
    if not matches:
        print("ℹ️  Kontrol edilecek maç yok\n")
        return 0
    
    print(f"📊 {len(matches)} maç kontrol ediliyor...\n")
    
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
            
            if status != "FT":
                continue
            
            home_score = fixture["goals"]["home"]
            away_score = fixture["goals"]["away"]
            
            prediction = match.get('ai_prediction', '')
            is_correct = False
            
            if 'HOME_WIN' in prediction and home_score > away_score:
                is_correct = True
            elif 'AWAY_WIN' in prediction and away_score > home_score:
                is_correct = True
            elif 'DRAW' in prediction and home_score == away_score:
                is_correct = True
            elif 'OVER_2.5' in prediction and (home_score + away_score) > 2.5:
                is_correct = True
            elif 'UNDER_2.5' in prediction and (home_score + away_score) < 2.5:
                is_correct = True
            elif 'BTTS_YES' in prediction and home_score > 0 and away_score > 0:
                is_correct = True
            
            stake = STAKE_AMOUNT
            profit_loss = -stake
            
            if is_correct:
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
                
                if odds > 0:
                    profit_loss = round((stake * odds) - stake, 2)
            
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
            print(f"  {status_icon} {match['home_team']} {home_score}-{away_score} {match['away_team']} ({profit_loss:+.2f} TL)")
            updated_count += 1
            
            time.sleep(1)
            
        except Exception as e:
            print(f"  ⚠️  {match['home_team']} - Hata: {e}")
    
    print(f"\n✅ {updated_count} maç sonucu güncellendi!\n")
    return updated_count

def send_daily_report():
    """Send daily performance report"""
    print("\n" + "="*60)
    print("📊 GÜNLÜK RAPOR HAZIRLANIYOR")
    print("="*60 + "\n")
    
    stats = get_performance_stats(days=1)
    
    if not stats or stats['total_predictions'] == 0:
        message = "📊 Bugün rapor edilecek tahmin yok."
        send_telegram_message(message)
        print(message + "\n")
        return
    
    total = stats['total_predictions']
    correct = stats['correct_predictions'] or 0
    accuracy = stats['accuracy_rate'] or 0
    profit = stats['total_profit_loss'] or 0
    
    report = f"""
📊 GÜNLÜK PERFORMANS RAPORU
📅 {datetime.now().strftime('%d.%m.%Y')}

{'='*40}
📈 İSTATİSTİKLER
Toplam Tahmin: {total}
Doğru: {correct} ✅
Yanlış: {total - correct} ❌
Başarı Oranı: %{accuracy:.1f}

💰 FİNANSAL ({STAKE_AMOUNT} TL/maç)
Kâr/Zarar: {profit:+.2f} TL

⭐ Ortalama Güven: %{stats.get('avg_confidence', 0):.1f}
{'='*40}
"""
    
    send_telegram_message(report)
    print("✅ Günlük rapor gönderildi!\n")


# ============================================
# SCHEDULER
# ============================================
def start_scheduler():
    """
    Arka plan scheduler: toplama, analiz, gönderim, sonuç kontrolü, rapor.
    Varsayılan olarak açık (SCHEDULER_ENABLED=1). Kapatmak için env=0.
    """
    if not SCHEDULER_ENABLED:
        print("ℹ️ Scheduler devre dışı (SCHEDULER_ENABLED=0)")
        return None
    
    scheduler = BackgroundScheduler(timezone="UTC")
    
    # Günlük sabah 07:00 UTC - maç toplama
    scheduler.add_job(
        lambda: collect_today_matches(TARGET_LEAGUES),
        CronTrigger(hour=7, minute=0, timezone="UTC"),
        id="collect_morning",
        replace_existing=True,
        max_instances=1
    )
    
    # Saat başı - yeni maçları analiz et
    scheduler.add_job(
        analyze_and_update_predictions,
        CronTrigger(minute=5, timezone="UTC"),
        id="analyze_hourly",
        replace_existing=True,
        max_instances=1
    )
    
    # Her 2 saatte bir - düşük risk tahminleri gönder
    scheduler.add_job(
        lambda: send_daily_predictions(min_confidence=70, max_risk='LOW'),
        CronTrigger(minute=10, hour="*/2", timezone="UTC"),
        id="send_predictions",
        replace_existing=True,
        max_instances=1
    )
    
    # Gece 23:30 UTC - sonuç kontrolü
    scheduler.add_job(
        check_and_update_results,
        CronTrigger(hour=23, minute=30, timezone="UTC"),
        id="check_results",
        replace_existing=True,
        max_instances=1
    )
    
    # Gece 23:45 UTC - günlük rapor
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=23, minute=45, timezone="UTC"),
        id="daily_report",
        replace_existing=True,
        max_instances=1
    )
    
    scheduler.start()
    print("✅ Scheduler başlatıldı (UTC zamanlaması)")
    return scheduler

# Flask App
app = Flask(__name__)
scheduler = start_scheduler()

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "service": "Football Match Prediction System",
        "version": "2.1",
        "stake_amount": STAKE_AMOUNT,
        "target_leagues": len(TARGET_LEAGUES),
        "improvements": [
            "DeepSeek retry mechanism (3 attempts)",
            "Response validation",
            "Better error handling",
            "Detailed logging",
            "Exponential backoff"
        ],
        "endpoints": {
            "/setup": "Create database tables (run once)",
            "/collect": "Collect today's matches",
            "/analyze": "Analyze with DeepSeek AI",
            "/send": "Send predictions to Telegram",
            "/check": "Check match results",
            "/report": "Send daily report",
            "/run": "Full cycle (collect + analyze + send)",
            "/stats": "Performance statistics"
        }
    })

@app.route("/setup")
def setup_endpoint():
    """Create database tables - run once!"""
    try:
        print("\n" + "="*60)
        print("📊 VERİTABANI TABLOLARI OLUŞTURULUYOR...")
        print("="*60 + "\n")
        
        from create_tables import create_tables
        create_tables()
        
        return jsonify({
            "success": True,
            "message": "Database tables created successfully!",
            "tables": [
                "predictions",
                "match_stats", 
                "telegram_logs",
                "performance_summary"
            ],
            "views": [
                "pending_predictions",
                "daily_performance"
            ],
            "next_step": "Run /run endpoint to start collecting matches"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Failed to create tables. Check logs for details."
        }), 500

@app.route("/collect")
def collect_endpoint():
    try:
        count = collect_today_matches(TARGET_LEAGUES)
        return jsonify({"success": True, "collected": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/analyze")
def analyze_endpoint():
    try:
        count = analyze_and_update_predictions()
        return jsonify({"success": True, "analyzed": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/send")
def send_endpoint():
    try:
        sent = send_daily_predictions()
        return jsonify({"success": True, "sent": sent})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/check")
def check_endpoint():
    try:
        updated = check_and_update_results()
        return jsonify({"success": True, "updated": updated})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/report")
def report_endpoint():
    try:
        send_daily_report()
        return jsonify({"success": True, "message": "Report sent"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/run")
def run_endpoint():
    """
    Full cycle: collect -> analyze -> send
    """
    try:
        collected = collect_today_matches(TARGET_LEAGUES)
        analyzed = analyze_and_update_predictions()
        sent = send_daily_predictions()
        return jsonify({
            "success": True,
            "collected": collected,
            "analyzed": analyzed,
            "sent": sent
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))**database.py**
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
    close_pool()**data_collector.py**
import requests
from datetime import datetime, timedelta
from database import get_db, close_db
import os
import time

API_KEY = os.getenv("API_KEY")
HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}
API_BASE = "https://v3.football.api-sports.io"

# Multiple bookmakers to try (in order of preference)
BOOKMAKERS = [
    8,   # Bet365 (Most reliable)
    11,  # Betfair
    5,   # William Hill
    6,   # Bwin
    9,   # 188Bet
    12,  # Unibet
    3,   # Pinnacle
]

# Rate limiting
LAST_REQUEST_TIME = None
MIN_REQUEST_INTERVAL = 1  # seconds

def rate_limit():
    """API rate limit kontrolü"""
    global LAST_REQUEST_TIME
    if LAST_REQUEST_TIME:
        elapsed = time.time() - LAST_REQUEST_TIME
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    LAST_REQUEST_TIME = time.time()

def api_request(url, params=None, retry_count=2):
    """Hata yönetimli API isteği with retry"""
    for attempt in range(retry_count):
        rate_limit()
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("errors"):
                error_msg = data['errors']
                print(f"⚠️ API Hatası: {error_msg}")
                if attempt < retry_count - 1:
                    time.sleep(2)
                    continue
                return None
                
            return data
        except requests.exceptions.RequestException as e:
            print(f"⚠️ İstek hatası (deneme {attempt + 1}/{retry_count}): {e}")
            if attempt < retry_count - 1:
                time.sleep(2)
                continue
            return None
    
    return None

def get_team_form(team_id):
    """Takımın son 5 maçının formu"""
    url = f"{API_BASE}/fixtures"
    params = {"team": team_id, "last": 5}
    
    data = api_request(url, params)
    if not data:
        return "N/A"
    
    form = ""
    for match in data.get("response", []):
        try:
            teams = match["teams"]
            goals = match["goals"]
            
            # Takımın ev sahibi mi deplasman mı olduğunu bul
            is_home = teams["home"]["id"] == team_id
            goals_for = goals["home"] if is_home else goals["away"]
            goals_against = goals["away"] if is_home else goals["home"]
            
            if goals_for > goals_against:
                form += "W"
            elif goals_for == goals_against:
                form += "D"
            else:
                form += "L"
        except (KeyError, TypeError):
            continue
    
    return form or "N/A"

def get_odds_from_bookmaker(fixture_id, bookmaker_id):
    """Tek bir bookmaker'dan odds çek"""
    url = f"{API_BASE}/odds"
    params = {
        "fixture": fixture_id,
        "bookmaker": bookmaker_id
    }
    
    data = api_request(url, params)
    if not data or not data.get("response"):
        return None
    
    odds_data = {
        "home_odds": None,
        "draw_odds": None,
        "away_odds": None,
        "over_2_5_odds": None,
        "under_2_5_odds": None,
        "btts_yes_odds": None,
        "btts_no_odds": None,
        "bookmaker_id": bookmaker_id
    }
    
    try:
        if not data["response"] or not data["response"][0].get("bookmakers"):
            return None
            
        bookmaker = data["response"][0]["bookmakers"][0]
        
        for bet in bookmaker["bets"]:
            bet_name = bet["name"]
            
            # Match Winner (1X2)
            if bet_name == "Match Winner":
                for value in bet["values"]:
                    if value["value"] == "Home":
                        odds_data["home_odds"] = float(value["odd"])
                    elif value["value"] == "Draw":
                        odds_data["draw_odds"] = float(value["odd"])
                    elif value["value"] == "Away":
                        odds_data["away_odds"] = float(value["odd"])
            
            # Goals Over/Under
            elif bet_name == "Goals Over/Under":
                for value in bet["values"]:
                    if "2.5" in value["value"]:
                        if "Over" in value["value"]:
                            odds_data["over_2_5_odds"] = float(value["odd"])
                        elif "Under" in value["value"]:
                            odds_data["under_2_5_odds"] = float(value["odd"])
            
            # Both Teams Score
            elif bet_name == "Both Teams Score":
                for value in bet["values"]:
                    if value["value"] == "Yes":
                        odds_data["btts_yes_odds"] = float(value["odd"])
                    elif value["value"] == "No":
                        odds_data["btts_no_odds"] = float(value["odd"])
        
        # Check if we got at least basic odds
        if odds_data["home_odds"] and odds_data["draw_odds"] and odds_data["away_odds"]:
            return odds_data
        
        return None
    
    except (KeyError, IndexError, ValueError) as e:
        print(f"⚠️ Odds parse hatası (bookmaker {bookmaker_id}): {e}")
        return None

def get_odds(fixture_id):
    """Maç için bahis oranlarını çek - MULTIPLE BOOKMAKERS"""
    print(f"  ↳ Odds bilgileri alınıyor...")
    
    for bookmaker_id in BOOKMAKERS:
        bookmaker_names = {
            8: "Bet365",
            11: "Betfair", 
            5: "William Hill",
            6: "Bwin",
            9: "188Bet",
            12: "Unibet",
            3: "Pinnacle"
        }
        bookmaker_name = bookmaker_names.get(bookmaker_id, f"Bookmaker {bookmaker_id}")
        
        print(f"     → {bookmaker_name} deneniyor...")
        odds = get_odds_from_bookmaker(fixture_id, bookmaker_id)
        
        if odds:
            odds["odds_source"] = bookmaker_name
            print(f"     ✅ {bookmaker_name}'den alındı!")
            return odds
        
        time.sleep(0.5)  # Bookmaker'lar arası kısa bekleme
    
    print(f"     ❌ Hiçbir bookmaker'dan odds alınamadı!")
    return None

def calculate_team_stats(team_id, last_n=10):
    """Takım istatistiklerini hesapla"""
    url = f"{API_BASE}/fixtures"
    params = {"team": team_id, "last": last_n}
    
    data = api_request(url, params)
    if not data:
        return {"goals_avg": 0, "conceded_avg": 0}
    
    total_goals = 0
    total_conceded = 0
    match_count = 0
    
    for match in data.get("response", []):
        try:
            teams = match["teams"]
            goals = match["goals"]
            
            is_home = teams["home"]["id"] == team_id
            goals_for = goals["home"] if is_home else goals["away"]
            goals_against = goals["away"] if is_home else goals["home"]
            
            total_goals += goals_for
            total_conceded += goals_against
            match_count += 1
        except (KeyError, TypeError):
            continue
    
    if match_count == 0:
        return {"goals_avg": 0, "conceded_avg": 0}
    
    return {
        "goals_avg": round(total_goals / match_count, 2),
        "conceded_avg": round(total_conceded / match_count, 2)
    }

def collect_match_data(fixture):
    """Tek maç için detaylı veri topla ve kaydet - ODDS YOKSA DA KAYDET"""
    try:
        fixture_id = str(fixture["fixture"]["id"])
        league = fixture["league"]["name"]
        match_date = fixture["fixture"]["date"]
        
        home = fixture["teams"]["home"]
        away = fixture["teams"]["away"]
        home_team = home["name"]
        away_team = away["name"]
        home_id = home["id"]
        away_id = away["id"]
        
        print(f"\n📊 Veri toplama: {home_team} vs {away_team}")
        
        # Form verileri
        print("  ↳ Form bilgileri alınıyor...")
        home_form = get_team_form(home_id)
        away_form = get_team_form(away_id)
        
        # İstatistikler
        print("  ↳ İstatistikler hesaplanıyor...")
        home_stats = calculate_team_stats(home_id)
        away_stats = calculate_team_stats(away_id)
        
        # Odds verileri - TRY ALL BOOKMAKERS
        odds = get_odds(fixture_id)
        
        # Veritabanına kaydet (odds yoksa da kaydet!)
        conn = get_db()
        cur = conn.cursor()
        
        if odds:
            # Odds varsa normal kayıt
            cur.execute("""
                INSERT INTO predictions (
                    match_id, home_team, away_team, league, match_date,
                    has_odds, odds_source,
                    home_odds, draw_odds, away_odds,
                    over_2_5_odds, under_2_5_odds,
                    btts_yes_odds, btts_no_odds,
                    ai_prediction, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s
                )
                ON CONFLICT (match_id) DO NOTHING
            """, (
                fixture_id, home_team, away_team, league, match_date,
                True, odds.get("odds_source"),
                odds["home_odds"], odds["draw_odds"], odds["away_odds"],
                odds["over_2_5_odds"], odds["under_2_5_odds"],
                odds["btts_yes_odds"], odds["btts_no_odds"],
                f"Form: H({home_form}) A({away_form}) | Avg Goals: H({home_stats['goals_avg']}) A({away_stats['goals_avg']})",
                datetime.now()
            ))
            print(f"  ✅ {home_team} - {away_team} (ODDS ile) kaydedildi!")
        else:
            # Odds yoksa NULL ile kaydet - YINE DE KAYDET!
            cur.execute("""
                INSERT INTO predictions (
                    match_id, home_team, away_team, league, match_date,
                    has_odds, odds_source,
                    home_odds, draw_odds, away_odds,
                    over_2_5_odds, under_2_5_odds,
                    btts_yes_odds, btts_no_odds,
                    ai_prediction, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    NULL, NULL, NULL,
                    NULL, NULL,
                    NULL, NULL,
                    %s, %s
                )
                ON CONFLICT (match_id) DO NOTHING
            """, (
                fixture_id, home_team, away_team, league, match_date,
                False, None,
                f"NO_ODDS | Form: H({home_form}) A({away_form}) | Avg Goals: H({home_stats['goals_avg']}) A({away_stats['goals_avg']})",
                datetime.now()
            ))
            print(f"  ⚠️ {home_team} - {away_team} (ODDS OLMADAN) kaydedildi!")
        
        # İstatistik tablosuna kaydet
        cur.execute("""
            INSERT INTO match_stats (
                match_id, home_form, away_form,
                home_goals_avg, away_goals_avg,
                created_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id) DO NOTHING
        """, (
            fixture_id, home_form, away_form,
            home_stats['goals_avg'], away_stats['goals_avg'],
            datetime.now()
        ))
        
        conn.commit()
        close_db(conn)
        
        return True
        
    except Exception as e:
        print(f"  ❌ Kritik Hata: {e}")
        return False

def collect_today_matches(league_ids=None):
    """Bugünkü maçları topla"""
    if league_ids is None:
        league_ids = [
            39,   # Premier League
            140,  # La Liga
            135,  # Serie A
            78,   # Bundesliga
            61,   # Ligue 1
            203,  # Süper Lig
        ]
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    print("\n" + "="*60)
    print(f"🔍 {today} TARİHLİ MAÇLAR ARANLIYOR")
    print("="*60 + "\n")
    
    total_collected = 0
    total_with_odds = 0
    total_without_odds = 0
    
    for league_id in league_ids:
        url = f"{API_BASE}/fixtures"
        params = {
            "league": league_id,
            "date": today
        }
        
        data = api_request(url, params)
        if not data:
            print(f"⚠️ Lig {league_id}: API yanıt vermedi")
            continue
        
        fixtures = data.get("response", [])
        
        if not fixtures:
            print(f"ℹ️  Lig {league_id}: Maç yok")
            continue
            
        print(f"📌 Lig {league_id}: {len(fixtures)} maç bulundu")
        
        for fixture in fixtures:
            if collect_match_data(fixture):
                total_collected += 1
                # Check if odds were found
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT home_odds FROM predictions 
                    WHERE match_id = %s
                """, (str(fixture["fixture"]["id"]),))
                result = cur.fetchone()
                close_db(conn)
                
                if result and result[0] is not None:
                    total_with_odds += 1
                else:
                    total_without_odds += 1
                
                time.sleep(2)  # API'ye nazik ol
    
    print("\n" + "="*60)
    print("📊 TOPLAMA SONUÇLARI")
    print("="*60)
    print(f"✅ Toplam Kaydedilen: {total_collected} maç")
    print(f"🟢 Odds ile: {total_with_odds} maç")
    print(f"🟡 Odds olmadan: {total_without_odds} maç")
    
    if total_without_odds > 0:
        print(f"\n⚠️ DİKKAT: {total_without_odds} maçın odds bilgisi eksik!")
        print("   Bu maçlar DeepSeek tarafından analiz EDİLEMEZ.")
        print("   Ancak veritabanında kayıtlı, ileride odds eklenebilir.")
    
    print("="*60 + "\n")
    
    return total_collected

if __name__ == "__main__":
    collect_today_matches()**dashboard.py**
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from database import (
    execute_query,
    get_performance_stats,
    get_league_performance,
    health_check
)

# Page config
st.set_page_config(
    page_title="⚽ Football Predictions Dashboard",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        font-weight: bold;
        text-align: center;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        text-align: center;
    }
    .stMetric {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown('<h1 class="main-header">⚽ FOOTBALL PREDICTIONS DASHBOARD</h1>', unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.image("https://img.icons8.com/color/96/000000/football.png", width=80)
    st.title("⚙️ Ayarlar")
    
    # Date range selector
    date_range = st.selectbox(
        "📅 Zaman Aralığı",
        ["Son 7 Gün", "Son 30 Gün", "Son 90 Gün", "Tüm Zamanlar"],
        index=1
    )
    
    days_map = {
        "Son 7 Gün": 7,
        "Son 30 Gün": 30,
        "Son 90 Gün": 90,
        "Tüm Zamanlar": 36500
    }
    selected_days = days_map[date_range]
    
    # Risk filter
    risk_filter = st.multiselect(
        "🎯 Risk Seviyesi",
        ["LOW", "MEDIUM", "HIGH"],
        default=["LOW", "MEDIUM", "HIGH"]
    )
    
    # League filter
    league_filter = st.text_input("🏆 Lig Filtrele (opsiyonel)")
    
    st.divider()
    
    # Database health check
    is_healthy, latency = health_check()
    if is_healthy:
        st.success(f"✅ Database Bağlantısı: {latency}ms")
    else:
        st.error("❌ Database Bağlantısı: Hatalı")
    
    st.divider()
    st.caption("🤖 Powered by DeepSeek AI")
    st.caption("💻 Developed with Streamlit")

# Main content
tab1, tab2, tab3, tab4 = st.tabs(["📊 Genel Bakış", "📈 Performans", "🏆 Ligler", "📋 Tahminler"])

# ============================================
# TAB 1: GENEL BAKIŞ
# ============================================
with tab1:
    # Get performance stats
    stats = get_performance_stats(selected_days)
    
    if stats and stats['total_predictions'] > 0:
        # Key Metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric(
                "🎯 Toplam Tahmin",
                f"{stats['total_predictions']}",
                delta=None
            )
        
        with col2:
            accuracy = stats['accuracy_rate'] or 0
            st.metric(
                "✅ Başarı Oranı",
                f"%{accuracy:.1f}",
                delta=f"%{accuracy - 50:.1f}" if accuracy > 50 else f"%{accuracy - 50:.1f}"
            )
        
        with col3:
            correct = stats['correct_predictions'] or 0
            st.metric(
                "🟢 Doğru",
                f"{correct}",
                delta=None
            )
        
        with col4:
            wrong = stats['total_predictions'] - correct
            st.metric(
                "🔴 Yanlış",
                f"{wrong}",
                delta=None
            )
        
        with col5:
            profit = stats['total_profit_loss'] or 0
            st.metric(
                "💰 Kar/Zarar",
                f"{profit:+.0f} TL",
                delta=f"{profit:+.0f} TL"
            )
        
        st.divider()
        
        # Charts
        col_left, col_right = st.columns(2)
        
        with col_left:
            st.subheader("📊 Başarı Dağılımı")
            
            # Pie chart
            fig_pie = go.Figure(data=[go.Pie(
                labels=['Doğru', 'Yanlış'],
                values=[correct, wrong],
                hole=0.4,
                marker_colors=['#00c853', '#ff1744']
            )])
            fig_pie.update_layout(
                height=300,
                showlegend=True,
                annotations=[dict(text=f'%{accuracy:.1f}', x=0.5, y=0.5, font_size=20, showarrow=False)]
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        
        with col_right:
            st.subheader("🎲 Risk Dağılımı")
            
            # Risk distribution
            risk_query = f"""
                SELECT 
                    risk_level,
                    COUNT(*) as count
                FROM predictions
                WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
                    AND result IS NOT NULL
                    AND risk_level IS NOT NULL
                GROUP BY risk_level
                ORDER BY risk_level;
            """
            risk_data = execute_query(risk_query)
            
            if risk_data:
                df_risk = pd.DataFrame(risk_data)
                fig_risk = px.bar(
                    df_risk,
                    x='risk_level',
                    y='count',
                    color='risk_level',
                    color_discrete_map={'LOW': '#00c853', 'MEDIUM': '#ffc107', 'HIGH': '#ff1744'},
                    labels={'risk_level': 'Risk Seviyesi', 'count': 'Tahmin Sayısı'}
                )
                fig_risk.update_layout(height=300, showlegend=False)
                st.plotly_chart(fig_risk, use_container_width=True)
        
        st.divider()
        
        # Daily performance trend
        st.subheader("📈 Günlük Performans Trendi")
        
        daily_query = f"""
            SELECT 
                DATE(match_date) as day,
                COUNT(*) as total,
                COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct,
                SUM(COALESCE(profit_loss, 0)) as profit
            FROM predictions
            WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
                AND result IS NOT NULL
            GROUP BY DATE(match_date)
            ORDER BY day;
        """
        daily_data = execute_query(daily_query)
        
        if daily_data:
            df_daily = pd.DataFrame(daily_data)
            df_daily['accuracy'] = (df_daily['correct'] / df_daily['total'] * 100).round(1)
            df_daily['cumulative_profit'] = df_daily['profit'].cumsum()
            
            fig_trend = go.Figure()
            
            # Accuracy line
            fig_trend.add_trace(go.Scatter(
                x=df_daily['day'],
                y=df_daily['accuracy'],
                name='Başarı Oranı (%)',
                line=dict(color='#667eea', width=3),
                yaxis='y'
            ))
            
            # Cumulative profit line
            fig_trend.add_trace(go.Scatter(
                x=df_daily['day'],
                y=df_daily['cumulative_profit'],
                name='Kümülatif Kâr (TL)',
                line=dict(color='#764ba2', width=3),
                yaxis='y2'
            ))
            
            fig_trend.update_layout(
                height=400,
                xaxis=dict(title='Tarih'),
                yaxis=dict(title='Başarı Oranı (%)', side='left', showgrid=False),
                yaxis2=dict(title='Kümülatif Kâr (TL)', side='right', overlaying='y', showgrid=False),
                hovermode='x unified'
            )
            
            st.plotly_chart(fig_trend, use_container_width=True)
    
    else:
        st.warning("⚠️ Bu tarih aralığında veri bulunamadı.")

# ============================================
# TAB 2: PERFORMANS ANALİZİ
# ============================================
with tab2:
    st.subheader("📊 Detaylı Performans Analizi")
    
    # Confidence vs Accuracy
    conf_query = f"""
        SELECT 
            CASE 
                WHEN ai_confidence >= 80 THEN '80-100%'
                WHEN ai_confidence >= 70 THEN '70-80%'
                WHEN ai_confidence >= 60 THEN '60-70%'
                ELSE '< 60%'
            END as confidence_range,
            COUNT(*) as total,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct,
            ROUND(COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / COUNT(*) * 100, 1) as accuracy
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
            AND result IS NOT NULL
            AND ai_confidence IS NOT NULL
        GROUP BY confidence_range
        ORDER BY confidence_range DESC;
    """
    conf_data = execute_query(conf_query)
    
    if conf_data:
        df_conf = pd.DataFrame(conf_data)
        
        col1, col2 = st.columns(2)
        
        with col1:
            fig_conf = px.bar(
                df_conf,
                x='confidence_range',
                y='accuracy',
                color='accuracy',
                color_continuous_scale='RdYlGn',
                labels={'confidence_range': 'Güven Aralığı', 'accuracy': 'Başarı Oranı (%)'},
                title='Güven Skoru vs Başarı Oranı'
            )
            fig_conf.update_layout(height=400, showlegend=False)
            st.plotly_chart(fig_conf, use_container_width=True)
        
        with col2:
            fig_total = px.bar(
                df_conf,
                x='confidence_range',
                y='total',
                color='total',
                color_continuous_scale='Blues',
                labels={'confidence_range': 'Güven Aralığı', 'total': 'Tahmin Sayısı'},
                title='Güven Aralığına Göre Tahmin Dağılımı'
            )
            fig_total.update_layout(height=400, showlegend=False)
            st.plotly_chart(fig_total, use_container_width=True)
    
    st.divider()
    
    # Prediction type performance
    st.subheader("🎯 Tahmin Tipi Bazlı Performans")
    
    pred_query = f"""
        SELECT 
            ai_prediction as prediction_type,
            COUNT(*) as total,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct,
            ROUND(COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / COUNT(*) * 100, 1) as accuracy,
            SUM(COALESCE(profit_loss, 0)) as profit
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
            AND result IS NOT NULL
            AND ai_prediction IS NOT NULL
            AND ai_prediction NOT LIKE '%Form:%'
        GROUP BY ai_prediction
        ORDER BY total DESC
        LIMIT 10;
    """
    pred_data = execute_query(pred_query)
    
    if pred_data:
        df_pred = pd.DataFrame(pred_data)
        
        fig_pred = go.Figure()
        
        fig_pred.add_trace(go.Bar(
            name='Toplam',
            x=df_pred['prediction_type'],
            y=df_pred['total'],
            marker_color='lightblue'
        ))
        
        fig_pred.add_trace(go.Bar(
            name='Doğru',
            x=df_pred['prediction_type'],
            y=df_pred['correct'],
            marker_color='green'
        ))
        
        fig_pred.update_layout(
            height=400,
            barmode='group',
            xaxis_title='Tahmin Tipi',
            yaxis_title='Sayı'
        )
        
        st.plotly_chart(fig_pred, use_container_width=True)
        
        # Table
        st.dataframe(
            df_pred.style.background_gradient(subset=['accuracy'], cmap='RdYlGn'),
            use_container_width=True
        )

# ============================================
# TAB 3: LİG PERFORMANSI
# ============================================
with tab3:
    st.subheader("🏆 Lig Bazlı Performans")
    
    league_data = get_league_performance(selected_days)
    
    if league_data:
        df_league = pd.DataFrame(league_data)
        
        # Top performing leagues
        st.markdown("### 🥇 En Başarılı Ligler")
        
        fig_league = px.bar(
            df_league.head(10),
            x='league',
            y='accuracy_rate',
            color='accuracy_rate',
            color_continuous_scale='RdYlGn',
            labels={'league': 'Lig', 'accuracy_rate': 'Başarı Oranı (%)'},
            hover_data=['total_predictions', 'total_profit_loss']
        )
        fig_league.update_layout(height=400, showlegend=False)
        fig_league.update_xaxes(tickangle=45)
        st.plotly_chart(fig_league, use_container_width=True)
        
        st.divider()
        
        # League performance table
        st.markdown("### 📊 Tüm Ligler Detay")
        
        # Add emoji based on profit
        def profit_emoji(val):
            if val > 0:
                return f"🟢 +{val:.0f} TL"
            elif val < 0:
                return f"🔴 {val:.0f} TL"
            else:
                return "⚪ 0 TL"
        
        df_league['Kar/Zarar'] = df_league['total_profit_loss'].apply(profit_emoji)
        df_league['Başarı'] = df_league['accuracy_rate'].apply(lambda x: f"%{x:.1f}")
        
        display_df = df_league[['league', 'total_predictions', 'correct_predictions', 'Başarı', 'Kar/Zarar']]
        display_df.columns = ['Lig', 'Toplam', 'Doğru', 'Başarı Oranı', 'Kar/Zarar']
        
        st.dataframe(
            display_df,
            use_container_width=True,
            height=400
        )
    else:
        st.warning("⚠️ Lig performans verisi bulunamadı.")

# ============================================
# TAB 4: TAHMİN LİSTESİ
# ============================================
with tab4:
    st.subheader("📋 Tüm Tahminler")
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        result_filter = st.selectbox(
            "Sonuç Durumu",
            ["Tümü", "Doğru", "Yanlış", "Beklemede"]
        )
    
    with col2:
        sort_by = st.selectbox(
            "Sırala",
            ["Tarih (Yeni)", "Tarih (Eski)", "Güven (Yüksek)", "Kâr (Yüksek)"]
        )
    
    with col3:
        limit = st.number_input("Göster", min_value=10, max_value=100, value=20, step=10)
    
    # Build query
    risk_filter_sql = "'" + "','".join(risk_filter) + "'"
    
    where_clauses = [
        f"match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'",
        f"risk_level IN ({risk_filter_sql})"
    ]
    
    if league_filter:
        where_clauses.append(f"league ILIKE '%{league_filter}%'")
    
    if result_filter == "Doğru":
        where_clauses.append("is_correct = TRUE")
    elif result_filter == "Yanlış":
        where_clauses.append("is_correct = FALSE")
    elif result_filter == "Beklemede":
        where_clauses.append("result IS NULL AND match_date > NOW()")
    
    order_map = {
        "Tarih (Yeni)": "match_date DESC",
        "Tarih (Eski)": "match_date ASC",
        "Güven (Yüksek)": "ai_confidence DESC",
        "Kâr (Yüksek)": "profit_loss DESC NULLS LAST"
    }
    
    predictions_query = f"""
        SELECT 
            match_id,
            home_team,
            away_team,
            league,
            match_date,
            ai_prediction,
            ai_confidence,
            risk_level,
            recommended_bet,
            result,
            is_correct,
            profit_loss,
            telegram_sent
        FROM predictions
        WHERE {' AND '.join(where_clauses)}
        ORDER BY {order_map[sort_by]}
        LIMIT {limit};
    """
    
    predictions = execute_query(predictions_query)
    
    if predictions:
        # Display as cards
        for pred in predictions:
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 1])
                
                with col1:
                    # Match info
                    match_emoji = "⚽"
                    if pred['is_correct'] == True:
                        match_emoji = "✅"
                    elif pred['is_correct'] == False:
                        match_emoji = "❌"
                    
                    st.markdown(f"### {match_emoji} {pred['home_team']} vs {pred['away_team']}")
                    st.caption(f"🏆 {pred['league']} | 📅 {pred['match_date'].strftime('%d.%m.%Y %H:%M')}")
                
                with col2:
                    # Prediction
                    risk_emoji = {'LOW': '🟢', 'MEDIUM': '🟡', 'HIGH': '🔴'}.get(pred['risk_level'], '⚪')
                    st.markdown(f"**Tahmin:** {pred['ai_prediction']}")
                    st.markdown(f"**Güven:** {pred['ai_confidence']:.0f}% | **Risk:** {risk_emoji} {pred['risk_level']}")
                
                with col3:
                    # Result
                    if pred['result']:
                        st.markdown(f"**Sonuç:** {pred['result']}")
                        if pred['profit_loss']:
                            profit_color = "green" if pred['profit_loss'] > 0 else "red"
                            st.markdown(f"**Kâr:** <span style='color:{profit_color}'>{pred['profit_loss']:+.0f} TL</span>", unsafe_allow_html=True)
                    else:
                        st.markdown("**Durum:** Beklemede...")
                
                st.divider()
    else:
        st.info("ℹ️ Seçilen filtrelere uygun tahmin bulunamadı.")

# Footer
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.caption("🤖 Powered by DeepSeek AI")
with col2:
    st.caption("⚽ Football Prediction System v2.1")
with col3:
    st.caption(f"📅 Son güncelleme: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
