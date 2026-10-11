import argparse
import difflib
import json
import sys
from pathlib import Path

from opentelemetry_experiments.core import runner


def render_recorded_report(directory: Path) -> str:
    """Render one canonical report without writing it."""
    results = directory / "results" / "results.json"
    payload = json.loads(results.read_text(encoding="utf-8"))
    question = runner.load_question(directory)
    recorded_id = payload.get("question", {}).get("id")
    if recorded_id != question.id:
        raise ValueError(
            f"{results}: question id {recorded_id!r} does not match {question.id!r}"
        )
    return runner.render_question(payload, question)


def check_reports(root: Path | None = None) -> tuple[int, list[str]]:
    """Compare every canonical local report with its in-memory rendering."""
    checked = 0
    problems = []
    for directory in runner.question_directories(root):
        question_file = directory / "QUESTION.md"
        fields = runner.front_matter(question_file.read_text(encoding="utf-8"))
        if fields.get("status") != "measured":
            continue
        if all(fields.get(name) for name in runner.EXTERNAL_FIELDS):
            continue

        results = directory / "results" / "results.json"
        report = directory / "results" / "REPORT.md"
        missing = [path for path in (results, report) if not path.is_file()]
        if missing:
            problems.extend(f"{path} does not exist" for path in missing)
            continue

        checked += 1
        try:
            expected = render_recorded_report(directory)
            recorded_bytes = report.read_bytes()
            recorded = recorded_bytes.decode("utf-8")
        except Exception as error:
            problems.append(f"{directory}: {type(error).__name__}: {error}")
            continue
        if recorded_bytes == expected.encode("utf-8"):
            continue

        diff = "".join(
            difflib.unified_diff(
                recorded.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{report} (recorded)",
                tofile=f"{report} (rendered)",
            )
        )
        problems.append(f"{report} does not match its recorded results\n{diff}")
    return checked, problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-render a question's REPORT.md from its recorded results.json."
    )
    parser.add_argument("question", nargs="?", help="a question id such as L0-01")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check every canonical report without writing files",
    )
    args = parser.parse_args()

    if args.check:
        if args.question:
            parser.error("--check does not accept a question id")
        checked, problems = check_reports()
        if problems:
            print("\n\n".join(problems), file=sys.stderr)
            return 1
        print(f"{checked} canonical reports match their recorded results")
        return 0
    if not args.question:
        parser.error("a question id is required unless --check is used")

    directory = runner.directory_for(args.question)
    results = directory / "results" / "results.json"
    if not results.exists():
        print(f"{results} does not exist, run the question first", file=sys.stderr)
        return 1
    destination = directory / "results" / "REPORT.md"
    destination.write_text(
        render_recorded_report(directory),
        encoding="utf-8",
    )
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
