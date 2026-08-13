# -*- coding: utf-8 -*-
"""计价模块：根据模型单价计算 DeepSeek API 调用费用。

单价单位：元 / 百万 tokens。
单价默认按 DeepSeek 官方平峰价内置在 config.json 中，可随时修改；
官方"峰谷分时计价（高峰翻倍）"暂未自动处理，如需可自行调整单价或后续扩展。
"""


def get_price(model: str, config: dict) -> dict:
    """根据模型名取单价；遇到未配置的模型时按兜底模型计价，避免漏计费。"""
    models = config.get("models", {})
    if model in models:
        return models[model]
    # 未知模型：按兜底模型计价（可在 config.json 中自行补充新模型及单价）
    fallback = config.get("unknown_model_fallback", "")
    return models.get(fallback, {"cache_hit": 0.0, "cache_miss": 0.0, "output": 0.0})


def calc_usage(usage: dict) -> tuple:
    """从 usage 中取出三类 token 数：(缓存命中, 缓存未命中, 输出)。"""
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    return hit, miss, completion


def calc_cost(usage: dict, price: dict) -> float:
    """计算一次调用的费用 = 命中数*命中单价 + 未命中数*未命中单价 + 输出数*输出单价。"""
    hit, miss, completion = calc_usage(usage)
    cost = (
        hit * float(price.get("cache_hit", 0.0))
        + miss * float(price.get("cache_miss", 0.0))
        + completion * float(price.get("output", 0.0))
    ) / 1_000_000.0
    return round(cost, 6)