"""ACP Agent Registry 客户端:动态发现 + 白名单 + 供应链安全。

安全模型(见 docs/architecture/acp-grading.md):
- Registry 只提供候选元数据;运行前必须通过本地白名单审核
  (Agent ID + 包身份/仓库身份 + 发行类型三重校验,防止条目被替换);
- npm/PyPI 只接受精确版本,拒绝 latest/范围/Git/本地路径;
- binary 发行必须携带 SHA-256,解压拒绝路径穿越、符号链接和
  清单外可执行文件;
- Registry 元数据可刷新,但已启用版本不自动升级;刷新失败时保留
  最后一次校验成功的缓存快照。

官方 Registry 格式(agentclientprotocol/registry,FORMAT.md):
- 顶层 ``{"version", "agents": [...], "extensions"}``;``agents`` 为列表,
  以 ``id`` 匹配;
- 每个 agent 携带 ``repository``(完整 URL)与 ``distribution`` 字典,
  键为 ``npx``/``uvx``/``binary``;
- ``npx``/``uvx`` 段:``{"package": "<name>[@<ver>]", "args": [...]}``;
- ``binary`` 段:平台键(darwin-aarch64 等)→
  ``{"archive", "sha256", "cmd", "args", "env"}``。
"""

from __future__ import annotations

import json
import logging
import re
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.acp.errors import AcpProtocolError

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_URL = (
    "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
)
CACHE_TTL_SECONDS = 6 * 3600

# 默认白名单:agent_id -> 允许的仓库身份、包身份与发行类型。
# 身份校验防止 Registry 条目被替换为其他包(供应链投毒)。
# 包/仓库身份与官方 Registry 实际发布一一对应,不得随意修改。
# 教师可在运行时增删条目:覆盖持久化在 cache 目录的
# whitelist-overrides.json(见下方"白名单管理"),生效名单用
# effective_whitelist() 计算;审核函数接受 whitelist 参数。
DEFAULT_AGENT_WHITELIST: dict[str, dict[str, Any]] = {
    "codex-acp": {
        "repo": "agentclientprotocol/codex-acp",
        "package": "@agentclientprotocol/codex-acp",
        "distributions": {"npx"},
    },
}

# 发行类型优先级:同一 agent 提供多种发行时按此顺序选取。
_DISTRIBUTION_ORDER = ("npx", "uvx", "binary")

# 精确语义化版本(不带前导 v 也接受)。
_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")


class RegistryError(AcpProtocolError):
    """Registry 数据不可用或条目未通过审核。"""


def is_exact_version(version: str) -> bool:
    return bool(_EXACT_VERSION.match(version))


def _repo_slug(value: str) -> str:
    """仓库 URL 或 slug 统一为 ``owner/repo``。"""
    v = str(value).strip().rstrip("/")
    if "://" in v:
        rest = v.split("://", 1)[1]
        rest = rest.split("/", 1)[1] if "/" in rest else rest
        v = rest
    if v.endswith(".git"):
        v = v[:-4]
    return v


def _package_base(spec: str) -> str:
    """从包描述中剥离版本后缀:``@scope/pkg@1.2.3``→``@scope/pkg``。"""
    s = str(spec).strip()
    if s.startswith("@"):
        return s.rsplit("@", 1)[0] if s.count("@") > 1 else s
    return s.split("@", 1)[0].split("==", 1)[0]


def _platform_key() -> str:
    """当前机器的 binary 发行平台键(darwin-aarch64 等)。"""
    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    return f"{system}-{arch}"


# ---------------------------------------------------------------------------
# 白名单管理:默认名单 + 教师自定义增删(覆盖持久化)
# ---------------------------------------------------------------------------

WHITELIST_OVERRIDES_FILE = "whitelist-overrides.json"
ALLOWED_DISTRIBUTIONS = ("npx", "uvx", "binary")

_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@/._+-]*$")


def acp_cache_dir() -> Path:
    """ACP 缓存目录的唯一定义点:``<backend>/uploads/acp-cache``。

    必须是绝对路径且不随进程 CWD 漂移:该目录既是 Registry 缓存/白名单
    覆盖的存放处,也作为 agent 会话的 cwd——codex 无法在相对路径 cwd 下
    启动注入的 MCP stdio 服务。历史上多个模块用 ``./uploads/acp-cache``
    相对 CWD 求值,从不同工作目录启动进程会产生分裂的缓存目录。
    """
    return Path(__file__).resolve().parents[2] / "uploads" / "acp-cache"


def whitelist_overrides_path(base_dir: Path | str) -> Path:
    return Path(base_dir) / WHITELIST_OVERRIDES_FILE


def load_whitelist_overrides(base_dir: Path | str) -> dict[str, Any]:
    """读取教师增删覆盖;文件缺失或损坏视为空覆盖。

    结构:``{"removed": [agent_id...], "added": {agent_id: rule}}``。
    只存覆盖而不复制整份默认名单——官方默认名单随代码演进仍然生效。
    """
    try:
        data = json.loads(
            whitelist_overrides_path(base_dir).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {"removed": [], "added": {}}
    if not isinstance(data, dict):
        return {"removed": [], "added": {}}
    removed = [str(x) for x in data.get("removed", []) if isinstance(x, str)]
    added = {
        str(k): dict(v)
        for k, v in (data.get("added") or {}).items()
        if isinstance(v, dict)
    }
    return {"removed": removed, "added": added}


def _save_whitelist_overrides(base_dir: Path | str, overrides: dict[str, Any]) -> None:
    path = whitelist_overrides_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def effective_whitelist(base_dir: Path | str) -> dict[str, dict[str, Any]]:
    """Return the immutable first-phase ACP whitelist.

    The old override file is intentionally ignored.  It may remain on disk
    after an upgrade, but it must not widen or replace the Codex-only public
    surface.
    """
    return {agent_id: dict(rule) for agent_id, rule in DEFAULT_AGENT_WHITELIST.items()}


def validate_whitelist_rule(
    agent_id: str,
    repo: str,
    package: str,
    distributions: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """校验并规范化教师提交的白名单条目;非法抛 ValueError(中文)。"""
    agent_id = str(agent_id).strip()
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError(
            "agent 标识只能包含小写字母/数字/./_/-,以字母或数字开头(≤64 字符)"
        )
    repo_slug = _repo_slug(str(repo).strip())
    if not _REPO_SLUG_RE.match(repo_slug):
        raise ValueError("仓库必须是 owner/repo 形式的 GitHub 仓库")
    pkg = _package_base(str(package).strip())
    if not pkg or not _PACKAGE_RE.match(pkg):
        raise ValueError("包名不合法,且不要携带版本后缀")
    dists = sorted({str(d).strip() for d in distributions if str(d).strip()})
    if not dists:
        raise ValueError("至少选择一种发行类型")
    unknown = [d for d in dists if d not in ALLOWED_DISTRIBUTIONS]
    if unknown:
        raise ValueError(f"不支持的发行类型: {', '.join(unknown)}")
    return {"repo": repo_slug, "package": pkg, "distributions": dists}


def add_whitelist_agent(
    base_dir: Path | str,
    agent_id: str,
    repo: str,
    package: str,
    distributions: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """新增/更新自定义白名单条目(同 id 覆盖;撤销对默认条目的移除)。"""
    rule = validate_whitelist_rule(agent_id, repo, package, distributions)
    agent_id = str(agent_id).strip()
    overrides = load_whitelist_overrides(base_dir)
    overrides["added"][agent_id] = rule
    overrides["removed"] = [x for x in overrides["removed"] if x != agent_id]
    _save_whitelist_overrides(base_dir, overrides)
    return rule


def remove_whitelist_agent(base_dir: Path | str, agent_id: str) -> bool:
    """移除白名单条目;默认条目记入 removed。未知条目返回 False。"""
    agent_id = str(agent_id).strip()
    overrides = load_whitelist_overrides(base_dir)
    if agent_id in overrides["added"]:
        overrides["added"].pop(agent_id)
        # 若同名默认条目存在,同时记入 removed,避免默认条目重新出现。
        if agent_id in DEFAULT_AGENT_WHITELIST and agent_id not in overrides["removed"]:
            overrides["removed"].append(agent_id)
    elif agent_id in DEFAULT_AGENT_WHITELIST:
        if agent_id not in overrides["removed"]:
            overrides["removed"].append(agent_id)
    else:
        return False
    _save_whitelist_overrides(base_dir, overrides)
    return True


def reset_whitelist(base_dir: Path | str) -> None:
    """删除全部覆盖,恢复默认白名单。"""
    whitelist_overrides_path(base_dir).unlink(missing_ok=True)


@dataclass
class AgentEntry:
    """一次审核通过的 agent 安装快照。"""

    agent_id: str
    registry_version: str | None
    distribution: str  # npx | uvx | binary
    package: str  # npm 包名 / PyPI 包名 / 二进制名
    version: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    sha256: str | None = None
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "registry_version": self.registry_version,
            "distribution": self.distribution,
            "package": self.package,
            "version": self.version,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "sha256": self.sha256,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentEntry":
        return cls(
            agent_id=data["agent_id"],
            registry_version=data.get("registry_version"),
            distribution=data["distribution"],
            package=data["package"],
            version=data["version"],
            command=data["command"],
            args=list(data.get("args", [])),
            env=dict(data.get("env", {})),
            sha256=data.get("sha256"),
            source_url=data.get("source_url"),
        )


def _pick_distribution(
    agent_id: str,
    raw: dict[str, Any],
    whitelist: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """从 Registry 条目中选取一个白名单允许的发行段。"""
    rule = whitelist.get(agent_id)
    if rule is None:
        raise RegistryError(f"agent {agent_id} 不在本机白名单中,拒绝运行")
    dist = raw.get("distribution")
    if not isinstance(dist, dict) or not dist:
        raise RegistryError(f"agent {agent_id} 的条目缺少发行描述")
    allowed = set(rule["distributions"])
    for dist_type in _DISTRIBUTION_ORDER:
        if dist_type in dist and dist_type in allowed:
            return dist_type, dist[dist_type]
    present = sorted(dist.keys())
    raise RegistryError(
        f"agent {agent_id} 的发行类型 {present} 与白名单 {sorted(allowed)} 不符"
    )


def audit_entry(
    agent_id: str,
    raw: dict[str, Any],
    *,
    whitelist: dict[str, dict[str, Any]] | None = None,
) -> None:
    """本地审核:三重校验通过才允许运行。不通过抛 RegistryError。

    ``raw`` 是官方 Registry 条目(见模块 docstring 的格式说明)。
    ``whitelist`` 为 None 时使用默认白名单;API 层传入教师生效名单。
    审核只依赖白名单身份与发行描述的内在一致性,不信任 Registry 自身声明。
    """
    wl = DEFAULT_AGENT_WHITELIST if whitelist is None else whitelist
    rule = wl.get(agent_id)
    if rule is None:
        raise RegistryError(f"agent {agent_id} 不在本机白名单中,拒绝运行")

    repo = _repo_slug(str(raw.get("repository") or ""))
    if repo != rule["repo"]:
        raise RegistryError(
            f"agent {agent_id} 的仓库身份 {repo!r} 与白名单 {rule['repo']!r} 不符"
        )

    version = str(raw.get("version") or "")
    if not is_exact_version(version):
        raise RegistryError(
            f"agent {agent_id} 版本 {version!r} 不是精确版本;拒绝 latest/范围/Git/本地路径"
        )

    dist_type, section = _pick_distribution(agent_id, raw, wl)

    if dist_type in ("npx", "uvx"):
        package = _package_base(str(section.get("package") or ""))
        if package != rule["package"]:
            raise RegistryError(
                f"agent {agent_id} 的包身份 {package!r} 与白名单 "
                f"{rule['package']!r} 不符"
            )
        return

    # binary:每个平台段都必须携带 SHA-256,且当前平台可用。
    if not isinstance(section, dict) or not section:
        raise RegistryError(f"agent {agent_id} 的 binary 发行缺少平台段")
    for platform_key, platform_section in section.items():
        if not isinstance(platform_section, dict) or not platform_section.get("sha256"):
            raise RegistryError(
                f"agent {agent_id} 的 binary 发行平台 {platform_key} 缺少 SHA-256"
            )
    if _platform_key() not in section:
        raise RegistryError(
            f"agent {agent_id} 的 binary 发行不包含当前平台 {_platform_key()}"
        )


def audit_snapshot(
    agent_id: str,
    snapshot: dict[str, Any],
    *,
    whitelist: dict[str, dict[str, Any]] | None = None,
) -> None:
    """复审已安装快照(AgentEntry.to_dict());worker 领取 run 时调用。

    快照不含仓库 URL,按"包身份 + 发行类型 + 精确版本"校验;whitelist
    为 None 时使用默认白名单,worker 传入教师生效名单。
    """
    wl = DEFAULT_AGENT_WHITELIST if whitelist is None else whitelist
    rule = wl.get(agent_id)
    if rule is None:
        raise RegistryError(f"agent {agent_id} 不在本机白名单中,拒绝运行")
    distribution = str(snapshot.get("distribution") or "")
    if distribution not in rule["distributions"]:
        raise RegistryError(
            f"agent {agent_id} 的发行类型 {distribution!r} 与白名单 "
            f"{sorted(rule['distributions'])} 不符"
        )
    version = str(snapshot.get("version") or "")
    if version and not is_exact_version(version):
        raise RegistryError(f"agent {agent_id} 快照版本 {version!r} 不是精确版本")
    package = _package_base(str(snapshot.get("package") or ""))
    if package != rule["package"]:
        raise RegistryError(
            f"agent {agent_id} 的包身份 {package!r} 与白名单 {rule['package']!r} 不符"
        )


def build_agent_entry(
    agent_id: str,
    raw: dict[str, Any],
    *,
    requested_version: str | None = None,
    whitelist: dict[str, dict[str, Any]] | None = None,
) -> AgentEntry:
    """审核 Registry 条目并物化为启动快照。

    ``requested_version`` 必须与 Registry 条目当前版本一致(Registry 每个
    条目只声明一个当前版本);为 None 时采用条目版本。
    ``whitelist`` 为 None 时使用默认白名单。
    """
    wl = DEFAULT_AGENT_WHITELIST if whitelist is None else whitelist
    rule = wl.get(agent_id)
    if rule is None:
        raise RegistryError(f"agent {agent_id} 不在本机白名单中,拒绝运行")

    version = str(requested_version or raw.get("version") or "")
    if not is_exact_version(version):
        raise RegistryError(f"版本 {version!r} 不是精确版本")
    if version != str(raw.get("version") or ""):
        raise RegistryError(
            f"请求版本 {version} 与 Registry 当前版本 {raw.get('version')} 不一致;"
            "请刷新 Registry 后重试"
        )

    audit_entry(agent_id, raw, whitelist=wl)

    dist_type, section = _pick_distribution(agent_id, raw, wl)
    registry_version = raw.get("registry_version")
    common = {
        "agent_id": agent_id,
        "registry_version": str(registry_version) if registry_version else None,
        "distribution": dist_type,
        "version": version,
    }

    if dist_type == "npx":
        package = _package_base(str(section.get("package")))
        return AgentEntry(
            package=package,
            command="npx",
            args=["-y", f"{package}@{version}", *map(str, section.get("args", []))],
            env={str(k): str(v) for k, v in (section.get("env") or {}).items()},
            **common,
        )
    if dist_type == "uvx":
        package = _package_base(str(section.get("package")))
        return AgentEntry(
            package=package,
            command="uvx",
            args=[f"{package}=={version}", *map(str, section.get("args", []))],
            env={str(k): str(v) for k, v in (section.get("env") or {}).items()},
            **common,
        )

    # binary:选取当前平台段;command 先记为可执行名,安装后指向解压路径。
    platform_section = section[_platform_key()]
    cmd = str(platform_section.get("cmd") or rule["package"])
    return AgentEntry(
        package=rule["package"],
        command=Path(cmd).name,
        args=[str(a) for a in platform_section.get("args", [])],
        env={str(k): str(v) for k, v in (platform_section.get("env") or {}).items()},
        sha256=str(platform_section.get("sha256")),
        source_url=str(platform_section.get("archive")),
        **common,
    )


class RegistryClient:
    """带缓存与离线兜底的 Registry 读取器。"""

    def __init__(
        self,
        cache_dir: Path,
        registry_url: str = DEFAULT_REGISTRY_URL,
        *,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        http_timeout: float = 15.0,
        whitelist: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_file = cache_dir / "registry.json"
        self._url = registry_url
        self._ttl = ttl_seconds
        self._timeout = http_timeout
        # None = 默认白名单;API 层传入教师生效名单。
        self._whitelist = whitelist

    # -- 缓存 ---------------------------------------------------------------

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._cache_file.read_text(encoding="utf-8"))
            fetched_at = float(raw.get("fetched_at", 0))

            if fetched_at <= 0:
                return None
            # 缓存过期仍然返回(offline 兜底),由调用方决定是否强制刷新。
            raw["_stale"] = True
            return raw
        except (OSError, ValueError):
            return None

    def _write_cache(self, payload: dict[str, Any]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- 读取 ---------------------------------------------------------------

    async def fetch(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """返回 Registry 载荷;网络失败时回退到最近一次校验成功的缓存。"""
        import time

        now = time.time()
        if not force_refresh:
            cache = self._read_cache()
            if cache is not None:
                age = now - float(cache.get("fetched_at", 0))
                if age < self._ttl:
                    cache.pop("_stale", None)
                    return cache
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            cache = self._read_cache()
            if cache is not None:
                logger.warning("Registry 刷新失败,使用缓存快照: %s", exc)
                return cache
            raise RegistryError(f"Registry 不可达且无缓存: {exc}") from exc
        payload["fetched_at"] = time.time()
        self._write_cache(payload)
        return payload

    def lookup(self, payload: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
        """从 Registry 载荷中取指定 agent 的条目(列表或字典均兼容)。"""
        agents = payload.get("agents")
        if isinstance(agents, dict):
            return agents.get(agent_id)
        if isinstance(agents, list):
            for item in agents:
                if isinstance(item, dict) and item.get("id") == agent_id:
                    return item
        return None

    async def resolve(
        self, agent_id: str, version: str, *, force_refresh: bool = False
    ) -> AgentEntry:
        """解析为精确版本的启动描述;解析结果必须先通过本地审核。"""
        if not is_exact_version(version):
            raise RegistryError(f"版本 {version!r} 不是精确版本")
        payload = await self.fetch(force_refresh=force_refresh)
        raw = self.lookup(payload, agent_id)
        if raw is None:
            raise RegistryError(f"Registry 中没有 agent {agent_id}")
        return build_agent_entry(
            agent_id, raw, requested_version=version, whitelist=self._whitelist
        )


# ---------------------------------------------------------------------------
# 归档安全解压(binary 发行)
# ---------------------------------------------------------------------------


def safe_extract(
    archive: Path, dest: Path, *, allowed_executables: set[str]
) -> list[Path]:
    """安全解压 .tar.gz/.zip 到 dest;返回解压出的可执行文件路径。

    拒绝:绝对路径、路径穿越(``..``)、符号链接、清单外可执行文件。
    """
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    def _check_member(name: str) -> Path:
        member_path = (dest / name).resolve()
        if dest.resolve() not in member_path.parents and member_path != dest.resolve():
            raise RegistryError(f"归档成员 {name!r} 存在路径穿越")
        if name.startswith("/") or ".." in Path(name).parts:
            raise RegistryError(f"归档成员 {name!r} 含绝对路径或穿越段")
        return member_path

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:  # S_IFLNK
                    raise RegistryError(f"归档包含符号链接: {info.filename!r}")
                target = _check_member(info.filename)
                base = Path(info.filename).name
                if base in allowed_executables:
                    extracted.append(target)
            zf.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if member.issym() or member.islnk():
                    raise RegistryError(f"归档包含链接: {member.name!r}")
                target = _check_member(member.name)
                base = Path(member.name).name
                if member.isdev():
                    raise RegistryError(f"归档包含设备文件: {member.name!r}")
                if base in allowed_executables:
                    extracted.append(target)
            tf.extractall(dest, filter="data")

    for target in extracted:
        if not target.exists():
            raise RegistryError(f"可执行文件 {target.name!r} 在归档中缺失")
        target.chmod(0o755)
    return extracted


def verify_sha256(file_path: Path, expected: str) -> None:
    """校验下载文件的 SHA-256;不符抛 RegistryError。"""
    import hashlib

    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise RegistryError(
            f"SHA-256 校验失败: 期望 {expected[:12]}…,实际 {digest[:12]}…"
        )


def agent_cache_dir(base: Path, agent_id: str, version: str) -> Path:
    """受控缓存路径:base/agents/<id>/<version>;不写入项目源码目录。"""
    if not is_exact_version(version) and agent_id != "local":
        raise RegistryError(f"版本 {version!r} 非法")
    return base / "agents" / agent_id / version


def download_to(url: str, dest: Path, *, timeout: float = 120.0) -> None:
    """同步下载(安装动作不频繁,不必 async);dest 的父目录必须已存在。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=dest.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=1 << 16):
                        fh.write(chunk)
        tmp_path.replace(dest)
    except httpx.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        raise RegistryError(f"下载失败: {exc}") from exc
