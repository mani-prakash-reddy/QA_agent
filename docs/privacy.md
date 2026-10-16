# Data privacy and model choice

BRDs and application data are confidential. This page says what leaves the machine, why the default model was
chosen, and how to run with a private model.

## What is sent to a model, and what is not
- **Never sent:** passwords, TOTP seeds, one-time codes, stored browser sessions, integration keys. They are
  held by the code that signs in and pushes; every outgoing model message is scanned for them and refused if
  one is found.
- **Sent:** BRD text (to extract requirements), screen descriptions (labels, field rules, masked captions: no
  table rows, e-mails and long tokens masked) and requirement text (to map and to write cases), plus the glossary
  terms and API rules that apply to a requirement. Screenshots are never sent (the models are text-only).
- **Stays local:** the browser, the knowledge model, the database, the OCR of scans and diagrams
  (CPU, bundled models).

## Why the default model
Chat: Groq-hosted `openai/gpt-oss-120b` (planning, authoring, extraction, mapping) and `gpt-oss-20b`. Chosen by
measurement: 0.5-1.3 s per call, correct tool calling and strict JSON-schema output, generous rate limits; the
free-tier models tried took about 10 s per call and could not honour a no-retention request. Embeddings: Azure
OpenAI `text-embedding-ada-002` when configured (an enterprise agreement), otherwise an OpenAI-compatible endpoint.
**Not verified:** Groq's data-retention terms for confidential documents. The `confidential` project flag refuses
free-tier models but does not make a hosted provider private.
