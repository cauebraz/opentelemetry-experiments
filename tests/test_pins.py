import re
import tomllib

from opentelemetry_experiments.core import stack

ROOT = stack.ROOT

PINNED = (
    "grpcio",
    "opentelemetry-exporter-otlp-proto-http",
    "opentelemetry-sdk",
)


def project_pins() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = {}
    for requirement in project["project"]["dependencies"]:
        name, version = requirement.split("==", 1)
        pins[name] = version
    return pins


def lock_resolved(name: str) -> str:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(
        rf'(?ms)^\[\[package\]\]\nname = "{re.escape(name)}"\nversion = "([^"]+)"',
        lock,
    )
    assert match is not None, f"{name} is missing from uv.lock"
    return match.group(1)


def lock_specifier(name: str) -> str:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(
        rf'\{{ name = "{re.escape(name)}", specifier = "==([^"]+)" \}}',
        lock,
    )
    assert match is not None, f"{name} has no exact specifier in uv.lock"
    return match.group(1)


def test_the_collector_version_is_the_same_in_every_place_it_is_written():
    dockerfile = (ROOT / "Dockerfile.collector").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    in_dockerfile = re.search(r"ARG OTELCOL_VERSION=(\S+)", dockerfile).group(1)
    in_compose = set(re.findall(r'OTELCOL_VERSION: "([^"]+)"', compose))

    assert in_dockerfile == stack.OTELCOL_VERSION
    assert in_compose == {stack.OTELCOL_VERSION}


def test_the_collector_checksums_are_the_same_in_the_dockerfile_and_compose():
    dockerfile = (ROOT / "Dockerfile.collector").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    for arch in ("AMD64", "ARM64"):
        in_dockerfile = re.search(
            rf"ARG OTELCOL_SHA256_{arch}=([0-9a-f]{{64}})", dockerfile
        ).group(1)
        pattern = rf'OTELCOL_SHA256_{arch}: "([0-9a-f]{{64}})"'
        in_compose = set(re.findall(pattern, compose))
        assert in_compose == {in_dockerfile}


def test_the_prometheus_version_matches_the_pinned_image():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    image = re.search(r"image: prom/prometheus:(v[\d.]+)@sha256:[0-9a-f]{64}", compose)

    assert image is not None, "the Prometheus image is not pinned by digest"
    assert image.group(1) == stack.PROMETHEUS_VERSION


def test_the_method_document_names_the_same_pins():
    method = (ROOT / "docs" / "METHOD.md").read_text(encoding="utf-8")

    assert f"`{stack.OTELCOL_VERSION}`" in method
    assert stack.PROMETHEUS_VERSION in method


def test_python_dependency_pins_match_the_lock_and_the_method():
    method = (ROOT / "docs" / "METHOD.md").read_text(encoding="utf-8")
    pins = project_pins()

    assert set(pins) == set(PINNED)
    for name, version in pins.items():
        assert lock_resolved(name) == version
        assert lock_specifier(name) == version
        assert f"`{version}`" in method, f"{name} {version} is missing from METHOD.md"
