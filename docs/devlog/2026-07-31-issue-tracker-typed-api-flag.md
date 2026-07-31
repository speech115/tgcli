## 2026-07-31 — Document `gh api -F` for wayfinding id endpoints (Claude)

**Did:** fixed the two wayfinding API examples in
`docs/agents/issue-tracker.md` that fail when followed literally. The
**Child** bullet now shows the sub-issue call
(`gh api repos/<owner>/<repo>/issues/<map>/sub_issues -F sub_issue_id=<id>`),
which the doc previously described only in prose, and the **Blocking edge**
bullet shows the dependency call with `-F issue_id=<id>`. Added a third
bullet that owns the shared id rule for both endpoints: numeric database id
(not `#number`, not `node_id`), fetched with `--jq .id`, and passed with the
typed `-F` flag. Documentation only — no code, no contract change.

**Decided:** no ADR. This corrects an example that never worked, not a
contract or a behavior. The id-provenance sentence moved out of **Blocking
edge** into its own bullet because it now governs two endpoints, and
duplicating it in both would be the next thing to drift.

**Learned:** `gh api -f` always sends a JSON string, so both
`sub_issue_id` and `issue_id` come back HTTP 422 with
`Invalid property /<field>: "<id>" is not of type integer`. Hit for real
while charting map #131 today; it cost a full round of failed calls before
the flag was the suspect. The doc's existing warning about `#number` and
`node_id` was correct and complete — the failure was purely the flag, which
is exactly the kind of thing prose omits and a copy-pasteable command
carries.

**Next:** nothing pending; the wayfinder flow in `docs/agents/` now matches
what actually succeeds against the API.
