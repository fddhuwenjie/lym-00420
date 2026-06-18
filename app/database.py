import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DB_PATH = Path(__file__).parent.parent / "approval_system.db"


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                department TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'draft',
                creator_id INTEGER NOT NULL,
                current_handler_id INTEGER,
                original_approver_id INTEGER,
                approval_mode TEXT NOT NULL DEFAULT 'single',
                deadline TIMESTAMP,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                escalated INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (creator_id) REFERENCES users(id),
                FOREIGN KEY (current_handler_id) REFERENCES users(id),
                FOREIGN KEY (original_approver_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS approvers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                has_voted INTEGER NOT NULL DEFAULT 0,
                vote_result TEXT,
                voted_at TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(task_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS approval_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                operator_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                comment TEXT DEFAULT '',
                operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (operator_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS saved_filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filter_name TEXT NOT NULL,
                filter_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS delegations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delegator_id INTEGER NOT NULL,
                delegatee_id INTEGER NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (delegator_id) REFERENCES users(id),
                FOREIGN KEY (delegatee_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS delegation_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                delegation_id INTEGER NOT NULL,
                original_approver_id INTEGER NOT NULL,
                delegatee_id INTEGER NOT NULL,
                delegated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reverted_at TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (delegation_id) REFERENCES delegations(id),
                FOREIGN KEY (original_approver_id) REFERENCES users(id),
                FOREIGN KEY (delegatee_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS reminder_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                operator_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,
                escalation_level INTEGER NOT NULL DEFAULT 0,
                comment TEXT DEFAULT '',
                reminded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (operator_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS approval_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER NOT NULL,
                template_name TEXT NOT NULL,
                department TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                approval_mode TEXT NOT NULL DEFAULT 'single',
                approver_ids_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (creator_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_department ON tasks(department);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
            CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline);
            CREATE INDEX IF NOT EXISTS idx_records_task_id ON approval_records(task_id);
            CREATE INDEX IF NOT EXISTS idx_approvers_task_id ON approvers(task_id);
            CREATE INDEX IF NOT EXISTS idx_delegations_active ON delegations(status, start_time, end_time);
            CREATE INDEX IF NOT EXISTS idx_delegation_records_task ON delegation_records(task_id);
            CREATE INDEX IF NOT EXISTS idx_reminder_records_task ON reminder_records(task_id);
            CREATE INDEX IF NOT EXISTS idx_templates_creator ON approval_templates(creator_id);
        """)

        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            cursor.executemany(
                "INSERT INTO users (username, name, department, role) VALUES (?, ?, ?, ?)",
                [
                    ("alice", "爱丽丝", "技术部", "user"),
                    ("bob", "鲍勃", "技术部", "user"),
                    ("charlie", "查理", "产品部", "user"),
                    ("david", "大卫", "产品部", "manager"),
                    ("eve", "伊芙", "财务部", "manager"),
                    ("frank", "弗兰克", "技术部", "manager"),
                ]
            )
