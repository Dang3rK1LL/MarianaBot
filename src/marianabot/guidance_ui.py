"""Answer real saved round questions; opening this form never starts inference."""

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static


class GuidanceScreen(ModalScreen[dict | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, questions):
        super().__init__()
        self.questions = questions

    def compose(self) -> ComposeResult:
        with Vertical(id="guidance-dialog"):
            yield Static(f"Round {self.questions[0]['number']} questions", classes="dialog-title")
            with VerticalScroll():
                for index, row in enumerate(self.questions):
                    yield Static(
                        row["question_id"] + " · " + row["question"],
                        classes="guidance-question",
                        markup=False,
                    )
                    yield Select(
                        [(option, option) for option in row["options"]],
                        prompt="Choose an answer",
                        id=f"answer-choice-{index}",
                    )
                    yield Input(
                        placeholder="Or type your answer",
                        max_length=1000,
                        id=f"answer-text-{index}",
                    )
                    yield Static(row["impact"], classes="guidance-impact", markup=False)
            yield Static("", id="guidance-error", markup=False)
            with Horizontal(id="guidance-actions"):
                yield Button("Save answers", id="save-guidance")
                yield Button("Cancel", id="cancel-guidance")

    @on(Button.Pressed, "#save-guidance")
    def save_answers(self):
        answers = {}
        for index, row in enumerate(self.questions):
            answer = self.query_one(f"#answer-text-{index}", Input).value.strip()
            chosen = self.query_one(f"#answer-choice-{index}", Select).value
            answer = answer or (str(chosen) if chosen is not Select.BLANK else "")
            if not answer:
                self.query_one("#guidance-error", Static).update(
                    "Answer each question, or choose an honest uncertainty option."
                )
                return
            answers[row["question_id"]] = answer
        self.dismiss(answers)

    @on(Button.Pressed, "#cancel-guidance")
    def action_cancel(self):
        self.dismiss(None)
