"""livepipe.tools.remedy_badges.verify"""

from __future__ import annotations

from pathlib import Path
import json

from .common import _iden
from .common import _log



# --------------------------------------------------------------------------- #
# ⑤ 核账：落盘前后按**身份**对（不按字节，见 `_iden` 的说明）
# --------------------------------------------------------------------------- #

def verify(old_tree: dict, root: Path, plan: dict) -> int:
    """每张旧图：要么在新树里找得到，要么在「掉档」单子里 —— 一张都不许凭空消失。"""
    new_iden: dict[str, list[str]] = {}
    doc = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for u in doc.get("users") or []:
        for im in u.get("images") or []:
            if (root / im["rel"]).exists():
                new_iden.setdefault(_iden(im), []).append(im["rel"])
    levels, dropped = plan.get("levels") or {}, plan.get("dropped") or {}
    if levels and not any("\x1f" in k for k in levels):
        # 老版本 plan 把 `levels` 记成 **sha1**（本文件早期版本，2026-10-14 上午那版）。
        # 图是每次 export **重画**的，字节会漂，拿 sha1 跟 `iden` 查必然条条落空 ⇒ 全部
        # 「掉档」都会被判成「凭空丢」（当天就真报过一次 2405 的假警报）。这种 plan 一律不认。
        _log("[补救][warn] 这份 plan 的 levels 是按 **sha1** 记的（老版本），跟按身份的核账对不上"
             " —— 本次核账作废，重跑一次（不加 --apply）拿新 plan 再核")
        return 0
    kept = same_dir = expect_gone = 0
    lost: list[dict] = []                    # 没落地、也**不在**掉档单子里的 = 真丢
    for (name, lv), rec in old_tree.items():
        for i, im in enumerate(rec["images"], 1):
            if im["iden"] in new_iden:
                kept += 1
                if new_iden[im["iden"]][0].startswith(rec["dir"] + "/"):
                    same_dir += 1
            elif levels.get(im["iden"]) in dropped:
                expect_gone += 1             # 拆开后不足 N 张 ⇒ 意料之中
            else:
                lost.append({"dir": rec["dir"], "img": f"{i}.jpg", "level": lv,
                             "content": im.get("content") or im.get("emoji_text")})
    n_new = sum(len(v) for v in new_iden.values())
    _log(f"[补救] 核账（按身份，不按字节）：新树 {len(doc.get('users') or [])} 档 / {n_new} 张；"
         f"旧图 {kept + expect_gone + len(lost)} 张 → 原地 {same_dir}、"
         f"换目录 {kept - same_dir}、掉档 {expect_gone}、**凭空丢 {len(lost)}**")
    if lost:
        _log(f"[补救][error] {len(lost)} 张不该消失（既没落到新树、又不在掉档单子里）：")
        for x in lost[:20]:
            _log(f"          {x['dir']}/{x['img']}  等级 {x['level'] or '(无标)'}"
                 f"  「{x['content']}」")
        return 1
    if expect_gone != sum(dropped.values()):
        _log(f"[补救][warn] 掉档单子写 {sum(dropped.values())} 张、实际没落地 {expect_gone} 张"
             f"（差额多半是「掉档的图本来就没在旧树里」）")
    return 0
