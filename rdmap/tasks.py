"""持久化下载任务与每张瓦片的结果，不保存服务密钥。"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import task_database_path
from .download_levels import format_levels

if TYPE_CHECKING:
    from .workers import DownloadOptions


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else task_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, provider_key TEXT NOT NULL,
                    output_dir TEXT NOT NULL, zoom INTEGER NOT NULL,
                    min_row INTEGER NOT NULL, max_row INTEGER NOT NULL,
                    min_col INTEGER NOT NULL, max_col INTEGER NOT NULL,
                    concurrency INTEGER NOT NULL, skip_existing INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT '',
                    batch_id TEXT NOT NULL DEFAULT '', batch_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS task_tiles (
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    col INTEGER NOT NULL, row INTEGER NOT NULL,
                    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (task_id, row, col)
                );
                CREATE INDEX IF NOT EXISTS task_tile_status ON task_tiles(task_id, status);
            """)
            # 兼容已有的单级下载任务库，保留原任务及瓦片结果。
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "batch_id" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            if "batch_order" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN batch_order INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS task_batch ON tasks(batch_id,batch_order)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, options: DownloadOptions, name: str = "") -> str:
        with self.connection() as db:
            return self._insert(db, options, name)

    def _insert(self, db, options, name: str, batch_id: str = "", batch_order: int = 0) -> str:
        tile_range = options.tile_range
        task_id = uuid.uuid4().hex
        now = timestamp()
        db.execute(
            "INSERT INTO tasks (id,name,provider_key,output_dir,zoom,min_row,max_row,min_col,max_col,"
            "concurrency,skip_existing,created_at,updated_at,batch_id,batch_order) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, name or f"{options.provider.name} · Z{tile_range.zoom}", options.provider.key,
             str(Path(options.output_dir).expanduser().resolve()), tile_range.zoom,
             tile_range.min_row, tile_range.max_row, tile_range.min_col, tile_range.max_col,
             options.concurrency, int(options.skip_existing), now, now, batch_id, batch_order),
        )
        return task_id

    def create_batch(self, options: list[DownloadOptions], name: str) -> list[str]:
        if not options:
            raise ValueError("没有需要下载的级别")
        options = sorted(options, key=lambda item: item.tile_range.zoom)
        if len({item.tile_range.zoom for item in options}) != len(options):
            raise ValueError("同一批次的级别不能重复")
        if len({(item.provider.key, str(Path(item.output_dir).resolve())) for item in options}) != 1:
            raise ValueError("同一批次必须使用相同图源和输出目录")
        batch_id = uuid.uuid4().hex if len(options) > 1 else ""
        with self.connection() as db:
            return [self._insert(db, item, f"{name} · Z{item.tile_range.zoom}", batch_id, index)
                    for index, item in enumerate(options)]

    def group_tasks(self, task_id: str) -> list[dict]:
        task = self.get(task_id)
        if not task["batch_id"]:
            return [task]
        with self.connection() as db:
            ids = [row[0] for row in db.execute(
                "SELECT id FROM tasks WHERE batch_id=? ORDER BY batch_order", (task["batch_id"],))]
        return [self.get(item) for item in ids]

    def list_groups(self) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for task in self.list_tasks():
            groups.setdefault(task["batch_id"] or task["id"], []).append(task)
        result = []
        for children in groups.values():
            children.sort(key=lambda item: item["batch_order"])
            summary = dict(children[0])
            summary["level_tasks"] = children
            summary["zoom_label"] = format_levels(item["zoom"] for item in children)
            if len(children) > 1:
                for field in ("done", "total", "failed"):
                    summary[field] = sum(item[field] for item in children)
                states = {item["status"] for item in children}
                summary["status"] = (
                    "running" if "running" in states else "completed" if states == {"completed"}
                    else "partial" if summary["failed"] else "paused" if states & {"paused", "completed"}
                    else "error" if "error" in states else "pending"
                )
                summary["name"] = summary["name"].rsplit(" · Z", 1)[0] + " · 多级下载"
                finished = sum(item["status"] == "completed" for item in children)
                summary["message"] = f"{len(children)} 个级别，已完成 {finished} 个；按级别从低到高连续下载。"
            result.append(summary)
        return result

    def list_tasks(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""
                SELECT t.*, (max_row-min_row+1)*(max_col-min_col+1) AS total,
                    (SELECT COUNT(*) FROM task_tiles r WHERE r.task_id=t.id
                        AND r.status IN ('success','skipped')) AS done,
                    (SELECT COUNT(*) FROM task_tiles r WHERE r.task_id=t.id
                        AND r.status='failed') AS failed
                FROM tasks t ORDER BY created_at DESC, rowid DESC
            """).fetchall()
        return [dict(row) for row in rows]

    def get(self, task_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("""
                SELECT t.*, (max_row-min_row+1)*(max_col-min_col+1) AS total,
                    (SELECT COUNT(*) FROM task_tiles r WHERE r.task_id=t.id
                        AND r.status IN ('success','skipped')) AS done,
                    (SELECT COUNT(*) FROM task_tiles r WHERE r.task_id=t.id
                        AND r.status='failed') AS failed
                FROM tasks t WHERE id=?
            """, (task_id,)).fetchone()
        if row is None:
            raise ValueError("下载任务不存在")
        return dict(row)

    def recover_interrupted(self) -> None:
        with self.connection() as db:
            db.execute("UPDATE tasks SET status='paused', message='上次运行中断，可继续下载', updated_at=? "
                       "WHERE status='running'", (timestamp(),))

    def start(self, task_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE tasks SET status='running', message='', updated_at=? WHERE id=?",
                       (timestamp(), task_id))

    def record_tiles(self, task_id: str, results: list[tuple[int, int, str, str]]) -> None:
        if not results:
            return
        with self.connection() as db:
            db.executemany(
                "INSERT INTO task_tiles (task_id,col,row,status,error) VALUES (?,?,?,?,?) "
                "ON CONFLICT(task_id,row,col) DO UPDATE SET status=excluded.status, error=excluded.error",
                ((task_id, col, row, status, error) for col, row, status, error in results),
            )
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (timestamp(), task_id))

    def finish(self, task_id: str, cancelled: bool, message: str) -> str:
        task = self.get(task_id)
        status = ("completed" if task["done"] == task["total"] else "paused" if cancelled
                  else "partial" if task["failed"] else "error")
        with self.connection() as db:
            db.execute("UPDATE tasks SET status=?, message=?, updated_at=? WHERE id=?",
                       (status, message, timestamp(), task_id))
        return status

    def failures(self, task_id: str, limit: int | None = None) -> list[dict]:
        task = self.get(task_id) if self._exists(task_id) else None
        if task is None:
            return []
        condition = "t.batch_id=?" if task["batch_id"] else "t.id=?"
        query = ("SELECT r.col,r.row,r.error,t.zoom FROM task_tiles r JOIN tasks t ON t.id=r.task_id "
                 f"WHERE {condition} AND r.status='failed' ORDER BY t.zoom,r.row,r.col")
        args: tuple = (task["batch_id"] or task_id,)
        if limit is not None:
            query += " LIMIT ?"
            args += (limit,)
        with self.connection() as db:
            return [dict(row) for row in db.execute(query, args)]

    def tile_states(self, task_id: str, zoom: int) -> tuple[set[int], set[int]]:
        done, failed = set(), set()
        with self.connection() as db:
            for row in db.execute("SELECT col,row,status FROM task_tiles WHERE task_id=?", (task_id,)):
                index = (int(row["row"]) << zoom) + int(row["col"])
                (failed if row["status"] == "failed" else done).add(index)
        return done, failed

    def delete(self, task_id: str) -> None:
        if not self._exists(task_id):
            return
        task = self.get(task_id)
        condition = "batch_id=?" if task["batch_id"] else "id=?"
        args = (task["batch_id"] or task_id,)
        with self.connection() as db:
            if db.execute(f"SELECT 1 FROM tasks WHERE {condition} AND status='running'", args).fetchone():
                return
            db.execute(f"DELETE FROM tasks WHERE {condition}", args)

    def _exists(self, task_id: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is not None
