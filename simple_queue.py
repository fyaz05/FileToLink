# Thunder/utils/simple_queue.py

import asyncio
import time

class SimpleQueueManager:
    def __init__(
        self,
        bot,  # pyrogram.Client instance
        max_requests_per_minute=5,
        max_queue_size=100
    ):
        self.bot = bot
        self.max_requests_per_minute = max_requests_per_minute
        self.max_queue_size = max_queue_size
        self.user_timestamps = {}     # user_id: [timestamps]
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.queue_messages = {}      # user_id: message object
        self.processing = False

    def is_rate_limited(self, user_id):
        now = time.time()
        recent = [t for t in self.user_timestamps.get(user_id, []) if now - t < 60]
        if len(recent) >= self.max_requests_per_minute:
            return True
        return False

    def record_request(self, user_id):
        now = time.time()
        if user_id not in self.user_timestamps:
            self.user_timestamps[user_id] = []
        self.user_timestamps[user_id].append(now)
        self.user_timestamps[user_id] = [t for t in self.user_timestamps[user_id] if now - t < 60]

    async def add_request(self, user_id, message, request_data):
        """
        message: pyrogram.Message object (for reply/edit)
        request_data: dict (any data you want to process)
        """
        if self.is_rate_limited(user_id):
            try:
                self.queue.put_nowait((user_id, message, request_data))
                await self.send_or_update_queue_message(user_id, message)
                return "queued"
            except asyncio.QueueFull:
                await self.send_queue_full_message(message)
                return "queue_full"
        else:
            self.record_request(user_id)
            await self.process_request(user_id, message, request_data)
            return "processed"

    async def send_or_update_queue_message(self, user_id, message):
        pos = self.find_user_position(user_id)
        total = self.queue.qsize()
        text = (
            f"⏳ Your position in the queue: `{pos}/{total}`\n"
            f"This message updates every few seconds until your request is processed."
        )
        try:
            if user_id in self.queue_messages:
                prev_msg = self.queue_messages[user_id]
                await self.bot.edit_message_text(chat_id=prev_msg.chat.id, message_id=prev_msg.id, text=text)
            else:
                sent_msg = await self.bot.send_message(chat_id=message.chat.id, text=text, reply_to_message_id=message.id)
                self.queue_messages[user_id] = sent_msg
        except Exception:
            sent_msg = await self.bot.send_message(chat_id=message.chat.id, text=text, reply_to_message_id=message.id)
            self.queue_messages[user_id] = sent_msg

    async def send_queue_full_message(self, message):
        text = "⚠️ The processing queue is full. Please try again later."
        await self.bot.send_message(chat_id=message.chat.id, text=text, reply_to_message_id=message.id)

    async def delete_queue_message(self, user_id):
        msg = self.queue_messages.pop(user_id, None)
        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

    def find_user_position(self, user_id):
        items = list(self.queue._queue)
        for idx, item in enumerate(items):
            if item[0] == user_id:
                return idx + 1
        return len(items)

    async def update_queue_positions(self):
        while True:
            try:
                items = list(self.queue._queue)
                for idx, (user_id, message, _) in enumerate(items):
                    await self.send_or_update_queue_message(user_id, message)
                await asyncio.sleep(5)
            except Exception:
                await asyncio.sleep(5)

    async def process_queue(self):
        self.processing = True
        while True:
            try:
                user_id, message, request_data = await self.queue.get()
                while self.is_rate_limited(user_id):
                    await asyncio.sleep(5)
                self.record_request(user_id)
                await self.delete_queue_message(user_id)
                await self.bot.send_message(chat_id=message.chat.id, text="🚀 Your request is now being processed.", reply_to_message_id=message.id)
                await self.process_request(user_id, message, request_data)
                self.queue.task_done()
            except Exception as e:
                print(f"Queue error: {e}")

    async def process_request(self, user_id, message, data):
        # Write your main request processing code here (e.g., send file, stream, etc.)
        # For test purposes, we just simulate a delay:
        print(f"Processing for user {user_id}: {data}")
        await asyncio.sleep(2)

    def start_worker(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.process_queue())
        loop.create_task(self.update_queue_positions())

# Usage Example:
# from Thunder.utils.simple_queue import SimpleQueueManager
# queue_manager = SimpleQueueManager(bot, max_requests_per_minute=5, max_queue_size=100)
# queue_manager.start_worker()
# await queue_manager.add_request(user_id, message, {"file": "your_file.xyz"})
