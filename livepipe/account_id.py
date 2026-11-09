# -*- coding: utf-8 -*-
"""交付账号的身份 = **昵称**，或 **(昵称, 等级标)** —— 由平台档案的 ``account_key`` 决定。

三档：``username``（只看昵称）/ ``nickname_level``（一律带等级）/
``nickname_level_if_masked``（只有**打码昵称**带等级，不打码的仍只看昵称）。

为什么不能只按昵称
------------------
抖音 App 素材里昵称是**打码的**（屏幕上就写着 `X***`，见 ``livepipe/风云T7.json`` 的平台
档案），同一个显示名底下是几十个不同的人。昵称相同**不能**说明是同一个人；唯一还能挂到
人身上的证据是粉丝团等级药丸 ``◆N`` —— 它和昵称一起画、跟着人走，所以：

    同一个昵称 + 同一个 N  ⇒ 算**一个**交付账号
    同一个昵称 + 不同的 N  ⇒ **两个**账号（名字一样，人不一样）

用户 2026-10-14 定：「等级标一致才是一个用户」。这条判据在管线里用两次 ——
解析层拿它归一行首脏读法（``parse_live_comments.normalize_badge_alias``：`22阳***`+等级 22
⇒ 就是 `阳***`），交付层拿它聚合账号（本模块 + ``user_filter`` / ``merge_segments``）。

读不到等级的评论**单独成一档** ``(昵称, "")``：既不并进读到的那个等级，也不因此丢掉 ——
与解析层同一个取舍：**宁可漏归一，不可错归并**。徽章块的检出率只有一半上下（实测两个
150 s 切片：`out/dyapp_v6y` 108 条里 48 条有读数 = 44%、`out/dyapp_v7y` 218 条里 112 条 = 51%），
所以这一档不小，是**选择过**的代价：把「这个人到底几级」赌在一处 OCR 读数上，
赌错就是把别人的评论记到他名下。

开关在**平台档案**里
--------------------
``platforms.<档案名>.account_key`` 有三种取值：

``"username"``（默认）
    只看昵称。昵称完整、没有打码的素材都走它。
``"nickname_level"``
    **一律**按 (昵称, 等级标) 认人 —— 整个版式的昵称都打码时用（风云T7 那套）。
``"nickname_level_if_masked"``（2026-11-08 新增）
    **打码的昵称**按 (昵称, 等级标) 认人，**没打码的昵称**仍只看昵称。
    一场直播里两种人混着出现（视频号：`爱**` 打码、`微风许甜` 不打码；
    快手/抖音有的场次只有部分观众被打码）时用这条：
      · 打码昵称 `爱**` 底下可能是几十个人 ⇒ 必须靠等级标区分，等级标不一致算两个人；
      · 不打码昵称本身唯一 ⇒ 不能因为 OCR 把等级读错/漏读就把同一个人拆成两个账号。

匿名（打码）昵称的**名字**怎么算一样
----------------------------------
打码名里露出来的只有星号前那一小截（通常一个字）。用户 2026-11-08 定：这一截必须
**逐字 100% 相同**（`小***` 与 `健***` 是两个名字；`10双***` 剥掉药丸 `10` 后是 `双***`），
星号个数被 OCR 读成不同不算差异（`小***` / `小**` 是同一个可见名）。名字相同之后，
还要**等级标一致**（两边都读到且相同）才算同一个人 —— 有一边没读到就不并。
所以交付层的认人是「可见部分逐字相同 + 等级标相同」；形近、大小写、共用正文都不算证据。

不写（默认）就只看昵称 —— 也就是改动前的老行为，逐字节不变。这样微博 / 视频号 /
抖音网页版（昵称完整、没有等级药丸）一个字节都不动，只有抖音 App 这类版式开。
"""
from __future__ import annotations

import re
import unicodedata

#: 只看昵称（**默认**）：昵称唯一的素材都走它。
KEY_USERNAME = "username"
#: 一律 (昵称, 等级标)：整个版式的昵称都打码（抖音 App + 粉丝团药丸）。
KEY_NICKNAME_LEVEL = "nickname_level"
#: **打码的**昵称按 (昵称, 等级标)，不打码的仍只看昵称（同一场里两种混着出现）。
KEY_NICKNAME_LEVEL_IF_MASKED = "nickname_level_if_masked"

#: 档案里 ``account_key`` 的合法取值（写错了要当场报，不能静默退回旧口径）。
KEY_CHOICES = (KEY_USERNAME, KEY_NICKNAME_LEVEL, KEY_NICKNAME_LEVEL_IF_MASKED)

#: 平台打码的写法：昵称**末尾**一串星号（`用***` / `爱**` / `乐**` / 全角 `＊`）。
#: 只认末尾，避免把真实昵称中间偶尔出现的 `*` 误判成打码。
_MASKED_RE = re.compile(r"[*＊]\s*$")
#: 末尾那串星号（可能多位、全角混写）——``visible_name`` 用它把遮挡符剥掉。
_MASKED_TAIL_RE = re.compile(r"[*＊]+\s*$")


def is_masked(username: str | None) -> bool:
    """这个显示名是不是**平台打码**过的（末尾一串星号）。"""
    return bool(_MASKED_RE.search(str(username or "")))


def visible_name(username: str | None) -> str:
    """打码昵称的**可见部分**（星号之前，通常就是第一个字）：``小***`` → ``小``。

    星号是遮挡符、不是名字：同一个人在不同帧被读成 ``小***`` / ``小**`` 时可见部分一样。
    但 ``小***`` 与 ``健***`` 的可见部分不同 ⇒ **两个名字、两个人**。用户 2026-11-08 定：
    匿名账号先按可见部分逐字 100% 相同认名字，名字相同后再要求等级标一致。
    没打码的昵称原样返回。
    """
    return _MASKED_TAIL_RE.sub("", str(username or "")).strip()


def badge_level(text: str | None) -> str:
    """等级标读数 → 数字串。``◆16`` / ``16`` / 全角 ``１６`` 都读成 ``"16"``；读不出数字 → ``""``。

    与解析层的 ``_badge_digits`` **同一条判据**（NFKC + Unicode 类别 ``Nd``）：药丸数字可能
    是全角、也可能被 OCR 连带咬出 ``◆``/空格/括号，只留数字最稳。
    """
    return "".join(ch for ch in unicodedata.normalize("NFKC", text or "")
                   if unicodedata.category(ch) == "Nd")


def level_of(comment: dict, split_panels: set[str] | frozenset[str] = frozenset(),
             masked_panels: set[str] | frozenset[str] = frozenset()) -> str:
    """一条评论的等级标 —— **只有**它所在的面板开了按等级标认人时才取。

    逐**面板**判而不是逐段判：一屏里可能同时有抖音 App 面板和视频号面板（参考语料的
    双栏素材就是），后者的评论不该因为隔壁面板的版式事实被拆账号。

    ``masked_panels``：这些面板走 ``nickname_level_if_masked`` —— 只有**打码昵称**
    才取等级标，没打码的评论一律 ``""``（身份就只是昵称本身）。
    """
    panel = str(comment.get("panel"))
    if panel not in split_panels:
        return ""
    if panel in masked_panels and not is_masked(comment.get("username")):
        return ""
    return badge_level(comment.get("badge_text"))


def account_key(username: str | None, level: str | None = None) -> tuple[str, str]:
    """交付账号的身份键 ``(昵称, 等级标)``（等级标读不到就是 ``""``）。

    老 ``users.jsonl`` 没有 ``level`` 字段 ⇒ 键是 ``(昵称, "")``，与旧口径**完全一致**，
    所以已经跑出来的中间产物还能接着用（qualify/collect 不用重跑 filter）。
    """
    return (str(username or ""), str(level or ""))


def display(username: str | None, level: str | None = None) -> str:
    """给人看的账号名：``用***◆16``；没读到等级就只是昵称（与旧名字逐字节相同）。

    目录名走 :func:`sanitize` 之后 ``*`` 会变成 ``_``，所以交付树里长这样：``用___◆16-3``。
    """
    name = str(username or "")
    lv = str(level or "")
    return f"{name}◆{lv}" if lv else name


# --------------------------------------------------------------------------- #
# 自检（纯函数，不读盘）
# --------------------------------------------------------------------------- #

def selftest() -> int:
    n = 0

    def eq(got, want, what: str) -> None:
        nonlocal n
        n += 1
        assert got == want, f"{what}: 得到 {got!r}，期望 {want!r}"

    # 读数：数字以外的一律不要（药丸被咬成括号/带 ◆/全角都算）
    eq(badge_level("16"), "16", "纯数字")
    eq(badge_level("◆16"), "16", "带 ◆")
    eq(badge_level("１６"), "16", "全角")
    eq(badge_level(" ◆ 3 "), "3", "带空白")
    eq(badge_level("213"), "213", "三位数（等级可以到 3 位）")
    eq(badge_level("）"), "", "括号：读不出等级")
    eq(badge_level(None), "", "没读到")
    eq(badge_level(""), "", "空串")

    # 身份键与显示名
    eq(account_key("用***", "16"), ("用***", "16"), "键 = (昵称, 等级)")
    eq(account_key("用***", None), ("用***", ""), "没等级 → 空串（与老 users.jsonl 同键）")
    eq(account_key("用***"), ("用***", ""), "level 缺省")
    eq(account_key(None, None), ("", ""), "没昵称")
    eq(display("用***", "16"), "用***◆16", "显示名带等级")
    eq(display("用***", ""), "用***", "没等级 → 就是昵称（与旧名字逐字节相同）")
    eq(display("用***"), "用***", "level 缺省")

    # 逐面板取等级：没开的面板一律 ""
    cs = [{"panel": "comments", "badge_text": "◆16"},
          {"panel": "right", "badge_text": "◆16"},
          {"panel": "comments", "badge_text": None}]
    eq(level_of(cs[0], {"comments"}), "16", "开了的面板取读数")
    eq(level_of(cs[1], {"comments"}), "", "没开的面板不取（隔壁版式不牵连）")
    eq(level_of(cs[2], {"comments"}), "", "开了但没读数 → 空串")
    eq(level_of(cs[0]), "", "默认不拆面板")
    eq(level_of({}, {"comments"}), "", "没有 panel 字段 → 空串")

    # 打码判定：只认**末尾**星号（全角也认），中间/开头的不算
    eq(is_masked("用***"), True, "三位星号打码")
    eq(is_masked("爱**"), True, "两位星号打码")
    eq(is_masked("乐** "), True, "尾部空白不影响")
    eq(is_masked("A＊"), True, "全角星号")
    eq(is_masked("微风许甜"), False, "正常昵称")
    eq(is_masked("张*三"), False, "中间星号不算打码")
    eq(is_masked("*三"), False, "开头星号不算打码")
    eq(is_masked(""), False, "空名")
    eq(is_masked(None), False, "缺省")

    # 可见部分：星号前那一截才是名字（用户 2026-11-08：匿名按可见部分 100% 相同认人）
    eq(visible_name("小***"), "小", "打码名 → 第一个字")
    eq(visible_name("小**"), "小", "星号个数不同，可见名相同")
    eq(visible_name("10双***"), "10双", "纯数字前缀等别处剥，这里只剥星号")
    eq(visible_name("微风许甜"), "微风许甜", "不打码原样返回")
    eq(visible_name("乐** "), "乐", "尾部空白不影响")
    eq(visible_name(None), "", "缺省")

    # nickname_level_if_masked：打码取等级、不打码一律 ""（身份只按昵称）
    plain = {"panel": "comments", "username": "微风许甜", "badge_text": "◆9"}
    masked = {"panel": "comments", "username": "爱**", "badge_text": "◆9"}
    eq(level_of(plain, {"comments"}, {"comments"}), "", "不打码 → 不取等级")
    eq(level_of(masked, {"comments"}, {"comments"}), "9", "打码 → 取等级")
    eq(level_of(masked, {"comments"}), "9", "只有 split_panels ⇒ 与 nickname_level 同口径")

    print(f"[selftest] 账号身份 (昵称, 等级标) {n} 项断言通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
