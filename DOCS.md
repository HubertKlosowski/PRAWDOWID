# PRAWDOWID — weryfikacja informacji ze wskazaniem źródeł

**Nazwa zespołu (propozycja):** PRAWDOWID

## Opis rozwiązania

PRAWDOWID pomaga sprawdzić wiadomość przed przekazaniem jej dalej. Użytkownik wkleja tekst lub link do artykułu w formularzu **WWW** albo wysyła go na **Discordzie** poleceniem `/sprawdz`. W obu kanałach otrzymuje odpowiedź po polsku. Przy każdym rozstrzygnięciu może przeczytać uzasadnienie, zobaczyć wykorzystany cytat i przejść do dokumentu, żeby samodzielnie sprawdzić jego kontekst.

### Jak przebiega weryfikacja

1. Aplikacja wyodrębnia z wiadomości do czterech twierdzeń i sprawdza każde osobno.
2. Materiał przesłany przez użytkownika służy do ustalenia, co wymaga weryfikacji. Dowody pochodzą z odrębnego korpusu. Model Qwen3-Embedding-0.6B umożliwia wyszukanie w PostgreSQL z rozszerzeniem pgvector fragmentów o znaczeniu zbliżonym do twierdzenia. Przy wyborze źródeł aplikacja uwzględnia okres, którego dotyczy pytanie.
3. Model oceniający, domyślnie Qwen3.5-4B, wskazuje fragmenty przemawiające za rozstrzygnięciem. W drugim kroku ponownie ocenia ich związek z całą tezą, bez dostępu do pierwszego werdyktu. Aplikacja sprawdza zgodność obu ocen i pobiera treść cytatów ze wskazanych dokumentów.
4. Gdy dowodów brakuje lub są sprzeczne, wstrzymuje się od rozstrzygnięcia. Awarię techniczną sygnalizuje osobno.

### Konfiguracja i uruchamianie modeli

Wyszukiwanie dowodów i ich ocena korzystają z osobnych modeli.

Zespół może podmienić zgodny model oceniający przez konfigurację, bez zmian w formularzu WWW, bocie Discord i indeksie źródeł. Zmiana modelu embeddingowego wymaga zgodnego wymiaru wektorów i ponownego przeliczenia indeksu.

Obciążenie można rozłożyć między urządzenia, na przykład wyszukiwać fragmenty przy użyciu CPU, a ich ocenę wykonywać na GPU. Model oceniający może również działać na osobnym serwerze dostępnym przez interfejs zgodny z `chat/completions`.

### Eksport danych

- W WWW i na Discordzie użytkownik może wyeksportować ostatnią odpowiedź lub historię swojej sesji do pliku **JSON**. Pozwala to wrócić do wyniku i jego źródeł bez ponownego uruchamiania modeli.
- Osobny eksport metadanych korpusu do **CSV** ułatwia zespołowi audyt dokumentów wykorzystywanych do weryfikacji.
