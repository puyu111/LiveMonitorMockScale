"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """评论 → 合格用户：按「正面评论数」筛人 + 用户名排除词。

规则（用户 2026-10-08 定）
------------------------
1. **账号 ≥ N 条正面评论**（默认 N=3）才算合格；「正面」由大模型逐条判定，
   **表情也要发给模型** —— 纯表情评论（``content`` 是空串）在直播里很常见，
   不看 ``emoji_text`` 就等于把这类评论全判成中性。
2. **账号命中排除词的一律排除**：品牌/4S/门店/车型（奇瑞、4s、店、qq3、汽车）
   + 一列高管与主播姓名 —— 这些是官方侧账号，不是「路人」。

**「账号」是谁**：默认认昵称；面板开了按等级标认人时认 **(昵称, 等级标)**。
两种口径（平台档案 ``account_key`` 自动填，也可手工给 ``--identity-panels``）：
- ``nickname_level``：**一律**拆等级 —— 抖音 App 的昵称打码成 ``X***``，几十个人共用
  一个显示名，只有粉丝团药丸 ``◆N`` 还能把人分开；
- ``nickname_level_if_masked``：**只有打码昵称**（末尾星号，``爱**``）拆等级，
  不打码昵称（``微风许甜``）只看昵称 —— 一场里匿名/非匿名混着出现时用（``--identity-masked``）。

打码昵称的**名字**怎么算同一个（2026-11-08 用户定）：可见部分（星号之前那一截，通常就是
第一个字，``account_id.visible_name()``）必须**逐字 100% 相同** —— ``小***`` 与 ``健***`` 是
两个人；星号个数被 OCR 读错不算差异（``小***`` / ``小**`` 同名）。名字对上后还要**等级标
一致**（两边都读到且相同）才算同一个人；形近/共用正文对打码名不算证据（跨段归一见
``tools/merge_segments/align.py``，段内见 ``parsing/text/badges.py``）。
细则与取舍见 ``livepipe/account_id.py``。

输入是 ``parse`` 步骤的 ``comments.jsonl``（含 ``username``/``content``/``emoji_text``）。

为什么先按条数预筛
------------------
「≥3 条正面」隐含「≥3 条评论」，所以**评论数 < N 的用户根本不可能合格**，
不必送模型。全片实测 6863 条评论里，只有 2439 条属于「≥3 条评论」的 380 个用户
（其余 3200+ 用户各只有 1~2 条）—— 预筛直接省掉 64% 的调用。

缓存
----
判定结果按 ``(content, emoji_text)`` 的哈希缓存到 ``judgments.jsonl``：直播评论大量
重复（``6666``、``支持`` 之类），重复的只花一次调用；重跑也不再花钱。

用法
----
    python livepipe/user_filter.py --comments out/x/comments.jsonl --out out/x/users.jsonl \\
        --api-key $NLP_API_KEY

    # 只估算要花多少次调用，不真调
    python livepipe/user_filter.py --comments out/x/comments.jsonl --dry-run
"""
