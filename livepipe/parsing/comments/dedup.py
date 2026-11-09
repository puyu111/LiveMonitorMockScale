"""livepipe.parsing.comments.dedup — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from collections import Counter

from ..comments.build import _decisive
from ..comments.build import _same_user
from ..common.log import _log
from ..common.text import norm_text
from ..ocr.pills import PILL_MIN_AGREE
from ..ocr.pills import _pill_vote
from ..text.badges import _badge_digits



# 「被 rid 链断开的**兄弟记录**」之间并票的最大**抽帧序号**差（不是秒 —— adaptive 模式的
# 抽帧步长随画面动/静伸缩，按秒写会随素材变松变紧，序号差才是「隔了几个抽帧」）。
#
# 链为什么会断：``assign_row_ids`` 只跟**紧邻的上一帧**对齐，中间缺一帧（那一行 det 掉了）
# 就把后面的行对到别的行上，同一条评论于是前后各拿一个新的 rid。所以断一次 ⇒ 两条记录
# 最多差 2 个抽帧（缺的那帧夹在中间）；连着缺两帧 ⇒ 差 3。取 3 就是「最多缺两帧」。
SIBLING_FRAME_GAP = 3


def _pool_sibling_votes(out: list[dict], rr: list[list[str]], st: dict) -> None:
    """把**被 rid 链断开的兄弟记录**的药丸带票并到一起（只并票，不并记录）。

    dedup 原有的池化只认「命中同一个身份键（rid / 文本键）」，接不住这一类：纯表情评论
    （正文为空）**没有文本键** —— ``body`` 为空就不加 ``txt:`` 键，这是故意的（见 ``pos:``
    那一段：空正文的评论只能靠位置认，否则一屏的纯表情评论会互相撞成一条）；rid 链一断
    它就只剩位置键，于是**同一条评论的两张票落在两个记录里**，各自只有一票，
    ``_pill_vote`` 的「≥2 票且过半」永远过不了。用户 2026-10-14 报的 1.jpg 就是现场：

      seg4 的 ``好***`` ``[赞]`` 在 t=3762.293（rid 787）读出 ``+7``、在 t=3772.336
      （rid 788）读出 ``+7``；中间那帧（t=3767.314）这一行 det 掉了 ⇒ 链断 ⇒ 两张票
      两档 ⇒ 都作废 ⇒ 交付里这条评论**无标**（而票明明是同一行读出来的）。

    合成一组的条件是**四样全同 + 抽帧相邻**（同 panel、同昵称、同正文、同表情文本，
    序号差 ≤ ``SIBLING_FRAME_GAP``）。正文非空的记录之间不可能满足四样还分成两条
    （``txt:`` 键早把它们并了），所以这条规则实际只在**纯表情评论**上开火。

    再上一道**一致闸**：并进来的票连同自己原有的票必须**众口一词**（全等，且 ≥
    ``PILL_MIN_AGREE`` 票）才并。这一道把「同昵称不同等级的两个人」挡在门外 —— 那正是
    「错归并」的唯一入口（同昵称、同表情、相邻几帧、等级却不同 ⇒ 票不一致 ⇒ 不并，
    宁可漏归一）。反过来，票全等就意味着这几行**读的是同一个等级标**，而「同昵称 +
    同等级标 = 同一个账号」本就是交付层的认定口径（见 account_id），所以并了也不会
    把别人的等级贴上来。

    **只并票、不并记录**：合并记录会改动「这条评论拍到几次」的计数，进而动到交付的张数
    门槛（``qualify.min_positive``）——那是会**少交付图**的一步；并票只影响「读不读得出
    等级标」，只多不少。票是**复制**过去的：原记录那份照旧留在原地，它自己的
    「二次读数准不准」那笔账（``badge_reread_agree/differ``）不受影响。

    有读数的记录**只当施主、不当受主**（受主一律是本来没读数的那些）—— 与 dedup 里
    「只补不改」同一条边界。
    """
    if not any(rr):
        return
    # 每个面板的抽帧序号表：t → 序号。同一条评论的几次拍摄都在同一批抽帧上，
    # 所以序号差就是「隔了几个抽帧」，与抽帧步长的绝对值无关（adaptive 模式下步长会变）。
    rank: dict[object, dict[float, int]] = {}
    for c in out:
        rank.setdefault(c.get("panel"), {}).setdefault(float(c.get("t") or 0.0), 0)
    for m in rank.values():
        for i, t in enumerate(sorted(m)):
            m[t] = i
    groups: dict[tuple, list[int]] = {}
    for i, c in enumerate(out):
        groups.setdefault((c.get("panel"),
                           norm_text(c.get("username") or ""),
                           norm_text(c.get("content") or ""),
                           c.get("emoji_text") or ""), []).append(i)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        rk = rank.get(out[idxs[0]].get("panel")) or {}
        for i in idxs:
            if _badge_digits(out[i].get("badge_text")):
                continue                      # 本来就有读数 ⇒ 一个字节都不动（只补不改）
            t_i = float(out[i].get("t") or 0.0)
            if t_i not in rk:
                continue
            pool: list[str] = []
            for j in idxs:
                t_j = float(out[j].get("t") or 0.0)
                if j == i or t_j not in rk or abs(rk[t_j] - rk[t_i]) > SIBLING_FRAME_GAP:
                    continue
                pool.extend(rr[j])
            vals = {v for v in rr[i] + pool if v}
            if len(vals) != 1 or len(rr[i]) + len(pool) < PILL_MIN_AGREE:
                continue                      # 没兄弟可借 / 票不一致 / 票不够 ⇒ 不并
            rr[i].extend(pool)
            st["badge_reread_pooled"] += 1


def _best_shot(recs: list[dict]) -> dict:
    """同一条评论的几次拍摄里挑「最清楚」的那次：位移最小，其次最早。"""
    with_motion = [r for r in recs if r.get("motion_px") is not None]
    if with_motion:
        return min(with_motion,
                   key=lambda r: (float(r["motion_px"]), float(r.get("t") or 0.0)))
    return min(recs, key=lambda r: float(r.get("t") or 0.0))


def _content_consensus(members: list[dict]) -> dict | None:
    """同一条评论的多次拍摄 → 选**最完整 / 多数**的那一次（正文 + 几何一起换）。

    为什么需要：去重默认留「最早 / 位移最小」的一次，但**最早的那次不一定是完整的那次**。
    手机 App 的评论列表是平滑滚动的：一条两行评论的第一行刚进可视区、折行还在屏幕底下
    时就会被先拍到 —— 那一帧的正文是**截断**的（实测 10.31 视频号 seg1 t=4630.9
    `摸鱼的西红柿`：正文只有 `在泉州开车逛古城，走走停停正`，`合适` 还没滚进来），
    而后面十几帧都是完整的 `…正合适`。同一现象在 seg1 里有 9 条。另外同一句在不同帧
    会被 OCR 读错一个字（`午休`/`牛休`、`开着`/`开看`），多数帧是对的。

    两条判据，都取从严（没有明确多数就返回 ``None``，不猜）：

      ① **完整性**：行数最多的那一档要占这次拍摄的 ≥1/3 且 ≥2 次；档内按正文多数决
         （≥2 票且 ≥2/3）。行数多 ⇒ 折行被拍全了（``lines`` 是解析层合并的折行数）。
      ② **OCR 抖动**：单行评论（或行数档没有多数时）只在**归一化后等长**的正文上投票
         （≥3 次、≥2/3）—— 等长是一道硬闸：吸进下一条评论/系统行的错误变体通常更长，
         进不了这一档（实测 `奇瑞造车造了很多年了666` / `…做到了内再次开播若主播7天`）。

    返回**胜出变体的一次拍摄记录**（不让调用方只换正文不换几何 —— ROI/``t``/``lines``
    必须同源，否则交付裁图会按旧的 t 裁新的框）。
    """
    recs = [c for c in members if (c.get("content") or "").strip()]
    if len(recs) < 2:
        return None
    lines = Counter(int(c.get("lines") or 1) for c in recs)
    lmax = max(lines)
    top = [c for c in recs if int(c.get("lines") or 1) == lmax]
    if lmax > 1 and len(top) >= 2 and len(top) / len(recs) >= 1 / 3:
        cnt = Counter(norm_text(c.get("content") or "") for c in top)
        win, n = cnt.most_common(1)[0]
        if win and n >= 2 and n / len(top) >= 2 / 3:
            return _best_shot([c for c in top
                               if norm_text(c.get("content") or "") == win])
    bodies = {norm_text(c.get("content") or "") for c in recs}
    if len(bodies) > 1 and len({len(b) for b in bodies}) == 1 and len(recs) >= 3:
        cnt = Counter(norm_text(c.get("content") or "") for c in recs)
        win, n = cnt.most_common(1)[0]
        if n / len(recs) >= 2 / 3:
            return _best_shot([c for c in recs
                               if norm_text(c.get("content") or "") == win])
    return None


def dedup_comments(comments: list[dict], stats: dict | None = None, *,
                   reread: dict[int, str] | None = None) -> list[dict]:
    """跨帧去重：同一条评论会连续出现在多帧，只留一次。

    身份优先用 ``rid``（``assign_row_ids`` 的滚动对齐结果）—— 这是唯一能处理
    「纯表情评论」的办法，因为它们的正文被 OCR 丢干净了，文本上根本认不出谁是谁。
    没有 ``rid``（例如从旧的 ``rows.jsonl`` 复算）时退回文本键；此时**空正文的评论
    必须带上位置**参与键，否则同一面板里所有纯表情评论会互相撞成一条。

    留哪一次：默认留**最早**的（``t`` 最小）。但内容触发模式会在行上带 ``motion_px``
    （拍摄时面板的瞬时位移）—— 有它时，同一个 ``rid`` 里改留**位移最小**的那次：滚动
    中途拍的帧 OCR 更糊，而「早」不等于「清楚」（这是 D 补质量那一步）。

    **等级标证据**：留哪一次看的是**正文清不清楚**，跟「这一帧的徽章块有没有被 OCR
    检出来」毫无关系 —— 而徽章块检出率只有三成上下（见 ``BADGE_SPAN`` 那段的实测），
    于是「这条评论本来读到了等级标、偏偏留下的那次没读出来」非常常见。本素材实测：
    8588 条唯一评论里 **952 条**是这样白丢的（有等级标的从 22% 提到 33%）。交付层按
    (昵称, 等级标) 认人 ⇒ 白丢一条就是把两个不同的人并进同一个账号目录（用户 2026-10-14
    报的 `初___-5` / `白___-14` 就是这么来的：同一个目录里的图等级标对不上）。
    所以留下之后再**把被丢掉的那些次读到的等级标补回来**，三条边界：

      * **只补不改**：留下的那条自己有读数就原样不动（不拿多数决去改它 —— 它本来就是
        这条评论的证据，改了只会平白多出一类没验证过的分歧）；
      * **同昵称**才算数。滚动对齐在 rid 链的**尾端**会把下一条顶进来的评论吸进来
        （实测 seg1 rid=13：``醉***：没有车标？`` 一路跟到 t=664 时吸进了
        ``时***：牛上天没？？？``），少加昵称这一格就会把**别人**的等级标记到这条评论上；
      * **平票不补**（见 :func:`_decisive`）。

    为什么是「被丢掉的那些次」而不是「同一个 ``rid`` 的其它次」：对齐会**断链**。实测
    seg5 的 ``白***：喜欢``（t=1727…1747）跨了 537/546/556 **三个 rid**，``◆21`` 那两次
    读数全在 546 上 —— 只按 rid 找证据，正好一条都找不到。而 dedup 自己已经用文本键
    认定这三段是**同一条评论**了，那就用它的判断。

    **药丸带**那一层的票还有个二级情形：同一条评论的两次拍摄被 rid 链**断成两条记录**
    （中间那帧那一行 det 掉了，见 :func:`_pool_sibling_votes`）时，原有的池化接不住
    （纯表情评论没有文本键），于是两张票分在两档、各自一票，投票闸永远过不了 ——
    并票那一步就补这个窟窿（四样全同 + 抽帧相邻 + **众口一词** 才并，只并票不并记录）。

    ``stats``：可选，就地累加取证计数（``badge_filled`` 补回来的条数、
    ``badge_fill_tied`` 因为平票没补的条数、``badge_reread*`` 二次读数的账、
    ``badge_reread_pooled`` 从兄弟记录并票的条数）。

    ``reread``：可选，**二次读数**（``--badge-reread``，见 :func:`reread_pills`）——
    ``{去重前 comments 的下标: 单帧读数}``。面板 OCR 读不出来的那些行在这里补，投票口径
    与上面那套一样（跨帧、多数决、平票不补），但**另立一条账**：它还要回答「这套读数
    准不准」。所以即使某条评论**已经有**面板读数，它的二次读数也照样投票、与面板读数比对
    （统计进 ``badge_reread_agree`` / ``badge_reread_differ``；票不够/平票则进
    ``badge_reread_novote`` —— **那不是分歧**，别并进 differ）—— 那是这一层唯一能自证的
    地方，值只写进「本来没读数」的那些条（只补不改）。
    """
    # 同一个 rid 里「最清楚」的那次 = 瞬时位移最小（只有内容触发模式带 motion_px）。
    best_motion: dict[str, float] = {}
    for c in comments:
        rid, m = c.get("rid"), c.get("motion_px")
        if rid is not None and m is not None:
            k = f"{c.get('panel')}#{rid}"
            if k not in best_motion or m < best_motion[k]:
                best_motion[k] = m
    st = stats if stats is not None else {}
    st.setdefault("badge_filled", 0)
    st.setdefault("badge_fill_tied", 0)
    st.setdefault("badge_reread", 0)          # 二次读数补回来的条数
    st.setdefault("badge_reread_tied", 0)     # 二次读数平票/不够票 ⇒ 没补
    st.setdefault("badge_reread_agree", 0)    # 已有面板读数、二次读数与它一致
    st.setdefault("badge_reread_differ", 0)   # 已有面板读数、二次读数与它不一致
    st.setdefault("badge_reread_novote", 0)   # 已有面板读数，但二次读数**投不出票**（≠不一致）
    st.setdefault("badge_reread_pooled", 0)   # 票是从**兄弟记录**（rid 链断开的那条）并过来的
    seen: dict[str, int] = {}          # 身份键 -> 它落在 out 里的下标
    out: list[dict] = []
    evidence: list[Counter] = []       # 与 out 一一对应：被丢掉的那些次读到的等级标
    rr: list[list[str]] = []           # 与 out 一一对应：二次读数（每个字是那一帧的一票）
    members: list[list[dict]] = []     # 与 out 一一对应：这条评论的**全部**拍摄（含不保留的）
    pending: dict[str, list[dict]] = {}   # 更清楚的那次还没轮到，先按 rid 存着这些拍摄
    for i, c in enumerate(comments):
        rid, m = c.get("rid"), c.get("motion_px")
        rid_key = f"{c.get('panel')}#{rid}" if rid is not None else None
        if rid is not None and m is not None and m > best_motion.get(rid_key, m):
            # 同 rid 里存在更清楚的一次 ⇒ 这次不保留。但它仍是**同一条评论的一次拍摄**：
            # 正文完整性 / 多数决要它的一票（见 ``_content_consensus``）。
            hit = seen.get(rid_key)
            if hit is not None:
                members[hit].append(c)
            else:
                pending.setdefault(rid_key, []).append(c)
            continue                              # 同 rid 里存在更清楚的一次 ⇒ 这次不留
        keys: list[str] = []
        if c.get("rid") is not None:
            keys.append(f"{c.get('panel')}#{c['rid']}")
        body = norm_text(c.get("content") or "")
        if body:
            # 有正文的评论：文本（用户名+正文）相同就是同一条 —— 肉眼也分不出来，
            # 而且这能补上「滚动对齐没对齐」的漏网（右面板只有 3 行、一屏多个同名
            # 通知时，位移证据不足，对齐会放弃）。
            keys.append(f"txt:{c.get('panel')}|{norm_text(c.get('username') or '')}|{body}")
        if not keys:
            # 无 rid 又无正文（纯表情评论从旧 rows 复算）：文本上认不出谁是谁，
            # 只能带位置，绝不能与别的空正文评论合并。
            keys.append(f"pos:{c.get('panel')}|{c.get('t')}|{c.get('sockets')}")
        one = (reread or {}).get(i)
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit is not None:
            # 这一次不留 —— 但它读到的等级标是**留下那条**的证据（同一条评论的另一次拍摄）
            members[hit].append(c)
            d = _badge_digits(c.get("badge_text"))
            if d and _same_user(out[hit], c):
                evidence[hit][d] += 1
            # 二次读数走同一套「一条评论的另几次拍摄」—— 同 rid 不同昵称的那些**不能借**
            # （rid 尾端会吸进下一条评论，药丸读数是那一行的）
            if one and _same_user(out[hit], c):
                rr[hit].append(one)
            continue
        for k in keys:
            seen[k] = len(out)
        out.append(c)
        evidence.append(Counter())
        rr.append([one] if one else [])
        members.append([c] + (pending.pop(rid_key, []) if rid_key else []))
    # 正文按「多次拍摄取最完整 / 多数」换一次（见 ``_content_consensus``）：最早拍到的那次
    # 可能还是**截断**的（折行还没滚进可视区），或个别帧 OCR 抖动读错一个字。
    swapped = 0
    for k, mem in enumerate(members):
        pick = _content_consensus(mem)
        if pick is None or pick is out[k]:
            continue
        if (norm_text(pick.get("content") or "") == norm_text(out[k].get("content") or "")
                and int(pick.get("lines") or 1) <= int(out[k].get("lines") or 1)):
            continue                      # 正文没变、行数也没变多 ⇒ 不值得换（别白改 t/ROI）
        keep_badge = out[k].get("badge_text")
        out[k] = dict(pick)
        if keep_badge and not out[k].get("badge_text"):
            out[k]["badge_text"] = keep_badge
        swapped += 1
    if swapped:
        _log(f"  [dedup] 正文按多次拍摄取最完整/多数：换掉 {swapped} 条")
        # 换过 ``t`` 的评论可能排到后面 ⇒ 重新按时间排；证据/二次读数两列跟着一起走。
        order = sorted(range(len(out)),
                       key=lambda j: (float(out[j].get("t") or 0.0),
                                      str(out[j].get("panel"))))
        if order != list(range(len(out))):
            out[:] = [out[j] for j in order]
            evidence[:] = [evidence[j] for j in order]
            rr[:] = [rr[j] for j in order]
        # 换正文可能把两条**本来就同句**的记录改成完全一样（rid 链断开 + 其中一条读错；
        # 实测 `主播，虎8HEV什么时候上市` 的 `什公`/`什么`）⇒ 按文本键再并一次，
        # 与主循环同一口径（同 panel + 同昵称 + 同正文 = 同一条评论）。票合并、记录只留一条。
        idx: dict[tuple, int] = {}
        o2: list[dict] = []
        e2: list[Counter] = []
        r2: list[list[str]] = []
        for c, ev, votes in zip(out, evidence, rr):
            body = norm_text(c.get("content") or "")
            key = ((c.get("panel"), norm_text(c.get("username") or ""), body)
                   if body else None)
            if key is not None and key in idx:
                j = idx[key]
                if int(c.get("lines") or 1) > int(o2[j].get("lines") or 1):
                    o2[j] = c
                e2[j] += ev
                r2[j].extend(votes)
                continue
            if key is not None:
                idx[key] = len(o2)
            o2.append(c)
            e2.append(ev)
            r2.append(votes)
        if len(o2) != len(out):
            _log(f"  [dedup] 正文归一后同句再并：{len(out)} → {len(o2)} 条")
            out[:] = o2
            evidence[:] = e2
            rr[:] = r2
    # 投票之前：先把**被 rid 链断开的兄弟记录**的票并起来（见 ``_pool_sibling_votes``）
    _pool_sibling_votes(out, rr, st)
    for c, cnt, votes in zip(out, evidence, rr):
        if cnt and not _badge_digits(c.get("badge_text")):
            lv = _decisive(cnt)                # 只补不改 + 平票不补
            if lv:
                c["badge_text"] = lv
                st["badge_filled"] += 1
            else:
                st["badge_fill_tied"] += 1
        if not votes:
            continue
        have = _badge_digits(c.get("badge_text"))
        if have:
            # 只补不改：这条有面板读数，**一个字节都不动** —— 但这一次「对不对得上」要记账，
            # 它是二次读数层唯一能自证的地方（两边独立：一边是面板 det+rec，一边是药丸带 rec）。
            # **「投不出票」不是「不一致」**：``_pill_vote`` 给 ``None`` 只说明这一层没读出结论
            # （票不够/平票），把它并进 differ 就是把「读不出来」报成「读得不一样」——
            # 实测 seg7 这两者差着近一倍，而它们说的是完全相反的两件事。所以分开记。
            vote = _pill_vote(votes)
            if vote is None:
                st["badge_reread_novote"] += 1
            else:
                st["badge_reread_agree" if vote == have else "badge_reread_differ"] += 1
            continue
        lv = _pill_vote(votes)
        if lv:
            c["badge_text"] = lv
            st["badge_reread"] += 1
        else:
            st["badge_reread_tied"] += 1
    return out
