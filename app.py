# app.py
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, session, redirect, url_for
import mimetypes
import os
import time
from pathlib import Path
import asyncio
import queue
from app.agent.manus import Manus
from app.logger import logger, log_queue
from app.config import config as app_config
import threading
import toml
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import logging
import json
import uuid
from werkzeug.utils import secure_filename
import shutil
import sqlite3
from dataclasses import dataclass, asdict
import hashlib
import csv
import io

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'
app.config['WORKSPACE'] = 'workspace'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CHAT_HISTORY_FILE'] = 'chat_history.json'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['DATABASE'] = 'advanced_app.db'

# Create necessary directories
os.makedirs(app.config['WORKSPACE'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Global variable to track running tasks
running_tasks = {}

# Load configuration
config = toml.load('config/config.toml')

# Advanced Data Models
@dataclass
class FilterCriteria:
    field: str
    operator: str
    value: Any
    logical_operator: str = "AND"

@dataclass
class DataRecord:
    id: str
    timestamp: datetime
    category: str
    tags: List[str]
    content: str
    metadata: Dict[str, Any]
    status: str = "active"

# Advanced Database Management
class AdvancedDatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database with advanced tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Main data table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS data_records (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    category TEXT NOT NULL,
                    tags TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Filter presets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filter_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    filter_criteria TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Export history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS export_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_type TEXT NOT NULL,
                    filter_criteria TEXT,
                    record_count INTEGER,
                    exported_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # User sessions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    user_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_activity TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
    
    def add_record(self, record: DataRecord) -> bool:
        """Add a new data record"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO data_records (id, timestamp, category, tags, content, metadata, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    record.id,
                    record.timestamp.isoformat(),
                    record.category,
                    json.dumps(record.tags),
                    record.content,
                    json.dumps(record.metadata),
                    record.status
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding record: {e}")
            return False
    
    def get_records(self, filters: List[FilterCriteria] = None, limit: int = 100, offset: int = 0) -> List[DataRecord]:
        """Get records with advanced filtering"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                query = "SELECT * FROM data_records WHERE 1=1"
                params = []
                
                if filters:
                    for filter_criteria in filters:
                        if filter_criteria.operator == "contains":
                            query += f" AND {filter_criteria.field} LIKE ?"
                            params.append(f"%{filter_criteria.value}%")
                        elif filter_criteria.operator == "equals":
                            query += f" AND {filter_criteria.field} = ?"
                            params.append(filter_criteria.value)
                        elif filter_criteria.operator == "greater_than":
                            query += f" AND {filter_criteria.field} > ?"
                            params.append(filter_criteria.value)
                        elif filter_criteria.operator == "less_than":
                            query += f" AND {filter_criteria.field} < ?"
                            params.append(filter_criteria.value)
                        elif filter_criteria.operator == "in":
                            placeholders = ','.join(['?' for _ in filter_criteria.value])
                            query += f" AND {filter_criteria.field} IN ({placeholders})"
                            params.extend(filter_criteria.value)
                
                query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                records = []
                for row in rows:
                    record = DataRecord(
                        id=row[0],
                        timestamp=datetime.fromisoformat(row[1]),
                        category=row[2],
                        tags=json.loads(row[3]) if row[3] else [],
                        content=row[4],
                        metadata=json.loads(row[5]) if row[5] else {},
                        status=row[6]
                    )
                    records.append(record)
                
                return records
        except Exception as e:
            logger.error(f"Error getting records: {e}")
            return []
    
    def save_filter_preset(self, name: str, description: str, filters: List[FilterCriteria]) -> bool:
        """Save a filter preset"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO filter_presets (name, description, filter_criteria)
                    VALUES (?, ?, ?)
                ''', (name, description, json.dumps([asdict(f) for f in filters])))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving filter preset: {e}")
            return False
    
    def get_filter_presets(self) -> List[Dict]:
        """Get all filter presets"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM filter_presets ORDER BY created_at DESC')
                rows = cursor.fetchall()
                
                presets = []
                for row in rows:
                    presets.append({
                        'id': row[0],
                        'name': row[1],
                        'description': row[2],
                        'filter_criteria': json.loads(row[3]),
                        'created_at': row[4]
                    })
                return presets
        except Exception as e:
            logger.error(f"Error getting filter presets: {e}")
            return []
    
    def update_record(self, record: DataRecord) -> bool:
        """Update an existing data record"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE data_records 
                    SET timestamp = ?, category = ?, tags = ?, content = ?, metadata = ?, status = ?
                    WHERE id = ?
                ''', (
                    record.timestamp.isoformat(),
                    record.category,
                    json.dumps(record.tags),
                    record.content,
                    json.dumps(record.metadata),
                    record.status,
                    record.id
                ))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating record: {e}")
            return False
    
    def delete_record(self, record_id: str) -> bool:
        """Delete a data record"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM data_records WHERE id = ?', (record_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting record: {e}")
            return False
    
    def get_record_by_id(self, record_id: str) -> Optional[DataRecord]:
        """Get a single record by ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM data_records WHERE id = ?', (record_id,))
                row = cursor.fetchone()
                
                if row:
                    return DataRecord(
                        id=row[0],
                        timestamp=datetime.fromisoformat(row[1]),
                        category=row[2],
                        tags=json.loads(row[3]) if row[3] else [],
                        content=row[4],
                        metadata=json.loads(row[5]) if row[5] else {},
                        status=row[6]
                    )
                return None
        except Exception as e:
            logger.error(f"Error getting record by ID: {e}")
            return None
    
    def get_categories(self) -> List[str]:
        """Get all unique categories"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT category FROM data_records ORDER BY category')
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            return []
    
    def get_tags(self) -> List[str]:
        """Get all unique tags"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT tags FROM data_records WHERE tags IS NOT NULL')
                all_tags = []
                for row in cursor.fetchall():
                    if row[0]:
                        tags = json.loads(row[0])
                        all_tags.extend(tags)
                return list(set(all_tags))  # Remove duplicates
        except Exception as e:
            logger.error(f"Error getting tags: {e}")
            return []
    
    def get_recent_records(self, hours: int = 24) -> List[DataRecord]:
        """Get records from the last N hours"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM data_records 
                    WHERE timestamp > ? 
                    ORDER BY timestamp DESC
                ''', (cutoff_time.isoformat(),))
                
                rows = cursor.fetchall()
                records = []
                for row in rows:
                    record = DataRecord(
                        id=row[0],
                        timestamp=datetime.fromisoformat(row[1]),
                        category=row[2],
                        tags=json.loads(row[3]) if row[3] else [],
                        content=row[4],
                        metadata=json.loads(row[5]) if row[5] else {},
                        status=row[6]
                    )
                    records.append(record)
                return records
        except Exception as e:
            logger.error(f"Error getting recent records: {e}")
            return []

# Initialize database manager
db_manager = AdvancedDatabaseManager(app.config['DATABASE'])

# Advanced API Key Management System
class AdvancedAPIKeyManager:
    def __init__(self, api_keys_config):
        self.api_keys = []
        self.usage_stats = {}
        self.disabled_keys = {}  # {key: disabled_until_timestamp}
        self.failure_counts = {}  # {key: consecutive_failures}
        self.last_used = {}  # {key: last_used_timestamp}
        
        # Initialize API keys from config
        for key_config in api_keys_config:
            self.api_keys.append({
                'api_key': key_config['api_key'],
                'name': key_config.get('name', f"Key_{key_config['api_key'][:8]}"),
                'max_requests_per_minute': key_config.get('max_requests_per_minute', 5),
                'max_requests_per_hour': key_config.get('max_requests_per_hour', 100),
                'max_requests_per_day': key_config.get('max_requests_per_day', 100),
                'priority': key_config.get('priority', 1),
                'enabled': key_config.get('enabled', True)
            })
            
            # Initialize stats for each key
            key = key_config['api_key']
            self.usage_stats[key] = {
                'requests_this_minute': [],
                'requests_this_hour': [],
                'requests_this_day': [],
                'total_requests': 0
            }
            self.failure_counts[key] = 0
            self.last_used[key] = None
        
        logger.info(f"Initialized advanced API key manager with {len(self.api_keys)} keys")
    
    def _clean_old_usage_data(self, api_key: str):
        """Clean old usage data for accurate rate limiting"""
        current_time = time.time()
        stats = self.usage_stats[api_key]
        
        # Clean minute data (older than 60 seconds)
        stats['requests_this_minute'] = [
            t for t in stats['requests_this_minute'] 
            if current_time - t < 60
        ]
        
        # Clean hour data (older than 3600 seconds)
        stats['requests_this_hour'] = [
            t for t in stats['requests_this_hour'] 
            if current_time - t < 3600
        ]
        
        # Clean day data (older than 86400 seconds)
        stats['requests_this_day'] = [
            t for t in stats['requests_this_day'] 
            if current_time - t < 86400
        ]
    
    def _is_key_available(self, key_config: dict) -> bool:
        """Check if an API key is available for use"""
        api_key = key_config['api_key']
        current_time = time.time()
        
        # Check if key is disabled
        if api_key in self.disabled_keys:
            if current_time < self.disabled_keys[api_key]:
                return False
            else:
                del self.disabled_keys[api_key]
        
        # Check if key is enabled
        if not key_config.get('enabled', True):
            return False
        
        # Clean old usage data
        self._clean_old_usage_data(api_key)
        
        # Check rate limits
        stats = self.usage_stats[api_key]
        
        if len(stats['requests_this_minute']) >= key_config['max_requests_per_minute']:
            return False
        
        if len(stats['requests_this_hour']) >= key_config['max_requests_per_hour']:
            return False
        
        if len(stats['requests_this_day']) >= key_config['max_requests_per_day']:
            return False
        
        return True
    
    def _disable_key_for_rate_limit(self, api_key: str, key_name: str):
        """Disable a key temporarily due to rate limiting"""
        disable_duration = min(300, 60 * (2 ** self.failure_counts[api_key]))  # Exponential backoff
        self.disabled_keys[api_key] = time.time() + disable_duration
        logger.warning(f"API key {key_name} disabled for {disable_duration} seconds due to rate limiting")
    
    def _calculate_key_score(self, key_config: dict) -> float:
        """Calculate a score for key selection (higher is better)"""
        api_key = key_config['api_key']
        current_time = time.time()
        
        # Base score from priority
        score = key_config.get('priority', 1) * 100
        
        # Penalty for recent usage (prefer less recently used keys)
        if api_key in self.last_used:
            time_since_last_use = current_time - self.last_used[api_key]
            score += min(time_since_last_use / 60, 50)  # Max 50 points for time
        
        # Penalty for failure count
        failure_penalty = self.failure_counts[api_key] * 10
        score -= failure_penalty
        
        # Bonus for low usage
        stats = self.usage_stats[api_key]
        usage_ratio = len(stats['requests_this_hour']) / key_config['max_requests_per_hour']
        score += (1 - usage_ratio) * 20
        
        return max(score, 0)
    
    def get_available_api_key(self, use_random: bool = True) -> Optional[Tuple[str, dict]]:
        """Get the best available API key"""
        available_keys = []
        
        for key_config in self.api_keys:
            if self._is_key_available(key_config):
                available_keys.append(key_config)
        
        if not available_keys:
            logger.error("No available API keys found")
            return None
        
        if use_random and len(available_keys) > 1:
            # Use weighted random selection based on scores
            scores = [self._calculate_key_score(key_config) for key_config in available_keys]
            total_score = sum(scores)
            
            if total_score > 0:
                weights = [score / total_score for score in scores]
                selected_key = random.choices(available_keys, weights=weights)[0]
            else:
                selected_key = random.choice(available_keys)
        else:
            # Select the key with the highest score
            selected_key = max(available_keys, key=lambda k: self._calculate_key_score(k))
        
        api_key = selected_key['api_key']
        self.last_used[api_key] = time.time()
        
        return api_key, selected_key
    
    def record_successful_request(self, api_key: str):
        """Record a successful API request"""
        if api_key in self.usage_stats:
            current_time = time.time()
            stats = self.usage_stats[api_key]
            
            stats['requests_this_minute'].append(current_time)
            stats['requests_this_hour'].append(current_time)
            stats['requests_this_day'].append(current_time)
            stats['total_requests'] += 1
            
            # Reset failure count on success
            self.failure_counts[api_key] = 0
    
    def record_rate_limit_error(self, api_key: str, key_name: str):
        """Record a rate limit error"""
        self.failure_counts[api_key] = self.failure_counts.get(api_key, 0) + 1
        self._disable_key_for_rate_limit(api_key, key_name)
        logger.warning(f"Rate limit error for API key {key_name}")
    
    def record_failure(self, api_key: str, key_name: str, error_type: str = "unknown"):
        """Record a general API failure"""
        self.failure_counts[api_key] = self.failure_counts.get(api_key, 0) + 1
        logger.error(f"API failure for key {key_name}: {error_type}")
    
    def get_keys_status(self) -> List[Dict]:
        """Get status of all API keys"""
        status_list = []
        
        for key_config in self.api_keys:
            api_key = key_config['api_key']
            current_time = time.time()
            
            # Clean old data
            self._clean_old_usage_data(api_key)
            
            # Check if disabled
            is_disabled = api_key in self.disabled_keys and current_time < self.disabled_keys[api_key]
            
            # Get usage stats
            stats = self.usage_stats[api_key]
            
            status = {
                'name': key_config['name'],
                'enabled': key_config.get('enabled', True) and not is_disabled,
                'priority': key_config.get('priority', 1),
                'usage': {
                    'minute': len(stats['requests_this_minute']),
                    'hour': len(stats['requests_this_hour']),
                    'day': len(stats['requests_this_day']),
                    'total': stats['total_requests']
                },
                'limits': {
                    'minute': key_config['max_requests_per_minute'],
                    'hour': key_config['max_requests_per_hour'],
                    'day': key_config['max_requests_per_day']
                },
                'failures': self.failure_counts.get(api_key, 0),
                'last_used': self.last_used.get(api_key),
                'disabled_until': self.disabled_keys.get(api_key)
            }
            
            status_list.append(status)
        
        return status_list

# Initialize API key manager
api_key_manager = AdvancedAPIKeyManager(config.get('api_keys', []))

# Advanced Data Export Manager
class DataExportManager:
    @staticmethod
    def export_to_csv(records: List[DataRecord]) -> str:
        """Export records to CSV format"""
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['ID', 'Timestamp', 'Category', 'Tags', 'Content', 'Status', 'Metadata'])
        
        # Write data
        for record in records:
            writer.writerow([
                record.id,
                record.timestamp.isoformat(),
                record.category,
                ', '.join(record.tags),
                record.content,
                record.status,
                json.dumps(record.metadata)
            ])
        
        return output.getvalue()
    
    @staticmethod
    def export_to_json(records: List[DataRecord]) -> str:
        """Export records to JSON format"""
        data = []
        for record in records:
            data.append({
                'id': record.id,
                'timestamp': record.timestamp.isoformat(),
                'category': record.category,
                'tags': record.tags,
                'content': record.content,
                'status': record.status,
                'metadata': record.metadata
            })
        
        return json.dumps(data, indent=2, ensure_ascii=False)
    
    @staticmethod
    def export_to_xml(records: List[DataRecord]) -> str:
        """Export records to XML format"""
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<records>']
        
        for record in records:
            xml_lines.append('  <record>')
            xml_lines.append(f'    <id>{record.id}</id>')
            xml_lines.append(f'    <timestamp>{record.timestamp.isoformat()}</timestamp>')
            xml_lines.append(f'    <category>{record.category}</category>')
            xml_lines.append('    <tags>')
            for tag in record.tags:
                xml_lines.append(f'      <tag>{tag}</tag>')
            xml_lines.append('    </tags>')
            xml_lines.append(f'    <content><![CDATA[{record.content}]]></content>')
            xml_lines.append(f'    <status>{record.status}</status>')
            xml_lines.append(f'    <metadata>{json.dumps(record.metadata)}</metadata>')
            xml_lines.append('  </record>')
        
        xml_lines.append('</records>')
        return '\n'.join(xml_lines)

# Advanced Filter Manager
class AdvancedFilterManager:
    @staticmethod
    def parse_filter_string(filter_string: str) -> List[FilterCriteria]:
        """Parse filter string into FilterCriteria objects"""
        filters = []
        
        # Simple parsing for demonstration
        # Format: field:operator:value,field2:operator2:value2
        filter_parts = filter_string.split(',')
        
        for part in filter_parts:
            if ':' in part:
                field, operator, value = part.split(':', 2)
                filters.append(FilterCriteria(
                    field=field.strip(),
                    operator=operator.strip(),
                    value=value.strip()
                ))
        
        return filters
    
    @staticmethod
    def validate_filter(filters: List[FilterCriteria]) -> Tuple[bool, str]:
        """Validate filter criteria"""
        valid_operators = ['contains', 'equals', 'greater_than', 'less_than', 'in']
        valid_fields = ['category', 'tags', 'content', 'status', 'timestamp']
        
        for filter_criteria in filters:
            if filter_criteria.field not in valid_fields:
                return False, f"Invalid field: {filter_criteria.field}"
            
            if filter_criteria.operator not in valid_operators:
                return False, f"Invalid operator: {filter_criteria.operator}"
        
        return True, "Valid"

# Initialize managers
export_manager = DataExportManager()
filter_manager = AdvancedFilterManager()

# 初始化工作目录
os.makedirs(app.config['WORKSPACE'], exist_ok=True)
LOG_FILE = 'logs/root_stream.log'
FILE_CHECK_INTERVAL = 2  # 文件检查间隔（秒）
PROCESS_TIMEOUT = 6099999990    # 最长处理时间（秒）

def get_files_pathlib(root_dir):
    """Get all files in directory using pathlib"""
    return list(Path(root_dir).rglob('*'))

@app.route('/')
def index():
    """Enhanced main page with advanced features"""
    return render_template('index.html')

@app.route('/file/<filename>')
def file(filename):
    """Serve uploaded files"""
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None:
                mime_type = 'application/octet-stream'
            
            return send_from_directory(
                app.config['UPLOAD_FOLDER'],
                filename,
                mimetype=mime_type
            )
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        logger.error(f"Error serving file {filename}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/keys/status')
def api_keys_status():
    """Get API keys status"""
    return jsonify(api_key_manager.get_keys_status())

# Advanced Data Management Routes
@app.route('/api/data/add', methods=['POST'])
def add_data_record():
    """Add a new data record with enhanced features"""
    try:
        data = request.get_json()
        
        # Enhanced validation
        if not data.get('content', '').strip():
            return jsonify({'success': False, 'error': 'Content is required'}), 400
        
        if not data.get('category', '').strip():
            return jsonify({'success': False, 'error': 'Category is required'}), 400
        
        # Auto-generate tags from content if not provided
        tags = data.get('tags', [])
        if not tags and data.get('content'):
            # Simple keyword extraction
            content_words = data['content'].lower().split()
            common_words = ['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by']
            keywords = [word for word in content_words if word not in common_words and len(word) > 3][:5]
            tags = keywords[:3]  # Limit to 3 auto-generated tags
        
        # Enhanced metadata
        metadata = data.get('metadata', {})
        metadata.update({
            'created_by': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', ''),
            'content_length': len(data.get('content', '')),
            'word_count': len(data.get('content', '').split()),
            'auto_generated_tags': len(tags) == 0
        })
        
        record = DataRecord(
            id=str(uuid.uuid4()),
            timestamp=datetime.fromisoformat(data.get('timestamp', datetime.now().isoformat())),
            category=data.get('category', 'general'),
            tags=tags,
            content=data.get('content', ''),
            metadata=metadata,
            status=data.get('status', 'active')
        )
        
        if db_manager.add_record(record):
            # Return the complete record for immediate display
            return jsonify({
                'success': True, 
                'id': record.id,
                'record': {
                    'id': record.id,
                    'timestamp': record.timestamp.isoformat(),
                    'category': record.category,
                    'tags': record.tags,
                    'content': record.content,
                    'status': record.status,
                    'metadata': record.metadata
                }
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to add record'}), 500
            
    except Exception as e:
        logger.error(f"Error adding data record: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/update', methods=['PUT'])
def update_data_record():
    """Update an existing data record"""
    try:
        data = request.get_json()
        record_id = data.get('id')
        
        if not record_id:
            return jsonify({'success': False, 'error': 'Record ID is required'}), 400
        
        # Get existing record
        existing_records = db_manager.get_records([FilterCriteria('id', 'equals', record_id)], limit=1)
        if not existing_records:
            return jsonify({'success': False, 'error': 'Record not found'}), 404
        
        existing_record = existing_records[0]
        
        # Update fields
        if 'category' in data:
            existing_record.category = data['category']
        if 'content' in data:
            existing_record.content = data['content']
        if 'tags' in data:
            existing_record.tags = data['tags']
        if 'status' in data:
            existing_record.status = data['status']
        
        # Update metadata
        existing_record.metadata.update({
            'last_modified': datetime.now().isoformat(),
            'modified_by': request.remote_addr,
            'content_length': len(existing_record.content),
            'word_count': len(existing_record.content.split())
        })
        
        # Update in database
        if db_manager.update_record(existing_record):
            return jsonify({
                'success': True,
                'record': {
                    'id': existing_record.id,
                    'timestamp': existing_record.timestamp.isoformat(),
                    'category': existing_record.category,
                    'tags': existing_record.tags,
                    'content': existing_record.content,
                    'status': existing_record.status,
                    'metadata': existing_record.metadata
                }
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to update record'}), 500
            
    except Exception as e:
        logger.error(f"Error updating data record: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/delete/<record_id>', methods=['DELETE'])
def delete_data_record(record_id):
    """Delete a data record"""
    try:
        if db_manager.delete_record(record_id):
            return jsonify({'success': True, 'message': 'Record deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to delete record'}), 500
            
    except Exception as e:
        logger.error(f"Error deleting data record: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/live', methods=['GET'])
def get_live_data():
    """Get live data updates with real-time features"""
    try:
        # Get query parameters
        filters_str = request.args.get('filters', '')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        last_update = request.args.get('last_update', '')
        
        filters = []
        if filters_str:
            filters = filter_manager.parse_filter_string(filters_str)
        
        # Get records
        records = db_manager.get_records(filters, limit, offset)
        
        # Check for new records since last update
        new_records = []
        if last_update:
            try:
                last_update_time = datetime.fromisoformat(last_update)
                new_records = [r for r in records if r.timestamp > last_update_time]
            except:
                pass
        
        # Convert to dict for JSON serialization
        records_data = []
        for record in records:
            records_data.append({
                'id': record.id,
                'timestamp': record.timestamp.isoformat(),
                'category': record.category,
                'tags': record.tags,
                'content': record.content,
                'status': record.status,
                'metadata': record.metadata,
                'is_new': record.id in [r.id for r in new_records]
            })
        
        return jsonify({
            'success': True, 
            'records': records_data,
            'total_count': len(records),
            'new_count': len(new_records),
            'current_time': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting live data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/get', methods=['GET'])
def get_data_records():
    """Get data records with advanced filtering"""
    try:
        # Parse query parameters
        filters_str = request.args.get('filters', '')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        
        filters = []
        if filters_str:
            filters = filter_manager.parse_filter_string(filters_str)
        
        # Validate filters
        is_valid, error_msg = filter_manager.validate_filter(filters)
        if not is_valid:
            return jsonify({'success': False, 'error': error_msg}), 400
        
        records = db_manager.get_records(filters, limit, offset)
        
        # Convert to dict for JSON serialization
        records_data = []
        for record in records:
            records_data.append({
                'id': record.id,
                'timestamp': record.timestamp.isoformat(),
                'category': record.category,
                'tags': record.tags,
                'content': record.content,
                'status': record.status,
                'metadata': record.metadata
            })
        
        return jsonify({'success': True, 'records': records_data})
        
    except Exception as e:
        logger.error(f"Error getting data records: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/export', methods=['POST'])
def export_data():
    """Export data in various formats"""
    try:
        data = request.get_json()
        export_format = data.get('format', 'json')
        filters_str = data.get('filters', '')
        
        filters = []
        if filters_str:
            filters = filter_manager.parse_filter_string(filters_str)
        
        records = db_manager.get_records(filters, limit=10000)  # Large limit for export
        
        if export_format == 'csv':
            content = export_manager.export_to_csv(records)
            return Response(content, mimetype='text/csv', headers={
                'Content-Disposition': f'attachment; filename=export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            })
        elif export_format == 'json':
            content = export_manager.export_to_json(records)
            return Response(content, mimetype='application/json', headers={
                'Content-Disposition': f'attachment; filename=export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            })
        elif export_format == 'xml':
            content = export_manager.export_to_xml(records)
            return Response(content, mimetype='application/xml', headers={
                'Content-Disposition': f'attachment; filename=export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xml'
            })
        else:
            return jsonify({'success': False, 'error': 'Unsupported export format'}), 400
            
    except Exception as e:
        logger.error(f"Error exporting data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/filters/presets', methods=['GET', 'POST'])
def manage_filter_presets():
    """Manage filter presets"""
    if request.method == 'GET':
        presets = db_manager.get_filter_presets()
        return jsonify({'success': True, 'presets': presets})
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            name = data.get('name')
            description = data.get('description', '')
            filters_str = data.get('filters', '')
            
            if not name or not filters_str:
                return jsonify({'success': False, 'error': 'Name and filters are required'}), 400
            
            filters = filter_manager.parse_filter_string(filters_str)
            is_valid, error_msg = filter_manager.validate_filter(filters)
            
            if not is_valid:
                return jsonify({'success': False, 'error': error_msg}), 400
            
            if db_manager.save_filter_preset(name, description, filters):
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Failed to save preset'}), 500
                
        except Exception as e:
            logger.error(f"Error saving filter preset: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/statistics', methods=['GET'])
def get_data_statistics():
    """Get enhanced data statistics"""
    try:
        # Get basic statistics
        all_records = db_manager.get_records(limit=10000)
        
        # Category statistics
        categories = {}
        tags = {}
        statuses = {}
        hourly_stats = {}
        daily_stats = {}
        
        for record in all_records:
            # Category count
            categories[record.category] = categories.get(record.category, 0) + 1
            
            # Tag count
            for tag in record.tags:
                tags[tag] = tags.get(tag, 0) + 1
            
            # Status count
            statuses[record.status] = statuses.get(record.status, 0) + 1
            
            # Hourly statistics
            hour = record.timestamp.hour
            hourly_stats[hour] = hourly_stats.get(hour, 0) + 1
            
            # Daily statistics
            day = record.timestamp.strftime('%Y-%m-%d')
            daily_stats[day] = daily_stats.get(day, 0) + 1
        
        # Time-based statistics
        now = datetime.now()
        recent_records = [r for r in all_records if (now - r.timestamp).days <= 7]
        today_records = [r for r in all_records if (now - r.timestamp).days == 0]
        
        # Content analysis
        content_lengths = [len(r.content) for r in all_records]
        word_counts = [r.metadata.get('word_count', len(r.content.split())) for r in all_records]
        
        statistics = {
            'total_records': len(all_records),
            'recent_records': len(recent_records),
            'today_records': len(today_records),
            'categories': categories,
            'top_tags': dict(sorted(tags.items(), key=lambda x: x[1], reverse=True)[:10]),
            'statuses': statuses,
            'hourly_distribution': dict(sorted(hourly_stats.items())),
            'daily_distribution': dict(sorted(daily_stats.items())[-30:]),  # Last 30 days
            'content_analysis': {
                'average_content_length': sum(content_lengths) / len(content_lengths) if content_lengths else 0,
                'average_word_count': sum(word_counts) / len(word_counts) if word_counts else 0,
                'max_content_length': max(content_lengths) if content_lengths else 0,
                'min_content_length': min(content_lengths) if content_lengths else 0
            },
            'growth_rate': {
                'last_7_days': len(recent_records),
                'last_24_hours': len(today_records),
                'average_daily': len(all_records) / max(1, (now - min(r.timestamp for r in all_records)).days) if all_records else 0
            }
        }
        
        return jsonify({'success': True, 'statistics': statistics})
        
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/categories', methods=['GET'])
def get_categories():
    """Get all available categories"""
    try:
        categories = db_manager.get_categories()
        return jsonify({'success': True, 'categories': categories})
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/tags', methods=['GET'])
def get_tags():
    """Get all available tags"""
    try:
        tags = db_manager.get_tags()
        return jsonify({'success': True, 'tags': tags})
    except Exception as e:
        logger.error(f"Error getting tags: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/recent', methods=['GET'])
def get_recent_data():
    """Get recent data records"""
    try:
        hours = int(request.args.get('hours', 24))
        records = db_manager.get_recent_records(hours)
        
        records_data = []
        for record in records:
            records_data.append({
                'id': record.id,
                'timestamp': record.timestamp.isoformat(),
                'category': record.category,
                'tags': record.tags,
                'content': record.content,
                'status': record.status,
                'metadata': record.metadata
            })
        
        return jsonify({'success': True, 'records': records_data})
    except Exception as e:
        logger.error(f"Error getting recent data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/record/<record_id>', methods=['GET'])
def get_single_record(record_id):
    """Get a single record by ID"""
    try:
        record = db_manager.get_record_by_id(record_id)
        if record:
            return jsonify({
                'success': True,
                'record': {
                    'id': record.id,
                    'timestamp': record.timestamp.isoformat(),
                    'category': record.category,
                    'tags': record.tags,
                    'content': record.content,
                    'status': record.status,
                    'metadata': record.metadata
                }
            })
        else:
            return jsonify({'success': False, 'error': 'Record not found'}), 404
    except Exception as e:
        logger.error(f"Error getting single record: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/data/bulk-import', methods=['POST'])
def bulk_import_data():
    """Bulk import data from various formats"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if file:
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Process based on file type
            if filename.endswith('.csv'):
                records = process_csv_import(file_path)
            elif filename.endswith('.json'):
                records = process_json_import(file_path)
            else:
                return jsonify({'success': False, 'error': 'Unsupported file format'}), 400
            
            # Add records to database
            success_count = 0
            for record in records:
                if db_manager.add_record(record):
                    success_count += 1
            
            return jsonify({
                'success': True,
                'imported': success_count,
                'total': len(records)
            })
            
    except Exception as e:
        logger.error(f"Error in bulk import: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def process_csv_import(file_path: str) -> List[DataRecord]:
    """Process CSV file for import"""
    records = []
    
    with open(file_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            record = DataRecord(
                id=str(uuid.uuid4()),
                timestamp=datetime.fromisoformat(row.get('timestamp', datetime.now().isoformat())),
                category=row.get('category', 'imported'),
                tags=row.get('tags', '').split(',') if row.get('tags') else [],
                content=row.get('content', ''),
                metadata={},
                status=row.get('status', 'active')
            )
            records.append(record)
    
    return records

def process_json_import(file_path: str) -> List[DataRecord]:
    """Process JSON file for import"""
    records = []
    
    with open(file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)
        
        for item in data:
            record = DataRecord(
                id=str(uuid.uuid4()),
                timestamp=datetime.fromisoformat(item.get('timestamp', datetime.now().isoformat())),
                category=item.get('category', 'imported'),
                tags=item.get('tags', []),
                content=item.get('content', ''),
                metadata=item.get('metadata', {}),
                status=item.get('status', 'active')
            )
            records.append(record)
    
    return records

def allowed_file(filename):
    """Check if file type is allowed"""
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'csv', 'json', 'xml'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_chat_history(chat_data):
    """Save chat history to file"""
    try:
        history_file = app.config['CHAT_HISTORY_FILE']
        
        # Load existing history
        existing_history = []
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                existing_history = json.load(f)
        
        # Add new chat data
        chat_data['timestamp'] = datetime.now().isoformat()
        existing_history.append(chat_data)
        
        # Keep only last 100 conversations
        if len(existing_history) > 100:
            existing_history = existing_history[-100:]
        
        # Save updated history
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(existing_history, f, indent=2, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error saving chat history: {e}")

def load_chat_history():
    """Load chat history from file"""
    try:
        history_file = app.config['CHAT_HISTORY_FILE']
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        logger.error(f"Error loading chat history: {e}")
        return []

# Enhanced Session Management
@app.route('/api/session/start', methods=['POST'])
def start_session():
    """Start a new user session"""
    try:
        session_id = str(uuid.uuid4())
        session_data = {
            'id': session_id,
            'start_time': datetime.now().isoformat(),
            'user_agent': request.headers.get('User-Agent', ''),
            'ip_address': request.remote_addr
        }
        
        # Store session in database
        with sqlite3.connect(app.config['DATABASE']) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO user_sessions (id, user_data)
                VALUES (?, ?)
            ''', (session_id, json.dumps(session_data)))
            conn.commit()
        
        return jsonify({'success': True, 'session_id': session_id})
        
    except Exception as e:
        logger.error(f"Error starting session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/session/update', methods=['POST'])
def update_session():
    """Update session activity"""
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'success': False, 'error': 'Session ID required'}), 400
        
        with sqlite3.connect(app.config['DATABASE']) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE user_sessions 
                SET last_activity = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (session_id,))
            conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Error updating session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Advanced Search and Analytics
@app.route('/api/search', methods=['POST'])
def advanced_search():
    """Advanced search functionality"""
    try:
        data = request.get_json()
        query = data.get('query', '')
        search_type = data.get('type', 'all')  # all, content, tags, category
        limit = int(data.get('limit', 50))
        
        if not query:
            return jsonify({'success': False, 'error': 'Search query required'}), 400
        
        # Build search filters based on type
        filters = []
        if search_type in ['all', 'content']:
            filters.append(FilterCriteria('content', 'contains', query))
        if search_type in ['all', 'tags']:
            filters.append(FilterCriteria('tags', 'contains', query))
        if search_type in ['all', 'category']:
            filters.append(FilterCriteria('category', 'contains', query))
        
        records = db_manager.get_records(filters, limit=limit)
        
        # Convert to response format
        results = []
        for record in records:
            results.append({
                'id': record.id,
                'timestamp': record.timestamp.isoformat(),
                'category': record.category,
                'tags': record.tags,
                'content': record.content[:200] + '...' if len(record.content) > 200 else record.content,
                'status': record.status,
                'relevance_score': calculate_relevance_score(record, query)
            })
        
        # Sort by relevance score
        results.sort(key=lambda x: x['relevance_score'], reverse=True)
        
        return jsonify({'success': True, 'results': results, 'total': len(results)})
        
    except Exception as e:
        logger.error(f"Error in advanced search: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def calculate_relevance_score(record: DataRecord, query: str) -> float:
    """Calculate relevance score for search results"""
    score = 0.0
    query_lower = query.lower()
    
    # Content relevance (highest weight)
    if query_lower in record.content.lower():
        score += 10.0
    
    # Tag relevance
    for tag in record.tags:
        if query_lower in tag.lower():
            score += 5.0
    
    # Category relevance
    if query_lower in record.category.lower():
        score += 3.0
    
    # Recency bonus
    days_old = (datetime.now() - record.timestamp).days
    if days_old <= 1:
        score += 2.0
    elif days_old <= 7:
        score += 1.0
    
    return score

# Data Backup and Restore
@app.route('/api/backup/create', methods=['POST'])
def create_backup():
    """Create a backup of all data"""
    try:
        backup_id = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_dir = os.path.join('backups', backup_id)
        os.makedirs(backup_dir, exist_ok=True)
        
        # Backup database
        shutil.copy2(app.config['DATABASE'], os.path.join(backup_dir, 'database.db'))
        
        # Backup uploaded files
        upload_backup_dir = os.path.join(backup_dir, 'uploads')
        if os.path.exists(app.config['UPLOAD_FOLDER']):
            shutil.copytree(app.config['UPLOAD_FOLDER'], upload_backup_dir)
        
        # Create backup manifest
        manifest = {
            'backup_id': backup_id,
            'created_at': datetime.now().isoformat(),
            'database_size': os.path.getsize(app.config['DATABASE']),
            'files_count': len(os.listdir(app.config['UPLOAD_FOLDER'])) if os.path.exists(app.config['UPLOAD_FOLDER']) else 0
        }
        
        with open(os.path.join(backup_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        
        return jsonify({'success': True, 'backup_id': backup_id, 'manifest': manifest})
        
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/list', methods=['GET'])
def list_backups():
    """List all available backups"""
    try:
        backups = []
        backup_dir = 'backups'
        
        if os.path.exists(backup_dir):
            for backup_id in os.listdir(backup_dir):
                backup_path = os.path.join(backup_dir, backup_id)
                manifest_path = os.path.join(backup_path, 'manifest.json')
                
                if os.path.exists(manifest_path):
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                    backups.append(manifest)
        
        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x['created_at'], reverse=True)
        
        return jsonify({'success': True, 'backups': backups})
        
    except Exception as e:
        logger.error(f"Error listing backups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Real-time Notifications
@app.route('/api/notifications/subscribe', methods=['POST'])
def subscribe_notifications():
    """Subscribe to real-time notifications"""
    try:
        data = request.get_json()
        notification_type = data.get('type', 'all')
        
        # In a real implementation, this would use WebSockets or Server-Sent Events
        # For now, we'll return a subscription ID
        subscription_id = str(uuid.uuid4())
        
        return jsonify({
            'success': True, 
            'subscription_id': subscription_id,
            'message': f'Subscribed to {notification_type} notifications'
        })
        
    except Exception as e:
        logger.error(f"Error subscribing to notifications: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Performance Monitoring
@app.route('/api/performance/metrics', methods=['GET'])
def get_performance_metrics():
    """Get system performance metrics"""
    try:
        import psutil
        
        metrics = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/').percent,
            'active_connections': len(running_tasks),
            'database_size': os.path.getsize(app.config['DATABASE']) if os.path.exists(app.config['DATABASE']) else 0,
            'uptime': time.time() - app.start_time if hasattr(app, 'start_time') else 0
        }
        
        return jsonify({'success': True, 'metrics': metrics})
        
    except ImportError:
        return jsonify({'success': False, 'error': 'psutil not available'}), 500
    except Exception as e:
        logger.error(f"Error getting performance metrics: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Initialize app start time
app.start_time = time.time()

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file uploads with enhanced features"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file part'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            
            # Add timestamp to filename to avoid conflicts
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{name}_{timestamp}{ext}"
            
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Create data record for the uploaded file
            record = DataRecord(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                category='uploaded_file',
                tags=['file', 'upload'],
                content=f"Uploaded file: {filename}",
                metadata={
                    'filename': filename,
                    'original_name': file.filename,
                    'file_size': os.path.getsize(file_path),
                    'file_type': ext[1:] if ext else 'unknown'
                },
                status='active'
            )
            
            db_manager.add_record(record)
            
            return jsonify({
                'success': True,
                'filename': filename,
                'message': 'File uploaded successfully',
                'record_id': record.id
            })
        else:
            return jsonify({'success': False, 'error': 'File type not allowed'}), 400
            
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/files')
def get_uploaded_files():
    """Get list of uploaded files with enhanced metadata"""
    try:
        files = []
        upload_folder = app.config['UPLOAD_FOLDER']
        
        if os.path.exists(upload_folder):
            for filename in os.listdir(upload_folder):
                file_path = os.path.join(upload_folder, filename)
                if os.path.isfile(file_path):
                    stat = os.stat(file_path)
                    files.append({
                        'name': filename,
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        'type': mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                    })
        
        # Sort by modification time (newest first)
        files.sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify({'success': True, 'files': files})
        
    except Exception as e:
        logger.error(f"Error getting uploaded files: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat-history')
def get_chat_history():
    """Get chat history"""
    history = load_chat_history()
    return jsonify({'success': True, 'history': history})

@app.route('/api/stop-task', methods=['POST'])
def stop_task():
    """Stop a running task"""
    try:
        data = request.get_json()
        task_id = data.get('task_id')
        
        if task_id in running_tasks:
            # Signal the task to stop
            running_tasks[task_id]['stop_event'].set()
            del running_tasks[task_id]
            
            return jsonify({'success': True, 'message': 'Task stopped successfully'})
        else:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
            
    except Exception as e:
        logger.error(f"Error stopping task: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

async def main(prompt, task_id=None):
    """Enhanced main function with advanced API key rotation and stop functionality"""
    max_retries = len(api_key_manager.api_keys)
    retry_count = 0
    
    while retry_count < max_retries:
        # Get available API key
        result = api_key_manager.get_available_api_key(use_random=True)
        if not result:
            logger.error("No API keys available for request")
            
            # Wait for next available key
            max_wait_time = 10  # 5 minutes
            wait_time = 5
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                logger.info(f"Waiting {wait_time}s for API key availability...")
                await asyncio.sleep(wait_time)
                
                result = api_key_manager.get_available_api_key(use_random=True)
                if result:
                    break
                    
                wait_time = min(wait_time * 2, 60)  # Exponential backoff, max 60s
            
            if not result:
                raise Exception("No API keys became available within timeout period")
        
        api_key, key_config = result
        key_name = key_config['name']
        
        try:
            logger.info(f"Using API key: {key_name}")
            
            # Create Manus agent with advanced API key manager
            agent = await Manus.create(
                api_key_manager=api_key_manager,
                api_key=api_key
            )
            
            # Execute the task
            await agent.run(prompt)
            
            # Record successful request
            api_key_manager.record_successful_request(api_key)
            logger.info(f"Task completed successfully with key: {key_name}")
            break
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Handle different types of errors
            if any(keyword in error_str for keyword in ["rate limit", "quota", "too many requests"]):
                logger.warning(f"Rate limit error with key {key_name}: {e}")
                api_key_manager.record_rate_limit_error(api_key, key_name)
            elif any(keyword in error_str for keyword in ["authentication", "invalid api key", "unauthorized"]):
                logger.error(f"Authentication error with key {key_name}: {e}")
                api_key_manager.record_failure(api_key, key_name, "auth_error")
            elif any(keyword in error_str for keyword in ["timeout", "connection"]):
                logger.warning(f"Connection error with key {key_name}: {e}")
                api_key_manager.record_failure(api_key, key_name, "connection_error")
            else:
                logger.error(f"Unexpected error with key {key_name}: {e}")
                api_key_manager.record_failure(api_key, key_name, "unknown_error")
            
            retry_count += 1
            if retry_count >= max_retries:
                logger.error("All API keys exhausted, task failed")
                raise Exception(f"Task failed after trying all available API keys. Last error: {e}")
            
            logger.info(f"Retrying with different API key (attempt {retry_count + 1}/{max_retries})")
            
        finally:
            if 'agent' in locals():
                await agent.cleanup()

# Thread wrapper
def run_async_task(message, task_id=None):
    """Run async task in new thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main(message, task_id))
    except Exception as e:
        logger.error(f"Task execution failed: {e}")
    finally:
        loop.close()

@app.route('/api/chat-stream', methods=['POST'])
def chat_stream():
    """Enhanced streaming chat interface with stop functionality"""
    # Clear log file
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

    # Get request data
    prompt_data = request.get_json()
    message = prompt_data["message"]
    task_id = prompt_data.get("task_id", str(uuid.uuid4()))
    uploaded_files = prompt_data.get("uploaded_files", [])
    
    logger.info(f"Received request: {message}")
    
    # Process uploaded files if any
    file_context = ""
    if uploaded_files:
        file_context = "\n\nUploaded files context:\n"
        for file_info in uploaded_files:
            try:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()[:2000]  # Limit content size
                    file_context += f"\n--- {file_info['original_name']} ---\n{content}\n"
            except Exception as e:
                logger.error(f"Error reading file {file_info['filename']}: {e}")
    
    full_message = message + file_context
    
    # Initialize task tracking
    running_tasks[task_id] = {
        'stop_flag': False,
        'start_time': time.time()
    }

    # Start async task thread
    task_thread = threading.Thread(
        target=run_async_task,
        args=(full_message, task_id)
    )
    task_thread.start()

    # Streaming generator
    def generate():
        start_time = time.time()
        full_response = ""

        while task_thread.is_alive() or not log_queue.empty():
            # Check for stop signal
            if running_tasks.get(task_id, {}).get('stop_flag', False):
                yield "Task stopped by user.\n"
                break
                
            # Timeout check
            if time.time() - start_time > PROCESS_TIMEOUT:
                yield """0303030"""
                break
            
            new_content = ""
            try:
                new_content = log_queue.get(timeout=0.1)
            except queue.Empty:
                pass

            if new_content:
                full_response += new_content
                yield new_content

            # Pause when no new content
            if not new_content:
                time.sleep(FILE_CHECK_INTERVAL)

        # Save chat history
        chat_data = {
            'id': task_id,
            'timestamp': datetime.now().isoformat(),
            'user_message': message,
            'agent_response': full_response,
            'agent_type': 'manus',
            'uploaded_files': uploaded_files
        }
        save_chat_history(chat_data)
        
        # Clean up task tracking
        if task_id in running_tasks:
            del running_tasks[task_id]

        # Final confirmation
        yield """0303030"""

    return Response(generate(), mimetype="text/plain")

# Run flow async task
async def run_flow_task(prompt, task_id=None):
    """Enhanced run_flow function with advanced API key rotation and stop functionality"""
    from app.agent.data_analysis import DataAnalysis
    from app.flow.flow_factory import FlowFactory, FlowType
    
    max_retries = len(api_key_manager.api_keys)
    retry_count = 0
    
    while retry_count < max_retries:
        # Get available API key
        result = api_key_manager.get_available_api_key(use_random=True)
        if not result:
            logger.error("No API keys available for request")
            
            # Wait for next available key
            max_wait_time = 300  # 5 minutes
            wait_time = 5
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                logger.info(f"Waiting {wait_time}s for API key availability...")
                await asyncio.sleep(wait_time)
                
                result = api_key_manager.get_available_api_key(use_random=True)
                if result:
                    break
                    
                wait_time = min(wait_time * 2, 60)  # Exponential backoff, max 60s
            
            if not result:
                raise Exception("No API keys became available within timeout period")
        
        api_key, key_config = result
        key_name = key_config['name']
        
        try:
            logger.info(f"Using API key: {key_name}")
            
            # Create agents with advanced API key manager
            agents = {
                "manus": await Manus.create(
                    api_key_manager=api_key_manager,
                    api_key=api_key
                ),
            }
            
            if app_config.run_flow_config.use_data_analysis_agent:
                agents["data_analysis"] = DataAnalysis()
            
            # Create and execute flow
            flow = FlowFactory.create_flow(
                flow_type=FlowType.PLANNING,
                agents=agents,
            )
            
            logger.warning("Processing your request with flow...")
            
            try:
                start_time = time.time()
                result = await asyncio.wait_for(
                    flow.execute(prompt),
                    timeout=3600,  # 60 minute timeout for the entire execution
                )
                elapsed_time = time.time() - start_time
                logger.info(f"Request processed in {elapsed_time:.2f} seconds")
                logger.info(result)
                
                # Record successful request
                api_key_manager.record_successful_request(api_key)
                logger.info(f"Flow task completed successfully with key: {key_name}")
                break
                
            except asyncio.TimeoutError:
                logger.error("Request processing timed out after 1 hour")
                logger.info("Operation terminated due to timeout. Please try a simpler request.")
                break
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Handle different types of errors
            if any(keyword in error_str for keyword in ["rate limit", "quota", "too many requests"]):
                logger.warning(f"Rate limit error with key {key_name}: {e}")
                api_key_manager.record_rate_limit_error(api_key, key_name)
            elif any(keyword in error_str for keyword in ["authentication", "invalid api key", "unauthorized"]):
                logger.error(f"Authentication error with key {key_name}: {e}")
                api_key_manager.record_failure(api_key, key_name, "auth_error")
            elif any(keyword in error_str for keyword in ["timeout", "connection"]):
                logger.warning(f"Connection error with key {key_name}: {e}")
                api_key_manager.record_failure(api_key, key_name, "connection_error")
            else:
                logger.error(f"Unexpected error with key {key_name}: {e}")
                api_key_manager.record_failure(api_key, key_name, "unknown_error")
            
            retry_count += 1
            if retry_count >= max_retries:
                logger.error("All API keys exhausted, flow task failed")
                raise Exception(f"Flow task failed after trying all available API keys. Last error: {e}")
            
            logger.info(f"Retrying with different API key (attempt {retry_count + 1}/{max_retries})")
            
        finally:
            if 'agents' in locals():
                for agent in agents.values():
                    if hasattr(agent, 'cleanup'):
                        await agent.cleanup()

def run_flow_async_task(message, task_id=None):
    """Run flow async task in new thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_flow_task(message, task_id))
    except Exception as e:
        logger.error(f"Flow task execution failed: {e}")
    finally:
        loop.close()

@app.route('/api/flow-stream', methods=['POST'])
def flow_stream():
    """Enhanced Flow streaming interface with stop functionality"""
    # Clear log file
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

    # Get request data
    prompt_data = request.get_json()
    message = prompt_data["message"]
    task_id = prompt_data.get("task_id", str(uuid.uuid4()))
    uploaded_files = prompt_data.get("uploaded_files", [])
    
    logger.info(f"Received Flow request: {message}")
    
    # Process uploaded files if any
    file_context = ""
    if uploaded_files:
        file_context = "\n\nUploaded files context:\n"
        for file_info in uploaded_files:
            try:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()[:2000]  # Limit content size
                    file_context += f"\n--- {file_info['original_name']} ---\n{content}\n"
            except Exception as e:
                logger.error(f"Error reading file {file_info['filename']}: {e}")
    
    full_message = message + file_context
    
    # Initialize task tracking
    running_tasks[task_id] = {
        'stop_flag': False,
        'start_time': time.time()
    }

    # Start async task thread
    task_thread = threading.Thread(
        target=run_flow_async_task,
        args=(full_message, task_id)
    )
    task_thread.start()

    # Streaming generator
    def generate():
        start_time = time.time()
        full_response = ""

        while task_thread.is_alive() or not log_queue.empty():
            # Check for stop signal
            if running_tasks.get(task_id, {}).get('stop_flag', False):
                yield "Task stopped by user.\n"
                break
                
            # Timeout check
            if time.time() - start_time > PROCESS_TIMEOUT:
                yield """0303030"""
                break
            
            new_content = ""
            try:
                new_content = log_queue.get(timeout=0.1)
            except queue.Empty:
                pass

            if new_content:
                full_response += new_content
                yield new_content

            # Pause when no new content
            if not new_content:
                time.sleep(FILE_CHECK_INTERVAL)

        # Save chat history
        chat_data = {
            'id': task_id,
            'timestamp': datetime.now().isoformat(),
            'user_message': message,
            'agent_response': full_response,
            'agent_type': 'flow',
            'uploaded_files': uploaded_files
        }
        save_chat_history(chat_data)
        
        # Clean up task tracking
        if task_id in running_tasks:
            del running_tasks[task_id]

        # Final confirmation
        yield """0303030"""

    return Response(generate(), mimetype="text/plain")

# WSGI entry point for deployment
application = app

if __name__ == '__main__':
    # Log initial API key status
    logger.info("=== Initial API Key Status ===")
    for status in api_key_manager.get_keys_status():
        logger.info(f"Key {status['name']}: Available={status['available']}, "
                    f"Usage={status['usage']['requests_this_day']}/{status['limits']['max_per_day']} today")
    
    app.run(host='0.0.0.0', port=3000,  debug=False)
