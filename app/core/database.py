from sqlmodel import create_engine, Session
from app.core.config import settings

# echo=True imprime las consultas SQL en la consola (ideal para ver qué hace por detrás)
engine = create_engine(settings.DATABASE_URL, echo=True)

def get_session():
    """Generador de sesiones para inyectar en los endpoints de FastAPI."""
    with Session(engine) as session:
        yield session