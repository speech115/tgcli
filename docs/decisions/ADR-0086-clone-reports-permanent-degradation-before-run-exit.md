# ADR-0086: Clone reports permanent degradation before the run can exit

Date: 2026-08-10
Status: accepted
Closes: #184

## Context

`clone sync` records two permanent fidelity degradations only in the result
document built after the copying loop: `sync.quote_flattened` and
`sync.skipped_unsupported`. That tail is never reached when a later Telegram
request ends the run with `FLOOD_WAIT`.

The completed work survives: clone mappings and cursors are checkpointed, and
already-synced source messages are never revisited. The report does not. A
quote fallback can therefore be planted permanently without the contract's
exit-2 signal ever firing, while a skipped unsupported message can disappear
from the operator-visible record. ADR-0085 fixed the same lifetime mismatch
for dropped bot keyboards by reporting each loss when it becomes permanent;
the two older siblings still have the tail-only shape.

## Decision

Report both degradations on stderr at their durable boundary, once per affected
source message:

- after an unsupported batch advances and saves its leg cursor, emit a warning
  naming the source message id and unsupported TL kind;
- after a copied message plants and checkpoints a quote fallback, emit a
  warning naming the source message id and fallback reason.

The existing tail result remains authoritative when the run reaches it.
`sync.skipped_unsupported`, `sync.quote_flattened`, the plain count columns,
and the exit-2 `PartialFailure` after a completed run do not change. If a later
flood ends the run, the command still returns the rate-limit envelope and exit
5; stderr is the surviving evidence for the permanent earlier degradation.

The warning count follows the affected messages. A one-line summary would
under-report a run that permanently degraded several messages before losing
its result document, repeating the review defect ADR-0085 already rejected.

No acknowledgement or replay state is added. This decision repairs the lost
report, not the larger question of whether a quote fallback should remain an
unacknowledged exit-2 condition across later invocations.

## Rejected alternatives

- **Persist unacknowledged degradations in clone state.** This needs a state
  schema, acknowledgement lifecycle, replay rule, and new operator surface.
  It changes the meaning of a later invocation instead of repairing the
  missing report in the invocation that made the permanent copy.
- **Stop immediately at the first quote fallback.** That would prevent a later
  flood from hiding the exit code, but would change clone throughput and make
  an already understood degradation abort otherwise valid work.
- **Keep only the tail JSON and exit code.** This is the confirmed defect: a
  later non-zero exit removes both permanently.
- **Emit one warning per run.** A run can degrade many messages before its
  tail disappears; one warning would make the surviving record incomplete.

## Contract impact

`docs/CONTRACT.md` section 11 will state that `quote_flattened` and
`skipped_unsupported` are announced on stderr as each durable degradation is
recorded. Flags, JSON fields, plain columns, and exit codes are unchanged.
Because this changes the observable behaviour of the released `clone sync`
command, it ships as a patch release.
