"""Local LLM integration."""

from .client import (
    HttpOllamaTransport,
    NullTransport,
    SuggestionEngine,
    parse_suggestions,
)

__all__ = [
    "HttpOllamaTransport",
    "NullTransport",
    "SuggestionEngine",
    "parse_suggestions",
]
