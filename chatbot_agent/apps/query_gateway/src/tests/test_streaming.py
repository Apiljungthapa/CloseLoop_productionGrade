import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_workflow
from app.routes import workflow as workflow_routes
from core.config import Settings
from core.exceptions import LLMUnavailable, RateLimited
from llm import providers
from llm.client import LLMClient
from shared.schemas.retrieval_result import RetrievedItem
from workflow.workflow import DbResult, WorkflowDeps, build_workflow, stream_workflow


def cfg(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


ALL = dict(
    GEMINI_API_KEY="g", GEMINI_MODEL="gem", OPENAI_API_KEY="o", OPENAI_MODEL="gpt-4.1-mini",
    ANTHROPIC_API_KEY="a", ANTHROPIC_MODEL="claude-sonnet-5-5",
)


class StreamResp:
    def __init__(self, status=200, lines=(), text="", headers=None, fail_after=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self._lines, self.closed, self._fail_after = list(lines), False, fail_after

    def iter_lines(self, chunk_size=512):
        self.chunk_size = chunk_size
        for index, line in enumerate(self._lines):
            if self._fail_after is not None and index == self._fail_after:
                raise providers.requests.ConnectionError("connection reset")
            yield line

    def close(self):
        self.closed = True


class Net:
    def __init__(self, monkeypatch, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls, self.responses = [], []
        monkeypatch.setattr(providers.requests, "post", self.post)
        monkeypatch.setattr(providers.time, "sleep", lambda _s: None)

    def post(self, url, headers=None, json=None, timeout=None, stream=False):
        self.calls.append({"url": url, "headers": headers or {}, "body": json, "stream": stream})
        for key, queue in self.routes.items():
            if key in url:
                response = queue.pop(0) if len(queue) > 1 else queue[0]
                self.responses.append(response)
                return response
        raise AssertionError(url)


def sse_lines(*events):
    out = []
    for event in events:
        out += [f"data: {json.dumps(event)}".encode(), b""]
    return out


GEMINI_LINES = sse_lines(
    {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
    {"candidates": [{"content": {"parts": [{"text": "lo"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}},
)
OPENAI_LINES = sse_lines(
    {"choices": [{"delta": {"role": "assistant"}}]},
    {"choices": [{"delta": {"content": "Hel"}}]},
    {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
) + [b"data: [DONE]"]
CLAUDE_LINES = [b"event: message_start"] + sse_lines(
    {"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
    {"type": "ping"},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
    {"type": "message_delta", "usage": {"output_tokens": 2}},
    {"type": "message_stop"},
)


# ---- provider streaming ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("provider", "host", "lines"),
    [("gemini", "generativelanguage", GEMINI_LINES), ("openai", "api.openai.com", OPENAI_LINES),
     ("anthropic", "api.anthropic.com", CLAUDE_LINES)],
)
def test_each_vendor_streams_text_in_pieces(monkeypatch, provider, host, lines):
    response = StreamResp(lines=lines)
    net = Net(monkeypatch, {host: [response]})

    pieces = list(LLMClient(cfg(LLM_PROVIDER=provider, **ALL)).stream_generate("sys", "usr", temperature=0))

    assert pieces == ["Hel", "lo"] and response.closed and net.calls[0]["stream"] is True


def test_stream_request_shapes(monkeypatch):
    net = Net(monkeypatch, {"generativelanguage": [StreamResp(lines=GEMINI_LINES)],
                            "api.openai.com": [StreamResp(lines=OPENAI_LINES)],
                            "api.anthropic.com": [StreamResp(lines=CLAUDE_LINES)]})
    for provider in ("gemini", "openai", "anthropic"):
        list(LLMClient(cfg(LLM_PROVIDER=provider, **ALL)).stream_generate("s", "u"))

    gemini, openai, claude = (c for c in net.calls)
    assert gemini["url"].endswith(":streamGenerateContent?alt=sse")
    assert openai["body"]["stream"] is True and openai["body"]["stream_options"] == {"include_usage": True}
    assert claude["body"]["stream"] is True


def test_compatible_servers_do_not_get_openai_only_options(monkeypatch):
    net = Net(monkeypatch, {"11434": [StreamResp(lines=OPENAI_LINES)]})
    client = LLMClient(cfg(LLM_PROVIDER="ollama", LLM_COMPAT_BASE_URL="http://h:11434/v1", LLM_COMPAT_MODEL="llama"))

    assert "".join(client.stream_generate("s", "u")) == "Hello"
    assert "stream_options" not in net.calls[0]["body"]


def test_closing_the_stream_early_closes_the_vendor_connection(monkeypatch):
    response = StreamResp(lines=OPENAI_LINES)
    Net(monkeypatch, {"api.openai.com": [response]})
    stream = LLMClient(cfg(LLM_PROVIDER="openai", **ALL)).stream_generate("s", "u")

    assert next(stream) == "Hel"
    assert not response.closed
    stream.close()  # what happens when the user stops / disconnects

    assert response.closed


def test_failure_before_the_first_piece_fails_over(monkeypatch):
    bad = StreamResp(503, text="overloaded")
    Net(monkeypatch, {"generativelanguage": [bad], "api.anthropic.com": [StreamResp(lines=CLAUDE_LINES)]})
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="anthropic", **ALL))

    assert "".join(client.stream_generate("s", "u")) == "Hello"
    assert bad.closed


def test_rate_limit_before_streaming_fails_over_and_parks_the_provider(monkeypatch):
    quota = StreamResp(429, text='{"retryDelay": "39s"}')
    net = Net(monkeypatch, {"generativelanguage": [quota], "api.openai.com": [StreamResp(lines=OPENAI_LINES)]})
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    assert "".join(client.stream_generate("s", "u")) == "Hello"
    assert "".join(client.stream_generate("s", "u")) == "Hello"
    assert sum("generativelanguage" in c["url"] for c in net.calls) == 1


def test_failure_after_text_started_is_raised_not_spliced_with_another_model(monkeypatch):
    broken = StreamResp(lines=GEMINI_LINES, fail_after=2)  # dies after the first two lines
    net = Net(monkeypatch, {"generativelanguage": [broken], "api.openai.com": [StreamResp(lines=OPENAI_LINES)]})
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))
    received = []

    with pytest.raises(LLMUnavailable, match="interrupted"):
        for piece in client.stream_generate("s", "u"):
            received.append(piece)

    assert received == ["Hel"] and not any("api.openai.com" in c["url"] for c in net.calls)


def test_empty_stream_counts_as_a_failure_and_fails_over(monkeypatch):
    Net(monkeypatch, {"generativelanguage": [StreamResp(lines=[b"data: {}", b""])],
                      "api.openai.com": [StreamResp(lines=OPENAI_LINES)]})
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    assert "".join(client.stream_generate("s", "u")) == "Hello"


def test_all_providers_rate_limited_raises_429_error(monkeypatch):
    Net(monkeypatch, {"generativelanguage": [StreamResp(429, text='{"retryDelay": "39s"}')]})

    with pytest.raises(RateLimited) as error:
        list(LLMClient(cfg(LLM_PROVIDER="gemini", **ALL)).stream_generate("s", "u"))

    assert error.value.retry_after == 39


# ---- graph events ----------------------------------------------------------------------------------------
def router(route):
    return json.dumps({"route": route, "reason": "test", "doc_query": None, "db_query": None})


class FakeGenerator:
    def __init__(self, *streams):
        self.streams, self.calls, self.closed = list(streams), [], []

    def stream(self, question, evidence, extra_instruction=""):
        self.calls.append(extra_instruction)
        pieces = self.streams.pop(0) if len(self.streams) > 1 else self.streams[0]
        outer = self

        def gen():
            try:
                yield from pieces
            finally:
                outer.closed.append(True)

        return gen()

    def generate(self, *a, **k):
        return "plain answer"


def make_graph(route="doc", generator=None, db=None, docs=1):
    generator = generator or FakeGenerator(["Hello ", "world."])
    deps = WorkflowDeps(
        llm_generate=lambda s, u, temperature=0.3: router(route),
        search_docs=lambda q, k, t: [RetrievedItem(text="chunk", score=0.8, title="Policy", document_id="d1")] * docs,
        run_database=db or (lambda q, t, m: DbResult("SELECT 1", [{"n": 459}], "Count tasks", ["a1"])),
        answer_generator=generator,
        db_configured=True,
    )
    return build_workflow(deps), generator


def events(graph, **kwargs):
    kwargs.setdefault("tenant_id", "t1")
    kwargs.setdefault("db_allowed", True)
    return list(stream_workflow(graph, "q", **kwargs))


def kinds(items):
    return [p["type"] for k, p in items if k == "event"]


def test_document_question_streams_progress_then_tokens():
    graph, _ = make_graph("doc")
    items = events(graph)

    assert kinds(items) == ["status", "route", "status", "sources", "status", "token"]
    steps = [p["step"] for k, p in items if k == "event" and p["type"] == "status"]
    assert steps == ["understanding", "searching_documents", "writing_answer"]
    assert items[-1][0] == "final" and items[-1][1]["answer"] == "Hello world."


def test_database_question_emits_interpretation_before_the_answer():
    graph, _ = make_graph("db")
    items = events(graph)

    meta = next(p for k, p in items if k == "event" and p["type"] == "meta")
    assert meta["interpretation"] == "Count tasks" and meta["sql"] == "SELECT 1" and meta["row_count"] == 1
    assert kinds(items).index("meta") < kinds(items).index("token")


def test_hybrid_reports_both_branches():
    graph, _ = make_graph("hybrid")
    steps = {p["step"] for k, p in events(graph) if k == "event" and p["type"] == "status"}

    assert {"searching_documents", "querying_database", "writing_answer"} <= steps


def test_long_answers_are_streamed_in_pieces_after_a_short_hold():
    pieces = ["word "] * 60  # 300 characters
    graph, _ = make_graph("doc", FakeGenerator(pieces))
    tokens = [p["text"] for k, p in events(graph) if k == "event" and p["type"] == "token"]

    assert len(tokens) > 10 and len(tokens[0]) >= 120  # first event is the held-back head
    assert "".join(tokens) == "".join(pieces)


def test_answer_that_leaks_the_prompt_is_regenerated_before_the_user_sees_it():
    leak = ["Based on the provided context excerpts, " + "x" * 150]
    clean = ["A clean answer. " * 12]
    generator = FakeGenerator(leak, clean)
    graph, _ = make_graph("doc", generator)

    tokens = "".join(p["text"] for k, p in events(graph) if k == "event" and p["type"] == "token")

    assert "context excerpts" not in tokens and tokens.startswith("A clean answer.")
    assert len(generator.calls) == 2 and generator.calls[1]  # second try carried the correction


def test_fixed_replies_are_sent_as_a_single_token_event():
    graph, _ = make_graph("chitchat")
    items = events(graph)

    assert kinds(items) == ["status", "route", "token"]
    assert items[-1][1]["answer"].startswith("Hello!")


def test_no_evidence_streams_the_not_found_reply():
    graph, _ = make_graph("doc", docs=0)
    tokens = [p["text"] for k, p in events(graph) if k == "event" and p["type"] == "token"]

    assert tokens == ["I couldn't find that in the available information."]


def test_cancelling_stops_reading_the_model_and_closes_the_stream():
    cancel = threading.Event()
    generator = FakeGenerator(["word "] * 200)
    graph, _ = make_graph("doc", generator)
    seen = 0

    for kind, payload in stream_workflow(graph, "q", cancel=cancel):
        if kind == "event" and payload["type"] == "token":
            seen += 1
            cancel.set()

    assert seen <= 2 and generator.closed == [True]


# ---- the SSE endpoint ---------------------------------------------------------------------------------------
def parse_sse(text):
    out = []
    for block in text.split("\n\n"):
        lines = [l for l in block.splitlines() if l and not l.startswith(":")]
        if lines and lines[0].startswith("event:"):
            out.append((lines[0][6:].strip(), json.loads(lines[1][5:])))
    return out


def client_for(workflow):
    app = FastAPI()
    app.include_router(workflow_routes.router)
    app.dependency_overrides[get_workflow] = lambda: workflow
    return TestClient(app)


def test_endpoint_streams_events_in_order_with_sse_headers():
    graph, _ = make_graph("doc")
    with client_for(graph).stream("POST", "/workflow/stream", json={"question": "hi there"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
    names = [name for name, _ in parse_sse(body)]
    assert names[0] == "status" and names[-1] == "done"
    assert "route" in names and "token" in names and "sources" in names
    done = dict(parse_sse(body))["done"]
    assert done["route"] == "doc" and done["elapsed_ms"] >= 0 and done["trace"]
    assert "".join(p["text"] for n, p in parse_sse(body) if n == "token") == "Hello world."


def test_endpoint_database_branch_needs_the_shared_key(monkeypatch):
    monkeypatch.setattr(
        workflow_routes, "get_settings",
        lambda: Settings(_env_file=None, DB_ENABLED=True, DB_URL="x", SQL_PLANNER_API_ENABLED=True,
                         SQL_QUERY_EXECUTION_ENABLED=True, SQL_PLANNER_API_KEY="secret"),
    )
    graph, _ = make_graph("db")
    client = client_for(graph)
    body = {"question": "count", "tenant_id": "t1"}

    without = parse_sse(client.post("/workflow/stream", json=body).text)
    with_key = parse_sse(client.post("/workflow/stream", json=body, headers={"X-SQL-API-Key": "secret"}).text)

    assert "meta" not in [n for n, _ in without] and "meta" in [n for n, _ in with_key]
    assert dict(without)["done"]["warnings"]  # explains why the database was skipped


def test_endpoint_reports_failures_as_an_error_event():
    class Exploding:
        def stream(self, *a, **k):
            yield "custom", {"type": "status", "step": "understanding", "message": "x"}
            raise RateLimited("busy", retry_after=30)

    body = client_for(Exploding()).post("/workflow/stream", json={"question": "hi"}).text
    events_ = parse_sse(body)

    assert events_[0][0] == "status" and events_[-1][0] == "error"
    assert events_[-1][1] == {"message": "The AI service is busy. Please retry in about 30 seconds.", "status": 429, "retry_after": 30}


def test_endpoint_hides_internal_errors():
    class Crashing:
        def stream(self, *a, **k):
            raise RuntimeError("secret internals: password=hunter2")
            yield

    body = client_for(Crashing()).post("/workflow/stream", json={"question": "hi"}).text

    assert "hunter2" not in body and "Internal server error" in body


def test_endpoint_sends_heartbeats_while_a_step_is_slow(monkeypatch):
    monkeypatch.setattr(workflow_routes, "HEARTBEAT_S", 0.05)

    class Slow:
        def stream(self, *a, **k):
            time.sleep(0.3)
            yield "values", {"route": "doc", "answer": "x", "warnings": [], "trace": [], "evidence": []}

    body = client_for(Slow()).post("/workflow/stream", json={"question": "hi"}).text

    assert body.count(": ping") >= 2 and "event: done" in body


def test_endpoint_rejects_blank_questions():
    graph, _ = make_graph("doc")
    assert client_for(graph).post("/workflow/stream", json={"question": "   "}).status_code == 400


# ---- robustness found in live testing ----------------------------------------------------------------------
def test_lines_are_read_as_they_arrive_not_in_512_byte_blocks(monkeypatch):
    response = StreamResp(lines=OPENAI_LINES)
    Net(monkeypatch, {"api.openai.com": [response]})

    list(LLMClient(cfg(LLM_PROVIDER="openai", **ALL)).stream_generate("s", "u"))

    assert response.chunk_size == 1


@pytest.mark.parametrize(
    ("provider", "host", "lines"),
    [("gemini", "generativelanguage", GEMINI_LINES[:2]),                    # no finishReason
     ("openai", "api.openai.com", OPENAI_LINES[:4]),                         # no finish_reason, no [DONE]
     ("anthropic", "api.anthropic.com", CLAUDE_LINES[:-2])],                 # no message_stop
)
def test_a_stream_cut_off_without_the_finished_marker_is_an_error_not_a_short_answer(monkeypatch, provider, host, lines):
    Net(monkeypatch, {host: [StreamResp(lines=lines)]})
    received = []

    with pytest.raises(LLMUnavailable, match="before the answer was complete"):
        for piece in LLMClient(cfg(LLM_PROVIDER=provider, **ALL)).stream_generate("s", "u"):
            received.append(piece)

    assert received  # the user already saw the first part, which is why this must surface as an error


def test_openai_compatible_servers_are_lenient_about_how_a_stream_ends(monkeypatch):
    Net(monkeypatch, {"11434": [StreamResp(lines=OPENAI_LINES[:3])]})  # no finish_reason, no [DONE]
    client = LLMClient(cfg(LLM_PROVIDER="ollama", LLM_COMPAT_BASE_URL="http://h:11434/v1", LLM_COMPAT_MODEL="m"))

    assert "".join(client.stream_generate("s", "u")) == "Hel"


def test_endpoint_reports_a_cut_off_model_stream_as_an_error_event():
    class Dying:
        def stream(self, *a, **k):
            yield "custom", {"type": "token", "text": "Half an ans"}
            raise LLMUnavailable("gemini stream ended before the answer was complete.")

    events_ = parse_sse(client_for(Dying()).post("/workflow/stream", json={"question": "hi"}).text)

    assert [n for n, _ in events_] == ["token", "error"] and "done" not in [n for n, _ in events_]
