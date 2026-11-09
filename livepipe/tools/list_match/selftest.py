"""撞库自检（零第三方依赖；只在一个临时目录里复制几张假图，不碰真实产物）。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .common import (
    dir_count,
    dir_username,
    display_account,
    is_number,
    match_score,
    normalize_name,
    platform_label,
    platform_sort_key,
    safe_dirname,
    score_norm,
    strip_level,
)
from .html import _match_kind, write_html
from .matching import build_report, match_platform
from .report import export_images
from .sources import (
    _find_header,
    load_judgments,
    load_seg_accounts,
    load_stream_accounts,
    merge_accounts,
    parse_name_table,
    stream_segments,
)


def selftest() -> int:
    n = 0

    def eq(got, want, what):
        nonlocal n
        n += 1
        assert got == want, f"{what}: 得到 {got!r}，期望 {want!r}"

    def close(got, want, what, tol=1e-9):
        nonlocal n
        n += 1
        assert abs(got - want) <= tol, f"{what}: 得到 {got!r}，期望 {want!r}"

    def lt(got, limit, what):
        nonlocal n
        n += 1
        assert got < limit, f"{what}: 得到 {got!r}，应 < {limit!r}"

    # ---- 平台标签 ----
    eq(platform_label("抖音"), "抖音", "抖音")
    eq(platform_label("抖音号"), "抖音", "抖音号")
    eq(platform_label("DouYin_App"), "抖音", "英文档案名 + 大小写")
    eq(platform_label("微信视频号"), "视频号", "微信视频号")
    eq(platform_label("快手号"), "快手", "快手号")
    eq(platform_label("Weibo"), "微博", "Weibo")
    eq(platform_label("西瓜"), None, "不认识的平台")
    eq(platform_sort_key("抖音") < platform_sort_key("视频号") < platform_sort_key("快手"),
       True, "平台顺序 抖音 < 视频号 < 快手")

    # ---- 归一化 ----
    eq(normalize_name("  Ａ B​ "), "ab", "NFKC + 去空白/零宽 + casefold")
    eq(normalize_name("西柚。"), "西柚。", "标点保留")
    eq(normalize_name("岁月如初1213"), "岁月如初1213", "数字保留")
    eq(normalize_name(None), "", "None → 空")
    eq(normalize_name("摸鱼的西红柿🍅"), "摸鱼的西红柿🍅", "emoji 保留（由相似度处理）")

    # ---- 匹配度 ----
    close(match_score("谯谯美食", "谯谯美食"), 1.0, "完全相同")
    close(match_score("期待", "期待sj"), 1.0, "短名被长名包含（截断）")
    close(match_score("期待", "期待sj", "ratio"), 2 / 3, "ratio 不享截断红利", 1e-6)
    close(match_score("章衡9116", "章衡"), 1.0, "长名包含短名")
    close(match_score("喜乐哥", "喜乐哥554"), 1.0, "后缀多出数字")
    close(match_score("👑63358", "63358"), 1.0, "emoji 前缀")
    close(match_score("德莉莎", "德丽莎"), 2 / 3, "差一个字", 1e-6)
    lt(match_score("李素裳", "李素熵"), 0.8, "差一个字的两档不该在 0.8 命中")
    lt(match_score("风", "-风-"), 0.8, "单字名单不做窗口匹配")
    lt(match_score("小小", "小燕子"), 0.8, "不相干短名")
    # 直播全量里 2 字名会在长名里碰巧出现；前后缀多余部分是汉字就不给满
    lt(match_score("天后", "明天后天"), 0.8, "`天后` 不该撞上 `明天后天`")
    lt(match_score("一路顺风", "顺风"), 0.8, "2 字中文后缀不享截断红利")
    close(match_score("希儿", "★1希儿"), 1.0, "徽章前缀（非汉字）可以给满")
    close(match_score("用户", "用户6758305"), 1.0, "数字后缀可以给满")
    close(match_score("将成", "李将成"), 0.8, "汉字前缀只按整体 ratio", 1e-6)
    close(score_norm("", "x"), 0.0, "空串")
    close(match_score("A B", "ab"), 1.0, "空白/大小写不影响")

    # ---- 目录名 / 等级标 ----
    eq(dir_username("6回头不是彼岸呀-4"), "6回头不是彼岸呀", "剥末尾条数")
    eq(dir_username("W-8"), "W", "W-8")
    eq(dir_username("-风--22"), "-风-", "名字里带连字符")
    eq(dir_username("0176-5"), "0176", "数字昵称")
    eq(dir_username("a-b"), "a-b", "没条数尾巴不动")
    eq(dir_count("W-8"), 8, "dir_count")
    eq(dir_count("a-b"), None, "dir_count 无尾巴")
    eq(strip_level("W◆16"), "W", "剥等级标")
    eq(strip_level("W◆"), "W", "空等级标也剥")
    eq(display_account("W", "16"), "W◆16", "display 带等级")
    eq(display_account("W", ""), "W", "display 无等级")
    eq(safe_dirname('a/b:c'), "a_b_c", "目录名非法字符")
    eq(safe_dirname(""), "未命名", "空名单名")
    eq(is_number("0176"), True, "0176 像序号")
    eq(is_number("小明"), False, "小明不像序号")

    # ---- 名单二维表 ----
    rows = [["序号", "抖音", None, "序号", "快手"],
            [1, "小明", None, 1, "小红"],
            [2, "小刚", None, 2, None]]
    g = parse_name_table(rows)
    eq([e["name"] for e in g["抖音"]], ["小明", "小刚"], "column 布局：抖音名字在表头下面")
    eq([e["name"] for e in g["快手"]], ["小红"], "column 布局：快手")
    eq(g["抖音"][0]["seq"], 1, "序号取名字左边一列")
    eq(g["抖音"][0]["row"], 2, "行号 1 起")
    # 表头左边是平台、名字在右边（row 布局）
    rows2 = [["抖音", "小明", "小红"], ["", "", ""]]
    g2 = parse_name_table(rows2)
    eq([e["name"] for e in g2["抖音"]], ["小明", "小红"], "row 布局：名字在表头右边")
    # 某个人就叫「抖音」不能抢当表头：有「序号」的行优先
    rows3 = [["抖音"], ["序号", "抖音"], [1, "小明"]]
    h, labels = _find_header(rows3)
    eq((h, labels), (1, [(1, "抖音")]), "表头行优先取带序号的那行")

    # ---- 平台匹配 ----
    accounts = [
        {"username": "6回头不是彼岸呀", "level": "", "dir": "6回头不是彼岸呀-4",
         "count": 4, "segs": {"seg1": 4}, "first_image": "6回头不是彼岸呀-4/1.jpg",
         "path": Path(".")},
        {"username": "德丽莎", "level": "", "dir": "德丽莎-3", "count": 3,
         "segs": {"seg2": 3}, "first_image": "", "path": Path(".")},
    ]
    entries = [
        {"platform": "抖音", "name": "回头不是彼岸呀", "row": 2, "seq": 1},
        {"platform": "抖音", "name": "德莉莎", "row": 3, "seq": 2},
    ]
    rep = match_platform("抖音", entries, accounts, tree=Path("."),
                         threshold=0.8, metric="partial", max_matches=5)
    eq(rep["list_count"], 2, "名单 2 人")
    eq(rep["matched_count"], 1, "命中 1 人（另一人 0.667）")
    eq(rep["unmatched_count"], 1, "未命中 1 人")
    eq(rep["matches"][0]["account"], "6回头不是彼岸呀", "命中账号")
    close(rep["matches"][0]["score"], 1.0, "命中分数")
    eq(rep["matches"][0]["dir_exists"], True, "目录存在")
    close(rep["unmatched"][0]["best"]["score"], 2 / 3, "未命中留下最近分数", 1e-4)

    # 一个名单名撞出多个账号时，--max-matches 封顶
    accounts3 = [{"username": f"小明{c}", "level": "", "dir": f"小明{c}-1",
                  "count": 1, "segs": {}, "first_image": "", "path": Path(".")}
                 for c in "ABC"]
    rep3 = match_platform("抖音", [{"platform": "抖音", "name": "小明", "row": 1,
                                    "seq": 1}], accounts3, threshold=0.8,
                          max_matches=2)
    eq(len(rep3["matches"]), 2, "max_matches=2")
    eq(rep3["matched_count"], 1, "多条命中仍算 1 个名单名")

    # ---- 报告汇总 ----
    full = build_report({"names": "x.xlsx", "live": "out", "threshold": 0.8,
                         "metric": "partial", "generated_at": "T"}, [rep])
    eq(full["summary"]["list_names"], 2, "汇总名单数")
    eq(full["summary"]["matched_names"], 1, "汇总命中数")
    eq(full["summary"]["matches"], 1, "汇总命中行数")
    eq(full["kind"], "list_match", "报告类型")
    eq(full["platforms"]["抖音"]["platform"], "抖音", "平台报告键")

    # ---- 直播全量 ∪ 交付树：只说过一条评论的人也要撞得出来 ----
    delivered = [{"username": "赵丽丽", "level": "", "dir": "赵丽丽-4", "count": 4,
                  "segs": {"seg1": 4}, "first_image": "", "path": Path("."),
                  "in_delivery": True, "n_comments": None, "n_positive": None}]
    stream = [
        {"username": "赵丽丽", "level": "", "dir": "", "count": None,
         "segs": {"seg1": 5, "seg2": 2}, "first_image": "", "path": None,
         "in_delivery": False, "n_comments": 9, "n_positive": 7},
        {"username": "在干嘛呢", "level": "", "dir": "", "count": None,
         "segs": {"seg1": 1}, "first_image": "", "path": None,
         "in_delivery": False, "n_comments": 1, "n_positive": 1},
    ]
    merged = merge_accounts(delivered, stream)
    eq(len(merged), 2, "合并：交付 ∪ 直播")
    by = {a["username"]: a for a in merged}
    eq(by["赵丽丽"]["in_delivery"], True, "合并：同一人保留交付身份")
    eq(by["赵丽丽"]["dir"], "赵丽丽-4", "合并：交付目录不丢")
    eq(by["赵丽丽"]["n_comments"], 9, "合并：补上评论数")
    eq(by["在干嘛呢"]["in_delivery"], False, "合并：只在直播里")
    rep5 = match_platform(
        "抖音", [{"platform": "抖音", "name": "在干嘛呢", "row": 1, "seq": 3}],
        merged, threshold=0.8)
    eq(rep5["matched_count"], 1, "仅直播也能撞出来")
    eq(rep5["matches"][0]["in_delivery"], False, "命中标注不在交付树")
    eq(rep5["matches"][0]["n_positive"], 1, "命中带正面数")
    eq(rep5["delivered_count"], 1, "平台交付账号数")
    eq(rep5["matched_not_delivered"], 1, "仅直播命中数")

    # ---- 池子按全部评论：负面/中性账号也要进（comments.jsonl + 判定缓存） ----
    from ..user_filter.cache import _key as judge_key  # 与 filter 共用的缓存键

    def cmt(name, content, t=0.0, badge=None):
        return {"username": name, "panel": "comments", "content": content,
                "emoji_text": "", "t": t, "roi": [0, 0, 10, 10],
                "badge_text": badge}

    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "抖音"
        seg1, seg2 = pdir / "seg1", pdir / "seg2"
        seg1.mkdir(parents=True)
        seg2.mkdir(parents=True)
        (seg1 / "users.json").write_text(json.dumps({
            "exclude_keywords": ["官方"], "identity_panels": [],
            "identity_masked": []}, ensure_ascii=False), encoding="utf-8")
        seg1_comments = [cmt("甲", "好看", 1), cmt("甲", "真不错", 2),
                         cmt("乙", "一般吧", 3), cmt("官方号", "欢迎", 4)]
        (seg1 / "comments.jsonl").write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in seg1_comments),
            encoding="utf-8")
        labels = {"好看": "正面", "真不错": "正面", "一般吧": "负面", "欢迎": "正面"}
        (pdir / "judgments.jsonl").write_text(
            "".join(json.dumps({"k": judge_key(k, ""), "label": v},
                               ensure_ascii=False) + "\n"
                    for k, v in labels.items()), encoding="utf-8")
        # seg2 只有 users.jsonl（老产物）：甲 1 条正面
        (seg2 / "users.jsonl").write_text(json.dumps(
            {"username": "甲", "level": "", "n_comments": 1, "n_positive": 1,
             "comments": [{"t": 9, "panel": "comments", "label": "正面",
                           "content": "可以", "emoji_text": "", "roi": [0, 0, 1, 1]}]},
            ensure_ascii=False) + "\n", encoding="utf-8")

        eq(len(stream_segments(pdir)), 2,
           "全量账号：comments.jsonl 与 users.jsonl 的段都算")
        cache = load_judgments(pdir)
        eq(cache[judge_key("一般吧", "")], "负面", "判定缓存读入")
        seg1_users = load_seg_accounts(seg1, cache)
        eq(sorted(seg1_users), [("乙", ""), ("甲", "")], "排除词账号不进池子")
        eq(seg1_users[("乙", "")]["n_positive"], 0, "只发负面也算账号")
        eq(seg1_users[("乙", "")]["n_negative"], 1, "负面计数")
        eq(seg1_users[("甲", "")]["n_comments"], 2, "全部评论计入")
        by = {(a["username"], a["level"]): a for a in load_stream_accounts(pdir)}
        eq(sorted(by), [("乙", ""), ("甲", "")], "跨段合并：comments ∪ users")
        eq(by[("甲", "")]["n_comments"], 3, "跨段评论累加")
        eq(by[("甲", "")]["n_positive"], 3, "跨段正面累加")
        eq(by[("乙", "")]["n_positive"], 0, "负面账号正面数 0")

        # 认人口径跟 filter 走：identity_panels 里开了「按等级认人」就进身份键
        (seg1 / "users.json").write_text(json.dumps({
            "exclude_keywords": [], "identity_panels": ["comments"],
            "identity_masked": []}, ensure_ascii=False), encoding="utf-8")
        (seg1 / "comments.jsonl").write_text(
            json.dumps(cmt("甲", "好看", 1, badge="◆12"), ensure_ascii=False) + "\n",
            encoding="utf-8")
        eq(list(load_seg_accounts(seg1, cache)), [("甲", "12")],
           "按等级认人：等级进身份键")
        eq(load_seg_accounts(seg1, {})[("甲", "12")]["n_positive"], 0,
           "判定缓存缺失按中性算")

    # ---- 素材路径重映射：summary.json 里的旧视频路径失效时 ----
    from .render import _resolve_video
    with tempfile.TemporaryDirectory() as td:
        seg = Path(td) / "seg1"
        seg.mkdir()
        real = Path(td) / "new" / "v.mp4"
        real.parent.mkdir()
        real.write_bytes(b"x")
        old_video = Path(td) / "old" / "v.mp4"
        (seg / "summary.json").write_text(
            json.dumps({"video": str(old_video)}), encoding="utf-8")
        eq(_resolve_video(seg, None), str(old_video),
           "视频路径失效且无重映射：原样返回")
        eq(_resolve_video(seg, [(str(Path(td) / "old") + "/",
                                 str(Path(td) / "new") + "/")]),
           str(real), "视频路径按前缀重映射")

    # ---- 标记图导出（临时目录里复制几张假图） ----
    with tempfile.TemporaryDirectory() as td:
        tree = Path(td) / "tree"
        acct = tree / "小明-3"
        acct.mkdir(parents=True)
        (acct / "1.jpg").write_bytes(b"jpg1")
        (acct / "2.jpg").write_bytes(b"jpg2")
        (acct / "note.txt").write_text("x")
        img_report = {"platforms": {"抖音": {"tree": str(tree), "matches": [
            {"list_name": "小明", "account_dir": "小明-3", "in_delivery": True},
            {"list_name": "小明", "account_dir": "小明-3", "in_delivery": True},  # 重复：只导一次
            {"list_name": "小红", "account_dir": "缺-3", "in_delivery": True},    # 源目录不存在
        ]}}}
        out = Path(td) / "out"
        info = export_images(out, img_report, log=lambda *a: None)
        eq(info["accounts"], 1, "标记图：账号目录数（重复去重）")
        eq(info["images"], 2, "标记图：只数图片")
        eq(info["missing"], 1, "标记图：缺失目录计数")
        base = out / "标记图" / "抖音" / "小明" / "小明-3"
        eq((base / "1.jpg").read_bytes(), b"jpg1", "标记图：原样复制")
        eq((base / "note.txt").exists(), False, "标记图：非图片不复制")

    # ---- HTML 交付清单（临时目录里造一个平台报告） ----
    eq(_match_kind("小明", {"account": "小明", "score": 1.0}), ("exact", "精确同名"),
       "HTML：精确同名")
    eq(_match_kind("期待", {"account": "期待sj", "score": 1.0}), ("norm", "截断同名"),
       "HTML：截断同名")
    eq(_match_kind("小明", {"account": "小铭", "score": 0.5}), ("fuzzy", "疑似 50%"),
       "HTML：疑似")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tree = root / "tree"
        (tree / "小明-3").mkdir(parents=True)
        (tree / "小明-3" / "1.jpg").write_bytes(b"a")
        (tree / "小明-3" / "2.jpg").write_bytes(b"b")
        mark = root / "标记图" / "抖音" / "小明" / "小明-3"
        mark.mkdir(parents=True)
        (mark / "1.jpg").write_bytes(b"a")
        (mark / "2.jpg").write_bytes(b"b")

        def plat(name):
            return {
                "platform": name, "tree": str(tree), "platform_dir": None,
                "list_count": 3, "matched_count": 2, "unmatched_count": 1,
                "accounts_count": 10, "delivered_count": 1,
                "matched_accounts": 2, "matched_not_delivered": 1,
                "matches": [
                    {"list_name": "小明", "account": "小明", "display": "小明",
                     "score": 1.0, "account_dir": "小明-3", "in_delivery": True,
                     "n_comments": 5, "n_positive": 3, "count": 3},
                    {"list_name": "小红<&", "account": "小红", "display": "小红",
                     "score": 0.85, "account_dir": "", "in_delivery": False,
                     "n_comments": 1, "n_positive": 1, "count": None},
                ],
                "unmatched": [{"list_name": "小刚", "list_seq": 3,
                               "best": {"display": "小刚x", "score": 0.75}}],
            }

        report = {
            "generated_at": "2026-11-07T00:00:00+08:00",
            "inputs": {"threshold": 0.8, "scope": "stream", "live": str(root / "live"),
                       "title": "测试场次", "date": "2026-10-31"},
            "platforms": {"抖音": plat("抖音"), "快手": plat("快手")},
        }
        info = write_html(root, report, log=lambda *a: None)
        eq(len(info["files"]), 3, "HTML：两平台各一页 + 汇总")
        names = sorted(p.name for p in info["files"])
        eq(len(names), 3, "HTML：三个文件")
        eq("20261031_测试场次_抖音.html" in names, True, "HTML：文件名带日期/场次/平台")
        eq("20261031_测试场次_快手.html" in names, True, "HTML：第二个平台")
        eq("汇总.html" in names, True, "HTML：多平台写汇总")
        page = next(p for p in info["files"] if "抖音" in p.name).read_text(encoding="utf-8")
        from urllib.parse import unquote
        eq('href="../标记图/抖音/小明/小明-3/1.jpg"' in unquote(page), True,
           "HTML：首图相对链接")
        eq("小红&lt;&amp;" in page, True, "HTML：名字转义")
        eq("共 2 个账号（2 人）" in page, True, "HTML：命中表标题")
        eq("未命中名单 · 共 1 人" in page, True, "HTML：未命中表")

    print(f"自检 {n} 项全过 ✅")
    return 0
