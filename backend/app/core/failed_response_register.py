from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.failed_response import (
    BusinessException,
    business_exception_handler,
    general_exception_handler,
    http_exception_handler,
    integrity_error_handler,
    sqlalchemy_error_handler,
    validation_exception_handler,
)


def register_exception_handlers(app):
    """注册全局异常处理器"""
    app.add_exception_handler(HTTPException, http_exception_handler)  # 使用正确的HTTPException类
    app.add_exception_handler(IntegrityError, integrity_error_handler)  # 处理数据库完整性错误
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)  # 处理SQLAlchemy异常
    app.add_exception_handler(BusinessException, business_exception_handler)  # 处理业务异常
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # 处理参数校验异常
    app.add_exception_handler(Exception, general_exception_handler)  # 处理其他异常
