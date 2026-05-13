# pii — Cloud DLP inspection of BigQuery tables

The pii bot uses Google's Cloud DLP to inspect configured BigQuery tables for personally-identifiable information (emails, phone numbers, SSNs, credit cards, etc.). Findings land in a dedicated `pii_findings` table (separate retention from `gap_reports`) and high-severity hits are mirrored into `gap_reports`.

**Module:** [`core/pii/`](../../src/sre_free_mcp/core/pii/)
**Task name:** `pii_audit`
**Default schedule:** disabled by default — enable when you've populated `pii_targets.yaml`

## How one audit works

For each active target in `pii_targets.yaml`:

1. Submit a DLP inspect job pointing at the BigQuery table, sampling `sample_rows` rows.
2. Poll until completion.
3. Read the `info_type_stats` from the job result.
4. Normalize each match into a `PiiFinding` record (info_type, likelihood, column_path, sample/finding counts).
5. Write to `governance.pii_findings`.
6. Mirror high-severity findings into `gap_reports` with `scope='pii'`.

## Rules produced

DLP returns `info_type` + `likelihood` per match. Severity mapping:

| info_type + likelihood | severity |
|---|---|
| Direct identifier (EMAIL, PHONE, SSN, CC) + LIKELY/VERY_LIKELY | critical |
| Direct identifier + POSSIBLE/UNLIKELY | high |
| Derived identifier (PERSON_NAME, etc.) + LIKELY/VERY_LIKELY | medium |
| Anything + VERY_UNLIKELY | low |

The `gap_kind` is the lowercased info_type (e.g., `pii_email_address`).

## Config

[`pii_targets.yaml`](../configuration.md):

```yaml
targets:
  - dataset: customers
    table: users
    sample_rows: 1000
    info_types:
      - EMAIL_ADDRESS
      - PHONE_NUMBER
      - US_SOCIAL_SECURITY_NUMBER
      - CREDIT_CARD_NUMBER
      - PERSON_NAME
```

Tables not listed are NOT inspected — explicit opt-in. Cross-validated at startup: no duplicate `(dataset, table)` pairs.

## Why a separate `pii_findings` table?

Three reasons:

1. **Retention** — DLP findings often need different retention than operational findings (compliance might require longer hold or shorter purge).
2. **Schema** — DLP findings carry info_type / likelihood / column_path / sample_count / finding_count fields that don't fit the generic `gap_reports` shape.
3. **Access control** — `pii_findings` can be restricted to a smaller group than `gap_reports` via dataset-level IAM.

High-severity PII findings are mirrored to `gap_reports` so they flow through the rollup + incidents pipeline; lower-severity ones stay only in `pii_findings`.

## Optional dependency

`google-cloud-dlp` ships in the `[pii]` optional extra:

```bash
pip install sre-free-mcp[pii]
```

The container image you build doesn't include it by default. Add to `pyproject.toml` deps before building.

## SA permissions

The Terraform module's default IAM doesn't include DLP. Add manually:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:sre-free-mcp@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/dlp.user"
```

## Tunable knobs

Per-target via YAML:

| Field | Default | Notes |
|---|---|---|
| `sample_rows` | 1000 | DLP inspects a sample for cost control |
| `info_types` | (5 common ones) | Override per-table; common types: `EMAIL_ADDRESS`, `PHONE_NUMBER`, `US_SOCIAL_SECURITY_NUMBER`, `CREDIT_CARD_NUMBER`, `PERSON_NAME`, `IP_ADDRESS`, `STREET_ADDRESS`, `DATE_OF_BIRTH` |

## Tables read / written

**Reads** — Cloud DLP API + BigQuery table sample.

**Writes**

- `governance.pii_findings` — every match
- `governance.gap_reports` — high-severity mirror

## Example pii_findings row

```json
{
  "id": "uuid",
  "generated_at": "2026-05-13T04:00:00+00:00",
  "dataset": "customers",
  "table_name": "users",
  "column_path": "profile.email_alt",
  "info_type": "EMAIL_ADDRESS",
  "pii_class": "pii.direct",
  "likelihood": "VERY_LIKELY",
  "sample_count": 1000,
  "finding_count": 842,
  "severity": "critical",
  "details": {...}
}
```

## Cost model

Cloud DLP charges per byte inspected. Sampling 1000 rows on a typical user table costs pennies. Inspecting an entire customer dataset can cost more — read the [DLP pricing page](https://cloud.google.com/dlp/pricing) before raising `sample_rows`.

## Common questions

**Q: Will this slow down my production tables?**
No. DLP creates a sampling job that runs against BigQuery in the background; the source table isn't locked or modified.

**Q: Can I redact / mask the PII it finds?**
Not in v1 — the bot is detection-only. DLP has separate de-identification APIs (`projects.deidentifyTemplates`) that you can call manually; integration is a v2 candidate.

**Q: What's the difference between `EMAIL_ADDRESS` and `PERSON_NAME` as info_types?**
DLP info_types are pre-built detectors. `EMAIL_ADDRESS` looks for `*@*.*` patterns; `PERSON_NAME` uses a name list. Direct identifiers (email, phone, SSN, CC) are higher-severity than derived identifiers (name alone). See the [DLP info-type catalog](https://cloud.google.com/dlp/docs/infotypes-reference).

**Q: How do I tag a column as "expected to contain PII" so it doesn't fire?**
v1: doesn't support exclusions. Workaround: omit the dataset/table from `pii_targets.yaml` if the entire table is expected to hold PII (e.g., you've already enforced encryption-at-rest + restrictive IAM). v2 candidate: column-level skip rules.
