#!/usr/bin/env python3
"""
llm_client.py — Unified LLM interface for The Firm
Supports: Grok (xAI), Claude (Anthropic), GPT-4o (OpenAI)
Each model is optional — skipped gracefully if key not set.

Usage:
    from llm_client import llm_reason, trade_review_prompt
    result = llm_reason(trade_review_prompt(...), primary="grok")
"""

import os
import json
import logging
import re

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# API Key Loading — all optional
# ─────────────────────────────────────────────────────────────────────────────

TOKENS_FILE = os.path.join(os.path.dirname(__file__), '..', 'config', 'bot-tokens.env')
load_dotenv(TOKENS_FILE)

GROK_API_KEY   = os.getenv("GROK_API_KEY", "")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── Model constants (update these when providers deprecate models) ──────────
GROK_MODEL_PRIMARY = "grok-3-fast"
GROK_MODEL_FALLBACK = "grok-beta"
CLAUDE_MODEL = "claude-haiku-4-5"
OPENAI_MODEL = "gpt-4o-mini"

log.info(f"[LLM] Using models: Grok={GROK_MODEL_PRIMARY}, Claude={CLAUDE_MODEL}, OpenAI={OPENAI_MODEL}")

# ─────────────────────────────────────────────────────────────────────────────
# Model API Functions
# ─────────────────────────────────────────────────────────────────────────────

def query_grok(prompt: str, system: str = "", max_tokens: int = 500) -> dict:
    """
    Query Grok via xAI API.
    Returns: {"content": str, "model": "grok", "ok": bool, "error": str|None}
    """
    if not GROK_API_KEY:
        return {"content": "", "model": "grok", "ok": False, "error": "GROK_API_KEY not set"}

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Try grok-3-fast first, fall back to grok-beta on 404
    for model_name in [GROK_MODEL_PRIMARY, GROK_MODEL_FALLBACK]:
        try:
            resp = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers=headers,
                json={
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
                timeout=30,
            )
            if resp.status_code == 404 and model_name == GROK_MODEL_PRIMARY:
                log.warning(f"[llm_client] {GROK_MODEL_PRIMARY} returned 404, falling back to {GROK_MODEL_FALLBACK}")
                continue
            if resp.status_code != 200:
                return {
                    "content": "",
                    "model": "grok",
                    "ok": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return {"content": content, "model": "grok", "ok": True, "error": None}
        except Exception as e:
            if model_name == GROK_MODEL_FALLBACK:
                return {"content": "", "model": "grok", "ok": False, "error": str(e)}

    return {"content": "", "model": "grok", "ok": False, "error": "all model variants failed"}


def query_claude(prompt: str, system: str = "", max_tokens: int = 500) -> dict:
    """
    Query Claude via Anthropic API.
    Returns: {"content": str, "model": "claude", "ok": bool, "error": str|None}
    """
    if not CLAUDE_API_KEY:
        return {"content": "", "model": "claude", "ok": False, "error": "CLAUDE_API_KEY not set"}

    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=30,
        )
        if resp.status_code != 200:
            return {
                "content": "",
                "model": "claude",
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        content = data["content"][0]["text"]
        return {"content": content, "model": "claude", "ok": True, "error": None}
    except Exception as e:
        return {"content": "", "model": "claude", "ok": False, "error": str(e)}


def query_openai(prompt: str, system: str = "", max_tokens: int = 500) -> dict:
    """
    Query GPT-4o-mini via OpenAI API.
    Returns: {"content": str, "model": "openai", "ok": bool, "error": str|None}
    """
    if not OPENAI_API_KEY:
        return {"content": "", "model": "openai", "ok": False, "error": "OPENAI_API_KEY not set"}

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={
                "model": OPENAI_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return {
                "content": "",
                "model": "openai",
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return {"content": content, "model": "openai", "ok": True, "error": None}
    except Exception as e:
        return {"content": "", "model": "openai", "ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Response Parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_llm_response(raw: str) -> dict:
    """
    Parse an LLM response into structured fields.
    Tries JSON first; falls back to heuristic text extraction.
    Returns partial dict with go, confidence, reasoning, risks.
    """
    if not raw or not raw.strip():
        return {"go": True, "confidence": "low", "reasoning": "", "risks": []}

    # Try JSON extraction (model may return ```json ... ``` blocks)
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if not json_match:
        # Try bare JSON object
        json_match = re.search(r'\{[^{}]*"go"[^{}]*\}', raw, re.DOTALL)

    if json_match:
        try:
            parsed = json.loads(json_match.group(1) if '```' in (json_match.group(0) or '') else json_match.group(0))
            go_val = parsed.get("go", True)
            # Normalize go to bool
            if isinstance(go_val, str):
                go_val = go_val.strip().lower() not in ("false", "no", "0", "block", "reject")
            return {
                "go":         bool(go_val),
                "confidence": str(parsed.get("confidence", "medium")).lower(),
                "reasoning":  str(parsed.get("reasoning", parsed.get("reason", ""))),
                "risks":      list(parsed.get("risks", [])),
            }
        except (json.JSONDecodeError, AttributeError):
            pass

    # Heuristic text extraction
    raw_lower = raw.lower()

    # Detect go/no-go
    go = True
    if any(phrase in raw_lower for phrase in [
        "do not trade", "don't trade", "no-go", "no go", "block", "reject",
        "against trading", "not recommended", "advise against", "decline",
        "\"go\": false", '"go":false',
    ]):
        go = False

    # Detect confidence
    confidence = "medium"
    if "high confidence" in raw_lower or "strongly" in raw_lower:
        confidence = "high"
    elif "low confidence" in raw_lower or "uncertain" in raw_lower or "weak" in raw_lower:
        confidence = "low"

    # Extract risks as bullet points or sentences containing "risk"
    risks = []
    for line in raw.splitlines():
        stripped = line.strip().lstrip("•-*123456789. ")
        if stripped and ("risk" in line.lower() or line.strip().startswith(("•", "-", "*"))):
            if len(stripped) > 10:
                risks.append(stripped[:200])

    return {
        "go":         go,
        "confidence": confidence,
        "reasoning":  raw.strip()[:1000],
        "risks":      risks[:5],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Model Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

_MODEL_FUNCS = {
    "grok":   query_grok,
    "claude": query_claude,
    "openai": query_openai,
}


def _call_model(model_name: str, prompt: str, system: str, max_tokens: int) -> dict:
    """Call the specified model. Returns raw API result dict."""
    fn = _MODEL_FUNCS.get(model_name)
    if fn is None:
        return {"content": "", "model": model_name, "ok": False, "error": f"unknown model: {model_name}"}
    return fn(prompt, system=system, max_tokens=max_tokens)


# ─────────────────────────────────────────────────────────────────────────────
# Main Reasoning Function
# ─────────────────────────────────────────────────────────────────────────────

def llm_reason(
    prompt: str,
    system: str = "",
    primary: str = "grok",
    shadow: str = None,
    require_consensus: bool = False,
    max_tokens: int = 500,
) -> dict:
    """
    Run prompt through primary model. Optionally run shadow model.
    If require_consensus=True and models disagree on go/no-go,
    returns go=False with reason="models_disagree".

    Returns:
    {
        "go": bool,           # primary model recommendation
        "confidence": str,    # "high" / "medium" / "low"
        "reasoning": str,     # plain English explanation
        "risks": [str],       # list of risks identified
        "shadow_go": bool,    # shadow model recommendation (None if not used)
        "consensus": bool,    # True if both agree (None if no shadow)
        "primary_raw": str,   # raw primary response
        "shadow_raw": str,    # raw shadow response (empty if not used)
        "models_used": [str], # list of model names actually called
        "ok": bool,           # True if primary call succeeded
        "error": str|None,    # error string if primary failed
    }

    IMPORTANT: Always returns the full dict — never raises exceptions to caller.
    LLM failure defaults to go=True (never blocks the trade).
    """
    result = {
        "go":         True,
        "confidence": "low",
        "reasoning":  "",
        "risks":      [],
        "shadow_go":  None,
        "consensus":  None,
        "primary_raw": "",
        "shadow_raw":  "",
        "models_used": [],
        "ok":         False,
        "error":      None,
    }

    try:
        # ── Primary model ────────────────────────────────────────────────────
        primary_resp = _call_model(primary, prompt, system, max_tokens)
        result["primary_raw"] = primary_resp.get("content", "")
        result["ok"]          = primary_resp.get("ok", False)
        result["error"]       = primary_resp.get("error")

        if primary_resp.get("ok") and primary_resp.get("content"):
            result["models_used"].append(primary)
            parsed = _parse_llm_response(primary_resp["content"])
            result["go"]         = parsed["go"]
            result["confidence"] = parsed["confidence"]
            result["reasoning"]  = parsed["reasoning"]
            result["risks"]      = parsed["risks"]
        else:
            # Primary failed — default go=True (graceful degradation)
            log.warning(f"[llm_client] Primary model '{primary}' failed: {primary_resp.get('error')}")
            result["go"] = True
            return result

        # ── Shadow model (optional) ───────────────────────────────────────────
        if shadow and shadow != primary:
            shadow_resp = _call_model(shadow, prompt, system, max_tokens)
            result["shadow_raw"] = shadow_resp.get("content", "")

            if shadow_resp.get("ok") and shadow_resp.get("content"):
                result["models_used"].append(shadow)
                shadow_parsed = _parse_llm_response(shadow_resp["content"])
                result["shadow_go"] = shadow_parsed["go"]

                # Consensus check
                primary_go = result["go"]
                shadow_go  = shadow_parsed["go"]
                result["consensus"] = (primary_go == shadow_go)

                if require_consensus and not result["consensus"]:
                    result["go"]       = False
                    result["reasoning"] = (
                        f"models_disagree: {primary} says {'GO' if primary_go else 'NO-GO'}, "
                        f"{shadow} says {'GO' if shadow_go else 'NO-GO'}. "
                        f"Blocking per require_consensus=True."
                    )
                    log.info(f"[llm_client] Consensus required but models disagree — blocking")
            else:
                log.warning(f"[llm_client] Shadow model '{shadow}' failed: {shadow_resp.get('error')}")

    except Exception as e:
        log.error(f"[llm_client] Unexpected error in llm_reason: {e}", exc_info=True)
        # Any exception defaults to go=True — never block
        result["go"]    = True
        result["error"] = str(e)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Template Helpers
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a quantitative trading analyst reviewing prediction market trades. "
    "Be concise and structured. Always include a go/no-go recommendation. "
    "If returning JSON, use keys: go (bool), confidence (high/medium/low), "
    "reasoning (string), risks (list of strings). "
    "If the trade thesis is sound, lean go=true. Only block on serious red flags."
)


def trade_review_prompt(
    market: str,
    direction: str,
    edge_pct: float,
    data_summary: str,
    macro_context: str = "",
) -> str:
    """
    Generate standardized trade review prompt for Donnie.
    Used for ECONOMIC_DATA prediction market trades.
    """
    macro_section = f"\nMacro context: {macro_context}" if macro_context else ""

    return (
        f"Review this prediction market trade and return a JSON decision.\n\n"
        f"Market: {market}\n"
        f"Direction: {direction}\n"
        f"Model Edge: {edge_pct:.1f}%\n"
        f"Data: {data_summary}"
        f"{macro_section}\n\n"
        f"Questions to answer:\n"
        f"1. Does the data edge support this direction?\n"
        f"2. Are there macro factors that could invalidate the quant signal?\n"
        f"3. Is the edge size ({edge_pct:.1f}%) meaningful or marginal?\n\n"
        f"Respond in JSON: "
        f'{{\"go\": true/false, \"confidence\": \"high/medium/low\", '
        f'\"reasoning\": \"...\", \"risks\": [\"...\", \"...\"]}}'
    )


def congressional_brief_prompt(
    member: str,
    ticker: str,
    trade_type: str,
    amount: str,
    score: int,
    committee: str,
    specialty: str,
) -> str:
    """
    Generate congressional trade analysis prompt for Rugrat.
    Evaluates insider trading signal from congressional disclosures.
    """
    action = "purchase" if "purchase" in trade_type.lower() else "sale"

    return (
        f"Analyze this congressional stock trade for insider signal quality.\n\n"
        f"Member: {member}\n"
        f"Committee: {committee}\n"
        f"Specialty/sector focus: {specialty}\n"
        f"Trade: {action.upper()} ${ticker} | Amount: {amount}\n"
        f"Conviction score: {score}/100\n\n"
        f"Evaluate:\n"
        f"1. Does committee/sector alignment suggest informational edge?\n"
        f"2. Is this {action} bullish or bearish signal given their position?\n"
        f"3. What are the top 3 risks to following this trade?\n\n"
        f"Respond in JSON: "
        f'{{\"go\": true/false, \"confidence\": \"high/medium/low\", '
        f'\"reasoning\": \"...\", \"risks\": [\"...\", \"...\"]}}'
    )


def options_setup_prompt(
    ticker: str,
    option_type: str,
    current_price: float,
    target: float,
    time_to_close_mins: int,
) -> str:
    """
    Generate 0DTE setup analysis prompt for Jordan.
    Evaluates whether to hold or exit a 0DTE options position.
    """
    direction = "bullish" if option_type.upper() == "CALL" else "bearish"
    move_pct = abs(target - current_price) / current_price * 100
    hours_left = time_to_close_mins / 60

    return (
        f"Analyze this 0DTE options exit decision.\n\n"
        f"Underlying: {ticker} | Current Price: ${current_price:.2f}\n"
        f"Option Type: {option_type.upper()} | Target: ${target:.2f}\n"
        f"Move required: {move_pct:.2f}% {direction}\n"
        f"Time remaining: {time_to_close_mins} minutes ({hours_left:.1f}h to 4PM close)\n\n"
        f"Assess:\n"
        f"1. Is {move_pct:.2f}% achievable in {time_to_close_mins} minutes for {ticker}?\n"
        f"2. Should we hold for target, trail a stop, or exit now?\n"
        f"3. Key risks given theta decay with {time_to_close_mins} min left.\n\n"
        f"Respond in JSON: "
        f'{{\"go\": true/false, \"confidence\": \"high/medium/low\", '
        f'\"reasoning\": \"...\", \"risks\": [\"...\", \"...\"]}}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    model = sys.argv[1] if len(sys.argv) > 1 else "grok"
    test_prompt = sys.argv[2] if len(sys.argv) > 2 else "Say hello in one word."

    print(f"Testing {model} with prompt: {test_prompt!r}")
    result = llm_reason(test_prompt, primary=model)
    print(json.dumps(result, indent=2))
