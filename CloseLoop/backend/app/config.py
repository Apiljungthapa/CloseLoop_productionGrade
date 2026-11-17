from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENV: str = "development"  # "development" | "production"

    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/closeloop"
    )

    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_MINUTES: int = 30           # access token lifetime
    JWT_REFRESH_EXPIRES_DAYS: int = 7       # refresh token lifetime

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    PORT: int = 8000

    # httpOnly session cookies
    COOKIE_SECURE: bool = False             # must be True behind HTTPS in production
    COOKIE_DOMAIN: Optional[str] = None

    # login brute-force protection
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # Task/sub-task attachment storage — a Docker volume in prod (see
    # docker-compose.yml), one sub-directory per tenant underneath.
    UPLOAD_DIR: str = "/app/uploads"

    # Origin used to build links sent in emails (password reset today).
    # Distinct from CORS_ORIGINS (which can be a comma list) — this is the
    # one canonical URL a human clicks.
    PUBLIC_APP_URL: str = "http://localhost:5173"

    # "Forgot password" email delivery. Blank SMTP_HOST/SMTP_USERNAME means
    # email sending is disabled — forgot-password still "succeeds" (same
    # generic response either way, so the endpoint never leaks whether an
    # email is registered) but no mail goes out; the reset link is only
    # logged, which is fine for local dev.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_TLS: bool = True
    RESET_TOKEN_EXPIRES_MINUTES: int = 30

    # HRMS integration (read-only project list for the HRMS Integration page).
    # The shared secret is NEVER committed — set HRMS_SECRET in the
    # environment (.env / .env.prod). Blank HRMS_TENANT_ID / HRMS_SECRET
    # disables the integration (the endpoint answers 503).
    HRMS_BASE_URL: str = "https://api-hrms.innovationnxt.com/backend"
    HRMS_TENANT_ID: str = ""
    HRMS_SECRET: str = ""
    HRMS_TIMEOUT_SECONDS: int = 20
    HRMS_CACHE_SECONDS: int = 60

    # Automated timesheet (HRMS Integration page). Both switches stay OFF until
    # the HRMS timesheet read/write API is wired into
    # services/hrms_timesheet_gateway.py: submitting can't work without it, and
    # reminders would nag about a timesheet nobody can complete.
    TIMESHEET_SUBMIT_ENABLED: bool = False
    TIMESHEET_REMINDERS_ENABLED: bool = False
    # The HRMS timesheet routes, relative to HRMS_BASE_URL. POST is the real route of
    # the HRMS in use (one request per day, 201 = created; blank = not connected, nothing
    # can be sent). That HRMS has no read route, so LIST stays blank: duplicates are then
    # only prevented against what CloseLoop itself sent (the page says so). Same
    # tenant-key header as activities.
    HRMS_TIMESHEET_POST_PATH: str = "/v1/external/timesheets"
    HRMS_TIMESHEET_LIST_PATH: str = ""
    # Reminders go out at this Nepal-time hour, on working days only.
    TIMESHEET_REMINDER_HOUR: int = 10
    # How many closed months (per calendar) keep nagging after their deadline.
    TIMESHEET_REMINDER_LOOKBACK: int = 3
    # Longest summary text sent for one HRMS entry (longer is cut, and flagged).
    TIMESHEET_SUMMARY_MAX_CHARS: int = 1000

    # Chatbot ("Close Bhai"). This backend is the TRUSTED front door to the chatbot's
    # query gateway: it authenticates the user, then forwards the question with the
    # user's own tenant_id and the shared keys below. The browser never talks to the
    # gateway and never sees a key. CHATBOT_SQL_API_KEY / CHATBOT_API_KEY must equal
    # the gateway's SQL_PLANNER_API_KEY / API_AUTH_KEY. Disabled unless CHATBOT_ENABLED.
    CHATBOT_ENABLED: bool = False
    CHATBOT_URL: str = "http://chatbot-gateway:8010"
    CHATBOT_SQL_API_KEY: str = ""
    CHATBOT_API_KEY: str = ""
    CHATBOT_TIMEOUT_SECONDS: int = 120      # max silence from the gateway mid-stream
    CHATBOT_RATE_LIMIT_PER_MINUTE: int = 20  # questions per user per minute
    # Sized for a multi-hundred-seat tenant base — this cap is PROCESS-GLOBAL (every
    # tenant shares it; see chatbot_service._active_total), not per tenant or per user.
    # It is a self-imposed ceiling only: raising it does not raise the real capacity of
    # the downstream query gateway, its LLM provider's own rate limit, or the shared
    # Postgres it reads from (see CHATBOT_URL) — tune this against an actual load test
    # against those, not in isolation.
    CHATBOT_MAX_CONCURRENT: int = 40         # simultaneous streams across all users
    CHATBOT_MAX_CONCURRENT_PER_USER: int = 3

    # Chatbot document upload. Same rule as the gateway: this backend is the only caller of the
    # ingestion API, and it supplies the signed-in user's own tenant_id. CHATBOT_INGEST_API_KEY must
    # equal the ingestion service's API_AUTH_KEY. Off unless CHATBOT_INGEST_ENABLED (and CHATBOT_ENABLED).
    CHATBOT_INGEST_ENABLED: bool = False
    CHATBOT_INGEST_URL: str = "http://chatbot-ingestion:8001"
    CHATBOT_INGEST_API_KEY: str = ""
    CHATBOT_INGEST_TIMEOUT_SECONDS: int = 120
    CHATBOT_MAX_DOCUMENTS: int = 500            # stored documents per tenant
    CHATBOT_MAX_FILES_PER_UPLOAD: int = 20      # files in one request (the browser sends bigger sets in several)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def assert_production_ready(self) -> None:
        """Fail fast at startup rather than silently running production
        traffic on a placeholder secret."""
        if self.ENV != "production":
            return
        if self.JWT_SECRET in ("", "change-me", "dev-secret-change-me-in-production"):
            raise RuntimeError(
                "JWT_SECRET is still a placeholder. Set a strong random JWT_SECRET "
                "before running with ENV=production."
            )
        if not self.COOKIE_SECURE:
            raise RuntimeError(
                "COOKIE_SECURE must be true when ENV=production (session cookies "
                "require HTTPS)."
            )


settings = Settings()
