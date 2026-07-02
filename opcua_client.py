import asyncio
import os
from datetime import datetime
from typing import Any

from asyncua import Client
from dotenv import load_dotenv

from app_logger import get_logger
from tag_config import OPCUA_TAGS


load_dotenv()

logger = get_logger()


OPCUA_TO_PLC_TYPE = {
    "Boolean": "BOOL",
    "SByte": "SINT",
    "Byte": "USINT / BYTE",
    "Int16": "INT",
    "UInt16": "UINT / WORD",
    "Int32": "DINT",
    "UInt32": "UDINT / DWORD",
    "Int64": "LINT",
    "UInt64": "ULINT / LWORD",
    "Float": "REAL",
    "Double": "LREAL",
    "String": "STRING",
    "DateTime": "DTL / DATE_AND_TIME",
    "ByteString": "ARRAY OF BYTE",
}


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


def get_opcua_config() -> tuple[str, str, str]:
    """
    从.env文件读取OPC UA配置。
    """

    server_url = os.getenv(
        "OPCUA_SERVER_URL",
        "",
    ).strip()

    username = os.getenv(
        "OPCUA_USERNAME",
        "",
    ).strip()

    password = os.getenv(
        "OPCUA_PASSWORD",
        "",
    ).strip()

    if not server_url:
        raise ValueError(
            "没有配置OPCUA_SERVER_URL，"
            "请检查项目根目录中的.env文件。"
        )

    return server_url, username, password


def create_client() -> Client:
    """
    创建只读OPC UA客户端。

    本程序没有任何变量写入函数。
    """

    server_url, username, password = get_opcua_config()

    timeout_seconds = get_float_env(
        "OPCUA_TIMEOUT_SECONDS",
        5.0,
    )

    session_timeout_ms = get_int_env(
        "OPCUA_SESSION_TIMEOUT_MS",
        30000,
    )

    client = Client(
        url=server_url,
        timeout=timeout_seconds,
    )

    client.session_timeout = session_timeout_ms

    if username:
        client.set_user(username)
        client.set_password(password)

    return client


def get_variant_type_name(
    variant_type: Any,
) -> str:
    """
    获取OPC UA数据类型名称。
    """

    type_name = getattr(
        variant_type,
        "name",
        None,
    )

    if type_name:
        return str(type_name)

    return str(variant_type).split(".")[-1]


def convert_to_plc_type(
    opcua_type: str,
) -> str:
    """
    将OPC UA类型转换为常见西门子PLC类型。
    """

    return OPCUA_TO_PLC_TYPE.get(
        opcua_type,
        opcua_type,
    )


def get_snapshot_time() -> str:
    """
    获取带时区的本机读取时间。
    """

    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


async def read_multiple_nodes(
    tags: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """
    一次连接OPC UA服务器，读取全部配置变量。

    每个变量返回：
    - 实时值
    - PLC数据类型
    - OPC UA数据类型
    - NodeId
    - 变量说明
    - 本次快照读取时间
    - 读取成功或失败状态
    """

    if not tags:
        logger.warning("OPC UA变量配置为空")
        return {}

    server_url, _, _ = get_opcua_config()
    snapshot_time = get_snapshot_time()

    client = create_client()
    results: dict[str, dict[str, Any]] = {}

    connected = False

    logger.info(
        "开始读取OPC UA变量，服务器=%s，变量数量=%s",
        server_url,
        len(tags),
    )

    try:
        await client.connect()
        connected = True

        logger.info(
            "OPC UA服务器连接成功，服务器=%s",
            server_url,
        )

        for tag_name, tag_config in tags.items():
            node_id = tag_config["node_id"]

            description = tag_config.get(
                "description",
                "",
            )

            try:
                node = client.get_node(node_id)

                value = await node.read_value()

                variant_type = (
                    await node.read_data_type_as_variant_type()
                )

                opcua_type = get_variant_type_name(
                    variant_type
                )

                plc_type = convert_to_plc_type(
                    opcua_type
                )

                results[tag_name] = {
                    "value": value,
                    "python_type": type(value).__name__,
                    "opcua_type": opcua_type,
                    "plc_type": plc_type,
                    "node_id": node_id,
                    "description": description,
                    "snapshot_time": snapshot_time,
                    "data_source": server_url,
                    "success": True,
                    "error": None,
                }

            except Exception as error:
                results[tag_name] = {
                    "value": None,
                    "python_type": None,
                    "opcua_type": None,
                    "plc_type": None,
                    "node_id": node_id,
                    "description": description,
                    "snapshot_time": snapshot_time,
                    "data_source": server_url,
                    "success": False,
                    "error": str(error),
                }

                logger.error(
                    "变量读取失败，变量=%s，NodeId=%s，错误=%s",
                    tag_name,
                    node_id,
                    error,
                )

        success_count = sum(
            1
            for result in results.values()
            if result["success"]
        )

        failed_count = len(results) - success_count

        logger.info(
            "OPC UA读取完成，成功=%s，失败=%s，快照时间=%s",
            success_count,
            failed_count,
            snapshot_time,
        )

        return results

    except Exception as error:
        logger.exception(
            "OPC UA连接或读取失败，服务器=%s，错误=%s",
            server_url,
            error,
        )
        raise

    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception as error:
                logger.warning(
                    "OPC UA断开连接时出现异常：%s",
                    error,
                )


def read_all_tags_sync() -> dict[str, dict[str, Any]]:
    """
    供Streamlit同步调用。
    """

    return asyncio.run(
        read_multiple_nodes(OPCUA_TAGS)
    )


async def test_opcua_connection() -> None:
    """
    在PowerShell中测试全部变量。
    """

    print("=" * 80)
    print("开始读取OPC UA变量")
    print(
        f"服务器地址："
        f"{os.getenv('OPCUA_SERVER_URL')}"
    )
    print(f"配置变量数量：{len(OPCUA_TAGS)}")
    print("=" * 80)

    try:
        results = await read_multiple_nodes(
            OPCUA_TAGS
        )

        success_count = 0
        failed_count = 0
        snapshot_time = "未知"

        for tag_name, result in results.items():
            snapshot_time = result.get(
                "snapshot_time",
                snapshot_time,
            )

            if result["success"]:
                success_count += 1

                print(
                    f"{tag_name}："
                    f"值={result['value']}，"
                    f"PLC类型={result['plc_type']}，"
                    f"OPC UA类型={result['opcua_type']}"
                )

            else:
                failed_count += 1

                print(
                    f"{tag_name}：读取失败，"
                    f"错误={result['error']}"
                )

        print()
        print(f"PLC快照时间：{snapshot_time}")
        print(f"成功读取：{success_count}个")
        print(f"读取失败：{failed_count}个")

    except Exception as error:
        print("OPC UA服务器连接失败")
        print(f"错误类型：{type(error).__name__}")
        print(f"错误内容：{error}")


if __name__ == "__main__":
    asyncio.run(
        test_opcua_connection()
    )