"""参数字段注册表

各平台定义各自的专有字段(存储于 extra JSON),
注册到这里后 Analyzer 自动按中文名显示。

用法::
    register("bilibili", "anime_stat", [
        ExtraField("coins", "投币数"),
    ])
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExtraField:
    """平台专有字段定义"""

    key: str = ""
    label: str = ""


# 注册表: {platform: {model_type: [ExtraField, ...]}}
_registry: dict[str, dict[str, list[ExtraField]]] = {}


def register(platform: str, model_type: str, fields: list[ExtraField]) -> None:
    """注册平台专有字段

    Args:
        platform: 平台标识(如 "bilibili")
        model_type: 模型类型("anime"/"anime_stat"/"episode"/"episode_stat")
        fields: 字段定义列表
    """
    if platform not in _registry:
        _registry[platform] = {}
    if model_type not in _registry[platform]:
        _registry[platform][model_type] = []
    _registry[platform][model_type].extend(fields)


def get_extra_fields(platform: str, model_type: str) -> list[ExtraField]:
    """获取某平台某模型的专有字段列表"""
    return _registry.get(platform, {}).get(model_type, [])
