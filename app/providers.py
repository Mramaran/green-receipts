import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    reference_provider: str
    reference_model: str
    is_mock: bool


class MockProvider:
    """Deterministic stand-in for a hosted LLM.

    Impact is estimated as if the answer came from the reference model, and the
    latency is simulated, so receipts from this provider say so.
    """

    name = "mock"
    reference_provider = "openai"
    reference_model = "gpt-4o-mini"

    def complete(self, prompt: str) -> Completion:
        input_tokens = max(1, math.ceil(len(prompt) / 4))
        output_tokens = min(400, max(40, input_tokens * 3))
        text = ("Simulated answer. " * (output_tokens // 3 + 1))[: output_tokens * 4]
        latency_s = round(0.5 + output_tokens * 0.015, 3)
        return Completion(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_s=latency_s,
            reference_provider=self.reference_provider,
            reference_model=self.reference_model,
            is_mock=True,
        )
