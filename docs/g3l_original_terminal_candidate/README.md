# Original G3-P terminal: offline historical identity candidate

Candidate for `protocol.g3p_original_review_terminal` only. The three exact
local files and their immutable Git commit/tree/blob references, lengths, and
SHA-256 digests are in [identity.json](identity.json). The validator requires
the current local bytes to match the historical blobs; no copies of the
original evidence are created here.

The recovered terminal was added by commit
`5a06629c34577c14c2fffd5ced1fda7bd62ab7f2` (tree
`a1411f46f1dfd014b5e84895960a820c155b9563`) on 2026-10-02. It records
`PASS` and binds the report digest to the original G3-P protocol commit
`117830a9b418cdf2a1b1f5146e074343623b8e3f` (tree
`07c4d72fcafb4897896e27dfbcc415e7ab078ee9`). The report was retained
at commit `24ac951704ba06d295b28a2b80ca6acdf197329d` (tree
`a8c4ed31fe8c75af923b5b2b54913cc67081ed6e`). The October 2 G3-L
identity audit and October 7 independent 77-row read-only audit identified
this as the smallest historical package candidate. These are local recovery
and byte-identity observations, not authentication of the reviewer or runtime.

Run from the repository root, without network access:

```sh
python3 tools/validate_g3l_original_terminal_candidate.py
python3 -O tools/validate_g3l_original_terminal_candidate.py
python3 -m unittest -v tests.test_g3l_original_terminal_candidate
python3 -O -m unittest -v tests.test_g3l_original_terminal_candidate
```

This package is untrusted historical G3-P protocol material. The original
PASS covers offline synthetic collector implementation only. It is not a
qualified inventory row, G3-L PASS, current executable or provider readiness,
or a score increase. Independent package-entry review and sealing into an
actual private object root are still required before any inventory entry.
The selected-window, provider, code, clock, storage, schedule, and private V4
manifest prerequisites remain open; this package changes none of them.
