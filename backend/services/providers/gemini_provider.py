
# backend/services/providers/gemini_provider.py

"""
Gemini API provider implementation.

Uses response_schema (a Pydantic model) instead of a hand-described JSON
shape in the prompt. Unlike GroqProvider's "json_object" mode - which only
guarantees *some* valid JSON comes back - response_schema constrains
generation to that exact structure, so response.parsed can be trusted
directly instead of defensively parsing a raw string.
"""

from pydantic import BaseModel
from google import genai
from google.genai import types

from .base import TranslationProvider
from .prompts import SYSTEM_PROMPT


class _BubbleTranslation(BaseModel):
    id: str
    translation: str


class _TranslationBatch(BaseModel):
    translations: list[_BubbleTranslation]


class GeminiProvider(TranslationProvider):
    def __init__(self, api_key: str, model: str, temperature: float, max_tokens: int):
        if not api_key:
            raise ValueError(
                "Gemini api_key is empty — fill it in settings.json under providers.gemini.api_key"
            )
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def translate(self, texts_payload: list[dict]) -> dict[str, str]:
        import json
        user_message = json.dumps(texts_payload, ensure_ascii=False)

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_TranslationBatch,
                    temperature=self._temperature,
                    max_output_tokens=self._max_tokens,
                ),
            )

            batch: _TranslationBatch | None = response.parsed
            if batch is None:
                print(f"\n[GEMINI TRANSLATION ERROR]: response.parsed is None. Raw text: {response.text!r}")
                return {}

            return {item.id: item.translation for item in batch.translations}

        except Exception as e:
            print(f"\n[GEMINI TRANSLATION ERROR]: {e}")
            return {}
