import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from loguru import logger
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


# ==================================================
# 1. 环境配置
# ==================================================

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")

if not DATABASE_URL:
    raise RuntimeError("缺少 DATABASE_URL 环境变量")

if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY 至少需要 32 个字符")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 120

CATEGORIES = [
    "餐饮",
    "交通",
    "购物",
    "居住",
    "娱乐",
    "教育",
    "医疗",
    "人情",
    "其他",
]

INCOME_EXPENSE_TYPES = ["收入", "支出"]


# ==================================================
# 2. FastAPI
# ==================================================

app = FastAPI(
    title="AI 智能记账助手",
    description="FastAPI + SQLAlchemy + PostgreSQL",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================================================
# 3. 数据库
# ==================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    records: Mapped[list["Record"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    record_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    note: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    owner: Mapped["User"] = relationship(
        back_populates="records",
    )


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


# ==================================================
# 4. Pydantic 数据模型
# ==================================================

class UserRegister(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if len(value) < 8:
            raise ValueError("密码至少需要 8 位")

        if not re.search(r"[A-Z]", value):
            raise ValueError("密码必须包含大写字母")

        if not re.search(r"[a-z]", value):
            raise ValueError("密码必须包含小写字母")

        if not re.search(r"\d", value):
            raise ValueError("密码必须包含数字")

        if not re.search(r"[^A-Za-z0-9]", value):
            raise ValueError("密码必须包含特殊字符")

        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: EmailStr


class RecordCreate(BaseModel):
    amount: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    type: Literal["收入", "支出"]

    category: str

    record_date: date

    note: str = Field(
        default="",
        max_length=1000,
    )

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        if value not in CATEGORIES:
            raise ValueError("不支持的记账分类")

        return value


class RecordUpdate(BaseModel):
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    type: Literal["收入", "支出"] | None = None

    category: str | None = None

    record_date: date | None = None

    note: str | None = Field(
        default=None,
        max_length=1000,
    )

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        if value is not None and value not in CATEGORIES:
            raise ValueError("不支持的记账分类")

        return value


class RecordResponse(BaseModel):
    id: int
    amount: Decimal
    type: str
    category: str
    record_date: date
    note: str

    model_config = {
        "from_attributes": True
    }


class ChatMessage(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=2000,
    )


# ==================================================
# 5. 用户认证
# ==================================================

password_hash = PasswordHash.recommended()

security = HTTPBearer()


def hash_password(password: str):
    return password_hash.hash(password)


def verify_password(
    password: str,
    hashed_password: str,
):
    return password_hash.verify(
        password,
        hashed_password,
    )


def create_access_token(user_id: int):
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        "sub": str(user_id),
        "exp": expire,
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials,
        Depends(security),
    ],
    db: DB,
):
    token = credentials.credentials

    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录凭证无效或已过期",
        headers={
            "WWW-Authenticate": "Bearer"
        },
    )

    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

        user_id = int(payload["sub"])

    except (
        InvalidTokenError,
        KeyError,
        ValueError,
    ):
        raise error

    user = db.get(User, user_id)

    if user is None:
        raise error

    return user


CurrentUser = Annotated[
    User,
    Depends(get_current_user),
]


# ==================================================
# 6. 注册与登录 API
# ==================================================

@app.post(
    "/api/auth/register",
    response_model=UserResponse,
    status_code=201,
)
def register(
    data: UserRegister,
    db: DB,
):
    email = str(data.email).lower()

    existing = db.scalar(
        select(User).where(
            User.email == email
        )
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="该邮箱已经注册",
        )

    user = User(
        email=email,
        password_hash=hash_password(
            data.password
        ),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(
        "用户注册成功 user_id={}",
        user.id,
    )

    return user


@app.post(
    "/api/auth/login",
    response_model=TokenResponse,
)
def login(
    data: UserLogin,
    db: DB,
):
    user = db.scalar(
        select(User).where(
            User.email == str(data.email).lower()
        )
    )

    if not user or not verify_password(
        data.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="邮箱或密码错误",
        )

    token = create_access_token(
        user.id
    )

    logger.info(
        "用户登录成功 user_id={}",
        user.id,
    )

    return {
        "access_token": token,
        "token_type": "bearer",
    }


@app.get(
    "/api/auth/me",
    response_model=UserResponse,
)
def get_me(
    current_user: CurrentUser,
):
    return current_user


# ==================================================
# 7. 记账业务函数
# 后续 LangChain Tool 将调用这些函数
# ==================================================

def accounting_add(
    db: Session,
    user_id: int,
    data: RecordCreate,
):
    record = Record(
        user_id=user_id,
        **data.model_dump(),
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(
        "新增账单 user_id={} record_id={}",
        user_id,
        record.id,
    )

    return record


def accounting_update(
    db: Session,
    user_id: int,
    record_id: int,
    data: RecordUpdate,
):
    record = db.scalar(
        select(Record).where(
            Record.id == record_id,
            Record.user_id == user_id,
        )
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="账单不存在",
        )

    updates = data.model_dump(
        exclude_unset=True
    )

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="没有需要修改的字段",
        )

    for key, value in updates.items():
        if value is None:
            raise HTTPException(
                status_code=422,
                detail=f"{key} 不能为 null",
            )

        setattr(
            record,
            key,
            value,
        )

    db.commit()
    db.refresh(record)

    logger.info(
        "修改账单 user_id={} record_id={}",
        user_id,
        record_id,
    )

    return record


def accounting_query(
    db: Session,
    user_id: int,
    start_date=None,
    end_date=None,
    category=None,
    record_type=None,
    page=1,
    page_size=10,
):
    if (
        start_date is not None
        and end_date is not None
        and start_date > end_date
    ):
        raise HTTPException(
            status_code=422,
            detail="开始日期不能晚于结束日期",
        )

    conditions = [
        Record.user_id == user_id
    ]

    if start_date:
        conditions.append(
            Record.record_date >= start_date
        )

    if end_date:
        conditions.append(
            Record.record_date <= end_date
        )

    if category:
        conditions.append(
            Record.category == category
        )

    if record_type:
        conditions.append(
            Record.type == record_type
        )

    total = db.scalar(
        select(func.count())
        .select_from(Record)
        .where(*conditions)
    ) or 0

    records = db.scalars(
        select(Record)
        .where(*conditions)
        .order_by(
            Record.record_date.desc(),
            Record.id.desc(),
        )
        .offset(
            (page - 1) * page_size
        )
        .limit(page_size)
    ).all()

    return {
        "items": records,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def accounting_delete(
    db: Session,
    user_id: int,
    record_id: int,
):
    record = db.scalar(
        select(Record).where(
            Record.id == record_id,
            Record.user_id == user_id,
        )
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="账单不存在",
        )

    db.delete(record)
    db.commit()

    logger.info(
        "删除账单 user_id={} record_id={}",
        user_id,
        record_id,
    )

    return {
        "message": "删除成功",
        "id": record_id,
    }


def accounting_statistics(
    db: Session,
    user_id: int,
    start_date=None,
    end_date=None,
):
    if (
        start_date is not None
        and end_date is not None
        and start_date > end_date
    ):
        raise HTTPException(
            status_code=422,
            detail="开始日期不能晚于结束日期",
        )

    conditions = [
        Record.user_id == user_id
    ]

    if start_date:
        conditions.append(
            Record.record_date >= start_date
        )

    if end_date:
        conditions.append(
            Record.record_date <= end_date
        )

    records = db.scalars(
        select(Record).where(
            *conditions
        )
    ).all()

    total_income = Decimal("0.00")
    total_expense = Decimal("0.00")

    by_category = {
        category: Decimal("0.00")
        for category in CATEGORIES
    }

    for record in records:
        if record.type == "收入":
            total_income += record.amount

        else:
            total_expense += record.amount

            by_category[
                record.category
            ] += record.amount

    percentages = {}

    for category, amount in by_category.items():
        if total_expense > 0:
            percentage = (
                amount / total_expense
            ) * 100
        else:
            percentage = Decimal("0")

        percentages[category] = round(
            float(percentage),
            2,
        )

    return {
        "total_income": float(total_income),
        "total_expense": float(total_expense),
        "balance": float(
            total_income - total_expense
        ),
        "record_count": len(records),
        "by_category": {
            key: float(value)
            for key, value in by_category.items()
        },
        "percentages": percentages,
    }


# ==================================================
# 8. 记账 REST API
# ==================================================

@app.post(
    "/api/records",
    response_model=RecordResponse,
    status_code=201,
)
def add_record(
    data: RecordCreate,
    db: DB,
    current_user: CurrentUser,
):
    return accounting_add(
        db,
        current_user.id,
        data,
    )


@app.get("/api/records")
def get_records(
    db: DB,
    current_user: CurrentUser,
    start_date: date | None = None,
    end_date: date | None = None,
    category: str | None = None,
    record_type: Literal["收入", "支出"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=10,
        ge=1,
        le=100,
    ),
):
    if category and category not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail="不支持的记账分类",
        )

    result = accounting_query(
        db=db,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        record_type=record_type,
        page=page,
        page_size=page_size,
    )

    return {
        "items": [
            RecordResponse.model_validate(
                item
            ).model_dump(mode="json")
            for item in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
    }


@app.put(
    "/api/records/{record_id}",
    response_model=RecordResponse,
)
def update_record(
    record_id: int,
    data: RecordUpdate,
    db: DB,
    current_user: CurrentUser,
):
    return accounting_update(
        db,
        current_user.id,
        record_id,
        data,
    )


@app.delete(
    "/api/records/{record_id}"
)
def delete_record(
    record_id: int,
    db: DB,
    current_user: CurrentUser,
):
    return accounting_delete(
        db,
        current_user.id,
        record_id,
    )


@app.get("/api/statistics")
def get_statistics(
    db: DB,
    current_user: CurrentUser,
    start_date: date | None = None,
    end_date: date | None = None,
):
    return accounting_statistics(
        db,
        current_user.id,
        start_date,
        end_date,
    )


# ==================================================
# 9. 聊天接口
# 通过 LangChain Agent 调用记账工具
# ==================================================


@app.post("/api/chat")
def chat(
    message: ChatMessage,
    current_user: CurrentUser,
):
    from agent_service import run_accounting_agent

    try:
        result = run_accounting_agent(
            user_id=current_user.id,
            text=message.text,
        )

        return result

    except RuntimeError as exc:
        logger.warning("Agent 配置错误：{}", exc)

        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )

    except Exception:
        logger.exception(
            "Agent 请求失败 user_id={}",
            current_user.id,
        )

        raise HTTPException(
            status_code=502,
            detail="智能体调用失败，请检查模型配置或网络连接",
        )



# ==================================================
# 10. 首页
# ==================================================

@app.get("/")
def home():
    return {
        "message": "AI 智能记账助手后端运行成功",
        "version": "1.0.0",
    }
