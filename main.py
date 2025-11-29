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
TARGET_LEAGES = [39, 78, 140, 61]


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
            if f.get("league", {}).get("id") in TARGET_LEAGES
        ]

        print(f"✅ Toplam {len(filtered)} maç bulundu (filtrelenmiş).")
        return filtered

    except Exception as e:
        print("❌ Maçları çekerken hata:", e)
        return []


# ------------------ TAKIM SON 10 MAÇ - VERİ ÇEKME ------------------ #

def get_team_last_matches(team_id: int, limit: int = 10):
    """
    Verilen takım için son 'limit' adet maçı çeker.
    Sadece bitmiş (FT) maçlar üzerinden gider.
    """
    url = f"{API_BASE_URL}/fixtures"
    params = {
        "team": team_id,
        "last": limit
    }

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            print(f"❌ get_team_last_matches Hatası (team={team_id}):",
                  r.status_code, r.text)
            return []

        data = r.json()
        matches = data.get("response", [])
        return matches

    except Exception as e:
        print(f"❌ get_team_last_matches Exception (team={team_id}):", e)
        return []


# ------------------ H2H (HEAD TO HEAD) MAÇ ÇEKME ------------------ #

def get_h2h_matches(home_id: int, away_id: int, limit: int = 5):
    """
    İki takım arasındaki son 'limit' adet H2H maçını çeker.
    """
    url = f"{API_BASE_URL}/fixtures/headtohead"
    params = {
        "h2h": f"{home_id}-{away_id}",
        "last": limit
    }

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            print(f"❌ get_h2h_matches Hatası ({home_id}-{away_id}):",
                  r.status_code, r.text)
            return []

        data = r.json()
        matches = data.get("response", [])
        return matches

    except Exception as e:
        print(f"❌ get_h2h_matches Exception ({home_id}-{away_id}):", e)
        return []


# ------------------ İSTATİSTİK HESAPLAMA ------------------ #

def compute_team_stats(team_id: int, matches: list) -> dict:
    """
    Bir takımın verdiğimiz maç listesi üzerinden
    temel istatistiklerini hesaplar.
    """
    total = len(matches)
    if total == 0:
        return {
            "matches": 0,
            "win": 0,
            "draw": 0,
            "loss": 0,
            "gf": 0,
            "ga": 0,
            "avg_gf": 0,
            "avg_ga": 0,
            "avg_total": 0,
            "btts_ratio": 0,
            "over25_ratio": 0,
            "clean_sheet_ratio": 0,
            "failed_to_score_ratio": 0
        }

    win = draw = loss = 0
    gf = ga = 0
    btts = 0
    over25 = 0
    clean_sheet = 0
    failed_to_score = 0

    for m in matches:
        goals_home = m["goals"]["home"]
        goals_away = m["goals"]["away"]
        home_id = m["teams"]["home"]["id"]
        away_id = m["teams"]["away"]["id"]

        if team_id == home_id:
            team_goals = goals_home
            opp_goals = goals_away
        else:
            team_goals = goals_away
            opp_goals = goals_home

        # Sonuç
        if team_goals > opp_goals:
            win += 1
        elif team_goals == opp_goals:
            draw += 1
        else:
            loss += 1

        gf += team_goals
        ga += opp_goals

        # KG Var
        if goals_home > 0 and goals_away > 0:
            btts += 1

        total_goals = goals_home + goals_away
        if total_goals >= 3:
            over25 += 1

        if opp_goals == 0:
            clean_sheet += 1

        if team_goals == 0:
            failed_to_score += 1

    avg_gf = gf / total
    avg_ga = ga / total
    avg_total = (gf + ga) / total

    def ratio(x): return round((x / total) * 100, 1)

    return {
        "matches": total,
        "win": win,
        "draw": draw,
        "loss": loss,
        "gf": gf,
        "ga": ga,
        "avg_gf": round(avg_gf, 2),
        "avg_ga": round(avg_ga, 2),
        "avg_total": round(avg_total, 2),
        "btts_ratio": ratio(btts),
        "over25_ratio": ratio(over25),
        "clean_sheet_ratio": ratio(clean_sheet),
        "failed_to_score_ratio": ratio(failed_to_score),
    }


def format_stats_for_prompt(team_name: str, stats: dict) -> str:
    """Takım istatistiklerini prompt için metne çevirir."""
    if stats["matches"] == 0:
        return f"- {team_name}: Yeterli veri yok.\n"

    return (
        f"- {team_name} (Son {stats['matches']} maç):\n"
        f"  • G-B-M: {stats['win']}-{stats['draw']}-{stats['loss']}\n"
        f"  • Attığı gol ort.: {stats['avg_gf']}  | Yediği gol ort.: {stats['avg_ga']}\n"
        f"  • Maç başı toplam gol: {stats['avg_total']}\n"
        f"  • KG Var oranı: %{stats['btts_ratio']}\n"
        f"  • 2.5 Üst oranı: %{stats['over25_ratio']}\n"
        f"  • Gol yememe oranı: %{stats['clean_sheet_ratio']}\n"
        f"  • Gol atamama oranı: %{stats['failed_to_score_ratio']}\n"
    )


def format_h2h_stats_for_prompt(home_name: str, away_name: str, matches: list) -> str:
    """H2H istatistiklerini prompt için metne çevirir."""
    total = len(matches)
    if total == 0:
        return "- H2H: Son dönemde resmi maç verisi bulunamadı.\n"

    home_w = away_w = d = 0
    total_goals = 0
    btts = 0
    over25 = 0

    for m in matches:
        gh = m["goals"]["home"]
        ga = m["goals"]["away"]
        total_goals += (gh + ga)

        if gh > 0 and ga > 0:
            btts += 1
        if gh + ga >= 3:
            over25 += 1

        home = m["teams"]["home"]["name"]
        away = m["teams"]["away"]["name"]

        if gh > ga:
            winner = home
        elif gh < ga:
            winner = away
        else:
            winner = "draw"

        if winner == home_name:
            home_w += 1
        elif winner == away_name:
            away_w += 1
        elif winner == "draw":
            d += 1

    avg_total = round(total_goals / total, 2)
    btts_ratio = round((btts / total) * 100, 1)
    over25_ratio = round((over25 / total) * 100, 1)

    return (
        f"- H2H (Son {total} maç): {home_name} galibiyet: {home_w}, "
        f"{away_name} galibiyet: {away_w}, Beraberlik: {d}\n"
        f"  • Maç başı toplam gol: {avg_total}\n"
        f"  • KG Var oranı: %{btts_ratio}\n"
        f"  • 2.5 Üst oranı: %{over25_ratio}\n"
    )


# ------------------ DEEPSEEK TAHMİN ------------------ #

def deepseek_predict(home: dict, away: dict, league: str,
                     home_stats: dict, away_stats: dict,
                     h2h_text: str) -> str:
    """
    DeepSeek'ten profesyonel analiz ister.
    Çıktıyı ham metin olarak döner (Telegram'da direkt gösteriyoruz).
    """
    if not DEEPSEEK_API_KEY:
        print("ℹ️ DEEPSEEK_API_KEY tanımlı değil, AI tahmini atlanıyor.")
        return "_(AI tahmini yapılamadı – DEEPSEEK_API_KEY eksik)_"

    home_name = home["name"]
    away_name = away["name"]

    home_block = format_stats_for_prompt(home_name, home_stats)
    away_block = format_stats_for_prompt(away_name, away_stats)

    stats_block = (
        "📊 İSTATİSTİK ÖZETİ (Son 10 maç):\n"
        f"{home_block}\n"
        f"{away_block}\n"
        f"{h2h_text}\n"
    )

    prompt = f"""
Sen üst seviye profesyonel futbol analisti bir yapay zekasın. 
Aşağıdaki maç için form, gol ortalamaları, H2H ve risk mantığını kullanarak 
detaylı ve yüzdelik tahmin hazırla.

MAÇ: {home_name} vs {away_name}
LİG: {league}

{stats_block}

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
            timeout=40,
        )

        if resp.status_code != 200:
            print("❌ DeepSeek API hatası:", resp.status_code, resp.text)
            return "_(AI tahmini alınamadı – API hatası)_"

        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        print(f"🤖 DeepSeek tahmini alındı: {home_name} vs {away_name}")
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

🤖 *Fatih Koç Tahmini (AI Destekli)*:
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
        home_team = f["teams"]["home"]
        away_team = f["teams"]["away"]
        league_name = f["league"]["name"]

        home_id = home_team["id"]
        away_id = away_team["id"]

        # Son 10 maç verisi
        home_last = get_team_last_matches(home_id, limit=10)
        away_last = get_team_last_matches(away_id, limit=10)

        home_stats = compute_team_stats(home_id, home_last)
        away_stats = compute_team_stats(away_id, away_last)

        # H2H verisi
        h2h_matches = get_h2h_matches(home_id, away_id, limit=5)
        h2h_text = format_h2h_stats_for_prompt(
            home_team["name"], away_team["name"], h2h_matches
        )

        # AI tahmini
        ai_text = deepseek_predict(
            home_team, away_team, league_name,
            home_stats, away_stats, h2h_text
        )

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
    # İstersen burayı yoruma alabilirsin, her restart'ta test mesajı atmasın:
    # send_telegram_message("TEST MESAJI — sistem çalışıyor 🚀")
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Flask server {port} portunda ayağa kalkıyor...")
    app.run(host="0.0.0.0", port=port)
