"""livepipe.tools.user_filter.judge"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from .api import _extract_json
from .api import _post
from .defaults import LABELS



def judge_batch(items: list[tuple[str, str]], *, base_url: str, api_key: str,
                model: str, prompt: str, timeout: float, retries: int,
                verbose: bool = False, allow_fallback: bool = True) -> list[str]:
    """一批 ``[(content, emoji_text)]`` → 逐条标签。

    批量失败（回复里没有合法 JSON / 缺项 / 标签非法）就**逐条重判** —— 一次批量崩掉
    不该让整批 20 条一起变成「判不出来」。
    """
    lines = []
    for k, (content, emoji) in enumerate(items):
        lines.append(f'{k}. 内容="{content}" 表情="{emoji or "无"}"')
    user = "评论：\n" + "\n".join(lines) + '\n\n只输出 JSON：{"0": "正面", ...}'
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": user}]

    got: dict[int, str] = {}
    for attempt in range(max(1, retries)):
        try:
            resp = _post(base_url, api_key,
                         {"model": model, "messages": messages, "temperature": 0},
                         timeout)
            text = resp["choices"][0]["message"]["content"]
            d = _extract_json(text) or {}
            got = {int(k): str(v).strip() for k, v in d.items()
                   if str(k).strip().lstrip("-").isdigit()}
            if all(got.get(k) in LABELS for k in range(len(items))):
                return [got[k] for k in range(len(items))]
            if verbose:
                print(f"  [nlp] 批量回复不完整（{len(got)}/{len(items)}），重试 "
                      f"{attempt + 1}/{retries}", file=sys.stderr, flush=True)
        except urllib.error.HTTPError as e:
            # 认证/请求类错误（key 不对、模型不存在…）：重试没用，**当场中止**。
            # 更不能把「没判出来」当成「中性」缓存下去 —— 那会污染缓存，之后再也纠正不回来。
            if e.code in (400, 401, 403, 404):
                hint = ""
                if e.code == 404:
                    # 404 基本只有一个来路：**模型名**（平台改名/下线，2026-10-12 就发生过：
                    # `deepseek-v4-flash` → `deepseek-v4-flash-0731`）。顺手把现名捞回来印出来，
                    # 省得人去猜 —— 失败也算出来的话就算了，别让诊断把主错误盖住。
                    try:
                        req = urllib.request.Request(
                            base_url.rstrip("/") + "/models",
                            headers={"Authorization": f"Bearer {api_key}"})
                        with urllib.request.urlopen(req, timeout=10.0) as r:
                            ids = [m.get("id") for m in (json.loads(r.read().decode()) or {})
                                   .get("data", [])]
                        fam = (model or "").split("-")[0].lower()
                        same = [i for i in ids if i and fam and fam in i.lower()]
                        hint = ("\n      ★ 该 base_url 的模型名（同族优先）："
                                + ", ".join((same or ids)[:8]))
                    except Exception:
                        pass
                raise SystemExit(
                    f"[nlp] 接口返回 HTTP {e.code} {e.reason} —— 认证/请求错误，重试无用，中止。\n"
                    f"      base_url={base_url}   model={model}\n"
                    f"      常见原因：api_key 与 base_url 不是同一家 / key 失效 / 模型名不存在。"
                    f"{hint}")
            if verbose:
                print(f"  [nlp] 请求失败（HTTP {e.code}），重试 "
                      f"{attempt + 1}/{retries}", file=sys.stderr, flush=True)
        except (urllib.error.URLError, KeyError, TimeoutError, OSError) as e:
            if verbose:
                print(f"  [nlp] 请求失败（{type(e).__name__}: {e}），重试 "
                      f"{attempt + 1}/{retries}", file=sys.stderr, flush=True)
        time.sleep(1.0 + attempt)

    # 批量没救回来。**单条不再往下走这套兜底**（allow_fallback=False）——否则单条失败又会进这段、
    # 再调单条，无限递归（曾把接口刷爆）。
    if not allow_fallback or len(items) == 1:
        return [got.get(k) if got.get(k) in LABELS else "中性" for k in range(len(items))]

    # 逐条重判（每条一次调用）
    out: list[str] = []
    for k, (content, emoji) in enumerate(items):
        if got.get(k) in LABELS:
            out.append(got[k])
            continue
        one = judge_batch([(content, emoji)], base_url=base_url, api_key=api_key,
                          model=model, prompt=prompt, timeout=timeout,
                          retries=1, verbose=verbose, allow_fallback=False)
        out.append(one[0] if one else "中性")
    return out
