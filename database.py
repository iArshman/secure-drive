"""
Database models and operations for Cloudyte
"""
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from typing import Optional, List, Dict
from cryptography.fernet import Fernet
import hashlib
import os
from config import MONGO_URI, DATABASE_NAME

class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DATABASE_NAME]
        
        # Collections
        self.users = self.db['users']
        self.accounts = self.db['drive_accounts']
        self.callback_data = self.db['callback_data']
        self.system_config = self.db['system_config']
        self.auth_users = self.db['auth_users']
    
    async def create_indexes(self):
        await self.users.create_index('user_id', unique=True)
        await self.accounts.create_index([('user_id', 1), ('email', 1)])
        # Fixed: Added user_id to match the main.py payload
        await self.callback_data.create_index([('user_id', 1), ('hash', 1)], unique=True)
        await self.callback_data.create_index('created_at', expireAfterSeconds=604800)
        await self.auth_users.create_index('username', unique=True)
        await self.auth_users.create_index([('telegram_id', 1), ('is_logged_in', 1)])

    async def get_or_create_encryption_key(self) -> bytes:
        config = await self.system_config.find_one({"_id": "master_key"})
        if config:
            return config['key']
        else:
            new_key = Fernet.generate_key()
            await self.system_config.insert_one({
                "_id": "master_key",
                "key": new_key,
                "created_at": datetime.now(timezone.utc)
            })
            return new_key
    
    async def register_user(self, telegram_id: int, username: str, password: str, full_name: str = None) -> dict:
        username_taken = await self.auth_users.find_one({'username': username})
        if username_taken:
            return {'success': False, 'error': 'username_taken'}
        
        await self.auth_users.update_many(
            {'telegram_id': telegram_id, 'is_logged_in': True},
            {'$set': {'is_logged_in': False}}
        )
        
        internal_id = int(hashlib.sha256(username.encode()).hexdigest(), 16) % (10 ** 15)
        
        user_data = {
            'telegram_id': telegram_id,
            'username': username,
            'password': password, # Storing plaintext password directly
            'full_name': full_name,
            'is_logged_in': True,
            'internal_user_id': internal_id,
            'created_at': datetime.now(timezone.utc),
            'last_login': datetime.now(timezone.utc)
        }
        
        await self.auth_users.insert_one(user_data)
        existing_user = await self.users.find_one({'user_id': internal_id})
        if not existing_user:
            await self.create_user(internal_id, username, full_name)
        
        return {'success': True, 'error': None, 'internal_user_id': internal_id}
    
    async def login_user(self, telegram_id: int, username: str, password: str) -> dict:
        user = await self.auth_users.find_one({'username': username})
        
        # Directly comparing plaintext passwords
        if user and user.get('password') == password:
            await self.auth_users.update_many(
                {'telegram_id': telegram_id, 'is_logged_in': True},
                {'$set': {'is_logged_in': False}}
            )
            await self.auth_users.update_one(
                {'_id': user['_id']},
                {'$set': {'telegram_id': telegram_id, 'is_logged_in': True, 'last_login': datetime.now(timezone.utc)}}
            )
            internal_id = user.get('internal_user_id') or (int(hashlib.sha256(username.encode()).hexdigest(), 16) % (10 ** 15))
            return {'success': True, 'internal_user_id': internal_id}
        
        return {'success': False, 'internal_user_id': None}
    
    async def logout_user(self, telegram_id: int) -> bool:
        result = await self.auth_users.update_many(
            {'telegram_id': telegram_id, 'is_logged_in': True},
            {'$set': {'is_logged_in': False}}
        )
        return result.modified_count > 0
    
    async def is_user_logged_in(self, telegram_id: int) -> bool:
        user = await self.auth_users.find_one({'telegram_id': telegram_id, 'is_logged_in': True})
        return user is not None
    
    async def get_internal_user_id(self, telegram_id: int) -> Optional[int]:
        auth_user = await self.auth_users.find_one({'telegram_id': telegram_id, 'is_logged_in': True})
        if auth_user:
            return auth_user.get('internal_user_id')
        return None
    
    async def get_user(self, user_id: int) -> Optional[Dict]:
        return await self.users.find_one({'user_id': user_id})
    
    async def create_user(self, user_id: int, username: str = None, full_name: str = None):
        user_data = {
            'user_id': user_id,
            'username': username,
            'full_name': full_name,
            'default_account_id': None,
            'backup_account_id': None,
            'backup_enabled': False,
            'encryption_enabled': False,
            'bot_decrypt_enabled': False,
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
        await self.users.insert_one(user_data)
        return user_data
    
    async def update_user(self, user_id: int, update_data: Dict):
        update_data['updated_at'] = datetime.now(timezone.utc)
        await self.users.update_one({'user_id': user_id}, {'$set': update_data})

    async def add_account(self, user_id: int, email: str, tokens: Dict) -> str:
        account_data = {
            'user_id': user_id,
            'email': email,
            'access_token': tokens['access_token'],
            'refresh_token': tokens.get('refresh_token'),
            'expires_at': tokens['expires_at'],
            'is_default': False,
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
        result = await self.accounts.insert_one(account_data)
        account_id = str(result.inserted_id)
        accounts_count = await self.accounts.count_documents({'user_id': user_id})
        if accounts_count == 1:
            await self.set_default_account(user_id, account_id)
        return account_id

    async def get_account(self, account_id: str) -> Optional[Dict]:
        from bson import ObjectId
        return await self.accounts.find_one({'_id': ObjectId(account_id)})

    async def get_user_accounts(self, user_id: int) -> List[Dict]:
        accounts = []
        async for account in self.accounts.find({'user_id': user_id}):
            account['account_id'] = str(account['_id'])
            accounts.append(account)
        return accounts

    async def set_default_account(self, user_id: int, account_id: str):
        from bson import ObjectId
        await self.accounts.update_many({'user_id': user_id}, {'$set': {'is_default': False}})
        await self.accounts.update_one({'_id': ObjectId(account_id)}, {'$set': {'is_default': True}})
        await self.update_user(user_id, {'default_account_id': account_id})

    async def set_backup_account(self, user_id: int, account_id: str):
        from bson import ObjectId
        await self.accounts.update_many({'user_id': user_id}, {'$set': {'is_backup': False}})
        await self.accounts.update_one({'_id': ObjectId(account_id)}, {'$set': {'is_backup': True}})
        await self.update_user(user_id, {'backup_account_id': account_id})

    async def get_backup_account(self, user_id: int) -> Optional[Dict]:
        return await self.accounts.find_one({'user_id': user_id, 'is_backup': True})

    async def toggle_backup(self, user_id: int, enabled: bool):
        await self.update_user(user_id, {'backup_enabled': enabled})

    async def is_backup_enabled(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        return user.get('backup_enabled', False) if user else False

    async def is_encryption_enabled(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        return user.get('encryption_enabled', False) if user else False

    async def toggle_encryption(self, user_id: int, enabled: bool):
        await self.update_user(user_id, {'encryption_enabled': enabled})

    async def is_bot_decrypt_enabled(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        return user.get('bot_decrypt_enabled', False) if user else False

    async def toggle_bot_decrypt(self, user_id: int, enabled: bool):
        await self.update_user(user_id, {'bot_decrypt_enabled': enabled})

    async def get_account_by_email(self, user_id: int, email: str) -> Optional[Dict]:
        return await self.accounts.find_one({'user_id': user_id, 'email': email})

db = Database()
