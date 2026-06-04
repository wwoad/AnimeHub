"""客户端工厂

根据平台标识动态创建对应平台的 API 客户端实例。
采用懒加载(Lazy Import), 避免加载不需要的客户端模块。

用法::

    async with ClientFactory.create("bilibili") as client:
        info = await client.get_season_info(season_id)

注册新平台::

    ClientFactory.register("iqiyi", "client.iqiyi.client:IqiyiClient")
"""

from __future__ import annotations

import importlib
from typing import Any

from client.base import BaseClient


class ClientFactoryError(Exception):
    """客户端工厂异常"""


class ClientFactory:
    """客户端工厂

    维护平台标识到客户端类的映射, 支持动态注册和懒加载创建。
    """

    _registry: dict[str, str] = {
        "bilibili": "client.bilibili.client:BilibiliClient",
    }

    @classmethod
    def register(cls, platform: str, import_path: str) -> None:
        """注册客户端类

        Args:
            platform: 平台标识(如 "iqiyi")
            import_path: 类的导入路径(如 "client.iqiyi.client:IqiyiClient")
        """
        cls._registry[platform] = import_path

    @classmethod
    def create(cls, platform: str, **kwargs: Any) -> BaseClient:
        """创建平台客户端实例

        Args:
            platform: 平台标识
            **kwargs: 传递给客户端构造函数的参数

        Returns:
            客户端实例

        Raises:
            ClientFactoryError: 不支持的平台或导入失败
        """
        if platform not in cls._registry:
            supported = list(cls._registry.keys())
            raise ClientFactoryError(f"不支持的平台: '{platform}'. 已注册: {supported}")

        import_path = cls._registry[platform]
        module_path, class_name = import_path.rsplit(":", 1)

        try:
            module = importlib.import_module(module_path)
            client_cls = getattr(module, class_name)
            return client_cls(**kwargs)
        except (ImportError, AttributeError) as e:
            raise ClientFactoryError(f"客户端类加载失败: {import_path}. {e}") from e
