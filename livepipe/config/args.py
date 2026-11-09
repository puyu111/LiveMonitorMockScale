"""livepipe.config.args"""

from __future__ import annotations

from pathlib import Path
import argparse
import json

from .errors import ConfigError
from .panels import identity_panels
from .panels import level_identity_declared
from .panels import masked_identity_panels
from .panels import resolve_panels
from .panels import segment_files
from .paths import segments



# --------------------------------------------------------------------------- #
# 配置节 → 各脚本的 argparse.Namespace
# --------------------------------------------------------------------------- #

def _plc():
    """导入核心模块（包内/脚本直跑两种方式都行）。"""
    try:
        from .. import parse_live_comments as plc
    except ImportError:  # 直接以脚本方式运行本包内文件时
        import parse_live_comments as plc  # type: ignore
    return plc


def _base_args(mod) -> argparse.Namespace:
    """拿某脚本的**全部默认值**当底稿 —— 保证不漏字段、与 CLI 默认一致。

    不能直接 ``make_parser().parse_args([])``：draw_boxes/locate_panels 有 **required**
    参数（``--json``/``--video``/…），空 argv 会当场 SystemExit。所以按 action 逐个取
    ``default`` 自己拼 Namespace —— 对无 required 的脚本与 ``parse_args([])`` 等价。
    """
    ns = argparse.Namespace()
    for action in mod.make_parser()._actions:  # noqa: SLF001
        if action.dest == "help":
            continue
        setattr(ns, action.dest, action.default)
    return ns


def build_parse_args(cfg: dict, *, write: bool = True,
                     seg: dict | None = None) -> argparse.Namespace:
    """配置 → ``parse_live_comments.run(args)`` 的 Namespace（``seg`` 给定时按该段走）。"""
    plc = _plc()
    a = _base_args(plc)
    inp, pa, pn, out = cfg["input"], cfg["parse"], cfg["panels"], cfg["output"]
    s = seg or segments(cfg)[0]
    odir = Path(s["out"])

    # 输入（按段）
    a.video, a.rows, a.text = s["video"], s["rows"], inp["text"]
    a.start = s["start"] if s["start"] is not None else inp["start"]
    a.duration = s["duration"] if s["duration"] is not None else inp["duration"]
    a.interval, a.max_frames = inp["interval"], inp["max_frames"]
    a.resume = bool(inp.get("resume", False))
    # 抽帧策略：mode 单独走一个 flag；其余参数打包成 JSON 字符串（与 --sample 同形）。
    # interval 模式下不管这些参数（保持与从前逐字节一致的默认行为），所以 sample=None。
    smp = dict(inp.get("sample") or {})
    a.sample_mode = smp.pop("mode", "interval")
    a.sample = (json.dumps(smp, ensure_ascii=False)
                if a.sample_mode != "interval" else None)

    # 面板：吃 manifest 的模式经平台档案展开；auto 让脚本自己定位
    a.auto_panels = pn["auto"]["method"] if pn["mode"] == "auto" else None
    a.panels_file, emoji_spec = resolve_panels(cfg, write=write, seg=s)
    a.dump_panels = s["panels_file"] if (out["panels"] and pn["mode"] == "auto") else None
    # auto 模式没有 panels 文件承载 x2_from，按平台档案推给命令行（reuse/manual/locate
    # 已经在 panels.resolved.json 里带上了，面板自己的值优先）。
    a.x2_from = (",".join(f"{n}={cfg['platforms'][p].get('x2_from', 'content')}"
                          for n, p in pn["auto"]["platforms"].items()
                          if p in cfg["platforms"])
                 if pn["mode"] == "auto" else None)
    # 同理把「带榜N药丸的面板」推给命令行（reuse/manual/locate 已写进 panels.resolved.json）。
    a.rank_pill = (",".join(n for n, p in pn["auto"]["platforms"].items()
                            if p in cfg["platforms"]
                            and cfg["platforms"][p].get("rank_pill"))
                   if pn["mode"] == "auto" else None) or None
    # 也把「行首「粉丝」药丸 + 它的几何闸」推给命令行（reuse/manual/locate 已写进
    # panels.resolved.json）。写成 ``面板=X``，与 --fan-badge 的取值格式一致。
    a.fan_badge = (",".join(f"{n}={cfg['platforms'][p]['fan_badge_x_max']}"
                            for n, p in pn["auto"]["platforms"].items()
                            if p in cfg["platforms"]
                            and cfg["platforms"][p].get("fan_badge_x_max") is not None)
                   if pn["mode"] == "auto" else None) or None
    # 也把「要做行首等级药丸别名归一的面板」推给命令行（reuse/manual/locate 已写进
    # panels.resolved.json）。布尔开关，取值就是面板名。见 parse_live_comments 的
    # normalize_badge_alias()（视频号手机 App：✦N 药丸并进正文 ⇒ 同一个人被拆成多个账号）。
    a.badge_alias = (",".join(n for n, p in pn["auto"]["platforms"].items()
                              if p in cfg["platforms"]
                              and cfg["platforms"][p].get("badge_alias"))
                     if pn["mode"] == "auto" else None) or None
    # 也把「要做**昵称尾巴**别名归一的面板」推给命令行（reuse/manual/locate 已写进
    # panels.resolved.json）。布尔开关，取值就是面板名。见 parse_live_comments 的
    # normalize_username_alias()（视频号手机 App：检测框切掉昵称末尾 1~2px ⇒ `W.`/`W`、
    # `期待sj`/`期待si` 被拆成两个账号）。
    a.username_alias = (",".join(n for n, p in pn["auto"]["platforms"].items()
                                 if p in cfg["platforms"]
                                 and cfg["platforms"][p].get("username_alias"))
                        if pn["mode"] == "auto" else None) or None
    # 也把「会被浮层卡压住的面板」推给命令行（reuse/manual/locate 已写进
    # panels.resolved.json）。取值是面板名，写成 ``面板=额外正则`` 时带上**本素材**才有的
    # 短语（广告气泡的品牌行这种，内置表里不敢放）。见 parse_live_comments 的
    # overlay_card_re（视频号手机 App：公告卡/主题卡/公众号卡/广告气泡压在评论列表上）。
    a.overlay_cards = (",".join(
        f"{n}={p['overlay_cards']}" if isinstance(p.get("overlay_cards"), str) else n
        for n, p in pn["auto"]["platforms"].items()
        if p in cfg["platforms"] and cfg["platforms"][p].get("overlay_cards"))
        if pn["mode"] == "auto" else None) or None
    au = pn["auto"]
    a.at, a.split_x, a.panel_frames = au["at"], au["split_x"], au["frames"]
    a.panel_span, a.sub_width = au["span"], au["sub_width"]
    a.pad_x, a.pad_y, a.pad_top = au["pad_x"], au["pad_y"], au["pad_top"]
    a.panel_model, a.quant, a.device = au["model"], au["quant"], au["device"]

    # 解析
    a.sep, a.max_username_len = pa["sep"], pa["max_username_len"]
    a.no_merge = not pa["merge_continuation"]
    a.no_adjacency = not pa["adjacency"]
    a.no_dedup = not pa["dedup"]
    # 等级标二次读数与认人口径绑定：不写（None）⇒ account_key 用等级就开、只看昵称就关。
    # 显式 true/false 仍以配置为准（见 config.defaults 的 parse.badge_reread）。
    if pa.get("badge_reread") is None:
        a.badge_reread = level_identity_declared(cfg, seg)
    else:
        a.badge_reread = bool(pa["badge_reread"])
    a.no_calibrate = not pa["calibrate"]
    a.right_pad = pa["right_pad"]
    a.emoji_workers = pa["emoji_workers"]
    a.no_colon_marks = not pa["colon_marks"]

    # 表情：按每个面板的平台选表
    a.emoji_table = emoji_spec
    a.no_emoji = not cfg["emoji"]["enabled"]

    # 输出
    a.out = str(odir) if out["write"] else None
    a.print = out["print"]
    a.report = False
    # 标注图要有 --out 才有地方放（脚本里同样：没 out 就不出图）。
    a.no_frames = not (out["frames"] and out["write"])
    a.dump_rows = str(odir / "rows.jsonl") if out["rows"] else None
    return a


def build_emoji_args(cfg: dict, src: str) -> argparse.Namespace:
    """配置 → 建表脚本（douyin/wechat/weibo/kuaishou_emoji_table）的 Namespace。"""
    b = cfg["emoji"]["tables"][src]["build"]
    if src == "douyin":
        from .. import douyin_emoji_table as mod
        a = _base_args(mod)
        a.out = b["out"]
        # `--api` 是 nargs="?"+const：不给值 ⇒ 用模块里的 API_URL。这里等价复刻，
        # 不能把 True 塞进 a.api（run_api 会对它调 .startswith）。
        if b["mode"] == "api":
            a.api = b["api"] or mod.API_URL
        else:                         # dom：从 --html 的 DOM 抽（默认 stdin）
            a.api, a.html = None, b["html"]
        a.no_download = b["no_download"]
        a.per_sheet = b["per_sheet"]
    elif src == "wechat":
        from .. import wechat_emoji_table as mod
        a = _base_args(mod)
        a.out = b["out"]
        a.src = b["src"]
        a.no_sheet = b["no_sheet"]
    elif src == "weibo":
        from .. import weibo_emoji_table as mod
        a = _base_args(mod)
        a.out = b["out"]
        # 自动模式：config 给了就跳过面板 DOM / 解析表（None ⇒ 手工模式）。
        # "mirror"/true ⇒ 公开镜像快照（等价 CLI 的裸 --config）；其余当路径/URL。
        cfg_src = b.get("config")
        a.config = (mod.CONFIG_URL if cfg_src is True or cfg_src == "mirror"
                    else cfg_src)
        a.cookie = b.get("cookie")
        a.include_parse_only = bool(b.get("include_parse_only", False))
        # 面板 DOM 与解析表的默认值都相对 <out>（None ⇒ 脚本自己按 out 拼），
        # 所以这里只在配置显式给了路径时才覆盖
        a.panel = b["panel"]
        a.map = b["map"]
        a.src = b["src"]
        a.no_sheet = b["no_sheet"]
    elif src == "kuaishou":
        from .. import kuaishou_emoji_table as mod
        a = _base_args(mod)
        a.out = b["out"]
        a.src = b["src"]
        a.no_download = b.get("no_download", False)
        a.no_sheet = b["no_sheet"]
    else:
        raise ConfigError(f"不认识的表情表源：{src!r}（只有 douyin / wechat / weibo / kuaishou）")
    return a


def build_batch_args(cfg: dict) -> argparse.Namespace:
    """配置 → ``batch_locate_panels.main(args)`` 的 Namespace（稳定性研究）。"""
    try:
        from .. import batch_locate_panels as mod
    except ImportError:
        import batch_locate_panels as mod  # type: ignore
    a = _base_args(mod)
    au, ba = cfg["panels"]["auto"], cfg["panels"]["batch"]
    a.video = cfg["input"]["video"]
    a.out = str(Path(cfg["output"]["dir"]) / "panel_batch")
    a.count, a.start, a.span, a.anchor = ba["count"], ba["start"], ba["span"], ba["anchor"]
    a.split_x, a.sub_width = au["split_x"], au["sub_width"]
    a.model, a.device, a.quant = au["model"], au["device"], au["quant"]
    return a


def build_draw_args(cfg: dict, seg: dict | None = None) -> argparse.Namespace:
    """配置 → ``draw_boxes.run(args)`` 的 Namespace（独立描框）。"""
    try:
        from .. import draw_boxes as mod
    except ImportError:
        import draw_boxes as mod  # type: ignore
    d = cfg["draw"]
    s = seg or segments(cfg)[0]
    a = _base_args(mod)
    a.json = d["source"] or str(Path(s["out"]) / "comments.jsonl")
    a.video = s["video"]
    a.out = d["out"] or str(Path(s["out"]) / f"draw_{d['at']:g}.jpg")
    a.at, a.window = d["at"], d["window"]
    a.width, a.quality = d["width"], d["quality"]
    a.no_labels = not d["labels"]
    return a


def build_export_args(cfg: dict, seg: dict | None = None) -> argparse.Namespace:
    """配置 → ``export_user_images.run(args)`` 的 Namespace（⑥ 逐段出交付图）。"""
    try:
        from .. import export_user_images as mod
    except ImportError:
        import export_user_images as mod  # type: ignore
    e = cfg["export"]
    s = seg or segments(cfg)[0]
    a = _base_args(mod)
    _pf, _src, usr = segment_files(cfg, s)
    qual = Path(s["out"]) / "users_qualified.jsonl"
    a.users = e["users"] or (str(qual) if (qual.exists() or cfg["qualify"]["enabled"])
                             else usr)
    # 其余路径全留默认：export 会以 users.jsonl 所在目录为准去找 summary.json /
    # panels.resolved.json / frames —— 而它们正好都在段目录里。
    a.out, a.summary, a.panels = None, None, None
    a.video = s["video"]
    a.frames = e["frames"] or str(Path(s["out"]) / "frames")
    a.base = e["base"]
    a.labels = e["labels"]
    a.min_positive = (e["min_positive"] if e["min_positive"] is not None
                      else (cfg["qualify"]["stage_min"] if cfg["qualify"]["enabled"]
                            else cfg["filter"]["min_positive"]))
    a.min_scope = e["min_scope"]
    a.flat = False
    a.no_box = False
    a.color, a.width = e["color"], e["width"]
    a.pad, a.pad_ratio, a.quality = e["pad"], e["pad_ratio"], e["quality"]
    a.limit = None
    a.force = e["force"]
    a.dry_run = False
    return a


def build_filter_args(cfg: dict, seg: dict | None = None) -> argparse.Namespace:
    """配置 → ``user_filter.run(args)`` 的 Namespace（评论 → 合格用户）。"""
    try:
        from .. import user_filter as mod
    except ImportError:
        import user_filter as mod  # type: ignore
    f, nlp = cfg["filter"], cfg["filter"]["nlp"]
    _pf, src, usr = segment_files(cfg, seg)
    a = _base_args(mod)
    a.comments, a.out = src, usr
    a.cache = f["cache"] or cfg.get("_judgments_cache")
    # 多段 + 跨段判合格时，**各段这一刀必须放到最松** —— 合格与否是 qualify 步按合计判的，
    # 这里若按交付阈值先筛一遍，会把「seg1 两条 + seg2 两条」的账号判掉（静默掉出名单）。
    a.min_positive = (cfg["qualify"]["stage_min"]
                      if cfg["qualify"]["enabled"] and len(segments(cfg)) > 1
                      else f["min_positive"])
    a.exclude = ",".join(f["exclude_keywords"])
    a.identity_panels = ",".join(identity_panels(cfg, seg))
    # 打码昵称才按等级拆的面板（account_key="nickname_level_if_masked"）。
    a.identity_masked = ",".join(masked_identity_panels(cfg, seg))
    a.api_key, a.api_key_env = nlp["api_key"], nlp["api_key_env"]
    a.base_url, a.model = nlp["base_url"], nlp["model"]
    a.batch_size, a.concurrency = nlp["batch_size"], nlp["concurrency"]
    a.timeout, a.retries, a.limit = nlp["timeout"], nlp["retries"], nlp["limit"]
    a.prompt = nlp["prompt"] or mod.DEFAULT_PROMPT
    a.dry_run = False
    return a
