import base64
import threading

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import sanitize_address
from google.auth import exceptions
from google.oauth2 import service_account
from googleapiclient.discovery import build

from .utils import get_credentials_file


class GSuiteEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):

        super().__init__(fail_silently=fail_silently)
        
        self.fail_silently = fail_silently
        self.credentials = get_credentials_file()
        self.API_SCOPE = ['https://www.googleapis.com/auth/gmail.send', ]
        # to reopen connection with different delegation when user changes
        self.current_user = None
        self.connection = None
        self._lock = threading.RLock()
        self.gmail_user = settings.GMAIL_USER

    def _delegate_user(self, user_id):
        credentials = service_account.Credentials.from_service_account_file(
            self.credentials, scopes=self.API_SCOPE)
        credentials_delegated = credentials.with_subject(user_id)
        return credentials_delegated

    def send_messages(self, email_messages):
        """
        Send one or more EmailMessage objects and return the number of email
        messages sent.
        """
        import re
        
        if not email_messages:
            return 0
        with self._lock:
            num_sent = 0
            for message in email_messages:
                encoding = message.encoding or settings.DEFAULT_CHARSET
                self.gmail_user = sanitize_address(message.from_email, encoding)
                self.gmail_user = re.findall(r"[a-z0-9\.\-+_]+@[a-z0-9\.\-+_]+\.[a-z]+", self.gmail_user)[0]
                new_conn_created = self.open()
                if not self.connection or new_conn_created is None:
                    # skip this message
                    continue
                sent = self._send(message)
                if sent:
                    num_sent += 1
            # not doing anything here
            if new_conn_created:
                self.close()
        return num_sent

    def open(self):
        """
        Ensure an open connection to the email server. Return whether or not a
        new connection was required (True or False) or None if an exception
        passed silently.
        """
        if not self.current_user:
            # first connection
            self.current_user = self.gmail_user

        if self.connection and self.gmail_user == self.current_user:
            # Nothing to do if the connection is already open for same delegation
            return False

        try:
            if self.connection is None: newConn=True
            else: newConn=False
            self.close()
            self.current_user = self.gmail_user
            credentials = self._delegate_user(self.current_user)
            self.connection = build("gmail", "v1", credentials=credentials)
            return newConn
        except (exceptions.DefaultCredentialsError, exceptions.GoogleAuthError, exceptions.RefreshError, exceptions.TransportError):
            if not self.fail_silently:
                self.close()
                raise

    def close(self):
        """Close the connection to the email server."""
        if self.connection is None: return
        # do something
        try:
            self.connection.close()
            self.current_user = None
            self.connection = None
        except:
            self.current_user = None
            self.connection = None
            if self.fail_silently:
                return
            raise

    def _send(self, email_message):
        """A helper method that does the actual sending."""
        if not email_message.recipients():
            return False
        # check this
        message = email_message.message()
        if email_message.bcc:
            message['Bcc'] = ','.join(map(str, email_message.bcc))
        # https://developers.google.com/gmail/api/guides/sending#creating_messages
        raw = base64.urlsafe_b64encode(message.as_bytes())
        raw = raw.decode()
        binary_content = {'raw': raw}
        try:
            # need different login to check success
            self.connection.users().messages().send(
                userId='me', body=binary_content).execute()
        except (exceptions.DefaultCredentialsError, exceptions.GoogleAuthError, exceptions.RefreshError, exceptions.TransportError):
            if not self.fail_silently:
                raise
            return False
        return True
