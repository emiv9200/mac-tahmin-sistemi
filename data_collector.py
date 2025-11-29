import requests
from datetime import datetime
from database import get_db
import os

API_KEY = os.getenv("API_KEY")

HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

API_BASE = "https://v3.football.api-sports.io"


def get_team_form(team_id):
    """Takımın son 5 maçının form grafiği (W/D/L)"""
    url = f"{API_BASE}/fixtures?team={team_id}&last=5"
    r = requests.get(url, headers=HEADERS).json()

    form = ""
    for match in r.get("response", []):
        goals_for = match["teams"]["home"]["goals"] if match["teams"]["home"]["id"] == team_id else match["teams"]["away"]["goals"]
        goals_against = match["teams"]["away"]["goals"] if match["teams"]["home"]["id"] == team_id else match["teams"]["home"]["goals"]

        if goals_for > goals_against:
            form += "W"
        elif goals_for == goals_against:
            form += "D"
        else:
            form += "L"

    return form


def collect_match_data(fixture):
    """Tek maç için detaylı veri alır ve DB'ye kaydeder"""
    fixture_id = fixture["fixture"]["id"]
    league_id = fixture["league"]["id"]
    date = fixture["fixture"]["date"]

    home = fixture["teams"]["home"]
    away = fixture["teams"]["away"]

    home_team = home["name"]
    away_team = away["name"]

    home_id = home["id"]
    away_id = away["id"]

    # Form verileri
    home_form = get_team_form(home_id)
    away_form = get_team_form(away_id)

    # xG ve şut verileri
    stats = fixture.get("statistics", [])
    home_xg = away_xg = 0
    home_shots = away_shots = 0

    # Eğer API xG verisi sağlıyorsa al
    try:
        for team_stats in stats:
            if team_stats["team"]["id"] == home_id:
                home_xg = team_stats["statistics"][16]["value"]  # örnek xG Index
                home_shots = team_stats["statistics"][2]["value"]
            else:
                away_xg = team_stats["statistics"][16]["value"]
                away_shots = team_stats["statistics"][2]["value"]
    except:
        pass

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO matches (fixture_id, league_id, date, home_team, away_team,
                             home_form, away_form, home_xg, away_xg,
                             home_shots, away_shots, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fixture_id, league_id, date, home_team, away_team,
        home_form, away_form, home_xg, away_xg,
        home_shots, away_shots,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()

    print(f"✓ {home_team} - {away_team} kaydedildi")
