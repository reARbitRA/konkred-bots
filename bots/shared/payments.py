"""Redis-backed free-use gate and Telegram Stars/USDT payment flows.

A user receives ``FREE_REQUESTS`` AI-powered actions in each bot. The next
request produces a native Telegram Stars invoice. A confirmed Stars payment
unlocks that bot automatically for ``PAID_ACCESS_DAYS``. An optional USDT path
accepts a transaction id and sends approve/reject buttons to configured admin
Telegram ids; it is deliberately manual because a wallet address alone cannot
prove an on-chain payment safely.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

from .config import BotSpec, settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessDecision:
    """Result of atomically reserving one AI-powered action."""

    allowed: bool
    used: int = 0
    limit: int = 0
    paid: bool = False
    reason: str = ""


class PaymentManager:
    """Revenue gate bound to one bot and one shared Redis connection."""

    def __init__(self, redis_client: Redis, spec: BotSpec) -> None:
        self.redis = redis_client
        self.spec = spec
        signing_secret = settings.payment_secret or settings.webhook_secret or spec.token
        self._signing_secret = signing_secret.encode("utf-8")

    # ------------------------------------------------------------------ keys

    def _counter_key(self, user_id: int | str) -> str:
        return f"konkred:pay:{self.spec.key}:free:{user_id}"

    def _access_key(self, user_id: int | str) -> str:
        return f"konkred:pay:{self.spec.key}:access:{user_id}"

    def _pending_key(self, user_id: int | str) -> str:
        return f"konkred:pay:{self.spec.key}:pending:{user_id}"

    def _tx_key(self, txid: str) -> str:
        digest = hashlib.sha256(txid.lower().encode("utf-8")).hexdigest()
        return f"konkred:pay:{self.spec.key}:usdt:{digest}"

    # ------------------------------------------------------------ free quota

    async def reserve_request(self, user_id: int) -> AccessDecision:
        """Atomically reserve a free action, or confirm paid access.

        Redis WATCH/MULTI keeps concurrent updates from granting more than the
        configured ceiling. It also works on hosted Redis services without
        requiring a custom Lua script.
        """
        if not settings.payments_enabled:
            return AccessDecision(True, paid=True, reason="payments_disabled")

        counter_key = self._counter_key(user_id)
        access_key = self._access_key(user_id)

        for _ in range(8):
            try:
                async with self.redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(access_key, counter_key)
                    access, raw_count = await pipe.mget(access_key, counter_key)
                    if access:
                        await pipe.unwatch()
                        return AccessDecision(True, paid=True, reason="paid")

                    try:
                        used = max(0, int(raw_count or 0))
                    except (TypeError, ValueError):
                        used = 0
                    if used >= settings.free_requests:
                        await pipe.unwatch()
                        return AccessDecision(
                            False,
                            used=used,
                            limit=settings.free_requests,
                            reason="free_limit_reached",
                        )

                    pipe.multi()
                    pipe.incr(counter_key)
                    result = await pipe.execute()
                    reserved = int(result[0])
                    return AccessDecision(
                        True,
                        used=reserved,
                        limit=settings.free_requests,
                        reason="free",
                    )
            except WatchError:
                continue
            except RedisError as exc:
                logger.error("payment access check failed bot=%s user=%s: %s", self.spec.key, user_id, exc)
                return AccessDecision(False, reason="storage_error")

        logger.warning("payment access check contention bot=%s user=%s", self.spec.key, user_id)
        return AccessDecision(False, reason="storage_error")

    async def require(self, message: Message, user_id: int) -> bool:
        """Reserve an action; send the paywall when no action is available."""
        decision = await self.reserve_request(user_id)
        if decision.allowed:
            return True
        if decision.reason == "storage_error":
            await message.answer(
                "⚠️ I can't verify your access right now. Please wait a moment and try again; "
                "you have not been charged."
            )
            return False
        await self.send_paywall(message, user_id)
        return False

    # --------------------------------------------------------------- invoices

    def _signature(self, value: str) -> str:
        return hmac.new(self._signing_secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:20]

    def build_payload(self, user_id: int) -> str:
        body = f"k1:{self.spec.key}:{user_id}:{secrets.token_hex(5)}"
        return f"{body}:{self._signature(body)}"

    def validate_payload(self, payload: str, user_id: int) -> bool:
        try:
            version, bot_key, raw_user, nonce, signature = payload.split(":", 4)
        except ValueError:
            return False
        body = f"{version}:{bot_key}:{raw_user}:{nonce}"
        return (
            version == "k1"
            and bot_key == self.spec.key
            and raw_user == str(user_id)
            and hmac.compare_digest(signature, self._signature(body))
        )

    async def send_paywall(self, message: Message, user_id: int) -> None:
        """Show the native Stars invoice and optional USDT fallback."""
        await message.answer(
            f"🔒 <b>Your {settings.free_requests} free requests are used.</b>\n\n"
            f"Unlock <b>{escape(self.spec.title)}</b> for {settings.paid_access_days} days. "
            "Telegram Stars activates access automatically as soon as Telegram confirms payment."
        )

        try:
            await message.bot.send_invoice(
                chat_id=message.chat.id,
                title=f"{self.spec.title} access"[:32],
                description=(
                    f"{settings.paid_access_days}-day access to {self.spec.title}. "
                    "Activation is automatic after payment."
                )[:255],
                payload=self.build_payload(user_id),
                currency="XTR",
                prices=[
                    LabeledPrice(
                        label=f"{settings.paid_access_days}-day pass",
                        amount=settings.stars_price,
                    )
                ],
                provider_token=None,
            )
        except Exception as exc:  # noqa: BLE001 - Telegram transport/API errors vary
            logger.exception("could not send Stars invoice bot=%s user=%s", self.spec.key, user_id)
            await message.answer(f"⚠️ I couldn't open the Stars invoice: <code>{escape(str(exc))}</code>")

        if settings.usdt_wallet_address:
            support = (
                f"\nSupport: {escape(settings.payment_support)}"
                if settings.payment_support
                else ""
            )
            await message.answer(
                "<b>USDT fallback (manual confirmation)</b>\n"
                f"Amount: <b>{escape(settings.usdt_price)} USDT</b>\n"
                f"Network: <b>{escape(settings.usdt_network)}</b>\n"
                f"Address: <code>{escape(settings.usdt_wallet_address)}</code>\n\n"
                "Send only on the exact network above. After payment, submit:\n"
                "<code>/verify YOUR_TRANSACTION_ID</code>\n"
                "An admin must approve USDT before access is enabled."
                f"{support}"
            )

    # ----------------------------------------------------------- confirmation

    async def grant_access(self, user_id: int, reference: str) -> bool:
        try:
            await self.redis.set(
                self._access_key(user_id),
                reference,
                ex=settings.paid_access_seconds,
            )
            return True
        except RedisError as exc:
            logger.error("could not grant paid access bot=%s user=%s: %s", self.spec.key, user_id, exc)
            return False

    async def confirm_stars(self, message: Message) -> bool:
        payment = message.successful_payment
        if payment is None:
            return False
        user_id = message.from_user.id
        valid = (
            payment.currency == "XTR"
            and payment.total_amount == settings.stars_price
            and self.validate_payload(payment.invoice_payload, user_id)
        )
        if not valid:
            logger.error("invalid successful payment payload bot=%s user=%s", self.spec.key, user_id)
            return False
        reference = f"stars:{payment.telegram_payment_charge_id}"
        return await self.grant_access(user_id, reference)

    # -------------------------------------------------------------- USDT flow

    async def submit_usdt(self, user_id: int, txid: str) -> str:
        txid = "".join(txid.split())[:160]
        if len(txid) < 8:
            return "invalid"
        try:
            claimed = await self.redis.set(self._tx_key(txid), str(user_id), ex=7 * 86400, nx=True)
            if not claimed:
                owner = await self.redis.get(self._tx_key(txid))
                return "pending" if owner == str(user_id) else "duplicate"
            await self.redis.set(self._pending_key(user_id), txid, ex=7 * 86400)
            return "submitted"
        except RedisError as exc:
            logger.error("USDT submission failed bot=%s user=%s: %s", self.spec.key, user_id, exc)
            return "error"

    async def approve_usdt(self, user_id: int) -> bool:
        try:
            txid = await self.redis.get(self._pending_key(user_id))
            if not txid:
                return False
            granted = await self.grant_access(user_id, f"usdt:{txid}")
            if granted:
                await self.redis.delete(self._pending_key(user_id))
            return granted
        except RedisError:
            return False

    async def reject_usdt(self, user_id: int) -> bool:
        try:
            txid = await self.redis.get(self._pending_key(user_id))
            if not txid:
                return False
            await self.redis.delete(self._pending_key(user_id), self._tx_key(txid))
            return True
        except RedisError:
            return False


def create_payment_router() -> Router:  # noqa: C901 - cohesive Telegram payment event surface
    """Build a fresh payment router for one dispatcher/runtime."""
    router = Router(name=f"payments-{secrets.token_hex(3)}")

    @router.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery, payments: PaymentManager) -> None:
        valid = (
            query.currency == "XTR"
            and query.total_amount == settings.stars_price
            and payments.validate_payload(query.invoice_payload, query.from_user.id)
        )
        if valid:
            await query.answer(ok=True)
        else:
            logger.warning("rejected invalid pre-checkout query user=%s", query.from_user.id)
            await query.answer(ok=False, error_message="This invoice is invalid or has expired. Request a new one.")

    @router.message(F.successful_payment)
    async def successful_payment(message: Message, payments: PaymentManager) -> None:
        if await payments.confirm_stars(message):
            await message.answer(
                f"✅ <b>Payment confirmed.</b>\n\n"
                f"{payments.spec.title} is unlocked for {settings.paid_access_days} days. "
                "Send your next request whenever you're ready."
            )
        else:
            await message.answer(
                "⚠️ Telegram reported a payment, but I could not activate access. "
                f"Please contact {escape(settings.payment_support or 'support')} with your receipt."
            )

    @router.message(Command("paysupport"))
    async def payment_support(message: Message) -> None:
        if settings.payment_support:
            await message.answer(
                "💳 <b>Payment support</b>\n\n"
                f"Contact {escape(settings.payment_support)} with your Telegram payment receipt "
                "or USDT transaction id. Never send your wallet seed phrase or private key."
            )
        else:
            await message.answer(
                "💳 Payment support has not been configured yet. Please contact the bot owner "
                "and include your Telegram receipt; never share a seed phrase or private key."
            )

    @router.message(Command("verify"))
    async def verify_usdt(
        message: Message,
        command: CommandObject,
        payments: PaymentManager,
        bot: Bot,
    ) -> None:
        if not settings.usdt_wallet_address:
            await message.answer("USDT payments are not enabled for this bot.")
            return
        if not settings.payment_admin_ids:
            await message.answer(
                "USDT verification is not configured. Use /paysupport before transferring funds."
            )
            return
        txid = (command.args or "").strip()
        status = await payments.submit_usdt(message.from_user.id, txid)
        if status == "invalid":
            await message.answer("Usage: <code>/verify YOUR_TRANSACTION_ID</code>")
            return
        if status == "duplicate":
            await message.answer("⚠️ That transaction id was already submitted by another account.")
            return
        if status == "pending":
            await message.answer("⏳ That transaction is already waiting for admin review.")
            return
        if status == "error":
            await message.answer("⚠️ I couldn't save the verification request. Please try again shortly.")
            return

        await message.answer("✅ Transaction submitted. An admin will review it before access is enabled.")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Approve", callback_data=f"pay:approve:{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"pay:reject:{message.from_user.id}"),
        ]])
        username = f"@{message.from_user.username}" if message.from_user.username else "(no username)"
        admin_text = (
            f"💵 <b>USDT verification — {escape(payments.spec.title)}</b>\n"
            f"User: <code>{message.from_user.id}</code> {escape(username)}\n"
            f"Network: {escape(settings.usdt_network)}\n"
            f"Expected: {escape(settings.usdt_price)} USDT\n"
            f"TXID: <code>{escape(txid)}</code>\n\n"
            "Verify this transaction in a block explorer before approving."
        )
        for admin_id in settings.payment_admin_ids:
            try:
                await bot.send_message(admin_id, admin_text, reply_markup=keyboard)
            except Exception:  # noqa: BLE001
                logger.exception("could not notify payment admin %s", admin_id)

    @router.callback_query(F.data.regexp(r"^pay:(approve|reject):\d+$"))
    async def review_usdt(query: CallbackQuery, payments: PaymentManager, bot: Bot) -> None:
        if query.from_user.id not in settings.payment_admin_ids:
            await query.answer("Admins only.", show_alert=True)
            return
        _, action, raw_user_id = query.data.split(":", 2)
        user_id = int(raw_user_id)
        if action == "approve":
            changed = await payments.approve_usdt(user_id)
            if changed:
                await query.answer("Access granted.")
                await query.message.edit_reply_markup(reply_markup=None)
                await bot.send_message(
                    user_id,
                    f"✅ Your USDT payment was approved. {payments.spec.title} is unlocked for "
                    f"{settings.paid_access_days} days.",
                )
            else:
                await query.answer("No pending payment found.", show_alert=True)
        else:
            changed = await payments.reject_usdt(user_id)
            if changed:
                await query.answer("Submission rejected.")
                await query.message.edit_reply_markup(reply_markup=None)
                await bot.send_message(user_id, "❌ Your USDT submission could not be verified. Please contact support.")
            else:
                await query.answer("No pending payment found.", show_alert=True)

    return router


__all__ = ["AccessDecision", "PaymentManager", "create_payment_router"]
