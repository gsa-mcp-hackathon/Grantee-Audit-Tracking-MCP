# Project overview


## The problem

Organizations that spend federal grant money above a threshold in a year must
get a **Single Audit** and file it with the **Federal Audit Clearinghouse
(FAC)**. HRSA staff need to know, for each grantee, whether it filed, what the
auditor found, and how the organization is structured.

Answering that for one grantee means piecing together records from several
federal systems that don't share a single identifier. A grantee's audit may be
filed under a mistyped UEI, findable only by EIN, or inside a parent
organization's audit that names it only in text. FAC uses the organization's
own fiscal year and USAspending the federal one. An analyst could spend hours on
one organization.

## The concept

HRSA's Delinquent Audit Tracker (DAT) runs fixed rules over every grantee. It
handles clear cases well, but many grantees end up in manual review,
especially ones that may be covered by another organization's audit.

This project gives an AI agent tools to investigate one of those grantees
across FAC, SAM.gov, USAspending, and IRS exempt-organization records, all
public, and return a sourced picture with its limits stated. **The agent
gathers the evidence; the analyst makes the call.** The tools never label a
grantee compliant or delinquent.

## Ground rules

- **Public data only.** No internal HRSA systems, hostnames, names, or emails
  anywhere in this repo, including tests and eval sets.
