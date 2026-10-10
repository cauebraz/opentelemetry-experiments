import sys

from opentelemetry_experiments.core import runner, stack


def main() -> int:
    problems: list[str] = []
    directories = runner.question_directories()
    if not directories:
        print("no question directories found", file=sys.stderr)
        return 1
    for directory in directories:
        path = directory / "QUESTION.md"
        if not path.exists():
            problems.append(f"{directory}: QUESTION.md is missing")
            continue
        problems.extend(runner.lint_question_file(path))
    template = stack.QUESTIONS / "_template" / "QUESTION.md"
    if not template.exists():
        problems.append(f"{template}: the template is missing")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"{len(directories)} questions, front matter and sections complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
