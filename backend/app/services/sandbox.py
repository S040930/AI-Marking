"""macOS Seatbelt policy for running untrusted student programs."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

SEATBELT_PARTIAL = "seatbelt:partial"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SANDBOX_POLICY_VERSION = "seatbelt-v1"
MAX_CASE_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_CASE_DISK_BYTES = 256 * 1024 * 1024
MAX_CASE_FILES = 10_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_OPEN_FILES = 256


def _literal(path: Path | str) -> str:
    value = str(path)
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise ValueError("Seatbelt path contains an invalid character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class SandboxPolicy:
    """One execution's exact read roots, writable work area and executable set."""

    input_root: Path
    work_root: Path
    executables: tuple[Path, ...]
    runtime_roots: tuple[Path, ...]
    additional_read_roots: tuple[Path, ...] = ()
    home: Path = Path.home()
    isolation: str = SEATBELT_PARTIAL

    def profile(self) -> str:
        input_root = self.input_root.resolve(strict=True)
        work_root = self.work_root.resolve(strict=True)
        home = self.home.resolve(strict=True)
        runtime_roots = tuple(
            sorted({path.resolve(strict=True) for path in self.runtime_roots}, key=str)
        )
        additional_read_roots = tuple(
            sorted({path.resolve(strict=True) for path in self.additional_read_roots}, key=str)
        )
        executables = tuple(
            sorted({Path(os.path.realpath(path)) for path in self.executables}, key=str)
        )
        if not executables or not runtime_roots:
            raise ValueError("Seatbelt requires a fixed executable and runtime roots")

        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process-info* (target same-sandbox))",
            "(allow signal (target same-sandbox))",
            "(allow sysctl-read)",
            '(allow iokit-open (iokit-registry-entry-class "RootDomainUserClient"))',
            "(allow mach-lookup)",
            "(allow ipc-posix-sem)",
            "(allow ipc-posix-shm-read-data ipc-posix-shm-write-create ipc-posix-shm-write-unlink (ipc-posix-name-regex #\"^/__KMP_REGISTERED_LIB_[0-9]+$\"))",
            '(allow file-write-data (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))',
            "(allow file-read-metadata)",
            # Start with filesystem reads needed by macOS runtimes, then carve
            # the user's home back out before reopening only runtime and input.
            "(allow file-read*)",
            f"(deny file-read* (subpath {_literal(home)}))",
            "(deny file-read* (subpath \"/private/var/folders\"))",
            f"(allow file-read* (subpath {_literal(input_root)}))",
            f"(allow file-read* (subpath {_literal(work_root)}))",
        ]
        rules.extend(
            f"(allow file-read* (subpath {_literal(root)}))"
            for root in (*runtime_roots, *additional_read_roots)
        )
        rules.extend(
            f"(allow process-exec* (literal {_literal(executable)}))"
            for executable in executables
        )
        # Writes are limited to a per-test working directory. In particular,
        # staged source and dataset files live in a separate read-only input tree.
        rules.extend(
            [
                "(allow file-write* (subpath " + _literal(work_root) + "))",
                "(deny network*)",
            ]
        )
        return "\n".join(rules) + "\n"


def python_executable() -> Path:
    """Return the standalone base interpreter, not the backend virtualenv shim."""
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    if not executable.is_file():
        raise FileNotFoundError("Python runtime executable is not a regular file")
    return executable


def runtime_roots(executables: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: set[Path] = set()
    for executable in executables:
        resolved = executable.resolve(strict=True)
        roots.add(resolved.parent)
        roots.add(resolved.parent.parent)
    return tuple(sorted(roots, key=str))


def build_sandbox_argv(policy: SandboxPolicy, command: tuple[str, ...]) -> list[str]:
    """Build a shell-free Seatbelt invocation for one trusted runtime command."""
    if not command or Path(command[0]).resolve(strict=True) not in {
        path.resolve(strict=True) for path in policy.executables
    }:
        raise ValueError("Execution command is outside the Seatbelt executable allowlist")
    return [SANDBOX_EXEC, "-p", policy.profile(), *command]
