"""SQLite persistence for meeting state, section archive and raw ASR.

Every rollout commits in one transaction, so a crash between the model call and
the commit leaves the previous state intact and the pending segments unconsumed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.meeting.models import (
    ASRSegment,
    MeetingSection,
    MeetingState,
    SectionIndexEntry,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0,
    current_section_id TEXT,
    next_segment_number INTEGER NOT NULL DEFAULT 1,
    next_section_number INTEGER NOT NULL DEFAULT 1,
    last_rollout_at REAL,
    finalized_at REAL,
    final_document TEXT
);
CREATE TABLE IF NOT EXISTS sections (
    meeting_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    PRIMARY KEY (meeting_id, section_id)
);
CREATE TABLE IF NOT EXISTS section_revisions (
    meeting_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    meeting_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    started_at REAL,
    ended_at REAL,
    speaker_id TEXT,
    consumed_section_id TEXT,
    consumed_at REAL,
    PRIMARY KEY (meeting_id, segment_id)
);
CREATE TABLE IF NOT EXISTS rollouts (
    meeting_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    metrics TEXT NOT NULL
);
"""


class ConcurrentStateUpdate(Exception):
    """Raised when a rollout tries to commit on top of a newer state version."""


@dataclass(slots=True)
class RolloutCommit:
    """One atomic state transition produced by the engine."""

    meeting_id: str
    expected_version: int
    current_section: MeetingSection
    section_position: int
    archived_section: MeetingSection | None = None
    consumed_segment_ids: list[str] = field(default_factory=list)
    next_section_number: int | None = None
    metrics: dict[str, object] = field(default_factory=dict)


class MeetingStore:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # -- meetings ---------------------------------------------------------

    def create_meeting(self, meeting_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO meetings (meeting_id, created_at) VALUES (?, ?)",
                (meeting_id, time.time()),
            )
            self._connection.commit()

    def meeting_exists(self, meeting_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM meetings WHERE meeting_id = ?", (meeting_id,)
            ).fetchone()
        return row is not None

    def list_meetings(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT meeting_id, created_at, state_version, finalized_at FROM meetings "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- segments ---------------------------------------------------------

    def append_segments(self, meeting_id: str, segments: list[dict[str, object]]) -> list[ASRSegment]:
        """Stores raw ASR verbatim and assigns canonical ids. Nothing is ever deleted here."""

        created: list[ASRSegment] = []
        with self._lock:
            row = self._connection.execute(
                "SELECT next_segment_number FROM meetings WHERE meeting_id = ?", (meeting_id,)
            ).fetchone()
            if row is None:
                raise KeyError(meeting_id)
            number = int(row["next_segment_number"])
            position = int(
                self._connection.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM segments WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()[0]
            )
            for raw in segments:
                text = str(raw.get("text") or "").strip()
                if not text:
                    continue
                position += 1
                segment = ASRSegment(
                    segment_id=f"s{number}",
                    text=text,
                    started_at=_optional_float(raw.get("start", raw.get("started_at"))),
                    ended_at=_optional_float(raw.get("end", raw.get("ended_at"))),
                    speaker_id=str(raw["speaker_id"]) if raw.get("speaker_id") else None,
                )
                number += 1
                self._connection.execute(
                    "INSERT INTO segments (meeting_id, segment_id, position, text, started_at, "
                    "ended_at, speaker_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        segment.segment_id,
                        position,
                        segment.text,
                        segment.started_at,
                        segment.ended_at,
                        segment.speaker_id,
                    ),
                )
                created.append(segment)
            self._connection.execute(
                "UPDATE meetings SET next_segment_number = ? WHERE meeting_id = ?",
                (number, meeting_id),
            )
            self._connection.commit()
        return created

    def transcript(self, meeting_id: str) -> list[ASRSegment]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT segment_id, text, started_at, ended_at, speaker_id FROM segments "
                "WHERE meeting_id = ? ORDER BY position",
                (meeting_id,),
            ).fetchall()
        return [ASRSegment(**dict(row)) for row in rows]

    # -- state ------------------------------------------------------------

    def load_state(self, meeting_id: str) -> MeetingState:
        with self._lock:
            meeting = self._connection.execute(
                "SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,)
            ).fetchone()
            if meeting is None:
                raise KeyError(meeting_id)
            current = None
            if meeting["current_section_id"]:
                current = self._load_section(meeting_id, meeting["current_section_id"])
            index_rows = self._connection.execute(
                "SELECT payload FROM sections WHERE meeting_id = ? AND archived = 1 ORDER BY position",
                (meeting_id,),
            ).fetchall()
            pending_rows = self._connection.execute(
                "SELECT segment_id, text, started_at, ended_at, speaker_id FROM segments "
                "WHERE meeting_id = ? AND consumed_section_id IS NULL ORDER BY position",
                (meeting_id,),
            ).fetchall()

        archived = [MeetingSection.from_dict(json.loads(row["payload"])) for row in index_rows]
        return MeetingState(
            meeting_id=meeting_id,
            current_section=current,
            section_index=[
                SectionIndexEntry(section.section_id, section.title, section.short_descriptor)
                for section in archived
            ],
            archived_section_ids=[section.section_id for section in archived],
            pending_segments=[ASRSegment(**dict(row)) for row in pending_rows],
            state_version=int(meeting["state_version"]),
            last_rollout_at=meeting["last_rollout_at"],
            next_segment_number=int(meeting["next_segment_number"]),
            next_section_number=int(meeting["next_section_number"]),
        )

    def _load_section(self, meeting_id: str, section_id: str) -> MeetingSection | None:
        row = self._connection.execute(
            "SELECT payload FROM sections WHERE meeting_id = ? AND section_id = ?",
            (meeting_id, section_id),
        ).fetchone()
        if row is None:
            return None
        return MeetingSection.from_dict(json.loads(row["payload"]))

    def get_section(self, meeting_id: str, section_id: str) -> MeetingSection | None:
        with self._lock:
            return self._load_section(meeting_id, section_id)

    def sections_in_order(self, meeting_id: str) -> list[MeetingSection]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM sections WHERE meeting_id = ? ORDER BY position", (meeting_id,)
            ).fetchall()
        return [MeetingSection.from_dict(json.loads(row["payload"])) for row in rows]

    def next_section_position(self, meeting_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(position), 0) FROM sections WHERE meeting_id = ?", (meeting_id,)
            ).fetchone()
        return int(row[0]) + 1

    def save_section_revision(self, meeting_id: str, section: MeetingSection, reason: str) -> None:
        """Keeps the pre-compaction copy so compaction never destroys the only copy."""

        with self._lock:
            self._connection.execute(
                "INSERT INTO section_revisions (meeting_id, section_id, created_at, reason, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    meeting_id,
                    section.section_id,
                    time.time(),
                    reason,
                    json.dumps(section.to_dict(), ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def section_revisions(self, meeting_id: str, section_id: str) -> list[MeetingSection]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM section_revisions WHERE meeting_id = ? AND section_id = ? "
                "ORDER BY created_at",
                (meeting_id, section_id),
            ).fetchall()
        return [MeetingSection.from_dict(json.loads(row["payload"])) for row in rows]

    def commit_rollout(self, commit: RolloutCommit) -> int:
        """Applies one state transition atomically and returns the new version."""

        now = time.time()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT state_version, next_section_number FROM meetings WHERE meeting_id = ?",
                    (commit.meeting_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(commit.meeting_id)
                if int(row["state_version"]) != commit.expected_version:
                    raise ConcurrentStateUpdate(
                        f"expected state_version {commit.expected_version}, found {row['state_version']}"
                    )

                if commit.archived_section is not None:
                    self._write_section(commit.meeting_id, commit.archived_section, archived=1)
                self._write_section(
                    commit.meeting_id,
                    commit.current_section,
                    archived=0,
                    position=commit.section_position,
                )
                for segment_id in commit.consumed_segment_ids:
                    self._connection.execute(
                        "UPDATE segments SET consumed_section_id = ?, consumed_at = ? "
                        "WHERE meeting_id = ? AND segment_id = ? AND consumed_section_id IS NULL",
                        (commit.current_section.section_id, now, commit.meeting_id, segment_id),
                    )
                version = commit.expected_version + 1
                self._connection.execute(
                    "UPDATE meetings SET state_version = ?, current_section_id = ?, "
                    "last_rollout_at = ?, next_section_number = ? WHERE meeting_id = ?",
                    (
                        version,
                        commit.current_section.section_id,
                        now,
                        commit.next_section_number
                        if commit.next_section_number is not None
                        else int(row["next_section_number"]),
                        commit.meeting_id,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO rollouts (meeting_id, state_version, created_at, metrics) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        commit.meeting_id,
                        version,
                        now,
                        json.dumps(commit.metrics, ensure_ascii=False),
                    ),
                )
                self._connection.commit()
                return version
            except Exception:
                self._connection.rollback()
                raise

    def _write_section(
        self, meeting_id: str, section: MeetingSection, archived: int, position: int | None = None
    ) -> None:
        payload = json.dumps(section.to_dict(), ensure_ascii=False)
        existing = self._connection.execute(
            "SELECT position FROM sections WHERE meeting_id = ? AND section_id = ?",
            (meeting_id, section.section_id),
        ).fetchone()
        if existing is None:
            if position is None:
                position = (
                    int(
                        self._connection.execute(
                            "SELECT COALESCE(MAX(position), 0) FROM sections WHERE meeting_id = ?",
                            (meeting_id,),
                        ).fetchone()[0]
                    )
                    + 1
                )
            self._connection.execute(
                "INSERT INTO sections (meeting_id, section_id, position, archived, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (meeting_id, section.section_id, position, archived, payload),
            )
            return
        self._connection.execute(
            "UPDATE sections SET archived = ?, payload = ? WHERE meeting_id = ? AND section_id = ?",
            (archived, payload, meeting_id, section.section_id),
        )

    def rollout_metrics(self, meeting_id: str, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT state_version, created_at, metrics FROM rollouts WHERE meeting_id = ? "
                "ORDER BY state_version DESC LIMIT ?",
                (meeting_id, limit),
            ).fetchall()
        return [
            {"state_version": row["state_version"], "created_at": row["created_at"], **json.loads(row["metrics"])}
            for row in rows
        ]

    def save_final_document(self, meeting_id: str, document: dict[str, object]) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE meetings SET finalized_at = ?, final_document = ? WHERE meeting_id = ?",
                (time.time(), json.dumps(document, ensure_ascii=False), meeting_id),
            )
            self._connection.commit()

    def final_document(self, meeting_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT final_document FROM meetings WHERE meeting_id = ?", (meeting_id,)
            ).fetchone()
        if row is None or not row["final_document"]:
            return None
        return json.loads(row["final_document"])


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
