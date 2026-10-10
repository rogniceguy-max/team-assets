"""SQLite database initialization, migration, and connection management."""
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path
from fastapi import HTTPException

from core.utils import uid, now
from episodes import migrate_episodes


class DBMixin:
    """Manages database connection lifecycle, DDL, and schema migrations."""

    def __init__(self, data_dir, *args, read_only=False, **kwargs):
        self.root = Path(data_dir).resolve()
        self.read_only = bool(read_only)
        if self.read_only:
            self.db = self.root / "database" / "assets.sqlite3"
            if not self.db.is_file():
                raise RuntimeError(f"Read-only data store is missing: {self.db}")
        else:
            for name in ("database", "originals", "thumbnails", "staging", "backups"):
                (self.root / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = self.root / "database" / "assets.sqlite3"
        fresh_store = not self.db.exists()
        super().__init__(*args, **kwargs)
        if not self.read_only:
            self._init_schema(fresh_store=fresh_store)

    def ensure_writable(self):
        if self.read_only:
            raise HTTPException(
                403,
                {"code": "READ_ONLY", "message": "此服务当前为只读预览，不能修改数据"},
            )

    @contextmanager
    def connect(self, write=False):
        if write:
            self.ensure_writable()
        with self.lock if write else nullcontext():
            if write:
                self._pending_change_revision = None
            if self.read_only:
                uri = self.db.as_uri() + "?mode=ro"
                c = sqlite3.connect(uri, timeout=30, uri=True)
            else:
                c = sqlite3.connect(self.db, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            if self.read_only:
                c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            committed = False
            try:
                yield c
                c.commit()
                committed = True
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()
                if write:
                    revision = self._pending_change_revision if committed else None
                    self._pending_change_revision = None
                    if revision is not None:
                        self.notify_changes(revision)

    def _init_schema(self, fresh_store=False):
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,category TEXT NOT NULL,role TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS variants(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),name TEXT NOT NULL,episodes TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',episode_usages TEXT NOT NULL DEFAULT '[]',version INTEGER NOT NULL DEFAULT 1,final_material_id TEXT,episode_scope TEXT,episodes_original TEXT,episodes_needs_review INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,sha256 TEXT UNIQUE NOT NULL,path TEXT NOT NULL,thumbnail TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,variant_id TEXT REFERENCES variants(id),name TEXT NOT NULL,source TEXT NOT NULL,file_id TEXT NOT NULL REFERENCES files(id),created_at TEXT NOT NULL,deleted_at TEXT,asset_id TEXT NOT NULL REFERENCES assets(id));
                CREATE TABLE IF NOT EXISTS candidates(
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    file_id TEXT NOT NULL REFERENCES files(id),
                    name TEXT NOT NULL,
                    relative_path TEXT NOT NULL DEFAULT '',
                    folder_path TEXT NOT NULL DEFAULT '',
                    sort_order REAL NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT '其它',
                    status TEXT NOT NULL DEFAULT 'active',
                    highlighted_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    delete_batch TEXT,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS candidate_folders(
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    path TEXT NOT NULL,
                    parent_path TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    sort_order REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(project_id,path)
                );
                CREATE TABLE IF NOT EXISTS candidate_references(
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidates(id),
                    asset_id TEXT NOT NULL REFERENCES assets(id),
                    variant_id TEXT REFERENCES variants(id),
                    created_at TEXT NOT NULL,
                    match_status TEXT NOT NULL DEFAULT 'unreviewed',
                    confidence REAL,
                    note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'mcp',
                    UNIQUE(candidate_id,asset_id,variant_id)
                );
                CREATE TABLE IF NOT EXISTS events(revision INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,object_id TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
            """)
            c.execute("BEGIN IMMEDIATE")
            self.migrate_domain(c, fresh_store=fresh_store)

            material_columns = {r["name"] for r in c.execute("PRAGMA table_info(materials)")}
            if "candidate_id" not in material_columns:
                c.execute("ALTER TABLE materials ADD COLUMN candidate_id TEXT REFERENCES candidates(id)")

            reference_columns = {r["name"] for r in c.execute("PRAGMA table_info(candidate_references)")}
            for name, definition in (
                ("match_status", "TEXT NOT NULL DEFAULT 'unreviewed'"),
                ("confidence", "REAL"),
                ("note", "TEXT NOT NULL DEFAULT ''"),
                ("source", "TEXT NOT NULL DEFAULT 'mcp'"),
            ):
                if name not in reference_columns:
                    c.execute(f"ALTER TABLE candidate_references ADD COLUMN {name} {definition}")

            candidate_columns = {r["name"] for r in c.execute("PRAGMA table_info(candidates)")}
            added_folder_path = "folder_path" not in candidate_columns
            if added_folder_path:
                c.execute("ALTER TABLE candidates ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''")
            added_sort_order = "sort_order" not in candidate_columns
            if added_sort_order:
                c.execute("ALTER TABLE candidates ADD COLUMN sort_order REAL NOT NULL DEFAULT 0")

            migrated = migrate_episodes(c)
            if migrated:
                self.event(c, "normalize_episodes", "workspace", {"variants": migrated})

            migration_applied = c.execute(
                "SELECT 1 FROM schema_migrations WHERE version=1"
            ).fetchone()
            if not migration_applied:
                next_orders = {}
                for row in c.execute(
                    "SELECT id,project_id,relative_path,folder_path,rowid FROM candidates ORDER BY rowid"
                ).fetchall():
                    folder_path = self._folder_path_from_relative(row["relative_path"])
                    if added_folder_path or row["folder_path"] != folder_path:
                        c.execute("UPDATE candidates SET folder_path=? WHERE id=?", (folder_path, row["id"]))
                    if added_sort_order:
                        c.execute("UPDATE candidates SET sort_order=? WHERE id=?", (float(row["rowid"]), row["id"]))
                    self._ensure_folder_records(c, row["project_id"], folder_path, next_orders)
                c.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(1,?)", (now(),)
                )

            c.execute("CREATE INDEX IF NOT EXISTS candidates_project_status ON candidates(project_id,status,deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS candidates_project_folder_order ON candidates(project_id,folder_path,sort_order,deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS candidate_folders_project_parent_order ON candidate_folders(project_id,parent_path,sort_order)")
            c.execute("CREATE INDEX IF NOT EXISTS candidate_folders_disabled_project ON candidate_folders(project_id) WHERE status='disabled'")
            c.execute("CREATE INDEX IF NOT EXISTS candidates_name ON candidates(name)")
            c.execute("CREATE INDEX IF NOT EXISTS candidates_file_id ON candidates(file_id)")
            c.execute("CREATE INDEX IF NOT EXISTS candidate_refs_candidate ON candidate_references(candidate_id)")
            c.execute("CREATE INDEX IF NOT EXISTS candidate_refs_asset ON candidate_references(asset_id,variant_id)")
            c.execute("CREATE INDEX IF NOT EXISTS candidate_refs_variant ON candidate_references(variant_id)")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS materials_candidate_asset ON materials(candidate_id,asset_id) WHERE candidate_id IS NOT NULL AND deleted_at IS NULL")
            c.execute("CREATE INDEX IF NOT EXISTS assets_project_active ON assets(project_id,deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS variants_asset_active ON variants(asset_id,deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS variants_episode_scope_missing ON variants(id) WHERE episode_scope IS NULL")
            c.execute("CREATE INDEX IF NOT EXISTS variants_final_material ON variants(final_material_id)")
            c.execute("CREATE INDEX IF NOT EXISTS materials_variant_active ON materials(variant_id,deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS materials_asset_active ON materials(asset_id,deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS materials_file_id ON materials(file_id)")
