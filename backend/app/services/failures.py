"""Durable terminal failures, centralized classification and atomic alert admission.

Only identifiers and sanitized exception diagnostics are stored. File contents,
request/session data and task argument payloads are never captured.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
import logging
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.database.connection import sync_session

logger = logging.getLogger(__name__)


def scrub(value: object, limit: int = 8192) -> str:
    # SQLAlchemy diagnostics can include bound chunk text and entire failing
    # rows. Omit SQL/parameters, including when Celery serialized the error.
    if isinstance(value, SQLAlchemyError):
        value = f'{type(value).__name__}: database operation failed; query diagnostics omitted.'
    value = str(value)
    if '[SQL:' in value or '[parameters:' in value or 'Failing row contains' in value:
        value = 'Database operation failed; query diagnostics omitted.'
    # Redact configured credentials too, including values in free-form errors.
    for field in type(settings).model_fields:
        if any(word in field.lower() for word in ('password', 'secret', 'api_key', 'token')):
            secret = getattr(settings, field, None)
            if isinstance(secret, str) and secret:
                value = value.replace(secret, '[redacted]')
    value = re.sub(r'(?im)(authorization\s*[:=]\s*)[^\r\n]+', r'\1[redacted]', value)
    value = re.sub(r'(?i)([\w-]*(?:api[_-]?key|password|secret|token|session|cookie)[\w-]*[\s\"\']*[:=][\s\"\']*)[^\s,;\"\'&]+', r'\1[redacted]', value)
    value = re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s,;]+', r'\1 [redacted]', value)
    value = re.sub(r'([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@', r'\1[redacted]@', value)
    value = re.sub(r'(?i)\b(?:sk-[\w-]+|AIza[\w-]+|gh[pousr]_[\w]+)\b', '[redacted]', value)
    # Drop query strings from diagnostics; signed URLs often carry credentials.
    value = re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[redacted]', value)
    return value.encode('utf-8')[:limit].decode('utf-8', errors='ignore')


def classify_failure(exc: BaseException, *, task_name=None) -> tuple[str, str]:
    """Prefer typed HTTP status (including causes), then specific input defects."""
    if isinstance(exc, (SQLAlchemyError, MemoryError)):
        return 'system', 'Processing is temporarily unavailable. Please try again later.'
    # Provider/configuration errors retain HTTP causes but are our fault.
    if any(name in cls.__name__.lower() for cls in type(exc).__mro__ for name in ('modelunavailable', 'classifierunavailable')):
        return 'system', getattr(exc,'public_message','Processing is temporarily unavailable. Please try again later.')
    if getattr(exc,'error_code',None) in ('arabic_style_confirmation_required','handwritten_arabic_unavailable','arabic_gemini_pro_not_configured'):
        return 'user_input', exc.public_message
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        response = getattr(cause, 'response', None)
        status = getattr(response, 'status_code', None) or getattr(cause, 'status_code', None)
        if status:
            if task_name is not None and task_name != '9xaipal.process_article_ingestion':
                return 'system', 'Processing is temporarily unavailable. Please try again later.'
            if status == 413:
                return 'user_input', 'This file or page is too large. Try a smaller document.'
            if status == 415:
                return 'user_input', 'This file type is not supported. Upload a PDF.'
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


def fingerprint(task_name: str, error_type: str, message: object) -> str:
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
                   traceback='', category=None, session=None, notify=True, execution_generation=None):
    error_type = type(exc).__name__
    classified, public = classify_failure(exc, task_name=task_name)
    category = category or classified
    message = scrub(public if category == 'user_input' else exc, 2048)
    public = getattr(exc,'public_message',public)
    is_ingestion = task_name in ('9xaipal.process_ingestion','9xaipal.process_article_ingestion')
    key = f'{task_name}:{job_id if is_ingestion and job_id else document_id or task_id}'
    params = dict(task=task_name, delivery=task_id, doc=document_id, job=job_id,
                  category=category, error_type=error_type, message=message,
                  trace=scrub(traceback), fingerprint=fingerprint(task_name, error_type, message), key=key)
    owns_session = session is None
    with (sync_session() if owns_session else nullcontext(session)) as session:
        # Missing/deleted documents stay nullable. Never overwrite a newer job.
        doc = session.execute(text('SELECT user_id,status FROM documents WHERE id=:id'), {'id':document_id}).mappings().first() if document_id else None
        params['user'] = doc['user_id'] if doc else None
        if not doc:
            params['doc'] = None
        current = session.execute(text('SELECT id,execution_generation,error_code FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1'), {'doc':document_id}).mappings().first() if doc else None
        if job_id and not session.execute(text('SELECT id FROM ingestion_jobs WHERE id=:id'), {'id':job_id}).first():
            params['job'] = None
        # Celery can replace a zero-argument domain exception with its
        # pickleable base. Recover only known typed errors persisted by the
        # pipeline for this exact delivery generation; never infer from text.
        from app.extraction import arabic_types
        if type(exc) is arabic_types.ArabicRoutingError and current and str(current['id']) == str(job_id) and (execution_generation is None or current['execution_generation'] == execution_generation):
            known = {cls.error_code:cls for cls in (
                arabic_types.ArabicStyleConfirmationRequired,
                arabic_types.HandwrittenArabicUnavailable,
                arabic_types.ArabicGeminiProNotConfigured,
                arabic_types.ArabicClassifierUnavailableError,
            )}
            if current['error_code'] in known:
                exc = known[current['error_code']]()
                category, public = classify_failure(exc, task_name=task_name)
                params.update(category=category, error_type=type(exc).__name__,
                              message=scrub(public if category == 'user_input' else exc, 2048))
        params['fingerprint'] = fingerprint(task_name, params['error_type'], exc)
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
        if doc and (is_ingestion or doc['status'] not in ('complete','failed')):
            if current and (job_id is None or str(current['id']) == str(job_id)) and (execution_generation is None or current['execution_generation']==execution_generation):
                if category == 'stalled':
                    public = 'Processing stopped responding. Your document is preserved; please try again.'
                session.execute(text("UPDATE documents SET status='failed',error_message=:message,updated_at=now() WHERE id=:doc"), {'doc':document_id,'message':public})
                session.execute(text("UPDATE ingestion_jobs SET status='failed',error_message=:message,error_code=:code,completed_at=now() WHERE id=:job"), {'job':current['id'],'message':public,'code':getattr(exc,'error_code',f'{category}_failure')})
        if owns_session:
            session.commit()
        result = dict(row)
    # Include input failures in summary counts, but no immediate mail.
    if notify and category != 'user_input' and task_name not in ('9xaipal.send_failure_alert', '9xaipal.daily_failure_summary'):
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
    # The cap lasts until the daily 08:00 summary boundary, including the
    # hours after midnight. A calendar-day key would reopen it too early.
    day = (datetime.now(ZoneInfo('Asia/Amman')) - timedelta(hours=8)).date().isoformat()
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


def sweep_stalled(*, active_ids: set[str], inspection_ok: bool, pending_document_ids=None) -> int:
    if not inspection_ok:
        return 0
    pending_document_ids = pending_document_ids or set()
    # Queue waiting is not work inactivity. Snapshot only IDs, then recheck
    # heartbeat/current-job/status under each candidate's own transaction.
    predicate = """j.status IN ('extracting','chunking','embedding','summarizing')
        AND d.status NOT IN ('complete','failed')
        AND j.id=(SELECT id FROM ingestion_jobs WHERE document_id=d.id ORDER BY created_at DESC,id DESC LIMIT 1)
        AND COALESCE(j.progress_updated_at,j.started_at,j.created_at)<clock_timestamp()-make_interval(mins => :minutes)"""
    with sync_session() as session:
        candidates = session.execute(text(f'SELECT j.id FROM ingestion_jobs j JOIN documents d ON d.id=j.document_id WHERE {predicate}'), {'minutes':settings.stalled_job_minutes}).scalars().all()
    marked = 0
    for job_id in candidates:
        with sync_session() as session:
            row = session.execute(text(f"""SELECT j.*,d.doc_kind FROM ingestion_jobs j
                JOIN documents d ON d.id=j.document_id WHERE j.id=:id AND {predicate}
                FOR UPDATE OF j SKIP LOCKED"""), {'id':job_id,'minutes':settings.stalled_job_minutes}).mappings().first()
            if not row or str(row['document_id']) in pending_document_ids:
                continue
            heartbeat = row['progress_updated_at'] or row['started_at'] or row['created_at']
            age = (datetime.now(timezone.utc)-heartbeat).total_seconds()
            task_id = row['execution_task_id'] or row['celery_task_id']
            active = task_id in active_ids or f"document:{row['document_id']}" in active_ids
            if active and age < settings.stalled_job_minutes*180:
                continue
            failure = record_failure('9xaipal.process_article_ingestion' if row['doc_kind']=='article' else '9xaipal.process_ingestion',
                task_id or f"stalled:{row['id']}",RuntimeError('Processing stopped responding.'),
                document_id=row['document_id'],job_id=row['id'],category='stalled',
                session=session,notify=False,execution_generation=row['execution_generation'])
            # State transition and DLQ/event insertion commit together.
            session.commit()
        queue_alert(failure['id'])
        marked += 1
    return marked
