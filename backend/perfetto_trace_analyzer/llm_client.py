"""LLM client supporting OpenAI-compatible APIs and Anthropic, with agentic tool calling."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

import httpx

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
        self._http = httpx.Client(
            timeout=httpx.Timeout(config.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )

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

    def repair_json(self, original_prompt: str, raw_text: str) -> LLMResponse:
        """Ask the model to rewrite a non-JSON answer into strict JSON."""
        schema_hint = self._extract_json_template(original_prompt)
        repair_prompt = (
            "你上一轮没有按要求输出严格 JSON。\n"
            "请基于下面的原始任务要求和你上一轮的回答，直接输出一个 JSON 对象。\n"
            "不要输出解释、不要输出 markdown 代码块、不要输出额外文字。\n"
            "如果某些字段无法确定，保留字段并使用空字符串、空数组、空对象或 null。\n\n"
            "【原始任务要求】\n"
            f"{original_prompt}\n\n"
            "【建议遵循的 JSON 模板】\n"
            f"{schema_hint or '{}'}\n\n"
            "【你上一轮的回答】\n"
            f"{raw_text}\n"
        )
        return self.analyze(repair_prompt)

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

        If max_turns is 0, falls back to simple analyze() without tool calling.
        """
        turns = max_turns if max_turns is not None else self.config.max_tool_turns

        # If max_turns is 0, skip agentic mode and use simple API call
        if turns == 0:
            logger.info("max_tool_turns=0, using simple analyze() without tool calling")
            return self.analyze(prompt)

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

    @staticmethod
    def _extract_json_template(prompt: str) -> str | None:
        """Extract the first JSON code block from the prompt as a schema hint."""
        match = re.search(r"```json\s*\n(.*?)```", prompt, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _post(self, url: str, payload: dict) -> dict:
        """POST JSON payload and return parsed response body."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "x-api-key": self.config.api_key,  # Anthropic uses this header
            "anthropic-version": "2023-06-01",
        }
        try:
            resp = self._http.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:2000]
            logger.error("LLM API HTTP %d response body: %s", e.response.status_code, body_text)
            raise RuntimeError(
                f"LLM API HTTP error {e.response.status_code}: {e.response.reason_phrase} | {body_text[:500]}"
            ) from e
        except httpx.ConnectError as e:
            raise RuntimeError(f"LLM API connection error: {e}") from e
        except httpx.TimeoutException as e:
            raise RuntimeError(f"LLM API timeout: {e}") from e
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
