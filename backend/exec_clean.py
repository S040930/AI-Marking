#!/usr/bin/env python3
"""执行全局环境清理:递归删除项目依赖闭包内的孤儿包。
策略:多轮扫描,每轮删除"Required-by 为空(或所有引用者已标记删除)"的包,
直到没有新的孤儿产生。删除顺序:先删上层依赖者,再删下层被依赖者。
"""
import subprocess
import sys

GLOBAL_PIP = "/opt/miniconda3/envs/cuda/bin/pip"
PROJECT_PKGS_FILE = "/tmp/venv_pkgs.txt"
NEVER_DELETE = {"pip", "setuptools", "wheel"}


def get_required_by(pkg: str) -> list[str]:
    try:
        out = subprocess.run(
            [GLOBAL_PIP, "show", pkg],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        return []
    for line in out.stdout.splitlines():
        if line.startswith("Required-by:"):
            val = line.split(":", 1)[1].strip()
            if not val:
                return []
            return [x.strip() for x in val.split(",") if x.strip()]
    return []


def uninstall(pkg: str) -> bool:
    r = subprocess.run(
        [GLOBAL_PIP, "uninstall", "-y", pkg],
        capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def main() -> None:
    with open(PROJECT_PKGS_FILE) as f:
        project_pkgs = [line.strip() for line in f if line.strip()]

    deleted: list[str] = []
    failed: list[str] = []
    skipped = set(NEVER_DELETE)

    # 多轮扫描,每轮找出当前可删的孤儿包并立即删除
    round_num = 0
    while True:
        round_num += 1
        changed = False
        for pkg in project_pkgs:
            if pkg in deleted or pkg in failed or pkg in skipped:
                continue
            reqs = get_required_by(pkg)
            external = [r for r in reqs if r not in deleted and r not in failed]
            if not external:
                if uninstall(pkg):
                    deleted.append(pkg)
                    changed = True
                    print(f"  [Round {round_num}] deleted: {pkg}")
                else:
                    failed.append(pkg)
                    print(f"  [Round {round_num}] FAILED:  {pkg}")
        if not changed:
            break

    print(f"\n=== 清理完成 ===")
    print(f"成功删除: {len(deleted)} 个")
    print(f"删除失败: {len(failed)} 个")
    if failed:
        print(f"失败列表: {failed}")


if __name__ == "__main__":
    main()
