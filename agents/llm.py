"""LLM抽象レイヤー: 設定済みバックエンドを順に試し、最初に成功したものを使う"""
import os
import json
import logging
import requests

logger = logging.getLogger("llm")


def _available_backends() -> list[tuple[str, callable]]:
    """設定済みのバックエンドを優先順にリストアップ"""
    backends = []
    if os.getenv("ANTHROPIC_API_KEY"):
        backends.append(("anthropic", _call_anthropic))
    if os.getenv("GEMINI_API_KEY"):
        backends.append(("gemini", _call_gemini))
    if os.getenv("GLM_API_KEY"):
        backends.append(("glm", _call_glm))
    return backends


def call_llm(prompt: str, max_tokens: int = 1024) -> str:
    """設定済みのLLMバックエンドを順に試し、最初に成功した結果を返す"""
    backends = _available_backends()
    if not backends:
        raise RuntimeError("LLM APIキーが未設定です（ANTHROPIC_API_KEY / GEMINI_API_KEY / GLM_API_KEY のいずれかを設定してください）")

    last_error = None
    for name, fn in backends:
        try:
            logger.info(f"Trying LLM backend: {name}")
            result = fn(prompt, max_tokens)
            logger.info(f"LLM backend {name}: success")
            return result
        except Exception as e:
            logger.warning(f"LLM backend {name} failed: {e}")
            last_error = e

    raise RuntimeError(f"全LLMバックエンドが失敗しました。最後のエラー: {last_error}")


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
        model="glm-4.7-flash",
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON部分を抽出して再試行
        import re
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        match = re.search(r'\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
