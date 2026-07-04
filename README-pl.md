# OpenKeyTranslate

Pipeline do tłumaczenia mangi w modelu **Human-in-the-Loop (HITL)**, tworzony w ramach pracy inżynierskiej.

> 🇬🇧 Wersja angielska README dostępna jest w pliku [`README.md`](./README.md).

**Status projektu: proof of concept.** Backend jest weryfikowany za pomocą zestawu manualnych testów obejmujących poszczególne etapy pipeline'u (OCR, inpainting, tłumaczenie, pamięć poprawek), które jednocześnie pomagają wskazać, które elementy wymagają najwięcej pracy. Frontend jest obecnie szkieletem — działającą powłoką, a nie gotowym edytorem.

Większość dostępnych narzędzi do automatycznego tłumaczenia mangi to zamknięte, black-boxowe usługi (np. Mantra Engine), które nie dają czytelnikowi/tłumaczowi żadnej możliwości poprawienia lub ukierunkowania wyniku. OpenKeyTranslate podchodzi do tego inaczej: automatyzuje żmudne, powtarzalne części pipeline'u (wyciąganie tekstu, czyszczenie obrazu), jednocześnie zostawiając człowieka w pętli tam, gdzie AI wciąż zawodzi — czyli przy jakości i niuansach tłumaczenia.

Obecnie pipeline obsługuje wyłącznie tłumaczenie **angielski → polski**, ponieważ to jedyna para językowa, którą autor zna wystarczająco dobrze, by ocenić jakość wyniku.

---

## Główna idea

Tłumaczenie AI nie jest na tyle niezawodne, żeby działać bez nadzoru — zwłaszcza dla języka takiego jak polski, gdzie forma gramatyczna zależy od płci mówiącej osoby, czego angielski w ogóle nie koduje. Zamiast udawać, że AI jest wystarczająco dobre, OpenKeyTranslate traktuje jego wynik jako wersję roboczą:

1. Tekst zostaje wyciągnięty, a AI generuje tłumaczenie.
2. Użytkownik przegląda tłumaczenie i w razie potrzeby poprawia je w interfejsie przypominającym edytor graficzny.
3. Poprawka jest zapisywana i wykorzystywana jako kontekst dla podobnego tekstu w przyszłości, dzięki czemu system stopniowo „uczy się” preferowanych sformułowań użytkownika bez żadnego fine-tuningu.

Pytanie badawcze, na które faktycznie odpowiada ta praca inżynierska, brzmi: **czy taka pętla sprzężenia zwrotnego mierzalnie poprawia jakość tłumaczenia** w porównaniu do zwykłego tłumaczenia AI bez korekt (zero-shot)? To porównanie — a nie gotowy, dopieszczony produkt — jest głównym celem akademickim.

---

## Filary projektu

- **Prywatność i prawa autorskie na pierwszym miejscu.** Strony mangi to obrazy chronione prawem autorskim, nie tylko tekst. Każdy etap, który dotyka samego obrazu — OCR, inpainting, analiza kontekstu wizualnego — działa **w 100% lokalnie**. Na zewnątrz, przez API, wysyłany jest wyłącznie wyciągnięty tekst.
- **BYOK (Bring Your Own Key).** Projekt nie dostarcza ani nie finansuje usługi tłumaczeniowej. Użytkownik podpina własny klucz API do LLM-a, stąd zresztą nazwa projektu. Obecnie podpięte pod API Groq (LLaMA 3.3 70B Versatile); jakość dla polskiego jest przeciętna, bo model jest trenowany głównie pod angielski — zmiana na Gemini jest w planach.
- **Działa na słabszym sprzęcie.** Docelowo projekt ma być dostępny w wersji z GPU i w wersji tylko na CPU, żeby tłumacze bez dedykowanej karty graficznej też mogli korzystać z niego za darmo.
- **Modułowość.** Komponenty (silnik OCR, model wizyjny, backend tłumaczeniowy) mają być wymienne, żeby ktoś z dostępem do lepszego modelu lub płatnej usługi mógł podpiąć własny zamiast domyślnego. Na razie jest to tylko założenie projektowe, niezaimplementowane.

---

## Pipeline 

1. **Detekcja tekstu i OCR (lokalnie):** PaddleOCR wyciąga angielski tekst z dymków. Obecnie uruchamiany na całej stronie, a nie per dymek, co jest bardziej zasobożerne niż to konieczne (patrz: Znane ograniczenia).
2. **Inpainting (lokalnie):** wykryty tekst jest usuwany z obrazu, tworząc czyste tło bez ingerencji w resztę grafiki.
3. **Tłumaczenie AI (chmura, BYOK):** wyciągnięty tekst jest wysyłany przez API do LLM-a w celu przetłumaczenia na polski.
4. **Ręczna korekta (frontend):** użytkownik przegląda tłumaczenie AI w prostym edytorze w stylu Photoshop/GIMP, nałożonym na wyczyszczony obraz, i może je poprawić lub zatwierdzić.
5. **Pamięć poprawek:** zatwierdzone poprawki użytkownika są zapisywane, embedowane i wykorzystywane do wyszukiwania podobieństw — jeśli kolejny fragment tekstu jest wystarczająco podobny do wcześniej poprawionego, stara poprawka jest wstrzykiwana do prompta jako wskazówka (np. *„użytkownik wcześniej przetłumaczył X jako Y — poniżej podobna linia”*). Dzięki temu unika się naiwnego porównywania 1:1.

Obecnie tylko strona backendowa/pipeline'u jest rozwinięta w jakimś stopniu. Frontend jest szkieletem.

---

## Czym różni się to od wcześniejszych badań (i dlaczego to ważne)

Podejście do kontekstu wizualnego w tym projekcie jest inspirowane pracą *„Context-Informed Machine Translation of Manga using Multimodal Large Language Models”*, która pokazała, że:
- podawanie modelowi wyczyszczonego (pozbawionego tekstu) obrazu jako kontekstu — zamiast pozwalania LLM-owi samodzielnie robić OCR — poprawia wyniki, ponieważ dedykowane narzędzie OCR i tak już zajmuje się ekstrakcją;
- okno kontekstu wielkości mniej więcej jednej strony (nie mniej, nie więcej) dawało najlepsze rezultaty.

Jednak w tamtej pracy do multimodalnego LLM-a wysyłano bezpośrednio sam obraz oraz ponumerowany, indeksowany tekst per dymek. Ten projekt nie może sobie na to pozwolić — zarówno ze względu na koszt API, jak i opisane wyżej ograniczenia dotyczące prywatności i praw autorskich (żaden surowy obraz nie opuszcza urządzenia użytkownika). Zamiast tego planowane jest wysyłanie do LLM-a wyłącznie:
- tekstu do przetłumaczenia oraz
- **tekstowego opisu sceny** (generowanego lokalnie, prawdopodobnie za pomocą Moondream) jako namiastki rzeczywistego kontekstu wizualnego — np. do wnioskowania o płci mówiącej osoby.

Można się spodziewać, że da to zauważalnie gorsze wyniki niż podejście oparte na obrazie z oryginalnej pracy. To świadomy kompromis na rzecz prywatności i kosztów, a nie przeoczenie — warto o tym pamiętać przy ocenie jakości wyników.

---

## Znane ograniczenia / otwarte problemy

- **OCR jest obecnie zasobożerny** (całostronicowy OCR zamiast wycinków per dymek). Przejście na detekcję dymków → wycinanie → OCR powinno znacznie pomóc, ale to spora przebudowa, nie drobna łatka.
- **Tekst pionowy nie jest jeszcze obsługiwany** — obecna konfiguracja OCR nie jest do tego przystosowana.
- **Kontekst wizualny/płciowy nie jest jeszcze zaimplementowany.** Czy Moondream będzie w stanie wiarygodnie wnioskować o płci postaci na podstawie kadru mangi, to wciąż otwarte pytanie, a nie potwierdzony rezultat.
- **Wyszukiwanie w pamięci poprawek** (ustalenie, co liczy się jako „wystarczająco podobny” tekst, by ponownie wykorzystać wcześniejszą poprawkę) nie jest w praktyce rozwiązane — jest planowane, ale strategia dopasowywania nie jest jeszcze ustalona.
- **Jakość tłumaczenia jest obecnie ograniczona przez model.** LLaMA 3.3 70B Versatile słabo radzi sobie z polskim; to znana luka, nie błąd.

---

## Plany rozwoju

- [ ] Kontekst wizualny przez Moondream (lokalnie), w tym eksperymenty z wykrywaniem płci mówiącego
- [ ] Detekcja dymków → wycinanie → OCR, żeby zmniejszyć zużycie zasobów przez OCR
- [ ] Obsługa własnych czcionek, wbudowana baza popularnych czcionek komiksowych, docelowo automatyczne dopasowywanie czcionek
- [ ] Wyszukiwanie podobieństw w pamięci poprawek dla pętli prompt injection
- [ ] Obsługa tekstu pionowego
- [ ] Zapakowanie całego stacku (backend FastAPI + SQLite + frontend webowy) w jedną aplikację desktopową (na wzór architektury Discorda), żeby użytkownik końcowy nie musiał widzieć terminala ani lokalnego serwera
- [ ] Gemini jako alternatywny/lepszy backend tłumaczeniowy
- [ ] Wymienne moduły OCR/wizji/tłumaczenia (własny model, nie tylko własny klucz)

---

## Dokumentacja i struktura repozytorium

- [`docs/ARCHITECTURE_API.md`](./docs/ARCHITECTURE_API.md) — dokumentacja architektury backendu i API (Markdown).
- Dodatkowa dokumentacja frontendu (HTML) - `docs/**` oraz `*.md` są oznaczone jako `linguist-documentation` w `.gitattributes`, dzięki czemu statystyki języków na GitHubie odzwierciedlają rzeczywisty kod backendu/frontendu, a nie są zaburzone przez pliki dokumentacji.
- Większość zawartości `docs/` docelowo ma zostać opublikowana jako dokumentacja projektu (np. przez GitHub Pages); `ARCHITECTURE_API.md` na razie pozostaje wewnętrzny i jest z tego wyłączony.

---

## Stos technologiczny

- **Backend:** Python, FastAPI, SQLite
- **Computer vision:** OpenCV, PaddleOCR (lokalny OCR)
- **Kontekst wizyjny (planowany):** Moondream
- **Tłumaczenie:** API LLM, BYOK (obecnie Groq / LLaMA 3.3 70B Versatile)
- **Frontend:** HTML5, CSS3, vanilla JS (obecnie szkielet edytora)

---

## Cel pracy inżynierskiej

Celem pracy inżynierskiej jest zmierzenie, czy system korekt/sprzężenia zwrotnego typu human-in-the-loop rzeczywiście poprawia jakość tłumaczenia AI w czasie, w porównaniu do zwykłego tłumaczenia zero-shot — z testami zaprojektowanymi wokół tego porównania, a nie wokół dostarczenia gotowego produktu.
