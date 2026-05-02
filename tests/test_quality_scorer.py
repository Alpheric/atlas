"""Tests for the self-heal quality scorer (Layer 1).

These are pure-unit tests — no DB, no LLM calls.
"""

import pytest

from a1.healing.quality_scorer import score_response


class TestScoreResponseEdgeCases:
    def test_empty_string_returns_zero(self):
        assert score_response("", "chat") == 0.0

    def test_whitespace_only_returns_zero(self):
        assert score_response("   \n\t  ", "chat") == 0.0

    def test_score_is_in_valid_range(self):
        for text in ["hello", "I cannot help.", "x" * 1000, ""]:
            s = score_response(text, "chat")
            assert 0.0 <= s <= 1.0, f"score out of range for {text!r}: {s}"


class TestRefusalDetection:
    def test_i_cannot_gets_refusal_penalty(self):
        refusal = "I cannot help with that request."
        normal = "Here is how you can accomplish that task."
        assert score_response(refusal, "chat") < score_response(normal, "chat")

    def test_as_an_ai_detected(self):
        s = score_response("As an AI language model, I don't have feelings.", "chat")
        # Should be lower than a non-refusal of similar length
        normal = "Here is a complete answer to your question about language models."
        assert s < score_response(normal, "chat")

    def test_refusal_not_detected_mid_text(self):
        # Refusal phrase only checked in first 200 chars; mid-text should not penalise
        padding = "This is a great answer. " * 10  # push refusal phrase past 200 chars
        text = padding + "I cannot believe how good this is."
        s = score_response(text, "chat")
        # Should get full refusal score (0.20) since phrase appears after char 200
        assert s >= 0.6  # a long, paragraph-filled text should score well


class TestTruncationDetection:
    def test_truncated_response_penalised(self):
        # Build a long text that is identical except the last character.
        # One ends with a period (complete), the other ends mid-word (truncated).
        # Same length → only the truncation signal differs.
        long_base = "Here is a thorough answer to your question about the topic " * 5
        complete = long_base[:-1] + "."   # replace last char with period
        truncated = long_base[:-1] + "x"  # replace last char with non-terminal letter
        assert len(complete) == len(truncated), "test setup: lengths must be equal"
        assert score_response(truncated, "chat") < score_response(complete, "chat")

    def test_ends_with_code_fence_not_truncated(self):
        code = "Here is the solution:\n```python\nprint('hello')\n```"
        s = score_response(code, "code")
        assert s >= 0.6

    def test_short_text_not_truncated(self):
        # Under 200 chars — truncation check skipped
        short = "Yes"
        s = score_response(short, "chat")
        assert s >= 0.0  # just checking it doesn't error


class TestTaskFormatMatch:
    def test_code_task_with_code_block_scores_higher(self):
        with_block = "Here's the solution:\n```python\nfor i in range(10):\n    print(i)\n```"
        without_block = "You can use a for loop with range to iterate through numbers."
        assert score_response(with_block, "code") > score_response(without_block, "code")

    def test_code_task_without_block_still_gets_partial_credit(self):
        without_block = "You can use a for loop with range to iterate through numbers and print them."
        s = score_response(without_block, "code")
        assert s > 0.3  # partial credit (0.05 format), but rest of signals still count

    def test_prose_task_with_paragraphs_scores_higher(self):
        with_paragraphs = (
            "This is the first paragraph with useful information.\n\n"
            "This is the second paragraph that provides more detail."
        )
        without_paragraphs = "This is a single-paragraph response without any line breaks at all."
        assert score_response(with_paragraphs, "data") > score_response(without_paragraphs, "data")

    def test_chat_task_has_no_format_requirement(self):
        no_format = "Sure, I can help with that!"
        s = score_response(no_format, "chat")
        # Should get full format score (0.20) for chat regardless of formatting
        assert s >= 0.4  # length + repetition + refusal + truncation + format


class TestRepetitionPenalty:
    def test_repetitive_text_penalised(self):
        repetitive = "the cat sat on the mat " * 20
        varied = " ".join(
            [
                "The quick brown fox jumps over the lazy dog",
                "A journey of a thousand miles begins with a single step",
                "To be or not to be that is the question here",
                "All that glitters is not gold but might be valuable",
            ]
        )
        assert score_response(repetitive, "chat") < score_response(varied, "chat")

    def test_short_text_no_repetition_penalty(self):
        # Under 10 words → no repetition check
        short = "Yes that is correct indeed"
        s = score_response(short, "chat")
        # Should get full repeat score (0.20)
        assert s >= 0.4


class TestLengthSignal:
    def test_longer_response_scores_higher_length(self):
        short = "Yes."
        long = "A" * 300
        # length score for long should be 0.25; for short ~0.003
        s_long = score_response(long, "chat")
        s_short = score_response(short, "chat")
        assert s_long > s_short

    def test_length_capped_at_300(self):
        # Very long text should not exceed max length score
        very_long = "This is a sentence. " * 100
        s = score_response(very_long, "chat")
        assert s <= 1.0
