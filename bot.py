# bot.py
import asyncio
import csv
import json
import sqlite3
import random
import time
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import re

from telethon import TelegramClient, events
from telethon.tl.functions.messages import AddChatUserRequest, ImportChatInviteRequest
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.types import InputPeerUser
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError

# ==================== الإعدادات ====================
API_ID = 35983238
API_HASH = "daf2ef391f5d9017043b33f4d1f84052"
BOT_TOKEN = "8910377047:AAE8UoZYKXoCRrGKmN8uDLbsDIYsu9dHzZ4"
ADMIN_ID = 5517628630
ADMIN_USERNAME = "Motazalkade"

MAX_INVITES_PER_DAY = 50
DELAY_BETWEEN_INVITES = 5
INVITED_IDS = set()
is_running = False

# ==================== إعدادات التسجيل ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== العميل ====================
client = TelegramClient('bot_session', API_ID, API_HASH)

# ==================== قاعدة البيانات ====================
class Database:
    def __init__(self):
        self.db_path = 'members.db'
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS invited_members (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    invited_date TIMESTAMP,
                    source_group TEXT,
                    target_group TEXT,
                    status TEXT DEFAULT 'active'
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS invite_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    source_group TEXT,
                    target_group TEXT,
                    invite_time TIMESTAMP,
                    success BOOLEAN,
                    error_message TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS saved_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_name TEXT,
                    group_id INTEGER,
                    group_type TEXT,
                    is_source BOOLEAN DEFAULT 0,
                    is_target BOOLEAN DEFAULT 0,
                    last_used TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE UNIQUE,
                    total_invited INTEGER DEFAULT 0,
                    total_failed INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
            logger.info("✅ قاعدة البيانات جاهزة")
    
    def add_invited_member(self, user_data):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO invited_members 
                (user_id, username, first_name, last_name, invited_date, source_group, target_group, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_data['id'],
                user_data.get('username', ''),
                user_data.get('first_name', ''),
                user_data.get('last_name', ''),
                datetime.now().isoformat(),
                user_data.get('source_group', ''),
                user_data.get('target_group', ''),
                'active'
            ))
            conn.commit()
    
    def is_member_invited(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM invited_members WHERE user_id = ?', (user_id,))
            return cursor.fetchone() is not None
    
    def get_today_invites(self):
        today = datetime.now().date().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM invite_log WHERE date(invite_time) = ? AND success = 1', (today,))
            return cursor.fetchone()[0]
    
    def log_invite(self, user_id, source_group, target_group, success, error=''):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO invite_log (user_id, source_group, target_group, invite_time, success, error_message)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, source_group, target_group, datetime.now().isoformat(), success, error))
            conn.commit()
    
    def get_stats(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM invited_members')
            total_invited = cursor.fetchone()[0]
            
            today = datetime.now().date().isoformat()
            cursor.execute('SELECT total_invited, total_failed FROM daily_stats WHERE date = ?', (today,))
            row = cursor.fetchone()
            
            return {
                'total_invited': total_invited,
                'today_invited': row[0] if row else 0,
                'today_failed': row[1] if row else 0,
                'remaining_today': MAX_INVITES_PER_DAY - (row[0] if row else 0)
            }

db = Database()

# ==================== وظائف البوت ====================
async def get_members_from_group(group_username, limit=100):
    members = []
    try:
        logger.info(f"📥 جلب أعضاء من {group_username}")
        try:
            group = await client.get_entity(group_username)
        except:
            if 't.me/' in group_username:
                hash_code = group_username.split('/')[-1]
                try:
                    await client(ImportChatInviteRequest(hash_code))
                    group = await client.get_entity(hash_code)
                except:
                    return []
            else:
                return []
        
        try:
            async for user in client.iter_participants(group, aggressive=True):
                if not user.bot and user.id:
                    members.append({
                        'id': user.id,
                        'username': user.username or '',
                        'first_name': user.first_name or 'مستخدم',
                        'last_name': user.last_name or '',
                        'access_hash': user.access_hash,
                        'source_group': group_username
                    })
                    if len(members) >= limit:
                        break
            return members
        except:
            try:
                async for message in client.iter_messages(group, limit=limit * 2):
                    if message.sender_id:
                        try:
                            user = await client.get_entity(message.sender_id)
                            if not user.bot and user.id and not any(m['id'] == user.id for m in members):
                                members.append({
                                    'id': user.id,
                                    'username': user.username or '',
                                    'first_name': user.first_name or 'مستخدم',
                                    'last_name': user.last_name or '',
                                    'access_hash': user.access_hash,
                                    'source_group': group_username
                                })
                                if len(members) >= limit:
                                    break
                        except:
                            continue
                return members
            except:
                return []
    except:
        return []

async def invite_to_group(target_group, user_data):
    try:
        target = await client.get_entity(target_group)
        user = InputPeerUser(user_data['id'], user_data.get('access_hash', 0))
        
        if hasattr(target, 'id'):
            await client(AddChatUserRequest(chat_id=target.id, user_id=user, fwd_limit=0))
        else:
            await client(InviteToChannelRequest(channel=target, users=[user_data['id']]))
        
        user_data['target_group'] = target_group
        db.add_invited_member(user_data)
        db.log_invite(user_data['id'], user_data.get('source_group', ''), target_group, True)
        logger.info(f"✅ تم دعوة {user_data.get('first_name', 'مستخدم')}")
        return True
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 2)
        return False
    except Exception as e:
        if "USER_ALREADY_PARTICIPANT" in str(e):
            db.add_invited_member(user_data)
            return True
        db.log_invite(user_data['id'], user_data.get('source_group', ''), target_group, False, str(e))
        return False

# ==================== أوامر البوت ====================
@client.on(events.NewMessage(pattern='/start'))
async def start_command(event):
    await event.reply(f"""
🤖 **بوت نقل الأعضاء**
👤 المالك: @{ADMIN_USERNAME}

📌 **الأوامر:**
/copy @source @target - نقل الأعضاء
/stats - الإحصائيات
/export - تصدير تقرير
/help - المساعدة
    """)

@client.on(events.NewMessage(pattern='/copy (.*?) (.*?)'))
async def copy_command(event):
    global is_running
    if is_running:
        await event.reply("⏳ عملية جارية...")
        return
    
    source = event.pattern_match.group(1).strip()
    target = event.pattern_match.group(2).strip()
    msg = await event.reply(f"⏳ جاري العمل...\n📥 {source}\n📤 {target}")
    
    try:
        is_running = True
        members = await get_members_from_group(source, 50)
        
        if not members:
            await msg.edit("❌ لم يتم جلب أعضاء")
            is_running = False
            return
        
        await msg.edit(f"✅ تم جلب {len(members)} عضو\n📨 بدء الدعوات...")
        
        success = 0
        for i, member in enumerate(members[:MAX_INVITES_PER_DAY], 1):
            if db.get_today_invites() >= MAX_INVITES_PER_DAY:
                await msg.edit(f"⛔ الحد اليومي {MAX_INVITES_PER_DAY}")
                break
            if await invite_to_group(target, member):
                success += 1
            await asyncio.sleep(DELAY_BETWEEN_INVITES + random.uniform(0, 2))
        
        await msg.edit(f"""
✅ **اكتمل!**
📥 {source} → 📤 {target}
✅ نجح: {success}
❌ فشل: {len(members[:MAX_INVITES_PER_DAY]) - success}
📊 المتبقي: {MAX_INVITES_PER_DAY - db.get_today_invites()}
        """)
    except Exception as e:
        await msg.edit(f"❌ خطأ: {str(e)}")
    finally:
        is_running = False

@client.on(events.NewMessage(pattern='/stats'))
async def stats_command(event):
    stats = db.get_stats()
    await event.reply(f"""
📊 **الإحصائيات**
👥 إجمالي: {stats['total_invited']}
📅 اليوم: {stats['today_invited']}
📊 المتبقي: {stats['remaining_today']}
    """)

@client.on(events.NewMessage(pattern='/export'))
async def export_command(event):
    await event.reply("⏳ جاري التصدير...")
    try:
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM invited_members ORDER BY invited_date DESC')
            data = cursor.fetchall()
        
        if not data:
            await event.reply("ℹ️ لا توجد بيانات")
            return
        
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['User ID', 'Username', 'First Name', 'Last Name', 'Date', 'Source', 'Target'])
            writer.writerows(data)
        
        await client.send_file(event.chat_id, filename)
        os.remove(filename)
    except Exception as e:
        await event.reply(f"❌ خطأ: {str(e)}")

@client.on(events.NewMessage(pattern='/help'))
async def help_command(event):
    await event.reply("""
📖 **المساعدة**
/copy @source @target - نقل الأعضاء
/stats - الإحصائيات
/export - تصدير تقرير
/status - حالة البوت

⚠️ استخدم بمسؤولية
    """)

@client.on(events.NewMessage(pattern='/status'))
async def status_command(event):
    stats = db.get_stats()
    await event.reply(f"""
✅ **البوت يعمل**
👤 المالك: @{ADMIN_USERNAME}
📅 اليوم: {stats['today_invited']}/{MAX_INVITES_PER_DAY}
📊 إجمالي: {stats['total_invited']}
    """)

# ==================== التشغيل ====================
async def main():
    await client.start(bot_token=BOT_TOKEN)
    logger.info("🚀 بدء التشغيل...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ تم الإيقاف")