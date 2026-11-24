# -*- coding: utf-8 -*-
"""DeepSeek 连通性自检：验证 key、余额、模型可用性与延迟。

用法（key 只从环境变量读，不落盘、不打印）：
    $env:DEEPSEEK_API_KEY='sk-...'
    python check_llm.py
    python check_llm.py --model deepseek-reasoner
"""
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
MODEL = os.environ.get("MODEL_MAIN", "deepseek-chat")


def mask(k: str) -> str:
    return "%s…%s（%d 位）" % (k[:6], k[-4:], len(k)) if len(k) > 12 else "(过短)"


def main() -> int:
    print("=" * 62)
    print("DeepSeek 连通性自检")
    print("=" * 62)
    if not KEY:
        print("FAIL 未设置 DEEPSEEK_API_KEY 环境变量")
        return 2
    print("key：%s" % mask(KEY))
    print("base：%s" % BASE)

    try:
        import httpx
    except ImportError:
        print("FAIL 缺少 httpx（pip install -r requirements-core.txt）")
        return 2

    model = MODEL
    for i, a in enumerate(sys.argv):
        if a == "--model" and i + 1 < len(sys.argv):
            model = sys.argv[i + 1]

    headers = {"Authorization": "Bearer %s" % KEY, "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }

    t0 = time.time()
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post("%s/chat/completions" % BASE, headers=headers, json=payload)
    except Exception as e:
        print("FAIL 网络异常：%s: %s" % (type(e).__name__, e))
        return 1
    dt = time.time() - t0

    print("HTTP %s｜耗时 %.2fs" % (r.status_code, dt))
    if r.status_code != 200:
        # 常见错误给出可操作结论
        try:
            err = r.json().get("error", {})
            msg = err.get("message", r.text[:300])
        except Exception:
            msg = r.text[:300]
        print("FAIL %s" % msg)
        if r.status_code == 401:
            print("→ key 无效或已被删除")
        elif r.status_code == 402:
            print("→ 账户余额不足，请充值")
        elif r.status_code == 429:
            print("→ 触发限流，稍后重试")
        return 1

    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content", "")
    usage = data.get("usage") or {}
    print("模型：%s" % data.get("model", "?"))
    print("回复：%r" % content.strip())
    print("用量：prompt %s / completion %s / total %s"
          % (usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")))
    print("\nOK 连通正常，可以进入 M3。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
