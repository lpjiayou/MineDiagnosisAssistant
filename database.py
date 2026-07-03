import json
import os
import sqlite3
import uuid

from datetime import datetime
from pathlib import Path
from typing import Any

class AutoClosingConnection(sqlite3.Connection):
    """
    SQLite连接子类。

    使用方式：

        with get_connection() as connection:
            ...

    退出with代码块时：
    1. 正常则提交事务；
    2. 异常则回滚事务；
    3. 无论成功或失败，最终都会关闭数据库连接。
    """

    def __exit__(
        self,
        exception_type,
        exception_value,
        traceback,
    ) -> bool:
        try:
            result = super().__exit__(
                exception_type,
                exception_value,
                traceback,
            )

            return bool(result)

        finally:
            self.close()
# ============================================================
# 数据库路径
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_DB_PATH = (
    PROJECT_DIR
    / "data"
    / "mine_diagnosis_assistant.db"
)

DB_PATH = Path(
    os.getenv(
        "APP_DB_PATH",
        str(DEFAULT_DB_PATH),
    )
)


# ============================================================
# 基础工具函数
# ============================================================

def current_datetime() -> str:
    """
    返回带本机时区的当前时间。
    """

    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def current_date() -> str:
    """
    返回本机当前日期。
    """

    return datetime.now().astimezone().date().isoformat()


def row_to_dict(
    row: sqlite3.Row | None,
) -> dict[str, Any] | None:
    """
    将SQLite Row转换成普通字典。
    """

    if row is None:
        return None

    return dict(row)


def get_connection() -> sqlite3.Connection:
    """
    创建数据库连接。

    每次操作使用独立连接，适合Streamlit重复执行脚本的模式。
    """

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
    DB_PATH,
    timeout=10,
    factory=AutoClosingConnection,
)

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    connection.execute(
        "PRAGMA journal_mode = WAL"
    )

    connection.execute(
        "PRAGMA synchronous = NORMAL"
    )

    return connection


# ============================================================
# 初始化数据库
# ============================================================

def init_database() -> None:
    """
    初始化数据库表。

    重复运行不会删除已有数据。
    """

    now = current_datetime()

    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_key TEXT NOT NULL UNIQUE,

                display_name TEXT NOT NULL,

                auth_source TEXT NOT NULL DEFAULT 'local',

                role TEXT NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'admin')),

                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),

                daily_limit INTEGER NOT NULL DEFAULT 50,

                created_at TEXT NOT NULL,

                last_login_at TEXT
            );


            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,

                user_id INTEGER NOT NULL,

                title TEXT NOT NULL,

                work_mode TEXT NOT NULL,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                conversation_id TEXT NOT NULL,

                role TEXT NOT NULL
                    CHECK (
                        role IN (
                            'system',
                            'user',
                            'assistant'
                        )
                    ),

                content TEXT NOT NULL,

                meta_json TEXT,

                created_at TEXT NOT NULL,

                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                usage_date TEXT NOT NULL,

                request_count INTEGER NOT NULL DEFAULT 0,

                updated_at TEXT NOT NULL,

                UNIQUE (user_id, usage_date),

                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            );


            CREATE INDEX IF NOT EXISTS
                idx_conversations_user_updated
            ON conversations (
                user_id,
                updated_at DESC
            );


            CREATE INDEX IF NOT EXISTS
                idx_messages_conversation
            ON messages (
                conversation_id,
                id
            );


            CREATE INDEX IF NOT EXISTS
                idx_daily_usage_user_date
            ON daily_usage (
                user_id,
                usage_date
            );
            """
        )

        # 数据库初始化记录。
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                metadata_key TEXT PRIMARY KEY,
                metadata_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            INSERT INTO app_metadata (
                metadata_key,
                metadata_value,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(metadata_key)
            DO UPDATE SET
                metadata_value = excluded.metadata_value,
                updated_at = excluded.updated_at
            """,
            (
                "database_version",
                "2.0.0",
                now,
            ),
        )

        connection.commit()


# ============================================================
# 用户管理
# ============================================================

def get_or_create_user(
    user_key: str,
    display_name: str,
    auth_source: str = "local",
    role: str = "user",
    daily_limit: int = 50,
) -> dict[str, Any]:
    """
    获取或创建用户。

    user_key后续可以使用：
    - 本地用户名
    - 微信OpenID
    - 企业账号ID
    """

    normalized_key = user_key.strip()
    normalized_name = display_name.strip()

    if not normalized_key:
        raise ValueError("user_key不能为空。")

    if not normalized_name:
        normalized_name = normalized_key

    if role not in {"user", "admin"}:
        raise ValueError(
            "role只能是user或admin。"
        )

    if daily_limit < 0:
        raise ValueError(
            "daily_limit不能小于0。"
        )

    now = current_datetime()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (
                user_key,
                display_name,
                auth_source,
                role,
                is_active,
                daily_limit,
                created_at,
                last_login_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_key)
            DO UPDATE SET
                display_name = excluded.display_name,
                last_login_at = excluded.last_login_at
            """,
            (
                normalized_key,
                normalized_name,
                auth_source,
                role,
                daily_limit,
                now,
                now,
            ),
        )

        row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE user_key = ?
            """,
            (normalized_key,),
        ).fetchone()

        connection.commit()

    user = row_to_dict(row)

    if user is None:
        raise RuntimeError(
            "用户创建或读取失败。"
        )

    return user


def get_user_by_id(
    user_id: int,
) -> dict[str, Any] | None:
    """
    根据用户ID查询用户。
    """

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    return row_to_dict(row)


def get_user_by_key(
    user_key: str,
) -> dict[str, Any] | None:
    """
    根据用户唯一标识查询用户。
    """

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM users
            WHERE user_key = ?
            """,
            (user_key.strip(),),
        ).fetchone()

    return row_to_dict(row)


# ============================================================
# 会话管理
# ============================================================

def create_conversation(
    user_id: int,
    title: str = "新对话",
    work_mode: str = "通用问答模式",
) -> dict[str, Any]:
    """
    创建新会话。
    """

    conversation_id = str(uuid.uuid4())
    now = current_datetime()

    normalized_title = title.strip() or "新对话"

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO conversations (
                id,
                user_id,
                title,
                work_mode,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                user_id,
                normalized_title,
                work_mode,
                now,
                now,
            ),
        )

        row = connection.execute(
            """
            SELECT *
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()

        connection.commit()

    conversation = row_to_dict(row)

    if conversation is None:
        raise RuntimeError(
            "新会话创建失败。"
        )

    return conversation


def get_conversation(
    conversation_id: str,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """
    获取单个会话。

    提供user_id时，同时检查该会话是否属于当前用户。
    """

    with get_connection() as connection:
        if user_id is None:
            row = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE id = ?
                """,
                (conversation_id,),
            ).fetchone()

        else:
            row = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE id = ?
                  AND user_id = ?
                """,
                (
                    conversation_id,
                    user_id,
                ),
            ).fetchone()

    return row_to_dict(row)


def list_conversations(
    user_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    查询用户的历史会话列表。

    最近更新的会话排在前面。
    """

    safe_limit = max(
        1,
        min(limit, 200),
    )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                conversations.*,

                (
                    SELECT COUNT(*)
                    FROM messages
                    WHERE messages.conversation_id =
                          conversations.id
                ) AS message_count

            FROM conversations

            WHERE user_id = ?

            ORDER BY updated_at DESC

            LIMIT ?
            """,
            (
                user_id,
                safe_limit,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def rename_conversation(
    conversation_id: str,
    user_id: int,
    new_title: str,
) -> bool:
    """
    修改会话标题。
    """

    normalized_title = new_title.strip()

    if not normalized_title:
        raise ValueError(
            "会话标题不能为空。"
        )

    now = current_datetime()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE conversations
            SET
                title = ?,
                updated_at = ?
            WHERE id = ?
              AND user_id = ?
            """,
            (
                normalized_title,
                now,
                conversation_id,
                user_id,
            ),
        )

        connection.commit()

        return cursor.rowcount > 0


def delete_conversation(
    conversation_id: str,
    user_id: int,
) -> bool:
    """
    删除一个会话。

    该会话下的消息会通过外键级联删除。
    """

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
              AND user_id = ?
            """,
            (
                conversation_id,
                user_id,
            ),
        )

        connection.commit()

        return cursor.rowcount > 0


# ============================================================
# 消息管理
# ============================================================

def add_message(
    conversation_id: str,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """
    保存一条聊天消息。

    提供user_id时，会先检查该会话是否属于当前用户，
    防止用户向其他用户的会话中写入消息。
    """

    if role not in {
        "system",
        "user",
        "assistant",
    }:
        raise ValueError(
            "消息role无效。"
        )

    normalized_content = content.strip()

    if not normalized_content:
        raise ValueError(
            "消息内容不能为空。"
        )

    now = current_datetime()

    meta_json = None

    if meta is not None:
        meta_json = json.dumps(
            meta,
            ensure_ascii=False,
        )

    with get_connection() as connection:
        # 用户归属检查
        if user_id is not None:
            owner_row = connection.execute(
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

            if owner_row is None:
                raise PermissionError(
                    "无权向该会话保存消息。"
                )

        cursor = connection.execute(
            """
            INSERT INTO messages (
                conversation_id,
                role,
                content,
                meta_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                role,
                normalized_content,
                meta_json,
                now,
            ),
        )

        connection.execute(
            """
            UPDATE conversations
            SET updated_at = ?
            WHERE id = ?
            """,
            (
                now,
                conversation_id,
            ),
        )

        message_id = cursor.lastrowid

        row = connection.execute(
            """
            SELECT *
            FROM messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()

        connection.commit()

    message = row_to_dict(row)

    if message is None:
        raise RuntimeError(
            "消息保存失败。"
        )

    message["meta"] = (
        json.loads(message["meta_json"])
        if message.get("meta_json")
        else None
    )

    return message

def get_messages(
    conversation_id: str,
    limit: int | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    获取指定会话中的消息。

    参数：
    conversation_id：
        会话ID。

    limit：
        最多返回多少条最近消息。
        None表示返回全部消息。

    user_id：
        当前登录用户ID。
        提供该参数时，只允许读取属于该用户的会话。
    """

    with get_connection() as connection:

        # ====================================================
        # 获取全部消息
        # ====================================================

        if limit is None:

            # 不检查用户归属
            if user_id is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY id ASC
                    """,
                    (
                        conversation_id,
                    ),
                ).fetchall()

            # 检查会话是否属于当前用户
            else:
                rows = connection.execute(
                    """
                    SELECT messages.*
                    FROM messages

                    INNER JOIN conversations
                        ON conversations.id =
                           messages.conversation_id

                    WHERE messages.conversation_id = ?
                      AND conversations.user_id = ?

                    ORDER BY messages.id ASC
                    """,
                    (
                        conversation_id,
                        user_id,
                    ),
                ).fetchall()

        # ====================================================
        # 只获取最近若干条消息
        # ====================================================

        else:
            safe_limit = max(
                1,
                min(limit, 500),
            )

            # 不检查用户归属
            if user_id is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM (
                        SELECT *
                        FROM messages
                        WHERE conversation_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC
                    """,
                    (
                        conversation_id,
                        safe_limit,
                    ),
                ).fetchall()

            # 检查会话是否属于当前用户
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM (
                        SELECT messages.*
                        FROM messages

                        INNER JOIN conversations
                            ON conversations.id =
                               messages.conversation_id

                        WHERE messages.conversation_id = ?
                          AND conversations.user_id = ?

                        ORDER BY messages.id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC
                    """,
                    (
                        conversation_id,
                        user_id,
                        safe_limit,
                    ),
                ).fetchall()

    messages: list[dict[str, Any]] = []

    for row in rows:
        message = dict(row)

        message["meta"] = (
            json.loads(message["meta_json"])
            if message.get("meta_json")
            else None
        )

        messages.append(message)

    return messages
# ============================================================
# 每日调用次数管理
# ============================================================

def get_daily_usage(
    user_id: int,
    usage_date: str | None = None,
) -> int:
    """
    查询用户某一天的AI调用次数。
    """

    target_date = (
        usage_date
        or current_date()
    )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT request_count
            FROM daily_usage
            WHERE user_id = ?
              AND usage_date = ?
            """,
            (
                user_id,
                target_date,
            ),
        ).fetchone()

    if row is None:
        return 0

    return int(row["request_count"])


def increment_daily_usage(
    user_id: int,
    amount: int = 1,
) -> int:
    """
    增加当天AI调用次数，并返回增加后的次数。
    """

    if amount <= 0:
        raise ValueError(
            "amount必须大于0。"
        )

    today = current_date()
    now = current_datetime()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO daily_usage (
                user_id,
                usage_date,
                request_count,
                updated_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id, usage_date)
            DO UPDATE SET
                request_count =
                    daily_usage.request_count
                    + excluded.request_count,

                updated_at =
                    excluded.updated_at
            """,
            (
                user_id,
                today,
                amount,
                now,
            ),
        )

        row = connection.execute(
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

        connection.commit()

    if row is None:
        raise RuntimeError(
            "每日使用次数更新失败。"
        )

    return int(row["request_count"])


def check_daily_limit(
    user_id: int,
) -> dict[str, Any]:
    """
    检查用户当天是否还可以调用AI。
    """

    user = get_user_by_id(user_id)

    if user is None:
        raise ValueError(
            "用户不存在。"
        )

    used = get_daily_usage(user_id)

    daily_limit = int(
        user["daily_limit"]
    )

    is_admin = (
        user["role"] == "admin"
    )

    allowed = (
        is_admin
        or daily_limit == 0
        or used < daily_limit
    )

    remaining: int | None

    if is_admin or daily_limit == 0:
        remaining = None
    else:
        remaining = max(
            daily_limit - used,
            0,
        )

    return {
        "allowed": allowed,
        "used": used,
        "daily_limit": daily_limit,
        "remaining": remaining,
        "is_admin": is_admin,
    }


# ============================================================
# 会话标题工具
# ============================================================

def build_conversation_title(
    question: str,
    max_length: int = 24,
) -> str:
    """
    根据用户第一条问题生成简单会话标题。
    """

    clean_text = " ".join(
        question.strip().split()
    )

    if not clean_text:
        return "新对话"

    if len(clean_text) <= max_length:
        return clean_text

    return (
        clean_text[:max_length]
        + "…"
    )

# ============================================================
# 第2.3阶段：历史会话搜索与空会话治理
# ============================================================

def search_conversations(
    user_id: int,
    query: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    搜索用户历史会话。

    搜索范围：
    1. 会话标题
    2. 用户消息和AI回答内容

    query为空时，返回普通历史会话列表。
    """

    normalized_query = query.strip()

    if not normalized_query:
        return list_conversations(
            user_id=user_id,
            limit=limit,
        )

    safe_limit = max(
        1,
        min(limit, 200),
    )

    search_pattern = (
        f"%{normalized_query}%"
    )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                conversations.*,

                (
                    SELECT COUNT(*)
                    FROM messages
                    WHERE messages.conversation_id =
                          conversations.id
                ) AS message_count

            FROM conversations

            WHERE conversations.user_id = ?

              AND (
                    conversations.title LIKE ?

                    OR EXISTS (
                        SELECT 1
                        FROM messages
                        WHERE messages.conversation_id =
                              conversations.id

                          AND messages.content LIKE ?
                    )
              )

            ORDER BY conversations.updated_at DESC

            LIMIT ?
            """,
            (
                user_id,
                search_pattern,
                search_pattern,
                safe_limit,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def find_empty_conversation(
    user_id: int,
    work_mode: str,
) -> dict[str, Any] | None:
    """
    查找某个用户、某种模式下最近创建的空会话。

    空会话定义：
    messages表中没有该会话的任何消息。
    """

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                conversations.*,
                0 AS message_count

            FROM conversations

            WHERE conversations.user_id = ?
              AND conversations.work_mode = ?

              AND NOT EXISTS (
                    SELECT 1
                    FROM messages
                    WHERE messages.conversation_id =
                          conversations.id
              )

            ORDER BY conversations.updated_at DESC

            LIMIT 1
            """,
            (
                user_id,
                work_mode,
            ),
        ).fetchone()

    return row_to_dict(row)


def create_or_reuse_conversation(
    user_id: int,
    work_mode: str,
    title: str = "新对话",
) -> tuple[dict[str, Any], bool]:
    """
    创建或复用空会话。

    返回：
    conversation：
        会话数据。

    created：
        True表示创建了新会话。
        False表示复用了已有空会话。

    这样可以防止用户连续点击“新建对话”
    导致数据库出现大量空会话。
    """

    empty_conversation = find_empty_conversation(
        user_id=user_id,
        work_mode=work_mode,
    )

    if empty_conversation is not None:
        return empty_conversation, False

    conversation = create_conversation(
        user_id=user_id,
        title=title,
        work_mode=work_mode,
    )

    return conversation, True


def cleanup_extra_empty_conversations(
    user_id: int,
    keep_conversation_id: str | None = None,
) -> int:
    """
    清理用户已经堆积的多余空会话。

    清理规则：
    1. 当前会话不会删除。
    2. 每种工作模式最多保留一个空会话。
    3. 优先保留最近更新的空会话。
    4. 有聊天消息的会话绝不会删除。

    返回实际删除的空会话数量。
    """

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                conversations.id,
                conversations.work_mode,
                conversations.updated_at

            FROM conversations

            WHERE conversations.user_id = ?

              AND NOT EXISTS (
                    SELECT 1
                    FROM messages
                    WHERE messages.conversation_id =
                          conversations.id
              )

            ORDER BY
                conversations.work_mode ASC,
                conversations.updated_at DESC
            """,
            (user_id,),
        ).fetchall()

        empty_conversations = [
            dict(row)
            for row in rows
        ]

        keep_ids: set[str] = set()
        kept_modes: set[str] = set()

        # 当前会话如果为空，必须优先保留。
        if keep_conversation_id:
            for conversation in empty_conversations:
                if (
                    conversation["id"]
                    == keep_conversation_id
                ):
                    keep_ids.add(
                        conversation["id"]
                    )

                    kept_modes.add(
                        conversation["work_mode"]
                    )

                    break

        # 每种模式保留最近的一个空会话。
        for conversation in empty_conversations:
            conversation_id = conversation["id"]
            work_mode = conversation["work_mode"]

            if conversation_id in keep_ids:
                continue

            if work_mode not in kept_modes:
                keep_ids.add(
                    conversation_id
                )

                kept_modes.add(
                    work_mode
                )

        delete_ids = [
            conversation["id"]
            for conversation in empty_conversations
            if conversation["id"] not in keep_ids
        ]

        if not delete_ids:
            return 0

        connection.executemany(
            """
            DELETE FROM conversations
            WHERE id = ?
              AND user_id = ?
            """,
            [
                (
                    conversation_id,
                    user_id,
                )
                for conversation_id in delete_ids
            ],
        )

        connection.commit()

    return len(delete_ids)
# ============================================================
# 单独运行时初始化数据库
# ============================================================

if __name__ == "__main__":
    init_database()

    print("=" * 60)
    print("数据库初始化完成")
    print(f"数据库路径：{DB_PATH}")
    print("数据库版本：2.0.0")
    print("=" * 60)