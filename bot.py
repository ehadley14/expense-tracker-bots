#!/usr/bin/env python3
"""
Telegram Expense Tracker Bot for Real Estate Flip Projects.
Self-contained, Railway-deployed. Reads config from environment variables.
Auth is hardcoded — Ed, Thomas, Josh always have access.
"""

import os
import sys
import json
import csv
import io
import logging
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("expense_bot")

# ── Conversation states ──────────────────────────────────────────────────────
CATEGORY, DESCRIPTION, AMOUNT, PAYER, PAYER_CUSTOM, RECEIPT = range(6)

# ── Categories ───────────────────────────────────────────────────────────────
CATEGORIES = [
    "Materials", "Labor", "Permits", "Demolition", "Plumbing",
    "Electrical", "HVAC", "Roofing", "Flooring", "Paint",
    "Appliances", "Landscaping", "Misc",
]

KNOWN_PAYERS = ["Ed", "Thomas", "Josh"]

# ── Hardcoded authorized users ───────────────────────────────────────────────
AUTHORIZED_USERS = {
    7126943593: {"name": "Ed Hadley", "role": "owner"},
    8679118477: {"name": "Thomas Purvis", "role": "editor"},
    8633263252: {"name": "Josh Moseley", "role": "editor"},
}


# ── Config from env ──────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PROPERTY_NAME = os.environ.get("PROPERTY_NAME", "Unknown Property")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


# ── Data helpers ─────────────────────────────────────────────────────────────
def expenses_path():
    return os.path.join(DATA_DIR, "expenses.json")


def receipts_dir():
    d = os.path.join(DATA_DIR, "receipts")
    os.makedirs(d, exist_ok=True)
    return d


def load_expenses():
    p = expenses_path()
    if os.path.exists(p):
        try:
            with open(p, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_expenses(data):
    p = expenses_path()
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


# ── Auth check ───────────────────────────────────────────────────────────────
def is_authorized(user_id):
    return int(user_id) in AUTHORIZED_USERS


def require_auth(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_authorized(uid):
            await update.effective_message.reply_text(
                f"⛔ Access denied.\n\nYour Telegram ID: `{uid}`\n"
                "Contact the project owner to be added.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END if hasattr(func, '_is_conv') else None
        return await func(update, context)
    wrapper._is_conv = getattr(func, '_is_conv', False)
    return wrapper


# ── /start ───────────────────────────────────────────────────────────────────
@require_auth
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    expenses = load_expenses()
    total = sum(e.get("amount", 0) for e in expenses)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Expense", callback_data="menu_add")],
        [InlineKeyboardButton("📊 Summary", callback_data="menu_summary")],
        [InlineKeyboardButton("📋 View All", callback_data="menu_viewall")],
        [InlineKeyboardButton("📤 Export CSV", callback_data="menu_export")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help")],
    ])
    await update.message.reply_text(
        f"🏠 *{PROPERTY_NAME}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Welcome, {user.first_name}!\n\n"
        f"💰 Running total: *${total:,.2f}* ({len(expenses)} expenses)\n\n"
        f"What would you like to do?",
        reply_markup=kb,
        parse_mode="Markdown",
    )


# ── /help ────────────────────────────────────────────────────────────────────
@require_auth
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🏠 *{PROPERTY_NAME}* — Commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "/start — Main menu\n"
        "/add — Add a new expense\n"
        "/summary — View totals & breakdown\n"
        "/viewall — List all expenses\n"
        "/export — Download CSV\n"
        "/receipt <id> — View a receipt photo\n"
        "/delete <id> — Delete an expense\n"
        "/deletelast — Delete most recent\n"
        "/cancel — Cancel current action\n"
        "/help — Show this message",
        parse_mode="Markdown",
    )


# ── Menu button router ──────────────────────────────────────────────────────
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "menu_add":
        return await conv_start_add(update, context)
    elif data == "menu_summary":
        await show_summary(q.message)
    elif data == "menu_viewall":
        await show_viewall(q.message)
    elif data == "menu_export":
        await do_export(q.message)
    elif data == "menu_help":
        await q.message.reply_text(
            f"🏠 *{PROPERTY_NAME}* — Commands\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "/start — Main menu\n"
            "/add — Add a new expense\n"
            "/summary — View totals & breakdown\n"
            "/viewall — List all expenses\n"
            "/export — Download CSV\n"
            "/receipt <id> — View a receipt photo\n"
            "/delete <id> — Delete an expense\n"
            "/deletelast — Delete most recent\n"
            "/cancel — Cancel current action\n"
            "/help — Show this message",
            parse_mode="Markdown",
        )


# ── Add expense conversation ────────────────────────────────────────────────
async def conv_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        uid = update.callback_query.from_user.id
    else:
        uid = update.effective_user.id
    if not is_authorized(uid):
        msg = update.callback_query.message if update.callback_query else update.message
        await msg.reply_text("⛔ Access denied.")
        return ConversationHandler.END

    rows = []
    for i in range(0, len(CATEGORIES), 3):
        row = [
            InlineKeyboardButton(c, callback_data=f"cat_{c}")
            for c in CATEGORIES[i:i+3]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cat_cancel")])
    kb = InlineKeyboardMarkup(rows)

    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text("📂 *Select a category:*", reply_markup=kb, parse_mode="Markdown")
    return CATEGORY


@require_auth
async def conv_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cat_cancel":
        await q.message.reply_text("❌ Cancelled.")
        return ConversationHandler.END
    cat = q.data.replace("cat_", "")
    context.user_data["new_expense"] = {"category": cat}
    await q.message.reply_text(f"📝 Category: *{cat}*\n\nType a description:", parse_mode="Markdown")
    return DESCRIPTION


@require_auth
async def conv_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if not desc:
        await update.message.reply_text("Description can't be empty. Try again:")
        return DESCRIPTION
    context.user_data["new_expense"]["description"] = desc
    await update.message.reply_text(f"💵 Enter the amount (numbers only, e.g. 1500.00):")
    return AMOUNT


@require_auth
async def conv_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace("$", "").replace(",", "")
    try:
        amount = round(float(raw), 2)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Enter a positive number:")
        return AMOUNT
    context.user_data["new_expense"]["amount"] = amount

    rows = [[InlineKeyboardButton(p, callback_data=f"payer_{p}") for p in KNOWN_PAYERS]]
    rows.append([InlineKeyboardButton("✏️ Other (type a name)", callback_data="payer_other")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="payer_cancel")])
    kb = InlineKeyboardMarkup(rows)
    await update.message.reply_text("💳 *Who paid?*", reply_markup=kb, parse_mode="Markdown")
    return PAYER


@require_auth
async def conv_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "payer_cancel":
        await q.message.reply_text("❌ Cancelled.")
        return ConversationHandler.END
    if q.data == "payer_other":
        await q.message.reply_text("Type the payer's name:")
        return PAYER_CUSTOM
    payer = q.data.replace("payer_", "")
    context.user_data["new_expense"]["paid_by"] = payer
    return await ask_receipt(q.message, context)


@require_auth
async def conv_payer_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name can't be empty. Try again:")
        return PAYER_CUSTOM
    context.user_data["new_expense"]["paid_by"] = name
    return await ask_receipt(update.message, context)


async def ask_receipt(message, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Attach Receipt Photo", callback_data="receipt_yes")],
        [InlineKeyboardButton("⏭ Skip — No Receipt", callback_data="receipt_skip")],
    ])
    await message.reply_text("📸 *Attach a receipt photo?*", reply_markup=kb, parse_mode="Markdown")
    return RECEIPT


async def conv_receipt_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "receipt_skip":
        context.user_data["new_expense"]["receipt"] = None
        return await save_expense(q.message, context, update.effective_user)
    elif q.data == "receipt_yes":
        await q.message.reply_text("📷 Send the receipt photo now:")
        return RECEIPT


async def conv_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    expenses = load_expenses()
    next_id = max((e.get("id", 0) for e in expenses), default=0) + 1
    filename = f"receipt_{next_id}.jpg"
    filepath = os.path.join(receipts_dir(), filename)
    await file.download_to_drive(filepath)
    context.user_data["new_expense"]["receipt"] = filename
    return await save_expense(update.message, context, update.effective_user)


async def save_expense(message, context, user):
    exp = context.user_data.pop("new_expense", {})
    expenses = load_expenses()
    next_id = max((e.get("id", 0) for e in expenses), default=0) + 1
    entry = {
        "id": next_id,
        "category": exp.get("category", "Misc"),
        "description": exp.get("description", ""),
        "amount": exp.get("amount", 0),
        "paid_by": exp.get("paid_by", "Unknown"),
        "receipt": exp.get("receipt"),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "added_by": user.first_name if user else "Unknown",
        "user_id": user.id if user else 0,
    }
    expenses.append(entry)
    save_expenses(expenses)
    total = sum(e.get("amount", 0) for e in expenses)
    receipt_icon = " 📸" if entry["receipt"] else ""
    await message.reply_text(
        f"✅ *Expense #{next_id} saved!*{receipt_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 {entry['category']}\n"
        f"📝 {entry['description']}\n"
        f"💵 ${entry['amount']:,.2f}\n"
        f"💳 Paid by: {entry['paid_by']}\n"
        f"📅 {entry['date']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Running total: *${total:,.2f}*",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_expense", None)
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ── /summary ─────────────────────────────────────────────────────────────────
@require_auth
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_summary(update.message)


async def show_summary(message):
    expenses = load_expenses()
    if not expenses:
        await message.reply_text(f"🏠 *{PROPERTY_NAME}*\n\nNo expenses logged yet.", parse_mode="Markdown")
        return
    total = sum(e.get("amount", 0) for e in expenses)
    receipt_count = sum(1 for e in expenses if e.get("receipt"))

    # By category
    by_cat = {}
    for e in expenses:
        c = e.get("category", "Misc")
        by_cat[c] = by_cat.get(c, 0) + e.get("amount", 0)
    cat_lines = []
    for c, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        pct = (amt / total * 100) if total > 0 else 0
        cat_lines.append(f"  {c}: ${amt:,.2f} ({pct:.1f}%)")

    # By payer
    by_payer = {}
    for e in expenses:
        p = e.get("paid_by", "Unknown")
        by_payer[p] = by_payer.get(p, 0) + e.get("amount", 0)
    payer_lines = []
    for p, amt in sorted(by_payer.items(), key=lambda x: -x[1]):
        pct = (amt / total * 100) if total > 0 else 0
        payer_lines.append(f"  💳 {p}: ${amt:,.2f} ({pct:.1f}%)")

    text = (
        f"🏠 *{PROPERTY_NAME}* — Summary\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Total: *${total:,.2f}* ({len(expenses)} expenses)\n"
        f"📸 Receipts: {receipt_count} of {len(expenses)}\n\n"
        f"*By Category:*\n" + "\n".join(cat_lines) + "\n\n"
        f"*By Who Paid:*\n" + "\n".join(payer_lines) +
        f"\n━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply_text(text, parse_mode="Markdown")


# ── /viewall ─────────────────────────────────────────────────────────────────
@require_auth
async def cmd_viewall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_viewall(update.message)


async def show_viewall(message):
    expenses = load_expenses()
    if not expenses:
        await message.reply_text(f"🏠 *{PROPERTY_NAME}*\n\nNo expenses logged yet.", parse_mode="Markdown")
        return
    lines = [f"🏠 *{PROPERTY_NAME}* — All Expenses\n━━━━━━━━━━━━━━━━━━━━━━"]
    for e in expenses:
        r = " 📸" if e.get("receipt") else ""
        lines.append(
            f"#{e['id']} | {e.get('date','')} | {e.get('category','')}\n"
            f"  {e.get('description','')} — ${e.get('amount',0):,.2f} "
            f"💳 {e.get('paid_by','?')}{r}"
        )
    total = sum(e.get("amount", 0) for e in expenses)
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━\n💰 Total: *${total:,.2f}*")

    text = "\n".join(lines)
    # Telegram max message is 4096 chars
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.reply_text(text[i:i+4000], parse_mode="Markdown")
    else:
        await message.reply_text(text, parse_mode="Markdown")


# ── /export ──────────────────────────────────────────────────────────────────
@require_auth
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_export(update.message)


async def do_export(message):
    expenses = load_expenses()
    if not expenses:
        await message.reply_text("No expenses to export.")
        return
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Date", "Category", "Description", "Amount", "Paid By", "Receipt", "Added By"])
    for e in expenses:
        writer.writerow([
            e.get("id", ""),
            e.get("date", ""),
            e.get("category", ""),
            e.get("description", ""),
            e.get("amount", 0),
            e.get("paid_by", ""),
            "Yes" if e.get("receipt") else "No",
            e.get("added_by", ""),
        ])
    buf.seek(0)
    filename = f"{PROPERTY_NAME.replace(' ', '_')}_expenses.csv"
    bio = io.BytesIO(buf.getvalue().encode("utf-8"))
    bio.name = filename
    await message.reply_document(document=bio, filename=filename, caption=f"📤 {PROPERTY_NAME} — {len(expenses)} expenses exported")


# ── /receipt <id> ────────────────────────────────────────────────────────────
@require_auth
async def cmd_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /receipt <id>\nExample: /receipt 3")
        return
    try:
        eid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID. Use a number.")
        return
    expenses = load_expenses()
    entry = next((e for e in expenses if e.get("id") == eid), None)
    if not entry:
        await update.message.reply_text(f"Expense #{eid} not found.")
        return
    if not entry.get("receipt"):
        await update.message.reply_text(f"Expense #{eid} has no receipt photo.")
        return
    filepath = os.path.join(receipts_dir(), entry["receipt"])
    if not os.path.exists(filepath):
        await update.message.reply_text(f"Receipt file missing from storage.")
        return
    await update.message.reply_photo(
        photo=open(filepath, "rb"),
        caption=(
            f"🧾 Receipt for expense #{eid}\n"
            f"📂 {entry.get('category','')}\n"
            f"📝 {entry.get('description','')}\n"
            f"💵 ${entry.get('amount',0):,.2f}\n"
            f"💳 {entry.get('paid_by','')}"
        ),
    )


# ── /delete <id> ─────────────────────────────────────────────────────────────
@require_auth
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /delete <id>\nExample: /delete 3")
        return
    try:
        eid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    expenses = load_expenses()
    entry = next((e for e in expenses if e.get("id") == eid), None)
    if not entry:
        await update.message.reply_text(f"Expense #{eid} not found.")
        return
    expenses = [e for e in expenses if e.get("id") != eid]
    save_expenses(expenses)
    # Clean up receipt
    if entry.get("receipt"):
        rpath = os.path.join(receipts_dir(), entry["receipt"])
        if os.path.exists(rpath):
            os.remove(rpath)
    await update.message.reply_text(
        f"🗑 Deleted expense #{eid}\n"
        f"  {entry.get('category','')} — {entry.get('description','')}\n"
        f"  ${entry.get('amount',0):,.2f} (paid by {entry.get('paid_by','')})"
    )


# ── /deletelast ──────────────────────────────────────────────────────────────
@require_auth
async def cmd_deletelast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expenses = load_expenses()
    if not expenses:
        await update.message.reply_text("No expenses to delete.")
        return
    entry = expenses.pop()
    save_expenses(expenses)
    if entry.get("receipt"):
        rpath = os.path.join(receipts_dir(), entry["receipt"])
        if os.path.exists(rpath):
            os.remove(rpath)
    await update.message.reply_text(
        f"🗑 Deleted last expense #{entry.get('id','')}\n"
        f"  {entry.get('category','')} — {entry.get('description','')}\n"
        f"  ${entry.get('amount',0):,.2f} (paid by {entry.get('paid_by','')})"
    )


# ── Build the application ───────────────────────────────────────────────────
def create_app():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN environment variable is required!")
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "receipts"), exist_ok=True)

    # Init expenses file if missing
    if not os.path.exists(expenses_path()):
        save_expenses([])

    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for adding expenses
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", conv_start_add),
            CallbackQueryHandler(conv_start_add, pattern="^menu_add$"),
        ],
        states={
            CATEGORY: [CallbackQueryHandler(conv_category, pattern="^cat_")],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_description)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_amount)],
            PAYER: [
                CallbackQueryHandler(conv_payer, pattern="^payer_"),
            ],
            PAYER_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_payer_custom)],
            RECEIPT: [
                CallbackQueryHandler(conv_receipt_button, pattern="^receipt_"),
                MessageHandler(filters.PHOTO, conv_receipt_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("viewall", cmd_viewall))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("receipt", cmd_receipt))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("deletelast", cmd_deletelast))
    app.add_handler(CallbackQueryHandler(menu_router, pattern="^menu_"))

    return app


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"=== Expense Bot Starting ===")
    log.info(f"Property: {PROPERTY_NAME}")
    log.info(f"Data dir: {DATA_DIR}")
    log.info(f"Authorized users: {list(AUTHORIZED_USERS.keys())}")

    app = create_app()
    log.info("Bot is now polling Telegram...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
