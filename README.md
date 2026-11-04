# OpenKeyTranslate

A **Human-in-the-Loop (HITL)** manga / webtoon translation tool (English → Polish), built as an engineering thesis
project. The program does the tedious work (reading the text, cleaning the page, putting the translation back), the
translator decides, and the AI learns from every correction.

> 🇵🇱 A Polish-language README is available at [`README-pl.md`](./README-pl.md) (older; this file is the current one).

**Status: working prototype, version 0.3.0** (see [`CHANGELOG.md`](./CHANGELOG.md)). The whole first chapter of a test
series has been translated with it. It is still a thesis project: expect rough edges, and the README will be rewritten
when the thesis is finished.

---

## What it does

1. **OCR (local)** - PaddleOCR (PP-OCRv5, GPU or CPU) reads the English text; lines are grouped into bubbles in reading
   order. Long webtoon strips are cut along blank bands. A second, careful reading rescues lines the first pass
   rejected (stacked vertical letters, boxes cut too tight, tilted map labels).
2. **Cleaning (local)** - only the letters are erased (glyph mask), white bubbles are filled with their own colour,
   text on plain paper with the paper colour, everything else from its surroundings (OpenCV TELEA). Leftover traces
   can be cleaned by hand ("Clean area").
3. **Translation (cloud, your own key)** - the text of the whole page goes to an LLM in one request (neighbouring
   bubbles are context). One ordered list of keys - Groq, Gemini or any OpenAI-compatible server (OpenRouter,
   Mistral, DeepSeek, or a local Ollama / LM Studio; beta), mixed freely - with automatic rotation on rate limits.
   **Only text leaves the computer - never the page images.**
4. **Review in the editor** - page + bubble list + bubble panel. Edit, accept, retranslate one bubble, re-read a
   bubble, draw a missed one, merge (Shift+click) / split / move lines between bubbles, skip page numbers and noise.
5. **Lettering** - a second editor mode: move, resize and rotate the text box of each bubble, font per bubble (your
   own fonts or system fonts, bold / italic), alignment, outline. Tilted original text is lettered along its slant.
6. **Export** - one page from the editor, or whole chapters from the project page: translated / clean / both,
   PNG or JPG, loose files or ZIP.
7. **Correction memory + glossary** - every correction is stored and embedded (all-MiniLM-L6-v2). For a similar new
   line, the EN → PL pair goes into the prompt as an example; short repeated lines use an exact lane. Glossary terms
   (names, invented words) are given to the model whenever they appear, and a missing term triggers a short
   post-editing request. "Find terms with AI" proposes terms from your corrections.
8. **Measurement** - every memory-assisted translation has a twin request with the identical payload minus the
   memory (zero-shot), sent through the same key and model. The History page (Research view) shows both, what was injected and how it compares with your final version -
   the data for the thesis question: *does the correction loop measurably improve the translation?*

The memory design follows Moslem (2024), *Language Modelling Approaches to Adaptive Machine Translation*
(PhD thesis, DCU, [arXiv:2401.14559](https://arxiv.org/abs/2401.14559)): similar approved translations and glossary
terms in the prompt instead of fine-tuning, plus term post-editing. OKT applies it to Polish, comics with OCR text and
a live correction loop.

---

## Design pillars

- **Privacy and copyright first.** Everything that touches the page image runs locally. Only extracted text is sent
  to the translation API.
- **BYOK (Bring Your Own Key).** No bundled or paid translation service: paste your own (free tier is enough) Groq or
  Gemini key in Settings > Translation & API keys - or point it at a local model (Ollama / LM Studio).
- **Modest hardware.** Developed on a GTX 1050 Ti; runs on CPU too (slower OCR).
- **The human stays in charge.** AI output is a draft; nothing is "final" until you accept or correct it, and the
  project list shows what you have reviewed versus what was only processed by the system.

---

## Quick start (Windows, Python 3.12)

```bash
git clone https://github.com/Weed050/OpenKeyTranslate.git
cd OpenKeyTranslate/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r ../setup/requirements_cpu_only.txt     # or ../setup/req_gtx_1050ti_OKT.txt for an NVIDIA GPU
python main.py                                         # API on http://127.0.0.1:8000
```

Then open `frontend/index.html` through a local web server (e.g. VS Code Live Server on port 5500 - the API only
accepts pages served from `localhost` / `127.0.0.1`). See `setup/pip_install_instruction.txt` for the GPU / CPU
package details. The first run downloads the OCR and embedding models.

1. **Settings > Translation & API keys**: follow the Quick start box (free key from Groq or Google AI Studio, paste,
   Test).
2. **Projects**: create a project from a folder with one sub-folder per chapter.
3. Open the project, process pages, review them in the editor, switch to Lettering, export.

User data (database, projects, fonts, settings, logs) lives in `%APPDATA%\OpenKeyTranslate`, outside the repository.
Settings > Backup & moving packs it into one ZIP for another computer; Settings > Storage & uninstall shows every
folder with its size and what to delete when removing the app.

---

## Known limitations

- Heuristics are tuned on black-and-white manga with white bubbles; colour webtoons, gradients and text over detailed
  art need more work (a per-project style profile is an idea).
- Text on screentone or artwork is filled with TELEA - smooth, without re-creating the texture. A learned inpainting
  model (e.g. LaMa) as an optional engine is under consideration.
- The OCR model reads English only; Korean / Japanese raws would need another recognition model, source language and
  multilingual memory embeddings.
- The speaker's gender (Polish verb endings) is not inferred from the image.
- One series, one translator and one model so far: the evaluation sample is small.
- Starting it still needs a terminal (backend) and a browser; a one-click desktop app is planned.

---

## Documentation & repository

- [`CHANGELOG.md`](./CHANGELOG.md) - what changed in each version.
- [`docs/ARCHITECTURE_API.md`](./docs/ARCHITECTURE_API.md) - backend architecture and API.
- **[API & code documentation (GitHub Pages)](https://weed050.github.io/OpenKeyTranslate/)** - generated with pdoc
  through `backend/scripts/build_docs.py` (never plain `pdoc`: the script scans for secrets first).
- `.githooks/pre-commit` blocks secrets and forbidden files - enable it with `git config core.hooksPath .githooks`.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLite (SQLAlchemy)
- **Vision:** PaddleOCR 3 (PP-OCRv5), OpenCV, Pillow
- **Memory:** sentence-transformers (all-MiniLM-L6-v2)
- **Translation:** Groq / Gemini / OpenAI-compatible APIs (BYOK, httpx)
- **Frontend:** HTML, CSS, vanilla JavaScript modules (no build step)

## License

Apache License 2.0 - see [`LICENSE`](./LICENSE).
