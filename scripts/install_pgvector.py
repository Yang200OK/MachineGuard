"""为项目管理的 Windows PostgreSQL 17 安装固定版本 pgvector。"""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

import httpx
import psycopg


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "local"
DOWNLOADS = [
    ("https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip", "zig-0.13.0.zip",
     "d859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158"),
    ("https://codeload.github.com/pgvector/pgvector/zip/refs/tags/v0.8.6", "pgvector-0.8.6.zip",
     "e93a1567219c9ce523ca16473f6c41cc80e01345b2d91ccdee40b473b7c5dd0a"),
    ("https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/port/strtof.c", "postgres-strtof-17.11.c",
     "ecfc717296d7358f60ca5c3861b866e7f517de04b21d071b3260045f0f83296f"),
]


def verified_download(url, name, expected):
    path = LOCAL / name
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".part")
        with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for block in response.iter_bytes():
                    output.write(block)
        if sha256(temporary.read_bytes()).hexdigest() != expected:
            raise RuntimeError("下载文件校验失败，未解压或运行。")
        temporary.replace(path)
    if sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("本地归档与固定官方来源指纹不一致。")
    return path


def main():
    installation = json.loads((LOCAL / "postgres-installation.json").read_text(encoding="utf-8"))
    pg = (LOCAL / "pgsql").resolve()
    if installation["version"] != "17.11" or Path(installation["installation_directory"]).resolve() != pg:
        raise RuntimeError("本安装入口仅适用于项目管理的 PostgreSQL 17.11，不操作其它数据库。")
    admin = json.loads((LOCAL / "postgres-admin.json").read_text(encoding="utf-8"))
    connection = dict(host=admin["host"], port=admin["port"], user=admin["username"],
                      password=admin["password"], dbname="machineguard", autocommit=True)
    with psycopg.connect(**connection) as database:
        version = database.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        if version:
            if version[0] != "0.8.6":
                raise RuntimeError("已有其它 pgvector 版本，拒绝覆盖。")
            record = json.loads((LOCAL / "pgvector-installation.json").read_text(encoding="utf-8"))
            if sha256((pg / "lib/vector.dll").read_bytes()).hexdigest() != record["dll_sha256"]:
                raise RuntimeError("现有扩展文件哈希与安装记录不一致。")
            print("pgvector 0.8.6 已启用，DLL 哈希一致；未覆盖文件。")
            return 0
    if (pg / "lib/vector.dll").exists() or (pg / "share/extension/vector.control").exists():
        raise RuntimeError("已有扩展文件但数据库未启用；请人工核对，不自动覆盖。")
    for url, name, expected in DOWNLOADS:
        archive = verified_download(url, name, expected)
        if archive.suffix == ".zip":
            with ZipFile(archive) as zipped:
                for member in zipped.infolist():
                    if not (LOCAL / member.filename).resolve().is_relative_to(LOCAL.resolve()):
                        raise RuntimeError("归档包含越界路径。")
                zipped.extractall(LOCAL)
    source = LOCAL / "pgvector-0.8.6"
    compiler = LOCAL / "zig-windows-x86_64-0.13.0/zig.exe"
    # 使用官方源码支持的便携指令分支，大地址模型避免 Windows 窄重定位。
    command = [str(compiler), "cc", "-target", "x86_64-windows-gnu", "-shared", "-O2", "-DWIN32",
        "-DDISABLE_DISPATCH", "-mcmodel=large", "-Wno-ignored-attributes",
        "-I" + str(pg / "include/server/port/win32"), "-I" + str(pg / "include/server"), "-I" + str(pg / "include")]
    command.extend(str(path) for path in sorted((source / "src").glob("*.c")))
    # PostgreSQL 官方 MinGW 浮点解析兼容实现，保持与该版本端口契约一致。
    command.extend([str(LOCAL / "postgres-strtof-17.11.c"), str(pg / "lib/postgres.lib"), "-o", str(source / "vector.dll")])
    environment = os.environ.copy()
    environment["ZIG_GLOBAL_CACHE_DIR"] = str(LOCAL / "zig-cache")
    environment["ZIG_LOCAL_CACHE_DIR"] = str(LOCAL / "zig-local-cache")
    result = subprocess.run(command, capture_output=True, text=True, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    (LOCAL / "pgvector-build.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode or result.stderr.strip():
        raise RuntimeError("pgvector 构建失败或有未处理的构建警告，请读取 local/pgvector-build.log。")
    shutil.copy2(source / "vector.dll", pg / "lib/vector.dll")
    shutil.copy2(source / "vector.control", pg / "share/extension/vector.control")
    for script in (source / "sql").glob("vector--*.sql"):
        shutil.copy2(script, pg / "share/extension" / script.name)
    shutil.copy2(source / "sql/vector.sql", pg / "share/extension/vector--0.8.6.sql")
    with psycopg.connect(**connection) as database:
        database.execute("CREATE EXTENSION vector")
        distance = database.execute("SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector").fetchone()[0]
        if distance != 1:
            raise RuntimeError("扩展余弦距离校验失败。")
    record = {"version": "0.8.6", "source": "https://github.com/pgvector/pgvector/tree/v0.8.6",
        "source_archive_sha256": DOWNLOADS[1][2], "compiler": "Zig 0.13.0 x86_64-windows-gnu",
        "options": ["DISABLE_DISPATCH", "mcmodel=large"], "postgres_port_source": DOWNLOADS[2][0],
        "dll_sha256": sha256((pg / "lib/vector.dll").read_bytes()).hexdigest()}
    (LOCAL / "pgvector-installation.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print("pgvector 0.8.6 已安装并启用，余弦距离校验通过；未修改原有业务表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
