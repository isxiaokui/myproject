import os
from contextlib import contextmanager
from urllib.parse import quote
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_pool = None


def init_history_pool() -> None:
    global _pool
    if _pool is not None:
        return
    from psycopg_pool import ConnectionPool

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        database_user = os.getenv("POSTGRES_USER", "").strip()
        database_password = os.getenv("POSTGRES_PASSWORD", "")
        database_name = os.getenv("POSTGRES_DB", "").strip()
        database_host = os.getenv("DB_HOST", "localhost").strip()
        database_port = os.getenv("DB_PORT", "5432").strip()
        if database_user and database_password and database_name:
            database_url = (
                f"postgresql://{quote(database_user, safe='')}:{quote(database_password, safe='')}"
                f"@{database_host}:{database_port}/{quote(database_name, safe='')}"
            )
    if not database_url:
        raise RuntimeError("未配置 DATABASE_URL 或 PostgreSQL 连接参数，无法使用 PostgreSQL 保存会话。")
    min_size = max(1, int(os.getenv("DB_POOL_MIN_SIZE", "1")))
    max_size = max(min_size, int(os.getenv("DB_POOL_MAX_SIZE", "10")))
    _pool = ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        timeout=max(1, float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "10"))),
        kwargs={"row_factory": dict_row, "connect_timeout": 5},
        open=False,
    )
    _pool.open(wait=True)


def close_history_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None

@contextmanager
def _connection():
    if _pool is None:
        init_history_pool()
    with _pool.connection() as connection:
        yield connection


def init_history_db() -> None:
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                owner_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS owner_id TEXT REFERENCES users(id) ON DELETE CASCADE"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id, id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated ON conversations(owner_id, updated_at DESC)"
        )


def create_user(username: str, password_hash: str) -> dict | None:
    user_id = str(uuid4())
    with _connection() as connection:
        row = connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s) "
            "ON CONFLICT (username) DO NOTHING RETURNING id, username",
            (user_id, username, password_hash),
        ).fetchone()
        if row is None:
            return None
    return dict(row)


def get_user_by_username(username: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = %s",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT id, username FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def create_conversation(owner_id: str, title: str) -> dict:
    conversation_id = str(uuid4())
    title = " ".join(title.strip().split())[:48] or "新对话"
    with _connection() as connection:
        row = connection.execute(
            """
            INSERT INTO conversations (id, owner_id, title)
            VALUES (%s, %s, %s)
            RETURNING id, title, created_at, updated_at
            """,
            (conversation_id, owner_id, title),
        ).fetchone()
    return dict(row)


def list_conversations(owner_id: str) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE owner_id = %s ORDER BY updated_at DESC, id DESC",
            (owner_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(owner_id: str, conversation_id: str) -> dict | None:
    with _connection() as connection:
        conversation = connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = %s AND owner_id = %s",
            (conversation_id, owner_id),
        ).fetchone()
        if conversation is None:
            return None
        rows = connection.execute(
            "SELECT id, role, content, metadata, created_at FROM messages "
            "WHERE conversation_id = %s ORDER BY id",
            (conversation_id,),
        ).fetchall()

    messages = []
    for row in rows:
        message = dict(row)
        message.update(message.pop("metadata") or {})
        messages.append(message)
    return {**dict(conversation), "messages": messages}


def conversation_exists(owner_id: str, conversation_id: str) -> bool:
    with _connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND owner_id = %s",
            (conversation_id, owner_id),
        ).fetchone()
    return row is not None


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> None:
    with _connection() as connection:
        connection.execute(
            "INSERT INTO messages (conversation_id, role, content, metadata) "
            "VALUES (%s, %s, %s, %s)",
            (conversation_id, role, content, Jsonb(metadata or {})),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
            (conversation_id,),
        )


def delete_conversation(owner_id: str, conversation_id: str) -> bool:
    with _connection() as connection:
        row = connection.execute(
            "DELETE FROM conversations WHERE id = %s AND owner_id = %s RETURNING id",
            (conversation_id, owner_id),
        ).fetchone()
    return row is not None
