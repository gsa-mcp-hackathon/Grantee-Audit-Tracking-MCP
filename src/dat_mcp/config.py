"""Settings loaded from environment variables or a local .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO = Path(__file__).resolve().parents[2]  # src/dat_mcp/config.py -> repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO / ".env", extra="ignore")

    fac_api_key: str = ""  # FAC_API_KEY — api.data.gov key for api.fac.gov
    sam_api_key: str = ""  # SAM_API_KEY — SAM.gov public Entity API key
    # DAT_DB_PATH — local SQLite database built by scripts/build_db.py. The
    # default is under the repo, wherever the server is launched from.
    dat_db_path: str = str(REPO / "data" / "dat.sqlite")


settings = Settings()
