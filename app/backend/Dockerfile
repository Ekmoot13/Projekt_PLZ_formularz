# 1. Wybierz lekki obraz bazowy (zgodnie z wymaganiami o odporności i wydajności)
FROM python:3.9-slim

# 2. Ustaw katalog roboczy
WORKDIR /app

# 3. Zainstaluj zależności systemowe dla Postgresa
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# 4. Skopiuj wymagania i zainstaluj biblioteki
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Skopiuj kod aplikacji
COPY . .

# 6. Uruchom aplikację
CMD ["python", "app.py"]