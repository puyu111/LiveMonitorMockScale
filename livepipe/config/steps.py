"""livepipe.config.steps"""

from __future__ import annotations

from .constants import _STEP_SWITCH



def resolve(cfg: dict) -> list[str]:
    """按 ``steps.order``/``steps.skip`` 算出实际要跑的步骤序列。"""
    skip = set(cfg["steps"]["skip"])
    return [s for s in cfg["steps"]["order"] if s not in skip]


def force_enable(cfg: dict, steps) -> list[str]:
    """``--only`` 点名的步骤 ⇒ 把它的 ``enabled`` 打开，返回**被打开的**步骤名。

    配置里的 ``enabled: false`` 意思是「默认这一趟别跑」（多半因为要花钱/要 GPU），
    不是「永远不许跑」。而 ``--only filter`` 是**点名要求** —— 若还按 false 静默跳过，
    用户看到的是「跑完了、什么都没做」，这是最坏的一种失败（尤其 filter 那条链下游
    全是它的产物：qualify/export/collect 会跟着一起空转）。所以点名即执行。
    """
    turned = []
    for s in steps or ():
        path = _STEP_SWITCH.get(s)
        if not path:
            continue
        node = cfg
        for k in path[:-1]:
            node = node.get(k) or {}
        if not node.get(path[-1]):
            node[path[-1]] = True
            turned.append(s)
    return turned
