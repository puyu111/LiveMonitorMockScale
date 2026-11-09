"""livepipe.config.errors"""

from __future__ import annotations



class ConfigError(SystemExit):
    """配置错误 —— 直接以 SystemExit 抛出，带可读中文信息。"""
