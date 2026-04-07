from sqlmodel import SQLModel, create_engine, Session
from app.core.config import settings

# El engine usa la URL de tu .env que ya comprobamos que funciona
engine = create_engine(settings.DATABASE_URL, echo=True)

def create_db_and_tables():
    # Esta es la línea mágica que crea las tablas en Postgres
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session