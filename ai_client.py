import os
import time
from typing import Any

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app_logger import get_logger
from prompts import SYSTEM_PROMPT


load_dotenv()

logger = get_logger()


class AIClientError(RuntimeError):
    """
    提供给页面显示的AI调用错误。
    """


def get_float_env(
    name: str,
    default: float,
) -> float:
    """
    安全读取浮点数环境变量。
    """

    value = os.getenv(name, "").strip()

    if not value:
        return default

    try:
        return float(value)

    except ValueError:
        logger.warning(
            "%s配置无效，使用默认值%s",
            name,
            default,
        )
        return default


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


def get_ai_client() -> OpenAI:
    """
    创建DeepSeek兼容客户端。
    """

    api_key = os.getenv(
        "DEEPSEEK_API_KEY",
        "",
    ).strip()

    base_url = os.getenv(
        "DEEPSEEK_BASE_URL",
        "https://api.deepseek.com",
    ).strip()

    timeout_seconds = get_float_env(
        "AI_TIMEOUT_SECONDS",
        60.0,
    )

    max_retries = get_int_env(
        "AI_MAX_RETRIES",
        1,
    )

    if not api_key:
        raise AIClientError(
            "没有配置DEEPSEEK_API_KEY，"
            "请检查项目根目录中的.env文件。"
        )

    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_seconds,
        max_retries=max_retries,
    )


def format_plc_value(
    value: Any,
    plc_type: str,
) -> str:
    """
    格式化PLC变量值。

    作用：
    1. REAL和LREAL避免显示过长的小数。
    2. BOOL统一显示True或False。
    3. None显示为破折号。
    """

    if value is None:
        return "—"

    if plc_type in {"REAL", "LREAL"}:
        try:
            return f"{float(value):.6g}"
        except (TypeError, ValueError):
            return str(value)

    if plc_type == "BOOL":
        return "True" if bool(value) else "False"

    return str(value)


def build_plc_context(
    plc_data: dict[str, dict[str, Any]],
) -> str:
    """
    将PLC快照转换成AI可理解的诊断上下文。
    """

    if not plc_data:
        return (
            "当前没有读取到任何PLC实时数据。"
            "不得推测设备实时状态。"
        )

    # --------------------------------------------------------
    # 读取本次快照时间和数据来源
    # --------------------------------------------------------

    snapshot_time = "未知"
    data_source = "未知"

    for result in plc_data.values():

        if result.get("snapshot_time"):
            snapshot_time = result["snapshot_time"]

        if result.get("data_source"):
            data_source = result["data_source"]

        if (
            snapshot_time != "未知"
            and data_source != "未知"
        ):
            break

    # --------------------------------------------------------
    # 必须先创建lines，再执行任何lines.append()
    # --------------------------------------------------------

    lines = [
        "以下PLC快照是本次请求唯一有效的实时数据。",
        "历史聊天记录中的PLC数值均属于旧快照，必须忽略。",
        "回答中的实时值必须严格采用本次读取结果。",
        f"PLC快照时间：{snapshot_time}",
        f"数据来源：{data_source}",
        "",
    ]

    success_count = 0
    failed_count = 0
    failed_names: list[str] = []

    # --------------------------------------------------------
    # 整理每一个PLC变量
    # --------------------------------------------------------

    for tag_name, result in plc_data.items():

        if result.get("success"):
            success_count += 1

            value = result.get("value")

            plc_type = result.get(
                "plc_type",
                "未知",
            )

            opcua_type = result.get(
                "opcua_type",
                "未知",
            )

            description = result.get(
                "description",
                "",
            )

            display_value = format_plc_value(
                value=value,
                plc_type=plc_type,
            )

            lines.append(
                f"- 变量名称：{tag_name}"
            )

            lines.append(
                f"  实时值：{display_value}"
            )

            lines.append(
                f"  PLC数据类型：{plc_type}"
            )

            lines.append(
                f"  OPC UA数据类型：{opcua_type}"
            )

            if description:
                lines.append(
                    f"  变量说明：{description}"
                )

            lines.append("")

        else:
            failed_count += 1
            failed_names.append(tag_name)

            error = result.get(
                "error",
                "未知错误",
            )

            plc_type = result.get(
                "plc_type",
                "未知",
            )

            description = result.get(
                "description",
                "",
            )

            lines.append(
                f"- 变量名称：{tag_name}"
            )

            lines.append(
                "  读取状态：失败"
            )

            lines.append(
                f"  PLC数据类型：{plc_type}"
            )

            lines.append(
                f"  错误：{error}"
            )

            if description:
                lines.append(
                    f"  变量说明：{description}"
                )

            lines.append(
                "  注意：读取失败不能判断为False、0或无故障。"
            )

            lines.append("")

    # --------------------------------------------------------
    # 添加本次读取汇总
    # --------------------------------------------------------

    lines.append(
        f"本次成功读取{success_count}个变量，"
        f"读取失败{failed_count}个变量。"
    )

    if failed_names:
        lines.append(
            "读取失败变量："
            + "、".join(failed_names)
        )

    lines.extend(
        [
            "",
            "请严格依据以上PLC快照回答。",
            "不得虚构未提供的变量、报警、联锁或设备状态。",
            "必须说明这些数据仅代表快照时间对应的状态。",
        ]
    )

    return "\n".join(lines)


def ask_deepseek(
    chat_history: list[dict],
    plc_data: dict[str, dict[str, Any]] | None = None,
) -> str:
    """
    调用DeepSeek并返回回答。
    """

    client = get_ai_client()

    model = os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-chat",
    ).strip()

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    if plc_data is not None:
        plc_context = build_plc_context(
            plc_data
        )

        messages.append(
            {
                "role": "system",
                "content": plc_context,
            }
        )

    messages.extend(chat_history)

    start_time = time.perf_counter()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
        )

        answer = response.choices[0].message.content

        if not answer:
            raise AIClientError(
                "DeepSeek没有返回有效内容。"
            )

        elapsed_seconds = (
            time.perf_counter() - start_time
        )

        logger.info(
            "DeepSeek调用成功，模型=%s，"
            "耗时=%.2f秒，上下文消息数=%s",
            model,
            elapsed_seconds,
            len(chat_history),
        )

        return answer

    except AuthenticationError as error:
        logger.error(
            "DeepSeek认证失败：%s",
            error,
        )

        raise AIClientError(
            "DeepSeek API认证失败，"
            "请检查API Key是否正确或是否已经失效。"
        ) from error

    except RateLimitError as error:
        logger.error(
            "DeepSeek频率或额度限制：%s",
            error,
        )

        raise AIClientError(
            "DeepSeek请求受到频率限制，"
            "或者账户额度不足，请稍后重试。"
        ) from error

    except APITimeoutError as error:
        logger.error(
            "DeepSeek请求超时：%s",
            error,
        )

        raise AIClientError(
            "DeepSeek请求超时，"
            "请检查网络连接后重新提问。"
        ) from error

    except APIConnectionError as error:
        logger.error(
            "DeepSeek网络连接失败：%s",
            error,
        )

        raise AIClientError(
            "无法连接DeepSeek服务，"
            "请检查网络、代理或防火墙设置。"
        ) from error

    except APIStatusError as error:
        status_code = getattr(
            error,
            "status_code",
            "未知",
        )

        logger.error(
            "DeepSeek服务返回异常，"
            "状态码=%s，错误=%s",
            status_code,
            error,
        )

        raise AIClientError(
            f"DeepSeek服务返回异常，"
            f"状态码：{status_code}。"
        ) from error

    except AIClientError:
        raise

    except Exception as error:
        logger.exception(
            "DeepSeek调用发生未知异常：%s",
            error,
        )

        raise AIClientError(
            "DeepSeek调用发生未知错误："
            f"{type(error).__name__}"
        ) from error