# -*- coding: utf-8 -*-
"""DeepSeek 客户端：只做一件事——稳定地把提示词变成文本/JSON。

设计要点：
  · key 只从环境变量读，不落盘、不进日志；
  · 429/5xx/网络异常按 1s→4s→16s 退避重试；
  · JSON 模式容错：容忍 ```json 围栏、前后废话，取第一个 { 到最后一个 } 之间解析。
"""
import json
import os
import re
import time

from . import config          # noqa: F401  触发 .env 加载，保证下面的读取拿到值
from .errors import AppError


def _env(k: str, default: str) -> str:
    return os.environ.get(k, default)


def base_url() -> str:
    return _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")


def api_key() -> str:
    return _env("DEEPSEEK_API_KEY", "").strip()


def model_main() -> str:
    return _env("MODEL_MAIN", "deepseek-chat")


def model_strong() -> str:
    return _env("MODEL_STRONG", "deepseek-reasoner")


def _timeout() -> float:
    return float(_env("LLM_TIMEOUT", "90"))


def _max_retry() -> int:
    return int(_env("LLM_MAX_RETRY", "3"))


class LLMError(AppError):
    code = "UPSTREAM_ERROR"
    http_status = 502


class LLMQuota(AppError):
    code = "UPSTREAM_QUOTA"
    http_status = 402


_stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}


def stats() -> dict:
    return dict(_stats)


def _post(payload: dict) -> dict:
    try:
        import httpx
    except ImportError:
        raise LLMError("缺少 httpx，无法调用模型（pip install -r requirements-core.txt）")
    headers = {"Authorization": "Bearer %s" % api_key(), "Content-Type": "application/json"}
    last = None
    for attempt in range(_max_retry() + 1):
        t0 = time.time()
        try:
            with httpx.Client(timeout=_timeout()) as c:
                r = c.post("%s/chat/completions" % base_url(), headers=headers, json=payload)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            if attempt < _max_retry():
                time.sleep(4 ** attempt)
                continue
            raise LLMError("调用模型失败（网络）：%s" % last)

        _stats["seconds"] += time.time() - t0
        if r.status_code == 200:
            return r.json()
        body = r.text[:300]
        if r.status_code in (429, 500, 502, 503, 504) and attempt < _max_retry():
            last = "HTTP %s %s" % (r.status_code, body)
            time.sleep(4 ** attempt)
            continue
        if r.status_code == 401:
            raise LLMError("API key 无效或已删除（HTTP 401）")
        if r.status_code == 402:
            raise LLMQuota("账户余额不足（HTTP 402），请充值后重试")
        if r.status_code == 429:
            raise LLMError("触发限流（HTTP 429），请稍后重试", retryable=True)
        raise LLMError("模型返回异常 HTTP %s：%s" % (r.status_code, body))
    raise LLMError("调用模型失败：%s" % last)


def chat(messages: list[dict], model: str | None = None, json_mode: bool = False,
         max_tokens: int = 2048, temperature: float = 0.2) -> dict:
    """调一次模型，返回 {text, model, usage}。"""
    if not api_key():
        raise LLMError("未配置 DEEPSEEK_API_KEY 环境变量")
    payload = {
        "model": model or model_main(),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    data = _post(payload)
    _stats["calls"] += 1
    usage = data.get("usage") or {}
    _stats["prompt_tokens"] += usage.get("prompt_tokens") or 0
    _stats["completion_tokens"] += usage.get("completion_tokens") or 0

    choices = data.get("choices") or []
    text = ((choices[0] if choices else {}).get("message") or {}).get("content") or ""
    return {"text": text, "model": data.get("model", payload["model"]), "usage": usage}


def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON：容忍 ``` 围栏与前后废话。"""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("模型没有返回可解析的 JSON（前 200 字：%s）" % text[:200])


def chat_json(messages: list[dict], model: str | None = None,
              max_tokens: int = 4096, temperature: float = 0.1) -> dict:
    """JSON 模式；第一次失败会用"只输出 JSON"再要一次。"""
    r = chat(messages, model=model, json_mode=True, max_tokens=max_tokens, temperature=temperature)
    try:
        return _extract_json(r["text"])
    except LLMError:
        retry = messages + [{"role": "assistant", "content": r["text"]},
                           {"role": "user", "content": "上面的输出不是合法 JSON。请只输出 JSON，不要任何解释、不要代码围栏。"}]
        r2 = chat(retry, model=model, json_mode=True, max_tokens=max_tokens, temperature=0)
        return _extract_json(r2["text"])
