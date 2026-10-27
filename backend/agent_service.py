
import json
import os
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
from fastapi import HTTPException
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from loguru import logger
from pydantic import ValidationError

# 复用现有后端业务函数、数据库会话和字段验证
from main import (
    CATEGORIES,
    SessionLocal,
    RecordCreate,
    RecordUpdate,
    RecordResponse,
    accounting_add,
    accounting_update,
    accounting_query,
    accounting_delete,
    accounting_statistics,
)

load_dotenv()

# 进程内短期记忆：按用户 ID 隔离会话
memory = InMemorySaver()


def as_json(value):
    """把日期、金额等 Python 类型转换为 JSON 文本。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
    )


def record_dict(record):
    """转换已有的 SQLAlchemy 账单模型。"""
    return RecordResponse.model_validate(
        record
    ).model_dump(mode="json")


def tool_error(exc):
    """统一处理工具参数错误和业务错误。"""
    if isinstance(exc, HTTPException):
        return as_json({
            "success": False,
            "error": exc.detail,
        })

    if isinstance(exc, ValidationError):
        return as_json({
            "success": False,
            "error": exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        })

    if isinstance(exc, (ValueError, TypeError)):
        return as_json({
            "success": False,
            "error": str(exc),
        })

    logger.exception("记账工具执行失败")

    return as_json({
        "success": False,
        "error": "工具执行失败，请检查输入或稍后重试",
    })


def build_tools(user_id: int):
    """
    为当前登录用户创建五个记账工具。

    user_id 由 FastAPI 的 JWT 鉴权结果提供，
    不能由大语言模型自行指定。
    """

    @tool
    def add_accounting_record(
        amount: float,
        record_type: str,
        category: str,
        record_date: str,
        note: str = "",
    ) -> str:
        """
        添加一笔收入或支出账单。

        amount: 正数金额，最多两位小数。
        record_type: 收入或支出。
        category: 九种固定分类之一。
        record_date: YYYY-MM-DD 格式的发生日期。
        note: 账单备注。
        """
        try:
            data = RecordCreate(
                amount=Decimal(str(amount)),
                type=record_type,
                category=category,
                record_date=record_date,
                note=note,
            )

            with SessionLocal() as db:
                record = accounting_add(
                    db, user_id, data
                )

                return as_json({
                    "success": True,
                    "record": record_dict(record),
                })

        except Exception as exc:
            return tool_error(exc)

    @tool
    def update_accounting_record(
        record_id: int,
        amount: float | None = None,
        record_type: str | None = None,
        category: str | None = None,
        record_date: str | None = None,
        note: str | None = None,
    ) -> str:
        """
        根据账单 ID 修改当前用户的账单。

        如果用户只描述金额、日期或备注，
        应先调用查询工具定位目标记录。
        只传需要修改的字段。
        """
        try:
            changes = {}

            if amount is not None:
                changes["amount"] = Decimal(
                    str(amount)
                )

            if record_type is not None:
                changes["type"] = record_type

            if category is not None:
                changes["category"] = category

            if record_date is not None:
                changes["record_date"] = record_date

            if note is not None:
                changes["note"] = note

            data = RecordUpdate(**changes)

            with SessionLocal() as db:
                record = accounting_update(
                    db,
                    user_id,
                    record_id,
                    data,
                )

                return as_json({
                    "success": True,
                    "record": record_dict(record),
                })

        except Exception as exc:
            return tool_error(exc)

    @tool
    def query_accounting_records(
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        record_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> str:
        """
        查询当前用户的账单。

        支持日期范围、分类、收支类型、分页。
        日期必须是 YYYY-MM-DD。
        用于回答查询问题，也用于修改或删除前定位账单。
        """
        try:
            if category and category not in CATEGORIES:
                raise ValueError("无效的账单分类")

            if record_type and record_type not in [
                "收入", "支出"
            ]:
                raise ValueError("无效的收支类型")

            if page < 1 or not 1 <= page_size <= 100:
                raise ValueError("分页参数无效")

            start = (
                date.fromisoformat(start_date)
                if start_date else None
            )

            end = (
                date.fromisoformat(end_date)
                if end_date else None
            )

            with SessionLocal() as db:
                result = accounting_query(
                    db=db,
                    user_id=user_id,
                    start_date=start,
                    end_date=end,
                    category=category,
                    record_type=record_type,
                    page=page,
                    page_size=page_size,
                )

                return as_json({
                    "success": True,
                    "items": [
                        record_dict(item)
                        for item in result["items"]
                    ],
                    "total": result["total"],
                    "page": result["page"],
                    "page_size": result["page_size"],
                })

        except Exception as exc:
            return tool_error(exc)

    @tool
    def delete_accounting_record(
        record_id: int,
    ) -> str:
        """
        删除当前用户指定 ID 的账单。

        必须先确认目标记录唯一。
        若用户的删除意图或目标不明确，
        先询问用户，不能直接删除。
        """
        try:
            with SessionLocal() as db:
                result = accounting_delete(
                    db,
                    user_id,
                    record_id,
                )

                return as_json({
                    "success": True,
                    **result,
                })

        except Exception as exc:
            return tool_error(exc)

    @tool
    def statistics_accounting_records(
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """
        统计当前用户指定日期范围内的账单。

        返回总收入、总支出、结余、记账笔数、
        九种支出分类金额及占比。
        """
        try:
            start = (
                date.fromisoformat(start_date)
                if start_date else None
            )

            end = (
                date.fromisoformat(end_date)
                if end_date else None
            )

            with SessionLocal() as db:
                result = accounting_statistics(
                    db=db,
                    user_id=user_id,
                    start_date=start,
                    end_date=end,
                )

                return as_json({
                    "success": True,
                    **result,
                })

        except Exception as exc:
            return tool_error(exc)

    return [
        add_accounting_record,
        update_accounting_record,
        query_accounting_records,
        delete_accounting_record,
        statistics_accounting_records,
    ]



def create_accounting_agent(user_id: int):
    """
    使用阿里云百炼 Qwen 创建 LangChain Agent。
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")

    base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )

    model_name = os.getenv(
        "QWEN_MODEL",
        "qwen-plus"
    )

    if not api_key:
        raise RuntimeError(
            "尚未配置 DASHSCOPE_API_KEY"
        )

    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        timeout=60,
        max_retries=2,
    )

    system_prompt = f"""
你是一个中文智能记账助手。

今天的日期是 {date.today().isoformat()}。

你必须使用 LangChain Tools 完成用户的记账请求。

支持五种操作：
1. 添加记账
2. 修改记账
3. 查询记账
4. 删除记账
5. 统计记账

固定分类：
餐饮、交通、购物、居住、娱乐、教育、医疗、人情、其他。

规则：
- 金额必须大于零。
- 金额最多保留两位小数。
- 日期格式为 YYYY-MM-DD。
- 根据自然语言识别收入和支出。
- 理解今天、昨天、上个月等日期表达。
- 利用对话历史理解“刚才那笔”等指代。
- 修改、删除前必须明确定位目标记录。
- 若目标不唯一，应先询问用户。
- 删除前先获得用户确认。
- 必须通过工具执行数据库操作。
- 不能编造工具执行结果。
- 只能操作当前登录用户的账单。
- 统计必须使用统计工具。
- 回复使用简洁、清楚的中文。
"""

    return create_agent(
        model=model,
        tools=build_tools(user_id),
        system_prompt=system_prompt,
        checkpointer=memory,
    )


def run_accounting_agent(
    user_id: int,
    text: str,
):
    """
    执行一轮 Agent 对话。

    每个用户使用独立的 thread_id，
    避免不同用户之间的聊天记录混合。
    """
    agent = create_accounting_agent(user_id)

    config = {
        "configurable": {
            "thread_id": f"accounting-user-{user_id}"
        },
        "recursion_limit": 20,
    }

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": text,
                }
            ]
        },
        config=config,
    )

    last_message = result["messages"][-1]
    content = last_message.content

    if isinstance(content, str):
        reply = content
    elif isinstance(content, list):
        reply = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
        )
    else:
        reply = str(content)

    logger.info(
        "Agent 对话完成 user_id={}",
        user_id,
    )

    return {
        "reply": reply,
    }
