# Payment, gift and runtime safety fixes

Base commit: `bd5d0afc823b4818308ba8c226aa7e78bd17fbf6`.

## What changed

- **Stars:** successful payments are handled as raw service-message updates, not ordinary messages. Pre-checkout validates owner, currency, amount, state and expiry. A durable, unique payment reference prevents double credit. A bounded, cursor-based history reconciliation job recovers missed successes. Pending invoices age out without rejecting a later verified payment.
- **Invoice creation:** persist a cryptographically random order ID and direct-pay link before exposing an invoice. Enforce the pending limit while holding the user's database lock. Check database errors before returning payment links.
- **ZarinPal:** persist merchant/sandbox configuration per invoice and the bank reference on payment. Old invoices enter reconciliation instead of being discarded without verification. Rechecks use age-based backoff. Manual checks enforce ownership. HTTP/protocol errors are non-successful responses.
- **Referral:** reward balance and its ledger record commit inside the original deposit transaction. Unique source keys and a unique first-bonus claim prevent replay; gifts and referral credits are not deposits. Rewards are independent of direct-pay fulfillment and notification delivery. Fixed `(False, reason)` tuple handling and atomic referral binding/invite increment.
- **Gifts:** fixed sync/async code generation, panel field names, code validation, callback size, and missing-service checks. Balance gifts credit wallet and record consumption atomically. Service gifts use durable absolute-target intents and renewable Redis locks; uncertain external writes are reconciled instead of incremented again. A definitively rejected write releases its exact reservation, preserving history. Re-entering a code resumes an unfinished request. Unlimited services remain unlimited. Panel API-key/password authentication and token refresh use the shared auth layer.
- **Restore:** destructive online restore is refused. The server CLI stops the bot, takes a private pre-drop safety backup, imports with backpressure, migrates the restored schema and reloads database secrets. Failures leave the bot stopped and retain the safety backup. Legacy backups without a recoverable crypto key are rejected; existing database secrets are not overwritten with arbitrary `.env` values. ZIP extraction is bounded and rejects traversal, duplicate entries and unrelated files.
- **Backup:** SQL streams to a mode-0600 file rather than being buffered in RAM. Safety backups are private and use unique timestamps.
- **Runtime:** critical bot/API task exits propagate and trigger bounded shutdown. Failed Redis clients are not cached; safety locks fail closed, renew, and release only for their owner. Unexpected middleware errors stop the pipeline.
- **Webhooks:** authentication uses constant-time comparison, payloads are bounded, schema errors return 400, and transient handler failures return 503. Completed events have durable deduplication hashes and bounded retention. Raw payloads/secrets are not logged by ingress.
- **Development:** regression tests are tracked (the previous `/tests` ignore was removed). CI now runs SQLite tests and MariaDB/Redis regressions in addition to lint/format/compile. `docker-compose.dev.yml` explicitly builds edited source rather than pulling an old image.

## Migration and rollout

1. Make a trusted backup of the existing database and configuration. Keep it outside a public channel or repository.
2. Test on a separate bot and database first. Real Telegram/merchant/panel interoperability must be checked against your installed versions.
3. Update both the application/image and the management script. **An old Docker image cannot run the new offline restore CLI.**
4. Run `uv run alembic upgrade head` before starting the application (Docker entrypoint and the native systemd unit already run migrations). New head: `f19c8d42a601`.
5. Set ZarinPal's **HTTPS callback URL** under payment settings. It must be on the merchant-approved domain, not `t.me`. An example endpoint is `https://bot.example.com/api/payments/zarinpal/return`; enable FastAPI and proxy it through HTTPS. This page is informational: query-string parameters never credit a wallet. Verification is server-to-server.
6. Test a low-value payment, a duplicate event, a missed-event reconciliation and each gift type. Verify wallet/ledger balances before enabling broadly.

For a local Docker build:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build bot
```

Supply a private `.env` as described by `.env.example`. In Docker, use `redis://redis:6379` and MariaDB hostname `mariadb`, not host-loopback addresses. A native installation uses its own loopback ports. The app's live credentials are not required for the test suite.

## Restore procedure

```sh
sudo pasarguardbot restore /absolute/path/to/trusted-backup.zip
```

The wrapper requires confirmation, obtains an installation lock, stops the bot, and invokes the offline Python implementation. All other writers to the same database must also be stopped. MariaDB and Redis must be healthy. Safety backups are retained under `logs/restore-safety/before-*/` with private permissions.

Do not restart the bot after failure. Inspect the error and use a known-good backup or the retained pre-drop backup. Automatic rollback of arbitrary partially imported SQL is intentionally not attempted. Backups selecting a different database name must first be restored to an isolated matching database; do not import them into a differently named live database.

Current backups store crypto/webhook secrets in the SQL `secrets` table. Legacy backups need their original `.env` `CRYPTO_KEY` so the secrets migration can seed the correct value. The current installation's Telegram token, database credentials and branding are not replaced.

## Tests

```sh
uv sync --frozen
uv run ruff check
uv run ruff format --check
uv run pytest -q
```

Real database/concurrency checks (disposable loopback database only; name must end in `_test`):

```sh
TEST_DATABASE_URL='mysql+asyncmy://testuser:TEST_PASSWORD@127.0.0.1:33306/pasarguard_test' \
TEST_REDIS_URL='redis://127.0.0.1:36379' uv run pytest -q
```

The fixture drops/recreates mapped tables in that **test** database. Never provide production credentials.

An explicit destructive restore round-trip test is available separately:

```sh
RESTORE_TEST_DATABASE_URL='mysql+asyncmy://testuser:TEST_PASSWORD@127.0.0.1:33306/restore_test' \
TEST_REDIS_URL='redis://127.0.0.1:36379' \
uv run python tests/integration/restore_roundtrip.py
```

Create an empty disposable database first. This exercises full migrations, a current backup, a legacy backup, actual invalid SQL and recovery. It does not contact Telegram or a real panel/payment gateway.

## Operational limits

- External APIs cannot share a SQL transaction. Ambiguous gift results retain an `applying` intent for reconciliation. If the remote value changed independently from both saved old/new targets, stop and investigate; do not blindly retry/add volume. Re-entering the code resumes the same request. Do not edit/delete these ledger rows casually.
- Historical incorrect gift consumptions or missing rewards from the old version are **not automatically backfilled**; old records may be ambiguous. Reconcile them with actual payments and panel state before any corrective credit.
- Webhook handling is at-least-once with deduplication of recorded completions, not an impossible exactly-once guarantee across Telegram and SQL. A crash after an external send but before recording completion can repeat a notification.
- Production concurrent financial operations should use MariaDB/PostgreSQL row locks, not SQLite. SQLite tests cover functional logic; the row-lock concurrency tests run on MariaDB.
- The return URL must match your merchant policy; live Stars settlement/refunds and panel API-version compatibility still require staging verification. Refunds/chargebacks are not automatically converted into negative wallet balances by this change.
