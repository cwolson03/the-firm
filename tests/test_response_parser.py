"""
tests/test_response_parser.py

Unit tests for llm_client._parse_llm_response().
Covers: clean JSON, JSON in markdown fences, prose-only, malformed JSON,
block phrases, and confidence extraction edge cases.
"""

import sys
import os
import json
import pytest

# Add bots/ to path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bots'))

from llm_client import _parse_llm_response


class TestParseCleanJSON:
    def test_clean_json_go_true(self):
        content = json.dumps({
            "go": True,
            "confidence": "high",
            "reasoning": "Strong directional edge confirmed by data.",
            "risks": ["execution timing", "liquidity thin"]
        })
        result = _parse_llm_response(content)
        assert result["go"] is True
        assert result["confidence"] == "high"
        assert len(result["risks"]) == 2

    def test_clean_json_go_false(self):
        content = json.dumps({
            "go": False,
            "confidence": "low",
            "reasoning": "Model edge is marginal and macro conditions uncertain.",
            "risks": ["directional uncertainty"]
        })
        result = _parse_llm_response(content)
        assert result["go"] is False
        assert result["confidence"] == "low"

    def test_clean_json_missing_fields(self):
        """Parser should handle partial JSON gracefully."""
        content = json.dumps({"go": True})
        result = _parse_llm_response(content)
        assert result["go"] is True
        # Should not raise even with missing keys


class TestMarkdownFencedJSON:
    def test_fenced_json_block(self):
        content = """Here is my analysis:

```json
{
  "go": true,
  "confidence": "high",
  "reasoning": "GDPNow at 1.24% is well below 2.0% threshold.",
  "risks": ["upward GDP revision risk"]
}
```

That's my recommendation."""
        result = _parse_llm_response(content)
        assert result["go"] is True
        assert result["confidence"] == "high"

    def test_fenced_block_no_language_tag(self):
        content = """Analysis:
```
{"go": false, "confidence": "medium", "reasoning": "Timing risk present.", "risks": []}
```"""
        result = _parse_llm_response(content)
        assert result["go"] is False


class TestProseOnly:
    def test_block_phrase_no_trade(self):
        """Prose with 'do not trade' should return go=False."""
        content = "After careful analysis, I recommend you do not trade this market. The edge is insufficient."
        result = _parse_llm_response(content)
        assert result["go"] is False

    def test_block_phrase_reject(self):
        content = "I would reject this trade. The directional model is weak."
        result = _parse_llm_response(content)
        assert result["go"] is False

    def test_block_phrase_no_go(self):
        content = "This is a no-go situation given the macro uncertainty."
        result = _parse_llm_response(content)
        assert result["go"] is False

    def test_affirmative_prose(self):
        """Prose without block phrases should default to go=True (advisory bias)."""
        content = "The trade looks reasonable. GDPNow supports the directional thesis."
        result = _parse_llm_response(content)
        assert result["go"] is True


class TestMalformedInput:
    def test_empty_string(self):
        result = _parse_llm_response("")
        # Should not raise; go defaults to True (graceful degradation)
        assert isinstance(result, dict)

    def test_truncated_json(self):
        content = '{"go": true, "confidence": "high", "reasoning": "Incomplete'
        result = _parse_llm_response(content)
        assert isinstance(result, dict)

    def test_none_content(self):
        result = _parse_llm_response(None)
        assert isinstance(result, dict)

    def test_non_json_object(self):
        result = _parse_llm_response("just a plain string with no structure")
        assert isinstance(result, dict)
        assert "go" in result


class TestConfidenceParsing:
    def test_confidence_variants(self):
        """Parser should normalize confidence strings."""
        for conf in ["high", "HIGH", "High"]:
            content = json.dumps({"go": True, "confidence": conf, "reasoning": "ok", "risks": []})
            result = _parse_llm_response(content)
            assert result.get("confidence", "").lower() == "high"


class TestRiskExtraction:
    def test_risks_as_list(self):
        content = json.dumps({
            "go": True, "confidence": "medium",
            "reasoning": "Reasonable setup.",
            "risks": ["macro uncertainty", "execution timing", "low liquidity"]
        })
        result = _parse_llm_response(content)
        assert isinstance(result.get("risks"), list)
        assert len(result["risks"]) == 3

    def test_risks_as_string(self):
        """Some models return risks as a string — parser should handle."""
        content = json.dumps({
            "go": True, "confidence": "medium",
            "reasoning": "ok",
            "risks": "macro uncertainty, execution timing"
        })
        result = _parse_llm_response(content)
        # Should not raise; risks may be string or list
        assert "risks" in result
