"""livepipe.parsing.selftest.fixtures — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations



# --------------------------------------------------------------------------- #
# 8. 自检 / 报告 / CLI
# --------------------------------------------------------------------------- #
EXAMPLE = [
    "1 时死你: 真帅啊",
    "6 暗黑盛战: ❤️",
    "10 胡杨: 👍👍",
    "1 时死你: 👍👍👍👍👍",
    "❤️❤️❤️",                  # 时死你的第二行
    "3 社恐小老虎: 1",
    "灵** 赞了直播",             # 过滤
    "3 xjbd: 👍",
    "1 时死你: 🌹🌹🌹🌹🌹",
    "3 社恐小老虎: 🌹🌹",
    "需先登录，才能开始聊天",      # 过滤
]

EXPECTED = [
    {"username": "时死你", "content": "真帅啊"},
    {"username": "暗黑盛战", "content": "❤️"},
    {"username": "胡杨", "content": "👍👍"},
    {"username": "时死你", "content": "👍👍👍👍👍 ❤️❤️❤️"},
    {"username": "社恐小老虎", "content": "1"},
    {"username": "xjbd", "content": "👍"},
    {"username": "时死你", "content": "🌹🌹🌹🌹🌹"},
    {"username": "社恐小老虎", "content": "🌹🌹"},
]
