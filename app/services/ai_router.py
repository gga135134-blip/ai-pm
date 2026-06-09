import json
import logging
from pathlib import Path
from app.config import BASE_DIR

CONFIG_FILE = BASE_DIR / "data" / "settings.json"
log = logging.getLogger(__name__)

PRICE_TABLE = {
    "claude": {"input": 0.003 / 1000, "output": 0.015 / 1000, "label": "Claude Sonnet"},
    "openai": {"input": 0.005 / 1000, "output": 0.015 / 1000, "label": "GPT-4o"},
    "deepseek": {"input": 0.00014 / 1000, "output": 0.00028 / 1000, "label": "DeepSeek V3"},
}

DEFAULT_FALLBACK_ORDER = ["claude", "openai", "deepseek"]


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_model_for_task(task_type: str = "", explicit_model: str = "auto") -> str:
    if explicit_model and explicit_model != "auto":
        return explicit_model

    config = _load_config()
    routes = config.get("routes", {})

    if task_type and task_type in routes:
        routed = routes[task_type]
        if routed and routed != "auto":
            return routed

    return config.get("default_ai_model", "claude")


def get_fallback_chain(primary: str) -> list[str]:
    config = _load_config()
    custom_order = config.get("fallback_order", [])
    if custom_order:
        chain = [m for m in custom_order if m != primary]
    else:
        chain = [m for m in DEFAULT_FALLBACK_ORDER if m != primary]
    return [primary] + chain


def estimate_cost(prompt: str, model: str = "auto", task_type: str = "") -> dict:
    resolved = get_model_for_task(task_type, model)
    prices = PRICE_TABLE.get(resolved, PRICE_TABLE["claude"])
    input_tokens_est = len(prompt) * 1.3
    output_tokens_est = 2000
    cost_est = input_tokens_est * prices["input"] + output_tokens_est * prices["output"]
    return {
        "model": resolved,
        "model_label": prices["label"],
        "input_tokens_est": int(input_tokens_est),
        "output_tokens_est": int(output_tokens_est),
        "cost_est": round(cost_est, 6),
    }


async def ask_ai(prompt: str, model: str = "auto", task_type: str = "", system_prompt: str = "") -> dict:
    resolved_model = get_model_for_task(task_type, model)
    config = _load_config()
    chain = get_fallback_chain(resolved_model)

    last_error = None
    for attempt_model in chain:
        try:
            result = await _call_model(attempt_model, prompt, system_prompt, config)
            if result.get("response", "").startswith("[错误]"):
                last_error = result
                log.warning("Model %s unavailable, trying fallback", attempt_model)
                continue
            if attempt_model != resolved_model:
                result["fallback_from"] = resolved_model
            return result
        except Exception as e:
            last_error = {"response": f"[错误] {attempt_model} 调用失败: {e}", "model": attempt_model, "tokens": 0, "cost": 0}
            log.warning("Model %s failed: %s, trying fallback", attempt_model, e)
            continue

    return last_error or {"response": "[错误] 所有模型均不可用", "model": "none", "tokens": 0, "cost": 0}


async def _call_model(model: str, prompt: str, system_prompt: str, config: dict) -> dict:
    if model == "claude":
        return await _call_claude(prompt, system_prompt, config)
    elif model == "deepseek":
        return await _call_deepseek(prompt, system_prompt, config)
    else:
        return await _call_openai(prompt, system_prompt, config)


async def _call_claude(prompt: str, system_prompt: str, config: dict) -> dict:
    import anthropic

    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置 Anthropic API Key", "model": "claude", "tokens": 0, "cost": 0}

    client = anthropic.AsyncAnthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    kwargs = {"model": "claude-sonnet-4-20250514", "max_tokens": 4096, "messages": messages}
    if system_prompt:
        kwargs["system"] = system_prompt

    resp = await client.messages.create(**kwargs)
    text = resp.content[0].text
    inp, out = resp.usage.input_tokens, resp.usage.output_tokens
    prices = PRICE_TABLE["claude"]
    cost = inp * prices["input"] + out * prices["output"]

    return {"response": text, "model": "claude", "tokens": inp + out, "cost": round(cost, 6)}


async def _call_openai(prompt: str, system_prompt: str, config: dict) -> dict:
    from openai import AsyncOpenAI

    api_key = config.get("openai_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置 OpenAI API Key", "model": "openai", "tokens": 0, "cost": 0}

    client = AsyncOpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    resp = await client.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=4096)
    text = resp.choices[0].message.content
    usage = resp.usage
    prices = PRICE_TABLE["openai"]
    cost = usage.prompt_tokens * prices["input"] + usage.completion_tokens * prices["output"]

    return {"response": text, "model": "openai", "tokens": usage.total_tokens, "cost": round(cost, 6)}


async def _call_deepseek(prompt: str, system_prompt: str, config: dict) -> dict:
    from openai import AsyncOpenAI

    api_key = config.get("deepseek_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置 DeepSeek API Key", "model": "deepseek", "tokens": 0, "cost": 0}

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    resp = await client.chat.completions.create(model="deepseek-chat", messages=messages, max_tokens=4096)
    text = resp.choices[0].message.content
    usage = resp.usage
    prices = PRICE_TABLE["deepseek"]
    cost = usage.prompt_tokens * prices["input"] + usage.completion_tokens * prices["output"]

    return {"response": text, "model": "deepseek", "tokens": usage.total_tokens, "cost": round(cost, 6)}
