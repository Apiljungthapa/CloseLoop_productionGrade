"""Report requests typed into the chat: the SSE frames the widget receives."""
import json
import unittest
from datetime import datetime, timezone

from app.services import report_chat
from app.services.report_intent import Context, interpret
from app.services.report_studio import Names, build_comparison, build_period_completion
from test_report_studio import NAMES, P10, P11, TODAY, StudioTestCase, T, dataset, period
from test_report_intent import CTX

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def parse(frames):
    out = []
    for f in frames:
        lines = f.strip().split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), f
        out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


class ChatFrames(StudioTestCase):
    def interp(self, text):
        return interpret(text, CTX).to_dict()

    def test_a_ready_report_streams_status_card_summary_done(self):
        report = build_period_completion(period(dataset()), NAMES, TODAY, generated_at=NOW)
        frames = parse(report_chat.frames_for(self.interp("Generate the monthly task completion report."), report, 12))
        self.assertEqual([name for name, _ in frames], ["status", "report", "token", "done"])
        card = frames[1][1]
        self.assertEqual(card["spec"]["kind"], "period_completion")
        self.assertEqual(card["downloads"], ["pdf", "xlsx", "csv"])
        self.assertEqual(card["format"], "dashboard")
        self.assertEqual(len(card["kpis"]), 4)
        self.assertEqual(card["kpis"][0]["label"], "Completion")
        self.assertTrue(card["findings"] and card["title"].startswith("Task completion report"))
        text = frames[2][1]["text"]
        self.assertIn("**Task completion report", text)
        self.assertIn("**Completion:** 42.9%", text)
        self.assertIn("What stands out", text)
        self.assertIn("Suggested next step", text)
        self.assertEqual(frames[3][1]["route"], "report")
        self.assertFalse(frames[3][1]["needs_clarification"])

    def test_the_comparison_summary_shows_the_change(self):
        cur, prev = period(dataset()), period([T("p1", "COMPLETED", None, completed_on=None)], P10)
        prev = period([T(f"p{i}", "COMPLETED", None) for i in range(4)], P10)
        report = build_comparison(cur, prev, [prev, cur], NAMES, TODAY, generated_at=NOW)
        text = parse(report_chat.frames_for(self.interp("Compare this period with the previous period."), report, 1))[2][1]["text"]
        self.assertIn("pts)", text)  # "(-62.5 pts)"

    def test_assumptions_are_stated_in_the_reply(self):
        report = build_period_completion(period(dataset()), NAMES, TODAY, generated_at=NOW)
        text = parse(report_chat.frames_for(self.interp("generate a report"), report, 1))[2][1]["text"]
        self.assertIn("No period named, so I used the current one (P11)", text)

    def test_a_clarifying_question_is_a_plain_reply_with_no_card(self):
        frames = parse(report_chat.frames_for(self.interp("completion report for P99"), None, 3))
        self.assertEqual([name for name, _ in frames], ["status", "token", "done"])
        self.assertIn("P99", frames[1][1]["text"])
        self.assertTrue(frames[2][1]["needs_clarification"])

    def test_every_frame_is_valid_sse(self):
        report = build_period_completion(period(dataset()), NAMES, TODAY, generated_at=NOW)
        for frame in report_chat.frames_for(self.interp("overdue report as pdf"), report, 1):
            self.assertTrue(frame.endswith("\n\n"))
            self.assertEqual(frame.count("\n\n"), 1)  # one blank line only, at the very end

    def test_the_card_carries_the_requested_download_format(self):
        report = build_period_completion(period(dataset()), NAMES, TODAY, generated_at=NOW)
        card = parse(report_chat.frames_for(self.interp("completion report as excel and pdf"), report, 1))[1][1]
        self.assertEqual((card["format"], card["formats"]), ("xlsx", ["xlsx", "pdf"]))


if __name__ == "__main__":
    unittest.main()
