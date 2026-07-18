from datetime import datetime
import json

from database import get_db_connection

def save_checkpoint(session_id,user_id,current_question_index,answered_questions):

    answered_questions_str = json.dumps(answered_questions)
    current_TimeStamp = datetime.now().isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()
    with open('insert_table.sql', 'r') as f:
        insert_table_queries = f.read()
    cursor.execute('''
        INSERT OR REPLACE INTO checkpoint
        (session_id, user_id, current_question_index, answered_questions, last_updated)
        VALUES (?, ?, ?, ?, ?)
    ''', (session_id, user_id, current_question_index, answered_questions_str, current_TimeStamp))
    conn.commit()
    conn.close()

def load_checkpoint(session_id, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM checkpoint
        WHERE session_id = ? AND user_id = ?
    ''', (session_id, user_id))
    fetched_row = cursor.fetchone()
    conn.close()

    if fetched_row is None:
        return None

    return {
        'current_question_index': fetched_row[2],
        'answered_questions': json.loads(fetched_row[3])
    }

def checkpoint_exists(session_id, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''select 1 from checkpoint
        where session_id=? and user_id=?'''
        ,(session_id,user_id)
    )
    fetched_row=cursor.fetchone()
    conn.close()
    
    if fetched_row is None:
        return False
    else:
        return True
        

def delete_checkpoint(session_id):
    conn = get_db_connection()
    cursor= conn.cursor()
    cursor.execute(
        '''delete from checkpoint where session_id=?''',
        (session_id,)
    )
    conn.commit()
    conn.close()