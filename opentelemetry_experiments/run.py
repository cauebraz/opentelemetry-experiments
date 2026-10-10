import argparse
import sys

from opentelemetry_experiments.core import runner, stack


def describe_ladder() -> list[str]:
    rows = []
    for directory in runner.question_directories():
        fields = runner.front_matter((directory / "QUESTION.md").read_text("utf-8"))
        if (directory / "results" / "results.json").exists():
            evidence = "measured"
        elif fields.get("evidence_commit"):
            evidence = "external"
        else:
            evidence = ""
        rows.append(
            f"{fields.get('id', directory.name[:5]):6} "
            f"{fields.get('status', 'unknown'):9} "
            f"{evidence:8}  "
            f"{fields.get('title', directory.name)}"
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one recorded question.")
    parser.add_argument("question", nargs="?", help="a question id such as L0-01")
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--list", action="store_true", help="show the ladder and exit")
    args = parser.parse_args()

    if args.list or args.question is None:
        print("\n".join(describe_ladder()))
        return 0

    directory = runner.directory_for(args.question)
    question = runner.load_question(directory)
    repetitions = args.repetitions or question.repetitions
    results = directory / "results"
    print(f"{question.id} {question.title}: {len(question.variants)} variants")
    try:
        payload = runner.run_question(question, repetitions, results)
    finally:
        stack.stop()
    (results / "REPORT.md").write_text(
        runner.render_question(payload, question),
        encoding="utf-8",
    )
    failures = runner.invalid_runs(payload)
    print(f"{len(payload['runs'])} runs, {len(failures)} invalid")
    print(f"wrote {results / 'results.json'} and {results / 'REPORT.md'}")
    return exit_code(payload)


def exit_code(payload: dict) -> int:
    """Any invalid repetition fails the command, so a partial table is never final."""
    return 1 if runner.invalid_runs(payload) or not payload["runs"] else 0


if __name__ == "__main__":
    sys.exit(main())
