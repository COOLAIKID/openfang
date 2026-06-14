"""
Course Builder — online course creation, module management, lessons, quizzes,
enrollment tracking, progress analytics, and certificate generation.

Supports digital course products with full curriculum management.
All data stored in SQLite via the shared database module.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LESSON_TYPES = [
    "video",
    "text",
    "audio",
    "quiz",
    "assignment",
    "live_session",
    "download",
    "external_link",
    "survey",
    "coding_exercise",
]

COURSE_STATUSES = ["draft", "review", "published", "archived"]

QUESTION_TYPES = [
    "multiple_choice",
    "true_false",
    "short_answer",
    "essay",
    "fill_blank",
    "matching",
    "ordering",
]

COMPLETION_RULES = ["lesson_viewed", "lesson_completed", "quiz_passed", "assignment_submitted"]

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_schema_ready = False


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


def _init_schema() -> None:
    conn = _db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS courses (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                slug                TEXT NOT NULL UNIQUE,
                title               TEXT NOT NULL,
                subtitle            TEXT,
                description         TEXT,
                instructor_name     TEXT NOT NULL DEFAULT 'AutoEarn Team',
                cover_image_url     TEXT,
                promo_video_url     TEXT,
                price               REAL NOT NULL DEFAULT 0.0,
                sale_price          REAL,
                currency            TEXT NOT NULL DEFAULT 'USD',
                level               TEXT NOT NULL DEFAULT 'beginner',
                language            TEXT NOT NULL DEFAULT 'en',
                category            TEXT,
                tags                TEXT NOT NULL DEFAULT '[]',
                status              TEXT NOT NULL DEFAULT 'draft',
                is_free             INTEGER NOT NULL DEFAULT 0,
                has_certificate     INTEGER NOT NULL DEFAULT 0,
                certificate_template TEXT,
                duration_hours      REAL NOT NULL DEFAULT 0.0,
                lesson_count        INTEGER NOT NULL DEFAULT 0,
                enrollment_count    INTEGER NOT NULL DEFAULT 0,
                completion_count    INTEGER NOT NULL DEFAULT 0,
                avg_rating          REAL NOT NULL DEFAULT 0.0,
                review_count        INTEGER NOT NULL DEFAULT 0,
                revenue_usd         REAL NOT NULL DEFAULT 0.0,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                published_at        TEXT,
                metadata            TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS course_modules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                description TEXT,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                is_preview  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS course_lessons (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id       INTEGER NOT NULL REFERENCES course_modules(id) ON DELETE CASCADE,
                course_id       INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                title           TEXT NOT NULL,
                lesson_type     TEXT NOT NULL DEFAULT 'video',
                content         TEXT,
                video_url       TEXT,
                video_duration_seconds INTEGER NOT NULL DEFAULT 0,
                audio_url       TEXT,
                file_url        TEXT,
                external_url    TEXT,
                sort_order      INTEGER NOT NULL DEFAULT 0,
                is_preview      INTEGER NOT NULL DEFAULT 0,
                is_free         INTEGER NOT NULL DEFAULT 0,
                completion_rule TEXT NOT NULL DEFAULT 'lesson_viewed',
                estimated_minutes INTEGER NOT NULL DEFAULT 10,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS course_quizzes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id       INTEGER NOT NULL REFERENCES course_lessons(id) ON DELETE CASCADE,
                title           TEXT NOT NULL,
                pass_percent    REAL NOT NULL DEFAULT 70.0,
                max_attempts    INTEGER NOT NULL DEFAULT 3,
                time_limit_mins INTEGER,
                randomize       INTEGER NOT NULL DEFAULT 0,
                show_answers    INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quiz_questions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id         INTEGER NOT NULL REFERENCES course_quizzes(id) ON DELETE CASCADE,
                question_type   TEXT NOT NULL DEFAULT 'multiple_choice',
                question_text   TEXT NOT NULL,
                points          REAL NOT NULL DEFAULT 1.0,
                sort_order      INTEGER NOT NULL DEFAULT 0,
                explanation     TEXT,
                options         TEXT NOT NULL DEFAULT '[]',
                correct_answers TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS course_enrollments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id       INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                student_email   TEXT NOT NULL,
                student_name    TEXT,
                status          TEXT NOT NULL DEFAULT 'active',
                enrolled_at     TEXT NOT NULL,
                completed_at    TEXT,
                last_accessed   TEXT,
                progress_pct    REAL NOT NULL DEFAULT 0.0,
                lessons_completed INTEGER NOT NULL DEFAULT 0,
                certificate_url TEXT,
                payment_ref     TEXT,
                amount_paid     REAL NOT NULL DEFAULT 0.0,
                UNIQUE(course_id, student_email)
            );

            CREATE TABLE IF NOT EXISTS lesson_progress (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                enrollment_id   INTEGER NOT NULL REFERENCES course_enrollments(id) ON DELETE CASCADE,
                lesson_id       INTEGER NOT NULL REFERENCES course_lessons(id),
                status          TEXT NOT NULL DEFAULT 'not_started',
                started_at      TEXT,
                completed_at    TEXT,
                watch_seconds   INTEGER NOT NULL DEFAULT 0,
                quiz_score      REAL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                UNIQUE(enrollment_id, lesson_id)
            );

            CREATE TABLE IF NOT EXISTS course_reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                enrollment_id INTEGER REFERENCES course_enrollments(id),
                student_email TEXT,
                rating      INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                title       TEXT,
                body        TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0,
                is_public   INTEGER NOT NULL DEFAULT 1,
                reviewed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_course_modules_course ON course_modules(course_id);
            CREATE INDEX IF NOT EXISTS idx_course_lessons_module ON course_lessons(module_id);
            CREATE INDEX IF NOT EXISTS idx_course_lessons_course ON course_lessons(course_id);
            CREATE INDEX IF NOT EXISTS idx_enrollments_course    ON course_enrollments(course_id);
            CREATE INDEX IF NOT EXISTS idx_enrollments_email     ON course_enrollments(student_email);
            CREATE INDEX IF NOT EXISTS idx_lesson_progress_enroll ON lesson_progress(enrollment_id);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Course:
    title: str
    slug: str = ""
    subtitle: str = ""
    description: str = ""
    instructor_name: str = "AutoEarn Team"
    price: float = 0.0
    sale_price: Optional[float] = None
    currency: str = "USD"
    level: str = "beginner"
    language: str = "en"
    category: str = ""
    tags: List[str] = field(default_factory=list)
    status: str = "draft"
    is_free: bool = False
    has_certificate: bool = False
    duration_hours: float = 0.0
    lesson_count: int = 0
    enrollment_count: int = 0
    completion_count: int = 0
    avg_rating: float = 0.0
    review_count: int = 0
    revenue_usd: float = 0.0
    created_at: str = ""
    updated_at: str = ""
    published_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    modules: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def effective_price(self) -> float:
        if self.is_free:
            return 0.0
        return self.sale_price if self.sale_price is not None else self.price

    @property
    def completion_rate(self) -> float:
        return self.completion_count / max(self.enrollment_count, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "subtitle": self.subtitle,
            "description": self.description,
            "instructor_name": self.instructor_name,
            "price": self.price,
            "sale_price": self.sale_price,
            "effective_price": self.effective_price,
            "is_free": self.is_free,
            "level": self.level,
            "language": self.language,
            "category": self.category,
            "tags": self.tags,
            "status": self.status,
            "has_certificate": self.has_certificate,
            "duration_hours": self.duration_hours,
            "lesson_count": self.lesson_count,
            "enrollment_count": self.enrollment_count,
            "completion_count": self.completion_count,
            "completion_rate": round(self.completion_rate * 100, 1),
            "avg_rating": self.avg_rating,
            "review_count": self.review_count,
            "revenue_usd": self.revenue_usd,
            "created_at": self.created_at,
            "published_at": self.published_at,
            "modules": self.modules,
        }


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _slug_from_title(title: str) -> str:
    slug = title.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    uid = uuid.uuid4().hex[:6]
    return f"{slug[:40]}-{uid}"


def _course_from_row(row: sqlite3.Row, modules: Optional[List] = None) -> Course:
    return Course(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        subtitle=row["subtitle"] or "",
        description=row["description"] or "",
        instructor_name=row["instructor_name"],
        price=row["price"],
        sale_price=row["sale_price"],
        currency=row["currency"],
        level=row["level"],
        language=row["language"],
        category=row["category"] or "",
        tags=json.loads(row["tags"] or "[]"),
        status=row["status"],
        is_free=bool(row["is_free"]),
        has_certificate=bool(row["has_certificate"]),
        duration_hours=row["duration_hours"],
        lesson_count=row["lesson_count"],
        enrollment_count=row["enrollment_count"],
        completion_count=row["completion_count"],
        avg_rating=row["avg_rating"],
        review_count=row["review_count"],
        revenue_usd=row["revenue_usd"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        published_at=row["published_at"],
        metadata=json.loads(row["metadata"] or "{}"),
        modules=modules or [],
    )


# ---------------------------------------------------------------------------
# Course CRUD
# ---------------------------------------------------------------------------

def create_course(
    title: str,
    subtitle: str = "",
    description: str = "",
    instructor_name: str = "AutoEarn Team",
    price: float = 0.0,
    sale_price: Optional[float] = None,
    level: str = "beginner",
    language: str = "en",
    category: str = "",
    tags: Optional[List[str]] = None,
    is_free: bool = False,
    has_certificate: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Course:
    """Create a new course."""
    _ensure()
    now = datetime.utcnow().isoformat()
    slug = _slug_from_title(title)
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO courses
               (slug, title, subtitle, description, instructor_name, price, sale_price,
                level, language, category, tags, is_free, has_certificate,
                created_at, updated_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                slug, title, subtitle, description, instructor_name, price, sale_price,
                level, language, category, json.dumps(tags or []),
                int(is_free), int(has_certificate), now, now,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return Course(
            id=cur.lastrowid,
            slug=slug,
            title=title,
            subtitle=subtitle,
            description=description,
            instructor_name=instructor_name,
            price=price,
            sale_price=sale_price,
            level=level,
            language=language,
            category=category,
            tags=tags or [],
            is_free=is_free,
            has_certificate=has_certificate,
            created_at=now,
            updated_at=now,
        )
    finally:
        conn.close()


def get_course(course_id: int) -> Optional[Course]:
    """Fetch a course by ID with all modules and lessons."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if not row:
            return None
        modules = _load_course_modules(conn, course_id)
        return _course_from_row(row, modules)
    finally:
        conn.close()


def get_course_by_slug(slug: str) -> Optional[Course]:
    """Fetch a course by slug."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM courses WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return None
        modules = _load_course_modules(conn, row["id"])
        return _course_from_row(row, modules)
    finally:
        conn.close()


def _load_course_modules(conn: sqlite3.Connection, course_id: int) -> List[Dict[str, Any]]:
    module_rows = conn.execute(
        "SELECT * FROM course_modules WHERE course_id = ? ORDER BY sort_order",
        (course_id,),
    ).fetchall()
    modules = []
    for m in module_rows:
        lesson_rows = conn.execute(
            "SELECT * FROM course_lessons WHERE module_id = ? ORDER BY sort_order",
            (m["id"],),
        ).fetchall()
        modules.append({
            **dict(m),
            "lessons": [dict(l) for l in lesson_rows],
        })
    return modules


def list_courses(
    status: Optional[str] = None,
    category: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 50,
) -> List[Course]:
    """List courses with optional filters."""
    _ensure()
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if level:
        clauses.append("level = ?")
        params.append(level)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM courses {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [_course_from_row(r) for r in rows]
    finally:
        conn.close()


def update_course(course_id: int, **kwargs) -> bool:
    """Update course fields."""
    _ensure()
    allowed = {
        "title", "subtitle", "description", "price", "sale_price", "level",
        "language", "category", "tags", "status", "is_free", "has_certificate",
        "cover_image_url", "promo_video_url", "metadata",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"])
    if "metadata" in updates:
        updates["metadata"] = json.dumps(updates["metadata"])
    updates["updated_at"] = datetime.utcnow().isoformat()
    if updates.get("status") == "published" and "published_at" not in updates:
        updates["published_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _db()
    try:
        conn.execute(
            f"UPDATE courses SET {set_clause} WHERE id = ?",
            (*updates.values(), course_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def publish_course(course_id: int) -> bool:
    """Publish a course."""
    return update_course(course_id, status="published")


def delete_course(course_id: int) -> bool:
    """Delete a draft course."""
    _ensure()
    conn = _db()
    try:
        conn.execute("DELETE FROM courses WHERE id = ? AND status = 'draft'", (course_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Modules and lessons
# ---------------------------------------------------------------------------

def add_module(
    course_id: int,
    title: str,
    description: str = "",
    sort_order: int = 0,
    is_preview: bool = False,
) -> Dict[str, Any]:
    """Add a module to a course."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO course_modules
               (course_id, title, description, sort_order, is_preview, created_at)
               VALUES (?,?,?,?,?,?)""",
            (course_id, title, description, sort_order, int(is_preview), now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "course_id": course_id,
            "title": title,
            "sort_order": sort_order,
            "is_preview": is_preview,
        }
    finally:
        conn.close()


def add_lesson(
    module_id: int,
    course_id: int,
    title: str,
    lesson_type: str = "video",
    content: str = "",
    video_url: str = "",
    video_duration_seconds: int = 0,
    audio_url: str = "",
    file_url: str = "",
    external_url: str = "",
    sort_order: int = 0,
    is_preview: bool = False,
    is_free: bool = False,
    estimated_minutes: int = 10,
    completion_rule: str = "lesson_viewed",
) -> Dict[str, Any]:
    """Add a lesson to a module."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO course_lessons
               (module_id, course_id, title, lesson_type, content, video_url,
                video_duration_seconds, audio_url, file_url, external_url,
                sort_order, is_preview, is_free, completion_rule, estimated_minutes,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                module_id, course_id, title, lesson_type, content, video_url,
                video_duration_seconds, audio_url, file_url, external_url,
                sort_order, int(is_preview), int(is_free), completion_rule,
                estimated_minutes, now, now,
            ),
        )
        conn.commit()
        # Update course lesson count and duration
        dur_mins = (video_duration_seconds / 60) if video_duration_seconds else estimated_minutes
        conn2 = _db()
        try:
            conn2.execute(
                """UPDATE courses
                   SET lesson_count = lesson_count + 1,
                       duration_hours = duration_hours + ?,
                       updated_at = ?
                   WHERE id = ?""",
                (dur_mins / 60, now, course_id),
            )
            conn2.commit()
        finally:
            conn2.close()
        return {
            "id": cur.lastrowid,
            "module_id": module_id,
            "course_id": course_id,
            "title": title,
            "lesson_type": lesson_type,
            "sort_order": sort_order,
            "estimated_minutes": estimated_minutes,
        }
    finally:
        conn.close()


def get_lesson(lesson_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a lesson by ID."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM course_lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_lesson(lesson_id: int, **kwargs) -> bool:
    """Update lesson fields."""
    _ensure()
    allowed = {
        "title", "lesson_type", "content", "video_url", "video_duration_seconds",
        "audio_url", "file_url", "external_url", "sort_order",
        "is_preview", "is_free", "estimated_minutes", "completion_rule",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _db()
    try:
        conn.execute(
            f"UPDATE course_lessons SET {set_clause} WHERE id = ?",
            (*updates.values(), lesson_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_lesson(lesson_id: int) -> bool:
    """Delete a lesson."""
    _ensure()
    conn = _db()
    try:
        conn.execute("DELETE FROM course_lessons WHERE id = ?", (lesson_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

def add_quiz(
    lesson_id: int,
    title: str,
    pass_percent: float = 70.0,
    max_attempts: int = 3,
    time_limit_mins: Optional[int] = None,
    randomize: bool = False,
    show_answers: bool = True,
) -> Dict[str, Any]:
    """Add a quiz to a lesson."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO course_quizzes
               (lesson_id, title, pass_percent, max_attempts, time_limit_mins,
                randomize, show_answers, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (lesson_id, title, pass_percent, max_attempts, time_limit_mins,
             int(randomize), int(show_answers), now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "lesson_id": lesson_id,
            "title": title,
            "pass_percent": pass_percent,
            "max_attempts": max_attempts,
        }
    finally:
        conn.close()


def add_question(
    quiz_id: int,
    question_text: str,
    question_type: str = "multiple_choice",
    options: Optional[List[str]] = None,
    correct_answers: Optional[List[Any]] = None,
    points: float = 1.0,
    explanation: str = "",
    sort_order: int = 0,
) -> Dict[str, Any]:
    """Add a question to a quiz."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO quiz_questions
               (quiz_id, question_type, question_text, points, sort_order,
                explanation, options, correct_answers)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                quiz_id, question_type, question_text, points, sort_order, explanation,
                json.dumps(options or []),
                json.dumps(correct_answers or []),
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "quiz_id": quiz_id,
            "question_text": question_text,
            "question_type": question_type,
            "options": options or [],
            "correct_answers": correct_answers or [],
        }
    finally:
        conn.close()


def grade_quiz(
    quiz_id: int,
    answers: Dict[int, Any],
) -> Dict[str, Any]:
    """Grade quiz answers. answers = {question_id: submitted_answer}."""
    _ensure()
    conn = _db()
    try:
        quiz = conn.execute(
            "SELECT * FROM course_quizzes WHERE id = ?", (quiz_id,)
        ).fetchone()
        if not quiz:
            return {"error": "quiz not found"}

        questions = conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY sort_order",
            (quiz_id,),
        ).fetchall()

        total_points = 0.0
        earned_points = 0.0
        results = []

        for q in questions:
            correct = json.loads(q["correct_answers"] or "[]")
            submitted = answers.get(q["id"])
            is_correct = False

            if q["question_type"] == "multiple_choice":
                is_correct = str(submitted) in [str(c) for c in correct]
            elif q["question_type"] == "true_false":
                is_correct = str(submitted).lower() == str(correct[0]).lower() if correct else False
            elif q["question_type"] == "short_answer":
                is_correct = str(submitted).strip().lower() in [str(c).lower() for c in correct]

            pt = q["points"]
            total_points += pt
            if is_correct:
                earned_points += pt

            results.append({
                "question_id": q["id"],
                "question_text": q["question_text"],
                "submitted": submitted,
                "is_correct": is_correct,
                "correct_answers": correct,
                "explanation": q["explanation"] or "",
                "points_earned": pt if is_correct else 0,
            })

        score_pct = (earned_points / total_points * 100) if total_points > 0 else 0
        passed = score_pct >= quiz["pass_percent"]

        return {
            "quiz_id": quiz_id,
            "score_pct": round(score_pct, 1),
            "earned_points": earned_points,
            "total_points": total_points,
            "passed": passed,
            "pass_threshold": quiz["pass_percent"],
            "results": results,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def enroll_student(
    course_id: int,
    student_email: str,
    student_name: str = "",
    amount_paid: float = 0.0,
    payment_ref: str = "",
) -> Dict[str, Any]:
    """Enroll a student in a course."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO course_enrollments
               (course_id, student_email, student_name, enrolled_at, amount_paid, payment_ref)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(course_id, student_email) DO UPDATE SET
                 status = 'active', amount_paid = excluded.amount_paid""",
            (course_id, student_email, student_name, now, amount_paid, payment_ref),
        )
        conn.execute(
            "UPDATE courses SET enrollment_count = enrollment_count + 1, revenue_usd = revenue_usd + ? WHERE id = ?",
            (amount_paid, course_id),
        )
        conn.commit()
        return {
            "enrollment_id": cur.lastrowid,
            "course_id": course_id,
            "student_email": student_email,
            "enrolled_at": now,
        }
    finally:
        conn.close()


def get_enrollment(course_id: int, student_email: str) -> Optional[Dict[str, Any]]:
    """Get a student's enrollment record."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM course_enrollments WHERE course_id = ? AND student_email = ?",
            (course_id, student_email),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_enrollments(
    course_id: Optional[int] = None,
    student_email: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List enrollments."""
    _ensure()
    clauses, params = [], []
    if course_id:
        clauses.append("course_id = ?")
        params.append(course_id)
    if student_email:
        clauses.append("student_email = ?")
        params.append(student_email)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM course_enrollments {where} ORDER BY enrolled_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_lesson_complete(
    enrollment_id: int,
    lesson_id: int,
    watch_seconds: int = 0,
    quiz_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Mark a lesson as completed for an enrollment."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        existing = conn.execute(
            "SELECT * FROM lesson_progress WHERE enrollment_id = ? AND lesson_id = ?",
            (enrollment_id, lesson_id),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE lesson_progress
                   SET status = 'completed', completed_at = ?,
                       watch_seconds = ?, quiz_score = ?, attempts = attempts + 1
                   WHERE enrollment_id = ? AND lesson_id = ?""",
                (now, watch_seconds, quiz_score, enrollment_id, lesson_id),
            )
        else:
            conn.execute(
                """INSERT INTO lesson_progress
                   (enrollment_id, lesson_id, status, started_at, completed_at, watch_seconds, quiz_score, attempts)
                   VALUES (?,?,?,?,?,?,?,1)""",
                (enrollment_id, lesson_id, "completed", now, now, watch_seconds, quiz_score),
            )

        # Recalculate progress
        enrollment = conn.execute(
            "SELECT course_id FROM course_enrollments WHERE id = ?", (enrollment_id,)
        ).fetchone()
        if enrollment:
            course_id = enrollment["course_id"]
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM course_lessons WHERE course_id = ?",
                (course_id,),
            ).fetchone()["cnt"] or 1
            completed = conn.execute(
                """SELECT COUNT(*) as cnt FROM lesson_progress lp
                   JOIN course_lessons cl ON cl.id = lp.lesson_id
                   WHERE lp.enrollment_id = ? AND lp.status = 'completed'
                   AND cl.course_id = ?""",
                (enrollment_id, course_id),
            ).fetchone()["cnt"] or 0
            progress = completed / total * 100
            is_complete = progress >= 100

            conn.execute(
                """UPDATE course_enrollments
                   SET progress_pct = ?, lessons_completed = ?,
                       last_accessed = ?,
                       status = CASE WHEN ? THEN 'completed' ELSE status END,
                       completed_at = CASE WHEN ? AND completed_at IS NULL THEN ? ELSE completed_at END
                   WHERE id = ?""",
                (progress, completed, now, int(is_complete), int(is_complete), now, enrollment_id),
            )
            if is_complete:
                conn.execute(
                    "UPDATE courses SET completion_count = completion_count + 1 WHERE id = ?",
                    (course_id,),
                )
        conn.commit()
        return {
            "enrollment_id": enrollment_id,
            "lesson_id": lesson_id,
            "status": "completed",
            "quiz_score": quiz_score,
        }
    finally:
        conn.close()


def get_student_progress(
    course_id: int,
    student_email: str,
) -> Dict[str, Any]:
    """Get a student's detailed progress in a course."""
    _ensure()
    enrollment = get_enrollment(course_id, student_email)
    if not enrollment:
        return {"error": "Student not enrolled"}

    conn = _db()
    try:
        progress_rows = conn.execute(
            """SELECT lp.*, cl.title as lesson_title, cl.lesson_type, cl.estimated_minutes
               FROM lesson_progress lp
               JOIN course_lessons cl ON cl.id = lp.lesson_id
               WHERE lp.enrollment_id = ?
               ORDER BY cl.sort_order""",
            (enrollment["id"],),
        ).fetchall()
        return {
            "enrollment": enrollment,
            "progress_pct": enrollment["progress_pct"],
            "lessons_completed": enrollment["lessons_completed"],
            "status": enrollment["status"],
            "lesson_progress": [dict(r) for r in progress_rows],
        }
    finally:
        conn.close()


def issue_certificate(
    enrollment_id: int,
    certificate_url: str,
) -> bool:
    """Issue a completion certificate."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE course_enrollments SET certificate_url = ? WHERE id = ?",
            (certificate_url, enrollment_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def add_course_review(
    course_id: int,
    rating: int,
    student_email: str = "",
    title: str = "",
    body: str = "",
    is_verified: bool = False,
) -> Dict[str, Any]:
    """Add a student review for a course."""
    _ensure()
    if not 1 <= rating <= 5:
        raise ValueError("Rating must be 1-5")
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO course_reviews
               (course_id, student_email, rating, title, body, is_verified, reviewed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (course_id, student_email, rating, title, body, int(is_verified), now),
        )
        # Update avg rating
        avg_row = conn.execute(
            "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM course_reviews WHERE course_id = ? AND is_public = 1",
            (course_id,),
        ).fetchone()
        conn.execute(
            "UPDATE courses SET avg_rating = ?, review_count = ? WHERE id = ?",
            (round(avg_row["avg"] or 0, 2), avg_row["cnt"] or 0, course_id),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "course_id": course_id,
            "rating": rating,
            "title": title,
            "reviewed_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def course_analytics(course_id: int) -> Dict[str, Any]:
    """Full analytics for a course."""
    _ensure()
    course = get_course(course_id)
    if not course:
        return {"error": f"Course {course_id} not found"}

    conn = _db()
    try:
        # Completion by module
        module_rows = conn.execute(
            """SELECT cm.title,
                      COUNT(DISTINCT ce.id) as enrolled,
                      COUNT(DISTINCT CASE WHEN lp.status = 'completed' THEN ce.id END) as completed_any
               FROM course_modules cm
               LEFT JOIN course_lessons cl ON cl.module_id = cm.id
               LEFT JOIN lesson_progress lp ON lp.lesson_id = cl.id
               LEFT JOIN course_enrollments ce ON ce.id = lp.enrollment_id
               WHERE cm.course_id = ?
               GROUP BY cm.id""",
            (course_id,),
        ).fetchall()

        # Revenue over time (monthly)
        rev_rows = conn.execute(
            """SELECT strftime('%Y-%m', enrolled_at) as month,
                      SUM(amount_paid) as revenue,
                      COUNT(*) as enrollments
               FROM course_enrollments WHERE course_id = ?
               GROUP BY month ORDER BY month""",
            (course_id,),
        ).fetchall()

        # Drop-off analysis
        drop_rows = conn.execute(
            """SELECT cl.title, cl.sort_order,
                      COUNT(DISTINCT lp.enrollment_id) as started,
                      COUNT(DISTINCT CASE WHEN lp.status = 'completed' THEN lp.enrollment_id END) as completed
               FROM course_lessons cl
               LEFT JOIN lesson_progress lp ON lp.lesson_id = cl.id
               WHERE cl.course_id = ?
               GROUP BY cl.id ORDER BY cl.sort_order""",
            (course_id,),
        ).fetchall()

        return {
            "course": course.to_dict(),
            "module_completion": [dict(r) for r in module_rows],
            "monthly_revenue": [dict(r) for r in rev_rows],
            "lesson_dropoff": [dict(r) for r in drop_rows],
        }
    finally:
        conn.close()


def course_catalog_summary() -> Dict[str, Any]:
    """High-level summary of all courses."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) as published,
                   SUM(enrollment_count) as total_enrollments,
                   SUM(completion_count) as total_completions,
                   SUM(revenue_usd) as total_revenue,
                   AVG(avg_rating) as avg_rating
               FROM courses"""
        ).fetchone()
        level_rows = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM courses GROUP BY level ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total_courses": row["total"] or 0,
            "published": row["published"] or 0,
            "total_enrollments": row["total_enrollments"] or 0,
            "total_completions": row["total_completions"] or 0,
            "total_revenue": round(row["total_revenue"] or 0, 2),
            "avg_rating": round(row["avg_rating"] or 0, 2),
            "by_level": [dict(r) for r in level_rows],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("cb_create_course", "Create a new online course")
def create_course_tool(
    title: str,
    description: str = "",
    price: float = 0.0,
    level: str = "beginner",
    category: str = "",
) -> str:
    try:
        course = create_course(
            title=title, description=description, price=price,
            level=level, category=category,
        )
        return json.dumps({"ok": True, "course": course.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cb_add_module", "Add a module to a course")
def add_module_tool(course_id: int, title: str, description: str = "", sort_order: int = 0) -> str:
    try:
        mod = add_module(course_id, title, description, sort_order)
        return json.dumps({"ok": True, "module": mod})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cb_add_lesson", "Add a lesson to a course module")
def add_lesson_tool(
    module_id: int,
    course_id: int,
    title: str,
    lesson_type: str = "video",
    content: str = "",
    estimated_minutes: int = 10,
) -> str:
    try:
        lesson = add_lesson(module_id, course_id, title, lesson_type, content,
                            estimated_minutes=estimated_minutes)
        return json.dumps({"ok": True, "lesson": lesson})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cb_enroll_student", "Enroll a student in a course")
def enroll_student_tool(
    course_id: int,
    student_email: str,
    student_name: str = "",
    amount_paid: float = 0.0,
) -> str:
    try:
        result = enroll_student(course_id, student_email, student_name, amount_paid)
        return json.dumps({"ok": True, **result})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cb_course_analytics", "Get analytics for a specific course")
def course_analytics_tool(course_id: int) -> str:
    try:
        return json.dumps(course_analytics(course_id), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("cb_catalog_summary", "High-level summary of all courses")
def catalog_summary_tool() -> str:
    try:
        return json.dumps(course_catalog_summary(), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("cb_list_courses", "List all courses with optional status/level filter")
def list_courses_tool(status: str = "", level: str = "", limit: int = 20) -> str:
    try:
        courses = list_courses(status=status or None, level=level or None, limit=limit)
        return json.dumps([c.to_dict() for c in courses], default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("cb_student_progress", "Get a student's progress in a course")
def student_progress_tool(course_id: int, student_email: str) -> str:
    try:
        return json.dumps(get_student_progress(course_id, student_email), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
