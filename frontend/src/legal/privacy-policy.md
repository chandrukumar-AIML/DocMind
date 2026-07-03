# Privacy Policy

**Last updated: 3 July 2026**

> **⚠️ DRAFT — PENDING LEGAL REVIEW.** This is a standard SaaS template provided as a
> starting point only. It is **not** legal advice and has **not** been reviewed by a
> qualified attorney or privacy professional. Placeholders in `{{DOUBLE_BRACES}}` must be
> completed, and the document must be reviewed against the specific data flows of your
> deployment (including your LLM and embedding sub-processors) and the privacy laws that
> apply to you (e.g. GDPR, UK GDPR, CCPA/CPRA, India DPDP Act) before publication.

This Privacy Policy explains how {{COMPANY_LEGAL_NAME}} ("**we**", "**us**") collects,
uses, and shares personal information in connection with the DocuMind AI platform (the
"**Service**"). For personal data contained **within documents you upload**, we act as a
**processor** on your behalf — see the [Data Processing Agreement](/legal/dpa).

## 1. Information We Collect

**Account information** — name, email, hashed password, workspace name, and role, provided
at registration or via SSO.

**Billing information** — plan, subscription status, and billing identifiers. Card details
are handled by our payment processor ({{PAYMENT_PROCESSOR}}); we do not store full card
numbers.

**Customer Content** — documents and queries you submit. We process these to provide the
Service (indexing, retrieval, AI answers). See Section 4.

**Usage and log data** — actions (uploads, queries), timestamps, correlation IDs, IP
address, and device/browser metadata, used for security, rate limiting, billing, and
debugging.

**Cookies** — we use strictly necessary cookies (including an httpOnly authentication
cookie) to operate the Service. {{COOKIE_ANALYTICS_STATEMENT}}

## 2. How We Use Information

We use personal information to: provide and secure the Service; authenticate users and
enforce workspace isolation; process payments and enforce plan limits; provide support;
detect and prevent abuse; comply with legal obligations; and improve the Service. **We do
not use your Customer Content to train foundation models.**

## 3. Legal Bases (where GDPR/UK GDPR applies)

We rely on: **contract** (to provide the Service you signed up for); **legitimate
interests** (security, fraud prevention, product improvement); **legal obligation**; and
**consent** where required (e.g. non-essential cookies).

## 4. AI and Sub-Processors

4.1 To generate answers, the Service sends query text and relevant document excerpts to
third-party model providers configured for your workspace (for example,
{{LLM_SUBPROCESSORS}}) and to an embedding provider ({{EMBEDDING_SUBPROCESSOR}}).

4.2 Where you configure your own API keys ("bring your own key"), your data is sent to the
provider **you** select under **their** terms.

4.3 A current list of sub-processors is available at {{SUBPROCESSOR_LIST_URL}}. We require
sub-processors to provide protections consistent with this Policy and the DPA.

## 5. Sharing

We share personal information with: sub-processors and service providers (hosting,
payments, model providers) under contract; authorities where required by law; and a
successor entity in a merger or acquisition. **We do not sell personal information.**

## 6. International Transfers

Personal information may be processed in {{DATA_LOCATIONS}}. Where required, we use
appropriate safeguards such as Standard Contractual Clauses for cross-border transfers.

## 7. Retention

We retain account and log data for as long as your account is active and as needed for the
purposes above, then delete or anonymize it. Customer Content is retained per your
instructions and the DPA; on termination, it is available for export for
{{EXPORT_WINDOW_DAYS}} days before deletion in the ordinary course.

## 8. Security

We implement technical and organizational measures including encryption in transit,
encryption of secrets at rest, workspace-level tenant isolation, role-based access
control, and audit logging. No method of transmission or storage is 100% secure.

## 9. Your Rights

Depending on your location, you may have rights to access, correct, delete, port, or
restrict processing of your personal information, and to object or withdraw consent. For
personal data inside uploaded documents, direct requests to the relevant workspace
administrator (the controller); we will assist them as processor. To exercise rights
regarding your **account** data, contact us at **{{PRIVACY_CONTACT_EMAIL}}**.

## 10. Children

The Service is not directed to children under {{CHILDREN_AGE}} and we do not knowingly
collect their personal information.

## 11. Changes

We may update this Policy. Material changes will be notified by email or in-app. The "Last
updated" date reflects the latest revision.

## 12. Contact

{{COMPANY_LEGAL_NAME}}, {{COMPANY_ADDRESS}}. Privacy enquiries: **{{PRIVACY_CONTACT_EMAIL}}**.
{{DPO_STATEMENT}}
