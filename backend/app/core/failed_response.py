import logging
import re
import traceback

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette import status


class Settings(BaseSettings):
    """项目配置类，自动从环境变量读取"""
    # 环境标识：dev(开发) / test(测试) / prod(生产)
    ENV: str = "dev"
    # DEBUG模式：开发环境默认True，生产环境强制False
    DEBUG_MODE: bool = True
    # 日志级别
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()

# 生产环境强制关闭 DEBUG_MODE，双保险防止泄露
DEBUG_MODE = settings.DEBUG_MODE if settings.ENV != "prod" else False

def setup_logger():
    """初始化项目日志器"""
    logger = logging.getLogger("app")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # 避免重复添加handler
    if logger.handlers:
        return logger

    # 控制台日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - [%(path)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()


class BusinessException(Exception):
    """
    自定义业务异常基类
    示例：
        if user_quota <= 0:
            raise BusinessException(code=4001, message="错误")
    """
    def __init__(self, code: int = 400, message: str = "出现错误"):
        self.code = code
        self.message = message
        super().__init__(message)


def mask_sensitive_info(text: str) -> str:
    """
    简单的敏感信息脱敏工具
    过滤API密钥、密码、数据库地址等敏感内容
    """
    if not text:
        return text

    sensitive_patterns = [
        # OpenAI/通义千问等API密钥格式
        r"sk-[a-zA-Z0-9]{32,}",
        r"api[-_]?key['\"]\s*[:=]\s*['\"][^'\"]{16,}['\"]",
        # 密码格式
        r"password['\"]\s*[:=]\s*['\"][^'\"]{4,}['\"]",
        r"passwd['\"]\s*[:=]\s*['\"][^'\"]{4,}['\"]",
        # 数据库连接串
        r"mysql://[^@]+@",
        r"postgresql://[^@]+@",
    ]

    masked_text = text
    for pattern in sensitive_patterns:
        masked_text = re.sub(pattern, "***", masked_text)

    return masked_text

async def business_exception_handler(request: Request, exc: BusinessException):
    """处理自定义业务异常（业务逻辑主动抛出）"""
    logger.warning(
        f"业务异常: {exc.code} - {exc.message}",
        extra={"path": str(request.url), "method": request.method}
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,  # 业务异常HTTP状态码统一200，用业务code区分
        content={"code": exc.code, "message": exc.message, "data": None}
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """处理 HTTP 异常（401/403/404/405等）"""
    # 常见HTTP状态码
    custom_msg_map = {
        401: "请先登录或确保您的token有效",
        403: "无权限访问该接口",
        404: "接口不存在，请检查URL",
        405: "请求方法不支持，请检查请求方式",
        429: "请求过于频繁，请稍后再试",
    }
    # 使用 or 替代 get(..., default) 是因为 exc.detail 可能是空字符串，
    # 但 Python 字典的 get 默认值不会在 key 存在但 value 为 falsy 时生效。
    # 改写成 or 后：key 不存在时返回 exc.detail，key 存在但 value 为空/falsy 时也回退到 exc.detail
    friendly_msg = custom_msg_map.get(exc.status_code) or exc.detail

    logger.warning(
        f"HTTP异常: {exc.status_code} - {friendly_msg}",
        extra={"path": str(request.url), "method": request.method}
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": friendly_msg, "data": None}
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理 FastAPI 参数校验异常（最常见的异常之一）"""
    # 把FastAPI的原始校验错误转换成用户友好的提示
    error_details: list[dict] = exc.errors()
    friendly_msg_parts = []

    for err in error_details:
        # 提取字段名（过滤掉 'body' 等定位前缀）
        field_parts = [str(x) for x in err["loc"] if x not in ("body", "query", "path")]
        field = ".".join(field_parts) if field_parts else "请求参数"
        # 提取错误类型并做友好转换
        msg = err["msg"]
        if err["type"] == "missing":
            msg = "为必填项"
        elif err["type"] == "int_parsing":
            msg = "应为整数类型"
        elif err["type"] == "float_parsing":
            msg = "应为数字类型"

        friendly_msg_parts.append(f"字段「{field}」{msg}")

    friendly_msg = "；".join(friendly_msg_parts)

    # 开发模式保留原始校验信息，生产模式只返回友好提示
    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": "RequestValidationError",
            "raw_errors": error_details,
            "path": str(request.url),
        }

    logger.warning(
        f"参数校验异常: {friendly_msg}",
        extra={"path": str(request.url), "method": request.method}
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": 400, "message": friendly_msg, "data": error_data}
    )


async def integrity_error_handler(request: Request, exc: IntegrityError):
    """处理数据库完整性约束错误（用户名重复、外键关联等）"""
    error_msg = str(exc.orig)
    detail = "数据库完整性约束错误"

    # 针对常见约束错误做友好提示
    if "username_UNIQUE" in error_msg or "Duplicate entry" in error_msg:
        detail = "用户名已存在"
    elif "user_id_fkey" in error_msg or "FOREIGN KEY" in error_msg:
        detail = "关联数据不存在或当前用户无权限"
    elif "email_UNIQUE" in error_msg:
        detail = "邮箱已被注册"

    # 开发模式保留详细错误信息
    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": "IntegrityError",
            "error_detail": mask_sensitive_info(error_msg),
            "path": str(request.url),
        }

    logger.error(
        f"数据库约束异常: {detail}",
        extra={"path": str(request.url), "method": request.method},
        exc_info=exc
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": 400, "message": detail, "data": error_data}
    )


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    """处理 SQLAlchemy 通用数据库错误"""
    # 开发模式保留详细错误信息，生产模式只返回友好提示
    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": type(exc).__name__,
            "error_detail": mask_sensitive_info(str(exc)),
            "traceback": mask_sensitive_info(traceback.format_exc()),
            "path": str(request.url),
        }

    logger.error(
        "数据库操作异常",
        extra={"path": str(request.url), "method": request.method},
        exc_info=exc
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"code": 500, "message": "数据库操作失败，请稍后重试", "data": error_data}
    )


async def general_exception_handler(request: Request, exc: Exception):
    """处理所有未捕获的系统异常（兜底）"""
    # 开发模式保留详细堆栈，生产模式只返回友好提示
    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": type(exc).__name__,
            "error_detail": mask_sensitive_info(str(exc)),
            "traceback": mask_sensitive_info(traceback.format_exc()),
            "path": str(request.url),
        }

    logger.critical(
        "未捕获系统异常",
        extra={"path": str(request.url), "method": request.method},
        exc_info=exc  # 这个参数会把完整堆栈打到日志里，生产环境排错全靠它
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"code": 500, "message": "服务器内部错误，请稍后重试", "data": error_data}
    )
