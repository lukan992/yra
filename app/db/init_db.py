from app.core.logging import setup_logging
from app.db.models import Base
from app.db.session import engine


def init_db() -> None:
    setup_logging()
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")


if __name__ == "__main__":
    init_db()
