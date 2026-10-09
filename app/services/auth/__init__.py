from app.services.auth.base import Authenticator, AuthError
from app.services.auth.firebase import FirebaseAuthenticator as Auth

__all__ = ["Auth", "AuthError", "Authenticator"]
