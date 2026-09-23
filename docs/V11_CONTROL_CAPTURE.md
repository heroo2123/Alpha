# Owner action: preserve control evidence

The connected development user cannot read the V10 database/status/release
files, and `sudo -n -l` requires a password. No permission has been broadened.
This is the remaining prerequisite for the actual V10 forensic baseline.

The prepared command is deliberately one bounded capture, with no deployment,
service restart, account action, order or executor-mask change. Review
`deploy/v11-capture-control-owner.sh` and its pinned `tools/v11_snapshot.py` first.
On `alpha-dev`, from the staged V11 checkout, the owner runs:

```sh
bash /home/alphaadmin/alpha-v11-phase0-20260923/deploy/v11-capture-control-owner.sh
```

Enter any required sudo credential on the host's own prompt, never in chat.
The command rechecks 221 deployed source/lock files, the launcher and unit,
executor containment, RAM and disk. Changed identities or insufficient headroom
stop the capture. `--check-only` runs just those read-only checks.

The transient capture service has 20% CPU, 128 MiB RAM, no swap, eight tasks and
90 seconds maximum runtime. The snapshot program has a separate 45-second
deadline and 1-GiB output bound. Control paths are read-only; execution credential
and financial-state paths are inaccessible; networking is disabled. No existing
systemd unit is edited or started/stopped by this action.

The new private copy appears at
`/var/tmp/alpha-v11-control-evidence-20260923`, owned by `alphaadmin`, directory
mode 0700 and artifact modes 0400. Existing evidence is never overwritten. The
manifest records the transactionally consistent SQLite copy plus bracketing
status/release reads and their hashes. Those separate status files are explicitly
not claimed to be atomic with the SQLite transaction.

The configuration digest covers nonsecret launcher/unit/runtime-pin hashes;
its scope excludes secret values. Installed package metadata and the captured
runtime status still need separate reconciliation. The older launcher release
marker is preserved as evidence rather than edited to agree with the source tree.

After capture, the integrator must transfer the private copy through authorized
access, verify every manifest hash, analyze it off-host, preserve the evidence,
and update `V11_V10_FORENSIC_BASELINE.md` with nonsecret findings. Journals,
account-specific entitlement, balances and complete operational acceptance remain
separate checks. The owner action does not make V11 ready to fund.
