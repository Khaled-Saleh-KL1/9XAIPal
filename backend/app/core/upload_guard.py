"""Refuse uploads before Starlette reads or spools the multipart body."""
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.errors import (UploadAdmissionError,TooManyQueuedJobs,InsufficientStorage,
                            NotAdmitted,UPLOAD_ADMISSION_MESSAGES)
from app.core.config import settings


async def admit_scope(scope):
    from app.api.deps import get_current_user
    from app.database.connection import async_session_factory
    from app.services.upload_admission import reserve_upload, check_broker
    await check_broker()  # Auth's Redis dependency can fail before user resolution.
    async with async_session_factory() as db:
        user = await get_current_user(Request(scope), db)
        token = await reserve_upload(db,user['id'],is_url=scope['path'].endswith('/import-url'), idem_key=Request(scope).headers.get('Idempotency-Key'))
        scope.setdefault('state', {})['upload_reservation'] = token
        return token


class UploadGuardMiddleware:
    def __init__(self,app,admission=admit_scope):
        self.app,self.admission=app,admission

    async def __call__(self,scope,receive,send):
        protected = scope['type']=='http' and scope['method']=='POST' and scope['path'] in {
            f'/api/v1/{prefix}/{path}' for prefix in ('papers','documents') for path in ('upload','import-url')}
        if not protected:
            return await self.app(scope,receive,send)
        try:
            token = await self.admission(scope)
        except TooManyQueuedJobs as exc:
            from app.services.upload_admission import retry_after
            error=UploadAdmissionError('queue_full',429,retry_after(exc.current))
        except InsufficientStorage:
            error=UploadAdmissionError('storage_full',503,600)
        except UploadAdmissionError as exc:
            error=exc
        except HTTPException as exc:
            return await JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)(scope,receive,send)
        except NotAdmitted as exc:
            return await JSONResponse({'code':'NOT_ADMITTED','queue_position':exc.queue_position,'detail':'The site is at capacity right now. Please wait.'},status_code=423)(scope,receive,send)
        else:
            error=None
        if error:
            return await JSONResponse({'code':error.code,'message':UPLOAD_ADMISSION_MESSAGES[error.code]},status_code=error.status_code,headers={'Retry-After':str(error.retry_after)})(scope,receive,send)
        try:
            # Bound both PDF envelopes and small URL JSON requests.
            cap = settings.max_upload_size_mb*1024*1024+65536 if scope['path'].endswith('/upload') else 65536
            headers = dict(scope['headers'])
            try:
                too_large = int(headers.get(b'content-length',b'0')) > cap
            except ValueError:
                too_large = False
            if too_large:
                return await JSONResponse({'detail':'File too large. Please upload a smaller document.'},status_code=413)(scope,receive,send)
            total = 0
            upstream = receive
            async def limited_receive():
                nonlocal total
                from app.services.upload_admission import verify_upload
                await verify_upload(token)
                message = await upstream()
                # The receive await can outlast a lease. Verify again before
                # handing bytes to the multipart parser's spool.
                await verify_upload(token)
                total += len(message.get('body',b''))
                if total > cap:
                    raise HTTPException(413,'File too large. Please upload a smaller document.')
                return message
            return await self.app(scope,limited_receive,send)
        finally:
            if token:
                from app.services.upload_admission import release_upload
                await release_upload(token)
