# Partial recall failure silently removes a memory category from exports

## Summary

The CLI gathers the 13 memory categories with one backend `recall` call per
category. A failed call was caught and replaced with an empty list. The export
continued as long as at least one other category succeeded.

This makes a transient, category-specific backend failure indistinguishable
from “this agent has no memories of this type.” A fresh Markdown or OKF export
can therefore omit every memory in the failed category and replace the last
complete cached/project snapshot without reporting an error.

## Impact

The source memories remain in Moorcheh, but the portable snapshot consumed by
an agent is incomplete. For example, a failed `instruction` recall removes all
durable instructions from the agent's exported context while facts and goals
still make the export look healthy. This can cause incorrect behavior until a
later successful sync restores the missing category.

The bug affects both `DirectClient` and `SdkClient`, and both the legacy
Markdown and OKF export paths because they share `_gather_memories_by_type`.

## Reproduction

1. Create a client and replace `recall` with a stub.
2. Make the stub raise for one member of `MEMORY_TYPE_ORDER`.
3. Return one valid memory for every other type.
4. Call `export_memory_md` or `export_okf_bundle`.

Before the fix, the call succeeds and writes an export with the failed category
empty. The regression test `test_partial_failure_refuses_incomplete_export`
reproduces the behavior without a live backend for both client implementations.

## Fix

Track the names of failed categories and fail closed if any category cannot be
recalled. The error lists the failed categories, so the operator can distinguish
a partial failure from a total backend outage. Sync callers can then preserve a
previous good cache instead of publishing an incomplete replacement.

An empty successful response is still accepted, so agents with genuinely no
memories in a category export normally.
