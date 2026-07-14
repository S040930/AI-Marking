"""OCR 配置校验测试。"""

import pytest

from app.services.errors import BusinessError
from app.services.ocr import _normalize_api_url


def test_normalize_async_jobs_url_passthrough():
    url = "https://x.com/api/v2/ocr/jobs"
    assert _normalize_api_url(url) == "https://x.com/api/v2/ocr/jobs"


def test_normalize_sync_layout_parsing_passthrough():
    url = "https://x.com/layout-parsing"
    assert _normalize_api_url(url) == "https://x.com/layout-parsing"


def test_normalize_rejects_mixed_suffix():
    """误拼为 .../api/v2/ocr/jobs/layout-parsing 必须直接报错,不发起请求。"""
    with pytest.raises(BusinessError):
        _normalize_api_url("https://x.com/api/v2/ocr/jobs/layout-parsing")


def test_normalize_rejects_bare_url():
    """缺少明确入口后缀的裸地址应被拒绝。"""
    with pytest.raises(BusinessError):
        _normalize_api_url("https://x.com/ocr")
