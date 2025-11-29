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

# Premier League, Bundesliga, La Liga, Ligue 1
TARGET_LEAGUES = [39, 78, 140, 61]


# ------------------ TELEGRAM ------------------ #

def send_telegram_message(text: str) -> None:
    """Telegram'a mesaj gönderir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram bilgisi eksik (TOKEN / CHAT_ID). Mesaj gönderilmeyecek.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            print("❌ Telegram hatası:", resp.status_code, resp.text)
        else:
            print("✅ Telegram mesajı gönderildi.")
    except Exception as e:
        print("⚠️ Telegram isteği sırasında hata:", e)


# ------------------ MAÇ ÇEKME ------------------ #

def get_today_fixtures():
    """
    Bugünün maçlarını API-FOOTBALL'dan çeker
    ve hedef liglere göre filtreler.
    """
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    print(f"📅 {today_str} tarihli maçlar çekiliyor...")

    url = f"{API_BASE_URL}/fixtures"
    params = {
        "date": today_str,
        "timezone": "Europe/Istanbul"
    }

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            print("❌ API Hatası:", r.status_code, r.text)
            return []

        data = r.json()
        fixtures = data.get("response", [])

        filtered = [
            f for f in fixtures
            if f.get("league", {}).get("id") in TARGET_LEAGUES
        ]

        print(f"✅ Toplam {len(filtered)} maç bulundu (filtrelenmiş).")
        return filtered

    except Exception as e:
        print("❌ Maçları çekerken hata:", e)
        return []


# ------------------ DEEPSEEK TAHMİN ------------------ #

def deepseek_predict(home: str, away: str, league: str) -> str:
    """
    DeepSeek'ten profesyonel analiz ister.
    Çıktıyı ham metin olarak döner (Telegram'da direkt gösteriyoruz).
    """
    if not DEEPSEEK_API_KEY:
        print("ℹ️ DEEPSEEK_API_KEY tanımlı değil, AI tahmini atlanıyor.")
        return "_(AI tahmini yapılamadı – DEEPSEEK_API_KEY eksik)_"

    prompt = f"""
Sen üst seviye profesyonel futbol analisti bir yapay zekasın. 
Aşağıdaki maç için form, gol ortalamaları, risk ve oran mantığını kullanarak 
detaylı ve yüzdelik tahmin hazırla.

MAÇ: {home} vs {away}
LİG: {league}

FORMAT (Bu formatın dışına ÇIKMA):

🏆 Tahmin Özeti:
- Ev Kazanır: %..
- Beraberlik: %..
- Deplasman Kazanır: %..
- KG Var: %..
- Toplam Gol Tahmini: .. (ör: 2–4)
- Alt/Üst Tahmini: Alt / Üst
- KG&Üst Kombin: %..

📊 Detaylı Analiz:
- Ev takımı son 5 maç formu:
- Deplasman takımı son 5 maç formu:
- Gol ortalamaları:
- Ev/deplasman performansı:
- Önemli eksikler:
- En güvenilir tahmin:
- Güven yüzdesi (%..)

Sadece bu formatta Türkçe cevap ver.
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
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print("❌ DeepSeek API hatası:", resp.status_code, resp.text)
            return "_(AI tahmini alınamadı – API hatası)_"

        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        print(f"🤖 DeepSeek tahmini alındı: {home} vs {away}")
        return content

    except Exception as e:
        print("❌ DeepSeek hata:", e)
        return "_(AI tahmini alınırken hata oluştu)_"


# ------------------ FORMAT - VIP KART ------------------ #

def format_match_card(fixture: dict, ai_text: str) -> str:
    """Tek maç için şık bir kart oluşturur."""
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    league = fixture["league"]["name"]

    # ISO tarih -> "HH:MM"
    raw_date = fixture["fixture"]["date"]
    time_str = raw_date[11:16]

    card = f"""
———————————————
⚽ *MAÇ*: {home} – {away}
🏆 *Lig*: {league}
🕒 *Saat*: {time_str}

🤖 *DeepSeek Analizi*:
{ai_text}
———————————————
"""
    return card


# ------------------ JOB ------------------ #

def run_daily_job():
    """
    Günlük işi çalıştırır:
    - Maçları çeker
    - En fazla 5 maç için DeepSeek tahmini alır
    - Şık bir Telegram mesajı gönderir
    """
    fixtures = get_today_fixtures()
    if not fixtures:
        msg = "⚠️ Bugün hedef liglerde maç bulunamadı."
        print(msg)
        send_telegram_message(msg)
        return {"ok": False, "msg": msg}

    # Render / DeepSeek için aşırı istek atmamak adına en fazla 5 maç
    max_matches = min(5, len(fixtures))
    selected = fixtures[:max_matches]

    cards = []
    for f in selected:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]

        ai_text = deepseek_predict(home, away, league)
        cards.append(format_match_card(f, ai_text))

    final_message = (
        "🔥 *GÜNÜN VIP MAÇ TAHMİNLERİ* 🔥\n"
        "_(Deneme / Beta sürüm – sadece bilgi amaçlıdır)_\n\n"
        + "\n".join(cards)
    )

    send_telegram_message(final_message)

    return {"ok": True, "count": len(cards)}


# ------------------ FLASK ------------------ #

app = Flask(__name__)


@app.route("/")
def home():
    return "✅ Maç Tahmin Sistemi Çalışıyor. /run ile manuel tetikleyebilirsin."


@app.route("/run")
def run_endpoint():
    result = run_daily_job()
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Flask server {port} portunda ayağa kalkıyor...")
    app.run(host="0.0.0.0", port=port)
