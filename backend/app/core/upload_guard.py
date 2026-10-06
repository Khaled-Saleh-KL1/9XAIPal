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
    from app.services.upload_admission import check_upload_admission, check_broker
    await check_broker()  # Auth's Redis dependency can fail before user resolution.
    async with async_session_factory() as db:
        user = await get_current_user(Request(scope), db)
        await check_upload_admission(db,user['id'])


class UploadGuardMiddleware:
    def __init__(self,app,admission=admit_scope):
        self.app,self.admission=app,admission

    async def __call__(self,scope,receive,send):
        protected = scope['type']=='http' and scope['method']=='POST' and scope['path'] in {
            f'/api/v1/{prefix}/{path}' for prefix in ('papers','documents') for path in ('upload','import-url')}
        if not protected:
            return await self.app(scope,receive,send)
        try:
            await self.admission(scope)
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
        # Bound multipart envelope storage too. The endpoint enforces the exact
        # file-byte cap; 64 KiB permits small normal form/header overhead.
        if scope['path'].endswith('/upload'):
            cap=settings.max_upload_size_mb*1024*1024+65536
            headers=dict(scope['headers'])
            try:
                too_large=int(headers.get(b'content-length',b'0'))>cap
            except ValueError:
                too_large=False
            if too_large:
                return await JSONResponse({'detail':'File too large. Please upload a smaller PDF.'},status_code=413)(scope,receive,send)
            total=0
            upstream=receive
            async def limited_receive():
                nonlocal total
                message=await upstream()
                total+=len(message.get('body',b''))
                if total>cap:
                    raise HTTPException(413,'File too large. Please upload a smaller PDF.')
                return message
            receive=limited_receive
        return await self.app(scope,receive,send)
