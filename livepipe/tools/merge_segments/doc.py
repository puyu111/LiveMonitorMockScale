"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """多段素材的**跨段**两步：``qualify``（判合格）与 ``collect``（拼交付树）。

同一个直播被切成几段录屏时（``input.videos``），每段各跑一遍管线是对的，
但有两件事**必须跨段**做，这个模块就是那两件：

1. **合格判定按合计判**（``qualify``）
   交付要求「该账号 ≥N 条正面」。一个账号在 seg1 有 2 条、seg2 有 2 条，合起来 4 条就该交付；
   若各段各自判 ≥3，这种账号会被**两段各判掉一次** ⇒ 静默掉出名单。
   所以各段的 filter 只出全量用户表（``--min-positive 1``，见 config 的 ``qualify.stage_min``），
   合格与否在这里按合计判，再把合格用户**回写**成每段的 ``users_qualified.jsonl`` 交给 export
   （少画一堆不要的图）。

2. **同名目录不能并存**（``collect``）
   export 的目录名是 ``<昵称>-<本段正面条数>``，同一个账号在 seg1/seg2 各出一个目录
   （``张三-2`` / ``张三-2-2``），交付树里就成了两个账号。这里按**昵称**并成一个目录，
   图号 ``1.jpg…N.jpg`` **跨段连续编号**，后缀取**合计条数**。

   根 manifest 的 ``users[]`` 除 ``username/dir/count/segs`` 外**还带 ``images``** ——
   逐图那一条（``t``/``roi``/``label``/``content``/``emoji_text``…）照抄段 manifest，
   只把 ``rel`` 重写成合并树里的路径（``<目录名>/<新图号>.jpg``）、另加 ``seg`` 记它来自哪段。
   理由：交付树这个路径是**复用**的（与单段 export 同名），下游按「逐图清单」格式读它时
   不能因为这里是合并产物就崩（bug-356）。图是 ``copy2`` 来的、与段内那张同字节，
   段 manifest 里的 ``roi`` 对合并后的文件**逐张成立**。

两步都幂等：``qualify`` 只读评论表、只写用户表；``collect`` 目标非空时默认报错，
``collect.force=true`` 才覆盖（与 ``export_user_images`` 同口径）。

段从 :func:`livepipe.config.segments` 来（单视频也算一段）—— 只有一段时这两步仍然能跑，
``qualify`` 就退化成「按本段判」，``collect`` 就退化成「把这一段的结果摆成交付树」。
"""
