"""livepipe.tools.user_filter.defaults"""

from __future__ import annotations


# 用户名排除词：品牌 / 4S / 门店 / 车型 + 官方侧姓名（用户给的名单）
DEFAULT_EXCLUDE = [
    "奇瑞", "4s", "店", "qq3", "汽车",
    "尹同跃", "张国忠", "鲍思语", "王津华", "王孝伟", "胡敬源", "尹祥领",
    "王来春", "李晶", "商文江", "杨棉之", "叶盛基", "路风", "黎汝雄",
    "李学用", "张贵兵", "代立宏", "章红玉", "高新华", "戚士龙", "王琅",
]

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# ⚠️ 模型名**会随平台改**，写错就是 404（`Model not exist.` / `model_not_found`），不是 key 的问题。
# 2026-10-12 实测：`deepseek-v4-flash`（10-10 那次跑还是它）已**不在** `GET /models` 的 175 个
# 名字里 ⇒ 换成同一系列的定版快照 `deepseek-v4-flash-0731`（实测 1.8s 稳定回 JSON）。
# 同族可选项：`deepseek-v4.1-flash`（非思考、1.1s、token 更省）、`deepseek-v4-pro`（更强更贵）。
# 换模型前先看一眼现名：`curl -s $BASE_URL/models -H "Authorization: Bearer $KEY"`。
DEFAULT_MODEL = "deepseek-v4-flash-0731"
LABELS = ("正面", "负面", "中性")

DEFAULT_PROMPT = """你在给「奇瑞汽车」直播间/视频的评论做情感标注。

对**每一条**评论判一个标签，取值只能是：正面 / 负面 / 中性

判据：
- 正面：夸车、夸品牌、夸直播、支持/点赞、想买或已买且满意、祝福加油；
- 负面：吐槽、贬低、质疑、骂、嘲讽、阴阳怪气；
- 中性：提问、闲聊、复读无实义内容（如「6666」「哈哈哈」）、看不出态度。

**表情必须用上**（表情已转成文字放在「表情」字段）：
- 内容为空但表情是 [赞]/[比心]/[爱心]/[强]/[玫瑰]/[鼓掌]/[666] 这类 ⇒ 正面；
- [捂脸]/[笑哭] 多是调侃 ⇒ 中性（跟正文一起看，正文是夸就是正面）；
- [微笑]/[呲牙]/[撇嘴] 在直播语境常是敷衍或不满 ⇒ 中性或负面。

只输出 JSON，不要解释，不要 markdown 代码块：
{"0": "正面", "1": "中性", ...}
"""
