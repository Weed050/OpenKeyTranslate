
# backend/services/translation_service.py

"""
LLM Translation Service Module
______________________________

This module acts as the localization (translation) engine for the application, bridging the
gap between the extracted OCR text and the Groq Large Language Model (LLM) API.
It is responsible for context-aware, English-to-Polish translation of manga
and comic speech bubbles.

Key Architectural Features:
    1. Singleton Connection: Manages a single, persistent Groq client instance
       to minimize overhead and redundant API initializations.
    2. Batch Processing: Aggregates all text bubbles from a page into a single
       API payload. This drastically reduces network latency and token usage
       compared to translating bubbles one by one.
    3. Structured Output Enforcement: Leverages the LLM's 'json_object' mode
       to guarantee machine-readable responses. It forces the model to return
       a strict JSON schema rather than conversational text.
    4. Defensive Mapping & Error Handling: Uses unique bubble IDs to map
       translations back to their source objects safely. If the API fails,
       times out, or hallucinates, the module gracefully falls back to empty
       strings, preventing downstream application crashes.
"""

import json
from groq import Groq
from core.config import GROQ_API_KEY, GROQ_MODEL, TRANSLATION_TEMPERATURE, TRANSLATION_MAX_TOKENS

_client = None


def _get_client() -> Groq:
    """
    Initialize and return a singleton instance of the Groq API client.

    Ensures that the API client is instantiated only once during the application's
    lifecycle to conserve resources and avoid redundant initializations.

    Raises:
        ValueError: If the GROQ_API_KEY is missing from the OpenKeyTranslate\\settings.json.

    Returns:
        Groq: The initialized Groq API client.
    """
    global _client
    if _client is None:
        if not GROQ_API_KEY:
            raise ValueError("groq_api_key is empty — fill it in settings.json")
        _client = Groq(api_key=GROQ_API_KEY)
    return _client



# NOTE: The model must return a JSON object {...}, not just an array [...].
# This is a strict requirement for utilizing the 'json_object' response mode in modern LLMs.

SYSTEM_PROMPT = """You are a manga/comic translator from English to Polish.
Translate the provided speech bubble texts accurately, preserving:
- tone and emotion (exclamations, hesitations, shouting)
- slang and informal language
- sound effects (onomatopoeia) — transliterate or adapt, don't translate literally
- line breaks if present

Return ONLY a JSON object containing a "translations" key with an array of objects. No explanations:
{
  "translations": [
    {"id": "bubble_0", "translation": "..."}
  ]
}"""


def translate_bubbles(bubbles: list[dict]) -> list[dict]:
    """
    Translate extracted speech bubble texts using the Groq LLM.

    Sends a batch of text segments to the LLM for English-to-Polish translation.

    It uses the LLM's JSON mode to guarantee a strictly structured output.

    Params:
        bubbles (list[dict]): A list of bubble dictionaries. Each dictionary must
                              contain at least 'bubble_id' and 'text'.

    Returns:
        list[dict]: The original list of bubbles, mutated to include a new
                    'translation' field containing the localized Polish text.
    """
    if not bubbles:
        return bubbles

    # 1. Build the payload for the model (filtering out empty texts)
    texts_payload = [
        {"id": b["bubble_id"], "text": b["text"]}
        for b in bubbles
        if b.get("text", "").strip()
    ]

    # If there is no valid text to translate, initialize empty translation fields and abort early
    if not texts_payload:
        for b in bubbles:
            b["translation"] = ""
        return bubbles

    user_message = json.dumps(texts_payload, ensure_ascii=False)

    try:
        # 2. Execute the API call
        response = _get_client().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},  # <--- ENFORCING CLEAN JSON RESPONSE

            # Controls creativity; low value prevents hallucinations and JSON structure breaks
            temperature=TRANSLATION_TEMPERATURE,

            # Limits maximum response (in tokens) size to avoid API overuse and ensure clean JSON closing
            max_tokens=TRANSLATION_MAX_TOKENS,
        )

        # The API can generate multiple response variants (e.g., 3 choices).
        # But, we usually request only one, so we take the first choice (index 0).
        raw = response.choices[0].message.content.strip()

        # 3. Defensive Parsing: Safely load the JSON payload
        parsed_data = json.loads(raw)

        # Extract the array from the "translations" key (returns an empty list [] if missing)
        translations_list = parsed_data.get("translations", [])

        # Create a mapping dictionary: {"bubble_0": "Polish text", ...}
        trans_map = {t.get("id"): t.get("translation", "") for t in translations_list if "id" in t}

        # 4. Update the original bubbles with their respective translations
        for bubble in bubbles:
            # Using .get() safely returns an empty string instead of throwing a KeyError
            # in case the LLM hallucinates or misses a specific bubble ID.
            bubble["translation"] = trans_map.get(bubble["bubble_id"], "")

    except Exception as e:
        # Print the error, but allow the program to continue running normally
        print(f"\n[TRANSLATION CRITICAL ERROR]: API communication or parsing error: {e}")
        print(f"[DEBUG] Raw model response: {raw if 'raw' in locals() else 'None'}")

        # Fallback: Fill missing "translation" fields with empty strings to prevent downstream crashes
        for bubble in bubbles:
            if "translation" not in bubble:
                bubble["translation"] = ""

    return bubbles