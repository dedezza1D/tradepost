# Security policy

Reports of security problems in tradepost are welcome, in Portuguese or English.

## How to report

Use GitHub's private vulnerability reporting:
[report a vulnerability](https://github.com/dedezza1D/tradepost/security/advisories/new).
The report stays private between you and the maintainer until a fix is published.

Please include what is affected, the steps to reproduce it and the impact you expect. Do not
open a public issue or pull request for a vulnerability.

## Scope

- The source code in this repository.
- The deployed web app and its API, `https://tradepost-2ay.pages.dev`.

The deployed app is a demonstration: payments run only in the providers' test modes and no
real money moves.

Out of scope: the payment providers, hosting, database and storage services themselves
(report to them), and findings that need a compromised device or browser.

## Rules for testing

- Use only accounts you created. Do not access, change or delete data of other users; if you
  reach any by accident, stop, do not keep it, and say so in the report.
- No denial of service, load testing, spam, or automated scanning that degrades the service.
- No social engineering or phishing of users or the maintainer.
- Do not test the payment providers directly; use only the checkout as the app offers it.
- Give a reasonable time to fix before any disclosure; the date is agreed in the report.

## What to expect

- A first answer within 7 days.
- An assessment and, when the report is confirmed, a plan and a target date for the fix.
- Credit in the published advisory, if you want it.

## Safe harbor

Research done in good faith and within these rules is considered authorized: no legal action
will be taken or supported against it. If in doubt about whether something is allowed, ask
first through the reporting form.
