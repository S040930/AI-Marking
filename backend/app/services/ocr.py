"""PaddleOCR-VL 文档解析服务模块。

提供 PDF / 图片文件的结构化解析能力:
- 通过 PaddleOCR-VL ``layout-parsing`` 接口,将 PDF 转成结构化 Markdown
- 支持表格、公式、阅读顺序的还原,适合作业批改场景

API URL / Access Token 由调用方(批改流水线)从数据库读取后传入,
不再依赖 ``settings`` 中的环境变量。
"""

import base64
from pathlib import Path

import httpx

# PaddleOCR-VL layout-parsing 接口超时(秒)
# 大 PDF + 复杂版面可能耗时较长,这里给到 120s
_TIMEOUT = httpx.Timeout(120.0)


class OCRError(Exception):
    """OCR 服务异常。"""


def _normalize_api_url(api_url: str) -> str:
    """规范化 API URL:自动补全 ``/layout-parsing`` 后缀。"""
    base = api_url.rstrip("/")
    if base.endswith("/layout-parsing"):
        return base
    return f"{base}/layout-parsing"


async def ocr_pdf(file_path: str, api_url: str, token: str) -> str:
    """对 PDF 文件执行 PaddleOCR-VL 文档解析,返回 Markdown 文本。

    Args:
        file_path: PDF 文件的本地路径
        api_url: PaddleOCR-VL API 入口(如 ``https://xxx/layout-parsing`` 或根地址)
        token: AI Studio 个人访问令牌(``Authorization: token <token>``)

    Returns:
        解析后的 Markdown 文本,保留表格、公式、阅读顺序。

    Raises:
        OCRError: 文件读取失败、API 配置缺失、网络异常、返回为空。
    """
    if not api_url or not token:
        raise OCRError("PaddleOCR-VL API URL / Token 未配置,请在设置页填写")

    path = Path(file_path)
    try:
        pdf_bytes = path.read_bytes()
    except OSError as exc:
        raise OCRError(f"读取 PDF 文件失败: {exc}") from exc

    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    url = _normalize_api_url(api_url)
    payload = {
        "file": pdf_b64,
        "fileType": 0,  # 0=PDF, 1=图片
    }
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise OCRError(f"PaddleOCR-VL 网络错误: {exc}") from exc

    data = resp.json()
    try:
        results = data["result"]["layoutParsingResults"]
    except (KeyError, TypeError) as exc:
        raise OCRError(f"PaddleOCR-VL 返回结构异常: {data}") from exc

    if not results:
        raise OCRError("PaddleOCR-VL 返回空文本,可能 PDF 无有效内容")

    # 遍历所有页面,用换行符拼接
    texts: list[str] = []
    for page in results:
        try:
            texts.append(page["markdown"]["text"])
        except (KeyError, TypeError):
            # 某页结构异常时跳过,不影响其他页
            continue

    combined = "\n\n".join(texts)

    if not combined or not combined.strip():
        raise OCRError("PaddleOCR-VL 返回空文本,可能 PDF 无有效内容")

    return combined.strip()
