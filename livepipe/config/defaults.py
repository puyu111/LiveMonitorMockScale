"""livepipe.config.defaults"""

from __future__ import annotations

from typing import Any

from .constants import STEPS
from .constants import VERSION


#: 完整默认配置。用户 JSON 与它深合并（用户值优先）。
DEFAULTS: dict[str, Any] = {
    "version": VERSION,
    "name": None,            # 只用于日志
    "base_dir": None,        # 相对路径的基准目录；None = 当前工作目录
    "input": {
        "video": None,       # 录屏 mp4（OCR 抽行）
        # 多段素材：同一个直播被切成几段录屏时，用 videos 一次列全（与 video 互斥）。
        # 可以是「目录 / 通配符 / 路径数组」，按**文件名排序**（= 时间顺序）逐段跑：
        #   "videos": "/data/视频号/*.mp4"      或   "videos": "/data/视频号/"
        #   "videos": ["/data/a.mp4", "/data/b.mp4"]
        # 每段自动在 output.dir 下开一个子目录（名字见 segment_names），
        # 面板块/输出/筛选都自动按段走 —— 见 CONFIG.md「多段素材」一节。
        "videos": None,
        "segment_names": None,   # 可选：显式段名（长度必须与展开出的段数一致）；默认 seg1、seg2…
        "rows": None,        # 或：已有 rows.jsonl（复算，省一次 OCR）
        "text": None,        # 或：纯文本行文件（- 读 stdin）
        "start": 0.0,
        "duration": None,    # None = 整段
        "interval": 2.0,     # 抽样帧间隔（秒）
        "max_frames": None,
        "resume": False,     # 断点续跑：rows.jsonl 已在就接着它跑（已处理帧跳过，新行追加）
        # 抽帧策略（只影响 --video 抽行那一步）。mode=interval 时下面这些都不用管。
        #   interval —— 固定间隔（默认，行为与从前完全一致）
        #   backoff  —— 按画面变化自适应间隔（便宜；对突发爆量只能事后反应 = 降级版）
        #   adaptive —— 按面板位移/停稳/入口内容触发（不漏且少重复，推荐长跑）
        # 详见 parse_live_comments.py 的 5b 节与 CONFIG.md。
        "sample": {
            "mode": "interval",   # interval | backoff | adaptive
            "stride": 1,          # adaptive：每 N 个解码帧估一次位移
            "safety_rows": 3.0,   # adaptive：累计位移 ≥ 这么多行 ⇒ 强制拍（不漏；上限 5）
            "settle_frames": 3,   # adaptive：停稳多少帧后拍（主力触发）
            "eps_px": 1.0,        # adaptive：相邻帧位移 < 它算「没动」
            "entry_thr": 8.0,     # adaptive：底带灰度均差 > 它 ⇒ 判入口有新评论
            "max_gap_s": 5.0,     # adaptive：距上次拍超过这么久也拍一次（0 = 关）
            "min_interval": 0.2,  # backoff：间隔下限
            "max_interval": 6.0,  # backoff：间隔上限
            "min_score": 0.3,     # 位移估计互相关下限；低于它当作「没有平移」（抗动画）
        },
    },
    "platforms": {
        # 平台档案：**决定一个面板怎么处理**（行网格 + 表情表）。人工在面板文件里
        # 给每个面板填 platform 名字，其余数字从这里取 —— 所以只需填名字，不用抄数字。
        # 名字随便起，加一个新平台 = 加一条档案。
        "douyin": {
            "label": "抖音直播（等级标 ◆N）",
            "emoji": "douyin",       # → emoji.tables 里的键（NOne/缺省 ⇒ 该面板不跑表情）
            "row_pitch": 68,         # 行距（像素）
            "y_phase": 17,           # 首行中心距面板顶（估算失败时的兜底；默认会自校准）
            "x_text": 70,            # 正文起始 x（徽章列右沿，裁切内坐标）
            "x2_from": "content",    # 裁切右沿怎么定，见下
        },
        "wechat": {
            "label": "视频号直播（等级标 ✦N；跑在网页里）",
            "emoji": "wechat",
            "row_pitch": 61,
            "y_phase": 21,
            "x_text": 60,
            # 视频号的面板**右边跟着内容伸缩**（实测内容右沿整片在 1323~2009 之间摆，跨 686px），
            # 所以裁切右沿不能由内容定 —— 定位那一刻定死的值，后面更宽的评论就被裁掉
            # （OCR 在裁切边界上不是平移不变的：整行丢/误读），连 ROI 右边界一起短。
            # "window" = 固定取窗口右沿（超集，按构造不裁内容）。
            # 代价：裁切面积 ×2.8，全片 OCR 约 +38%。抖音面板是固定宽，仍用 content（更省）。
            "x2_from": "window",
        },
    },
    "emoji": {
        "enabled": True,
        "tables": {
            "douyin": {
                "path": "out/douyin_emoji/douyin-emoji.json",
                "build": {"enabled": False, "mode": "api", "out": "out/douyin_emoji",
                          "api": None, "html": "-", "per_sheet": 100,
                          "no_download": False},
            },
            "wechat": {
                "path": "out/wechat_emoji/wechat-emoji.json",
                "build": {"enabled": False, "mode": "fetch", "out": "out/wechat_emoji",
                          "src": None, "no_sheet": False},
            },
            "weibo": {
                # 手工模式吃面板 DOM + key→URL 解析表；两份文件都在 out/（不在 git 里，
                # 清掉就没了）。自动模式直接读 config JSON，见 build.config。
                # **阈值没标定**：表里的 thresholds 是照视频号抄的起点值，跑微博录屏前先量。
                "path": "out/weibo_emoji/weibo-emoji.json",
                "build": {"enabled": False, "mode": "panel", "out": "out/weibo_emoji",
                          "config": None, "cookie": None, "include_parse_only": False,
                          "panel": None, "map": None, "src": None, "no_sheet": False},
            },
            "kuaishou": {
                # 快手 App：表从 live.kuaishou.com 页面内嵌的 emojiPanel 建
                # （207 个 token→图片；运行 `python3 livepipe/kuaishou_emoji_table.py`）。
                # 阈值是 2026-11-03 在 590×1280 App 录屏上量的，跟着表走。
                "path": "out/kuaishou_emoji/kuaishou-emoji.json",
                "build": {"enabled": False, "mode": "web", "out": "out/kuaishou_emoji",
                          "src": None, "no_download": False, "no_sheet": False},
            },
        },
    },
    "panels": {
        # 两阶段工作流：
        #   ① 第一次跑 → 定位评论区，产出面板文件，每个面板的 platform 留空；
        #   ② 打开该文件，人工给每个面板填 platform（可用值见 platforms 段）；
        #   ③ 再跑一遍 → 面板文件已存在，自动复用，进入解析（不会被覆盖）。
        # 为什么 platform 要人工填：评论区范围是**可变的**（视频号尤其），定位只能给出「哪里
        # 有一片评论」，说不出「这是哪个平台」；而平台决定行距与表情表，填错就整片认不出。
        #
        # **mode 不写 = 自动**（省去手工切换）：
        #   auto    解析阶段自己定位（--auto-panels，需 GPU/模型）；平台按 auto.platforms 归属
        #   reuse   复用 file 里的面板（--panels-file），走人工标注的平台
        #   manual  把下面的 manual 写进 file，再复用
        #   locate  先跑一次定位写进 file（platform 留空），再复用
        #   batch   跑稳定性研究（batch_locate_panels）；不动 file
        #   builtin 用脚本内置的 DEFAULT_PANELS（只对特定录屏有效）
        "mode": None,        # None ⇒ 自动：file 已存在就 reuse，否则 locate
        "file": None,        # None ⇒ <output.dir>/panels.json（**人工编辑的就是这个**）
        "overwrite": False,  # locate/manual：file 已存在时是否覆盖（默认不覆盖，保住人工标注）
        "auto": {
            "method": "hybrid",          # model | ocr | hybrid
            "at": None,                  # None = input.start
            "frames": 5,
            "span": None,
            "split_x": 1150,
            "pad_x": 180,
            "pad_top": 80,
            "pad_y": 16,
            "sub_width": 1000,
            "model": "nvidia/LocateAnything-3B",
            "quant": "4bit",             # none | 4bit | 8bit
            "device": "cuda",
            # 只在 mode=auto/builtin/batch 时用：定位窗口名 → 平台（无人值守的快捷路径）
            "platforms": {"left": "douyin", "right": "wechat"},
            # x2_from="window" 时，窗口右沿从哪来：优先用定位产物里的窗口框
            # （_locate_meta.<面板>.window[2]）；手写面板文件没有它时用这个兜底。
            # 留 null 又推不出来 ⇒ 不覆盖，并在日志里说明（此时裁切边仍是内容定值）。
            "frame_width": None,
        },
        # 手写面板：{面板名: {"rect": [x1,y1,x2,y2], "platform": "douyin"}}
        # platform 必填（行网格从平台档案取）；也可就地补 row_pitch/x_text/y_phase 覆盖档案
        # 多段素材：写成**数组**，一段一项（顺序与 segments 一致）——
        #   "manual": [ {...seg1 的面板...}, {...seg2 的面板...} ]
        "manual": {},
        # 每段的面板文件路径（多段 + mode=reuse/locate/manual 时用）；
        # null ⇒ <段目录>/panels.json（多段）或 panels.file（单视频）
        "files": None,
        "batch": {"count": 30, "start": 0.0, "span": 600.0, "anchor": "panel"},
    },
    "parse": {
        "sep": " ",
        "max_username_len": 24,
        "merge_continuation": True,   # False ⇒ --no-merge
        "adjacency": True,            # False ⇒ --no-adjacency
        "dedup": True,                # False ⇒ --no-dedup
        # 等级标二次读数（⇒ --badge-reread）：面板整屏 det+rec 读不出的药丸，
        # 按行把药丸带裁出来只跑识别、跨帧投票补回来。要配 --video（从原帧裁）。
        # 只补不改：已有面板读数不动，两者对上的比例就是这一层的自证。
        # **None = 自动绑定认人口径**：平台档案的 account_key 是
        # nickname_level / nickname_level_if_masked ⇒ 开；认人只看昵称 ⇒ 关
        # （按昵称认人时药丸读数不参与身份，回原帧读它纯属浪费）。显式 true/false
        # 仍以配置为准（征服8.json 的微博侧就显式关了）。
        "badge_reread": None,
        "calibrate": True,            # False ⇒ --no-calibrate
        "right_pad": 0,               # ROI 右边界距面板右边的余量
        "emoji_workers": 1,           # 表情扫描的线程数（1 = 不开；4~8 ⇒ 快 1.5~2.5 倍）
        # 标注图里把每条评论的**分隔冒号**（昵称与正文之间的 : / ：）圈出来
        # （False ⇒ --no-colon-marks）。只影响 frames/ 标注图，交付图不受影响。
        "colon_marks": True,
    },
    "output": {
        "dir": None,         # None ⇒ 自动：out/<name 或 视频文件名>（多次运行不会互相覆盖）
        "write": True,       # 写 comments.jsonl + summary.json（= 给 --out）；False ⇒ 只打 stdout
        "rows": True,        # rows.jsonl（--dump-rows）
        "panels": True,      # panels.json（--dump-panels / 定位产物）
        "frames": True,      # frames/<t>_roi.jpg 标注图（取反 --no-frames）
        "print": False,      # 除写盘外也打到 stdout（--print）
    },
    "filter": {
        # 评论 → 合格用户：用户名 ≥ min_positive 条**正面**评论（正面由大模型判定，
        # 表情也一起发给它），并排除用户名命中关键词的官方侧账号。
        # 默认关：这一步要**花钱调模型**，按需打开。
        "enabled": False,
        "source": None,          # None ⇒ <段目录>/comments.jsonl
        "out": None,             # None ⇒ <段目录>/users.jsonl
        # 判定结果缓存（同一句话只判一次）。null ⇒ 单段时落在 comments 同目录；
        # **多段时默认全段共用 <output.dir>/judgments.jsonl**（同一句话只判一次，省钱且口径一致）
        "cache": None,
        "min_positive": 3,       # 用户名至少要有几条正面评论
        "exclude_keywords": [
            "奇瑞", "4s", "店", "qq3", "汽车",
            "尹同跃", "张国忠", "鲍思语", "王津华", "王孝伟", "胡敬源", "尹祥领",
            "王来春", "李晶", "商文江", "杨棉之", "叶盛基", "路风", "黎汝雄",
            "李学用", "张贵兵", "代立宏", "章红玉", "高新华", "戚士龙", "王琅",
        ],
        "nlp": {
            "api_key": None,     # 直接写 key（**别提交到仓库**）；不写则读 api_key_env
            "api_key_env": "NLP_API_KEY",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "deepseek-v4-flash-0731",
            "batch_size": 20,    # 一次调用判几条（越大越省，越大越怕一条坏整批）
            "concurrency": 4,
            "timeout": 60.0,
            "retries": 3,
            "limit": None,       # 最多调多少批（控成本）
            "prompt": None,      # None ⇒ 用 user_filter.DEFAULT_PROMPT
        },
    },
    # 跨段判合格（全局步）：把各段的 users.jsonl 按**合计**判 ≥min_positive 条正面，
    # 回写每段的 users_qualified.jsonl + 写一份 merged_users.json（名单 + 每段各几条）。
    # **为什么要按合计判**：一个账号 seg1 有 2 条、seg2 有 2 条，合起来 4 条就该交付；
    # 各段自判 ≥3 会把它在两段各判掉一次 ⇒ 静默掉出名单。
    # 只有一段时它就是「按本段判」，与直接给 filter 设阈值等价（但仍建议走它，保证出口一致）。
    "qualify": {
        "enabled": False,
        "min_positive": 3,   # 交付阈值（**跨段合计**）
        "stage_min": 1,      # 各段 filter/export 用的阈值（先出全量，合格留给这一步判）
    },

    # ⑥ 逐段出交付图（export_user_images）：读该段合格用户表，按底帧画绿框、一人一个目录。
    "export": {
        "enabled": False,
        "base": "frame",          # frame=原帧 / annotated=标注图                       --base
        "labels": "正面",          # 出哪些标签的评论，逗号分隔                          --labels
        "min_positive": None,     # 出图阈值；null ⇒ qualify.stage_min
        "min_scope": "platform",  # platform（按平台判）/ global（不分平台）             --min-scope
        "users": None,            # 合格用户表；null ⇒ <段目录>/users_qualified.jsonl（没有就退回 users.jsonl）
        "frames": None,           # 标注图目录（base=annotated 时用）；null ⇒ <段目录>/frames
        "color": "0,255,0",       # 绿框颜色 R,G,B（对齐参考语料）
        "width": 3,               # 框线宽
        "pad": 0,
        "pad_ratio": 0.25,
        "quality": 90,
        "force": False,           # 输出目录非空也照写
    },

    # 拼最终交付树（全局步）：把各段导出的目录按**昵称**并成一个账号目录、图号跨段连续，
    # 后缀取合计条数；顺带写 正面账号.txt + manifest.json。
    "collect": {
        "enabled": False,
        "stage_min": None,        # 各段导出目录名里的数字；null ⇒ qualify.stage_min
        "platform_dir": None,     # 交付树里的平台目录名（视频号/抖音…）；null ⇒ 从各段导出目录自动认
        "out": None,              # 交付树根；null ⇒ <output.dir>/<平台目录>/all_users_正面_<N>张以上
        "force": False,           # 目标非空时整棵重来
    },

    "draw": {                # 独立的描框步骤（draw_boxes.py）
        "enabled": False,
        "source": None,      # None ⇒ <output.dir>/comments.jsonl
        "at": 0.0,
        "window": 5.0,
        "out": None,         # None ⇒ <output.dir>/draw_<at>.jpg
        "width": 5,
        "quality": 90,
        "labels": True,
    },
    "steps": {
        "order": list(STEPS),
        "skip": [],
    },
}
