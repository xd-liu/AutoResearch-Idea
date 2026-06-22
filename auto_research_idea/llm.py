"""Thin wrapper around the Anthropic SDK for this project's agents.

All agents talk to Claude through `LLMClient`, which offers two call shapes:
  - `complete()` -> free-form text
  - `parse()`    -> a validated Pydantic object

Structured output is done via a portable prompt-and-validate approach (embed the
JSON schema in the prompt, extract and validate the JSON, retry once on failure)
rather than the SDK's `messages.parse` helper, so the code runs against the wide
range of `anthropic` SDK versions — including the 0.72.x line that is the latest
supported on Python 3.8. Requests are streamed to avoid HTTP timeouts; the SDK
retries 429/5xx automatically.
"""

from __future__ import annotations

import json
from typing import Optional, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_MAX_TOKENS = 16000

_JSON_INSTRUCTION = """

Respond with a SINGLE JSON object and nothing else — no prose, no markdown code
fences. It must conform exactly to this JSON schema:

{schema}
"""


class LLMClient:
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def _stream_text(self, *, model: str, system: str, prompt: str, max_tokens: int) -> str:
        with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
        return "".join(b.text for b in message.content if b.type == "text").strip()

    def complete(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        effort: str = "high",  # accepted for API symmetry; unused on this SDK line
        max_tokens: int = _MAX_TOKENS,
    ) -> str:
        """Return Claude's free-form text response."""
        return self._stream_text(model=model, system=system, prompt=prompt, max_tokens=max_tokens)

    def parse(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: Type[T],
        effort: str = "high",  # accepted for API symmetry; unused on this SDK line
        max_tokens: int = _MAX_TOKENS,
    ) -> T:
        """Return a validated instance of `schema`."""
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        full_prompt = prompt + _JSON_INSTRUCTION.format(schema=schema_json)

        text = self._stream_text(model=model, system=system, prompt=full_prompt, max_tokens=max_tokens)
        obj = self._validate(text, schema)
        if obj is not None:
            return obj

        # One repair attempt: hand the model its own output and the error.
        repair = (
            "Your previous response did not parse as valid JSON for the schema. "
            "Return ONLY the corrected JSON object.\n\nPrevious response:\n" + text
        )
        text2 = self._stream_text(
            model=model, system=system, prompt=full_prompt + "\n\n" + repair, max_tokens=max_tokens
        )
        obj = self._validate(text2, schema)
        if obj is None:
            raise RuntimeError(f"Model did not return parseable JSON for {schema.__name__}.")
        return obj

    @staticmethod
    def _validate(text: str, schema: Type[T]) -> Optional[T]:
        blob = _extract_json(text)
        if blob is None:
            return None
        try:
            return schema.model_validate_json(blob)
        except ValidationError:
            return None


def _extract_json(text: str) -> Optional[str]:
    """Pull the first balanced top-level JSON object out of a text blob.

    Scans for a brace-balanced object while tracking string state, so it
    transparently handles ```-fenced output, leading prose, and stray backticks
    inside string values — no fence pre-stripping needed (pre-stripping on
    triple-backticks would itself corrupt JSON whose strings contain them).
    """
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None
