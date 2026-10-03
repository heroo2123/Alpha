# InventoryTransform offline SHADOW observer

Run an explicit saved JSON fixture through the accepted `structural_evidence` normalizer and reconciliation engine:

```sh
python -m polymarket_scanner.v11.inventory_shadow \
  --input /path/to/local-fixture.json \
  --event highest-temperature-in-singapore-on-october-3-2026 \
  --output /path/to/inventory-shadow.json
```

The input has `source`, `payload`, and `evidence_class: "API_OBSERVED"`. `source` follows `structural_evidence.Source`; `payload` is a saved activity page. An optional `coverage.state` must match the engine's computed coverage. An optional `synthetic_conversion` supplies explicit route, topology, selected mask, quantity, and available NO units for the accepted synthetic NegRisk model. It is reported separately as `SYNTHETIC_PROOF` with no window coverage. Deployed attestation is refused.

The output is a deterministic, content-identified diagnostic. Replaying the same input accepts an identical existing file; changed content at the output path is refused. The loader refuses symlinks and nonregular inputs, caps input bytes/records/time, and the artifact writer caps output bytes. The observer has no provider, receipt acquisition, account, order, weather runtime, or service integration. `CHAIN_RECEIPT` count stays zero; transaction proof, qualification, financial authority, and account/order effects stay false or empty for every output. Activity cash and price arithmetic remain vendor observations. Complete pagination alone does not resolve opening inventory, basis, fees, masks, receipts, lineage, or deployed route.

The reviewed, integrated bounded batch start, its refusal conditions, and the meaning of "activated" are defined in `docs/V11_INVENTORY_SHADOW_START_CONTRACT.md`. The observer remains dormant except for explicit local fixture diagnostics; integration does not establish transaction qualification or weather SHADOW admission.
