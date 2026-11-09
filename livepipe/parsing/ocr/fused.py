"""livepipe.parsing.ocr.fused — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..constants import TALL_BLOCK_FRAC
from ..constants import WRAP_GAP_RATIO
from ..constants import WRAP_MIN_GAP_RATIO
from ..ocr.grouping import _merge_straddled_lines
from ..ocr.lines import _is_wrapped_shape
from ..text.patterns import _COLON_RE



def split_fused_rows(sockets: dict, panel: dict) -> list[tuple]:
    """``group_rows`` 的结果 → ``[(socket, blocks)]``，并把「一个槽里并了两行」拆回两行。

    现象（用户报 t=107.2 的第 7 条）：`苏***：续航真够跑` 和 `晨***：座椅支撑到位` 是两条
    评论，标注图上却是**一行** —— 一个框圈住两行、正文粘成一串。seg4 实测 515 行里 35 行
    （6.8%）都这样，不是个例。

    为什么会并：两条相邻评论的行中心本来就差**一个行距**，而``group_rows`` 的槽位判定是
    「离哪条网格线近就归哪一槽，偏超过半个行距才丢」。手机 App 面板逐帧滚动、**同一帧里
    相位还会上下漂**（实测上半屏 −10px、下半屏 +16px）⇒ 网格线正好落在两行**中间**时，
    两行各自偏 ~0.5 行距、都算「没超」，于是一起归进同一槽：``assemble_row`` 按 x 拼文字、
    框取并集（h≈1.5 个行距 + 字高），下游就当一条评论。

    拆法只看**几何**：块高超过 ``TALL_BLOCK_FRAC`` 个行距、且块按 y 聚成**两簇**（簇间空档
    > 0.3 个行距；同一行的碎片空档只有几个 px，不会被拆），上簇归本槽、下簇归整数槽 ``k+1``。

    **它是兜底，不是主治**：并块的根因是 ``row_pitch`` 丢精度（53.5 被归整成 54，每行
    多漂 0.5px —— 见 ``_apply_panel_override``）。行距对了 seg4 十八帧一个并块都没有；
    这里防的是行距**真错了**的素材（换平台/换缩放、自标定置信度不够而档案例子又不准）。

    **下簇一律当另一行**，不按「有没有昵称冒号」猜续行：折行续行跟首行的间距是 0.15~0.73
    个行距（``_is_true_wrapped_line``），那样并出来的块高最多 1.1 个行距，**进不了这里**；
    而这个版式里没有冒号的行也是真评论（昵称被遮挡，如 ``外观真耐看``），拿冒号当续行判据
    会把一条真评论并进上一条的正文里 —— 比多一条匿名评论更糟。槽 ``k+1`` 已有别的块时分
    三种：**同一视觉行**的碎片（yc 差 < 0.4 行距，并块只吞掉下一条评论的最左一块）并进去；
    坐的是下簇这一行的**折行续行**（yc 差落在折行间距闸里、无冒号、形状是续行 —— 见
    ``_is_wrap_occupant``）⇒ 照拆，续行顺延到小数槽 ``k+1.5``；**另一条行**（yc 差 ≈ 一个
    行距）就**整槽不拆**、保持原样 —— 兜底宁可少拆也不能把两条真评论粘成第三条假评论
    （实测相位偏半个行距时会这么并）。

    拆完还有**相反方向**的一步 ``_merge_straddled_lines``：同一视觉行被网格线切进两个整数槽
    时要并回去。顺序不能反 —— 拆之前那个槽里往往还压着下一条评论的块（实测 socket 9 里既有
    ``167 161``（y 496）又有下一条的 ``***：``（y 548）），合并闸看到的「行中心」被拉低
    半个行距，就判不出「同一行」，这一对永远并不上。
    """
    return sorted(_merge_straddled_lines(
        dict(_split_fused_sockets(sockets, panel)),
        panel["y_phase"], float(panel["row_pitch"])).items())


def _is_wrap_occupant(blocks: list, diff: float, pitch: float, panel: dict) -> bool:
    """目标槽 ``k+i`` 里坐的是不是**下簇这一行的折行续行**（而不是另一条评论）？

    ``_split_fused_sockets`` 原来的闸是「目标槽被占、且 y 差 > 0.4 行距 ⇒ 放弃拆」——
    防的是把两条真评论硬粘成第三条假评论。但它把**续行**也一起挡了：续行落在目标槽时，
    它跟下簇首行的 y 差是**折行间距**（0.15~0.73 个行距），正好过不了 0.4 那道闸。

    现场（风云 A9L 视频号 seg1 t≈57，用户报的「第一个评论两个评论识别为一个」）：
    平滑滚动帧的相位估偏半个行距 ⇒ ``恋恋：…`` 与 ``和和睦睦：…`` 并进同一槽，下簇要去的
    槽 ``k+1`` 坐着 ``来蹲守``（和睦睦那条的续行，y 差 0.64 个行距）⇒ 整槽不拆，两条评论
    粘成 ``恋恋：…粉丝和和睦睦：…``。

    判据三条，都取**从严**（分不清就返回 False，照旧不拆）：
      * y 差落在折行间距闸里（``wrap_min_gap_ratio``~``wrap_gap_ratio`` 个行距）——
        两条评论的行中心差 ≈1 个行距，进不来；
      * 块里**没有冒号**——带冒号的是「昵称：正文」，那是另一条评论（续行里的时间冒号
        这种边角宁可漏，按旧行为不拆）；
      * **最左**那块形状是续行（``_is_wrapped_shape``：贴左沿 / 缩进进正文列左边）。
    """
    gap_ratio = float(panel.get("wrap_gap_ratio", WRAP_GAP_RATIO))
    min_ratio = float(panel.get("wrap_min_gap_ratio", WRAP_MIN_GAP_RATIO))
    if not (pitch * min_ratio <= diff <= pitch * gap_ratio):
        return False
    if any(_COLON_RE.search(b[4]) for b in blocks):
        return False
    if panel.get("x_text") is None:       # 夹具只给行网格时松手（形状判不了 ⇒ 不拆）
        return False
    left = min(blocks, key=lambda b: b[0])
    return _is_wrapped_shape(left[0], left[2], left[4], panel)


def _split_fused_sockets(sockets: dict, panel: dict) -> list[tuple]:
    """``split_fused_rows`` 的前半步：只拆「一个槽里并了两行」，不动跨线的同一行。"""
    pitch = float(panel["row_pitch"])
    items = list(sockets.items())
    fused = []
    for sock, blocks in items:
        if len(blocks) < 2:
            continue
        _, box = assemble_row(blocks)
        if box[3] - box[1] > pitch * TALL_BLOCK_FRAC:
            fused.append(sock)
    if not fused:
        return sorted(items)
    out: dict = {s: list(b) for s, b in items}
    for sock in fused:
        blocks = out.pop(sock, [])
        if len(blocks) < 2:
            out.setdefault(sock, []).extend(blocks)
            continue
        order = sorted(blocks, key=lambda b: (b[1] + b[3]) / 2)
        cys = [(b[1] + b[3]) / 2 for b in order]
        # 按 y 聚簇（相邻块的 y 空档 ≥ 0.3 行距就断）—— 只切最大的那一刀不够：
        # 实测有「三块两块是行、中间夹个徽章数字」的并块，切一刀剩下那半仍然超高。
        clusters: list[list] = [[order[0]]]
        for k in range(len(order) - 1):
            if cys[k + 1] - cys[k] >= pitch * 0.30:
                clusters.append([])
            clusters[-1].append(order[k + 1])
        if len(clusters) < 2:                     # 同一行的碎片并排，不是两行
            out.setdefault(sock, []).extend(blocks)
            continue
        # 落槽前先看目标槽里**已经是谁**：yc 差不到 0.4 个行距 = 同一条视觉行被 OCR 切开的
        # 另一半（并块经常只吞掉下一条评论的**最左一块**，其余块还在 k+1 上那半截），
        # 那就并进去；差近一个行距 = 另一条行 ⇒ **放弃拆、保持原样**（宁可不拆也不能拆错）：
        # 实测相位偏半个行距时硬并会把两条真评论粘成第三条假评论
        # （`11用***：这是在4s店直播？` 被并进下面那条 `主播风云新能源汽车：…`）。
        # 第三种（``_is_wrap_occupant``）：坐着的是**下簇这一行的折行续行** ⇒ 不是障碍，
        # 续行顺延到小数槽 ``target+0.5``（它本来就该排在首行后面），拆继续。
        plan: list[tuple] = [(sock, clusters[0])]      # 上簇留本槽（本槽已腾空）
        blocked = False
        follow: tuple | None = None                    # (原槽, 顺延到的小数槽, 续行块)
        for i, cl in enumerate(clusters[1:], start=1):
            target = sock + i
            push = None
            occ = out.get(target)
            if occ:
                oy = sum((b[1] + b[3]) / 2 for b in occ) / len(occ)
                cy = sum((b[1] + b[3]) / 2 for b in cl) / len(cl)
                diff = oy - cy
                if abs(diff) > pitch * 0.40:
                    if (i == len(clusters) - 1
                            and _is_wrap_occupant(occ, diff, pitch, panel)
                            and (target + 0.5) not in out):
                        push = target
                    else:
                        blocked = True
                        break
            plan.append((target, cl))
            if push is not None:                       # 只在最后一簇（循环也就到此为止）
                follow = (push, push + 0.5, occ)
        if blocked:
            out.setdefault(sock, []).extend(blocks)
            continue
        if follow is not None:
            del out[follow[0]]
            out.setdefault(follow[1], []).extend(follow[2])
        for target, cl in plan:
            out.setdefault(target, []).extend(cl)
    return sorted(out.items())


def badge_box_of(badge_boxes: dict | None, socket) -> list[int] | None:
    """把一个槽位里所有徽章块的并集取出来（没记录则 None）。"""
    boxes = (badge_boxes or {}).get(socket)
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def badge_text_of(badge_boxes: dict | None, socket) -> str | None:
    """把一个槽位里所有徽章块**读出来的文字**按 x 拼起来（没记录 / 只剩空白则 None）。

    徽章 = 左边的菱形图标 + **粉丝团等级数字**（``◆6`` / ``◆29``）。图标和数字常被 OCR
    切成两块（``◆`` + ``6``），所以按 x 拼回一串再交给 ``_badge_digits`` 取数字。
    这串读数就是这条评论的**等级标**——同一个人的昵称与等级标绑定，所以它是身份证据，
    不是一个纯装饰字段（见 ``_badge_renames``）。
    """
    boxes = (badge_boxes or {}).get(socket)
    if not boxes:
        return None
    s = "".join(str(b[4]) for b in sorted(boxes, key=lambda b: b[0])).strip()
    return s or None


def _offset_box(box: list[int], dx: int, dy: int) -> list[int]:
    """裁切内坐标 → 原图坐标。"""
    return [int(box[0]) + dx, int(box[1]) + dy, int(box[2]) + dx, int(box[3]) + dy]


def assemble_row(blocks: list) -> tuple[str, list[int]]:
    """把同一槽位的文字块按 x 拼成整行，返回 (text, box_xyxy)。"""
    blocks = sorted(blocks, key=lambda b: b[0])
    text = "".join(b[4] for b in blocks)
    x1 = min(b[0] for b in blocks)
    y1 = min(b[1] for b in blocks)
    x2 = max(b[2] for b in blocks)
    y2 = max(b[3] for b in blocks)
    return text, [int(x1), int(y1), int(x2), int(y2)]
