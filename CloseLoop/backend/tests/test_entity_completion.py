import unittest
from types import SimpleNamespace as NS

from app.routers.tasks import _entity_completion_reached


def _task(required, *done):
    return NS(is_required=required, entity_assignees=[NS(done=d) for d in done])


class EntityCompletionTests(unittest.TestCase):
    def test_optional_one_done_completes(self):
        self.assertTrue(_entity_completion_reached(_task(False, True, False, False)))

    def test_optional_none_done(self):
        self.assertFalse(_entity_completion_reached(_task(False, False, False, False)))

    def test_mandatory_needs_everyone(self):
        self.assertFalse(_entity_completion_reached(_task(True, True, False, False)))
        self.assertTrue(_entity_completion_reached(_task(True, True, True, True)))

    def test_no_assignees(self):
        self.assertFalse(_entity_completion_reached(_task(False)))
        self.assertFalse(_entity_completion_reached(_task(True)))


if __name__ == "__main__":
    unittest.main()
