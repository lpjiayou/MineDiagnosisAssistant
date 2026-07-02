from getpass import getpass

from auth_security import AuthValidationError
from auth_service import (
    create_or_reset_admin,
    init_auth_schema,
)
from database import init_database


def main() -> None:
    print("=" * 60)
    print("矿山设备智能诊断助手")
    print("初始管理员创建工具")
    print("=" * 60)

    init_database()
    init_auth_schema()

    username = input(
        "请输入管理员用户名："
    ).strip()

    display_name = input(
        "请输入管理员显示名称："
    ).strip()

    password = getpass(
        "请输入管理员密码："
    )

    confirm_password = getpass(
        "请再次输入管理员密码："
    )

    if password != confirm_password:
        print("两次输入的密码不一致。")
        return

    try:
        user = create_or_reset_admin(
            username=username,
            display_name=display_name,
            password=password,
        )

    except (
        AuthValidationError,
        ValueError,
        RuntimeError,
    ) as error:
        print(f"管理员创建失败：{error}")
        return

    print()
    print("管理员创建或更新成功")
    print(f"用户ID：{user['id']}")
    print(f"用户名：{user['user_key']}")
    print(f"显示名称：{user['display_name']}")
    print(f"角色：{user['role']}")
    print("状态：已启用")
    print("=" * 60)


if __name__ == "__main__":
    main()