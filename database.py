"""
Database models and operations for Cloudyte
"""
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from typing import Optional, List, Dict
from cryptography.fernet import Fernet
from config import MONGO_URI, DATABASE_NAME

class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DATABASE_NAME]
        
        # Collections
        self.users = self.db['users']
        self.accounts = self.db['drive_accounts']
        self.callback_data = self.db['callback_data']
        self.system_config = self.db['system_config']  # New collection for encryption keys
    
    async def create_indexes(self):
        """Create database indexes for better performance"""
        await self.users.create_index('user_id', unique=True)
        await self.accounts.create_index([('user_id', 1), ('email', 1)])
        await self.callback_data.create_index([('user_id', 1), ('hash', 1)], unique=True)
        await self.callback_data.create_index('created_at', expireAfterSeconds=604800)  # 7 days

    # === SYSTEM SECURITY (NEW) ===
    async def get_or_create_encryption_key(self) -> bytes:
        """
        Check DB for encryption key. If not found, generate and save it.
        """
        config = await self.system_config.find_one({"_id": "master_key"})
        
        if config:
            # Key found in DB
            return config['key']
        else:
            # Generate new key
            new_key = Fernet.generate_key()
            await self.system_config.insert_one({
                "_id": "master_key",
                "key": new_key,
                "created_at": datetime.now(timezone.utc)
            })
            print("🔐 New Encryption Key Generated & Saved to Database!")
            return new_key
    
    # User Operations
    async def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID"""
        return await self.users.find_one({'user_id': user_id})
    
    async def create_user(self, user_id: int, username: str = None, full_name: str = None):
        """Create new user"""
        user_data = {
            'user_id': user_id,
            'username': username,
            'full_name': full_name,
            'default_account_id': None,
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
        await self.users.insert_one(user_data)
        return user_data
    
    async def update_user(self, user_id: int, update_data: Dict):
        """Update user data"""
        update_data['updated_at'] = datetime.now(timezone.utc)
        await self.users.update_one(
            {'user_id': user_id},
            {'$set': update_data}
        )
    
    # Drive Account Operations
    async def add_account(self, user_id: int, email: str, tokens: Dict) -> str:
        """Add Drive account for user"""
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
        
        # Set as default if it's the first account
        accounts_count = await self.accounts.count_documents({'user_id': user_id})
        if accounts_count == 1:
            await self.set_default_account(user_id, account_id)
        
        return account_id
    
    async def get_account(self, account_id: str) -> Optional[Dict]:
        """Get account by ID"""
        from bson import ObjectId
        return await self.accounts.find_one({'_id': ObjectId(account_id)})
    
    async def get_user_accounts(self, user_id: int) -> List[Dict]:
        """Get all accounts for user"""
        accounts = []
        async for account in self.accounts.find({'user_id': user_id}):
            account['account_id'] = str(account['_id'])
            accounts.append(account)
        return accounts
    
    async def update_account_tokens(self, account_id: str, tokens: Dict):
        """Update account tokens"""
        from bson import ObjectId
        await self.accounts.update_one(
            {'_id': ObjectId(account_id)},
            {'$set': {
                'access_token': tokens['access_token'],
                'refresh_token': tokens.get('refresh_token'),
                'expires_at': tokens['expires_at'],
                'updated_at': datetime.now(timezone.utc)
            }}
        )
    
    async def delete_account(self, account_id: str):
        """Delete Drive account"""
        from bson import ObjectId
        account = await self.get_account(account_id)
        if not account:
            return
        
        # Delete account
        await self.accounts.delete_one({'_id': ObjectId(account_id)})
        
        # Update default account if needed
        user = await self.get_user(account['user_id'])
        if user and user.get('default_account_id') == account_id:
            remaining_accounts = await self.get_user_accounts(account['user_id'])
            if remaining_accounts:
                await self.set_default_account(account['user_id'], remaining_accounts[0]['account_id'])
            else:
                await self.update_user(account['user_id'], {'default_account_id': None})
    
    async def set_default_account(self, user_id: int, account_id: str):
        """Set default account for user"""
        from bson import ObjectId
        
        # Remove default from all accounts
        await self.accounts.update_many(
            {'user_id': user_id},
            {'$set': {'is_default': False}}
        )
        
        # Set new default
        await self.accounts.update_one(
            {'_id': ObjectId(account_id)},
            {'$set': {'is_default': True}}
        )
        
        await self.update_user(user_id, {'default_account_id': account_id})

# Global database instance
db = Database()
