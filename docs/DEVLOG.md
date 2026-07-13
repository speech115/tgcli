# DEVLOG

Append-only session log. Newest entry on top. Every agent session that
touches this repo adds one entry (AGENTS.md rule).

Template:

```markdown
## YYYY-MM-DD — <short title> (<agent/model>)
**Did:** what actually changed (files, commands, results)
**Decided:** decisions made + link to ADR if architectural
**Learned:** surprises, gotchas, dead ends worth remembering
**Next:** the single most useful next step
```

---

## 2026-07-13 — Mock provisioning dispatcher hardened (Codex GPT-5)
**Did:** added an injected async dispatcher boundary that persists `prepared`
and `dispatched` checkpoints before calling a fake executor, validates a
normalized observation, then persists `confirmed`. Exceptions, cancellation,
and malformed observations persist `ambiguous`. Create observations record a
unique positive peer id plus verified lab marker and add the exact cleanup
obligation. Provisioning journals must remain a continuous serial plan prefix.
**Decided:** a successful callback return is not evidence by itself. Create
intents require exact peer-role/id/marker evidence, later intents cannot run
before prior confirmation, and `create` cannot advance without all expected
peer records and cleanup obligations. The dispatcher remains dependency-
injected and has no Telegram client, session, or live request wiring.
**Learned:** confirming an RPC-shaped operation without persisting its created
peer would strand cleanup and make resume unsafe. Likewise, stable intent keys
do not enforce topology order unless the persisted journal is validated as an
ordered prefix of the plan.
**Checks:** focused mirror lab/probe suite: `136 passed, 1 skipped`; full suite:
`344 passed, 9 skipped`; namespace coverage: `coverage OK: 23 namespaces`;
`git diff --check` clean. No live Telegram access or mutation ran.
**Next:** materialize the five allowed provisioning intent families into pinned
Telethon request objects under mocked resolved-role tests, still without calls.

## 2026-07-13 — Provisioning intent journal made resumable (Codex GPT-5)
**Did:** added pure checkpoint operations that prepare only exact scenario-plan
intents, mark them dispatched, record confirmed/ambiguous/blocked outcomes, and
reconcile uncertain dispatches from explicit observed/absent evidence. The
`create` phase now refuses to advance until every exact provisioning intent for
that cell exists and is confirmed.
**Decided:** an ambiguous or merely dispatched intent can never be sent again
directly. It remains blocked for reconciliation; only an explicit positive
observation confirms it, while an explicit absence returns it to `prepared`.
Stable journal entries contain no server result payload or resolved identity.
**Learned:** a resumable phase enum is not enough by itself. Without binding
phase advancement to the complete exact intent set, a caller could skip
provisioning work and still advance from `create` to `seed`.
**Checks:** focused mirror lab/probe suite: `131 passed, 1 skipped`; full suite:
`339 passed, 9 skipped`; namespace coverage: `coverage OK: 23 namespaces`;
`git diff --check` clean. No live Telegram access or mutation ran.
**Next:** add a fake-client dispatcher that persists prepared/dispatched state
before invoking one mocked request and records ambiguous exceptions safely.

## 2026-07-13 — Expanded provisioning intents defined (Codex GPT-5)
**Did:** added pure stable-key provisioning intents for all 16 scenarios using
the pinned Telethon schema's exact operation families: `messages.createChat`
for the disposable basic source, `channels.createChannel` for channels,
supergroups and forums, `messages.toggleNoForwards` for independent source and
discussion protection, and `channels.setDiscussionGroup` for both source and
destination channel discussions. Every linked discussion now has a preceding
`channels.getGroupsForDiscussion` eligibility intent.
**Decided:** intents contain only semantic peer roles, booleans, topology flags,
and stable scenario-local reconcile keys. They contain no aliases, user ids,
access hashes, resolved entities, titles, or executable Telegram requests.
**Learned:** forum creation is represented directly by the pinned
`channels.createChannel(..., megagroup=True, forum=True)` schema, while basic
groups require `messages.createChat` with the controlled `lab_peer`. Discussion
eligibility must be an explicit verified step before linking, not an assumption.
**Checks:** focused mirror lab/probe suite: `127 passed, 1 skipped`; full suite:
`335 passed, 9 skipped`; namespace coverage: `coverage OK: 23 namespaces`;
`git diff --check` clean. No live Telegram access or mutation ran.
**Next:** add a mocked dispatcher boundary that journals each stable intent as
prepared before dispatch and leaves ambiguous outcomes resumable.

## 2026-07-13 — Expanded topology preflight plans added (Codex GPT-5)
**Did:** added a pure privacy-safe preflight planner that binds every selected
scenario to its exact source/destination family, independent source/discussion
protection, linked plain/forum shape, content profile, required domains, and
account-role requirements. Basic-group scenarios now fail preflight without a
distinct validated `lab_peer` binding and explicitly normalize to an owner-only
private destination supergroup. Also replaced a flaky probe privacy assertion
that searched for the digits `999` inside timestamps/hashes with direct checks
that raw `peer_id` structure and value are absent.
**Decided:** preflight output exposes role names and topology requirements only;
it never includes aliases, numeric user ids, digests, or fingerprint payloads.
No provisioning request or Telegram client call exists in this slice.
**Learned:** substring scanning serialized reports is not a valid privacy test:
random timestamps and hashes can contain the same digits as a fixture id. Tests
must assert the forbidden field/value structurally.
**Checks:** focused mirror lab/probe suite: `122 passed, 1 skipped`; full suite:
`330 passed, 9 skipped`; namespace coverage: `coverage OK: 23 namespaces`;
`git diff --check` clean. No live Telegram access or mutation ran.
**Next:** define mocked provisioning intents for basic groups, supergroups,
forums, channels, and linked plain/forum discussions, with no dispatcher yet.

## 2026-07-13 — Resumable scenario phase state added (Codex GPT-5)
**Did:** added deterministic all-or-one scenario selection, validated resume-phase
lookup, and pure sequential checkpoint advancement across the accepted
`preflight` through `complete` phases. RED→GREEN tests cover unknown targets,
malformed checkpoints, out-of-order confirmation, defensive copying, terminal
idempotency, and the cleanup gate before completion.
**Decided:** the first runner foundation remains three small dict-based pure
functions. It introduces no generic execution framework and no Telegram calls;
future mocked phase handlers must reconcile their own marked operations before
using these transitions.
**Learned:** phase order alone is insufficient for safe completion. Teardown can
advance only when obligations are empty and the independent cleanup domain is
green, otherwise resume remains explicitly blocked at teardown.
**Checks:** focused mirror lab/probe suite: `116 passed, 1 skipped`; full suite:
`324 passed, 9 skipped`; namespace coverage: `coverage OK: 23 namespaces`;
`git diff --check` clean. No live Telegram access or mutation ran.
**Next:** add a mocked preflight/provisioning planner that binds each selected
scenario to its exact topology, protection, discussion, and account-role needs.

## 2026-07-13 — Aggregate mirror compatibility gate completed (Codex GPT-5)
**Did:** added pure fingerprint comparison, topology-specific required domains,
30-day freshness and clock checks, per-cell verdict classification, and strict
16-cell aggregate acceptance for the ADR-0015 topology/protection matrix. After
taking over an interrupted delegated slice, added RED→GREEN regressions proving
that an unsupported checkpoint version, malformed checkpoint envelope, and an
unknown scenario key cannot be classified as valid or merely missing.
**Decided:** targeted green evidence never upgrades aggregate acceptance; every
required cell must be fresh, fingerprint-compatible, complete, all-domain green,
and independently cleanup-green. Pure classification remains local and uses
aware `datetime` values as specified; persistence, CLI scheduling, and live
Telegram mutation remain outside this slice.
**Learned:** validating fingerprints and verdicts was not sufficient at the
public classifier boundary: without re-validating the checkpoint envelope, a
caller could bypass the manifest loader and present an unsupported checkpoint
version as machine-compatible.
**Checks:** focused mirror lab/probe suite: `113 passed, 1 skipped`; full suite:
`321 passed, 9 skipped`; namespace coverage: `coverage OK: 23 namespaces`;
`git diff --check` clean. No live Telegram access or mutation ran.
**Next:** implement the first mocked resumable scenario-engine slice before any
controlled-live provisioning; actual chat creation remains a human-gated step.

## 2026-07-13 — Versioned scenario checkpoints and fingerprints added (Codex GPT-5)
**Did:** upgraded the lab manifest to version 3 with an in-memory-only v2
migration, empty per-scenario checkpoint storage, versioned checkpoint and
compatibility-fingerprint builders, and strict validation for the 16 frozen
scenario keys, phases, digests, dependency/schema facts, and account-role
bindings. The vertical TDD cycles observed focused REDs before each behavior;
the final review regression reproduced and then blocked a neighboring-cell
fingerprint from being silently reclassified by the checkpoint builder.
**Decided:** legacy v2 channel recovery data never becomes expanded scenario
evidence. Loading a valid v2 manifest preserves its data, adds no compatible
cell, and does not rewrite the file. Fingerprints remain bound to exactly one
scenario in both the public builder and persisted-manifest validator.
**Learned:** defensive copying alone is insufficient when a builder also
normalizes identity fields: rewriting `scenario_key` could manufacture a green
result for a different topology/protection cell. Identity mismatch must fail
closed before copying.
**Checks:** focused mirror lab/probe suite: `85 passed, 1 skipped`; full suite:
`293 passed, 9 skipped`; `git diff --check` clean. Terra performed one narrow
read-only specification pass. Sol's first read-only gate found the cross-cell
fingerprint blocker; after the Luna RED→GREEN fix, Sol's focused re-review was
clean (`2 passed`). No live Telegram access or mutation ran.
**Next:** add pure compatibility comparison and aggregate stale/missing/blocked
classification before provisioning any expanded topology; groups, forums, and
comments remain unsupported without controlled-live evidence.

## 2026-07-13 — Economical model-routed mirror agents configured (Codex GPT-5)
**Did:** added project-scoped custom agents for bounded Luna implementation,
read-only Terra investigation, and read-only Sol xhigh risk review. Added a
three-thread, depth-one, 20-minute worker cap to prevent recursive or runaway
delegation.
**Decided:** Luna may edit only assigned pure/mocked TDD slices and never access
live Telegram; Terra is read-only and evidence-focused; Sol is reserved for the
final crash-safety, privacy, cleanup, and controlled-live plan gate. Telegram
mutations remain with the parent agent under the accepted lab policy.
**Learned:** current Codex supports per-agent `model` and
`model_reasoning_effort` through project agent files even though the direct
spawn call does not expose an inline model field. Agent configuration is loaded
for sessions started from this worktree.
**Checks:** all four TOML files parsed with Python `tomllib`; `codex
--strict-config doctor --json` reported `config loaded` for this worktree and
the active GPT-5.6 Sol configuration. No subagent or Telegram mutation ran.
**Next:** start a new Codex task from this worktree so the three roles are loaded,
then assign only the next checkpoint/fingerprint TDD slice to Luna.

## 2026-07-13 — Expanded topology scenario taxonomy started with TDD (Codex GPT-5)
**Did:** added a pure immutable scenario model covering all 16 accepted
topology/protection cells, independent channel/discussion protection, full versus
sentinel content allocation, and basic-group destination normalization. RED was
observed separately for missing scenario enumeration, content allocation,
protection fields, and destination normalization before each minimal GREEN.
**Decided:** the exact matrix is executable code rather than a prose-only list;
later provisioning and verdicts must consume these stable scenario specs.
**Learned:** the matrix contains eight standalone full-content cells and eight
linked discussion sentinel cells. Encoding discussion protection separately
prevents an open channel result from concealing a protected discussion failure.
**Checks:** focused mirror lab/probe suite: `73 passed, 1 skipped`; full suite:
`281 passed, 9 skipped`; `git diff --check` clean.
**Next:** add versioned per-scenario checkpoints and compatibility fingerprints
with pure manifest migration tests before implementing any Telegram mutation.

## 2026-07-13 — Visual promotion and presentation-release gate accepted (Codex GPT-5)
**Did:** documented when visual approval blocks topology promotion and releases,
its 30-day lifetime, presentation compatibility fingerprint, and the narrower
machine-only rule for focused development.
**Decided:** first production enablement and presentation-affecting releases need
fresh `visual_approved`. Non-presentation development may use `machine_green`,
but release evidence must prove the visual fingerprint was unaffected.
**Learned:** requiring a reviewer for every debug rerun would slow safe recovery,
while making review informational would allow native Telegram presentation to
regress exactly at promotion time. A scoped release gate preserves both speed
and accountability.
**Next:** execute the accepted topology expansion through the TDD slices in the
mirror plan, starting with versioned scenario keys, manifests, and classifier
coverage before any new Telegram mutation.

## 2026-07-13 — Opt-in sanitized visual bundles accepted (Codex GPT-5)
**Did:** documented default no-capture behavior, explicit local review bundles,
scope/redaction gates, owner-only storage, raw-buffer cleanup, 30-day retention,
and verified lazy purge semantics for the stateless CLI.
**Decided:** screenshots exist only with `--capture-review` and never include
unrelated dialogs, member lists, private identifiers, notifications, or account
switching UI. Unsafe frames are discarded rather than retained.
**Learned:** a no-daemon CLI cannot guarantee deletion at an unattended wall
clock instant. It can make expired evidence invalid immediately and verify
physical purge before every later lab action or through an explicit command.
**Next:** decide where `visual_approved` is mandatory: development diagnostics,
topology promotion, or every release acceptance.

## 2026-07-13 — Bounded foreground visual-review lease accepted (Codex GPT-5)
**Did:** documented a 30-minute visual-review lease, explicit extensions capped
at two hours, foreground teardown triggers, crash recovery, and a cleanup-first
gate on every later lab invocation.
**Decided:** tgcli gains no daemon. A live foreground runner tears down on
approval, rejection, cancellation, or expiry; after process/host loss the
durable manifest blocks new fixture creation until cleanup is reconciled.
**Learned:** a claimed wall-clock expiry is not deletion evidence. Stateless CLI
safety requires a persistent cleanup obligation and verification on the next
invocation when no process survived to execute the timer.
**Next:** choose whether visual evidence retains screenshots or only a local
checklist verdict and privacy-safe metadata.

## 2026-07-13 — Independent machine and visual acceptance accepted (Codex GPT-5)
**Did:** documented separate `machine_green` and `visual_approved` verdicts, an
explicit representative visual-review mode, its native Telegram checklist, and
the durable pre-teardown review checkpoint.
**Decided:** the full matrix remains unattended machine evidence. Visual review
uses one open and protected representative per topology and requires an explicit
human approve/reject; neither verdict substitutes for the other.
**Learned:** protocol mappings can be correct while Telegram presentation feels
unnatural, but screenshots also cannot prove resume, idempotence, or coverage.
The two evidence classes need independent gates.
**Next:** choose the no-response timeout and cleanup behavior while a disposable
visual-review scenario is waiting for a human.

## 2026-07-13 — Thirty-day compatible-evidence lifetime accepted (Codex GPT-5)
**Did:** documented immediate fingerprint invalidation, a 30-day lifetime for
otherwise compatible controlled-live results, selective stale-cell reruns, and
explicit completion/expiry/stale-reason reporting.
**Decided:** code, fixture, dependency, schema, account-role, or configuration
drift invalidates affected evidence immediately. Purely time-stale evidence is
rerun cell by cell; unrelated fresh results remain valid.
**Learned:** fingerprints detect local drift but not silent Telegram server-side
behavior changes. A bounded lifetime supplies that missing live revalidation
without forcing the full mutation matrix on every acceptance run.
**Next:** decide whether organic-copy acceptance needs a human visual gate in
addition to machine-verifiable topology and content invariants.

## 2026-07-13 — Resumable scenario and aggregate runner accepted (Codex GPT-5)
**Did:** documented phase checkpoints, targeted scenario execution, serial
aggregate acceptance, compatibility fingerprints, explicit non-pass states, and
evidence-backed resume/cleanup reconciliation.
**Decided:** one interrupted or red cell does not erase compatible evidence from
other cells. A targeted pass never becomes overall acceptance; the aggregate
requires every required cell and a separate green teardown for each scenario.
**Learned:** a monolithic live run conflates Telegram transport failure with
orchestration failure and encourages unsafe recreation after ambiguous replies.
Durable per-scenario manifests make both retry and cleanup auditable.
**Next:** choose the time-based freshness lifetime for otherwise compatible live
evidence so server-side Telegram drift cannot remain green forever.

## 2026-07-13 — Exact expanded content inventory frozen (Codex GPT-5)
**Did:** reconciled the R1 classifier and fixtures against Telethon 1.44 layer
227, then documented full authored, family-scoped, observation-negative,
action-specific service, and excluded content classes.
**Decided:** webpage becomes an authored hydrated fixture; protected non-byte
content needs semantic reconstruction evidence; todo and live location are
group-family probes; `MessageMediaVideoStream` and custom emoji receive explicit
classification instead of generic fallthrough. Version/layer drift invalidates
inherited verdicts until compatibility is checked.
**Learned:** the old 15-kind broadcast result did not author webpage, collapsed
all service actions, skipped protected non-byte reconstruction, and could not
prove any group/forum capability. Content verdicts must be peer-family scoped.
**Next:** freeze the resumable execution and final aggregation contract for the
larger live scenario suite.

## 2026-07-13 — Peer-family content covering matrix accepted (Codex GPT-5)
**Did:** documented structural sentinels per protection cell and full content
suites per open/protected peer family, including group/forum re-probes for todo
and live location.
**Decided:** linked discussion cells prove topology coupling and mixed protection
without duplicating every media upload. No content result crosses peer-family or
protection boundaries.
**Learned:** a covering design preserves evidence strength at Telegram's actual
behavior boundaries while avoiding a redundant full Cartesian upload matrix.
**Next:** reconcile the exact R1 content inventory with the pinned Telegram
schema and classify additions, exclusions, and family-specific probes.

## 2026-07-13 — Full structural fidelity matrix accepted (Codex GPT-5)
**Did:** documented the required profile, content, reply, pin, edit/delete,
membership, migration, topic, comment-root, teardown, and idempotent rerun
evidence; added structural fidelity to the glossary and acceptance plan.
**Decided:** a group or forum is not complete when only message transport works.
Calls, boosts, reactions, and monetization remain explicit exclusions.
**Learned:** content fidelity and topology fidelity are independent verdicts;
collapsing them would let valid media conceal broken forum or comment structure.
**Next:** decide how the full content-kind matrix is distributed across the many
topology/protection cells without creating redundant Telegram mutations.

## 2026-07-13 — Bounded active lab-peer script accepted (Codex GPT-5)
**Did:** documented the exact lab-peer mutation allowlist, per-action audit and
state verification, two-account teardown proof, and ambiguous-cleanup stop rule.
**Decided:** the secondary user sends one marked text, one generated media item,
and one reply; receives and loses admin; leaves and is re-added. It never touches
another dialog or uses personal content.
**Learned:** passive membership cannot prove author attribution, replies, role
facts, leave/rejoin handling, or their service updates; a bounded script can do
so without granting the lab open-ended write authority.
**Next:** freeze the structural event matrix required to call groups and forums
complete in the expanded lab.

## 2026-07-13 — Existing secondary alias verified for lab-peer role (Codex GPT-5)
**Did:** selected local alias `recklessou` as the deployment-specific lab-peer
and ran read-only `tg info me` checks for it and `main`. Both sessions were
authorized user accounts with distinct canonical ids; ids remain redacted from
repository documentation.
**Decided:** portable code and defaults do not hardcode `recklessou`. The alias
is supplied explicitly to the local lab manifest, which binds the verified ids
before any mutation.
**Learned:** an already configured controlled secondary user account satisfies
the basic-group fixture identity requirement without provisioning another
Telegram account.
**Next:** decide whether the lab peer is passive or performs a bounded set of
messages, replies, membership, and role transitions for fidelity evidence.

## 2026-07-13 — Dedicated user account selected for basic-group lab fixtures (Codex GPT-5)
**Did:** documented the lab-peer account role, identity-bound manifest preflight,
and acceptance rule forbidding bots or unrelated contacts as basic-group fixture
participants.
**Decided:** a dedicated secondary Telegram user account is invited only to a
uniquely marked disposable basic-group source. It never joins production
destinations, and any alias/auth/identity mismatch blocks before mutation.
**Learned:** a bot satisfies Telegram's participant count but does not prove
ordinary user membership, permissions, or service-message behavior.
**Next:** inspect registered local tgcli aliases without opening sessions, then
freeze provisioning if no dedicated lab-peer account exists.

## 2026-07-13 — Basic groups normalized into lineage-aware supergroups (Codex GPT-5)
**Did:** documented owner-only supergroup normalization, stable source lineage,
predecessor discovery, peer-scoped cursors, and migration-boundary tests; updated
the proposed ledger schema accordingly.
**Decided:** a basic-group source never causes invitations. Its history and any
migrated supergroup successor feed one destination and one lineage-root mirror
identity while retaining distinct peer/message keys.
**Learned:** canonical current peer id is not a stable mirror identity across
Telegram migration; the earliest discoverable predecessor must anchor identity.
**Next:** choose the controlled second identity required to create disposable
basic-group source fixtures without involving an unrelated real person.

## 2026-07-13 — Owner-only basic-group constraint confirmed (Codex GPT-5)
**Did:** checked current official Telegram MTProto documentation for basic-group
and supergroup creation and recorded the constraint in ADR-0015.
**Decided:** no destination decision yet. The grill must choose explicitly
between a supergroup destination, inviting a real participant to manufacture a
basic group, or dropping basic-group support.
**Learned:** `messages.createChat` requires invited users and may return
`USERS_TOO_FEW`; `channels.createChannel(megagroup=True)` can create the required
owner-only private supergroup without involving another person.
**Next:** choose the legacy basic-group destination and migration-lineage rule.

## 2026-07-13 — Verified membership change history accepted (Codex GPT-5)
**Did:** documented normalized current membership state, idempotent append-only
change facts, and complete-scan plus targeted-lookup evidence for departures.
**Decided:** membership retains `joined`, `left`, `role_changed`, and
`name_changed` facts without full repeated snapshots. Incomplete visibility,
access loss, cancellation, or FloodWait never produces a `left` fact.
**Learned:** member absence needs the same evidence discipline as source message
deletion; one incomplete iterator cannot distinguish departure from invisibility.
**Next:** verify Telegram's owner-only basic-group creation constraints before
freezing legacy-group destination and migration behavior.

## 2026-07-13 — Minimal privacy-safe membership snapshot accepted (Codex GPT-5)
**Did:** froze allowed membership fields, completeness metadata, forbidden
profile fields, and the aggregate-status versus explicit-local-export boundary.
**Decided:** local rows retain only numeric id, display name, public username,
role, bot/deleted flags, and observation time. Phone, bio, access hash, and
profile-photo bytes are forbidden; row-level output is always explicit and local.
**Learned:** stable author correlation needs a local peer id, but normal status
and logs do not need row-level personal data.
**Next:** decide whether membership history overwrites the last snapshot or
records verified changes over time.

## 2026-07-13 — Owner-only destinations with local membership snapshots accepted (Codex GPT-5)
**Did:** documented private owner-only destinations, non-mutating membership
snapshots, and acceptance checks forbidding member, role, and ban replication.
**Decided:** source participants and roles may be observed in private local
mirror state but are never invited or posted into the destination. Incomplete
Telegram visibility is reported as such, not upgraded to a complete member list.
**Learned:** preserving membership as evidence is separate from recreating a
community; the latter would notify and affect real people outside the archive.
**Next:** freeze the minimum privacy-safe fields and completeness metadata stored
in a membership snapshot.

## 2026-07-13 — Full protected topology matrix accepted (Codex GPT-5)
**Did:** documented the open/protected matrix across basic groups, standalone
supergroups/forums, and linked plain/forum discussions; added corresponding
production-phase acceptance gates and the glossary term.
**Decided:** every matrix cell requires independent controlled-live evidence.
Disposable scenarios run sequentially and must tear down completely before the
next cell; no result is inferred from a neighboring topology or protection
combination.
**Learned:** channel protection and linked-discussion protection are independent
capabilities, so one protected broadcast fixture cannot prove comment or topic
transport behavior.
**Next:** decide whether group mirrors copy membership and permissions or remain
private content archives owned only by the operator.

## 2026-07-13 — Full comment threads for mirrored posts accepted (Codex GPT-5)
**Did:** added the comment-coverage domain term and documented full per-post
thread backfill, resumable paging, watcher continuation, and excluded-root
reporting in ADR-0015 and the production plan.
**Decided:** every mirrored channel post receives its complete accessible
comment history regardless of comment dates. Comments for posts outside the
authorized mirror range are not copied and remain visible as excluded coverage.
**Learned:** applying the post date window to individual comments creates
arbitrarily truncated discussions; the post root, not comment date, owns the
coverage boundary.
**Next:** decide the protected/unprotected topology matrix required for lab
acceptance.

## 2026-07-13 — Exact channel-discussion topology parity accepted (Codex GPT-5)
**Did:** added discussion-topology parity to ADR-0015 and the glossary, and
expanded the planned creation/comment acceptance gates with a controlled live
forum-link proof.
**Decided:** no source discussion creates none; a plain discussion stays plain;
a forum discussion stays a forum. A failed live forum-link gate blocks that
topology and never authorizes silent downgrade.
**Learned:** Telegram's public schema documents discussion linking and forums
separately but does not itself prove their combined behavior; an auto-forwarded
root and reply roundtrip are required evidence.
**Next:** decide the history boundary for comments attached to mirrored channel
posts.

## 2026-07-13 — Parent dependency queue and proven fallback accepted (Codex GPT-5)
**Did:** documented durable parent dependencies, the unavailable-parent domain
term, and exact M2 reply/comment acceptance behavior in ADRs and the mirror plan.
**Decided:** an unmapped child waits for its peer-scoped parent mapping. Only a
complete-range scan plus targeted lookup may release it through an explicit
unavailable-parent fallback; it is never flattened early, misattached, or
silently dropped.
**Learned:** dependency absence and proven parent absence are different states,
just as missing iterator output is not deletion evidence under ADR-0013.
**Next:** decide the destination topology for channel discussions that are not
forums at the source.

## 2026-07-13 — Capability-based source attribution accepted (Codex GPT-5)
**Did:** added the source-attribution glossary term and documented native-forward
and reconstructed author presentation in ADR-0015 and ADR-0013.
**Decided:** comments and forum messages retain Telegram's official forwarded
author header when permitted. Protected or otherwise non-forwardable messages
use a compact display-name/public-username label and never expose phone numbers,
numeric ids, or access hashes.
**Learned:** source attribution is presentation data, not sender identity; the
destination message remains authored technically by the mirror account.
**Next:** decide how replies and comments behave when their mapped parent is not
yet available or cannot be copied.

## 2026-07-13 — Native topic state with append-only history accepted (Codex GPT-5)
**Did:** extended ADR-0015 with forum-topic fidelity rules, clarified ADR-0013's
append-only boundary, and added the archived-topic glossary term.
**Decided:** destination topic title, icon/color, and open/closed state follow
the current source while every transition is recorded idempotently. Source
topic deletion closes and archives the destination topic instead of deleting
copied history.
**Learned:** native current-state fidelity and archival history are compatible
when mutable topic metadata is separated from immutable copied messages and
changelog facts.
**Next:** decide how source authors are represented in copied comments and forum
messages when Telegram cannot impersonate them.

## 2026-07-13 — Staged production topology expansion accepted (Codex GPT-5)
**Did:** recorded ADR-0015, amended ADR-0013's scope boundary, and extended the
glossary and project map for basic groups, supergroups, and the topology matrix.
**Decided:** the final product includes channels with linked comments,
standalone supergroups, standalone forum supergroups, and legacy basic groups.
Delivery is lab-first, then channel discussions, then standalone
supergroups/forums, then basic groups.
**Learned:** the existing channel-first ADR remains a useful first milestone;
expanding the final boundary does not require mixing every topology into one
implementation or allowing incomplete lab evidence into production routing.
**Next:** freeze forum-topic fidelity requirements before designing the expanded
lab fixtures and acceptance matrix.

## 2026-07-13 — Both forum topologies made explicit (Codex GPT-5)
**Did:** added the project glossary and defined standalone forum supergroups,
channel discussions with topics, topics, channel comments, and organic channel
copies as distinct domain concepts. Updated MAP for the new glossary.
**Decided:** the required topology matrix includes both a standalone forum
supergroup and a channel-linked discussion group with topics enabled. This does
not yet decide whether both become production mirror sources or remain lab
fixtures until a later phase.
**Learned:** testing topic mechanics in a standalone forum does not prove the
post-to-comment-root mapping required by a channel-linked forum discussion.
**Next:** decide the production scope and staged delivery order before amending
ADR-0013 or writing implementation tests.

## 2026-07-13 — Mirror topology scope audit before forum grill (Codex GPT-5)
**Did:** audited the accepted R1 controlled lab, production mirror plan, ADR-0013,
and focused mirror tests before expanding the lab to forums and linked comments.
The focused suite reported `79 passed, 1 skipped`; an independent full-suite run
reported `277 passed, 9 skipped`. No Telegram state was changed.
**Decided:** no scope decision yet. The next design gate must distinguish a
standalone forum supergroup from a channel-linked discussion group with topics
before changing the lab or production plan.
**Learned:** R1 currently proves content transports only between owned broadcast
channels and explicitly rejects megagroups. Basic groups, supergroups, forums,
and linked channel comments have no mirror lab or live acceptance evidence;
production `tg mirror` remains planned.
**Next:** resolve which forum topologies are required, then update the glossary
and topology matrix before writing implementation tests.

## 2026-07-13 — Final R1 controlled-lab acceptance (Codex GPT-5)
**Did:** completed two repaired R1 runs, including a fresh post-review run
against manifest-v2 channels, with Telethon 1.44.0 at layer 227 and probe
schema 2. Manifest v2 binds each role to an exact random lab id in its live
title, persists in-flight creates, reconciles accepted ambiguous creates
without duplication, and preserves ambiguous delete entries. Blocked kinds or
old probe schemas now force red; transport verdicts require the copy-phase
report and the protected-forward gate; reupload albums require exact ordered
hashes. Both source roles
seeded all 15 supported kinds: album, animation, audio, contact, dice,
document, geo, photo, poll, sticker, text, venue, video, video note, and voice.
The protected-source probe was green, the expected
`ChatForwardsRestrictedError` was confirmed, native copy passed all 15 rows,
and protected download/reupload passed all 9 applicable byte/album rows. Album
comparison preserved two distinct ordered items. The post-review run's 64 live
operations had
paired attempt/result audit records; the only failed result was the expected
protected-forward rejection. Teardown deleted all four channels and left the
manifest with no channel or in-flight create entries.
The final independent re-review reported no Critical or Important findings.
Fresh closeout checks reported 277 passed, 9 skipped; fixture smoke reported
63 passed; and the coverage gate covered all 23 namespaces.
**Decided:** R1 is accepted for the narrowed ADR-0013 capability matrix.
`todo` remains explicitly unsupported after `MediaInvalidError`, and
`geo_live` remains explicitly unsupported because native forwarding degraded
it to static `geo`; neither is silently counted as supported.
**Learned:** valid containers, server-returned subtype classification, a
complete expected matrix, copy-phase evidence, and exact album comparison are
all necessary for a meaningful transport verdict. An unavailable channel after
an ambiguous delete is not deletion proof, so its manifest entry must remain.
**Next:** wait at the push boundary; publish only after the user's next
explicit instruction.

## 2026-07-12 — R1 safety and fixture repair before rerun (Codex GPT-5)
**Did:** independently audited the GLM 5.2 R1 branch, reproduced a false-green
transport verdict, and repaired the lab before any merge. Manifest loading now
validates roles, markers, peer ids, and uniqueness; every mutation revalidates
the exact live title, creator ownership, and broadcast type. Kill switches and
fixture preflight run before config/session access. Protected-source setup and
teardown are resumable, and every mutation emits correlated attempt/result
audit records. Verdicts require the manifest's complete expected matrix and
album grouping. Valid named MP3/OGG/MP4/WebP fixtures are generated only in a
temporary directory under ADR-0014; `todo` is explicitly unsupported under
ADR-0013 after the live `MediaInvalidError` evidence. Restored the R0 DEVLOG
heading and synchronized MAP, ADRs, and the committed R1 plan. Corrected the
server-attribute-order classifier and bumped probe reports to schema 2; schema-1
R0 hashes remain byte-access evidence but subtype labels are not mixed with v2.
**Decided:** a red or collapsed lab result never upgrades to transport fidelity
evidence. R1 may merge only after a fresh controlled run returns complete green
verdicts for the supported matrix.
**Learned:** Telegram classifies actual container bytes, not claimed filenames
or `DocumentAttribute*`; named paths plus explicit MIME are required for a
meaningful fixture lab. The repaired live run also proved that Telegram native
channel forwarding converts `geo_live` into static `geo`; that kind is now an
explicit unsupported placeholder rather than a silent fidelity loss.
**Next:** commit the repaired harness, rerun all seven live phases against new
disposable channels, tear them down, and record the fresh evidence.

## 2026-07-11 — Initial R1 controlled-lab run (GLM 5.2 Deep via OpenCode)

**Did:** implemented `src/tgcli/mirror_lab.py` (manifest model, frozen fixture
matrix, verdict/fidelity comparison, provisioning/seeding/copy/teardown engines)
and `scripts/mirror_lab.py` (7-phase CLI entrypoint) in worktree
`claude/mirror-r1-controlled-lab` via 6 TDD commits. 249 passed, 8 skipped;
coverage gate green (23 namespaces). `mirror_probe.py` unchanged.

Ran the initial live acceptance (account `main`, Telethon 1.44, layer 227) against
4 disposable lab channels (`tgcli-r1-lab` marker prefix).

**Seeding results** (both `protected_source` and `open_source`):
16 of 17 planned kinds `seeded`; `todo` = `blocked:MediaInvalidError` on both
roles (server rejects `InputMediaTodo` for broadcast channels — explicit
unsupported, not a silent skip). `album` = 2 photos grouped. All non-byte kinds
(contact, dice, geo, geo_live, poll, venue) seeded and decoded `pass`.

**Protected-source probe verdict:** `red`. 7 byte kinds (animation, audio,
photo, sticker, video, video_note, voice) missing — Telegram classified all lab
payloads as `document` because raw deterministic bytes are not valid media
containers (mp3, mp4, ogg, webp). `document` itself returned `telethon_bytes:
pass` with 3 samples. Non-byte kinds all `pass`. `protected: true` confirmed.

**`restricted_check`:** `confirmed` — `CHAT_FORWARDS_RESTRICTED` raised on
native `forwardMessages(drop_author=True)` from protected source to dest, exactly
as ADR-0013 §10 predicts.

**Native forward (open_source → dest_native):** all 16 kinds `forwarded`.
**Reupload (protected_source → dest_reupload):** all 9 byte kinds + album
`copied`; 7 non-byte kinds `not_applicable`.

**Fidelity verdicts:** both `red`. The run proved that both transport phase
calls completed for the seeded messages, but it did not prove per-kind media
fidelity. Invalid synthetic MP3/MP4/OGG/WebP payloads were classified by
Telegram as `document`, collapsing distinct fixture kinds into one report
bucket. A successful phase call is not fidelity evidence.

**Excluded kinds (unchanged):** game, giveaway, giveaway_results, invoice,
paid_media_preview, paid_media_revealed, story.

**Decided:** `todo` follows R1 Decision Gate Branch 2 and becomes explicitly
unsupported with `MediaInvalidError` evidence. `CHAT_FORWARDS_RESTRICTED`
confirms ADR-0013 §10's protected-source routing assumption. Overall R1 remains
red/inconclusive because neither transport fidelity verdict passed; it does not
authorize per-kind renderer behavior or close the content gate.

**Learned:** the probe classifier depends on Telegram server-side media-type
detection, which ignores `DocumentAttribute*` on non-container bytes. To get
per-kind fidelity in a future lab run, fixtures need valid minimal containers
(valid mp4 for video/animation/video_note, valid ogg for voice, valid webp for
sticker, valid mp3 for audio) — or the probe must classify by sent fixture kind
rather than by server-returned attributes. The `Poll` constructor on layer 227
requires `hash` as a positional argument (discovered during TDD).

**Next:** repair manifest/account safety and verdict completeness, replace
arbitrary bytes with valid minimal containers, rerun the live lab, and require
green per-kind fidelity before merge or M1/M2 reliance.

## 2026-07-11 — Configure project-local GLM provider (Codex)
**Did:** added `opencode.json` with the OpenAI-compatible ai& endpoint, the
`zai-org/glm-5.2` model, an environment-backed API key, a 600s request timeout,
a 120s streamed-chunk timeout; updated `docs/MAP.md`. Verified the
Keychain-backed shell environment from the `tgcli` directory and confirmed
`GET /v1/models` returns GLM 5.2.
**Decided:** keep the provider configuration project-local while keeping the
credential exclusively environment-backed; no API key is stored in the repo.
**Learned:** direct streamed GLM requests returned HTTP 200 in about 5 seconds.
Do not constrain model reasoning to diagnose transport issues: that changes
answer quality rather than repairing the connection.
**Next:** launch `opencode` from this directory and select `GLM 5.2 Fast`,
`GLM 5.2 Normal`, or `GLM 5.2 Deep` in `/models` as appropriate.

## 2026-07-11 — Merged capability design; wrote R1 controlled-lab plan (Claude Fable 5)
**Did:** fast-forwarded `main` to `b26ac94` (13 commits: R0 probe + gotd-free
capability consolidation), verified `pytest -q` green on the merge result
(214 passed, 8 skipped), pushed, and deleted `codex/mirror-capability-design`.
Wrote `docs/superpowers/plans/2026-07-11-mirror-r1-controlled-lab.md`: a
7-task TDD plan for a disposable owned lab (4 marked channels via a local
manifest), seeding every self-authorable kind R0 could not observe
(`animation` plus owned-role `audio`/`document`/`sticker`/`voice`, and
`todo`/`contact`/`geo`/`geo_live`/`venue`/`dice`/`poll`), byte-proof via the
frozen R0 probe, and per-kind fidelity checks for both ADR-0013 §10
transports, including the expected `CHAT_FORWARDS_RESTRICTED` rejection.
**Decided:** kinds requiring bots/Premium/monetization (`game`, `giveaway*`,
`invoice`, `paid_media_*`, `story`) are explicit lab exclusions with reasons,
not silent gaps. Reupload fidelity is byte-exact for document-backed kinds
and re-encode-tolerant only for `photo`. Every lab mutation is gated by
`enforce_mutation_allowed`, audited, and restricted to manifest-listed peers.
**Learned:** M1 remains blocked by two independent evidence gates: M0 Task 0.1
(watcher-session concurrency) and a successful R1 acceptance run. R1 can run
independently of Task 0.1, but its result must not be presumed green.
**Next:** execute R1, record its actual verdict without upgrading red or
inconclusive evidence to green, then proceed only through the resulting
decision-gate branch.

## 2026-07-11 — R0 protected-content probe evidence (Claude Opus 4.8)
**Did:** ran the read-only mirror capability probe (`scripts/mirror_probe.py`,
commit 7de7c90) live against two real protected broadcast channels — one where
the account is owner, one where it is an ordinary subscriber. Runtime: Telegram
layer 227, Telethon 1.44.0. Both sources reported `protected: true`. Owned scan
covered 86 messages; subscriber scan covered 2394. The paired privacy validator
passed and `audit.jsonl` was byte-for-byte unchanged (no mutation).
**Decided:** R0 Decision Gate → **Branch 1**. Every discovered byte-bearing kind
returned Telethon `pass` for both account roles, so gotd is NOT required. The
next step is an R1 controlled-lab plan to fill `not_found`/`limited` kinds, not a
second backend.
**Learned:** Telethon streamed complete bytes (full SHA-256) for every media kind
in both roles, including a ~1.0 GB video read as an ordinary subscriber of a
`noforwards` channel — strong evidence the four-tier capability router collapses
to native-copy (unprotected) + Telethon-reconstruction (protected). Byte-`pass`
kinds: owned = photo, video, video_note; subscriber = audio, document, photo,
sticker, video, video_note, voice. `not_applicable` (no byte payload): text,
webpage, service, poll, story. Zero `fail`/`inconclusive`. Gotcha: an earlier
Codex run mis-selected an unprotected channel as the "owned protected" source,
which is why its paired validator failed; the real owned protected channel was
used here.
**Next:** consolidate ADR-0013 + the router design + the M0-M4 plan into one
gotd-free decision, then write the R1 controlled-lab plan.

## 2026-07-11 — Telegram message-type probe inventory expanded (Codex GPT-5)
**Did:** compared the current official Telegram Message/MessageMedia schema with
the pinned Telethon 1.44 constructors. No mirror plan or production code changed.
**Decided:** use a tiered probe matrix: P0 core content and structure, P1
interactive media, P2 transactional/service/edge cases. Test all document
subtypes separately even though TL represents them under MessageMediaDocument.
**Learned:** the pinned layer exposes 18 MessageMedia constructors and 58
MessageAction constructors; the current Telegram schema additionally lists
MessageMediaVideoStream, so new-layer/unsupported behavior needs an explicit
probe result instead of silent omission.
**Next:** freeze paid-media policy, then approve the complete read-only probe
design before writing or running it.

## 2026-07-11 — Native Telegram mirror simplification identified (Codex GPT-5)
**Did:** checked the proposed mirror architecture against the pinned Telethon
1.44 client/source and current official Telegram MTProto documentation. No plan
or production code was changed.
**Decided:** test a native-first path before executing the current M0-M4 plan:
raw `messages.forwardMessages(drop_author=True)` with persisted batch random ids,
plus Telethon `catch_up=True` for watcher gap recovery. Keep download/reupload out
of the primary path unless a protected-content fallback is explicitly chosen.
**Learned:** native server-side copy can remove most media rendering and temp-file
state for unprotected public/private channels. Official content protection
explicitly rejects forwarding/copying with `CHAT_FORWARDS_RESTRICTED`, so a
simple official design cannot promise protected-channel copying.
**Next:** run a disposable source/destination matrix probe before revising
ADR-0013 and the M0-M4 plan again.

## 2026-07-11 — Mirror ADR and plan made crash-safe (Codex GPT-5)
**Did:** rewrote proposed ADR-0013 and the M0-M4 implementation plan to address
the blocking review findings. Added a dedicated watcher-session topology,
peer-scoped ledger keys, durable random-id outbox transitions, resumable channel
creation, race-free watch startup, targeted deletion confirmation, and an
executable protected-content probe. Synchronized MAP, FEATURES, and historical
PLAN pointers; no production mirror code or Telegram state was changed.
**Decided:** M0 now has two hard gates: concurrent primary/watcher session proof
and a protected-content capability matrix. Implementation cannot begin until
both are green. Ambiguous sends must recover through the persisted random id;
ledger membership alone is not accepted as idempotency.
**Learned:** Telethon's high-level send helpers do not expose a caller-supplied
random id, while raw SendMessage/SendMedia/SendMultiMedia requests do; the plan
therefore freezes raw durable dispatch after upload.
**Next:** review/accept ADR-0013, then execute M0 only; stop before destination
creation unless both live evidence gates pass.

## 2026-07-11 — Mirror ADR and implementation plan reviewed (Codex GPT-5)
**Did:** reviewed untracked ADR-0013 and the M0-M4 plan against MAP, PLAN,
CONTRACT, FEATURES, ADR-0002/0005/0009, and the current session/safety/media/
export implementations. No mirror source code or proposed document was changed.
**Decided:** implementation is blocked pending a crash-consistency protocol,
race-free watcher startup, peer-scoped ledger keys, deletion-confirmation rules,
and an explicit answer for the exclusive session lock held by `watch`.
**Learned:** the current plan would make every other command for the watched
account fail busy; `tg api upload.getFile` is not available through the current
raw contract; audit state also contradicts the plan's `mirrors/`-only rule.
**Next:** revise ADR-0013 and the plan around session topology and a durable
`pending -> dispatched -> confirmed` operation state before starting M0/M1.

---

## 2026-07-11 — Bench default retargeted; PLAN.md marked historical (Claude Fable 5)
**Did:** the dr34m.txt channel was renamed to "MIR Сергея Иванова"
(@mir_ivanova) and is now reachable from the main account, so
`scripts/bench.py` defaults its subscribers step to `@mir_ivanova` instead of
the mirror-channel id (verified live: 20-row CSV export, exit 0). Added a
completed/historical status banner to docs/PLAN.md.
**Decided:** PLAN.md stays at its current path as a historical record — MAP,
README, and kb notes link to it and its Risks table is still operationally
current; new work gets a fresh scoped plan or ADR, never a new phase there.
**Next:** none; maintenance mode.

## 2026-07-11 — v1 closeout: PRs merged, CI, live bench, numeric-id fix (Claude Fable 5)
**Did:** reviewed and merged PR #2 (hardening + invocation diagnostics) and
PR #3 (legacy MCP decommission record, DEVLOG conflict resolved); deleted all
stale branches and worktrees (only `main` remains); added GitHub Actions CI
(`pytest` + coverage gate, green in 16 s); set repo description/topics; docs
truth-up (MAP phases 0–7 + ADR index 0011–0012, README v1-complete status,
DEVLOG chronology repaired). Built `scripts/bench.py`: live 13-step benchmark
of every command on one account (~20 s), JSON on stdout, table on stderr,
takeout delays SKIP. First run failed `export subscribers <numeric id>` —
every wrapped command passed digit strings straight to `get_entity()`, which
treats them as phone numbers. Fixed via `chatref.parse()` in all seven
resolution sites (TDD: unit + CLI regressions). Final: 198 unit tests pass,
live bench 13/13 PASS.
**Decided:** branch protection stays off — GitHub free plan rejects it on
private repos (403); revisit if the repo goes public or plan upgrades. Bench
defaults subscribers export to `mirror: dr34m.txt` (-1003890108644) because
the main account is not a member of the public dr34m.txt channel.
**Learned:** the live bench paid for itself on the first run: unit tests with
fakes could not catch Telethon's phone-number interpretation of digit strings.
**Next:** run `scripts/bench.py` after any Telethon pin bump alongside
`check-coverage.py`.

## 2026-07-11 — Preserve cancellation cleanup regression (Codex)
**Did:** recovered the one unique untracked regression from an obsolete Claude
worktree: cancellation during an atomic export removes its temporary file.
The production cleanup behavior was already present in the hardening branch.
**Decided:** retain the test in the active PR rather than duplicate its older
source changes or publish the stale worktree.
**Next:** run the full suite, update the PR, then remove only verified stale
Git residues.

## 2026-07-10 — Add invocation journal and verbose diagnostics (Codex)
**Did:** added metadata-only `invocations.jsonl` for successfully parsed CLI
commands and made `-v/--verbose` configure Python/Telethon debug output on
stderr. The journal records command, resolved account, exit/result metadata,
and duration, never message/search text, chat references, or raw parameters.
Updated CONTRACT, MAP, and ADR-0012.
**Decided:** journal write failures warn and preserve the command result;
mutation audit remains separately fail-closed (ADR-0011/0012).
**Next:** run the full regression suite and inspect the exact diff before any
commit.

## 2026-07-10 — Close minor Phase 3–5 review findings (Codex)
**Did:** added TDD regressions and fixed cancellation cleanup for atomic
exports, CSV formula injection in subscriber names, structured audit-write
failures, and media filename/checkpoint/progress throttling. A resume now
truncates bytes written after its last persisted checkpoint before continuing.
Updated CONTRACT, MAP, and ADR-0011. Verification: `uv run pytest -q` →
`183 passed, 8 skipped`; `git diff --check` passes.
**Decided:** audit persistence is fail-closed: an audit-path `OSError` is a
structured exit-2 policy block, so tgcli never makes an authorised unaudited
mutation (ADR-0011).
**Learned:** checkpoint throttling needs a matching resume rule; otherwise a
crash can leave a partial file longer than its persisted offset.
**Next:** commit these reviewed hardening fixes when requested.

## 2026-07-10 — Legacy Telegram MCP daemons decommissioned (Codex)
**Did:** unloaded the four `com.sereja.telegram-mcp-http*` LaunchAgents and
their four logrotate jobs from `gui/501`. Verified each service is absent from
launchd and no listener remains on ports 8799–8802. Preserved the matching
plist files, old-stack sessions, and unrelated `telegram-mirror-prime-set`.
**Decided:** tgcli is the sole live Telegram CLI route. Restoring a legacy MCP
daemon is a deliberate rollback operation, not a fallback agents may take.
**Next:** no roadmap work remains; maintain tgcli through normal scoped
changes and rerun the coverage gate on Telethon pin updates.

## 2026-07-10 — Roadmap completion doc pass (Codex)
**Did:** reconciled the master-plan status with completed acceptance evidence:
Phase 4 safe writes and Phase 6 migration/cutover are now marked complete.
**Decided:** all planned phases 0–7 are complete once Phase 7 PR #1 merges;
legacy daemon decommission remains outside the roadmap and requires a separate
explicit decision.
**Next:** merge PR #1, then treat future work as a new scoped feature rather
than an unfinished roadmap phase.

## 2026-07-10 — Phase 7 Telethon coverage closure (Codex)
**Did:** normalized `docs/FEATURES.md` to the 23 namespaces exposed by pinned
Telethon 1.44 and moved non-TL exclusions into prose. Added the executable
`scripts/check-coverage.py` gate and regressions for missing, unknown,
duplicate, malformed, and unexplained excluded classifications.
**Decided:** coverage is namespace-level: daily workflows are `wrapped`, raw
TL is `api`, and deliberately unsupported runtime models are `excluded` with
a reason. The checker is fail-closed and must run on every Telethon pin bump.
**Verified:** `.venv/bin/python scripts/check-coverage.py` reports
`coverage OK: 23 namespaces`; `.venv/bin/pytest -q` reports `177 passed,
8 skipped`.
**Next:** Phase 7 is complete; future Telethon upgrades must update the matrix
and pass the gate in the same commit.

## 2026-07-10 — Accept immediate tgcli cutover (Codex)
**Did:** removed the phase-6 parallel-window requirement from the master plan,
agent skill, and migration plan; updated global Claude routing to treat old MCP
daemons as legacy infrastructure rather than an ordinary fallback.
**Decided:** tgcli is the operational base after the completed local migration,
PATH cutover, and three-account read-only smoke. Legacy daemon decommission is
not implicit and still requires its own explicit authorization.
**Next:** push the verified local `main` to `origin/main`, then execute Phase 7
coverage closure when requested.

## 2026-07-10 — Retire unauthorized `pl` from Phase 6 migration (Codex)
**Did:** removed `pl` from the default import aliases, updated the CLI contract,
agent skill, phase plan, master plan, and ADR-0004, and added a regression that
proves default import ignores an existing old-stack `pl` directory.
**Decided:** `pl` is not a migration account until it is explicitly
reauthorized. Its old-stack source remains untouched; the tgcli config block
and copied state session are removed at the user's direction.
**Next:** push and merge the Phase 6 branch, then start the parallel-use window
with `main`, `recklessou`, and `teamsyncsage`.

## 2026-07-10 — Phase 6 local migration and cutover implementation (Codex)
**Did:** added pure-local `tg accounts import`: it backs up old Telethon
SQLite sessions under the same per-account lock as normal tgcli work, protects
existing warmed sessions unless `--force`, appends only missing account blocks,
and never opens Telegram. Added `scripts/install-link.sh`, then verified its
temporary-repo symlink behavior. Added root `SKILL.md`, and parser-checked all
14 documented command examples. Recorded the phase-6 TDD plan, updated MAP
and CONTRACT. Local test suite: `173 passed, 8 skipped`.
**Decided:** migration is additive and reversible at the old-stack side: it
copies sessions and credentials but does not alter daemon state or unload any
LaunchAgent. `vermassov` remains excluded because ADR-0009 records it as
revoked.
**Learned:** the phase-6 linked worktree needs its own `uv sync --locked`
environment; the root checkout's ignored `.venv` is not shared.
**Live validation:** import returned `main: skipped_existing` and imported
`pl`, `recklessou`, and `teamsyncsage`. Read-only dialog smoke succeeded for
`main`, `recklessou`, and `teamsyncsage`; `pl` exits 3 because its old session
is not authorized. `scripts/install-link.sh` now resolves `tg` through
`~/.local/bin/tg`, and `tg --version` is `0.1.0`. Updated
`~/.claude/CLAUDE.md` so tgcli is first route and MCP is explicit fallback.
**Next:** reauthorize `pl` in the old stack and force-import it, or deliberately
retire that alias; begin the parallel-use window only after that decision.

## 2026-07-10 — Phases 3–5 reviewed, fixed, merged to main (Claude Fable 5 + subagents)
**Did:** orchestrated parallel Sonnet review of `codex/phase-3-media`,
`codex/phase-4-write`, `codex/phase-5-export` against PLAN.md acceptance.
Found and fixed pre-merge: Phase 3 major — uncaught `FileNotFoundError` in
`_resume_offset` when `state.json` exists without its `.part` file (4b73560);
Phase 4 blocker — case-variant denylist bypass (`auth.LogOut` resolved to
`LogOutRequest` but missed the exact-string denylist and confirm gate; fixed
by canonicalizing method names from the resolved Telethon class before all
policy checks, fail-closed, 9378459) plus uncaught `SystemExit` from `send`
usage validation. Merged all three branches sequentially with conflict
resolution in `cli.py`/docs; full suite after final merge: `165 passed,
8 skipped`. Live CLI re-check on merged main: `auth.LogOut --write` → exit 2,
`auth.logOut --write --confirm` → exit 2, `TGCLI_NO_SEND=1 send --commit` →
exit 2.
**Decided:** policy identity for `tg api` is the canonical name derived from
the resolved TLRequest class, never the raw user string.
**Learned:** uncommitted WIP (invocation journal: `invocations.py`,
`cli.py` edits, 2 test files) was sitting on main and blocked the merge;
preserved on branch `wip/invocation-journal` (df54c0a), not merged — it
references a design that was never reviewed.
**Next:** decide the fate of `wip/invocation-journal`; Phase 6 migration
only when requested. Minor review findings tracked in review notes
(CSV formula-escaping in export, audit-write try/except, progress throttling).

## 2026-07-10 — Phase 4 safe write path (Codex)
**Did:** added `safety.py` for pre-network `--readonly`, `TGCLI_READONLY=1`,
and `TGCLI_NO_SEND=1` gates; five-minute single-use JSON previews; and JSONL
audit records under `TGCLI_STATE_DIR`. Added `tg send CHAT TEXT --preview` and
`tg send --commit PREVIEW_ID`; commits replay only stored target/text. Enabled
raw `tg api --write` behind the same gate, exact destructive confirmation, the
ADR-0008 permanent denylist, and pre-dispatch audit. Added unit/CLI regressions
for all gates, preview replay/expiry, audit, confirmation, denylist, and
unknown write methods. Final command: `uv run pytest -q` → `126 passed,
8 skipped in 0.33s`; no Telegram mutation was performed.
**Decided:** previews are consumed before network dispatch, so a failed send
cannot be retried with the same id; this preserves the single-use safety
contract and leaves a local audit record for every authorised attempt.
**Learned:** raw API policy must resolve an allowed write method before config
or session acquisition; otherwise a typo can create an unnecessary Telegram
connection despite being invalid.
**Next:** review the Phase 4 diff and commit it on `codex/phase-4-write` when
the user requests a commit.

## 2026-07-10 — Phase 3 media implementation (Codex)
**Did:** implemented `tg media download` with public/private link parsing,
private-channel dialog scanning plus `channels.getChannels` validation, safe
output paths, resumable serial Telethon streaming, opt-in offset/stride
parallel transfer, stderr progress, and clear revoked-session reauth errors.
Added 19 media/CLI tests and two session-revocation regressions. Final local
suite: `129 passed, 8 skipped`; the existing gated live read suite passed
`8 passed`. The incident link returned the expected exit-4 account-access
diagnostic for configured account `main`.
**Decided:** existing completed output files are never overwritten (exit 2).
Serial transfers resume via state under `~/.local/state/tgcli/downloads/`;
parallel transfers start fresh and reject a partial serial state.
**Learned:** Telethon's `iter_download` directly supports the offset and
stride control required for resume and parallel chunks, so Phase 3 needs no
TDLib or additional dependency.
**Acceptance:** downloaded 126,231,815-byte public media to `~/Downloads`;
serial took 53 seconds and `--parallel 4` took 22 seconds. The serial and
parallel files had the same SHA-256
`5ddf8830464e7f02c53bae0f796738464527472fbab432cf94346dab4e6c8506`.
An interrupted serial transfer resumed successfully. The incident link
`t.me/c/3817664407/878` returned the specified exit-4 `main`-lacks-access
diagnostic, not a Telethon media failure.
**Next:** begin Phase 4 write safety only when requested.

## 2026-07-10 — Phase 5 live acceptance passed (Codex)
**Did:** selected public `@msk7days` after `tg count` returned `14,296`, then
exported it through account `main` to
`/Users/sereja/Downloads/tgcli-phase5-msk7days-2026-07-10.jsonl`. The atomic
destination contains `14,296` valid JSONL records, ordered from id `1` to
`16058`; no FloodWait occurred. The live run exposed a legacy malformed
SQLite `takeout_id` value (`b''`), so export now reuses valid integer takeout
ids and clears malformed values before initializing a new takeout. Added two
regressions for both behaviours.
**Decided:** Phase 5 is accepted. The malformed-ID repair is local session
compatibility handling, not a new architecture, so no ADR is required.
**Learned:** a copied Telegram session can carry a non-integer stale takeout
identifier that passes connection/auth checks but fails only while Telethon
serializes `InvokeWithTakeoutRequest`.
**Next:** proceed to the next explicitly requested phase.

## 2026-07-10 — Phase 5 completion audit strengthened (Codex)
**Did:** added the 10k-message local takeout regression, asserting all 10,000
JSONL records are written oldest-first and the completion summary reports the
same count. Focused export tests report `9 passed in 0.19s`; the full local
suite reports `117 passed, 8 skipped in 0.37s`.
**Decided:** the simulation proves the full streaming command path at the
acceptance cardinality, but does not replace the required real Telegram
takeout evidence.
**Learned:** the remaining live gate cannot be inferred from unit tests: it
requires a deliberately supplied non-sensitive 10k+ dialog and authorized
account, because selecting one automatically could export private content.
**Next:** run the designated live export, record its count and FloodWait result,
then mark Phase 5 accepted only if it succeeds.

## 2026-07-10 — Phase 5 export implementation (Codex)
**Did:** added `tg export messages <chat> --output PATH` (streaming Telethon
takeout JSONL, oldest first) and `tg export subscribers <channel> --output
PATH` (streaming quoted CSV). Both commands use an atomic sibling temporary
file, return a normal completion summary, and leave an existing destination
unchanged when the export fails. Added eight focused export tests covering
takeout, JSONL order, CSV headers/quoting, empty exports, not-found, atomic
failure, `TakeoutInitDelayError`, and the no-default-timeout contract;
`uv run pytest -q` reported `116 passed, 8 skipped`.
**Decided:** record files require explicit `--output`, while stdout retains
the one-document JSON/TSV contract as a completion summary. No ADR was needed:
this adds a phase-planned command without changing the architecture.
**Learned:** the source checkout's existing `.venv` is installed editable for
that checkout, so the isolated worktree must use its own `uv run` environment
to test its changed `src/` tree. The normal 60-second deadline would invalidate
the 10k-message acceptance criterion, so exports intentionally have no default
overall timeout while an explicit `--timeout` remains available.
**Next:** run the 10k-message live export only after a safe designated dialog
and authorized account are supplied; do not select a private dialog by guess.

## 2026-07-10 — Raw API read allowlist expanded to 35 methods (Claude Fable 5 + subagents)
**Did:** expanded `READ_METHOD_ALLOWLIST` in `src/tgcli/commands/api.py` from
`users.getFullUser` to the 35 batch-reviewed read methods across messages
(18), channels (7), users (2), contacts (3), photos (1), and stats (4). TDD:
red run of the new parametrized coverage reported `35 failed, 13 passed`;
after the one-constant change the full suite reported `108 passed, 8 skipped`.
Every allowlisted name is asserted to resolve to a real TLRequest of pinned
Telethon 1.44 (catches typos), and five rejected read-looking methods
(`messages.getMessagesViews`, `contacts.getLocated`, `contacts.resolvePhone`,
`messages.getExportedChatInvites`, `messages.getBotCallbackAnswer`) are
regression-tested to exit 2 before config loading. Updated ADR-0010 (full
list + "Reviewed and rejected" table), CONTRACT §6, and FEATURES rows.
**Decided:** the 2026-07-10 batch review is the second ADR-0010 allowlist
revision; default-deny stands, and "new method = ADR update + regression
test" remains the only path in. auth.* and account.* stay excluded wholesale.
**Learned:** the resolve-to-TLRequest test is the cheap safety net for batch
allowlist edits — a misspelled method would otherwise pass policy tests and
only fail at dispatch time.
**Next:** live-check comment counting via `messages.getReplies`/discussion
methods, then merge the branch.

## 2026-07-10 — Phase 2 accepted: side-by-side parity smoke passed (Claude Fable 5 + subagents)
**Did:** ran the Phase 2 acceptance smoke: old daemon-stack `tg` CLI vs new
tgcli, side by side on 3 real dialogs. Counts, latest message ids, and message
text all match 3/3: Saved Messages (count 1374, latest 280484), @karlobrans
channel (75, 965 — text byte-identical), @totwtop (1249, 280271). Cross-check:
`message` lookup by id from the new CLI returns identical content in the old
CLI. Marked Phase 2 done in PLAN.md.
**Decided:** Phase 2 is accepted; branch is ready to merge to main.
**Learned:** the acceptance run itself surfaced two old-stack defects: the
main daemon's read lanes sat in circuit_open for ~25 minutes, and @poremido
fails all old-stack message lanes with a reproducible Telethon "Could not find
a matching Constructor ID" error while new tgcli reads the same chat fine
(count 354, latest 280253). The comparison baseline was flakier than the thing
under test — which is the reason this project exists.
**Next:** merge phases 1–2 to main, then execute Phase 3 (media downloads).

## 2026-07-10 — Default-deny raw API read policy (Codex)
**Did:** replaced the raw method-name prefix heuristic with the reviewed,
explicit Phase-2 allowlist `users.getFullUser`; added regression coverage that
`auth.checkPassword` and `account.getTmpPassword` exit 2 before config loading
or session acquisition; and recorded the policy in ADR-0010, CONTRACT, and
MAP. TDD red run: `.venv/bin/pytest tests/test_cli_api_policy.py -q` reported
`2 failed, 5 passed` because both methods tried to load config. Green focused
run reported `7 passed in 0.14s`; full suite reported `67 passed, 8 skipped in
0.25s`; `TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reported `8 passed
in 6.99s`.
**Decided:** no raw TL method is classified as read-only by its name. Phase 2
permits only ADR-0010's explicit allowlist; all other methods fail closed.
**Learned:** TL names such as `checkPassword` and `getTmpPassword` can hide
credential-sensitive operations, so verb prefixes are not a safety boundary.
**Next:** add further raw methods only through a reviewed ADR-0010 allowlist
update with dispatcher and no-session policy regressions.

## 2026-07-10 — Read-only raw API CLI passthrough (Codex)
**Did:** wired allowlisted `tg api` calls through the normal session context,
added CLI envelope/FloodWait tests, a gated live
`users.getFullUser --params '{"id":"@self"}'` check, and a no-session
`messages.sendMessage --write` policy regression. The live check exposed a
stale entity-cache edge case for `@self`; it now maps directly to
`InputUserSelf` for an input-user field. Final local checks:
`.venv/bin/pytest tests/test_api_conversion.py tests/test_cli_api.py -q`
reported `17 passed in 0.14s`; `.venv/bin/pytest -q` reported
`65 passed, 8 skipped in 0.23s`; and
`TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reported
`8 passed in 7.80s`.
**Decided:** raw API writes remain unavailable in phase 2: any non-allowlisted
method and any `--write` request exits 2 before session acquisition; phase 4
owns enabling audited writes under ADR-0008.
**Learned:** `get_input_entity("@self")` may use a cached non-user peer, so a
known self-user input must not rely on generic entity-cache coercion.
**Next:** complete the remaining Phase 2 acceptance review or proceed to Phase
3 media work.

## 2026-07-10 — Phase 2A TSV contract hardened (Codex)
**Did:** sanitized sender names as well as message text in every four-column
message TSV path (`read`, `search`, `latest`, and `message`) and added
regression tests. Final local suite: `43 passed, 7 skipped`.
**Decided:** control characters in all untrusted human-visible fields become
spaces before TSV or default human output; JSON retains source data.
**Learned:** frozen TSV requires sanitizing every cell, not only the message
body.
**Next:** merge or hand off Phase 2A, then execute the separate read-only raw
API plan.

## 2026-07-10 — Phase 2 live smoke harness corrected (Codex)
**Did:** changed the opt-in live harness to run the installed `tg` console
script beside the active virtualenv Python, then ran the focused harness check,
one live `info me` check, the full gated live suite, and the full suite. Final
results: `TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reported
`7 passed in 5.84s`; `.venv/bin/pytest -q` reported
`39 passed, 7 skipped in 0.17s`.
**Decided:** the live subprocess must use the real console script and its real
tgcli state, while preserving the suite's opt-in gate.
**Learned:** the earlier exit-3 result was not an unauthorized `main` account:
the autouse test fixture set `TGCLI_STATE_DIR` to a temporary directory and
the subprocess inherited it, so it opened an empty temporary session. Removing
only that test-only environment variable lets the subprocess use the authorized
`main` session.
**Next:** proceed with the remaining Phase 2 acceptance work.

## 2026-07-10 — Phase 2 read parity contract and live smoke (Codex)
**Did:** added opt-in (`TGCLI_LIVE_SMOKE=1`) JSON-shape checks against the
explicit `main` account for `info me`, `latest me`, `count me`, and bounded
`search me tgcli-live-smoke --limit 1`; corrected the pre-existing test harness
to invoke the CLI entrypoint rather than import the module without running it.
Documented Phase 2 JSON and TSV shapes, and marked `search.py` and `info.py`
done in MAP. `.venv/bin/pytest -q` reported `39 passed, 6 skipped in 0.26s`.
**Decided:** live smoke asserts response structure and the search bound only;
it never depends on a message count or a particular Saved Message.
**Learned:** `TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reached the
CLI but all six checks failed because the configured `main` session is not
authorized (exit 3, `session 'main' is not authorized`). No configuration or
session was changed; successful live validation requires an authorized `main`
session.
**Next:** authorize or provide an authorized `main` tgcli session, then rerun
the gated live suite.

## 2026-07-10 — Phase 2 read parity started (Codex)
**Did:** added one canonical message projection and exact read-by-ID support;
unit suite after the change reports `28 passed, 2 skipped`.
**Decided:** preserve the Phase 1 message JSON shape and reuse it instead of
creating a second formatter for `tg message`.
**Learned:** exact message lookup is a small read-only addition with the same
not-found contract as dialog lookup (exit 4).
**Next:** add `search`, `latest`, and CLI `message` on top of this projection.

## 2026-07-10 — Phase 3 design approved (Codex)
**Did:** created an isolated `codex/phase-3-media` worktree, restored the
locked uv environment, and recorded the Telethon-only media-download design.
Baseline in the isolated worktree: `108 passed, 8 skipped`.
**Decided:** final media files never overwrite existing paths; interrupted
downloads resume from state under `~/.local/state/tgcli/downloads/`.
**Learned:** the source checkout has an unrelated untracked invocation test,
so all Phase 3 work remains in the separate worktree.
**Next:** review this design, write the TDD implementation plan, then start
the first failing media-command test.

## 2026-07-09 — Phase 2 split into read parity and raw API plans (Codex)
**Did:** reviewed the completed Phase 1 CLI, the old stack's command surface,
CONTRACT.md, FEATURES.md, and ADR-0008. Wrote two TDD execution plans:
read parity first, then the independent raw API security surface.
**Decided:** do not delay daily read workflows on the 300–500 LOC raw API
resolver. Raw API remains Phase 2 but is a separate reviewable plan with an
explicit fail-closed policy gate before request construction or a network call.
**Learned:** the old CLI's daily read set maps cleanly to `search`, `count`,
`latest`, `info`, and `message`; Phase 1 already supplies the session and
FloodWait plumbing they need.
**Next:** execute `2026-07-09-phase-2-read-parity.md`, then execute the raw
API plan and run the combined Phase 2 acceptance checks.

## 2026-07-09 — Phase 1 acceptance gates passed (Codex)
**Did:** created the local `main` tgcli configuration from the existing private
Telegram runtime variables and copied its SQLite session with SQLite's backup
API, then checked the backup integrity. Verified `26 passed, 2 skipped`, a
read-only `tg --json dialogs --limit 1` smoke (one dialog returned), and a
second invocation under an intentionally held `main.lock` (exit 3 with the
machine-readable busy error).
**Decided:** Phase 1 is accepted. The migration is deliberately minimal:
one existing account and no replacement for Phase 6 `tg accounts import`.
**Learned:** the session lock contract is observable end-to-end without making
any Telegram mutation.
**Next:** write the Phase 2 TDD plan for read parity and read-only `tg api`.

## 2026-07-09 — Phase 1 implementation complete; live gate blocked by missing config (Codex)
**Did:** implemented the remaining Phase 1 modules in commits `b227247`,
`ab33686`, `1dccd09`, `c3916b5`, and `267f258`: per-account session locking,
CLI dispatch and account listing, `dialogs`, `read`, FloodWait mapping, and a
gated live smoke suite. Added contract tests for global flags and output modes.
Final local validation: `26 passed, 2 skipped`; `tg --version` prints `0.1.0`.
**Decided:** corrected the Phase 1 CLI implementation where the plan omitted
CONTRACT.md requirements: global `--readonly`/`-v`, distinct human and TSV
output, and controlled parser-error return handling. CONTRACT.md remains law.
**Learned:** the attempted read-only live `tg dialogs --json --limit 1` smoke
exits 3 because the default tgcli config is not present; no Telegram account or
session was touched.
**Next:** provision or point `TGCLI_CONFIG` at an authorized `main` account,
then run the Phase 1 live dialog and concurrent-lock acceptance checks.

## 2026-07-06 — TDLib re-audit: fallback backend cut from plan (Claude Fable 5)
**Did:** re-audited the TDLib claim behind ADR-0006 against the old stack's
own records: its ADR (2026-06-21) had already ruled TDLib out as a runtime;
the benchmark PoC never produced RESULTS.md; the 2026-07-06 incident's root
causes were a revoked `vermassov` session, cold entity cache on `t.me/c/`
links + Telethon 1.44 parse bug, and a TDLib backend that wasn't even
installed. Wrote ADR-0009 (supersedes 0006), rewrote phase 3 as
Telethon-only with in-code fixes, updated MAP (backends/ removed), risks,
research-base line.
**Decided:** no TDLib in v1 (ADR-0009). Re-entry only via reproducible
Telethon failure on the incident case → measured, isolated PoC. Kept assets:
authorized TDLib sessions `~/.telegram-mcp-tdlib/{main,vermassov}` + PoC harness.
**Learned:** "TDLib is the reliable backend" was folklore from one manual
rescue download, promoted into our ADR without a benchmark behind it.
Re-audits of inherited claims pay off. Also: `vermassov` is missing from the
ADR-0004 import list but held the only access in the incident — revisit at
phase 6 cutover.
**Next:** execute phase-1 plan (still unchanged).
**Follow-up (same day):** user ratified cutting TDLib after a from-scratch
re-analysis (key datum: iyear/tdl, the fastest private-channel downloader,
uses gotd/td MTProto, not TDLib). Phase 3 now explicitly lists the tdl
techniques: parallel chunks (FastTelethon-style), offset resume with state
in `~/.local/state/tgcli/downloads/`, takeout for bulk (phase 5); acceptance
adds "parallel beats single-stream" check.

## 2026-07-06 — Scope grill: "all functions" resolved via raw passthrough (Claude Fable 5)
**Did:** grilled the "new version with ALL Telegram functions" request;
competitor survey (iyear/tdl 7.7k★ media-only; b1rd33/tg-cli — closest analog,
62 commands, MIT, bus-factor 1; ~10 telegram-mcp servers). Ran a loophole
cycle on the strategy until it converged (3 iterations, 6 major holes fixed).
Added ADR-0008, docs/FEATURES.md, CONTRACT §6 (tg api), PLAN updates
(non-goals, phases 2/4, new phase 7, risks, research addendum), MAP rows.
**Decided:** "all functions" = wrapped commands for daily use + `tg api`
raw TL passthrough for the long tail + FEATURES.md coverage matrix with
explicit exclusions (ADR-0008). Source of truth = pinned Telethon TL schema.
Do not fork b1rd33/tg-cli; borrow typed `--confirm` + single-use previews.
**Learned:** loopholes found by the cycle: raw passthrough would have
bypassed preview→commit (fixed: read-only until phase 4, `--write` gate);
`export*` methods look like reads but mutate (fixed: strict verb allowlist);
`auth.logOut` via passthrough would kill the managed session (fixed: hard
denylist); TL output can't obey our JSON stability rules (fixed: CONTRACT §6
exemption); secret chats/calls are impossible in Telethon (fixed: explicit
exclusions, otherwise "all functions" acceptance is unfalsifiable).
**Next:** execute phase-1 plan (unchanged by this session).

## 2026-07-06 — Phase 0: project born (Claude Fable 5)
**Did:** researched gogcli internals (deepwiki) and Telethon session/flood
semantics (context7); created docs-first scaffold: README, AGENTS, CLAUDE,
MAP, CONTRACT, PLAN, ADR-0001…0007, this DEVLOG, phase-1 TDD plan.
**Decided:** Python+Telethon over Go rewrite (ADR-0001); stateless CLI-first,
no daemons, MCP non-goal (ADR-0002); output contract with fixed exit codes
(ADR-0003); per-account SQLiteSession + file lock, import sessions from old
stack (ADR-0004); preview→commit write safety with audit log (ADR-0005);
TDLib as media fallback only (ADR-0006); MAP+ADR+DEVLOG discipline (ADR-0007).
**Learned:** gogcli has NO MCP server — it's an explicit non-goal in their
spec; agents drive it purely via CLI + SKILL.md. That validates dropping the
daemon layer entirely. Telethon's entity cache in the session file is the
key enabler for cheap short-lived processes.
**Next:** execute phase-1 plan (docs/superpowers/plans/2026-07-06-phase-1-core-and-read.md).
