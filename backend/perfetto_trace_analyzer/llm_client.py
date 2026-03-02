"""LLM client supporting OpenAI-compatible APIs and Anthropic, with agentic tool calling."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import LLMConfig, LLMResponse

if TYPE_CHECKING:
    from .tools import BoundToolRegistry

logger = logging.getLogger(__name__)


class LLMClient:
    """Sends analysis prompts to an LLM and parses structured JSON responses.

    Supports both OpenAI-compatible (function calling) and Anthropic (tool_use)
    providers. When a BoundToolRegistry is provided, runs an agentic loop where
    the LLM can call tools up to max_tool_turns times before producing a final answer.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    # ── Simple one-shot analysis ──────────────────────────────

    def analyze(self, prompt: str) -> LLMResponse:
        """Send prompt to LLM and return parsed response (no tool calling)."""
        try:
            raw_text = self._call_api(prompt)
            parsed = self._parse_response(raw_text)
            return LLMResponse(
                raw_text=raw_text,
                parsed_data=parsed,
                success=True,
                error=None if parsed is not None else "Failed to parse JSON from response",
            )
        except Exception as e:
            logger.error("LLM API call failed: %s", e)
            return LLMResponse(
                raw_text="",
                parsed_data=None,
                success=False,
                error=str(e),
            )

    # ── Agentic loop with tool calling ────────────────────────

    def analyze_with_tools(
        self,
        prompt: str,
        bound_registry: "BoundToolRegistry",
        max_turns: int | None = None,
    ) -> LLMResponse:
        """Run an agentic loop: LLM can call tools up to max_turns times.

        Automatically selects OpenAI or Anthropic format based on config.provider.
        After max_turns tool calls (or when LLM stops calling tools), parses the
        final text response as JSON.
        """
        turns = max_turns if max_turns is not None else self.config.max_tool_turns
        provider = self.config.provider.lower()

        try:
            if provider == "anthropic":
                raw_text = self._agentic_loop_anthropic(prompt, bound_registry, turns)
            else:
                raw_text = self._agentic_loop_openai(prompt, bound_registry, turns)

            parsed = self._parse_response(raw_text)
            return LLMResponse(
                raw_text=raw_text,
                parsed_data=parsed,
                success=True,
                error=None if parsed is not None else "Failed to parse JSON from response",
            )
        except Exception as e:
            logger.error("LLM agentic loop failed: %s", e)
            return LLMResponse(
                raw_text="",
                parsed_data=None,
                success=False,
                error=str(e),
            )

    # ── OpenAI agentic loop ───────────────────────────────────

    def _agentic_loop_openai(
        self,
        prompt: str,
        bound_registry: "BoundToolRegistry",
        max_turns: int,
    ) -> str:
        """OpenAI function calling agentic loop."""
        url = f"{self.config.api_endpoint.rstrip('/')}/chat/completions"
        tools = bound_registry.openai_schemas()
        messages: list[dict] = [
            {
                "role": "system",
                "content": "你是一个 Android 性能分析专家。请严格以 JSON 格式返回最终分析结果。",
            },
            {"role": "user", "content": prompt},
        ]

        for turn in range(max_turns + 1):
            payload: dict[str, Any] = {
                "model": self.config.model_name,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
            # Only offer tools if we still have budget
            if turn < max_turns and tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            body = self._post(url, payload)
            choice = body["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")

            # Append assistant message to history
            messages.append(message)

            if finish_reason != "tool_calls" or not message.get("tool_calls"):
                # LLM is done calling tools — return final text
                return message.get("content") or ""

            # Execute each tool call and append results
            for tc in message["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_args = tc["function"].get("arguments", "{}")
                logger.debug("Tool call [OpenAI]: %s(%s)", tool_name, tool_args)
                result = bound_registry.call_json(tool_name, tool_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        # Budget exhausted — ask for final answer without tools
        logger.warning("Tool call budget exhausted (%d turns), forcing final answer", max_turns)
        return messages[-1].get("content") or ""

    # ── Anthropic agentic loop ────────────────────────────────

    def _agentic_loop_anthropic(
        self,
        prompt: str,
        bound_registry: "BoundToolRegistry",
        max_turns: int,
    ) -> str:
        """Anthropic tool_use agentic loop."""
        url = f"{self.config.api_endpoint.rstrip('/')}/messages"
        tools = bound_registry.claude_schemas()
        messages: list[dict] = [{"role": "user", "content": prompt}]
        system = "你是一个 Android 性能分析专家。请严格以 JSON 格式返回最终分析结果。"

        for turn in range(max_turns + 1):
            payload: dict[str, Any] = {
                "model": self.config.model_name,
                "max_tokens": self.config.max_tokens,
                "system": system,
                "messages": messages,
                "temperature": self.config.temperature,
            }
            if turn < max_turns and tools:
                payload["tools"] = tools
                payload["tool_choice"] = {"type": "auto"}

            body = self._post(url, payload)
            stop_reason = body.get("stop_reason", "end_turn")
            content_blocks = body.get("content", [])

            # Append assistant turn
            messages.append({"role": "assistant", "content": content_blocks})

            if stop_reason != "tool_use":
                # Extract text from content blocks
                text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
                return "\n".join(text_parts)

            # Execute tool_use blocks
            tool_results = []
            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block["name"]
                tool_input = block.get("input", {})
                logger.debug("Tool call [Anthropic]: %s(%s)", tool_name, tool_input)
                result = bound_registry.call_json(tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

        logger.warning("Tool call budget exhausted (%d turns), forcing final answer", max_turns)
        # Return last assistant text if available
        for block in content_blocks:
            if block.get("type") == "text":
                return block["text"]
        return ""

    # ── HTTP helpers ──────────────────────────────────────────

    def _call_api(self, prompt: str) -> str:
        """Call OpenAI-compatible chat completions API (no tools)."""
        url = f"{self.config.api_endpoint.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个 Android 性能分析专家。请严格以 JSON 格式返回分析结果。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        body = self._post(url, payload)
        return body["choices"][0]["message"]["content"]

    def _post(self, url: str, payload: dict) -> dict:
        """POST JSON payload and return parsed response body."""
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "x-api-key": self.config.api_key,  # Anthropic uses this header
            "anthropic-version": "2023-06-01",
        }
        req = Request(url, data=data, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.config.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raise RuntimeError(f"LLM API HTTP error {e.code}: {e.reason}") from e
        except URLError as e:
            raise RuntimeError(f"LLM API connection error: {e.reason}") from e
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected LLM API response format: {e}") from e

    @staticmethod
    def _parse_response(raw_text: str) -> dict[str, Any] | None:
        """Extract JSON object from LLM response text."""
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            pass

        patterns = [
            r"```json\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
            r"\{.*\}",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw_text, re.DOTALL)
            if match:
                try:
                    candidate = match.group(1) if match.lastindex else match.group(0)
                    return json.loads(candidate)
                except (json.JSONDecodeError, IndexError):
                    continue

        return None
