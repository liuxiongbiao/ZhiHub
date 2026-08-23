"""
backend/tests/integration/test_alembic_from_scratch.py

职责: 验证 alembic upgrade head 从全新空库跑通且产出与模型一致的 schema(无缺表/缺列)

设计:
  - 用 MySQL root 建临时库 zhihub_alembic_test,subprocess 跑 alembic upgrade head
    (经 env.py 读 MYSQL_DATABASE env var 指向临时库)
  - inspect 比对 Base.metadata 全部表 + 全部列存在(直接验证"从零建库不缺列")
  - teardown 删临时库
  - 无 MYSQL_ROOT_PASSWORD env var 时 skip(集成测试,需 MySQL root 建库)

为什么用 root: 应用账号 zhihub 无 CREATE DATABASE 权限(docker-compose 仅授权 zhihub 库)。
为什么 subprocess 跑 alembic: env.py 在 import 时读 settings 单例,subprocess 隔离 env 注入,
  通过 MYSQL_DATABASE env var 让子进程 settings 指向临时库,比 in-process 调 command 更干净。
为什么 inspect 比对而非 compare_metadata: compare_metadata 会报 server_default/类型变体等噪声,
  本测试只关心"缺表/缺列"(SC-002),用 has_table + get_columns 精确断言。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 无 root 密码则 skip(与项目其他集成测试一致的"端到端阶段验证"模式)
pytestmark = pytest.mark.skipif(
    not os.environ.get("MYSQL_ROOT_PASSWORD"),
    reason="需 MYSQL_ROOT_PASSWORD 建临时库,集成测试阶段验证",
)


def _root_engine():
    """用 root 连 MySQL server(连 mysql 系统库),用于建/删临时库与授权。"""
    from app.config import settings

    root_pw = os.environ["MYSQL_ROOT_PASSWORD"]
    url = f"mysql+pymysql://root:{root_pw}@{settings.mysql_host}:{settings.mysql_port}/mysql"
    return create_engine(url)


@pytest.fixture()
def fresh_db():
    """建全新临时库,授权给 zhihub 用户,yield 库名,teardown 删除。

    库名固定 zhihub_alembic_test: 若上次测试崩溃残留,setup 的 DROP IF EXISTS 兜底清理。
    """
    from app.config import settings

    db_name = "zhihub_alembic_test"
    root = _root_engine()
    with root.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        c.execute(text(f"CREATE DATABASE {db_name} DEFAULT CHARACTER SET utf8mb4"))
        c.execute(
            text(f"GRANT ALL PRIVILEGES ON {db_name}.* TO '{settings.mysql_user}'@'%'")
        )
        c.execute(text("FLUSH PRIVILEGES"))
        c.commit()
    try:
        yield db_name
    finally:
        with root.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            c.commit()


def test_from_scratch_upgrade_creates_all_model_tables_and_columns(fresh_db):
    """从零 alembic upgrade head 后,Base.metadata 全部表与列都在 DB 中存在(SC-001/SC-002)。"""
    from app.config import settings
    from app.models import Base  # 触发 __init__.py 注册全部模型到 metadata

    db_name = fresh_db
    # subprocess 跑 alembic upgrade head,MYSQL_DATABASE env var 让 env.py 指向临时库
    env = {**os.environ, "MYSQL_DATABASE": db_name}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head 失败(returncode={result.returncode}):\n"
        f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )

    # inspect 临时库 schema,逐表逐列比对
    url = (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{db_name}"
    )
    engine = create_engine(url)
    insp = inspect(engine)

    missing_tables: list[str] = []
    missing_cols: list[tuple[str, list[str]]] = []
    for table_name, table in Base.metadata.tables.items():
        if not insp.has_table(table_name):
            missing_tables.append(table_name)
            continue
        db_cols = {c["name"] for c in insp.get_columns(table_name)}
        model_cols = {c.name for c in table.columns}
        miss = sorted(model_cols - db_cols)
        if miss:
            missing_cols.append((table_name, miss))

    assert not missing_tables, f"从零 upgrade 后缺表: {missing_tables}"
    assert not missing_cols, f"从零 upgrade 后缺列: {missing_cols}"


def test_downgrade_d11_is_reversible(fresh_db):
    """upgrade head 后 downgrade -1 能回退 d11:7 张新表消失,既有表(tenants/users)仍在(SC-004)。"""
    from app.config import settings

    db_name = fresh_db
    env = {**os.environ, "MYSQL_DATABASE": db_name}
    # 先 upgrade head
    up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
    )
    assert up.returncode == 0, f"upgrade failed:\n{up.stderr}"
    # 再 downgrade 到 d11 的上一个 revision（merge 36e0613c0a65，验证 d11 可逆）
    # 不用 "downgrade -N" 硬编码：链尾会随新迁移增长（如 d17），-2 会停在 d11 之上导致断言失败
    down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "36e0613c0a65"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
    )
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}\n{down.stdout}"

    url = (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{db_name}"
    )
    engine = create_engine(url)
    insp = inspect(engine)

    # d11 建的 7 表应消失
    d11_tables = [
        "quick_replies", "refund_approvals", "billing_invoices",
        "csat_evaluations", "usage_records", "answer_feedback", "security_incidents",
    ]
    for t in d11_tables:
        assert not insp.has_table(t), f"downgrade 后 {t} 应被删除"
    # d11 给 tenants 加的 4 列应消失
    tenant_cols = {c["name"] for c in insp.get_columns("tenants")}
    for col in ["widget_theme_color", "widget_logo_url", "widget_welcome_text", "default_model"]:
        assert col not in tenant_cols, f"downgrade 后 tenants.{col} 应被删除"
    # 既有表/列仍在
    assert insp.has_table("tenants"), "tenants 表不应被 downgrade -1 删除"
    assert insp.has_table("users"), "users 表不应被 downgrade -1 删除"

