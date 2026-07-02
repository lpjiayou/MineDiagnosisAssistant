from database import (
    add_message,
    build_conversation_title,
    check_daily_limit,
    create_conversation,
    get_messages,
    get_or_create_user,
    increment_daily_usage,
    init_database,
    list_conversations,
)


def main() -> None:
    print("=" * 60)
    print("开始数据库基础功能测试")
    print("=" * 60)

    # 1. 初始化数据库
    init_database()

    print("1. 数据库初始化成功")

    # 2. 创建测试用户
    user = get_or_create_user(
        user_key="phase2-test-user",
        display_name="第二阶段测试用户",
        auth_source="local",
        role="user",
        daily_limit=20,
    )

    print(
        f"2. 用户读取成功："
        f"ID={user['id']}，"
        f"名称={user['display_name']}"
    )

    # 3. 创建测试会话
    first_question = (
        "请介绍S7-1500中TON定时器的使用方法"
    )

    conversation = create_conversation(
        user_id=user["id"],
        title=build_conversation_title(
            first_question
        ),
        work_mode="通用问答模式",
    )

    print(
        f"3. 会话创建成功："
        f"{conversation['title']}"
    )

    # 4. 保存用户消息
    add_message(
        conversation_id=conversation["id"],
        role="user",
        content=first_question,
        meta={
            "work_mode": "通用问答模式",
        },
    )

    # 5. 保存AI回答
    add_message(
        conversation_id=conversation["id"],
        role="assistant",
        content=(
            "TON是IEC接通延时定时器，"
            "当IN保持为TRUE时开始计时。"
        ),
        meta={
            "prompt_version": "1.0.0",
            "plc_data_used": False,
        },
    )

    print("4. 用户消息和AI消息保存成功")

    # 6. 读取消息
    messages = get_messages(
        conversation["id"]
    )

    print(
        f"5. 当前会话消息数量："
        f"{len(messages)}"
    )

    for message in messages:
        print(
            f"   [{message['role']}] "
            f"{message['content']}"
        )

    # 7. 查询历史会话
    conversations = list_conversations(
        user_id=user["id"]
    )

    print(
        f"6. 当前用户历史会话数量："
        f"{len(conversations)}"
    )

    # 8. 检查每日次数
    usage_before = check_daily_limit(
        user["id"]
    )

    print(
        "7. 调用次数增加前："
        f"已用={usage_before['used']}，"
        f"剩余={usage_before['remaining']}"
    )

    new_usage = increment_daily_usage(
        user["id"]
    )

    usage_after = check_daily_limit(
        user["id"]
    )

    print(
        "8. 调用次数增加后："
        f"已用={new_usage}，"
        f"剩余={usage_after['remaining']}"
    )

    print("=" * 60)
    print("数据库基础功能测试通过")
    print("=" * 60)


if __name__ == "__main__":
    main()