import os
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from ai_client import AIClientError, ask_deepseek
from app_logger import get_logger
from database import (
    add_message,
    build_conversation_title,
    check_daily_limit,
    cleanup_extra_empty_conversations,
    create_or_reuse_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    get_or_create_user,
    increment_daily_usage,
    init_database,
    list_conversations,
    rename_conversation,
    search_conversations,
)
from opcua_client import read_all_tags_sync
from prompts import PROMPT_VERSION
from tag_config import OPCUA_TAGS


load_dotenv()

logger = get_logger()


# ============================================================
# 工作模式常量
# ============================================================

GENERAL_MODE = "通用问答模式"
PLC_MODE = "PLC实时诊断模式"


# ============================================================
# 环境变量读取
# ============================================================

def get_int_env(
    name: str,
    default: int,
) -> int:
    """
    安全读取整数环境变量。
    """

    value = os.getenv(name, "").strip()

    if not value:
        return default

    try:
        return int(value)

    except ValueError:
        logger.warning(
            "%s配置无效，使用默认值%s",
            name,
            default,
        )

        return default


MAX_HISTORY_MESSAGES = max(
    2,
    get_int_env(
        "MAX_HISTORY_MESSAGES",
        20,
    ),
)


# ============================================================
# 页面设置
# ============================================================

st.set_page_config(
    page_title="矿山设备智能诊断助手",
    page_icon="⛏️",
    layout="wide",
)


# ============================================================
# 初始化数据库
# ============================================================

if "database_initialized" not in st.session_state:
    init_database()

    st.session_state.database_initialized = True

    logger.info(
        "SQLite数据库初始化完成"
    )


# ============================================================
# 初始化本地用户
# ============================================================

if "current_user" not in st.session_state:
    local_user_key = os.getenv(
        "LOCAL_USER_KEY",
        "local-desktop-user",
    ).strip()

    local_display_name = os.getenv(
        "LOCAL_DISPLAY_NAME",
        "本地用户",
    ).strip()

    local_daily_limit = max(
        0,
        get_int_env(
            "LOCAL_DAILY_LIMIT",
            50,
        ),
    )

    st.session_state.current_user = (
        get_or_create_user(
            user_key=local_user_key,
            display_name=local_display_name,
            auth_source="local",
            role="user",
            daily_limit=local_daily_limit,
        )
    )

    logger.info(
        "本地用户加载完成，user_key=%s",
        local_user_key,
    )


current_user = st.session_state.current_user
current_user_id = int(current_user["id"])


# ============================================================
# 清理已有的多余空会话
# ============================================================

if "empty_conversations_cleaned" not in st.session_state:
    deleted_empty_count = (
        cleanup_extra_empty_conversations(
            user_id=current_user_id,
            keep_conversation_id=(
                st.session_state.get(
                    "current_conversation_id"
                )
            ),
        )
    )

    if deleted_empty_count > 0:
        logger.info(
            "已清理多余空会话，数量=%s",
            deleted_empty_count,
        )

    st.session_state.empty_conversations_cleaned = True


# ============================================================
# 确保当前会话有效
# ============================================================

def ensure_current_conversation() -> dict[str, Any]:
    """
    保证当前用户始终有一个有效会话。
    """

    conversation_id = st.session_state.get(
        "current_conversation_id"
    )

    if conversation_id:
        conversation = get_conversation(
            conversation_id=conversation_id,
            user_id=current_user_id,
        )

        if conversation is not None:
            return conversation

    conversations = list_conversations(
        user_id=current_user_id,
        limit=50,
    )

    if conversations:
        conversation = conversations[0]

    else:
        conversation, _ = (
            create_or_reuse_conversation(
                user_id=current_user_id,
                title="新对话",
                work_mode=GENERAL_MODE,
            )
        )

    st.session_state.current_conversation_id = (
        conversation["id"]
    )

    return conversation


current_conversation = ensure_current_conversation()

current_conversation_id = (
    current_conversation["id"]
)

current_work_mode = (
    current_conversation["work_mode"]
)


# ============================================================
# 页面标题
# ============================================================

st.title("⛏️ 矿山设备智能诊断助手")

st.markdown(
    """
    <div style="
        font-size: 20px;
        font-weight: 600;
        color: #4A5568;
        margin-top: -8px;
        margin-bottom: 8px;
    ">
        烟台东方冶金设计研究院有限公司
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "V2.0 Alpha：历史会话管理开发版本"
)


# ============================================================
# 配置检查
# ============================================================

deepseek_key_ok = bool(
    os.getenv(
        "DEEPSEEK_API_KEY",
        "",
    ).strip()
)

opcua_url = os.getenv(
    "OPCUA_SERVER_URL",
    "",
).strip()

opcua_config_ok = bool(opcua_url)
tag_config_ok = len(OPCUA_TAGS) > 0

usage_status = check_daily_limit(
    current_user_id
)


# ============================================================
# 删除确认状态
# ============================================================

if "pending_delete_conversation_id" not in st.session_state:
    st.session_state.pending_delete_conversation_id = None

if "pending_delete_conversation_title" not in st.session_state:
    st.session_state.pending_delete_conversation_title = None


# ============================================================
# 侧边栏
# ============================================================

with st.sidebar:
    st.header("用户与会话")

    # --------------------------------------------------------
    # 当前用户
    # --------------------------------------------------------

    st.markdown("### 当前用户")

    st.write(
        f"用户名：`{current_user['display_name']}`"
    )

    st.write(
        f"用户角色：`{current_user['role']}`"
    )

    st.write(
        f"今日已调用：`{usage_status['used']}` 次"
    )

    if usage_status["remaining"] is None:
        st.write("今日剩余：`不限制`")

    else:
        st.write(
            f"今日剩余："
            f"`{usage_status['remaining']}` 次"
        )

    st.divider()

    # --------------------------------------------------------
    # 新建对话
    # --------------------------------------------------------

    st.markdown("### 新建对话")

    if st.button(
        "新建通用问答",
        use_container_width=True,
        type="primary",
    ):
        new_conversation, created = (
            create_or_reuse_conversation(
                user_id=current_user_id,
                title="新对话",
                work_mode=GENERAL_MODE,
            )
        )

        st.session_state.current_conversation_id = (
            new_conversation["id"]
        )

        if created:
            logger.info(
                "创建通用问答会话，会话ID=%s",
                new_conversation["id"],
            )

        else:
            logger.info(
                "复用已有通用问答空会话，会话ID=%s",
                new_conversation["id"],
            )

        st.rerun()

    if st.button(
        "新建PLC实时诊断",
        use_container_width=True,
    ):
        new_conversation, created = (
            create_or_reuse_conversation(
                user_id=current_user_id,
                title="新对话",
                work_mode=PLC_MODE,
            )
        )

        st.session_state.current_conversation_id = (
            new_conversation["id"]
        )

        if created:
            logger.info(
                "创建PLC诊断会话，会话ID=%s",
                new_conversation["id"],
            )

        else:
            logger.info(
                "复用已有PLC诊断空会话，会话ID=%s",
                new_conversation["id"],
            )

        st.rerun()

    st.divider()

    # --------------------------------------------------------
    # 当前会话
    # --------------------------------------------------------

    st.markdown("### 当前会话")

    st.write(
        f"标题：`{current_conversation['title']}`"
    )

    st.write(
        f"模式：`{current_work_mode}`"
    )

    auto_fallback = True

    if current_work_mode == PLC_MODE:
        auto_fallback = st.checkbox(
            "PLC断线时自动降级为通用问答",
            value=True,
            key=(
                f"auto_fallback_"
                f"{current_conversation_id}"
            ),
        )

    # --------------------------------------------------------
    # 当前会话重命名
    # --------------------------------------------------------

    with st.expander(
        "重命名当前会话",
        expanded=False,
    ):
        new_conversation_title = st.text_input(
            "新会话标题",
            value=current_conversation["title"],
            max_chars=50,
            key=(
                f"rename_input_"
                f"{current_conversation_id}"
            ),
        )

        if st.button(
            "保存新标题",
            use_container_width=True,
            key=(
                f"rename_save_"
                f"{current_conversation_id}"
            ),
        ):
            normalized_title = (
                new_conversation_title.strip()
            )

            if not normalized_title:
                st.error(
                    "会话标题不能为空。"
                )

            else:
                renamed = rename_conversation(
                    conversation_id=(
                        current_conversation_id
                    ),
                    user_id=current_user_id,
                    new_title=normalized_title,
                )

                if renamed:
                    logger.info(
                        "会话重命名成功，会话ID=%s",
                        current_conversation_id,
                    )

                    st.success(
                        "会话标题修改成功。"
                    )

                    st.rerun()

                else:
                    st.error(
                        "会话标题修改失败。"
                    )

    st.divider()

    # --------------------------------------------------------
    # 历史会话搜索
    # --------------------------------------------------------

    st.markdown("### 历史会话")

    conversation_search_query = st.text_input(
        "搜索标题或聊天内容",
        placeholder="例如：TON、PP01、变频器",
        key="conversation_search_query",
    )

    conversations = search_conversations(
        user_id=current_user_id,
        query=conversation_search_query,
        limit=50,
    )

    if conversation_search_query:
        st.caption(
            f"找到 {len(conversations)} 个相关会话"
        )

    if not conversations:
        st.info("没有找到相关历史会话。")

    # --------------------------------------------------------
    # 历史会话列表
    # --------------------------------------------------------

    for conversation in conversations:
        conversation_id = conversation["id"]
        conversation_title = conversation["title"]
        conversation_mode = conversation["work_mode"]
        message_count = int(
            conversation["message_count"]
        )

        if conversation_mode == GENERAL_MODE:
            mode_icon = "💬"
        else:
            mode_icon = "📡"

        if (
            conversation_id
            == current_conversation_id
        ):
            active_mark = "▶ "
        else:
            active_mark = ""

        select_column, delete_column = st.columns(
            [6, 1]
        )

        with select_column:
            if st.button(
                (
                    f"{active_mark}{mode_icon} "
                    f"{conversation_title}"
                ),
                key=(
                    f"history_select_"
                    f"{conversation_id}"
                ),
                use_container_width=True,
            ):
                st.session_state.current_conversation_id = (
                    conversation_id
                )

                logger.info(
                    "切换历史会话，会话ID=%s",
                    conversation_id,
                )

                st.rerun()

        with delete_column:
            if st.button(
                "🗑️",
                key=(
                    f"history_delete_"
                    f"{conversation_id}"
                ),
                help="删除该会话",
                use_container_width=True,
            ):
                st.session_state.pending_delete_conversation_id = (
                    conversation_id
                )

                st.session_state.pending_delete_conversation_title = (
                    conversation_title
                )

                st.rerun()

        st.caption(
            f"{conversation_mode}｜"
            f"{message_count}条消息｜"
            f"{conversation['updated_at']}"
        )

    # --------------------------------------------------------
    # 删除二次确认
    # --------------------------------------------------------

    pending_delete_id = (
        st.session_state.pending_delete_conversation_id
    )

    pending_delete_title = (
        st.session_state.pending_delete_conversation_title
    )

    if pending_delete_id:
        st.divider()

        st.warning(
            "确定删除会话："
            f"“{pending_delete_title}”吗？\n\n"
            "删除后，该会话中的全部聊天消息"
            "也会被永久删除。"
        )

        confirm_column, cancel_column = st.columns(
            2
        )

        with confirm_column:
            if st.button(
                "确认删除",
                type="primary",
                use_container_width=True,
                key="confirm_delete_conversation",
            ):
                deleted = delete_conversation(
                    conversation_id=(
                        pending_delete_id
                    ),
                    user_id=current_user_id,
                )

                if deleted:
                    logger.info(
                        "会话删除成功，会话ID=%s",
                        pending_delete_id,
                    )

                    if (
                        pending_delete_id
                        == current_conversation_id
                    ):
                        st.session_state.pop(
                            "current_conversation_id",
                            None,
                        )

                    st.session_state.pending_delete_conversation_id = (
                        None
                    )

                    st.session_state.pending_delete_conversation_title = (
                        None
                    )

                    st.rerun()

                else:
                    st.error(
                        "会话删除失败或会话不存在。"
                    )

        with cancel_column:
            if st.button(
                "取消",
                use_container_width=True,
                key="cancel_delete_conversation",
            ):
                st.session_state.pending_delete_conversation_id = (
                    None
                )

                st.session_state.pending_delete_conversation_title = (
                    None
                )

                st.rerun()

    st.divider()

    # --------------------------------------------------------
    # 系统配置
    # --------------------------------------------------------

    with st.expander(
        "系统配置自检",
        expanded=False,
    ):
        if deepseek_key_ok:
            st.write("DeepSeek API：`已配置`")
        else:
            st.write("DeepSeek API：`未配置`")

        if opcua_config_ok:
            st.write("OPC UA地址：`已配置`")
        else:
            st.write("OPC UA地址：`未配置`")

        if tag_config_ok:
            st.write(
                f"PLC变量：`{len(OPCUA_TAGS)}个`"
            )
        else:
            st.write("PLC变量：`未配置`")

        st.write("OPC UA模式：`只读`")

        st.write(
            f"提示词版本：`{PROMPT_VERSION}`"
        )

        st.write(
            f"最大上下文："
            f"`{MAX_HISTORY_MESSAGES}条消息`"
        )


# ============================================================
# 消息元数据格式化
# ============================================================

def format_message_meta(
    meta: dict[str, Any] | None,
) -> str:
    """
    格式化AI回答下方的元数据。
    """

    if not meta:
        return ""

    parts: list[str] = []

    work_mode = meta.get("work_mode")

    if work_mode:
        parts.append(
            f"工作模式：{work_mode}"
        )

    if meta.get("degraded_mode"):
        parts.append(
            "已自动降级为通用问答"
        )

    snapshot_time = meta.get(
        "plc_snapshot_time"
    )

    if snapshot_time:
        parts.append(
            f"PLC快照时间：{snapshot_time}"
        )

    success_count = meta.get(
        "plc_success_count"
    )

    total_count = meta.get(
        "plc_total_count"
    )

    if (
        success_count is not None
        and total_count is not None
    ):
        parts.append(
            f"PLC读取："
            f"{success_count}/{total_count}"
        )

    prompt_version = meta.get(
        "prompt_version"
    )

    if prompt_version:
        parts.append(
            f"提示词：{prompt_version}"
        )

    return "｜".join(parts)


# ============================================================
# 主页面当前会话信息
# ============================================================

st.subheader(
    current_conversation["title"]
)

if current_work_mode == GENERAL_MODE:
    st.info(
        "当前为通用问答会话。"
        "本模式不会连接PLC。"
    )

else:
    st.info(
        "当前为PLC实时诊断会话。"
        "每次提问时都会读取PLC最新状态快照。"
    )


# ============================================================
# 显示数据库中的历史消息
# ============================================================

stored_messages = get_messages(
    conversation_id=current_conversation_id
)

for message in stored_messages:
    with st.chat_message(
        message["role"]
    ):
        st.markdown(
            message["content"]
        )

        meta_text = format_message_meta(
            message.get("meta")
        )

        if meta_text:
            st.caption(meta_text)


# ============================================================
# 输入框
# ============================================================

if current_work_mode == GENERAL_MODE:
    input_placeholder = (
        "请输入PLC、WinCC、仪表或矿山自动化问题"
    )

else:
    input_placeholder = (
        "请输入设备状态查询或PLC实时诊断问题"
    )


user_question = st.chat_input(
    input_placeholder
)


# ============================================================
# 处理用户问题
# ============================================================

if user_question:
    latest_usage_status = check_daily_limit(
        current_user_id
    )

    if not latest_usage_status["allowed"]:
        st.error(
            "今天的AI调用次数已经达到上限。"
        )

        st.stop()

    # 保存用户消息
    add_message(
        conversation_id=current_conversation_id,
        role="user",
        content=user_question,
        meta={
            "work_mode": current_work_mode,
        },
    )

    # 第一条消息自动生成标题
    if current_conversation["title"] == "新对话":
        generated_title = build_conversation_title(
            user_question,
            max_length=24,
        )

        rename_conversation(
            conversation_id=current_conversation_id,
            user_id=current_user_id,
            new_title=generated_title,
        )

    with st.chat_message("user"):
        st.markdown(user_question)

    with st.chat_message("assistant"):
        with st.spinner(
            "正在分析，请稍候……"
        ):
            try:
                recent_db_messages = get_messages(
                    conversation_id=(
                        current_conversation_id
                    ),
                    limit=MAX_HISTORY_MESSAGES,
                )

                recent_messages = [
                    {
                        "role": message["role"],
                        "content": message["content"],
                    }
                    for message in recent_db_messages
                    if message["role"] in {
                        "user",
                        "assistant",
                    }
                ]

                assistant_meta: dict[str, Any] = {
                    "work_mode": current_work_mode,
                    "prompt_version": PROMPT_VERSION,
                    "degraded_mode": False,
                    "plc_data_used": False,
                }

                # ====================================================
                # 通用问答
                # ====================================================

                if current_work_mode == GENERAL_MODE:
                    mode_context = {
                        "role": "system",
                        "content": (
                            "当前为通用问答模式。"
                            "本次没有读取PLC实时数据。"
                            "用户询问当前设备实时状态时，"
                            "必须说明没有实时PLC数据，"
                            "不得虚构设备状态。"
                        ),
                    }

                    answer = ask_deepseek(
                        chat_history=[
                            mode_context,
                            *recent_messages,
                        ],
                        plc_data=None,
                    )

                # ====================================================
                # PLC实时诊断
                # ====================================================

                else:
                    latest_plc_data = None
                    plc_read_error: Exception | None = None
                    degraded_mode = False

                    total_count = 0
                    success_count = 0
                    failed_count = 0
                    snapshot_time = "未知"

                    try:
                        latest_plc_data = (
                            read_all_tags_sync()
                        )

                        total_count = len(
                            latest_plc_data
                        )

                        success_count = sum(
                            1
                            for result
                            in latest_plc_data.values()
                            if result.get("success")
                        )

                        failed_count = (
                            total_count
                            - success_count
                        )

                        for result in (
                            latest_plc_data.values()
                        ):
                            if result.get(
                                "snapshot_time"
                            ):
                                snapshot_time = result[
                                    "snapshot_time"
                                ]

                                break

                        if success_count == 0:
                            raise ConnectionError(
                                "没有成功读取任何PLC变量。"
                            )

                    except Exception as error:
                        plc_read_error = error

                        logger.error(
                            "PLC实时读取失败：%s",
                            error,
                        )

                        if auto_fallback:
                            degraded_mode = True
                            latest_plc_data = None

                            st.warning(
                                "PLC读取失败，"
                                "本次已自动降级为通用问答。"
                            )

                        else:
                            raise ConnectionError(
                                "PLC读取失败，"
                                "且未启用自动降级。"
                                f"错误：{error}"
                            ) from error

                    if not degraded_mode:
                        if failed_count > 0:
                            st.warning(
                                f"成功读取{success_count}个变量，"
                                f"失败{failed_count}个变量。"
                            )

                        mode_context = {
                            "role": "system",
                            "content": (
                                "当前为PLC实时诊断模式。"
                                "本次PLC快照是唯一有效实时数据。"
                                "历史聊天中的PLC数值均为旧快照。"
                            ),
                        }

                        answer = ask_deepseek(
                            chat_history=[
                                mode_context,
                                *recent_messages,
                            ],
                            plc_data=latest_plc_data,
                        )

                        assistant_meta.update(
                            {
                                "plc_data_used": True,
                                "plc_snapshot_time": (
                                    snapshot_time
                                ),
                                "plc_success_count": (
                                    success_count
                                ),
                                "plc_total_count": (
                                    total_count
                                ),
                                "plc_failed_count": (
                                    failed_count
                                ),
                            }
                        )

                    else:
                        mode_context = {
                            "role": "system",
                            "content": (
                                "PLC实时读取失败，"
                                "本次已经降级为通用问答。"
                                "没有有效的实时PLC数据。"
                                "不得推测设备当前状态。"
                                "可以提供通用检查步骤。"
                            ),
                        }

                        answer = ask_deepseek(
                            chat_history=[
                                mode_context,
                                *recent_messages,
                            ],
                            plc_data=None,
                        )

                        error_type = (
                            type(plc_read_error).__name__
                            if plc_read_error
                            else "未知错误"
                        )

                        assistant_meta.update(
                            {
                                "degraded_mode": True,
                                "plc_data_used": False,
                                "plc_error_type": error_type,
                            }
                        )

                # 保存AI回答
                add_message(
                    conversation_id=current_conversation_id,
                    role="assistant",
                    content=answer,
                    meta=assistant_meta,
                )

                # 成功调用后增加次数
                increment_daily_usage(
                    current_user_id
                )

                logger.info(
                    "消息保存完成，用户ID=%s，"
                    "会话ID=%s，模式=%s",
                    current_user_id,
                    current_conversation_id,
                    current_work_mode,
                )

                st.rerun()

            except AIClientError as error:
                st.error(
                    f"AI服务错误：{error}"
                )

            except ConnectionError as error:
                logger.error(
                    "PLC连接或读取失败：%s",
                    error,
                )

                st.error(
                    f"PLC数据读取失败：{error}"
                )

            except Exception as error:
                logger.exception(
                    "请求处理异常：%s",
                    error,
                )

                st.error(
                    "请求处理失败："
                    f"{type(error).__name__}：{error}"
                )