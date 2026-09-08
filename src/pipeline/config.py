from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:netlist@localhost:5433/netlist"
    s3_bucket: str = "netlist"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minio"
    s3_secret_key: str = "minio12345"
    s3_endpoint: str | None = "http://localhost:9000"  # unset in production (real S3)

    worker_poll_seconds: float = 1.0
    job_stale_after_seconds: int = 900
    job_max_attempts: int = 3

    kicad_cli: str = "kicad-cli"
    freerouting_bin: str = "freerouting"  # launcher from the linux-x64 bundle, or "java -jar x.jar"
    freerouting_passes: int = 20
    freerouting_timeout_seconds: int = 600

    judge_url: str | None = None
    judge_token: str | None = None
    sentry_dsn: str | None = None


settings = Settings()
