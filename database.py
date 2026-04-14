"""
Database models and operations for Cloudyte
"""
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from typing import Optional, List, Dict
from cryptography.fernet import Fernet
import hashlib
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
        self.auth_users = self.db['auth_users']  # New: user authentication
    
    async def create_indexes(self):
        """Create database indexes for better performance"""
        await self.users.create_index('user_id', unique=True)
        await self.accounts.create_index([('user_id', 1), ('email', 1)])
        await self.callback_data.create_index([('user_id', 1), ('hash', 1)], unique=True)
        await self.callback_data.create_index('created_at', expireAfterSeconds=604800)  # 7 days
        await self.auth_users.create_index('username', unique=True)
        await self.auth_users.create_index([('telegram_id', 1), ('is_logged_in', 1)])

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
    
    # === Authentication Operations ===
    
    def _hash_password(self, password: str) -> str:
        """Hash password using SHA256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    async def register_user(self, telegram_id: int, username: str, password: str, full_name: str = None) -> dict:
        """Register new user with username and password. Returns dict with 'success' and 'error' keys"""
        # Check if username is taken
        username_taken = await self.auth_users.find_one({'username': username})
        if username_taken:
            return {'success': False, 'error': 'username_taken'}
        
        # Logout any currently logged in user on this telegram
        await self.auth_users.update_many(
            {'telegram_id': telegram_id, 'is_logged_in': True},
            {'$set': {'is_logged_in': False}}
        )
        
        hashed_pwd = self._hash_password(password)
        # Generate internal user ID from username only (not telegram_id)
        internal_id = abs(hash(username)) % (10 ** 10)
        
        user_data = {
            'telegram_id': telegram_id,
            'username': username,
            'password': hashed_pwd,
            'full_name': full_name,
            'is_logged_in': True,
            'internal_user_id': internal_id,
            'created_at': datetime.now(timezone.utc),
            'last_login': datetime.now(timezone.utc)
        }
        
        await self.auth_users.insert_one(user_data)
        
        # Create internal user entry with unique ID based on username
        existing_user = await self.users.find_one({'user_id': internal_id})
        if not existing_user:
            await self.create_user(internal_id, username, full_name)
        
        return {'success': True, 'error': None, 'internal_user_id': internal_id}
    
    async def login_user(self, telegram_id: int, username: str, password: str) -> dict:
        """Login user with username and password"""
        hashed_pwd = self._hash_password(password)
        user = await self.auth_users.find_one({
            'username': username,
            'password': hashed_pwd
        })
        
        if user:
            # Logout any other user logged in on this telegram
            await self.auth_users.update_many(
                {'telegram_id': telegram_id, 'is_logged_in': True},
                {'$set': {'is_logged_in': False}}
            )
            
            # Update login status and last login
            await self.auth_users.update_one(
                {'_id': user['_id']},
                {'$set': {
                    'telegram_id': telegram_id,
                    'is_logged_in': True,
                    'last_login': datetime.now(timezone.utc)
                }}
            )
            
            # Get internal user ID from username
            internal_id = abs(hash(username)) % (10 ** 10)
            return {'success': True, 'internal_user_id': internal_id}
        return {'success': False, 'internal_user_id': None}
    
    async def logout_user(self, telegram_id: int) -> bool:
        """Logout user"""
        result = await self.auth_users.update_many(
            {'telegram_id': telegram_id, 'is_logged_in': True},
            {'$set': {'is_logged_in': False}}
        )
        return result.modified_count > 0
    
    async def is_user_logged_in(self, telegram_id: int) -> bool:
        """Check if user is logged in"""
        user = await self.auth_users.find_one({'telegram_id': telegram_id, 'is_logged_in': True})
        return user is not None
    
    async def get_auth_user(self, telegram_id: int) -> Optional[Dict]:
        """Get currently logged in authenticated user by telegram ID"""
        return await self.auth_users.find_one({'telegram_id': telegram_id, 'is_logged_in': True})
    
    async def get_internal_user_id(self, telegram_id: int) -> Optional[int]:
        """Get internal user ID for currently logged in user"""
        auth_user = await self.get_auth_user(telegram_id)
        if auth_user:
            return abs(hash(auth_user['username'])) % (10 ** 10)
        return None
    
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
            'backup_account_id': None,
            'backup_enabled': False,
            'encryption_enabled': False,
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
    
    # Backup Account Operations
    async def set_backup_account(self, user_id: int, account_id: str):
        """Set backup account for user"""
        from bson import ObjectId
        
        # Remove backup from all accounts
        await self.accounts.update_many(
            {'user_id': user_id},
            {'$set': {'is_backup': False}}
        )
        
        # Set new backup
        await self.accounts.update_one(
            {'_id': ObjectId(account_id)},
            {'$set': {'is_backup': True}}
        )
        
        await self.update_user(user_id, {'backup_account_id': account_id})
    
    async def get_backup_account(self, user_id: int) -> Optional[Dict]:
        """Get backup account for user"""
        return await self.accounts.find_one({'user_id': user_id, 'is_backup': True})
    
    async def toggle_backup(self, user_id: int, enabled: bool):
        """Enable or disable backup for user"""
        await self.update_user(user_id, {'backup_enabled': enabled})
    
    async def is_backup_enabled(self, user_id: int) -> bool:
        """Check if backup is enabled for user"""
        user = await self.get_user(user_id)
        return user and user.get('backup_enabled', False)
    
    async def get_account_by_email(self, user_id: int, email: str) -> Optional[Dict]:
        """Get account by email for specific user"""
        return await self.accounts.find_one({'user_id': user_id, 'email': email})


    # Encryption Settings
    async def is_encryption_enabled(self, user_id: int) -> bool:
        """Check if encryption is enabled for user (default: False)"""
        user = await self.get_user(user_id)
        if not user:
            return False
        return user.get('encryption_enabled', False)

    async def toggle_encryption(self, user_id: int, enabled: bool):
        """Enable or disable encryption for user"""
        await self.update_user(user_id, {'encryption_enabled': enabled})

    async def is_bot_decrypt_enabled(self, user_id: int) -> bool:
        """Check if bot decryption is enabled for user (default: False)"""
        user = await self.get_user(user_id)
        if not user:
            return False
        return user.get('bot_decrypt_enabled', False)

    async def toggle_bot_decrypt(self, user_id: int, enabled: bool):
        """Enable or disable bot decryption for user"""
        await self.update_user(user_id, {'bot_decrypt_enabled': enabled})

# Global database instance
db = Database()
