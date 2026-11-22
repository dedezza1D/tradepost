"""Moderation evaluation: rules, model and their combination on the hand-labelled set.

Usage (local Ollama with the chat model; uses the search evaluation database for the model's
call log and answer cache, so a run cut short resumes where it stopped):

    uv run python -m eval.moderation_eval

Listings and messages are scored as a yes/no decision (send to review, warn), with precision
(how many flagged were right) and recall (how many of the bad ones were caught), also on the
hard cases alone. Reports are scored on priority: exact match, and recall of the urgent ones.
"""

import argparse
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.moderation_dataset import LISTINGS, MESSAGES, REPORTS
from eval.search_eval import eval_database_url
from tradepost import moderation as m
from tradepost.ai.client import AI, OllamaProvider
from tradepost.config import get_settings
from tradepost.db import Database

HERE = Path(__file__).parent


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, predicted: bool, actual: bool) -> None:
        if predicted and actual:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif actual:
            self.fn += 1
        else:
            self.tn += 1

    def row(self) -> str:
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0
        recall = self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return f"{precision:.0%} | {recall:.0%} | {f1:.2f} | {self.fp} | {self.fn}"


async def ask(
    ai: AI, database: Database, feature: str, prompt: str, text: str, schema: dict[str, Any]
) -> dict[str, Any]:
    async with database.sessionmaker() as db, db.begin():
        answer = await ai.complete_json(db, feature, prompt, text, schema, max_tokens=200)
    assert isinstance(answer, dict)
    return answer


async def run() -> str:
    settings = get_settings().model_copy(
        update={"ai_provider": "ollama", "database_url": eval_database_url()}
    )
    database = Database(settings)
    ai = AI(settings, database, OllamaProvider(settings.ai_base_url))
    started = time.perf_counter()

    # --- listings
    arms = {
        "Rules": Score(),
        "Model (risk medium or high)": Score(),
        "Model (risk high)": Score(),
        "Model (any category)": Score(),
        "Rules or model (any category)": Score(),
        "Rules or model (medium+)": Score(),
    }
    hard = {name: Score() for name in arms}
    category_right = category_total = 0
    for case in LISTINGS:
        rule = m.listing_rules(case.title, case.description)
        answer = await ask(
            ai,
            database,
            m.FEATURE_LISTING,
            m.LISTING_PROMPT,
            f"Título: {case.title}\nDescrição: {case.description}",
            m.LISTING_SCHEMA,
        )
        model_category = answer["category"] if answer["category"] != "none" else None
        risk = answer["risk"]
        actual = case.label != "ok"
        decisions = {
            "Rules": rule is not None,
            "Model (risk medium or high)": model_category is not None
            and risk in ("medium", "high"),
            "Model (risk high)": model_category is not None and risk == "high",
            "Model (any category)": model_category is not None,
            "Rules or model (any category)": rule is not None or model_category is not None,
            "Rules or model (medium+)": rule is not None
            or (model_category is not None and risk in ("medium", "high")),
        }
        for name, decision in decisions.items():
            arms[name].add(decision, actual)
            if case.hard:
                hard[name].add(decision, actual)
        if actual and model_category:
            category_total += 1
            category_right += model_category == case.label
    listing_seconds = time.perf_counter() - started

    # --- messages
    msg_arms = {
        "Rules (payment outside the site)": Score(),
        "Model": Score(),
        "Rules or model": Score(),
    }
    msg_hard = {name: Score() for name in msg_arms}
    for message in MESSAGES:
        flagged = m.message_rules(message.text)
        answer = await ask(
            ai, database, m.FEATURE_MESSAGE, m.MESSAGE_PROMPT, message.text, m.MESSAGE_SCHEMA
        )
        decisions = {
            "Rules (payment outside the site)": flagged,
            "Model": bool(answer["scam"]),
            "Rules or model": flagged or bool(answer["scam"]),
        }
        for name, decision in decisions.items():
            msg_arms[name].add(decision, message.scam)
            if message.hard:
                msg_hard[name].add(decision, message.scam)

    # --- reports
    exact = {"Rules": 0, "Model": 0, "More urgent of the two": 0}
    urgent = {"Rules": Score(), "Model": Score(), "More urgent of the two": Score()}
    for report in REPORTS:
        rule_priority = m.report_rules(report.reason, report.target)
        answer = await ask(
            ai,
            database,
            m.FEATURE_REPORT,
            m.REPORT_PROMPT,
            f"Motivo: {report.reason}\nComentário: {report.details}\nConteúdo denunciado: {report.target}",
            m.REPORT_SCHEMA,
        )
        model_priority = int(answer["priority"])
        for name, predicted in (
            ("Rules", rule_priority),
            ("Model", model_priority),
            ("More urgent of the two", min(rule_priority, model_priority)),
        ):
            exact[name] += predicted == report.priority
            urgent[name].add(predicted == 1, report.priority == 1)
    await database.dispose()

    header = "| Decision | Precision | Recall | F1 | False positives | Missed |\n|---|---|---|---|---|---|"
    lines = [
        "# Moderation evaluation",
        "",
        f"Hand-labelled set (`eval/moderation_dataset.py`): {len(LISTINGS)} listings"
        f" ({sum(c.label != 'ok' for c in LISTINGS)} prohibited, {sum(c.hard for c in LISTINGS)} hard"
        f" cases), {len(MESSAGES)} chat messages ({sum(x.scam for x in MESSAGES)} scams,"
        f" {sum(x.hard for x in MESSAGES)} hard cases), {len(REPORTS)} reports. Model:"
        f" {settings.ai_chat_model} on a local Ollama server.",
        "",
        "Generated by `uv run python -m eval.moderation_eval`.",
        "",
        "## Listings: send to review before publishing",
        "",
        header,
        *[f"| {name} | {score.row()} |" for name, score in arms.items()],
        "",
        "Hard cases only (look-alikes and disguised listings):",
        "",
        header,
        *[f"| {name} | {score.row()} |" for name, score in hard.items()],
        "",
        f"When the model flags a prohibited listing, the category is right in {category_right} of"
        f" {category_total}.",
        "",
        "## Chat: warn about a scam",
        "",
        header,
        *[f"| {name} | {score.row()} |" for name, score in msg_arms.items()],
        "",
        "Hard cases only (honest messages about Pix or contact; scams without keywords):",
        "",
        header,
        *[f"| {name} | {score.row()} |" for name, score in msg_hard.items()],
        "",
        "## Reports: priority for the admin queue",
        "",
        "| Triage | Exact priority | Urgent: precision | Urgent: recall |",
        "|---|---|---|---|",
        *[
            f"| {name} | {exact[name]} of {len(REPORTS)} | {urgent[name].row().split(' | ')[0]} |"
            f" {urgent[name].row().split(' | ')[1]} |"
            for name in exact
        ],
        "",
        "## Policy in use",
        "",
        "- Listings: held for review when the rules flag them or the model sees medium or high"
        " risk (the best row above). The model alone misses many: it often names the right"
        " category but calls the risk low.",
        "- Chat: with a model, the model decides, and it clears the rules' payment warning on"
        " honest messages; without one (the live demo), the rules warn.",
        "- Reports: priority from the rules; the model adds what the content is and why.",
        "",
        f"Listing checks took {listing_seconds / len(LISTINGS):.1f} s each on average (laptop GPU;"
        " cached answers are instant on a rerun).",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=HERE / "results" / "moderation.md")
    args = parser.parse_args()
    text = asyncio.run(run())
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
