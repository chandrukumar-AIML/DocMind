# Data Processing Agreement (DPA)

**Last updated: 3 July 2026**

> **⚠️ DRAFT — PENDING LEGAL REVIEW.** This is a standard SaaS template provided as a
> starting point only. It is **not** legal advice and has **not** been reviewed by a
> qualified attorney or privacy professional. Enterprise customers frequently require
> their own DPA and Standard Contractual Clauses; placeholders in `{{DOUBLE_BRACES}}` must
> be completed and the whole document reviewed by counsel before you sign it.

This Data Processing Agreement ("**DPA**") forms part of the
[Terms of Service](/legal/terms) between {{COMPANY_LEGAL_NAME}} ("**Processor**",
"**we**") and the Customer ("**Controller**", "**you**") and applies where we process
**personal data contained within Customer Content** on your behalf.

## 1. Roles

You are the **Controller** (or a processor acting for your own controller) of personal
data within Customer Content. We act solely as **Processor**, and only on your documented
instructions, which include your use of the Service's features and configuration.

## 2. Scope of Processing

| Item | Description |
|---|---|
| **Subject matter** | Provision of the DocuMind AI Service |
| **Duration** | For the term of the Terms of Service, plus the export/retention window |
| **Nature & purpose** | Storage, indexing, embedding, retrieval, and AI-assisted analysis of documents you upload |
| **Types of personal data** | Any personal data you choose to include in uploaded documents (you control this) |
| **Categories of data subjects** | As determined by your Customer Content (e.g. your employees, customers, counterparties) |

## 3. Processor Obligations

We will: (a) process personal data only on your documented instructions, including for
transfers, unless required by law (in which case we notify you unless legally prohibited);
(b) ensure personnel are bound by confidentiality; (c) implement the security measures in
Annex A; (d) assist you, taking into account the nature of processing, with data-subject
requests and with your obligations regarding security, breach notification, and impact
assessments; and (e) at your choice, delete or return personal data at the end of the
Service, as described in the Terms.

## 4. Sub-Processors

4.1 You provide **general authorization** for us to engage sub-processors (including
hosting, model, and embedding providers) to deliver the Service. A current list is at
{{SUBPROCESSOR_LIST_URL}}.

4.2 We will inform you of intended changes to sub-processors with reasonable notice and
give you the opportunity to object on reasonable data-protection grounds.

4.3 We remain responsible for sub-processors' performance of their data-protection
obligations, which must be materially as protective as this DPA.

> **Note:** where you configure your own model API keys ("bring your own key"), the model
> provider you select acts under **your** agreement with them, not as our sub-processor.

## 5. International Transfers

Where processing involves transfer of personal data across borders in a manner requiring
safeguards, the parties will rely on {{TRANSFER_MECHANISM}} (e.g. EU Standard Contractual
Clauses / UK IDTA), incorporated by reference and completed with the details in Annex B.

## 6. Personal Data Breach

We will notify you without undue delay after becoming aware of a personal data breach
affecting your Customer Content, and provide information reasonably available to help you
meet your notification obligations.

## 7. Audits

We will make available information reasonably necessary to demonstrate compliance with
this DPA and allow for audits, including via up-to-date third-party certifications or
reports ({{AUDIT_REPORTS}}) where available, subject to confidentiality and reasonable
limits on frequency and scope.

## 8. Deletion and Return

On termination, we will, at your choice, delete or return personal data within Customer
Content and delete existing copies within {{DELETION_WINDOW_DAYS}} days, except to the
extent retention is required by law.

## 9. Liability

Each party's liability under this DPA is subject to the limitations of liability in the
Terms of Service.

---

## Annex A — Technical and Organizational Measures

- Encryption of data in transit (TLS); encryption of secrets at rest (symmetric
  encryption of stored API keys and SSO client secrets).
- **Tenant isolation**: per-workspace separation of vector collections, indexes, and
  document registries so one customer cannot access another's data.
- Role-based access control (workspace admin / admin / editor / viewer) and, optionally,
  enterprise SSO (OIDC).
- Authentication via signed tokens with rotation; httpOnly cookies.
- Audit logging of security-relevant actions and correlation-ID request tracing.
- Rate limiting and per-plan usage enforcement.
- Access to production systems limited to authorized personnel on a need-to-know basis.

*(Complete/adjust to reflect your actual controls and any certifications such as SOC 2 or
ISO 27001 before signing.)*

## Annex B — Transfer Details

{{TRANSFER_ANNEX_DETAILS}} — parties, roles, competent supervisory authority, and the
specifics required by the applicable transfer mechanism, to be completed by counsel.

## Contact

Data-protection matters: **{{PRIVACY_CONTACT_EMAIL}}**, {{COMPANY_LEGAL_NAME}},
{{COMPANY_ADDRESS}}.
