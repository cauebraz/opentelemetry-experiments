import argparse
import json
import sys

from opentelemetry_experiments.core import runner


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-render a question's REPORT.md from its recorded results.json."
    )
    parser.add_argument("question", help="a question id such as L0-01")
    args = parser.parse_args()

    directory = runner.directory_for(args.question)
    results = directory / "results" / "results.json"
    if not results.exists():
        print(f"{results} does not exist, run the question first", file=sys.stderr)
        return 1
    question = runner.load_question(directory)
    payload = json.loads(results.read_text(encoding="utf-8"))
    destination = directory / "results" / "REPORT.md"
    destination.write_text(
        runner.render_question(payload, question),
        encoding="utf-8",
    )
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
