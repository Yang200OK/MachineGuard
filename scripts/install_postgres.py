"""从官方 Windows 归档安装项目管理的本地 PostgreSQL 服务器。"""

from hashlib import sha256
import csv
import json
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request
from zipfile import ZipFile

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "local"
DOWNLOAD_URL = "https://get.enterprisedb.com/postgresql/postgresql-17.11-4-windows-x64-binaries.zip"


def restrict_credentials(path: Path) -> None:
    """本地凭据只允许当前 Windows 用户、SYSTEM 和管理员访问。"""
    identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
    sid = next(csv.reader([identity.strip()]))[1]
    if not sid.startswith("S-1-"):
        raise RuntimeError("无法识别当前 Windows 用户 SID。")
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)",
                    "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"], check=True, stdout=subprocess.DEVNULL)


def main() -> int:
    """不覆盖现有环境或数据库，密码只写入被 Git 忽略的本地文件。"""
    LOCAL.mkdir(exist_ok=True)
    data_directory = LOCAL / "postgres-data"
    env_path = ROOT / ".env"
    if data_directory.exists() or env_path.exists():
        raise RuntimeError("本地数据目录或 .env 已存在，拒绝覆盖；请检查已有安装。")
    archive_path = LOCAL / "postgresql-17.11-4-windows-x64-binaries.zip"
    if not archive_path.exists():
        temporary = archive_path.with_suffix(".zip.part")
        print("下载官方 PostgreSQL 17.11 Windows 归档。", flush=True)
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=60) as response, temporary.open("wb") as output:
            if response.headers.get_content_type() != "application/zip":
                raise RuntimeError("官方下载未返回 ZIP 归档。")
            total, last_reported = 0, 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                total += len(chunk)
                if total - last_reported >= 25 * 1024 * 1024:
                    print(f"已下载 {total // (1024 * 1024)} MiB。", flush=True)
                    last_reported = total
            expected = response.headers.get("Content-Length")
            if expected is None or total != int(expected):
                raise RuntimeError("归档大小与官方响应不一致。")
        temporary.replace(archive_path)
    binary_root = LOCAL / "pgsql"
    if binary_root.exists():
        raise RuntimeError("PostgreSQL 二进制目录已存在，拒绝覆盖。")
    print("校验归档路径并解压服务器组件。", flush=True)
    with ZipFile(archive_path) as archive:
        members = [item for item in archive.infolist() if item.filename.startswith(
            ("pgsql/bin/", "pgsql/lib/", "pgsql/share/", "pgsql/include/"))]
        if not members:
            raise RuntimeError("归档缺少 PostgreSQL 服务器组件。")
        for item in members:
            target = (LOCAL / item.filename).resolve()
            if not target.is_relative_to(LOCAL.resolve()):
                raise RuntimeError("归档路径超出本地安装目录。")
        archive.extractall(LOCAL, members=members)
    admin_password, migration_password, app_password = [secrets.token_urlsafe(32) for _ in range(3)]
    password_path = LOCAL / "initdb-password.txt"
    password_path.write_text(admin_password + "\n", encoding="utf-8")
    try:
        subprocess.run([str(binary_root / "bin/initdb.exe"), "-D", str(data_directory), "-U", "postgres",
                        "--encoding=UTF8", "--locale=C", "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                        f"--pwfile={password_path}"], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        password_path.unlink(missing_ok=True)
    with (data_directory / "postgresql.conf").open("a", encoding="utf-8") as configuration:
        configuration.write("\n# MachineGuard 本地开发配置，仅监听本机。\nlisten_addresses = '127.0.0.1'\nport = 5432\ntimezone = 'UTC'\n")
    subprocess.run([str(binary_root / "bin/pg_ctl.exe"), "start", "-D", str(data_directory), "-l",
                    str(LOCAL / "postgres.log"), "-w"], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    with psycopg.connect(host="127.0.0.1", port=5432, user="postgres", password=admin_password,
                         dbname="postgres", autocommit=True) as connection:
        for role, password in (("machineguard_migrator", migration_password), ("machineguard_app", app_password)):
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(role), sql.Literal(password)))
        connection.execute("CREATE DATABASE machineguard OWNER machineguard_migrator ENCODING 'UTF8'")
        connection.execute("REVOKE ALL ON DATABASE machineguard FROM PUBLIC")
        connection.execute("GRANT CONNECT ON DATABASE machineguard TO machineguard_app")
    with psycopg.connect(host="127.0.0.1", port=5432, user="postgres", password=admin_password,
                         dbname="machineguard", autocommit=True) as connection:
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE, CREATE ON SCHEMA public TO machineguard_migrator")
        connection.execute("GRANT USAGE ON SCHEMA public TO machineguard_app")
        connection.execute("ALTER DEFAULT PRIVILEGES FOR ROLE machineguard_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO machineguard_app")
        connection.execute("ALTER DEFAULT PRIVILEGES FOR ROLE machineguard_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO machineguard_app")
    env_path.write_text(
        "# 本地凭据，不提交，不在日志或对话中展示。\n"
        f"MG_DATABASE_URL=postgresql+psycopg://machineguard_app:{app_password}@127.0.0.1:5432/machineguard\n"
        f"MG_MIGRATION_DATABASE_URL=postgresql+psycopg://machineguard_migrator:{migration_password}@127.0.0.1:5432/machineguard\n"
        'MG_CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]\n'
        "MG_SESSION_HOURS=8\nMG_GUEST_MINUTES=30\n", encoding="utf-8")
    (LOCAL / "postgres-admin.json").write_text(json.dumps({"host": "127.0.0.1", "port": 5432,
                                                          "username": "postgres", "password": admin_password}, indent=2) + "\n", encoding="utf-8")
    restrict_credentials(env_path)
    restrict_credentials(LOCAL / "postgres-admin.json")
    (LOCAL / "postgres-installation.json").write_text(json.dumps({"source": DOWNLOAD_URL,
        "archive_sha256": sha256(archive_path.read_bytes()).hexdigest(), "version": "17.11",
        "installation_directory": str(binary_root), "data_directory": str(data_directory),
        "host": "127.0.0.1", "port": 5432, "database": "machineguard"}, indent=2) + "\n", encoding="utf-8")
    print("PostgreSQL 已启动：127.0.0.1:5432 / machineguard。")
    print("应用与迁移账号已分离；密码仅保存在本地 .env 和 local 目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
