# validator.py - Production Email Validation Module
import smtplib
import socket
import dns.resolver
import imaplib
import poplib
import requests
import random
import time
from typing import Optional

# --- Configuration for real validation (you'd put this in config.py or env) ---
DISPOSABLE_DOMAINS = {
    "mailinator.com", "yopmail.com", "temp-mail.org", "grr.la", "guerillamail.com"
}
SMTP_PORTS = [25, 587, 465]
IMAP_PORTS = [143, 993]
POP3_PORTS = [110, 995]


class Proxy:
    def __init__(self, host: str, port: int, username: Optional[str] = None, password: Optional[str] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def __str__(self):
        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        return f"{auth}{self.host}:{self.port}"

class ValidationResult:
    def __init__(self, email: str, status: str, method: str, details: str = "", proxy: Optional[Proxy] = None):
        self.email = email
        self.status = status # "valid", "invalid", "error", "disposable"
        self.method = method
        self.details = details
        self.proxy = str(proxy) if proxy else None
    
    def __repr__(self):
        return f"ValidationResult(email='{self.email}', status='{self.status}', method='{self.method}', details='{self.details}', proxy='{self.proxy}')"

class EmailValidator:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.disposable_domains = DISPOSABLE_DOMAINS
        dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
        dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']

    def _get_domain_from_email(self, email: str) -> Optional[str]:
        if "@" not in email:
            return None
        return email.split('@')[1]

    def _check_disposable(self, email: str) -> bool:
        domain = self._get_domain_from_email(email)
        return domain in self.disposable_domains

    def validate_smtp(self, email: str, password: Optional[str] = None, proxy: Optional[Proxy] = None) -> ValidationResult:
        domain = self._get_domain_from_email(email)
        if not domain:
            return ValidationResult(email, "error", "smtp", "Invalid email format", proxy)
        if self._check_disposable(email):
            return ValidationResult(email, "disposable", "smtp", "Disposable email detected", proxy)

        try:
            mx_records = [str(r.exchange) for r in dns.resolver.resolve(domain, 'MX')]
            if not mx_records:
                return ValidationResult(email, "invalid", "smtp", "No MX records found for domain", proxy)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
            return ValidationResult(email, "invalid", "smtp", "Could not resolve MX records", proxy)
        except Exception as e:
            return ValidationResult(email, "error", "smtp", f"MX lookup error: {e}", proxy)

        for mx_host in mx_records:
            try:
                for port in SMTP_PORTS:
                    with smtplib.SMTP(mx_host, port, timeout=self.timeout) as server:
                        server.ehlo(socket.gethostname())
                        server.mail('noreply@validator.local')
                        code, msg = server.rcpt(email)
                        
                        if code == 250:
                            return ValidationResult(email, "valid", "smtp", "SMTP RCPT TO success", proxy)
                        elif code == 550:
                            return ValidationResult(email, "invalid", "smtp", f"SMTP RCPT TO failed: {msg.decode()}", proxy)
                        else:
                            return ValidationResult(email, "invalid", "smtp", f"SMTP RCPT TO: unexpected code {code} - {msg.decode()}", proxy)
            except smtplib.SMTPServerDisconnected:
                continue
            except (socket.timeout, smtplib.SMTPConnectError, ConnectionRefusedError) as e:
                print(f"SMTP connection error to {mx_host}:{port}: {e}")
                continue
            except Exception as e:
                return ValidationResult(email, "error", "smtp", f"Unexpected SMTP error: {e}", proxy)

        return ValidationResult(email, "invalid", "smtp", "Could not validate via SMTP (all MX servers failed or refused)", proxy)


    def validate_mx(self, email: str, proxy: Optional[Proxy] = None) -> ValidationResult:
        domain = self._get_domain_from_email(email)
        if not domain:
            return ValidationResult(email, "error", "mx", "Invalid email format", proxy)
        if self._check_disposable(email):
            return ValidationResult(email, "disposable", "mx", "Disposable email detected", proxy)

        try:
            mx_records = dns.resolver.resolve(domain, 'MX', lifetime=self.timeout)
            if mx_records:
                return ValidationResult(email, "valid", "mx", f"MX records found: {[str(r.exchange) for r in mx_records]}", proxy)
            else:
                return ValidationResult(email, "invalid", "mx", "No MX records found for domain", proxy)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            return ValidationResult(email, "invalid", "mx", "Domain does not exist or has no MX records", proxy)
        except dns.exception.Timeout:
            return ValidationResult(email, "error", "mx", "DNS MX lookup timed out", proxy)
        except Exception as e:
            return ValidationResult(email, "error", "mx", f"MX lookup error: {e}", proxy)

    def validate_imap(self, email: str, password: str, proxy: Optional[Proxy] = None) -> ValidationResult:
        if not password:
            return ValidationResult(email, "error", "imap", "Password required for IMAP validation", proxy)
        if self._check_disposable(email):
            return ValidationResult(email, "disposable", "imap", "Disposable email detected", proxy)

        domain = self._get_domain_from_email(email)
        if not domain:
            return ValidationResult(email, "error", "imap", "Invalid email format", proxy)

        possible_imap_hosts = [f"imap.{domain}", f"mail.{domain}"] 

        for host in possible_imap_hosts:
            try:
                for port in IMAP_PORTS:
                    try:
                        if port == 993:
                            mail = imaplib.IMAP4_SSL(host, port, timeout=self.timeout)
                        else:
                            mail = imaplib.IMAP4(host, port, timeout=self.timeout)
                            mail.starttls()

                        mail.login(email, password)
                        mail.logout()
                        return ValidationResult(email, "valid", "imap", "IMAP login successful", proxy)
                    except imaplib.IMAP4.error as e:
                        return ValidationResult(email, "invalid", "imap", f"IMAP login failed: {e}", proxy)
                    except (socket.timeout, ConnectionRefusedError, OSError) as e:
                        print(f"IMAP connection error to {host}:{port}: {e}")
                        continue
            except Exception as e:
                print(f"IMAP error for {email} on {host}: {e}")
                continue
        
        return ValidationResult(email, "invalid", "imap", "Could not connect or login via IMAP (all attempts failed)", proxy)

    def validate_pop3(self, email: str, password: str, proxy: Optional[Proxy] = None) -> ValidationResult:
        if not password:
            return ValidationResult(email, "error", "pop3", "Password required for POP3 validation", proxy)
        if self._check_disposable(email):
            return ValidationResult(email, "disposable", "pop3", "Disposable email detected", proxy)

        domain = self._get_domain_from_email(email)
        if not domain:
            return ValidationResult(email, "error", "pop3", "Invalid email format", proxy)

        possible_pop3_hosts = [f"pop3.{domain}", f"mail.{domain}"] 

        for host in possible_pop3_hosts:
            try:
                for port in POP3_PORTS:
                    try:
                        if port == 995:
                            mail = poplib.POP3_SSL(host, port, timeout=self.timeout)
                        else:
                            mail = poplib.POP3(host, port, timeout=self.timeout)
                        
                        mail.user(email)
                        mail.pass_(password)
                        mail.quit()
                        return ValidationResult(email, "valid", "pop3", "POP3 login successful", proxy)
                    except poplib.error_proto as e:
                        return ValidationResult(email, "invalid", "pop3", f"POP3 login failed: {e}", proxy)
                    except (socket.timeout, ConnectionRefusedError, OSError) as e:
                        print(f"POP3 connection error to {host}:{port}: {e}")
                        continue
            except Exception as e:
                print(f"POP3 error for {email} on {host}: {e}")
                continue
        
        return ValidationResult(email, "invalid", "pop3", "Could not connect or login via POP3 (all attempts failed)", proxy)


    def validate_http(self, email: str, proxy: Optional[Proxy] = None) -> ValidationResult:
        domain = self._get_domain_from_email(email)
        if not domain:
            return ValidationResult(email, "error", "http", "Invalid email format", proxy)
        if self._check_disposable(email):
            return ValidationResult(email, "disposable", "http", "Disposable email detected", proxy)

        # Configure proxy if provided
        proxies_config = {}
        if proxy:
            proxy_url = f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}" if proxy.username else f"http://{proxy.host}:{proxy.port}"
            proxies_config = {"http": proxy_url, "https": proxy_url}

        # Try multiple URL patterns for HTTP validation
        urls_to_try = [
            f"https://mail.{domain}",
            f"https://webmail.{domain}",
            f"https://login.{domain}",
            f"https://{domain}"
        ]
        
        last_error = None
        
        for url in urls_to_try:
            try:
                response = requests.head(url, timeout=self.timeout, proxies=proxies_config, allow_redirects=True)
                
                if response.status_code in [200, 301, 302, 403, 405]:  # Indicates server exists
                    # Try to check if it's actually an email service
                    if any(keyword in response.headers.get('server', '').lower() for keyword in ['mail', 'exchange', 'postfix']):
                        return ValidationResult(email, "valid", "http", f"Mail server detected at {url}, status: {response.status_code}", proxy)
                    elif response.status_code in [200, 301, 302]:
                        return ValidationResult(email, "valid", "http", f"Web service accessible at {url}, status: {response.status_code}", proxy)
                    else:
                        return ValidationResult(email, "valid", "http", f"Service exists at {url}, status: {response.status_code}", proxy)
                        
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                continue
            except Exception as e:
                last_error = str(e)
                continue
        
        # If all HTTP attempts failed, try basic DNS check
        try:
            dns.resolver.resolve(domain, 'A', lifetime=self.timeout)
            return ValidationResult(email, "invalid", "http", f"Domain exists but no web services accessible. Last error: {last_error}", proxy)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            return ValidationResult(email, "invalid", "http", "Domain does not exist", proxy)
        except dns.exception.Timeout:
            return ValidationResult(email, "error", "http", "DNS timeout during validation", proxy)
        except Exception as e:
            return ValidationResult(email, "error", "http", f"HTTP validation error: {e}", proxy)
