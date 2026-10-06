import os
import subprocess
import sys
from pathlib import Path

import pytest

@pytest.mark.parametrize('script',['dlq.py','backfill_content_hash.py'])
def test_cli_help(script):
    result = subprocess.run([sys.executable,str(Path('scripts')/script),'--help'],capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout


def test_beat_configuration_and_singleton():
    from app.core.celery_app import celery_app
    assert celery_app.conf.worker_prefetch_multiplier == 1
    schedules=celery_app.conf.beat_schedule
    assert schedules['q1-stalled-jobs']['task']=='9xaipal.sweep_stalled_jobs'
    assert schedules['q1-daily-summary']['task']=='9xaipal.daily_failure_summary'
    assert celery_app.conf.timezone == 'Asia/Amman'
    for filename in ('docker-compose.yml','docker-compose.prod.yml'):
        value=Path(filename).read_text().split('  celery_worker_light:',1)[1].split('\n  #',1)[0]
        assert '-B --schedule /tmp/celerybeat-schedule' in value
        assert 'replicas: 1' in value


async def test_concurrent_migrations():
    import asyncio
    from app.database.migrations import apply_migrations
    from app.database.connection import async_session_factory
    from sqlalchemy import text
    await asyncio.gather(*(apply_migrations() for _ in range(3)))
    async with async_session_factory() as db:
        assert await db.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='failed_jobs'"))>=17
