# PRAWDOWID

Aplikacja Django porównuje tekst lub artykuł z korpusem instytucjonalnym. Qwen3-Embedding-0.6B koduje materiał i fragmenty dokumentów. PostgreSQL z pgvector zwraca pulę najbliższych fragmentów. Model oceniający wybiera z niej bezpośrednie dowody, a następnie ocenia twierdzenie na ich podstawie. Widok wyniku pokazuje uzasadnienie, nieskalibrowane oceny klas i cytaty z linkami do dokumentów.

## Uruchomienie lokalne

Po sklonowaniu repozytorium uruchom z jego katalogu `./run-demo.sh` jako zwykły
użytkownik Linuksa. Potrzebne są Bash, `flock`, uv i Python 3.12 oraz narzędzia
lokalnego PostgreSQL (`pg_config`, `initdb`, `pg_ctl`, `pg_isready`, `psql`,
`createdb`, `pg_restore`). Użyj klienta PostgreSQL 18 do odczytu dołączonego
zrzutu i pgvector zainstalowanego dla wersji używanego serwera.

Skrypt przygotowuje środowisko z `uv.lock`, lokalny klaster, migracje i korpus.
Potem ładuje modele i udostępnia formularz pod http://127.0.0.1:8000/.
Pierwsze uruchomienie wymaga pobrania zależności i wag modeli, jeśli nie są
w cache. W formularzu wpisz „Regionalny System Ostrzegania jest darmowy.”
i sprawdź, czy wynik zawiera cytat oraz link do źródła MSWiA.

`./run-demo.sh --prepare` kończy po utworzeniu bazy i audycie korpusu.
Domyślnie używa katalogu `/tmp/prawdowid-pg`. Jeśli sprawdzasz start od zera
na tej samej maszynie, wskaż osobny pusty katalog przez
`PRAWDOWID_DEMO_PG_ROOT`. Adres WWW można ustawić w `PRAWDOWID_DEMO_BIND`.

Jeśli skrypt kończy się wcześniej, jego komunikat wskazuje etap. Przy błędzie
PostgreSQL sprawdź `server.log` w katalogu klastra. Przy komunikacie o braku
pgvector doinstaluj rozszerzenie dla wersji serwera. Przy błędzie ładowania
modelu sprawdź dostępność wag i pamięć urządzenia, a na komputerze bez GPU
możesz wymusić `QWEN_DEVICE=cpu PRAWDOWID_LLM_DEVICE=cpu`.

Dla samodzielnie zarządzanej instancji utwórz pustą bazę `prawdowid` dla swojego
użytkownika PostgreSQL i ustaw parametry połączenia `PGHOST`, `PGPORT`, `PGUSER`
i `PGDATABASE` odpowiednio do środowiska. Migracja tworzy rozszerzenie vector,
co wymaga odpowiednich uprawnień lub wcześniejszego utworzenia go przez administratora.
Import wymaga narzędzia `pg_restore` obsługującego dołączony zrzut.

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py load_demo_corpus
uv run python manage.py audit_corpus --check --csv audyt-demo.csv
uv run hf download Qwen/Qwen3.5-4B
uv run hf download Qwen/Qwen3-Embedding-0.6B
uv run python manage.py runserver
```

Otwórz http://127.0.0.1:8000/. Przykładowe twierdzenie brzmi „Regionalny System
Ostrzegania jest darmowy”. Import wykorzystuje `prawdowid-demo-2026-11-21.dump`
i [katalog metadanych](corpus/demo-sources.csv). Dodaje brakujące dokumenty
i uzupełnia zgodne dane bez ponownego pobierania stron ani liczenia embeddingów.
Zachowuje istniejącą treść dokumentów i uzupełnia wektory, gdy zgadza się wersja
treści i podział na fragmenty.

Dodatkowe źródła możesz zaindeksować poleceniem
`uv run python manage.py ingest_sources --sources sources.txt`. Podaj po jednym
adresie statycznego dokumentu w wierszu. Komenda respektuje robots.txt i zgłasza
błędy pobrania. Polecenie `ingest_sources --snapshot KATALOG` służy do importu
własnej migawki Parquet. Pełny korpus demonstracyjny jest w dołączonym zrzucie.

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
| RAG_TOP_K | Maksymalna liczba fragmentów przekazywanych do oceny, domyślnie 2. Przed filtrowaniem według okresu pgvector pobiera do trzech razy tyle kandydatów, maksymalnie 40 |
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

Testy jednostkowe korzystają z atrap inferencji i połączeń sieciowych. Formularz przyjmuje do 12 000 znaków. Pobieranie URL obsługuje HTML i zwykły tekst. Gdy strona wymaga JavaScriptu lub artykuł jest zbyt długi, można wkleić fragment ręcznie. Historia demonstratora przechowuje zamaskowane zgłoszenie i identyfikatory dowodów przez okres opisany niżej.

Aplikacja zwraca `resolved` z werdyktem i klikalnymi cytatami albo jeden ze statusów bez werdyktu. `abstained` oznacza niewystarczające lub sprzeczne dowody. `material_unavailable` oznacza niedostępny lub nieczytelny artykuł albo przekroczenie limitu długości pobranego tekstu. `needs_clarification` prosi o doprecyzowanie tezy. `out_of_scope` dotyczy zadań innych niż sprawdzanie twierdzeń faktograficznych. `technical_error` oznacza awarię wyszukiwania lub brak poprawnej odpowiedzi modelu.

Po filtrowaniu i uporządkowaniu kandydatów według zgodności z okresem tezy aplikacja przekazuje do oceny najwyżej sklasyfikowane fragmenty z pgvector. Model ocenia tezę i wybiera identyfikatory gotowych cytatów w jednym wywołaniu. Druga ocena sprawdza związek cytatów z całą tezą bez dostępu do proponowanego werdyktu. Rozstrzygnięcie wymaga zgodności obu ocen. Przy sprzecznych relacjach aplikacja wstrzymuje rozstrzygnięcie. Cytaty, ich metadane i tekst objaśnienia pochodzą z aplikacji.

### Próba spójności kanałów

Po przygotowaniu domyślnej bazy uruchom test akceptacyjny:

```bash
PGHOST=/tmp/prawdowid-pg PGPORT=5432 uv run --locked --extra discord \
  python manage.py test verify.test_demo_channels
```

Test tworzy osobną bazę PostgreSQL, odtwarza korpus i porównuje tekst i URL w WWW
oraz CLI. Sprawdza wynik każdej tezy, kod braku rozstrzygnięcia, identyfikatory
dokumentów, fragmentów i cytatów oraz linki w HTML. Osobna próba wykonuje
callback `/sprawdz` z rzeczywistą usługą weryfikacji i sprawdza odpowiedź
oraz zapis przebiegu. Automatyczny zestaw używa atrap inferencji i transportu
sieciowego.

Po pobraniu modeli można włączyć próbę WWW–CLI z rzeczywistą inferencją:

```bash
PRAWDOWID_LIVE_CHANNEL_TESTS=1 PGHOST=/tmp/prawdowid-pg PGPORT=5432 \
  uv run --locked python manage.py test verify.test_demo_channels.LiveDemoChannelsTests
```

Dołącz `PRAWDOWID_DEMO_TEST_URL` z publicznym adresem krótkiego artykułu HTML,
jeżeli chcesz wykonać również rzeczywiste pobranie URL. Próba porównuje oba
kanały przy tym samym skonfigurowanym modelu, z wyłączonym Google Fact Check.
Na CPU może potrwać kilka minut.

## Google Fact Check Tools API

Integracja jest opcjonalna. Ustaw `GOOGLE_FACTCHECK_API_KEY` w środowisku procesu lub w lokalnym `.env`. Skrypt `run-demo.sh` korzysta z `.env`, jeśli plik istnieje. Przy ręcznym uruchomieniu serwera lub komendy dodaj `uv run --env-file .env`.

Po włączeniu integracji aplikacja wysyła do Google do trzech krótkich fragmentów zgłoszenia zawierających twierdzenia. Polskie zapytania używają filtra `pl`, a dla nierozpoznanego języka filtr jest pomijany. Wyniki są filtrowane według zgodności twierdzeń, trafiają do Qwen obok cytatów z pgvector i mogą pojawić się z oryginalną oceną wydawcy, datą oraz linkiem. API dostarcza metadane artykułów. Jednoznaczny werdykt nadal wymaga cytatów z korpusu, które przejdą kontrolę dowodów. Przy awarii wyszukiwania Google aplikacja informuje o niedostępności i kontynuuje weryfikację za pomocą korpusu.

## Weryfikacja z terminala

```bash
uv run python manage.py verify_claim --text "Regionalny System Ostrzegania jest darmowy."
uv run python manage.py verify_claim --url "https://example.org/artykul"
```

Komenda zwraca wynik w JSON-ie i wywołuje tę samą usługę `verify(...)` co formularz WWW. Usługa przyjmuje opcjonalne adaptery `complete(system_prompt, data)` i `search(text, limit)`. Domyślny adapter modelu korzysta z konfiguracji opisanej wyżej, a domyślny adapter wyszukiwania zwraca uporządkowane fragmenty z pgvector wraz z metadanymi źródeł.

## Katalog, audyt i aktualność źródeł

Pełny pakiet demonstracyjny ma 435 dokumentów i 543 fragmenty z embeddingami.
Katalog przypisuje dokumenty do 10 domen wydawców i 8 rodzajów materiału.
Obejmuje źródła urzędowe, pierwotne i wtórne. Domena w katalogu grupuje
subdomeny należące do tego samego wydawcy. Pole `source_type` określa charakter
źródła, a `kind` rodzaj materiału, np. pomiar, komunikat lub analiza redakcyjna.

`audit_corpus` raportuje stan aktywnej bazy. Do minimów wlicza dokumenty mające
embeddingi wszystkich fragmentów i liczy identyczne skróty treści tylko raz.
Opcja `--csv` eksportuje także rekordy niepełne, z identyfikatorem dokumentu,
adresem, domeną, wydawcą, metadanymi dat i statusem indeksowania. Z opcją
`--check` komenda kończy się błędem, jeśli baza nie spełnia minimów.

Do demonstratora przeniesiono okresy ważności z katalogu migawki. Publikację
uzupełniono tam, gdzie data jest widoczna w zachowanym materiale lub pochodzi
z pola daty raportu w migawce. Daty utworzenia pomiaru i pobrania dokumentu
mają odrębne znaczenie. Dowody w WWW, CLI i Discordzie zawierają wydawcę oraz
daty, jeśli są znane. Czas pobrania jest przechowywany jako `fetched_at` i zwracany
w wyniku jako `retrieved_at`. Przy nowszej wersji materiału katalog służy do
uzupełnienia danych wydawcy i rodzaju źródła, a daty pozostają związane z wersją
treści.

Wśród wyników pgvector pierwszeństwo mają źródła pasujące do okresu tezy. Przedziały ważności poza tym okresem są odrzucane. Dla pytań o stan bieżący bez przedziału ważności wymagamy publikacji lub początku ważności z bieżącego dnia i model nadal ocenia treść. Rozpoznawanie okresu w kodzie obejmuje typowe daty, lata i proste określenia względne. Pozostałe sformułowania interpretuje model. Daty bez strefy czasowej z polskiej migawki są interpretowane w strefie `Europe/Warsaw`.

### Discord — komenda `/sprawdz`

Adapter korzysta z Discord Gateway. Komputer z demonstratorem potrzebuje połączenia wychodzącego z Discordem. Pole **Interactions Endpoint URL** w konfiguracji aplikacji pozostaw puste.

1. W [Discord Developer Portal](https://discord.com/developers/applications) utwórz osobną aplikację dla PRAWDOWID. W zakładce **Bot** wygeneruj token. W zakładce **Installation** skonfiguruj **Guild Install** z zakresami `bot` i `applications.commands`, następnie przez link instalacyjny dodaj aplikację na swój serwer demonstracyjny. Wystarczą zwykłe uprawnienia do kanału, wysyłania wiadomości i załączania plików.
2. W Discordzie włącz tryb dewelopera i skopiuj identyfikator serwera. W lokalnym pliku `.env` ustaw `DISCORD_BOT_TOKEN` i `DISCORD_GUILD_ID`. Token zachowaj w tajemnicy.
3. Dla lokalnej bazy demonstracyjnej uruchom poniższy skrypt. Korzysta z tego samego katalogu bazy co `run-demo.sh` (`/tmp/prawdowid-pg/db`), przygotowuje korpus, wczytuje `.env` i uruchamia bota.

   ```bash
   ./run-discord-demo.sh
   ```

4. Na kanale serwera wybierz `/sprawdz`, wpisz w pole `material` twierdzenie lub sam adres HTTPS i wyślij. Wynik widzi osoba wywołująca komendę. Podczas pokazu sprawdź twierdzenie objęte korpusem, otwórz źródło w odpowiedzi, a następnie wyślij twierdzenie spoza korpusu.

Przy innej bazie ustaw jej zmienne połączenia i uruchom `uv run --locked --extra discord --env-file .env python manage.py run_discord`. Bot działa jako osobny proces i obsługuje jedną analizę naraz. Przy lokalnej inferencji jednoczesne uruchomienie bota i WWW wymaga pamięci na modele obu procesów. Komenda działa również przy wyłączonym serwerze WWW. Bot kończy oczekiwanie po 14 minutach. Rozpoczęta analiza może jeszcze zajmować model. Długie raporty są dostępne w załączniku tekstowym.

Test adaptera z atrapami Discorda i weryfikacji uruchom poleceniem `uv run --extra discord python manage.py test verify.test_discord`.

## Zgłoszenia z kilkoma tezami

Model wydziela najwyżej cztery tezy z tekstu lub z pobranego artykułu. Teza i ewentualny
kontekst muszą dosłownie wystąpić w zgłoszeniu po ujednoliceniu odstępów. Każda teza
ma własne wyszukiwanie, ocenę i dowody. Jeżeli model zgłosi pominięcie dalszych tez,
WWW i Discord wyświetlą komunikat, a JSON zwróci `claims_truncated: true`.
Dla kilku tez CLI zwraca `status: multiple_claims` i listę `claims` z wynikami.
Pojedyncza teza zachowuje dotychczasowe pola wyniku i zyskuje pole `claim`.

Werdykt `misleading` („Wprowadzające w błąd”) wymaga osobnych cytatów dla prawdziwego
elementu tezy i pominiętego kontekstu, który zmienia jej sens. Przy źródłach, które
sobie przeczą, aplikacja wstrzymuje rozstrzygnięcie.

### Powody braku werdyktu i siła dowodów

Wynik bez werdyktu zawiera `reason_code`, np. `no_sources`, `conflicting_evidence`,
`period_mismatch` albo `article_unreadable`. WWW i Discord wyświetlają objaśnienie,
a CLI udostępnia objaśnienie i kod w JSON-ie.

`evidence_strength` uwzględnia bezpośredniość zweryfikowanych cytatów, liczbę dokumentów,
rodzaj źródła i zgodność dat z okresem tezy. Wydawcy lub domeny są wskaźnikiem odrębności.
Tę samą domenę, tego samego wydawcę, ten sam dokument i identyczne cytaty grupujemy razem.
Silna podstawa wymaga zgodności dat z okresem tezy, co najmniej dwóch odrębnych grup
wspierających ten sam werdykt oraz źródła urzędowego lub pierwotnego. Jeśli adapter nie
podaje rodzaju źródła, domeny w gov.pl klasyfikujemy jako urzędowe, a pozostałe oznaczamy
jako nieustalone. Ocena siły dowodów opiera się na metadanych i zweryfikowanych relacjach
cytatów do tezy.

## Historia sesji i metryki

Po aktualizacji uruchom `uv run python manage.py migrate`. WWW, CLI i Discord
zapisują jeden rekord przebiegu na zgłoszenie. Zawiera on identyfikator żądania,
czas, zamaskowane pytanie, wyniki poszczególnych tez i identyfikatory dokumentów
oraz fragmentów. Użyte weryfikacje Google mają zapisany host i skrót adresu.
Dla zgłoszonego URL zapisujemy wyłącznie schemat i host. Cytaty i treść pobranego
artykułu nie są archiwizowane w historii. Wejścia odrzucone przez usługę z powodu
walidacji, np. niedozwolony URL, mają kod zdarzenia bez zapisanego pytania.

Maskowanie obejmuje oczywiste e-maile, polskie numery telefonu, PESEL i oznaczone
numery identyfikacyjne. Działa przed zapisem i wysłaniem danych do Google Fact
Check lub zdalnego modelu. To maskowanie wzorców, więc może pominąć np. nazwiska
i niestandardowo zapisane identyfikatory. Przy zdalnym modelu analiza pracuje na
zamaskowanym materiale, co może ograniczyć możliwość sprawdzenia tez opartych
na tych danych. Pobieranie strony nadal wymaga przesłania jej adresu do serwera
źródłowego.

- **WWW** — użyj przycisku „Usuń historię tej sesji”. Przeglądarka identyfikuje
  sesję losowym kluczem w ciasteczku HttpOnly.
- **Discord** — użyj `/usun_sesje`. Klucz sesji jest wyprowadzany z użytkownika
  i serwera przy użyciu sekretu bota. Usunięcie obejmuje rekordy aplikacji.
- **CLI** — zachowaj `session_key` z wyniku jako sekret. Aby użyć tej samej
  sesji, ustaw go jako `PRAWDOWID_DEMO_SESSION` lub przekaż przez `--session`.
  Gdy zmienna jest ustawiona, `uv run python manage.py verify_claim --delete-session`
  usuwa jej historię. Baza przechowuje skróty kluczy sesji.

Domyślna retencja sesji, jej historii i zapisanych zdarzeń wynosi **7 dni od
utworzenia sesji**. Ustawienie `PRAWDOWID_RETENTION_DAYS` dotyczy nowo tworzonych
sesji. Raport metryk pomija wygasłe rekordy. Przy aktywnym demonstratorze
aplikacja sprząta wygasłe sesje przy otwieraniu historii, najwyżej raz na dobę
w procesie. W środowisku bez ruchu uruchamiaj codziennie
`uv run python manage.py prune_verification_history` przez harmonogram systemu.
Komenda usuwa sesje i ich przebiegi oraz pozostawia wspólny korpus. Logi konsolowe
procesu zawierają diagnostykę techniczną, a ich rotację ustawia operator,
jeżeli zapisuje je poza procesem. Retencja wiadomości Discorda i danych po
stronie zewnętrznych dostawców zależy od ich konfiguracji.

Operator może uruchomić `uv run python manage.py verification_metrics`, aby otrzymać
liczbę analiz i statusów, udział `abstained`, medianę czasu, czasy etapów, błędy
retrievalu i identyfikatory użytych modeli. Czasy etapów obejmują pobranie,
wyszukiwanie w korpusie wraz z embeddingiem, model oceniający oraz opcjonalne
zapytania Google. Rewizja lokalnego modelu jest zapisywana, gdy jest dostępna
w metadanych. Koszt jest oznaczany jako `unknown`, ponieważ aplikacja nie ma danych
rozliczeniowych dostawców ani pomiaru kosztu lokalnej inferencji.

## Limity i zdarzenia bezpieczeństwa

WWW i `/sprawdz` przyjmują do **5 zgłoszeń do analizy w ruchomym oknie 10 minut**.
WWW stosuje limit dla sesji i adresu IP połączenia, a Discord dla użytkownika
na serwerze demonstracyjnym. Wspólny adres IP oznacza wspólny limit dodatkowy.
WWW odpowiada `429` z nagłówkiem `Retry-After`, a Discord wysyła prywatny komunikat.
Limit jest sprawdzany przed pobraniem treści i wywołaniem modelu. CLI pozostaje
kanałem lokalnym.

Licznik WWW używa pseudonimu HMAC adresu `REMOTE_ADDR`. Przy wielu procesach
WWW ustaw wspólne, stałe `DJANGO_SECRET_KEY`. Nagłówki przekazywane przez klienta
nie zmieniają adresu użytego do limitu. Za reverse proxy limit IP dotyczy
adresu proxy, o ile serwer aplikacji nie przekazuje wiarygodnego adresu klienta
w `REMOTE_ADDR`.

Rejestr `SecurityEvent` zawiera czas, kanał, kod i identyfikator analizy, gdy jest
dostępny. Obejmuje przekroczenia limitu, blokady URL oraz nieprawidłowe odpowiedzi
modelu. Przekroczenia tego samego limitu są rejestrowane najwyżej raz w oknie.
Zdarzenie modelowe oznacza problem z walidacją. Próby prompt injection ocenia się
osobno w testach. Rejestr pomija treść zgłoszenia, pełny URL i surowe IP.
`prune_verification_history` usuwa wygasłe liczniki i zdarzenia zgodnie z retencją.

Po migracji możesz odczytać najnowsze zdarzenia z aktywnej bazy:

```bash
PGHOST=/tmp/prawdowid-pg PGDATABASE=prawdowid uv run python manage.py shell -c \
  'from verify.models import SecurityEvent; print(list(SecurityEvent.objects.order_by("-created_at").values_list("created_at", "channel", "code", "request_id")[:10]))'
```
