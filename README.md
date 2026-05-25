# Quiz – Sternik motorowodny

Interaktywny quiz (368 pytań) z pliku PDF Wind Sailing School.

## Uruchomienie lokalnie

Otwórz w przeglądarce plik `quiz-sternik-motorowodny.html` (folder `quiz-images/` musi być obok).

## GitHub Pages (link na telefon)

Po wrzuceniu repozytorium na GitHub:

1. Repozytorium → **Settings** → **Pages**
2. **Source:** Deploy from a branch → **main** → folder **/ (root)** → **Save**
3. Po ~1–2 min quiz jest pod adresem:  
   `https://TWOJA-NAZWA.github.io/NAZWA-REPO/quiz-sternik-motorowodny.html`

## Przebudowa quizu z PDF

```bash
pip install pymupdf pillow
python3 build_quiz.py
```

## Pliki

| Plik | Opis |
|------|------|
| `quiz-sternik-motorowodny.html` | Quiz do otwarcia w przeglądarce |
| `quiz-images/` | Obrazy znaków wycięte z PDF |
| `quiz-data.json` | Dane pytań (JSON) |
| `build_quiz.py` | Generator HTML z PDF |
| `sternik-motorowodny-pytania.pdf` | Źródłowy PDF |
