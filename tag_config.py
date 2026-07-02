# ============================================================
# OPC UA变量配置
#
# node_id：
#   UaExpert中显示的完整NodeId
#
# description：
#   变量含义，将一并提供给AI进行诊断
#
# 注意：
#   当前程序只读取这些变量，不执行任何OPC UA写入操作。
# ============================================================


OPCUA_TAGS = {
    "测试布尔量": {
        "node_id": 'ns=3;s="AI_Assistant"."TestBool"',
        "description": "OPC UA基础测试布尔变量",
    },

    "测试整数": {
        "node_id": 'ns=3;s="AI_Assistant"."TestInt"',
        "description": "OPC UA基础测试整数变量",
    },

    "测试浮点数": {
        "node_id": 'ns=3;s="AI_Assistant"."TestReal"',
        "description": "OPC UA基础测试浮点变量",
    },

    "测试字符串": {
        "node_id": 'ns=3;s="AI_Assistant"."TestString"',
        "description": "OPC UA基础测试字符串变量",
    },

    "写入测试命令（仅监视）": {
        "node_id": 'ns=3;s="AI_Assistant"."WriteCommand"',
        "description": (
            "测试命令变量，当前诊断助手只读取该变量，"
            "不会对该变量进行写入"
        ),
    },

    "PP01故障状态": {
        "node_id": 'ns=3;s="AI_Assistant"."PP01Fault"',
        "description": (
            "PP01设备故障状态，True表示存在故障，"
            "False表示当前未检测到故障"
        ),
    },

    "PP01远程状态": {
        "node_id": 'ns=3;s="AI_Assistant"."PP01Remote"',
        "description": (
            "PP01设备远程控制状态，True表示处于远程模式，"
            "False表示不在远程模式"
        ),
    },

    "PP01运行状态": {
        "node_id": 'ns=3;s="AI_Assistant"."PP01Run"',
        "description": (
            "PP01设备运行反馈，True表示设备正在运行，"
            "False表示设备未运行"
        ),
    },

    "PP02故障状态": {
        "node_id": 'ns=3;s="AI_Assistant"."PP02Fault"',
        "description": (
            "PP02设备故障状态，True表示存在故障，"
            "False表示当前未检测到故障"
        ),
    },

    "PP02远程状态": {
        "node_id": 'ns=3;s="AI_Assistant"."PP02Remote"',
        "description": (
            "PP02设备远程控制状态，True表示处于远程模式，"
            "False表示不在远程模式"
        ),
    },

    "PP02运行状态": {
        "node_id": 'ns=3;s="AI_Assistant"."PP02Run"',
        "description": (
            "PP02设备运行反馈，True表示设备正在运行，"
            "False表示设备未运行"
        ),
    },
}