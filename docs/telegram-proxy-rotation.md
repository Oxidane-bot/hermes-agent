# Telegram proxy rotation helper

This fork keeps Telegram reconnect handling inside `gateway/platforms/telegram.py`, but the **Clash/Mihomo node selection** lives outside the gateway state machine on purpose.

Helper module:
- `gateway/telegram_proxy_rotation.py`

Clash controller assumptions:
- local controller on `http://127.0.0.1:9090`
- selector name: `电报消息`
- Telegram test URL: `https://api.telegram.org`

## Why it exists

When Telegram long-polling breaks, the bot channel may be unable to carry any command traffic back to Hermes. A separate local helper can still act on the Clash controller directly and switch the proxy selector without relying on Telegram itself.

## Event-driven mode

The preferred path is now **active event reporting**:
- each profile emits a small failure event locally
- the helper keeps the global state and cooldown
- the helper decides whether to rotate `电报消息`
- profile identity is for diagnostics only
- reporting must be fire-and-forget and must never block reconnect

Use the helper only for **network-style Telegram failures**:
- `ConnectError`
- `RemoteProtocolError`
- `TimedOut`
- heartbeat probe failure after a reconnect

Do **not** use it for:
- `telegram_polling_conflict`
- bot token already in use
- auth/config errors
- missing controller / missing selector

Suggested threshold:
- first network failure: retry only
- second consecutive network failure: run selector rotation
- if rotation succeeds: cool down for 30 minutes

## Rotation order

1. Read the current `电报消息` selector state from `/proxies/电报消息`.
2. Build the candidate list.
   - Skip `节点选择`
   - Skip `自动选择`
   - Skip `手动切换`
   - Skip `DIRECT`
   - Prefer leaving the current node as the last fallback only
3. For each candidate, call the delay endpoint:
   - `/proxies/{candidate}/delay?url=https://api.telegram.org&timeout=5000`
4. Sort by delay.
5. Put the lowest-delay healthy candidate into `电报消息`.
6. Confirm the selector changed.
7. Enter a cooldown window so the helper does not thrash.

## CLI usage

The helper can be used in three modes:

```bash
python gateway/telegram_proxy_rotation.py plan
python gateway/telegram_proxy_rotation.py once
python gateway/telegram_proxy_rotation.py watch --log-path ~/.hermes/logs/gateway.log
```

### `plan`
Prints the measured candidate order and exits.

### `once`
Measures candidates once and switches at most one time.

### `watch`
Tails `gateway.log` and triggers selector rotation after repeated network failures.

Use `--dry-run` to inspect decisions without writing to Clash.

## Runtime notes

- The helper uses the local Clash/Mihomo controller directly.
- It does not depend on Telegram delivery.
- It only changes the `电报消息` selector.
- It does not touch the rest of the Hermes gateway logic.

## Failure modes

If the controller is down, the selector name changes, or all candidate nodes fail the Telegram delay test, the helper should stop rotating and leave the current selector untouched.
