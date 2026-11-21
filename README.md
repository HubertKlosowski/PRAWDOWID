# PRAWDOWID

Aplikacja Django porównuje tekst lub artykuł z korpusem instytucjonalnym. Qwen3-Embedding-4B koduje materiał i fragmenty dokumentów. PostgreSQL z pgvector zwraca najbliższe fragmenty, a osobny model ocenia twierdzenie. Widok wyniku pokazuje uzasadnienie, nieskalibrowane oceny klas i cytaty z linkami do dokumentów.

## Uruchomienie lokalne

Potrzebne są Python 3.12, uv oraz działający PostgreSQL z zainstalowanym rozszerzeniem serwerowym pgvector. Utwórz bazę prawdowid dla swojego użytkownika PostgreSQL. Migracja tworzy rozszerzenie vector, co wymaga odpowiednich uprawnień lub wcześniejszego utworzenia go przez administratora. Połączenie korzysta ze standardowych zmiennych PGHOST, PGPORT, PGUSER, PGPASSWORD i PGDATABASE.

```bash
uv sync
uv run python manage.py migrate
uv run hf download Qwen/Qwen3.5-4B
uv run hf download Qwen/Qwen3-Embedding-4B
uv run python manage.py ingest_sources --snapshot data/2026-11-21
uv run python manage.py runserver
```

Otwórz http://127.0.0.1:8000/. Dla dołączonej migawki przykładowe twierdzenie brzmi „Regionalny System Ostrzegania jest darmowy”. Migawka zawiera również komunikaty ograniczone czasowo. Data pobrania opisuje stan korpusu.

Korpus możesz też zbudować przez `uv run python manage.py ingest_sources --sources sources.txt`. Podaj po jednym adresie statycznego dokumentu w wierszu. Komenda respektuje robots.txt i zgłasza błędy pobrania. Import migawki odczytuje istniejące lokalne rekordy, w tym dane z widoków dynamicznych. Powtórzenie importu aktualizuje dokumenty po URL i skrócie treści.

## Konfiguracja modeli

Domyślnie oba modele działają w procesie aplikacji i są ładowane przy pierwszym użyciu. Wymagają pamięci na oba zestawy wag. Na CPU czas odpowiedzi może być długi. Przy gotowym cache można ustawić HF_HUB_OFFLINE=1, a na CPU ograniczyć liczbę wątków przez OMP_NUM_THREADS.

| Zmienna | Znaczenie |
| --- | --- |
| QWEN_EMBEDDING_PATH | Lokalizacja wag Qwen3-Embedding-4B lub domyślny identyfikator Hugging Face |
| QWEN_DEVICE | Urządzenie embeddingów, np. cpu lub cuda |
| RAG_TOP_K | Liczba fragmentów przekazywanych modelowi, domyślnie 5 |
| PRAWDOWID_LLM_DEVICE | Opcjonalne urządzenie oceny. Domyślny model używa CPU, jeśli GPU ma mniej niż 10 GiB wolnej pamięci |
| PRAWDOWID_LLM_MODEL | Model oceniający, domyślnie Qwen/Qwen3.5-4B z lokalnego cache |
| PRAWDOWID_LLM_URL | Opcjonalny pełny adres endpointu chat/completions. Po ustawieniu materiał i fragmenty są wysyłane do tego endpointu |
| PRAWDOWID_LLM_API_KEY | Opcjonalny klucz endpointu |
| DJANGO_ALLOWED_HOSTS | Dozwolone hosty oddzielone przecinkami |
| DJANGO_SECRET_KEY | Stały sekret instancji |
| DJANGO_DEBUG | Domyślnie 1 dla demonstratora lokalnego |

## Sprawdzenie

```bash
uv run python manage.py check
uv run python manage.py test
```

Testy jednostkowe korzystają z atrap inferencji i połączeń sieciowych. Formularz przyjmuje do 12 000 znaków. Pobieranie URL obsługuje HTML i zwykły tekst. Gdy strona wymaga JavaScriptu lub artykuł jest zbyt długi, można wkleić fragment ręcznie. Materiał użytkownika służy wyłącznie bieżącej analizie.

Aplikacja sprawdza identyfikatory i dosłowność cytatów. Interpretacja cytatu oraz przypisana klasa pozostają oceną modelu. Pusty korpus, błąd usługi lub niepoprawna odpowiedź powodują wyświetlenie przyczyny i wyniku „Niezweryfikowane”.
