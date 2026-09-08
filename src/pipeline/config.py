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
    job_timeout_seconds: int = 3600  # per job; also when a 'running' job counts as stale
    job_max_attempts: int = 3

    kicad_cli: str = "kicad-cli"
    freerouting_bin: str = "freerouting"  # launcher from the linux-x64 bundle, or "java -jar x.jar"
    freerouting_passes: int = 20
    freerouting_timeout_seconds: int = 600

    judge_url: str | None = None
    judge_token: str | None = None
    judge_version: str = "rules"  # "rules" = in-process reference; else an approved artifact
    judge_artifact: str | None = None  # S3 key or local path; default models/judge/<version>.joblib
    experiments_dir: str | None = "docs/experiments"
    claude_effort: str = "medium"  # placer=claude: low|medium|high|xhigh|max
    claude_timeout_seconds: int = 1200  # per seed  # unset on Render: no experiment records
    sentry_dsn: str | None = None


settings = Settings()
