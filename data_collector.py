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

BOOKMAKERS = [8, 11, 5, 6, 9, 12, 3]

LAST_REQUEST_TIME = None
MIN_REQUEST_INTERVAL = 1  # seconds

def rate_limit():
    global LAST_REQUEST_TIME
    if LAST_REQUEST_TIME:
        elapsed = time.time() - LAST_REQUEST_TIME
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    LAST_REQUEST_TIME = time.time()

def api_request(url, params=None, retry_count=2):
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
    url = f"{API_BASE}/odds"
    params = {"fixture": fixture_id, "bookmaker": bookmaker_id}
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
            if bet_name == "Match Winner":
                for value in bet["values"]:
                    if value["value"] == "Home":
                        odds_data["home_odds"] = float(value["odd"])
                    elif value["value"] == "Draw":
                        odds_data["draw_odds"] = float(value["odd"])
                    elif value["value"] == "Away":
                        odds_data["away_odds"] = float(value["odd"])
            elif bet_name == "Goals Over/Under":
                for value in bet["values"]:
                    if "2.5" in value["value"]:
                        if "Over" in value["value"]:
                            odds_data["over_2_5_odds"] = float(value["odd"])
                        elif "Under" in value["value"]:
                            odds_data["under_2_5_odds"] = float(value["odd"])
            elif bet_name == "Both Teams Score":
                for value in bet["values"]:
                    if value["value"] == "Yes":
                        odds_data["btts_yes_odds"] = float(value["odd"])
                    elif value["value"] == "No":
                        odds_data["btts_no_odds"] = float(value["odd"])
        if odds_data["home_odds"] and odds_data["draw_odds"] and odds_data["away_odds"]:
            return odds_data
        return None
    except (KeyError, IndexError, ValueError) as e:
        print(f"⚠️ Odds parse hatası (bookmaker {bookmaker_id}): {e}")
        return None

def get_odds(fixture_id):
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
        time.sleep(0.5)
    print(f"     ❌ Hiçbir bookmaker'dan odds alınamadı!")
    return None

def calculate_team_stats(team_id, last_n=10):
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
        print("  ↳ Form bilgileri alınıyor...")
        home_form = get_team_form(home_id)
        away_form = get_team_form(away_id)
        print("  ↳ İstatistikler hesaplanıyor...")
        home_stats = calculate_team_stats(home_id)
        away_stats = calculate_team_stats(away_id)
        odds = get_odds(fixture_id)
        conn = get_db()
        cur = conn.cursor()
        if odds:
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
    if league_ids is None:
        league_ids = [39, 140, 135, 78, 61, 203]
    today = datetime.now().strftime("%Y-%m-%d")
    print("\n" + "="*60)
    print(f"🔍 {today} TARİHLİ MAÇLAR ARANLIYOR")
    print("="*60 + "\n")
    total_collected = 0
    total_with_odds = 0
    total_without_odds = 0
    for league_id in league_ids:
        url = f"{API_BASE}/fixtures"
        params = {"league": league_id, "date": today}
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
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT home_odds FROM predictions WHERE match_id = %s", (str(fixture["fixture"]["id"]),))
                result = cur.fetchone()
                close_db(conn)
                if result and result[0] is not None:
                    total_with_odds += 1
                else:
                    total_without_odds += 1
                time.sleep(2)
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
    collect_today_matches()
