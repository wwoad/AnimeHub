"""B站平台专有字段定义

注册 B站存储在 extra 中的字段, 供 Analyzer 使用中文名显示。
"""

from client.display import ExtraField, register

# Anime 表 extra 字段
register(
    "bilibili",
    "anime",
    [
        ExtraField("media_id", "媒体ID"),
        ExtraField("season_type", "番剧类型"),
        ExtraField("up_mid", "UP主UID"),
        ExtraField("up_name", "UP主"),
        ExtraField("update_weekday", "更新星期"),
        ExtraField("share_url", "分享链接"),
    ],
)

# AnimeStat 表 extra 字段 (coins已在顶层)
register("bilibili", "anime_stat", [])

# EpisodeStat 表 extra 字段 (coins已在顶层, his_rank已在顶层)
register("bilibili", "episode_stat", [])
