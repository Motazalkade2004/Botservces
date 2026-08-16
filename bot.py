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
from telethon.tl.functions.messages import AddChatUserRequest, ImportChatInviteRequest, GetDialogsRequest
from telethon.tl.functions.channels import InviteToChannelRequest, GetParticipantsRequest
from telethon.tl.types import InputPeerUser, ChannelParticipantsSearch, MessageEntityMentionName
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, ChatAdminRequiredError

# ==================== الإعدادات ====================
API_ID = 35983238
API_HASH = "daf2ef391f5d9017043b33f4d1f84052"
BOT_TOKEN = "8910377047:AAE8UoZYKXoCRrGKmN8uDLbsDIYsu9dHzZ4"
ADMIN_ID = 5517628630
ADMIN_USERNAME = "Motazalkade"

# إعدادات البوت
MAX_INVITES_PER_DAY = 50
DELAY_BETWEEN_INVITES = 5
MAX_RETRIES = 3

# متغيرات عامة
INVITED_COUNT = 0
INVITED_IDS = set()
is_running = False

# ==================== إعدادات التسجيل ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== العميل ====================
client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# ==================== قاعدة البيانات ====================
class Database:
    def __init__(self):
        self.db_path = 'members.db'
        self.init_db()
    
    def init_db(self):
        """تهيئة قاعدة البيانات"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # جدول الأعضاء المدعوين
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
            
            # جدول سجل الدعوات
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
            
            # جدول المجموعات المحفوظة
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
            
            # جدول الإحصائيات اليومية
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE UNIQUE,
                    total_invited INTEGER DEFAULT 0,
                    total_failed INTEGER DEFAULT 0,
                    success_rate REAL DEFAULT 0
                )
            ''')
            
            conn.commit()
            logger.info("✅ قاعدة البيانات جاهزة")
    
    def add_invited_member(self, user_data: Dict):
        """إضافة عضو مدعو"""
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
    
    def is_member_invited(self, user_id: int) -> bool:
        """التحقق من أن العضو تمت دعوته سابقاً"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM invited_members WHERE user_id = ?', (user_id,))
            return cursor.fetchone() is not None
    
    def get_today_invites(self) -> int:
        """عدد الدعوات اليوم"""
        today = datetime.now().date().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM invite_log 
                WHERE date(invite_time) = ? AND success = 1
            ''', (today,))
            return cursor.fetchone()[0]
    
    def log_invite(self, user_id: int, source_group: str, target_group: str, success: bool, error: str = ''):
        """تسجيل عملية دعوة"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO invite_log (user_id, source_group, target_group, invite_time, success, error_message)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, source_group, target_group, datetime.now().isoformat(), success, error))
            conn.commit()
            
            # تحديث الإحصائيات اليومية
            today = datetime.now().date().isoformat()
            cursor.execute('''
                INSERT INTO daily_stats (date, total_invited, total_failed)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_invited = total_invited + ?,
                    total_failed = total_failed + ?
            ''', (today, 1 if success else 0, 0 if success else 1, 1 if success else 0, 0 if success else 1))
            conn.commit()
    
    def get_stats(self) -> Dict:
        """الحصول على الإحصائيات"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM invited_members')
            total_invited = cursor.fetchone()[0]
            
            today = datetime.now().date().isoformat()
            cursor.execute('''
                SELECT total_invited, total_failed FROM daily_stats WHERE date = ?
            ''', (today,))
            row = cursor.fetchone()
            
            if row:
                today_invited = row[0]
                today_failed = row[1]
            else:
                today_invited = 0
                today_failed = 0
            
            return {
                'total_invited': total_invited,
                'today_invited': today_invited,
                'today_failed': today_failed,
                'remaining_today': MAX_INVITES_PER_DAY - today_invited
            }
    
    def get_source_groups(self) -> List[Dict]:
        """الحصول على المجموعات المصدر المحفوظة"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT group_name, group_id FROM saved_groups WHERE is_source = 1
            ''')
            return [{'name': row[0], 'id': row[1]} for row in cursor.fetchall()]
    
    def get_target_groups(self) -> List[Dict]:
        """الحصول على المجموعات الهدف المحفوظة"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT group_name, group_id FROM saved_groups WHERE is_target = 1
            ''')
            return [{'name': row[0], 'id': row[1]} for row in cursor.fetchall()]

db = Database()

# ==================== وظائف البوت الأساسية ====================

async def get_members_from_group(group_username: str, limit: int = 100) -> List[Dict]:
    """
    جلب أعضاء من مجموعة عامة (حتى لو لست مشرفاً)
    """
    members = []
    
    try:
        logger.info(f"📥 جلب أعضاء من {group_username}")
        
        # محاولة الحصول على المجموعة
        try:
            group = await client.get_entity(group_username)
        except:
            # محاولة كرابط دعوة
            if 't.me/' in group_username:
                hash_code = group_username.split('/')[-1]
                try:
                    await client(ImportChatInviteRequest(hash_code))
                    group = await client.get_entity(hash_code)
                    logger.info("✅ تم الانضمام للمجموعة مؤقتاً")
                except:
                    logger.error(f"❌ لا يمكن الوصول للمجموعة {group_username}")
                    return []
            else:
                logger.error(f"❌ معرف مجموعة غير صحيح: {group_username}")
                return []
        
        # الطريقة 1: محاولة جلب مباشر
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
                    
                    if len(members) % 20 == 0:
                        logger.info(f"⏳ تم جلب {len(members)} عضو...")
                        
            if members:
                logger.info(f"✅ تم جلب {len(members)} عضو بنجاح")
                return members
                
        except Exception as e:
            logger.warning(f"⚠️ الطريقة المباشرة فشلت: {e}")
        
        # الطريقة 2: جلب من رسائل المجموعة
        logger.info("🔄 محاولة الطريقة البديلة (من الرسائل)...")
        try:
            async for message in client.iter_messages(group, limit=limit * 2):
                if message.sender_id:
                    try:
                        user = await client.get_entity(message.sender_id)
                        if not user.bot and user.id:
                            if not any(m['id'] == user.id for m in members):
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
            
            logger.info(f"✅ تم جلب {len(members)} عضو من الرسائل")
            return members
            
        except Exception as e:
            logger.error(f"❌ فشلت جميع الطرق: {e}")
            return []
            
    except Exception as e:
        logger.error(f"❌ خطأ عام: {e}")
        return []

async def invite_to_group(target_group: str, user_data: Dict) -> bool:
    """
    دعوة عضو إلى المجموعة (أنت مشرف فيها)
    """
    try:
        # الحصول على المجموعة الهدف
        target = await client.get_entity(target_group)
        
        # إنشاء كائن المستخدم
        user = InputPeerUser(user_data['id'], user_data.get('access_hash', 0))
        
        # إضافة المستخدم
        if hasattr(target, 'id'):
            await client(AddChatUserRequest(
                chat_id=target.id,
                user_id=user,
                fwd_limit=0
            ))
        else:
            await client(InviteToChannelRequest(
                channel=target,
                users=[user_data['id']]
            ))
        
        # تسجيل في قاعدة البيانات
        user_data['target_group'] = target_group
        db.add_invited_member(user_data)
        db.log_invite(user_data['id'], user_data.get('source_group', ''), target_group, True)
        
        logger.info(f"✅ تم دعوة {user_data.get('first_name', 'مستخدم')}")
        return True
        
    except FloodWaitError as e:
        wait_time = e.seconds
        logger.warning(f"⏳ تم الحظر {wait_time} ثانية")
        await asyncio.sleep(wait_time + 2)
        return False
        
    except UserPrivacyRestrictedError:
        logger.warning(f"🔒 {user_data.get('first_name', 'مستخدم')} يمنع الدعوات")
        db.log_invite(user_data['id'], user_data.get('source_group', ''), target_group, False, 'Privacy restricted')
        return False
        
    except Exception as e:
        error_msg = str(e)
        if "USER_ALREADY_PARTICIPANT" in error_msg:
            logger.info(f"ℹ️ {user_data.get('first_name', 'مستخدم')} موجود بالفعل")
            db.add_invited_member(user_data)
            return True
        else:
            logger.error(f"❌ فشل دعوة {user_data.get('first_name', 'مستخدم')}: {e}")
            db.log_invite(user_data['id'], user_data.get('source_group', ''), target_group, False, error_msg)
            return False

# ==================== أوامر البوت ====================

@client.on(events.NewMessage(pattern='/start'))
async def start_command(event):
    """أمر البدء"""
    await event.reply(f"""
🤖 **مرحباً بك في بوت نقل الأعضاء المتكامل!**

👤 **المالك:** @{ADMIN_USERNAME}

📌 **الأوامر المتاحة:**

🎯 **الأوامر الرئيسية:**
/copy @source @target - نقل الأعضاء
/copy_bulk @source1,@source2 @target - نقل من عدة مصادر

📊 **الإحصائيات:**
/stats - عرض الإحصائيات
/today - إحصائيات اليوم

📁 **التقارير:**
/export - تصدير تقرير CSV
/export_all - تصدير جميع البيانات

📋 **إدارة المجموعات:**
/save_source @group - حفظ مجموعة كمصدر
/save_target @group - حفظ مجموعة كهدف
/my_sources - عرض المصادر المحفوظة
/my_targets - عرض الأهداف المحفوظة

🔄 **جدولة:**
/schedule 14:30 - جدولة عملية
/stop_schedule - إيقاف الجدولة

ℹ️ **معلومات:**
/status - حالة البوت
/help - المساعدة

⚙️ **الإعدادات:**
/set_limit 50 - تغيير الحد اليومي
/set_delay 5 - تغيير التأخير بين الدعوات

⚠️ **تنبيه:** استخدم البوت بمسؤولية
    """)

@client.on(events.NewMessage(pattern='/copy (.*?) (.*?)'))
async def copy_members_command(event):
    """نسخ الأعضاء من مجموعة إلى أخرى"""
    global INVITED_COUNT, INVITED_IDS, is_running
    
    if is_running:
        await event.reply("⏳ هناك عملية جارية حالياً، انتظر حتى تكتمل")
        return
    
    source = event.pattern_match.group(1).strip()
    target = event.pattern_match.group(2).strip()
    
    # رسالة بدء
    msg = await event.reply(f"⏳ جاري البدء...\n📥 المصدر: {source}\n📤 الهدف: {target}")
    
    try:
        is_running = True
        
        # الحصول على المجموعة المصدر
        try:
            source_entity = await client.get_entity(source)
            source_title = source_entity.title
        except:
            await msg.edit(f"❌ لا يمكن الوصول للمجموعة المصدر: {source}")
            is_running = False
            return
        
        # الحصول على المجموعة الهدف
        try:
            target_entity = await client.get_entity(target)
            target_title = target_entity.title
        except:
            await msg.edit(f"❌ لا يمكن الوصول للمجموعة الهدف: {target}")
            is_running = False
            return
        
        await msg.edit(f"✅ تم الوصول للمجموعتين\n📥 {source_title}\n📤 {target_title}\n\n⏳ جاري جلب الأعضاء...")
        
        # جلب الأعضاء من المصدر
        members = await get_members_from_group(source, 100)
        
        if not members:
            await msg.edit("❌ لم يتم جلب أي عضو من المصدر")
            is_running = False
            return
        
        await msg.edit(f"✅ تم جلب {len(members)} عضو\n📨 بدء الدعوات...")
        
        # فلترة الأعضاء (إزالة المدعوين سابقاً)
        new_members = [
            m for m in members 
            if m['id'] not in INVITED_IDS 
            and not db.is_member_invited(m['id'])
        ]
        
        if not new_members:
            await msg.edit("ℹ️ جميع الأعضاء تمت دعوتهم سابقاً")
            is_running = False
            return
        
        # دعوة الأعضاء
        success_count = 0
        failed_count = 0
        
        for i, member in enumerate(new_members[:MAX_INVITES_PER_DAY], 1):
            # التحقق من الحد اليومي
            if db.get_today_invites() >= MAX_INVITES_PER_DAY:
                await msg.edit(f"⛔ وصلت للحد اليومي ({MAX_INVITES_PER_DAY})")
                break
            
            # دعوة العضو
            success = await invite_to_group(target, member)
            
            if success:
                success_count += 1
                INVITED_IDS.add(member['id'])
            else:
                failed_count += 1
            
            # تحديث التقدم
            if i % 5 == 0:
                await msg.edit(f"⏳ التقدم: {i}/{len(new_members[:MAX_INVITES_PER_DAY])}\n✅ نجح: {success_count}\n❌ فشل: {failed_count}")
            
            # تأخير عشوائي
            delay = DELAY_BETWEEN_INVITES + random.uniform(0, 2)
            await asyncio.sleep(delay)
        
        # التقرير النهائي
        await msg.edit(f"""
✅ **اكتملت العملية!**

📊 **النتائج:**
- 📥 المصدر: {source_title}
- 📤 الهدف: {target_title}
- 👥 تم جلب: {len(members)} عضو
- ✅ تمت الدعوة: {success_count} عضو
- ❌ فشل: {failed_count} عضو
- 📅 الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 📊 المتبقي اليوم: {MAX_INVITES_PER_DAY - db.get_today_invites()}

📁 **للحصول على تقرير:** /export
        """)
        
    except Exception as e:
        await msg.edit(f"❌ خطأ: {str(e)}")
    
    finally:
        is_running = False

@client.on(events.NewMessage(pattern='/stats'))
async def stats_command(event):
    """عرض الإحصائيات"""
    stats = db.get_stats()
    
    # إحصائيات إضافية
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        
        # أفضل المصادر
        cursor.execute('''
            SELECT source_group, COUNT(*) as count 
            FROM invited_members 
            WHERE source_group != ''
            GROUP BY source_group 
            ORDER BY count DESC 
            LIMIT 5
        ''')
        top_sources = cursor.fetchall()
        
        # إجمالي المحاولات
        cursor.execute('SELECT COUNT(*) FROM invite_log')
        total_attempts = cursor.fetchone()[0]
        
        # نسبة النجاح
        cursor.execute('''
            SELECT 
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success,
                COUNT(*) as total
            FROM invite_log
        ''')
        row = cursor.fetchone()
        success_rate = (row[0] / row[1] * 100) if row and row[1] > 0 else 0
    
    response = f"""
📊 **إحصائيات البوت**

👥 **الإجمالي:**
- إجمالي المدعوين: {stats['total_invited']}
- مدعوي اليوم: {stats['today_invited']}
- فشل اليوم: {stats['today_failed']}
- المتبقي اليوم: {stats['remaining_today']}

📈 **الأداء:**
- نسبة النجاح: {success_rate:.1f}%
- إجمالي المحاولات: {total_attempts}

📌 **أفضل المصادر:**
"""
    if top_sources:
        for source, count in top_sources:
            response += f"- {source}: {count} عضو\n"
    else:
        response += "- لا توجد بيانات\n"
    
    response += f"""
⚙️ **الإعدادات:**
- الحد اليومي: {MAX_INVITES_PER_DAY}
- التأخير: {DELAY_BETWEEN_INVITES} ثانية
- البوت: {'قيد التشغيل ✅' if not is_running else 'جاري العمل ⏳'}
    """
    
    await event.reply(response)

@client.on(events.NewMessage(pattern='/today'))
async def today_stats_command(event):
    """إحصائيات اليوم"""
    stats = db.get_stats()
    
    response = f"""
📅 **إحصائيات اليوم ({datetime.now().strftime('%Y-%m-%d')})**

✅ **النجاح:** {stats['today_invited']} دعوة
❌ **الفشل:** {stats['today_failed']} دعوة
📊 **المتبقي:** {stats['remaining_today']} من {MAX_INVITES_PER_DAY}
📈 **نسبة النجاح:** {stats['today_invited'] / (stats['today_invited'] + stats['today_failed'] + 0.001) * 100:.1f}%
    """
    
    await event.reply(response)

@client.on(events.NewMessage(pattern='/export'))
async def export_command(event):
    """تصدير تقرير CSV"""
    await event.reply("⏳ جاري إنشاء التقرير...")
    
    try:
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, invited_date, source_group, target_group, status
                FROM invited_members
                ORDER BY invited_date DESC
            ''')
            data = cursor.fetchall()
        
        if not data:
            await event.reply("ℹ️ لا توجد بيانات للتصدير")
            return
        
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['User ID', 'Username', 'First Name', 'Last Name', 'Invited Date', 'Source', 'Target', 'Status'])
            writer.writerows(data)
        
        await client.send_file(event.chat_id, filename, caption=f"📊 تقرير الأعضاء المدعوين ({len(data)} عضو)")
        os.remove(filename)
        
    except Exception as e:
        await event.reply(f"❌ خطأ في التصدير: {str(e)}")

@client.on(events.NewMessage(pattern='/export_all'))
async def export_all_command(event):
    """تصدير جميع البيانات"""
    await event.reply("⏳ جاري تصدير جميع البيانات...")
    
    try:
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM invited_members')
            members = cursor.fetchall()
            
            cursor.execute('SELECT * FROM invite_log')
            logs = cursor.fetchall()
        
        # إنشاء ملفات CSV
        members_file = f"members_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(members_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['User ID', 'Username', 'First Name', 'Last Name', 'Invited Date', 'Source', 'Target', 'Status'])
            writer.writerows(members)
        
        logs_file = f"logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(logs_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['ID', 'User ID', 'Source', 'Target', 'Time', 'Success', 'Error'])
            writer.writerows(logs)
        
        await client.send_file(event.chat_id, members_file, caption=f"📊 الأعضاء المدعوين ({len(members)} عضو)")
        await client.send_file(event.chat_id, logs_file, caption=f"📋 سجل الدعوات ({len(logs)} سجل)")
        
        os.remove(members_file)
        os.remove(logs_file)
        
        await event.reply("✅ تم تصدير جميع البيانات بنجاح")
        
    except Exception as e:
        await event.reply(f"❌ خطأ في التصدير: {str(e)}")

@client.on(events.NewMessage(pattern='/save_source (.*)'))
async def save_source_command(event):
    """حفظ مجموعة كمصدر"""
    group = event.pattern_match.group(1).strip()
    
    try:
        entity = await client.get_entity(group)
        
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO saved_groups (group_name, group_id, group_type, is_source, is_target, last_used)
                VALUES (?, ?, ?, 1, 0, ?)
            ''', (entity.title, entity.id, 'source', datetime.now().isoformat()))
            conn.commit()
        
        await event.reply(f"✅ تم حفظ {entity.title} كمصدر")
        
    except Exception as e:
        await event.reply(f"❌ خطأ: {str(e)}")

@client.on(events.NewMessage(pattern='/save_target (.*)'))
async def save_target_command(event):
    """حفظ مجموعة كهدف"""
    group = event.pattern_match.group(1).strip()
    
    try:
        entity = await client.get_entity(group)
        
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO saved_groups (group_name, group_id, group_type, is_source, is_target, last_used)
                VALUES (?, ?, ?, 0, 1, ?)
            ''', (entity.title, entity.id, 'target', datetime.now().isoformat()))
            conn.commit()
        
        await event.reply(f"✅ تم حفظ {entity.title} كهدف")
        
    except Exception as e:
        await event.reply(f"❌ خطأ: {str(e)}")

@client.on(events.NewMessage(pattern='/my_sources'))
async def my_sources_command(event):
    """عرض المصادر المحفوظة"""
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT group_name, group_id, last_used FROM saved_groups WHERE is_source = 1')
        sources = cursor.fetchall()
    
    if not sources:
        await event.reply("❌ لا توجد مصادر محفوظة")
        return
    
    response = "📥 **المصادر المحفوظة:**\n\n"
    for i, source in enumerate(sources, 1):
        response += f"{i}. 📌 {source[0]} (ID: {source[1]})\n"
        response += f"   آخر استخدام: {source[2]}\n\n"
    
    await event.reply(response)

@client.on(events.NewMessage(pattern='/my_targets'))
async def my_targets_command(event):
    """عرض الأهداف المحفوظة"""
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT group_name, group_id, last_used FROM saved_groups WHERE is_target = 1')
        targets = cursor.fetchall()
    
    if not targets:
        await event.reply("❌ لا توجد أهداف محفوظة")
        return
    
    response = "📤 **الأهداف المحفوظة:**\n\n"
    for i, target in enumerate(targets, 1):
        response += f"{i}. 📌 {target[0]} (ID: {target[1]})\n"
        response += f"   آخر استخدام: {target[2]}\n\n"
    
    await event.reply(response)

@client.on(events.NewMessage(pattern='/set_limit (\\d+)'))
async def set_limit_command(event):
    """تغيير الحد اليومي"""
    global MAX_INVITES_PER_DAY
    new_limit = int(event.pattern_match.group(1))
    
    if 1 <= new_limit <= 500:
        MAX_INVITES_PER_DAY = new_limit
        await event.reply(f"✅ تم تغيير الحد اليومي إلى {new_limit} دعوة")
    else:
        await event.reply("❌ الحد يجب أن يكون بين 1 و 500")

@client.on(events.NewMessage(pattern='/set_delay (\\d+)'))
async def set_delay_command(event):
    """تغيير التأخير"""
    global DELAY_BETWEEN_INVITES
    new_delay = int(event.pattern_match.group(1))
    
    if 1 <= new_delay <= 30:
        DELAY_BETWEEN_INVITES = new_delay
        await event.reply(f"✅ تم تغيير التأخير إلى {new_delay} ثانية")
    else:
        await event.reply("❌ التأخير يجب أن يكون بين 1 و 30 ثانية")

@client.on(events.NewMessage(pattern='/status'))
async def status_command(event):
    """حالة البوت"""
    stats = db.get_stats()
    
    # قراءة حالة الجدولة
    try:
        with open('schedule.json', 'r') as f:
            schedule = json.load(f)
            schedule_status = "مفعلة" if schedule.get('enabled', False) else "معطلة"
            schedule_time = schedule.get('time', 'غير محدد')
    except:
        schedule_status = "معطلة"
        schedule_time = "غير محدد"
    
    # حساب وقت التشغيل
    try:
        with open('start_time.txt', 'r') as f:
            start_time = datetime.fromisoformat(f.read())
            uptime = datetime.now() - start_time
            uptime_str = str(uptime).split('.')[0]
    except:
        uptime_str = "غير معروف"
    
    response = f"""
📊 **حالة البوت**

✅ **الحالة:** {'يعمل' if not is_running else 'جاري العمل'}
📅 **التاريخ:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏱️ **وقت التشغيل:** {uptime_str}

👥 **الإحصائيات:**
- إجمالي المدعوين: {stats['total_invited']}
- اليوم: {stats['today_invited']}/{MAX_INVITES_PER_DAY}

⚙️ **الإعدادات:**
- الحد اليومي: {MAX_INVITES_PER_DAY}
- التأخير: {DELAY_BETWEEN_INVITES} ثانية

⏰ **الجدولة:**
- الحالة: {schedule_status}
- الوقت: {schedule_time}

👤 **المالك:** @{ADMIN_USERNAME}
📁 **قاعدة البيانات:** {db.db_path}
    """
    
    await event.reply(response)

@client.on(events.NewMessage(pattern='/help'))
async def help_command(event):
    """المساعدة"""
    await event.reply("""
📖 **دليل استخدام البوت**

🎯 **الأوامر الرئيسية:**

1. **نقل الأعضاء:**
   `/copy @source @target`
   مثال: `/copy @tech_group @my_group`

2. **نقل من عدة مصادر:**
   `/copy_bulk @source1,@source2 @target`

3. **الإحصائيات:**
   `/stats` - إحصائيات عامة
   `/today` - إحصائيات اليوم

4. **التقارير:**
   `/export` - تصدير تقرير CSV
   `/export_all` - تصدير جميع البيانات

5. **إدارة المجموعات:**
   `/save_source @group` - حفظ كمصدر
   `/save_target @group` - حفظ كهدف
   `/my_sources` - عرض المصادر
   `/my_targets` - عرض الأهداف

6. **الجدولة:**
   `/schedule 14:30` - جدولة العملية
   `/stop_schedule` - إيقاف الجدولة

7. **الإعدادات:**
   `/set_limit 50` - تغيير الحد اليومي
   `/set_delay 5` - تغيير التأخير

8. **معلومات:**
   `/status` - حالة البوت
   `/help` - هذه المساعدة

⚠️ **نصائح مهمة:**
- ابدأ بـ 20-30 دعوة يومياً
- استخدم تأخيراً 5-7 ثواني
- تأكد من أن المجموعة الهدف تسمح بالدعوات
- المصدر يجب أن يكون مجموعة عامة

📞 **التواصل:** @{ADMIN_USERNAME}
    """)

# ==================== الجدولة التلقائية ====================
async def run_scheduler():
    """تشغيل الجدولة"""
    logger.info("🔄 بدء تشغيل المجدول...")
    
    while True:
        try:
            # قراءة الجدولة
            try:
                with open('schedule.json', 'r') as f:
                    schedule = json.load(f)
                    if not schedule.get('enabled', False):
                        await asyncio.sleep(60)
                        continue
                    schedule_time = schedule.get('time', '')
            except:
                await asyncio.sleep(60)
                continue
            
            if not schedule_time:
                await asyncio.sleep(60)
                continue
            
            # التحقق من الوقت
            now = datetime.now().strftime('%H:%M')
            
            if now == schedule_time:
                logger.info(f"⏰ تنفيذ الجدولة في {schedule_time}")
                
                # جلب المصادر المحفوظة
                sources = db.get_source_groups()
                targets = db.get_target_groups()
                
                if sources and targets:
                    for source in sources[:3]:  # حد أقصى 3 مصادر
                        for target in targets[:1]:  # أول هدف
                            logger.info(f"📥 جدولة: {source['name']} → {target['name']}")
                            
                            # محاكاة الأمر
                            await client.send_message(
                                ADMIN_ID,
                                f"/copy @{source['name']} @{target['name']}"
                            )
                            
                            await asyncio.sleep(5)
                else:
                    await client.send_message(
                        ADMIN_ID,
                        "⚠️ لا توجد مجموعات محفوظة للجدولة\nقم بحفظ مصدر وهدف أولاً"
                    )
                
                # انتظار حتى لا يتكرر التنفيذ
                await asyncio.sleep(60)
            
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"❌ خطأ في المجدول: {e}")
            await asyncio.sleep(60)

# ==================== التشغيل ====================
async def main():
    """تشغيل البوت"""
    logger.info("🚀 بدء تشغيل البوت...")
    logger.info(f"👤 المالك: @{ADMIN_USERNAME}")
    logger.info(f"🆔 المعرف: {ADMIN_ID}")
    
    # حفظ وقت البدء
    with open('start_time.txt', 'w') as f:
        f.write(datetime.now().isoformat())
    
    # بدء الجدولة
    asyncio.create_task(run_scheduler())
    
    # تشغيل البوت
    await client.start()
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ تم إيقاف البوت")
    except Exception as e:
        logger.error(f"❌ خطأ: {e}")
