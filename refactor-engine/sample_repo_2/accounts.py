"""Account domain: a manager class that has accumulated multiple
responsibilities over time, plus a stateless password-hashing utility."""

import hashlib


class UserAccountManager:
    def __init__(self, username):
        self.username = username
        self.email = ""
        self.display_name = ""
        self.password_hash = ""
        self.failed_login_attempts = 0
        self.is_locked = False
        self.notifications_sent = []

    def update_profile(self, display_name, email):
        self.display_name = display_name
        self.email = email

    def get_display_name(self):
        if self.display_name:
            return self.display_name
        return self.username

    def set_password(self, raw_password):
        if len(raw_password) < 8:
            raise ValueError("Password too short")
        self.password_hash = hashlib.sha256(raw_password.encode()).hexdigest()

    def check_password(self, raw_password):
        candidate = hashlib.sha256(raw_password.encode()).hexdigest()
        if candidate == self.password_hash:
            self.failed_login_attempts = 0
            return True
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 5:
            self.is_locked = True
        return False

    def unlock_account(self):
        self.is_locked = False
        self.failed_login_attempts = 0

    def send_welcome_email(self):
        message = f"Welcome, {self.get_display_name()}!"
        self.notifications_sent.append(message)
        return message

    def send_password_reset_email(self):
        message = f"Password reset requested for {self.email}"
        self.notifications_sent.append(message)
        return message

    def send_lockout_warning(self):
        if self.is_locked:
            message = f"Account {self.username} is locked due to failed logins."
            self.notifications_sent.append(message)
            return message
        return None


class PasswordHasher:
    def hash(self, raw_password):
        return hashlib.sha256(raw_password.encode()).hexdigest()

    def verify(self, raw_password, password_hash):
        return self.hash(raw_password) == password_hash

    def is_strong(self, raw_password):
        has_digit = any(ch.isdigit() for ch in raw_password)
        has_upper = any(ch.isupper() for ch in raw_password)
        return len(raw_password) >= 8 and has_digit and has_upper
