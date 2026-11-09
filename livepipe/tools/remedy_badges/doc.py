"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """补救：按修好的解析层把**已经交付出去的树**重算一遍 —— 先给账、后动手。

问题（2026-10-14 用户报告）
--------------------------
``out/风云T7_20260826_01/抖音/all_users_正面_3张以上/初___-5/`` 里 1.jpg 与 2.jpg 的
粉丝团等级标不一样（``白___-14/`` 更明显：1/2/3 三个等级、还有几张压根没标）——
按「(昵称, 等级标) 才是一个账号」的口径，这些图**不该待在同一个目录里**。

根因在解析层（已修，见 ``parse_live_comments.dedup_comments`` /
``normalize_badge_alias``）：一条评论的 ``badge_text`` 从前只取**去重后留下的那一帧**
的读数，而留下哪一帧是按**正文清晰度**挑的（最早 / 抖动最小），跟徽章有没有检出来毫无
关系 —— 同一条评论在**别的帧**上明明读到了 ``◆19``，却跟着那些帧一起被丢掉。修完之后
``白***`` 那 14 张会按读数分成 ◆19 / ◆21 / 读不到 三档。

本脚本干的事
------------
**不重新 OCR、不重新调模型**，只用已经躺在段目录里的中间产物（``rows.jsonl`` +
``judgments.jsonl`` 判定缓存）把交付树重算一遍，而且分两拍：**先印单子，人看过再落盘**。

  ① 冻结现状：读交付树的 ``manifest.json``（每张图按 `iden` 记**身份**，不读字节）
  ② 预检：七段的 ``rows.jsonl`` 都在（⇒ 零 OCR）；判定缓存对新评论的覆盖率
     （100% ⇒ 零模型调用，此时连 ``NLP_API_KEY`` 都不用设）
  ③ 重算：parse（零 OCR；**例外**见下）→ filter（只读缓存）→ qualify（跨段合计 ⇒ 账号重新分档）
  ④ 对账：印「旧目录 → 新档」的拆分明细 + 逐图归属，**特别是**因为拆分而掉到
     ``qualify.min_positive``（3 张）以下、**不再交付**的那几档 —— 这是本脚本唯一
     可能**丢图**的地方，必须人看过再落盘
  ⑤ ``--apply`` 才动交付树：export → collect（force）→ 按 `iden` 核账：每张旧图要么
     在新树里找得到，要么在④的单子里，一张都不许凭空消失

跑法（**必须在仓库根的 ``Embodied/`` 下跑**）
------------------------------------------
``output.dir`` 是相对路径，基准目录 = **当前工作目录**：在 ``livepipe/`` 下跑会解析到
另一个不存在的 ``livepipe/out/…`` 里去（预检第一步就会拦住）。::

    cd /home/puyu/github/LiveMonitorMockScale
    python3 -m livepipe.remedy_badges livepipe/风云T7.json               # 只印单子
    python3 -m livepipe.remedy_badges livepipe/风云T7.json --show 初___-5,白___-14
    NLP_API_KEY=… python3 -m livepipe.remedy_badges livepipe/风云T7.json --apply

**只改阈值**（``qualify.min_positive``）时加 ``--no-parse``：读数一个字节都没变，没必要再回原帧
重读一遍（七段约半小时）。此时单子直接吃上一次 ``parse`` 留下的 ``comments.jsonl``，脚本会把它的
**时间戳**印出来 —— 复用别人的旧产物是可以的，但得**看得见**它有多旧。

单子同时写成 ``<output.dir>/remedy_plan.json``（全量、机器可读）。**零 OCR、零模型调用**
的前提没满足时脚本停在预检，不会写交付树。

**「零 OCR」的唯一例外**：``parse.badge_reread`` 生效时，parse 会按每行的**药丸带**回原帧
重读等级标（只跑识别、跳过 det，见 ``parse_live_comments`` 5b 节）—— 它**不重抽行**（行还是
``rows.jsonl`` 里那些），只是给没有等级标读数的评论补一次读数。这一段要几十秒/段，且要视频
还在原地；视频没了会当场降级跳过（不报错）。写 ``badge_reread: false`` 就回到严格零 OCR；
**不写这个键**（默认 ``null``）则跟着 ``account_key`` 走：``nickname_level`` /
``nickname_level_if_masked`` ⇒ 开，``username`` ⇒ 关。本脚本逐段印出实际是开是关。
"""
