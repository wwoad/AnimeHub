"""Bilibili API 异步客户端

基于 httpx.AsyncClient 实现的 B站数据采集客户端, 使用:
- asyncio.Semaphore 控制并发连接数
- tenacity 实现指数退避重试
- B站自身 429/412 响应作为唯一限速信号, 不设人工速率限制
- Pydantic 模型验证 API 响应

所有方法均为 async, 需在 async 上下文中调用。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from client.base import BaseClient
from config import get_settings
from utils.rate_limiter import RateLimiter

from .types import CatalogResult, SearchResult, SeasonInfo, SeasonStat, VideoStat

logger = logging.getLogger(__name__)

# === B站 API 端点 ===
_BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/all/v2"
_BILIBILI_SEASON_API = "https://api.bilibili.com/pgc/view/web/season"
_BILIBILI_SEASON_STAT_API = "https://api.bilibili.com/pgc/web/season/stat"
_BILIBILI_VIDEO_STAT_API = "https://api.bilibili.com/x/web-interface/view"
_BILIBILI_EP_STAT_API = "https://api.bilibili.com/pgc/season/episode/web/info"
_BILIBILI_INDEX_API = "https://api.bilibili.com/pgc/season/index/result"
_BILIBILI_ONLINE_API = "https://api.bilibili.com/x/player/online/total"

# === 默认请求头 ===
_DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com",
}


class BilibiliClient(BaseClient):
    """B站 API 异步客户端

    不做人工速率限制, 仅用 Semaphore 控制同时连接数。
    429/412 响应由 tenacity 自动退避处理。

    Args:
        max_concurrency: 最大并发连接数, 默认 80
        headers: 自定义请求头(覆盖默认值)
    """

    def __init__(
        self,
        max_concurrency: int | None = None,
        headers: dict[str, str] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        settings = get_settings()
        self._max_concurrency = max_concurrency or settings.max_concurrency
        self._headers = {**_DEFAULT_HEADERS, **(headers or {})}
        if settings.bilibili_cookie_1 and "Cookie" not in self._headers:
            self._headers["Cookie"] = settings.bilibili_cookie_1

        conns = settings.max_connections

        self._limiter = rate_limiter or RateLimiter(rate=10, burst=12)
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=settings.request_timeout,
            limits=httpx.Limits(
                max_connections=conns,
                max_keepalive_connections=min(50, conns),
            ),
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端连接池"""
        await self._client.aclose()

    async def __aenter__(self) -> BilibiliClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._limiter.acquire()
        async with self._semaphore:
            return await self._request(url, params)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    async def _request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(3):
            resp = await self._client.get(url, params=params)

            if resp.status_code == 412:
                logger.warning("触发WAF(412), 等待重试(attempt=%d): url=%s", attempt + 1, url)
                if attempt < 2:
                    await asyncio.sleep(5)
                    continue
                raise RuntimeError(f"WAF(412) persisted after 3 inner retries: {url}")

            if resp.status_code == 429:
                logger.warning("限流(429), 等待重试(attempt=%d): url=%s", attempt + 1, url)
                if attempt < 2:
                    await asyncio.sleep(3)
                    continue
                raise RuntimeError(f"Rate limit(429) persisted after 3 inner retries: {url}")

            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(
                    "API错误: code=%s msg=%s url=%s",
                    data.get("code"),
                    data.get("message"),
                    url,
                )
            return data

        return {"code": -1, "message": "max_retries"}

    # ============================================================
    # 搜索 API
    # ============================================================

    async def search(self, keyword: str) -> list[SearchResult]:
        """搜索动画, 返回所有页的结果

        自动翻页直到无更多结果。
        """
        results: list[SearchResult] = []
        page = 1

        while True:
            data = await self._get(
                _BILIBILI_SEARCH_API,
                params={
                    "keyword": keyword,
                    "search_type": "media_bangumi",
                    "page": page,
                    "pagesize": 20,
                },
            )

            result_list = None
            if data.get("code") == 0 and data.get("data"):
                for item in data["data"].get("result", []):
                    if item.get("result_type") == "media_bangumi":
                        result_list = item
                        break

            if result_list is None:
                break

            entries = result_list.get("data", [])
            if not entries:
                break

            for entry in entries:
                score_info = entry.get("media_score", {})
                results.append(
                    SearchResult(
                        season_id=entry.get("season_id", 0),
                        media_id=entry.get("media_id", 0),
                        title=entry.get("title", "").replace("<em>", "").replace("</em>", ""),
                        season_type_name=entry.get("season_type_name", ""),
                        styles=entry.get("styles", ""),
                        area=entry.get("areas", ""),
                        ep_size=entry.get("ep_size", 0),
                        score=score_info.get("score", 0.0),
                        user_count=score_info.get("user_count", 0),
                        cover=entry.get("cover", ""),
                        index_show=entry.get("index_show", ""),
                    )
                )

            total_pages = data.get("data", {}).get("numPages", 1)
            if page >= total_pages:
                break
            page += 1

        return results

    # ============================================================
    # 动画详情 API
    # ============================================================

    async def get_season_info(self, season_id: int) -> SeasonInfo | None:
        """获取动画详情, 包含基本信息和集数列表

        主接口返回 sections 可能不含 episodes 子数组,
        因此对每个 section 并发调用子接口补全集数。
        """
        data = await self._get(_BILIBILI_SEASON_API, params={"season_id": season_id})
        if data.get("code") != 0:
            return None

        result = data["result"]
        rating = result.get("rating", {})
        up_info = result.get("up_info", {})
        publish = result.get("publish", {})
        styles_raw = result.get("styles", [])
        styles = styles_raw if isinstance(styles_raw, list) else []

        areas = result.get("areas", [])
        area_name = areas[0].get("name", "") if areas else ""

        new_ep = result.get("new_ep") or {}

        sections = result.get("section", [])
        logger.debug(
            "season=%d sections=%d episodes_in_main=%d",
            season_id, len(sections), len(result.get("episodes", [])),
        )

        return SeasonInfo(
            season_id=result["season_id"],
            media_id=result.get("media_id", 0),
            title=result.get("title", ""),
            cover=result.get("cover", ""),
            area=area_name,
            styles=styles,
            season_type=result.get("type", 0),
            rating_score=rating.get("score", 0.0),
            rating_count=rating.get("count", 0),
            is_finish=publish.get("is_finish", 0) == 1,
            total=result.get("total", 0),
            pub_time=publish.get("pub_time", ""),
            update_weekday=publish.get("weekday", 0),
            up_mid=up_info.get("mid", 0),
            up_name=up_info.get("uname", ""),
            subtitle=result.get("subtitle", ""),
            share_url=result.get("share_url", ""),
            evaluate=result.get("evaluate", ""),
            new_ep_title=new_ep.get("index", ""),
            new_ep_id=new_ep.get("id", 0),
            episodes=result.get("episodes", []),
            sections=sections,
        )

    # ============================================================
    # 动画统计 API
    # ============================================================

    async def get_season_stat(self, season_id: int) -> SeasonStat | None:
        """获取动画级统计快照(播放量、追番、点赞等)"""
        data = await self._get(_BILIBILI_SEASON_STAT_API, params={"season_id": season_id})
        if data.get("code") != 0:
            return None
        result = data.get("result", {})
        if not result:
            return None
        return SeasonStat(
            views=result.get("views", 0),
            follow=result.get("follow", 0),
            likes=result.get("likes", 0),
            coins=result.get("coins", 0),
            danmaku=result.get("danmakus", 0),
            shares=result.get("shares", 0),
            favorite=result.get("favorite", 0),
        )

    # ============================================================
    # 单集视频统计 API
    # ============================================================

    async def get_video_stat(self, aid: int | None = None, bvid: str | None = None) -> VideoStat | None:
        """获取单个视频的统计数据(播放量、弹幕、评论等)"""
        params: dict[str, Any] = {}
        if aid:
            params["aid"] = aid
        elif bvid:
            params["bvid"] = bvid
        else:
            return None

        data = await self._get(_BILIBILI_VIDEO_STAT_API, params=params)
        if data.get("code") != 0:
            return None

        stat = data.get("data", {}).get("stat", {})
        if not stat:
            return None

        return VideoStat(
            aid=stat.get("aid", aid or 0),
            bvid=stat.get("bvid", bvid or ""),
            view=stat.get("view", 0),
            danmaku=stat.get("danmaku", 0),
            reply=stat.get("reply", 0),
            favorite=stat.get("favorite", 0),
            coin=stat.get("coin", 0),
            share=stat.get("share", 0),
            like=stat.get("like", 0),
            his_rank=stat.get("his_rank", 0),
        )

    async def get_bangumi_ep_stat(self, ep_id: int) -> dict | None:
        """获取单集统计信息(不会被WAF封禁的 API)"""
        data = await self._get(
            "https://api.bilibili.com/pgc/season/episode/web/info",
            params={"ep_id": ep_id},
        )
        if data.get("code") == 0 and data.get("data", {}).get("stat"):
            return data["data"]["stat"]
        return None

    async def get_bangumi_ep_stats_batch(
        self,
        ep_ids: list[int],
        progress_callback: Any | None = None,
        batch_size: int | None = None,
    ) -> list[dict | None]:
        """批量获取单集统计, 分批执行以保证多动画间的公平调度

        Args:
            ep_ids: 单集ID列表
            progress_callback: 每完成一个请求后调用的回调, 签名 callback()
            batch_size: 每批并发请求数, 默认 max(10, max_concurrency // 3)
        """
        if batch_size is None:
            batch_size = max(15, self._max_concurrency // 2)

        results: list[dict | None] = [None] * len(ep_ids)

        for offset in range(0, len(ep_ids), batch_size):
            batch = ep_ids[offset : offset + batch_size]
            batch_results: list = await asyncio.gather(
                *[self.get_bangumi_ep_stat(ep_id) for ep_id in batch],
                return_exceptions=True,
            )

            for j, r in enumerate(batch_results):
                idx = offset + j
                if isinstance(r, Exception):
                    logger.warning("获取 ep_id=%s 统计失败: %s", batch[j], r)
                else:
                    results[idx] = r
                if progress_callback:
                    progress_callback()

            await asyncio.sleep(0.01)

        return results

    async def get_video_stats_batch(self, aids: list[int]) -> list[VideoStat | None]:
        """并发获取多个视频的统计数据

        使用 asyncio.gather + Semaphore 控制并发数和限速。
        返回列表长度与 aids 相同, 失败的项为 None。
        """
        results: list[VideoStat | None] = [None] * len(aids)

        async def _fetch(index: int, aid: int) -> None:
            try:
                results[index] = await self.get_video_stat(aid=aid)
            except Exception as e:
                logger.warning("获取视频统计失败 aid=%s: %s", aid, e)

        tasks = [_fetch(i, aid) for i, aid in enumerate(aids)]
        await asyncio.gather(*tasks)
        return results

    # ============================================================
    # 分类索引 API
    # ============================================================

    async def get_online_count(
        self,
        aid: int | None = None,
        cid: int | None = None,
        bvid: str | None = None,
    ) -> int:
        """获取视频实时在线人数

        来源: x/player/online/total
        注意: 必须同时传 aid+cid 或 bvid+cid
        """
        params: dict[str, str | int] = {}
        if aid:
            params["aid"] = aid
        if bvid:
            params["bvid"] = bvid
        if cid:
            params["cid"] = cid

        if not params or "cid" not in params:
            return 0

        data = await self._get(_BILIBILI_ONLINE_API, params=params)
        if data.get("code") != 0:
            return 0

        result = data.get("data", {})
        if not result:
            return 0

        total = result.get("total")
        if total is not None:
            try:
                return int(str(total))
            except (ValueError, TypeError):
                pass

        return 0

    async def get_online_counts_batch(
        self,
        video_refs: list[dict],
    ) -> list[int]:
        """批量获取多个视频的在线人数

        Args:
            video_refs: [{"aid": xxx, "cid": xxx, "bvid": "xxx"}, ...]

        Returns:
            与 video_refs 等长的在线人数列表
        """
        results: list[int] = [0] * len(video_refs)

        async def _fetch(index: int, ref: dict) -> None:
            with contextlib.suppress(Exception):
                results[index] = await self.get_online_count(
                    aid=ref.get("aid"),
                    cid=ref.get("cid"),
                    bvid=ref.get("bvid"),
                )

        tasks = [_fetch(i, ref) for i, ref in enumerate(video_refs)]
        await asyncio.gather(*tasks)
        return results

    async def browse_seasons(
        self,
        season_type: int = 4,
        order: int = 2,
        pagesize: int = 50,
    ) -> list[CatalogResult]:
        """浏览分类索引, 自动翻页获取全部结果

        Args:
            season_type: 分类类型 (1=番剧, 4=国创)
            order: 排序方式 (2=追番数, 3=播放量, 5=评分)
            pagesize: 每页条数 (最大50)
        """
        all_results: list[CatalogResult] = []
        page = 1
        _max_safe_page = 500

        while page <= _max_safe_page:
            data = await self._get(
                _BILIBILI_INDEX_API,
                params={
                    "season_type": season_type,
                    "order": order,
                    "page": page,
                    "pagesize": pagesize,
                    "media_list_type": 1,
                    "type": 1,
                },
            )

            if data.get("code") != 0:
                logger.warning("分类索引API返回错误: code=%s msg=%s", data.get("code"), data.get("message"))
                break

            result_data = data.get("data", {})
            items = result_data.get("list", [])
            if not items:
                break

            for item in items:
                all_results.append(
                    CatalogResult(
                        season_id=item.get("season_id", 0),
                        title=item.get("title", "").replace("<em>", "").replace("</em>", ""),
                        cover=item.get("cover", ""),
                        score=item.get("score", 0.0) if item.get("score") else 0.0,
                        ep_size=item.get("ep_size", 0),
                        area=item.get("area", ""),
                        season_type_name=item.get("season_type_name", ""),
                        index_show=item.get("index_show", ""),
                        styles=item.get("styles", "") if isinstance(item.get("styles"), str) else ",".join(item.get("styles", [])),
                    )
                )

            total = result_data.get("total", 0)
            if page * pagesize >= total:
                break
            page += 1

        logger.info("分类索引查询完成: season_type=%d, 共 %d 条", season_type, len(all_results))
        return all_results
