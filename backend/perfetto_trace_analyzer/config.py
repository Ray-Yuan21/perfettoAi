"""Configuration management with YAML support."""

from __future__ import annotations

import copy
import logging
import os
from typing import Any

import yaml

from .models import AppConfig, LLMConfig, ScoringConfig

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "default_config.yaml")
_USER_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


class ConfigManager:
    """Loads, validates, and merges default + user configuration."""

    @staticmethod
    def load(config_path: str | None = None) -> AppConfig:
        """Load configuration, merging user config over defaults."""
        with open(_DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
            default = yaml.safe_load(f) or {}

        # Auto-detect user config.yaml if not specified
        if config_path is None and os.path.exists(_USER_CONFIG_PATH):
            config_path = _USER_CONFIG_PATH
            logger.info("Auto-loaded user config: %s", config_path)

        user: dict[str, Any] = {}
        if config_path:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    user = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("Failed to load user config %s: %s", config_path, e)

        merged = ConfigManager._merge_configs(default, user)
        warnings = ConfigManager._validate_config(merged)
        for w in warnings:
            logger.warning("Config warning: %s", w)

        return ConfigManager._dict_to_config(merged)

    @staticmethod
    def _merge_configs(default: dict, user: dict) -> dict:
        """Deep-merge user config over default config (user takes priority)."""
        result = copy.deepcopy(default)
        for key, value in user.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = ConfigManager._merge_configs(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @staticmethod
    def _validate_config(config: dict) -> list[str]:
        """Validate config values, returning warnings for invalid entries."""
        warnings: list[str] = []

        llm = config.get("llm", {})
        if not isinstance(llm, dict):
            warnings.append("'llm' should be a dict; using defaults")
            config["llm"] = {}

        scoring = config.get("scoring", {})
        weights = scoring.get("weights", {}) if isinstance(scoring, dict) else {}
        if isinstance(weights, dict):
            total = sum(weights.values())
            if abs(total - 1.0) > 0.01:
                warnings.append(
                    f"Scoring weights sum to {total:.3f}, expected 1.0"
                )

        analyzers = config.get("analyzers", {})
        if not isinstance(analyzers, dict):
            warnings.append("'analyzers' should be a dict; using defaults")
            config["analyzers"] = {}

        temp = llm.get("temperature") if isinstance(llm, dict) else None
        if temp is not None and (not isinstance(temp, (int, float)) or temp < 0 or temp > 2):
            warnings.append(f"Invalid temperature {temp}; using default")
            llm["temperature"] = 0.1

        return warnings

    @staticmethod
    def _dict_to_config(d: dict) -> AppConfig:
        """Convert a raw dict to typed AppConfig."""
        llm_dict = d.get("llm", {})
        api_key = llm_dict.get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            api_key = os.environ.get(env_var, "")
        # Direct env var override (for Docker deployments)
        api_key = os.environ.get("LLM_API_KEY") or api_key

        llm = LLMConfig(
            provider=llm_dict.get("provider", "openai"),
            api_endpoint=os.environ.get("LLM_API_ENDPOINT") or llm_dict.get("api_endpoint", "https://api.openai.com/v1"),
            model_name=os.environ.get("LLM_MODEL_NAME") or llm_dict.get("model_name", "gpt-4"),
            api_key=api_key,
            temperature=llm_dict.get("temperature", 0.1),
            max_tokens=llm_dict.get("max_tokens", 4096),
            timeout=int(os.environ.get("LLM_TIMEOUT") or llm_dict.get("timeout", 300)),
            max_retries=llm_dict.get("max_retries", 1),
            max_tool_turns=llm_dict.get("max_tool_turns", 5),
        )

        scoring_dict = d.get("scoring", {})
        scoring = ScoringConfig(
            weights=scoring_dict.get("weights", ScoringConfig().weights)
        )

        return AppConfig(
            llm=llm,
            scoring=scoring,
            analyzers=d.get("analyzers", {}),
            prompt_overrides=d.get("prompt_overrides", {}),
        )
