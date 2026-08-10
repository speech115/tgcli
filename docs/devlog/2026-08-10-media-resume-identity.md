## 2026-08-10 — a media resume must identify its media

**Did:** landed ADR-0084 (#180). The `media download` resume record now carries
`media_id` and `size`, and `_resume_offset` reuses a partial file only when
both still match; a mismatch drops the partial, notes one line on stderr, and
restarts. `media_identity` moved out of `clone/reupload.py` into
`transfer.py` beside `media_byte_size`, so the clone and `media download`
paths share one definition instead of diverging again.

**Decided:** a changed source media is not operator error — restart quietly
(the `resumable: false` precedent), not exit 2 (the "state does not match
requested output" precedent). Records written by older versions compare
unequal and restart once; that is the cost, not a migration.

**Learned:** `test_parallel_download_refuses_resuming_partial_transfer` was
passing for the wrong reason — its interrupted client and its parallel client
described different media (6 bytes vs 1 MiB), so the new identity check fired
before the parallel-resume guard it meant to exercise. Fixed by giving both
the same size. Worth remembering that a test double's incidental mismatch can
mask which guard is actually under test.

**Next:** nothing open from the #169–#175 wave. #179 was closed not-planned by
the owner: outside clone the same peer-refusal shape only mis-words an error
and mis-codes an exit, with no data at risk.

**Review fix:** the identity check was asked before the `source`/`destination`
consistency check, so a re-run with a different `--output` *plus* a replaced
media restarted quietly instead of raising the exit-2 "does not match
requested output" the wrong-output case has always had. Reordered, with two
regression tests — that branch had no direct coverage at all before, which is
why the ordering could regress unnoticed.
