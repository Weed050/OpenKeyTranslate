
# backend/services/providers/groq_provider.py

"""
Groq API provider implementation.

Wraps the Groq chat-completions API behind the shared TranslationProvider
interface (see base.py). Uses "json_object" response mode - this guarantees
syntactically valid JSON, but not the exact key names/shape, hence the
defensive .get() calls below (contrast with GeminiProvider, which enforces
a real schema instead of just asking nicely in the prompt).
"""

import json
from groq import Groq

from .base import TranslationProvider
from .prompts import SYSTEM_PROMPT


class GroqProvider(TranslationProvider):
    def __init__(self, api_key: str, model: str, temperature: float, max_tokens: int):
        if not api_key:
            raise ValueError(
                "Groq api_key is empty — fill it in settings.json under providers.groq.api_key"
            )
        self._client = Groq(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def translate(self, texts_payload: list[dict]) -> dict[str, str]:
        user_message = json.dumps(texts_payload, ensure_ascii=False)
        raw = None
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

            raw = response.choices[0].message.content.strip()
            parsed_data = json.loads(raw)
            translations_list = parsed_data.get("translations", [])

            return {t.get("id"): t.get("translation", "") for t in translations_list if "id" in t}

        except Exception as e:
            print(f"\n[GROQ TRANSLATION ERROR]: {e}")
            print(f"[DEBUG] Raw model response: {raw if raw is not None else 'None'}")
            return {}
