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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
