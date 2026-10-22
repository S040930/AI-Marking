"""Validate the fixed language runtimes and production Seatbelt policy."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.code_runner_errors import CodeRunnerInfrastructureError
from app.services.sandbox import (
    SANDBOX_EXEC,
    SandboxPolicy,
    build_sandbox_argv,
    python_executable,
    runtime_roots,
)

EXPECTED_PYTHON_VERSION = "3.12.13"
EXPECTED_R_VERSION = "4.5.2"
EXPECTED_JAVA_VERSION = "11.0.28"
JAVA_HOME_TOOL = Path("/usr/libexec/java_home")
RSCRIPT_TOOL = Path("/usr/local/bin/Rscript")

R_PROFILE_SOURCE = "\n".join(
    (
        "base_environment <- baseenv()",
        "unlockBinding('Sys.which', base_environment)",
        "assign('Sys.which', function(names) structure(rep('', length(names)), names = names), envir = base_environment)",
        "lockBinding('Sys.which', base_environment)",
    )
)


@lru_cache(maxsize=1)
def load_runtimes() -> dict[str, Any]:
    if sys.platform != "darwin":
        raise CodeRunnerInfrastructureError("Seatbelt 代码运行仅支持 macOS")
    python = python_executable()
    python_result = subprocess.run(
        [str(python), "--version"], capture_output=True, text=True, timeout=4, check=True
    )
    python_version = (python_result.stdout or python_result.stderr).strip().removeprefix("Python ")
    if python_version != EXPECTED_PYTHON_VERSION:
        raise CodeRunnerInfrastructureError(
            f"Python 运行时版本必须为 {EXPECTED_PYTHON_VERSION}，当前为 {python_version}"
        )

    if not RSCRIPT_TOOL.is_file():
        raise CodeRunnerInfrastructureError(f"未找到固定 R 运行时：{RSCRIPT_TOOL}")
    rscript = Path(os.path.realpath(RSCRIPT_TOOL))
    r_version_result = subprocess.run(
        [str(rscript), "--version"], capture_output=True, text=True, timeout=30, check=True
    )
    r_match = re.search(
        r"version\s+([0-9]+\.[0-9]+\.[0-9]+)",
        r_version_result.stderr + r_version_result.stdout,
    )
    if r_match is None or r_match.group(1) != EXPECTED_R_VERSION:
        actual = r_match.group(1) if r_match else "unknown"
        raise CodeRunnerInfrastructureError(
            f"R 运行时版本必须为 {EXPECTED_R_VERSION}，当前为 {actual}"
        )
    r_home_result = subprocess.run(
        [str(rscript), "--vanilla", "-e", "cat(R.home())"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "TZ": "UTC"},
    )
    r_home = Path(r_home_result.stdout.strip()).resolve(strict=True)
    r_main = r_home / "bin" / "exec" / "R"
    if not r_main.is_file():
        raise CodeRunnerInfrastructureError("R 安装缺少固定主程序")

    java_home_result = subprocess.run(
        [str(JAVA_HOME_TOOL)],
        capture_output=True,
        text=True,
        timeout=4,
        check=True,
    )
    java_home = Path(java_home_result.stdout.strip()).resolve(strict=True)
    java = java_home / "bin" / "java"
    javac = java_home / "bin" / "javac"
    java_version_result = subprocess.run(
        [str(java), "-version"], capture_output=True, text=True, timeout=8, check=True
    )
    java_match = re.search(r'version "([0-9]+\.[0-9]+\.[0-9]+)', java_version_result.stderr)
    if java_match is None or java_match.group(1) != EXPECTED_JAVA_VERSION:
        actual = java_match.group(1) if java_match else "unknown"
        raise CodeRunnerInfrastructureError(
            f"Java 运行时版本必须为 {EXPECTED_JAVA_VERSION}，当前为 {actual}"
        )
    if not java.is_file() or not javac.is_file():
        raise CodeRunnerInfrastructureError("固定 Java 安装缺少 java 或 javac")

    executables = (python, r_main, java, javac)
    roots = tuple(sorted(set(runtime_roots(executables)) | {r_home, java_home}, key=str))
    return {
        "python": python,
        "rscript": rscript,
        "r_home": r_home,
        "r_main": r_main,
        "java": java,
        "javac": javac,
        "executables": executables,
        "runtime_roots": roots,
        "versions": {
            "python": python_version,
            "r": EXPECTED_R_VERSION,
            "java": EXPECTED_JAVA_VERSION,
        },
    }


@lru_cache(maxsize=1)
def sandbox_readiness() -> tuple[bool, str]:
    """Fail closed unless the native Seatbelt boundary and fixed runtimes work."""
    if sys.platform != "darwin" or not Path(SANDBOX_EXEC).is_file():
        return False, f"未找到 macOS Seatbelt 执行器：{SANDBOX_EXEC}"
    try:
        permissive_probe = subprocess.run(
            [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Seatbelt 自检失败：{type(exc).__name__}"
    if permissive_probe.returncode:
        detail = (permissive_probe.stderr or permissive_probe.stdout).strip()[:240]
        return False, f"Seatbelt 自检未通过：{detail or 'sandbox-exec 失败'}"
    try:
        runtimes = load_runtimes()
    except (CodeRunnerInfrastructureError, OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    try:
        probe_restrictive_profile(runtimes)
    except (OSError, subprocess.SubprocessError, CodeRunnerInfrastructureError) as exc:
        return False, f"Seatbelt 限制策略自检失败：{exc}"
    versions = runtimes["versions"]
    return True, (
        "macOS Seatbelt 与固定运行时已就绪："
        f"Python {versions['python']} / R {versions['r']} / Java {versions['java']}。"
    )


def probe_restrictive_profile(runtimes: dict[str, Any]) -> None:
    """Apply the production policy to a harmless read/write smoke check."""
    with tempfile.TemporaryDirectory(prefix="ai-marking-seatbelt-check-") as temporary:
        root = Path(temporary)
        input_root = root / "input"
        work_root = root / "work"
        support_root = root / "support"
        input_root.mkdir(mode=0o700)
        work_root.mkdir(mode=0o700)
        support_root.mkdir(mode=0o700)
        input_file = input_root / "probe.txt"
        output_file = work_root / "probe.txt"
        input_file.write_text("seatbelt-input", encoding="utf-8")
        input_file.chmod(0o444)
        r_profile = support_root / "r-init.R"
        r_profile.write_text(R_PROFILE_SOURCE, encoding="utf-8")
        r_profile.chmod(0o444)
        policy = SandboxPolicy(
            input_root=input_root,
            work_root=work_root,
            executables=runtimes["executables"],
            runtime_roots=runtimes["runtime_roots"],
            additional_read_roots=(support_root,),
        )
        script = "\n".join(
            (
                "import pathlib, socket, subprocess, sys",
                "assert pathlib.Path(sys.argv[1]).read_text() == 'seatbelt-input'",
                "pathlib.Path(sys.argv[2]).write_text('seatbelt-output')",
                "try:",
                "    pathlib.Path(sys.argv[1]).parent.joinpath('write-denied').write_text('bad')",
                "except OSError:",
                "    pass",
                "else:",
                "    raise SystemExit('input directory is writable')",
                "for denied in (sys.argv[3], '/private/var/folders'):",
                "    try:",
                "        list(pathlib.Path(denied).iterdir())",
                "    except OSError:",
                "        pass",
                "    else:",
                "        raise SystemExit('protected directory is readable')",
                "try:",
                "    subprocess.run(['/bin/true'], timeout=2, check=False)",
                "except (OSError, subprocess.SubprocessError):",
                "    pass",
                "else:",
                "    raise SystemExit('student child process is allowed')",
                "try:",
                "    with socket.socket() as sock: sock.bind(('127.0.0.1', 0))",
                "except OSError:",
                "    pass",
                "else:",
                "    raise SystemExit('network socket is allowed')",
                "print('SEATBELT_PROFILE_READY')",
            )
        )
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(work_root),
            "TMPDIR": str(work_root),
            "TMP": str(work_root),
            "TEMP": str(work_root),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            result = subprocess.run(
                build_sandbox_argv(
                    policy,
                    (
                        str(runtimes["python"]),
                        "-s",
                        "-S",
                        "-B",
                        "-c",
                        script,
                        str(input_file),
                        str(output_file),
                        str(Path.home().resolve(strict=True)),
                    ),
                ),
                cwd=work_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodeRunnerInfrastructureError("Python Seatbelt 探针超时") from exc
        if result.returncode or "SEATBELT_PROFILE_READY" not in result.stdout:
            detail = (result.stderr or result.stdout or "无输出").strip()[:300]
            raise CodeRunnerInfrastructureError(detail)
        if not output_file.is_file() or output_file.read_text(encoding="utf-8") != "seatbelt-output":
            raise CodeRunnerInfrastructureError("Seatbelt 工作目录写入未通过")

        runtime_checks = (
            (
                runtimes["r_main"],
                (
                    "--slave",
                    "--no-site-file",
                    "--no-restore",
                    "--no-save",
                    "-e",
                    "stopifnot(requireNamespace('utils', quietly=TRUE), requireNamespace('stats', quietly=TRUE))",
                ),
                {
                    "R_HOME": str(runtimes["r_home"]),
                    "R_USER": str(work_root),
                    "R_LIBS_USER": str(work_root / "empty-r-library"),
                    "R_PROFILE_USER": str(r_profile),
                    "R_PROFILE_SITE": "/dev/null",
                    "R_ENVIRON": "/dev/null",
                    "R_ENVIRON_USER": "/dev/null",
                },
            ),
            (
                runtimes["java"],
                ("-Duser.home=" + str(work_root), "-version"),
                {"JAVA_HOME": str(runtimes["java"].parent.parent)},
            ),
            (
                runtimes["javac"],
                ("-version",),
                {"JAVA_HOME": str(runtimes["java"].parent.parent)},
            ),
        )
        for executable, arguments, extra_environment in runtime_checks:
            try:
                runtime_result = subprocess.run(
                    build_sandbox_argv(policy, (str(executable), *arguments)),
                    cwd=work_root,
                    env={**environment, **extra_environment},
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CodeRunnerInfrastructureError(
                    f"{Path(executable).name} Seatbelt 探针超时"
                ) from exc
            if runtime_result.returncode:
                detail = (runtime_result.stderr or runtime_result.stdout or "无输出").strip()[:300]
                raise CodeRunnerInfrastructureError(
                    f"{Path(executable).name} 无法在 Seatbelt 下启动：{detail}"
                )
