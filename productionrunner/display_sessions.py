"""Rolling device sessions for read-only TV accounts; normal logins retain their TTL."""
from datetime import datetime, timedelta, timezone
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature


class DisplaySessionInterface(SecureCookieSessionInterface):
    lifetime = timedelta(days=365)

    def open_session(self, app, request):
        serializer = self.get_signing_serializer(app)
        if serializer is None:
            return None
        value = request.cookies.get(self.get_cookie_name(app))
        if not value:
            return self.session_class()
        try:
            data = serializer.loads(value, max_age=int(self.lifetime.total_seconds()))
            if not data.get('display_device'):
                data = serializer.loads(value, max_age=int(app.permanent_session_lifetime.total_seconds()))
            return self.session_class(data)
        except BadSignature:
            return self.session_class()

    def get_expiration_time(self, app, session):
        if session.get('display_device'):
            return datetime.now(timezone.utc) + self.lifetime
        return super().get_expiration_time(app, session)
