"""회사 프록시 환경에서도 OS 신뢰 저장소를 사용하는 HTTPS 설정."""

from __future__ import annotations


def enable_system_trust_store() -> bool:
    """requests/urllib3가 운영체제의 신뢰된 루트 인증서를 사용하게 한다."""
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except (ImportError, RuntimeError):
        return False
