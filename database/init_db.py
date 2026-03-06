#!/usr/bin/env python3
"""Initialize SQLite database for the Habit Tracker (database container).

This script is the canonical entrypoint for provisioning the local SQLite schema
used by the rest of the system (backend + any tooling).

Contract:
- Inputs:
  - Uses DB_NAME constant (SQLite file in the current working directory).
- Outputs:
  - Ensures the SQLite database file exists and contains the habit-tracker schema.
  - Writes/updates:
    - db_connection.txt (human-readable connection hints)
    - db_visualizer/sqlite.env (for the included Node.js DB viewer)
- Side effects:
  - Creates tables and indexes if they do not exist.
  - Seeds a minimal set of rows when the tables are empty.
- Errors:
  - Raises sqlite3.Error for SQLite issues (printed with context at the boundary).
"""

import os
import sqlite3
from typing import Optional

DB_NAME = "myapp.db"
DB_USER = "kaviasqlite"  # Not used for SQLite, kept for consistency with template tooling
DB_PASSWORD = "kaviadefaultpassword"  # Not used for SQLite
DB_PORT = "5000"  # Not used for SQLite


def _connect(db_path: str) -> sqlite3.Connection:
    """Create a SQLite connection with recommended pragmas."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Enforce referential integrity in SQLite.
    conn.execute("PRAGMA foreign_keys = ON")
    # Improve concurrency for read/write (safe for a local file DB).
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create habit tracker tables and indexes if they don't exist."""
    cur = conn.cursor()

    # Metadata table (kept from template, repurposed).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Habits master table.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            color_hex TEXT,                 -- e.g. "#3B82F6"
            icon_name TEXT,                 -- e.g. "check_circle"
            is_archived INTEGER NOT NULL DEFAULT 0,  -- 0/1
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Habit logs / completions: one row per "habit done at timestamp".
    # Note: we store the local date (YYYY-MM-DD) as habit_date for easy streak queries.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            habit_date TEXT NOT NULL,       -- "YYYY-MM-DD" in user's local time
            completed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            note TEXT,
            mood INTEGER,                   -- optional small int rating
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
        )
        """
    )

    # Prevent duplicate "daily completion" per habit per date (common for habit trackers).
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_habit_logs_unique_daily
        ON habit_logs (habit_id, habit_date)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_id_date
        ON habit_logs (habit_id, habit_date)
        """
    )

    # Reminders: simple daily reminder time per habit. Scheduling is handled elsewhere.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS habit_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,     -- 0/1
            time_of_day TEXT NOT NULL,              -- "HH:MM" 24h
            days_of_week_mask INTEGER NOT NULL DEFAULT 127, -- bitmask, 1=Mon ... 64=Sun; 127=everyday
            message TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_habit_reminders_habit_id
        ON habit_reminders (habit_id)
        """
    )

    # App settings (single-row KV store).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Triggers to keep updated_at fresh.
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_habits_updated_at
        AFTER UPDATE ON habits
        FOR EACH ROW
        BEGIN
            UPDATE habits SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
        END
        """
    )
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_habit_reminders_updated_at
        AFTER UPDATE ON habit_reminders
        FOR EACH ROW
        BEGIN
            UPDATE habit_reminders SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
        END
        """
    )

    conn.commit()


def _table_row_count(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) as c FROM {table}")
    return int(cur.fetchone()[0])


def _seed_minimal_data(conn: sqlite3.Connection) -> None:
    """Seed minimal starter data (only if the DB is empty)."""
    cur = conn.cursor()

    # Always keep app_info consistent.
    cur.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("project_name", "habit-tracker"),
    )
    cur.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("version", "0.1.0"),
    )
    cur.execute(
        "INSERT OR REPLACE INTO app_info (key, value) VALUES (?, ?)",
        ("description", "Habit tracker local database schema (SQLite)"),
    )

    # Seed settings defaults (idempotent).
    def _set_default(key: str, value: str) -> None:
        cur.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value),
        )

    _set_default("week_starts_on", "mon")  # "mon" | "sun"
    _set_default("reminders_enabled_global", "true")

    # Only seed example habits if none exist.
    if _table_row_count(conn, "habits") == 0:
        cur.execute(
            """
            INSERT INTO habits (name, description, color_hex, icon_name)
            VALUES (?, ?, ?, ?)
            """,
            ("Drink Water", "Have at least 8 cups of water", "#06B6D4", "water_drop"),
        )
        cur.execute(
            """
            INSERT INTO habits (name, description, color_hex, icon_name)
            VALUES (?, ?, ?, ?)
            """,
            ("Read", "Read 10 pages", "#3B82F6", "menu_book"),
        )

        # Add a reminder for the first habit (id 1 in a fresh DB, but we do not assume;
        # retrieve it deterministically).
        cur.execute("SELECT id FROM habits WHERE name = ?", ("Drink Water",))
        row = cur.fetchone()
        habit_id: Optional[int] = int(row[0]) if row else None
        if habit_id is not None:
            cur.execute(
                """
                INSERT INTO habit_reminders (habit_id, enabled, time_of_day, days_of_week_mask, message)
                VALUES (?, 1, '09:00', 127, ?)
                """,
                (habit_id, "Time to drink water"),
            )

    conn.commit()


def _write_connection_hints(db_name: str) -> None:
    current_dir = os.getcwd()
    connection_string = f"sqlite:///{current_dir}/{db_name}"
    with open("db_connection.txt", "w", encoding="utf-8") as f:
        f.write("# SQLite connection methods:\n")
        f.write(f"# Python: sqlite3.connect('{db_name}')\n")
        f.write(f"# Connection string: {connection_string}\n")
        f.write(f"# File path: {current_dir}/{db_name}\n")


def _write_visualizer_env(db_name: str) -> None:
    db_path = os.path.abspath(db_name)
    os.makedirs("db_visualizer", exist_ok=True)
    with open("db_visualizer/sqlite.env", "w", encoding="utf-8") as f:
        f.write(f'export SQLITE_DB="{db_path}"\n')


def main() -> None:
    """Boundary/entrypoint for the init flow."""
    print("Starting SQLite setup (habit tracker schema)...")

    db_exists = os.path.exists(DB_NAME)
    if db_exists:
        print(f"SQLite database already exists at {DB_NAME}")
    else:
        print("Creating new SQLite database...")

    try:
        conn = _connect(DB_NAME)
        try:
            _create_schema(conn)
            _seed_minimal_data(conn)

            # Print some basic stats.
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            table_count = int(cur.fetchone()[0])
            habits_count = _table_row_count(conn, "habits")
            logs_count = _table_row_count(conn, "habit_logs")
        finally:
            conn.close()

        _write_connection_hints(DB_NAME)
        print("Connection information saved to db_connection.txt")

        _write_visualizer_env(DB_NAME)
        print("Environment variables saved to db_visualizer/sqlite.env")

        current_dir = os.getcwd()
        print("\nSQLite setup complete!")
        print(f"Database: {DB_NAME}")
        print(f"Location: {current_dir}/{DB_NAME}")
        print("\nTo use with Node.js viewer, run: source db_visualizer/sqlite.env")

        print("\nDatabase statistics:")
        print(f"  Tables: {table_count}")
        print(f"  Habits: {habits_count}")
        print(f"  Habit logs: {logs_count}")

        # If sqlite3 CLI is available, show how to use it.
        try:
            import subprocess

            result = subprocess.run(["which", "sqlite3"], capture_output=True, text=True)
            if result.returncode == 0:
                print("\nSQLite CLI is available. You can also use:")
                print(f"  sqlite3 {DB_NAME}")
        except Exception:
            pass

        print("\nScript completed successfully.")

    except sqlite3.Error as e:
        # Boundary-level error handling: provide actionable context.
        print("ERROR: Failed to initialize SQLite database.")
        print(f"Reason: {e}")
        raise


if __name__ == "__main__":
    main()
