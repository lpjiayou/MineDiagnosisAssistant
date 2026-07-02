import sqlite3
from typing import Any

from auth_security import (
    hash_password,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)
from database import (
    current_datetime,
    get_connection,
    row_to_dict,
)


# ============================================================
# 认证数据库迁移
# ============================================================

def init_auth_schema() -> None:
    """
    为现有users表增加认证字段。

    重复执行不会删除已有用户或聊天记录。
    """

    with get_connection() as connection:
        existing_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        if "password_hash" not in existing_columns:
            connection.execute(
                """
                ALTER TABLE users
                ADD COLUMN password_hash TEXT
                """
            )

        if (
            "password_changed_at"
            not in existing_columns
        ):
            connection.execute(
                """
                ALTER TABLE users
                ADD COLUMN password_changed_at TEXT
                """
            )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_users_auth_source_key
            ON users (
                auth_source,
                user_key
            )
            """
        )

        connection.commit()


# ============================================================
# 用户查询
# ============================================================

def get_local_user_by_username(
    username: str,
) -> dict[str, Any] | None:
    """
    根据本地用户名查询用户。
    """

    normalized_username = normalize_username(
        username
    )

    if not normalized_username:
        return None

    init_auth_schema()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE user_key = ?
              AND auth_source = 'local'
            """,
            (normalized_username,),
        ).fetchone()

    return row_to_dict(row)


# ============================================================
# 创建普通用户
# ============================================================

def create_local_user(
    username: str,
    display_name: str,
    password: str,
    role: str = "user",
    daily_limit: int = 50,
) -> dict[str, Any]:
    """
    创建本地用户名和密码用户。

    role：
    - user
    - admin
    """

    normalized_username = validate_username(
        username
    )

    normalized_display_name = (
        display_name.strip()
        or normalized_username
    )

    if role not in {
        "user",
        "admin",
    }:
        raise ValueError(
            "用户角色只能是user或admin。"
        )

    if daily_limit < 0:
        raise ValueError(
            "每日调用上限不能小于0。"
        )

    password_hash = hash_password(
        password
    )

    now = current_datetime()

    init_auth_schema()

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    user_key,
                    display_name,
                    auth_source,
                    role,
                    is_active,
                    daily_limit,
                    created_at,
                    last_login_at,
                    password_hash,
                    password_changed_at
                )
                VALUES (
                    ?, ?, 'local', ?, 1, ?, ?, NULL, ?, ?
                )
                """,
                (
                    normalized_username,
                    normalized_display_name,
                    role,
                    daily_limit,
                    now,
                    password_hash,
                    now,
                ),
            )

            user_id = cursor.lastrowid

            row = connection.execute(
                """
                SELECT *
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()

            connection.commit()

    except sqlite3.IntegrityError as error:
        raise ValueError(
            "该用户名已经存在。"
        ) from error

    user = row_to_dict(row)

    if user is None:
        raise RuntimeError(
            "用户创建失败。"
        )

    return user


# ============================================================
# 创建或重置管理员
# ============================================================

def create_or_reset_admin(
    username: str,
    display_name: str,
    password: str,
) -> dict[str, Any]:
    """
    创建初始管理员。

    如果管理员用户名已经存在，则：
    - 重置密码
    - 恢复启用状态
    - 设置为管理员
    - 每日调用次数不限制
    """

    normalized_username = validate_username(
        username
    )

    normalized_display_name = (
        display_name.strip()
        or normalized_username
    )

    password_hash = hash_password(
        password
    )

    now = current_datetime()

    init_auth_schema()

    with get_connection() as connection:
        existing_row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE user_key = ?
              AND auth_source = 'local'
            """,
            (normalized_username,),
        ).fetchone()

        if existing_row is None:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    user_key,
                    display_name,
                    auth_source,
                    role,
                    is_active,
                    daily_limit,
                    created_at,
                    last_login_at,
                    password_hash,
                    password_changed_at
                )
                VALUES (
                    ?, ?, 'local', 'admin',
                    1, 0, ?, NULL, ?, ?
                )
                """,
                (
                    normalized_username,
                    normalized_display_name,
                    now,
                    password_hash,
                    now,
                ),
            )

            user_id = cursor.lastrowid

        else:
            user_id = int(
                existing_row["id"]
            )

            connection.execute(
                """
                UPDATE users
                SET
                    display_name = ?,
                    role = 'admin',
                    is_active = 1,
                    daily_limit = 0,
                    password_hash = ?,
                    password_changed_at = ?
                WHERE id = ?
                """,
                (
                    normalized_display_name,
                    password_hash,
                    now,
                    user_id,
                ),
            )

        row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        connection.commit()

    user = row_to_dict(row)

    if user is None:
        raise RuntimeError(
            "管理员创建或更新失败。"
        )

    return user


# ============================================================
# 用户登录认证
# ============================================================

def authenticate_local_user(
    username: str,
    password: str,
) -> dict[str, Any] | None:
    """
    验证本地用户名和密码。

    返回：
    - 登录成功：用户字典
    - 用户名或密码错误：None
    - 用户被禁用：抛出PermissionError
    """

    normalized_username = normalize_username(
        username
    )

    if not normalized_username or not password:
        return None

    init_auth_schema()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE user_key = ?
              AND auth_source = 'local'
            """,
            (normalized_username,),
        ).fetchone()

        if row is None:
            return None

        user = dict(row)

        if int(user["is_active"]) != 1:
            raise PermissionError(
                "该用户已经被管理员禁用。"
            )

        stored_hash = user.get(
            "password_hash"
        )

        if not stored_hash:
            return None

        if not verify_password(
            password,
            stored_hash,
        ):
            return None

        now = current_datetime()

        connection.execute(
            """
            UPDATE users
            SET last_login_at = ?
            WHERE id = ?
            """,
            (
                now,
                user["id"],
            ),
        )

        updated_row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (user["id"],),
        ).fetchone()

        connection.commit()

    return row_to_dict(updated_row)


# ============================================================
# 修改用户密码
# ============================================================

def change_local_password(
    user_id: int,
    current_password: str,
    new_password: str,
) -> bool:
    """
    用户验证旧密码后修改新密码。
    """

    validate_password(
        new_password
    )

    init_auth_schema()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT password_hash
            FROM users
            WHERE id = ?
              AND auth_source = 'local'
            """,
            (user_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                "用户不存在。"
            )

        current_hash = row[
            "password_hash"
        ]

        if not current_hash:
            raise ValueError(
                "当前用户没有设置本地密码。"
            )

        if not verify_password(
            current_password,
            current_hash,
        ):
            return False

        new_password_hash = hash_password(
            new_password
        )

        now = current_datetime()

        cursor = connection.execute(
            """
            UPDATE users
            SET
                password_hash = ?,
                password_changed_at = ?
            WHERE id = ?
            """,
            (
                new_password_hash,
                now,
                user_id,
            ),
        )

        connection.commit()

    return cursor.rowcount > 0