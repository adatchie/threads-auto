"""LLM抽象レイヤー: Anthropic / Gemini(REST) / GLM を環境に応じて切り替え"""
import os
import json
import logging
import requests

logger = logging.getLogger("llm")

# 優先順位: ANTHROPIC > GEMINI > GLM
_BACKENDS = ["anthropic", "gemini", "glm"]


def _detect_backend() -> str:
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("GLM_API_KEY"):
        return "glm"
    raise RuntimeError("LLM APIキーが未設定です（ANTHROPIC_API_KEY / GEMINI_API_KEY / GLM_API_KEY のいずれかを設定してください）")


def call_llm(prompt: str, max_tokens: int = 1024) -> str:
    """設定済みのLLMバックエンドを呼び出してテキストを返す"""
    backend = _detect_backend()
    logger.debug(f"Using LLM backend: {backend}")

    if backend == "anthropic":
        return _call_anthropic(prompt, max_tokens)
    elif backend == "gemini":
        return _call_gemini(prompt, max_tokens)
    elif backend == "glm":
        return _call_glm(prompt, max_tokens)


def _call_anthropic(prompt: str, max_tokens: int) -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


def _call_gemini(prompt: str, max_tokens: int) -> str:
    """Gemini REST API 直接呼び出し（google-generativeaiライブラリ不要）"""
    key = os.getenv("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
    resp = requests.post(
        url,
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_glm(prompt: str, max_tokens: int) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("GLM_API_KEY"),
        base_url="https://open.bigmodel.cn/api/paas/v4/",
    )
    msg = client.chat.completions.create(
        model="glm-4-flash",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.choices[0].message.content.strip()


def call_llm_json(prompt: str, max_tokens: int = 1024) -> dict | list:
    """LLMを呼び出し、JSON応答をパースして返す"""
    text = call_llm(prompt, max_tokens)
    # コードブロックの除去
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)
