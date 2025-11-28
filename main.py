import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, jsonify

# ------------------ ENV AYARLARI ------------------ #

load_dotenv()  # Lokalde .env okur, Render'da env panelini kullanacağız

API_KEY = os.getenv("API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")  # ŞİMDİLİK YOK, İLERİDE

if not API_KEY:
    print("❌ API_KEY bulunamadı! Render Environment Variables kısmına eklemelisin.")
else:
    print("✅ API_KEY bulundu.")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("⚠️ Telegram bilgileri eksik (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). "
          "Yine de sistem çalışacak ama Telegram'a mesaj gönderemeyecek.")
else:
    print("✅ Telegram ayarları yüklendi.")

# API-FOOTBALL ayarları
API_BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# Hedef ligler (istersen çoğaltırız)
TARGET_LEAGUES = [39]  # Premier League (örnek)


# ------------------ TELEGRAM FONKSİYONU ------------------ #

def send_telegram_message(text: str):
    """Telegram'a basit bir mesaj yollar."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram bilgileri tanımlı değil, mesaj gönderilmeyecek.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print("❌ Telegram hatası:", resp.status_code, resp.text)
        else:
            print("✅ Telegram mesajı gönderildi.")
    except Exception as e:
        print("⚠️ Telegram isteğinde hata:", e)


# ------------------ MAÇ VERİSİ ÇEKME ------------------ #

def get_today_fixtures():
    """
    Bugünün (veya istersen yarının) maçlarını API-FOOTBALL'dan çeker.
    Şimdilik sadece TARGET_LEAGUES içindeki ligleri filtreliyoruz.
    """
    # Avrupa saatine göre bugün
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    print(f"📅 {today_str} tarihli maçlar çekiliyor...")

    url = f"{API_BASE_URL}/fixtures"
    params = {
        "date": today_str,
        "timezone": "Europe/Istanbul"
    }

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code != 200:
            print("❌ API Hatası:", resp.status_code, resp.text)
            return []

        data = resp.json()
        if "response" not in data:
            print("⚠️ Beklenmeyen API cevabı:", data)
            return []

        fixtures = data["response"]

        # Lig filtrele (isteğe bağlı)
        filtered = [
            f for f in fixtures
            if f.get("league", {}).get("id") in TARGET_LEAGUES
        ]

        print(f"✅ Toplam {len(filtered)} maç bulundu (filtrelenmiş).")
        return filtered

    except Exception as e:
        print("⚠️ Maçları çekerken hata:", e)
        return []


# ------------------ BASİT "AI" TAHMİN (YER TUTUCU) ------------------ #

def simple_score_fixture(fixture: dict) -> float:
    """
    Şimdilik çok basit bir skor hesaplayacağız.
    İleride burayı DeepSeek tahmini ile değiştireceğiz.
    """
    league_name = fixture.get("league", {}).get("name", "")
    importance_bonus = 0.0
    if "Premier League" in league_name:
        importance_bonus = 0.2  # Örnek: önemli liglere ufak bonus

    # Ev sahibi ismi uzun ve "büyük kulüp" gibi diye saçma bir kural koymayalım :)
    # Şimdilik tamamen dummy skor:
    base_score = 0.5

    return base_score + importance_bonus


def pick_best_5(fixtures: list) -> list:
    """
    Maç listesi içinden en yüksek "skor"lu 5 maçı seçer.
    Şimdilik simple_score_fixture kullanıyor.
    İleride buraya DeepSeek destekli gerçek model gelecek.
    """
    scored = []
    for f in fixtures:
        score = simple_score_fixture(f)
        scored.append((score, f))

    # Skora göre sırala, en yüksek 5 taneyi al
    scored.sort(key=lambda x: x[0], reverse=True)
    best = [f for score, f in scored[:5]]
    return best


# ------------------ (İLERİDE) DEEPSEEK ENTEGRASYONU ------------------ #

def call_deepseek_for_predictions(fixtures: list):
    """
    DeepSeek API key geldiğinde gerçek yapay zeka tahmini burada çalışacak.
    Şimdilik sadece "None" döndürüyoruz.
    """
    if not DEEPSEEK_API_KEY:
        print("ℹ️ DEEPSEEK_API_KEY tanımlı değil, simple mode kullanılıyor.")
        return None

    # Buraya DeepSeek entegrasyonunu ekleyeceğiz.
    # Şu an için placeholder:
    return None


# ------------------ TAHMİN ÇALIŞTIRICI ------------------ #

def run_daily_job():
    """
    Günlük tahmin işini çalıştırır:
    - Maçları çeker
    - (İleride) DeepSeek'ten tahmin alır
    - Şimdilik basit skor ile en iyi 5 maçı seçer
    - Telegram'a mesaj gönderir
    """
    fixtures = get_today_fixtures()
    if not fixtures:
        print("⚠️ Bugün için maç bulunamadı veya API boş döndü.")
        return {
            "ok": False,
            "message": "Bugün için maç bulunamadı."
        }

    # (Şimdilik) simple mode
    best_5 = pick_best_5(fixtures)

    lines = ["📊 *Günün Önerilen 5 Maçı* (BETA)"]
    for f in best_5:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        time_utc = f["fixture"]["date"]  # ISO format

        lines.append(f"- {home} vs {away}  \n  _({league})_")

    message = "\n\n".join(lines)

    print("\n--- TELEGRAM MESAJI BAŞLANGIÇ ---")
    print(message)
    print("--- TELEGRAM MESAJI BİTİŞ ---\n")

    send_telegram_message(message)

    return {
        "ok": True,
        "count": len(best_5),
        "sent_to_telegram": TELEGRAM_BOT_TOKEN is not None and TELEGRAM_CHAT_ID is not None
    }


# ------------------ FLASK SERVER (RENDER İÇİN ZORUNLU) ------------------ #

app = Flask(__name__)


@app.route("/")
def home():
    return "✅ Maç Tahmin Sistemi Çalışıyor (BETA). /run endpoint'ini kullan."


@app.route("/run")
def run_endpoint():
    """
    Bu endpoint çağrıldığında günlük işi çalıştırır.
    Cron-job.org veya manuel tarayıcıdan tetikleyebilirsin.
    """
    result = run_daily_job()
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Flask server {port} portunda ayağa kalkıyor...")
    app.run(host="0.0.0.0", port=port)
