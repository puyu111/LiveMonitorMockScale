"""``livepipe.tools.dataset.doc`` —— 命令行完整说明。"""

from __future__ import annotations

DOC = """识别结果 → YOLO 数据集（review 出复核图，build 落盘 + 过八道闸）。

两个子命令，中间夹一步**人**：

    dataset review --runs out/<run> [...] --out out/dataset_v1
        # 顺序解码一次，每条评论出一张复核横条，
        # 写 review/index.jsonl（video / frame / 全部框）。
        # username / colon_box 为空的行在 scan 阶段就过滤掉（LABEL_SOURCE 闸）。

    # 人在 out/dataset_v1/review/ 里看：
    #   · 删掉不对的 → 这条评论的框都不进

    dataset build --out out/dataset_v1 --dry-run   # 先算账：复核 N · 删除 M · 图 F
    dataset build --out out/dataset_v1             # 落盘 + 八道闸

产物（标准 YOLO 结构，不加私有前缀）：::

    out/dataset_v1/
    ├── images/{train,val,test}/<视频键>_f<帧号>.jpg   一帧一张（整帧原图，不裁）
    ├── labels/{train,val,test}/<同名>.txt             <cls> <cx> <cy> <w> <h>，6 位小数
    ├── preview/                PREVIEW 闸从**标签文件**反画的 20 张
    ├── review/                 复核图 + index.jsonl + rejected.jsonl
    ├── data.yaml
    └── manifest.json           每张图来自哪段视频、哪一帧、切到哪个 split

四个类（顺序 = cls 编号，写进 data.yaml 的 names）::

    0 comment_row   整行带            ← roi
    1 body_text     昵称 + 正文        ← text_box
    2 colon         昵称与正文之间那个冒号 ← colon_box
    3 emoji_run     每个表情各一个框    ← emojis[].box

**等级标不进数据集。** 上游 ``badge_box`` 有 850 条「有读数没坐标」；写进去就是
32.2% 的可见等级标假负（详见 ``review_granularity.html`` §04）。要恢复这一类，
先修上游的框检测，再把 ``(level_badge, badge_box)`` 加回 ``common.CLASSES`` ——
注意它会插在 ``emoji_run`` 前面，cls 编号整体后移。

几条硬规矩
----------
* **取帧不能 seek**：``adaptive`` 抽帧是顺序解码数计数器，回读也照样顺序 ``grab()``
  （用 ``export_user_images`` 那把 ``FrameReader``）。这批录屏是变帧率，按
  ``POS_MSEC`` seek 会漂半秒，框就画在另一时刻的画面上（bug-264）。
* **帧号只能 ``round(t × fps)`` 现算**：``rows.jsonl`` 里的 ``frame`` 是「本次抽样的
  第几张」，不是解码器帧号。
* **split 按视频切，不按帧随机切**：相邻两帧的差别只是多了一条新评论，随机切就是
  把答案从训练集抄进验证集。默认规则：微博那段 → test，17：13-17：33 → val，其余 train。
* **一个复核单元 = 一条看得见的评论**：同一 (视频, 帧) 下 ROI IoU ≥ 0.6 的记录并成
  一条（同一批录屏被两个 run 解析过），删一次就全没了。
* 复核图**不能**放 ``frames/``：那是 parse 的产物，下次 parse 会被整个清空
  （``render.py`` 的 ``stale.unlink()``）。所以单独放 ``<out>/review/``。

用法
----
    python3 -m livepipe.dataset --selftest          # 纯函数自检（不读盘、不要 cv2）
    python3 -m livepipe.dataset review --runs out/风云T7_20260826_11 out/征服8 --out out/dataset_v1
    python3 -m livepipe.dataset build --out out/dataset_v1 --dry-run
    python3 -m livepipe.dataset build --out out/dataset_v1
    python3 -m livepipe.dataset build --out out/dataset_v1 --restore 12-01-13-00_f010466_03.jpg

依赖：取原帧要 OpenCV、画框要 Pillow。不碰模型、不调 API。
"""
