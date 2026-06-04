"""平台适配层

每个平台在 platforms/ 下有独立的 .py 文件，
注册到 PLATFORM_REGISTRY 中供通用框架调用。
"""

from __future__ import annotations

from typing import Any

PLATFORM_REGISTRY: dict[str, Any] = {}


def register_platform(name: str, module: Any) -> None:
    PLATFORM_REGISTRY[name] = module


def get_platforms() -> list[str]:
    return sorted(PLATFORM_REGISTRY.keys())


def get_platform_module(name: str) -> Any:
    return PLATFORM_REGISTRY.get(name)


try:
    from web.platforms import bilibili as _bilibili

    register_platform("bilibili", _bilibili)
except ImportError:
    pass
