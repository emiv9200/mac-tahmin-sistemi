import os
import time
import requests
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# API Key'i al
API_KEY = os.getenv("API_KEY")
print("API KEY yüklendi:", API_KEY)

# Request header
headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# Premier League (39) – 2023 sezonu
URL = "https://v3.football.api-sports.io/fixtures?league=39&season=2023"


def maclari_cek():
    """API'den maçları çeker ve ekrana yazar"""
    try:
        print("\n🔍 API isteği gönderiliyor...")
        response = requests.get(URL, headers=headers, timeout=15)

        if response.status_code != 200:
            print("❌ API Hatası:", response.status_code, response.text)
            return

        data = response.json()
        print("✅ API cevabı alındı!")
        print(data)

    except Exception as e:
        print("⚠️ İstek sırasında hata oluştu:", e)


print("\n🚀 Sistem başladı! Render kapanmaması için sürekli çalışıyor...\n")

# Sonsuz döngü (Render kapanmasın)
while True:
    maclari_cek()
    print("⏳ Bir sonraki istek 1 saat sonra...")
    time.sleep(3600)  # 1 saat bekle
