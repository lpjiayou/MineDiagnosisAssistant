import os
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from ai_client import AIClientError, ask_deepseek
from app_logger import get_logger
from auth_security import AuthValidationError
from auth_service import (
    authenticate_local_user,
    change_local_password,
    create_local_user,
    init_auth_schema,
)
from database import (
    add_message,
    build_conversation_title,
    check_daily_limit,
    cleanup_extra_empty_conversations,
    create_or_reuse_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    get_user_by_id,
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
# 工作模式
# ============================================================

GENERAL_MODE = "通用问答模式"
PLC_MODE = "PLC实时诊断模式"


# ============================================================
# 环境变量工具
# ============================================================

def get_int_env(
    name: str,
    default: int,
) -> int:
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


def get_bool_env(
    name: str,
    default: bool,
) -> bool:
    value = os.getenv(name, "").strip().lower()

    if not value:
        return default

    if value in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if value in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

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

ALLOW_SELF_REGISTER = get_bool_env(
    "ALLOW_SELF_REGISTER",
    True,
)

REGISTER_DAILY_LIMIT = max(
    0,
    get_int_env(
        "REGISTER_DAILY_LIMIT",
        50,
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
# 页面公共标题
# ============================================================

def render_brand_header() -> None:
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


# ============================================================
# 用户对象脱敏
# ============================================================

def build_session_user(
    user: dict[str, Any],
) -> dict[str, Any]:
    """
    只把页面真正需要的用户信息放入Session State。

    password_hash等敏感字段不会进入页面会话状态。
    """

    return {
        "id": int(user["id"]),
        "user_key": user["user_key"],
        "display_name": user["display_name"],
        "auth_source": user["auth_source"],
        "role": user["role"],
        "is_active": int(user["is_active"]),
        "daily_limit": int(user["daily_limit"]),
    }


# ============================================================
# 清理用户工作区状态
# ============================================================

def clear_user_workspace_state() -> None:
    """
    登录其他用户时，清理上一个用户的页面状态。

    防止旧用户的会话ID、搜索词和删除确认状态
    被新用户继续使用。
    """

    keys_to_remove = [
        "current_conversation_id",
        "empty_conversations_cleaned",
        "pending_delete_conversation_id",
        "pending_delete_conversation_title",
        "conversation_search_query",
    ]

    for key in keys_to_remove:
        st.session_state.pop(
            key,
            None,
        )


def logout_user(
    notice: str = "已经安全退出登录。",
) -> None:
    """
    清空当前浏览器会话中的全部登录状态。
    """

    for key in list(
        st.session_state.keys()
    ):
        del st.session_state[key]

    st.session_state.auth_notice = notice

    logger.info(
        "用户退出登录"
    )

    st.rerun()


# ============================================================
# 初始化数据库和认证结构
# ============================================================

try:
    init_database()
    init_auth_schema()

except Exception as error:
    render_brand_header()

    st.error(
        "数据库初始化失败："
        f"{type(error).__name__}：{error}"
    )

    logger.exception(
        "数据库或认证结构初始化失败：%s",
        error,
    )

    st.stop()


# ============================================================
# 登录与注册页面
# ============================================================

def render_auth_page() -> None:
    render_brand_header()

    st.caption(
        "V2.0 Alpha：用户登录与多用户数据隔离版本"
    )

    auth_notice = st.session_state.pop(
        "auth_notice",
        None,
    )

    if auth_notice:
        st.success(auth_notice)

    left_column, center_column, right_column = (
        st.columns(
            [1, 1.2, 1]
        )
    )

    with center_column:
        st.markdown("## 用户认证")

        if ALLOW_SELF_REGISTER:
            auth_mode = st.radio(
                "请选择",
                options=[
                    "登录",
                    "注册",
                ],
                horizontal=True,
                label_visibility="collapsed",
            )

        else:
            auth_mode = "登录"

        # ----------------------------------------------------
        # 登录
        # ----------------------------------------------------

        if auth_mode == "登录":
            with st.form(
                "login_form",
                clear_on_submit=False,
            ):
                login_username = st.text_input(
                    "用户名",
                    placeholder="请输入用户名",
                    max_chars=32,
                )

                login_password = st.text_input(
                    "密码",
                    type="password",
                    placeholder="请输入密码",
                )

                login_submitted = (
                    st.form_submit_button(
                        "登录",
                        type="primary",
                        use_container_width=True,
                    )
                )

            if login_submitted:
                try:
                    authenticated_user = (
                        authenticate_local_user(
                            username=login_username,
                            password=login_password,
                        )
                    )

                    if authenticated_user is None:
                        st.error(
                            "用户名或密码错误。"
                        )

                        logger.warning(
                            "用户登录失败，用户名=%s",
                            login_username.strip().lower(),
                        )

                    else:
                        st.session_state.authenticated = True

                        st.session_state.current_user = (
                            build_session_user(
                                authenticated_user
                            )
                        )

                        clear_user_workspace_state()

                        logger.info(
                            "用户登录成功，用户ID=%s，用户名=%s",
                            authenticated_user["id"],
                            authenticated_user["user_key"],
                        )

                        st.rerun()

                except PermissionError as error:
                    st.error(str(error))

                    logger.warning(
                        "禁用用户尝试登录，用户名=%s",
                        login_username.strip().lower(),
                    )

                except Exception as error:
                    logger.exception(
                        "登录处理异常：%s",
                        error,
                    )

                    st.error(
                        "登录处理失败："
                        f"{type(error).__name__}"
                    )

        # ----------------------------------------------------
        # 注册
        # ----------------------------------------------------

        else:
            with st.form(
                "register_form",
                clear_on_submit=False,
            ):
                register_username = st.text_input(
                    "用户名",
                    placeholder=(
                        "3～32位英文、数字、下划线、点或减号"
                    ),
                    max_chars=32,
                )

                register_display_name = st.text_input(
                    "显示名称",
                    placeholder="例如：蔺鹏",
                    max_chars=50,
                )

                register_password = st.text_input(
                    "密码",
                    type="password",
                    placeholder=(
                        "至少8位，包含英文字母和数字"
                    ),
                )

                register_password_confirm = (
                    st.text_input(
                        "确认密码",
                        type="password",
                        placeholder="请再次输入密码",
                    )
                )

                register_submitted = (
                    st.form_submit_button(
                        "注册并登录",
                        type="primary",
                        use_container_width=True,
                    )
                )

            if register_submitted:
                normalized_display_name = (
                    register_display_name.strip()
                )

                if not normalized_display_name:
                    st.error(
                        "显示名称不能为空。"
                    )

                elif (
                    register_password
                    != register_password_confirm
                ):
                    st.error(
                        "两次输入的密码不一致。"
                    )

                else:
                    try:
                        new_user = create_local_user(
                            username=register_username,
                            display_name=(
                                normalized_display_name
                            ),
                            password=register_password,
                            role="user",
                            daily_limit=(
                                REGISTER_DAILY_LIMIT
                            ),
                        )

                        st.session_state.authenticated = True

                        st.session_state.current_user = (
                            build_session_user(
                                new_user
                            )
                        )

                        clear_user_workspace_state()

                        logger.info(
                            "新用户注册成功，用户ID=%s，用户名=%s",
                            new_user["id"],
                            new_user["user_key"],
                        )

                        st.rerun()

                    except AuthValidationError as error:
                        st.error(str(error))

                    except ValueError as error:
                        st.error(str(error))

                    except Exception as error:
                        logger.exception(
                            "用户注册异常：%s",
                            error,
                        )

                        st.error(
                            "注册处理失败："
                            f"{type(error).__name__}"
                        )

        st.divider()

        st.caption(
            "管理员账号请使用命令 "
            "`python create_admin.py` 创建或重置。"
        )


# ============================================================
# 登录状态检查
# ============================================================

if not st.session_state.get(
    "authenticated",
    False,
):
    render_auth_page()
    st.stop()


session_user = st.session_state.get(
    "current_user"
)

if not session_user:
    logout_user(
        "登录状态无效，请重新登录。"
    )


# 每次页面运行都重新读取用户状态，
# 这样管理员禁用用户或修改限额后可以及时生效。
database_user = get_user_by_id(
    int(session_user["id"])
)

if database_user is None:
    logout_user(
        "用户不存在，请重新登录。"
    )

if int(database_user["is_active"]) != 1:
    logout_user(
        "当前账号已经被管理员禁用。"
    )

current_user = build_session_user(
    database_user
)

st.session_state.current_user = (
    current_user
)

current_user_id = int(
    current_user["id"]
)


# ============================================================
# 清理当前用户多余空会话
# ============================================================

cleanup_state_key = (
    f"empty_conversations_cleaned_"
    f"{current_user_id}"
)

if not st.session_state.get(
    cleanup_state_key,
    False,
):
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
            "清理用户多余空会话，用户ID=%s，数量=%s",
            current_user_id,
            deleted_empty_count,
        )

    st.session_state[
        cleanup_state_key
    ] = True


# ============================================================
# 确保当前会话有效
# ============================================================

def ensure_current_conversation() -> dict[str, Any]:
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


current_conversation = (
    ensure_current_conversation()
)

current_conversation_id = (
    current_conversation["id"]
)

current_work_mode = (
    current_conversation["work_mode"]
)


# ============================================================
# 主页面标题
# ============================================================

render_brand_header()

st.caption(
    "V2.0 Alpha：登录、注册与多用户数据隔离版本"
)


# ============================================================
# 配置状态
# ============================================================

deepseek_key_ok = bool(
    os.getenv(
        "DEEPSEEK_API_KEY",
        "",
    ).strip()
)

opcua_config_ok = bool(
    os.getenv(
        "OPCUA_SERVER_URL",
        "",
    ).strip()
)

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

    st.markdown("### 当前用户")

    st.write(
        f"显示名称：`{current_user['display_name']}`"
    )

    st.write(
        f"用户名：`{current_user['user_key']}`"
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

    if st.button(
        "退出登录",
        use_container_width=True,
    ):
        logout_user()

    # --------------------------------------------------------
    # 修改密码
    # --------------------------------------------------------

    with st.expander(
        "修改密码",
        expanded=False,
    ):
        with st.form(
            "change_password_form",
            clear_on_submit=True,
        ):
            current_password = st.text_input(
                "当前密码",
                type="password",
            )

            new_password = st.text_input(
                "新密码",
                type="password",
                help=(
                    "至少8位，必须包含英文字母和数字。"
                ),
            )

            confirm_new_password = st.text_input(
                "确认新密码",
                type="password",
            )

            change_password_submitted = (
                st.form_submit_button(
                    "修改密码",
                    use_container_width=True,
                )
            )

        if change_password_submitted:
            if (
                new_password
                != confirm_new_password
            ):
                st.error(
                    "两次输入的新密码不一致。"
                )

            else:
                try:
                    changed = change_local_password(
                        user_id=current_user_id,
                        current_password=(
                            current_password
                        ),
                        new_password=new_password,
                    )

                    if changed:
                        st.success(
                            "密码修改成功。"
                        )

                        logger.info(
                            "用户修改密码成功，用户ID=%s",
                            current_user_id,
                        )

                    else:
                        st.error(
                            "当前密码不正确。"
                        )

                except (
                    AuthValidationError,
                    ValueError,
                ) as error:
                    st.error(str(error))

                except Exception as error:
                    logger.exception(
                        "密码修改异常：%s",
                        error,
                    )

                    st.error(
                        "密码修改失败："
                        f"{type(error).__name__}"
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

        logger.info(
            "打开通用问答会话，用户ID=%s，会话ID=%s，新建=%s",
            current_user_id,
            new_conversation["id"],
            created,
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

        logger.info(
            "打开PLC诊断会话，用户ID=%s，会话ID=%s，新建=%s",
            current_user_id,
            new_conversation["id"],
            created,
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
                f"{current_user_id}_"
                f"{current_conversation_id}"
            ),
        )

    # --------------------------------------------------------
    # 重命名会话
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
                f"{current_user_id}_"
                f"{current_conversation_id}"
            ),
        )

        if st.button(
            "保存新标题",
            use_container_width=True,
            key=(
                f"rename_save_"
                f"{current_user_id}_"
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
                        "会话重命名，用户ID=%s，会话ID=%s",
                        current_user_id,
                        current_conversation_id,
                    )

                    st.rerun()

                else:
                    st.error(
                        "会话标题修改失败。"
                    )

    st.divider()

    # --------------------------------------------------------
    # 搜索历史会话
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
        st.info(
            "没有找到相关历史会话。"
        )

    for conversation in conversations:
        conversation_id = conversation["id"]
        conversation_title = conversation["title"]
        conversation_mode = conversation["work_mode"]
        message_count = int(
            conversation["message_count"]
        )

        mode_icon = (
            "💬"
            if conversation_mode == GENERAL_MODE
            else "📡"
        )

        active_mark = (
            "▶ "
            if conversation_id
            == current_conversation_id
            else ""
        )

        select_column, delete_column = (
            st.columns(
                [6, 1]
            )
        )

        with select_column:
            if st.button(
                (
                    f"{active_mark}{mode_icon} "
                    f"{conversation_title}"
                ),
                key=(
                    f"history_select_"
                    f"{current_user_id}_"
                    f"{conversation_id}"
                ),
                use_container_width=True,
            ):
                st.session_state.current_conversation_id = (
                    conversation_id
                )

                logger.info(
                    "切换会话，用户ID=%s，会话ID=%s",
                    current_user_id,
                    conversation_id,
                )

                st.rerun()

        with delete_column:
            if st.button(
                "🗑️",
                key=(
                    f"history_delete_"
                    f"{current_user_id}_"
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
    # 删除确认
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
            "删除后无法恢复。"
        )

        confirm_column, cancel_column = (
            st.columns(2)
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

                    logger.info(
                        "删除会话，用户ID=%s，会话ID=%s",
                        current_user_id,
                        pending_delete_id,
                    )

                    st.rerun()

                else:
                    st.error(
                        "会话删除失败或没有权限。"
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

    with st.expander(
        "系统配置自检",
        expanded=False,
    ):
        st.write(
            "DeepSeek API："
            f"`{'已配置' if deepseek_key_ok else '未配置'}`"
        )

        st.write(
            "OPC UA地址："
            f"`{'已配置' if opcua_config_ok else '未配置'}`"
        )

        st.write(
            f"PLC变量：`{len(OPCUA_TAGS)}个`"
        )

        st.write("OPC UA模式：`只读`")

        st.write(
            f"提示词版本：`{PROMPT_VERSION}`"
        )

        st.write(
            f"最大上下文："
            f"`{MAX_HISTORY_MESSAGES}条消息`"
        )


# ============================================================
# 消息元数据
# ============================================================

def format_message_meta(
    meta: dict[str, Any] | None,
) -> str:
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
# 当前会话
# ============================================================

st.subheader(
    current_conversation["title"]
)

if current_work_mode == GENERAL_MODE:
    st.info(
        "当前为通用问答会话，本模式不会连接PLC。"
    )

else:
    st.info(
        "当前为PLC实时诊断会话，"
        "每次提问都会读取PLC最新状态快照。"
    )


# ============================================================
# 显示当前用户当前会话的消息
# ============================================================

stored_messages = get_messages(
    conversation_id=current_conversation_id,
    user_id=current_user_id,
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

input_placeholder = (
    "请输入PLC、WinCC、仪表或矿山自动化问题"
    if current_work_mode == GENERAL_MODE
    else "请输入设备状态查询或PLC实时诊断问题"
)

user_question = st.chat_input(
    input_placeholder
)


# ============================================================
# 处理问题
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

    add_message(
        conversation_id=current_conversation_id,
        role="user",
        content=user_question,
        meta={
            "work_mode": current_work_mode,
        },
        user_id=current_user_id,
    )

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
                    user_id=current_user_id,
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

                # ----------------------------------------------------
                # 通用问答
                # ----------------------------------------------------

                if current_work_mode == GENERAL_MODE:
                    mode_context = {
                        "role": "system",
                        "content": (
                            "当前为通用问答模式。"
                            "本次没有读取PLC实时数据。"
                            "如果用户询问当前设备实时状态，"
                            "必须明确说明没有实时PLC数据。"
                        ),
                    }

                    answer = ask_deepseek(
                        chat_history=[
                            mode_context,
                            *recent_messages,
                        ],
                        plc_data=None,
                    )

                # ----------------------------------------------------
                # PLC实时诊断
                # ----------------------------------------------------

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
                            "PLC读取失败，用户ID=%s，错误=%s",
                            current_user_id,
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
                                "本次已降级为通用问答。"
                                "当前没有有效实时PLC数据，"
                                "不得推测设备当前状态。"
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

                add_message(
                    conversation_id=current_conversation_id,
                    role="assistant",
                    content=answer,
                    meta=assistant_meta,
                    user_id=current_user_id,
                )

                increment_daily_usage(
                    current_user_id
                )

                logger.info(
                    "聊天处理完成，用户ID=%s，会话ID=%s，模式=%s",
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
                    "PLC连接失败，用户ID=%s，错误=%s",
                    current_user_id,
                    error,
                )

                st.error(
                    f"PLC数据读取失败：{error}"
                )

            except PermissionError as error:
                logger.warning(
                    "用户访问了无权限会话，用户ID=%s，错误=%s",
                    current_user_id,
                    error,
                )

                st.error(
                    "当前用户无权访问该会话。"
                )

            except Exception as error:
                logger.exception(
                    "请求处理异常，用户ID=%s，错误=%s",
                    current_user_id,
                    error,
                )

                st.error(
                    "请求处理失败："
                    f"{type(error).__name__}：{error}"
                )