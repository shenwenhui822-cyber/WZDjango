"""RAR 解压（持仓对账单等）：依赖系统 unrar/7z/bsdtar/unar 之一。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def extract_rar_archive(rar_path: Path, output_dir: Path) -> None:
    """将 rar_path 解压到 output_dir（目录须已存在或可创建）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    commands: list[list[str]] = []
    if shutil.which("unrar"):
        commands.append(["unrar", "x", "-o+", str(rar_path), str(output_dir)])
    if shutil.which("7z"):
        commands.append(["7z", "x", "-y", f"-o{output_dir}", str(rar_path)])
    if shutil.which("7za"):
        commands.append(["7za", "x", "-y", f"-o{output_dir}", str(rar_path)])
    if shutil.which("bsdtar"):
        commands.append(["bsdtar", "-xf", str(rar_path), "-C", str(output_dir)])
    if shutil.which("unar"):
        commands.append(["unar", "-o", str(output_dir), str(rar_path)])
    if not commands:
        raise RuntimeError("未找到可用解压命令（unrar/7z/7za/bsdtar/unar），无法解压 RAR。")

    last_err = ""
    for cmd in commands:
        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            return
        except subprocess.CalledProcessError as exc:
            msg = (exc.stderr or b"").decode("utf-8", errors="ignore")
            last_err = f"{' '.join(cmd[:3])} 失败: {msg or exc}"
    raise RuntimeError(last_err or "RAR 解压失败。")
