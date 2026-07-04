# OpenKeyTranslate

A **Human-in-the-Loop (HITL)** manga translation pipeline, built as an engineering thesis project.

> 🇵🇱 A Polish-language README is available at [`README-pl.md`](./README-pl.md).

**Project status: proof of concept.** The backend pipeline is being validated through a set of manual tests covering individual stages (OCR, inpainting, translation, correction memory), which also help identify which parts need the most work next. The frontend is currently a skeleton — a working shell, not a finished editor.

Most existing automated manga translation tools are closed-source, black-box services (e.g. Mantra Engine) that give the reader/translator no way to correct or steer the output. OpenKeyTranslate takes a different approach: it automates the tedious, repetitive parts of the pipeline (text extraction, image cleaning) while keeping a human in the loop for the part AI still gets wrong — translation quality and nuance.

Currently the pipeline only supports **English → Polish**, since that's the only language pair the author is fluent enough in to evaluate quality.

---

## Core idea

AI translation is not reliable enough to run unsupervised, especially for a language like Polish where grammar changes depending on the speaker's gender — something English doesn't encode at all. Instead of pretending the AI is good enough, OpenKeyTranslate treats it as a first draft:

1. Text is extracted and the AI produces a translation.
2. The user reviews and, if needed, corrects it in an image-editor-style interface.
3. The correction is stored and reused as context for similar text in the future, so the system gradually "learns" the user's preferred phrasing without any fine-tuning.

The thesis question this project is actually trying to answer is: **does a feedback loop like this measurably improve translation quality**, compared to plain zero-shot AI translation? That comparison — not a polished product — is the main academic goal.

---

## Design pillars

- **Privacy & copyright first.** Manga pages are copyrighted images, not just text. Every step that touches the actual image — OCR, inpainting, visual context analysis — runs **100% locally**. Only extracted plain text is ever sent to an external API.
- **BYOK (Bring Your Own Key).** The project doesn't bundle or pay for a translation service. You plug in your own LLM API key, which is where the project's name comes from. Currently wired up to Groq's API (LLaMA 3.3 70B Versatile); quality for Polish is mediocre since that model is trained primarily on English, so switching to Gemini is on the todo list.
- **Runs on low-end hardware.** The goal is to eventually ship in both GPU and CPU-only variants, so translators without a dedicated GPU can still use it for free.
- **Modular.** Components (OCR engine, vision model, translation backend) are meant to be swappable, so someone with access to a better model or paid service can plug it in instead of the defaults. This is currently just a design goal, not implemented yet.

---

## Pipeline (current state)

1. **Text detection & OCR (local):** PaddleOCR extracts English text from speech bubbles. It's currently run on the whole page rather than per-bubble, which is more resource-heavy than it needs to be (see Known Limitations).
2. **Inpainting (local):** the detected text is erased from the image to produce a clean background, without touching the surrounding artwork.
3. **AI translation (cloud, BYOK):** the extracted text is sent via API to an LLM for translation into Polish.
4. **Manual correction (frontend):** the user reviews the AI's translation in a simple, Photoshop/GIMP-like editor overlaid on the cleaned image, and can edit or confirm it.
5. **Correction memory:** confirmed user edits are stored, embedded, and used for similarity search — if a future text string is close enough to a previously corrected one, the old correction is injected into the prompt as a hint (e.g. *"the user previously translated X as Y — a similar line follows"*). This avoids naive 1:1 string matching.

At the moment, only the backend/pipeline side of this is implemented in any depth. The frontend is a skeleton.

---

## Where this differs from prior research (and why that matters)

This project's approach to visual context is inspired by the paper *"Context-Informed Machine Translation of Manga using Multimodal Large Language Models"*, which found that:
- feeding the model the inpainted (text-erased) image as context — instead of letting the LLM attempt OCR itself — improved results, since a dedicated OCR tool already handles extraction;
- a context window of roughly one page (not more, not less) worked best.

However, the paper's setup sent the actual image plus indexed, numbered text per bubble directly to a multimodal LLM. This project can't afford that, both in API cost and in the privacy/copyright constraints described above (no raw image ever leaves the machine). Instead, the plan is to send the LLM only:
- the text to translate, and
- a **textual description** of the visual scene (generated locally, likely via Moondream), as a stand-in for actual visual context — e.g. for inferring a speaker's gender.

This is expected to perform noticeably worse than the original paper's image-based approach. It's an intentional tradeoff for privacy and cost, not an oversight — worth keeping in mind when judging output quality.

---

## Known limitations / open problems

- **OCR is resource-heavy** as currently implemented (whole-page OCR rather than per-bubble crops). Switching to bubble-detection-then-crop-then-OCR should help a lot, but is a significant rework, not a small patch.
- **Vertical text is not supported yet** — the current OCR setup isn't built for it.
- **Visual/gender context is not implemented yet.** Whether Moondream can reliably infer speaker gender from a manga panel is still an open question, not a validated result.
- **Correction-memory retrieval** (deciding what counts as a "similar enough" text to reuse a past correction) is unsolved in practice — this is planned but the matching strategy isn't finalized.
- **Translation quality is currently limited by the model.** LLaMA 3.3 70B Versatile is not well suited to Polish; this is a known gap, not a bug.

---

## Roadmap

- [ ] Visual context via Moondream (local), including experiments on speaker-gender detection
- [ ] Bubble detection → crop → OCR, to reduce OCR resource usage
- [ ] Custom font support, a bundled library of common comic fonts, and eventually automatic font matching
- [ ] Correction-memory similarity search for the prompt-injection feedback loop
- [ ] Vertical text support
- [ ] Packaging the whole stack (FastAPI backend + SQLite + web frontend) into a single desktop app (Discord-style), so the end user never sees a terminal or a local server
- [ ] Gemini as an alternative/better translation backend
- [ ] Swappable modules for OCR/vision/translation (BYO-model, not just BYO-key)

---

## Documentation & repo structure

- [`docs/ARCHITECTURE_API.md`](./docs/ARCHITECTURE_API.md) — backend architecture and API documentation (Markdown).
- Additional frontend/HTML documentation lives alongside the frontend code.
- `docs/**` and `*.md` are marked `linguist-documentation` in `.gitattributes`, so GitHub's language stats reflect the actual backend/frontend code rather than being skewed by documentation files.
- Most of `docs/` is intended to be published as project documentation (e.g. via GitHub Pages); `ARCHITECTURE_API.md` is kept internal for now and excluded from that.

---

## Tech stack

- **Backend:** Python, FastAPI, SQLite
- **Computer vision:** OpenCV, PaddleOCR (local OCR)
- **Vision context (planned):** Moondream
- **Translation:** LLM API, BYOK (currently Groq / LLaMA 3.3 70B Versatile)
- **Frontend:** HTML5, CSS3, vanilla JS (currently a skeleton/editor shell)

---

## Thesis objective

The engineering-thesis goal is to measure whether a human-in-the-loop correction/feedback system actually improves AI translation quality over time, compared to plain zero-shot translation — with tests designed around that comparison, not around shipping a finished product.
