import gc
import os
import tempfile
import time
from pathlib import Path


temporary_directory = tempfile.TemporaryDirectory()

test_database_path = (
    Path(temporary_directory.name)
    / "usage_test.db"
)

os.environ["APP_DB_PATH"] = str(
    test_database_path
)


from database import (  # noqa: E402
    create_conversation,
    get_or_create_user,
    init_database,
)
from usage_service import (  # noqa: E402
    finalize_ai_request,
    get_usage_status,
    init_usage_schema,
    reserve_ai_request,
)


def main() -> None:
    print("=" * 60)
    print("开始每日调用限制测试")
    print("=" * 60)

    init_database()
    init_usage_schema()

    user = get_or_create_user(
        user_key="usage-test-user",
        display_name="调用限制测试用户",
        auth_source="local",
        role="user",
        daily_limit=2,
    )

    conversation = create_conversation(
        user_id=user["id"],
        title="调用限制测试",
        work_mode="通用问答模式",
    )

    first = reserve_ai_request(
        request_id="request-1",
        user_id=user["id"],
        conversation_id=conversation["id"],
    )

    assert first["allowed"] is True

    second = reserve_ai_request(
        request_id="request-2",
        user_id=user["id"],
        conversation_id=conversation["id"],
    )

    assert second["allowed"] is True

    third_blocked = reserve_ai_request(
        request_id="request-3",
        user_id=user["id"],
        conversation_id=conversation["id"],
    )

    assert third_blocked["allowed"] is False

    print("1. 并发预占不能突破每日上限")

    finalize_ai_request(
        request_id="request-1",
        success=False,
        error_type="TestFailure",
    )

    third_allowed = reserve_ai_request(
        request_id="request-3",
        user_id=user["id"],
        conversation_id=conversation["id"],
    )

    assert third_allowed["allowed"] is True

    print("2. 失败请求不会占用成功调用次数")

    finalize_ai_request(
        request_id="request-2",
        success=True,
    )

    # 重复结算不能重复增加次数。
    duplicate_finalize = finalize_ai_request(
        request_id="request-2",
        success=True,
    )

    assert (
        duplicate_finalize[
            "already_finalized"
        ]
        is True
    )

    finalize_ai_request(
        request_id="request-3",
        success=True,
    )

    status = get_usage_status(
        user["id"]
    )

    assert status["used"] == 2
    assert status["remaining"] == 0
    assert status["allowed"] is False

    print("3. 成功请求准确计数")
    print("4. 重复结算不会重复计数")

    fourth = reserve_ai_request(
        request_id="request-4",
        user_id=user["id"],
        conversation_id=conversation["id"],
    )

    assert fourth["allowed"] is False

    print("5. 达到上限后拒绝新请求")

    admin = get_or_create_user(
        user_key="usage-test-admin",
        display_name="测试管理员",
        auth_source="local",
        role="admin",
        daily_limit=0,
    )

    admin_conversation = create_conversation(
        user_id=admin["id"],
        title="管理员调用测试",
        work_mode="通用问答模式",
    )

    for index in range(5):
        request_id = (
            f"admin-request-{index}"
        )

        reservation = reserve_ai_request(
            request_id=request_id,
            user_id=admin["id"],
            conversation_id=(
                admin_conversation["id"]
            ),
        )

        assert reservation["allowed"] is True

        finalize_ai_request(
            request_id=request_id,
            success=True,
        )

    admin_status = get_usage_status(
        admin["id"]
    )

    assert admin_status["used"] == 5
    assert admin_status["remaining"] is None
    assert admin_status["allowed"] is True

    print("6. 管理员不受每日上限限制")

    print("=" * 60)
    print("每日调用限制测试全部通过")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()

    finally:
        gc.collect()
        time.sleep(0.2)
        temporary_directory.cleanup()