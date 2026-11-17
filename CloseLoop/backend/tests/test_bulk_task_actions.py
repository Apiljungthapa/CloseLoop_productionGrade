import unittest

from pydantic import ValidationError

from app.routers.tasks import router
from app.schemas import BULK_TASK_LIMIT, TaskBulkAssignIn, TaskBulkReviewIn


class BulkSchemaTests(unittest.TestCase):
    def test_assign_dedupes_and_defaults(self):
        p = TaskBulkAssignIn(task_ids=["a", "b", "a", ""], user_ids=["u", "u"])
        self.assertEqual(p.task_ids, ["a", "b"])
        self.assertEqual(p.user_ids, ["u"])
        self.assertEqual(p.scope, "occurrence")

    def test_assign_empty_users_means_unassign(self):
        self.assertEqual(TaskBulkAssignIn(task_ids=["a"]).user_ids, [])

    def test_assign_requires_tasks(self):
        with self.assertRaises(ValidationError):
            TaskBulkAssignIn(task_ids=[])

    def test_assign_caps_batch_size(self):
        ids = [str(i) for i in range(BULK_TASK_LIMIT + 1)]
        with self.assertRaises(ValidationError):
            TaskBulkAssignIn(task_ids=ids)
        TaskBulkAssignIn(task_ids=ids[:BULK_TASK_LIMIT])

    def test_assign_rejects_unknown_scope(self):
        with self.assertRaises(ValidationError):
            TaskBulkAssignIn(task_ids=["a"], scope="everything")

    def test_review_dedupes(self):
        p = TaskBulkReviewIn(task_ids=["a", "a"], decision="APPROVE")
        self.assertEqual(p.task_ids, ["a"])


class BulkRouteTests(unittest.TestCase):
    def test_bulk_routes_registered(self):
        paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
        self.assertIn(("/api/tasks/bulk-assign", ("POST",)), paths)
        self.assertIn(("/api/tasks/bulk-review", ("POST",)), paths)


if __name__ == "__main__":
    unittest.main()
