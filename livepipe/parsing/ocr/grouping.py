"""livepipe.parsing.ocr.grouping — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..constants import ASCII_FRAGMENT_RE
from ..constants import OVERLAY_STRIP_FRAC
from ..constants import WRAP_GAP_RATIO
from ..constants import WRAP_MIN_GAP_RATIO
from ..constants import WRAP_TEXT_LEFT_SLACK
from ..ocr.grid import snap_to_grid
from ..ocr.lines import _in_badge_gutter
from ..ocr.lines import _is_true_wrapped_line
from ..ocr.lines import _is_wrapped_line
from ..ocr.lines import _is_wrapped_shape
from ..text.cleaning import _clean
from ..text.cleaning import _is_junk
from ..text.patterns import _COLON_RE



def _text_left_candidates(rows: dict, panel: dict) -> list[tuple]:
    """``wrap_at_text_left`` 面板（微博）的**折行候选**块 → ``[(行中心 y, 槽位, 块), …]``（按 y 升序）。

    为什么这类面板走不了 ``_is_wrapped_line`` 那条形状入口：那条入口的前提是「续行从
    **徽章列左边**起、普通行从徽章列右边起」。微博**没有左侧徽章列**（等级标是跟在昵称
    右边的「榜N」药丸，见 ``weibo.json`` 的 ``x_text`` 注释）—— 续行跟首行一样从正文列
    左沿起，实测折行首块 x1 ∈ [38, 43]、独立评论首块 x1 ∈ [38, 45]，**两个分布完全重叠**，
    形状上分不开（``WRAP_TEXT_LEFT_SLACK`` 那段有全量数字）。分得开的是**间距**：
    折行占 0.59~0.64 个行距、独立评论占 0.97~1.01。

    所以候选的筛选只剩「像正文的一行」这一件事：没有冒号、不是纯噪声、从正文列左沿起。
    **块留在 ``rows`` 里**，由调用方按间距决定去留 —— 落选的照旧当普通行，**不能像抖音
    那样丢掉**：微博没有徽章列，「没有冒号」多半是昵称的冒号被 OCR 吃掉，那是一条**真评论**
    （实测这种行 216 对，逐条看过都是独立评论或系统行残句，不是续行）。
    """
    xmax = int(panel["x_text"]) + WRAP_TEXT_LEFT_SLACK
    out: list[tuple] = []
    for sock, blocks in rows.items():
        for b in blocks:
            if b[0] > xmax or _COLON_RE.search(b[4]) or _is_junk(b[4]):
                continue
            out.append(((b[1] + b[3]) / 2, sock, b))
    out.sort(key=lambda t: t[0])
    return out


def _colon_wrap_candidates(rows: dict, panel: dict) -> list[tuple]:
    """**带冒号、但形状像折行**的块 → ``[(行中心 y, 槽位, 块), …]``（按 y 升序）。

    为什么需要：手机 App 版里**主播的评论是两行的**，第二行以时间开头、带冒号
    （``18：45盛大启幕！敬请期待！``）。``_is_wrapped_line`` 的冒号闸把它当「昵称：正文」
    挡回去 ⇒ 三个后果都在用户报的帧上出现过：

      * 它落成**第二条评论**，昵称被读成 ``18``（`主播风云新能源汽车：风云T7上市发布会`
        + `18：45盛大启幕！敬请期待！` 本来是一条）；
      * 这两行间距只有 **0.66~0.68 个行距**（比评论行距紧），网格线正好卡在中间时它们
        并进同一槽 ⇒ 一个框圈两行、正文粘成
        ``18：45盛大启幕！敬请期待！主播风云新能源汽车：风云T7上市发布会``（两次）。

    **不能、也不该塞进 ``_is_wrapped_line`` 的硬入口**：那一类块没过间距闸就被**丢**，
    而左对齐 + 带冒号的块里有真评论（昵称没有等级徽章、昵称顶到面板左沿，实测
    ``苏***：续航真够跑`` x1=30）。所以这里跟 ``_text_left_candidates``（微博）一样走
    **软入口**：块先留在 ``rows`` 里，由调用方按间距闸定去留，**落选的照旧当普通行**。
    """
    out: list[tuple] = []
    for sock, blocks in rows.items():
        for b in blocks:
            if not _COLON_RE.search(b[4]):
                continue
            if not _is_wrapped_shape(b[0], b[2], b[4], panel):
                continue
            out.append(((b[1] + b[3]) / 2, sock, b))
    out.sort(key=lambda t: t[0])
    return out


def _merge_straddled_lines(rows: dict, phase: float, pitch: float) -> dict:
    """把「同一视觉行被行网格切进两个整数槽」的碎片并回一槽。

    现象（seg4 t=0 的现场，两条一模一样的纯表情评论）：一行评论被 OCR 切成两三块，块高
    略有差别 ⇒ 块中心差几个 px。逐块定槽（``snap_to_grid``）时，若网格线正好从这个缝里
    穿过去，两块就各自落进相邻的整数槽：实测 ``***``（yc 504.5，槽号 8.448）与
    ``167 161``（yc 508.5，槽号 8.522）—— 同一个 y、同一行字，却成了 socket 8 和 9。
    后果有三条，没有一条是小的：

      * 一行变成**两条评论**（``assemble_row`` 各拼各的），下游去重按正文做 key，两条都对
        不上 ⇒ 交付里两张 ROI 一模一样、账号数虚高（实测 ``***`` 那一行的 ``[打call]×4``
        被算了两条）；
      * 抢走的那一槽本来是**空槽**，``sweep_emoji_slots`` 就不去补扫了 ⇒ 真评论被吃掉
        （同一帧 y≈1239 的 ``[爱心]×4`` 就是被这行的错位占槽挡掉的）；
      * 槽位错位会传给「相邻性」判据（``require_adjacent_socket``），把不相关的行串起来。

    判据只看几何、且**只在整数槽之间**做（折行/续行一律挂 ``k±0.5`` 小数槽，见下面 units
    那段，所以动不到它们）：两槽的行中心差 < 0.30 个行距、且 y 区间**相交** ⇒ 同一条视觉行。
    0.30 行距这道闸同时把「并块行 + 它的下一条评论」挡在外面（中心差 ≈ 一个行距），所以
    不会把两条真评论并起来。合并后按**整行块中心**重定时槽（网格线离谁近归谁），保证
    结果与「先聚行、再定槽」的理想做法一致。
    """
    ints = [k for k, blks in rows.items()
            if blks and abs(k - round(k)) < 1e-6]
    if len(ints) < 2:
        return rows
    info = {}
    for k in ints:
        ys = [(b[1], b[3]) for b in rows[k]]
        info[k] = (min(y[0] for y in ys), max(y[1] for y in ys),
                   sum((b[1] + b[3]) / 2 for b in rows[k]) / len(rows[k]))
    order = sorted(ints, key=lambda k: info[k][2])
    groups: list[list] = [[order[0]]]
    for k in order[1:]:
        a, b = info[groups[-1][-1]], info[k]
        overlap = min(a[1], b[1]) - max(a[0], b[0])
        if b[2] - a[2] < pitch * 0.30 and overlap > 0:
            groups[-1].append(k)
        else:
            groups.append([k])
    out = dict(rows)
    for grp in groups:
        if len(grp) < 2:
            continue
        blocks = [b for k in grp for b in rows[k]]
        yc = sum((b[1] + b[3]) / 2 for b in blocks) / len(blocks)
        target = min(grp, key=lambda k: abs(phase + k * pitch - yc))
        for k in grp:
            del out[k]
        out[target] = blocks
    return out


def group_rows(ocr_items: list, panel: dict, badge_boxes: dict | None = None) -> dict[int, list]:
    """把 OCR 文字块按行槽位归组。

    裁切图已经是纯评论滚动区，所以行中心相对裁切顶端的偏移就能定槽位。
    同一行可能被 OCR 切成「昵称：」和「正文」两块，甚至「昵称」+「图标」+「正文」，
    它们 y 中心接近 → 落到同一槽位；按 x 排序后拼成整行。

    ``badge_boxes``：给了就把**被丢掉的等级徽章块**按槽位记进去
    （``{socket: [[x1,y1,x2,y2,读数], …]}``），让调用方能拿它把 ROI 的左边界扩到徽章 ——
    徽章块本身进不了正文（见 ``_in_badge_gutter``），但它是「这一行长什么样」的一部分，
    出图/裁图要完整的一行；它的**读数**（``◆6`` 里的 ``6``）是这条评论的**等级标**，
    是身份证据（见 ``_badge_renames``）。

    **折行续行**有两个入口，落槽规则相同（都是 ``k±0.5`` 的小数槽位），看面板有没有
    左侧徽章列：

      * 有徽章列的平台（抖音）：``_is_wrapped_line`` 按**形状**认（续行在徽章列左边），
        认下的块最后由 ``_is_true_wrapped_line`` 的间距闸定去留，**没过的照旧丢**；
      * 没有徽章列的平台（微博，``panel["wrap_at_text_left"]``）：形状判据天生失效
        （续行与首行同左沿），改由 ``_text_left_candidates`` 收集、**同一个间距闸**定去留，
        没过的**留成普通行**（见那边说明为什么不能丢）。

    ``panel["x_ignore_from"]``（自标定给的，可选）：这个 x 以右的文字块是**直播间浮层**
    （商品讲解卡 / 预约按钮），不是评论 —— 直接丢。它压在评论列右边，不丢就会被 OCR 读成
    评论正文（实测拼出 ``叮***：杨紫宝宝NB更美更如讲解中`` 这种尾巴）。

    还有一条**不靠标定**的同类过滤：右端条带里的**纯 ASCII 短碎片**（见
    ``ASCII_FRAGMENT_RE`` / ``OVERLAY_STRIP_FRAC``）是品牌水印/logo 被读出来的，直接丢。
    为什么不交给 ``x_ignore_from``：水印在标定采样帧里常常压根没被 OCR 到（实测该素材
    4 帧标定窗里右半区一个块都没有），标定器看不见它，自然设不出界。
    """
    pitch = panel["row_pitch"]
    phase = panel["y_phase"]
    gap_ratio = float(panel.get("wrap_gap_ratio", WRAP_GAP_RATIO))
    min_gap_ratio = float(panel.get("wrap_min_gap_ratio", WRAP_MIN_GAP_RATIO))
    x_ignore = panel.get("x_ignore_from")
    # 右端条带的左界（**裁切内** x，块坐标也是裁切内）。没给 rect 就关掉这条过滤 ——
    # 自检夹具只给行网格不给 rect（见 ``_selftest_roi``），别让夹具为了新规则改签名。
    _rect = panel.get("rect")
    overlay_strip_x = (_rect[2] - _rect[0]) * OVERLAY_STRIP_FRAC if _rect else None
    rows: dict[float, list] = {}
    shaped: list[tuple] = []           # 形状像整行的左对齐块：普通行都归好槽位后再定夺
    for box, text, score in ocr_items:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        if x_ignore is not None and x1 >= x_ignore:   # 浮层文字，不是评论
            continue
        if (overlay_strip_x is not None and x1 >= overlay_strip_x
                and ASCII_FRAGMENT_RE.match(_clean(text))):
            continue      # 右端条带 + 纯 ASCII 短碎片 = 水印/logo，不是评论（见 ASCII_FRAGMENT_RE）
        yc = (y1 + y2) / 2                # 相对裁切顶端的行中心
        socket = snap_to_grid(yc, phase, pitch)
        # 行中心偏离槽位超过半个行距 → 不属于任何稳定行，丢弃（碎块/噪声）
        if abs(yc - (phase + socket * pitch)) > pitch * 0.5:
            continue
        if _in_badge_gutter(x1, x2, text, panel):   # 左侧徽章列碎片，丢弃（整行正文不能丢）
            if badge_boxes is not None:
                # 框 + **读出来的文字**。文字进不了正文，但它是**等级标**的读数，而等级标是
                # 身份的一部分（同一个人的昵称与等级标绑定 ⇒ 等级标一致才是同一个人，
                # 见 ``_badge_renames``）。从前只留框、把读数丢了，等于把身份证据丢掉。
                badge_boxes.setdefault(socket, []).append(
                    [int(x1), int(y1), int(x2), int(y2), _clean(text)])
            continue
        if _is_wrapped_line(x1, x2, text, panel):
            shaped.append((yc, (x1, y1, x2, y2, text, score)))
            continue
        rows.setdefault(socket, []).append((x1, y1, x2, y2, text, score))

    # 「形状像整行」的块分两种，靠**跟上一行的间距**分开（``_is_true_wrapped_line``，
    # 两个方向都卡：上沿挡左对齐横幅、下沿挡「同一行被 OCR 切开的另一半」）。
    # 非续行一律按旧规则丢掉，所以横幅认不认识都不影响结果。
    #
    # **必须按 y 顺序逐条判、认下的立刻并进 ``centers``**：一条评论能折成 3 行（实测
    # t=1504 `影子，*超级：须感谢平台…` + 两行续行），第 3 行离**首行**有 0.96 个行距 ——
    # 若只拿普通行当「上一行」，它会被上沿当横幅挡掉，只留前两行。逐条串起来才对。
    centers = sorted((b[1] + b[3]) / 2 for blks in rows.values() for b in blks)
    wrapped: list[tuple] = []
    for yc, blk in sorted(shaped, key=lambda t: t[0]):
        if _is_true_wrapped_line(yc, centers, pitch, gap_ratio, min_gap_ratio):
            wrapped.append((yc, blk))
            centers.append(yc)

    # ★ 没有左侧徽章列的平台（微博，``panel["wrap_at_text_left"]``）：**形状入口在那类
    #   面板上天生失效**（续行跟首行一样从正文列左沿起、两个 x1 分布完全重叠），所以改走
    #   **间距入口** —— 上面那趟是「先按形状挑出来、再按间距定去留」，这里是「普通行都归好
    #   槽了、拿到行中心之后再按间距挑折行」。同一个闸门（``_is_true_wrapped_line``）、
    #   同一套下游（小数槽位 + 认领同线碎片），只是入口不同。
    #   **挑不中的照旧是普通行**（上面那趟是丢掉）：微博没有徽章列，这些行多半是「昵称的
    #   冒号被 OCR 吃掉」的真评论 —— 丢了就是真丢数据（实测 216 对，逐条看过）。
    tl_wrapped: list[tuple] = []
    if panel.get("wrap_at_text_left"):
        for yc, sock_k, blk in _text_left_candidates(rows, panel):
            if not _is_true_wrapped_line(yc, centers, pitch, gap_ratio, min_gap_ratio):
                continue
            rows[sock_k].remove(blk)        # 从普通槽里摘出来，下面挂小数槽
            if not rows[sock_k]:
                del rows[sock_k]
            tl_wrapped.append((yc, blk))
            centers.append(yc)              # 认下的立刻当「上一行」：一条评论能折 3 行

    # ★ **带冒号的折行**（主播那种两行评论的第二行，见 ``_colon_wrap_candidates``）：同一个
    #   软入口 —— 过了间距闸就摘出来当续行，没过照旧当普通行。必须在普通行都归好槽之后跑：
    #   它有两种现场，一种是自己单独占一槽（``18：45盛大启幕！敬请期待！`` 落成 socket k），
    #   另一种是**已经跟上一条并进同一槽**了（网格线卡在两行中间，h≈1.5 个行距），这里按
    #   **块**处理，两种都捞得出来。
    colon_wrapped: list[tuple] = []
    for yc, sock_k, blk in _colon_wrap_candidates(rows, panel):
        if not _is_true_wrapped_line(yc, centers, pitch, gap_ratio, min_gap_ratio):
            continue
        rows[sock_k].remove(blk)
        if not rows[sock_k]:
            del rows[sock_k]
        colon_wrapped.append((yc, blk))
        centers.append(yc)              # 认下的立刻当「上一行」：一条评论能折 3 行

    # 认领**同一视觉行**的其它碎片：续行常被行内的表情间隙切成几块（`杨紫 杨紫 杨紫`
    # 中间夹表情 ⇒ 5 个块），只有**最左**那块缩进、形状像续行；右边的块 x1 落在正文列里、
    # 单看和普通行分不开。不认领的话它们会各自落成一条假行（实测 `None | 杨紫杨紫…`）。
    # **只在续行判定通过后**认领 —— 横幅那种「同一行被切开、但整行不是续行」的情形
    # （``_is_true_wrapped_line`` 不通过）不受影响：头部照旧丢、尾部照旧留成普通行。
    # 第三项 ``force``：软入口（微博 / 带冒号的折行）认下的续行**必须**挂小数槽位 ——
    # 它们没有「无冒号」这条文本证据，下游只能靠小数槽位认折行（见下面那段）。
    units: list[tuple] = []
    for force, group in ((False, wrapped), (True, tl_wrapped), (True, colon_wrapped)):
        for yc, blk in group:
            sibs = []
            for sock_k in list(rows):
                keep = [b for b in rows[sock_k]
                        if abs((b[1] + b[3]) / 2 - yc) > pitch * 0.4]
                sibs += [b for b in rows[sock_k] if abs((b[1] + b[3]) / 2 - yc) <= pitch * 0.4]
                if keep:
                    rows[sock_k] = keep
                else:
                    del rows[sock_k]
            units.append((yc, [blk] + sibs, force))

    # 折行续行的落槽。行网格是给**评论行**用的（一槽一个行距），而折行只占半个行高，
    # 天生卡在两槽之间 —— 直接吸附必然和它的首行（或下一行）撞进同一个槽，
    # ``assemble_row`` 一拼就把两行文字搅成一句（'有成就感…！5念口：一集一集…'），
    # 既丢了续行也毁了首行（bug-229）。所以给它一个**小数槽位**：撞上哪个槽，就按 y
    # 决定插在它前面还是后面（k±0.5）。这样排序仍是屏幕从上到下，
    # ``parse_live_rows`` 的相邻性判据也能认（见那边的 ``adjacent``）。
    #
    # ⚠️ 软入口（微博 ``tl_wrapped``、带冒号的折行 ``colon_wrapped``）**不许复用空出来的
    # 整数槽 k**：k 正是它自己刚腾出来的那一个（折行的 y 就落在下一个整数槽里），复用回去
    # 它就又成了一行普通行 —— 而下游正是靠「**小数**槽位」认折行的（``parse_live_rows`` 的
    # ``_wrapped_socket``：微博没有徽章列，``_has_own_badge`` 那条路在那里恒为真，认不出
    # 折行；带冒号的折行更糟，文本层看着就是「昵称：正文」，会被当成另一条评论）。
    # 所以这两类一律挂 k±0.5（首行在 k-1，折行落在 k-0.5，屏幕顺序不变）。
    force_fractional = bool(panel.get("wrap_at_text_left"))
    for yc, blks, force in units:
        k = snap_to_grid(yc, phase, pitch)
        if k not in rows and not (force_fractional or force):
            rows[k] = list(blks)
            continue
        below = yc > (phase + k * pitch)
        for step in (0.5, 0.75, 1.5, 1.75, 2.5, 2.75):
            cand = k + step if below else k - step
            if cand not in rows:
                rows[cand] = list(blks)
                break
        else:                              # 兜底：真挤不下就并回原槽（宁可乱序也别丢行）
            rows.setdefault(k, []).extend(blks)
    return rows
