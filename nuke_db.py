from app.core.database import engine
from sqlalchemy import text

def reset_extremo():
    print("☢️  Iniciando borrado extremo de PostgreSQL...")
    with engine.connect() as conn:
        # Esto borra TODO el esquema public (tablas, relaciones, todo)
        conn.execute(text("DROP SCHEMA public CASCADE;"))
        # Esto lo vuelve a crear vacío y limpio
        conn.execute(text("CREATE SCHEMA public;"))
        conn.commit()
    print("✅ Base de datos purgada. Terreno 100% limpio.")

if __name__ == "__main__":
    reset_extremo()