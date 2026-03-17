from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "OEE Lite API"
    VERSION: str = "1.0.0"
    
    # URL de conexión a PostgreSQL extraída del archivo .env
    DATABASE_URL: str

    class Config:
        env_file = ".env"

settings = Settings()