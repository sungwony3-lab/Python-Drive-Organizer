import base64
import re
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "gmail_send_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailAuthenticationError(RuntimeError):
    """Raised when the dedicated Gmail send OAuth flow fails."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "GMAIL_AUTH_UNKNOWN",
        cause_type: str = "Unknown",
        diagnostic_detail: str = "No diagnostic detail",
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.cause_type = cause_type
        self.diagnostic_detail = diagnostic_detail


class GmailSendError(RuntimeError):
    """Raised when Gmail definitively rejects a send request."""


class GmailApiNotEnabledError(GmailSendError):
    """Raised when the Google Cloud project has not enabled Gmail API."""


class GmailDeliveryUncertainError(RuntimeError):
    """Raised when transport failed and delivery cannot be safely retried."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        cause_type: str,
        diagnostic_detail: str,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.cause_type = cause_type
        self.diagnostic_detail = diagnostic_detail


def _safe_transport_detail(error: BaseException) -> str:
    """Return useful transport diagnostics without credentials or message data."""
    detail = " ".join(str(error).split()) or "No exception message"
    detail = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        detail,
    )
    detail = re.sub(
        r"(?i)(access_token|refresh_token|client_secret|api_key|key)"
        r"(\s*[:=]\s*)[^\s&,;]+",
        r"\1\2[REDACTED]",
        detail,
    )
    errno = getattr(error, "errno", None)
    winerror = getattr(error, "winerror", None)
    metadata = []
    if errno is not None:
        metadata.append(f"errno={errno}")
    if winerror is not None:
        metadata.append(f"winerror={winerror}")
    if metadata:
        detail = f"{'; '.join(metadata)}; {detail}"
    return detail[:500]


def authenticate_gmail_send() -> Credentials:
    """Authorize Gmail send only, using a token separate from Drive."""
    credentials = None

    try:
        if TOKEN_FILE.exists():
            credentials = Credentials.from_authorized_user_file(
                TOKEN_FILE, SCOPES
            )
    except (GoogleAuthError, OSError, ValueError) as error:
        raise GmailAuthenticationError(
            "The Gmail send token could not be loaded.",
            reason="TOKEN_LOAD_FAILED",
            cause_type=type(error).__name__,
            diagnostic_detail=_safe_transport_detail(error),
        ) from error

    try:
        if credentials and credentials.valid and credentials.has_scopes(SCOPES):
            return credentials

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
            and credentials.has_scopes(SCOPES)
        ):
            try:
                credentials.refresh(Request())
            except (GoogleAuthError, OSError, ValueError) as error:
                raise GmailAuthenticationError(
                    "The Gmail send token could not be refreshed.",
                    reason="TOKEN_REFRESH_FAILED",
                    cause_type=type(error).__name__,
                    diagnostic_detail=_safe_transport_detail(error),
                ) from error
        else:
            if not CREDENTIALS_FILE.exists():
                raise GmailAuthenticationError(
                    "The Gmail OAuth client credentials file is unavailable.",
                    reason="CREDENTIALS_FILE_MISSING",
                    cause_type="FileNotFoundError",
                    diagnostic_detail=f"missing_file={CREDENTIALS_FILE.name}",
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, SCOPES
                )
                credentials = flow.run_local_server(
                    port=0, timeout_seconds=300
                )
            except (GoogleAuthError, OSError, ValueError) as error:
                raise GmailAuthenticationError(
                    "The Gmail send OAuth flow failed.",
                    reason="OAUTH_FLOW_FAILED",
                    cause_type=type(error).__name__,
                    diagnostic_detail=_safe_transport_detail(error),
                ) from error

        if not credentials.has_scopes(SCOPES):
            raise GmailAuthenticationError(
                "The Gmail token does not contain gmail.send.",
                reason="TOKEN_SCOPE_MISSING",
                cause_type="ScopeValidationError",
                diagnostic_detail="required_scope=gmail.send",
            )

        try:
            TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
        except OSError as error:
            raise GmailAuthenticationError(
                "The refreshed Gmail send token could not be saved.",
                reason="TOKEN_SAVE_FAILED",
                cause_type=type(error).__name__,
                diagnostic_detail=_safe_transport_detail(error),
            ) from error
        return credentials
    except GmailAuthenticationError:
        raise
    except (GoogleAuthError, OSError, ValueError) as error:
        raise GmailAuthenticationError(
            "Gmail send OAuth failed. Reauthorize the dedicated token.",
            reason="GMAIL_AUTH_UNKNOWN",
            cause_type=type(error).__name__,
            diagnostic_detail=_safe_transport_detail(error),
        ) from error


def build_gmail_service(credentials: Credentials | None = None):
    if credentials is None:
        credentials = authenticate_gmail_send()
    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )


def send_message(service, message_bytes: bytes) -> str:
    """Send once with client retries disabled to reduce duplicate delivery."""
    encoded = base64.urlsafe_b64encode(message_bytes).decode("ascii")
    try:
        response = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded})
            .execute(num_retries=0)
        )
    except HttpError as error:
        status = getattr(error.resp, "status", None)
        content = error.content.decode("utf-8", errors="ignore").lower()
        if status == 403 and any(
            marker in content
            for marker in (
                "accessnotconfigured",
                "servicedisabled",
                "gmail api has not been used",
            )
        ):
            raise GmailApiNotEnabledError(
                "Gmail API is not enabled for the OAuth client project."
            ) from None
        raise GmailSendError(
            f"Gmail rejected the send request (HTTP {status or 'unknown'})."
        ) from None
    except (OSError, TimeoutError) as error:
        reason = (
            "TRANSPORT_TIMEOUT"
            if isinstance(error, TimeoutError)
            else "TRANSPORT_OS_ERROR"
        )
        raise GmailDeliveryUncertainError(
            "The Gmail transport failed; delivery status is uncertain.",
            reason=reason,
            cause_type=type(error).__name__,
            diagnostic_detail=_safe_transport_detail(error),
        ) from error

    message_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(message_id, str) or not message_id:
        response_type = type(response).__name__
        response_keys = (
            sorted(str(key) for key in response.keys())
            if isinstance(response, dict)
            else []
        )
        raise GmailDeliveryUncertainError(
            "Gmail returned no message id; delivery status is uncertain.",
            reason="RESPONSE_MISSING_ID",
            cause_type=response_type,
            diagnostic_detail=(
                f"response_type={response_type}; response_keys={response_keys}"
            )[:500],
        )
    return message_id
