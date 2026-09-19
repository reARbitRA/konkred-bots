# CI workflow

`github-actions-ci.yml` in this directory is the project's complete GitHub
Actions pipeline. It is **not** at `.github/workflows/ci.yml` in this branch's
history because the GitHub App that pushed the branch does not hold the
`workflows` permission, and GitHub rejects any push that creates or updates a
file under `.github/workflows/` without it:

```
refusing to allow a GitHub App to create or update workflow
`.github/workflows/ci.yml` without `workflows` permission
```

## Activate it

One command, from a clone where your own credentials are in use:

```bash
mkdir -p .github/workflows
git mv ci/github-actions-ci.yml .github/workflows/ci.yml
git commit -m "ci: activate the GitHub Actions pipeline"
git push
```

The file needs no edits — it is the exact workflow that was validated.

## What it runs

Four jobs, on every push to `main` / `feat/**` / `fix/**` / `arena/**` and on
every pull request into `main`:

| Job | Checks |
|---|---|
| **gateway** | Registry validation (no duplicate keys, no missing fields, no unknown providers), `node --check` on every `.mjs`, full ESM import-graph resolution, 51 unit tests, then a live server smoke test: health, models, meta, a real inference call, `401` on a bad admin key, `200` on the right one, and `400` on a malformed body |
| **bots** | `compileall`, `flake8`, router/command wiring against `BOT_SPECS`, 300 randomised message-splitter invariant cases, and Redis history + FSM namespace isolation on fakeredis |
| **integration** | The real `GatewayClient` against a real gateway over HTTP: every task route the bots use, multimodal audio parts, JSON mode, and a typed rate-limit error |
| **compose** | `docker compose config`, exact service list, YAML validity of every manifest, and `setup.sh` executable + `bash -n` + shellcheck |

Jobs run with `MOCK_ONLY=true`, so CI needs **no secrets and makes no outbound
model calls**.

## Verified locally

Every job step that can run without Docker or GitHub-hosted infrastructure was
executed locally and passed:

```
[PASS] gateway/Validate the quota registry
[PASS] gateway/Check ESM syntax of every source file
[PASS] gateway/Verify the full ESM import graph resolves
[PASS] gateway/Unit tests
[PASS] bots/Byte-compile every module
[PASS] bots/flake8
[PASS] bots/Import graph and handler wiring
[PASS] bots/Telegram message splitter invariants
[PASS] bots/Redis-backed history and FSM isolation
[PASS] compose/Validate YAML manifests
[PASS] compose/setup.sh must be executable and syntactically valid
```

The gateway smoke test and the integration job were also run by hand against a
live `MOCK_ONLY` gateway; both passed, including the `user_rpm` limiter path.
Only `docker compose config` and `shellcheck` could not run here — Docker and
shellcheck are not installed in the build sandbox.
