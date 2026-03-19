from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base
from routers.config import DATABASE_PATH

db_url_path = DATABASE_PATH.replace('\\', '/')
SQLALCHEMY_DATABASE_URL = f"sqlite:///./{db_url_path}"


engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread":False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()