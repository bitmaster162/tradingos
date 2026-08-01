# Command issues and recovery

All issues below were confined to disposable scratch and produced no source,
runtime, network or capital effect.

1. `git bundle verify` was first invoked outside a Git repository and returned
   `need a repository to verify a bundle`. It was rerun inside the disposable
   clone and passed.
2. Several read-only PowerShell summaries used an invalid direct
   `foreach { ... } | Format-*` parse shape. Each command failed before its body
   ran and was rerun with an explicit results array.
3. Evaluator attempt 1 failed closed because the tombstone registry key was
   `entries`, not `tombstones`. The parser was corrected and rerun.
4. Evaluator attempt 2 failed closed because the preregistration uses
   `train_gate`, not `gates.train`. The parser was corrected and rerun.
5. One generated-output copy used `-LiteralPath` with a wildcard and copied
   nothing. It was rerun by enumerating files explicitly.
6. Broad root unittest discovery reports two pre-existing missing Bitunix docs.
   The exact M2A suite and all bounded M2B/relevant tests pass; no unrelated
   repair was attempted.

