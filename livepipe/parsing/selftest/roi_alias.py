"""livepipe.parsing.selftest.roi_alias — 徽章昵称归一化（badge_alias）的断言。"""

from __future__ import annotations

from ..text.badges import MIN_GAP
from ..text.badges import _is_badge_token
from ..text.badges import _tail_variant
from ..text.badges import normalize_badge_alias
from ..text.badges import normalize_username_alias


def _add_alias_cases(cases):

    # ⑤h~⑤q 行首等级药丸并进正文 ⇒ 同一个人的多种读法被拆成多个账号（bug-349）。
    #     判据是**几何见证**：同一身份里文字框最靠右的那版才没并进药丸（见 normalize_badge_alias）。
    def cm(pnel, rid, name, x1, body="好看", badge=None):
        c = {"panel": pnel, "rid": rid, "username": name, "content": body,
             "text_box": [x1, 100, x1 + 200, 130], "badge_text": badge}
        return c

    def alias_of(rows, panels=None):
        st = normalize_badge_alias(rows, panels)
        return [r["username"] for r in rows], st

    # 实测原案（seg1，正文「这圈速可以啊…」）：药丸并进正文那版文字框落在药丸左沿 27~29，
    # 干净版落在昵称列 84~85。同一条评论被滚动对齐拆成 5 个 rid，这里按**正文**分组。
    g = [cm("comments", 91, "A-幸运小福星", 85), cm("comments", 91, "+1A-幸运小福星", 28),
         cm("comments", 91, "★1A-幸运小福星", 28), cm("comments", 91, "?1A-幸运小福星", 27),
         cm("comments", 91, "1A-幸运小福星", 27), cm("comments", 91, "￥1A-幸运小福星", 28)]
    got_h, st_h = alias_of(g)
    cases.append(("同一行被读成多种昵称 ⇒ 全收敛到文字框最靠右的那版（实测 rid 91 的 7 种）",
                  got_h == ["A-幸运小福星"] * 6 and st_h["rewritten"] == 5))
    cases.append(("药丸读数按 Unicode 类别判：?1 / ￥1 / ★1 / +1 都认",
                  all(_is_badge_token(t) for t in ("1", "+1", "+.1", "✦1", "★1", "?1", "￥1"))))
    cases.append(("真昵称不会被当成药丸：11 / 123 / 11天空 / 123亩176 / A- / 空 / 超长",
                  not any(_is_badge_token(t) for t in
                          ("11", "123", "11天空", "123亩176", "A-", "", "  ", "abcde"))))

    # ⑤h2 改名的**同时**把等级标捡回来：`13白***` 里那一截 `13` 就是这位的等级。
    #     只改名不捡标的话，这些人的交付目录又变成「没有等级标」的那一桶（本次用户报告的
    #     症状之一）；反过来，自己读到过等级标的一律逐字节不动（OCR 的直接读数证据更强）。
    g = [cm("comments", 501, "13白***", 30), cm("comments", 501, "白***", 86, badge="◆13"),
         cm("comments", 502, "13白***", 30, badge="◆19")]
    alias_of(g)
    cases.append(("改名顺手捡等级标：并进昵称的药丸数字补上，读到的逐字节不动",
                  [r["username"] for r in g] == ["白***"] * 3
                  and [r["badge_text"] for r in g] == ["13", "◆13", "◆19"]))

    # ⑤i 同一个名字自己的抖动（实测 ≤3px）⇒ 不算证据，一个字都不改。
    got_i, st_i = alias_of([cm("comments", 9, "梁红", 86), cm("comments", 9, "梁红", 84)])
    cases.append(("同一个名字自己的抖动（2px）⇒ 不归一（没有第二种读法可比）",
                  got_i == ["梁红", "梁红"] and st_i["rewritten"] == 0))

    # ⑤j 差距不够 MIN_GAP ⇒ 不归一：OCR 丢了个行首字同样会让框右移（丢一个数字 ≈13px），
    #     必须跟「药丸占位（≈42~56px）」区分开。7喜 / 1 7喜 是语料里的真名，差 13px。
    got_j, st_j = alias_of([cm("comments", 21, "7喜", 99), cm("comments", 21, "1 7喜", 86)])
    cases.append((f"文字框只差 {MIN_GAP}px 以内 ⇒ 不归一（防「OCR 丢了一个行首字」）",
                  got_j == ["7喜", "1 7喜"] and st_j["rewritten"] == 0))

    # ⑤k 不是「后缀 + 药丸」的关系 ⇒ 不归一：这是**字符级误读**（羊/幸、生/星），
    #     几何判据救不了，也不该救（硬收敛会把两个人的话并给一个人）。
    got_k, st_k = alias_of([cm("comments", 33, "A-幸运小福星", 85),
                            cm("comments", 33, "A-羊运小福生", 44)])
    cases.append(("两种读法是字符级误读（不构成后缀关系）⇒ 不归一（宁缺勿错并）",
                  got_k == ["A-幸运小福星", "A-羊运小福生"] and st_k["rewritten"] == 0))

    # ⑤l **取证在 rid 组、改写是全局的**（这是本修复的关键）：
    #     同一条评论被滚动对齐拆成多个 rid，干净读法只落在其中一个组里，另外几个组
    #     只有脏读法 —— 只改组内的话，那些脏读法会原样留下（实测：账号数一个都不少）。
    g = [cm("comments", 91, "A-幸运小福星", 85), cm("comments", 91, "+1A-幸运小福星", 28),
         cm("comments", 91, "★1A-幸运小福星", 27),
         cm("comments", 144, "+1A-幸运小福星", 28), cm("comments", 146, "★1A-幸运小福星", 27)]
    got_l, st_l = alias_of(g)
    cases.append(("取证在 rid 91、改写要落到别的 rid 上（否则等于没修）",
                  got_l == ["A-幸运小福星"] * 5 and st_l["rewritten"] == 4))

    # ⑤l2 正文不同也能一并归一：同一条评论「药丸并进去」和「没并进去」两种读法下
    #     **正文本身就不同**（并进去时 OCR 读全了、没并进去时尾巴丢了），所以正文不能当身份。
    g = [cm("comments", 91, "A-幸运小福星", 85, "这圈速可以啊，家用车"),
         cm("comments", 91, "+1A-幸运小福星", 28, "这圈速可以啊，家用车 跑成这样"),
         cm("comments", 144, "+1A-幸运小福星", 28, "这圈速可以啊，家用车 跑成这样")]
    got_l2, st_l2 = alias_of(g)
    cases.append(("正文不同也是同一个人（正文不能当身份：两种读法的正文本身就不一样）",
                  got_l2 == ["A-幸运小福星"] * 3 and st_l2["rewritten"] == 2))

    # ⑤l3 某组的证据自相矛盾（混进了别人的名字）⇒ 只跳过那一组，不连累别的组。
    g = [cm("comments", 91, "A-幸运小福星", 85), cm("comments", 91, "+1A-幸运小福星", 28),
         cm("comments", 143, "西西", 44), cm("comments", 143, "+1A-幸运小福星", 27)]
    got_l3, st_l3 = alias_of(g)
    cases.append(("某一组证据自相矛盾（混进别人的名字）⇒ 只跳过那一组，别的组照修",
                  got_l3 == ["A-幸运小福星", "A-幸运小福星", "西西", "A-幸运小福星"]
                  and st_l3["rewritten"] == 2))

    # ⑤m 没配 badge_alias 的面板（抖音/微博）行为一字不变。
    g = [cm("comments", 5, "小明", 44), cm("comments", 5, "1小明", 12)]
    got_np, st_np = alias_of([dict(r) for r in g], {"danmu"})
    got_mm, st_mm = alias_of([dict(r) for r in g], {"comments"})
    cases.append(("没配 badge_alias 的面板 ⇒ 一字不动（别的平台不受影响）",
                  got_np == ["小明", "1小明"] and st_np["rewritten"] == 0
                  and got_mm == ["小明", "小明"] and st_mm["rewritten"] == 1))

    # ⑤n 有一行没文字框 ⇒ 这一组没证据，别动（宁可漏归一，不可错归并）。
    got_m, st_m = alias_of([cm("comments", 6, "小明", 44),
                            {"panel": "comments", "rid": 6, "username": "1小明",
                             "content": "好看"}])
    cases.append(("同组里有一行没有文字框 ⇒ 整组放弃（不给没证据的组做归并）",
                  got_m == ["小明", "1小明"] and st_m["rewritten"] == 0))

    # ⑤o 名字为空的读法（纯表情行/续行）既不当见证、也不被改写。
    got_n, st_n = alias_of([cm("comments", 7, "小明", 44), cm("comments", 7, "1小明", 12),
                            cm("comments", 7, None, 12), cm("comments", 7, "", 44)])
    cases.append(("空名字的读法不当见证也不被改写（续行/纯表情行不干扰）",
                  got_n == ["小明", "小明", None, ""] and st_n["rewritten"] == 1))

    # ⑤p 没有正文的评论（纯表情）只能靠 rid 认身份：同一个 rid 收敛，不同 rid 各归各的。
    got_p, st_p = alias_of([cm("comments", 7, "小明", 44, ""), cm("comments", 7, "1小明", 12, ""),
                            cm("comments", 8, "1小明", 12, "")])
    cases.append(("没正文的纯表情评论照样能取证（按 rid 认身份），且改写同样落到别的 rid",
                  got_p == ["小明"] * 3 and st_p["rewritten"] == 2))

    # ⑤q2 取证到**链式**对照（`11小明->1小明` 与 `1小明->小明`）时要一路走到根。
    g = [cm("comments", 61, "小明", 90), cm("comments", 61, "1小明", 40),
         cm("comments", 62, "1小明", 44), cm("comments", 62, "11小明", 12)]
    got_q2, st_q2 = alias_of(g)
    cases.append(("链式对照要走到根（11小明 -> 1小明 -> 小明）",
                  got_q2 == ["小明"] * 4 and st_q2["rewritten"] == 3))

    # ⑤q3 证据自相矛盾成环（甲->乙 且 乙->甲）⇒ 一字不改（宁可漏，不可错）。
    g = [cm("comments", 71, "乙", 90), cm("comments", 71, "甲", 60, "好看"),
         cm("comments", 72, "甲", 90), cm("comments", 72, "乙", 60, "好看")]
    got_q3, st_q3 = alias_of(g)
    cases.append(("对照表成环（甲->乙 且 乙->甲）⇒ 一字不改",
                  got_q3 == ["乙", "甲", "甲", "乙"] and st_q3["rewritten"] == 0))

    # ⑤q 没有身份（既没正文也没 rid）的评论不动。
    got_q, st_q = alias_of([{"panel": "comments", "username": "1小明",
                             "content": "", "text_box": [12, 100, 212, 130]}])
    cases.append(("既没正文也没 rid ⇒ 没有身份，不动",
                  got_q == ["1小明"] and st_q["rewritten"] == 0))

    # ⑤r 等级标 = 身份：**两条读法的等级标一致才算同一个人**（实测抖音 App 的 ◆N 是
    #     粉丝团等级，挂在人身上、与昵称绑定）。三段切片里 13 对「同一个人的两种读法」
    #     **全部**是「内容一字不差 + 相邻但不同的 rid」⇒ A 通道看不见，靠内容配。
    g = [cm("comments", 87, "山***", 104, "内饰真有档次", badge="◆11"),
         cm("comments", 115, "11山***", 46, "内饰真有档次")]
    got_r, st_r = alias_of(g)
    cases.append(("跨 rid 的同一句话：干净那版读出等级标 11 = 脏那版多出来的 11 ⇒ 归一",
                  got_r == ["山***", "山***"] and st_r["rewritten"] == 1
                  and st_r["badge_pairs"] == 1))
    g = [cm("comments", 87, "山***", 104, "内饰真有档次", badge="◆4"),
         cm("comments", 115, "11山***", 46, "内饰真有档次")]
    got_r2, st_r2 = alias_of(g)
    cases.append(("跨 rid 同句、但等级标对不上（多出来 11 vs 读到 4）⇒ 不是一个用户，不并",
                  got_r2 == ["山***", "11山***"] and st_r2["rewritten"] == 0))
    # 内容不同 ⇒ B 通道不配（A 通道不看内容，见 ⑤l2）：这里两组 rid 都不同、正文也不同
    g = [cm("comments", 87, "山***", 104, "内饰真有档次", badge="◆11"),
         cm("comments", 115, "11山***", 46, "空间真大")]
    got_r3, st_r3 = alias_of(g)
    cases.append(("跨 rid 但正文不同 ⇒ B 通道不配（宁缺勿错并）",
                  got_r3 == ["山***", "11山***"] and st_r3["rewritten"] == 0))
    # 文字框差距不够 ⇒ 不配：那是「OCR 丢了个行首字」而不是药丸占位。
    g = [cm("comments", 87, "山***", 104, "内饰真有档次", badge="◆11"),
         cm("comments", 115, "11山***", 90, "内饰真有档次")]
    got_r4, st_r4 = alias_of(g)
    cases.append((f"跨 rid 同句但两版只差 {MIN_GAP}px 以内 ⇒ 不配（防丢字误判成药丸）",
                  got_r4 == ["山***", "11山***"] and st_r4["rewritten"] == 0))

    # ⑤s 「光秃秃两位数」的药丸（`22阳***`）：`_is_badge_token` 为了不啃 `11天空`/`123亩176`
    #     这类真名必须挡两位数 —— 但**读出来的等级标正是 22** 时就是对账成立，认下。
    #     这是 seg6/seg7 里 21~25% 具名评论仍带药丸数字的那笔残留的落点。
    g = [cm("comments", 51, "阳***", 96, "好看", badge="◆22"),
         cm("comments", 51, "22阳***", 47, "好看")]
    got_s, st_s = alias_of(g)
    cases.append(("同组内两位数药丸（22阳***）凭读出的等级标 22 认下（形状判据挡的那种）",
                  got_s == ["阳***", "阳***"] and st_s["rewritten"] == 1
                  and st_s["badge_ok"] == 1))
    g = [cm("comments", 51, "阳***", 96, "好看", badge="◆16"),
         cm("comments", 51, "22阳***", 47, "好看")]
    got_s2, st_s2 = alias_of(g)
    cases.append(("药丸数字与等级标不一致（22 vs 16）⇒ 不并，且留下 vetoed 证据",
                  got_s2 == ["阳***", "22阳***"] and st_s2["rewritten"] == 0
                  and len(st_s2["vetoed"]) == 1))
    g = [cm("comments", 51, "阳***", 96, "好看"), cm("comments", 51, "22阳***", 47, "好看")]
    got_s3, st_s3 = alias_of(g)
    cases.append(("没有等级标读数 ⇒ 退回形状判据（两位数仍不认，与改动前逐字节一致）",
                  got_s3 == ["阳***", "22阳***"] and st_s3["rewritten"] == 0))
    # 单字符药丸 + 等级标不一致 ⇒ 也否（一位数药丸本来靠形状就能过，现在要对账）。
    g = [cm("comments", 53, "平***", 99, "给力", badge="◆7"),
         cm("comments", 53, "6平***", 73, "给力")]
    got_s4, st_s4 = alias_of(g)
    cases.append(("一位数药丸与等级标不一致（6 vs 7）⇒ 不并（形状过得去也不认）",
                  got_s4 == ["平***", "6平***"] and st_s4["rewritten"] == 0
                  and len(st_s4["vetoed"]) == 1))
    g = [cm("comments", 53, "平***", 99, "给力", badge="◆6"),
         cm("comments", 53, "6平***", 73, "给力")]
    got_s5, st_s5 = alias_of(g)
    cases.append(("一位数药丸与等级标一致（6 = 6）⇒ 归一（老行为不变）",
                  got_s5 == ["平***", "平***"] and st_s5["rewritten"] == 1))

    # ⑥ 昵称**尾巴**被检测框切掉/末字漏读 ⇒ 同一个人被拆成两个账号（bug-350，用户
    #    2026-11-06 报 W-3/W-5、期待si-3/期待sj-26）。判据 = 同一条评论的拍摄里两个
    #    读法同现（同 rid 组各 ≥2 次）+ 只差一个尾巴；主写法取面板内行数最多的那版。
    def nm(pnel, rid, name, body="好看", badge=None):
        return {"panel": pnel, "rid": rid, "username": name, "content": body,
                "badge_text": badge}

    def ualias_of(rows, panels=None):
        st = normalize_username_alias(rows, panels)
        return [r["username"] for r in rows], st

    # ⑥a 实测原案 1：`W.` / `W`。`W.` 末尾 2×3px 的句点在三帧里被检测框切掉（10.31
    #     视频号实测 W. 192 行 vs W 68 行）。标点整段被切 ⇒ 允许。
    g = [nm("comments", 1, "W."), nm("comments", 1, "W."), nm("comments", 1, "W"),
         nm("comments", 1, "W"), nm("comments", 2, "W."), nm("comments", 2, "W.")]
    got_ua, st_ua = ualias_of(g)
    cases.append(("`W.`/`W`（末尾句点被框切）⇒ 按行数多的 `W.` 归一，且改写落到别的 rid",
                  got_ua == ["W."] * 6 and st_ua["rewritten"] == 2
                  and st_ua["renames"][("comments", "W")] == "W."))

    # ⑥b 实测原案 2：`期待sj` / `期待si`。等长、只差末尾一字 —— `j` 的降部被检测框切
    #     1px 认成 `i`（同一份像素，框高不同读出不同；实测 1343 vs 42）。
    g = ([nm("comments", 3, "期待sj", "你好") for _ in range(3)]
         + [nm("comments", 3, "期待si", "你好") for _ in range(2)])
    got_ub, st_ub = ualias_of(g)
    cases.append(("`期待sj`/`期待si`（j 降部被框切 1px）⇒ 收敛到多数读法 `期待sj`",
                  got_ub == ["期待sj"] * 5 and st_ub["rewritten"] == 2))

    # ⑥c 实测原案 3：`李素熵` / `李素`。末字整个漏读（实测 244 vs 53）。
    g = ([nm("comments", 4, "李素熵") for _ in range(3)]
         + [nm("comments", 4, "李素") for _ in range(2)])
    got_uc, st_uc = ualias_of(g)
    cases.append(("`李素熵`/`李素`（末字漏读）⇒ 收敛到多数读法 `李素熵`",
                  got_uc == ["李素熵"] * 5 and st_uc["rewritten"] == 2))

    # ⑥d 形状闸：只差尾部一字/纯标点才算。中段不同、等长但 <4 字的换尾字、多出 4 个
    #     标点、单字名多一字 —— 都不认（宁缺勿错并）。
    cases.append(("尾巴判据只放行三类：多/少 1 个末字、≤3 个纯标点、≥4 字换末字",
                  _tail_variant("李素", "李素熵")
                  and _tail_variant("W", "W.")
                  and _tail_variant("W", "W...")
                  and _tail_variant("期待si", "期待sj")
                  and not _tail_variant("A-幸运小福星", "A-羊运小福生")
                  and not _tail_variant("小明", "小梦")        # 二字换尾字不认
                  and not _tail_variant("W", "W....")         # 标点超过 3 个不认
                  and not _tail_variant("王", "王强")         # 单字名不认
                  and _tail_variant("abc", "abcd")))          # 多一个末字与文字系统无关
    # 等长换尾字要求两边都 ≥4 字：`小明甲`/`小明乙`（3 字）不认。
    cases.append(("等长换末字要 ≥4 字（3 字名的末字差异可能是真不同的人）",
                  not _tail_variant("小明甲", "小明乙")))

    # ⑥e 没有 rid 同现 ⇒ 没证据，不并（两个读法可能真是两个人）。
    got_ue, st_ue = ualias_of([nm("comments", 5, "W."), nm("comments", 6, "W"),
                               nm("comments", 5, "W."), nm("comments", 6, "W")])
    cases.append(("两个读法从未在同一条评论的拍摄里同现 ⇒ 不并",
                  got_ue == ["W.", "W", "W.", "W"] and st_ue["rewritten"] == 0))

    # ⑥f 同组里每种读法各只出现 1 次 ⇒ 证据不足（可能是两行相邻滚动错位）。
    got_uf, st_uf = ualias_of([nm("comments", 7, "李素熵"), nm("comments", 7, "李素")])
    cases.append(("同组里每种读法只有 1 次 ⇒ 证据不足，不并",
                  got_uf == ["李素熵", "李素"] and st_uf["rewritten"] == 0))

    # ⑥g 等级标硬闸：两边都读到等级且不一致 ⇒ 不是同一个人（与 badge_alias 同一条）。
    g = ([{"panel": "comments", "rid": 8, "username": "山***", "content": "好看",
           "badge_text": "◆11"} for _ in range(2)]
         + [{"panel": "comments", "rid": 8, "username": "山****", "content": "好看",
             "badge_text": "◆4"} for _ in range(2)])
    got_ug, st_ug = ualias_of(g)
    cases.append(("尾巴相像但等级标不一致（11 vs 4）⇒ 不并，且留 vetoed 证据",
                  got_ug == ["山***"] * 2 + ["山****"] * 2
                  and st_ug["rewritten"] == 0
                  and len(st_ug["vetoed"]) == 1))

    # ⑥g2 打码昵称（匿名）——用户 2026-11-08 定：可见部分（星号前，通常第一个字）
    #      必须逐字 100% 相同，星号个数被 OCR 读错不算差异；名字对上之后，两边等级
    #      标都要读到且一致才并。形近 / 共用正文对打码名**一律不算证据**：打码名底下
    #      本来就挂着几十个人，模板化直播里任意两个号共用文案是常态。
    def masked_rows(u, v, bu=None, bv=None, rid=12):
        return ([nm("comments", rid, u, badge=bu) for _ in range(2)]
                + [nm("comments", rid, v, badge=bv) for _ in range(2)])

    got_uk, st_uk = ualias_of(masked_rows("小***", "健***", "◆7", "◆7"))
    cases.append(("打码名可见部分不同（小*** / 健***）⇒ 不并，等级一致也不行",
                  got_uk == ["小***"] * 2 + ["健***"] * 2 and st_uk["rewritten"] == 0))

    got_ul, st_ul = ualias_of(masked_rows("小***", "小**"))
    cases.append(("打码名星号个数读错但等级标都没读到 ⇒ 不并（名字对上还要等级一致）",
                  got_ul == ["小***"] * 2 + ["小**"] * 2 and st_ul["rewritten"] == 0))

    got_um, st_um = ualias_of(masked_rows("小***", "小**", "◆7", "◆7"))
    cases.append(("打码名星号个数读错 + 等级标一致（7 = 7）⇒ 并到多数写法",
                  got_um == ["小***"] * 4 and st_um["rewritten"] == 2))

    got_un, st_un = ualias_of(masked_rows("小***", "小**", "◆7", "◆9"))
    cases.append(("打码名星号个数读错但等级标不一致（7 vs 9）⇒ 不并，留 vetoed 证据",
                  got_un == ["小***"] * 2 + ["小**"] * 2 and st_un["rewritten"] == 0
                  and len(st_un["vetoed"]) == 1))

    # ⑥h 面板闸：没配 username_alias 的面板一字不动。
    g = [nm("comments", 9, "W."), nm("comments", 9, "W"),
         nm("comments", 9, "W."), nm("comments", 9, "W")]
    got_uh, st_uh = ualias_of([dict(r) for r in g], {"danmu"})
    got_uh2, st_uh2 = ualias_of([dict(r) for r in g], {"comments"})
    cases.append(("没配 username_alias 的面板 ⇒ 一字不动（别的平台不受影响）",
                  got_uh == ["W.", "W", "W.", "W"] and st_uh["rewritten"] == 0
                  and got_uh2 == ["W."] * 4 and st_uh2["rewritten"] == 2))

    # ⑥i 主写法 = 面板内行数最多的那版（不是「更长的」）。`期待si` 更长？一样长；这里用
    #     `李素`/`李素熵` 反着摆：脏读法行数更多时也应选它 —— 与 badge_alias 的几何判据
    #     **相反**，这是两套机制不能混的原因（药丸并进正文是「更靠左」，行数多数反而错）。
    g = ([nm("comments", 10, "李素") for _ in range(5)]
         + [nm("comments", 10, "李素熵") for _ in range(2)])
    got_ui, st_ui = ualias_of(g)
    cases.append(("主写法按行数多数（这里脏读法行数更多 ⇒ 选 `李素`）",
                  got_ui == ["李素"] * 7
                  and st_ui["renames"][("comments", "李素熵")] == "李素"))

    # ⑥j 空昵称既不当见证、也不被改写。
    g = [nm("comments", 11, "W."), nm("comments", 11, "W"),
         nm("comments", 11, "W."), nm("comments", 11, "W"), nm("comments", 11, "")]
    got_uj, st_uj = ualias_of(g)
    cases.append(("空昵称不当见证也不被改写（续行/纯表情行不干扰）",
                  got_uj == ["W."] * 4 + [""] and st_uj["rewritten"] == 2))
