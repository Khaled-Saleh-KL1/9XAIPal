"""Exception diagnostics must never retain SQL parameter/document payloads."""
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.services.failures import classify_failure, fingerprint, scrub


@pytest.mark.parametrize('error_class',[IntegrityError, OperationalError])
def test_database_exception_payload_is_not_a_diagnostic(error_class):
    first=error_class('INSERT INTO chunks(content) VALUES (:content)',
                      {'content':'first document text'},
                      RuntimeError('Failing row contains encrypted PDF first document text'))
    second=error_class('INSERT INTO chunks(content) VALUES (:content)',
                       {'content':'other document text'},
                       RuntimeError('Failing row contains other document text'))
    assert 'document text' not in scrub(first)
    assert 'document text' not in scrub(str(first))
    assert 'INSERT INTO' not in scrub(first)
    assert error_class.__name__ in scrub(first)
    assert classify_failure(first)[0]=='system'
    assert fingerprint('task',error_class.__name__,first)==fingerprint('task',error_class.__name__,second)
