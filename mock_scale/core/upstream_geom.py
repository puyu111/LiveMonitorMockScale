#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游几何从哪里来：显式 ``--geom``、语料根 sidecar，或从交付树自动重建。

正文线的两个入口（``mock/measure_bodies.py`` / ``core/batch_body.py``）都需要
``{相对路径: {text_box, colon_box, badge_box, emojis}}``。优先级：

1. **显式 ``--geom``** —— 给了就必须能读；读不了直接报错，不悄悄退回自动。
2. **``<source>/geom.json``** —— 上游 sidecar（自带几何的语料树）。
3. **自动重建** —— ``<source>/manifest.json`` 是 livepipe ``collect`` 写的交付树清单，
   每张图带 ``seg`` / ``t`` / ``roi``；各段导出目录的 ``comments.jsonl`` 里带四个几何字段。
   按 ``(seg, round(t, 3), roi)`` 配回去即可。

为什么能这样配
--------------
``collect`` 把 ``roi`` 从 ``comments.jsonl`` 原样抄进 manifest，图也是从同一帧导出的。
同一时刻屏幕上通常有多条评论（``t`` 相同），``roi`` 才能唯一定位。实测 8pro 批次
2,410 张全部唯一命中（缺 0 / 歧义 0）。

⚠️ 自动重建**只读**语料，不写任何文件。``collect`` 每次重建交付树都会重新编号并
重写 ``rel`` ⇒ 树重建一次，几何就要重算一次，不能拿旧文件凑。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger("upstream_geom")

GEOM_FIELDS = ("text_box", "colon_box", "badge_box", "emojis")
"""从 ``comments.jsonl`` 抄进 ``geom.json`` 的四个字段（与上游 v1 形状一致）。"""


class GeomError(Exception):
    """几何拿不到 / 形状不对 —— 调用方打印后退出 2，不猜。"""


@dataclass(frozen=True)
class GeomSource:
    """一份可用的几何 + 它是怎么来的（写日志、报告与指纹用）。"""

    items: dict[str, dict]
    origin: str
    """``explicit`` = ``--geom`` · ``sidecar`` = ``<source>/geom.json`` · ``manifest`` = 自动重建。"""

    path: Optional[Path] = None
    """显式 / sidecar 的几何文件；自动重建时是 ``manifest.json``。"""

    images: int = 0
    matched: int = 0
    missing: int = 0
    ambiguous: int = 0
    checked: tuple[Path, ...] = ()
    """参与指纹的文件（几何文件，或 manifest + 用到的 comments.jsonl）。"""

    @property
    def auto(self) -> bool:
        return self.origin == "manifest"

    def describe(self) -> str:
        if self.auto:
            return (
                f"自动重建（manifest.json + 各段 comments.jsonl）"
                f" · {self.matched}/{self.images} 张命中"
                f" · 缺 {self.missing} · 歧义 {self.ambiguous}"
            )
        return f"{self.path} · {len(self.items)} 条"


def load(source: Path, geom_path: Optional[Path] = None) -> GeomSource:
    """按优先级取几何；``geom_path`` 给了就不退回自动。

    抛 :class:`GeomError`，错误消息直接给人看（调用方原样打印）。
    """
    source = Path(source)
    if geom_path is not None:
        path = Path(geom_path)
        return GeomSource(read_file(path), "explicit", path, checked=(path,))
    sidecar = source / "geom.json"
    if sidecar.is_file():
        return GeomSource(read_file(sidecar), "sidecar", sidecar, checked=(sidecar,))
    return build_from_manifest(source)


def read_file(path: Path) -> dict:
    """读一份 ``geom.json``，校验顶层形状后返回 ``items``。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise GeomError(f"几何文件读不了：{path}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise GeomError(f"几何文件不是合法 JSON：{path}（{exc}）") from exc
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        raise GeomError(f'几何文件的顶层要是 {{"items": {{相对路径: 几何}}}}：{path}')
    return items


def _segment_out(raw: object, source: Path) -> Path:
    """``segments[].out`` → 目录。相对路径按交付树的几层上级依次试（配置基准不一定和我们相同）。"""
    path = Path(str(raw))
    if path.is_absolute():
        return path
    for base in (source, source.parent, source.parent.parent, source.parent.parent.parent):
        candidate = base / path
        if (candidate / "comments.jsonl").is_file():
            return candidate
    return path


def build_from_manifest(source: Path) -> GeomSource:
    """从交付树重建几何：``manifest.json`` 定 (seg, t, roi)，各段 ``comments.jsonl`` 给框。"""
    source = Path(source)
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise GeomError(
            f"没有几何可用：{source} 下既没有 geom.json，也没有交付树的 manifest.json"
            "（用 --geom 指一份，或 --source 指 livepipe 的交付树）"
        )
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GeomError(f"manifest.json 读不了：{manifest_path}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise GeomError(f"manifest.json 不是合法 JSON：{manifest_path}（{exc}）") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("users"), list):
        raise GeomError(f"manifest.json 不是交付树形状（缺 users[]）：{manifest_path}")

    segments = doc.get("segments")
    if not isinstance(segments, list) or not segments:
        raise GeomError(f"manifest.json 缺 segments[]，不知道各段导出目录：{manifest_path}")
    seg_out: dict[str, Path] = {}
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        # 与 livepipe merge_segments.common._label 同口径：没 name 就用视频文件名
        name = seg.get("name") or Path(str(seg.get("video") or "?")).name
        out = seg.get("out")
        if name and out:
            seg_out[str(name)] = _segment_out(out, source)
    if not seg_out:
        raise GeomError(f"manifest.json 的 segments[] 没有可用的 out：{manifest_path}")

    indexes: dict[str, dict[int, list[dict]]] = {}
    comments_paths: list[Path] = []

    def index_of(seg_name: str) -> Optional[dict[int, list[dict]]]:
        """该段 comments.jsonl 的 ``{round(t,3): [评论…]}``；段不在表里返回 None。"""
        if seg_name in indexes:
            return indexes[seg_name]
        out_dir = seg_out.get(seg_name)
        if out_dir is None:
            return None
        path = out_dir / "comments.jsonl"
        if not path.is_file():
            raise GeomError(
                f"自动重建几何缺 comments.jsonl：{path}"
                "（该段的上游几何不在这棵树上；用 --geom 指一份）"
            )
        index: dict[int, list[dict]] = {}
        with path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    comment = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise GeomError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
                if not isinstance(comment, dict) or "t" not in comment:
                    continue
                try:
                    key = round(float(comment["t"]), 3)
                except (TypeError, ValueError):
                    continue
                index.setdefault(key, []).append(comment)
        indexes[seg_name] = index
        comments_paths.append(path)
        return index

    items: dict[str, dict] = {}
    images = matched = missing = ambiguous = 0
    missing_rels: list[str] = []
    for user in doc["users"]:
        if not isinstance(user, dict):
            continue
        for image in user.get("images") or []:
            if not isinstance(image, dict):
                continue
            images += 1
            rel = image.get("rel")
            seg_name = str(image.get("seg") or "")
            roi = image.get("roi")
            t = image.get("t")
            index = index_of(seg_name) if rel and t is not None and roi is not None else None
            if index is None:
                missing += 1
                if rel and len(missing_rels) < 3:
                    missing_rels.append(str(rel))
                continue
            try:
                key = round(float(t), 3)
            except (TypeError, ValueError):
                missing += 1
                continue
            candidates = [c for c in index.get(key, []) if c.get("roi") == roi]
            if not candidates:
                missing += 1
                if len(missing_rels) < 3:
                    missing_rels.append(str(rel))
                continue
            if len(candidates) > 1:
                ambiguous += 1
            chosen = candidates[0]
            items[str(rel)] = {k: chosen.get(k) for k in GEOM_FIELDS}
            matched += 1

    if missing:
        LOGGER.warning(
            "自动几何：%d/%d 张在 comments.jsonl 里配不上（例如 %s）",
            missing, images, " · ".join(missing_rels),
        )
    if ambiguous:
        LOGGER.warning("自动几何：%d 张有多个同 roi 候选，取第一条", ambiguous)
    LOGGER.debug(
        "自动几何：%d/%d 张命中 · 歧义 %d · manifest %s",
        matched, images, ambiguous, manifest_path,
    )
    return GeomSource(
        items=items,
        origin="manifest",
        path=manifest_path,
        images=images,
        matched=matched,
        missing=missing,
        ambiguous=ambiguous,
        checked=(manifest_path, *sorted(comments_paths, key=str)),
    )
