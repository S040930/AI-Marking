"""数据库时间工具。"""

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """返回 UTC 时间，匹配数据库的 ``TIMESTAMP WITHOUT TIME ZONE`` 字段。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
