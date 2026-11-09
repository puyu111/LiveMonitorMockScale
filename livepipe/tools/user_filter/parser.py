"""livepipe.tools.user_filter.parser"""

from __future__ import annotations

from .doc import DOC
import argparse

from .defaults import DEFAULT_BASE_URL
from .defaults import DEFAULT_EXCLUDE
from .defaults import DEFAULT_MODEL
from .defaults import DEFAULT_PROMPT



def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--comments", required=True,
                   help="parse 步骤产出的 comments.jsonl")
    p.add_argument("--out", default=None, help="合格用户 JSONL；默认 <comments 同目录>/users.jsonl")
    p.add_argument("--cache", default=None, help="判定缓存 JSONL；默认 <同目录>/judgments.jsonl")
    p.add_argument("--min-positive", type=int, default=3,
                   help="用户名至少要有几条正面评论才算合格（默认 3）")
    p.add_argument("--exclude", default=",".join(DEFAULT_EXCLUDE),
                   help="用户名排除词，逗号分隔（默认品牌/4S/门店/车型 + 高管姓名名单）")
    p.add_argument("--identity-panels", default="",
                   help="这些面板的评论按 **(昵称, 等级标 ◆N)** 认账号（逗号分隔）；"
                        "默认空 = 只看昵称。管线里由平台档案的 account_key 自动填"
                        "（见 livepipe/account_id.py）")
    p.add_argument("--identity-masked", default="",
                   help="这些面板里**只有打码昵称**（末尾星号，如 `爱**`）才按 "
                        "(昵称, 等级标) 认人，没打码的昵称仍只看昵称（逗号分隔）；"
                        "管线里由平台档案 account_key=nickname_level_if_masked 自动填")
    p.add_argument("--api-key", default=None, help="API key；不给则读 --api-key-env")
    p.add_argument("--api-key-env", default="NLP_API_KEY", help="读 key 的环境变量名")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI 兼容 base_url")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="判定提示词（整体覆盖）")
    p.add_argument("--batch-size", type=int, default=20, help="一次调用判几条（默认 20）")
    p.add_argument("--concurrency", type=int, default=4, help="并发请求数（默认 4）")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--limit", type=int, default=None, help="最多调多少批（控成本）")
    p.add_argument("--dry-run", action="store_true", help="只估算调用量，不真调")
    return p
