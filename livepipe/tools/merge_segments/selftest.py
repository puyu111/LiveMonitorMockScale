"""livepipe.tools.merge_segments.selftest — 跨段账号归一的断言。

跑法：``python3 -m livepipe.tools.merge_segments.selftest``

每条断言都对应一个真实踩过的坑（10.31 视频号/抖音/快手），改判据前先跑一遍。
"""

from __future__ import annotations

from .align import consolidate_users


def _u(name: str, contents=(), *, level: str = "", n: int | None = None) -> dict:
    rows = [{"content": c, "t": float(i)} for i, c in enumerate(contents)]
    return {"username": name, "level": level,
            "n_comments": len(rows) if n is None else n, "comments": rows}


def _run(cases: dict[int, list[dict]], *, promote: bool = False):
    """跑一遍并返回 stat（``promote`` = 按等级认人时把数字药丸前缀补回等级）。"""
    st = consolidate_users(cases, promote_prefix_level=promote)
    return st


def _selftest_align() -> bool:
    ok = True

    def check(name: str, got, want) -> None:
        nonlocal ok
        if got != want:
            ok = False
            print(f"  [align] ✗ {name}: got={got!r} want={want!r}")

    # ① 带数字的药丸前缀：0 条共用正文也认（视频号 +1八重樱 / 八重樱）
    st = _run({1: [_u("+1八重樱", [])], 2: [_u("八重樱", ["a", "b"])]})
    check("①强药丸前缀", st["renames"], {"+1八重樱": "八重樱"})
    check("①强药丸前缀-证据", any("药丸字形前缀" in d for d in st["details"]), True)

    # ② 纯数字前缀 + 共用 1 条正文 ⇒ 并（抖音 1家 / 家，同段 12 秒内同句）
    st = _run({1: [_u("1家", ["支持国产"])], 2: [_u("家", ["支持国产", "大气"])]})
    check("②数字前缀有佐证", st["renames"], {"1家": "家"})

    # ③ 纯数字前缀 + 无佐证 ⇒ 不并（视频号 1李素熵 / 李素熵）
    st = _run({1: [_u("1李素熵", ["x"])], 2: [_u("李素熵", ["y", "z"])]})
    check("③数字前缀无佐证", st["renames"], {})

    # ④ 无数字的标点前缀**不能**单独定案（快手 -风-(23) / 风-(1)，只共用 1 条）
    st = _run({1: [_u("-风-", ["a", "b", "c"], n=23)], 2: [_u("风-", ["a"], n=1)]})
    check("④标点前缀不并", st["renames"], {})

    # ⑤ 标点前缀 + 只差一个字符 + 共用 ≥2 ⇒ 并，主写法按条数（快手 ）0176 / 0176）
    st = _run({1: [_u("）0176", ["a", "b", "c"])], 2: [_u("0176", ["a", "b", "d", "e"])]})
    check("⑤标点前缀+正文", st["renames"], {"）0176": "0176"})

    # ⑥ 货币符号（金币徽章）⇒ 直接认（快手 ￥63358 / 63358）
    st = _run({1: [_u("￥63358", ["a"])], 2: [_u("63358", ["b", "c"])]})
    check("⑥货币符号", st["renames"], {"￥63358": "63358"})

    # ⑦ 等级标硬闸：两边都读到且不一致 ⇒ 不并
    st = _run({1: [_u("+1八重樱", [], level="7")],
               2: [_u("八重樱", ["a", "b"], level="8")]})
    check("⑦等级标不一致", st["renames"], {})

    # ⑧ 仅大小写不同 ⇒ 直接认（快手 Vkgkgnf / vkgkgnf）
    st = _run({1: [_u("Vkgkgnf", ["a"])], 2: [_u("vkgkgnf", ["b"])]})
    check("⑧大小写", st["renames"], {"Vkgkgnf": "vkgkgnf"})

    # ⑨ 只差一个字符但要 ≥2 条共用正文（抖音 谯蕉美食 / 谯谯美食）
    st = _run({1: [_u("谯蕉美食", ["a", "b"])], 2: [_u("谯谯美食", ["a", "b", "c"])]})
    check("⑨形近+正文", st["renames"], {"谯蕉美食": "谯谯美食"})
    st = _run({1: [_u("谯蕉美食", ["a"])], 2: [_u("谯谯美食", ["a", "c"])]})
    check("⑨形近正文不足", st["renames"], {})

    # ⑩ 链式：+1吖完蛋了 / ★1吖完蛋了 / 吖完蛋了 并成一组，主写法是干净名
    st = _run({1: [_u("+1吖完蛋了", ["a"])], 2: [_u("★1吖完蛋了", ["b"])],
               3: [_u("吖完蛋了", ["c", "d"])]})
    check("⑩链式", st["renames"],
          {"+1吖完蛋了": "吖完蛋了", "★1吖完蛋了": "吖完蛋了"})

    # ⑪ 主写法选「被加药丸前缀」的那版，哪怕它评论更少（抖音 7用户6758305）
    st = _run({1: [_u("7用户6758305", ["a", "b"], n=20)],
               2: [_u("用户6758305", ["a", "b", "c"], n=5)]})
    check("⑪主写法", st["renames"], {"7用户6758305": "用户6758305"})

    # ⑫ 改名是**整批跨段**应用：脏名在没取证的那段也要改
    cases = {1: [_u("+1圣芙蕾雅", ["a"])], 2: [_u("圣芙蕾雅", ["b"])],
             3: [_u("+1圣芙蕾雅", ["c"])]}
    _run(cases)
    check("⑫整批改写", [u["username"] for u in cases[3]], ["圣芙蕾雅"])

    # ⑬ 同段合并后正文拼接、计数相加
    cases = {1: [_u("+1星悦", ["a"]), _u("星悦", ["b"])]}
    st = _run(cases)
    check("⑬同段合并", len(cases[1]), 1)
    check("⑬同段合并-正文", [c["content"] for c in cases[1][0]["comments"]], ["a", "b"])
    check("⑬同段合并-计数", (cases[1][0]["n_comments"], st["merged"]), (2, 1))

    # ⑭ 证据键顺序：前缀类脏名 -> 主写法不能显示成「链式」
    st = _run({1: [_u("）0176", ["a", "b", "c"])],
               2: [_u("0176", ["a", "b", "d", "e"])]})
    check("⑭证据不是链式", all("链式" not in d for d in st["details"]), True)

    # ⑮ 完全无关的名字不并
    st = _run({1: [_u("张三", ["a", "b"])], 2: [_u("李四", ["c", "d"])]})
    check("⑮无关名字", st["renames"], {})

    # ⑯ 打码名可见部分不同 ⇒ 共用正文再多也不并（`小***` / `健***` 是两个人）
    st = _run({1: [_u("小***", ["a", "b"], level="7")],
               2: [_u("健***", ["a", "b"], level="7")]})
    check("⑯打码名不同不并", st["renames"], {})

    # ⑰ 打码名可见部分相同（星号个数被 OCR 读错）⇒ 名字一样，但还要等级标一致
    st = _run({1: [_u("小***", ["a", "b"], level="7")],
               2: [_u("小**", ["a", "b"], level="7")]})
    check("⑰打码名星号读错", st["renames"], {"小**": "小***"})
    st = _run({1: [_u("小***", ["a", "b"], level="7")],
               2: [_u("小**", ["a", "b"], level="9")]})
    check("⑰打码名等级不一致", st["renames"], {})
    st = _run({1: [_u("小***", ["a", "b"], level="7")],
               2: [_u("小**", ["a", "b"])]})
    check("⑰打码名等级缺失", st["renames"], {})

    # ⑱ 数字药丸前缀：`15木***` 的 `15` 就是这张行的等级 ⇒ 并进 `木***◆15`
    cases = {1: [_u("15木***", [])], 2: [_u("木***", ["a", "b"], level="15")]}
    st = _run(cases, promote=True)
    check("⑱药丸前缀补等级-改名", st["renames"], {"15木***": "木***"})
    check("⑱药丸前缀补等级-值", cases[1][0]["level"], "15")

    # ⑲ 不按等级认人的老口径：改名照旧，但**不能**补 level（否则 username 口径被拆账号）
    cases = {1: [_u("15木***", [])], 2: [_u("木***", ["a", "b"], level="15")]}
    st = _run(cases)
    check("⑲老口径不补等级", cases[1][0]["level"], "")
    check("⑲老口径仍改名", st["renames"], {"15木***": "木***"})

    # ⑳ 打码名 100% 逐字比较：大小写不同也是两个名字
    st = _run({1: [_u("C***", ["a", "b"], level="5")],
               2: [_u("c***", ["a", "b"], level="5")]})
    check("⑳打码名大小写", st["renames"], {})

    if ok:
        print("[selftest] 跨段账号归一 28 项断言通过 ✔")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if _selftest_align() else 1)
