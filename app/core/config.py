from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str = "development"
    app_secret_key: str = "changeme"
    log_level: str = "INFO"
    disable_scheduler: bool = False
    cors_origins: str = "*"

    # Auth
    admin_username: str = "admin"
    admin_password: str = "tcc2024"
    jwt_expire_hours: int = 24

    # Database
    database_url: str = "postgresql+asyncpg://tcc_user:tcc_pass@localhost:5432/tcc_informes"
    database_echo: bool = False

    # TCC Integration
    tcc_integration_mode: str = "web"  # "web" | "api" | "auto" (legacy: "scraping")
    tcc_enable_web_fallback: bool = True
    tcc_base_url: str = "https://tcc.com.co"
    tcc_tracking_url: str = "https://tcc.com.co/courier/mensajeria/rastrear-envio/"
    tcc_tracking_query_param: str = "guia"
    tcc_min_html_length: int = 300
    tcc_request_timeout: int = 30
    tcc_max_retries: int = 3
    tcc_retry_delay: float = 2.0
    tcc_api_base_url: str = ""
    tcc_api_key: str = ""
    tcc_api_tracking_path: str = "/tracking/{tracking_number}"
    tcc_api_health_path: str = "/health"
    tcc_api_auth_scheme: str = "Bearer"

    # Email (SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from_name: str = "Sistema TCC ASTECO"
    email_from_address: str = "noreply@asteco.com.co"
    # Número de reintentos antes de declarar fallo en envío de correo
    email_max_retries: int = 3
    email_retry_delay: float = 5.0

    # Scheduler — horarios fijos (America/Bogota)
    # Seguimiento diario: 07:00, 12:00, 16:00
    # Consolidado semanal: lunes 07:00
    # Alertas: cada N minutos
    alert_check_interval_minutes: int = 30

    # Alertas
    alert_no_movement_hours: int = 72

    # Reportes
    # Directorio base donde se guardan los archivos generados.
    # Puede ser absoluto o relativo al directorio de trabajo del proceso.
    reports_output_dir: str = "reports"

    @property
    def reports_path(self) -> Path:
        p = Path(self.reports_output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reports_daily_path(self) -> Path:
        p = self.reports_path / "diario"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reports_weekly_path(self) -> Path:
        p = self.reports_path / "semanal"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
