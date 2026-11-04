"""Engineering-artifact store on seekdb / OceanBase (MySQL protocol).

Uses FULLTEXT retrieval so query results actually feed the development context.
Configure via env:

  SEEKDB_MODE=mysql|sqlite   (default mysql — required for official scoring)
  SEEKDB_HOST (default 127.0.0.1)
  SEEKDB_PORT (default 2881)
  SEEKDB_USER (default root)
  SEEKDB_PASSWORD (default empty)
  SEEKDB_DATABASE (default sdd_minimax)

sqlite mode is only for offline plumbing checks; scoring needs seekdb/OB.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymysql


@dataclass
class ArtifactHit:
    kind: str
    path: str
    title: str
    content: str
    score_hint: str


class ArtifactDB:
    def __init__(self) -> None:
        self.mode = os.getenv("SEEKDB_MODE", "mysql").lower()
        self.host = os.getenv("SEEKDB_HOST", "127.0.0.1")
        self.port = int(os.getenv("SEEKDB_PORT", "2881"))
        self.user = os.getenv("SEEKDB_USER", "root")
        self.password = os.getenv("SEEKDB_PASSWORD", "")
        self.database = os.getenv("SEEKDB_DATABASE", "sdd_minimax")
        self.sqlite_path = Path(
            os.getenv(
                "SEEKDB_SQLITE_PATH",
                str(Path(__file__).resolve().parents[1] / "evidence" / "artifacts.sqlite3"),
            )
        )

    def _connect(self, with_db: bool = True):
        kwargs: dict[str, Any] = dict(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        if with_db:
            kwargs["database"] = self.database
        return pymysql.connect(**kwargs)

    def init_schema(self) -> None:
        if self.mode == "sqlite":
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute("DROP TABLE IF EXISTS artifacts")
                conn.execute(
                    """
                    CREATE TABLE artifacts (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      kind TEXT NOT NULL,
                      path TEXT NOT NULL UNIQUE,
                      title TEXT NOT NULL,
                      content TEXT NOT NULL
                    )
                    """
                )
            return
        with self._connect(with_db=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS artifacts")
                cur.execute(
                    """
                    CREATE TABLE artifacts (
                      id BIGINT PRIMARY KEY AUTO_INCREMENT,
                      kind VARCHAR(32) NOT NULL,
                      path VARCHAR(512) NOT NULL,
                      title VARCHAR(256) NOT NULL,
                      content LONGTEXT NOT NULL,
                      UNIQUE KEY uk_path (path),
                      FULLTEXT KEY ft_content (title, content)
                    ) ENGINE=InnoDB
                    """
                )

    def upsert(self, kind: str, path: str, title: str, content: str) -> None:
        if self.mode == "sqlite":
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute(
                    """
                    INSERT INTO artifacts (kind, path, title, content)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                      kind=excluded.kind,
                      title=excluded.title,
                      content=excluded.content
                    """,
                    (kind, path, title, content),
                )
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO artifacts (kind, path, title, content)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      kind=VALUES(kind),
                      title=VALUES(title),
                      content=VALUES(content)
                    """,
                    (kind, path, title, content),
                )

    def search(self, query: str, limit: int = 8) -> list[ArtifactHit]:
        tokens = [t for t in re.split(r"\W+", query) if len(t) >= 2]
        if self.mode == "sqlite":
            like = f"%{tokens[0] if tokens else query}%"
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT kind, path, title, content FROM artifacts
                    WHERE title LIKE ? OR content LIKE ? OR path LIKE ?
                    LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
            return [
                ArtifactHit(r["kind"], r["path"], r["title"], r["content"], "sqlite-like")
                for r in rows
            ]

        boolean = " ".join(f"+{t}*" for t in tokens) or query
        hits: list[ArtifactHit] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT kind, path, title, content,
                               MATCH(title, content) AGAINST (%s IN BOOLEAN MODE) AS score
                        FROM artifacts
                        WHERE MATCH(title, content) AGAINST (%s IN BOOLEAN MODE)
                        ORDER BY score DESC
                        LIMIT %s
                        """,
                        (boolean, boolean, limit),
                    )
                    for row in cur.fetchall():
                        hits.append(
                            ArtifactHit(
                                kind=row["kind"],
                                path=row["path"],
                                title=row["title"],
                                content=row["content"],
                                score_hint=f"ft:{row['score']:.4f}",
                            )
                        )
                except pymysql.Error:
                    pass
                if not hits:
                    like = f"%{tokens[0] if tokens else query}%"
                    cur.execute(
                        """
                        SELECT kind, path, title, content
                        FROM artifacts
                        WHERE title LIKE %s OR content LIKE %s OR path LIKE %s
                        LIMIT %s
                        """,
                        (like, like, like, limit),
                    )
                    for row in cur.fetchall():
                        hits.append(
                            ArtifactHit(
                                kind=row["kind"],
                                path=row["path"],
                                title=row["title"],
                                content=row["content"],
                                score_hint="like",
                            )
                        )
        return hits

    def dump_query_log(self, query: str) -> dict[str, Any]:
        hits = self.search(query)
        return {
            "engine": "sqlite-dev" if self.mode == "sqlite" else "seekdb/oceanbase-mysql",
            "mode": self.mode,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "query": query,
            "hit_count": len(hits),
            "hits": [
                {"kind": h.kind, "path": h.path, "title": h.title, "score": h.score_hint}
                for h in hits
            ],
        }
