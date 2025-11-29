import os
import requests
from datetime import datetime
from flask import Flask, jsonify
from database import get_db, close_db  # database.py dosyasından gelecek

# ------------------ Environment Variables ------------------ #

API_KEY = os.getenv("API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

API_BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

TARGET_LEAGUES = [39, 78, 140, 61]   # PL, Bundesliga, La Liga, Ligue 1


# -------------------------------------------------------------
#                     TELEGRAM MESSAGE
# -------------------------------------------------------------

def send_telegram_message(text: str):
    """Telegram'a mesaj gönderir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram bilgisi yok.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("⚠️ Telegram gönderim hatası:", e)


# -------------------------------------------------------------
#                   BUGÜNÜN MAÇLARINI ÇEKME
# -------------------------------------------------------------

def get_today_fixtures():
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    url = f"{API_BASE_URL}/fixtures"
    params = {"date": today_str, "timezone": "Europe/Istanbul"}

    try:
        r = requests.get(url, headers=HEADERS, params=params)
        fixtures = r.json().get("response", [])
        return [f for f in fixtures if f["league"]["id"] in TARGET_LEAGUES]
    except:
        return []


# -------------------------------------------------------------
#                   VERITABANINA KAYDETME
# -------------------------------------------------------------

def save_prediction(match_id, home, away, league, match_date, ai_text):
    conn = get_db()
    if not conn:
        return

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO predictions (match_id, home_team, away_team, league, match_date, ai_prediction)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (match_id, home, away, league, match_date, ai_text))

    conn.commit()
    close_db(conn)


# -------------------------------------------------------------
#                   DEEPSEEK TAHMİN (AI)
# -------------------------------------------------------------

def deepseek_predict(home, away, league):
    """DeepSeek API ile basit formatta tahmin alır."""
    if not DEEPSEEK_API_KEY:
        return "(Tahmin alınamadı – API KEY yok)"

    prompt = f"""
MAÇ: {home} vs {away}
LİG: {league}

Bu maç için yüzdelikli tahmin ver.

FORMAT:
Ev Kazanır: %..
Beraberlik: %..
Deplasman Kazanır: %..
""".strip()

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}]
            }
        )

        return resp.json()["choices"][0]["message"]["content"]

    except Exception as e:
        print("DeepSeek API error:", e)
        return "(AI hata verdi)"


# -------------------------------------------------------------
#                   MAÇ KARTI FORMAT
# -------------------------------------------------------------

def format_match_card(f, ai_text):
    home = f["teams"]["home"]["name"]
    away = f["teams"]["away"]["name"]
    league = f["league"]["name"]
    time_str = f["fixture"]["date"][11:16]

    return f"""
———————————————
⚽ *MAÇ*: {home} – {away}
🏆 *Lig*: {league}
🕒 *Saat*: {time_str}

🤖 *Fatih Koç Tahmini (AI)*:
{ai_text}
———————————————
"""


# -------------------------------------------------------------
#             GÜNLÜK TAHMİN ÜRETME (CRON → /run)
# -------------------------------------------------------------

def run_daily_job():
    fixtures = get_today_fixtures()

    if not fixtures:
        send_telegram_message("⚠️ Bugün maç yok.")
        return {"ok": False}

    messages = []

    for f in fixtures[:5]:  # max 5 maç
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        match_id = f["fixture"]["id"]
        match_date = f["fixture"]["date"]

        ai_text = deepseek_predict(home, away, league)

        # DB'ye kaydet
        save_prediction(match_id, home, away, league, match_date, ai_text)

        messages.append(format_match_card(f, ai_text))

    final_msg = "🔥 *GÜNÜN VIP MAÇ TAHMİNLERİ* 🔥\n\n" + "\n".join(messages)
    send_telegram_message(final_msg)

    return {"ok": True}


# -------------------------------------------------------------
#               MAÇ SONUCU KONTROLÜ (CRON → /check_results)
# -------------------------------------------------------------

def check_results():
    conn = get_db()
    if not conn:
        return {"ok": False}

    cur = conn.cursor()

    # Daha sonucu kaydedilmemiş tahminler
    cur.execute("SELECT * FROM predictions WHERE result IS NULL")
    rows = cur.fetchall()

    for row in rows:
        match_id = row["match_id"]

        url = f"{API_BASE_URL}/fixtures?id={match_id}"
        r = requests.get(url, headers=HEADERS).json()
        data = r.get("response", [])

        if not data:
            continue

        fx = data[0]
        status = fx["fixture"]["status"]["short"]

        if status != "FT":
            continue  # maç bitmemiş

        gh = fx["goals"]["home"]
        ga = fx["goals"]["away"]

        ai = row["ai_prediction"]

        # Basit başarı kontrolü
        correct = None
        if "Ev Kazanır" in ai and gh > ga:
            correct = True
        elif "Deplasman Kazanır" in ai and ga > gh:
            correct = True
        elif "Beraberlik" in ai and gh == ga:
            correct = True
        else:
            correct = False

        cur.execute("""
            UPDATE predictions
            SET result=%s, is_correct=%s
            WHERE id=%s
        """, (f"{gh}-{ga}", correct, row["id"]))

    conn.commit()
    close_db(conn)

    return {"ok": True}


# -------------------------------------------------------------
#           GÜNLÜK ÖZET RAPORU (CRON → /daily_report)
# -------------------------------------------------------------

def daily_report():
    conn = get_db()
    if not conn:
        return {"ok": False}

    cur = conn.cursor()

    today = datetime.utcnow().strftime("%Y-%m-%d")

    cur.execute("""
        SELECT * FROM predictions
        WHERE DATE(match_date) = DATE(%s)
        AND result IS NOT NULL
    """, (today,))

    rows = cur.fetchall()

    if not rows:
        send_telegram_message("📊 Bugün oynanan maç yok.")
        return {"ok": False}

    total = len(rows)
    correct = len([r for r in rows if r["is_correct"]])

    msg = f"🏆 *Günlük Başarı Raporu*\n✓ {correct} maç tuttu\n✗ {total - correct} maç tutmadı\nBaşarı: %{round((correct / total) * 100)}\n\n"

    for r in rows:
        status = "Tuttu" if r["is_correct"] else "Tutmadı"
        msg += f"- {r['home_team']} – {r['away_team']} → {status}\n"

    send_telegram_message(msg)
    return {"ok": True}


# -------------------------------------------------------------
#                       FLASK SERVER
# -------------------------------------------------------------

app = Flask(__name__)

@app.route("/")
def home():
    return "OK – Match Prediction System is running."

@app.route("/run")
def run_endpoint():
    return jsonify(run_daily_job())

@app.route("/check_results")
def check_endpoint():
    return jsonify(check_results())

@app.route("/daily_report")
def report_endpoint():
    return jsonify(daily_report())


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
