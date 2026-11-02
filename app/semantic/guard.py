"""SQL 安全校验：本地 AST 白名单，不依赖 LLM 自觉。

规则（与《产品需求文档与技术方案》一致）：
1. 只允许单条 SELECT（含 CTE），禁止任何写操作 / DDL / 多语句；
2. FROM 引用的表必须在白名单内；
3. 引用的列必须在 schema 白名单内（或为本查询内定义的别名）；
4. 禁止 JOIN（MVP 场景用不到，直接禁掉以缩小攻击面）。
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"sales"}
ALLOWED_COLUMNS = {
    "order_year",
    "order_month",
    "platform_name",
    "country_name",
    "cate_lvl3_cn",
    "trd_amt_eur",
    "trd_prod_qty",
    "period",
    "month_start",
}

FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Pragma, exp.Attach, exp.Detach, exp.Copy, exp.Export,
)


class SQLRejected(Exception):
    pass


def validate(sql: str) -> str:
    """通过则返回规范化 SQL，否则抛 SQLRejected（消息直接给用户看）。"""
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        raise SQLRejected("SQL 为空")
    if ";" in sql:
        raise SQLRejected("不支持多语句 SQL")

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as e:  # noqa: BLE001
        raise SQLRejected(f"SQL 语法无法解析：{e}") from e
    if len(statements) != 1 or statements[0] is None:
        raise SQLRejected("只允许一条查询语句")

    stmt = statements[0]
    if isinstance(stmt, FORBIDDEN):
        raise SQLRejected("只允许 SELECT 查询，禁止写操作")
    if not isinstance(stmt, (exp.Select, exp.Union)):
        raise SQLRejected(f"不支持该语句类型：{type(stmt).__name__}")

    # 全树禁止写操作（覆盖子查询）
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN):
            raise SQLRejected("子查询中包含不允许的语句")

    # 禁止 JOIN
    if any(isinstance(n, exp.Join) for n in stmt.walk()):
        raise SQLRejected("当前不支持 JOIN，请改用单个表的聚合/窗口查询")

    # 查询内定义的别名（列别名 / CTE 名）
    defined = set()
    for alias in stmt.find_all(exp.Alias):
        defined.add(alias.alias.lower())
    cte_names = set()
    for cte in stmt.find_all(exp.CTE):
        cte_names.add(cte.alias.lower())
    defined |= cte_names

    # 表白名单（CTE 名视作本查询内的临时表）
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name in ALLOWED_TABLES or name in cte_names:
            continue
        raise SQLRejected(f"表 `{name}` 不在白名单内，只允许：{sorted(ALLOWED_TABLES)}")

    for col in stmt.find_all(exp.Column):
        name = col.name.lower()
        if name in ALLOWED_COLUMNS or name in defined:
            continue
        raise SQLRejected(f"列 `{name}` 不在白名单内")

    return stmt.sql(dialect="duckdb")
