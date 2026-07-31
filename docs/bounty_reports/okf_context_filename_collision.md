# OKF export silently overwrites context files that share a basename

## Summary

`OkfExportService._write_docs_section()` copies every source file to `section_dir / src.name`. Two valid source paths from different directories can therefore resolve to the same destination. The later copy silently replaces the earlier one, while the exporter still reports both inputs as exported and writes duplicate links into the section index.

This affects the `summaries` and `sessions` inputs accepted by `write_okf_bundle()`. The returned metadata can say that two files were exported even though the bundle contains only one payload.

## Affected code

`memanto/app/services/okf_export_service.py`, in `_write_docs_section()`:

~~~python
existing = sorted(f for f in files if f.exists())

for src in existing:
    shutil.copy2(str(src), str(section_dir / src.name))
    links.append((src.name, src.name))
~~~

The destination name is derived only from `src.name`, not from the full source path or a collision-safe generated name.

## Minimal reproduction

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

bundle = Path(result["output_path"])
payloads = [
    p for p in (bundle / "sessions").glob("*.md")
    if p.name != "index.md"
]

print(result["sections"]["sessions"]["count"])
print([p.name for p in payloads])
print([p.read_text(encoding="utf-8") for p in payloads])
print((bundle / "sessions" / "index.md").read_text(encoding="utf-8"))
~~~

## Actual result

- `sections.sessions.count` is `2`.
- Only one payload file, `sessions/session.md`, remains.
- Its content is whichever source was copied last.
- `sessions/index.md` contains two entries that both point to the same `session.md`.

The export completes successfully, so callers have no indication that data was lost.

## Expected result

Every existing input supplied to `summaries` or `sessions` should be preserved in the bundle, and every index entry should resolve to its own exported file. If that guarantee cannot be met, export should fail explicitly rather than silently overwrite data.

## Suggested failing regression test

~~~python
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
    payloads = [
        p for p in session_dir.glob("*.md")
        if p.name != "index.md"
    ]

    assert len(payloads) == 2
    assert {p.read_text(encoding="utf-8") for p in payloads} == {
        "first",
        "second",
    }
~~~

This test fails against the current implementation because only one payload survives.

## Suggested fix

Generate a deterministic unique destination name whenever a basename is already used, for example by adding a short hash of the normalized source path. Build the index from those final destination names. Alternatively, preserve enough relative path structure to guarantee uniqueness.

The collision check should be case-insensitive on filesystems where `Session.md` and `session.md` refer to the same destination. A regression test should cover both ordinary duplicate basenames and case-folding collisions.

## Impact

This is a silent data-integrity failure in an export path: a bundle advertised as containing multiple context documents can omit documents without warning. Consumers of the bundle may then reason over incomplete context while the export metadata incorrectly reports success.

Bounty: #770
