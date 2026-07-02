import os

import streamlit as st
from dotenv import load_dotenv

from ai_client import AIClientError, ask_deepseek
from app_logger import get_logger
from opcua_client import read_all_tags_sync
from prompts import PROMPT_VERSION
from tag_config import OPCUA_TAGS


load_dotenv()

logger = get_logger()


# ============================================================
# 基础配置读取
# ============================================================

def get_int_env(
    name: str,
    default: int,
) -> int:
    """
    安全读取整数类型环境变量。
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


GENERAL_MODE = "通用问答模式"
PLC_MODE = "PLC实时诊断模式"

WORK_MODES = (
    GENERAL_MODE,
    PLC_MODE,
)


# ============================================================
# 页面基本设置
# ============================================================

st.set_page_config(
    page_title="矿山设备智能诊断助手",
    page_icon="⛏️",
    layout="wide",
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
    "V1.0：通用问答与PLC实时诊断双模式"
)


# ============================================================
# 初始化聊天记录
#
# 两种模式分别保存聊天记录，防止：
# 1. 通用问答引用旧PLC值
# 2. PLC诊断被通用话题干扰
# ============================================================

if "chat_histories" not in st.session_state:

    # 兼容旧版本的messages聊天记录。
    # 旧版本主要是PLC诊断，因此迁移到PLC模式。
    legacy_messages = st.session_state.get(
        "messages",
        [],
    )

    st.session_state.chat_histories = {
        GENERAL_MODE: [],
        PLC_MODE: legacy_messages,
    }


if "startup_logged" not in st.session_state:
    logger.info(
        "矿山设备智能诊断助手启动，"
        "提示词版本=%s，PLC变量数量=%s",
        PROMPT_VERSION,
        len(OPCUA_TAGS),
    )

    st.session_state.startup_logged = True


# ============================================================
# 配置自检
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


# ============================================================
# 左侧工作模式和系统设置
# ============================================================

with st.sidebar:
    st.header("系统设置")

    st.markdown("### 工作模式")

    work_mode = st.radio(
        "请选择当前工作模式",
        options=WORK_MODES,
        index=1,
        help=(
            "通用问答模式不会连接PLC；"
            "PLC实时诊断模式会在每次提问时读取PLC最新数据。"
        ),
    )

    auto_fallback = True

    if work_mode == PLC_MODE:
        auto_fallback = st.checkbox(
            "PLC断线时自动降级为通用问答",
            value=True,
            help=(
                "启用后，如果PLC或OPC UA连接失败，"
                "AI仍可提供通用排查建议，"
                "但不会声称获得了实时设备状态。"
            ),
        )

    st.divider()

    # --------------------------------------------------------
    # 根据模式判断必要配置
    # --------------------------------------------------------

    if work_mode == GENERAL_MODE:
        mode_configuration_ok = deepseek_key_ok

    else:
        mode_configuration_ok = (
            deepseek_key_ok
            and opcua_config_ok
            and tag_config_ok
        )

    if mode_configuration_ok:
        if work_mode == GENERAL_MODE:
            st.success(
                "通用问答模式已就绪，"
                "本模式不会连接PLC。"
            )
        else:
            st.success(
                "PLC诊断配置自检通过，"
                "提问时将在后台读取PLC最新数据。"
            )
    else:
        st.error(
            "当前工作模式所需配置不完整。"
        )

    st.markdown("### 配置自检")

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
            f"PLC变量配置：`{len(OPCUA_TAGS)}个`"
        )
    else:
        st.write("PLC变量配置：`为空`")

    st.write("OPC UA运行模式：`只读`")

    st.write(
        f"提示词版本：`{PROMPT_VERSION}`"
    )

    st.write(
        f"最大上下文消息数：`{MAX_HISTORY_MESSAGES}`"
    )

    st.caption(
        "“OPC UA地址已配置”只表示参数存在，"
        "不代表当前网络一定已连接。"
        "只有PLC实时诊断模式会实际读取PLC。"
    )

    st.divider()

    if st.button(
        "清空当前模式聊天记录",
        use_container_width=True,
    ):
        st.session_state.chat_histories[
            work_mode
        ] = []

        logger.info(
            "用户清空当前模式聊天记录，模式=%s",
            work_mode,
        )

        st.rerun()

    if st.button(
        "清空全部聊天记录",
        use_container_width=True,
    ):
        st.session_state.chat_histories = {
            GENERAL_MODE: [],
            PLC_MODE: [],
        }

        logger.info(
            "用户清空全部模式聊天记录"
        )

        st.rerun()


# ============================================================
# 获取当前模式的独立聊天记录
# ============================================================

current_messages = (
    st.session_state.chat_histories[
        work_mode
    ]
)


# ============================================================
# 主页面显示当前模式
# ============================================================

if work_mode == GENERAL_MODE:
    st.info(
        "当前为通用问答模式："
        "不会连接PLC，适合咨询PLC编程、WinCC、"
        "仪表、控制原理和矿山自动化知识。"
    )

else:
    st.info(
        "当前为PLC实时诊断模式："
        "每次发送问题时都会读取PLC最新状态快照。"
    )


# ============================================================
# 显示当前模式的历史聊天记录
# ============================================================

for message in current_messages:
    with st.chat_message(
        message["role"]
    ):
        st.markdown(
            message["content"]
        )

        message_meta = message.get(
            "meta"
        )

        if message_meta:
            st.caption(message_meta)


# ============================================================
# 根据模式设置输入提示
# ============================================================

if work_mode == GENERAL_MODE:
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

    # 保存用户问题
    current_messages.append(
        {
            "role": "user",
            "content": user_question,
        }
    )

    # 显示用户问题
    with st.chat_message("user"):
        st.markdown(user_question)

    # --------------------------------------------------------
    # 开始生成AI回答
    # --------------------------------------------------------

    with st.chat_message("assistant"):
        with st.spinner(
            "正在分析，请稍候……"
        ):
            try:
                # 只发送最近若干条消息，
                # 防止上下文无限增长。
                recent_messages = current_messages[
                    -MAX_HISTORY_MESSAGES:
                ]

                answer = ""
                answer_meta = ""

                # ====================================================
                # 模式一：通用问答
                # ====================================================

                if work_mode == GENERAL_MODE:

                    mode_context = {
                        "role": "system",
                        "content": (
                            "当前工作模式为通用问答模式。"
                            "本次请求没有读取PLC实时数据。"
                            "请回答通用的矿山自动化、PLC、"
                            "WinCC、仪表或控制技术问题。"
                            "如果用户询问当前设备的实时状态，"
                            "必须明确说明当前模式没有实时PLC数据，"
                            "不得虚构设备状态。"
                        ),
                    }

                    request_messages = [
                        mode_context,
                        *recent_messages,
                    ]

                    answer = ask_deepseek(
                        chat_history=request_messages,
                        plc_data=None,
                    )

                    answer_meta = (
                        "工作模式：通用问答｜"
                        "本次未读取PLC数据"
                    )

                    logger.info(
                        "通用问答完成"
                    )

                # ====================================================
                # 模式二：PLC实时诊断
                # ====================================================

                else:
                    latest_plc_data = None
                    plc_read_error = None
                    degraded_mode = False

                    total_count = 0
                    success_count = 0
                    failed_count = 0
                    snapshot_time = "未知"

                    # -----------------------------------------------
                    # 尝试读取PLC
                    # -----------------------------------------------

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
                            "PLC实时读取失败，错误=%s",
                            error,
                        )

                        # -------------------------------------------
                        # 自动降级为通用问答
                        # -------------------------------------------

                        if auto_fallback:
                            degraded_mode = True
                            latest_plc_data = None

                            st.warning(
                                "PLC实时数据读取失败，"
                                "本次请求已自动降级为通用问答。"
                                "AI只会提供通用分析和检查建议，"
                                "不会推测当前设备状态。"
                            )

                        else:
                            raise ConnectionError(
                                "PLC实时数据读取失败，"
                                "并且自动降级功能未启用。"
                                f"错误：{error}"
                            ) from error

                    # -----------------------------------------------
                    # PLC读取成功
                    # -----------------------------------------------

                    if not degraded_mode:

                        if failed_count > 0:
                            st.warning(
                                f"本次成功读取"
                                f"{success_count}个变量，"
                                f"读取失败"
                                f"{failed_count}个变量。"
                                "读取失败的变量不会被判断为"
                                "False、0或无故障。"
                            )

                        mode_context = {
                            "role": "system",
                            "content": (
                                "当前工作模式为PLC实时诊断模式。"
                                "本次PLC快照是唯一有效的实时状态。"
                                "历史聊天中的PLC值均为旧快照，"
                                "不得使用旧值代替本次读取结果。"
                            ),
                        }

                        request_messages = [
                            mode_context,
                            *recent_messages,
                        ]

                        answer = ask_deepseek(
                            chat_history=request_messages,
                            plc_data=latest_plc_data,
                        )

                        answer_meta = (
                            "工作模式：PLC实时诊断｜"
                            f"PLC快照时间：{snapshot_time}｜"
                            f"读取成功："
                            f"{success_count}/{total_count}"
                        )

                        logger.info(
                            "PLC实时诊断完成，"
                            "成功读取=%s，失败=%s，"
                            "快照时间=%s",
                            success_count,
                            failed_count,
                            snapshot_time,
                        )

                    # -----------------------------------------------
                    # PLC读取失败后自动降级
                    # -----------------------------------------------

                    else:
                        mode_context = {
                            "role": "system",
                            "content": (
                                "当前原计划使用PLC实时诊断模式，"
                                "但本次PLC或OPC UA数据读取失败，"
                                "系统已经降级为通用问答模式。"
                                "本次没有任何有效的实时PLC数据。"
                                "不得声称已经读取设备状态，"
                                "不得推测Run、Remote、Fault、"
                                "报警、联锁或命令的当前值。"
                                "可以提供通用原因分析、"
                                "检查步骤以及建议补充的变量。"
                            ),
                        }

                        request_messages = [
                            mode_context,
                            *recent_messages,
                        ]

                        answer = ask_deepseek(
                            chat_history=request_messages,
                            plc_data=None,
                        )

                        error_type = (
                            type(plc_read_error).__name__
                            if plc_read_error
                            else "未知错误"
                        )

                        answer_meta = (
                            "工作模式：PLC实时诊断"
                            "（已自动降级）｜"
                            "本次未获得PLC实时数据｜"
                            f"读取错误：{error_type}"
                        )

                        logger.info(
                            "PLC诊断已降级为通用问答，"
                            "错误类型=%s",
                            error_type,
                        )

                # ====================================================
                # 显示并保存AI回答
                # ====================================================

                st.markdown(answer)

                if answer_meta:
                    st.caption(answer_meta)

                current_messages.append(
                    {
                        "role": "assistant",
                        "content": answer,
                        "meta": answer_meta,
                    }
                )

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
                    "诊断流程出现异常：%s",
                    error,
                )

                st.error(
                    "诊断请求失败："
                    f"{type(error).__name__}：{error}"
                )