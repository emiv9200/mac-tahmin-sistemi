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

# Environment Variables
API_KEY = os.getenv("API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not all([API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY]):
    print("WARNING: Some environment variables are missing!")
    print(f"   API_KEY: {'OK' if API_KEY else 'MISSING'}")
    print(f"   TELEGRAM_BOT_TOKEN: {'OK' if TELEGRAM_BOT_TOKEN else 'MISSING'}")
    print(f"   TELEGRAM_CHAT_ID: {'OK' if TELEGRAM_CHAT_ID else 'MISSING'}")
    print(f"   DEEPSEEK_API_KEY: {'OK' if DEEPSEEK_API_KEY else 'MISSING'}")

API_BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

TARGET_LEAGUES = [
    39,   # Premier League
    78,   # Bundesliga
    140,  # La Liga
    61,   # Ligue 1
    135,  # Serie A
    203,  # Super Lig
]

def send_telegram_message(text: str, parse_mode="Markdown", disable_preview=True):
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing!")
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
        print(f"Telegram error: {e}")
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
        print(f"Log error: {e}")

def deepseek_predict(match_data):
    """Get AI prediction from DeepSeek"""
    if not DEEPSEEK_API_KEY:
        return {
            'prediction': 'NO_API_KEY',
            'confidence': 0,
            'reasoning': 'DeepSeek API key not configured',
            'recommended_bet': 'SKIP',
            'risk_level': 'HIGH',
            'expected_value': 0
        }

    prompt = f"""You are a professional football analyst. Analyze this match:

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

Return ONLY valid JSON:
{{
  "prediction": "HOME_WIN or DRAW or AWAY_WIN or OVER_2.5 or UNDER_2.5 or BTTS_YES",
  "confidence": 75,
  "reasoning": "Brief analysis (max 150 chars)",
  "recommended_bet": "Recommended bet with odds",
  "risk_level": "LOW or MEDIUM or HIGH",
  "expected_value": 1.15
}}
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
                "temperature": 0.3,
                "max_tokens": 500
            },
            timeout=30
        )
        
        response.raise_for_status()
        result = response.json()
        
        import json
        ai_response = result["choices"][0]["message"]["content"]
        ai_response = ai_response.replace('```json', '').replace('```', '').strip()
        
        prediction_data = json.loads(ai_response)
        
        return {
            'prediction': prediction_data.get('prediction', 'UNKNOWN'),
            'confidence': float(prediction_data.get('confidence', 0)),
            'reasoning': prediction_data.get('reasoning', 'No analysis available'),
            'recommended_bet': prediction_data.get('recommended_bet', 'SKIP'),
            'risk_level': prediction_data.get('risk_level', 'HIGH'),
            'expected_value': float(prediction_data.get('expected_value', 0))
        }
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return {
            'prediction': 'PARSE_ERROR',
            'confidence': 0,
            'reasoning': 'Failed to parse AI response',
            'recommended_bet': 'SKIP',
            'risk_level': 'HIGH',
            'expected_value': 0
        }
    except Exception as e:
        print(f"DeepSeek API error: {e}")
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
    print("\nStarting DeepSeek analysis...\n")
    
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
        print("No matches to analyze")
        return 0
    
    analyzed_count = 0
    
    for match in matches:
        print(f"  Analyzing: {match['home_team']} vs {match['away_team']}")
        
        ai_result = deepseek_predict(match)
        
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
            
            print(f"    OK: {ai_result['prediction']} (Confidence: {ai_result['confidence']}%)")
            analyzed_count += 1
            time.sleep(2)
            
        except Exception as e:
            print(f"    ERROR: {e}")
    
    print(f"\nAnalyzed {analyzed_count} matches!\n")
    return analyzed_count

def format_prediction_message(match):
    """Format prediction message for Telegram"""
    risk_emoji = {
        'LOW': '🟢',
        'MEDIUM': '🟡',
        'HIGH': '🔴'
    }.get(match.get('risk_level', 'HIGH'), '⚪')
    
    match_time = match['match_date'].strftime('%H:%M') if isinstance(match['match_date'], datetime) else str(match['match_date'])[11:16]
    
    message = f"""
{'='*40}
{match['home_team']} vs {match['away_team']}
{match['league']}
Time: {match_time}

ODDS
Home: {match.get('home_odds', 'N/A')}
Draw: {match.get('draw_odds', 'N/A')}
Away: {match.get('away_odds', 'N/A')}

AI PREDICTION
{risk_emoji} {match.get('recommended_bet', 'SKIP')}

Confidence: {match.get('ai_confidence', 0):.0f}%
Analysis: {match.get('ai_reasoning', 'No analysis')}
Risk: {match.get('risk_level', 'HIGH')}
{'='*40}
"""
    return message.strip()

def send_daily_predictions(min_confidence=60, max_risk='MEDIUM'):
    """Send predictions to Telegram"""
    print("\nSending predictions to Telegram...\n")
    
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
        print("  No predictions to send")
        return 0
    
    filtered_matches = [
        m for m in matches 
        if risk_levels.get(m.get('risk_level', 'HIGH'), 3) <= max_risk_value
    ]
    
    if not filtered_matches:
        print(f"  No predictions matching risk level: {max_risk}")
        return 0
    
    header = f"""
DAILY VIP PREDICTIONS
Date: {datetime.now().strftime('%d.%m.%Y')}
Total: {len(filtered_matches)} matches

Min Confidence: {min_confidence}% | Max Risk: {max_risk}
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
            print(f"  OK: {match['home_team']} vs {match['away_team']}")
        else:
            print(f"  FAIL: {match['home_team']} vs {match['away_team']}")
        
        time.sleep(2)
    
    footer = f"""
{'='*40}
Sent: {sent_count} predictions
Powered by DeepSeek AI
{'='*40}
"""
    send_telegram_message(footer)
    
    print(f"\nSent {sent_count} predictions!\n")
    return sent_count

def check_and_update_results():
    """Check match results and update database"""
    print("\nChecking match results...\n")
    
    query = """
        SELECT * FROM predictions
        WHERE result IS NULL
          AND match_date < NOW()
          AND match_date > NOW() - INTERVAL '7 days'
        ORDER BY match_date DESC;
    """
    
    matches = execute_query(query)
    
    if not matches:
        print("  No matches to check")
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
            
            stake = 10
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
            
            status_icon = "OK" if is_correct else "FAIL"
            print(f"  {status_icon}: {match['home_team']} {home_score}-{away_score} {match['away_team']}")
            updated_count += 1
            
            time.sleep(1)
            
        except Exception as e:
            print(f"  ERROR: {match['home_team']} - {e}")
    
    print(f"\nUpdated {updated_count} results!\n")
    return updated_count

def send_daily_report():
    """Send daily performance report"""
    print("\nGenerating daily report...\n")
    
    stats = get_performance_stats(days=1)
    
    if not stats or stats['total_predictions'] == 0:
        send_telegram_message("No predictions to report today.")
        return
    
    total = stats['total_predictions']
    correct = stats['correct_predictions'] or 0
    accuracy = stats['accuracy_rate'] or 0
    profit = stats['total_profit_loss'] or 0
    
    report = f"""
DAILY PERFORMANCE REPORT
Date: {datetime.now().strftime('%d.%m.%Y')}

{'='*40}
STATISTICS
Total Predictions: {total}
Correct: {correct}
Wrong: {total - correct}
Accuracy: {accuracy:.1f}%

FINANCIAL
Profit/Loss: {profit:+.2f} TL

Average Confidence: {stats.get('avg_confidence', 0):.1f}%
{'='*40}
"""
    
    send_telegram_message(report)
    print("Daily report sent!")

# Flask App
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "service": "Football Match Prediction System",
        "version": "2.0",
        "endpoints": {
            "/collect": "Collect today's matches",
            "/analyze": "Analyze with DeepSeek AI",
            "/send": "Send predictions to Telegram",
            "/check": "Check match results",
            "/report": "Send daily report",
            "/run": "Full cycle (collect + analyze + send)",
            "/stats": "Performance statistics"
        }
    })

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
        min_confidence = int(request.args.get('min_confidence', 60))
        max_risk = request.args.get('max_risk', 'MEDIUM')
        
        count = send_daily_predictions(min_confidence, max_risk)
        return jsonify({"success": True, "sent": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/check")
def check_endpoint():
    try:
        count = check_and_update_results()
        return jsonify({"success": True, "updated": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/report")
def report_endpoint():
    try:
        send_daily_report()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/run")
def run_endpoint():
    try:
        results = {}
        
        print("Step 1: Collecting matches...")
        results['collected'] = collect_today_matches(TARGET_LEAGUES)
        
        print("Step 2: AI Analysis...")
        results['analyzed'] = analyze_and_update_predictions()
        
        print("Step 3: Sending to Telegram...")
        results['sent'] = send_daily_predictions(min_confidence=65, max_risk='MEDIUM')
        
        return jsonify({"success": True, **results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/stats")
def stats_endpoint():
    try:
        days = int(request.args.get('days', 30))
        stats = get_performance_stats(days)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    
    print("\n" + "="*60)
    print("FOOTBALL PREDICTION SYSTEM v2.0")
    print("="*60)
    print(f"Server: http://0.0.0.0:{port}")
    print(f"Target Leagues: {len(TARGET_LEAGUES)}")
    print(f"DeepSeek AI: {'OK' if DEEPSEEK_API_KEY else 'NOT CONFIGURED'}")
    print(f"Telegram: {'OK' if TELEGRAM_BOT_TOKEN else 'NOT CONFIGURED'}")
    print("="*60 + "\n")
    
    app.run(host="0.0.0.0", port=port, debug=False)
