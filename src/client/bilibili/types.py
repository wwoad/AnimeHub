"""Bilibili API 响应数据模型

使用 Pydantic 定义 B站 API 返回数据的结构, 提供:
- 自动类型转换与验证
- 字段缺失/异常值的容错处理
- 清晰的文档字符串标注数据来源
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """动画搜索结果条目

    来源: 搜索API response.data.result[media_bangumi].data[]
    """

    season_id: int = Field(default=0, description="动画season_id")
    media_id: int = Field(default=0, description="媒体media_id")
    title: str = Field(default="", description="标题(已去除em标签)")
    season_type_name: str = Field(default="", description="类型名称(如'番剧','国创')")
    styles: str = Field(default="", description="风格标签")
    area: str = Field(default="", description="地区")
    ep_size: int = Field(default=0, description="集数")
    score: float = Field(default=0.0, description="评分")
    user_count: int = Field(default=0, description="评分人数")
    cover: str = Field(default="", description="封面URL")
    index_show: str = Field(default="", description="更新信息(如'全12话')")


class SeasonInfo(BaseModel):
    """动画详情

    来源: 详情API response.result
    包含基本信息和集数列表, episodes/sections 保留原始字典格式,
    由 Pipeline 层负责转换为 ORM 模型。
    """

    season_id: int = Field(description="动画season_id")
    media_id: int = Field(default=0, description="媒体media_id")
    title: str = Field(default="", description="标题")
    cover: str = Field(default="", description="封面URL")
    area: str = Field(default="", description="地区")
    styles: list[str] = Field(default_factory=list, description="风格标签列表")
    season_type: int = Field(default=0, description="类型(1=番剧,4=国创等)")
    rating_score: float = Field(default=0.0, description="评分")
    rating_count: int = Field(default=0, description="评分人数")
    is_finish: bool = Field(default=False, description="是否完结")
    total: int = Field(default=0, description="总集数")
    pub_time: str = Field(default="", description="开播时间(原始字符串)")
    update_weekday: int = Field(default=0, description="更新星期(0=未知)")
    up_mid: int = Field(default=0, description="UP主UID")
    up_name: str = Field(default="", description="UP主昵称")
    subtitle: str = Field(default="", description="副标题")
    share_url: str = Field(default="", description="分享链接")
    evaluate: str = Field(default="", description="简介/描述")
    new_ep_title: str = Field(default="", description="最新一集标题")
    new_ep_id: int = Field(default=0, description="最新一集ep_id")
    episodes: list[dict[str, Any]] = Field(default_factory=list, description="正片集数列表(原始数据)")
    sections: list[dict[str, Any]] = Field(default_factory=list, description="花絮/特别篇列表(原始数据)")


class SeasonStat(BaseModel):
    """动画级统计快照

    来源: 统计API response.result
    包含动画维度的汇总数据(播放量、追番数等)。
    """

    views: int = Field(default=0, description="总播放量")
    follow: int = Field(default=0, description="追番数")
    likes: int = Field(default=0, description="点赞总数")
    coins: int = Field(default=0, description="投币总数")
    danmaku: int = Field(default=0, description="弹幕总数")
    shares: int = Field(default=0, description="分享总数")
    favorite: int = Field(default=0, description="收藏总数")


class VideoStat(BaseModel):
    """单集视频统计

    来源: 视频统计API response.data.stat
    包含单个视频维度的详细数据(播放量、弹幕、评论等)。
    """

    aid: int = Field(default=0, description="AV号")
    bvid: str = Field(default="", description="BV号")
    view: int = Field(default=0, description="播放量")
    danmaku: int = Field(default=0, description="弹幕数")
    reply: int = Field(default=0, description="评论数")
    favorite: int = Field(default=0, description="收藏数")
    coin: int = Field(default=0, description="投币数")
    share: int = Field(default=0, description="分享数")
    like: int = Field(default=0, description="点赞数")
    his_rank: int = Field(default=0, description="历史最高排名")


class CatalogResult(BaseModel):
    """分类索引条目

    来源: 分类索引API response.data.list[]
    用于浏览国创/番剧等分类下的动画列表。
    """

    season_id: int = Field(default=0, description="动画season_id")
    title: str = Field(default="", description="标题")
    cover: str = Field(default="", description="封面URL")
    score: float = Field(default=0.0, description="评分")
    ep_size: int = Field(default=0, description="集数")
    area: str = Field(default="", description="地区")
    season_type_name: str = Field(default="", description="类型名称")
    index_show: str = Field(default="", description="更新信息(如'全12话')")
    styles: str = Field(default="", description="风格标签")
