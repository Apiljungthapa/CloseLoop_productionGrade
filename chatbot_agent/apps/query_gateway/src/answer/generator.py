from typing import Iterator

from shared.schemas.retrieval_result import RetrievedItem

from llm.client import LLMClient
from .prompt import SYSTEM_PROMPT, build_prompt


class AnswerGenerator:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def generate(self, question: str, evidence: list[RetrievedItem], extra_instruction: str = "") -> str:
        system = SYSTEM_PROMPT + (f"\n\nADDITIONAL INSTRUCTION\n{extra_instruction}" if extra_instruction else "")
        return self.client.generate(system, build_prompt(question, evidence))

    def stream(self, question: str, evidence: list[RetrievedItem], extra_instruction: str = "") -> Iterator[str]:
        system = SYSTEM_PROMPT + (f"\n\nADDITIONAL INSTRUCTION\n{extra_instruction}" if extra_instruction else "")
        return self.client.stream_generate(system, build_prompt(question, evidence))
