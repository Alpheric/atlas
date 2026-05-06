"""Unit tests for task type classifier."""

from a1.proxy.request_models import ChatCompletionRequest, MessageInput
from a1.routing.classifier import classify_task


def req(*contents, system: str | None = None) -> ChatCompletionRequest:
    msgs = []
    if system:
        msgs.append(MessageInput(role="system", content=system))
    for c in contents:
        msgs.append(MessageInput(role="user", content=c))
    return ChatCompletionRequest(model="auto", messages=msgs)


def test_security_cve():
    task, conf = classify_task(req("Explain CVE-2023-1234 and its impact"))
    assert task == "security"


def test_security_sqli():
    task, conf = classify_task(req("Check this code for SQL injection vulnerability"))
    assert task == "security"


def test_coding_function():
    task, conf = classify_task(req("Write a function to parse JSON"))
    assert task == "coding"


def test_coding_medium_message():
    filler = "please " * 150
    content = f"{filler} Write a Python function to sort a list of integers"
    task, conf = classify_task(req(content))
    assert task == "coding"
    assert conf > 0.0


def test_math_general():
    # Math no longer has its own category — falls through to general
    task, conf = classify_task(req("Calculate the derivative of f(x) = 3x^2 + 2x - 5"))
    assert task in ("general", "reasoning", "coding")


def test_translation_general():
    task, conf = classify_task(req("Translate the following text to Spanish: Hello world"))
    assert task in ("general", "documents")


def test_summarization_documents():
    task, conf = classify_task(
        req("Summarize this article: The quick brown fox jumps over the lazy dog")
    )
    assert task in ("documents", "general")


def test_structured_extraction_general():
    task, conf = classify_task(req('Extract JSON from: {"name": "Alice", "age": 30}'))
    assert task in ("general", "coding", "data")


def test_code_tools_only():
    r = req("Run this task for me")
    r.tools = [{"function": {"name": "search_web"}}]  # type: ignore[attr-defined]
    task, conf = classify_task(r)
    # Tools with no code markers → falls to general
    assert task in ("general", "coding")


def test_very_long_with_system():
    long_content = "word " * 2500
    task, conf = classify_task(req(long_content, system="You are a helpful assistant."))
    assert task in ("reasoning", "general", "long_context")


def test_short_no_system():
    task, conf = classify_task(req("Hi there"))
    assert task in ("general", "chat")


def test_question_short():
    task, conf = classify_task(req("What is the capital of France?"))
    assert task in ("general", "chat", "reasoning")


def test_general_default():
    task, conf = classify_task(req("word " * 50))
    assert task in ("general", "reasoning", "chat")


def test_confidence_range():
    task, conf = classify_task(req("Write a Python hello world program"))
    assert 0.0 <= conf <= 1.0
