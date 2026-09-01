import json
import logging
from pathlib import Path
from app.config import BASE_DIR

CONFIG_FILE = BASE_DIR / "data" / "settings.json"
log = logging.getLogger(__name__)

PRICE_TABLE = {
    "claude": {"input": 0.005 / 1000, "output": 0.025 / 1000, "label": "Claude Opus 5"},
    "openai": {"input": 0.005 / 1000, "output": 0.015 / 1000, "label": "GPT-4o"},
    "deepseek": {"input": 0.00014 / 1000, "output": 0.00028 / 1000, "label": "DeepSeek V3"},
    "qwen": {"input": 0.00011 / 1000, "output": 0.00028 / 1000, "label": "通义千问 Plus"},
}

CLAUDE_MODEL = "claude-opus-5"

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _claude_text(content) -> str:
    """从 Claude 响应里取正文。

    Opus 5 默认开着自适应思考，content 里会混进 thinking 块，
    直接取 content[0].text 会拿到思考块（没有 .text 属性）而炸。
    只收 type=="text" 的块，按顺序拼起来。
    """
    return "".join(b.text for b in content if getattr(b, "type", "") == "text")

DEFAULT_FALLBACK_ORDER = ["claude", "openai", "deepseek", "qwen"]


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

    # 自动选择：优先选已配置 Key 的模型，默认 deepseek（最便宜）
    default = config.get("default_ai_model", "")
    if default:
        return default
    # 按已配置 Key 的优先级自动选
    if config.get("deepseek_api_key"):
        return "deepseek"
    if config.get("qwen_api_key"):
        return "qwen"
    if config.get("anthropic_api_key"):
        return "claude"
    if config.get("openai_api_key"):
        return "openai"
    return "deepseek"


def get_fallback_chain(primary: str) -> list[str]:
    config = _load_config()
    custom_order = config.get("fallback_order", [])
    if custom_order:
        chain = [m for m in custom_order if m != primary]
    else:
        chain = [m for m in DEFAULT_FALLBACK_ORDER if m != primary]
    full = [primary] + chain

    # 只保留有配置 API Key 的模型，避免触发"未配置 Key"的错误
    key_map = {
        "claude": "anthropic_api_key",
        "openai": "openai_api_key",
        "deepseek": "deepseek_api_key",
        "qwen": "qwen_api_key",
    }
    available = [m for m in full if config.get(key_map.get(m, ""), "")]
    # 如果一个都没配置，至少返回主模型让它走错误提示
    return available or [primary]


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


# 单次请求最大字符数（约 10-15 万 tokens）。超过直接拦截，防止天价账单
MAX_PROMPT_CHARS = 300_000


async def ask_ai(prompt: str, model: str = "auto", task_type: str = "", system_prompt: str = "", json_mode: bool = False) -> dict:
    if len(prompt) > MAX_PROMPT_CHARS:
        est_tokens = len(prompt) // 3
        return {
            "response": f"[费用保护] 本次请求过大（{len(prompt):,} 字符，约 {est_tokens:,} tokens），已拦截未发送。请缩小范围或分批处理。",
            "model": "guard", "tokens": 0, "cost": 0,
        }
    resolved_model = get_model_for_task(task_type, model)
    config = _load_config()
    chain = get_fallback_chain(resolved_model)

    last_error = None
    for attempt_model in chain:
        try:
            result = await _call_model(attempt_model, prompt, system_prompt, config, json_mode=json_mode)
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


# 识图能用的三家（DeepSeek 不支持）。值是 (调用函数名, 该家的 key 字段, 报错时显示的名字)。
# 顺序即 auto 时的优先级：**千问排第一**——它是三家里最便宜的识图模型，
# 跟 get_model_for_task 里「auto 选最便宜」是同一个原则。要更准就去设置页显式指定。
_VISION_ORDER = ["qwen", "claude", "openai"]
_VISION_KEY = {"qwen": "qwen_api_key", "claude": "anthropic_api_key", "openai": "openai_api_key"}
_VISION_NAME = {"qwen": "通义千问", "claude": "Claude", "openai": "OpenAI"}


def _vision_caller(name: str):
    """延迟取调用函数——这几个 _call_*_vision 定义在本函数下面。"""
    return {"qwen": _call_qwen_vision, "claude": _call_claude_vision,
            "openai": _call_openai_vision}[name]


async def ask_ai_vision(prompt: str, images: list[dict], system_prompt: str = "") -> dict:
    """识图调用。images: [{"media_type": "image/png", "data": "<base64字符串>"}]

    选哪家：设置页「AI 路由规则 → 识图」显式指定的优先（routes['vision']），
    没指定就按 _VISION_ORDER（千问 → Claude → OpenAI）挑第一个配了 Key 的。
    指定的那家没配 Key 或调用失败时，继续往后退，不直接报死。
    """
    config = _load_config()
    routed = (config.get("routes") or {}).get("vision") or "auto"

    chain = [m for m in _VISION_ORDER if config.get(_VISION_KEY[m])]
    if routed != "auto" and routed in _VISION_KEY and config.get(_VISION_KEY[routed]):
        chain = [routed] + [m for m in chain if m != routed]
    if not chain:
        return {
            "response": "[错误] 图片分析需要 通义千问、Claude 或 OpenAI 的 API Key"
                        "（DeepSeek 不支持识图）。请到「设置」页面配置任意一家的 Key 后再试。",
            "model": "none", "tokens": 0, "cost": 0,
        }

    last = ""
    for name in chain:
        try:
            return await _vision_caller(name)(prompt, images, system_prompt, config)
        except Exception as e:
            last = f"{_VISION_NAME[name]} 识图调用失败: {e}"
            log.warning("%s vision failed: %s", name, e)
    return {"response": f"[错误] {last}", "model": chain[-1], "tokens": 0, "cost": 0}


async def _call_qwen_vision(prompt: str, images: list[dict], system_prompt: str, config: dict) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config["qwen_api_key"], base_url=QWEN_BASE_URL)
    content = []
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"}})
    content.append({"type": "text", "text": prompt})
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    resp = await client.chat.completions.create(model="qwen-vl-plus", messages=messages, max_tokens=4096)
    text = resp.choices[0].message.content
    usage = resp.usage
    # qwen-vl-plus 约 ¥1.5/1M 输入、¥4.5/1M 输出
    cost = usage.prompt_tokens * (0.00021 / 1000) + usage.completion_tokens * (0.00062 / 1000)
    return {"response": text, "model": "qwen-vl(识图)", "tokens": usage.total_tokens, "cost": round(cost, 6)}


async def _call_claude_vision(prompt: str, images: list[dict], system_prompt: str, config: dict) -> dict:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=config["anthropic_api_key"])
    content = []
    for img in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]},
        })
    content.append({"type": "text", "text": prompt})
    kwargs = {"model": CLAUDE_MODEL, "max_tokens": 4096, "messages": [{"role": "user", "content": content}]}
    if system_prompt:
        kwargs["system"] = system_prompt

    resp = await client.messages.create(**kwargs)
    text = _claude_text(resp.content)
    inp, out = resp.usage.input_tokens, resp.usage.output_tokens
    prices = PRICE_TABLE["claude"]
    cost = inp * prices["input"] + out * prices["output"]
    return {"response": text, "model": "claude(识图)", "tokens": inp + out, "cost": round(cost, 6)}


async def _call_openai_vision(prompt: str, images: list[dict], system_prompt: str, config: dict) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config["openai_api_key"])
    content = []
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"}})
    content.append({"type": "text", "text": prompt})
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    resp = await client.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=4096)
    text = resp.choices[0].message.content
    usage = resp.usage
    prices = PRICE_TABLE["openai"]
    cost = usage.prompt_tokens * prices["input"] + usage.completion_tokens * prices["output"]
    return {"response": text, "model": "openai(识图)", "tokens": usage.total_tokens, "cost": round(cost, 6)}


async def _call_model(model: str, prompt: str, system_prompt: str, config: dict, json_mode: bool = False) -> dict:
    if model == "claude":
        return await _call_claude(prompt, system_prompt, config)  # Claude 不支持 response_format，靠 prompt 控制
    elif model == "deepseek":
        return await _call_deepseek(prompt, system_prompt, config, json_mode=json_mode)
    elif model == "qwen":
        return await _call_qwen(prompt, system_prompt, config, json_mode=json_mode)
    else:
        return await _call_openai(prompt, system_prompt, config, json_mode=json_mode)


async def _call_qwen(prompt: str, system_prompt: str, config: dict, json_mode: bool = False) -> dict:
    from openai import AsyncOpenAI

    api_key = config.get("qwen_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置通义千问 API Key", "model": "qwen", "tokens": 0, "cost": 0}

    client = AsyncOpenAI(api_key=api_key, base_url=QWEN_BASE_URL)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs = dict(model="qwen-plus", messages=messages, max_tokens=4096)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content
    usage = resp.usage
    prices = PRICE_TABLE["qwen"]
    cost = usage.prompt_tokens * prices["input"] + usage.completion_tokens * prices["output"]
    return {"response": text, "model": "qwen", "tokens": usage.total_tokens, "cost": round(cost, 6)}


async def _call_claude(prompt: str, system_prompt: str, config: dict) -> dict:
    import anthropic

    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置 Anthropic API Key", "model": "claude", "tokens": 0, "cost": 0}

    client = anthropic.AsyncAnthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    kwargs = {"model": CLAUDE_MODEL, "max_tokens": 4096, "messages": messages}
    if system_prompt:
        kwargs["system"] = system_prompt

    resp = await client.messages.create(**kwargs)
    text = _claude_text(resp.content)
    inp, out = resp.usage.input_tokens, resp.usage.output_tokens
    prices = PRICE_TABLE["claude"]
    cost = inp * prices["input"] + out * prices["output"]

    return {"response": text, "model": "claude", "tokens": inp + out, "cost": round(cost, 6)}


async def _call_openai(prompt: str, system_prompt: str, config: dict, json_mode: bool = False) -> dict:
    from openai import AsyncOpenAI

    api_key = config.get("openai_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置 OpenAI API Key", "model": "openai", "tokens": 0, "cost": 0}

    client = AsyncOpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs = dict(model="gpt-4o", messages=messages, max_tokens=4096)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content
    usage = resp.usage
    prices = PRICE_TABLE["openai"]
    cost = usage.prompt_tokens * prices["input"] + usage.completion_tokens * prices["output"]

    return {"response": text, "model": "openai", "tokens": usage.total_tokens, "cost": round(cost, 6)}


async def _call_deepseek(prompt: str, system_prompt: str, config: dict, json_mode: bool = False) -> dict:
    from openai import AsyncOpenAI

    api_key = config.get("deepseek_api_key", "")
    if not api_key:
        return {"response": "[错误] 未配置 DeepSeek API Key", "model": "deepseek", "tokens": 0, "cost": 0}

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs = dict(model="deepseek-v4-flash", messages=messages, max_tokens=4096)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content
    usage = resp.usage
    prices = PRICE_TABLE["deepseek"]
    cost = usage.prompt_tokens * prices["input"] + usage.completion_tokens * prices["output"]

    return {"response": text, "model": "deepseek", "tokens": usage.total_tokens, "cost": round(cost, 6)}
