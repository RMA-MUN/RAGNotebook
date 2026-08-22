"""failed_response_register 测试：验证 register_exception_handlers 正确接线各处理器。"""
import httpx
from fastapi import FastAPI, HTTPException
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
from app.core.failed_response_register import register_exception_handlers

ALL_EXPECTED = {
    HTTPException: http_exception_handler,
    RequestValidationError: validation_exception_handler,
    IntegrityError: integrity_error_handler,
    SQLAlchemyError: sqlalchemy_error_handler,
    BusinessException: business_exception_handler,
    Exception: general_exception_handler,
}


class TestRegisterExceptionHandlers:
    def _fresh_app(self) -> FastAPI:
        app = FastAPI()
        register_exception_handlers(app)
        return app

    def test_all_six_handlers_wired(self):
        app = self._fresh_app()
        for exc_type, handler in ALL_EXPECTED.items():
            assert app.exception_handlers[exc_type] is handler, f"{exc_type} 未注册到 {handler.__name__}"

    def test_register_installs_our_handlers(self):
        # 全新 FastAPI 应用不预装我们的处理器，注册后按异常类型正确接线。
        app = FastAPI()
        assert http_exception_handler not in app.exception_handlers.values()
        assert validation_exception_handler not in app.exception_handlers.values()

        register_exception_handlers(app)
        assert app.exception_handlers[HTTPException] is http_exception_handler
        assert app.exception_handlers[RequestValidationError] is validation_exception_handler

    def test_returns_none(self):
        # 注册函数本身无返回值
        assert register_exception_handlers(FastAPI()) is None

    async def test_registered_handlers_receive_exceptions(self):
        # 组合验证：通过最小 FastAPI 应用的真实分发路径，确认处理器被调用并返回统一信封。
        app = self._fresh_app()

        @app.get("/business")
        async def boom_business():
            raise BusinessException(code=4001, message="业务失败")

        @app.get("/http")
        async def boom_http():
            raise HTTPException(status_code=404, detail="nope")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            business_resp = await client.get("/business")
            http_resp = await client.get("/http")

        assert business_resp.status_code == 200
        assert business_resp.json() == {"code": 4001, "message": "业务失败", "data": None}

        assert http_resp.status_code == 404
        body = http_resp.json()
        assert body["code"] == 404
        assert body["message"] == "接口不存在，请检查URL"
        assert body["data"] is None