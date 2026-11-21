# PROJECT_CONTEXT.md — specyfikacja MVP PRAWDOWID

## 1. Dlaczego istnieje projekt

**Kontekst zadania konkursowego:** w świecie nadmiaru informacji odbiorcom coraz trudniej ocenić, którym treściom zaufać. Zadaniem jest wykorzystać AI i nowoczesne technologie do zbudowania **prototypu narzędzia** wspierającego weryfikację informacji, wyszukiwanie wiarygodnych źródeł i świadomą ocenę napotkanych treści.

**Cel użytkowy:** ktoś wkleja twierdzenie/wiadomość albo URL artykułu; aplikacja pokazuje, czy dostępne źródła wspierają lub podważają informację, skąd to wynika i czego nie udało się sprawdzić. System **pomaga ocenić wiarygodność**, a nie ogłasza nieomylnego werdyktu.

## 2. Zakres MVP — ustalony w rozmowach

1. **RAG**: pobranie treści wybranych stron rządowych, ekstrakcja tekstu, podział na fragmenty, embeddingi modelem **Qwen3-Embedding-0.6B**, indeks w **PostgreSQL + pgvector**, proste wyszukiwanie podobieństwa.
2. **Aplikacja webowa**: jedna aplikacja **Django full-stack**, obsługa wejścia **tekstowego i URL**, wyświetlanie rezultatów i odnośników do źródeł.
3. **Ocena modelowa**: wysłanie materiału od użytkownika oraz kontekstu wyszukanego przez RAG do **modelu bazowego/LLM**; otrzymanie klasy, szacunkowych wyników/prawdopodobieństw klas, argumentacji i dowodów dla wskazanej oceny.
4. **Zabezpieczenia**: izolacja instrukcji od niezaufanych danych, blokowanie podstawowych ataków prompt injection, bezpieczna obsługa adresów URL.

**Świadomie poza MVP:** Docker, Redis, Celery, kolejki, Kubernetes, FastAPI, Vue/SPA, agenci wykonujący autonomiczne działania, zaawansowane wyszukiwanie hybrydowe, automatyczne okresowe crawle, wielousługowa infrastruktura i rozbudowane uprawnienia. Nie dodawać ich „na przyszłość”.

## 3. Proponowany minimalny przepływ danych

### A. Budowa korpusu (indeksowanie)

```text
Konfigurowana lista oficjalnych URL-i
    -> pobranie HTML / dostępnego tekstu
    -> ekstrakcja czytelnej treści + metadanych
    -> czyszczenie i podział na chunki
    -> Qwen3-Embedding-0.6B: embedding każdego chunku
    -> PostgreSQL: dokumenty, chunki, wektory (pgvector)
```

- Uruchamianie **ręcznie** przez prostą komendę Django (`manage.py`), bez kolejkowania.
- Aktualizacja dokumentów idempotentna: identyfikacja po kanonicznym URL i/lub hash treści; brak niekontrolowanych duplikatów.
- Jeden model embeddingowy do dokumentów i zapytań; wymiar wektora zgodny z faktycznym modelem i konfiguracją (nie zakładaj go bez sprawdzenia).
- Wyszukiwanie: embedding query, podobieństwo wektorowe, `top_k` konfigurowalne, zwrot treści i metadanych.
- Jeżeli strona jest zasilana JavaScriptem albo używa fragmentów `#`, sprawdź, czy da się uzyskać dane z dostępnego źródła; **nie zakładaj**, że sam GET do URL zwróci informacje z widoku dynamicznego.

### B. Weryfikacja przez użytkownika

```text
Formularz: tekst lub URL
    -> walidacja i pobranie tekstu (dla URL)
    -> określenie treści / twierdzenia do oceny
    -> embedding zapytania
    -> pgvector: top-k fragmentów z korpusu zaufanych źródeł
    -> model bazowy: twierdzenie + odzyskane dowody (jako dane)
    -> walidacja struktury wyniku i referencji do dowodów
    -> strona z klasą, rozkładem/oceną klas, wyjaśnieniem i źródłami
```

**Ważne rozdzielenie:** link podany przez użytkownika oznacza treść **do weryfikacji**, nie „oficjalne źródło potwierdzające”. Treści takiego URL-a nie należy samoczynnie dopisywać do korpusu zaufanego RAG.

## 4. Źródła danych

Organizator przekazał listę URL-i ze źródłami weryfikującymi. W dotychczasowym kontekście wystąpiły m.in.:

- https://odyseusz.gov.pl/
- https://www.gov.pl/web/gov/ostrzezenia
- https://www.gov.pl/web/gov/ostrzezenia#/_/mazowieckie/wszystkie
- https://www.gov.pl/web/mswia/regionalny-system-ostrzegania
- https://www.gov.pl/web/rcb/raport-dobowy3

To **część** listy, a nie potwierdzony pełen katalog. Docelowa konfiguracja źródeł powinna pozwalać dopisywać kolejne URL-e bez zmian w kodzie. Nie traktuj wszystkich podstron tych serwisów jako automatycznie zgodnych z tematyką zadanego twierdzenia.

Każdy dokument/chunk powinien mieć co najmniej: URL źródłowy, tytuł, treść, czas pobrania, pozycję fragmentu i ewentualnie datę publikacji. Stare komunikaty i ostrzeżenia mogą być nieaktualne — pokazuj daty i nie ukrywaj ograniczeń czasowych.

## 5. Wynik analizy i logika oceny

**Proponowane etykiety MVP** (robocze, do zmiany po ustaleniu schematu z organizatorem):

- `POTWIERDZONE` — znalezione dowody wspierają analizowane twierdzenie.
- `ZAPRZECZONE` — znalezione dowody podważają analizowane twierdzenie.
- `NIEZWERYFIKOWANE` — dane są niewystarczające, niejednoznaczne lub nie dotyczą sprawdzanego twierdzenia.

Minimalny wynik logiczny powinien zawierać:

```json
{
  "label": "NIEZWERYFIKOWANE",
  "class_scores": {
    "POTWIERDZONE": 0.15,
    "ZAPRZECZONE": 0.10,
    "NIEZWERYFIKOWANE": 0.75
  },
  "score_type": "model_estimate_not_calibrated",
  "explanation": "Nie znaleziono wystarczająco bezpośrednich dowodów.",
  "evidence": [
    {
      "chunk_id": "existing-retrieved-chunk-id",
      "relation": "context",
      "quote": "Krótki fragment rzeczywiście pochodzący ze źródła"
    }
  ]
}
```

Powyższe wartości są **wyłącznie przykładem formatu**, a nie wynikiem prawdziwego uruchomienia modelu.

- `chunk_id` musi pochodzić z faktycznie pobranych fragmentów. Link, tytuł, data i cytat są uzupełniane i weryfikowane **po stronie aplikacji**, nie wymyślane przez model.
- `class_scores` to szacowane wyniki modelu; liczby sumujące się do 1 nie oznaczają jeszcze poprawnie skalibrowanych prawdopodobieństw. Jeśli brak podstaw do kalibracji, oznacz je odpowiednio w interfejsie.
- Model powinien odróżniać dowody **za**, **przeciw** i tło informacyjne. Można pokazywać sprzeczne źródła, nie ukrywając niepewności.
- Brak trafnych źródeł lub awaria modelu musi mieć jawny, bezpieczny wynik; nie generuj dowodów zastępczych.

## 6. Minimalna architektura implementacyjna

```text
Django (templates + views + forms)
  |-- wejście tekst / URL
  |-- services: fetch/extract, embeddings, retrieval, verification
  |-- management command: ingest oficjalnych URL-i
  |-- models: Document, Chunk (+ ewentualnie Source)
  `-- strona wyniku z odniesieniami do źródeł

PostgreSQL + pgvector
  `-- dokumenty, chunki i embeddingi

Modele ML
  |-- Qwen3-Embedding-0.6B: wektory
  `-- model bazowy: analiza i klasyfikacja (konkretny model nieustalony)
```

**To układ odpowiedzialności, nie sztywny spis katalogów.** Nie twórz wielu aplikacji Django ani dużej warstwy abstrakcji tylko po to, by odwzorować ten diagram. Na początku wystarczą proste widoki, moduły usług i 1–3 modele bazy danych.

Dobrym pierwszym milestone'em jest uruchomienie całego przepływu na niewielkim korpusie i jednej stronie wyniku, następnie rozszerzanie danych i testów. Jeśli generowanie embeddingów jest kosztowne, rozdziel jednorazowy ingest od obsługi żądania WWW, nadal bez dodatkowej kolejki.

## 7. Ograniczenia bezpieczeństwa i jakości

- **Prompt injection:** każdy dokument i HTML to niezaufany materiał. Model nie może wykonywać instrukcji tekstu analizowanego ani RAG. Użyj wyraźnej separacji ról, cytowania kontekstu jako danych, ograniczonego schematu wyjścia, walidacji dowodów i testów adversarialnych.
- **SSRF:** waliduj schemat, domenę/adres IP, przekierowania, timeouty, rozmiar i typ odpowiedzi; blokuj połączenia z sieciami prywatnymi/lokalnymi, adresami specjalnymi i metadanymi chmury. W miarę potrzeb ponownie weryfikuj adres po rozwiązaniu DNS.
- **Rzetelność:** oficjalne źródło może być nieaktualne, dotyczyć innego obszaru lub nie odpowiadać na twierdzenie. Wysoki wynik wektorowy nie oznacza potwierdzenia.
- **Dostęp do stron:** pobieraj tylko dopuszczone dane, umiarkowanie, z obsługą błędów; nie omijaj kontroli dostępu ani zabezpieczeń stron.
- **Testowalność:** testy jednostkowe usług bez GPU, fixture'y HTML oraz testy przypadków „brak danych”, błędny URL, przekierowanie SSRF, prompt injection i fałszywe ID dowodów.

## 8. Wciąż otwarte decyzje

Nie przypisuj im arbitralnie statusu wymagań ustalonych przez użytkownika:

- Konkretny **model bazowy/LLM** do oceny treści, jego API i sposób liczenia `class_scores`.
- Czy inferencja modeli odbywa się w procesie lokalnym, na **gpu-server**, czy przez prosty zewnętrzny endpoint.
- Pełna lista źródeł od organizatora oraz możliwe API/formaty dla dynamicznych serwisów.
- Ostateczne etykiety klas, format wyników na konkurs i sposób ewaluacji jakości.
- Retencja historii analiz i ewentualny mechanizm odświeżania korpusu (nie są potrzebne do pierwszego MVP).

**Domyślna zasada:** gdy szczegół nie jest ustalony, wybierz najmniejszą odwracalną decyzję, oznacz ją jako założenie i nie rozbudowuj projektu przed potwierdzeniem potrzeby.
