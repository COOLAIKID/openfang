"""
Tests for autoearn.core.course_builder
"""

import json
import pytest
from autoearn.core import course_builder as cb


# ---------------------------------------------------------------------------
# Isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("autoearn.core.database.get_db_path", lambda: str(db_path))
    cb._schema_ready = False
    yield
    cb._schema_ready = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_course(**kwargs):
    defaults = dict(title="Test Course", description="A test course", price=49.99)
    defaults.update(kwargs)
    return cb.create_course(**defaults)


def _make_module(course_id, title="Module 1", **kwargs):
    return cb.add_module(course_id, title, **kwargs)


def _make_lesson(module_id, course_id, title="Lesson 1", **kwargs):
    return cb.add_lesson(module_id, course_id, title, **kwargs)


def _enroll(course_id, email="student@example.com", **kwargs):
    return cb.enroll_student(course_id, email, **kwargs)


# ===========================================================================
# 1. TestCourseCreation
# ===========================================================================

class TestCourseCreation:

    def test_create_course_returns_course_object(self):
        course = cb.create_course("Python Basics")
        assert course is not None
        assert course.title == "Python Basics"

    def test_create_course_has_id(self):
        course = cb.create_course("Python Basics")
        assert course.id is not None
        assert isinstance(course.id, int)
        assert course.id > 0

    def test_create_course_default_status_is_draft(self):
        course = cb.create_course("My Course")
        assert course.status == "draft"

    def test_create_course_default_price_is_zero(self):
        course = cb.create_course("Free Course")
        assert course.price == 0.0

    def test_create_course_with_price(self):
        course = cb.create_course("Paid Course", price=99.99)
        assert course.price == 99.99

    def test_create_course_with_description(self):
        course = cb.create_course("Course", description="Learn everything")
        assert course.description == "Learn everything"

    def test_create_course_with_level(self):
        course = cb.create_course("Advanced Course", level="advanced")
        assert course.level == "advanced"

    def test_create_course_default_level_is_beginner(self):
        course = cb.create_course("Beginner Course")
        assert course.level == "beginner"

    def test_create_course_with_category(self):
        course = cb.create_course("Dev Course", category="programming")
        assert course.category == "programming"

    def test_create_course_generates_slug(self):
        course = cb.create_course("My Awesome Course")
        assert course.slug
        assert "my-awesome-course" in course.slug

    def test_create_course_slug_is_unique(self):
        c1 = cb.create_course("Same Title")
        c2 = cb.create_course("Same Title")
        assert c1.slug != c2.slug

    def test_create_course_has_created_at(self):
        course = cb.create_course("Course")
        assert course.created_at
        assert len(course.created_at) > 0

    def test_get_course_by_id(self):
        course = cb.create_course("Test Course")
        fetched = cb.get_course(course.id)
        assert fetched is not None
        assert fetched.id == course.id
        assert fetched.title == "Test Course"

    def test_get_course_nonexistent_returns_none(self):
        result = cb.get_course(999999)
        assert result is None

    def test_get_course_by_slug(self):
        course = cb.create_course("Slug Course")
        fetched = cb.get_course_by_slug(course.slug)
        assert fetched is not None
        assert fetched.id == course.id

    def test_get_course_by_slug_nonexistent_returns_none(self):
        result = cb.get_course_by_slug("nonexistent-slug-xyz")
        assert result is None

    def test_list_courses_returns_list(self):
        cb.create_course("Course A")
        cb.create_course("Course B")
        courses = cb.list_courses()
        assert isinstance(courses, list)
        assert len(courses) >= 2

    def test_list_courses_empty_initially(self):
        courses = cb.list_courses()
        assert courses == []

    def test_create_course_effective_price_equals_price(self):
        course = cb.create_course("Course", price=50.0)
        assert course.effective_price == 50.0

    def test_create_course_effective_price_with_sale(self):
        course = cb.create_course("Course", price=100.0, sale_price=75.0)
        assert course.effective_price == 75.0

    def test_create_course_is_free_makes_effective_price_zero(self):
        course = cb.create_course("Free Course", price=100.0, is_free=True)
        assert course.effective_price == 0.0

    def test_completion_rate_no_enrollments(self):
        course = cb.create_course("Course")
        assert course.completion_rate == 0.0

    def test_course_to_dict_has_required_keys(self):
        course = cb.create_course("Course")
        d = course.to_dict()
        for key in ("id", "title", "slug", "status", "price", "level"):
            assert key in d


# ===========================================================================
# 2. TestCourseLifecycle
# ===========================================================================

class TestCourseLifecycle:

    def test_publish_course_sets_status_published(self):
        course = cb.create_course("My Course")
        result = cb.publish_course(course.id)
        assert result is True
        fetched = cb.get_course(course.id)
        assert fetched.status == "published"

    def test_update_course_title(self):
        course = cb.create_course("Old Title")
        cb.update_course(course.id, title="New Title")
        fetched = cb.get_course(course.id)
        assert fetched.title == "New Title"

    def test_update_course_price(self):
        course = cb.create_course("Course", price=10.0)
        cb.update_course(course.id, price=29.99)
        fetched = cb.get_course(course.id)
        assert fetched.price == 29.99

    def test_update_course_description(self):
        course = cb.create_course("Course")
        cb.update_course(course.id, description="Updated description")
        fetched = cb.get_course(course.id)
        assert fetched.description == "Updated description"

    def test_update_course_returns_true(self):
        course = cb.create_course("Course")
        result = cb.update_course(course.id, title="New")
        assert result is True

    def test_update_course_no_allowed_fields_returns_false(self):
        course = cb.create_course("Course")
        result = cb.update_course(course.id, nonexistent_field="value")
        assert result is False

    def test_delete_draft_course(self):
        course = cb.create_course("To Delete")
        result = cb.delete_course(course.id)
        assert result is True
        fetched = cb.get_course(course.id)
        assert fetched is None

    def test_delete_published_course_does_not_delete(self):
        course = cb.create_course("Published Course")
        cb.publish_course(course.id)
        cb.delete_course(course.id)
        # Published courses should not be deleted by delete_course
        fetched = cb.get_course(course.id)
        assert fetched is not None

    def test_list_courses_filter_by_status_draft(self):
        cb.create_course("Draft Course")
        pub = cb.create_course("Published Course")
        cb.publish_course(pub.id)
        drafts = cb.list_courses(status="draft")
        assert all(c.status == "draft" for c in drafts)

    def test_list_courses_filter_by_status_published(self):
        cb.create_course("Draft Course")
        pub = cb.create_course("Published Course")
        cb.publish_course(pub.id)
        published = cb.list_courses(status="published")
        assert all(c.status == "published" for c in published)
        assert len(published) == 1

    def test_list_courses_filter_by_level(self):
        cb.create_course("Beginner", level="beginner")
        cb.create_course("Advanced", level="advanced")
        advanced = cb.list_courses(level="advanced")
        assert all(c.level == "advanced" for c in advanced)

    def test_list_courses_limit(self):
        for i in range(5):
            cb.create_course(f"Course {i}")
        result = cb.list_courses(limit=3)
        assert len(result) <= 3

    def test_update_category(self):
        course = cb.create_course("Course")
        cb.update_course(course.id, category="design")
        fetched = cb.get_course(course.id)
        assert fetched.category == "design"

    def test_publish_course_sets_published_at(self):
        course = cb.create_course("Course")
        cb.publish_course(course.id)
        fetched = cb.get_course(course.id)
        assert fetched.published_at is not None


# ===========================================================================
# 3. TestModuleManagement
# ===========================================================================

class TestModuleManagement:

    def test_add_module_returns_dict(self):
        course = _make_course()
        module = cb.add_module(course.id, "Introduction")
        assert isinstance(module, dict)

    def test_add_module_has_id(self):
        course = _make_course()
        module = cb.add_module(course.id, "Introduction")
        assert "id" in module
        assert module["id"] > 0

    def test_add_module_has_correct_course_id(self):
        course = _make_course()
        module = cb.add_module(course.id, "Introduction")
        assert module["course_id"] == course.id

    def test_add_module_has_title(self):
        course = _make_course()
        module = cb.add_module(course.id, "Chapter 1")
        assert module["title"] == "Chapter 1"

    def test_add_module_with_sort_order(self):
        course = _make_course()
        module = cb.add_module(course.id, "Second Module", sort_order=2)
        assert module["sort_order"] == 2

    def test_add_multiple_modules(self):
        course = _make_course()
        m1 = cb.add_module(course.id, "Module 1", sort_order=1)
        m2 = cb.add_module(course.id, "Module 2", sort_order=2)
        m3 = cb.add_module(course.id, "Module 3", sort_order=3)
        assert m1["id"] != m2["id"]
        assert m2["id"] != m3["id"]

    def test_modules_appear_on_fetched_course(self):
        course = _make_course()
        cb.add_module(course.id, "Intro Module")
        fetched = cb.get_course(course.id)
        assert len(fetched.modules) == 1
        assert fetched.modules[0]["title"] == "Intro Module"

    def test_modules_ordered_by_sort_order(self):
        course = _make_course()
        cb.add_module(course.id, "Second", sort_order=2)
        cb.add_module(course.id, "First", sort_order=1)
        fetched = cb.get_course(course.id)
        titles = [m["title"] for m in fetched.modules]
        assert titles == ["First", "Second"]

    def test_add_module_with_description(self):
        course = _make_course()
        module = cb.add_module(course.id, "Module", description="Module description")
        # Module description stored; verify by fetching the course
        fetched = cb.get_course(course.id)
        assert fetched.modules[0]["description"] == "Module description"


# ===========================================================================
# 4. TestLessonManagement
# ===========================================================================

class TestLessonManagement:

    def test_add_lesson_returns_dict(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "First Lesson")
        assert isinstance(lesson, dict)

    def test_add_lesson_has_id(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        assert "id" in lesson
        assert lesson["id"] > 0

    def test_add_lesson_title(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "My Lesson")
        assert lesson["title"] == "My Lesson"

    def test_add_lesson_default_type_is_video(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        assert lesson["lesson_type"] == "video"

    def test_add_lesson_with_type_text(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Text Lesson", lesson_type="text")
        assert lesson["lesson_type"] == "text"

    def test_add_lesson_with_type_audio(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Audio Lesson", lesson_type="audio")
        assert lesson["lesson_type"] == "audio"

    def test_add_lesson_with_type_quiz(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Quiz Lesson", lesson_type="quiz")
        assert lesson["lesson_type"] == "quiz"

    def test_get_lesson_returns_dict(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        fetched = cb.get_lesson(lesson["id"])
        assert fetched is not None
        assert isinstance(fetched, dict)

    def test_get_lesson_has_correct_id(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        fetched = cb.get_lesson(lesson["id"])
        assert fetched["id"] == lesson["id"]

    def test_get_lesson_nonexistent_returns_none(self):
        result = cb.get_lesson(999999)
        assert result is None

    def test_update_lesson_title(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Old Title")
        result = cb.update_lesson(lesson["id"], title="New Title")
        assert result is True
        updated = cb.get_lesson(lesson["id"])
        assert updated["title"] == "New Title"

    def test_update_lesson_type(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson", lesson_type="video")
        cb.update_lesson(lesson["id"], lesson_type="text")
        updated = cb.get_lesson(lesson["id"])
        assert updated["lesson_type"] == "text"

    def test_update_lesson_no_allowed_fields_returns_false(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        result = cb.update_lesson(lesson["id"], fake_field="value")
        assert result is False

    def test_delete_lesson(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        result = cb.delete_lesson(lesson["id"])
        assert result is True
        assert cb.get_lesson(lesson["id"]) is None

    def test_lessons_appear_in_module_on_course_fetch(self):
        course = _make_course()
        module = _make_module(course.id)
        cb.add_lesson(module["id"], course.id, "Lesson A")
        cb.add_lesson(module["id"], course.id, "Lesson B")
        fetched = cb.get_course(course.id)
        lessons = fetched.modules[0]["lessons"]
        assert len(lessons) == 2

    def test_add_lesson_with_estimated_minutes(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson", estimated_minutes=30)
        assert lesson["estimated_minutes"] == 30

    def test_add_lesson_increments_course_lesson_count(self):
        course = _make_course()
        module = _make_module(course.id)
        cb.add_lesson(module["id"], course.id, "Lesson 1")
        cb.add_lesson(module["id"], course.id, "Lesson 2")
        fetched = cb.get_course(course.id)
        assert fetched.lesson_count == 2

    def test_add_lesson_with_content(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Text Lesson",
                               lesson_type="text", content="Hello world")
        fetched = cb.get_lesson(lesson["id"])
        assert fetched["content"] == "Hello world"


# ===========================================================================
# 5. TestQuizSystem
# ===========================================================================

class TestQuizSystem:

    def _setup_quiz(self, pass_percent=70.0):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Quiz Lesson", lesson_type="quiz")
        quiz = cb.add_quiz(lesson["id"], "Test Quiz", pass_percent=pass_percent)
        return quiz

    def test_add_quiz_returns_dict(self):
        quiz = self._setup_quiz()
        assert isinstance(quiz, dict)

    def test_add_quiz_has_id(self):
        quiz = self._setup_quiz()
        assert "id" in quiz
        assert quiz["id"] > 0

    def test_add_quiz_has_title(self):
        quiz = self._setup_quiz()
        assert quiz["title"] == "Test Quiz"

    def test_add_quiz_pass_percent(self):
        quiz = self._setup_quiz(pass_percent=80.0)
        assert quiz["pass_percent"] == 80.0

    def test_add_question_returns_dict(self):
        quiz = self._setup_quiz()
        q = cb.add_question(
            quiz["id"], "What is Python?",
            question_type="multiple_choice",
            options=["A language", "A snake", "A library", "A framework"],
            correct_answers=["A language"],
        )
        assert isinstance(q, dict)

    def test_add_question_has_id(self):
        quiz = self._setup_quiz()
        q = cb.add_question(quiz["id"], "Question text")
        assert "id" in q
        assert q["id"] > 0

    def test_add_question_stores_text(self):
        quiz = self._setup_quiz()
        q = cb.add_question(quiz["id"], "What is 2+2?")
        assert q["question_text"] == "What is 2+2?"

    def test_grade_quiz_correct_multiple_choice(self):
        quiz = self._setup_quiz(pass_percent=70.0)
        q = cb.add_question(
            quiz["id"], "Capital of France?",
            question_type="multiple_choice",
            options=["Berlin", "Paris", "London"],
            correct_answers=["Paris"],
        )
        result = cb.grade_quiz(quiz["id"], {q["id"]: "Paris"})
        assert result["passed"] is True
        assert result["score_pct"] == 100.0

    def test_grade_quiz_incorrect_multiple_choice(self):
        quiz = self._setup_quiz(pass_percent=70.0)
        q = cb.add_question(
            quiz["id"], "Capital of France?",
            question_type="multiple_choice",
            options=["Berlin", "Paris", "London"],
            correct_answers=["Paris"],
        )
        result = cb.grade_quiz(quiz["id"], {q["id"]: "Berlin"})
        assert result["passed"] is False
        assert result["score_pct"] == 0.0

    def test_grade_quiz_true_false_correct(self):
        quiz = self._setup_quiz(pass_percent=50.0)
        q = cb.add_question(
            quiz["id"], "Is Python a language?",
            question_type="true_false",
            correct_answers=["true"],
        )
        result = cb.grade_quiz(quiz["id"], {q["id"]: "true"})
        assert result["passed"] is True

    def test_grade_quiz_true_false_incorrect(self):
        quiz = self._setup_quiz(pass_percent=50.0)
        q = cb.add_question(
            quiz["id"], "Is Python a database?",
            question_type="true_false",
            correct_answers=["false"],
        )
        result = cb.grade_quiz(quiz["id"], {q["id"]: "true"})
        assert result["passed"] is False

    def test_grade_quiz_partial_score(self):
        quiz = self._setup_quiz(pass_percent=60.0)
        q1 = cb.add_question(
            quiz["id"], "Q1",
            question_type="multiple_choice",
            options=["A", "B"],
            correct_answers=["A"],
        )
        q2 = cb.add_question(
            quiz["id"], "Q2",
            question_type="multiple_choice",
            options=["C", "D"],
            correct_answers=["C"],
        )
        result = cb.grade_quiz(quiz["id"], {q1["id"]: "A", q2["id"]: "D"})
        assert result["score_pct"] == 50.0
        assert result["passed"] is False

    def test_grade_quiz_returns_results_list(self):
        quiz = self._setup_quiz()
        q = cb.add_question(quiz["id"], "Q?", correct_answers=["A"])
        result = cb.grade_quiz(quiz["id"], {q["id"]: "A"})
        assert "results" in result
        assert isinstance(result["results"], list)

    def test_grade_quiz_nonexistent_returns_error(self):
        result = cb.grade_quiz(999999, {})
        assert "error" in result

    def test_grade_quiz_empty_answers_scores_zero(self):
        quiz = self._setup_quiz(pass_percent=70.0)
        cb.add_question(quiz["id"], "Q?", correct_answers=["A"])
        result = cb.grade_quiz(quiz["id"], {})
        assert result["score_pct"] == 0.0
        assert result["passed"] is False


# ===========================================================================
# 6. TestEnrollment
# ===========================================================================

class TestEnrollment:

    def test_enroll_student_returns_dict(self):
        course = _make_course()
        result = cb.enroll_student(course.id, "alice@example.com")
        assert isinstance(result, dict)

    def test_enroll_student_has_enrollment_id(self):
        course = _make_course()
        result = cb.enroll_student(course.id, "alice@example.com")
        assert "enrollment_id" in result
        assert result["enrollment_id"] > 0

    def test_enroll_student_has_course_id(self):
        course = _make_course()
        result = cb.enroll_student(course.id, "alice@example.com")
        assert result["course_id"] == course.id

    def test_enroll_student_has_student_email(self):
        course = _make_course()
        result = cb.enroll_student(course.id, "alice@example.com")
        assert result["student_email"] == "alice@example.com"

    def test_enroll_student_has_enrolled_at(self):
        course = _make_course()
        result = cb.enroll_student(course.id, "alice@example.com")
        assert "enrolled_at" in result
        assert result["enrolled_at"]

    def test_get_enrollment_returns_dict(self):
        course = _make_course()
        cb.enroll_student(course.id, "bob@example.com")
        enrollment = cb.get_enrollment(course.id, "bob@example.com")
        assert enrollment is not None
        assert isinstance(enrollment, dict)

    def test_get_enrollment_not_enrolled_returns_none(self):
        course = _make_course()
        result = cb.get_enrollment(course.id, "nobody@example.com")
        assert result is None

    def test_get_enrollment_nonexistent_course_returns_none(self):
        result = cb.get_enrollment(999999, "student@example.com")
        assert result is None

    def test_duplicate_enrollment_does_not_raise(self):
        course = _make_course()
        cb.enroll_student(course.id, "carol@example.com")
        # Should not raise
        cb.enroll_student(course.id, "carol@example.com")

    def test_list_enrollments_by_course(self):
        course = _make_course()
        cb.enroll_student(course.id, "student1@example.com")
        cb.enroll_student(course.id, "student2@example.com")
        enrollments = cb.list_enrollments(course_id=course.id)
        assert len(enrollments) == 2

    def test_list_enrollments_by_email(self):
        c1 = _make_course(title="Course 1")
        c2 = _make_course(title="Course 2")
        cb.enroll_student(c1.id, "multi@example.com")
        cb.enroll_student(c2.id, "multi@example.com")
        enrollments = cb.list_enrollments(student_email="multi@example.com")
        assert len(enrollments) == 2

    def test_list_enrollments_empty_when_none(self):
        course = _make_course()
        enrollments = cb.list_enrollments(course_id=course.id)
        assert enrollments == []

    def test_enroll_with_amount_paid(self):
        course = _make_course()
        cb.enroll_student(course.id, "payer@example.com", amount_paid=49.99)
        enrollment = cb.get_enrollment(course.id, "payer@example.com")
        assert enrollment["amount_paid"] == 49.99

    def test_enroll_increments_enrollment_count(self):
        course = _make_course()
        cb.enroll_student(course.id, "s1@example.com")
        cb.enroll_student(course.id, "s2@example.com")
        fetched = cb.get_course(course.id)
        assert fetched.enrollment_count >= 2

    def test_enroll_with_student_name(self):
        course = _make_course()
        cb.enroll_student(course.id, "named@example.com", student_name="Alice Smith")
        enrollment = cb.get_enrollment(course.id, "named@example.com")
        assert enrollment["student_name"] == "Alice Smith"


# ===========================================================================
# 7. TestProgressTracking
# ===========================================================================

class TestProgressTracking:

    def _setup_course_with_lessons(self, n=3):
        course = _make_course()
        module = _make_module(course.id)
        lessons = []
        for i in range(n):
            lesson = cb.add_lesson(module["id"], course.id, f"Lesson {i+1}",
                                   sort_order=i)
            lessons.append(lesson)
        enrollment = cb.enroll_student(course.id, "progress@example.com")
        return course, module, lessons, enrollment

    def test_mark_lesson_complete_returns_dict(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        result = cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        assert isinstance(result, dict)

    def test_mark_lesson_complete_status_is_completed(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        result = cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        assert result["status"] == "completed"

    def test_mark_lesson_complete_updates_progress(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons(n=4)
        cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert progress["lessons_completed"] >= 1

    def test_get_student_progress_returns_dict(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert isinstance(progress, dict)

    def test_get_student_progress_initial_zero(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert progress["lessons_completed"] == 0
        assert progress["progress_pct"] == 0.0

    def test_get_student_progress_not_enrolled_returns_error(self):
        course = _make_course()
        result = cb.get_student_progress(course.id, "nobody@example.com")
        assert "error" in result

    def test_progress_percent_after_half_lessons(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons(n=4)
        cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[1]["id"])
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert progress["progress_pct"] == 50.0

    def test_progress_percent_full_completion(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons(n=2)
        for lesson in lessons:
            cb.mark_lesson_complete(enrollment["enrollment_id"], lesson["id"])
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert progress["progress_pct"] == 100.0

    def test_get_student_progress_has_lesson_progress_list(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert "lesson_progress" in progress
        assert isinstance(progress["lesson_progress"], list)

    def test_mark_lesson_complete_twice_does_not_error(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        # Second call should not raise
        result = cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        assert result["status"] == "completed"

    def test_enrollment_status_becomes_completed_on_full_completion(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons(n=1)
        cb.mark_lesson_complete(enrollment["enrollment_id"], lessons[0]["id"])
        enrolled = cb.get_enrollment(course.id, "progress@example.com")
        assert enrolled["status"] == "completed"

    def test_get_student_progress_has_enrollment_key(self):
        course, module, lessons, enrollment = self._setup_course_with_lessons()
        progress = cb.get_student_progress(course.id, "progress@example.com")
        assert "enrollment" in progress


# ===========================================================================
# 8. TestCertificates
# ===========================================================================

class TestCertificates:

    def _setup_completed_enrollment(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Only Lesson")
        enrollment = cb.enroll_student(course.id, "cert_student@example.com")
        cb.mark_lesson_complete(enrollment["enrollment_id"], lesson["id"])
        return course, enrollment

    def test_issue_certificate_returns_true(self):
        course, enrollment = self._setup_completed_enrollment()
        result = cb.issue_certificate(enrollment["enrollment_id"], "https://certs.example.com/abc123")
        assert result is True

    def test_issue_certificate_stores_url(self):
        course, enrollment = self._setup_completed_enrollment()
        cert_url = "https://certs.example.com/cert-xyz"
        cb.issue_certificate(enrollment["enrollment_id"], cert_url)
        stored = cb.get_enrollment(course.id, "cert_student@example.com")
        assert stored["certificate_url"] == cert_url

    def test_issue_certificate_for_incomplete_enrollment_still_stores(self):
        # The function stores certificate_url regardless of completion status
        course = _make_course()
        enrollment = cb.enroll_student(course.id, "incomplete@example.com")
        result = cb.issue_certificate(enrollment["enrollment_id"], "https://certs.example.com/early")
        assert result is True

    def test_certificate_url_initially_none(self):
        course = _make_course()
        enrollment = cb.enroll_student(course.id, "nocert@example.com")
        stored = cb.get_enrollment(course.id, "nocert@example.com")
        assert stored["certificate_url"] is None

    def test_issue_certificate_different_url(self):
        course, enrollment = self._setup_completed_enrollment()
        url1 = "https://certs.example.com/first"
        url2 = "https://certs.example.com/updated"
        cb.issue_certificate(enrollment["enrollment_id"], url1)
        cb.issue_certificate(enrollment["enrollment_id"], url2)
        stored = cb.get_enrollment(course.id, "cert_student@example.com")
        assert stored["certificate_url"] == url2

    def test_multiple_students_can_each_have_certificate(self):
        course = _make_course()
        module = _make_module(course.id)
        lesson = cb.add_lesson(module["id"], course.id, "Lesson")
        e1 = cb.enroll_student(course.id, "student_a@example.com")
        e2 = cb.enroll_student(course.id, "student_b@example.com")
        cb.mark_lesson_complete(e1["enrollment_id"], lesson["id"])
        cb.mark_lesson_complete(e2["enrollment_id"], lesson["id"])
        cb.issue_certificate(e1["enrollment_id"], "https://certs.example.com/a")
        cb.issue_certificate(e2["enrollment_id"], "https://certs.example.com/b")
        stored_a = cb.get_enrollment(course.id, "student_a@example.com")
        stored_b = cb.get_enrollment(course.id, "student_b@example.com")
        assert stored_a["certificate_url"] == "https://certs.example.com/a"
        assert stored_b["certificate_url"] == "https://certs.example.com/b"


# ===========================================================================
# 9. TestReviews
# ===========================================================================

class TestReviews:

    def test_add_review_returns_dict(self):
        course = _make_course()
        result = cb.add_course_review(course.id, 5, student_email="reviewer@example.com")
        assert isinstance(result, dict)

    def test_add_review_has_id(self):
        course = _make_course()
        result = cb.add_course_review(course.id, 4)
        assert "id" in result
        assert result["id"] > 0

    def test_add_review_stores_rating(self):
        course = _make_course()
        result = cb.add_course_review(course.id, 3)
        assert result["rating"] == 3

    def test_add_review_updates_avg_rating(self):
        course = _make_course()
        cb.add_course_review(course.id, 4)
        cb.add_course_review(course.id, 2)
        fetched = cb.get_course(course.id)
        assert fetched.avg_rating == 3.0

    def test_add_review_updates_review_count(self):
        course = _make_course()
        cb.add_course_review(course.id, 5)
        cb.add_course_review(course.id, 3)
        fetched = cb.get_course(course.id)
        assert fetched.review_count == 2

    def test_add_review_invalid_rating_raises(self):
        course = _make_course()
        with pytest.raises(ValueError):
            cb.add_course_review(course.id, 6)

    def test_add_review_rating_zero_raises(self):
        course = _make_course()
        with pytest.raises(ValueError):
            cb.add_course_review(course.id, 0)

    def test_add_review_with_body(self):
        course = _make_course()
        result = cb.add_course_review(course.id, 5, body="Excellent course!")
        assert result["id"] > 0  # review was created

    def test_add_review_with_title(self):
        course = _make_course()
        result = cb.add_course_review(course.id, 4, title="Pretty good")
        assert result["id"] > 0

    def test_analytics_shows_avg_rating(self):
        course = _make_course()
        cb.add_course_review(course.id, 5)
        cb.add_course_review(course.id, 3)
        analytics = cb.course_analytics(course.id)
        course_data = analytics["course"]
        assert course_data["avg_rating"] == 4.0

    def test_course_with_five_star_avg(self):
        course = _make_course()
        cb.add_course_review(course.id, 5)
        fetched = cb.get_course(course.id)
        assert fetched.avg_rating == 5.0


# ===========================================================================
# 10. TestToolFunctions
# ===========================================================================

class TestToolFunctions:

    def test_create_course_tool_returns_valid_json(self):
        result = cb.create_course_tool("Tool Course")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_create_course_tool_ok_true(self):
        result = cb.create_course_tool("Tool Course", description="desc", price=29.99)
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_create_course_tool_returns_course_dict(self):
        result = cb.create_course_tool("Tool Course")
        parsed = json.loads(result)
        assert "course" in parsed
        assert parsed["course"]["title"] == "Tool Course"

    def test_add_module_tool_returns_valid_json(self):
        course = cb.create_course_tool("Course")
        course_data = json.loads(course)["course"]
        result = cb.add_module_tool(course_data["id"], "Module A")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_add_module_tool_ok_true(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        result = cb.add_module_tool(course_data["id"], "Module A")
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_add_lesson_tool_returns_valid_json(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        module_data = json.loads(cb.add_module_tool(course_data["id"], "Mod"))["module"]
        result = cb.add_lesson_tool(module_data["id"], course_data["id"], "Lesson Title")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_add_lesson_tool_ok_true(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        module_data = json.loads(cb.add_module_tool(course_data["id"], "Mod"))["module"]
        result = cb.add_lesson_tool(module_data["id"], course_data["id"], "Lesson")
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_enroll_student_tool_returns_valid_json(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        result = cb.enroll_student_tool(course_data["id"], "tool_student@example.com")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_enroll_student_tool_ok_true(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        result = cb.enroll_student_tool(course_data["id"], "tool_student@example.com",
                                        student_name="Tool Student", amount_paid=0.0)
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_enroll_student_tool_has_enrollment_id(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        result = cb.enroll_student_tool(course_data["id"], "tool2@example.com")
        parsed = json.loads(result)
        assert "enrollment_id" in parsed

    def test_course_analytics_tool_returns_valid_json(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        result = cb.course_analytics_tool(course_data["id"])
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_course_analytics_tool_nonexistent_course(self):
        result = cb.course_analytics_tool(999999)
        parsed = json.loads(result)
        assert "error" in parsed

    def test_catalog_summary_tool_returns_valid_json(self):
        result = cb.catalog_summary_tool()
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_catalog_summary_tool_has_total_courses(self):
        cb.create_course_tool("Course A")
        cb.create_course_tool("Course B")
        result = cb.catalog_summary_tool()
        parsed = json.loads(result)
        assert "total_courses" in parsed
        assert parsed["total_courses"] >= 2

    def test_list_courses_tool_returns_valid_json(self):
        cb.create_course_tool("Listed Course")
        result = cb.list_courses_tool()
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_list_courses_tool_filter_by_status(self):
        result = cb.list_courses_tool(status="draft")
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_list_courses_tool_filter_by_level(self):
        cb.create_course_tool("Adv Course", level="advanced")
        result = cb.list_courses_tool(level="advanced")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert all(c["level"] == "advanced" for c in parsed)

    def test_student_progress_tool_returns_valid_json(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        cb.enroll_student_tool(course_data["id"], "progress_tool@example.com")
        result = cb.student_progress_tool(course_data["id"], "progress_tool@example.com")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_student_progress_tool_not_enrolled_returns_error_json(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        result = cb.student_progress_tool(course_data["id"], "noenroll@example.com")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_catalog_summary_tool_has_total_enrollments(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        cb.enroll_student_tool(course_data["id"], "s@example.com")
        result = cb.catalog_summary_tool()
        parsed = json.loads(result)
        assert "total_enrollments" in parsed

    def test_catalog_summary_tool_has_total_revenue(self):
        result = cb.catalog_summary_tool()
        parsed = json.loads(result)
        assert "total_revenue" in parsed

    def test_add_lesson_tool_with_type(self):
        course_data = json.loads(cb.create_course_tool("Course"))["course"]
        module_data = json.loads(cb.add_module_tool(course_data["id"], "Mod"))["module"]
        result = cb.add_lesson_tool(module_data["id"], course_data["id"], "Text Lesson",
                                    lesson_type="text")
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["lesson"]["lesson_type"] == "text"
