import os
from dotenv import load_dotenv
import requests
import pandas as pd

# .env dosyasını oku
load_dotenv()

# API Key'i al
API_KEY = os.getenv("API_KEY")
print("API KEY:", repr(API_KEY))  # TEST SATIRI

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

url = "https://v3.football.api-sports.io/fixtures?league=39&season=2023"

response = requests.get(url, headers=headers)
data = response.json()

print("API Cevabı:", data)
