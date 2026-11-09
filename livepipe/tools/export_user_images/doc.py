"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """合格用户 → 「每用户一个文件夹、每条评论一张图」的交付形态（**按平台分目录**）。

产物（与既有交付语料 ``<活动>/<平台>/all_users_正面_3张以上/`` 同形）::

    <out>/<平台>/all_users_正面_3张以上/<用户名>-<条数>/1.jpg  2.jpg  ...
    <out>/<平台>/all_users_正面_3张以上/manifest.json
    <out>/<平台>/all_users_正面_3张以上/正面账号.txt

每张图 = **整帧原图 + 该条评论一个绿框**：只圈这一条，图上**没有任何识别出来的
文字/编号**。与参考交付语料
``…/2-2026-06-17艾瑞泽8征服版上市/视频号/all_users_正面_3张以上/+.11天空-6/1.jpg``
逐项对齐 —— 实测该语料：整帧 2560×1520 不裁 · 绿框 **RGB(0,255,0)** · **线宽 3** ·
框内只有「徽章 + 用户名 + 正文」 · 其余画面一律不动。

**框要留白**（默认）：外扩量 = ``max(--pad, --pad-ratio × 行高)``，默认
``0px / 0.25``（行高 29px ⇒ 外扩 7px）。按**行高**取比例是因为同一批语料行高
20~109px 差 5 倍（带表情、折行的高条），只用固定像素要么紧贴矮行、要么在高行上
小得看不见。外扩后夹进画面内 —— 贴面板边的评论会算出负数坐标，PIL 静默裁掉半条
边框。要参考语料那种**紧贴不留白**的形态：``--pad-ratio 0``。

**底帧取法必须与抽行同口径**（bug-264）：底图从**视频**按 ``sample_mode`` 取 ——
``adaptive`` 抽行是**顺序解码数计数器**（``t = idx/fps``，全程不 seek），这里就照样
顺序 ``grab()``；``interval`` / ``backoff`` 抽行本身按 t seek，这里才 seek。口径从
``summary.json`` 的 ``sample_mode`` / ``start`` 读（那就是这批行的出身证明），命令行
只兜底。**绝不把「adaptive 的行」配上「按 t seek 的取帧」** —— 这条录屏变帧率，
seek 的落点在关键帧处会跳（实测 t=206.5s 晚 218 帧），漂了框就画在另一时刻的画面上，
而屏幕一直在滚 ⇒ 框与内容整体错行。

``--base annotated`` 退回「复制 ``frames/`` 里画好的标注图」（调试/对比用 —— 那上面
带着编号、用户名和**同帧全部**评论的框，**不是交付形态**）。

用法
----
    P=/home/puyu/anaconda3/envs/vlx/bin/python

    # 默认：读 <run>/users.jsonl，底图从视频顺序解码，出到 <run>/<平台>/all_users_正面_3张以上/
    $P livepipe/export_user_images.py --users out/livepipe_example3/users.jsonl

    # 自检（纯函数，零第三方依赖）
    python3 livepipe/export_user_images.py --selftest

依赖：画框要 Pillow、取原帧要 OpenCV（都在 conda env ``vlx``）；不碰模型、不调 API。
"""
