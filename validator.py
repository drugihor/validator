# validator.py
import smtplib
import socket
import time
import random
import logging
from dataclasses import dataclass
from typing import List, Optional

logging.basicConfig(
    filename='logs/validator.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@dataclass
class Proxy:
    host: str
    port: int
    username: str = ""
    password: str = ""

@dataclass
class ValidationResult:
    email: str
    status: str
    method: str
    proxy: str = ""
    details: str = ""
    processing_time: float = 0.0

class EmailValidator:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.disposable_domains = {
            '10minutemail.com', 'tempmail.org', 'guerrillamail.com',
            'mailinator.com', 'yopmail.com', 'temp-mail.org',
            'throwaway.email', 'maildrop.cc', 'getnada.com'
        }

    def validate_smtp(self, email: str, password: str, proxy: Optional[Proxy] = None) -> ValidationResult:
        start = time.time()
        if '@' not in email:
            return ValidationResult(email, "invalid", "syntax", details="invalid_format", processing_time=time.time()-start)

        local, domain = email.rsplit('@', 1)

        # Синтаксис
        if not self._is_valid_syntax(email):
            return ValidationResult(email, "invalid", "syntax", details="invalid_syntax", processing_time=time.time()-start)

        # Disposable
        if domain in self.disposable_domains:
            return ValidationResult(email, "invalid", "disposable", details="disposable_domain", processing_time=time.time()-start)

        # Прокси
        proxy_str = f"{proxy.host}:{proxy.port}" if proxy else "no_proxy"

        # Проверка через SMTP (25 и 587)
        for port in [25, 587]:
            try:
                server = smtplib.SMTP(domain, port, timeout=self.timeout)
                server.ehlo()

                if port == 587:
                    server.starttls()
                    server.ehlo()

                server.mail('check@example.com')
                code, msg = server.rcpt(email)
                server.quit()

                if code == 250:
                    return ValidationResult(email, "valid", "smtp", proxy_str, f"port_{port}", time.time()-start)
                elif code in (550, 551, 553):
                    return ValidationResult(email, "invalid", "smtp", proxy_str, f"rejected_{code}", processing_time=time.time()-start)

            except smtplib.SMTPConnectError as e:
                logging.warning(f"Connect error for {email} on port {port}: {e}")
            except socket.timeout:
                logging.warning(f"Timeout for {email} on port {port}")
            except Exception as e:
                logging.error(f"Error validating {email}: {e}")

        return ValidationResult(email, "error", "smtp", proxy_str, "no_response", time.time()-start)

    def _is_valid_syntax(self, email: str) -> bool:
        import re
        pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        return bool(pattern.match(email))
