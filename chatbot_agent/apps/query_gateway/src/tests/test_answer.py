from answer.repair_loop import NOT_FOUND_ANSWER, generate_with_repair
from answer.validator import validate_answer


class Gen:
    def __init__(self, replies):
        self.replies, self.instructions = list(replies), []

    def generate(self, question, evidence, extra_instruction=""):
        self.instructions.append(extra_instruction)
        return self.replies.pop(0)


def test_validator():
    assert validate_answer("30 days.")[0]
    assert not validate_answer("")[0]
    assert not validate_answer("According to the context excerpts, 30 days.")[0]


def test_repair_retries_once_with_reason():
    gen = Gen(["Per the provided context, 30 days.", "You have 30 days."])
    assert generate_with_repair(gen, "q", []) == "You have 30 days."
    assert gen.instructions[0] == "" and gen.instructions[1]


def test_falls_back_when_repair_fails():
    assert generate_with_repair(Gen(["", ""]), "q", []) == NOT_FOUND_ANSWER
