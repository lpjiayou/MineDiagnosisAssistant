import os
import tempfile
from pathlib import Path


# 必须在导入database之前设置测试数据库路径。
temporary_directory = tempfile.TemporaryDirectory()

test_database_path = (
    Path(temporary_directory.name)
    / "auth_test.db"
)

os.environ["APP_DB_PATH"] = str(
    test_database_path
)


from auth_service import (  # noqa: E402
    authenticate_local_user,
    create_local_user,
    init_auth_schema,
)
from database import init_database  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("开始用户认证基础测试")
    print("=" * 60)

    init_database()
    init_auth_schema()

    print("1. 数据库和认证字段初始化成功")

    user = create_local_user(
        username="test.user",
        display_name="认证测试用户",
        password="TestPassword123",
        role="user",
        daily_limit=20,
    )

    print(
        "2. 测试用户创建成功："
        f"ID={user['id']}，"
        f"用户名={user['user_key']}"
    )

    success_user = authenticate_local_user(
        username="test.user",
        password="TestPassword123",
    )

    assert success_user is not None

    print("3. 正确密码登录测试通过")

    wrong_password_user = (
        authenticate_local_user(
            username="test.user",
            password="WrongPassword123",
        )
    )

    assert wrong_password_user is None

    print("4. 错误密码拒绝测试通过")

    uppercase_user = authenticate_local_user(
        username="TEST.USER",
        password="TestPassword123",
    )

    assert uppercase_user is not None

    print("5. 用户名大小写规范化测试通过")

    try:
        create_local_user(
            username="test.user",
            display_name="重复用户",
            password="AnotherPassword123",
        )

        raise AssertionError(
            "重复用户名没有被拒绝。"
        )

    except ValueError:
        print("6. 重复用户名拒绝测试通过")

    print("=" * 60)
    print("用户认证基础测试全部通过")
    print(f"临时测试数据库：{test_database_path}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()

    finally:
        temporary_directory.cleanup()