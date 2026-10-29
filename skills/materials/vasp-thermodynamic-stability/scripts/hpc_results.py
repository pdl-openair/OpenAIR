#!/usr/bin/env python3
"""安全解压 HPC 结果，或提取带能量口径的 energies.json（仅标准库）。"""
import argparse
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile


def extract(archive, destination):
    """只解压到新目录；拒绝路径穿越、链接和特殊文件，不执行包内脚本。"""
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f"解压目标已存在，拒绝覆盖: {destination}")
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        seen = set()
        for member in members:
            name = member.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            mode = member.external_attr >> 16
            if (not parts or name.startswith("/") or ".." in parts
                    or any(":" in p or p.endswith((".", " ")) for p in parts)
                    or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError(f"不安全的 zip 成员: {member.filename}")
            key = name.rstrip("/").casefold()
            if key in seen:
                raise ValueError(f"重复的 zip 成员: {member.filename}")
            seen.add(key)
        destination.mkdir(parents=True, exist_ok=False)
        # 校验结束后才写出；失败时保留独立目录供诊断，不合并旧结果。
        bundle.extractall(destination)
    return destination


def collect(workdir):
    workdir = Path(workdir)
    info = json.loads((workdir / "submission_info.json").read_text(encoding="utf-8-sig"))
    records = {}
    for phase in info:
        name = phase["dirname"]
        if Path(name).name != name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError(f"无效 phase dirname: {name}")
        result = workdir / "results" / f"{name}_static"
        outcars = sorted(result.rglob("OUTCAR"))
        if not outcars:
            archives = sorted(result.glob("*.zip"))
            if len(archives) == 1:
                extract(archives[0], result / "download")
                outcars = sorted((result / "download").rglob("OUTCAR"))
        if len(outcars) != 1:
            raise ValueError(f"{name}: 需要唯一 OUTCAR，找到 {len(outcars)} 个；不覆盖旧 energies.json")
        energy = natoms = None
        with outcars[0].open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = re.search(r"\bNIONS\s*=\s*(\d+)", line)
                if match:
                    natoms = int(match.group(1))
                match = re.search(r"energy\(sigma->0\)\s*=\s*([-+\d.EeDd]+)", line)
                if match:
                    energy = float(match.group(1).replace("D", "E").replace("d", "e"))
        if energy is None or not math.isfinite(energy) or not natoms:
            raise ValueError(f"{name}: OUTCAR 缺少有限 energy(sigma->0) 或 NIONS")
        records[name] = {
            "energy_eV": energy, "basis": "cell", "natoms": natoms,
            "source": outcars[0].relative_to(workdir).as_posix(),
        }
    if not records:
        raise ValueError("没有相能量，拒绝写出空 energies.json")
    # 原子替换，任何提取错误都不会破坏已有数据。
    fd, temporary = tempfile.mkstemp(prefix="energies-", suffix=".tmp", dir=workdir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, workdir / "energies.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    unpack = sub.add_parser("extract")
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("destination", type=Path)
    energies = sub.add_parser("energies")
    energies.add_argument("workdir", type=Path)
    args = parser.parse_args()
    if args.command == "extract":
        extract(args.archive, args.destination)
    else:
        collect(args.workdir)


if __name__ == "__main__":
    main()
