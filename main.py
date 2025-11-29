import os
import requests
from datetime import datetime
from flask import Flask, jsonify

# ------------------ ENV ------------------ #

API_KEY = os.getenv("API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

API_BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

TARGET_LEAGUES = [39, 78, 140, 61]  # Premier, Bundesliga, La Liga, Ligue1


# ============================================================
#                    TELEGRAM GÖNDERİMİ
# ============================================================

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram bilgisi eksik")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        print("📨 Telegram gönderildi:", resp.status_code)
    except Exception as e:
        print("⚠️ Telegram hata:", e)


# ============================================================
#                 MAÇ VERİSİ ÇEKME
# ============================================================

def get_today_fixtures():
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    url = f"{API_BASE_URL}/fixtures"
    params = {"date": today_str, "timezone": "Europe/Istanbul"}

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        data = r.json()

        fixtures = data.get("response", [])

        filtered = [
            f for f in fixtures
            if f.get("league", {}).get("id") in TARGET_LEAGUES
        ]

        print(f"📌 Toplam maç: {len(filtered)}")
        return filtered

    except Exception as e:
        print("❌ API Error:", e)
        return []


# ============================================================
#               DEEPSEEK TAHMİN (AI MODEL)
# ============================================================

def deepseek_predict(home, away, league):
    if not DEEPSEEK_API_KEY:
        return {
            "home_win": "N/A",
            "btts": "N/A",
            "goals": "N/A",
            "confidence": "N/A"
        }

    prompt = f"""
Sen profesyonel futbol veri analisti bir yapay zekasın.
Aşağıdaki maç için yüzdesel ihtimaller ve güven skoru ver:

MAÇ: {home} vs {away}
LİG: {league}

Dönüş formatı:

Ev Kazanır: %..
KG Var: %..
Gol Aralığı: ..
Güven Skoru: %..
"""

    try:
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )

        content = r.json()["choices"][0]["message"]["content"]
        lines = [l.strip() for l in content.split("\n") if l.strip()]

        # Hataya dayanıklı ayrıştırma
        parsed = {
            "home_win": "N/A",
            "btts": "N/A",
            "goals": "N/A",
            "confidence": "N/A"
        }

        for line in lines:
            if line.startswith("Ev") or "Ev Kazanır" in line:
                parsed["home_win"] = line.split(":", 1)[-1].strip()
            elif line.startswith("KG"):
                parsed["btts"] = line.split(":", 1)[-1].strip()
            elif "Gol" in line:
                parsed["goals"] = line.split(":", 1)[-1].strip()
            elif "Güven" in line:
                parsed["confidence"] = line.split(":", 1)[-1].strip()

        return parsed

    except Exception as e:
        print("❌ DeepSeek hata:", e)
        return {
            "home_win": "N/A",
            "btts": "N/A",
            "goals": "N/A",
            "confidence": "N/A"
        }


# ============================================================
#                   VIP MAÇ KARTI FORMAT
# ============================================================

def format_match_card(fixture, ai):
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    league = fixture["league"]["name"]
    time_str = fixture["fixture"]["date"][11:16]

    return f"""
———————————————
⚽ *MAÇ*: {home} – {away}
🏆 *Lig*: {league}
🕒 *Saat*: {time_str}

🤖 *DeepSeek Tahmini*:
• Ev Kazanır: {ai['home_win']}
• KG Var: {ai['btts']}
• Toplam Gol: {ai['goals']}
• Güven Skoru: {ai['confidence']}
———————————————
"""


# ============================================================
#                     GÜNLÜK JOB
# ============================================================

def run_daily_job():
    fixtures = get_today_fixtures()
    if not fixtures:
        return {"ok": False, "msg": "Bugün maç yok"}

    cards = []
    limit = min(5, len(fixtures))  # 5 taneden fazla maç yoksa hata verme

    for f in fixtures[:limit]:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]

        ai = deepseek_predict(home, away, league)
        cards.append(format_match_card(f, ai))

    final_message = "🔥 *Günün VIP Maç Tahminleri* 🔥\n\n" + "\n".join(cards)

    send_telegram_message(final_message)

    return {"ok": True, "count": len(cards)}


# ============================================================
#                      FLASK SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "OK - MAC TAHMIN SISTEMI AKTIF"


@app.route("/run")
def run_endpoint():
    result = run_daily_job()
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
