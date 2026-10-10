import argparse
import shutil
import sys
from pathlib import Path

from opentelemetry_experiments.core import runner, stack

THEMES = ("collector", "traces", "metrics", "logs", "sdk", "semconv")


def create(questions: Path, question_id: str, slug: str, theme: str) -> Path:
    """Copy the template and replace every placeholder in both files it fills."""
    question_id = question_id.upper()
    name = f"{question_id}-{slug}"
    match = runner.DIRECTORY_PATTERN.match(name)
    if match is None:
        raise ValueError(f"{name} is not L<level>-<nn>-<slug>")
    if theme not in THEMES:
        raise ValueError(f"{theme} is not one of {', '.join(THEMES)}")
    destination = questions / name
    if destination.exists():
        raise FileExistsError(str(destination))

    level = match.group("level")
    shutil.copytree(questions / "_template", destination)
    replacements = {
        "L0-00-slug": name,
        "L0-00": question_id,
        "level: 0": f"level: {level}",
        "level=0": f"level={level}",
        "theme: collector": f"theme: {theme}",
        'theme="collector"': f'theme="{theme}"',
    }
    for path in (destination / "QUESTION.md", destination / "question.py"):
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy questions/_template into a new question directory."
    )
    parser.add_argument("id", help="for example L1-01")
    parser.add_argument("slug", help="for example pipeline-from-zero")
    parser.add_argument("--theme", required=True, help=f"one of {', '.join(THEMES)}")
    args = parser.parse_args()

    try:
        destination = create(stack.QUESTIONS, args.id, args.slug, args.theme)
    except (ValueError, FileExistsError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"created {destination}")
    print("write the hypothesis before the first run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
