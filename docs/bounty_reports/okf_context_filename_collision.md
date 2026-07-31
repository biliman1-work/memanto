# OKF export silently overwrites colliding context filenames

## Summary

`OkfExportService._write_docs_section()` copies each source file to `section_dir / src.name`. Different source paths can therefore resolve to the same destination. The later copy silently replaces the earlier one, while the exporter still reports both inputs as exported and writes duplicate index links.

There is a second collision with the section's reserved `index.md`: a source context file with that basename is copied first and then overwritten by the generated section index.

Both cases affect the `summaries` and `sessions` inputs accepted by `write_okf_bundle()`.

## Affected code

`memanto/app/services/okf_export_service.py`, in `_write_docs_section()`:

~~~python
existing = sorted(f for f in files if f.exists())

for src in existing:
    shutil.copy2(str(src), str(section_dir / src.name))
    links.append((src.name, src.name))

self._write_index(section_dir / "index.md", title, links)
~~~

The destination name is derived only from `src.name`. No preflight step detects duplicate, case-folded, or reserved destination names.

## Reproduction 1: duplicate basenames

~~~python
from pathlib import Path
from memanto.app.services.okf_export_service import OkfExportService

root = Path("/tmp/memanto-okf-repro")
first = root / "agent-a"
second = root / "agent-b"
first.mkdir(parents=True, exist_ok=True)
second.mkdir(parents=True, exist_ok=True)

(first / "session.md").write_text("content from A", encoding="utf-8")
(second / "session.md").write_text("content from B", encoding="utf-8")

service = OkfExportService(exports_dir=root / "exports")
result = service.write_okf_bundle(
    "demo-agent",
    {},
    sessions=[first / "session.md", second / "session.md"],
)

session_dir = Path(result["output_path"]) / "sessions"
payloads = [p for p in session_dir.glob("*.md") if p.name != "index.md"]

print(result["sections"]["sessions"])
print([p.name for p in payloads])
print([p.read_text(encoding="utf-8") for p in payloads])
print((session_dir / "index.md").read_text(encoding="utf-8"))
~~~

### Actual result

- `result["sections"]["sessions"]` says `"2 session log file(s)"`.
- Only one payload, `sessions/session.md`, remains.
- Its content is whichever source was copied last.
- `sessions/index.md` contains two entries that both target `session.md`.

The export completes successfully, so callers have no indication that one input was lost.

## Reproduction 2: reserved index filename

~~~python
source = root / "index.md"
source.write_text("context payload", encoding="utf-8")

result = service.write_okf_bundle(
    "reserved-name",
    {},
    sessions=[source],
)

session_dir = Path(result["output_path"]) / "sessions"
print(result["sections"]["sessions"])
print((session_dir / "index.md").read_text(encoding="utf-8"))
~~~

### Actual result

The result says one session log was exported, but the copied payload is replaced by the generated section index. The index then links `index.md` to itself. No exported file contains `context payload`.

## Expected result

Every existing input supplied to `summaries` or `sessions` should be preserved, and each section-index entry should target its own existing payload. If a collision-safe mapping cannot be produced, export should fail before copying anything.

## Suggested regression tests

~~~python
import re
from pathlib import Path

from memanto.app.services.okf_export_service import OkfExportService


def _index_targets(index_path: Path) -> list[str]:
    return re.findall(r"\]\(([^)]+)\)", index_path.read_text(encoding="utf-8"))


def test_okf_export_preserves_context_files_with_same_basename(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "session.md").write_text("first", encoding="utf-8")
    (second / "session.md").write_text("second", encoding="utf-8")

    service = OkfExportService(exports_dir=tmp_path / "exports")
    result = service.write_okf_bundle(
        "agent",
        {},
        sessions=[first / "session.md", second / "session.md"],
    )

    session_dir = Path(result["output_path"]) / "sessions"
    targets = _index_targets(session_dir / "index.md")

    assert len(targets) == 2
    assert len({target.casefold() for target in targets}) == 2
    assert all((session_dir / target).is_file() for target in targets)
    assert {
        (session_dir / target).read_text(encoding="utf-8")
        for target in targets
    } == {"first", "second"}


def test_okf_export_preserves_context_named_index_md(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("context payload", encoding="utf-8")

    service = OkfExportService(exports_dir=tmp_path / "exports")
    result = service.write_okf_bundle(
        "agent",
        {},
        sessions=[source],
    )

    session_dir = Path(result["output_path"]) / "sessions"
    targets = _index_targets(session_dir / "index.md")

    assert len(targets) == 1
    assert targets[0].casefold() != "index.md"
    assert (session_dir / targets[0]).read_text(encoding="utf-8") == (
        "context payload"
    )
~~~

Both tests fail against the current implementation. The first detects the overwritten duplicate and the duplicate index targets; the second detects collision with the generated index itself.

The `casefold()` assertion defines a portable export contract: generated payload names remain distinct when the bundle is moved from a case-sensitive filesystem to a case-insensitive one.

## Suggested fix

Perform a preflight mapping from every source path to a final destination name before copying:

1. Reserve `index.md` and every already-assigned destination using a documented Unicode-normalization and case-folding policy.
2. Start with `src.name`. On collision, add a deterministic suffix derived from the normalized source path.
3. Check the complete candidate again. If it still collides (including a hash or normalization collision), deterministically extend/disambiguate it or fail before any copy.
4. Copy using only the finalized names and build index links from the same mapping.

A short hash alone is not a uniqueness guarantee; the final membership check is required.

## Impact

This is a silent data-integrity failure in an export path. A bundle advertised as containing multiple context documents can omit documents without warning, and a single valid `index.md` input is always lost. Consumers may reason over incomplete context while the export metadata incorrectly reports success.

Bounty: #770
