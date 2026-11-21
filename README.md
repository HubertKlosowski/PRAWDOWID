# PRAWDOWID

Aplikacja Django porównuje tekst lub artykuł z korpusem instytucjonalnym. Qwen3-Embedding-0.6B koduje materiał i fragmenty dokumentów. PostgreSQL z pgvector zwraca pulę najbliższych fragmentów. Model oceniający wybiera z niej bezpośrednie dowody, a następnie ocenia twierdzenie na ich podstawie. Widok wyniku pokazuje uzasadnienie, nieskalibrowane oceny klas i cytaty z linkami do dokumentów.

## Uruchomienie lokalne

Potrzebne są Python 3.12, uv oraz działający PostgreSQL z zainstalowanym rozszerzeniem serwerowym pgvector. Utwórz bazę prawdowid dla swojego użytkownika PostgreSQL. Migracja tworzy rozszerzenie vector, co wymaga odpowiednich uprawnień lub wcześniejszego utworzenia go przez administratora. Połączenie korzysta ze standardowych zmiennych PGHOST, PGPORT, PGUSER, PGPASSWORD i PGDATABASE.

```bash
uv sync
uv run python manage.py migrate
uv run hf download Qwen/Qwen3.5-4B
uv run hf download Qwen/Qwen3-Embedding-0.6B
uv run python manage.py ingest_sources --snapshot data/2026-11-21
uv run python manage.py runserver
```

Otwórz http://127.0.0.1:8000/. Dla dołączonej migawki przykładowe twierdzenie brzmi „Regionalny System Ostrzegania jest darmowy”. Migawka zawiera również komunikaty ograniczone czasowo. Data pobrania opisuje stan korpusu.

Korpus możesz też zbudować przez `uv run python manage.py ingest_sources --sources sources.txt`. Podaj po jednym adresie statycznego dokumentu w wierszu. Komenda respektuje robots.txt i zgłasza błędy pobrania. Import migawki odczytuje istniejące lokalne rekordy, w tym dane z widoków dynamicznych. Powtórzenie importu aktualizuje dokumenty po URL i skrócie treści.

Przy aktualizacji z 4B migracja zachowuje dokumenty i teksty fragmentów, a usuwa stare wektory. Po migracji i pobraniu modelu uruchom `uv run python manage.py reembed_chunks`, aby przeliczyć zapisane fragmenty bez ponownego pobierania źródeł. Wyszukiwanie uwzględnia fragmenty w miarę uzupełniania ich embeddingów.

## Konfiguracja modeli

Domyślnie oba modele działają w procesie aplikacji i są ładowane przy pierwszym użyciu. Wymagają pamięci na oba zestawy wag. Na CPU czas odpowiedzi może być długi. Przy gotowym cache można ustawić HF_HUB_OFFLINE=1, a na CPU ograniczyć liczbę wątków przez OMP_NUM_THREADS.

Do pokazu użyj `./run-demo.sh`. Skrypt uruchamia `manage.py serve_demo --noreload`, który ładuje modele raz, przed przyjęciem pierwszego żądania. Jeśli skonfigurowano endpoint oceniający przez `PRAWDOWID_LLM_URL`, serwer ładuje lokalnie tylko model embeddingowy. Możesz wymusić pracę na CPU poleceniem `QWEN_DEVICE=cpu PRAWDOWID_LLM_DEVICE=cpu ./run-demo.sh`.

Na CPU z AVX-512 BF16 i obsługą oneDNN lokalny Qwen3.5 z wagami BF16 przygotowuje wagi warstw liniowych dekodera podczas ładowania. Kolejne kroki generowania korzystają z gotowego układu wag. Aby porównać czasy na własnym procesorze, uruchom osobny proces z `PRAWDOWID_CPU_PREPACK=0` i sprawdź te same pytania po załadowaniu modelu.

Na Linux x86-64 z kartą NVIDIA uruchom Qwen3.5 z opcjonalnymi kernelami CUDA. Pakiet `causal-conv1d` jest skompilowany dla wersji PyTorch 2.11 i CUDA 12.8 używanych przez projekt.

```bash
PRAWDOWID_LLM_DEVICE=cuda uv run --extra cuda-kernels python manage.py runserver
```

Polecenie wymaga pamięci GPU na model oceniający. Możesz ustawić `QWEN_DEVICE=cpu`, aby zwolnić pamięć GPU zajmowaną przez model embeddingowy. Przy powrocie do pracy na CPU uruchom `uv sync`, aby usunąć opcjonalne kernele CUDA ze środowiska. Tryb CPU używa implementacji referencyjnej PyTorch.

| Zmienna | Znaczenie |
| --- | --- |
| QWEN_EMBEDDING_PATH | Opcjonalna ścieżka do lokalnej kopii Qwen3-Embedding-0.6B (1024 wymiary). Domyślnie `Qwen/Qwen3-Embedding-0.6B` |
| QWEN_DEVICE | Urządzenie embeddingów, np. cpu lub cuda |
| RAG_TOP_K | Maksymalna liczba fragmentów do końcowej oceny, domyślnie 5. pgvector pobiera do trzech razy tyle kandydatów, maksymalnie 40 |
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

Aplikacja zwraca `resolved` z werdyktem i klikalnymi cytatami albo jeden ze statusów bez werdyktu. `abstained` oznacza niewystarczające lub sprzeczne dowody albo niedostępny materiał. `needs_clarification` prosi o doprecyzowanie tezy. `out_of_scope` dotyczy zadań innych niż sprawdzanie twierdzeń faktograficznych. `technical_error` oznacza awarię wyszukiwania lub brak poprawnej odpowiedzi modelu.

Model wybiera identyfikatory gotowych cytatów. Druga ocena sprawdza ich związek z całą tezą bez dostępu do proponowanego werdyktu. Rozstrzygnięcie wymaga zgodności obu ocen. Przy sprzecznych relacjach aplikacja wstrzymuje rozstrzygnięcie. Cytaty, ich metadane i tekst objaśnienia pochodzą z aplikacji. Ocena związku cytatu z tezą zależy od modelu. Po pustej selekcji fragmentów ocena korzysta z najbliższych wyników pgvector, do limitu RAG_TOP_K.

## Google Fact Check Tools API

Integracja jest opcjonalna. Ustaw `GOOGLE_FACTCHECK_API_KEY` w środowisku procesu lub w lokalnym `.env`. Skrypt `run-demo.sh` korzysta z `.env`, jeśli plik istnieje. Przy ręcznym uruchomieniu serwera lub komendy dodaj `uv run --env-file .env`.

Po włączeniu integracji aplikacja wysyła do Google do trzech krótkich fragmentów zgłoszenia zawierających twierdzenia. Polskie zapytania używają filtra `pl`, a dla nierozpoznanego języka filtr jest pomijany. Wyniki są filtrowane według zgodności twierdzeń, trafiają do Qwen obok cytatów z pgvector i mogą pojawić się z oryginalną oceną wydawcy, datą oraz linkiem. API dostarcza metadane artykułów. Jednoznaczny werdykt nadal wymaga cytatów z korpusu, które przejdą kontrolę dowodów. Przy awarii wyszukiwania Google aplikacja informuje o niedostępności i kontynuuje weryfikację za pomocą korpusu.

## Weryfikacja z terminala

```bash
uv run python manage.py verify_claim --text "Regionalny System Ostrzegania jest darmowy."
uv run python manage.py verify_claim --url "https://example.org/artykul"
```

Komenda zwraca wynik w JSON-ie i wywołuje tę samą usługę `verify(...)` co formularz WWW. Usługa przyjmuje opcjonalne adaptery `complete(system_prompt, data)` i `search(text, limit)`. Domyślny adapter modelu korzysta z konfiguracji opisanej wyżej, a domyślny adapter wyszukiwania zwraca uporządkowane fragmenty z pgvector wraz z metadanymi źródeł.

## Aktualność źródeł

Uruchom `uv run python manage.py migrate`, a następnie ponów import migawki lub skonfigurowanych stron, aby uzupełnić metadane istniejących dokumentów. Datę publikacji zapisujemy wtedy, gdy pochodzi z migawki lub jawnych metadanych HTML. Czas pobrania jest przechowywany w dotychczasowym polu bazy `fetched_at` i jest dostępny jako `retrieved_at` w wyniku weryfikacji. Zgrupowane rekordy migawki mają metadane wspólne dla całego dokumentu, jeśli rekordy są zgodne. Daty przypisane do poszczególnych rekordów pozostają również w ich treści.

Wśród wyników pgvector pierwszeństwo mają źródła pasujące do okresu tezy. Przedziały ważności poza tym okresem są odrzucane. Dla pytań o stan bieżący bez przedziału ważności wymagamy publikacji lub początku ważności z bieżącego dnia i model nadal ocenia treść. Rozpoznawanie okresu w kodzie obejmuje typowe daty, lata i proste określenia względne. Pozostałe sformułowania interpretuje model. Daty bez strefy czasowej z polskiej migawki są interpretowane w strefie `Europe/Warsaw`.
