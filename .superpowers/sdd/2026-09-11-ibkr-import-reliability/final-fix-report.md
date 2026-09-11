# Final fix report: Late optional transaction identity

## Problem

An execution first accepted without `transactionID` keeps a null canonical
transaction ID. A later successful report may add the optional ID, but the
canonical UPSERT deliberately does not rewrite identity. Future validation
therefore lost the accepted relationship and allowed either side to change.

## Fix

The batched consistency query now combines canonical identities with immutable
raw Trade identity evidence. Raw evidence is eligible only when the raw row's
owning run succeeded or the artifact has a successful completing run, matching
the canonical successful-source policy. It filters `Trades:Trade:*`, derives the
same blank-ID execution fallbacks as mapping, and deduplicates repeated identity
tuples before returning one representative raw source ID.

Raw rows provide identity evidence only. Canonical rows remain the sole source
for protected economics comparisons. No canonical identity is changed, no alias
is created, and failed reports cannot bind an identity.

## Verification

- Red reproduction: after successful `E1`/`T1` evidence, both `E1`/`T2` and
  `E2`/`T1` were accepted by the validator.
- Green PostgreSQL regressions cover successful origin and successful completion
  lineage, both conflict directions, failed evidence exclusion, non-Trade row
  exclusion, and preservation of the null canonical transaction ID.
- Root live-workflow regressions cover late enrichment, both later conflicts,
  failed late evidence, and no canonical identity mutation.
- Focused result: `71 passed`.
- Ruff and MyPy passed for `app/db/trade_consistency.py`.
