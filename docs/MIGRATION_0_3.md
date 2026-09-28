# Migrating from 0.2 to 0.3

This release intentionally changes unsafe or misleading result contracts. Update callers before upgrading a running integration.

1. Remove code that treats `allow_auto`, `escalate_to`, or `is_complete` as authority. Command assessment reports risk; completion assessment reports evidence support and gaps. Native permission checks and executed verifiers remain authoritative.
2. Check `status` and `source` before consuming an assessment. Missing keys, malformed provider replies and outages are unavailable. Offline mode returns no guessed decision. Unavailable results must preserve the normal model workflow and original evidence.
3. Use `jev-1.13.0` and the official HTTPS endpoint. The client validates all IDs, types, completeness, distributions, legends and the resolved model. Noul values are probabilities, Score values can be fractional, and missing usage or Noul confidence is null.
4. Supply descriptive Score criteria, rather than only numeric scales. Keep original artifacts, source references and command exit status. Batched evidence uses complete windows; oversized windows are retained instead of truncated for a provider call.
5. Automatic pruning now defaults off. Previous percentage-savings examples were not measured evidence and have been removed. Enable omission only after matched development and held-out validation demonstrates retained required facts and useful net savings.
6. Replace editable-checkout harness launchers with a built, versioned managed runtime. Enter credentials through `jev auth set --gui` on Windows or masked local terminal setup. Do not embed keys in MCP settings, skills, logs or shell command arguments.
7. The managed ledger is shared across processes at one runtime home. Every attempt reserves the documented maximum cost; unknown usage stays reserved. A separate provider SDK or different JEV_HOME can bypass this local accounting and is outside the managed integration.
8. Reinstall using the preview/apply workflow. Keep the private backup manifest for selective restoration. Restart existing Jev processes after credential, model-policy or configuration changes, then perform one real typed request through each client. Distinguish configured, authenticated and operational status.

No benchmark grade, permission boundary, completion claim or memory mutation should be based solely on a Jev assessment. The TypeScript guard now awaits the supplied client; it no longer silently uses a fallback path.
