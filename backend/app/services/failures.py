"""Durable terminal failures, centralized classification and atomic alert admission.

Only identifiers and sanitized exception diagnostics are stored. File contents,
request/session data and task argument payloads are never captured.
"""
from __future__ import annotations

import hashlib
import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.config import settings
from app.database.connection import sync_session

logger = logging.getLogger(__name__)


def scrub(value: object, limit: int = 8192) -> str:
    value = str(value)
    # Redact configured credentials too, including values in free-form errors.
    for field in type(settings).model_fields:
        if any(word in field.lower() for word in ('password', 'secret', 'api_key', 'token')):
            secret = getattr(settings, field, None)
            if isinstance(secret, str) and secret:
                value = value.replace(secret, '[redacted]')
    value = re.sub(r'(?im)(authorization\s*[:=]\s*)[^\r\n]+', r'\1[redacted]', value)
    value = re.sub(r'(?i)([\w-]*(?:api[_-]?key|password|secret|token|session|cookie)[\w-]*[\s\"\']*[:=][\s\"\']*)[^\s,;\"\'&]+', r'\1[redacted]', value)
    value = re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s,;]+', r'\1 [redacted]', value)
    value = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', value)
    value = re.sub(r'(?i)\b(?:sk-[\w-]+|AIza[\w-]+|gh[pousr]_[\w]+)\b', '[redacted]', value)
    # Drop query strings from diagnostics; signed URLs often carry credentials.
    value = re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[redacted]', value)
    return value.encode('utf-8')[:limit].decode('utf-8', errors='ignore')


def classify_failure(exc: BaseException) -> tuple[str, str]:
    """Prefer typed HTTP status (including causes), then specific input defects."""
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        response = getattr(cause, 'response', None)
        status = getattr(response, 'status_code', None) or getattr(cause, 'status_code', None)
        if status:
            if 400 <= status < 500 and status not in (408, 429):
                return 'user_input', 'That link could not be opened. Check the address and access permissions.'
            return 'system', 'Processing is temporarily unavailable. Please try again later.'
        cause = cause.__cause__ or cause.__context__
    message = str(exc).lower()
    defects = [
        (('encrypted', 'password protected', 'password-protected'), 'This PDF is encrypted. Upload an unlocked copy.'),
        (('corrupt pdf', 'broken document', 'cannot open broken', 'invalid pdf', 'filedataerror'), 'This PDF could not be read. Try downloading a fresh copy.'),
        (('zero pages', 'no pages', 'page count is 0', 'empty pdf'), 'This PDF has no pages. Upload a document with readable pages.'),
        (('empty text', 'no readable text', 'no chunks extracted', 'no text extracted'), 'No readable text was found in this document.'),
        (('unsupported type', 'unsupported file', 'only pdf', 'no pdf header'), 'This file type is not supported. Upload a PDF.'),
        (('too large', 'size limit', 'maximum allowed size'), 'This file or page is too large. Try a smaller document.'),
    ]
    for patterns, public in defects:
        if any(pattern in message or pattern in type(exc).__name__.lower() for pattern in patterns):
            return 'user_input', public
    return 'system', 'Processing failed. Your document is preserved; please try again later.'


def fingerprint(task_name: str, error_type: str, message: str) -> str:
    normalized = scrub(message)
    normalized = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b', '<id>', normalized)
    normalized = re.sub(r'(?:/[\w.~-]+)+', '<path>', normalized)
    normalized = re.sub(r'\b\d+(?:\.\d+)?\b', '<number>', normalized)
    return hashlib.sha256(f'{task_name}|{error_type}|{normalized}'.encode()).hexdigest()


def queue_alert(row_id):
    # The failure process publishes only an ID; it never sends SMTP itself.
    try:
        from app.workers.reliability import send_failure_alert
        send_failure_alert.apply_async(args=[str(row_id)], queue='celery')
    except Exception as exc:
        logger.warning('Failure alert could not be queued: %s', type(exc).__name__)


def record_failure(task_name, task_id, exc, *, document_id=None, job_id=None,
                   traceback='', category=None):
    error_type = type(exc).__name__
    classified, public = classify_failure(exc)
    category = category or classified
    message = scrub(exc, 2048)
    key = f'{task_name}:{job_id or task_id}'
    params = dict(task=task_name, delivery=task_id, doc=document_id, job=job_id,
                  category=category, error_type=error_type, message=message,
                  trace=scrub(traceback), fingerprint=fingerprint(task_name, error_type, message), key=key)
    with sync_session() as session:
        # Missing/deleted documents stay nullable. Never overwrite a newer job.
        doc = session.execute(text('SELECT user_id FROM documents WHERE id=:id'), {'id':document_id}).mappings().first() if document_id else None
        params['user'] = doc['user_id'] if doc else None
        if not doc:
            params['doc'] = None
        row = session.execute(text('''
            INSERT INTO failed_jobs(task_name,celery_task_id,document_id,user_id,job_id,
                category,error_type,error_message,traceback,fingerprint,logical_key)
            VALUES (:task,:delivery,:doc,:user,:job,:category,:error_type,:message,:trace,:fingerprint,:key)
            ON CONFLICT (logical_key) DO UPDATE SET attempts=failed_jobs.attempts+1,
                last_failed_at=clock_timestamp(),status='open',error_message=EXCLUDED.error_message,
                traceback=EXCLUDED.traceback,category=EXCLUDED.category,error_type=EXCLUDED.error_type,
                fingerprint=EXCLUDED.fingerprint,celery_task_id=EXCLUDED.celery_task_id
            RETURNING *
        '''), params).mappings().one()
        session.execute(text('INSERT INTO failure_events(failed_job_id,fingerprint,category) VALUES (:id,:fp,:category)'), {'id':row['id'],'fp':row['fingerprint'],'category':category})
        if doc and (job_id or task_name in ('9xaipal.embed_document',)):
            current = session.execute(text('SELECT id FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1'), {'doc':document_id}).scalar()
            if job_id is None or str(current) == str(job_id):
                if category == 'stalled':
                    public = 'Processing stopped responding. Your document is preserved; please try again.'
                session.execute(text("UPDATE documents SET status='failed',error_message=:message,updated_at=now() WHERE id=:doc"), {'doc':document_id,'message':public})
                session.execute(text("UPDATE ingestion_jobs SET status='failed',error_message=:message,error_code=:code,completed_at=now() WHERE id=:job"), {'job':current,'message':public,'code':f'{category}_failure'})
        session.commit()
        result = dict(row)
    # Include input failures in summary counts, but no immediate mail.
    if category != 'user_input':
        queue_alert(result['id'])
    return result


_ALERT_LUA = '''
if redis.call('EXISTS',KEYS[1]) == 1 then
    redis.call('INCR',KEYS[3]); redis.call('EXPIRE',KEYS[3],172800); return 0
end
local count=tonumber(redis.call('GET',KEYS[2]) or '0')
if count >= tonumber(ARGV[2]) then
    redis.call('INCR',KEYS[3]); redis.call('EXPIRE',KEYS[3],172800); return 0
end
redis.call('SET',KEYS[1],'1','NX','EX',ARGV[1])
redis.call('INCR',KEYS[2]); redis.call('EXPIRE',KEYS[2],172800)
return 1
'''


def alert_client():
    import redis
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)


def admit_alert(client, fp: str) -> bool:
    day = datetime.now(ZoneInfo('Asia/Amman')).date().isoformat()
    return bool(client.eval(_ALERT_LUA, 3, f'q1:alert:repeat:{fp}', f'q1:alert:cap:{day}',
                            f'q1:alert:suppressed:{fp}', max(1, settings.alert_repeat_window_minutes * 60),
                            settings.alert_max_emails_per_day))


def send_email(subject, body) -> bool:
    if not (settings.smtp_host and settings.alert_email_to and settings.alert_email_from):
        logger.info('Owner email disabled; failure details are in failed_jobs')
        return False
    message = EmailMessage()
    message['Subject'] = scrub(subject, 300).replace('\n', ' ').replace('\r', ' ')
    message['From'] = settings.alert_email_from
    message['To'] = settings.alert_email_to
    message.set_content(scrub(body, 65536))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
    return True


def send_row_alert(row_id):
    with sync_session() as session:
        row = session.execute(text('SELECT f.*,d.original_filename FROM failed_jobs f LEFT JOIN documents d ON d.id=f.document_id WHERE f.id=:id'), {'id':row_id}).mappings().first()
        if not row or row['category'] == 'user_input' or row['status'] != 'open':
            return
        client = alert_client()
        if not admit_alert(client, row['fingerprint']):
            return
        suppressed = client.get(f"q1:alert:suppressed:{row['fingerprint']}") or 0
        body = '\n'.join(f'{key}: {row[key]}' for key in ('task_name','last_failed_at','document_id','original_filename','user_id','error_message','traceback','attempts'))
        body += f"\nSuppressed repeats: {suppressed}\nRetry: python backend/scripts/dlq.py retry {row['id']}\n"
        if send_email(f"[9XAIPal] {row['category']}: {row['error_type']} in {row['task_name']}", body):
            session.execute(text('UPDATE failed_jobs SET notified_at=now() WHERE id=:id'), {'id':row_id})
            session.commit()


def daily_summary():
    now = datetime.now(ZoneInfo('Asia/Amman'))
    # Window ends at today's 08:00, preventing drift and gaps after late runs.
    end = now.replace(hour=8, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    if now < end:
        end -= timedelta(days=1)
    start = end - timedelta(days=1)
    with sync_session() as session:
        counts = session.execute(text('''SELECT fingerprint,category,COUNT(*) AS count
            FROM failure_events WHERE failed_at>=:start AND failed_at<:end
            GROUP BY fingerprint,category'''), {'start':start,'end':end}).mappings().all()
        rows = session.execute(text("SELECT id,task_name,category,error_type,attempts FROM failed_jobs WHERE status='open' ORDER BY first_failed_at")).mappings().all()
        if not counts and not rows:
            return
        client = alert_client()
        if not client.set(f'q1:summary:{end.date()}', '1', nx=True, ex=172800):
            return
        try:
            body = 'Failure counts by fingerprint/category:\n' + '\n'.join(str(dict(row)) for row in counts)
            body += '\nOpen DLQ rows:\n' + '\n'.join(str(dict(row)) for row in rows)
            send_email('[9XAIPal] Daily queue failure summary', body)
        except Exception:
            client.delete(f'q1:summary:{end.date()}')
            raise


def sweep_stalled(*, active_ids: set[str], inspection_ok: bool) -> int:
    if not inspection_ok:
        return 0
    marked = 0
    with sync_session() as session:
        rows = session.execute(text('''SELECT j.*,d.status AS doc_status FROM ingestion_jobs j
            JOIN documents d ON d.id=j.document_id
            WHERE j.status NOT IN ('complete','failed') AND d.status NOT IN ('complete','failed')
            AND j.id=(SELECT id FROM ingestion_jobs WHERE document_id=d.id ORDER BY created_at DESC,id DESC LIMIT 1)
            AND COALESCE(j.progress_updated_at,j.created_at)<clock_timestamp()-make_interval(mins => :minutes)
            FOR UPDATE OF j SKIP LOCKED'''), {'minutes':settings.stalled_job_minutes}).mappings().all()
        for row in rows:
            age = (datetime.now(timezone.utc) - (row['progress_updated_at'] or row['created_at'])).total_seconds()
            task_id = row['execution_task_id'] or row.get('celery_task_id')
            if task_id in active_ids and age < settings.stalled_job_minutes * 180:
                continue
            # Commit the guarded state transition before opening the DLQ session.
            session.execute(text("UPDATE ingestion_jobs SET status='failed',error_message='Processing stopped responding.',error_code='stalled_failure',completed_at=now() WHERE id=:id"), {'id':row['id']})
            session.execute(text("UPDATE documents SET status='failed',error_message='Processing stopped responding. Your document is preserved; please try again.',updated_at=now() WHERE id=:id"), {'id':row['document_id']})
            session.commit()
            record_failure('9xaipal.process_article_ingestion' if session.execute(text('SELECT doc_kind FROM documents WHERE id=:id'), {'id':row['document_id']}).scalar() == 'article' else '9xaipal.process_ingestion',
                           task_id or f"stalled:{row['id']}", RuntimeError('Processing stopped responding.'),
                           document_id=row['document_id'], job_id=row['id'], category='stalled')
            marked += 1
    return marked
