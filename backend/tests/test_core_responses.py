"""core 层响应工具测试：success_response 与 failed_response（脱敏 + 全部异常处理器）。

调用方式：直接调用处理器函数（均为普通 async 函数），不做整个应用的拉起。
请求对象用 SimpleNamespace 提供 .url / .method 即可。
"""
import json
import types
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.failed_response import (
    BusinessException,
    business_exception_handler,
    general_exception_handler,
    http_exception_handler,
    integrity_error_handler,
    mask_sensitive_info,
    sqlalchemy_error_handler,
    validation_exception_handler,
)
from app.core.success_response import success_response


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _req():
    """处理器只用到 request.url / request.method，用 SimpleNamespace 即可。"""
    return types.SimpleNamespace(url="http://test/", method="GET")


def _body(response):
    return json.loads(response.body)


# ---------------------------------------------------------------------------
# success_response
# ---------------------------------------------------------------------------
class TestSuccessResponse:
    def test_default_message_and_null_data(self):
        resp = success_response()
        assert resp.status_code == 200
        assert resp.media_type == "application/json"
        assert _body(resp) == {"code": 200, "message": "success", "data": None}

    def test_custom_message_and_data(self):
        resp = success_response(message="已保存", data={"id": "n1"})
        assert resp.status_code == 200
        assert _body(resp) == {"code": 200, "message": "已保存", "data": {"id": "n1"}}

    def test_custom_data_only_keeps_default_message(self):
        resp = success_response(data=[1, 2])
        assert _body(resp) == {"code": 200, "message": "success", "data": [1, 2]}

    def test_data_is_jsonable_encoded(self):
        resp = success_response(data={"ts": datetime(2024, 1, 1, 0, 0, 0)})
        body = _body(resp)
        assert body["data"]["ts"] == "2024-01-01T00:00:00"


# ---------------------------------------------------------------------------
# mask_sensitive_info
# ---------------------------------------------------------------------------
class TestMaskSensitiveInfo:
    def test_sk_api_key_masked(self):
        secret = "sk-" + "a" * 36
        masked = mask_sensitive_info(f"调用失败: {secret}")
        assert "***" in masked
        assert secret not in masked

    def test_api_key_json_assignment_masked(self):
        text = '"api_key": "abcdefghijklmnopqrst"'
        masked = mask_sensitive_info(text)
        assert masked == '"***'
        assert "abcdefghijklmnopqrst" not in masked

    def test_api_key_bare_assignment_masked(self):
        text = 'api_key = "abcdefghijklmnopqrst"'
        masked = mask_sensitive_info(text)
        assert masked == "***"
        assert "abcdefghijklmnopqrst" not in masked

    def test_api_key_with_hyphen_masked(self):
        text = '{"api-key": "abcdefghijklmnopqrst"}'
        assert "abcdefghijklmnopqrst" not in mask_sensitive_info(text)

    def test_password_json_assignment_masked(self):
        text = '"password": "hunter2"'
        masked = mask_sensitive_info(text)
        assert masked == '"***'
        assert "hunter2" not in masked

    def test_password_bare_assignment_masked(self):
        text = 'password= "hunter2"'
        masked = mask_sensitive_info(text)
        assert masked == "***"
        assert "hunter2" not in masked

    def test_password_kwarg_style_masked(self):
        text = 'create_user(password="hunter2")'
        masked = mask_sensitive_info(text)
        assert masked == "create_user(***)"
        assert "hunter2" not in masked

    def test_passwd_assignment_masked(self):
        text = '"passwd": "hunter2"'
        masked = mask_sensitive_info(text)
        assert masked == '"***'
        assert "hunter2" not in masked

    def test_mysql_credentials_masked(self):
        text = "mysql://root:s3cr3t@dbhost:3306/app"
        masked = mask_sensitive_info(text)
        assert masked == "***dbhost:3306/app"
        assert "root:s3cr3t" not in masked

    def test_postgresql_credentials_masked(self):
        text = "postgresql://alice:secret@dbhost:5432/mydb"
        masked = mask_sensitive_info(text)
        assert masked == "***dbhost:5432/mydb"
        assert "alice:secret" not in masked

    def test_plain_text_untouched(self):
        text = "普通日志内容，没有敏感信息"
        assert mask_sensitive_info(text) == text

    def test_empty_and_none_passthrough(self):
        assert mask_sensitive_info(None) is None
        assert mask_sensitive_info("") == ""


# ---------------------------------------------------------------------------
# business_exception_handler
# ---------------------------------------------------------------------------
class TestBusinessExceptionHandler:
    async def test_returns_200_with_business_code(self):
        resp = await business_exception_handler(_req(), BusinessException(code=4001, message="余额不足"))
        assert resp.status_code == 200
        assert _body(resp) == {"code": 4001, "message": "余额不足", "data": None}

    async def test_default_code_and_message(self):
        resp = await business_exception_handler(_req(), BusinessException())
        assert resp.status_code == 200
        body = _body(resp)
        assert body["code"] == 400
        assert body["message"] == "出现错误"
        assert body["data"] is None

    async def test_custom_exception_subclass(self):
        class QuotaExceeded(BusinessException):
            pass

        resp = await business_exception_handler(_req(), QuotaExceeded(code=4002, message="配额用尽"))
        assert _body(resp) == {"code": 4002, "message": "配额用尽", "data": None}


# ---------------------------------------------------------------------------
# http_exception_handler
# ---------------------------------------------------------------------------
class TestHttpExceptionHandler:
    @pytest.mark.parametrize(
        "status_code, expected_msg",
        [
            (401, "请先登录或确保您的token有效"),
            (403, "无权限访问该接口"),
            (404, "接口不存在，请检查URL"),
            (405, "请求方法不支持，请检查请求方式"),
            (429, "请求过于频繁，请稍后再试"),
        ],
    )
    async def test_friendly_message_mapping(self, status_code, expected_msg):
        resp = await http_exception_handler(_req(), HTTPException(status_code=status_code, detail="原始detail"))
        assert resp.status_code == status_code
        body = _body(resp)
        assert body["code"] == status_code
        assert body["message"] == expected_msg
        assert body["data"] is None

    async def test_unmapped_status_uses_detail(self):
        resp = await http_exception_handler(_req(), HTTPException(status_code=400, detail="参数不合法"))
        assert resp.status_code == 400
        assert _body(resp)["message"] == "参数不合法"

    async def test_empty_detail_falls_back_to_friendly_map(self):
        # exc.detail 为空串时走 or 回退逻辑，仍然得到友好文案
        resp = await http_exception_handler(_req(), HTTPException(status_code=401, detail=""))
        assert _body(resp)["message"] == "请先登录或确保您的token有效"


# ---------------------------------------------------------------------------
# validation_exception_handler
# ---------------------------------------------------------------------------
class TestValidationExceptionHandler:
    def _make_exc(self):
        return RequestValidationError(
            errors=[
                {"loc": ("body", "username"), "msg": "field required", "type": "missing"},
                {"loc": ("body", "page"), "msg": "Input should be a valid integer", "type": "int_parsing"},
                {"loc": ("body", "score"), "msg": "Input should be a valid number", "type": "float_parsing"},
            ]
        )

    async def test_returns_400_with_friendly_messages(self):
        resp = await validation_exception_handler(_req(), self._make_exc())
        assert resp.status_code == 400
        body = _body(resp)
        assert body["code"] == 400
        assert "字段「username」为必填项" in body["message"]
        assert "字段「page」应为整数类型" in body["message"]
        assert "字段「score」应为数字类型" in body["message"]

    async def test_dev_mode_keeps_raw_errors(self):
        # 测试环境 ENV=dev、DEBUG_MODE=True，data 里保留 raw_errors
        body = _body(await validation_exception_handler(_req(), self._make_exc()))
        assert body["data"]["error_type"] == "RequestValidationError"
        assert len(body["data"]["raw_errors"]) == 3
        assert body["data"]["path"] == "http://test/"

    async def test_missing_field_only(self):
        exc = RequestValidationError(errors=[{"loc": ("body", "email"), "msg": "field required", "type": "missing"}])
        body = _body(await validation_exception_handler(_req(), exc))
        assert body["message"] == "字段「email」为必填项"


# ---------------------------------------------------------------------------
# integrity_error_handler
# ---------------------------------------------------------------------------
class TestIntegrityErrorHandler:
    @staticmethod
    def _exc(message: str) -> IntegrityError:
        return IntegrityError("INSERT INTO ...", {}, Exception(message))

    async def test_duplicate_username(self):
        exc = self._exc("sqlite3.IntegrityError) UNIQUE constraint failed: user.username_UNIQUE")
        resp = await integrity_error_handler(_req(), exc)
        assert resp.status_code == 400
        body = _body(resp)
        assert body["code"] == 400
        assert body["message"] == "用户名已存在"

    async def test_duplicate_entry_username(self):
        exc = self._exc('Duplicate entry "alice" for key "user.username_UNIQUE"')
        assert _body(await integrity_error_handler(_req(), exc))["message"] == "用户名已存在"

    async def test_foreign_key_violation(self):
        exc = self._exc("sqlite3.IntegrityError) FOREIGN KEY constraint failed")
        assert _body(await integrity_error_handler(_req(), exc))["message"] == "关联数据不存在或当前用户无权限"

    async def test_duplicate_email(self):
        exc = self._exc("sqlite3.IntegrityError) UNIQUE constraint failed: user.email_UNIQUE")
        assert _body(await integrity_error_handler(_req(), exc))["message"] == "邮箱已被注册"

    async def test_unknown_constraint_uses_generic_message(self):
        exc = self._exc("some other constraint")
        assert _body(await integrity_error_handler(_req(), exc))["message"] == "数据库完整性约束错误"

    async def test_dev_mode_keeps_masked_detail(self):
        exc = self._exc('UNIQUE constraint failed: user.username_UNIQUE mysql://root:pw@host')
        data = _body(await integrity_error_handler(_req(), exc))["data"]
        assert data["error_type"] == "IntegrityError"
        assert "mysql://root:pw@host" not in data["error_detail"]
        assert data["path"] == "http://test/"


# ---------------------------------------------------------------------------
# sqlalchemy_error_handler
# ---------------------------------------------------------------------------
class TestSQLAlchemyErrorHandler:
    async def test_returns_500_with_generic_message(self):
        exc = OperationalError("SELECT * FROM x", {}, Exception("(pymysql) Can't connect to MySQL server"))
        resp = await sqlalchemy_error_handler(_req(), exc)
        assert resp.status_code == 500
        body = _body(resp)
        assert body["code"] == 500
        assert body["message"] == "数据库操作失败，请稍后重试"
        assert body["data"] is not None

    async def test_dev_mode_keeps_masked_traceback(self):
        exc = OperationalError("SELECT * FROM x", {}, Exception('password= "hunter2"'))
        data = _body(await sqlalchemy_error_handler(_req(), exc))["data"]
        assert data["error_type"] == "OperationalError"
        assert "hunter2" not in data["error_detail"]
        assert "hunter2" not in data["traceback"]
        assert data["path"] == "http://test/"


# ---------------------------------------------------------------------------
# general_exception_handler
# ---------------------------------------------------------------------------
class TestGeneralExceptionHandler:
    async def test_returns_500_with_generic_message(self):
        resp = await general_exception_handler(_req(), ValueError("boom"))
        assert resp.status_code == 500
        body = _body(resp)
        assert body["code"] == 500
        assert body["message"] == "服务器内部错误，请稍后重试"

    async def test_dev_mode_masks_secrets_in_error_and_traceback(self):
        secret = "sk-" + "b" * 36
        data = _body(await general_exception_handler(_req(), ValueError(f"接口报错 {secret}")))[
            "data"
        ]
        assert data["error_type"] == "ValueError"
        assert secret not in data["error_detail"]
        assert "***" in data["error_detail"]
        assert secret not in data["traceback"]
        assert data["path"] == "http://test/"