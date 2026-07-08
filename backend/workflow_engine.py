"""
工作流状态机 + SQLite 持久化模块

管理工作流生命周期：创建、步骤推进、用户干预、文章保存。
SQLite 使用 Python 内置 sqlite3 模块，无需额外依赖。

表结构：
- workflows: 工作流主表（id, title, source, source_data, config, current_step, status, article_title, article_content, created_at, updated_at）
- workflow_steps: 步骤明细表（id, workflow_id, step_name, status, input, output, sub_tasks, created_at, updated_at）

6 步流程：parse(素材解析) → research(搜索调研) → topics(选题讨论) → outline(大纲确认) → generate(正文生成) → verify(检验输出)
其中 topics/outline/verify 涉及用户干预（waiting_user 状态）。
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime
from threading import Lock

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'workflows.db')

# 6 步定义（顺序即执行顺序）
STEPS = ['parse', 'research', 'topics', 'outline', 'generate', 'verify']

STEP_NAMES = {
    'parse': '素材解析',
    'research': '搜索调研',
    'topics': '选题讨论',
    'outline': '大纲确认',
    'generate': '正文生成',
    'verify': '检验输出',
}

# 需要用户干预的步骤
USER_INTERVENTION_STEPS = {'topics', 'outline', 'verify'}

# 工作流状态
WF_STATUS_RUNNING = 'running'
WF_STATUS_WAITING_USER = 'waiting_user'
WF_STATUS_COMPLETED = 'completed'
WF_STATUS_FAILED = 'failed'

# 步骤状态
STEP_STATUS_PENDING = 'pending'
STEP_STATUS_RUNNING = 'running'
STEP_STATUS_COMPLETED = 'completed'
STEP_STATUS_FAILED = 'failed'
STEP_STATUS_WAITING_USER = 'waiting_user'

_lock = Lock()
_conn = None


def _get_conn():
    """获取 SQLite 连接（线程安全：check_same_thread=False + 写操作加锁）"""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.execute('PRAGMA foreign_keys=ON')
    return _conn


def init_db():
    """初始化数据库表结构"""
    conn = _get_conn()
    with _lock:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                title TEXT,
                source TEXT,
                source_data TEXT,
                config TEXT,
                current_step TEXT,
                status TEXT,
                article_title TEXT,
                article_content TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS workflow_steps (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                step_name TEXT,
                status TEXT,
                input TEXT,
                output TEXT,
                sub_tasks TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_steps_wf ON workflow_steps(workflow_id)')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS saved_articles (
                id TEXT PRIMARY KEY,
                title TEXT,
                content TEXT,
                style TEXT,
                source TEXT,
                hotspot_title TEXT,
                word_count INTEGER,
                created_at TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_saved_articles_created ON saved_articles(created_at DESC)')
        conn.commit()


def _now():
    return datetime.now().isoformat()


def _uuid():
    return uuid.uuid4().hex


def _ensure_steps(wf_id):
    """确保工作流的所有步骤行存在（初始化为 pending）"""
    conn = _get_conn()
    with _lock:
        for step in STEPS:
            existing = conn.execute(
                'SELECT id FROM workflow_steps WHERE workflow_id=? AND step_name=?',
                (wf_id, step)
            ).fetchone()
            if not existing:
                conn.execute(
                    'INSERT INTO workflow_steps (id, workflow_id, step_name, status, input, output, sub_tasks, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (_uuid(), wf_id, step, STEP_STATUS_PENDING, None, None, '[]', _now(), _now())
                )
        conn.commit()


def create_workflow(source, source_data, config, title=None):
    """
    创建工作流。
    - source: 'hotspot' 或 'custom'
    - source_data: dict（热点数据或自定义主题文本）
    - config: dict（创作参数）
    - title: 工作流标题（可选，默认从 source_data 推断）
    返回 workflow_id
    """
    init_db()
    wf_id = _uuid()
    if not title:
        if source == 'hotspot':
            title = source_data.get('title', '未命名工作流')[:80]
        else:
            title = (source_data.get('topic', '') or '自定义创作')[:80]

    source_data_json = json.dumps(source_data, ensure_ascii=False)
    config_json = json.dumps(config, ensure_ascii=False)
    now = _now()

    conn = _get_conn()
    with _lock:
        conn.execute(
            'INSERT INTO workflows (id, title, source, source_data, config, current_step, status, article_title, article_content, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (wf_id, title, source, source_data_json, config_json, 'parse', WF_STATUS_RUNNING, None, None, now, now)
        )
        conn.commit()

    _ensure_steps(wf_id)
    return wf_id


def _row_to_workflow(row, include_steps=True):
    """将数据库行转为工作流字典"""
    if row is None:
        return None
    wf = {
        'id': row['id'],
        'title': row['title'],
        'source': row['source'],
        'source_data': json.loads(row['source_data']) if row['source_data'] else None,
        'config': json.loads(row['config']) if row['config'] else {},
        'current_step': row['current_step'],
        'status': row['status'],
        'article_title': row['article_title'],
        'article_content': row['article_content'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }
    if include_steps:
        steps = get_steps(row['id'])
        wf['steps'] = steps
        # 派生当前步骤索引和进度
        wf['current_step_index'] = STEPS.index(row['current_step']) if row['current_step'] in STEPS else 0
        wf['step_progress'] = {
            step: (steps[i]['status'] if i < len(steps) else STEP_STATUS_PENDING)
            for i, step in enumerate(STEPS)
        }
    return wf


def _step_row_to_dict(row):
    """步骤行转字典"""
    return {
        'step_name': row['step_name'],
        'status': row['status'],
        'input': json.loads(row['input']) if row['input'] else None,
        'output': json.loads(row['output']) if row['output'] else None,
        'sub_tasks': json.loads(row['sub_tasks']) if row['sub_tasks'] else [],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def get_steps(wf_id):
    """获取工作流的所有步骤（按 STEPS 顺序）"""
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            'SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY created_at ASC',
            (wf_id,)
        ).fetchall()
    # 按 STEPS 顺序排列
    by_name = {r['step_name']: r for r in rows}
    return [_step_row_to_dict(by_name[s]) for s in STEPS if s in by_name]


def get_step(wf_id, step_name):
    """获取单个步骤"""
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            'SELECT * FROM workflow_steps WHERE workflow_id=? AND step_name=?',
            (wf_id, step_name)
        ).fetchone()
    return _step_row_to_dict(row) if row else None


def get_workflow(wf_id):
    """获取工作流完整状态"""
    init_db()
    conn = _get_conn()
    with _lock:
        row = conn.execute('SELECT * FROM workflows WHERE id=?', (wf_id,)).fetchone()
    return _row_to_workflow(row)


def list_workflows(limit=20):
    """列出最近的工作流"""
    init_db()
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            'SELECT * FROM workflows ORDER BY updated_at DESC LIMIT ?',
            (limit,)
        ).fetchall()
    return [_row_to_workflow(r, include_steps=False) for r in rows]


def update_step(wf_id, step_name, status=None, output=None, input_data=None, sub_tasks=None):
    """
    更新步骤状态。
    - status: 新状态（可选）
    - output: 步骤输出 dict（可选，存为 JSON）
    - input_data: 步骤输入 dict（可选）
    - sub_tasks: 子任务列表 [{name, status}]（可选）
    """
    conn = _get_conn()
    now = _now()
    with _lock:
        row = conn.execute(
            'SELECT * FROM workflow_steps WHERE workflow_id=? AND step_name=?',
            (wf_id, step_name)
        ).fetchone()
        if row is None:
            # 不存在则创建
            conn.execute(
                'INSERT INTO workflow_steps (id, workflow_id, step_name, status, input, output, sub_tasks, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (_uuid(), wf_id, step_name, status or STEP_STATUS_PENDING,
                 json.dumps(input_data, ensure_ascii=False) if input_data else None,
                 json.dumps(output, ensure_ascii=False) if output else None,
                 json.dumps(sub_tasks, ensure_ascii=False) if sub_tasks else '[]', now, now)
            )
        else:
            sets = ['updated_at=?']
            params = [now]
            if status is not None:
                sets.append('status=?')
                params.append(status)
            if output is not None:
                sets.append('output=?')
                params.append(json.dumps(output, ensure_ascii=False))
            if input_data is not None:
                sets.append('input=?')
                params.append(json.dumps(input_data, ensure_ascii=False))
            if sub_tasks is not None:
                sets.append('sub_tasks=?')
                params.append(json.dumps(sub_tasks, ensure_ascii=False))
            params.append(wf_id)
            params.append(step_name)
            conn.execute(
                f"UPDATE workflow_steps SET {', '.join(sets)} WHERE workflow_id=? AND step_name=?",
                params
            )
        # 同步工作流 updated_at
        conn.execute('UPDATE workflows SET updated_at=? WHERE id=?', (now, wf_id))
        conn.commit()


def set_workflow_status(wf_id, status, current_step=None):
    """更新工作流状态和当前步骤"""
    conn = _get_conn()
    now = _now()
    with _lock:
        if current_step is not None:
            conn.execute(
                'UPDATE workflows SET status=?, current_step=?, updated_at=? WHERE id=?',
                (status, current_step, now, wf_id)
            )
        else:
            conn.execute(
                'UPDATE workflows SET status=?, updated_at=? WHERE id=?',
                (status, now, wf_id)
            )
        conn.commit()


def advance_step(wf_id):
    """
    推进到下一步。返回 (next_step, is_last)。
    若当前是最后一步，则标记工作流为 completed。
    """
    wf = get_workflow(wf_id)
    if wf is None:
        return None, True
    cur = wf['current_step']
    if cur not in STEPS:
        return None, True
    idx = STEPS.index(cur)
    if idx >= len(STEPS) - 1:
        set_workflow_status(wf_id, WF_STATUS_COMPLETED, current_step=cur)
        return None, True
    next_step = STEPS[idx + 1]
    # 判断下一步是否需要用户干预
    new_status = WF_STATUS_WAITING_USER if next_step in USER_INTERVENTION_STEPS and next_step != 'topics' else WF_STATUS_RUNNING
    # topics 需要先自动生成候选再等待用户选择，所以状态保持 running 直到候选生成
    set_workflow_status(wf_id, new_status, current_step=next_step)
    return next_step, False


def save_article(wf_id, title, content):
    """保存最终文章"""
    conn = _get_conn()
    now = _now()
    with _lock:
        conn.execute(
            'UPDATE workflows SET article_title=?, article_content=?, updated_at=? WHERE id=?',
            (title, content, now, wf_id)
        )
        conn.commit()


def reset_to_step(wf_id, target_step):
    """
    将工作流重置到指定步骤（含该步骤），清空该步骤及之后所有步骤的输出和状态。
    允许用户回到前置步骤重新选择（如重新选题、重新确认大纲等）。
    
    - target_step: 目标步骤名 ('parse', 'research', 'topics', 'outline', 'generate', 'verify')
    返回 (ok, message)
    """
    if target_step not in STEPS:
        return False, f'无效的步骤：{target_step}'
    
    target_idx = STEPS.index(target_step)
    conn = _get_conn()
    now = _now()
    
    with _lock:
        # 清空目标步骤及之后所有步骤的输出和状态
        for step_name in STEPS[target_idx:]:
            conn.execute(
                '''UPDATE workflow_steps 
                   SET status=?, output=NULL, input=NULL, sub_tasks='[]', updated_at=?
                   WHERE workflow_id=? AND step_name=?''',
                (STEP_STATUS_PENDING, now, wf_id, step_name)
            )
        
        # 重置工作流状态和当前步骤
        new_status = WF_STATUS_WAITING_USER if target_step in USER_INTERVENTION_STEPS else WF_STATUS_RUNNING
        conn.execute(
            'UPDATE workflows SET status=?, current_step=?, article_title=NULL, article_content=NULL, updated_at=? WHERE id=?',
            (new_status, target_step, now, wf_id)
        )
        conn.commit()
    
    return True, f'已重置到步骤：{STEP_NAMES.get(target_step, target_step)}'


def delete_workflow(wf_id):
    """删除工作流（级联删除步骤）"""
    conn = _get_conn()
    with _lock:
        conn.execute('DELETE FROM workflow_steps WHERE workflow_id=?', (wf_id,))
        conn.execute('DELETE FROM workflows WHERE id=?', (wf_id,))
        conn.commit()


# ============================================================
# 文章历史管理（saved_articles 表）
# ============================================================

def save_article_history(title, content, style='', source='', hotspot_title='', word_count=0):
    """保存一篇文章到历史记录"""
    aid = _uuid()
    conn = _get_conn()
    now = _now()
    with _lock:
        conn.execute(
            'INSERT INTO saved_articles (id, title, content, style, source, hotspot_title, word_count, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (aid, title, content, style, source, hotspot_title, word_count, now)
        )
        conn.commit()
    return aid


def get_saved_articles(limit=50, offset=0):
    """获取已保存的文章列表（按时间倒序）"""
    conn = _get_conn()
    rows = conn.execute(
        'SELECT id, title, style, source, hotspot_title, word_count, created_at '
        'FROM saved_articles ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (limit, offset)
    ).fetchall()
    return [dict(r) for r in rows]


def get_saved_article(aid):
    """获取单篇文章的完整内容"""
    conn = _get_conn()
    row = conn.execute('SELECT * FROM saved_articles WHERE id=?', (aid,)).fetchone()
    return dict(row) if row else None


def delete_saved_article(aid):
    """删除一篇已保存的文章"""
    conn = _get_conn()
    with _lock:
        conn.execute('DELETE FROM saved_articles WHERE id=?', (aid,))
        conn.commit()
    return True


# 初始化数据库（模块加载时执行）
try:
    init_db()
except Exception as e:
    # 数据库初始化失败不阻塞导入，首次调用时再尝试
    print(f"[workflow_engine] init_db warning: {e}")
