"""livepipe.tools.export_user_images.selftest"""

from __future__ import annotations

from .common import expand_box
from .common import frame_index
from .common import frame_name
from .common import parse_color
from .common import pick_comments
from .common import platform_dir
from .common import platform_of
from .common import sanitize
from .common import unique_dir
from .select import select



# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #

def selftest() -> int:
    n = 0

    def eq(got, want, what):
        nonlocal n
        n += 1
        assert got == want, f"{what}: 得到 {got!r}，期望 {want!r}"

    # 文件名必须和 draw_frames 同口径
    eq(frame_name(0.0), "00000.0_roi.jpg", "frame_name(0)")
    eq(frame_name(91.68724016923025), "00091.7_roi.jpg", "frame_name(91.687)")
    eq(frame_name(6294.618), "06294.6_roi.jpg", "frame_name(6294.618)")

    # t → 帧计数器（与 sample_frames_adaptive / draw_frames 同一把尺子）
    eq(frame_index(0.0, 4.8425), 0, "idx(0)")
    eq(frame_index(91.68724016923025, 4.8425), 444, "idx(91.687) = 444")
    eq(frame_index(206.9, 4.8425), 1002, "idx(206.9) = 1002")

    # 平台目录名
    eq(platform_dir("wechat"), "视频号", "wechat→视频号")
    eq(platform_dir("douyin"), "抖音", "douyin→抖音")
    eq(platform_dir("WeChat"), "视频号", "大小写不敏感")
    eq(platform_dir("weibo"), "微博", "weibo→微博")
    eq(platform_dir("Weibo"), "微博", "weibo 大小写不敏感")
    eq(platform_dir("kuaishou"), "快手", "kuaishou")
    eq(platform_dir("kuaishou_app"), "快手", "kuaishou_app")
    eq(platform_dir(""), "未标平台", "空")
    eq(platform_dir(None), "未标平台", "None")
    eq(platform_dir("myapp"), "myapp", "不认识的平台原样用")

    # 目录名清洗
    eq(sanitize("肘死你"), "肘死你", "普通中文")
    eq(sanitize("A/B:C*D?E"), "A_B_C_D_E", "非法字符")
    eq(sanitize("  天空  "), "天空", "两端空白")
    eq(sanitize("天空."), "天空", "结尾点")
    eq(sanitize(""), "匿名", "空名")
    eq(sanitize(None), "匿名", "None")
    eq(sanitize("a" * 80).startswith("a" * 51), True, "超长截断")
    eq(sanitize("a" * 80) != sanitize("b" * 80), True, "截断后不撞名")
    eq(sanitize("🦄淘气💨"), "🦄淘气💨", "emoji 保留")

    # 目录去重
    used: set[str] = set()
    eq(unique_dir(used, "张三-3"), "张三-3", "首个")
    eq(unique_dir(used, "张三-3"), "张三-3-2", "第二个")
    eq(unique_dir(used, "张三-3"), "张三-3-3", "第三个")

    # 颜色
    eq(parse_color("0,255,0"), (0, 255, 0), "颜色")
    eq(parse_color(" 0 255 0 "), (0, 255, 0), "颜色（空格分隔）")

    # 画框留白：外扩 = max(pad, ratio×行高)，并夹在画面内
    big = (4000, 4000)
    eq(expand_box([10, 20, 110, 50], 0, 0.0, big), (10, 20, 110, 50), "紧贴（ratio=0）")
    eq(expand_box([10, 20, 110, 50], 6, 0.0, big), (4, 14, 116, 56), "固定像素外扩")
    eq(expand_box([10, 20, 110, 50], 0, 0.25, big), (2, 12, 118, 58), "行高 30 ⇒ 外扩 8")
    eq(expand_box([10, 20, 110, 50], 6, 0.1, big), (4, 14, 116, 56), "pad 当下限（6 > 3）")
    eq(expand_box([10, 20, 110, 50], 0, 0.25, (400, 300)), (2, 12, 118, 58), "小图照样外扩")
    eq(expand_box([0, 0, 30, 30], 6, 0.0, big), (0, 0, 36, 36), "左上越界夹到 0")
    eq(expand_box([370, 270, 400, 300], 6, 0.0, (400, 300)), (364, 264, 399, 299),
       "右下越界夹到 w-1/h-1（否则 PIL 把这条边静默裁掉）")
    eq(expand_box([100, 40, 10, 20], 0, 0.0, big), (10, 20, 100, 40), "坐标反序先归一")
    eq(expand_box([10.4, 20.6, 110.5, 50.4], 0, 0.0, big), (10, 21, 110, 50), "浮点取整")

    # 选评论：只留命中标签的，按 t 升序
    rec = {"username": "u", "comments": [
        {"t": 9.0, "label": "正面", "roi": [1, 2, 3, 4], "panel": "right"},
        {"t": 2.0, "label": "中性", "panel": "right"},
        {"t": 5.0, "label": "正面", "panel": "right"},
        {"t": 7.0, "label": "负面", "panel": "right"},
    ]}
    got = pick_comments(rec, {"正面"})
    eq([c["t"] for c in got], [5.0, 9.0], "只留正面且按 t 升序")
    eq(len(pick_comments(rec, {"正面", "中性"})), 3, "多标签")
    eq(len(pick_comments({"username": "u"}, {"正面"})), 0, "没有 comments 字段")

    # 分平台 + 阈值口径：一个账号在抖音 2 条、视频号 2 条
    pp = {"left": "douyin", "right": "wechat"}
    both = {"username": "跨平台", "comments": [
        {"t": 1.0, "label": "正面", "panel": "left"}, {"t": 2.0, "label": "正面", "panel": "left"},
        {"t": 3.0, "label": "正面", "panel": "right"}, {"t": 4.0, "label": "正面", "panel": "right"}]}
    only_left = {"username": "只抖音", "comments": [
        {"t": 1.0, "label": "正面", "panel": "left"}, {"t": 2.0, "label": "正面", "panel": "left"},
        {"t": 3.0, "label": "正面", "panel": "left"}]}
    only_right = {"username": "只视频号", "comments": [
        {"t": 1.0, "label": "正面", "panel": "right"}, {"t": 2.0, "label": "正面", "panel": "right"},
        {"t": 3.0, "label": "正面", "panel": "right"}]}
    unk = {"username": "没面板", "comments": [
        {"t": 1.0, "label": "正面"}, {"t": 2.0, "label": "正面"}, {"t": 3.0, "label": "正面"}]}
    all4 = [both, only_left, only_right, unk]
    eq(platform_of(both["comments"][0], pp), "douyin", "left→douyin")
    eq(platform_of(both["comments"][2], pp), "wechat", "right→wechat")
    eq(platform_of(unk["comments"][0], pp), "未标平台", "没 panel → 未标平台")

    names = lambda d: sorted(r["username"] for r, _ in d)      # noqa: E731
    sel = select(all4, pp, {"正面"}, 3, "platform")
    eq(sorted(sel), ["douyin", "wechat", "未标平台"], "三个平台组都在")
    eq(names(sel["douyin"]), ["只抖音"], "platform 口径：跨平台账号两边都只有 2 条 ⇒ 抖音侧出局")
    eq(names(sel["wechat"]), ["只视频号"], "视频号侧同理")
    eq(names(sel["未标平台"]), ["没面板"], "没有 panel 的按 未标平台 计数")

    selg = select(all4, pp, {"正面"}, 3, "global")
    eq(names(selg["douyin"]), ["只抖音", "跨平台"], "global 口径：跨平台账号两边都进")
    eq(names(selg["wechat"]), ["只视频号", "跨平台"], "global 口径：视频号侧也一样")
    eq(len([cs for r, cs in selg["wechat"] if r["username"] == "跨平台"][0]), 2,
       "global 口径下它在视频号只有 2 条，也照出")

    print(f"自检 {n} 项全过 ✅")
    return 0
