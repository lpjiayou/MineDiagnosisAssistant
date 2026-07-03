import os
from datetime import datetime, timedelta
from typing import Any

from database import (
    current_date,
    current_datetime,
    get_connection,
)


# ============================================================
# 环境变量
# ============================================================

def get_int_env(
    name: str,
    default: int,
) -> int:
    """
    安全读取整数环境变量。
    """

    value = os.getenv(name, "").strip()

    if not value:
        return default

    try:
        return int(value)

    except ValueError:
        return default


PENDING_TIMEOUT_MINUTES = max(
    1,
    get_int_env(
        "AI_REQUEST_PENDING_TIMEOUT_MINUTES",
        15,
    ),
)


# ============================================================
# 初始化调用记录表
# ============================================================

def init_usage_schema() -> None:
    """
    创建AI请求明细表。

    daily_usage：
        保存已经成功完成的AI调用次数。

    ai_requests：
        保存每次请求的处理中、成功和失败状态。
    """

    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_requests (
                request_id TEXT PRIMARY KEY,

                user_id INTEGER NOT NULL,

                conversation_id TEXT,

                usage_date TEXT NOT NULL,

                status TEXT NOT NULL
                    CHECK (
                        status IN (
                            'pending',
                            'success',
                            'failed'
                        )
                    ),

                error_type TEXT,

                created_at TEXT NOT NULL,

                completed_at TEXT,

                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE SET NULL
            );


            CREATE INDEX IF NOT EXISTS
                idx_ai_requests_user_date_status
            ON ai_requests (
                user_id,
                usage_date,
                status
            );


            CREATE INDEX IF NOT EXISTS
                idx_ai_requests_created_at
            ON ai_requests (
                created_at
            );
            """
        )

        connection.commit()


# ============================================================
# 清理超时请求
# ============================================================

def cleanup_stale_pending_requests(
    user_id: int | None = None,
) -> int:
    """
    将长时间未完成的pending请求标记为failed。

    防止程序异常关闭后，处理中请求永久占用用户额度。
    """

    init_usage_schema()

    threshold = (
        datetime.now().astimezone()
        - timedelta(
            minutes=PENDING_TIMEOUT_MINUTES
        )
    ).isoformat(
        timespec="seconds"
    )

    now = current_datetime()

    with get_connection() as connection:
        if user_id is None:
            cursor = connection.execute(
                """
                UPDATE ai_requests
                SET
                    status = 'failed',
                    error_type = 'PendingTimeout',
                    completed_at = ?
                WHERE status = 'pending'
                  AND created_at < ?
                """,
                (
                    now,
                    threshold,
                ),
            )

        else:
            cursor = connection.execute(
                """
                UPDATE ai_requests
                SET
                    status = 'failed',
                    error_type = 'PendingTimeout',
                    completed_at = ?
                WHERE status = 'pending'
                  AND user_id = ?
                  AND created_at < ?
                """,
                (
                    now,
                    user_id,
                    threshold,
                ),
            )

        connection.commit()

        return cursor.rowcount


# ============================================================
# 查询使用状态
# ============================================================

def get_usage_status(
    user_id: int,
) -> dict[str, Any]:
    """
    查询用户当天调用状态。

    used：
        已成功完成的调用次数。

    pending：
        当前正在处理、暂时占用额度的请求数。

    remaining：
        扣除成功和处理中请求后的剩余次数。
    """

    init_usage_schema()

    cleanup_stale_pending_requests(
        user_id=user_id
    )

    today = current_date()

    with get_connection() as connection:
        user_row = connection.execute(
            """
            SELECT
                id,
                role,
                is_active,
                daily_limit
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user_row is None:
            raise ValueError(
                "用户不存在。"
            )

        if int(user_row["is_active"]) != 1:
            raise PermissionError(
                "当前用户已经被禁用。"
            )

        usage_row = connection.execute(
            """
            SELECT request_count
            FROM daily_usage
            WHERE user_id = ?
              AND usage_date = ?
            """,
            (
                user_id,
                today,
            ),
        ).fetchone()

        pending_row = connection.execute(
            """
            SELECT COUNT(*) AS pending_count
            FROM ai_requests
            WHERE user_id = ?
              AND usage_date = ?
              AND status = 'pending'
            """,
            (
                user_id,
                today,
            ),
        ).fetchone()

    used = (
        int(usage_row["request_count"])
        if usage_row is not None
        else 0
    )

    pending = int(
        pending_row["pending_count"]
    )

    daily_limit = int(
        user_row["daily_limit"]
    )

    is_admin = (
        user_row["role"] == "admin"
    )

    unlimited = (
        is_admin
        or daily_limit == 0
    )

    occupied = used + pending

    if unlimited:
        remaining = None
        allowed = True

    else:
        remaining = max(
            daily_limit - occupied,
            0,
        )

        allowed = (
            occupied < daily_limit
        )

    return {
        "allowed": allowed,
        "used": used,
        "pending": pending,
        "occupied": occupied,
        "daily_limit": daily_limit,
        "remaining": remaining,
        "is_admin": is_admin,
        "unlimited": unlimited,
    }


# ============================================================
# 原子预占调用额度
# ============================================================

def reserve_ai_request(
    request_id: str,
    user_id: int,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    在调用DeepSeek前，原子预占一个调用名额。

    使用BEGIN IMMEDIATE可以防止多个浏览器窗口
    同时读取到相同的剩余额度并突破限制。
    """

    normalized_request_id = (
        request_id.strip()
    )

    if not normalized_request_id:
        raise ValueError(
            "request_id不能为空。"
        )

    init_usage_schema()

    cleanup_stale_pending_requests(
        user_id=user_id
    )

    today = current_date()
    now = current_datetime()

    with get_connection() as connection:
        # 获取SQLite写锁，保证检查和插入是一个原子操作。
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        existing_row = connection.execute(
            """
            SELECT *
            FROM ai_requests
            WHERE request_id = ?
            """,
            (normalized_request_id,),
        ).fetchone()

        if existing_row is not None:
            existing_status = (
                existing_row["status"]
            )

            return {
                "allowed": (
                    existing_status
                    in {
                        "pending",
                        "success",
                    }
                ),
                "reserved": False,
                "existing": True,
                "request_id": (
                    normalized_request_id
                ),
                "status": existing_status,
            }

        user_row = connection.execute(
            """
            SELECT
                id,
                role,
                is_active,
                daily_limit
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user_row is None:
            raise ValueError(
                "用户不存在。"
            )

        if int(user_row["is_active"]) != 1:
            raise PermissionError(
                "当前用户已经被禁用。"
            )

        if conversation_id is not None:
            conversation_row = (
                connection.execute(
                    """
                    SELECT id
                    FROM conversations
                    WHERE id = ?
                      AND user_id = ?
                    """,
                    (
                        conversation_id,
                        user_id,
                    ),
                ).fetchone()
            )

            if conversation_row is None:
                raise PermissionError(
                    "当前用户无权使用该会话。"
                )

        usage_row = connection.execute(
            """
            SELECT request_count
            FROM daily_usage
            WHERE user_id = ?
              AND usage_date = ?
            """,
            (
                user_id,
                today,
            ),
        ).fetchone()

        pending_row = connection.execute(
            """
            SELECT COUNT(*) AS pending_count
            FROM ai_requests
            WHERE user_id = ?
              AND usage_date = ?
              AND status = 'pending'
            """,
            (
                user_id,
                today,
            ),
        ).fetchone()

        used = (
            int(usage_row["request_count"])
            if usage_row is not None
            else 0
        )

        pending = int(
            pending_row["pending_count"]
        )

        daily_limit = int(
            user_row["daily_limit"]
        )

        is_admin = (
            user_row["role"] == "admin"
        )

        unlimited = (
            is_admin
            or daily_limit == 0
        )

        occupied = used + pending

        if (
            not unlimited
            and occupied >= daily_limit
        ):
            return {
                "allowed": False,
                "reserved": False,
                "existing": False,
                "request_id": (
                    normalized_request_id
                ),
                "status": "rejected",
                "used": used,
                "pending": pending,
                "daily_limit": daily_limit,
                "remaining": 0,
                "is_admin": is_admin,
            }

        connection.execute(
            """
            INSERT INTO ai_requests (
                request_id,
                user_id,
                conversation_id,
                usage_date,
                status,
                error_type,
                created_at,
                completed_at
            )
            VALUES (
                ?, ?, ?, ?, 'pending',
                NULL, ?, NULL
            )
            """,
            (
                normalized_request_id,
                user_id,
                conversation_id,
                today,
                now,
            ),
        )

        pending_after = pending + 1

        if unlimited:
            remaining = None

        else:
            remaining = max(
                daily_limit
                - used
                - pending_after,
                0,
            )

        connection.commit()

    return {
        "allowed": True,
        "reserved": True,
        "existing": False,
        "request_id": normalized_request_id,
        "status": "pending",
        "used": used,
        "pending": pending_after,
        "daily_limit": daily_limit,
        "remaining": remaining,
        "is_admin": is_admin,
    }


# ============================================================
# 完成或释放调用额度
# ============================================================

def finalize_ai_request(
    request_id: str,
    success: bool,
    error_type: str | None = None,
) -> dict[str, Any]:
    """
    完成AI请求。

    success=True：
        pending变为success，并将daily_usage增加1。

    success=False：
        pending变为failed，不增加daily_usage。

    同一request_id重复调用不会重复计数。
    """

    normalized_request_id = (
        request_id.strip()
    )

    if not normalized_request_id:
        raise ValueError(
            "request_id不能为空。"
        )

    init_usage_schema()

    now = current_datetime()

    with get_connection() as connection:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        request_row = connection.execute(
            """
            SELECT *
            FROM ai_requests
            WHERE request_id = ?
            """,
            (normalized_request_id,),
        ).fetchone()

        if request_row is None:
            raise ValueError(
                "AI请求记录不存在。"
            )

        current_status = (
            request_row["status"]
        )

        # 已经完成时直接返回，不重复计数。
        if current_status != "pending":
            return {
                "request_id": (
                    normalized_request_id
                ),
                "status": current_status,
                "counted": False,
                "already_finalized": True,
            }

        new_status = (
            "success"
            if success
            else "failed"
        )

        connection.execute(
            """
            UPDATE ai_requests
            SET
                status = ?,
                error_type = ?,
                completed_at = ?
            WHERE request_id = ?
              AND status = 'pending'
            """,
            (
                new_status,
                None if success else error_type,
                now,
                normalized_request_id,
            ),
        )

        counted = False

        if success:
            connection.execute(
                """
                INSERT INTO daily_usage (
                    user_id,
                    usage_date,
                    request_count,
                    updated_at
                )
                VALUES (?, ?, 1, ?)

                ON CONFLICT(user_id, usage_date)
                DO UPDATE SET
                    request_count =
                        daily_usage.request_count + 1,

                    updated_at =
                        excluded.updated_at
                """,
                (
                    request_row["user_id"],
                    request_row["usage_date"],
                    now,
                ),
            )

            counted = True

        connection.commit()

    return {
        "request_id": normalized_request_id,
        "status": new_status,
        "counted": counted,
        "already_finalized": False,
    }
