# Green Receipts for AI Compute

A proxy that gives every AI request a carbon receipt: estimated energy and CO2e, the grid
intensity used, the region and hour, and an error range. Built for the IEEE ClimateChain
Global Hackathon 2026, Track 1 (Carbon Markets & Emissions Transparency).

## Status

| Slice | State |
|---|---|
| Scaffold and core loop (request in, answer plus receipt out) | working |
| Trust layer (signed receipts, on-chain record, verify endpoint, tamper rejection) | working on a local chain |
| Demo surface (dashboard, region comparison, methodology page) | working |

## Run it

Python 3.14 and Node 24 were used. Everything runs on a local test chain with no real value.

```bash
# 1. Python setup and tests
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q

# 2. Contract: build, test, start a local node (leave it running)
cd chain
npm install
npx hardhat build
npx hardhat test
npx hardhat node

# 3. In another terminal: deploy, then start the API
.venv/Scripts/python.exe scripts/deploy_local.py
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ for the dashboard (send a request, verify it, tamper with it, compare
regions, read the organisation ledger) and http://127.0.0.1:8000/methodology for the method,
the error range and the limits.

The Python tests use an in-memory stand-in for the chain, except `tests/test_web3_chain.py`,
which runs against the real contract when a node is up and is skipped otherwise.

## How trust works

1. The service builds a receipt and hashes it (keccak256 over a canonical JSON form: sorted keys,
   no whitespace, numbers as plain decimals with no exponent).
2. The issuer key signs the hash, and the contract records the hash with the emissions range
   and adds it to the organisation's running total.
3. `POST /v1/verify` takes a receipt and its signature and checks that the signature recovers a
   signer, that the signer is the account the chain recorded this receipt from, that this exact
   hash is recorded, and that the recorded organisation and range match the receipt.
   Changing any field changes the hash, so an edited receipt fails.

`GET /v1/orgs/{org}/totals` reads the organisation's running total from the chain.

Try a request:

```bash
curl -X POST http://127.0.0.1:8000/v1/chat -H "Content-Type: application/json" \
  -d '{"org":"acme","prompt":"Explain carbon markets in two sentences.","region":"eu-north"}'
```

## Check a receipt without our server

The dashboard's "Download receipt file" button saves the receipt, its signature, the contract address and the node URL. `scripts/verify_standalone.py` then checks it against the chain directly. It imports none of the service's code, so it cannot share a bug with it.

```bash
.venv/Scripts/python.exe scripts/verify_standalone.py receipt-1a2b3c4d.json
```

It prints PASS or FAIL for each check and exits 0 when the receipt verifies, 1 when it does not, and 2 when the node cannot be reached. Because the signer is compared with the issuer the chain recorded for that receipt, a receipt stays valid after the issuer role is revoked or the key is rotated, while new receipts from the revoked key are refused.

## Why a blockchain

A signature alone catches edits. It does not stop the issuer from backdating a receipt, deleting one, or showing two parties different totals. Recording each receipt's hash and emissions range in a contract gives a timestamped, append-only history and organisation totals that anyone can read from the chain instead of from our page.

It does **not** make the inputs true. The record shows who vouched for a number and that it was not changed afterwards.

## What this proves, and what it does not

| It shows | It does not show |
|---|---|
| This exact receipt was issued by the issuer key and recorded at a point in time | That the energy, region or grid values are correct |
| An edited receipt is detected | That the request really happened as described (receipts are not yet bound to a prompt or provider response) |
| Organisation totals match the recorded receipts | That an issuer who colludes with a user cannot mint receipts |

The words to use are **tamper-evident** and **attributable**, not "proven emissions".

## API

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat` | Send a request; returns the answer, the receipt and its proof (hash, signature, issuer, transaction) |
| `POST /v1/verify` | Check a receipt and signature: well formed, signer recovered, signer is the recorded issuer, hash recorded, recorded values match |
| `GET /v1/orgs/{org}/totals` | An organisation's running total, read from the contract |
| `GET /v1/methodology` | The formula, error range, grid profiles and limits the receipts use |
| `GET /v1/status` | Whether the chain is reachable and the contract deployed |
| `GET /` and `GET /methodology` | The dashboard and the methodology page |

## How the estimate works

`gCO2e = energy_kWh x grid_intensity_gCO2e_per_kWh + embodied_gCO2e`

This has the same shape as the Green Software Foundation's Software Carbon Intensity formula, ((E x I) + M) per functional unit, with one AI request as the unit. It is not a certified SCI score.

- Energy range per request comes from the open-source EcoLogits estimator (MPL-2.0).
- Grid intensity is an hourly value for the region the caller declares.
- The emissions range combines the estimator's energy range with an assumed 15% uncertainty on intensity.

## Licence

MIT, copyright 2026 Ember Syndicate. See [LICENSE](LICENSE). The energy estimator (EcoLogits, MPL-2.0) and OpenZeppelin Contracts (MIT) are used as dependencies under their own licences.

## Limits, stated plainly

- This is an estimate with a range. It is not audit-grade accounting.
- The answering model is a mock. Impact is estimated as if a reference model (openai/gpt-4o-mini) served it, with simulated latency.
- Grid intensity comes from bundled illustrative profiles, not live measurements.
- The region is declared by the caller. The provider's real data-centre region is not observed.
- There is no authentication. Anyone who can reach the service can create receipts for any organisation name, and each one costs a transaction. Run it on 127.0.0.1 only. Before any real use it needs per-organisation credentials, rate limits and a gas budget, and the issuer key must move to a key management service.
- Recorded totals are whole nanograms, so a receipt under half a nanogram records as zero.
- The local node's accounts are the public Hardhat test accounts, and the contract admin is the first of them. Never expose the node beyond localhost.
- Restarting the node wipes the chain. Run `scripts/deploy_local.py` again; the API notices the new contract on its next request.
