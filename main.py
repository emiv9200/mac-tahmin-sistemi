import os
from dotenv import load_dotenv
import requests
import pandas as pd
from flask import Flask, jsonify

# .env dosyasını yükle (lokalde işine yarar, Render'da env panelinden alacağız)
load_dotenv()

# API Key'i al
API_KEY = os.getenv("API_KEY")

if not API_KEY:
    print("❌ API_KEY bulunamadı! Render panelinden ya da .env'den tanımlamalısın.")
else:
    print("✅ API_KEY yüklendi (gizli, sadece varlığını kontrol ediyoruz).")

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

url = "https://v3.football.api-sports.io/fixtures?league=39&season=2023"


def getir_maclar():
    """
    API'den maçları çekip DataFrame olarak döndürür.
    Şimdilik sadece test amaçlı; ileride buraya model / filtre ekleriz.
    """
    print("➡ Maç verileri çekiliyor...")
    response = requests.get(url, headers=headers)
    data = response.json()

    # Güvenlik amaçlı log
    if "response" not in data:
        print("⚠ Beklenmeyen API cevabı:", data)
        return None, data

    # Basit bir DataFrame örneği
    fixtures = data["response"]
    rows = []
    for f in fixtures:
        try:
            row = {
                "tarih": f["fixture"]["date"],
                "ev": f["teams"]["home"]["name"],
                "deplasman": f["teams"]["away"]["name"],
                "lig": f["league"]["name"],
                "ülke": f["league"]["country"],
                "durum": f["fixture"]["status"]["short"],
            }
            rows.append(row)
        except Exception as e:
            print("Satır parse edilirken hata:", e)

    df = pd.DataFrame(rows)
    print(f"✅ Toplam {len(df)} maç çekildi.")
    return df, data


# ---------------------- Flask Uygulaması ---------------------- #

app = Flask(__name__)


@app.route("/")
def home():
    return "Maç Tahmin Sistemi Çalışıyor ✅"


@app.route("/run")
def run_job():
    """
    Bu endpoint çağrıldığında API'den maçları çeker.
    İleride buraya tahmin modeli + Telegram gönderme ekleriz.
    """
    df, raw = getir_maclar()
    if df is None:
        return jsonify({"ok": False, "message": "API cevabı beklenenden farklı."}), 500

    # Sadece ilk birkaç maçı döndürelim
    preview = df.head(5).to_dict(orient="records")
    return jsonify({
        "ok": True,
        "toplam_mac": len(df),
        "ilk_5_mac": preview
    })


if __name__ == "__main__":
    # Render PORT env değişkeni gönderiyor, ona göre dinle
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Flask server {port} portunda ayağa kalkıyor...")
    app.run(host="0.0.0.0", port=port)
