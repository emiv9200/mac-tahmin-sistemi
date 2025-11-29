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
        requests.post(url, json=payload, timeout=15)
        print("📨 Telegram gönderildi")
    except Exception as e:
        print("⚠️ Telegram hata:", e)


# ------------------ MAÇ ÇEKME ------------------ #

def get_today_fixtures():
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    print(f"📅 {today_str} maçlar çekiliyor...")

    url = f"{API_BASE_URL}/fixtures"
    params = {"date": today_str, "timezone": "Europe/Istanbul"}

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            print("❌ API Error:", r.status_code, r.text)
            return []

        fixtures = r.json().get("response", [])

        filtered = [
            f for f in fixtures
            if f.get("league", {}).get("id") in TARGET_LEAGUES
        ]
        print("✅ Bulunan maç:", len(filtered))
        return filtered

    except Exception as e:
        print("❌ Fixture Error:", e)
        return []


# ------------------ TAKIM SON 10 MAÇ ------------------ #

def get_team_last_matches(team_id: int, limit=10):
    url = f"{API_BASE_URL}/fixtures"
    params = {"team": team_id, "last": limit}

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        return r.json().get("response", [])
    except:
        return []


# ------------------ H2H ------------------ #

def get_h2h_matches(home_id: int, away_id: int, limit=5):
    url = f"{API_BASE_URL}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}", "last": limit}

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        return r.json().get("response", [])
    except:
        return []


# ------------------ İSTATİSTİK ------------------ #

def compute_team_stats(team_id: int, matches: list) -> dict:
    total = len(matches)
    if total == 0:
        return {"matches": 0}

    win = draw = loss = 0
    gf = ga = 0
    btts = over25 = 0
    cs = fts = 0

    for m in matches:
        gh = m["goals"]["home"]
        ga_ = m["goals"]["away"]
        home = m["teams"]["home"]["id"]

        team_goals = gh if team_id == home else ga_
        opp_goals = ga_ if team_id == home else gh

        if team_goals > opp_goals: win += 1
        elif team_goals == opp_goals: draw += 1
        else: loss += 1

        gf += team_goals
        ga += opp_goals

        if gh > 0 and ga_ > 0: btts += 1
        if gh + ga_ >= 3: over25 += 1
        if opp_goals == 0: cs += 1
        if team_goals == 0: fts += 1

    def pct(x): return round(x / total * 100, 1)

    return {
        "matches": total,
        "win": win,
        "draw": draw,
        "loss": loss,
        "avg_gf": round(gf / total, 2),
        "avg_ga": round(ga / total, 2),
        "avg_total": round((gf + ga) / total, 2),
        "btts_ratio": pct(btts),
        "over25_ratio": pct(over25),
        "clean_sheet_ratio": pct(cs),
        "failed_to_score_ratio": pct(fts),
    }


def format_stats_for_prompt(name, st):
    if st["matches"] == 0:
        return f"- {name}: Veri yok.\n"

    return (
        f"- {name} (Son {st['matches']} maç):\n"
        f"  • G-B-M: {st['win']}-{st['draw']}-{st['loss']}\n"
        f"  • Attığı gol ort.: {st['avg_gf']} | Yediği gol ort.: {st['avg_ga']}\n"
        f"  • Toplam gol ort.: {st['avg_total']}\n"
        f"  • KG Var: %{st['btts_ratio']}\n"
        f"  • 2.5 Üst: %{st['over25_ratio']}\n"
        f"  • Gol yememe: %{st['clean_sheet_ratio']}\n"
        f"  • Gol atamama: %{st['failed_to_score_ratio']}\n"
    )


def format_h2h_stats(name1, name2, matches):
    total = len(matches)
    if total == 0:
        return "- H2H: Veri yok.\n"

    h = a = d = 0
    total_goals = btts = o25 = 0

    for m in matches:
        gh = m["goals"]["home"]
        ga = m["goals"]["away"]
        home = m["teams"]["home"]["name"]

        total_goals += (gh + ga)
        if gh > 0 and ga > 0: btts += 1
        if gh + ga >= 3: o25 += 1

        if gh > ga: win = m["teams"]["home"]["name"]
        elif gh < ga: win = m["teams"]["away"]["name"]
        else: win = "draw"

        if win == name1: h += 1
        elif win == name2: a += 1
        else: d += 1

    return (
        f"- H2H (Son {total}): {name1}: {h}, {name2}: {a}, Beraberlik: {d}\n"
        f"  • Ortalama gol: {round(total_goals/total, 2)}\n"
        f"  • KG Var: %{round(btts/total*100,1)}\n"
        f"  • 2.5 Üst: %{round(o25/total*100,1)}\n"
    )


# ------------------ DEEPSEEK ------------------ #

def deepseek_predict(home, away, league, hs, as_, h2h_text):
    if not DEEPSEEK_API_KEY:
        return "_(AI tahmini yapılamadı – API KEY eksik)_"

    stats_block = (
        "📊 İSTATİSTİK ÖZETİ:\n" +
        format_stats_for_prompt(home["name"], hs) +
        "\n" +
        format_stats_for_prompt(away["name"], as_) +
        "\n" +
        h2h_text +
        "\n"
    )

    prompt = f"""
Aşağıdaki maç için yüzdelik ve profesyonel futbol analizi üret.

MAÇ: {home['name']} vs {away['name']}
LİG: {league}

{stats_block}

FORMAT:

🏆 Tahmin Özeti:
- Ev Kazanır: %..
- Beraberlik: %..
- Deplasman Kazanır: %..
- KG Var: %..
- Toplam Gol Tahmini: ..
- Alt/Üst Tahmini: Alt / Üst
- KG&Üst Kombin: %..

📊 Detaylı Analiz:
- Ev formu:
- Deplasman formu:
- Gol ortalamaları:
- Ev/deplasman performansı:
- Önemli eksikler:
- En güvenilir tahmin:
- Güven yüzdesi (%..)
"""

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",    # YENİ ENDPOINT
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=40,
        )

        if resp.status_code != 200:
            return "_(AI tahmini alınamadı – API hatası)_"

        return resp.json()["choices"][0]["message"]["content"].strip()

    except Exception:
        return "_(AI tahmini alınırken hata oluştu)_"


# ------------------ FORMAT ------------------ #

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

🤖 *Fatih Koç Tahmini (AI Destekli)*:
{ai_text}
———————————————
"""


# ------------------ JOB ------------------ #

def run_daily_job():
    fixtures = get_today_fixtures()
    if not fixtures:
        send_telegram_message("⚠️ Bugün maç yok.")
        return {"ok": False}

    selected = fixtures[:5]
    cards = []

    for f in selected:
        h = f["teams"]["home"]
        a = f["teams"]["away"]
        league = f["league"]["name"]

        h_last = get_team_last_matches(h["id"])
        a_last = get_team_last_matches(a["id"])

        h_stats = compute_team_stats(h["id"], h_last)
        a_stats = compute_team_stats(a["id"], a_last)

        h2h = get_h2h_matches(h["id"], a["id"])
        h2h_text = format_h2h_stats(h["name"], a["name"], h2h)

        ai = deepseek_predict(h, a, league, h_stats, a_stats, h2h_text)
        cards.append(format_match_card(f, ai))

    final_msg = (
        "🔥 *GÜNÜN VIP MAÇ TAHMİNLERİ* 🔥\n"
        "_(Bilgi amaçlıdır)_\n\n" +
        "\n".join(cards)
    )

    send_telegram_message(final_msg)
    return {"ok": True, "count": len(cards)}


# ------------------ FLASK ------------------ #

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Sistem çalışıyor /run"

@app.route("/run")
def run_endpoint():
    return jsonify(run_daily_job())


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
