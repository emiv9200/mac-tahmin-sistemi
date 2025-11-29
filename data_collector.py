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

# Rate limiting için
LAST_REQUEST_TIME = None
MIN_REQUEST_INTERVAL = 1  # saniye

def rate_limit():
    """API rate limit kontrolü"""
    global LAST_REQUEST_TIME
    if LAST_REQUEST_TIME:
        elapsed = time.time() - LAST_REQUEST_TIME
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    LAST_REQUEST_TIME = time.time()

def api_request(url, params=None):
    """Hata yönetimli API isteği"""
    rate_limit()
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("errors"):
            print(f"⚠️ API Hatası: {data['errors']}")
            return None
            
        return data
    except requests.exceptions.RequestException as e:
        print(f"❌ İstek hatası: {e}")
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

def get_odds(fixture_id):
    """Maç için bahis oranlarını çek"""
    url = f"{API_BASE}/odds"
    params = {
        "fixture": fixture_id,
        "bookmaker": 8  # Bet365
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
        "btts_no_odds": None
    }
    
    try:
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
            elif bet_name == "Goals Over/Under" and "2.5" in str(bet.get("values", [])):
                for value in bet["values"]:
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
    
    except (KeyError, IndexError, ValueError) as e:
        print(f"⚠️ Odds parse hatası: {e}")
        return None
    
    return odds_data

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
    """Tek maç için detaylı veri topla ve kaydet"""
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
        
        # Odds verileri - EN ÖNEMLİ!
        print("  ↳ Odds bilgileri alınıyor...")
        odds = get_odds(fixture_id)
        if not odds:
            print(f"  ⚠️ Odds bulunamadı, maç atlanıyor")
            return False
        
        # İstatistikler
        print("  ↳ İstatistikler hesaplanıyor...")
        home_stats = calculate_team_stats(home_id)
        away_stats = calculate_team_stats(away_id)
        
        # Veritabanına kaydet
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO predictions (
                match_id, home_team, away_team, league, match_date,
                home_odds, draw_odds, away_odds,
                over_2_5_odds, under_2_5_odds,
                btts_yes_odds, btts_no_odds,
                ai_prediction, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s
            )
            ON CONFLICT (match_id) DO NOTHING
        """, (
            fixture_id, home_team, away_team, league, match_date,
            odds["home_odds"], odds["draw_odds"], odds["away_odds"],
            odds["over_2_5_odds"], odds["under_2_5_odds"],
            odds["btts_yes_odds"], odds["btts_no_odds"],
            f"Form: H({home_form}) A({away_form}) | Avg Goals: H({home_stats['goals_avg']}) A({away_stats['goals_avg']})",
            datetime.now()
        ))
        
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
        
        print(f"  ✅ {home_team} - {away_team} başarıyla kaydedildi!")
        return True
        
    except Exception as e:
        print(f"  ❌ Hata: {e}")
        return False

def collect_today_matches(league_ids=None):
    """Bugünkü maçları topla"""
    if league_ids is None:
        # Önemli ligler (örnekler)
        league_ids = [
            39,   # Premier League
            140,  # La Liga
            135,  # Serie A
            78,   # Bundesliga
            61,   # Ligue 1
            203,  # Süper Lig
        ]
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n🔍 {today} tarihli maçlar aranıyor...\n")
    
    total_collected = 0
    
    for league_id in league_ids:
        url = f"{API_BASE}/fixtures"
        params = {
            "league": league_id,
            "date": today
        }
        
        data = api_request(url, params)
        if not data:
            continue
        
        fixtures = data.get("response", [])
        print(f"📌 Lig {league_id}: {len(fixtures)} maç bulundu")
        
        for fixture in fixtures:
            if collect_match_data(fixture):
                total_collected += 1
                time.sleep(2)  # API'ye nazik ol
    
    print(f"\n✅ Toplam {total_collected} maç kaydedildi!")
    return total_collected

if __name__ == "__main__":
    collect_today_matches()
