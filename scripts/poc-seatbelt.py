#!/usr/bin/env python3
"""一次性 PoC：验证 Seatbelt 能否替代 Docker 跑 Python 测试。

本轮策略变化（基于上一轮实测）：
  上一轮 M1 用 /bin/cat（不涉及 Python）也 SIGABRT(rc=-6)，说明窄读白名单缺的是
  「任何进程启动都必需」的某条路径，而不是 Python 特有的操作。宽读（M4/M5）正常。
  因此本轮改为「宽读打底 + 把 home 收回」，即 Codex 对 .git 用的 allow-then-deny 手法。

用法（必须在普通终端里跑）::

    python3 scripts/poc-seatbelt.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
CAT_BIN = "/bin/cat"
TEST_TIMEOUT_SECONDS = 10
HOME = str(Path.home())

SYSTEM_READ_ROOTS = (
    "/usr",
    "/System",
    "/Library",
    "/bin",
    "/sbin",
    "/dev",
    "/opt/homebrew",
    "/private/tmp",
    "/tmp",
)

# 操作维度白名单，取自 codex base policy。三处刻意收紧：
#   process-exec 按 literal 限制（阻止学生代码起 /bin/sh）；
#   不给 process-fork（阻止 fork bomb）；不写宽 file-read*（由调用方决定）。
BASE_OPS = """(allow process-info* (target same-sandbox))
(allow signal (target same-sandbox))
(allow sysctl-read)
(allow iokit-open (iokit-registry-entry-class "RootDomainUserClient"))
(allow mach-lookup)
(allow ipc-posix-sem)
(allow ipc-posix-shm-read-data ipc-posix-shm-write-create ipc-posix-shm-write-unlink
  (ipc-posix-name-regex #"^/__KMP_REGISTERED_LIB_[0-9]+$"))
(allow file-write-data
  (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))"""


# 退路：只收回已知敏感目录，而不是整个 home。信息量不如全收回，但不会误伤解释器。
SENSITIVE_DIRS = (
    HOME + "/Library/Application Support/AI-Marking",
    HOME + "/.ssh",
    HOME + "/.aws",
    HOME + "/.config",
    HOME + "/.gnupg",
    HOME + "/Documents",
    HOME + "/Desktop",
    HOME + "/Downloads",
)


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _exec_paths() -> tuple[str, ...]:
    paths: list[str] = []
    for candidate in (sys.executable, _real(sys.executable)):
        if candidate and candidate not in paths:
            paths.append(candidate)
    return tuple(paths)


def _interpreter_roots() -> tuple[str, ...]:
    roots: list[str] = []
    for candidate in (sys.executable, sys.prefix, sys.base_prefix):
        resolved = _real(candidate)
        if resolved in {"/", HOME}:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _exec_rules(exec_mode: str) -> list[str]:
    if exec_mode == "any":
        return ["(allow process-exec)"]
    literals = " ".join(f'(literal "{p}")' for p in _exec_paths())
    dirs = " ".join(
        f'(subpath "{Path(p).parent}")' for p in _exec_paths() if Path(p).parent != Path("/")
    )
    return [f"(allow process-exec {literals})", f"(allow process-exec {dirs})"]


def build_narrow(case_dir: Path, extra_roots: tuple[str, ...] = ()) -> str:
    """默认拒绝 + 路径白名单。上一轮证明该路线会让任何进程 SIGABRT。"""
    case_dir = case_dir.resolve()
    roots = (*SYSTEM_READ_ROOTS, *extra_roots, *_interpreter_roots())
    allow = " ".join(f'(subpath "{root}")' for root in roots)
    lines = ["(version 1)", "(deny default)", *_exec_rules("python"), BASE_OPS]
    # metadata 必须在宽 allow 之前：后写的规则优先，若放在 deny 之后会把
    # home 的 stat 权限重新打开（读不到内容，但能枚举整个 home 的文件名）。
    lines.append("(allow file-read-metadata)")
    lines.append(f"(allow file-read* {allow})")
    lines.append(f'(allow file-read* (subpath "{case_dir}"))')
    lines.append(f'(allow file-write* (subpath "{case_dir}"))')
    lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def build_carveout(
    case_dir: Path,
    *,
    exec_mode: str = "python",
    deny_roots: tuple[str, ...] | None = None,
) -> str:
    """宽读打底，再把指定目录收回，并单独放回解释器与 case 目录。

    SBPL 后写的规则覆盖先写的，因此 allow 之后紧跟 deny 即可收回，
    再 allow 特定子路径放回来 —— Codex 就是这样保护 .git 的。
    ``deny_roots`` 为 None 时收回整个 home；否则只收回给定的敏感目录。
    """
    case_dir = case_dir.resolve()
    roots_to_deny = (HOME,) if deny_roots is None else deny_roots
    lines = ["(version 1)", "(deny default)", *_exec_rules(exec_mode), BASE_OPS]
    # metadata 先放行，宽 allow 与 deny 都在其后，保证 deny 是最后生效的规则。
    lines.append("(allow file-read-metadata)")
    lines.append("(allow file-read*)")
    for root in roots_to_deny:
        lines.append(f'(deny file-read* (subpath "{root}"))')
    for root in (*_interpreter_roots(), str(case_dir)):
        lines.append(f'(allow file-read* (subpath "{root}"))')
    lines.append(f'(allow file-write* (subpath "{case_dir}"))')
    lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def run(profile: str, argv: list[str], workdir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [SANDBOX_EXEC, "-p", profile, *argv],
        capture_output=True,
        text=True,
        timeout=TEST_TIMEOUT_SECONDS,
        env={
            "HOME": str(workdir),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(workdir),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


def brief(text: str, limit: int = 70) -> str:
    flat = " | ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    return flat[:limit]


CASES: tuple[dict[str, str], ...] = (
    {
        "name": "读 case 目录",
        "expect": "ALLOWED",
        "code": """
import sys
try:
    open(sys.argv[1]).read(1)
except (PermissionError, OSError):
    print("DENIED")
else:
    print("ALLOWED")
""",
    },
    {
        "name": "读 HOME 诱饵",
        "expect": "DENIED",
        "code": """
import sys
try:
    open(sys.argv[1]).read(1)
except (PermissionError, OSError):
    print("DENIED")
else:
    print("LEAKED")
""",
    },
    {
        "name": "写 HOME",
        "expect": "DENIED",
        "code": """
import sys
try:
    open(sys.argv[1], "w").write("x")
except (PermissionError, OSError):
    print("DENIED")
else:
    print("WRITTEN")
""",
    },
    {
        "name": "os.fork()",
        "expect": "DENIED",
        "code": """
import os
try:
    os.fork()
except (PermissionError, OSError):
    print("DENIED")
else:
    print("FORKED")
""",
    },
    {
        "name": "subprocess 起 sh",
        "expect": "DENIED",
        "code": """
import subprocess, sys
try:
    subprocess.run(["/bin/sh", "-c", "echo hi"], capture_output=True, timeout=3)
except BaseException as exc:
    print("denied:" + type(exc).__name__, file=sys.stderr)
    print("DENIED")
else:
    print("EXECED")
""",
    },
    {
        "name": "网络 connect",
        "expect": "DENIED",
        "code": """
import socket, sys
try:
    s = socket.socket()
    s.settimeout(2)
    s.connect(("1.1.1.1", 53))
except BaseException as exc:
    print("denied:" + type(exc).__name__, file=sys.stderr)
    print("DENIED")
else:
    print("CONNECTED")
finally:
    s.close()
""",
    },
    {
        "name": "列 pgdata 目录",
        "expect": "DENIED",
        "code": """
import os, sys
try:
    os.listdir(sys.argv[1])
except (PermissionError, FileNotFoundError, OSError):
    print("DENIED")
else:
    print("LISTED")
""",
    },
)


def main() -> int:
    if not Path(SANDBOX_EXEC).exists():
        print(f"找不到 {SANDBOX_EXEC}")
        return 1

    probe = subprocess.run(
        [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/bin/echo", "OK"],
        capture_output=True,
        text=True,
    )
    if "OK" not in probe.stdout:
        print("sandbox-exec 无法使用：", brief(probe.stderr or probe.stdout))
        print("若提示 Operation not permitted，说明当前进程已在沙箱内，请换普通终端重跑。")
        return 1

    work = Path(tempfile.mkdtemp(prefix="aim-seatbelt-poc-"))
    decoy_dir = Path.home() / ".aim-seatbelt-poc"
    pgdata = Path.home() / "Library/Application Support/AI-Marking/pgdata"
    try:
        case_dir = work / "case"
        case_dir.mkdir()
        case_dir = case_dir.resolve()
        (case_dir / "ok.txt").write_text("INSIDE_OK\n", encoding="utf-8")
        decoy_dir.mkdir(exist_ok=True)
        decoy = decoy_dir / "secret.txt"
        decoy.write_text("FAKE_KEY=sk-should-never-be-readable\n", encoding="utf-8")

        print(f"HOME     : {HOME}")
        print(f"解释器   : {sys.executable}")
        print(f"case 目录: {case_dir}\n")

        py_boot = [sys.executable, "-c", "print('BOOT_OK')"]
        matrix = [
            ("M1 窄读 + /private/var（验证上轮 ABRT 是否路径缺失）",
             build_narrow(case_dir, ("/private/var",)), py_boot, "BOOT_OK"),
            ("M2 窄读 + /private/var + /private/etc",
             build_narrow(case_dir, ("/private/var", "/private/etc", HOME + "/Library/Preferences")),
             py_boot, "BOOT_OK"),
            ("M3 宽读 + 收回 home + 窄 exec（生产候选）",
             build_carveout(case_dir), py_boot, "BOOT_OK"),
            ("M4 宽读 + 收回 home + 宽 exec（对照）",
             build_carveout(case_dir, exec_mode="any"), py_boot, "BOOT_OK"),
            ("M5 宽读 + 只收回敏感目录（退路）",
             build_carveout(case_dir, deny_roots=SENSITIVE_DIRS), py_boot, "BOOT_OK"),
            ("M6 M3 + 再收回 /private/var/folders（可选加固）",
             build_carveout(case_dir, deny_roots=(HOME, "/private/var/folders")),
             py_boot, "BOOT_OK"),
        ]

        print("=== 探针矩阵 ===")
        print(f"{'探针':<46}{'rc':>4}  输出")
        print("-" * 100)
        booted: dict[str, str] = {}
        for label, profile, argv, token in matrix:
            try:
                result = run(profile, argv, case_dir)
            except subprocess.TimeoutExpired:
                print(f"{label:<46}{'T':>4}  超时")
                continue
            ok = token in result.stdout
            print(f"{label:<46}{result.returncode:>4}  {brief(result.stdout if ok else (result.stderr or result.stdout), 48)}")
            if ok:
                booted[label] = profile
        print("-" * 100)

        # 偏好最严的：窄读优先，其次 carve-out + 窄 exec，最后 carve-out + 宽 exec。
        chosen: tuple[str, str] | None = None
        for label, _profile, _argv, _token in matrix:
            if label in booted:
                chosen = (label, booted[label])
                break
        if chosen is None:
            print("\n全部失败：该 macOS 上 Seatbelt 无法承载运行器，建议保留 Docker。")
            return 1
        if chosen[0].startswith("M5"):
            print("\n采用「宽读 + 只收回敏感目录」退路。注意：这是黑名单形态，")
            print("home 下未列出的其他文件仍可读，安全性取决于清单是否列全。")
        elif chosen[0].startswith(("M3", "M4")):
            print(f"\n采用「宽读 + 收回 {HOME}」形态。可读范围 = 系统路径 + 解释器目录 + case 目录；")
            print("home 下除解释器外一律不可读，因此 pgdata / .env / 其他作业都不在射程内。")

        m6_booted = any(lbl.startswith("M6") for lbl in booted)
        if m6_booted and chosen[0].startswith("M3"):
            print("\n可选加固：M6 也能启动 → 连 /private/var/folders（其他 App 的临时目录）")
            print("一起收回同样可行，后续可并入生产 profile。")

        label, profile = chosen
        print(f"\n=== 行为断言（使用：{label}）===")
        rows: list[tuple[str, str, str, str]] = []
        for case in CASES:
            arg = ""
            if case["name"] == "读 case 目录":
                arg = str(case_dir / "ok.txt")
            elif case["name"] == "读 HOME 诱饵":
                arg = str(decoy)
            elif case["name"] == "写 HOME":
                arg = str(decoy_dir / "evil.txt")
            elif case["name"] == "列 pgdata 目录":
                arg = str(pgdata)
            try:
                result = run(profile, [sys.executable, "-c", case["code"], arg], case_dir)
            except subprocess.TimeoutExpired:
                rows.append(("FAIL", case["name"], f"期望 {case['expect']} / 实际 超时", ""))
                continue
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            verdict = lines[-1] if lines else "(空)"
            rows.append(
                (
                    "PASS" if verdict == case["expect"] else "FAIL",
                    case["name"],
                    f"期望 {case['expect']} / 实际 {verdict}",
                    f"rc={result.returncode} {brief(result.stderr)}"[:64],
                )
            )
        (decoy_dir / "evil.txt").unlink(missing_ok=True)

        width = max(len(row[1]) for row in rows) + 2
        print(f"{'结果':<6}{'用例':<{width}}{'判定':<28}备注")
        print("-" * 100)
        for status, name, verdict, detail in rows:
            print(f"{status:<6}{name:<{width}}{verdict:<28}{detail}")
        failed = sum(1 for row in rows if row[0] == "FAIL")
        print("-" * 100)
        print("全部通过：Seatbelt 可作为 container 之外的第二后端。"
              if failed == 0 else f"{failed} 项未达预期。")
        return 0 if failed == 0 else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(decoy_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
